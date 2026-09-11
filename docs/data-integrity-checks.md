# Data Integrity Checks — Intranet Telemetry Pipeline

Status: **proposal**, 2026-09-09, revised 2026-09-11 against the workspace.

| Companion | What it is for |
|---|---|
| [`data-integrity-checks.html`](data-integrity-checks.html) | Executive one-pager: the April incident and the ratio watchlist |
| [`integrity-checks-by-example.html`](integrity-checks-by-example.html) | **Each check on a worked example**, healthy data beside the failure it catches. Read this to understand what a check means without reading SQL |
| [`../dq_checks_draft.sql`](../dq_checks_draft.sql) | The SQL per check. Table map in §9 |

Scope: the `pageViews` (and, where noted, `customEvents`) telemetry that flows
Application Insights → Staging → Bronze → Silver → Gold → semantic layer → Power BI.

---

## 1. Why — the April incident in one paragraph

Around **8 April 2026** the identity behaviour of the telemetry changed: the
browser identifier (`user_Id`) stopped being persistent for a person, and with
it every page view arrived with a fresh `session_Id`. The effect on the reported
numbers was silent but severe: **one page view = one visit**. Page view totals
stayed correct, so the dashboards looked plausible; visits inflated, pages per
visit and time on page collapsed. **Unique visitors were not affected**: they are
not counted on `user_Id` but on the employee identifier and e-mail from the
telemetry (`CustomProps.GPN` / `Email`), matched to HR data and transformed to a
contact ID. The change was
found by people, not by the pipeline. A single ratio — page views per visit,
normally 1.1–1.2 — dropped to 1.0 on day one and would have flagged it
immediately. This document turns that lesson into a check catalogue.

What the pipeline already knows (measured on the corp store in July 2026, i.e.
after the change): ~92 % of official sessions span exactly one view, `user_Id`
is near-unique per view, and the derived `person_id` (employee identifier, else
anonymous device) + `visit_id` (30-minute inactivity rule) grouping is the usable
unit for engagement — see `scripts/flatten_appinsights.py` and
`SiteOwnerDashboard/scripts/reconcile_visit_session.py`.

---

## 2. Design principles

1. **Ratios over counts.** Absolute volumes move with the business calendar;
   ratios between fields (views ÷ visits, visits ÷ visitors, devices ÷ person)
   are stable by construction and break sharply when the data model breaks.
2. **Every check answers one of four questions.** Did everything arrive? Do we
   still recognise people? Does the data still look like the data? Do the
   numbers make sense? A check that answers none of them is not a check.
3. **Baseline = trailing 8 weeks, same weekday, robust statistics.** Median and
   MAD (median absolute deviation), not mean and standard deviation; a corridor
   is `median ± 3·MAD`, floored at a minimum width. Holidays come from a
   calendar table. No alerting before 4 weeks of history exist.
4. **Step change beats point outlier.** For ratios, compare the 7-day mean with
   the prior 28-day mean; a persistent shift is the signal, one noisy day is not.
5. **Three severities, three actions.** Info (annotate the day), Warning (notify
   the data owner, keep publishing, badge in Power BI), Blocker (hold the Gold /
   semantic refresh, banner in Power BI, open an incident).
6. **Check at the earliest layer that can answer.** Sampling and schema at
   Staging, volume and nulls at Bronze, identity ratios at Silver, plausibility
   and reconciliation at Gold, tie-out at the semantic layer.
7. **Store every result.** One row per day × check in `gold.dq_check_result`;
   alerts read from that table, and so does a "Data Health" page in Power BI.
8. **Ground truth beats proxy.** We have a server-side employee identifier
   (`CustomProps.GPN`) on most views. It is independent of the browser cookie
   and is the anchor for the strongest identity checks.

---

## 3. What each field can tell us

