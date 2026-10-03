# Online generation

Start with [`PROMPT.md`](PROMPT.md). It is the frozen actor prompt for the
Online condition. [`SEED_PROMPT.md`](SEED_PROMPT.md) defines optional train
memory induction, and [`REFLECTION_PROMPT.md`](REFLECTION_PROMPT.md) defines the
separate validation writer. The host supplies an initial memory catalogue,
exposes it through `memory_read`, and keeps the `memory/` namespace read-only
for the actor. The writer never receives the actor's final answer or generated
files as evidence.

`online_runner.py` is a single-repository actor reference runner. It accepts a
JSON memory catalogue, selects the path-addressed initial records, and runs one
independent actor session per episode. The catalogue is copied into the run
directory for auditability and is not mutated by the actor runner. A host
reflection step may validate and apply accepted updates after an episode.

The runner fixes the client contract to `gpt-5.5` with low reasoning and the
official ChatGPT OAuth provider. Provider switches, custom endpoints, API-key
adapters, automatic recovery, and quality-driven retries are not exposed.
Use `--dry-run` to inspect the memory view, command, and prompt without calling
a model.

```bash
python experiments/agents/online/online_runner.py \
  --agent codex \
  --episodes data/k75_evaluation_cohort/validation.jsonl \
  --repo REPOSITORY_NAME \
  --repos-root /path/to/base-repositories \
  --run-root /tmp/repotem-online-preview \
  --memory-catalog /path/to/initial-memory.json \
  --limit 1 \
  --dry-run
```

For a real stream, remove `--dry-run`, choose a new `--run-root`, and add
`--export-predictions`. The runner saves each workspace patch before resetting
the next episode and writes `RUN_ROOT/REPOSITORY_NAME/predictions.jsonl`.
A client or export failure stops the stream, records its status in
`stream_summary.json`, and exits unsuccessfully. See
[the evaluation workflow](../../../README_REPRODUCE.md).

After an actor episode, `reflection_runner.py` can run the separate writer
against `episode.json`, `input/prod.diff`, retrieved records, and a host-created
base-observation JSON file. It writes a candidate `updates.json` but never
applies it; the host remains responsible for causal and path validation before
updating its memory event store.

The writer supplies the declared output schema through Codex's structured-output
option or includes the schema in the OpenCode request.

The train-seed catalogue, writer validation, and memory event store are
external host inputs. The actor runner holds the supplied catalogue fixed
during its stream; a host implementing per-episode reflection must run the
writer and accept updates before preparing the next actor's catalogue.
Frozen Online result components are listed in `results/rq3/manifest.json`,
with the seed and update protocol in
[`history_and_memory_protocol.md`](../../../docs/history_and_memory_protocol.md).

The workspace server treats each episode's `prod_files` as read-only. Other
paths under `repo/` are eligible non-production workspace files, including
tests and repository support files, when the production change justifies them.
