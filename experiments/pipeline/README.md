# Public construction core

The public pipeline contains the method components needed to inspect and
demonstrate construction logic:

- `commit_test_locator.mjs` locates changed tests;
- `coverage_parser.mjs` normalizes supported coverage artifacts;
- `graph_pairer.mjs` pairs commit records into episode samples.

Run the complete toy path with:

```bash
node experiments/demo/pair_records_demo.mjs
```

The public demo exercises the `pair-records` path. The `trace-commit` and
full `run` modes require the staging-only environment workers described below.

Repository-specific environment setup, coverage execution, and recovery
workers are kept under `_internal/construction_pipeline/` in the staging
workspace. They require external repository snapshots and environment
profiles and are not part of the public artifact's reconstruction claim.
