# Participant Side

`benchmark/participant/` is the public runner for the static repo-level test-maintenance benchmark.

The task is binary:

- `positive`: the production change needs test maintenance; return a `test_patch` if available.
- `negative`: no test maintenance is needed; return an empty `test_patch`.

This version does **not** run dynamic patch validation. It only provides the public agent harness, public data, commit-pinned repo tools, traces, predictions, and local submission validation.

## Directory Layout

```text
benchmark/participant/
  agents/       # example/user agents
  core/         # runner, models, traces
  loaders/     # JSONL loaders and train reader code
  runs/         # default local run outputs
  tools/        # git-backed read-only repo tools
```

`loaders/` is the Python package for JSONL loading and train gold access. The
benchmark dataset lives under `data/benchmark_release/`, and run outputs are
written to `benchmark/participant/runs/`.

## Quick Start

From the repository root:

```bash
python -m benchmark info
python -m benchmark run --agent benchmark.participant.agents.examples.dummy_agent:DummyAgent
python -m benchmark validate benchmark/participant/runs/<run-id>
python -m benchmark package benchmark/participant/runs/<run-id>
```

For your own agent, usually only `--agent` changes:

```bash
python -m benchmark run --agent my_package.my_agent:MyAgent
```

Defaults:

- Dataset: `data/benchmark_release/`
- Repos root: `repos/`
- Output directory: `benchmark/participant/runs/`
- Trace recording: enabled by default

## Bundled Public Dataset

The bundled public dataset lives here:

```text
data/benchmark_release/
  manifest.json
  train.jsonl       # includes gold labels for learning
  validation.jsonl  # no gold labels or answer fields
```

Dataset construction policy:

- Source root: `experiments/workspace/latest_results/`
- Select one non-empty `pairing.json` per repo whose run directory contains `200_commits`.
- Exclude smoke runs.
- If a repo has multiple matching runs, choose the largest one.
- Split each repo chronologically into `70%` train and `30%` validation.

Current dataset summary:

- Repos: `23`
- Total episodes: `16,907`
- Train: `11,823` (`11,638` negative, `185` positive)
- Validation: `5,084` (`4,981` negative, `103` positive)

Private validation gold is not included in `benchmark/participant/`.

## Run Outputs

A run creates:

```text
benchmark/participant/runs/<run-id>/
  config.json
  predictions.jsonl
  summary.json
  manifest.json
  logs/
  traces/
```

`traces/` is kept by default for analysis. `python -m benchmark package <run-dir>` includes traces in the zip by default.

## SiliconFlow Smoke Test

The CLI auto-loads `.env` from the repository root. Fill in these variables:

```bash
SILICONFLOW_API_KEY="your_key_here"
SILICONFLOW_BASE_URL="https://api.siliconflow.cn/v1"
SILICONFLOW_MODEL="Qwen/Qwen3-32B"
SILICONFLOW_TEMPERATURE="0"
SILICONFLOW_MAX_TOKENS="512"
SILICONFLOW_TIMEOUT_SECONDS="60"
```

Then run a small smoke test:

```bash
python -m benchmark run \
  --agent benchmark.participant.agents.examples.siliconflow_agent:SiliconFlowAgent \
  --limit 3 \
  --run-id siliconflow_smoke_3

python -m benchmark validate benchmark/participant/runs/siliconflow_smoke_3
```

We recommend starting with `--limit 3` or `--limit 5` to verify the prompt,
output format, and network connectivity before running a larger sample.

## Agent Interface

Implement a class compatible with `BaseAgent`:

```python
from benchmark.participant import AgentResult, BaseAgent, Episode


class MyAgent(BaseAgent):
    name = "my_agent"

    def setup(self, context):
        # Optional: inspect public train examples.
        print(len(context.train_reader))

    def predict(self, episode: Episode, context):
        return AgentResult(
            maintenance_label="negative",
            test_patch="",
            rationale="example prediction",
        )
```

The validation `episode` contains task inputs only: `episode_id`, `repo`, `base_commit`, `prod_files`, `prod_diff`, `commit_message`, and `metadata`.

## Repo Tools

`context.tools` is bound to the current episode. Tools read only the `base_commit` version of the repo. They do not apply `prod_diff`; the agent must reason about `prod_diff` itself.

Available tools:

- `context.tools.repo_info()`
- `context.tools.ls(path=".")`
- `context.tools.tree(path=".", depth=2)`
- `context.tools.read_file(path, start=None, end=None)`
- `context.tools.search(query, path=".")`

Default limits: tree depth `4`, file size `1MB`, read window `400` lines, search results `50`. Tools reject absolute paths, `..`, `.git`, and symlinks.

The implementation reads directly from git objects and does not clone, checkout, archive, or create per-episode snapshot directories.

## Docker

Official-style runs should disable network access:

```bash
docker build -f benchmark/participant/Dockerfile -t repo-test-benchmark:participant-static .

docker run --rm --network none \
  -v "$PWD/repos:/app/repos:ro" \
  -v "$PWD/benchmark/participant/runs:/app/benchmark/participant/runs" \
  repo-test-benchmark:participant-static run --agent my_package.my_agent:MyAgent
```

The participant image should include public participant code and public data only. Private gold, server-side scoring, dynamic patch runners, dashboards, and admin scripts are outside this participant module.
