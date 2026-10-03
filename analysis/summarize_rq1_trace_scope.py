#!/usr/bin/env python3
"""Recompute file-access counts from the released RQ1 event records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def analyze_run(run: dict) -> dict:
    paths = {row["path_id"]: row for row in run["paths"]}
    events = run["events"]
    if [event["index"] for event in events] != list(range(len(events))):
        raise ValueError("Tool-call indices must be consecutive and zero-based")
    if any(path_id not in paths for event in events for path_id in event["path_ids"]):
        raise ValueError("Unknown path identifier")

    reads = [e for e in events if e["operation"] == "read" and e["ok"] is True]
    writes = [e for e in events if e["operation"] == "write" and e["ok"] is True]
    searches = [e for e in events if e["operation"] == "search"]

    def classified(event: dict, flag: str) -> bool:
        return any(paths[p][flag] for p in event["path_ids"])

    def first_read(flag: str) -> int | None:
        return next((e["index"] for e in reads if classified(e, flag)), None)

    first_test = first_read("is_test")
    first_write = writes[0]["index"] if writes else None
    end = first_write if first_write is not None else len(events)
    window = [e for e in events if first_test is not None and first_test < e["index"] < end]
    before = {p for e in reads if first_test is not None and e["index"] <= first_test for p in e["path_ids"]}
    after = {p for e in window if e["operation"] == "read" and e["ok"] is True for p in e["path_ids"]}
    new_paths = after - before
    read_paths = {p for e in reads for p in e["path_ids"]}
    written_paths = {p for e in writes for p in e["path_ids"]}
    return {
        "agent": run["agent"],
        "episode_id": run["episode_id"],
        "tool_call_count": len(events),
        "read_event_count": len(reads),
        "search_event_count": len(searches),
        "write_event_count": len(writes),
        "first_production_diff_read": first_read("is_production_diff"),
        "first_changed_production_file_read": first_read("is_changed_production"),
        "first_test_file_read": first_test,
        "first_recorded_test_file_read": first_read("is_recorded_test"),
        "first_write": first_write,
        "distinct_test_files_read": sum(paths[p]["is_test"] for p in read_paths),
        "distinct_test_files_written": sum(paths[p]["is_test"] for p in written_paths),
        "direct_changed_production_file_count": sum(paths[p]["is_changed_production"] for p in read_paths),
        "exploration_after_first_test_before_write": {
            "event_count": len(window),
            "read_count": sum(e["operation"] == "read" and e["ok"] is True for e in window),
            "search_count": sum(e["operation"] == "search" for e in window),
            "distinct_path_count": len({p for e in window for p in e["path_ids"]}),
            "new_distinct_path_count": len(new_paths),
            "new_test_path_count": sum(paths[p]["is_test"] for p in new_paths),
            "changed_production_path_count": sum(paths[p]["is_changed_production"] for p in new_paths),
        },
    }


def summarize(runs: list[dict]) -> dict:
    pairs = [(r["agent"], r["episode_id"]) for r in runs]
    if len(pairs) != len(set(pairs)):
        raise ValueError("Duplicate agent/episode pair")
    agents = sorted({r["agent"] for r in runs})
    episodes = {r["episode_id"] for r in runs}
    if set(pairs) != {(a, e) for a in agents for e in episodes}:
        raise ValueError("Incomplete paired cohort")
    records = [analyze_run(r) for r in runs]
    metrics = {
        "records_read_production_diff": sum(r["first_production_diff_read"] is not None for r in records),
        "records_with_repository_search": sum(r["search_event_count"] > 0 for r in records),
        "records_read_changed_production_file": sum(r["direct_changed_production_file_count"] > 0 for r in records),
        "records_read_any_test_file": sum(r["first_test_file_read"] is not None for r in records),
        "records_read_recorded_test_file": sum(r["first_recorded_test_file_read"] is not None for r in records),
        "records_wrote_test_file": sum(r["distinct_test_files_written"] > 0 for r in records),
    }
    for name, field in (
        ("records_explored_after_test_before_write", "event_count"),
        ("records_read_new_path_after_test", "new_distinct_path_count"),
        ("records_read_new_test_path_after_test", "new_test_path_count"),
        ("records_read_new_changed_production_path_after_test", "changed_production_path_count"),
    ):
        metrics[name] = sum(r["exploration_after_first_test_before_write"][field] > 0 for r in records)
    metrics["records_with_no_post_test_exploration_before_write"] = sum(
        r["first_test_file_read"] is not None and r["first_write"] is not None
        and r["first_test_file_read"] < r["first_write"]
        and r["exploration_after_first_test_before_write"]["event_count"] == 0
        for r in records
    )
    return {
        "schema": "rq1_selected_trace_scope_audit",
        "cohort": {"positive_episodes": len(episodes), "agents": agents, "records": len(records)},
        "records_ok": len(records),
        "missing_traces": [],
        "trace_metrics": metrics,
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "results/rq1/trace_events.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "results/rq1/trace_scope_audit.json")
    parser.add_argument("--check", action="store_true", help="compare with the released summary without writing")
    args = parser.parse_args()
    runs = [json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
    result = summarize(runs)
    if args.check:
        if json.loads(args.output.read_text()) != result:
            raise SystemExit("Trace summary differs from the released event records")
    else:
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(result, indent=2) + "\n")
        temporary.replace(args.output)
    print(json.dumps({"cohort": result["cohort"], "trace_metrics": result["trace_metrics"]}))


if __name__ == "__main__":
    main()
