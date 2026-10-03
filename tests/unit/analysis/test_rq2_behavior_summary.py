"""RQ2 reproduction accepts roundoff and rejects changed measurements."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "analysis/summarize_rq2_behaviors.py"
SUMMARY = ROOT / "results/rq2/behavior_summary.json"


class BehaviorSummaryCheckTests(unittest.TestCase):
    def check_summary(self, path: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--output", str(path), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_released_summary_reproduces(self):
        result = self.check_summary(SUMMARY)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_changed_missing_share_is_rejected(self):
        summary = json.loads(SUMMARY.read_text())
        summary["paired_comparison"]["differences_agent_minus_developer"]["codex"]["mean"] += 0.0001
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            path.write_text(json.dumps(summary))
            result = self.check_summary(path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(".codex.mean", result.stderr)

    def test_changed_behavior_count_is_rejected(self):
        summary = json.loads(SUMMARY.read_text())
        summary["patch_semantics_counts"]["developer"]["complete"] += 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            path.write_text(json.dumps(summary))
            result = self.check_summary(path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(".developer.complete", result.stderr)


if __name__ == "__main__":
    unittest.main()
