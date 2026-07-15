# Audit: customEvents consumers vs. `name` discrimination

**Date:** 2026-07-15 · **Trigger:** page_engagement beacon proposal
(`docs/superpowers/specs/2026-07-15-page-engagement-heartbeat-design.md` §4, V3)

**Question:** which consumers read the Application Insights `customEvents`
stream without filtering on the event-family discriminator `name` — and would
therefore silently inflate their numbers when the `page_engagement` family
ships?

## Scope

Our repositories: `Clicks/`, `CampaignWe/`, `Databricks/` (including
`SiteOwnerDashboard/`). NOT covered: the platform team's bronze→gold
notebooks — flagged as their checklist item in the proposal (spec §4, V3);
we have no visibility into that code (per `imep_pipeline_ops_q28_findings`,
even DESCRIBE HISTORY only shows write timestamps, not query logic).

**Grep command** (from `/Users/micha/Documents/Arbeit`):

```bash
grep -rn -iE "customevents" \
  --include="*.kql" --include="*.py" --include="*.sql" \
  --include="*.js" --include="*.html" --include="*.md" \
  Clicks/ CampaignWe/ Databricks/ \
  | grep -v -E "\.git/|node_modules/|docs/superpowers/|/audits/"
```

133 matching lines across **26 distinct files**. Beyond the brief's own
exclusions (`.git/`, `node_modules/`, `docs/superpowers/`, `/audits/`), no
further exclusions were needed: this task's own brief/report live under
`.superpowers/sdd/` (not `docs/superpowers/`) and were confirmed to contain
zero `customevents` hits, so no additional exclusion rule was required.

## Findings

### Real consumers (code that reads or processes customEvents data)

