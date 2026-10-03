# Stateless generation

Start with [`PROMPT.md`](PROMPT.md). It is the frozen researcher-authored
prompt used by the stateless generation condition. [`prompt.json`](prompt.json)
records its placeholder, tool set, state boundary, and output schema.

[`experiment_config.json`](experiment_config.json) is the public protocol
boundary. The formal runs used `gpt-5.5`, reasoning effort `low`, one actor call
per episode, and official ChatGPT OAuth for both clients. Provider switching,
custom endpoints, API-key adapters, third-party relays, fallback models, and
quality-driven retries are not part of the reported condition.

Only infrastructure failures may be retried, and a retry is a separate run with
the same episode input and frozen settings. A successful model response is never
rerun because its label or patch quality is inconvenient. The canonical result
files under `results/rq1/stateless_agents/` are the reported merged outputs;
their historical batch and recovery ledgers remain private provenance.

The single-episode runner is intentionally small and has no provider-selection
flag. It needs a public episode, a checked-out base repository, the matching
Codex or OpenCode client, and the client's official authentication state. It
does not include private gold, repository snapshots, credentials, batch
workspaces, or raw event logs.

```bash
python experiments/agents/stateless/stateless_runner.py \
  --agent codex \
  --episode data/k75_evaluation_cohort/validation.jsonl \
  --episode-id REPLACE_WITH_EPISODE_ID \
  --repos-root /path/to/base-repositories \
  --run-root /tmp/repotem-stateless-preview \
  --dry-run
```

For a real call, remove `--dry-run`, choose a new `--run-root`, and add
`--export-predictions`. The runner writes `EPISODE_ID/predictions.jsonl` with
the final decision and a patch derived from the workspace. Export validates
that production files remain unchanged and includes non-production support
edits. Failure records `export_error` and exits unsuccessfully; preview runs
create no submission. See [the evaluation workflow](../../../README_REPRODUCE.md).

The OpenCode path uses the same prompt, schema, tool boundary, model, timeout,
and official `openai` provider. A real OpenCode run copies the supplied official
OAuth `auth.json` into its isolated run state; pass `--opencode-auth` when the
local auth file is not at the standard path. The runner does not expose
Continuous or Online memory flags; their reference runners are documented in
[`continuous/`](../continuous/README.md) and [`online/`](../online/README.md).

The workspace server treats the episode's `prod_files` as read-only. Other
paths under `repo/` are eligible non-production workspace files, including
tests and repository support files, when the production change justifies them.
