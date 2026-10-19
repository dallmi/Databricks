# App Insights Sessions and Identity: `session_Id`, `user_Id` and `person_id`

> Source: export analysis on real intranet exports with the SiteOwnerDashboard diagnosis scripts (2026-07-13 to 2026-07-15) and the incident review for the integrity-check proposal (2026-09-09). Status: confirmed (the cause of the April 2026 change is still open).

## Context

On real exports, the SiteOwnerDashboard collapsed to **Page Views = Page Visits = Unique Visitors**, with an average session of 0 s and an engagement ratio of exactly one. This page records why that happened, what still identifies people and visits, and which column each metric now uses.

## Findings

### 1. `session_Id` and `user_Id` are (near-)unique per page view

On the intranet `pageViews` stream, almost every navigation brings a fresh `session_Id` and `user_Id`. In `diagnose_exports.py`, the ratio of distinct to non-null values is close to 100 % for both columns. `COUNT(DISTINCT session_Id)` and `COUNT(DISTINCT user_Id)` therefore equal the page-view count. A plausible mechanism is that the browser no longer keeps the persistent App Insights cookie. The cause is not confirmed.

The real person is `CustomProps.GPN` (the `gpn` column). It repeats as expected, with several views per person, and nearly every row on real data carries one.

### 2. The change dates to about 8 April 2026

Since about **8 April 2026**, every page view arrives with a fresh `session_Id` and `user_Id`, so one view equals one visit. Views per visit fell from slightly above one to exactly one. Page-view totals stayed correct, so the dashboards still looked plausible. The issue was escalated internally and is being investigated with Microsoft support. As of 2026-09-09 the cause was not confirmed.

Every figure measured in July 2026 describes the state after the change. That includes "almost all official sessions are single-view" and "`user_Id` is near-unique per view". Calibrate healthy baselines on data from **before 8 April 2026**.

### 3. Unique visitors in the gold layer were not affected

The production pipeline does not count visitors on `user_Id`. It takes the GPN and/or e-mail from `CustomProps`, matches them against HR data, and turns them into a contact ID in a dedicated table. That table is most likely `sharepoint_gold.pbi_db_employeecontact`, which feeds `viewingcontactid` on the gold facts. Only the metrics built on session grouping broke: visits (counted on `session_Id`) and the engagement metrics derived from them. An early draft claimed that visitors were inflated, and that claim was corrected.

**Lineage caveat:** the SharePoint gold facts come from the same App Insights Bronze (`sharepoint_bronze.pageviews` → `sharepoint_silver.*` → `sharepoint_gold.pbi_db_*`). Comparing Bronze with gold only ties out the transformation. It is not a check by an independent collector.

### 4. `session_Id` has a real 30-minute timeout but over-fragments

`detect_session_timeout.py` takes consecutive views of the same person and scans gap thresholds from 15 to 120 minutes:

- Up to 30 minutes, the share of consecutive views that stay in the same `session_Id` is roughly flat. Just after 30 minutes it drops sharply, and beyond that it approaches zero. So the source has a **30-minute inactivity timeout**, which confirms the pipeline constant `SESSION_GAP_MIN = 30`.
- Below 30 minutes, however, the source over-fragments: **roughly half of the follow-up views within 30 minutes start a new `session_Id`**, even when they are less than a minute apart. The session is also renewed within one sitting, for example on a reload or a new tab. The official `session_Id` therefore gives clearly more visits than a person-based 30-minute reconstruction.

### 5. Why "Avg time 0s" appears on high-traffic pages

`time_on_page_sec` is the gap to the next view **in the same official `session_Id`**. With single-view sessions, it is null on nearly every row. The few non-null values come from rare same-session pairs, and those are same-instant double fires less than one second apart, for example a client-side language redirect. A page's average is then an average of sub-second artefacts and renders as **"0s"**. Pages without any such pair render "–", because the dashboard formats null as "–" and values below 0.5 s as "0s". So "0s" means there is no time signal at all. It is not a formatting bug.

### 6. Derived person and visit columns

`derive_person_visit()` in `scripts/flatten_appinsights.py` is the canonical implementation, and the SiteOwnerDashboard wrapper imports it. It keeps the source `user_id` and `session_id` unchanged and **adds** these columns:

| Column | Definition |
|---|---|
| `person_id` | GPN. Without a GPN, `anon:<user_id>`, so anonymous devices stay distinct and are never collapsed into one person |
| `visit_id` | A new visit starts when `person_id` changes or after an inactivity gap above `SESSION_GAP_MIN` (30 min) |
| `time_on_page_sec`, `is_last_in_session` | Gap to the next view within the official `session_id`, capped at 30 minutes. Kept for reconciliation and QA |
| `time_on_page_visit_sec` | Gap to the next view within `visit_id`. Needs no cap, because gaps inside a visit are at most 30 minutes by construction |

The derivation runs over the **whole store** on every run, so it is deterministic and idempotent. In the star schema, `fact_page_view` is re-derived on every run. `agg_visit` is the visit-grain twin of `agg_session`, and `agg_session` stays for official counts and QA. When more than 30 % of views lack a GPN, the flattener logs a warning, because the anonymous tail then inflates Unique Visitors.

### 7. Metric definitions (as of 2026-07-15)

| Metric | Column | Reason |
|---|---|---|
| Unique Visitors | `COUNT(DISTINCT person_id)` | A session is not a person |
| Visits | `COUNT(DISTINCT session_id)`, the **official** App Insights session | Company standard since 2026-07-13, so that the numbers reconcile with other company reports |
| Avg. time on page, Avg. session duration | `time_on_page_visit_sec` | Decided on 2026-07-15 after a real-data run of `reconcile_visit_session.py`. Almost all official sessions are single-view, so time grouped by session has no signal |
| Bounce, pages per visit, duration, entry and exit pages (star schema, Power BI) | `agg_visit` | Same reason |
| Pages per session, bounce rate (SiteOwnerDashboard) | still `session_id` | Knowingly left as is, not changed yet |

### 8. Diagnosis scripts

All of these are read-only and live in `SiteOwnerDashboard/scripts/`:

| Script | Purpose |
|---|---|
| `diagnose_exports.py` | Checks exports, store and freshness. Reports the distinct-to-non-null ratio per id column and GPN coverage. Tells apart a source property (ids unique per row) and an export defect (a column shift from an unquoted comma in the timestamp, recognised by `itemType`/`customDimensions` fingerprints) |
| `reconcile_visit_session.py` | Compares the `visit_id` and `session_id` partitions. Writes a business-case workbook with recomputed time on page for both groupings and a data-driven verdict (`output/session_vs_visit_analysis.xlsx`, sheets Verdict, Time on Page, Dwell Distribution, Group Size, Pages, Method) |
| `detect_session_timeout.py` | Scans gap thresholds to find the source's timeout cliff |

Unit test: `SiteOwnerDashboard/scripts/test_derive_person_visit.py`.

## Consequences

- Never count people on `user_Id`. Never count visits or time on raw cookie identifiers unless you accept that, since April 2026, they mean "page views".
- Count people with `person_id` (pipeline) or the contact ID (gold). Visits follow the official `session_id` standard. Time and depth metrics use the cookie-independent `visit_id`.
- **Open decision:** the reporting definition of visits, either the official `session_Id` (standard since 2026-07-13) or person-based (`person_id` + 30-minute rule). See [data-integrity-checks.md §8](../data-integrity-checks.md#8-open-decisions).
- **Monitoring:** page views per visit would have flagged the April change on the first day. That ratio is in the watchlist of [data-integrity-checks.md](../data-integrity-checks.md).
- After changing inputs, run `python scripts/process_site_pageviews.py --rebuild` and then `build_standalone_dashboard.py`. The standalone HTML embeds the parquet when it is built.

## Related

- [appinsights-source.md](appinsights-source.md): where `session_Id`, `user_Id` and `CustomProps.GPN` sit
- [hr-employee-identifiers.md](hr-employee-identifiers.md): GPN, T-number and the HR bridge
- [siteowner-dashboard-pipeline.md](siteowner-dashboard-pipeline.md): the dashboard pipeline that consumes these columns
- [incremental-input-pipeline.md](incremental-input-pipeline.md): the `event_key` built on the source `user_id`/`session_id`
- [data-integrity-checks.md](../data-integrity-checks.md): check catalogue that grew out of the April incident
- [powerbi-visualization.md](../powerbi-visualization.md): Power BI measures that follow these definitions
