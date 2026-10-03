#!/usr/bin/env python3
"""Check released RQ2 write classifications and public-plan aggregates."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "results/rq2"
AGENTS = ("codex", "opencode")


def cohort(rows: list[dict]) -> dict:
    return {
        "records": len(rows),
        "episodes": len({r["episode_id"] for r in rows}),
        "repos": len({r["repo"] for r in rows}),
        "production_families": len({r["production_family"] for r in rows}),
        "patch_labels": dict(Counter(r["patch_semantics"] for r in rows)),
    }


def main() -> None:
    timeline = json.loads((ROOT / "file_write_timeline.json").read_text())
    counts = Counter()
    per_agent = {a: Counter() for a in AGENTS}
    pairs = set()
    for row in timeline["rows"]:
        key = (row["agent"], row["episode_id"])
        if key in pairs:
            raise ValueError("Duplicate run in write timeline")
        pairs.add(key)
        events = row["write_events"]
        files = list(dict.fromkeys(e["path"] for e in events))
        kind = "no_write" if not events else "single_write" if len(events) == 1 else "same_file_followup" if len(files) == 1 else "new_file_followup"
        assert row["successful_write_count"] == len(events)
        assert row["successful_write_files"] == files
        assert row["classification"] == kind
        counts[kind] += 1
        per_agent[row["agent"]][kind] += 1
    summary = {
        "total_runs": len(pairs),
        "no_write": counts["no_write"],
        "with_writes": len(pairs) - counts["no_write"],
        **{k: counts[k] for k in ("single_write", "same_file_followup", "new_file_followup")},
        "continued_after_first_write": counts["same_file_followup"] + counts["new_file_followup"],
    }
    assert summary == timeline["summary"]
    process = json.loads((ROOT / "trace_process_summary.json").read_text())
    run_map = {(r["agent"], r["episode_id"]): r for r in process["runs"]}
    assert pairs == set(run_map)
    for row in timeline["rows"]:
        assert row["successful_write_count"] == run_map[row["agent"], row["episode_id"]]["successful_write_events"]
    for a in AGENTS:
        assert len([p for p in pairs if p[0] == a]) == 50
        assert 50 - per_agent[a]["no_write"] == process["observable_totals"][a]["made_successful_file_write"]

    plans = [json.loads(line) for line in (ROOT / "public_plan_labels.jsonl").read_text().splitlines()]
    labels = list(csv.DictReader((ROOT / "behavior_labels.csv").open()))
    label_map = {(r["episode_id"], r["behavior_id"]): r for r in labels}
    expected = {(a, e, b) for e, b in label_map for a in AGENTS}
    assert len(plans) == len(expected)
    assert {(r["agent"], r["episode_id"], r["behavior_id"]) for r in plans} == expected
    released = json.loads((ROOT / "public_process_summary.json").read_text())["summary"]
    for r in plans:
        ref = label_map[r["episode_id"], r["behavior_id"]]
        assert r["patch_semantics"] == ref[r["agent"]]
        assert r["developer_patch_semantics"] == ref["developer"]
        evidence = r["evidence"]
        assert r["specific_behavior_stated_before_edit"] == bool(evidence)
        assert r["specific_test_plan_before_edit"] == any(s["kind"] == "plan" for s in evidence)
        assert all(r["first_successful_edit_line"] is None or s["line"] < r["first_successful_edit_line"] for s in evidence)
    for a in AGENTS:
        subset = [r for r in plans if r["agent"] == a]
        derived = {"all": cohort(subset)}
        for field in ("specific_behavior_stated_before_edit", "specific_test_plan_before_edit"):
            selected = [r for r in subset if r[field]]
            derived[field] = cohort(selected)
            derived[field + "_missing"] = cohort([r for r in selected if r["patch_semantics"] == "missing"])
        derived["trace_unavailable"] = cohort([r for r in subset if r["trace_status"] == "unavailable"])
        derived["no_specific_preedit_statement"] = cohort([r for r in subset if r["trace_status"] == "available" and not r["specific_behavior_stated_before_edit"]])
        gaps = [r for r in subset if r["specific_test_plan_before_edit"] and r["patch_semantics"] == "missing"]
        derived["planned_missing_with_successful_edit"] = cohort([r for r in gaps if r["first_successful_edit_line"] is not None])
        derived["planned_missing_with_developer_check"] = cohort([r for r in gaps if r["developer_patch_semantics"] in {"complete", "partial"}])
        assert derived == released[a]
    print(json.dumps({"write_summary": summary, "plan_counts": {a: released[a]["specific_test_plan_before_edit"]["records"] for a in AGENTS}, "planned_missing": {a: released[a]["specific_test_plan_before_edit_missing"]["records"] for a in AGENTS}}))


if __name__ == "__main__":
    main()
