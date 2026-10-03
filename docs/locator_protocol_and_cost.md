# Existing-target locator protocol and cost

The independent locator diagnostic compares repository exploration with
[TCTracer](https://doi.org/10.1007/s10664-021-10079-1) naming rules adapted to file-level retrieval. It locates existing
test paths and does not generate a patch.

## Inputs and output

The formal protocol is `target-unknown-path-seeded`. Each run receives changed
production paths, commit metadata and a read-only base snapshot. Production
diffs, reference targets, test patches and future history are hidden. The output
is a ranked list of at most 20 repository-relative test-side paths. This differs
from the main patch-generation interface, which may expose the production diff.

Of 103 positive validation episodes, 74 have at least one existing target and
29 add only new tests. Recovery and mean costs use all 74 applicable episodes,
including runs without a valid result. Newly added paths cannot be recovered
from the base snapshot.

## Recorded configuration

| Setting | Codex | OpenCode |
|---|---|---|
| Client version | CLI 0.144.1 | 1.17.18 |
| Model | GPT-5.5 | GPT-5.5 |
| Reasoning | low | default |
| Completion budget | 900 seconds | 1,800 seconds |
| Selected records using original official OAuth provider | 100 | 100 |
| Updated-input records using AILink Responses API | 3 | 3 |

Both agents use the same locator prompt with their own tools. The provider rows
describe the selected historical locator records, rather than the provider
policy of the released patch-generation runners. Credentials, endpoints and
raw model contexts are excluded from the package.

## Cost diagnostics

Mean runtime and client-recorded token costs include all applicable episodes.
In paired traces, the median ratio of tool-output characters
(Codex/OpenCode) is 2.4. OpenCode's median share of runtime spent on repository
search is 67.9%; search time and total runtime have Spearman correlation
0.963. One applicable OpenCode episode returns no valid result despite the
1,800-second budget; its searches occupy 1,564 seconds. These observations
describe the search and context costs accompanying target recovery.

The costs use client-recorded totals. Different clients' context/cache
accounting and search tools remain part of the complete configurations.

## Released records

- [`metrics.json`](../results/locator/metrics.json): recovery outcomes, applicable
  status counts and mean runtime/token costs.
- [`per_episode_metrics.jsonl`](../results/locator/per_episode_metrics.jsonl):
  selected per-episode results.
- [`public_episodes.jsonl`](../results/locator/public_episodes.jsonl):
  locator inputs and scope metadata.
- [`trace_cost_summary.json`](../results/locator/trace_cost_summary.json):
  character ratio, search share, correlation and timeout search duration.
- [`manifest.json`](../results/locator/manifest.json): released file inventory.

These are curated derived records. The full private locator master record and
raw search trajectories are not distributed.