| Field (source) | Used for | Integrity signal it carries |
|---|---|---|
| `timestamp` | everything | freshness, daily/hourly volume, seasonality |
| `id` (view id) | primary key | duplicate rate — **known not to be event-unique in exports**, so the composite key (second-truncated timestamp + user + session + page) is the dedup key |
| `session_Id` | visits (company standard since 2026-07-13) | views per visit, single-view share, session timeout behaviour |
| `user_Id` | browser identity (monitoring only, not a reporting key) | recurring-identity rate, visits per browser identity, devices per person |
| `CustomProps.GPN`, `Email` | person → HR match → contact ID; **the unique-visitor unit** | identified share, devices per person, HR hit rate — the cookie-independent ground truth |
| `sdkVersion` | — (not used in reporting today) | **leading indicator**: a new SDK version or config release precedes behaviour changes such as April |
| `iKey`, `appId` | — | instrumentation key must be constant; a new key means a different app or a config change |
| `itemCount` | — | sampling factor: `sum(itemCount) ÷ count(*)` must be exactly 1.00 |
| `client_Type`, `client_OS`, `client_Browser` | drill-downs | client mix drift — a browser roll-out that changes cookie policy shows here first |
| `client_CountryOrRegion`, `client_City` | drill-downs | geo mix drift |
| `operation_Id`, `operation_Name` | — | one id per page load; helps separate double-fires from real navigation |
| `name`, `url`, `CustomProps.PageURL`, `PageId`, `SiteID`, `SiteName` | page dimension | referential integrity to the page inventory, top-page stability, language-redirect double-fires |
| `CustomProps.CammsTrackingID` | cross-channel join | coverage share, format validity (5 segments) |
| `CustomProps.PublishingDate`, `ContentType`, `ContentOwner`, `Theme`, `Topic` | content dims | validity (publish date ≤ view), null rates, category mix |
| `refUri` | referrer | referrer mix drift |
| `duration`, `performanceBucket` | performance | plausibility only (not integrity) |
| `customEvents` (clicks, search, video) | interactions | clicks per view ratio; an independent stream with the same identity fields |

---

## 4. Check catalogue

Severity legend: **I** Info · **W** Warning · **B** Blocker. "April?" = would
this check have detected the 8 April identity change, and how fast.

### A. Completeness & freshness — "Did everything arrive?"

| ID | Check | Formula / rule | Fields | Layer | Threshold | Sev | April? |
|---|---|---|---|---|---|---|---|
| A1 | Daily volume corridor | `count(*)` per day vs median of the same weekday over the trailing 8 weeks | `timestamp` | Bronze | outside ±25 % → W; outside ±50 % → B | W/B | No — volume looked normal |
| A2 | Freshness per layer | `now() − max(timestamp)` and `now() − max(ingested_at)` per table | `timestamp`, ingestion ts | every layer | > 24 h → W; > 48 h → B | W/B | No |
| A3 | Hourly arrival profile | share of views per hour-of-day vs 8-week baseline; flags partial-day loads | `timestamp` | Bronze | any hour < 30 % of baseline on a business day → W | W | No |
| A4 | Sampling factor | `sum(itemCount) ÷ count(*)` | `itemCount` | Staging | ≠ 1.00 → B (adaptive sampling switched on; counts understate) | B | No |
| A5 | Duplicate rate | share of rows repeated on the composite key | `timestamp`, `user_Id`, `session_Id`, `PageURL` | Staging → Bronze | > 1 % → W; > 5 % → B | W/B | No |
| A6 | Layer tie-out | `count(Bronze) = count(Staging) − documented filters`; `sum(Gold.views) = count(Silver)` per day; semantic measure = Gold | row counts | every hop | any difference → B | B | No |
| A7 | Late-arrival drift | recount of day D after 3 days vs first load | `timestamp` | Bronze | > 2 % growth → W (backfill window too short) | W | No |

### B. Identity & sessionisation — "Do we still recognise people?"

These checks read the raw browser and session identifiers. They are **monitoring
signals**, not reported KPIs: unique visitors are counted on the contact ID and
stay correct even when every signal below breaks; visits (on `session_Id`) do not.

