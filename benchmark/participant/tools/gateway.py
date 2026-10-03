"""Episode-scoped read-only repository tools exposed to agents."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from benchmark.participant.core.models import Episode
from benchmark.participant.core.trace import ToolCallRecord, TraceRecorder
from benchmark.participant.tools.snapshot import GitCommitReader

HIDDEN_SYSTEM_NAMES = {".git"}


@dataclass(frozen=True)
class ToolLimits:
    max_tree_depth: int = 4
    max_file_bytes: int = 1_000_000
    max_read_lines: int = 400
    max_search_matches: int = 50


class ToolGateway:
    """Read-only tools bound to the current episode's base commit."""

    def __init__(
        self,
        reader: GitCommitReader,
        trace_recorder: TraceRecorder,
        limits: ToolLimits | None = None,
    ) -> None:
        self.reader = reader
        self.trace_recorder = trace_recorder
        self.limits = limits or ToolLimits()
        self._episode: Episode | None = None

    def bind_episode(self, episode: Episode) -> None:
        self._episode = episode

    def clear_episode(self) -> None:
        self._episode = None

    @property
    def episode(self) -> Episode:
        if self._episode is None:
            raise RuntimeError("tools are not bound to an episode")
        return self._episode

    def _record(self, tool: str, args: dict[str, Any], fn: Callable[[], Any]) -> Any:
        started = time.time()
        try:
            output = fn()
            record = ToolCallRecord(
                tool=tool,
                args=args,
                output=output,
                elapsed_ms=(time.time() - started) * 1000,
                ok=True,
            )
            self.trace_recorder.record_tool_call(record)
            return output
        except Exception as exc:
            record = ToolCallRecord(
                tool=tool,
                args=args,
                output=None,
                elapsed_ms=(time.time() - started) * 1000,
                ok=False,
                error=str(exc),
            )
            self.trace_recorder.record_tool_call(record)
            raise

    def repo_info(self) -> dict[str, Any]:
        episode = self.episode
        args = {"episode_id": episode.episode_id}

        def run() -> dict[str, Any]:
            self.reader.ensure_commit(episode.repo, episode.base_commit)
            return {
                "repo": episode.repo,
                "base_commit": episode.base_commit,
                "prod_files": episode.prod_files,
            }

        return self._record("repo_info", args, run)

    def ls(self, path: str = ".") -> dict[str, Any]:
        args = {"path": path}

        def run() -> dict[str, Any]:
            episode = self.episode
            entries = []
            for entry in self.reader.list_dir(episode.repo, episode.base_commit, path):
                if entry.name in HIDDEN_SYSTEM_NAMES or entry.is_symlink:
                    continue
                entries.append({"name": entry.name, "type": "dir" if entry.is_dir else "file"})
            return {"path": path, "entries": entries}

        return self._record("ls", args, run)

    def tree(self, path: str = ".", depth: int = 2) -> dict[str, Any]:
        bounded_depth = max(0, min(depth, self.limits.max_tree_depth))
        args = {"path": path, "depth": depth, "effective_depth": bounded_depth}

        def run() -> dict[str, Any]:
            episode = self.episode
            rel_path = self.reader.safe_path(path)
            start_entry = self.reader.entry_for_path(episode.repo, episode.base_commit, rel_path)
            if not start_entry.is_dir:
                raise NotADirectoryError(path)
            lines: list[str] = []

            def walk(current_path: str, prefix: str, remaining: int) -> None:
                if remaining < 0:
                    return
                entries = [
                    entry
                    for entry in self.reader.list_dir(
                        episode.repo, episode.base_commit, current_path
                    )
                    if entry.name not in HIDDEN_SYSTEM_NAMES and not entry.is_symlink
                ]
                entries = sorted(entries, key=lambda item: (not item.is_dir, item.name))
                for index, entry in enumerate(entries):
                    connector = "└── " if index == len(entries) - 1 else "├── "
                    lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir else ''}")
                    if entry.is_dir and remaining > 0:
                        extension = "    " if index == len(entries) - 1 else "│   "
                        walk(entry.path, prefix + extension, remaining - 1)

            lines.append(f"{path.rstrip('/') or '.'}/")
            walk(rel_path, "", bounded_depth - 1)
            return {"path": path, "depth": bounded_depth, "tree": "\n".join(lines)}

        return self._record("tree", args, run)

    def read_file(
        self,
        path: str,
        start: int | None = None,
        end: int | None = None,
    ) -> dict[str, Any]:
        args = {"path": path, "start": start, "end": end}

        def run() -> dict[str, Any]:
            episode = self.episode
            content = self.reader.read_blob(
                episode.repo,
                episode.base_commit,
                path,
                max_bytes=self.limits.max_file_bytes,
            )
            lines = content.splitlines()
            start_index = max((start or 1) - 1, 0)
            requested_end = end if end is not None else len(lines)
            end_index = min(requested_end, start_index + self.limits.max_read_lines, len(lines))
            selected = lines[start_index:end_index]
            return {
                "path": path,
                "start": start_index + 1,
                "end": start_index + len(selected),
                "total_lines": len(lines),
                "truncated": end_index < min(requested_end, len(lines)),
                "content": "\n".join(selected),
            }

        return self._record("read_file", args, run)

    def search(self, query: str, path: str = ".") -> dict[str, Any]:
        args = {"query": query, "path": path}

        def run() -> dict[str, Any]:
            if not query:
                raise ValueError("query must be non-empty")
            episode = self.episode
            matches = self.reader.grep(
                episode.repo,
                episode.base_commit,
                query,
                path,
                max_matches=self.limits.max_search_matches,
            )
            return {
                "query": query,
                "path": path,
                "matches": matches,
                "truncated": len(matches) >= self.limits.max_search_matches,
            }

        return self._record("search", args, run)
