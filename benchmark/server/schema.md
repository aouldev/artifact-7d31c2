# Official Benchmark Server Schemas

This directory uses strict productized schemas. Server evaluators reject non-conforming rows instead of guessing aliases or raw-data variants.

## Private Gold

Canonical server file: `benchmark/server/data/releases/<release_id>/validation_gold.jsonl`

Release build source file: `benchmark/releases/<release_id>/private/validation_gold.jsonl`

Each row is one JSON object with:

- `schema_version`: `1`
- `kind`: `official_gold`
- `episode_id`: string
- `repo`: string
- `base_commit`: string
- `prod_diff`: string
- `prod_files`: string[]
- `maintenance_label`: `positive` or `negative`
- `test_patch`: string
- `test_files`: string[]
- `metadata`: object

Rules:

- `positive` rows must have non-empty `test_patch` and non-empty `test_files`.
- `negative` rows must have empty `test_patch` and empty `test_files`.
- No `gold`, `ground_truth`, `sample_id`, `base`, or `label` aliases are accepted by evaluators.

## Predictions

Participant runner output: `predictions.jsonl`.

Each row is one JSON object with:

- `episode_id`: string
- `repo`: string
- `base_commit`: string
- `result`: object or `null`
- `metadata`: object

When `result` is an object:

- `maintenance_label`: `positive` or `negative`
- `test_patch`: string

Rules:

- `negative` predictions must have empty `test_patch`.
- The server ignores any extra non-schema fields only if they are nested inside `metadata`.

## Release Builder Output

The release builder that produces the private Gold file and public
train/validation files is staging-only and is not included in the public
participant package.


## Server Evaluation Result

File: `benchmark/server/storage/evaluated_results/<submission_id>/result.json`

Top-level fields:

- `schema_version`: `1`
- `kind`: `server_evaluation_result`
- `submission_id`: string
- `release_id`: string or null
- `inputs`: object
- `storage`: object
- `config`: object
- `metrics`: object
- `artifacts`: object

This file is the single closed-loop artifact for a submission. It embeds the decision metrics, static patch metrics, dynamic patch metrics, and optional prepare metrics, along with stable output paths.
