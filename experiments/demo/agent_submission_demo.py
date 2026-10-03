#!/usr/bin/env python3
"""Verify workspace-to-submission export and evaluation without model calls."""

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
    parser.add_argument("--output", type=Path, required=True, help="New directory outside the checkout")
    output = parser.parse_args().output.resolve()
    if output.exists() or output.is_relative_to(PUBLIC_ROOT):
        raise ValueError("Choose a new output directory outside the artifact checkout")
    if not all(shutil.which(command) for command in ("git", "node")):
        raise RuntimeError("Git and Node.js must be on PATH")
    output.mkdir(parents=True)
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["REPO_TEST_EVOLUTION_TOOL_CACHE"] = str(output / "tool_cache")

    def run(command: list[str], cwd: Path = PUBLIC_ROOT) -> str:
        result = subprocess.run(command, cwd=cwd, env=environment, capture_output=True,
                                text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result.stdout

    repo = output / "repos/demo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    production, test = repo / "src/calc.cjs", repo / "tests/calc.test.cjs"
    production.write_text("exports.value = () => 1;\n")
    base_test = "const assert = require('node:assert/strict');\nconst { value } = require('../src/calc.cjs');\nassert.equal(value(), 1);\n"
    test.write_text(base_test)
    run(["git", "init", "-q"], repo)
    run(["git", "add", "."], repo)
    run(["git", "-c", "user.name=Artifact Demo", "-c", "user.email=demo@example.invalid",
         "commit", "-qm", "Fixture base"], repo)
    base = run(["git", "rev-parse", "HEAD"], repo).strip()
    production.write_text("exports.value = () => 2;\n")
    prod_diff = run(["git", "diff", "--", "src/calc.cjs"], repo)
    production.write_text("exports.value = () => 1;\n")
    test.write_text(base_test.replace("value(), 1", "value(), 2"))
    gold_patch = run(["git", "diff", "--", "tests/calc.test.cjs"], repo)
    test.write_text(base_test)
    episodes, golds = [], []
    for agent in ("codex", "opencode"):
        episode = {"episode_id": f"demo__{agent}", "repo": "demo", "base_commit": base,
                   "prod_files": ["src/calc.cjs"], "prod_diff": prod_diff}
        episodes.append(episode)
        golds.append({**episode, "schema_version": 1, "kind": "official_gold",
                      "maintenance_label": "positive", "test_patch": gold_patch,
                      "test_files": ["tests/calc.test.cjs"], "metadata": {}})
    episode_file, gold_file = output / "episodes.jsonl", output / "gold.jsonl"
    for path, rows in ((episode_file, episodes), (gold_file, golds)):
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    predictions = output / "predictions.jsonl"
    for episode, agent in zip(episodes, ("codex", "opencode")):
        run([sys.executable, "experiments/agents/stateless/stateless_runner.py",
             "--agent", agent, "--episode", str(episode_file), "--episode-id", episode["episode_id"],
             "--repos-root", str(output / "repos"), "--run-root", str(output / "actors"), "--dry-run"])
        case = output / "actors" / episode["episode_id"]
        workspace = case / "workspace/repo"
        (workspace / "tests/calc.test.cjs").write_text(base_test.replace("value(), 1", "value(), 2"))
        (workspace / "tests/support.json").write_text('{"expected":2}\n')
        answer = {"maintenance_label": "positive", "rationale": "Synthetic fixture output"}
        if agent == "codex":
            (case / "last_message.json").write_text(json.dumps(answer))
        else:
            (case / "events.jsonl").write_text(json.dumps({"type": "text", "part": {
                "type": "text", "text": json.dumps(answer)}}) + "\n")
        command = [sys.executable, "experiments/agents/prediction_export.py", "--agent", agent,
                   "--case-dir", str(case), "--source-repo", str(repo), "--output", str(predictions)]
        run(command + (["--append"] if agent == "opencode" else []))
    evaluation = output / "evaluation"
    run([sys.executable, "-m", "benchmark.server", "eval-submission", "--gold", str(gold_file),
         "--predictions", str(predictions), "--repos-root", str(output / "repos"),
         "--profiles-dir", "benchmark/server/repo_profiles", "--storage-root", str(output / "storage"),
         "--output-dir", str(evaluation), "--prepare-dynamic", "--no-install"])
    records = [json.loads(line) for line in (evaluation / "dynamic_records.jsonl").read_text().splitlines()]
    if len(records) != 2 or any(row[key] != "passed" for row in records
                              for key in ("csr_status", "tps_status", "ucr_status")):
        raise AssertionError("Exported fixture submissions did not pass evaluation")
    if run(["git", "status", "--porcelain"], repo).strip():
        raise AssertionError("Source repository changed")
    summary = {"status": "passed", "fixture_cases": 2, "model_calls": 0,
               "actor_output": "synthetic", "native_coverage": True,
               "predictions": str(predictions), "evaluation": str(evaluation)}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
