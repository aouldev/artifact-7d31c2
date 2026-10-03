# Local runnable examples

Run from the artifact root with Python 3.12+, Git and Node.js. Install the
package first as described in [the running instructions](../../README_REPRODUCE.md).

```bash
node experiments/demo/pair_records_demo.mjs
python experiments/demo/evaluate_submission_demo.py --output /tmp/repotem-evaluator-demo
python experiments/demo/agent_submission_demo.py --output /tmp/repotem-submission-demo
```

Use new output directories outside the checkout. The construction example
creates a small Git history, pairs synthetic coverage edges with production
changes, and emits an episode. The ten-case evaluator example exercises the
official server's success and failure paths with native Node coverage.

The submission example prepares Codex and OpenCode workspaces through the
Stateless runner's dry-run interface, supplies synthetic final messages and
non-production edits, exports standard predictions, and evaluates both patches.
It checks that the source repository stays unchanged. Its `summary.json`
records `model_calls: 0`; the example verifies plumbing rather than model quality.

These fixtures do not contain private validation answers. Repository-specific
construction workers remain staging-only under `_internal/`.
