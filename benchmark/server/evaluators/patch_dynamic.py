"""Dynamic patch validation for official server-side scoring."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from benchmark.participant.loaders.dataset import read_jsonl, write_jsonl
from benchmark.server.evaluators.coverage_ucr import evaluate_update_coverage
from benchmark.server.evaluators.dynamic_metadata import dynamic_gold_metadata, metadata_list
from benchmark.server.evaluators.patch_common import (
    PatchGoldCase,
    PatchPrediction,
    load_gold_cases,
    load_predictions,
)
from benchmark.server.evaluators.patch_static import (
    StaticPatchRecord,
    evaluate_patch_static_records,
)
from benchmark.server.tools.dynamic_runner import CommandResult, DynamicTestRunner, TestRunResult
from benchmark.server.tools.prepared_snapshot import (
    PRODUCT_STATE_POLICY_VERSION,
    PreparedSnapshot,
    apply_patch_to_snapshot,
    ensure_prepared_snapshot,
    reset_prepared_snapshot,
)
from benchmark.server.tools.repo_profile import RepoProfile, load_repo_profiles


@dataclass(frozen=True)
class DynamicPatchRecord:
    episode_id: str
    repo: str
    stage: str
    csr_status: str
    tps_status: str
    ucr_status: str = "not_implemented"
    install_results: list[CommandResult] = field(default_factory=list)
    compile_result: CommandResult | None = None
    test_results: list[TestRunResult] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DynamicPatchRecord":
        compile_result = data.get("compile_result")
        return cls(
            episode_id=str(data.get("episode_id") or ""),
            repo=str(data.get("repo") or ""),
            stage=str(data.get("stage") or ""),
            csr_status=str(data.get("csr_status") or "not_run"),
            tps_status=str(data.get("tps_status") or "not_run"),
            ucr_status=str(data.get("ucr_status") or "not_implemented"),
            install_results=[
                CommandResult.from_dict(item) for item in data.get("install_results") or []
            ],
            compile_result=(
                CommandResult.from_dict(compile_result)
                if isinstance(compile_result, dict)
                else None
            ),
            test_results=[TestRunResult.from_dict(item) for item in data.get("test_results") or []],
            coverage=dict(data.get("coverage") or {}),
            error=str(data["error"]) if data.get("error") is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "repo": self.repo,
            "stage": self.stage,
            "csr_status": self.csr_status,
            "tps_status": self.tps_status,
            "ucr_status": self.ucr_status,
            "install_results": [item.to_dict() for item in self.install_results],
            "compile_result": self.compile_result.to_dict() if self.compile_result else None,
            "test_results": [item.to_dict() for item in self.test_results],
            "coverage": self.coverage,
            "error": self.error,
        }


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def _snapshot_key(gold: PatchGoldCase) -> str:
    metadata = dynamic_gold_metadata(gold)
    prod_commits = metadata_list(metadata, "prod_commits")
    prod_hash = hashlib.sha256(gold.prod_diff.encode("utf-8")).hexdigest()
    if prod_commits:
        return f"{gold.repo}:{gold.base_commit or ''}:{','.join(prod_commits)}:{prod_hash}"
    return f"{gold.repo}:{gold.base_commit or ''}:{prod_hash}"


def _safe_test_output_name(test_file: str) -> str:
    safe = test_file.replace("\\", "/").replace("/", "__")
    return "".join(char if char.isalnum() or char in {".", "_", "-"} else "_" for char in safe)


def _evaluate_one_dynamic(
    *,
    gold: PatchGoldCase,
    prediction: PatchPrediction,
    static_record: StaticPatchRecord,
    profile: RepoProfile,
    repos_root: Path,
    snapshots_root: Path,
    output_dir: Path,
    install: bool,
    force_install: bool,
    prepared_snapshot: PreparedSnapshot | None = None,
) -> DynamicPatchRecord:
    if static_record.stage != "static_passed":
        return DynamicPatchRecord(
            episode_id=gold.episode_id,
            repo=gold.repo,
            stage=static_record.stage,
            csr_status="not_run",
            tps_status="not_run",
            ucr_status="not_run",
            error=static_record.error,
        )
    if not gold.base_commit:
        return DynamicPatchRecord(
            episode_id=gold.episode_id,
            repo=gold.repo,
            stage="invalid_gold",
            csr_status="not_run",
            tps_status="not_run",
            ucr_status="not_run",
            error="missing base_commit",
        )

    snapshot = None
    install_results: list[CommandResult] = []
    test_results: list[TestRunResult] = []
    compile_result: CommandResult | None = None
    try:
        gold_metadata = dynamic_gold_metadata(gold)
        if prepared_snapshot is not None:
            if (
                prepared_snapshot.episode_id != gold.episode_id
                or prepared_snapshot.repo != gold.repo
                or prepared_snapshot.base_commit != gold.base_commit
                or prepared_snapshot.metadata.get("product_state_policy_version")
                != PRODUCT_STATE_POLICY_VERSION
                or prepared_snapshot.metadata.get("product_state_source") != "prod_diff"
                or prepared_snapshot.metadata.get("original_prod_diff_sha256")
                != hashlib.sha256(gold.prod_diff.encode("utf-8")).hexdigest()
                or prepared_snapshot.metadata.get("prod_commits", [])
                != metadata_list(gold_metadata, "prod_commits")
                or sorted(prepared_snapshot.metadata.get("excluded_product_state_paths", []))
                != sorted(gold.test_files)
            ):
                raise ValueError("supplied prepared snapshot does not match gold case")
            snapshot = prepared_snapshot
        else:
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
        runner_output_dir = output_dir / "runs" / gold.episode_id
        runner = DynamicTestRunner(
            profile=profile, repo_root=snapshot.repo_root, output_dir=runner_output_dir
        )
        reset_prepared_snapshot(snapshot)
        if install:
            install_results = runner.run_install_once(
                marker_payload=runner.install_marker_payload(snapshot.prepared_commit),
                force=force_install,
            )
            if install_results and not all(result.ok for result in install_results):
                return DynamicPatchRecord(
                    episode_id=gold.episode_id,
                    repo=gold.repo,
                    stage="install_failed",
                    csr_status="not_run",
                    tps_status="not_run",
                    ucr_status="not_run",
                    install_results=install_results,
                )

        apply_patch_to_snapshot(snapshot, prediction.test_patch)

        compile_validation = runner.run_compile()
        compile_result = compile_validation.result
        csr_status = compile_validation.status
        if csr_status == "failed":
            return DynamicPatchRecord(
                episode_id=gold.episode_id,
                repo=gold.repo,
                stage="compile_failed",
                csr_status=csr_status,
                tps_status="not_run",
                ucr_status="not_run",
                install_results=install_results,
                compile_result=compile_result,
            )

        for test_file in gold.test_files:
            test_runner = DynamicTestRunner(
                profile=profile,
                repo_root=snapshot.repo_root,
                output_dir=runner_output_dir / "tests" / _safe_test_output_name(test_file),
            )
            test_results.append(test_runner.run_test_file(test_file, install=False))
        tests_passed = bool(test_results) and all(
            result.status == "passed" for result in test_results
        )
        tps_status = "passed" if tests_passed else "failed"
        ucr_status = "not_run"
        coverage: dict[str, Any] = {"status": "not_run"}
        if tests_passed:
            ucr_result = evaluate_update_coverage(
                prod_diff=gold.prod_diff,
                prod_files=gold.prod_files,
                repo_root=snapshot.repo_root,
                coverage_root=runner_output_dir,
            )
            ucr_result.coverage["coverage_target_source"] = "gold"
            ucr_status = ucr_result.ucr_status
            coverage = ucr_result.coverage
        return DynamicPatchRecord(
            episode_id=gold.episode_id,
            repo=gold.repo,
            stage="passed" if tests_passed else "test_failed",
            csr_status=csr_status,
            tps_status=tps_status,
            ucr_status=ucr_status,
            install_results=install_results,
            compile_result=compile_result,
            test_results=test_results,
            coverage=coverage,
        )
    except Exception as exc:  # noqa: BLE001 - evaluator records per-case infrastructure failures.
        return DynamicPatchRecord(
            episode_id=gold.episode_id,
            repo=gold.repo,
            stage="error",
            csr_status="not_run",
            tps_status="not_run",
            ucr_status="not_run",
            install_results=install_results,
            compile_result=compile_result,
            test_results=test_results,
            error=str(exc),
        )
    finally:
        if snapshot is not None:
            reset_prepared_snapshot(snapshot)


def summarize_dynamic_records(
    records: list[DynamicPatchRecord], gold_cases: dict[str, PatchGoldCase]
) -> dict[str, Any]:
    positive_count = sum(case.is_positive for case in gold_cases.values())
    dynamic_attempts = [
        record
        for record in records
        if record.stage not in {"intent_fn", "intent_fp", "intent_tn", "locator_unresolved"}
    ]
    compile_configured = [
        record for record in dynamic_attempts if record.csr_status in {"passed", "failed"}
    ]
    test_attempts = [
        record for record in dynamic_attempts if record.tps_status in {"passed", "failed"}
    ]
    passed = [record for record in records if record.stage == "passed"]
    ucr_attempts = [
        record for record in dynamic_attempts if record.ucr_status in {"passed", "failed"}
    ]
    ucr_passed = [record for record in records if record.ucr_status == "passed"]
    stage_counts: dict[str, int] = {}
    coverage_status_counts: dict[str, int] = {}
    for record in records:
        stage_counts[record.stage] = stage_counts.get(record.stage, 0) + 1
    for record in dynamic_attempts:
        coverage_status = str(record.coverage.get("status") or record.ucr_status or "not_run")
        coverage_status_counts[coverage_status] = coverage_status_counts.get(coverage_status, 0) + 1
    coverage_evaluable = [
        record
        for record in ucr_attempts
        if record.coverage.get("status") in {"parsed", "parsed_file", "parsed_file_fallback"}
    ]
    return {
        "evaluated_count": len(records),
        "gold_positive_count": positive_count,
        "dynamic_attempt_count": len(dynamic_attempts),
        "csr": (
            sum(record.csr_status == "passed" for record in compile_configured)
            / len(compile_configured)
            if compile_configured
            else None
        ),
        "csr_configured_count": len(compile_configured),
        "tps": _safe_div(
            sum(record.tps_status == "passed" for record in test_attempts), len(test_attempts)
        ),
        "tps_attempt_count": len(test_attempts),
        "ucr": _safe_div(len(ucr_passed), positive_count),
        "update_accuracy": _safe_div(len(ucr_passed), positive_count),
        "ucr_attempt_count": len(ucr_attempts),
        "coverage_evaluable_count": len(coverage_evaluable),
        "ucr_evaluable": (
            len(ucr_passed) / len(coverage_evaluable) if coverage_evaluable else None
        ),
        "coverage_status": "implemented",
        "coverage_status_counts": coverage_status_counts,
        "end_to_end_success_rate": _safe_div(len(passed), positive_count),
        "stage_counts": stage_counts,
    }


def _load_existing_dynamic_records(
    records_path: str | Path | None,
) -> dict[str, DynamicPatchRecord]:
    if records_path is None:
        return {}
    path = Path(records_path)
    if not path.exists():
        return {}
    records: dict[str, DynamicPatchRecord] = {}
    for row in read_jsonl(path):
        record = DynamicPatchRecord.from_dict(row)
        if record.episode_id:
            records[record.episode_id] = record
    return records


def _write_dynamic_progress(
    *,
    records: list[DynamicPatchRecord],
    gold_cases: dict[str, PatchGoldCase],
    records_path: str | Path | None,
    output_path: str | Path | None,
) -> dict[str, Any]:
    records.sort(key=lambda record: (record.repo, record.episode_id))
    metrics = summarize_dynamic_records(records, gold_cases)
    if records_path is not None:
        write_jsonl(records_path, [record.to_dict() for record in records])
    if output_path is not None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return metrics


def _can_reuse_dynamic_record(record: DynamicPatchRecord) -> bool:
    return record.ucr_status != "not_implemented"


def evaluate_patch_dynamic(
    *,
    gold_path: str | Path,
    predictions_path: str | Path,
    repos_root: str | Path,
    profiles_dir: str | Path,
    snapshots_root: str | Path,
    output_dir: str | Path,
    install: bool = True,
    force_install: bool = False,
    max_workers: int = 1,
    output_path: str | Path | None = None,
    records_path: str | Path | None = None,
) -> dict[str, Any]:
    gold_cases = load_gold_cases(gold_path)
    predictions = load_predictions(predictions_path)
    static_records = evaluate_patch_static_records(
        gold_cases=gold_cases,
        predictions=predictions,
        repos_root=repos_root,
    )
    static_by_id = {record.episode_id: record for record in static_records}
    profiles = load_repo_profiles(profiles_dir)
    repos_root_path = Path(repos_root)
    snapshots_root_path = Path(snapshots_root)
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    existing_records = _load_existing_dynamic_records(records_path)
    records_by_id: dict[str, DynamicPatchRecord] = {}
    runnable: list[tuple[PatchGoldCase, PatchPrediction, StaticPatchRecord, RepoProfile]] = []
    for episode_id, gold in sorted(gold_cases.items()):
        existing_record = existing_records.get(episode_id)
        if (
            existing_record is not None
            and not force_install
            and _can_reuse_dynamic_record(existing_record)
        ):
            records_by_id[episode_id] = existing_record
            continue

        prediction = predictions.get(episode_id)
        static_record = static_by_id[episode_id]
        profile = profiles.get(gold.repo)
        if prediction is None or not prediction.is_positive or not gold.is_positive:
            records_by_id[episode_id] = DynamicPatchRecord(
                episode_id=episode_id,
                repo=gold.repo,
                stage=static_record.stage,
                csr_status="not_run",
                tps_status="not_run",
                ucr_status="not_run",
                error=static_record.error,
            )
            continue
        if profile is None:
            records_by_id[episode_id] = DynamicPatchRecord(
                episode_id=episode_id,
                repo=gold.repo,
                stage="missing_profile",
                csr_status="not_run",
                tps_status="not_run",
                ucr_status="not_run",
                error=f"missing repo profile: {gold.repo}",
            )
            continue
        runnable.append((gold, prediction, static_record, profile))

    _write_dynamic_progress(
        records=list(records_by_id.values()),
        gold_cases=gold_cases,
        records_path=records_path,
        output_path=output_path,
    )

    if max_workers <= 1:
        for gold, prediction, static_record, profile in runnable:
            records_by_id[gold.episode_id] = _evaluate_one_dynamic(
                gold=gold,
                prediction=prediction,
                static_record=static_record,
                profile=profile,
                repos_root=repos_root_path,
                snapshots_root=snapshots_root_path,
                output_dir=output_dir_path,
                install=install,
                force_install=force_install,
            )
            _write_dynamic_progress(
                records=list(records_by_id.values()),
                gold_cases=gold_cases,
                records_path=records_path,
                output_path=output_path,
            )
    else:
        grouped: dict[
            str, list[tuple[PatchGoldCase, PatchPrediction, StaticPatchRecord, RepoProfile]]
        ] = {}
        for item in runnable:
            # A repo profile can reset shared services and reuse fixed ports.
            # Keep episodes from one repo on the same worker; only different
            # repos may execute concurrently.
            grouped.setdefault(item[0].repo, []).append(item)

        records_lock = Lock()

        def run_group(
            items: list[tuple[PatchGoldCase, PatchPrediction, StaticPatchRecord, RepoProfile]],
        ) -> list[DynamicPatchRecord]:
            group_records: list[DynamicPatchRecord] = []
            for gold, prediction, static_record, profile in items:
                record = _evaluate_one_dynamic(
                    gold=gold,
                    prediction=prediction,
                    static_record=static_record,
                    profile=profile,
                    repos_root=repos_root_path,
                    snapshots_root=snapshots_root_path,
                    output_dir=output_dir_path,
                    install=install,
                    force_install=force_install,
                )
                group_records.append(record)
                # Persist each completed episode so an interrupted repo group
                # resumes from the last finished episode instead of rerunning
                # the whole group.
                with records_lock:
                    records_by_id[record.episode_id] = record
                    _write_dynamic_progress(
                        records=list(records_by_id.values()),
                        gold_cases=gold_cases,
                        records_path=records_path,
                        output_path=output_path,
                    )
            return group_records

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(run_group, items) for items in grouped.values()]
            for future in as_completed(futures):
                future.result()

    return _write_dynamic_progress(
        records=list(records_by_id.values()),
        gold_cases=gold_cases,
        records_path=records_path,
        output_path=output_path,
    )