| ID | Check | Formula / rule | Fields | Layer | Threshold | Sev | April? |
|---|---|---|---|---|---|---|---|
| B1 | **Page views per visit** | `count(*) ÷ count(distinct session_Id)` per day | `session_Id` | Silver | baseline 1.1–1.2; < 1.05 → W; ≤ 1.02 → B; 7-day mean shift > 10 % vs prior 28 days → W | W/B | **Yes — day 1** |
| B2 | Visits per browser identity (7-day) | `count(distinct session_Id) ÷ count(distinct user_Id)` over a rolling week | `session_Id`, `user_Id` | Silver | → 1.00 → B; shift > 15 % → W | W/B | Yes — day 1–2 |
| B3 | **Recurring browser-identity rate** | share of today's distinct `user_Id` seen in the previous 28 days | `user_Id`, `timestamp` | Silver | drop > 20 pp vs baseline → B | B | **Yes — day 1**, the most direct test of cookie persistence |
| B4 | **Devices per person** | `count(distinct user_Id) ÷ count(distinct GPN)` per day, views with GPN only | `user_Id`, `GPN` | Silver | baseline ≈ 1.0–1.3 (laptop + phone); > 2 → W; ≈ views per person → B | W/B | **Yes — day 1**; unique to our setup because GPN is server-side ground truth |
| B5 | Single-view session share | sessions with exactly one view ÷ all sessions | `session_Id` | Silver | shift > 10 pp → W; > 98 % → B | W/B | Yes — day 1 (reads as bounce rate for the business) |
| B6 | Session timeout behaviour | `P(same session_Id \| gap to the same person's previous view < 30 min)` | `session_Id`, `person_id`, `timestamp` | Silver | < 50 % → B | B | Yes; existing script `detect_session_timeout.py` |
| B7 | Official vs reconstructed visits | `count(distinct session_Id) ÷ count(distinct visit_id)` (person + 30-min rule) | `session_Id`, `visit_id` | Silver | baseline ≈ 1.0–1.3; > 1.5 → W; > 2 → B | W/B | Yes — day 1; uses the pipeline's own capability |
| B8 | Identified share | views with a valid GPN ÷ all views | `GPN` | Bronze | drop > 5 pp → W; > 15 pp → B | W/B | No, but a drop degrades every person-based metric |

### C. Schema & field validity — "Does the data still look like the data?"

| ID | Check | Formula / rule | Fields | Layer | Threshold | Sev | April? |
|---|---|---|---|---|---|---|---|
| C1 | Schema drift | set of top-level columns and `CustomProps` keys vs the registered contract | `customDimensions` | Staging | new key → I; missing key → B | I/B | No |
| C2 | Null rate per critical field | null share per day vs baseline for `session_Id`, `user_Id`, `GPN`, `PageURL`, `PageId`, `SiteID`, `PublishingDate`, `CammsTrackingID` | listed | Bronze | +5 pp → W; `session_Id` / `user_Id` / `PageURL` > 1 % null → B | W/B | No |
| C3 | **SDK version watch** | distribution of `sdkVersion` per day; new value or share shift | `sdkVersion` | Staging | new version → I + annotate the day; share shift > 20 % in a day → W | I/W | **Likely, as a leading indicator** — check whether a version change coincides with 8 April |
| C4 | Instrumentation key constant | `count(distinct iKey)`, `count(distinct appId)` per day | `iKey`, `appId` | Staging | any value outside the known set → B | B | Possibly |
| C5 | Format validity | GPN = 8 digits; `CammsTrackingID` = 5 segments; `PublishingDate ≤ timestamp`; `timestamp` inside the load window; `PageURL` parseable and on the known host | listed | Staging (DLT expectations) | > 0.5 % invalid → W; > 5 % → B | W/B | No |
| C6 | Referential integrity | `PageId` found in the page inventory; `SiteID` in sites; GPN found in the month's HR snapshot (HR hit rate) | `PageId`, `SiteID`, `GPN` | Silver | hit rate drop > 5 pp → W | W | No |
| C7 | Client mix drift | shares of `client_Browser` / `client_OS` / `client_Type` vs 4-week baseline | `client_*` | Bronze | shift > 15 pp → I/W | I/W | Possibly — a browser or policy roll-out that blocks cookies shows here |
| C8 | Double-fire rate | pairs of views of the same page by the same person < 1 s apart ÷ views | `person_id`, `PageURL`, `timestamp` | Silver | rise > 5 pp → W | W | No |

### D. Plausibility & reconciliation — "Do the numbers make sense?"

