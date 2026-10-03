"""Submission validation and packaging helpers."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmark.participant.core.models import VALID_MAINTENANCE_LABELS
from benchmark.participant.loaders.dataset import read_jsonl
from benchmark.participant.release import PublicRelease, sha256_file

REQUIRED_RUN_FILES = {"manifest.json", "predictions.jsonl", "summary.json"}


@dataclass
class ValidationReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "stats": self.stats,
        }


def _expected_prediction_count(run_path: Path) -> int | None:
    summary_path = run_path / "summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            value = summary.get("validation_count")
            if isinstance(value, int) and value > 0:
                return value
        except json.JSONDecodeError:
            return None
    config_path = run_path / "config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            value = config.get("limit")
            if isinstance(value, int) and value > 0:
                return value
        except json.JSONDecodeError:
            return None
    return None


def validate_submission(run_dir: str | Path, release: PublicRelease) -> ValidationReport:
    run_path = Path(run_dir)
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, Any] = {"run_dir": str(run_path), "release_id": release.release_id}

    if not run_path.exists():
        return ValidationReport(False, [f"run directory not found: {run_path}"], [], stats)
    if not run_path.is_dir():
        return ValidationReport(False, [f"run path is not a directory: {run_path}"], [], stats)

    for filename in sorted(REQUIRED_RUN_FILES):
        if not (run_path / filename).exists():
            errors.append(f"missing required file: {filename}")

    validation_rows = read_jsonl(release.validation_path)
    expected_ids = [str(row["episode_id"]) for row in validation_rows]
    requested_count = _expected_prediction_count(run_path)
    if requested_count is not None:
        expected_ids = expected_ids[:requested_count]
    expected_id_set = set(expected_ids)
    stats["expected_predictions"] = len(expected_ids)

    predictions_path = run_path / "predictions.jsonl"
    prediction_rows: list[dict[str, Any]] = []
    if predictions_path.exists():
        try:
            prediction_rows = read_jsonl(predictions_path)
        except Exception as exc:  # noqa: BLE001 - report validation failure.
            errors.append(f"failed to read predictions.jsonl: {exc}")
    stats["prediction_count"] = len(prediction_rows)

    seen_ids: list[str] = []
    invalid_labels = 0
    invalid_patch_shapes = 0
    for index, row in enumerate(prediction_rows, start=1):
        episode_id = row.get("episode_id")
        if episode_id is None:
            errors.append(f"prediction row {index} missing episode_id")
            continue
        seen_ids.append(str(episode_id))
        result = row.get("result")
        if result is None:
            errors.append(f"prediction row {index} has null result")
            continue
        if not isinstance(result, dict):
            errors.append(f"prediction row {index} result must be an object")
            continue
        label = result.get("maintenance_label")
        if label not in VALID_MAINTENANCE_LABELS:
            invalid_labels += 1
        test_patch = result.get("test_patch")
        if test_patch is not None and not isinstance(test_patch, str):
            invalid_patch_shapes += 1
        if label == "negative" and result.get("test_patch"):
            invalid_patch_shapes += 1

    seen_counter = Counter(seen_ids)
    duplicates = sorted(episode_id for episode_id, count in seen_counter.items() if count > 1)
    missing = sorted(expected_id_set - set(seen_ids))
    extra = sorted(set(seen_ids) - expected_id_set)

    if duplicates:
        errors.append(f"duplicate episode_id count: {len(duplicates)}")
    if missing:
        errors.append(f"missing prediction count: {len(missing)}")
    if extra:
        errors.append(f"extra prediction count: {len(extra)}")
    if invalid_labels:
        errors.append(f"invalid maintenance_label count: {invalid_labels}")
    if invalid_patch_shapes:
        errors.append(f"invalid test_patch shape count: {invalid_patch_shapes}")

    traces_dir = run_path / "traces"
    trace_count = len(list(traces_dir.glob("*.json"))) if traces_dir.exists() else 0
    if trace_count != len(expected_ids):
        warnings.append(f"trace count is {trace_count}, expected {len(expected_ids)}")

    stats.update(
        {
            "duplicate_count": len(duplicates),
            "missing_count": len(missing),
            "extra_count": len(extra),
            "invalid_label_count": invalid_labels,
            "invalid_patch_shape_count": invalid_patch_shapes,
            "trace_count": trace_count,
            "predictions_sha256": (
                sha256_file(predictions_path) if predictions_path.exists() else None
            ),
        }
    )
    return ValidationReport(ok=not errors, errors=errors, warnings=warnings, stats=stats)


def package_submission(
    run_dir: str | Path,
    release: PublicRelease,
    output: str | Path | None = None,
    include_traces: bool = True,
) -> Path:
    run_path = Path(run_dir)
    report = validate_submission(run_path, release)
    if not report.ok:
        raise ValueError(f"submission is not valid: {report.errors}")

    output_path = Path(output) if output else run_path.with_suffix(".zip")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    package_manifest = {
        "schema_version": 1,
        "kind": "participant_submission_package",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "release_id": release.release_id,
        "run_dir": str(run_path),
        "include_traces": include_traces,
        "validation_report": report.to_dict(),
    }

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in sorted(REQUIRED_RUN_FILES | {"config.json"}):
            path = run_path / filename
            if path.exists():
                archive.write(path, arcname=filename)
        logs_dir = run_path / "logs"
        if logs_dir.exists():
            for path in sorted(logs_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, arcname=str(path.relative_to(run_path)))
        if include_traces:
            traces_dir = run_path / "traces"
            if traces_dir.exists():
                for path in sorted(traces_dir.rglob("*")):
                    if path.is_file():
                        archive.write(path, arcname=str(path.relative_to(run_path)))
        archive.writestr(
            "package_manifest.json", json.dumps(package_manifest, ensure_ascii=False, indent=2)
        )

    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    sidecar = {
        "package": str(output_path),
        "sha256": digest,
        "bytes": output_path.stat().st_size,
        "release_id": release.release_id,
        "include_traces": include_traces,
    }
    output_path.with_suffix(output_path.suffix + ".manifest.json").write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output_path
