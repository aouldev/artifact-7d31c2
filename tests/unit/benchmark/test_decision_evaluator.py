from __future__ import annotations

import json
from pathlib import Path

from benchmark.server.evaluators.decision import evaluate_decision


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _gold_row(episode_id: str, label: str) -> dict:
    return {
        "schema_version": 1,
        "kind": "official_gold",
        "episode_id": episode_id,
        "repo": "demo",
        "base_commit": f"base-{episode_id}",
        "prod_diff": "diff --git a/src/a.ts b/src/a.ts\n",
        "prod_files": ["src/a.ts"],
        "maintenance_label": label,
        "test_patch": (
            "diff --git a/tests/a.test.ts b/tests/a.test.ts\n" if label == "positive" else ""
        ),
        "test_files": ["tests/a.test.ts"] if label == "positive" else [],
        "metadata": {},
    }


def _prediction_row(episode_id: str, label: str) -> dict:
    return {
        "episode_id": episode_id,
        "repo": "demo",
        "base_commit": f"base-{episode_id}",
        "result": {"maintenance_label": label, "test_patch": ""},
        "metadata": {},
    }


def test_evaluate_decision_uses_binary_maintenance_label(tmp_path: Path) -> None:
    gold_path = tmp_path / "gold.jsonl"
    predictions_path = tmp_path / "predictions.jsonl"
    _write_jsonl(gold_path, [_gold_row("e1", "positive"), _gold_row("e2", "negative")])
    _write_jsonl(
        predictions_path,
        [_prediction_row("e1", "positive"), _prediction_row("e2", "negative")],
    )

    metrics = evaluate_decision(gold_path=gold_path, predictions_path=predictions_path)

    assert metrics["accuracy"] == 1.0
    assert metrics["evaluated_count"] == 2
    assert metrics["invalid_predictions"] == 0


def test_evaluate_decision_rejects_non_binary_labels(tmp_path: Path) -> None:
    gold_path = tmp_path / "gold.jsonl"
    predictions_path = tmp_path / "predictions.jsonl"
    _write_jsonl(gold_path, [_gold_row("e1", "positive")])
    _write_jsonl(
        predictions_path,
        [
            {
                "episode_id": "e1",
                "repo": "demo",
                "base_commit": "base-e1",
                "result": {"maintenance_label": "unsupported"},
                "metadata": {},
            }
        ],
    )

    metrics = evaluate_decision(gold_path=gold_path, predictions_path=predictions_path)

    assert metrics["invalid_predictions"] == 1
    assert metrics["missing_predictions"] == 1
    assert metrics["invalid_prediction_ids"] == ["e1"]