| ID | Check | Formula / rule | Fields | Layer | Threshold | Sev | April? |
|---|---|---|---|---|---|---|---|
| D1 | KPI corridors | views, visits, unique visitors, pages per visit, avg time on page, bounce rate — each vs 8-week same-weekday median ± 3·MAD, plus 7-day-vs-28-day step change | Gold KPIs | Gold | outside corridor → W; step change > 25 % → B | W/B | Yes — visits jumped and engagement collapsed while views and unique visitors stayed flat; that pattern alone points at the session id |
| D2 | Weekday pattern | weekend ÷ weekday volume ratio vs baseline | `timestamp` | Gold | shift > 50 % → I | I | No |
| D3 | Top-page stability | Jaccard overlap of the top-50 pages with the prior week | `PageURL` | Gold | < 0.5 → W (URL scheme or tagging change) | W | No |
| D4 | Tracking coverage | views with `CammsTrackingID` ÷ views; tracked pages ÷ pages | `CammsTrackingID` | Gold | drop > 5 pp → W | W | No |
| D5 | **Gold matches a fresh recount from bronze** | per page-day for the top pages: `sharepoint_gold.pbi_db_interactions_metrics` (`views`, `visits`, distinct `viewingcontactid`) vs a recount on `sharepoint_bronze.pageviews` (`count(*)`, distinct `session_Id`, distinct `user_gpn`) joined on `marketingPageId = pageId` | bronze + gold | Gold | ratio outside 0.8–1.25 → W; visits ratio > 2 or people ratio outside → B | W/B | **Yes — day 1** as a pattern: visits diverge while people agree, which localises the fault to the session cookie. Gold is derived from the same App Insights bronze, so this is a transformation tie-out, not an independent collector |
| D6 | Funnel plausibility | for tracked packs: intranet views on the landing page ≥ email clicks to that page (order of magnitude) | `CammsTrackingID`, iMEP clicks | Gold | views < 50 % of clicks → W | W | No |
| D7 | Clicks per view | `customEvents` clicks ÷ `pageViews` per day | both streams | Gold | shift > 25 % → W | W | No — but separates "views dropped" from "clicks dropped" |
| D8 | Power BI tie-out | KPI value in the semantic layer vs Gold for the same day (control measure or dataset query) | Gold, dataset | semantic | any difference → B | B | No |

---

## 5. The ratio watchlist (executive subset)

Ten signals, one line each. These are the rows on the one-pager.

| # | Signal | Plain-language meaning | Healthy | Broken looks like | April? |
|---|---|---|---|---|---|
| 1 | Page views per visit (B1) | How many pages a visit contains | 1.1–1.2, stable | exactly 1.0 | Yes, day 1 |
| 2 | Recurring browser identities (B3) | Share of today's browser identities seen in the last 4 weeks | high and stable | near 0 % | Yes, day 1 |
| 3 | Devices per employee (B4) | Browser identities per employee identifier and day | about 1 | equals page views | Yes, day 1 |
| 4 | Visits per browser identity, weekly (B2) | How many visits one browser accumulates in a week | clearly above 1 | exactly 1.0 | Yes, day 1–2 |
| 5 | Cookie visits vs employee visits (B7) | Do the two ways of counting visits agree | about 1 | inflates | Yes, day 1 |
| 6 | Gold matches a fresh recount (D5) | Visits and people in Gold equal a recount from the raw events | about 1 | visits diverge, people agree | Yes, day 1 |
| 7 | Software version watch (C3) | Did the tracking software change | unchanged | new version appears | Leading indicator |
| 8 | Arrival volume (A1) | Did today's data arrive in the expected amount | within ±25 % of the same weekday | outside the corridor | No |
| 9 | Sampling factor (A4) | Is every event recorded, or only a sample | exactly 1.00 | above 1.00 | No |
| 10 | Layer tie-out (A6, D8) | The number in Power BI equals the number in Gold, Silver, Bronze | exact | any gap | No |

---

## 6. Operating model

### Severity → action

| Severity | Publishing | Power BI | People |
|---|---|---|---|
| Info | continues | none; the day is annotated in the check table | none |
| Warning | continues | badge on the Data Health page and on the affected KPI card | data owner notified (Databricks SQL alert) |
| Blocker | **Gold / semantic refresh held** for the affected date range | banner "Data under review since <date>" on every report page | data owner + product owner notified, incident opened |

