"""Static patch analysis for official server-side scoring."""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark.participant.core.paths import get_metadata_path
from benchmark.participant.loaders.dataset import write_jsonl
from benchmark.server.evaluators.patch_common import (
    PatchGoldCase,
    PatchPrediction,
    load_gold_cases,
    load_predictions,
)
from benchmark.server.tools.prepared_snapshot import (
    _fetch_commit_if_missing,
    _product_state_patch,
    normalize_git_patch,
)


@dataclass(frozen=True)
class StaticPatchRecord:
    episode_id: str
    repo: str
    stage: str
    patch_present: bool
    apply_success: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "repo": self.repo,
            "stage": self.stage,
            "patch_present": self.patch_present,
            "apply_success": self.apply_success,
            "error": self.error,
        }


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def _prediction_stage(gold: PatchGoldCase, prediction: PatchPrediction | None) -> str:
    pred_label = prediction.maintenance_label if prediction else None
    if gold.maintenance_label == "positive" and pred_label == "positive":
        return "intent_tp"
    if gold.maintenance_label == "positive":
        return "intent_fn"
    if pred_label == "positive":
        return "intent_fp"
    return "intent_tn"


def _git_with_temp_index(repo_source: Path, index_path: Path, args: list[str]) -> list[str]:
    return [
        "git",
        f"--git-dir={repo_source.resolve() / '.git'}",
        f"--work-tree={repo_source.resolve()}",
        "--literal-pathspecs",
        *args,
    ]


def _metadata_list(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key)
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value:
        return [value]
    return []


def _source_jsonl_candidates() -> list[Path]:
    metadata_root = get_metadata_path()
    # Try the canonical release metadata first, then the historical v1 fallback.
    candidates = [
        metadata_root / "source.jsonl",
        get_metadata_path("repo_test_maintenance_v1") / "source.jsonl",
    ]
    return [p for p in candidates if p.exists()]


def _lookup_source_metadata(episode_id: str) -> dict[str, Any]:
    for path in _source_jsonl_candidates():
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if episode_id not in line:
                    continue
                row = json.loads(line)
                if row.get("episode_id") != episode_id:
                    continue
                metadata: dict[str, Any] = {}
                for key in ("prod_commits", "test_commits"):
                    if key in row:
                        metadata[key] = row[key]
                return metadata
    return {}


def _static_gold_metadata(gold: PatchGoldCase) -> dict[str, Any]:
    metadata = dict(gold.metadata)
    if not _metadata_list(metadata, "prod_commits"):
        metadata.update(_lookup_source_metadata(gold.episode_id))
    return metadata


