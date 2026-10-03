# Continuous generation

Start with [`PROMPT.md`](PROMPT.md). It is the frozen actor prompt for the
Continuous condition. [`experiment_config.json`](experiment_config.json)
records the public protocol boundary.

`continuous_runner.py` is a single-repository reference runner. It materializes
each base snapshot in episode order, keeps one client session, and preserves
`memory/` between episodes. An optional `train/` directory is exposed as a
read-only namespace. [`HISTORY_PROMPT.md`](HISTORY_PROMPT.md) records the
separate historical warmup prompt; the warmup catalogue and repository
snapshots are external inputs and are not bundled with the anonymous artifact.

The runner fixes the reported client contract to `gpt-5.5` with low reasoning
and the official ChatGPT OAuth provider. Provider switches, custom endpoints,
API-key adapters, batch recovery, and quality-driven retries are not exposed.
Use `--dry-run` to inspect the commands and prompts without calling a model.

```bash
python experiments/agents/continuous/continuous_runner.py \
  --agent codex \
  --episodes data/k75_evaluation_cohort/validation.jsonl \
  --repo REPOSITORY_NAME \
  --repos-root /path/to/base-repositories \
  --run-root /tmp/repotem-continuous-preview \
  --limit 1 \
  --dry-run
```

For a real stream, remove `--dry-run`, choose a new `--run-root`, and add
`--export-predictions`. The runner writes
`RUN_ROOT/REPOSITORY_NAME/predictions.jsonl`, saving each patch before the next
episode resets `workspace/repo`. An export or client failure stops the stream,
records its status in `stream_summary.json`, and exits unsuccessfully. Do not
derive an earlier episode's patch from the final shared workspace.

The frozen result components are listed in `results/rq3/manifest.json`.
Historical warmup and batch scheduling remain external host steps; this runner
exposes the single-stream protocol. See
[the evaluation workflow](../../../README_REPRODUCE.md).

The workspace server treats each episode's `prod_files` as read-only. Other
paths under `repo/` are eligible non-production workspace files, including
tests and repository support files, when the production change justifies them.
