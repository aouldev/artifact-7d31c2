# Saleor product-diagnostics case

This case supports the RQ3 comparison of history availability and delivered
checks. Episode `saleor__a11d1fc587f5` starts at Saleor revision
`53474a00566363d6164023201a70fb32aa2f7b19`. Its public production change is in
[`data/k75_evaluation_cohort/validation.jsonl`](../../../data/k75_evaluation_cohort/validation.jsonl).

The three patches are the selected Codex outputs for Stateless, Continuous and
Online. Stateless and Continuous update utility-test counts. Online also adds
component assertions, including a purchasable product that requires no shipping.
The supplied historical records, inspected paths and outcome references are in
[`product_diagnostics_case.json`](../../../results/rq3/product_diagnostics_case.json).
The historical source episodes belong to the released positive training seeds.

The Online component test executes 12 tests: ten pass and two fail. One failure
expects `2 advisories`, while the current mock returns `{count} advisories`.
These are recorded execution results; the case does not establish a causal effect
of memory. The formal CSR/TPS/UCR outcomes come from the shared RQ3 dynamic ledger.

Inspect a patch after applying the episode's public production diff to its base
snapshot. `git apply --check PATH_TO_PATCH` checks applicability; native execution
also requires Saleor's dependencies and the evaluator profile. The result-data
checks below require no repository checkout or model calls:

```sh
python analysis/check_rq3_results.py
python analysis/summarize_rq3_evidence.py
```
