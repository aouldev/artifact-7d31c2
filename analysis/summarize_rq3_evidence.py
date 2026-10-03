#!/usr/bin/env python3
"""Recompute the released RQ3 cost and history summaries and check case links."""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = tuple(f"{a}_{c}" for a in ("codex", "opencode") for c in ("stateless", "continuous", "online"))


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def describe(values):
    values = [v for v in values if v is not None]
    require(bool(values), "Empty cost observations")
    ordered = sorted(values)
    return {"known_n": len(values), "sum": sum(values), "mean": statistics.mean(values),
            "median": statistics.median(values), "p90": ordered[(90 * len(values) + 99) // 100 - 1]}


def compare(actual, expected):
    require(set(actual) == set(expected), "Cost statistic fields differ")
    require(all(type(expected[k]) in (int, float) and math.isfinite(expected[k]) and
                math.isclose(actual[k], expected[k], rel_tol=1e-12, abs_tol=1e-9)
                for k in actual), "Cost statistic differs from observations")


def check():
    directory = ROOT / "results/rq3"
    manifest = read(directory / "manifest.json")
    for name, expected in manifest["files"].items():
        data = (directory / name).read_bytes()
        require(len(data) == expected["bytes"] and hashlib.sha256(data).hexdigest() == expected["sha256"], "RQ3 evidence hash differs")
    cohort = {r["episode_id"]: r for r in rows(ROOT / "data/k75_evaluation_cohort/validation.jsonl")}
    train = {r["episode_id"]: r for r in rows(ROOT / "data/benchmark_release/train.jsonl")}
    seeds = set((directory / "history_seed_ids.txt").read_text().splitlines())
    cost = read(directory / "cost_metrics.json")
    observations = rows(directory / "cost_records.jsonl")
    require(len(observations) == len({(r["condition"], r["phase"], r["episode_id"]) for r in observations}), "Duplicate cost record")
    require(all(set(r) == {"condition", "phase", "episode_id", "repo", "total_tokens", "time_seconds", "token_included"} for r in observations), "Unexpected cost record fields")
    for r in observations:
        require(r["condition"] in CONDITIONS and r["phase"] in {"actor", "seed", "reflection"}, "Unknown cost scope")
        require(r["phase"] == "actor" or r["condition"].endswith("online"), "Unexpected memory phase")
        source = train if r["phase"] == "seed" else cohort
        require(r["episode_id"] in source and r["repo"] == source[r["episode_id"]]["repo"], "Cost episode identity differs")
        require(type(r["token_included"]) is bool, "Invalid cost inclusion flag")
        require(r["total_tokens"] is None or type(r["total_tokens"]) is int and r["total_tokens"] >= 0, "Invalid token count")
        require(r["time_seconds"] is None or type(r["time_seconds"]) in (int, float) and math.isfinite(r["time_seconds"]) and r["time_seconds"] >= 0, "Invalid elapsed time")
        require(r["token_included"] or r["total_tokens"] is None, "Excluded cumulative tokens retained")
    require(set(cost["conditions"]) == set(CONDITIONS) and set(cost["online_phases"]) == {"codex", "opencode"}, "Cost conditions differ")
    for condition in CONDITIONS:
        current = cost["conditions"][condition]
        require(current["selected_output_sha256"] == manifest["prediction_sources"][condition]["selected_output_sha256"], "Cost prediction source differs")
        values = [r for r in observations if r["condition"] == condition and r["phase"] == "actor"]
        require(len(values) == current["episodes"] == 1673 and {r["episode_id"] for r in values} == set(cohort), "Cost cohort differs")
        denominator = 23 if condition == "codex_continuous" else 1673
        included = [r for r in values if r["token_included"]]
        require(len(included) == current["token_observation_denominator"] == denominator, "Token observation denominator differs")
        require(current["token_observation_unit"] == ("repository_session" if denominator == 23 else "episode"), "Token aggregation unit differs")
        if denominator == 23:
            require(len({r["repo"] for r in included}) == 23, "Persistent sessions do not cover all repositories")
        compare(describe([r["total_tokens"] for r in included]), current["tokens"])
        compare(describe([r["time_seconds"] for r in values]), current["time_seconds"])
    for agent in ("codex", "opencode"):
        current = cost["online_phases"][agent]
        for phase in ("seed", "reflection"):
            values = [r for r in observations if r["condition"] == f"{agent}_online" and r["phase"] == phase]
            expected = seeds if phase == "seed" else set(cohort)
            require(len(values) == current[phase]["calls"] == len(expected) and {r["episode_id"] for r in values} == expected, "Memory cost coverage differs")
            require(all(r["token_included"] for r in values), "Memory phase cost excluded")
            compare(describe([r["total_tokens"] for r in values]), current[phase]["tokens"])
            compare(describe([r["time_seconds"] for r in values]), current[phase]["time_seconds"])
        total = cost["conditions"][f"{agent}_online"]["tokens"]["sum"] + sum(current[p]["tokens"]["sum"] for p in ("seed", "reflection"))
        require(total == current["known_actor_seed_reflection_tokens"], "Combined Online tokens differ")

    history = read(directory / "online_history_coverage.json")
    events = rows(directory / "online_history_events.jsonl")
    positives = set((ROOT / "results/rq1/conditional_generation_working_set.txt").read_text().splitlines()) | {"formbricks__9fddd4dd44da"}
    require(history["reference_episodes"] == 50 and set(history["conditions"]) == {"codex_online", "opencode_online"}, "History reference scope differs")
    require(len(events) == len({(r["condition"], r["episode_id"]) for r in events}) == 100, "Duplicate or missing history record")
    for r in events:
        require(r["condition"] in history["conditions"] and r["episode_id"] in positives, "Unknown history episode")
        require((r["repo"], r["base_commit"]) == (cohort[r["episode_id"]]["repo"], cohort[r["episode_id"]]["base_commit"]), "History identity differs")
        require(type(r["trace_usable"]) is bool, "Invalid history usability")
        fields = ("initial_history_records", "eligible_catalog_records", "changed_path_anchors", "nonempty_history_query_calls")
        if r["trace_usable"]:
            require(all(type(r[k]) is int and r[k] >= 0 for k in fields if k != "changed_path_anchors"), "Invalid history counts")
            require(isinstance(r["changed_path_anchors"], list) and all(isinstance(p, str) and p and not p.startswith("/") and ".." not in Path(p).parts for p in r["changed_path_anchors"]), "Invalid path anchors")
        else:
            require(all(r[k] is None for k in fields), "Unknown history filled with values")
    for condition, current in history["conditions"].items():
        values = [r for r in events if r["condition"] == condition]
        require({r["episode_id"] for r in values} == positives, "History reference IDs differ")
        usable = [r for r in values if r["trace_usable"]]
        expected = {"usable_n": len(usable), "unknown_n": 50 - len(usable),
            "initial_nonempty_n": sum(r["initial_history_records"] > 0 for r in usable),
            "initial_or_retrieved_nonempty_n": sum(r["initial_history_records"] > 0 or r["nonempty_history_query_calls"] > 0 for r in usable),
            "empty_initial_with_catalog_and_no_anchor_n": sum(r["initial_history_records"] == 0 and r["eligible_catalog_records"] > 0 and not r["changed_path_anchors"] for r in usable),
            "selected_output_sha256": manifest["prediction_sources"][condition]["selected_output_sha256"]}
        require(current == expected, "History coverage summary differs")

    case = read(directory / "product_diagnostics_case.json")
    eid = case["episode_id"]
    require(eid == "saleor__a11d1fc587f5" and case["base_commit"] == cohort[eid]["base_commit"], "Case identity differs")
    dynamic = {(r["condition"], r["episode_id"]): r for r in rows(directory / "generation_records.jsonl")}
    require(set(case["conditions"]) == {"codex_stateless", "codex_continuous", "codex_online"}, "Case conditions differ")
    for condition, current in case["conditions"].items():
        require(current["selected_output_sha256"] == manifest["prediction_sources"][condition]["selected_output_sha256"], "Case prediction source differs")
        patch_path = Path(current["patch"])
        require(not patch_path.is_absolute() and ".." not in patch_path.parts, "Unsafe case patch path")
        patch = (ROOT / patch_path).read_text()
        require(sorted(re.findall(r"^diff --git a/.*? b/(.+)$", patch, re.M)) == sorted(current["edited_paths"]), "Case patch paths differ")
        require(current["outcomes"] == {m: dynamic[condition, eid][m] for m in ("csr", "tps", "ucr")}, "Case outcomes differ")
    require(all(set(r["source_episodes"]) <= seeds for r in case["supplied_history"]), "Case history is outside training seeds")
    execution = case["conditions"]["codex_online"]["target_execution"]
    require(execution["passed_tests"] + execution["failed_tests"] == execution["total_tests"] == 12 and execution["failed_tests"] == 2, "Case test counts differ")
    return {"status": "passed", "cost_records": len(observations), "online_history_records": len(events),
            "case_patches": len(case["conditions"]), "online_known_tokens": {a: cost["online_phases"][a]["known_actor_seed_reflection_tokens"] for a in ("codex", "opencode")}}


if __name__ == "__main__":
    print(json.dumps(check(), indent=2))
