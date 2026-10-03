# Replication documentation and additional evidence

This directory is the entry point for protocol notes and additional evidence
provided through the anonymous replication repository. No separate supplementary
PDF is submitted. Core task definitions, evaluation denominators and main
findings remain in the paper.

The documents point to the released machine-readable records under `data/` and
`results/`. They describe the cohort, labels, denominators, review boundaries,
and privacy boundary. Private validation gold, credentials, local paths, raw
model event trees, and exploratory runs are excluded.

## Documents

- [`benchmark_construction.md`](benchmark_construction.md): corpus screening,
  episode construction, splits, and evaluation interfaces.
- [`annotation_and_trajectory_protocol.md`](annotation_and_trajectory_protocol.md):
  maintenance labels, behavior-level review, selected trace summaries, and
  the limits of the derived evidence.
- [`target_conditions_and_sensitivity.md`](target_conditions_and_sensitivity.md):
  old-source and episode-wide name conditions, all recorded targets, three
  sensitivity variants, and the corrected Saleor condition check.
- [`locator_protocol_and_cost.md`](locator_protocol_and_cost.md):
  path-seeded input, versions and providers, existing-target applicability,
  and search/context cost diagnostics.
- [`data_licensing.md`](data_licensing.md): upstream source repositories,
  reference license texts, and the scope of repository-derived data reuse.
- [`history_and_memory_protocol.md`](history_and_memory_protocol.md): RQ3
  conditions, history scope, shared-positive generation comparison, matched
  inspection summaries, additional case execution, evaluation production
  dependencies, resource accounting, and verification commands.

## Paper-to-repository guide

| Paper content or former appendix section | Readable document | Supporting result group |
|---|---|---|
| Construction, execution profiles and task schemas | [Construction protocol](benchmark_construction.md) | [Repository profiles](../benchmark/server/repo_profiles/README.md), [evaluator schema](../benchmark/server/schema.md) |
| Test-maintenance patterns: classification and counting | [Annotation protocol](annotation_and_trajectory_protocol.md#maintenance-pattern-annotation) | [Characterization records](../results/benchmark_characterization/) |
| Repository histories and within-episode commit timing | [History coordinates and counts](../results/benchmark_characterization/maintenance_history/) | [Summary](../results/benchmark_characterization/maintenance_history/summary.json) |
| Target conditions and sensitivity | [Conditions and sensitivity](target_conditions_and_sensitivity.md) | [Sensitivity aggregates](../results/benchmark_characterization/condition_sensitivity.json) |
| Existing-target locator configuration and cost | [Locator protocol](locator_protocol_and_cost.md) | [Locator records](../results/locator/) |
| Experiment versions and tool permissions | [Configuration links](history_and_memory_protocol.md) | [Stateless](../experiments/agents/stateless/experiment_config.json), [Continuous](../experiments/agents/continuous/experiment_config.json), [Online](../experiments/agents/online/experiment_config.json) |
| History and memory analysis | [History protocol](history_and_memory_protocol.md) | [RQ3 records](../results/rq3/) |
| Generation on shared positives | [Shared-positive comparison](history_and_memory_protocol.md#shared-positive-generation-comparison) | [Counts and episode IDs](../results/rq3/shared_positive_generation.json) |

The four former appendix sections are available as linked Markdown documents.
Frozen summaries expose their counting scope and evidence boundary. The
repository does not distribute private evaluation Gold, every prepared
snapshot, all annotation revision notes or raw model contexts.

The result manifests are the authoritative record of scope and status. A file
under `results/` is a derived release record; it is not a claim that the
underlying private repository snapshots or hidden gold are redistributed.