### Result table

```sql
CREATE TABLE gold.dq_check_result (
  check_date   DATE,
  check_id     STRING,      -- A1 … D8
  family       STRING,      -- completeness | identity | schema | plausibility
  layer        STRING,      -- staging | bronze | silver | gold | semantic
  metric_value DOUBLE,
  baseline     DOUBLE,
  lower_bound  DOUBLE,
  upper_bound  DOUBLE,
  status       STRING,      -- ok | info | warning | blocker
  note         STRING,
  computed_at  TIMESTAMP
);
```

One row per day × check. Alerts, the Data Health page and the incident history
all read from this table; no check result lives only in a log.

### Where each family runs on Databricks

| Family | Mechanism |
|---|---|
| Row-level rules (C2, C5) | Delta Live Tables expectations (`expect` for Warning, `expect_or_fail` for Blocker) on the Staging → Bronze step |
| Aggregate ratios (A, B, D) | one daily SQL/notebook job after the Silver refresh, writing `gold.dq_check_result` |
| Drift (C7, D1) | optional: Lakehouse Monitoring on the Silver fact with custom metrics for the ratios |
| Notification | Databricks SQL alerts on `status IN ('warning','blocker')`; a Power BI Data Health page on the same table |
| Hold | the Gold job reads yesterday's `blocker` rows and skips publishing the affected range |
| SQL drafts | [`dq_checks_draft.sql`](../dq_checks_draft.sql): BLOCK 0 column probes, BLOCK 1 result table + check catalogue, BLOCK 2–3 daily metrics and same-weekday baseline, BLOCK 4 generic corridor engine, BLOCK 5–8 explicit checks, BLOCK 9 alert query, hold view, DLT expectations |

### Make the KPI robust, not only monitored

Unique visitors are already robust: GPN / e-mail from the telemetry are matched
to HR data and transformed to a contact ID, so the April change did not touch
them. Visits are not: they count `session_Id`. The pipeline already derives
`person_id` (employee identifier, else anonymous device) and `visit_id`
(30-minute inactivity rule), which do not depend on the browser cookie.
**Recommendation:** apply the same principle to visits — report them on
`visit_id` — and keep the cookie-based `session_Id` / `user_Id` counts as
monitored signals (B1–B7).
**Decision needed:** the current standard counts visits on `session_Id`
(set 2026-07-13). Changing it changes reported history; either restate or
report both with a clear label.

---

## 7. Phasing

| Phase | Weeks | Checks | Outcome |
|---|---|---|---|
| 1 | 1–2 | B1, B3, B4, A1, A2, A4, A6, C3 | the eight checks that catch an April-type change on day one plus basic completeness; result table and one SQL alert |
| 2 | 3–4 | B2, B5, B7, C1, C2, C5, D1, D5, D8 | full identity family, row-level expectations, cross-collector reconciliation, Power BI tie-out and Data Health page |
| 3 | 5–6 | remaining A, C, D checks; Blocker hold in the Gold job; banner in Power BI | operating model complete |

Before phase 1: calibrate every baseline on the store (at least 8 weeks before
8 April for the "healthy" values), and confirm on `sdkVersion` whether a version
change coincides with the incident.

---

## 8. Open decisions

1. Visit definition for reporting: cookie-based (`session_Id`) or person-based
   (`person_id` + 30-minute rule)? Unique visitors already use the contact ID.
   See §6.
2. Threshold values above are starting points; calibrate on the store and
   review after 4 weeks of running.
3. Owner of alerts and of the Blocker hold (product owner vs. data engineer).
4. Holiday calendar source for the baseline.
5. Whether `customEvents` gets the same identity checks (same fields, separate
   stream) in phase 2 or 3.
6. ~~Name of the staging table or volume.~~ **Resolved 2026-09-11.** There is no
   staging *schema* (Q26 scanned the workspace and found none), but the ingestion
   notebook is parameterised with a landing-zone and a raw-storage container
   alongside bronze, silver and gold. Staging is therefore a storage container,
   not a queryable table. The staging-level checks (A4, A5, C1, C3) stay on
   `sharepoint_bronze.pageviews`, which is a 1:1 append landing of the source.
