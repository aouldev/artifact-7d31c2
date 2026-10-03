#!/usr/bin/env python3
"""Check RQ2 behavior contracts, source reviews and recorded mutation evidence."""

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/rq2"
CASE = ROOT / "experiments/case_studies/saleor_attribute_choices"
CONTRACT_FIELDS = ("behavior", "trigger", "expected", "violation_example")


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def key(row: dict) -> tuple[str, str]:
    return row["episode_id"], row["behavior_id"]


def check_source(row: dict, episodes: dict) -> None:
    episode = episodes[row["episode_id"]]
    assert row["repo"] == episode["repo"]
    assert row["base_commit"] == episode["base_commit"]
    diff = episode["prod_diff"]
    assert row["prod_diff_sha256"] == sha(diff.encode())
    lines = diff.splitlines()
    assert row["source_anchors"]
    for anchor in row["source_anchors"]:
        number = anchor["diff_line"]
        assert 1 <= number <= len(lines)
        assert anchor["line_sha256"] == sha(lines[number - 1].encode())
        header = next(line for line in reversed(lines[:number]) if line.startswith("diff --git "))
        assert anchor["path"] == header.split(" b/", 1)[1]
    contract = row.get("proposal", row)
    assert all(isinstance(contract[field], str) and contract[field].strip() for field in CONTRACT_FIELDS)


