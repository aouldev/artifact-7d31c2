# History and memory conditions

RQ3 compares Codex and OpenCode under three configurations, using the same
1,673 K75 episodes: 50 positives and 1,623 negatives. All conditions use
`gpt-5.5` with low reasoning effort.

| Condition | Session and history |
|---|---|
| Stateless | An independent session for each episode |
| Continuous / agent-native memory | A persistent session within each repository, with access to a training catalogue and a writable memory namespace |
| Online / task-specific memory | An independent actor session per episode, host-managed history retrieval, and a separate reflection step |

Continuous exposes a catalogue of 8,596 training episodes. Online starts from
154 eligible positive training episodes; their IDs are in
[`history_seed_ids.txt`](../results/rq3/history_seed_ids.txt). The history pool
requires an exact production-change provenance match and history preceding the
repository's K75 targets. The public training dataset is shared by these inputs;
it is not duplicated here.

Online supplies semantic, procedural and episodic records, connected by module
and path relations. Reflection uses the public change and inspected base source,
without the current validation label, reference test patch or evaluator feedback.
The history scopes and historical runtime settings differ between configurations;
the results compare complete configurations rather than isolate memory's causal
effect. The public agent directories provide prompts and reference interfaces.

Retrieval starts from a module anchor matching the changed production path,
then expands through parent nodes and links. Semantic records encode module–test
relations, procedural records describe setup and assertion conventions, and
episodic records provide previous maintenance examples.

The released settings and tool boundaries are in:

- [Stateless configuration](../experiments/agents/stateless/experiment_config.json)
  and [prompt/tool contract](../experiments/agents/stateless/prompt.json).
- [Continuous configuration](../experiments/agents/continuous/experiment_config.json)
  and [prompt/tool contract](../experiments/agents/continuous/prompt.json).
- [Online configuration](../experiments/agents/online/experiment_config.json)
  and [prompt/tool contract](../experiments/agents/online/prompt.json).

These links describe the released runnable contracts. Continuous and Online
configuration files are marked `reference_runner_not_result_freeze`; their
current provider/budget settings should not be substituted for every historical
run's settings. Result manifests identify the selected historical outputs.

## Shared-positive generation comparison

The main generation table uses each condition's own correctly identified,
evaluable positives. A second comparison restricts an agent to the intersection
of the three conditional sets: 37 Codex episodes and 30 OpenCode episodes.
It reuses the same selected patches and execution outcomes.

| Agent | Shared episodes | Stateless TPS/UCR | Agent-native TPS/UCR | Task-specific TPS/UCR |
|---|---:|---:|---:|---:|
| Codex | 37 | 20/37 | 18/37 | 16/37 |
| OpenCode | 30 | 15/30 | 16/30 | 18/30 |

[`shared_positive_generation.json`](../results/rq3/shared_positive_generation.json)
preserves the shared episode IDs and CSR/TPS/UCR counts. It is derived directly
from [`generation_records.jsonl`](../results/rq3/generation_records.jsonl), by
intersecting episode IDs across the three conditions within each agent and
counting `passed` outcomes. It supplements the formal condition-specific table
without changing its denominators or scores.

## Inspection and additional case execution

The trace comparison verifies 296 of the 300 reference-positive condition
records against selected outputs and the episode's base and public production
input. Two Codex Agent-native traces have a different production input; one
OpenCode Agent-native and one OpenCode Task-specific trace are unavailable.
These four records are excluded from the matched trace comparison rather than
assigned zero reads. Formal result selection remains unchanged.

Distinct-file counts include successful client-visible reads of current
repository files. Searches, failed reads and training-document reads are
excluded. On matched episodes where both the history condition and Stateless
predict positive, the median within-episode differences are:

| Agent | History condition | Paired episodes | Distinct files read, median difference from Stateless |
|---|---|---:|---:|
| Codex | Agent-native | 39 | 0 |
| Codex | Task-specific | 38 | +1.5 |
| OpenCode | Agent-native | 36 | −0.5 |
| OpenCode | Task-specific | 35 | +2 |

These counts describe inspection scope rather than behavioral completeness.
[`trace_comparison_summary.json`](../results/rq3/trace_comparison_summary.json)
retains scope counts, aggregate differences, exclusions and selected-output
identities; raw per-event traces are not distributed.

In the Saleor ProductDoctor episode, OpenCode Task-specific adds a badge suite;
Stateless already edits component tests. The formal test entry omits the new
file. Additional execution of the new badge suite and modified utility tests
passes 18/18 tests across two suites. The record includes the test command,
file and patch hashes, exit status and elapsed time. This case execution is
separate from formal TPS/UCR. The main-text Codex comparison instead connects
supplied history, delivered assertions and a current mock's message-template
mismatch.

## Evaluation production state

Five episodes require production dependency files from their original
production commits when preparing isolated evaluation snapshots. Within each
episode, the reference patch and all applicable conditions use the same
recovered production state. Agent inputs and selected agent patches are
unchanged.

