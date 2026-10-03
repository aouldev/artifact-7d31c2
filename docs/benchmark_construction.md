# Benchmark construction protocol

## Corpus and screening

The corpus is a purposive collection of public JavaScript/TypeScript
repositories. The screening checkpoints are reported at repository level:

| checkpoint | repositories |
|---|---:|
| initial candidate pool | 45 |
| executable historical test entry point | 31 |
| repositories contributing benchmark episodes | 23 |

The released benchmark contains 16,907 episodes: 288 positive and 16,619
negative. The internal construction process may use `unresolved` while a record
is being checked; the public benchmark exposes only `positive` and `negative`.

## Episode construction

An episode joins a production change with its historical test-side evidence
within the frozen temporal window. Coverage and repository-history relations
are used to form candidate production/test components. The public release
contains the resulting episode inputs and split manifests, while private gold
and repository snapshots remain outside the anonymous package.

The public split is:

| split | positive | negative | total |
|---|---:|---:|---:|
| train | 185 | 11,638 | 11,823 |
| validation | 103 | 4,981 | 5,084 |

The fixed K75 evaluation cohort contains 1,673 episodes, including 50 positive
and 1,623 negative episodes. Its answer-free input and episode order are under
`data/k75_evaluation_cohort/`.

## Evaluation interfaces

The benchmark separates identification from test-patch generation. These tasks
provide the production diff, changed production paths, commit metadata, and
access to the base snapshot. Target test paths, hidden labels, and developer
test changes are withheld. The separate locator diagnostic receives changed
production paths and commit metadata with read-only snapshot access, without
the production diff. The released evaluator defines submission schemas and
denominator rules.

The public workspace policy allows changes to non-production repository paths.
Paths listed in an episode's `prod_files` are read-only. This policy applies to
tests and other support files as well as to any other non-production path that
the episode evidence makes relevant.

## Execution profiles and scoring records

The [repository profiles](../benchmark/server/repo_profiles/README.md) document
the selected execution configurations and their host prerequisites. The
[evaluator schema](../benchmark/server/schema.md) and
[evaluator instructions](../benchmark/server/README.md) describe inputs,
submission fields and stage results. Formal agent versions, prompts and tool
boundaries are linked from the [history protocol](history_and_memory_protocol.md).

UCR is a file-level criterion: successful execution covers at least one changed
production file. It does not establish coverage of modified statements,
branches or complete behavior. Released RQ1 Generation records retain the applicable
denominator and identify the numerator source in their aggregation metadata.
Coding-agent RQ1 and RQ3 results use native target-test outcomes; prior-method
RQ1 results aggregate the evaluator pass counts recorded for those runs.
Five RQ3 evaluation episodes need
additional original-production dependencies; their actual production state is
documented in the [evaluation-state record](../results/rq3/evaluation_environment.json).

## Public boundary

`data/` is the public input surface. `results/` contains frozen derived records
used by the paper. Repository-specific environment preparation, coverage
workers, private snapshots, and hidden evaluator inputs are not required for a
reviewer to inspect the released benchmark schema and reported aggregates.
