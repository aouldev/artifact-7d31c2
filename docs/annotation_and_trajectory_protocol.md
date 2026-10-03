# Annotation and trajectory protocol

## Maintenance-pattern annotation

The characterization bundle contains the final activity-level labels for the
288 positive episodes. `results/benchmark_characterization/annotation_codebook.json`
defines the pattern categories and aggregation rules, while
`maintenance_requirement_annotations.csv` contains 1,359 final activity rows.
Unattributed, uncertain, and unresolved records remain explicit in the data;
they are not silently converted into a pattern.

The public release reports the final labels and codebook. It does not identify
the annotator or include private working notes, local paths, credentials, or
superseded drafts. The annotation is a single-author review with Codex-assisted
code inspection; it is not an inter-rater agreement study.

### Review and counting unit

One author inspected production/test patches and relevant repository history
to record each test edit's purpose and its relation to the supplied production
change. Codex assisted with code inspection, classification boundaries and
review of activities affected by dataset repairs. The author adopted the
resulting annotation corrections. The released CSV contains final labels;
the underlying activity evidence and revision notes remain in the research
records.

Each activity represents a maintenance purpose within a test scenario. Inputs,
mocks and assertions added for a new scenario form one Test Augmentation (TA)
activity. For existing scenarios, expectation changes, setup changes and
interface access changes are recorded separately. Pattern statistics include
only attributed activities with a determined category, counting each pattern
once per episode. A shared commit or time window alone does not establish
attribution.

| Pattern | Classification boundary |
|---|---|
| TA: Test Augmentation | Add a behavioral check to an existing or new test. Setup and assertions introduced for a new scenario belong to this activity. |
| AR: Assertion Revision | Change the expected result, predicate, property or interaction of an existing check. |
| FA: Fixture Adaptation | Adjust existing scenario data, state or mocked dependencies. Moving an import and its mock target together to preserve dependency mocking also belongs here. |
| IA: Interface Adaptation | Adapt calls, returned-value access, UI locators or interactions while preserving test intent; includes necessary symbol-move and type-definition adaptations. |

Argument edits are classified by their role. Independent import/type cleanup
and test relocation are excluded from IA. Attributed work outside the four
main patterns is recorded as `OTHER`.

### Attribution and unresolved records

Of 1,359 activities, 1,228 were attributed to the supplied production change,
107 were not, and 24 remained uncertain. The mutually exclusive episode groups
are:

| Attribution of recorded activities | Episodes |
|---|---:|
| All attributed | 221 |
| Attributed activities mixed with unattributed or uncertain edits | 57 |
| No attributed activity | 10 |

All ten episodes in the last group belong to training. They include independent
type/import cleanup, production slices that omit changes addressed by test
edits, and uncertain attribution. A relationship established in the full
history can therefore be unsupported by the supplied production patch.
Training inputs and labels remain frozen; some correction candidates have not
been adopted. These records may affect training, retrieval or maintenance
histories; their effect on method performance has not been quantified.

[`attribution_summary.json`](../results/benchmark_characterization/attribution_summary.json)
is recomputed from the released activity CSV and training membership. It retains
the activity counts, episode groups and split counts.

Among 288 positives, 274 contain at least one main pattern, four contain only
attributed other activities and ten have no established category. Other
activities occur in 32 episodes and can coexist with main patterns. Fifteen
episodes contain an unresolved category or attribution; these episodes overlap
the preceding groups. An unresolved activity is not evidence of pattern absence.

Ninety episodes contain at least two main patterns, including 11 with all four.
TA occurs in 37/39 Affine positives (94.87%) and 22/38 Formbricks positives
(57.89%). The recorded-production-commit comparison in the historical analysis
is 36/152 (23.68%) for Cal.com and 17/144 (11.81%) for Hoppscotch. These last
rates use distinct recorded production commits, rather than episodes, and
describe the inspected corpus. Commit-level timing inputs are separate from
the released activity-label table.