[`evaluation_environment.json`](../results/rq3/evaluation_environment.json)
identifies the five episodes, original production commits, companion paths,
patch fingerprints and affected conditions. Private prepared snapshots and
reference test patches remain outside the package. This record describes the
actual evaluation state; it does not imply that the frozen public production
slice alone reconstructs every required dependency.

## Released evidence

[`results/rq3/`](../results/rq3/) contains complete identification metrics,
conditional static and dynamic generation results and Codex Continuous history-access
records. The four new decision files share the RQ1 Stateless files, as specified
in the result manifest. Decision rows retain only episode identity, the recorded
label and execution status. Null labels remain null and score negative under
the official identification rule. Timeout skips remain separately identified.

Static application uses `D_m = E49 intersect TP_m`, with the existing
[conditional working set](../results/rq1/conditional_generation_working_set.txt).
`generation_metrics.json` and `generation_records.jsonl` contain complete
CSR/TPS/UCR results for all six conditions, across 253 condition–episode cases.
CSR requires the specified target tests to compile/transform and load; TPS
requires their successful execution, and UCR requires changed-production-file
coverage. All three rates use the same condition denominator. A failed
criterion includes cases that cannot reach it after an earlier failure.

RQ1 Stateless and RQ3 use the same native target-test outcomes and conditional
denominators. The RQ1 `legacy_evaluator` field retains the original evaluator
view separately; the main agent metrics use the released per-case evidence.

The history-access JSONL contains per-episode event counts and memory-file
counts. Its summary can be recomputed from those records: 557 episodes read
training indexes/descriptions and three read concrete historical artifacts.
All 1,673 before/after memory-file snapshots are empty. These are file-access
observations; they do not measure the usefulness of persistent session context.

`online_history_events.jsonl` records history availability for the 50 reference
positives in each Online condition. Its summary reproduces concrete initial
history in 25/50 Codex and 27/49 verifiable OpenCode episodes; one OpenCode trace
is unavailable. Subsequent queries do not expand either supported set. A catalogue
without a changed-path anchor is recorded separately from an absent catalogue.

`cost_records.jsonl` and `cost_metrics.json` support the paper's resource table.
Tokens include cached context. Codex totals use input plus output; OpenCode uses
the adapter total. Codex Continuous counts the final cumulative usage of each
of 23 repository sessions once. Other token observations and all actor times
are per episode. For Codex Continuous, `token_included=false` marks
intermediate rows whose token field is null because session totals are counted
at session end. Missing included observations remain null and unknown.
Online seed and reflection calls are separate phases; known combined totals
are 474.7M and 144.3M tokens.
The scope includes retained maintenance calls and successful memory-writer calls,
excluding discarded attempts, workspace preparation and evaluation. Time medians
and p90 values across phases are not additive.

Each agent has 154 seed calls, 1,672 successful reflections and one reflection
without a normal success record. Missing consumption remains unknown. Known
Task-specific seed/reflection overhead is 42.2M tokens for Codex and 17.6M for
OpenCode, bringing known maintenance-plus-overhead totals to 474.7M and 144.3M.

The main cost table aggregates maintenance calls with available observations:

| Condition | Token observations | Time observations |
|---|---|---|
| Codex Stateless | 1,671/1,673 episodes | 1,671/1,673 episodes |
| Codex Agent-native | Final cumulative totals from all 23 repository sessions | 1,673/1,673 episodes |
| Codex Task-specific | 1,673/1,673 episodes | 1,673/1,673 episodes |
| OpenCode Stateless | 1,667/1,673 episodes | 1,671/1,673 episodes |
| OpenCode Agent-native | 1,669/1,673 episodes | 1,673/1,673 episodes |
| OpenCode Task-specific | 1,673/1,673 episodes | 1,673/1,673 episodes |

Tokens include cached context without double counting. Missing calls are not
assigned zero cost. The released cost records preserve phase, observation
coverage and available total-token/time values; they do not distribute the
complete native cache-breakdown ledger.

The [product-diagnostics case](../experiments/case_studies/saleor_product_diagnostics/README.md)
includes three selected patches, supplied training history, inspected paths and
the recorded component-test outcome supporting the paper's case discussion.

## Check the released components

From the repository root, with Python 3.12 or later:

```sh
python analysis/check_rq3_results.py
python analysis/summarize_rq3_evidence.py
```

This checks hashes, cohort identities, decision totals, metric arithmetic,
paired transition totals, static and dynamic denominators, per-case outcome
counts, CSR/TPS/UCR arithmetic, history-event summaries and
training seed membership. The second command recomputes resource statistics and
history coverage and checks the case's patches and shared outcomes.
An evaluator operator with host-only Gold can also
recompute the confusion matrices and paired transitions:

```sh
python analysis/check_rq3_results.py --gold PATH_TO_HOST_GOLD
```

Gold is neither distributed nor written by this checker. Endpoint/user
configuration, local paths, internal selection diagnostics and raw model logs
are excluded from the released records. Source-output hashes identify the
selected inputs; public decision files are derived views.
