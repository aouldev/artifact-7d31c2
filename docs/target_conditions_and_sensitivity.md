# Target conditions and sensitivity

This analysis examines all 896 recorded test-file targets in the 288 positive
episodes. Each path is counted once per episode; separately recorded source
and destination paths for renames are retained. Supporting fixture and
configuration files are not additional targets. Relationship labels do not
filter the main analysis.

## Conditions

**Old sources.** A target satisfies this condition when its original source,
or a renamed, copied or extracted source, exists in the base revision or
available history. Otherwise, the record says no old source was found. This
is file provenance: an existing source does not imply that a particular
maintained test case has an old version or meets a method-level input contract.

**Name correspondence.** Remove the language extension and consecutive trailing
`.spec`, `.test`, `.unit`, `.integration` and `.e2e` markers, then lowercase
names. An `index` module also uses its parent directory name as an alias. A
test target satisfies the condition if its normalized name or alias matches
at least one recorded production module in the same episode. This JS/TS
module-level rule does not reproduce a Java class- or method-level miner.

Each condition is evaluated independently. `All`, `Some` and `None` indicate
whether all, some or no recorded targets satisfy it. `Some` and `None` imply
incomplete coverage of the recorded target set. The conditions can fail in
the same episode, so their counts cannot be added. A failing condition alone
does not establish a missed, semantically attributed maintenance requirement.

## Results and sensitivity

| Condition, main scope | All | Some | None | Episodes with incomplete coverage |
|---|---:|---:|---:|---:|
| Old source | 177 | 32 | 79 | 111/288 (38.54%) |
| Name correspondence | 200 | 34 | 54 | 88/288 (30.56%) |

Sensitivity analyses change relationship filtering, repository composition
and naming rules separately:

| Scope or rule | Episodes | Targets | No old source found | No name correspondence |
|---|---:|---:|---:|---:|
| All recorded targets, with entry-module aliases | 288 | 896 | 177 (19.75%) | 346 (38.62%) |
| Related targets only, with entry-module aliases | 280 | 730 | 172 (23.56%) | 215 (29.45%) |
| Excluding Typebot, with entry-module aliases | 270 | 712 | 173 (24.30%) | 222 (31.18%) |
| Strict basename, without entry-module aliases | 288 | 896 | 177 (19.75%) | 381 (42.52%) |

| Scope or rule | Episodes with incomplete old-source coverage | Episodes with incomplete name coverage |
|---|---:|---:|
| Main scope | 111/288 (38.54%) | 88/288 (30.56%) |
| Related targets only | 110/280 (39.29%) | 80/280 (28.57%) |
| Excluding Typebot | 107/270 (39.63%) | 78/270 (28.89%) |
| Strict basename | 111/288 (38.54%) | 90/288 (31.25%) |

Across these settings, incomplete coverage ranges from 38.54–39.63% for old
sources and 28.57–31.25% for names. The variants retain their own denominators;
excluding unrelated targets also removes episodes without retained targets.

## Saleor condition check

Saleor test revision
[`01c6fca3`](https://github.com/saleor/saleor-dashboard/commit/01c6fca3a6c87824a65e45bb4ebb75bcc347a4ca)
contains an existing test and a new test. The old-source condition retains only
the existing test. Episode-wide name correspondence retains both because
`DiscountRules.tsx` and `utils.ts` occur among the production changes. The
differently named `RuleSummary.tsx` relation illustrates cross-module maintenance;
it does not make either target fail the episode-wide name condition.

## Released records

- [`condition_coverage.json`](../results/benchmark_characterization/condition_coverage.json)
  and [`condition_coverage_by_episode.csv`](../results/benchmark_characterization/condition_coverage_by_episode.csv):
  main-scope aggregates and per-episode decisions.
- [`condition_sensitivity.json`](../results/benchmark_characterization/condition_sensitivity.json):
  frozen aggregate results for the three variants, including All/Some/None,
  target counts and each denominator.
- [`saleor_condition_case.json`](../results/benchmark_characterization/saleor_condition_case.json):
  selected case provenance.

The package supports checks of aggregate arithmetic and agreement with the main
scope. Repeating the historical provenance judgments requires the underlying
source review; the sensitivity export does not distribute private reference
test patches or all historical snapshots.
