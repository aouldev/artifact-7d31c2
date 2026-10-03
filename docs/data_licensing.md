# Source and data licensing

The benchmark contains repository-derived production diffs, commit text and
training examples. Result records may also contain repository-derived patches
or snippets. Those portions retain their upstream copyrights and applicable
licenses; the artifact's MIT license covers its original implementation and
associated documentation only.

The table identifies the upstream repositories and the reference revisions
used for the bundled notices. Exact source paths, full revisions and SHA-256
values are in [upstream_sources.csv](../licenses/upstream_sources.csv).
An episode's `base_commit`, changed paths and any file-specific notice determine
the applicable historical source terms. The reference texts do not establish
that every file at every episode revision has the same license.

| Dataset identifier | Upstream repository | Reference license | Revision | Local notices |
|---|---|---|---|---|
| `activepieces` | [activepieces/activepieces](https://github.com/activepieces/activepieces) | MIT with enterprise exclusions | `d6f673aa50c7` | [license texts](../licenses/upstream/activepieces.txt) |
| `affine` | [toeverything/AFFiNE](https://github.com/toeverything/AFFiNE) | MIT with directory-specific exceptions | `60acd81d4b8a` | [license texts](../licenses/upstream/affine.txt) |
| `cal.com` | [calcom/cal.com](https://github.com/calcom/cal.com) | AGPLv3 with commercial-directory exceptions | `fb364971fadc` | [license texts](../licenses/upstream/cal.com.txt) |
| `chatwoot` | [chatwoot/chatwoot](https://github.com/chatwoot/chatwoot) | MIT with enterprise exclusions | `ed0e87405cfa` | [license texts](../licenses/upstream/chatwoot.txt) |
| `directus` | [directus/directus](https://github.com/directus/directus) | Business Source License 1.1 | `0e0a7b5259bb` | [license texts](../licenses/upstream/directus.txt) |
| `documenso` | [documenso/documenso](https://github.com/documenso/documenso) | AGPLv3 | `bac2bf11f415` | [license texts](../licenses/upstream/documenso.txt) |
| `formbricks` | [formbricks/formbricks](https://github.com/formbricks/formbricks) | AGPLv3 with MIT and enterprise exceptions | `b3c16c87319e` | [license texts](../licenses/upstream/formbricks.txt) |
| `formkit` | [formkit/formkit](https://github.com/formkit/formkit) | MIT | `460db8c299c3` | [license texts](../licenses/upstream/formkit.txt) |
| `grafana` | [grafana/grafana](https://github.com/grafana/grafana) | AGPLv3 | `3a73f665ee44` | [license texts](../licenses/upstream/grafana.txt) |
| `hoppscotch` | [hoppscotch/hoppscotch](https://github.com/hoppscotch/hoppscotch) | MIT | `0ce64684340e` | [license texts](../licenses/upstream/hoppscotch.txt) |
| `joplin` | [laurent22/joplin](https://github.com/laurent22/joplin) | AGPL-3.0-or-later with subdirectory exceptions | `ef32d53f72fe` | [license texts](../licenses/upstream/joplin.txt) |
| `medusa` | [medusajs/medusa](https://github.com/medusajs/medusa) | MIT | `237b472e7331` | [license texts](../licenses/upstream/medusa.txt) |
| `payload` | [payloadcms/payload](https://github.com/payloadcms/payload) | MIT | `9239164e8409` | [license texts](../licenses/upstream/payload.txt) |
| `rallly` | [lukevella/rallly](https://github.com/lukevella/rallly) | AGPLv3 | `becb1d4bb0db` | [license texts](../licenses/upstream/rallly.txt) |
| `requestly` | [requestly/requestly](https://github.com/requestly/requestly) | AGPLv3 with enterprise exceptions | `6f5588c5c15a` | [license texts](../licenses/upstream/requestly.txt) |
| `rocketchat` | [RocketChat/Rocket.Chat](https://github.com/RocketChat/Rocket.Chat) | MIT with enterprise exceptions | `2356c889ed82` | [license texts](../licenses/upstream/rocketchat.txt) |
| `saleor` | [saleor/saleor-dashboard](https://github.com/saleor/saleor-dashboard) | BSD-3-Clause | `44bcc083f990` | [license texts](../licenses/upstream/saleor.txt) |
| `storybook` | [storybookjs/storybook](https://github.com/storybookjs/storybook) | MIT | `57a3301edd2c` | [license texts](../licenses/upstream/storybook.txt) |
| `strapi` | [strapi/strapi](https://github.com/strapi/strapi) | MIT with enterprise and component exceptions | `7861fc41cbe5` | [license texts](../licenses/upstream/strapi.txt) |
| `supabase` | [supabase/supabase](https://github.com/supabase/supabase) | Apache-2.0 | `4844e96dcf88` | [license texts](../licenses/upstream/supabase.txt) |
| `typebot` | [botpress/botpress](https://github.com/botpress/botpress) | MIT | `0a3b9c04ee16` | [license texts](../licenses/upstream/typebot.txt) |
| `umami` | [umami-software/umami](https://github.com/umami-software/umami) | MIT at the listed reference revision | `a3733b042472` | [license texts](../licenses/upstream/umami.txt) |
| `vendure` | [vendure-ecommerce/vendure](https://github.com/vendure-ecommerce/vendure) | GPLv3-or-later with plugin exception; commercial option | `090db23464c0` | [license texts](../licenses/upstream/vendure.txt) |

## Identifiers and license scope

The dataset identifier `saleor` refers to Saleor Dashboard. The identifier
`typebot` is retained in the released records, while the referenced source
revision and license belong to Botpress. These identifiers are record keys;
the upstream links above identify the source projects for attribution.

Several projects use separate enterprise, commercial or component licenses.
Those exceptions, as well as notices in embedded third-party components, take
precedence over a repository's default license. Directus uses Business Source
License terms at the listed reference revision. Public source availability is
not a grant of unrestricted reuse.

For Umami, the bundled text is from a later released base revision containing
the LICENSE file. It is not evidence that the earliest historical revision
included that notice.

When using or redistributing repository-derived material, retain the relevant
copyright and license notices and comply with its applicable source terms.
The original repositories supply the corresponding source history. The
bundled notices preserve upstream copyright holders and may contain their
names or contact addresses; these are source attributions.
