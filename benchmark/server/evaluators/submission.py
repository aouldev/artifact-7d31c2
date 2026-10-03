"""One-command official submission evaluation orchestration."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark.participant.loaders.dataset import read_jsonl
from benchmark.server.evaluators.decision import evaluate_decision
from benchmark.server.evaluators.dynamic_prepare import prepare_dynamic_snapshots
from benchmark.server.evaluators.patch_dynamic import evaluate_patch_dynamic
from benchmark.server.evaluators.patch_static import evaluate_patch_static

SERVER_ROOT = Path(__file__).resolve().parents[1]
SERVER_STORAGE_ROOT = SERVER_ROOT / "storage"
SERVER_RELEASE_DATA_ROOT = SERVER_ROOT / "data/releases"
DEFAULT_PROFILES_DIR = SERVER_ROOT / "repo_profiles" / "generated"


@dataclass(frozen=True)
class SubmissionEvaluationInputs:
    gold_path: Path
    predictions_path: Path
    release_root: Path | None
    submission_dir: Path | None
    submission_id: str


def default_tool_cache_root() -> Path:
    return Path(
        os.environ.get(
            "REPO_TEST_EVOLUTION_TOOL_CACHE",
            str(Path.home() / ".cache" / "repo-test-evolution" / "tools"),
        )
    )


def server_storage_root() -> Path:
    return SERVER_STORAGE_ROOT


def default_profiles_dir() -> Path:
    return DEFAULT_PROFILES_DIR


def default_snapshots_root(storage_root: str | Path | None = None) -> Path:
    configured = os.environ.get("REPO_TEST_EVOLUTION_SNAPSHOTS_ROOT")
    if configured:
        return Path(configured)
    return Path(storage_root or SERVER_STORAGE_ROOT) / "snapshots"


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _release_id(release_root: Path | None, release_id: str | None = None) -> str | None:
    if release_id:
        return release_id
    if release_root is None:
        return None
    manifest = _read_json_if_exists(release_root / "manifest.json") or {}
    value = manifest.get("release_id") or manifest.get("id") or release_root.name
    return str(value)


def _submission_id(submission_dir: Path | None, predictions_path: Path) -> str:
    if submission_dir is not None:
        manifest = _read_json_if_exists(submission_dir / "manifest.json") or {}
        summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
        run_id = summary.get("run_id") or manifest.get("run_id")
        if isinstance(run_id, str) and run_id:
            return run_id
        return submission_dir.name
    return predictions_path.stem


def server_release_gold_path(
    release_id: str,
    server_release_data_root: str | Path = SERVER_RELEASE_DATA_ROOT,
) -> Path:
    return Path(server_release_data_root) / release_id / "validation_gold.jsonl"


def resolve_gold_path(
    *,
    release_root: str | Path | None = None,
    release_id: str | None = None,
    gold_path: str | Path | None = None,
    server_release_data_root: str | Path = SERVER_RELEASE_DATA_ROOT,
) -> Path:
    if gold_path is None:
        if release_id is not None:
            gold_path = server_release_gold_path(release_id, server_release_data_root)
        elif release_root is not None:
            gold_path = Path(release_root) / "private" / "validation_gold.jsonl"
        else:
            raise ValueError("release_id, release_root, or gold_path is required")
    resolved = Path(gold_path)
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    return resolved


def configure_storage_tool_cache(storage_root: str | Path) -> Path:
    cache_root = (Path(storage_root) / "tool_cache").resolve()
    os.environ.setdefault("REPO_TEST_EVOLUTION_TOOL_CACHE", str(cache_root))
    return Path(os.environ["REPO_TEST_EVOLUTION_TOOL_CACHE"])


def resolve_evaluation_inputs(
    *,
    release_root: str | Path | None = None,
    release_id: str | None = None,
    gold_path: str | Path | None = None,
    submission_dir: str | Path | None = None,
    predictions_path: str | Path | None = None,
    server_release_data_root: str | Path = SERVER_RELEASE_DATA_ROOT,
) -> SubmissionEvaluationInputs:
    release_root_path = Path(release_root) if release_root is not None else None
    submission_dir_path = Path(submission_dir) if submission_dir is not None else None

    gold_path = resolve_gold_path(
        release_root=release_root_path,
        release_id=release_id,
        gold_path=gold_path,
        server_release_data_root=server_release_data_root,
    )
    if predictions_path is None:
        if submission_dir_path is None:
            raise ValueError("either submission_dir or predictions_path is required")
        predictions_path = submission_dir_path / "predictions.jsonl"

    gold_path_resolved = Path(gold_path)
    predictions_path_resolved = Path(predictions_path)
    if not predictions_path_resolved.exists():
        raise FileNotFoundError(predictions_path_resolved)

    return SubmissionEvaluationInputs(
        gold_path=gold_path_resolved,
        predictions_path=predictions_path_resolved,
        release_root=release_root_path,
        submission_dir=submission_dir_path,
        submission_id=_submission_id(submission_dir_path, predictions_path_resolved),
    )


def _prediction_count(predictions_path: Path) -> int:
    return len(read_jsonl(predictions_path))


def _artifact_paths(
    output_dir: Path, include_dynamic: bool, include_prepare: bool
) -> dict[str, str]:
    artifacts = {
        "result": str(output_dir / "result.json"),
        "decision_metrics": str(output_dir / "decision_metrics.json"),
        "static_metrics": str(output_dir / "static_metrics.json"),
        "static_records": str(output_dir / "static_records.jsonl"),
    }
    if include_prepare:
        artifacts["prepare_summary"] = str(output_dir / "prepare_summary.json")
        artifacts["prepare_records"] = str(output_dir / "prepare_records.jsonl")
    if include_dynamic:
        artifacts["dynamic_metrics"] = str(output_dir / "dynamic_metrics.json")
        artifacts["dynamic_records"] = str(output_dir / "dynamic_records.jsonl")
        artifacts["dynamic_run_dir"] = str(output_dir / "dynamic")
    return artifacts


def evaluate_submission(
    *,
    release_root: str | Path | None = None,
    release_id: str | None = None,
    gold_path: str | Path | None = None,
    submission_dir: str | Path | None = None,
    predictions_path: str | Path | None = None,
    server_release_data_root: str | Path = SERVER_RELEASE_DATA_ROOT,
    repos_root: str | Path = "repos",
    profiles_dir: str | Path | None = None,
    storage_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    snapshots_root: str | Path | None = None,
    dynamic_output_dir: str | Path | None = None,
    run_dynamic: bool = True,
    prepare_dynamic: bool = False,
    install: bool = True,
    force_install: bool = False,
    max_workers: int = 1,
) -> dict[str, Any]:
    """Run the official server evaluation and write a stable result.json."""

    inputs = resolve_evaluation_inputs(
        release_root=release_root,
        release_id=release_id,
        gold_path=gold_path,
        submission_dir=submission_dir,
        predictions_path=predictions_path,
        server_release_data_root=server_release_data_root,
    )
    storage_root_path = Path(storage_root or SERVER_STORAGE_ROOT)
    configure_storage_tool_cache(storage_root_path)
    output_dir_path = Path(
        output_dir or storage_root_path / "evaluated_results" / inputs.submission_id
    )
    output_dir_path.mkdir(parents=True, exist_ok=True)
    snapshots_root_path = Path(snapshots_root or default_snapshots_root(storage_root_path))
    dynamic_output_dir_path = Path(dynamic_output_dir or output_dir_path / "dynamic")
    profiles_dir_path = Path(profiles_dir or DEFAULT_PROFILES_DIR)
    artifacts = _artifact_paths(output_dir_path, run_dynamic, prepare_dynamic)

    prepare_metrics: dict[str, Any] | None = None
    if prepare_dynamic:
        prepare_metrics = prepare_dynamic_snapshots(
            gold_path=inputs.gold_path,
            repos_root=repos_root,
            profiles_dir=profiles_dir_path,
            snapshots_root=snapshots_root_path,
            output_dir=output_dir_path / "prepare",
            install=install,
            force_install=force_install,
            max_workers=max_workers,
            output_path=output_dir_path / "prepare_summary.json",
            records_path=output_dir_path / "prepare_records.jsonl",
        )

    decision_metrics = evaluate_decision(
        gold_path=inputs.gold_path,
        predictions_path=inputs.predictions_path,
        output_path=output_dir_path / "decision_metrics.json",
    )
    static_metrics = evaluate_patch_static(
        gold_path=inputs.gold_path,
        predictions_path=inputs.predictions_path,
        repos_root=repos_root,
        output_path=output_dir_path / "static_metrics.json",
        records_path=output_dir_path / "static_records.jsonl",
    )
    dynamic_metrics: dict[str, Any] | None = None
    if run_dynamic:
        dynamic_metrics = evaluate_patch_dynamic(
            gold_path=inputs.gold_path,
            predictions_path=inputs.predictions_path,
            repos_root=repos_root,
            profiles_dir=profiles_dir_path,
            snapshots_root=snapshots_root_path,
            output_dir=dynamic_output_dir_path,
            install=install,
            force_install=force_install,
            max_workers=max_workers,
            output_path=output_dir_path / "dynamic_metrics.json",
            records_path=output_dir_path / "dynamic_records.jsonl",
        )

    result = {
        "schema_version": 1,
        "kind": "server_evaluation_result",
        "submission_id": inputs.submission_id,
        "release_id": _release_id(inputs.release_root, release_id),
        "inputs": {
            "release_root": str(inputs.release_root) if inputs.release_root is not None else None,
            "release_id": release_id,
            "gold_path": str(inputs.gold_path),
            "submission_dir": (
                str(inputs.submission_dir) if inputs.submission_dir is not None else None
            ),
            "predictions_path": str(inputs.predictions_path),
            "prediction_count": _prediction_count(inputs.predictions_path),
            "repos_root": str(repos_root),
            "profiles_dir": str(profiles_dir_path),
        },
        "storage": {
            "storage_root": str(storage_root_path),
            "evaluated_result_dir": str(output_dir_path),
            "snapshots_root": str(snapshots_root_path),
            "dynamic_run_dir": str(dynamic_output_dir_path) if run_dynamic else None,
            "dependency_cache_root": str(default_tool_cache_root()),
            "node_modules_location": str(
                snapshots_root_path / "<episode_id>" / "repo" / "node_modules"
            ),
        },
        "config": {
            "run_dynamic": run_dynamic,
            "prepare_dynamic": prepare_dynamic,
            "install": install,
            "force_install": force_install,
            "max_workers": max_workers,
        },
        "metrics": {
            "decision": decision_metrics,
            "patch_static": static_metrics,
            "patch_dynamic": dynamic_metrics,
            "prepare_dynamic": prepare_metrics,
        },
        "artifacts": artifacts,
    }
    result_path = output_dir_path / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