## Behavior-level comparison

The RQ2 derived package covers 50 positive K75 episodes. Forty-nine episodes
contain runtime behavior, producing 240 behavior records; 239 source-confirmed
records form the primary comparison set. The public files record the developer,
Codex, and OpenCode patch labels (`complete`, `partial`, `missing`, or
`unknown`) and the 46 paired episodes used for per-episode missing-share
comparisons.

A single AI assistant performed the behavior decomposition and patch-coverage
coding. An author reviewed 60 source-only cards for behavior necessity and
responsibility boundaries. Those cards did not include the anonymized patch
materials, so this review does not establish independent human verification
of the developer or agent patch labels. Inter-rater agreement was not measured.

`analysis/summarize_rq2_behaviors.py` recomputes the patch-label counts,
checks the 46-episode paired table against the behavior labels, and reproduces
the 20,000-draw bootstrap intervals with seed 20260930. Missing shares are
proportions; multiply differences and interval endpoints by 100 to express
them in percentage points.

`results/rq2/behavior_catalog.jsonl` contains the 239 behavior definitions,
trigger conditions, expected results and candidate violations, with source
anchors into the released K75 production diffs. `diff_line` is one-based within
the episode's `prod_diff`; `old_line` and `new_line` refer to the corresponding
file revisions and are null where unavailable, including diff headers. The
diff and anchor-line SHA-256 values bind each citation to that released input.

`results/rq2/source_review_cards.jsonl` contains all 60 filled source-only review
cards, including proposals, necessity and responsibility decisions, reasons
and source anchors. Chinese contract and review prose is translated into
English; labels and source references are preserved. These cards calibrate the
behavior decomposition and responsibility boundary; they are not a second full
annotation and do not estimate inter-rater agreement. Their counts are reported
in `behavior_review_summary.json`.

The two Saleor mutation rows are illustrative diagnostic evidence and are not a
population estimate. `results/rq2/mutation_execution.json` records normal/mutant
counts, production/test-file hashes and newly failed assertions. Exact edits,
admission probes, frozen test patches and preparation/execution commands are
available in
[`experiments/case_studies/saleor_attribute_choices/`](../experiments/case_studies/saleor_attribute_choices/README.md).
`analysis/check_rq2_details.py` checks these detailed records against the released
inputs and summaries.

## Trace-derived records

The RQ1 trace audit contains 100 selected positive records (50 per agent).
`results/rq1/trace_events.jsonl` preserves tool-call order, success flags, path
identity, and path classifications. Path identifiers are local to each run.
`trace_scope_schema.json` defines every event and summary field, and
`analysis/summarize_rq1_trace_scope.py` recomputes the audit. These are tool-server
observations; a successful server call does not establish client receipt.

The RQ2 process files contain 100 run summaries, pre-edit public-plan labels,
and read/write counts. The plan-label and patch-comparison tables cover the
same 239 behaviors (478 behavior-agent plan records).
Plan records include the supporting message excerpt, its line position, and
the behavior mapping. `file_write_timeline.json` records successful client
write events and classifies each run by its number of writes and distinct
written files. `analysis/check_rq2_process.py` checks these counts and the
plan aggregates against the released records.
A single AI assistant coded the public plans retrospectively; this coding
was not independent of the patch review.
Private trace roots, credentials, hidden gold, and full model contexts are
excluded.

The trace summaries describe observable tool activity. A read or search event
does not establish comprehension, and the absence of a public plan does not
prove that the agent failed to understand a behavior. Unknown and unavailable
states remain separate from confirmed omissions.

## Reproduction boundary

The machine-readable files under `results/rq1/` and `results/rq2/` are the
released derived views used for the reported summaries. Their manifests state
the cohort, model, reasoning setting, and counts. The package
does not require publication of all raw trajectories or all private source
snapshots to inspect these aggregate claims.
