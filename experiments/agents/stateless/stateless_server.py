#!/usr/bin/env python3
"""Episode-scoped MCP tools for the public stateless generation runner.

The runner materializes the checked-out base snapshot before starting this
server.  The server then exposes only the two documented namespaces: ``repo/``
for the working copy and ``input/`` for read-only episode inputs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any


PROTOCOL_VERSION = "2025-06-18"
MAX_READ_LINES = 400
MAX_FILE_BYTES = 1_000_000
MAX_SEARCH_MATCHES = 50
MAX_TREE_DEPTH = 4
READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def _success(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


class Trace:
    """Small JSON trace compatible with the public artifact's trace shape."""

    def __init__(self, episode_id: str, episode: dict[str, Any], traces_dir: Path) -> None:
        self.episode_id = episode_id
        self.episode = episode
        self.traces_dir = traces_dir
        self.started_at = time.time()
        self.calls: list[dict[str, Any]] = []

    def record(
        self,
        tool: str,
        args: dict[str, Any],
        output: Any,
        elapsed_ms: float,
        *,
        ok: bool = True,
        error: str | None = None,
    ) -> None:
        self.calls.append(
            {
                "tool": tool,
                "args": args,
                "output": output,
                "elapsed_ms": elapsed_ms,
                "ok": ok,
                "error": error,
            }
        )
        self.flush()

    def flush(self, *, error: str | None = None) -> None:
        now = time.time()
        payload = {
            "episode_id": self.episode_id,
            "agent_name": "stateless_public_mcp",
            "episode_input": self.episode,
            "started_at": self.started_at,
            "finished_at": now,
            "elapsed_ms": (now - self.started_at) * 1000,
            "tool_calls": self.calls,
            "prediction": None,
            "error": error,
        }
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        (self.traces_dir / f"{self.episode_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


class StatelessServer:
    def __init__(
        self,
        *,
        episode_path: Path,
        workspace_root: Path,
        input_root: Path,
        traces_dir: Path,
    ) -> None:
        payload = json.loads(episode_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("episode JSON must contain one object")
        self.episode = payload
        self.episode_id = str(payload.get("episode_id") or episode_path.stem)
        self.repo_root = workspace_root.resolve()
        self.input_root = input_root.resolve()
        self.production_files = {
            self._normalize_relative_path(str(path))
            for path in payload.get("prod_files") or []
            if str(path).strip()
        }
        self.trace = Trace(self.episode_id, payload, traces_dir.resolve())
        self._check_root(self.repo_root)
        self._check_root(self.input_root)

    @staticmethod
    def _check_root(root: Path) -> None:
        if not root.exists() or not root.is_dir() or root.is_symlink():
            raise ValueError(f"workspace namespace is not a directory: {root}")

    @staticmethod
    def _normalize_relative_path(path: str) -> str:
        normalized = path.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return PurePosixPath(normalized).as_posix()

    @property
    def tool_definitions(self) -> list[dict[str, Any]]:
        definitions = [
            {
                "name": "tree",
                "description": "List a repository subtree at the episode base snapshot.",
                "annotations": READ_ONLY_ANNOTATIONS,
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "path": {"type": "string", "default": "repo"},
                        "depth": {"type": "integer", "minimum": 0, "maximum": MAX_TREE_DEPTH, "default": 2},
                    },
                },
            },
            {
                "name": "ls",
                "description": "List one directory in the episode repository or input namespace.",
                "annotations": READ_ONLY_ANNOTATIONS,
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"path": {"type": "string", "default": "repo"}},
                },
            },
            {
                "name": "read_file",
                "description": "Read at most 400 UTF-8 lines from repo/ or input/.",
                "annotations": READ_ONLY_ANNOTATIONS,
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path"],
                    "properties": {
                        "path": {"type": "string"},
                        "start": {"type": ["integer", "null"], "minimum": 1},
                        "end": {"type": ["integer", "null"], "minimum": 1},
                    },
                },
            },
            {
                "name": "search",
                "description": "Search literal text in repository or input files.",
                "annotations": READ_ONLY_ANNOTATIONS,
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "path": {"type": "string", "default": "repo"},
                    },
                },
            },
            {
                "name": "write_file",
                "description": (
                    "Write complete UTF-8 content to any non-production file under repo/. "
                    "The episode's prod_files are read-only."
                ),
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path", "content"],
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                },
            },
            {
                "name": "delete_file",
                "description": (
                    "Delete one non-production file under repo/. "
                    "The episode's prod_files are read-only."
                ),
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": True,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path"],
                    "properties": {"path": {"type": "string"}},
                },
            },
        ]
        return definitions

    def _resolve(self, raw: str, *, allow_input: bool = True) -> tuple[str, Path]:
        normalized = str(raw or "").replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            raise ValueError("path outside allowed namespaces")
        parts = path.parts
        namespace = parts[0] if parts and parts[0] in {"repo", "input"} else "repo"
        if namespace == "input" and not allow_input:
            raise ValueError("input/ is read-only")
        root = self.repo_root if namespace == "repo" else self.input_root
        relative = parts[1:] if parts and parts[0] in {"repo", "input"} else parts
        target = root.joinpath(*relative)
        current = root
        for part in relative:
            current /= part
            if current.is_symlink():
                raise ValueError("symlinks are not exposed")
        resolved = target.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError("path escapes namespace")
        return namespace, resolved

    @staticmethod
    def _visible(namespace: str, root: Path, path: Path) -> str:
        relative = path.relative_to(root)
        prefix = namespace + "/"
        return prefix + (str(relative) if str(relative) != "." else "")

    def _walk_files(self, target: Path):
        if target.is_file():
            yield target
            return
        pending = [target]
        while pending:
            directory = pending.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            files: list[Path] = []
            directories: list[Path] = []
            for entry in entries:
                if entry.name == ".git" or entry.is_symlink():
                    continue
                path = Path(entry.path)
                try:
                    if entry.is_file(follow_symlinks=False):
                        files.append(path)
                    elif entry.is_dir(follow_symlinks=False):
                        directories.append(path)
                except OSError:
                    continue
            for path in sorted(files):
                yield path
            pending.extend(sorted(directories, reverse=True))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            result = self._call_tool(name, arguments)
        except Exception as exc:
            self.trace.record(name, arguments, None, (time.perf_counter() - started) * 1000, ok=False, error=str(exc))
            raise
        self.trace.record(name, arguments, result, (time.perf_counter() - started) * 1000)
        return result

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name in {"tree", "ls", "read_file", "search"}:
            return self._read_tool(name, arguments)
        if name in {"write_file", "delete_file"}:
            return self._write_tool(name, arguments)
        raise ValueError(f"unknown tool: {name}")

    def _read_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(arguments.get("path") or "repo")
        namespace, target = self._resolve(raw_path)
        root = self.repo_root if namespace == "repo" else self.input_root
        if name == "ls":
            if not target.is_dir():
                raise ValueError(f"not a directory: {raw_path}")
            entries = []
            for item in sorted(target.iterdir(), key=lambda p: p.name):
                if item.name == ".git" or item.is_symlink():
                    continue
                entries.append({"name": item.name, "type": "dir" if item.is_dir() else "file"})
            return {"path": raw_path, "entries": entries}
        if name == "tree":
            if not target.is_dir():
                raise ValueError(f"not a directory: {raw_path}")
            depth = max(0, min(int(arguments.get("depth", 2)), MAX_TREE_DEPTH))
            entries: list[str] = []
            for item in sorted(target.rglob("*")):
                if item.is_symlink() or ".git" in item.relative_to(target).parts:
                    continue
                relative = item.relative_to(target)
                if len(relative.parts) <= depth:
                    entries.append(str(relative) + ("/" if item.is_dir() else ""))
                if len(entries) >= 1000:
                    break
            return {"path": raw_path, "depth": depth, "entries": entries, "truncated": len(entries) >= 1000}
        if name == "read_file":
            if not target.is_file():
                raise ValueError(f"not a file: {raw_path}")
            if target.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("file exceeds the public read limit")
            lines = target.read_text(encoding="utf-8").splitlines()
            start = max(1, int(arguments.get("start") or 1))
            requested_end = int(arguments.get("end") or len(lines))
            end = min(len(lines), requested_end, start + MAX_READ_LINES - 1)
            return {
                "path": raw_path,
                "start": start,
                "end": end,
                "total_lines": len(lines),
                "truncated": end < min(requested_end, len(lines)),
                "content": "\n".join(lines[start - 1 : end]),
            }
        query = str(arguments.get("query") or "")
        if not query:
            raise ValueError("query must be non-empty")
        search_root = target if target.is_dir() else target.parent
        matches = []
        for item in self._walk_files(search_root):
            try:
                if item.stat().st_size > MAX_FILE_BYTES:
                    continue
                lines = item.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(lines, 1):
                if query in line:
                    matches.append({"path": self._visible(namespace, root, item), "line": number, "text": line})
                    if len(matches) >= MAX_SEARCH_MATCHES:
                        return {"query": query, "path": raw_path, "matches": matches, "truncated": True}
        return {"query": query, "path": raw_path, "matches": matches, "truncated": False}

    def _write_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(arguments.get("path") or "")
        if not raw_path.startswith("repo/"):
            raise ValueError("write operations are limited to repo/")
        relative = raw_path[len("repo/") :]
        _, target = self._resolve(raw_path, allow_input=False)
        normalized_relative = target.relative_to(self.repo_root).as_posix()
        if not relative or normalized_relative in self.production_files:
            raise ValueError("production files are read-only: " + normalized_relative)
        if name == "write_file":
            content = arguments.get("content")
            if not isinstance(content, str):
                raise ValueError("content must be a string")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"path": raw_path, "bytes": len(content.encode("utf-8")), "operation": "write"}
        if not target.exists():
            raise ValueError(f"file does not exist: {raw_path}")
        if not target.is_file():
            raise ValueError(f"not a file: {raw_path}")
        target.unlink()
        return {"path": raw_path, "operation": "delete"}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "initialize":
            return _success(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "instructions": "Use only repo/ and input/ namespaces through the listed tools; input/ is read-only.",
                    "serverInfo": {"name": "repo-test-evolution-stateless", "version": "1.0.0"},
                },
            )
        if method in {"notifications/initialized", "notifications/cancelled"}:
            return None
        if method == "ping":
            return _success(request_id, {})
        if method == "tools/list":
            return _success(request_id, {"tools": self.tool_definitions})
        if method == "tools/call":
            params = message.get("params") or {}
            try:
                result = self.call_tool(str(params.get("name") or ""), params.get("arguments") or {})
                return _success(
                    request_id,
                    {
                        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                        "structuredContent": result,
                        "isError": False,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                return _success(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(server: StatelessServer) -> None:
    error: str | None = None
    try:
        for raw_line in sys.stdin:
            if not raw_line.strip():
                continue
            try:
                message = json.loads(raw_line)
                response = server.handle(message)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": error}}
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                sys.stdout.flush()
    finally:
        server.trace.flush(error=error)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", type=Path, required=True)
    parser.add_argument("--repos-root", type=Path, help="Accepted for runner compatibility; base snapshot is already materialized.")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--traces-dir", type=Path, required=True)
    args = parser.parse_args()
    serve(StatelessServer(episode_path=args.episode.resolve(), workspace_root=args.workspace_root.resolve(), input_root=args.input_root.resolve(), traces_dir=args.traces_dir.resolve()))


if __name__ == "__main__":
    main()
