# Benchmark server

The server reads host Gold and standard participant predictions, then evaluates
binary intent (`positive` / `negative`), patch application and test execution.
Start with [the runnable workflow](../../README_REPRODUCE.md).

## Local verification

From the artifact root, with Python 3.12+, Git and Node.js:

```bash
python experiments/demo/evaluate_submission_demo.py --output /tmp/repotem-evaluator-demo
python experiments/demo/agent_submission_demo.py --output /tmp/repotem-submission-demo
```

Use new directories. The ten-case evaluator fixture exercises success, syntax
and assertion failures, invalid or missing patches, intent errors and missing
changed-file coverage. The two-case submission fixture also tests export from
actor workspaces. Both use synthetic Gold and Node's native coverage without
model calls or upstream dependency installation.

## Evaluate participant predictions

```bash
python -m benchmark.server eval-submission \
  --gold /path/to/host-gold.jsonl \
  --predictions /path/to/predictions.jsonl \
  --repos-root /path/to/base-repositories \
  --profiles-dir benchmark/server/repo_profiles \
  --storage-root /tmp/repotem-evaluator-storage \
  --output-dir /tmp/repotem-evaluation \
  --prepare-dynamic
```

Gold must match the submitted cohort. The source repositories must contain its
base revisions. The profile directory contains per-repository JSON/YAML
configurations; see [their prerequisites](repo_profiles/README.md).
Dependency installation is enabled by default. Use `--no-install` only when
dependencies are already available, or `--force-install` to reinstall them.

Intent metrics cover the whole input. Static and dynamic patch stages operate
on positive-intent true positives. The static stage records patch presence and
application to `base_commit + prod_diff`; it does not use text similarity.
Dynamic records include CSR, TPS, UCR and end-to-end success, with diagnostic
loading and coverage evidence. A profile's build check alone is not sufficient
to establish that the requested tests loaded successfully.

Production state and coverage targets come from Gold `prod_diff` and
`prod_files`. `prod_commits` does not replace them. Participant evaluation
applies the participant patch and uses the Gold test-file targets. By default,
it runs those changed tests rather than the whole suite. The agent workspace
policy additionally prevents edits to episode `prod_files`.

The output contains `result.json`, `decision_metrics.json`, `static_metrics.json`,
`dynamic_metrics.json`, `static_records.jsonl` and `dynamic_records.jsonl`.
Missing profiles and preparation failures appear in the records. Paper
condition denominators and cross-condition comparisons are separate analyses;
use the frozen evidence under `results/` for the reported tables.

## Individual stages and runtime files

Use `python -m benchmark.server COMMAND --help` for `eval-decision`,
`eval-patch-static`, `prepare-dynamic`, `eval-patch-dynamic` and `eval-submission`.
All host data and generated storage can remain outside the checkout.

The evaluator reuses prepared snapshots and package-manager caches.
`REPO_TEST_EVOLUTION_TOOL_CACHE` selects the cache location; dependency and test
commands inherit the caller's environment. No personal executable paths,
package-cache paths or proxy settings are prescribed by the public profiles.
Old snapshots created under a different preparation policy must be refreshed.

Hidden validation labels, Gold patches, OAuth files and raw upload logs are
host inputs rather than public artifact contents. The public submission format
is documented in [schema.md](schema.md).