7. The `[VERIFY]` columns in the SQL draft (`session_Id`, `user_Id`, `sdkVersion`,
   `itemCount`, `iKey`, `appId`, `client_*` on bronze; the GPN / e-mail column on
   `pbi_db_employeecontact`; the timestamp column on `sharepoint_silver.pageviewed`).

---

## 9. Databricks table map and lineage

What the repo documents (inventory of April 2026, `docs/knowledge_base.md`,
`docs/diagrams/er_sharepoint_*.md`, `docs/tables/`). Columns marked **[VERIFY]**
are presumed from the App Insights envelope and the 57-column bronze schema;
BLOCK 0 of the SQL draft confirms them.

### Lineage of the intranet strand

```
App Insights pageViews / customEvents
  → (staging — name not yet documented)
  → sharepoint_bronze.pageviews (173M, 57 cols)  +  sharepoint_bronze.customevents (262M, 62 cols)
  → sharepoint_silver.pageviewed (136M) / pagevisited (105M) / webpagevisited (262M) + dims webpage, website, marketingpage, marketingsite
  → sharepoint_gold.pbi_db_interactions_metrics (84M, page × date × contact)  + pbi_db_pageviewed_metric, pbi_db_pagevisited_metric, pbi_db_datewise_overview_fact_tbl
  → semantic model → Power BI
```

Gold visits and unique visitors are **derived from the same App Insights bronze**.
A bronze-to-gold comparison therefore verifies the transformation (D5), it is
not an independent measurement.

### Relationships the checks rely on

| From | To | Key | Used by |
|---|---|---|---|
| `sharepoint_bronze.pageviews.pageId` | `sharepoint_bronze.pages.pageUUID` | page GUID | C6, D4, D6 |
| `sharepoint_bronze.pages.UBSGICTrackingID` | `imep_bronze.tbl_email.TrackingId` | pack = SEG1-SEG2 | D6 |
| `sharepoint_bronze.pageviews.user_gpn` | `imep_bronze.tbl_hr_employee.WORKER_ID` (→ `T_NUMBER`, `ORGANIZATIONAL_UNIT`) | 8-digit GPN | C6, HR dims |
| `sharepoint_bronze.pageviews.user_gpn` / e-mail | `sharepoint_gold.pbi_db_employeecontact.contactId` **[VERIFY column]** | GPN / e-mail → contact | C6; **the unique-visitor unit** |
| `sharepoint_gold.pbi_db_interactions_metrics.marketingPageId` | `sharepoint_bronze.pages.pageUUID` | page GUID | D5 |
| `sharepoint_gold.pbi_db_interactions_metrics.viewingcontactid` | `sharepoint_gold.pbi_db_employeecontact.contactId` | contact GUID | D1c |

### Which table and columns each check reads

| Check | Table(s) | Columns | Column status |
|---|---|---|---|
| A1, A3, A7 | `sharepoint_bronze.pageviews` | `ViewTime` | verified |
| A2 | bronze `ViewTime`; gold `visitdatekey`; silver timestamp | — | silver column [VERIFY] |
| A4 | bronze | `itemCount` | [VERIFY] |
| A5 | bronze | `Id`, `ViewTime`, `user_Id`, `session_Id`, `pageId` | `user_Id`, `session_Id` [VERIFY] |
| A6 | bronze, `sharepoint_silver.pageviewed`, gold `views` | row counts per day | silver column [VERIFY] |
| B1, B2, B5, B6, B7 | bronze | `session_Id`, `user_Id`, `user_gpn`, `ViewTime` | `session_Id`, `user_Id` [VERIFY] |
| B3 | bronze | `user_Id`, `ViewTime` | [VERIFY] |
| B4, B8 | bronze | `user_Id`, `user_gpn` | `user_Id` [VERIFY] |
| C1 | `information_schema.columns` | column set of `pageviews`, `customevents` | verified |
| C2 | bronze | null shares of the key fields | mixed |
| C3, C4 | bronze | `sdkVersion`, `iKey`, `appId` | [VERIFY] |
| C5 | bronze | `user_gpn`, `GICTrackingID`, `ViewTime` | verified |
| C6 | bronze, `pages`, `tbl_hr_employee`, `pbi_db_employeecontact` | `pageId`/`pageUUID`, `user_gpn`/`WORKER_ID`, contact key | contact key [VERIFY] |
| C7 | bronze | `client_Browser`, `client_OS`, `client_Type` | [VERIFY] |
| C8 | bronze | `user_gpn`, `pageId`, `ViewTime` | verified |
| D1 | `sharepoint_gold.pbi_db_interactions_metrics` | `visitdatekey`, `views`, `visits`, `viewingcontactid` | verified |
| D3, D4 | bronze | `pageId`, `GICTrackingID` | verified |
| D5 | bronze vs gold | `pageId` = `marketingPageId`; `session_Id` vs `visits`; `user_gpn` vs `viewingcontactid` | `session_Id` [VERIFY] |
| D6 | `imep_bronze.tbl_analytics_link`, `tbl_email`, `pages` | `LinkTypeEnum`, `EmailId`/`Id`, `TrackingId`, `UBSGICTrackingID` | enum value [VERIFY] |
| D7 | `sharepoint_bronze.customevents` | `name`, `timestamp` | timestamp column [VERIFY] |
| D8 | semantic model | control measure (DAX) | — |

