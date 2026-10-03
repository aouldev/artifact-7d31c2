# Repository execution profiles

Pass this directory to `--profiles-dir` from the artifact root. It contains
18 repository configurations plus `demo.json` for the model-free Node example.
`example.yaml` illustrates the same profile schema. The evaluator selects a
profile by its `repo` field and the first runner matching the target test path.

Configurations describe install commands, loading/build checks, test commands
and coverage collection. They require the episode's upstream revision, its
package-manager lockfile and compatible toolchains. The profile parser and
shell syntax are checked for every configuration; the local Node example is
executed end to end. This does not establish a fresh dependency rebuild for
all upstream repositories.

Available repository profiles: `affine`, `cal.com`, `chatwoot`, `directus`,
`documenso`, `formbricks`, `formkit`, `grafana`, `hoppscotch`, `medusa`, `payload`,
`rallly`, `requestly`, `saleor`, `strapi`, `supabase`, `typebot` and `vendure`.
The benchmark's remaining repositories (`activepieces`, `joplin`, `rocketchat`,
`storybook`, `umami`) have no positive episodes in the released maintenance
annotation set and no execution profile in this directory. They remain part of
intent evaluation; a generated positive patch for them needs a host profile.

## Environment and services

Choose toolchains and caches in the caller's environment. Chatwoot requires
Ruby/Bundler and its application services; JavaScript repositories require the
package manager specified by their upstream lockfiles. Browser runners also
require the corresponding Playwright browser installation.

Service-backed runners require dedicated local test services and application
configuration supplied by the host. The following keys are inherited rather
than embedded in the profiles:

| Repository | Host environment inputs |
| --- | --- |
| cal.com | `DATABASE_URL`, `DATABASE_DIRECT_URL`, `NEXTAUTH_SECRET`, `CALENDSO_ENCRYPTION_KEY`; unit fixtures also use `CALCOM_SERVICE_ACCOUNT_ENCRYPTION_KEY` and `DAILY_API_KEY` |
| documenso | `DATABASE_URL`, `NEXT_PRIVATE_DATABASE_URL`, `NEXT_PRIVATE_DIRECT_DATABASE_URL`, `NEXTAUTH_SECRET`, `NEXT_PRIVATE_ENCRYPTION_KEY`, `NEXT_PRIVATE_ENCRYPTION_SECONDARY_KEY` |
| formbricks | `DATABASE_URL`, `MIGRATE_DATABASE_URL`, `REDIS_URL`, `ENCRYPTION_KEY`, `NEXTAUTH_SECRET`, `CRON_SECRET` |

Use disposable test databases with the necessary permissions and schema.
The host prepares services; the browser commands run migrations and, for
cal.com, seed test data. They do not kill existing port listeners, replace
Docker containers, or drop an existing database/schema. Application URLs such
as `http://localhost:3000` refer to the local test server, not an external API.

## Placeholders and coverage

Common placeholders are `{{repo_root}}`, `{{test_file}}`,
`{{test_file_from_cwd}}`, `{{test_name}}`, `{{test_dir}}`,
`{{nearest_package_dir}}` and `{{output_dir}}`. The runner also supports the
legacy aliases used by existing configurations, including `{{assetPath}}`,
`{{assetPathFromCwd}}`, `{{coverageDir}}` and `{{nearestPackageDir}}`.

Coverage is emitted by the configured runner and collected by the evaluator.
Preload assets referenced under `{{output_dir}}` are supplied by
`benchmark/server/tools/dynamic_runner.py`; their source files are included in
`experiments/configs/shims/`. A missing artifact or unloaded test is recorded as
such rather than counted as successful coverage.
