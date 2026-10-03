#!/usr/bin/env python3
"""MCP server for the public Continuous and Online reference runners.

Continuous exposes a persistent ``memory/`` namespace that the actor may read
and write. Online exposes a host-provided memory catalog through
``memory_read`` and keeps that namespace read-only. Both modes share the
episode-scoped repository tools used by the stateless condition.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any

STATELESS_DIR = Path(__file__).resolve().parent / "stateless"
if str(STATELESS_DIR) not in sys.path:
    sys.path.insert(0, str(STATELESS_DIR))

from stateless_server import (
    READ_ONLY_ANNOTATIONS,
    StatelessServer,
)


def _path_matches(anchor: str, candidate: str) -> bool:
    anchor_parts = PurePosixPath(anchor).parts
    candidate_parts = PurePosixPath(candidate).parts
    return bool(anchor_parts) and candidate_parts[: len(anchor_parts)] == anchor_parts


def select_memory_records(
    records: list[dict[str, Any]],
    *,
    prod_files: list[str],
    path: str | None = None,
    max_records: int = 8,
    max_linked: int = 4,
    max_chars: int = 6000,
) -> tuple[list[dict[str, Any]], int]:
    """Select complete records using path-addressed direct/parent/linked routes."""

    requested = [str(value).replace("\\", "/") for value in prod_files]
    if path:
        requested.append(str(path).replace("\\", "/"))
    candidates: list[tuple[int, int, str, dict[str, Any]]] = []
    linked_count = 0
    for record in records:
        if not isinstance(record, dict) or record.get("status", "active") != "active":
            continue
        anchor = str(record.get("anchor_path") or "").strip().strip("/")
        if not anchor:
            continue
        route: str | None = None
        rank = 0
        if any(_path_matches(anchor, item) or _path_matches(item, anchor) for item in requested):
            route, rank = "direct", 0
        elif any(
            _path_matches(anchor, str(PurePosixPath(item).parent)) for item in requested
        ):
            route, rank = "parent", 2
        else:
            links = record.get("links") or []
            if any(
                isinstance(link, dict)
                and any(_path_matches(str(link.get("target_path") or ""), item) for item in requested)
                for link in links
            ):
                if linked_count >= max_linked:
                    continue
                route, rank = "linked", 1
                linked_count += 1
        if route is None:
            continue
        candidates.append(
            (
                rank,
                -int(record.get("support_count") or 1),
                str(record.get("memory_id") or ""),
                {"route": route, "record": record},
            )
        )
    candidates.sort(key=lambda item: item[:3])
    selected: list[dict[str, Any]] = []
    omitted = 0

    def payload(items: list[dict[str, Any]], skipped: int) -> dict[str, Any]:
        result: dict[str, Any] = {"memory_records": items, "omitted_by_budget": skipped}
        if path:
            result["path"] = path
        return result

    for _, _, _, item in candidates:
        if len(selected) >= max_records:
            omitted += 1
            continue
        if len(json.dumps(payload([*selected, item], omitted), ensure_ascii=False)) > max_chars:
            omitted += 1
            continue
        selected.append(item)
    return selected, omitted


class StatefulServer(StatelessServer):
    def __init__(
        self,
        *,
        mode: str,
        episode_path: Path,
        workspace_root: Path,
        input_root: Path,
        memory_root: Path,
        traces_dir: Path,
        train_root: Path | None = None,
        memory_catalog_path: Path | None = None,
        read_only_repo: bool = False,
    ) -> None:
        if mode not in {"continuous", "online"}:
            raise ValueError("mode must be continuous or online")
        super().__init__(
            episode_path=episode_path,
            workspace_root=workspace_root,
            input_root=input_root,
            traces_dir=traces_dir,
        )
        self.mode = mode
        self.memory_root = memory_root.resolve()
        self.memory_root.mkdir(parents=True, exist_ok=True)
        self.train_root = train_root.resolve() if train_root else None
        self.read_only_repo = read_only_repo
        if self.train_root is not None and not self.train_root.is_dir():
            raise ValueError(f"train namespace is not a directory: {self.train_root}")
        self.memory_catalog: list[dict[str, Any]] = []
        if memory_catalog_path is not None:
            payload = json.loads(memory_catalog_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload = (
                    payload.get("memory_catalog")
                    or payload.get("memory_records")
                    or payload.get("records")
                    or []
                )
            if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
                raise ValueError("memory catalog must be a JSON array of objects")
            payload = [
                item["record"] if isinstance(item.get("record"), dict) else item
                for item in payload
            ]
            episode_repo = str(self.episode.get("repo") or "")
            foreign = sorted(
                str(item.get("repo"))
                for item in payload
                if item.get("repo") and str(item.get("repo")) != episode_repo
            )
            if foreign:
                raise ValueError(
                    f"memory catalog contains records from another repository: {foreign}"
                )
            self.memory_catalog = payload
            if self.mode == "online":
                initial, omitted = select_memory_records(
                    payload,
                    prod_files=[str(value) for value in self.episode.get("prod_files") or []],
                )
                (self.memory_root / "initial_records.json").write_text(
                    json.dumps(
                        {"memory_records": initial, "omitted_by_budget": omitted},
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )

    @property
    def tool_definitions(self) -> list[dict[str, Any]]:
        definitions = super().tool_definitions
        descriptions = {
            "tree": "List a subtree in repo/, input/, memory/, or optional train/.",
            "ls": "List one directory in repo/, input/, memory/, or optional train/.",
            "read_file": "Read at most 400 UTF-8 lines from repo/, input/, memory/, or optional train/.",
            "search": "Search literal text in repo/, input/, memory/, or optional train/.",
            "write_file": (
                "Write complete UTF-8 content to any non-production file under repo/ "
                "or to memory/ in Continuous mode; the episode's prod_files are read-only."
            ),
            "delete_file": (
                "Delete one non-production file under repo/; memory/ records are write-only "
                "in Continuous mode."
            ),
        }
        for definition in definitions:
            if definition["name"] in descriptions:
                definition["description"] = descriptions[definition["name"]]
        if self.mode == "online":
            definitions.append(
                {
                    "name": "memory_read",
                    "description": "Retrieve relevant records from the host-managed memory catalog.",
                    "annotations": READ_ONLY_ANNOTATIONS,
                    "inputSchema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["path"],
                        "properties": {"path": {"type": "string", "minLength": 1}},
                    },
                }
            )
        return definitions

    def _resolve(self, raw: str, *, allow_input: bool = True) -> tuple[str, Path]:
        normalized = str(raw or "").replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            raise ValueError("path outside allowed namespaces")
        parts = path.parts
        namespaces = {"repo": self.repo_root, "input": self.input_root, "memory": self.memory_root}
        if self.train_root is not None:
            namespaces["train"] = self.train_root
        namespace = parts[0] if parts and parts[0] in namespaces else "repo"
        if namespace == "input" and not allow_input:
            raise ValueError("input/ is read-only")
        if namespace == "memory" and self.mode == "online" and not allow_input:
            raise ValueError("memory/ is read-only in Online mode")
        if namespace == "train" and not allow_input:
            raise ValueError("train/ is read-only")
        root = namespaces[namespace]
        relative = parts[1:] if parts and parts[0] in namespaces else parts
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

    def _read_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(arguments.get("path") or "repo")
        namespace, target = self._resolve(raw_path)
        original_repo, original_input = self.repo_root, self.input_root
        if namespace in {"memory", "train"}:
            if name == "search":
                query = str(arguments.get("query") or "")
                if not query:
                    raise ValueError("query must be non-empty")
                matches = []
                for item in self._walk_files(target if target.is_dir() else target.parent):
                    try:
                        lines = item.read_text(encoding="utf-8").splitlines()
                    except (OSError, UnicodeDecodeError):
                        continue
                    for number, line in enumerate(lines, 1):
                        if query in line:
                            matches.append({"path": f"{namespace}/{item.relative_to(self.memory_root if namespace == 'memory' else self.train_root)}", "line": number, "text": line})
                            if len(matches) >= 50:
                                return {"query": query, "path": raw_path, "matches": matches, "truncated": True}
                return {"query": query, "path": raw_path, "matches": matches, "truncated": False}
            if name == "read_file":
                if not target.is_file():
                    raise ValueError(f"not a file: {raw_path}")
                lines = target.read_text(encoding="utf-8").splitlines()
                start = max(1, int(arguments.get("start") or 1))
                end = min(len(lines), int(arguments.get("end") or len(lines)), start + 399)
                return {"path": raw_path, "start": start, "end": end, "total_lines": len(lines), "truncated": end < min(int(arguments.get("end") or len(lines)), len(lines)), "content": "\n".join(lines[start - 1:end])}
            if name == "ls":
                if not target.is_dir():
                    raise ValueError(f"not a directory: {raw_path}")
                return {"path": raw_path, "entries": [{"name": item.name, "type": "dir" if item.is_dir() else "file"} for item in sorted(target.iterdir(), key=lambda p: p.name) if not item.is_symlink()]}
            if name == "tree":
                if not target.is_dir():
                    raise ValueError(f"not a directory: {raw_path}")
                depth = max(0, min(int(arguments.get("depth", 2)), 4))
                entries = []
                for item in sorted(target.rglob("*")):
                    if item.is_symlink():
                        continue
                    relative = item.relative_to(target)
                    if len(relative.parts) <= depth:
                        entries.append(str(relative) + ("/" if item.is_dir() else ""))
                return {"path": raw_path, "depth": depth, "entries": entries[:1000], "truncated": len(entries) > 1000}
        self.repo_root, self.input_root = original_repo, original_input
        return super()._read_tool(name, arguments)

    def _write_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(arguments.get("path") or "")
        if self.read_only_repo:
            namespace, _ = self._resolve(raw_path, allow_input=False)
            if namespace == "repo":
                raise ValueError("repo/ is read-only for this historical-memory turn")
        if raw_path.startswith("memory/"):
            if self.mode != "continuous":
                raise ValueError("memory/ is read-only in Online mode")
            _, target = self._resolve(raw_path, allow_input=False)
            if name != "write_file":
                raise ValueError("Continuous memory entries cannot be deleted through this interface")
            content = arguments.get("content")
            if not isinstance(content, str):
                raise ValueError("content must be a string")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"path": raw_path, "bytes": len(content.encode("utf-8")), "operation": "write"}
        return super()._write_tool(name, arguments)

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "memory_read":
            if self.mode != "online":
                raise ValueError("memory_read is available only in Online mode")
            path = str(arguments.get("path") or "")
            records, omitted = select_memory_records(
                self.memory_catalog,
                prod_files=list(self.episode.get("prod_files") or []),
                path=path,
            )
            return {"path": path, "memory_records": records, "omitted_by_budget": omitted}
        return super()._call_tool(name, arguments)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("continuous", "online"), required=True)
    parser.add_argument("--episode", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--memory-root", type=Path, required=True)
    parser.add_argument("--traces-dir", type=Path, required=True)
    parser.add_argument("--train-root", type=Path)
    parser.add_argument("--memory-catalog", type=Path)
    parser.add_argument("--read-only-repo", action="store_true")
    parser.add_argument("--repos-root", type=Path, help="Accepted for runner compatibility; source snapshots are materialized by the host.")
    args = parser.parse_args()
    server = StatefulServer(
        mode=args.mode,
        episode_path=args.episode.resolve(),
        workspace_root=args.workspace_root.resolve(),
        input_root=args.input_root.resolve(),
        memory_root=args.memory_root.resolve(),
        traces_dir=args.traces_dir.resolve(),
        train_root=args.train_root.resolve() if args.train_root else None,
        memory_catalog_path=args.memory_catalog.resolve() if args.memory_catalog else None,
        read_only_repo=args.read_only_repo,
    )
    # Reuse the stdio loop without importing a second server entry point.
    from stateless_server import serve

    serve(server)


if __name__ == "__main__":
    main()