def _apply_check(
    repo_source: Path,
    base_commit: str,
    prod_diff: str,
    patch_text: str,
    *,
    prod_commits: list[str] | None = None,
    excluded_paths: list[str] | None = None,
) -> tuple[bool, str | None]:
    with tempfile.TemporaryDirectory(prefix="benchmark_static_apply_") as tmp_dir:
        index_path = Path(tmp_dir) / "index"
        env = {"GIT_INDEX_FILE": str(index_path)}
        _fetch_commit_if_missing(repo_source, repo_source, base_commit)
        product_patch, _ = _product_state_patch(
            repo_root=repo_source,
            repo_source=repo_source,
            base_commit=base_commit,
            prod_diff=prod_diff,
            prod_commits=prod_commits or [],
            excluded_paths=excluded_paths,
        )
        read_tree = subprocess.run(
            _git_with_temp_index(repo_source, index_path, ["read-tree", base_commit]),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if read_tree.returncode != 0:
            return False, read_tree.stderr.strip() or read_tree.stdout.strip()
        if product_patch.strip():
            prod_apply = subprocess.run(
                _git_with_temp_index(
                    repo_source, index_path, ["apply", "--cached", "--whitespace=nowarn", "-"]
                ),
                input=normalize_git_patch(product_patch),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if prod_apply.returncode != 0:
                return (
                    False,
                    f"prod_diff_apply_failed: {prod_apply.stderr.strip() or prod_apply.stdout.strip()}",
                )
        patch_apply = subprocess.run(
            _git_with_temp_index(
                repo_source,
                index_path,
                ["apply", "--cached", "--check", "--whitespace=nowarn", "-"],
            ),
            input=normalize_git_patch(patch_text),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if patch_apply.returncode != 0:
            return False, patch_apply.stderr.strip() or patch_apply.stdout.strip()
        return True, None


def evaluate_patch_static_records(
    *,
    gold_cases: dict[str, PatchGoldCase],
    predictions: dict[str, PatchPrediction],
    repos_root: str | Path,
) -> list[StaticPatchRecord]:
    repos_root_path = Path(repos_root)
    records: list[StaticPatchRecord] = []
    for episode_id, gold in sorted(gold_cases.items()):
        prediction = predictions.get(episode_id)
        stage = _prediction_stage(gold, prediction)
        if stage != "intent_tp":
            records.append(
                StaticPatchRecord(
                    episode_id=episode_id,
                    repo=gold.repo,
                    stage=stage,
                    patch_present=bool(prediction and prediction.test_patch.strip()),
                    apply_success=False,
                )
            )
            continue

        assert prediction is not None
        patch_present = bool(prediction.test_patch.strip())
        if not patch_present:
            records.append(
                StaticPatchRecord(
                    episode_id=episode_id,
                    repo=gold.repo,
                    stage="patch_missing",
                    patch_present=False,
                    apply_success=False,
                    error="positive prediction must include test_patch",
                )
            )
            continue
        if not gold.base_commit:
            records.append(
                StaticPatchRecord(
                    episode_id=episode_id,
                    repo=gold.repo,
                    stage="invalid_gold",
                    patch_present=True,
                    apply_success=False,
                    error="missing base_commit",
                )
            )
            continue
        if gold.is_positive and not gold.test_files:
            records.append(
                StaticPatchRecord(
                    episode_id=episode_id,
                    repo=gold.repo,
                    stage="invalid_gold",
                    patch_present=True,
                    apply_success=False,
                    error="positive gold must include test_files",
                )
            )
            continue

        gold_metadata = _static_gold_metadata(gold)
        apply_success, apply_error = _apply_check(
            repos_root_path / gold.repo,
            gold.base_commit,
            gold.prod_diff,
            prediction.test_patch,
            prod_commits=_metadata_list(gold_metadata, "prod_commits"),
            excluded_paths=gold.test_files,
        )
        records.append(
            StaticPatchRecord(
                episode_id=episode_id,
                repo=gold.repo,
                stage="static_passed" if apply_success else "apply_failed",
                patch_present=True,
                apply_success=apply_success,
                error=apply_error,
            )
        )
    return records


def summarize_static_records(records: list[StaticPatchRecord]) -> dict[str, Any]:
    intent_tp = [
        record
        for record in records
        if record.stage in {"static_passed", "apply_failed", "patch_missing", "invalid_gold"}
    ]
    apply_success = [record for record in intent_tp if record.apply_success]
    stage_counts: dict[str, int] = {}
    for record in records:
        stage_counts[record.stage] = stage_counts.get(record.stage, 0) + 1
    return {
        "evaluated_count": len(records),
        "intent_tp_count": len(intent_tp),
        "patch_present_rate": _safe_div(
            sum(record.patch_present for record in intent_tp), len(intent_tp)
        ),
        "patch_apply_rate": _safe_div(len(apply_success), len(intent_tp)),
        "stage_counts": stage_counts,
    }


def evaluate_patch_static(
    *,
    gold_path: str | Path,
    predictions_path: str | Path,
    repos_root: str | Path,
    output_path: str | Path | None = None,
    records_path: str | Path | None = None,
) -> dict[str, Any]:
    gold_cases = load_gold_cases(gold_path)
    predictions = load_predictions(predictions_path)
    records = evaluate_patch_static_records(
        gold_cases=gold_cases,
        predictions=predictions,
        repos_root=repos_root,
    )
    metrics = summarize_static_records(records)
    if records_path is not None:
        write_jsonl(records_path, [record.to_dict() for record in records])
    if output_path is not None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return metrics
