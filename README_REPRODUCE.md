# Running the artifact

Run these commands from the artifact root. Python 3.12+, Git and Node.js are
required. The model-free examples use Node's built-in assertions and coverage.

Create an isolated environment before installing. Replace `python3.12` with
another Python 3.12+ interpreter, such as `python3.13`, if needed.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m benchmark info
python -m pytest tests
```

For installation using the supplied dependency lock, `uv sync --frozen --extra dev`
creates the environment; use `uv run --extra dev` before the Python commands below.

`benchmark info` loads 11,823 train and 5,084 validation episodes and checks
that validation inputs contain no answers. The fixed K75 input is under
`data/k75_evaluation_cohort/`; private validation Gold is not included.

## Local examples without model calls

```bash
node experiments/demo/pair_records_demo.mjs
python experiments/demo/evaluate_submission_demo.py --output /tmp/repotem-evaluator-demo
python experiments/demo/agent_submission_demo.py --output /tmp/repotem-submission-demo
```

Choose new output directories outside the checkout. The construction example
creates a small Git history and pairs synthetic coverage edges with changes.
The evaluator example checks ten success and failure cases, including native
Node coverage. The submission example prepares both client workspaces, supplies
synthetic actor output, exports test and support-file edits, and evaluates the
two submissions through the official server. Neither example calls a model or
represents a paper experiment.

## Run an agent and export its submission

Place the episode's source Git repository at `BASE_REPOS/REPOSITORY_NAME`.
It must contain the episode's `base_commit`; the runner archives that revision
into a separate workspace. Install the client used by the selected condition:
Codex CLI 0.144.6 or OpenCode 1.17.18, with official ChatGPT OAuth authentication.
The runners fix `gpt-5.5`, low reasoning, and one actor call per episode.

First inspect the prepared command and prompt:

```bash
python experiments/agents/stateless/stateless_runner.py \
  --agent codex \
  --episode data/k75_evaluation_cohort/validation.jsonl \
  --episode-id EPISODE_ID \
  --repos-root /path/to/base-repositories \
  --run-root /tmp/repotem-stateless-preview \
  --dry-run
```

Run with a new directory and export evaluator input automatically:

```bash
python experiments/agents/stateless/stateless_runner.py \
  --agent codex \
  --episode data/k75_evaluation_cohort/validation.jsonl \
  --episode-id EPISODE_ID \
  --repos-root /path/to/base-repositories \
  --run-root /tmp/repotem-stateless-run \
  --export-predictions
```

The submission is `/tmp/repotem-stateless-run/EPISODE_ID/predictions.jsonl`.
For OpenCode, use `--agent opencode` and, when needed,
`--opencode-auth /path/to/auth.json`. Select an installed client explicitly
with `--codex-bin` or `--opencode-bin`.

For repository streams, use the [Continuous](experiments/agents/continuous/README.md)
or [Online](experiments/agents/online/README.md) runner with
`--export-predictions`. Each writes `RUN_ROOT/REPOSITORY_NAME/predictions.jsonl`
before resetting the shared workspace for the next episode. Continuous retains
one client session; Online uses independent actor sessions and a host-supplied
memory catalogue. Reflection and acceptance of memory updates are separate host
steps described in the Online instructions.

The exporter derives `result.test_patch` from workspace edits relative to the
base revision, including justified non-production support files. It rejects
changes to `prod_files`, invalid final output, duplicate episodes, and negative
decisions with edits. A failed export stops the run and records `export_error`;
a dry run creates no predictions. Local run directories contain raw events and
may contain copied OAuth state; publish only the intended submission files.

An existing Stateless run can also be exported separately:

```bash
python experiments/agents/prediction_export.py \
  --agent codex \
  --case-dir /path/to/stateless-run/EPISODE_ID \
  --source-repo /path/to/base-repositories/REPOSITORY_NAME \
  --output /tmp/repotem-predictions.jsonl
```

## Evaluate a submission

The server evaluates one standard submission at a time. Supply the matching
host Gold, source repositories, and an applicable repository profile:

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

This writes `result.json`, separate decision/static/dynamic metrics and
per-episode records. Installation is enabled by default; `--no-install` is for
snapshots whose dependencies are already prepared. See the
[profile instructions](benchmark/server/repo_profiles/README.md) for the 18
repository configurations, required services and environment inputs. These
configurations require the matching upstream versions and dependencies;
missing profiles or failed preparation are reported explicitly.

## Check the released paper evidence

```bash
python analysis/summarize_rq1_trace_scope.py --check
python analysis/summarize_rq2_behaviors.py --check
python analysis/check_rq2_process.py
python analysis/check_rq2_details.py
python analysis/check_rq3_results.py
python analysis/summarize_rq3_evidence.py
python analysis/figures/plot_characterization.py --output-dir /tmp/repotem-figures
python analysis/figures/plot_locator.py --output-dir /tmp/repotem-figures
```

`results/` contains characterization, locator, RQ1, RQ2 and the complete RQ3
components listed in `results/rq3/manifest.json`. Paper-specific aggregation is
separate from the server's submission evaluator. RQ1 prior-method values are
frozen records; their environment-specific adapters are not public rerun
entry points. RQ2 mutation replay is documented in
[the case-study instructions](experiments/case_studies/saleor_attribute_choices/README.md).

`analysis/paper_mapping.csv` maps evidence to artifact paths; `docs/` explains
construction, annotation and trajectory protocols. `MANIFEST.sha256` defines
the public file surface and excludes staging-only `_internal/` infrastructure.
