# Open Questions

Hypotheses and gaps from the deep-dive sessions that still need validation. Treat
each item as unconfirmed until it is settled; then move the answer into the
relevant finding and delete it here. Collected 2026-10-19.

## Identities and HR

- **T-number in only two tables?** Q27 Rule 2 says the T-number exists only in
  `tbl_email_receiver_status` and `tbl_analytics_link`, which would make person
  analytics email-only. Re-search `INFORMATION_SCHEMA.COLUMNS` across all schemas
  for `%tnumber%` / `T_NUMBER` and synonyms (`RecipientID` in `final`,
  `EmployeeId`, `WorkerId`, `UserId`, SharePoint `employeecontact`). Until then
  cite it as "per Q27, pending re-validation".
- **Person bridge for `viewingcontactid`.** Is `sharepoint_gold.pbi_db_employeecontact`
  (or `employeecontact`) the contact-id → GPN/e-mail table? Its grain is unclear: it
  is far larger than the employee population and close in size to
  `sharepoint_bronze.master_employee_cdm_data`.
- **iMEP person → division/region table.** Does iMEP have its own mapping table,
  like the contact table on the SharePoint side? It could explain or close the
  NULL-region gap (Q16).
- **Root cause of NULL region (Q16).** Org-unit codes from `tbl_hr_employee` that
  do not match `tbl_hr_costcenter`, or missing cost-centre history?
- **`tbl_hr_employees` vs. `tbl_hr_employee`.** Q3 used the plural form only. Same table?
- **Case in the HR-user join.** The transcribed join compares lower-case
  `T_NUMBER` with upper-case `__ORG__Id` without normalising case. Check whether
  Town is NULL throughout Gold.
- **`tbl_pbi_kpi.MailingCreatedBy`** holds clear-text person names. Is there a
  T-number alternative?
- **PII cleanup needs a new plan.** GPN and e-mail were kept in `fact_page_view`
  and `agg_session` as debugging aids, to be dropped once the pipeline is stable.
  Unique visitors now rest on `person_id` (the GPN), so the cleanup has to
  pseudonymise the GPN rather than drop it. Needs a decision.
- **BRD OP-07b and OP-07c** stay open: uniqueness and history of the
  organisational-unit join, and creator reporting.

## App Insights and the SiteOwnerDashboard

- **Cause of the April 2026 identity change.** Cookie ids stopped persisting
  around 8 April 2026; Microsoft support was investigating, cause unconfirmed as
  of 2026-09-09.
- **Reporting definition of visits:** official `session_Id` (company standard
  since 2026-07-13) or person-based (`person_id` + 30-minute rule). Also listed in
  [../data-integrity-checks.md](../data-integrity-checks.md) §8.
- **Dashboard still on `session_id`.** Pages/Session and Bounce Rate in the
  SiteOwnerDashboard use `session_id`, while the star schema and Power BI use
  `agg_visit`. Decide whether to align.
- **Day-first CSV fix not in all code paths.** `_convert_timestamp_utc()` in
  `scripts/flatten_appinsights.py` calls `pd.to_datetime` without `format` or
  `dayfirst`; `SiteOwnerDashboard/scripts/rename_exports_by_timerange.py` uses
  `format="mixed"` without `dayfirst` and likely has the same risk (not tested).

## Cross-channel coverage

- **Real funnel coverage.** How many SharePoint packs (segments 1–2) intersect
  with the email `EMI` packs, per year 2024–2026? Never measured.
  [../joins/cross_channel_via_tracking_id.md](../joins/cross_channel_via_tracking_id.md)
  states the overlap equals the `EMI` pack count; that is unproven.
- **Placeholder tracking ids.** Several Gold and CPLAN samples start with the
  placeholder cluster and pack `CCCCC-0000000`. If that is a default for
  unassigned packs, matching on segments 1–2 merges unrelated activities. Measure
  how common it is.
- **Time floor conflict.** Q25 sets the dashboard floor to 2024-09-01; the join
  recipes and `pages.md` still default to 2025-01-01. Reconcile.
- **Site filter.** The site id of "News and events" is not known yet; the site key
  was transcribed as `SPT_SiteId` in Q25 but `SiteId` in `pages.md` — check with
  `DESCRIBE`.
- **Q15b.** Does `pageviews.GICTrackingID` agree with `pages.__ORG__GICTrackingID`,
  and what is the traffic-weighted coverage?
- **`EMI` suffix on SharePoint pages.** A handful of pages carry it: email landing
  pages or noise?

## Schemas and inventory

- **Gold table labels in Q28.** Confirm the real names behind
  `tbl_pbi_mailings_region`, `tbl_pbi_mailings_division` and `tbl_pbi_analytics`;
  by size they look shifted by one row. Several docs use these labels.
- **Diverging sizes.** Q29's sizes for the per-mailing summary tables differ
  markedly from Q16/Q21. Which are current?
- **`engagement` mixes ids.** Does the excess of distinct ids consist only of
  event ids (Q29)?
- **`imep_gold.final` columns.** Exact list, including the hour bucket and the
  event-type columns (Q26/Q29).
- **`tbl_pbi_kpi.measure`.** A count (samples suggest so) or a rate? Full list of
  `MailingReceiverStatus` values (BRD OP-02). `er_imep_gold.md` draws `measure` as
  a name column, which contradicts Q21.
- **`tbl_pbi_mailingid` vs. `tbl_pbi_platform_mailings`.** Identical or 1:1?
- **`tbl_analytics_link` columns.** Genie listed more names than its reported
  column count; settle with `DESCRIBE`.
- **`sharepoint_silver` table names** differ between Q17 (`webpagevisited`,
  `pagevisited`) and Q26 (`webpageviewed`, `pageposted`); check with `SHOW TABLES`
  and fetch their schemas.
- **`page_metadata_bronze`** (likes, comments, moderation): relevant for engagement
  enrichment? No card yet.
- **Power BI inventory tables** `pbi_db_website_page_inventory`,
  `pbi_db_website_webpage_inventory`, `pbi_db_website_page_view`: derived from
  `pages`/`pageviews`?
- **`flag` and `marketingPageIdGuid`** in `pbi_db_interactions_metrics`: meaning?
- **`sharepoint_cplan`** directly usable for packs and clusters (Q17e)? Is
  `externalcommunicationactivities_bronze` populated?
- **`pbi_gold`** (60 tables) needs an access request (Q17f).
- **Multi-device subquery.** As transcribed it matches on `RecipientID` only, not
  on (EmailId, RecipientID). Does Gold (`final`, `tbl_pbi_deviceTypeall`) inherit
  the over-labelling? Is Pattern 2 the build logic of `final`?

## Pipeline operations

- **Writes outside the observed window.** The append volume per observed
  `pageviews` run is far too small to explain the table's growth (Q28).
- **Jobs outside our visibility.** Frequencies are observations from Delta
  history, not a schedule; order and length of the Gold rebuild window are unknown.
- **Dates in `sources.md`.** Q17 and Q22 are dated 2026-04-18 and 2026-04-19
  there, but 2026-04-17 and 2026-04-20 in the notes and transcripts.