| File | How it reads customEvents | name-filtered? | Risk when page_engagement ships | Action |
|---|---|---|---|---|
| `Clicks/clicks_query.kql` (all 7 queries, e.g. :35, :106, :157, :211, :266, :290) | KQL `where name == "click_event"` on every query before any projection/aggregation | yes | none | none |
| `Clicks/clicks_explorer.kql` (queries 0a,0b,2–13, e.g. :39, :71, :110, :128, :138, :265, :277) | KQL `where name == 'click_event'` guard on every metric query | yes | none | none |
| `Clicks/clicks_explorer.kql:99-102` (Query 1 "Event name discovery") | `customEvents \| summarize EventCount = count() by name` — intentionally spans all families to inventory event names | n/a — by design | none | none — **profiling, by design, no risk** |
| `Databricks/kql/customevents_clicks.kql:24-73` (Query 0, the live exporter) | KQL `where name == 'click_event'` before flatten/project | yes | none | none |
| `Databricks/kql/customevents_clicks.kql:76-83` (Query 1, commented-out) | `customEvents \| summarize EventCount = count() by name` — event-family inventory, explicitly documented as "discover which event families exist besides click_event" | n/a — by design | none | none — **profiling, by design, no risk** |
| `Databricks/kql/validate_page_engagement.kql:16-19` (V1) | KQL `where timestamp > ago(7d) and name == 'click_event'` | yes | none | none |
| `Databricks/kql/validate_page_engagement.kql:36-41, 47-51` (V2, V3) | `union pageViews, customEvents \| summarize ... by itemType` — compares stream-level volumes (pageViews vs. customEvents), not per-`name` metrics | n/a — by design | none | none — **profiling/baseline query, by design, no risk**. This file is itself the spec's own V1–V3 validation harness. |
| `Databricks/SiteOwnerDashboard/export_site_interactions.kql:39-55` | KQL `where name == 'click_event'`, explicitly commented "Scope: name == 'click_event' only. Search events ... are out of scope for this dashboard phase" | yes | none | none |
| `Databricks/SiteOwnerDashboard/scripts/process_site_interactions.py:193-200` | pandas: `if others: clean = clean[clean["event_name"] == "click_event"]` (warns and drops any other family before it reaches the output parquet) | yes | none | none — confirmed as the brief's known-guarded example |
| `Databricks/SiteOwnerDashboard/scripts/analyze_click_volume.py:84-91` | pandas: `clean = clean[clean["event_name"] == "click_event"]` (same drop-non-click pattern, read-only diagnostic script) | yes | none | none |
| `Databricks/SiteOwnerDashboard/scripts/rename_exports_by_timerange.py` | Renames/routes AppInsights export files by min/max timestamp and **column-set** (`url`/`duration`/`itemType` present → pv, absent → ce). Does not read or filter on `name` values at all — the docstring (:27-34) explicitly explains why `name` is *not* a reliable discriminator for this purpose. | n/a — not a metrics consumer | none | none — **file-routing utility, computes no counts, no risk** |
| `Databricks/SiteOwnerDashboard/dashboard/dashboard.html` (:829, :940 — comments only) | Reads the **already-filtered** `output/site_interactions.parquet` produced by `process_site_interactions.py` (click_event-only); the two grep hits are code comments referencing "customEvents" conceptually, not a live query against the raw stream | n/a — inherits upstream filter | none, contingent on the upstream guard in `process_site_interactions.py` staying intact | none |
| `Databricks/SiteOwnerDashboard/output/site_dashboard_standalone.html` (:907, :1018 — comments only) | Bundled/standalone build of `dashboard.html` (same two comments, shifted line numbers); same conclusion as above | n/a — inherits upstream filter | none | none |
| `Databricks/scripts/flatten_appinsights.py:106-154, 365-393` | Shared flatten/rename utility. Renames source `name` → `event_name` and **keeps** it (unlike the pageViews variant, which drops it) specifically so callers can filter on the event-family discriminator (:116-118 comment). Computes no counts or aggregates itself — it is not itself a metrics consumer, its callers are. | n/a — not a metrics consumer; preserves the discriminator for callers | none (both current callers — `process_site_interactions.py`, `analyze_click_volume.py` — do filter) | none |
| `Clicks/scripts/generate_seed.py:6` | Docstring comment only ("Produces a CSV in the same shape as a KQL export... `customEvents \| where name == "click_event"`"); the script generates **synthetic** seed data and never reads the real customEvents stream | n/a — not a consumer | none | none |
| `CampaignWe/schema_explorer.kql:17-23` (Query 0) | `customEvents \| ... \| summarize count() by name` for one PageURL — explicit event-name discovery | n/a — by design | none | none — **profiling, by design, no risk** |
| `CampaignWe/campaignwe_query.kql:30, 79, 733, 754` (ClickData, Query C anonymized export, Query E, Query D) | KQL `where name == 'click_event'` guard before flatten/aggregate | yes | none | none |
| `CampaignWe/campaignwe_query.kql:122-177, 342-438` (Query F raw multi-family export; Query I daily HLL aggregation) | `customEvents` read with **no** `where name` filter, but `name` is retained as an explicit output column (Query F) / GROUP BY key (Query I, e.g. :408, :428). A `page_engagement` row surfaces as its own row/value — it is never summed into `click_event`'s count. | no — but harmless | none — new family adds new rows, doesn't inflate existing ones | none required; recommend a one-line comment noting `page_engagement` will appear as a new `name` value once shipped, for analyst awareness |
| `CampaignWe/campaignwe_query.kql:630-725`, specifically **line 677** (Query L, session-level aggregation) | KQL: `isSearch = name startswith "SEARCH"`, `isVideo = customDimensions has "Video_Action"`, then **`ClickCount = countif(not(isSearch) and not(isVideo))`** — the click bucket is defined by exclusion, never by `name == 'click_event'` | **NO** | **INFLATED** — any future event that is not SEARCH-prefixed and carries no `Video_Action` key (this is exactly what `page_engagement` looks like per the spec's schema, §3) falls into `ClickCount` by default | **Add filter** — replace the exclusion logic with an explicit `isClick = name == 'click_event'` and use that for `ClickCount`, or extend the exclusion to name every non-click family (including `page_engagement`) before Phase 1 ships |

### Pure documentation (mentions `customEvents`, reads nothing)

One collective row per the brief's instruction — these files describe or
reference the `customEvents` schema/pipeline in prose but contain no
executable query or code path that reads the stream unguarded (any embedded
sample KQL/SQL snippets they quote are themselves guarded, e.g.
`docs/tables/sharepoint/customevents.md:69` states the rule explicitly:
*"For click analytics, always filter `name == 'click_event'`"*).

| Files | Nature | Risk |
|---|---|---|
| `CampaignWe/docs/data-pipeline.md`, `Clicks/docs/data-pipeline.md`, `Databricks/SiteOwnerDashboard/DESIGN.md`, `Databricks/SiteOwnerDashboard/README.md`, `Databricks/docs/diagrams/er_sharepoint_bronze.md`, `Databricks/docs/knowledge_base.md`, `Databricks/docs/tables/sharepoint/customevents.md`, `Databricks/docs/tables/sharepoint_gold/pbi_db_interactions_metrics.md`, `Databricks/kql/README.md`, `Databricks/docs/fitnesse/preview/index.html`, `Databricks/docs/fitnesse/preview/Diagrams.SharePointBronze.html`, `Databricks/docs/fitnesse/preview/DataGlossary.SharePointGold.PbiDbInteractionsMetrics.html` (12 files; the three `.html` files are rendered FitNesse wiki exports of the same underlying docs, not separate authored content) | Schema cards, ER diagrams, pipeline READMEs, and rendered wiki previews that describe `customevents`/`click_event` for humans | none — not consumers |

Two of these docs (`pbi_db_interactions_metrics.md:131` and the matching
`DataGlossary...html:168`) describe the lineage
`sharepoint_bronze.customevents + pageviews → sharepoint_silver.webpagevisited
→ sharepoint_gold.pbi_db_interactions_metrics` — that is the **platform
team's** gold pipeline, explicitly out of scope for this audit (see Scope),
called out here only because our docs reference it.

## Verdict

26 distinct files matched the grep. Of these, **14 are real consumers**
(code that reads, filters, aggregates, or routes customEvents data) and
**12 are documentation-only mentions** (collapsed to one row above per the
brief's instruction). Of the 14 real consumers, **13 are safe**: 11 carry an
explicit `name == 'click_event'` (or `startswith "SEARCH"`) guard before any
counting happens, 1 is a profiling/inventory query that intentionally spans
all families (`schema_explorer.kql`, `clicks_explorer.kql` Query 1,
`customevents_clicks.kql` Query 1, `validate_page_engagement.kql` V2/V3 — by
design, no risk), and the remaining safe files (`flatten_appinsights.py`,
`rename_exports_by_timerange.py`, `generate_seed.py`, `dashboard.html`,
`site_dashboard_standalone.html`) either preserve the discriminator for their
callers, don't compute business counts at all, or inherit an upstream guard.

**One genuine unguarded consumer was found**: `CampaignWe/campaignwe_query.kql`
line 677 (Query L, session-level aggregation) computes
`ClickCount = countif(not(isSearch) and not(isVideo))` — a click bucket
defined by *excluding* known non-click families rather than by *matching*
`name == 'click_event'`. Because `page_engagement` events are neither
SEARCH-prefixed nor carry `Video_Action`, they would silently fall into
`ClickCount` the moment the new family starts flowing — precisely the
"legacy consumer counts engagement as clicks" risk the design spec's §7 risk
table names. This must be fixed (replace the exclusion logic with an
explicit `name == 'click_event'` check) before Phase 1 rollout reaches any
site whose CampaignWe-style session query is in active use. It is a **query
template in a `.kql` scratch file**, not a scheduled pipeline — so the fix is
a one-line edit to the query text, with no deployed job to patch.

No issues were found in the production dashboard pipeline
(`SiteOwnerDashboard/scripts/process_site_interactions.py`, the scheduled
consumer that actually populates the dashboard) — it already guards
correctly and is the reference implementation the spec's §5 pipeline changes
build on.
