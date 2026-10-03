"""Private decision-level evaluator for official benchmark submissions."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from benchmark.participant.loaders.dataset import read_jsonl
from benchmark.server.evaluators.patch_common import gold_case_from_row, prediction_from_row

VALID_DECISION_LABELS = {"positive", "negative"}


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def _binary_metrics(golds: list[str], preds: list[str]) -> dict[str, Any]:
    labels = ["positive", "negative"]
    per_label = {}
    for label in labels:
        tp = sum(g == label and p == label for g, p in zip(golds, preds, strict=True))
        fp = sum(g != label and p == label for g, p in zip(golds, preds, strict=True))
        fn = sum(g == label and p != label for g, p in zip(golds, preds, strict=True))
        precision = _safe_div(tp, tp + fp)
        recall = _safe_div(tp, tp + fn)
        f1 = _safe_div(2 * precision * recall, precision + recall)
        per_label[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": sum(g == label for g in golds),
        }
    accuracy = _safe_div(sum(g == p for g, p in zip(golds, preds, strict=True)), len(golds))
    macro_f1 = sum(per_label[label]["f1"] for label in labels) / len(labels)
    balanced_accuracy = sum(per_label[label]["recall"] for label in labels) / len(labels)
    return {
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "balanced_accuracy": balanced_accuracy,
        "per_label": per_label,
        "gold_counts": dict(Counter(golds)),
        "prediction_counts": dict(Counter(preds)),
    }


def _load_gold_from_private_jsonl(gold_path: str | Path) -> tuple[dict[str, str], dict[str, str]]:
    gold_by_id: dict[str, str] = {}
    repo_by_id: dict[str, str] = {}
    for line_number, row in enumerate(read_jsonl(gold_path), start=1):
        gold = gold_case_from_row(row, line_number)
        if gold.episode_id in gold_by_id:
            raise ValueError(f"duplicate gold episode_id: {gold.episode_id}")
        gold_by_id[gold.episode_id] = gold.maintenance_label
        repo_by_id[gold.episode_id] = gold.repo
    return gold_by_id, repo_by_id


def evaluate_decision(
    predictions_path: str | Path | None = None,
    output_path: str | Path | None = None,
    gold_path: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate participant predictions against strict official private gold."""

    if predictions_path is None:
        raise ValueError("predictions_path is required")
    if gold_path is None:
        raise ValueError("gold_path is required")
    gold_by_id, repo_by_id = _load_gold_from_private_jsonl(gold_path)

    rows = read_jsonl(predictions_path)
    prediction_by_id: dict[str, str | None] = {}
    extra_prediction_ids: list[str] = []
    invalid = 0
    invalid_prediction_ids: list[str] = []
    for line_number, row in enumerate(rows, start=1):
        try:
            prediction = prediction_from_row(row, line_number)
        except ValueError:
            invalid += 1
            episode_id = row.get("episode_id")
            if isinstance(episode_id, str):
                invalid_prediction_ids.append(episode_id)
            continue
        if prediction.episode_id not in gold_by_id:
            extra_prediction_ids.append(prediction.episode_id)
        prediction_by_id[prediction.episode_id] = prediction.maintenance_label

    golds: list[str] = []
    preds: list[str] = []
    missing_prediction_ids: list[str] = []
    per_repo: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"golds": [], "preds": []})
    for episode_id, gold in sorted(gold_by_id.items()):
        pred = prediction_by_id.get(episode_id)
        if pred is None:
            missing_prediction_ids.append(episode_id)
            pred = "negative"
        golds.append(gold)
        preds.append(pred)
        repo = repo_by_id.get(episode_id, "unknown")
        per_repo[repo]["golds"].append(gold)
        per_repo[repo]["preds"].append(pred)

    metrics = _binary_metrics(golds, preds) if golds else {}
    metrics["invalid_predictions"] = invalid
    metrics["invalid_prediction_ids"] = invalid_prediction_ids
    metrics["evaluated_count"] = len(golds)
    metrics["missing_predictions"] = len(missing_prediction_ids)
    metrics["extra_predictions"] = len(extra_prediction_ids)
    metrics["per_repo"] = {
        repo: _binary_metrics(values["golds"], values["preds"])
        for repo, values in sorted(per_repo.items())
    }
    if output_path is not None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return metrics
