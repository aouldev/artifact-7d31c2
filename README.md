# Anonymous artifact

This repository contains the curated artifact for the RepoTEM benchmark. The
package separates benchmark inputs, executable code, and the result data used
by the paper.

Start with [the running instructions](README_REPRODUCE.md) for installation,
model-free examples, agent submission export and evaluation.

## Repository layout

```text
.
├── benchmark/                 # participant interface and evaluator
├── data/                      # public benchmark inputs
│   ├── benchmark_release/     # full train/validation release
│   └── k75_evaluation_cohort/ # fixed cohort used by formal experiments
├── experiments/               # construction, agent runners and case studies
│   ├── agents/stateless/      # formal stateless prompt and runner
│   ├── agents/continuous/     # continuous reference prompt and runner
│   ├── agents/online/         # online actor and reflection boundaries
│   ├── case_studies/          # frozen RQ2 mutation inputs and replay runner
│   ├── demo/                  # construction and evaluator examples
│   ├── pipeline/              # public construction core
│   └── configs/shims/         # evaluator preload assets
├── results/                   # curated evidence used by paper tables and figures
│   ├── benchmark_characterization/
│   ├── locator/
│   ├── rq1/
│   ├── rq2/
│   └── rq3/                   # identification, generation and history-access evidence
├── docs/                      # replication protocols and additional evidence
├── licenses/upstream/         # upstream copyright and license texts
├── analysis/                  # public result summaries and figure renderers
│   └── figures/
├── tests/                     # tests for the released code
├── MANIFEST.sha256            # hashes for the public file surface
├── LICENSE                    # original implementation and documentation
├── NOTICE.md                  # third-party license scope and attribution
├── pyproject.toml
└── uv.lock
```

`data/benchmark_release/` contains `train.jsonl`, `validation.jsonl`, and its
manifest. `data/k75_evaluation_cohort/` contains the answer-free cohort input,
the fixed episode-ID order, and its manifest. These are separate because the
full release is the public benchmark while K75 is the fixed input for the
formal experiments reported in the paper.

`results/` contains derived records that support paper tables and
figures. It is organized by analysis scope rather than by run date. Each
result group identifies its cohort, condition, denominator, evaluator, and
source manifest. The RQ1 and RQ2 directories include sanitized derived trace
summaries and review labels; raw model event logs, temporary checkpoints,
private gold, and exploratory or superseded runs are excluded.

RQ3 includes complete six-condition identification, static application and
dynamic CSR/TPS/UCR results, four decision projections reusing the RQ1 Stateless records, history
seed IDs and per-episode Continuous history-access counts. See
[the protocol and checks](docs/history_and_memory_protocol.md).
The resource table is supported by per-call token/time records, including
Online seed and reflection phases. Online history-availability records and the
Saleor product-diagnostics case provide the evidence for the RQ3 history discussion.

RQ2 also includes behavior definitions with source anchors, all 60 source-only
review cards, and recorded mutation execution evidence. The two Saleor case
studies include exact edits, admission probes, frozen test patches and a replay
runner; see [the case instructions](experiments/case_studies/saleor_attribute_choices/README.md).

[`docs/`](docs/README.md) is the entry point for replication protocols and
additional evidence; no separate supplementary PDF is submitted. Its
paper-to-repository guide maps the former appendix sections to construction,
annotation, target-condition sensitivity, locator and history documents,
together with their supporting result records.

`analysis/figures/` contains dependency-free SVG renderers for the released
characterization and locator summaries. `analysis/paper_mapping.csv` records the
current paper-facing result groups and the status of the RQ evidence directories.

`experiments/agents/stateless/` is the public reproduction boundary for the
stateless generation condition. Start with [`PROMPT.md`](experiments/agents/stateless/PROMPT.md),
then use [`experiment_config.json`](experiments/agents/stateless/experiment_config.json),
[`stateless_runner.py`](experiments/agents/stateless/stateless_runner.py), and the
episode-scoped [`stateless_server.py`](experiments/agents/stateless/stateless_server.py).

`experiments/agents/continuous/` and `experiments/agents/online/` expose the
corresponding prompts and single-stream reference runners. Continuous keeps one
client session and a writable `memory/` namespace. Online gives each actor a
read-only host-managed memory view and keeps reflection as a separate writer
step. The complete selected RQ3 components, including all 253 conditional
dynamic cases, are listed in `results/rq3/manifest.json`.

All three runners support `--export-predictions` to save evaluator submissions
from workspace edits; the running instructions include a model-free example.

For all three agent conditions, the episode's `prod_files` are read-only. Other
paths under `repo/` are eligible non-production workspace files when supported
by the current episode evidence.

The benchmark characterization results include final per-episode maintenance
patterns, condition coverage, and a language-neutral requirement-level label
table with its codebook. Internal evidence prose and annotation revision
ledgers are not part of the public package.

The RQ1 prior-method metrics are released as frozen result records under
`results/rq1/prior_methods/`. Their original adapters and environment-specific
setup are staging-only under `_internal/`; they are not presented as a
standalone rerunnable package.

The public construction code is a runnable method demonstration. The
repository-specific construction workers and retry logs remain under
`_internal/` in the staging workspace. Eighteen evaluator configurations and a
portable Node example are public under
[`benchmark/server/repo_profiles/`](benchmark/server/repo_profiles/README.md).
The small evaluator preload shims under `experiments/configs/shims/` are kept
public because the benchmark server uses them.

Release-building, hidden-Gold synchronization, oracle-submission, and
leaderboard-generation utilities are also staging-only under
`_internal/benchmark_release_scripts/`.

The original implementation and associated documentation use the [MIT license](LICENSE).
Repository-derived data and snippets retain their upstream terms; see
[NOTICE.md](NOTICE.md) and [source and data licensing](docs/data_licensing.md).
