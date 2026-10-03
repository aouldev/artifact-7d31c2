#!/usr/bin/env python3
"""Exercise the official evaluator with a local Git/Node fixture and no API calls."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


PUBLIC_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("Output directory must be new")
    if output.is_relative_to(PUBLIC_ROOT):
        raise ValueError("Output must be outside the artifact checkout")
    node = shutil.which("node")
    if node is None or shutil.which("git") is None:
        raise RuntimeError("Git and Node.js must be on PATH")
    output.mkdir(parents=True)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["REPO_TEST_EVOLUTION_TOOL_CACHE"] = str(output / "tool_cache")

    def run(command: list[str], cwd: Path = PUBLIC_ROOT) -> str:
        completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                                   text=True, timeout=120)
        if completed.returncode:
            raise RuntimeError(f"Command failed: {command[0]}\n{completed.stderr}")
        return completed.stdout

    repo = output / "repos/demo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    production = repo / "src/calc.cjs"
    test = repo / "tests/calc.test.cjs"
    production.write_text("exports.value = () => 1;\n")
    base_test = ("const assert = require('node:assert/strict');\n"
                 "const { value } = require('../src/calc.cjs');\n"
                 "assert.equal(value(), 1);\n")
    test.write_text(base_test)
    run(["git", "init", "-q"], repo)
    run(["git", "add", "."], repo)
    run(["git", "-c", "user.name=Artifact Demo", "-c",
         "user.email=demo@example.invalid", "commit", "-qm", "Fixture base"], repo)
    base = run(["git", "rev-parse", "HEAD"], repo).strip()
    production.write_text("exports.value = () => 2;\n")
    prod_diff = run(["git", "diff", "--", "src/calc.cjs"], repo)
    production.write_text("exports.value = () => 1;\n")

    def patch(content: str) -> str:
        test.write_text(content)
        diff = run(["git", "diff", "--", "tests/calc.test.cjs"], repo)
        test.write_text(base_test)
        return diff

    good = patch(base_test.replace("value(), 1", "value(), 2"))
    assertion_failure = patch(base_test.replace("value(), 1", "value(), 3"))
    syntax_failure = patch(base_test.replace("value(), 1);", "value(), ); }"))
    unrelated = patch("const assert = require('node:assert/strict');\nassert.ok(true);\n")
    cases = {
        "success": ("positive", "positive", good, "passed", "passed", "passed", "passed"),
        "test_failure": ("positive", "positive", assertion_failure, "test_failed", "passed", "failed", "not_run"),
        "compile_failure": ("positive", "positive", syntax_failure, "compile_failed", "failed", "not_run", "not_run"),
        "coverage_failure": ("positive", "positive", unrelated, "passed", "passed", "passed", "failed"),
        "patch_missing": ("positive", "positive", "", "patch_missing", "not_run", "not_run", "not_run"),
        "apply_failure": ("positive", "positive", "invalid patch\n", "apply_failed", "not_run", "not_run", "not_run"),
        "intent_fn": ("positive", "negative", "", "intent_fn", "not_run", "not_run", "not_run"),
        "intent_tn": ("negative", "negative", "", "intent_tn", "not_run", "not_run", "not_run"),
        "intent_fp": ("negative", "positive", good, "intent_fp", "not_run", "not_run", "not_run"),
        "missing_prediction": ("positive", None, "", "intent_fn", "not_run", "not_run", "not_run"),
    }
    golds, predictions = [], []
    for name, (gold, predicted, test_patch, *_) in cases.items():
        identity = {"episode_id": f"demo__{name}", "repo": "demo", "base_commit": base}
        golds.append({**identity, "schema_version": 1, "kind": "official_gold",
                      "prod_diff": prod_diff, "prod_files": ["src/calc.cjs"],
                      "maintenance_label": gold, "test_patch": good if gold == "positive" else "",
                      "test_files": ["tests/calc.test.cjs"] if gold == "positive" else [], "metadata": {}})
        if predicted is not None:
            predictions.append({**identity, "result": {"maintenance_label": predicted,
                                                        "test_patch": test_patch, "rationale": "Local fixture"}})
    gold_path, prediction_path = output / "gold.jsonl", output / "predictions.jsonl"
    for path, rows in ((gold_path, golds), (prediction_path, predictions)):
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    profiles = output / "profiles"
    profiles.mkdir()
    profile = {"repo": "demo", "validation": {"compile": {
        "argv": [node, "--check", "tests/calc.test.cjs"]}}, "runners": [{
        "name": "node", "argv": [node, "{{test_file}}"],
        "env": {"NODE_V8_COVERAGE": "{{output_dir}}/coverage"}}]}
    (profiles / "demo.json").write_text(json.dumps(profile, indent=2) + "\n")
    common = ["--gold", str(gold_path), "--repos-root", str(output / "repos"),
              "--profiles-dir", str(profiles), "--storage-root", str(output / "storage"),
              "--no-install"]
    evaluation = output / "evaluation"
    run([sys.executable, "-m", "benchmark.server", "eval-submission", *common,
         "--predictions", str(prediction_path), "--output-dir", str(evaluation),
         "--prepare-dynamic", "--max-workers", "2"])
    result = json.loads((evaluation / "result.json").read_text())
    records = [json.loads(line) for line in (evaluation / "dynamic_records.jsonl").read_text().splitlines()]
    actual = {row["episode_id"].removeprefix("demo__"): row for row in records}
    if set(actual) != set(cases):
        raise AssertionError("Evaluator did not account for every fixture case")
    for name, (_, _, _, stage, csr, tps, ucr) in cases.items():
        row = actual[name]
        observed = tuple(row[key] for key in ("stage", "csr_status", "tps_status", "ucr_status"))
        if observed != (stage, csr, tps, ucr):
            raise AssertionError(f"Unexpected {name} result: {observed}")
    native = actual["success"]["coverage"]
    if native["artifact_kind"] != "raw-v8-native":
        raise AssertionError("Success case must use actual native Node coverage")
    if result["metrics"]["decision"]["missing_predictions"] != 1:
        raise AssertionError("Missing prediction was not recorded")
    if run(["git", "status", "--porcelain"], repo).strip():
        raise AssertionError("Evaluation modified the source fixture")
    summary = {"status": "passed", "fixture_cases": len(cases), "model_calls": 0,
               "native_coverage": True, "metrics": result["metrics"],
               "case_statuses": {name: {key: row[key] for key in
                 ("stage", "csr_status", "tps_status", "ucr_status")} for name, row in actual.items()}}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"status": "passed", "fixture_cases": len(cases),
                      "native_coverage": True, "summary": str(output / "summary.json")}))


if __name__ == "__main__":
    main()
