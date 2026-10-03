#!/usr/bin/env python3
"""Check released RQ3 components; optionally recompute decisions with host Gold."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = (
    "codex_stateless", "codex_continuous", "codex_online",
    "opencode_stateless", "opencode_continuous", "opencode_online",
)
TRACE_METRICS = (
    "n_tool_calls", "n_successful_tool_calls", "n_failed_tool_calls",
    "n_unfinished_tool_calls", "n_unknown_tool_calls", "train_schema_reads",
    "train_artifact_reads", "train_artifact_searches", "memory_list_calls",
    "memory_file_read_calls", "memory_search_calls", "memory_read_tool_calls",
    "memory_nonempty_content_calls", "memory_content_unknown_calls",
    "memory_write_calls", "memory_delete_calls", "repo_read_calls",
    "input_read_calls", "repo_write_calls", "repo_delete_calls", "unique_repo_read_files",
)
MEMORY_FIELDS = (
    "memory_files_before", "memory_files_after", "memory_files_added",
    "memory_files_deleted", "memory_files_modified",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text())


def keyed(path):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    result = {row["episode_id"]: row for row in rows}
    require(len(rows) == len(result), f"Duplicate episode IDs: {path.name}")
    return result


def close(left, right, message):
    require(math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12), message)


def outcome(gold, predicted):
    return {("positive", "positive"): "TP", ("positive", "negative"): "FN",
            ("negative", "positive"): "FP", ("negative", "negative"): "TN"}[gold, predicted]


def check_generation(directory, manifest, static, cohort):
    summary = read(directory / "generation_metrics.json")
    rq1 = read(ROOT / "results/rq1/stateless_agents/generation_metrics.json")
    require(summary["status"] == "complete", "Dynamic metrics are not complete")
    require(summary["eligible_positive_episodes"] == 49, "Dynamic reference set differs")
    require(set(summary["conditions"]) == set(CONDITIONS), "Dynamic conditions differ")
    rows = [json.loads(line) for line in (directory / "generation_records.jsonl").read_text().splitlines() if line.strip()]
    require(all(set(row) == {"condition", "episode_id", "repo", "base_commit", "csr", "tps", "ucr"}
                for row in rows), "Unexpected dynamic fields")
    require(len(rows) == len({(row["condition"], row["episode_id"]) for row in rows}), "Duplicate dynamic episode")
    require(all(row["condition"] in CONDITIONS and row["episode_id"] in cohort for row in rows), "Unknown dynamic episode")
    require(all((row["repo"], row["base_commit"]) == (cohort[row["episode_id"]]["repo"], cohort[row["episode_id"]]["base_commit"])
                for row in rows), "Dynamic episode identity differs")
    require(all(row[m] in {"passed", "failed"} for row in rows for m in ("csr", "tps", "ucr")), "Unresolved dynamic outcome")
    for condition in CONDITIONS:
        current = summary["conditions"][condition]
        require(current["selected_output_sha256"] == manifest["prediction_sources"][condition]["selected_output_sha256"], "Dynamic prediction source differs")
        cases = [row for row in rows if row["condition"] == condition]
        eligible = set(static["conditions"][condition]["episode_ids"])
        require({row["episode_id"] for row in cases} == eligible, "Dynamic denominator episodes differ")
        require(set(current["metrics"]) == {"csr", "tps", "ucr"}, "Dynamic metric set differs")
        passed = {m: {row["episode_id"] for row in cases if row[m] == "passed"} for m in ("csr", "tps", "ucr")}
        require(passed["ucr"] <= passed["tps"] <= passed["csr"], "Dynamic criterion nesting differs")
        for metric in ("csr", "tps", "ucr"):
            counts = current["metrics"][metric]
            require(counts["complete"] is True and counts["pending"] == 0, "Dynamic metric is pending")
            require(counts["denominator"] == len(eligible) == len(cases), "Dynamic metric denominator differs")
            require(counts["passed"] == len(passed[metric]), "Dynamic pass count differs")
            require(counts["failed"] == len(eligible - passed[metric]), "Dynamic failure count differs")
            close(counts["rate"], counts["passed"] / len(eligible), "Dynamic rate differs")
            if condition.endswith("stateless"):
                agent = rq1["agents"][condition.split("_")[0]]
                require(agent[metric + "_pass_count"] == counts["passed"]
                        and agent[metric + "_denominator"] == counts["denominator"],
                        "RQ1 and RQ3 Stateless counts differ")
                close(agent[metric], counts["rate"], "RQ1 and RQ3 Stateless rates differ")
    require(len(rows) == 253, "Dynamic case count differs")
    return len(rows)


def check(gold_path=None):
    directory = ROOT / "results/rq3"
    manifest = read(directory / "manifest.json")
    for name, record in manifest["files"].items():
        data = (directory / name).read_bytes()
        require(len(data) == record["bytes"], f"Byte count differs: {name}")
        require(hashlib.sha256(data).hexdigest() == record["sha256"], f"Hash differs: {name}")
    cohort = keyed(ROOT / "data/k75_evaluation_cohort/validation.jsonl")
    identification = read(directory / "identification_metrics.json")
    static = read(directory / "static_metrics.json")
    require(len(cohort) == identification["cohort"]["episodes"] == 1673, "Unexpected cohort")
    require(identification["status"] == static["status"] == "complete", "Unexpected component status")
    require(set(identification["conditions"]) == set(static["conditions"]) == set(CONDITIONS), "Condition mismatch")
    gold = keyed(gold_path) if gold_path else None
    if gold is not None:
        require(set(gold) == set(cohort), "Gold cohort differs")
        require(Counter(r["maintenance_label"] for r in gold.values()) == {"positive": 50, "negative": 1623}, "Gold counts differ")
        require(all((r["repo"], r["base_commit"]) == (cohort[eid]["repo"], cohort[eid]["base_commit"])
                    for eid, r in gold.items()), "Gold episode identity differs")
    decisions = {}
    for condition in CONDITIONS:
        current = identification["conditions"][condition]
        path = ROOT / current["decisions"]
        rows = keyed(path)
        require(set(rows) == set(cohort), f"Decision cohort differs: {condition}")
        require(all(set(r) == {"episode_id", "repo", "base_commit", "maintenance_label", "status"}
                    for r in rows.values()), f"Unexpected decision fields: {condition}")
        require(all((r["repo"], r["base_commit"]) == (cohort[eid]["repo"], cohort[eid]["base_commit"])
                    for eid, r in rows.items()), f"Episode identity differs: {condition}")
        require(all(r["maintenance_label"] in {None, "positive", "negative"} for r in rows.values()), "Invalid decision label")
        decisions[condition] = rows
        metrics = current["metrics"]
        tp, fp, tn, fn = (metrics[k] for k in ("TP", "FP", "TN", "FN"))
        require(tp + fn == 50 and tn + fp == 1623 and tp + fp + tn + fn == 1673, "Confusion counts differ")
        require(Counter(r["maintenance_label"] or "negative" for r in rows.values()) == {"positive": tp + fp, "negative": tn + fn}, "Prediction totals differ")
        close(metrics["accuracy"], (tp + tn) / 1673, "Accuracy differs")
        close(metrics["precision_positive"], tp / (tp + fp), "Precision differs")
        close(metrics["recall_positive"], tp / 50, "Recall differs")
        close(metrics["f1_positive"], 2 * tp / (2 * tp + fp + fn), "Positive F1 differs")
        close(metrics["f1_negative"], 2 * tn / (2 * tn + fp + fn), "Negative F1 differs")
        close(metrics["macro_f1"], (metrics["f1_positive"] + metrics["f1_negative"]) / 2, "Macro-F1 differs")
        close(metrics["balanced_accuracy"], (tp / 50 + tn / 1623) / 2, "Balanced accuracy differs")
        missing, skipped = set(current["missing_decision_ids"]), set(current["timeout_skip_ids"])
        require(len(missing) == len(current["missing_decision_ids"]) and missing <= set(rows), "Invalid missing decision IDs")
        require(missing == {eid for eid, r in rows.items() if r["maintenance_label"] is None}, "Recorded missing decisions differ")
        require(skipped <= missing, "Timeout skips must have missing decisions")
        require(all(rows[eid]["maintenance_label"] is None for eid in missing), "Missing decisions must remain null")
        require(all(rows[eid]["status"] == "timeout" for eid in skipped), "Skip status differs")
        conditional = static["conditions"][condition]
        eligible = set(conditional["episode_ids"])
        working = set((ROOT / "results/rq1/conditional_generation_working_set.txt").read_text().splitlines())
        require(eligible == {eid for eid in working if rows[eid]["maintenance_label"] == "positive"}, "Conditional denominator differs")
        require(len(eligible) == conditional["denominator"] == conditional["patch_apply_passed"], "Static counts differ")
        close(conditional["patch_apply_rate"], conditional["patch_apply_passed"] / conditional["denominator"], "Static rate differs")
        if gold is not None:
            computed = Counter(outcome(gold[eid]["maintenance_label"], r["maintenance_label"] or "negative") for eid, r in rows.items())
            require(all(computed[k] == metrics[k] for k in ("TP", "FP", "TN", "FN")), f"Gold scoring differs: {condition}")
    for agent, comparisons in identification["transitions_vs_stateless"].items():
        baseline = decisions[f"{agent}_stateless"]
        for condition, transitions in comparisons.items():
            require(sum(transitions.values()) == 1673, "Transition total differs")
            require(all(sum(n for key, n in transitions.items() if key.split("->")[0] == state)
                        == identification["conditions"][f"{agent}_stateless"]["metrics"][state]
                        for state in ("TP", "FP", "TN", "FN")), "Baseline transition counts differ")
            require(all(sum(n for key, n in transitions.items() if key.split("->")[1] == state)
                        == identification["conditions"][condition]["metrics"][state]
                        for state in ("TP", "FP", "TN", "FN")), "Condition transition counts differ")
            if gold is not None:
                computed = Counter(
                    outcome(gold[eid]["maintenance_label"], baseline[eid]["maintenance_label"] or "negative") + "->" +
                    outcome(gold[eid]["maintenance_label"], decisions[condition][eid]["maintenance_label"] or "negative")
                    for eid in cohort)
                require(dict(computed) == transitions, f"Gold transitions differ: {condition}")
    events = keyed(directory / "continuous_history_events.jsonl")
    require(set(events) == set(cohort), "History-access cohort differs")
    allowed = {"episode_id", "repo", "base_commit", "trace_usable", *TRACE_METRICS, *MEMORY_FIELDS}
    require(all(set(row) == allowed for row in events.values()), "Unexpected history-event fields")
    require(all((row["repo"], row["base_commit"]) == (cohort[eid]["repo"], cohort[eid]["base_commit"])
                for eid, row in events.items()), "History-event identity differs")
    require(all(isinstance(row["trace_usable"], bool) and all(isinstance(row[k], int) and row[k] >= 0
                for k in TRACE_METRICS + MEMORY_FIELDS) for row in events.values()), "Invalid history counts")
    usable = [row for row in events.values() if row["trace_usable"]]
    expected = {
        "scope": "Codex Continuous selected history access", "cohort_n": 1673,
        "trace_usable_n": len(usable),
        "event_incidence": {k: {"episodes": sum(row[k] > 0 for row in usable),
                               "denominator": len(usable), "calls_or_files": sum(row[k] for row in usable)} for k in TRACE_METRICS},
        "memory_state": {k: {"known_n": sum(row[k] is not None for row in events.values()),
                             "nonzero_n": sum(bool(row[k]) for row in events.values())} for k in MEMORY_FIELDS},
    }
    require(expected == read(directory / "continuous_history_access.json"), "History-access summary differs")
    seeds = (directory / manifest["history"]["online_seed_ids"]).read_text().splitlines()
    training = keyed(ROOT / "data/benchmark_release/train.jsonl")
    require(len(seeds) == len(set(seeds)) == manifest["history"]["online_seed_episodes"] == 154, "History seed counts differ")
    require(all(eid in training and training[eid]["gold"]["maintenance_label"] == "positive" for eid in seeds), "History seeds differ from released positive training inputs")
    dynamic_available = manifest["components"]["dynamic_metrics"] == "complete"
    require((directory / "generation_metrics.json").exists() == dynamic_available
            and (directory / "generation_records.jsonl").exists() == dynamic_available,
            "Dynamic component manifest differs")
    dynamic_cases = check_generation(directory, manifest, static, cohort) if dynamic_available else 0
    return {"status": "passed", "conditions": len(CONDITIONS), "decision_rows": 6 * 1673,
            "conditional_static_cases": sum(v["denominator"] for v in static["conditions"].values()),
            "conditional_dynamic_cases": dynamic_cases,
            "history_event_rows": len(events), "history_seed_episodes": len(seeds),
            "gold_scoring_checked": gold is not None,
            "train_schema_read_episodes": expected["event_incidence"]["train_schema_reads"]["episodes"],
            "train_artifact_read_episodes": expected["event_incidence"]["train_artifact_reads"]["episodes"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, help="Optional host-only Gold; never copied or written")
    args = parser.parse_args()
    print(json.dumps(check(args.gold), indent=2))
