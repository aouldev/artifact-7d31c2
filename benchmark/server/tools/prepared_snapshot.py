"""Prepared warm snapshot utilities for dynamic benchmark evaluation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PreparedSnapshot:
    episode_id: str
    repo: str
    path: Path
    base_commit: str
    prepared_commit: str
    prod_diff_sha256: str
    created_at: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def repo_root(self) -> Path:
        return self.path / "repo"

    @property
    def manifest_path(self) -> Path:
        return self.path / "manifest.json"

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "repo": self.repo,
            "path": str(self.path),
            "repo_root": str(self.repo_root),
            "base_commit": self.base_commit,
            "prepared_commit": self.prepared_commit,
            "prod_diff_sha256": self.prod_diff_sha256,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


def _run(
    argv: list[str],
    cwd: str | Path | None = None,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd is not None else None,
        input=input_text,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def _fetch_commit_if_missing(repo_root: Path, repo_source: Path, commit: str) -> None:
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if exists.returncode == 0:
        return
    _run(["git", "fetch", str(repo_source.resolve()), commit], cwd=repo_root)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_git_patch(patch_text: str) -> str:
    """Return the unified-diff portion of a patch-like string."""

    lines = patch_text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("diff --git "):
            normalized = "".join(lines[index:])
            return normalized if normalized.endswith("\n") else f"{normalized}\n"
    return patch_text if not patch_text or patch_text.endswith("\n") else f"{patch_text}\n"


PRODUCT_STATE_POLICY_VERSION = 4

_TEST_PATH_RE = re.compile(
    r"(^|/)(__tests__|tests?|specs?|e2e|playwright|cypress|fixtures?|__fixtures__|mocks|__mocks__)(/|$)"
    r"|\.(test|spec)\.[^/]+$|(^|/)(mocksData|fixtures|vitestSetup|testSetup|setupTests)\.[^/]+$",
    re.IGNORECASE,
)


def _is_test_like_path(path: str) -> bool:
    normalized = path.strip("/")
    return bool(_TEST_PATH_RE.search(normalized))


def _changed_paths_between(repo_root: Path, base_commit: str, target_commit: str) -> list[str]:
    # Enumerate both sides of renames; otherwise excluding the new test path can
    # turn a rename into an unintended deletion of the old test in the patch.
    output = _run(
        ["git", "diff", "--no-renames", "--name-only", "-z", base_commit, target_commit],
        cwd=repo_root,
    ).stdout
    return [path for path in output.split("\0") if path]


def _product_state_patch_from_commit(
    *,
    repo_root: Path,
    base_commit: str,
    target_commit: str,
    excluded_paths: list[str],
) -> str:
    excluded = {path.strip("/") for path in excluded_paths if path.strip("/")}
    for path in _changed_paths_between(repo_root, base_commit, target_commit):
        if _is_test_like_path(path):
            excluded.add(path)

    argv = ["git", "diff", "--binary", base_commit, target_commit, "--", "."]
    argv.extend(f":(top,literal,exclude){path}" for path in sorted(excluded))
    # Keep CRLF bytes in patch context. Python's text-mode newline conversion
    # strips carriage returns from `git diff` output, which makes a patch fail
    # against CRLF blobs even though it was generated from the correct base.
    proc = subprocess.run(argv, cwd=repo_root, capture_output=True, check=False)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or f"git diff failed with exit code {proc.returncode}")
    return proc.stdout.decode("utf-8")


def _prod_commits_from_metadata(metadata: dict[str, Any] | None) -> list[str]:
    if not metadata:
        return []
    value = metadata.get("prod_commits")
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value:
        return [value]
    return []


def _linear_product_target_commit(
    repo_root: Path, base_commit: str, prod_commits: list[str]
) -> str | None:
    best_commit: str | None = None
    best_distance = -1
    for commit in prod_commits:
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", base_commit, commit],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if ancestor.returncode != 0:
            continue
        distance_proc = subprocess.run(
            ["git", "rev-list", "--count", f"{base_commit}..{commit}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if distance_proc.returncode != 0:
            continue
        try:
            distance = int(distance_proc.stdout.strip() or "0")
        except ValueError:
            continue
        if distance > best_distance:
            best_commit = commit
            best_distance = distance
    return best_commit if best_distance > 0 else None


def _product_state_patch(
    *,
    repo_root: Path,
    repo_source: Path,
    base_commit: str,
    prod_diff: str,
    prod_commits: list[str],
    excluded_paths: list[str] | None,
) -> tuple[str, str]:
    # Commit IDs describe provenance, not additional evaluation input. Replaying
    # their complete trees silently introduces changes absent from the episode.
    # Keep the arguments for existing callers and diagnostic tooling.
    return normalize_git_patch(prod_diff), "prod_diff"


def _commit_prepared_repo(repo_root: Path, episode_id: str) -> str:
    _run(["git", "add", "-A"], cwd=repo_root)
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "repo-test-benchmark",
        "GIT_AUTHOR_EMAIL": "benchmark@example.invalid",
        "GIT_COMMITTER_NAME": "repo-test-benchmark",
        "GIT_COMMITTER_EMAIL": "benchmark@example.invalid",
    }
    _run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "gc.auto=0",
            "-c",
            "maintenance.auto=false",
            "commit",
            "--allow-empty",
            "-m",
            f"benchmark prepared source: {episode_id}",
        ],
        cwd=repo_root,
        env=commit_env,
    )
    return _run(["git", "rev-parse", "HEAD"], cwd=repo_root).stdout.strip()


def _write_prepared_snapshot_metadata(
    *,
    snapshot_dir: Path,
    episode_id: str,
    repo: str,
    base_commit: str,
    prod_diff: str,
    product_patch: str,
    product_state_source: str,
    metadata: dict[str, Any] | None,
    prod_commits: list[str],
    excluded_paths: list[str] | None,
    prepared_commit: str,
) -> PreparedSnapshot:
    snapshot_metadata = dict(metadata or {})
    snapshot_metadata.update(
        {
            "product_state_source": product_state_source,
            "product_state_policy_version": PRODUCT_STATE_POLICY_VERSION,
            "prod_commits": prod_commits,
            "excluded_product_state_paths": excluded_paths or [],
            "original_prod_diff_sha256": sha256_text(prod_diff),
        }
    )
    snapshot = PreparedSnapshot(
        episode_id=episode_id,
        repo=repo,
        path=snapshot_dir,
        base_commit=base_commit,
        prepared_commit=prepared_commit,
        prod_diff_sha256=sha256_text(product_patch),
        created_at=time.time(),
        metadata=snapshot_metadata,
    )
    (snapshot_dir / "prod.diff").write_text(prod_diff, encoding="utf-8")
    (snapshot_dir / "product_state.diff").write_text(product_patch, encoding="utf-8")
    snapshot.manifest_path.write_text(
        json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return snapshot


def _refresh_existing_snapshot(
    *,
    snapshot: PreparedSnapshot,
    repo_source: Path,
    episode_id: str,
    repo: str,
    base_commit: str,
    prod_diff: str,
    metadata: dict[str, Any] | None,
    prod_commits: list[str],
    excluded_paths: list[str] | None,
) -> PreparedSnapshot:
    """Rebuild tracked source state in place while retaining installed dependencies."""

    repo_root = snapshot.repo_root
    _fetch_commit_if_missing(repo_root, repo_source, base_commit)
    _run(["git", "reset", "--hard", base_commit], cwd=repo_root)
    _clean_untracked(repo_root, DEFAULT_PRESERVED_UNTRACKED)

    product_patch, product_state_source = _product_state_patch(
        repo_root=repo_root,
        repo_source=repo_source,
        base_commit=base_commit,
        prod_diff=prod_diff,
        prod_commits=prod_commits,
        excluded_paths=excluded_paths,
    )
    if product_patch.strip():
        _run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=repo_root,
            input_text=normalize_git_patch(product_patch),
        )
    prepared_commit = _commit_prepared_repo(repo_root, episode_id)
    return _write_prepared_snapshot_metadata(
        snapshot_dir=snapshot.path,
        episode_id=episode_id,
        repo=repo,
        base_commit=base_commit,
        prod_diff=prod_diff,
        product_patch=product_patch,
        product_state_source=product_state_source,
        metadata=metadata,
        prod_commits=prod_commits,
        excluded_paths=excluded_paths,
        prepared_commit=prepared_commit,
    )


def create_prepared_snapshot(
    *,
    repo_source: str | Path,
    output_root: str | Path,
    episode_id: str,
    repo: str,
    base_commit: str,
    prod_diff: str,
    metadata: dict[str, Any] | None = None,
    overwrite: bool = False,
    prod_commits: list[str] | None = None,
    excluded_paths: list[str] | None = None,
) -> PreparedSnapshot:
    """Create a reusable source snapshot at base_commit + prod_diff.

    The snapshot contains a synthetic git commit for the prepared source state. Dependencies are
    intentionally installed by a separate runner step so callers can choose local or Docker install.
    """

    snapshot_dir = Path(output_root).resolve() / episode_id
    repo_root = snapshot_dir / "repo"
    if snapshot_dir.exists():
        if not overwrite:
            raise FileExistsError(snapshot_dir)
        shutil.rmtree(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    repo_source_path = Path(repo_source).resolve()
    # Partial/promisor source mirrors can fail during clone's implicit checkout
    # when a promised blob is fetched lazily. Clone metadata first, then fetch
    # and checkout the exact requested commit explicitly so failures are
    # attributable to the requested revision rather than clone checkout order.
    _run(
        ["git", "clone", "--no-hardlinks", "--no-checkout", str(repo_source_path), str(repo_root)]
    )
    _fetch_commit_if_missing(repo_root, repo_source_path, base_commit)
    resolved_prod_commits = prod_commits or _prod_commits_from_metadata(metadata)
    _run(["git", "checkout", "--detach", base_commit], cwd=repo_root)
    product_patch, product_state_source = _product_state_patch(
        repo_root=repo_root,
        repo_source=repo_source_path,
        base_commit=base_commit,
        prod_diff=prod_diff,
        prod_commits=resolved_prod_commits,
        excluded_paths=excluded_paths,
    )
    if product_patch.strip():
        _run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=repo_root,
            input_text=normalize_git_patch(product_patch),
        )
    prepared_commit = _commit_prepared_repo(repo_root, episode_id)
    return _write_prepared_snapshot_metadata(
        snapshot_dir=snapshot_dir,
        episode_id=episode_id,
        repo=repo,
        base_commit=base_commit,
        prod_diff=prod_diff,
        product_patch=product_patch,
        product_state_source=product_state_source,
        metadata=metadata,
        prod_commits=resolved_prod_commits,
        excluded_paths=excluded_paths,
        prepared_commit=prepared_commit,
    )


def ensure_prepared_snapshot(
    *,
    repo_source: str | Path,
    output_root: str | Path,
    episode_id: str,
    repo: str,
    base_commit: str,
    prod_diff: str,
    metadata: dict[str, Any] | None = None,
    overwrite: bool = False,
    prod_commits: list[str] | None = None,
    excluded_paths: list[str] | None = None,
) -> PreparedSnapshot:
    """Load an existing prepared snapshot or create a new matching one."""

    snapshot_dir = Path(output_root).resolve() / episode_id
    resolved_prod_commits = prod_commits or _prod_commits_from_metadata(metadata)
    repo_source_path = Path(repo_source).resolve()
    expected_source = "prod_diff"
    if snapshot_dir.exists() and not overwrite:
        try:
            snapshot = load_prepared_snapshot(snapshot_dir)
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
            shutil.rmtree(snapshot_dir)
        else:
            expected_original_hash = sha256_text(prod_diff)
            if (
                snapshot.repo == repo
                and snapshot.base_commit == base_commit
                and snapshot.metadata.get("product_state_source", "prod_diff") == expected_source
                and snapshot.metadata.get("original_prod_diff_sha256", snapshot.prod_diff_sha256)
                == expected_original_hash
                and snapshot.metadata.get("prod_commits", []) == resolved_prod_commits
                and snapshot.metadata.get("product_state_policy_version")
                == PRODUCT_STATE_POLICY_VERSION
                and sorted(snapshot.metadata.get("excluded_product_state_paths", []))
                == sorted(excluded_paths or [])
                and snapshot.repo_root.exists()
            ):
                return snapshot
            if (
                snapshot.repo == repo
                and snapshot.base_commit == base_commit
                and snapshot.repo_root.exists()
            ):
                return _refresh_existing_snapshot(
                    snapshot=snapshot,
                    repo_source=repo_source_path,
                    episode_id=episode_id,
                    repo=repo,
                    base_commit=base_commit,
                    prod_diff=prod_diff,
                    metadata=metadata,
                    prod_commits=resolved_prod_commits,
                    excluded_paths=excluded_paths,
                )
            shutil.rmtree(snapshot_dir)

    return create_prepared_snapshot(
        repo_source=repo_source,
        output_root=output_root,
        episode_id=episode_id,
        repo=repo,
        base_commit=base_commit,
        prod_diff=prod_diff,
        metadata=metadata,
        overwrite=overwrite,
        prod_commits=resolved_prod_commits,
        excluded_paths=excluded_paths,
    )


def load_prepared_snapshot(snapshot_dir: str | Path) -> PreparedSnapshot:
    data = json.loads((Path(snapshot_dir) / "manifest.json").read_text(encoding="utf-8"))
    return PreparedSnapshot(
        episode_id=data["episode_id"],
        repo=data["repo"],
        path=Path(data["path"]),
        base_commit=data["base_commit"],
        prepared_commit=data["prepared_commit"],
        prod_diff_sha256=data["prod_diff_sha256"],
        created_at=float(data["created_at"]),
        metadata=dict(data.get("metadata") or {}),
    )


DEFAULT_PRESERVED_UNTRACKED = [
    "node_modules",
    ".pnpm-store",
    ".yarn",
    ".cache",
    ".turbo",
    ".next/cache",
    ".benchmark",
]

DEPENDENCY_MANIFEST_NAMES = {
    "package.json",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lock",
    "bun.lockb",
    "Gemfile",
    "Gemfile.lock",
    "requirements.txt",
    "pyproject.toml",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "go.mod",
    "go.sum",
    "Cargo.toml",
    "Cargo.lock",
    "composer.json",
    "composer.lock",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "gradle.lockfile",
    "mix.exs",
    "mix.lock",
    "pubspec.yaml",
    "pubspec.lock",
    "setup.py",
    "setup.cfg",
}


def _should_preserve_untracked(rel_path: str, preserve_patterns: list[str]) -> bool:
    normalized = rel_path.strip("/")
    parts = Path(normalized).parts
    for pattern in preserve_patterns:
        cleaned = pattern.strip("/")
        if not cleaned:
            continue
        if normalized == cleaned or normalized.startswith(cleaned + "/"):
            return True
        if "/" not in cleaned and cleaned in parts:
            return True
        if fnmatch(normalized, cleaned) or fnmatch(normalized, cleaned + "/**"):
            return True
    return False


def dependency_manifests_unchanged(
    repo_root: str | Path, old_commit: str, new_commit: str
) -> bool:
    """Return whether two prepared commits have identical dependency manifests."""

    proc = subprocess.run(
        ["git", "diff", "--name-only", "-z", old_commit, new_commit],
        cwd=str(repo_root),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return False
    changed_paths = [path for path in proc.stdout.decode("utf-8").split("\0") if path]
    for rel_path in changed_paths:
        name = Path(rel_path).name
        if name in DEPENDENCY_MANIFEST_NAMES:
            return False
        if name.startswith("requirements") and name.endswith(".txt"):
            return False
    return True


def _clean_untracked(repo_root: Path, preserve_patterns: list[str]) -> None:
    status = _run(
        ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=repo_root
    ).stdout
    for line in status.splitlines():
        if not line.startswith("?? "):
            continue
        rel = line[3:].rstrip("/")
        if _should_preserve_untracked(rel, preserve_patterns):
            continue
        target = repo_root / rel
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)


def reset_prepared_snapshot(
    snapshot: PreparedSnapshot, preserve_untracked: list[str] | None = None
) -> None:
    """Reset source files while retaining dependency caches and install metadata."""

    repo_root = snapshot.repo_root
    install_marker = repo_root / ".benchmark" / "install_state.json"
    install_marker_contents = (
        install_marker.read_bytes() if install_marker.is_file() else None
    )
    _run(["git", "reset", "--hard", snapshot.prepared_commit], cwd=repo_root)
    if install_marker_contents is not None:
        install_marker.parent.mkdir(parents=True, exist_ok=True)
        install_marker.write_bytes(install_marker_contents)
    preserve = preserve_untracked or DEFAULT_PRESERVED_UNTRACKED
    _clean_untracked(repo_root, preserve)


def apply_patch_to_snapshot(snapshot: PreparedSnapshot, patch_text: str) -> None:
    _run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=snapshot.repo_root,
        input_text=normalize_git_patch(patch_text),
    )
