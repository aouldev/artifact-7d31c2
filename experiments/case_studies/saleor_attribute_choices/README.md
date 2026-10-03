# Saleor attribute-choice mutation cases

These two diagnostic cases examine BOOLEAN static choices in Saleor Dashboard:
the handler branch itself and the builder-to-handler propagation of the attribute
type. They illustrate behavior boundaries and do not estimate population-wide
mutation detection.

`case.json` pins the base revision and production diff from the released K75
input. `specs/` describes each exact production edit and its admission oracle;
`probes/` contains the independent admission tests. `patches/` contains the
recorded developer, Codex and OpenCode test patches for this case. The `old`
condition uses the base tests. Recorded counts, test-file hashes and newly failed
assertions are in [mutation_execution.json](../../../results/rq2/mutation_execution.json).

## Prepare the case

Use Python 3.12 or later, Git, and a Saleor Dashboard checkout containing commit
`bf4a407a7921d4d6cf64ab74526d357267da8a16`. Preparation does not require Node or
installed JavaScript dependencies:

```bash
python experiments/case_studies/saleor_attribute_choices/run_mutations.py \
  --source-repo /path/to/saleor-dashboard \
  --output /tmp/repotem-saleor-prepare \
  --prepare-only
```

The script creates ten isolated workspaces: an admission probe and four suite
conditions for each mutation. It checks the production and test-file hashes
against the recorded cases. Each normal/mutant pair uses identical physical
test files. The source checkout is read-only, and the output directory must be
new. Use `--behavior attribute_type_forwarding` or `--behavior
boolean_static_choices` to prepare only one case.

## Execute the tests

Install the pinned checkout's locked dependencies in a separate workspace,
using its declared Node and pnpm versions. Pass that workspace's `node_modules`:

```bash
python experiments/case_studies/saleor_attribute_choices/run_mutations.py \
  --source-repo /path/to/saleor-dashboard \
  --dependencies-root /path/to/installed/node_modules \
  --output /tmp/repotem-saleor-execute \
  --execute
```

Compare the emitted counts, test hashes and newly failed assertion names with
the recorded evidence:

```bash
python analysis/check_rq2_details.py \
  --mutation-replay /tmp/repotem-saleor-execute/result.json
```

The recorded runs used Node `v22.21.1`. The base checkout declares Node
`>=24 <25` and pnpm `>=10`; a fresh installation under those declared versions
is a new execution environment. The runner adds an empty
`src/extensions/data/extensions.json` fixture only when absent, matching the
case setup. It saves normal/mutant Jest reports, logs and a summary under the
output directory. Runtime errors or a failing normal suite cannot establish
mutation detection. No model calls are needed to replay these frozen patches.

Repository-derived patches retain the Saleor Dashboard source terms; see
[source and data licensing](../../../docs/data_licensing.md) and the
[bundled Saleor notices](../../../licenses/upstream/saleor.txt).
