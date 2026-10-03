"""Commit-pinned git readers for participant-side repository tools."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

HIDDEN_GIT_NAMES = {".git"}


@dataclass(frozen=True)
class GitTreeEntry:
    """One entry returned by git ls-tree."""

    mode: str
    kind: str
    object_id: str
    name: str
    path: str

    @property
    def is_dir(self) -> bool:
        return self.kind == "tree"

    @property
    def is_file(self) -> bool:
        return self.kind == "blob" and not self.is_symlink

    @property
    def is_symlink(self) -> bool:
        return self.mode == "120000"


class GitCommitReader:
    """Read repository contents directly from git at a pinned commit.

    This reader never checks out or exports a working tree. All data is fetched
    through read-only git commands such as ls-tree, cat-file, show, and grep.
    """

    def __init__(
        self, repos_root: str | Path, _unused_cache_root: str | Path | None = None
    ) -> None:
        self.repos_root = Path(repos_root).resolve()

    def repo_path(self, repo: str) -> Path:
        path = (self.repos_root / repo).resolve()
        if not path.exists():
            raise FileNotFoundError(f"repo not found: {path}")
        if not (path / ".git").exists():
            raise FileNotFoundError(f"repo is not a git checkout: {path}")
        return path

    def run_git(
        self,
        repo: str,
        args: list[str],
        *,
        text: bool = True,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.repo_path(repo),
            capture_output=True,
            text=text,
            check=False,
        )
        if check and proc.returncode != 0:
            stderr = (
                proc.stderr
                if isinstance(proc.stderr, str)
                else proc.stderr.decode("utf-8", "replace")
            )
            stdout = (
                proc.stdout
                if isinstance(proc.stdout, str)
                else proc.stdout.decode("utf-8", "replace")
            )
            raise RuntimeError(
                (stderr or stdout).strip() or f"git command failed: {' '.join(args)}"
            )
        return proc

    def ensure_commit(self, repo: str, commit: str) -> None:
        self.run_git(repo, ["cat-file", "-e", f"{commit}^{{commit}}"])

    def safe_path(self, user_path: str) -> str:
        if not user_path or user_path == ".":
            return ""
        candidate = Path(user_path)
        if candidate.is_absolute():
            raise ValueError("absolute paths are not allowed")
        if any(part == ".." for part in candidate.parts):
            raise ValueError("path traversal is not allowed")
        if any(part in HIDDEN_GIT_NAMES for part in candidate.parts):
            raise ValueError("internal git paths are not accessible")
        normalized = candidate.as_posix().strip("/")
        if normalized in {"", "."}:
            return ""
        return normalized

    def object_spec(self, commit: str, rel_path: str) -> str:
        return commit if not rel_path else f"{commit}:{rel_path}"

    def literal_pathspec(self, rel_path: str) -> str:
        return f":(literal){rel_path}"

    def parse_ls_tree(self, output: str, parent: str = "") -> list[GitTreeEntry]:
        entries: list[GitTreeEntry] = []
        for record in output.split("\0"):
            if not record:
                continue
            metadata, name = record.split("\t", 1)
            mode, kind, object_id = metadata.split(" ", 2)
            path = f"{parent}/{name}" if parent else name
            entries.append(
                GitTreeEntry(
                    mode=mode,
                    kind=kind,
                    object_id=object_id,
                    name=name,
                    path=path,
                )
            )
        return entries

    def entry_for_path(self, repo: str, commit: str, user_path: str) -> GitTreeEntry:
        rel_path = self.safe_path(user_path)
        if not rel_path:
            return GitTreeEntry(mode="040000", kind="tree", object_id="", name="", path="")
        proc = self.run_git(
            repo,
            ["ls-tree", "-z", commit, "--", self.literal_pathspec(rel_path)],
        )
        entries = self.parse_ls_tree(proc.stdout)
        matches = [entry for entry in entries if entry.path == rel_path]
        if not matches:
            raise FileNotFoundError(user_path)
        return matches[0]

    def list_dir(self, repo: str, commit: str, user_path: str = ".") -> list[GitTreeEntry]:
        rel_path = self.safe_path(user_path)
        entry = self.entry_for_path(repo, commit, rel_path)
        if not entry.is_dir:
            raise NotADirectoryError(user_path)
        proc = self.run_git(repo, ["ls-tree", "-z", self.object_spec(commit, rel_path)])
        return self.parse_ls_tree(proc.stdout, rel_path)

    def read_blob(self, repo: str, commit: str, user_path: str, max_bytes: int) -> str:
        rel_path = self.safe_path(user_path)
        if not rel_path:
            raise FileNotFoundError(user_path)
        entry = self.entry_for_path(repo, commit, rel_path)
        if entry.is_symlink:
            raise ValueError("symlinks are not accessible")
        if not entry.is_file:
            raise FileNotFoundError(user_path)
        object_spec = self.object_spec(commit, rel_path)
        size_proc = self.run_git(repo, ["cat-file", "-s", object_spec])
        size = int(size_proc.stdout.strip() or "0")
        if size > max_bytes:
            raise ValueError(f"file too large: {size} bytes")
        content_proc = self.run_git(repo, ["show", object_spec], text=False)
        return content_proc.stdout.decode("utf-8", "replace")

    def grep(
        self,
        repo: str,
        commit: str,
        query: str,
        user_path: str,
        max_matches: int,
    ) -> list[dict[str, object]]:
        rel_path = self.safe_path(user_path)
        if rel_path:
            entry = self.entry_for_path(repo, commit, rel_path)
            if entry.is_symlink:
                raise ValueError("symlinks are not accessible")
        command = [
            "grep",
            "--line-number",
            "--fixed-strings",
            "--max-count",
            str(max_matches),
            "-e",
            query,
            commit,
        ]
        if rel_path:
            command.extend(["--", self.literal_pathspec(rel_path)])
        proc = self.run_git(repo, command, check=False)
        if proc.returncode == 1:
            return []
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout).strip())
        prefix = f"{commit}:"
        matches: list[dict[str, object]] = []
        for line in proc.stdout.splitlines():
            if len(matches) >= max_matches:
                break
            if line.startswith(prefix):
                line = line[len(prefix) :]
            try:
                file_path, line_number, text = line.split(":", 2)
            except ValueError:
                continue
            if any(part in HIDDEN_GIT_NAMES for part in Path(file_path).parts):
                continue
            matches.append({"path": file_path, "line": int(line_number), "text": text})
        return matches

    def cleanup(self) -> None:
        """Compatibility hook; git-backed reads do not create per-run snapshots."""


# Backwards-compatible name for older imports/tests. It no longer materializes snapshots.
SnapshotManager = GitCommitReader
