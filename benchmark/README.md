# Repo-level Test Maintenance Benchmark

The participant interface lets agents read a production diff and a commit-pinned repository context, then predict whether test maintenance is needed and return a patch.

The public task is binary:

- `positive`: the production change needs test maintenance; the agent may provide a `test_patch`.
- `negative`: no test maintenance is needed; `test_patch` must be empty.

The participant CLI records predictions and traces. The separate
[server evaluator](server/README.md) checks patch application, compilation,
test execution and coverage when Gold, snapshots and profiles are supplied.
Its local demo runs without private benchmark answers or model calls.

## Quick Start

Show the bundled dataset:

```bash
python -m benchmark info
```

Run the example agent:

```bash
python -m benchmark run
```

Run your agent:

```bash
python -m benchmark run --agent my_package.my_agent:MyAgent
```

Validate a run directory:

```bash
python -m benchmark validate benchmark/participant/runs/<run-id>
```

Package a valid run directory:

```bash
python -m benchmark package benchmark/participant/runs/<run-id>
```

By default, `package` includes traces. Use `--no-traces` only when you want a smaller local zip; traces remain in the run directory.

## Defaults

The CLI is intentionally small. These defaults should work from the repository root:

- Dataset: `data/benchmark_release/`
- Repos root: `repos/`
- Output directory: `benchmark/participant/runs/`
- Example agent: `benchmark.participant.agents.examples.dummy_agent:DummyAgent`

The full run command with defaults expanded is:

```bash
python -m benchmark run \
  --agent benchmark.participant.agents.examples.dummy_agent:DummyAgent \
  --repos-root repos \
  --output-dir benchmark/participant/runs
```

## Quick LLM Smoke Test

For an OpenAI-compatible provider such as SiliconFlow, use the example agent and a small validation limit first. The CLI auto-loads `.env` from the repository root.

```bash
SILICONFLOW_API_KEY="your_key_here"
SILICONFLOW_BASE_URL="https://api.siliconflow.cn/v1"
SILICONFLOW_MODEL="Qwen/Qwen3-32B"
```

```bash
python -m benchmark run \
  --agent benchmark.participant.agents.examples.siliconflow_agent:SiliconFlowAgent \
  --limit 3 \
  --run-id siliconflow_smoke_3

python -m benchmark validate benchmark/participant/runs/siliconflow_smoke_3
```

## Bundled Public Dataset

The participant bundle contains only public dataset inputs:

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

## Agent API

Implement a class compatible with `BaseAgent`:

```python
from benchmark.participant import AgentResult, BaseAgent, Episode


class MyAgent(BaseAgent):
    name = "my_agent"

    def predict(self, episode: Episode, context):
        return AgentResult(
            maintenance_label="negative",
            test_patch="",
            rationale="example prediction",
        )
```

During prediction, `episode` includes:

- `episode_id`
- `repo`
- `base_commit`
- `prod_files`
- `prod_diff`
- `commit_message`
- `metadata`

`context.train_reader` exposes the public train split with gold labels. `context.tools` exposes read-only repository tools.

## Repository Tools

Tools are bound to the current episode and read only `base_commit`. The production diff is supplied in the episode input and is not applied to the repository context.

Available tools:

- `context.tools.repo_info()`
- `context.tools.ls(path=".")`
- `context.tools.tree(path=".", depth=2)`
- `context.tools.read_file(path, start=None, end=None)`
- `context.tools.search(query, path=".")`

Implementation note: tools read directly from git objects; they do not clone, checkout, export snapshots, or create per-episode repository directories.

## Outputs

Each run creates:

```text
benchmark/participant/runs/<run-id>/
  config.json
  predictions.jsonl
  summary.json
  manifest.json
  logs/
  traces/
```

Use `python -m benchmark validate <run-dir>` before sharing a run. Use `python -m benchmark package <run-dir>` to create a zip package.

## Docker

The participant image should be run without network access:

```bash
docker build -f benchmark/participant/Dockerfile -t repo-test-benchmark:participant-static .

docker run --rm --network none \
  -v "$PWD/repos:/app/repos:ro" \
  -v "$PWD/benchmark/participant/runs:/app/benchmark/participant/runs" \
  repo-test-benchmark:participant-static run --agent my_package.my_agent:MyAgent
```

Only the participant-side runner and public dataset belong in the participant image. Private gold and server-only evaluation code are not part of the public participant workflow.
