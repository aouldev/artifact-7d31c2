"""Strict official patch-evaluation schemas.

Server evaluators intentionally accept only the productized benchmark contract:
JSONL rows emitted by the release builder for private gold and by the participant
runner for predictions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmark.participant.core.models import VALID_MAINTENANCE_LABELS
from benchmark.participant.loaders.dataset import read_jsonl

OFFICIAL_GOLD_SCHEMA_VERSION = 1
OFFICIAL_PREDICTION_SCHEMA_VERSION = 1

OFFICIAL_GOLD_FIELDS = {
    "schema_version",
    "kind",
    "episode_id",
    "repo",
    "base_commit",
    "prod_diff",
    "prod_files",
    "maintenance_label",
    "test_patch",
    "test_files",
    "metadata",
}
OFFICIAL_PREDICTION_FIELDS = {"episode_id", "repo", "base_commit", "result", "metadata"}
OFFICIAL_RESULT_FIELDS = {"maintenance_label", "test_patch", "rationale", "metadata"}


@dataclass(frozen=True)
class PatchGoldCase:
    episode_id: str
    repo: str
    base_commit: str
    maintenance_label: str
    test_patch: str
    test_files: list[str]
    prod_diff: str
    prod_files: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_positive(self) -> bool:
        return self.maintenance_label == "positive"


@dataclass(frozen=True)
class PatchPrediction:
    episode_id: str
    repo: str
    base_commit: str
    maintenance_label: str | None
    test_patch: str
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_positive(self) -> bool:
        return self.maintenance_label == "positive"


def _reject_unknown_fields(row: dict[str, Any], allowed: set[str], row_name: str) -> None:
    unknown = sorted(set(row) - allowed)
    if unknown:
        raise ValueError(f"{row_name} has unknown fields: {unknown}")


def _require_str(row: dict[str, Any], key: str, row_name: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{row_name} requires non-empty string field: {key}")
    return value


def _require_list_of_str(row: dict[str, Any], key: str, row_name: str) -> list[str]:
    value = row.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{row_name} requires list[str] field: {key}")
    return list(value)


def _require_label(value: Any, row_name: str) -> str:
    if value not in VALID_MAINTENANCE_LABELS:
        raise ValueError(f"{row_name} has invalid maintenance_label: {value!r}")
    return str(value)


def _optional_metadata(row: dict[str, Any], row_name: str) -> dict[str, Any]:
    value = row.get("metadata", {})
    if not isinstance(value, dict):
        raise ValueError(f"{row_name} metadata must be an object")
    return dict(value)


def gold_case_from_row(row: dict[str, Any], line_number: int | None = None) -> PatchGoldCase:
    row_name = f"gold row {line_number}" if line_number is not None else "gold row"
    _reject_unknown_fields(row, OFFICIAL_GOLD_FIELDS, row_name)
    schema_version = row.get("schema_version")
    if schema_version != OFFICIAL_GOLD_SCHEMA_VERSION:
        raise ValueError(
            f"{row_name} requires schema_version={OFFICIAL_GOLD_SCHEMA_VERSION}, got {schema_version!r}"
        )
    if row.get("kind") != "official_gold":
        raise ValueError(f"{row_name} requires kind='official_gold'")

    episode_id = _require_str(row, "episode_id", row_name)
    repo = _require_str(row, "repo", row_name)
    base_commit = _require_str(row, "base_commit", row_name)
    maintenance_label = _require_label(row.get("maintenance_label"), row_name)
    prod_diff = _require_str(row, "prod_diff", row_name)
    prod_files = _require_list_of_str(row, "prod_files", row_name)
    test_files = _require_list_of_str(row, "test_files", row_name)
    test_patch = str(row.get("test_patch") or "")

    if maintenance_label == "positive":
        if not test_patch.strip():
            raise ValueError(f"{row_name} positive gold requires non-empty test_patch")
        if not test_files:
            raise ValueError(f"{row_name} positive gold requires non-empty test_files")
    else:
        if test_patch.strip():
            raise ValueError(f"{row_name} negative gold must have empty test_patch")
        if test_files:
            raise ValueError(f"{row_name} negative gold must have empty test_files")

    return PatchGoldCase(
        episode_id=episode_id,
        repo=repo,
        base_commit=base_commit,
        maintenance_label=maintenance_label,
        test_patch=test_patch,
        test_files=test_files,
        prod_diff=prod_diff,
        prod_files=prod_files,
        metadata=_optional_metadata(row, row_name),
        raw=row,
    )


def prediction_from_row(row: dict[str, Any], line_number: int | None = None) -> PatchPrediction:
    row_name = f"prediction row {line_number}" if line_number is not None else "prediction row"
    _reject_unknown_fields(row, OFFICIAL_PREDICTION_FIELDS, row_name)
    episode_id = _require_str(row, "episode_id", row_name)
    repo = _require_str(row, "repo", row_name)
    base_commit = _require_str(row, "base_commit", row_name)
    result = row.get("result")

    if result is None:
        maintenance_label = None
        test_patch = ""
    elif isinstance(result, dict):
        _reject_unknown_fields(result, OFFICIAL_RESULT_FIELDS, f"{row_name} result")
        maintenance_label = _require_label(result.get("maintenance_label"), row_name)
        test_patch = str(result.get("test_patch") or "")
        if maintenance_label == "negative" and test_patch.strip():
            raise ValueError(f"{row_name} negative prediction must have empty test_patch")
    else:
        raise ValueError(f"{row_name} result must be an object or null")

    return PatchPrediction(
        episode_id=episode_id,
        repo=repo,
        base_commit=base_commit,
        maintenance_label=maintenance_label,
        test_patch=test_patch,
        metadata=_optional_metadata(row, row_name),
        raw=row,
    )


def load_gold_cases(gold_path: str | Path) -> dict[str, PatchGoldCase]:
    cases: dict[str, PatchGoldCase] = {}
    for line_number, row in enumerate(read_jsonl(gold_path), start=1):
        case = gold_case_from_row(row, line_number)
        if case.episode_id in cases:
            raise ValueError(f"duplicate gold episode_id: {case.episode_id}")
        cases[case.episode_id] = case
    return cases


def load_predictions(predictions_path: str | Path) -> dict[str, PatchPrediction]:
    predictions: dict[str, PatchPrediction] = {}
    for line_number, row in enumerate(read_jsonl(predictions_path), start=1):
        prediction = prediction_from_row(row, line_number)
        if prediction.episode_id in predictions:
            raise ValueError(f"duplicate prediction episode_id: {prediction.episode_id}")
        predictions[prediction.episode_id] = prediction
    return predictions
