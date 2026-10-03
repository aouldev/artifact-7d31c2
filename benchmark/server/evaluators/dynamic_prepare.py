"""Prepare reusable dynamic-evaluation snapshots and dependency installs."""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmark.participant.loaders.dataset import write_jsonl
from benchmark.server.evaluators.dynamic_metadata import dynamic_gold_metadata, metadata_list
from benchmark.server.evaluators.patch_common import PatchGoldCase, load_gold_cases
from benchmark.server.tools.dynamic_runner import CommandResult, DynamicTestRunner
from benchmark.server.tools.prepared_snapshot import (
    ensure_prepared_snapshot,
    reset_prepared_snapshot,
)
from benchmark.server.tools.repo_profile import RepoProfile, load_repo_profiles


@dataclass(frozen=True)
class PrepareRecord:
    episode_id: str
    repo: str
    status: str
    snapshot_path: str | None = None
    repo_root: str | None = None
    install_results: list[CommandResult] = field(default_factory=list)
    install_reused: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "repo": self.repo,
            "status": self.status,
            "snapshot_path": self.snapshot_path,
            "repo_root": self.repo_root,
            "install_results": [result.to_dict() for result in self.install_results],
            "install_reused": self.install_reused,
            "error": self.error,
        }


def _prepare_one(
    *,
    gold: PatchGoldCase,
    profile: RepoProfile,
    repos_root: Path,
    snapshots_root: Path,
    output_dir: Path,
    install: bool,
    force_install: bool,
) -> PrepareRecord:
    try:
        gold_metadata = dynamic_gold_metadata(gold)
        snapshot = ensure_prepared_snapshot(
            repo_source=repos_root / gold.repo,
            output_root=snapshots_root,
            episode_id=gold.episode_id,
            repo=gold.repo,
            base_commit=gold.base_commit,
            prod_diff=gold.prod_diff,
            metadata={**gold_metadata, "test_files": gold.test_files},
            prod_commits=metadata_list(gold_metadata, "prod_commits"),
            excluded_paths=gold.test_files,
        )
        reset_prepared_snapshot(snapshot)
        runner = DynamicTestRunner(
            profile=profile,
            repo_root=snapshot.repo_root,
            output_dir=output_dir / "prepare_runs" / gold.episode_id,
        )
        marker_path = runner.install_marker_path()
        if marker_path.exists():
            try:
                marker_data = json.loads(marker_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                marker_data = None
            old_marker_commit = (
                marker_data.get("prepared_commit")
                if isinstance(marker_data, dict)
                else None
            )
            if old_marker_commit and old_marker_commit != snapshot.prepared_commit:
                runner.retarget_install_marker(
                    old_prepared_commit=old_marker_commit,
                    new_prepared_commit=snapshot.prepared_commit,
                )
        install_results: list[CommandResult] = []
        if install:
            install_results = runner.run_install_once(
                marker_payload=runner.install_marker_payload(snapshot.prepared_commit),
                force=force_install,
            )
            if install_results and not all(result.ok for result in install_results):
                return PrepareRecord(
                    episode_id=gold.episode_id,
                    repo=gold.repo,
                    status="install_failed",
                    snapshot_path=str(snapshot.path),
                    repo_root=str(snapshot.repo_root),
                    install_results=install_results,
                )
        marker_reused = False
        if install and not install_results and marker_path.exists():
            try:
                marker_reused = (
                    json.loads(marker_path.read_text(encoding="utf-8"))
                    == runner.install_marker_payload(snapshot.prepared_commit)
                )
            except json.JSONDecodeError:
                marker_reused = False
        reset_prepared_snapshot(snapshot)
        return PrepareRecord(
            episode_id=gold.episode_id,
            repo=gold.repo,
            status="prepared",
            snapshot_path=str(snapshot.path),
            repo_root=str(snapshot.repo_root),
            install_results=install_results,
            install_reused=marker_reused,
        )
    except Exception as exc:  # noqa: BLE001 - preparation records per-case infrastructure failures.
        error = str(exc)
        if isinstance(exc, subprocess.CalledProcessError):
            details = "\n".join(part for part in (exc.stdout, exc.stderr) if part)
            if details:
                error = f"{error}\n{details}"
        return PrepareRecord(
            episode_id=gold.episode_id,
            repo=gold.repo,
            status="error",
            error=error,
        )


def summarize_prepare_records(records: list[PrepareRecord]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for record in records:
        status_counts[record.status] = status_counts.get(record.status, 0) + 1
    return {
        "prepared_count": status_counts.get("prepared", 0),
        "attempt_count": len(records),
        "status_counts": status_counts,
    }


def _validate_source_snapshot_roots(
    *, gold_cases: dict[str, PatchGoldCase], repos_root: Path, snapshots_root: Path
) -> None:
    """Reject overlapping source and output trees before snapshot cleanup can run."""

    snapshot_root = snapshots_root.resolve()
    for gold in gold_cases.values():
        if not gold.is_positive:
            continue
        source_root = (repos_root / gold.repo).resolve()
        if (
            source_root == snapshot_root
            or source_root in snapshot_root.parents
            or snapshot_root in source_root.parents
        ):
            raise ValueError(
                "repository source and prepared snapshot roots overlap: "
                f"source={source_root}, snapshots={snapshot_root}. "
                "Snapshot preparation deletes and rebuilds episode directories; "
                "set --repos-root to an independent checkout directory."
            )


def prepare_dynamic_snapshots(
    *,
    gold_path: str | Path,
    repos_root: str | Path,
    profiles_dir: str | Path,
    snapshots_root: str | Path,
    output_dir: str | Path,
    install: bool = True,
    force_install: bool = False,
    max_workers: int = 1,
    records_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    gold_cases = load_gold_cases(gold_path)
    profiles = load_repo_profiles(profiles_dir)
    repos_root_path = Path(repos_root).resolve()
    snapshots_root_path = Path(snapshots_root).resolve()
    _validate_source_snapshot_roots(
        gold_cases=gold_cases,
        repos_root=repos_root_path,
        snapshots_root=snapshots_root_path,
    )
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    records: list[PrepareRecord] = []
    runnable: list[tuple[PatchGoldCase, RepoProfile]] = []
    for gold in sorted(gold_cases.values(), key=lambda item: (item.repo, item.episode_id)):
        if not gold.is_positive:
            continue
        profile = profiles.get(gold.repo)
        if profile is None:
            records.append(
                PrepareRecord(
                    episode_id=gold.episode_id,
                    repo=gold.repo,
                    status="missing_profile",
                    error=f"missing repo profile: {gold.repo}",
                )
            )
            continue
        runnable.append((gold, profile))

    def run_item(item: tuple[PatchGoldCase, RepoProfile]) -> PrepareRecord:
        gold, profile = item
        return _prepare_one(
            gold=gold,
            profile=profile,
            repos_root=repos_root_path,
            snapshots_root=snapshots_root_path,
            output_dir=output_dir_path,
            install=install,
            force_install=force_install,
        )

    if max_workers <= 1:
        records.extend(run_item(item) for item in runnable)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(run_item, item) for item in runnable]
            for future in as_completed(futures):
                records.append(future.result())

    records.sort(key=lambda record: (record.repo, record.episode_id))
    summary = summarize_prepare_records(records)
    if records_path is not None:
        write_jsonl(records_path, [record.to_dict() for record in records])
    if output_path is not None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return summary
