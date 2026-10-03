from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def test_multi_commit_episode_is_emitted_from_one_correct_base(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src/a.js").write_text("a0\n", encoding="utf-8")
    (repo / "src/b.js").write_text("b0\n", encoding="utf-8")
    (repo / "tests/a.test.js").write_text("t0\n", encoding="utf-8")
    base = _commit(repo, "base")

    (repo / "src/a.js").write_text("a1\n", encoding="utf-8")
    first = _commit(repo, "first")
    (repo / "src/b.js").write_text("b1\n", encoding="utf-8")
    (repo / "tests/a.test.js").write_text("t1\n", encoding="utf-8")
    second = _commit(repo, "second")

    def record(commit: str, timestamp: int, prod_path: str, with_test: bool) -> dict:
        edge = {
            "testFile": "tests/a.test.js",
            "coveredProdFiles": ["src/a.js", "src/b.js"],
            "coverageKind": "coverage-final",
            "coverageStatus": "collected",
            "runnerFamily": "vitest",
            "notes": [],
        }
        return {
            "repoRoot": str(repo),
            "commit": commit,
            "base": base if commit == first else first,
            "commitMeta": {
                "shortHash": commit[:8],
                "authorDate": f"2026-01-01T00:00:0{timestamp}Z",
            },
            "timestampMs": timestamp * 1000,
            "prodChanges": [
                {
                    "nodeId": f"prod:{commit}:{prod_path}",
                    "path": prod_path,
                    "previousPath": None,
                    "diffPaths": [prod_path],
                    "gitStatus": "M",
                    "semantic": {"decision": "keep", "reason": "test"},
                }
            ],
            "coverage": {
                "selectedForCoverageCount": 1 if with_test else 0,
                "edges": [edge] if with_test else [],
            },
        }

    records = tmp_path / "records.json"
    records.write_text(
        json.dumps([record(second, 2, "src/b.js", True), record(first, 1, "src/a.js", False)]),
        encoding="utf-8",
    )
    output = tmp_path / "pairing.json"
    subprocess.run(
        [
            "node",
            str(ROOT / "experiments/pipeline/graph_pairer.mjs"),
            "pair-records",
            "--records-file",
            str(records),
            "--output-json",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    [episode] = json.loads(output.read_text(encoding="utf-8"))
    assert episode["base_commit"] == base
    assert episode["prod_commits"] == [first, second]
    assert episode["test_commits"] == [second]
    assert episode["prod_diff"] == subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            base,
            second,
            "--",
            "src/a.js",
            "src/b.js",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert episode["test_diff"].endswith("\n")
