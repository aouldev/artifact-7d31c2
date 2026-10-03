from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from benchmark.participant.core.models import Episode
from benchmark.participant.core.trace import TraceRecorder
from benchmark.participant.tools.gateway import ToolGateway
from benchmark.participant.tools.snapshot import SnapshotManager


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _make_repo(root: Path) -> tuple[Path, str, str]:
    repo = root / "demo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-m", "base")
    base_commit = _git(repo, "rev-parse", "HEAD")
    (repo / "file.txt").write_text("future\n", encoding="utf-8")
    _git(repo, "commit", "-am", "future")
    future_commit = _git(repo, "rev-parse", "HEAD")
    return repo, base_commit, future_commit


def test_tools_are_bound_to_base_snapshot(tmp_path: Path) -> None:
    _repo, base_commit, _future_commit = _make_repo(tmp_path / "repos")
    manager = SnapshotManager(tmp_path / "repos", tmp_path / "snapshots")
    traces = TraceRecorder(tmp_path / "traces")
    tools = ToolGateway(manager, traces)
    episode = Episode("e1", "demo", base_commit, ["file.txt"], "diff")
    traces.start_episode("e1", "agent", episode.to_dict(include_gold=False))
    tools.bind_episode(episode)

    result = tools.read_file("file.txt")

    assert result["content"] == "base"
    assert ".git" not in tools.ls()["entries"][0]["name"]


def test_tools_reject_path_traversal(tmp_path: Path) -> None:
    _repo, base_commit, _future_commit = _make_repo(tmp_path / "repos")
    manager = SnapshotManager(tmp_path / "repos", tmp_path / "snapshots")
    tools = ToolGateway(manager, TraceRecorder(tmp_path / "traces"))
    tools.bind_episode(Episode("e1", "demo", base_commit, [], ""))

    with pytest.raises(ValueError, match="path traversal"):
        tools.read_file("../secret")
