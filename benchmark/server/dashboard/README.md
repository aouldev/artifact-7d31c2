# RepoTest-Evolution Leaderboard Dashboard

This is an optional static leaderboard page for official server-side benchmark results.
The public artifact keeps the page source only; evaluated submission results and the generated
`data/leaderboard.json` snapshot are omitted. Build that private snapshot from
`benchmark/server/storage/evaluated_results/*/result.json` before local deployment.
The release-side generator is kept as staging-only administration code under
`_internal/benchmark_release_scripts/`.

## Build Local Data

From the repository root:

The public package does not include the release-side leaderboard generator.

Optional per-submission metadata can be placed next to a `result.json` as
`leaderboard_metadata.json`:

```json
{
  "display_name": "My Agent",
  "team": "Example Lab",
  "model": "ExampleModel-1",
  "method": "Short method description.",
  "links": {
    "paper": "https://example.com/paper.pdf",
    "code": "https://github.com/example/agent",
    "report": "https://example.com/report"
  }
}
```

## Preview Locally

`fetch()` requires serving the folder over HTTP:

```bash
cd benchmark/server/dashboard
python -m http.server 8000
```

Open `http://127.0.0.1:8000/`.

## Deployment

After generating `data/leaderboard.json`, deploy the dashboard directory as static files.