Not yet in the repo: the catalog name (docs use two-part names) and the
write-side schema for the check results (`dq` in the draft).

### Measured on the workspace, 2026-09-11

Block 0b ran over three fortnights: before the change, just after it, and the
most recent two weeks. Every figure below is from `sharepoint_bronze.pageviews`.

That first run cut its windows on `gmdp_date`, the platform's ingestion date.
The query now cuts them on the event time in `timestamp` instead, because a late
load would otherwise move an event into the wrong fortnight. The figures are
expected to move very little; a re-run confirms them, and any figure that does
move is measuring late arrival, which is worth knowing separately.

| | 2–15 Mar, before | 13–26 Apr, after | last 14 days |
|---|---|---|---|
| Page views scanned | 2,861,485 | 2,384,105 | 2,122,418 |
| Page views per session | **2.231** | 1.062 | 1.157 |
| Browser identities per person | **1.71** | 20.004 | 17.342 |
| Sessions per person | 11.123 | 20.005 | 17.111 |
| Single-view session share | **0.584** | 0.939 | 0.888 |
| Distinct people (GPN) | 115,298 | 112,168 | 107,222 |
| Distinct browser identities | 197,107 | **2,243,859** | 1,859,435 |
| Views carrying a GPN | 1.000 | 1.000 | 1.000 |
| SDK versions present | 3.3.6, 2.8.16, 2.7.4 | **2.8.16, 2.7.4** | 2.8.16, 2.7.4 |

Five conclusions.

1. **People are stable, identities are not.** The number of distinct employees
   moved by less than 7 % across all three windows, while distinct browser
   identities rose elevenfold. That is the incident, measured. It also confirms
   that unique visitors, which rest on the contact id, were never affected.
2. **After the change one identity equals one session.** Browser identities per
   person (20.004) and sessions per person (20.005) are the same number to three
   decimals, and page views per session is 1.06. Effectively a fresh identity per
   view.
3. **The SDK version set changed at the incident, by losing a version.** Version
   `javascript:3.3.6` is present before and absent from the April window onward.
   The check was written expecting a new version to appear; a version vanishing is
   the same signal. This is the most concrete lead for the vendor investigation.
4. **The healthy baselines are not what the documentation assumed.** Page views
   per session was 2.23, not 1.1–1.2, and browser identities per person was 1.71,
   not near 1. The figure of 1.1–1.2 quoted at the outset most likely came from the
   reported views-to-visits ratio in Power BI, which is computed on the gold
   `visits` column with its own de-duplication, not on the raw session id. Both
   numbers can be right; they measure different things.
5. **Partial recovery, still broken.** The most recent fortnight is better than
   April on every ratio and still far from March.

Thresholds in `dq.check_def` now hold these measured values. B1, B4 and B5
therefore fire today by design and will keep firing until the source is fixed.
They must not be retuned to the current state, which would define the incident
away.

**Every row carries a GPN** (share 1.000 in all three windows), so the
person-based route is fully available whenever the visit definition is revisited.

### Integrity per medallion layer