def check_mutation_replay(path: Path, mutations: list[dict]) -> int:
    replay = json.loads(path.read_text())
    assert replay["mode"] == "execute", "Preparation alone is not mutation execution"
    expected = {key(row): row for row in mutations}
    rows = replay["rows"]
    assert rows and len(rows) == len({key(row) for row in rows}), "Empty or duplicate replay cases"
    fields = ("exit_code", "total", "passed", "failed", "runtime_error_suites")
    for row in rows:
        reference = expected[key(row)]
        assert set(row["suite_runs"]) == set(reference["suite_runs"])
        for state in ("normal", "mutant"):
            assert {field: row["admission_probe"][state][field] for field in fields} == reference["admission_probe"][state]
        for suite, runs in row["suite_runs"].items():
            recorded = reference["suite_runs"][suite]
            assert runs["test_files"] == recorded["test_files"]
            assert runs["test_sha256"] == recorded["test_sha256"]
            for state in ("normal", "mutant"):
                actual = runs["states"][state]
                assert {field: actual[field] for field in fields} == recorded[state], (key(row), suite, state)
                assertions = actual["assertions"]
                assert len(assertions) == actual["total"]
                assert Counter(a["status"] for a in assertions) == Counter({
                    "passed": actual["passed"], "failed": actual["failed"]})
                assert all(a["path"] in runs["test_files"] for a in assertions)
            def failed(state):
                return {(a["path"], a["name"]) for a in runs["states"][state]["assertions"] if a["status"] == "failed"}
            assert failed("mutant") - failed("normal") == {
                (a["path"], a["name"]) for a in recorded["newly_failed_assertions"]}
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mutation-replay", type=Path, help="Optional result.json from an executed mutation replay")
    args = parser.parse_args()
    episodes = {row["episode_id"]: row for row in read_jsonl(
        ROOT / "data/k75_evaluation_cohort/validation.jsonl")}
    with (RESULTS / "behavior_labels.csv").open(newline="") as handle:
        labels = {key(row): row for row in csv.DictReader(handle)}
    catalog = read_jsonl(RESULTS / "behavior_catalog.jsonl")
    contracts = {key(row): row for row in catalog}
    assert len(catalog) == len(contracts) == len(labels) == 239
    assert contracts.keys() == labels.keys()
    for row in catalog:
        check_source(row, episodes)
        label = labels[key(row)]
        for field in ("requirement_basis", "responsibility_relation"):
            assert row[field] == label[field]
    cards = read_jsonl(RESULTS / "source_review_cards.jsonl")
    summary = json.loads((RESULTS / "behavior_review_summary.json").read_text())
    assert len(cards) == len({row["card_id"] for row in cards}) == summary["sample"]["behaviors"] == 60
    assert len({row["episode_id"] for row in cards}) == summary["sample"]["episodes"] == 30
    assert len({row["repo"] for row in cards}) == summary["sample"]["repositories"] == 14
    for row in cards:
        check_source(row, episodes)
        assert key(row) in contracts
        assert row["proposal"] == {field: contracts[key(row)][field] for field in CONTRACT_FIELDS}
        assert row["necessity_rationale"] and row["responsibility_rationale"]
    for field, counts in (("review_status", "status_counts"), ("necessity", "necessity_counts"),
                          ("responsibility_relation", "responsibility_counts")):
        assert dict(Counter(row[field] for row in cards)) == summary["source_review"][counts]
    config = json.loads((CASE / "case.json").read_text())
    episode = episodes[config["episode_id"]]
    assert config["base_commit"] == episode["base_commit"]
    assert config["prod_diff_sha256"] == sha(episode["prod_diff"].encode())
    for entry in config["patches"].values():
        assert entry["sha256"] == sha((CASE / entry["path"]).read_bytes())
    mutations = json.loads((RESULTS / "mutation_execution.json").read_text())["rows"]
    summaries = {row["behavior_id"]: row for row in json.loads(
        (RESULTS / "mutation_case_summary.json").read_text())["rows"]}
    assert len(mutations) == len(summaries) == 2
    assert {row["behavior_id"] for row in mutations} == summaries.keys()
    for row in mutations:
        assert row["episode_id"] == config["episode_id"]
        spec = json.loads((CASE / "specs" / (row["behavior_id"] + ".json")).read_text())
        assert spec["admission_status"] == "admitted"
        assert spec["episode_id"] == row["episode_id"] and spec["behavior_id"] == row["behavior_id"]
        assert spec["probe"]["sha256"] == sha((CASE / spec["probe"]["source"]).read_bytes())
        assert row["normal_source_sha256"] == config["normal_source_sha256"]
        assert row["mutant_source_sha256"] == spec["mutant_source_sha256"]
        assert row["admission_probe"]["normal"] == {
            "exit_code": 0, "total": 1, "passed": 1, "failed": 0, "runtime_error_suites": 0}
        assert row["admission_probe"]["mutant"] == {
            "exit_code": 1, "total": 1, "passed": 0, "failed": 1, "runtime_error_suites": 0}
        assert set(row["suite_runs"]) == {"old", "developer", "codex", "opencode"}
        for suite, runs in row["suite_runs"].items():
            assert set(runs["test_files"]) == set(runs["test_sha256"])
            normal, mutant = runs["normal"], runs["mutant"]
            assert normal["total"] == mutant["total"]
            assert normal["passed"] == normal["total"] and normal["failed"] == normal["exit_code"] == 0
            assert normal["runtime_error_suites"] == mutant["runtime_error_suites"] == 0
            assert mutant["passed"] + mutant["failed"] == mutant["total"]
            assert mutant["failed"] == len(runs["newly_failed_assertions"])
            assert mutant["exit_code"] == (1 if mutant["failed"] else 0)
            assert all(assertion["path"] in runs["test_files"] for assertion in runs["newly_failed_assertions"])
            match = re.search(r"(\d+)/(\d+) normal; (\d+)/(\d+) mutant", summaries[row["behavior_id"]]["outcomes"][suite])
            assert tuple(map(int, match.groups())) == (normal["passed"], normal["total"], mutant["passed"], mutant["total"])
    output = {"behavior_contracts": len(catalog),
              "source_anchor_records": sum(len(row["source_anchors"]) for row in catalog),
              "source_review_cards": len(cards), "mutation_cases": len(mutations), "consistent": True}
    if args.mutation_replay:
        output["replayed_mutation_cases"] = check_mutation_replay(args.mutation_replay, mutations)
        output["replay_matches_recorded"] = True
    print(json.dumps(output))


if __name__ == "__main__":
    main()
