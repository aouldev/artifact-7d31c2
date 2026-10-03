from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from benchmark.server.tools.prepared_snapshot import (
    PRODUCT_STATE_POLICY_VERSION,
    _changed_paths_between,
    ensure_prepared_snapshot,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_prepared_state_preserves_renamed_tests_and_browser_helpers(tmp_path: Path) -> None:
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    old_test = "src/old.spec.ts"
    helpers = ["playwright/filter-helpers.ts", "cypress/support/commands.ts"]
    unusual = "tests/space [x]\nname.ts"
    for path in [old_test, *helpers, unusual, "src/prod.ts", "src/rename.ts"]:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"// {path}\nexport const value = 1;\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / old_test).rename(repo / "src/new.spec.ts")
    (repo / "src/rename.ts").rename(repo / "src/renamed.ts")
    for path in [*helpers, unusual, "src/prod.ts"]:
        with (repo / path).open("a") as handle:
            handle.write("export const added = 2;\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "mixed changes")
    target = git(repo, "rev-parse", "HEAD")
    changed = _changed_paths_between(repo, base, target)
    assert old_test in changed and "src/new.spec.ts" in changed
    assert unusual in changed
    snapshot = ensure_prepared_snapshot(
        repo_source=repo,
        output_root=tmp_path / "snapshots",
        episode_id="episode",
        repo="demo",
        base_commit=base,
        prod_diff=git(repo, "diff", base, target, "--", "src/prod.ts", "src/rename.ts", "src/renamed.ts"),
        prod_commits=[target],
        excluded_paths=["src/new.spec.ts"],
    )
    for path in [old_test, *helpers, unusual]:
        expected = subprocess.run(
            ["git", "-C", str(repo), "show", f"{base}:{path}"],
            capture_output=True,
            check=True,
        ).stdout
        assert (snapshot.repo_root / path).read_bytes() == expected
    assert not (snapshot.repo_root / "src/new.spec.ts").exists()
    assert "added = 2" in (snapshot.repo_root / "src/prod.ts").read_text()
    assert not (snapshot.repo_root / "src/rename.ts").exists()
    assert (snapshot.repo_root / "src/renamed.ts").exists()


def test_prepared_snapshot_clones_without_implicit_checkout(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "file.ts").write_text("old\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    snapshot = __import__("benchmark.server.tools.prepared_snapshot", fromlist=["create_prepared_snapshot"]).create_prepared_snapshot(
        repo_source=repo,
        output_root=tmp_path / "snapshots",
        episode_id="no-implicit-checkout",
        repo="demo",
        base_commit=base,
        prod_diff="",
    )
    assert (snapshot.repo_root / "file.ts").read_text() == "old\n"


@pytest.mark.parametrize("invalidate", ["policy", "exclusions"])
def test_prepared_cache_respects_policy_and_exclusions(tmp_path: Path, invalidate: str) -> None:
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "helper.ts").write_text("old\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "helper.ts").write_text("new\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "change")
    target = git(repo, "rev-parse", "HEAD")
    kwargs = dict(
        repo_source=repo,
        output_root=tmp_path / "snapshots",
        episode_id="episode",
        repo="demo",
        base_commit=base,
        prod_diff=git(repo, "diff", base, target),
        prod_commits=[target],
        excluded_paths=[],
    )
    first = ensure_prepared_snapshot(**kwargs)
    assert ensure_prepared_snapshot(**kwargs).created_at == first.created_at
    marker = first.repo_root / "stale-marker"
    marker.write_text("old snapshot")
    dependency = first.repo_root / "node_modules" / "cached-package" / "index.js"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("cached dependency")
    if invalidate == "policy":
        manifest = json.loads(first.manifest_path.read_text())
        manifest["metadata"].pop("product_state_policy_version")
        first.manifest_path.write_text(json.dumps(manifest))
    else:
        kwargs["excluded_paths"] = ["helper.ts"]
    fresh = ensure_prepared_snapshot(**kwargs)
    assert not marker.exists()
    assert dependency.read_text() == "cached dependency"
    assert fresh.metadata["product_state_policy_version"] == PRODUCT_STATE_POLICY_VERSION
    # Exclusion metadata must never remove files supplied by the episode diff.
    assert (fresh.repo_root / "helper.ts").read_text() == "new\n"