Decision 2026-09-11: the identity family (B1–B7) **stays on bronze**. The raw
cookie signals are the early warning, and they are what broke in April. Silver
and gold get their own families instead, so every layer carries integrity rather
than inheriting it.

| Layer | Owns | Checks | Tie-out to the next layer |
|---|---|---|---|
| Bronze | arrival, schema, formats, raw identity | A1–A7, B1–B8, C1–C8 | A6 bronze = silver = gold row counts |
| Silver | person resolution, key completeness | **S1–S3** | S1 bronze GPN = silver contactId |
| Gold | aggregate correctness, KPI plausibility | **G1–G3**, D1–D8 | G3 silver rows = gold views |

**S1 person resolution.** Distinct `GPN` in bronze against distinct `contactId`
in silver for the same day. This is the check that guards the unique-visitor
number. Far below 1 means people are collapsing into one another, far above
means one person is splitting into several.

**S2 key completeness.** Fill rate of `contactId`, `visitorId`, `sessionId` and
`marketingPageId` on `sharepoint_silver.pageviewed`.

**S3 returning-visitor mix.** Silver computes `visitorReturningStatus` itself, so
the April signature is readable without reconstructing anything. A collapse here
confirms at the silver layer what B3 sees at bronze.

**G1 grain uniqueness.** The documented primary key of
`pbi_db_interactions_metrics` must be unique per day. A duplicated grain
multiplies every KPI silently and is invisible in any corridor.

**G2 row-level consistency.** Visits never exceed views, no negative metrics, and
`durationavg` matches `durationsum ÷ views`.

**G3 aggregation tie-out.** Gold views equal silver rows, gold contacts equal
silver contacts.

### Verified in the workspace, 2026-09-11

- **`sharepoint_silver` holds 7 tables**: `marketingpage`, `marketingsite`,
  `pageviewed`, `pagevisited`, `webpage`, `webpagevisited`, `website`. This
  confirms the April Q17 inventory and refutes the Q26 transcription, which read
  `webpageviewed` and `pageposted` off a photo of the Genie output. `marketingpage`
  and `marketingsite` appear in neither April list — check whether the gold
  facts' `marketingPageId` joins to `marketingpage` rather than to
  `sharepoint_bronze.pages`, which would shorten every attribution path.
- **`information_schema` is not available.** It is a Unity Catalog feature and the
  workspace still resolves two-part names through the Hive Metastore. Every probe
  must use `DESCRIBE` / `SHOW`, or read the Spark schema. The SQL draft was
  rewritten accordingly (BLOCK 0 and C1).
- **17 schemas carry the `_silver` suffix**, matching Q26.
- **Bronze is already flat.** There is no `customDimensions` column; the former
  CustomProps are first-class columns (`GPN`, `Email`, `PageURL`, `PageName`,
  `PublishingDate`, `SiteId`, `SiteName`, `GICTrackingID`). The table card in
  `docs/tables/sharepoint/customevents.md` still claims the opposite.
- **The person column is `GPN`, not `user_gpn`**, and the event-time column is
  `timestamp`, not `ViewTime`. `timestamp` is typed STRING and must be parsed.
  `gmdp_timestamp` is the ingestion time of the source platform and must never
  be used for the daily grain.
- **`sdkVersion`, `itemCount`, `iKey` and `appId` are all present on bronze**, so
  checks A4, C3 and C4 run there. The KQL fallback is not needed. Bronze also
  carries `ingestiontime`, which lets A2 measure true load latency.
- **`pageId` is an INT on bronze** while `pages.pageUUID` is a GUID string, so the
  documented page join cannot be a key join. C6 joins on the URL instead, and the
  real relationship still needs confirming.
- **`pbi_db_employeecontact` is not the person bridge.** Its 17 columns carry
  `contactId` and no GPN, e-mail or T-number. The resolution happens inside the
  bronze-to-silver transformation, which surfaces `contactId` on
  `sharepoint_silver.pageviewed`. Check S1 measures it from the outside.
- **Silver carries identity columns we do not have to rebuild**: `contactId`,
  `visitorId`, `sessionId`, `visitorReturningStatus`, `visitorAnonymousStatus`.
- The gold comment column is `comments`, not `commentss` as the table card says.
