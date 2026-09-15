-- ============================================================================
-- DATA INTEGRITY CHECKS — PROD READ-ONLY EDITION
--
-- Companion to dq_checks_draft.sql, which is the persistent version intended for
-- Dev and pre-prod. This edition exists so the same checks can be run against
-- PROD today without leaving anything behind.
--
-- WHAT THIS FILE DOES NOT DO
--   · no CREATE SCHEMA, no CREATE TABLE, no INSERT, no MERGE, no DELETE
--   · nothing is written to storage, and nothing appears in the metastore
--   · no source table is modified, and no permission beyond SELECT is needed
--
-- WHAT IT DOES
--   · CREATE OR REPLACE TEMPORARY VIEW only. A temporary view is a query
--     definition held in YOUR session. It is invisible to everyone else and it
--     disappears when the notebook detaches. It is not a table.
--   · CACHE TABLE on the 70-day slice, so twenty checks do not each re-scan
--     173M rows, and on the small results derived from it (cell 9e). Caching materialises into cluster memory and spilled local
--     disk, never into the lakehouse. The last cell releases it.
--
-- HOW TO RUN
--   Cell 0 first: it checks every column against the live schema and costs
--   nothing. Then 1 to 9e in order. 9e materialises the verdicts, so 10 and 10b
--   are both instant afterwards and either can be skipped.
--     cell 10   the detail grid, every check including the ones that passed.
--               For whoever maintains the checks.
--     cell 10b  the health board, grouped by cause and written in plain words.
--               For everyone else. This is the one to read first.
--     cell 10c  the trends: one chart per daily metric against the value
--               expected for that weekday, to see the shape of a problem and
--               which figures broke on the same day. Run after 10b for titles.
--     cell 10d  four read-only queries, each its own cell (a to d): verify the
--               layer-flow checks, calibrate their limits, list the days they
--               fired, and show which layer moved on each of those days.
--   Cell 11 cleans up and is safe to run at any point, including after a failure.
--   To run the lot from a single cell instead, see the note at the foot.
--
-- NAMING
--   Temporary views cannot be schema-qualified, so they are prefixed `dq_`
--   rather than living in a `dq` schema. If a name collides with something in
--   your session, a temporary view shadows it only for you and only until
--   detach; rename the prefix if that is still a concern.
--
-- Conventions are identical to the persistent edition: CAST rather than
-- to_timestamp, string ranges that stay file-skippable, event time never
-- ingestion time, identity ratios daily rather than pooled. See
-- docs/dq_blocks_engineering_notes.md §0.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- CELL 0 — PRE-FLIGHT. Run this first. It reads nothing but schemas.
--
-- Every column this notebook touches, checked in one go. Two runs were lost to
-- single wrong column names taken from documentation rather than from the
-- workspace: `user_gpn` which is really `GPN`, and `referenceapplicationid`
-- which is really `referrerapplicationid`. This cell surfaces all of them at
-- once instead of one failure per attempt.
--
-- If anything prints as MISSING, fix the name in the cells below before running
-- them, and correct docs/dq_blocks_engineering_notes.md too — the table cards
-- were transcribed from photographs and are still wrong in the same places.
-- ----------------------------------------------------------------------------
%python
REQUIRED = {
  "sharepoint_bronze.pageviews": [
      "id","timestamp","gmdp_timestamp","ingestiontime","session_Id","user_Id",
      "user_AuthenticatedId","GPN","Email","pageId","PageURL","GICTrackingID",
      "sdkVersion","itemCount","iKey","appId","client_Browser","client_OS","client_Type"],
  "sharepoint_bronze.customevents": ["timestamp","name","user_Id","session_Id","GPN"],
  "sharepoint_bronze.pages":        ["PageURL"],
  "sharepoint_silver.pageviewed":   ["timestamp","contactId","visitorId","sessionId",
                                     "marketingPageId","visitorReturningStatus"],
  "sharepoint_gold.pbi_db_interactions_metrics": [
      "visitdatekey","marketingpageid","viewingcontactid","referrerapplicationid",
      "views","visits","durationsum","durationavg"],
  "imep_bronze.tbl_hr_employee":    ["WORKER_ID"],
}

problems = 0
for table, cols in REQUIRED.items():
    try:
        have = {f.name.lower() for f in spark.table(table).schema.fields}
    except Exception as e:
        problems += 1
        print(f"UNREADABLE  {table}  ->  {type(e).__name__}: {str(e)[:110]}")
        continue
    missing = [c for c in cols if c.lower() not in have]
    if missing:
        problems += len(missing)
        print(f"MISSING     {table}")
        for m in missing:
            near = sorted(h for h in have if m.lower()[:5] in h or h[:5] in m.lower())[:4]
            print(f"              {m:<24} closest: {near if near else 'nothing similar'}")
    else:
        print(f"ok          {table}  ({len(cols)} columns)")

print()
print("PRE-FLIGHT CLEAR — run cells 1 to 11" if problems == 0
      else f"{problems} problem(s). Fix the names above before running cell 1.")


-- ----------------------------------------------------------------------------
-- CELL 1 — the working slice (70 days), then cache it
-- ----------------------------------------------------------------------------
%sql
CREATE OR REPLACE TEMPORARY VIEW dq_pv_window AS
SELECT
  id                                            AS view_id,
  `timestamp`                                   AS view_ts_raw,
  CAST(`timestamp` AS TIMESTAMP)                AS view_ts,
  CAST(CAST(`timestamp` AS TIMESTAMP) AS DATE)  AS view_date,
  gmdp_timestamp                                AS gmdp_ingested_ts,
  session_Id                                    AS session_id,
  user_Id                                       AS browser_id,
  user_AuthenticatedId                          AS auth_id,
  CASE WHEN GPN RLIKE '^[0-9]{8}$' THEN GPN END AS gpn,
  GPN                                           AS gpn_raw,
  Email                                         AS email,
  pageId                                        AS page_id,
  PageURL                                       AS page_url,
  GICTrackingID                                 AS tracking_id,
  sdkVersion                                    AS sdk_version,
  COALESCE(itemCount, 1)                        AS item_count,
  iKey                                          AS ikey,
  appId                                         AS app_id,
  client_Browser                                AS client_browser,
  client_OS                                     AS client_os,
  client_Type                                   AS client_type
FROM sharepoint_bronze.pageviews
WHERE `timestamp` >= date_format(date_sub(current_date(), 70), 'yyyy-MM-dd');

%sql
CACHE TABLE dq_pv_window;


-- ----------------------------------------------------------------------------
-- CELL 2 — daily metrics from the slice
-- ----------------------------------------------------------------------------
%sql
CREATE OR REPLACE TEMPORARY VIEW dq_pv_daily AS
WITH sess AS (
  SELECT view_date, session_id, COUNT(*) AS n_views
  FROM   dq_pv_window GROUP BY view_date, session_id
),
pairs AS (
  SELECT view_date, page_id, view_ts,
         LAG(view_ts)  OVER (PARTITION BY gpn ORDER BY view_ts) AS prev_ts,
         LAG(page_id)  OVER (PARTITION BY gpn ORDER BY view_ts) AS prev_page
  FROM   dq_pv_window WHERE gpn IS NOT NULL
),
b7d AS (
  SELECT d.view_date,
         COUNT(DISTINCT w.session_id) / NULLIF(COUNT(DISTINCT w.browser_id), 0) AS sessions_per_browser_7d
  FROM  (SELECT DISTINCT view_date FROM dq_pv_window) d
  JOIN   dq_pv_window w ON w.view_date BETWEEN date_sub(d.view_date, 6) AND d.view_date
  GROUP  BY d.view_date
),
clicks AS (
  SELECT CAST(CAST(`timestamp` AS TIMESTAMP) AS DATE) AS view_date, COUNT(*) AS clicks
  FROM   sharepoint_bronze.customevents
  WHERE  name = 'click_event'
    AND  `timestamp` >= date_format(date_sub(current_date(), 70), 'yyyy-MM-dd')
  GROUP  BY 1
)
SELECT
  w.view_date,
  COUNT(*)                                                              AS views,
  SUM(w.item_count) / COUNT(*)                                          AS sampling_factor,
  COUNT(DISTINCT w.session_id)                                          AS sessions,
  COUNT(*) / NULLIF(COUNT(DISTINCT w.session_id), 0)                    AS views_per_session,
  COUNT(DISTINCT w.browser_id)                                          AS browser_ids,
  COUNT(DISTINCT w.gpn)                                                 AS persons,
  COUNT(DISTINCT CASE WHEN w.gpn IS NOT NULL THEN w.browser_id END)
    / NULLIF(COUNT(DISTINCT w.gpn), 0)                                  AS browser_ids_per_person,
  COUNT(w.gpn) / COUNT(*)                                               AS identified_share,
  COUNT(w.tracking_id) / COUNT(*)                                       AS tracked_share,
  MAX(s.single_share)                                                   AS single_view_session_share,
  MAX(p.double_share)                                                   AS double_fire_share,
  MAX(b.sessions_per_browser_7d)                                        AS sessions_per_browser_7d,
  MAX(c.clicks) / COUNT(*)                                              AS clicks_per_view
FROM dq_pv_window w
LEFT JOIN (SELECT view_date, AVG(CASE WHEN n_views = 1 THEN 1.0 ELSE 0.0 END) AS single_share
           FROM sess GROUP BY view_date) s ON s.view_date = w.view_date
LEFT JOIN (SELECT view_date,
                  SUM(CASE WHEN page_id = prev_page AND view_ts < prev_ts + INTERVAL 1 SECOND
                           THEN 1 ELSE 0 END) / COUNT(*) AS double_share
           FROM pairs GROUP BY view_date) p ON p.view_date = w.view_date
LEFT JOIN b7d    b ON b.view_date = w.view_date
LEFT JOIN clicks c ON c.view_date = w.view_date
GROUP BY w.view_date;


-- ----------------------------------------------------------------------------
-- CELL 3 — gold daily, and silver daily
-- ----------------------------------------------------------------------------
%sql
CREATE OR REPLACE TEMPORARY VIEW dq_gold_daily AS
SELECT to_date(visitdatekey, 'yyyyMMdd')      AS view_date,
       SUM(views)                             AS gold_views,
       SUM(visits)                            AS gold_visits,
       COUNT(DISTINCT viewingcontactid)       AS gold_unique_visitors,
       SUM(views) / NULLIF(SUM(visits), 0)    AS gold_views_per_visit
FROM   sharepoint_gold.pbi_db_interactions_metrics
WHERE  visitdatekey >= date_format(date_sub(current_date(), 70), 'yyyyMMdd')
GROUP  BY 1;

%sql
CREATE OR REPLACE TEMPORARY VIEW dq_sv_daily AS
SELECT CAST(`timestamp` AS DATE)                                   AS view_date,
       COUNT(*)                                                    AS sv_rows,
       COUNT(contactId)       / COUNT(*)                           AS contact_resolution_rate,
       COUNT(visitorId)       / COUNT(*)                           AS visitor_id_rate,
       COUNT(sessionId)       / COUNT(*)                           AS session_id_rate,
       COUNT(marketingPageId) / COUNT(*)                           AS page_guid_rate,
       COUNT(DISTINCT contactId)                                   AS sv_contacts,
       AVG(CASE WHEN LOWER(visitorReturningStatus) LIKE '%return%' THEN 1.0 ELSE 0.0 END) AS returning_share
FROM   sharepoint_silver.pageviewed
WHERE  `timestamp` >= date_sub(current_date(), 70)
GROUP  BY 1;


-- ----------------------------------------------------------------------------
-- CELL 4 — long format, one row per day and metric
--
-- The last two blocks are the LAYER FLOW: silver's own volumes, and the ratios
-- between layers for page views and for people. These measure what the pipeline
-- itself does, which is what this team owns and can fix. A ratio between two
-- layers is also the cleanest signal in the notebook: traffic moves numerator
-- and denominator together, so weekends, holidays and quiet Mondays cancel out
-- and a healthy ratio is a flat line. A6, G3, S1 and G4 judge them in cell 6;
-- as daily series they get a history, an onset date and a chart, which the
-- single-day versions formerly in cell 9 could not have.
--
-- Visits are deliberately absent. Gold stores visits per page, person and day,
-- so a visit across three pages sits in three rows and SUM(visits) is not
-- additive across pages. A daily total against a daily total would measure the
-- aggregation rule, not data loss. It needs a like-for-like grain first.
-- ----------------------------------------------------------------------------
%sql
CREATE OR REPLACE TEMPORARY VIEW dq_metric_daily AS
SELECT view_date, metric, CAST(value AS DOUBLE) AS value FROM (
  SELECT view_date, stack(13,
    'views', CAST(views AS DOUBLE), 'sampling_factor', CAST(sampling_factor AS DOUBLE),
    'views_per_session', CAST(views_per_session AS DOUBLE),
    'sessions_per_browser_7d', CAST(sessions_per_browser_7d AS DOUBLE),
    'browser_ids_per_person', CAST(browser_ids_per_person AS DOUBLE),
    'single_view_session_share', CAST(single_view_session_share AS DOUBLE),
    'identified_share', CAST(identified_share AS DOUBLE),
    'tracked_share', CAST(tracked_share AS DOUBLE),
    'double_fire_share', CAST(double_fire_share AS DOUBLE),
    'clicks_per_view', CAST(clicks_per_view AS DOUBLE),
    'sessions', CAST(sessions AS DOUBLE), 'browser_ids', CAST(browser_ids AS DOUBLE),
    'persons', CAST(persons AS DOUBLE)) AS (metric, value)
  FROM dq_pv_daily
  UNION ALL
  SELECT view_date, stack(4,
    'gold_views', CAST(gold_views AS DOUBLE), 'gold_visits', CAST(gold_visits AS DOUBLE),
    'gold_unique_visitors', CAST(gold_unique_visitors AS DOUBLE),
    'gold_views_per_visit', CAST(gold_views_per_visit AS DOUBLE)) AS (metric, value)
  FROM dq_gold_daily
  UNION ALL
  SELECT view_date, stack(2,
    'silver_rows', CAST(sv_rows AS DOUBLE), 'silver_contacts', CAST(sv_contacts AS DOUBLE)) AS (metric, value)
  FROM dq_sv_daily
  UNION ALL
  -- A layer that did not load at all has no row, so its numerator is defaulted
  -- to 0: a missing gold day must read as a ratio of 0 (critical), never as a
  -- NULL that the engine reports as "cannot be judged".
  SELECT pv.view_date, stack(4,
    'keep_share_silver',        CAST(COALESCE(sv.sv_rows, 0)             / NULLIF(pv.views, 0)       AS DOUBLE),
    'gold_to_silver_views',     CAST(COALESCE(g.gold_views, 0)           / NULLIF(sv.sv_rows, 0)     AS DOUBLE),
    'silver_to_bronze_persons', CAST(COALESCE(sv.sv_contacts, 0)         / NULLIF(pv.persons, 0)     AS DOUBLE),
    'gold_to_silver_persons',   CAST(COALESCE(g.gold_unique_visitors, 0) / NULLIF(sv.sv_contacts, 0) AS DOUBLE)) AS (metric, value)
  FROM dq_pv_daily pv
  LEFT JOIN dq_sv_daily   sv ON sv.view_date = pv.view_date
  LEFT JOIN dq_gold_daily g  ON g.view_date  = pv.view_date
);


-- ----------------------------------------------------------------------------
-- CELL 5 — same-weekday baseline, median and MAD, plus the step comparison
-- ----------------------------------------------------------------------------
%sql
CREATE OR REPLACE TEMPORARY VIEW dq_metric_baseline AS
WITH hist AS (
  SELECT cur.view_date, cur.metric, cur.value, h.value AS hist_value
  FROM   dq_metric_daily cur
  JOIN   dq_metric_daily h
         ON  h.metric = cur.metric
         AND dayofweek(h.view_date) = dayofweek(cur.view_date)
         AND h.view_date BETWEEN date_sub(cur.view_date, 56) AND date_sub(cur.view_date, 7)
),
med AS (
  SELECT view_date, metric, value, COUNT(*) AS n_hist,
         percentile_approx(hist_value, 0.5) AS baseline
  FROM   hist GROUP BY view_date, metric, value
),
mad AS (
  SELECT m.view_date, m.metric,
         percentile_approx(ABS(h.hist_value - m.baseline), 0.5) AS mad
  FROM   med m JOIN hist h ON h.view_date = m.view_date AND h.metric = m.metric
  GROUP  BY m.view_date, m.metric
),
steps AS (
  SELECT cur.view_date, cur.metric,
         AVG(CASE WHEN h.view_date BETWEEN date_sub(cur.view_date, 6)  AND cur.view_date              THEN h.value END) AS mean_7d,
         AVG(CASE WHEN h.view_date BETWEEN date_sub(cur.view_date, 34) AND date_sub(cur.view_date, 7) THEN h.value END) AS mean_prior_28d
  FROM   dq_metric_daily cur
  JOIN   dq_metric_daily h ON h.metric = cur.metric
         AND h.view_date BETWEEN date_sub(cur.view_date, 34) AND cur.view_date
  GROUP  BY cur.view_date, cur.metric
)
SELECT med.view_date, med.metric, med.value, med.n_hist, med.baseline, mad.mad,
       med.baseline - 3 * mad.mad AS corr_low,
       med.baseline + 3 * mad.mad AS corr_high,
       s.mean_7d, s.mean_prior_28d,
       (s.mean_7d - s.mean_prior_28d) / NULLIF(s.mean_prior_28d, 0) AS step_pct
FROM med
LEFT JOIN mad   ON mad.view_date = med.view_date AND mad.metric = med.metric
LEFT JOIN steps s ON s.view_date = med.view_date AND s.metric = med.metric;


-- ----------------------------------------------------------------------------
-- CELL 6 — the check catalogue, inline instead of a stored table
-- Thresholds are the measured ones. See BRD §10.1.
--
-- band_warn: may the statistical band (+/- 3 MAD) raise a warning on its own?
-- True for behavioural metrics, where the band is the only judgement. False for
-- the layer-flow checks at the bottom: a ratio between layers is nearly constant,
-- so its MAD is tiny and the band would flag harmless wobble in the third decimal.
-- Their stated limits decide, exactly as they did as explicit checks in cell 9.
-- A6 is now relative to the same-weekday baseline (5 % / 10 % of it) rather than
-- 5 / 10 points against a pooled 28-day median; at a share well below 1 that is
-- slightly stricter. G4 is new and not yet measured, hence relative limits.
-- ----------------------------------------------------------------------------
%sql
CREATE OR REPLACE TEMPORARY VIEW dq_check_def AS
SELECT * FROM VALUES
  ('A1','completeness','bronze','views',                 CAST(NULL AS DOUBLE),CAST(NULL AS DOUBLE),CAST(NULL AS DOUBLE),CAST(NULL AS DOUBLE),0.25,0.50,CAST(NULL AS DOUBLE),CAST(NULL AS DOUBLE),true,'daily page views vs same-weekday median'),
  ('A4','completeness','staging','sampling_factor',      NULL,NULL,1.001,1.001,NULL,NULL,NULL,NULL,true,'sum(itemCount)/count(*) must be 1.00'),
  ('B1','identity','bronze','views_per_session',         1.30,1.50,NULL,NULL,NULL,NULL,0.10,0.25,true,'healthy 1.94-2.35 weekday, broken 1.06'),
  ('B2','identity','bronze','sessions_per_browser_7d',   1.02,1.10,NULL,NULL,NULL,NULL,0.15,0.30,true,'visits per browser identity, rolling week'),
  ('B4','identity','bronze','browser_ids_per_person',    NULL,NULL,1.5,2.5,NULL,NULL,0.15,0.30,true,'per DAY; healthy 1.06-1.15, broken 3.7-4.15'),
  ('B5','identity','bronze','single_view_session_share', NULL,NULL,0.80,0.90,NULL,NULL,0.10,0.20,true,'healthy 0.54-0.76, broken 0.93-0.95'),
  ('B8','identity','bronze','identified_share',          0.90,0.97,NULL,NULL,NULL,NULL,0.05,0.15,true,'measured 1.000, every row carries a GPN'),
  ('C8','schema','bronze','double_fire_share',           NULL,NULL,0.10,0.25,NULL,NULL,0.05,NULL,true,'same person, same page, under 1 s apart'),
  ('D1a','plausibility','gold','gold_views',             NULL,NULL,NULL,NULL,0.25,0.50,NULL,NULL,true,'gold views per day'),
  ('D1b','plausibility','gold','gold_visits',            NULL,NULL,NULL,NULL,0.25,0.50,0.25,0.50,true,'gold visits per day'),
  ('D1c','plausibility','gold','gold_unique_visitors',   NULL,NULL,NULL,NULL,0.25,0.50,0.25,0.50,true,'gold distinct contacts per day'),
  ('D1d','plausibility','gold','gold_views_per_visit',   1.02,1.05,NULL,NULL,NULL,NULL,0.10,0.25,true,'the ratio the business sees'),
  ('D4','plausibility','gold','tracked_share',           NULL,NULL,NULL,NULL,NULL,NULL,0.05,NULL,true,'views with a tracking id'),
  ('D7','plausibility','gold','clicks_per_view',         NULL,NULL,NULL,NULL,NULL,NULL,0.25,NULL,true,'customEvents clicks per page view'),
  -- layer flow: page views, then people
  ('A6','completeness','silver','keep_share_silver',     NULL,NULL,NULL,NULL,0.05,0.10,NULL,NULL,false,'silver rows / bronze views; drafts and unpublished pages are filtered by design'),
  ('G3','completeness','gold','gold_to_silver_views',    0.95,0.99,1.01,1.05,NULL,NULL,NULL,NULL,false,'gold views / silver rows, must be 1.00'),
  ('S1','identity','silver','silver_to_bronze_persons',  0.75,0.90,1.10,1.35,NULL,NULL,NULL,NULL,false,'silver distinct contactId / bronze distinct GPN'),
  ('G4','identity','gold','gold_to_silver_persons',      NULL,NULL,NULL,NULL,0.05,0.10,NULL,NULL,false,'gold distinct contacts / silver distinct contactId')
AS t(check_id, family, layer, metric,
     abs_block_low, abs_warn_low, abs_warn_high, abs_block_high,
     rel_warn_pct, rel_block_pct, step_warn_pct, step_block_pct, band_warn, note);


-- ----------------------------------------------------------------------------
-- CELL 7 — which reported figure does each check cast doubt on?
-- Drives the "affected / unaffected" split. BRD §8.0, FR-RSP-08.
-- ----------------------------------------------------------------------------
%sql
CREATE OR REPLACE TEMPORARY VIEW dq_check_affects AS
SELECT * FROM VALUES
  ('A1','page_views'),('A1','visits'),('A1','unique_visitors'),
  ('A2','page_views'),('A2','visits'),('A2','unique_visitors'),
  ('A4','page_views'),('A4','visits'),('A4','unique_visitors'),
  ('A6','page_views'),('A6','visits'),('A6','unique_visitors'),
  ('G1','page_views'),('G1','visits'),('G1','unique_visitors'),
  ('A5','page_views'),
  ('B1','visits'),('B1','pages_per_visit'),('B1','avg_time_on_page'),('B1','bounce_rate'),
  ('B2','visits'),('B3','visits'),('B4','visits'),
  ('B5','visits'),('B5','bounce_rate'),
  ('B6','avg_time_on_page'),('B6','pages_per_visit'),
  ('B7','visits'),('B7','pages_per_visit'),
  ('C8','page_views'),('C8','avg_time_on_page'),
  ('B8','unique_visitors'),('S1','unique_visitors'),('S2','unique_visitors'),
  ('S3','unique_visitors'),
  ('D1a','page_views'),('D1b','visits'),('D1c','unique_visitors'),('D1d','visits'),
  ('D4','tracking_coverage'),('D7','clicks'),
  ('C6','page_breakdowns'),('D3','page_breakdowns'),
  ('G2','visits'),('G2','avg_time_on_page'),
  ('G3','page_views'),('G3','unique_visitors'),
  ('G4','unique_visitors')
AS t(check_id, figure);


-- ----------------------------------------------------------------------------
-- CELL 8 — the corridor engine
-- ----------------------------------------------------------------------------
%sql
CREATE OR REPLACE TEMPORARY VIEW dq_r_corridor AS
SELECT
  b.view_date AS check_date, d.check_id, d.family, d.layer,
  b.value AS metric_value, b.baseline,
  -- The bounds shown are the ones that decide: fixed limits first; for a check
  -- judged by limits alone, its relative limit around the baseline; the band only
  -- where the band may warn.
  COALESCE(d.abs_warn_low,
           CASE WHEN NOT d.band_warn AND d.rel_warn_pct IS NOT NULL THEN b.baseline - ABS(b.baseline) * d.rel_warn_pct END,
           CASE WHEN d.band_warn THEN b.corr_low END)  AS lower_bound,
  COALESCE(d.abs_warn_high,
           CASE WHEN NOT d.band_warn AND d.rel_warn_pct IS NOT NULL THEN b.baseline + ABS(b.baseline) * d.rel_warn_pct END,
           CASE WHEN d.band_warn THEN b.corr_high END) AS upper_bound,
  CASE
    WHEN b.value IS NULL                                                                THEN 'info'
    WHEN b.n_hist < 4                                                                   THEN 'info'
    -- flat zero with a flat-zero baseline: there is nothing to compare against,
    -- so the honest verdict is "cannot judge", not "warning"
    WHEN b.value = 0 AND COALESCE(b.baseline, 0) = 0 AND COALESCE(b.mad, 0) = 0        THEN 'info'
    WHEN d.abs_block_low  IS NOT NULL AND b.value <= d.abs_block_low                    THEN 'critical'
    WHEN d.abs_block_high IS NOT NULL AND b.value >= d.abs_block_high                   THEN 'critical'
    WHEN d.rel_block_pct  IS NOT NULL AND ABS(b.value - b.baseline) / NULLIF(b.baseline,0) > d.rel_block_pct THEN 'critical'
    WHEN d.step_block_pct IS NOT NULL AND ABS(b.step_pct) > d.step_block_pct            THEN 'critical'
    WHEN d.abs_warn_low   IS NOT NULL AND b.value <  d.abs_warn_low                     THEN 'warning'
    WHEN d.abs_warn_high  IS NOT NULL AND b.value >  d.abs_warn_high                    THEN 'warning'
    WHEN d.rel_warn_pct   IS NOT NULL AND ABS(b.value - b.baseline) / NULLIF(b.baseline,0) > d.rel_warn_pct  THEN 'warning'
    WHEN d.step_warn_pct  IS NOT NULL AND ABS(b.step_pct) > d.step_warn_pct             THEN 'warning'
    WHEN d.band_warn AND (b.value < b.corr_low OR b.value > b.corr_high)                THEN 'warning'
    ELSE 'ok'
  END AS status,
  CONCAT(d.note, ' | 7d ', ROUND(b.mean_7d, 3), ' vs prior 28d ', ROUND(b.mean_prior_28d, 3),
         ' (', ROUND(100 * b.step_pct, 1), ' %)') AS note,
  -- Has this moved lately, or has it been sitting at this value for weeks? A
  -- failing check that has not moved is a known condition, not today's news, and
  -- a first-line responder needs to tell the two apart before escalating.
  -- A step percentage computed from a base of zero is arithmetic noise, not
  -- movement. D4 measured 0.0 against a prior mean of 0.0 and still reported
  -- -17.8 %, which put an artefact at the top of the board as the only thing
  -- that had "changed recently". Anything with no baseline to move away from is
  -- reported as unknown instead.
  CASE WHEN b.step_pct IS NULL OR ABS(COALESCE(b.mean_prior_28d, 0)) < 1e-9 THEN 'unknown'
       WHEN ABS(b.step_pct) > 0.10 THEN 'moving' ELSE 'stable' END AS trend
FROM dq_metric_baseline b
JOIN dq_check_def d ON d.metric = b.metric;


-- ----------------------------------------------------------------------------
-- CELL 9 — the explicit checks, for one day (default: yesterday)
-- Change the date in the first CTE to judge a different day.
-- ----------------------------------------------------------------------------
%sql
CREATE OR REPLACE TEMPORARY VIEW dq_r_explicit AS
WITH p AS (SELECT date_sub(current_date(), 1) AS d),

-- A2 freshness, and true load latency
a2 AS (
  SELECT p.d AS check_date, 'A2' AS check_id, 'completeness' AS family, 'bronze' AS layer,
         CAST(timestampdiff(HOUR, MAX(w.view_ts), current_timestamp()) AS DOUBLE) AS metric_value,
         CAST(NULL AS DOUBLE) AS baseline, CAST(NULL AS DOUBLE) AS lower_bound, 24.0 AS upper_bound,
         CASE WHEN timestampdiff(HOUR, MAX(w.view_ts), current_timestamp()) > 48 THEN 'critical'
              WHEN timestampdiff(HOUR, MAX(w.view_ts), current_timestamp()) > 24 THEN 'warning' ELSE 'ok' END AS status,
         CONCAT('newest event ', CAST(MAX(w.view_ts) AS STRING),
                ' | p95 load lag ',
                ROUND(percentile_approx(timestampdiff(MINUTE, w.view_ts, w.gmdp_ingested_ts), 0.95) / 60.0, 1), ' h') AS note
  FROM dq_pv_window w CROSS JOIN p WHERE w.view_date >= date_sub(p.d, 2) GROUP BY p.d
),
-- A5 duplicate rate on the composite key
a5k AS (
  SELECT date_trunc('SECOND', w.view_ts) AS ts_s, w.browser_id, w.session_id, w.page_id, COUNT(*) AS n
  FROM dq_pv_window w JOIN p ON w.view_date = p.d GROUP BY 1,2,3,4
),
a5 AS (
  SELECT p.d, 'A5', 'completeness', 'bronze',
         SUM(n - 1) / SUM(n), 0.0, CAST(NULL AS DOUBLE), 0.01,
         CASE WHEN SUM(n-1)/SUM(n) > 0.05 THEN 'critical' WHEN SUM(n-1)/SUM(n) > 0.01 THEN 'warning' ELSE 'ok' END,
         CONCAT(SUM(n - 1), ' duplicate rows of ', SUM(n))
  FROM a5k CROSS JOIN p GROUP BY p.d
),
-- A6 (layer retention), S1 (people bronze -> silver) and G3 (gold vs silver)
-- used to be judged here for one day. They are now daily series in cell 4 and
-- corridor checks in cell 6, so they have a history, an onset and a chart.
--
-- A6 background, still true: bronze holds MORE rows than silver by design,
-- because unpublished pages, drafts and similar are filtered out on the way in
-- (confirmed 2026-09-11). The gap is not a defect. A change in the share that
-- survives is.
-- B3 recurring browser identities
b3t AS (SELECT DISTINCT browser_id FROM dq_pv_window w JOIN p ON w.view_date = p.d),
b3p AS (SELECT DISTINCT browser_id FROM dq_pv_window w JOIN p ON w.view_date BETWEEN date_sub(p.d,28) AND date_sub(p.d,1)),
b3 AS (
  SELECT p.d, 'B3', 'identity', 'bronze',
         COUNT(b3p.browser_id) / COUNT(*), CAST(NULL AS DOUBLE), 0.30, CAST(NULL AS DOUBLE),
         CASE WHEN COUNT(b3p.browser_id)/COUNT(*) < 0.10 THEN 'critical'
              WHEN COUNT(b3p.browser_id)/COUNT(*) < 0.30 THEN 'warning' ELSE 'ok' END,
         CONCAT(COUNT(b3p.browser_id), ' of ', COUNT(*), ' browser ids seen in the prior 28 days')
  FROM b3t LEFT JOIN b3p ON b3p.browser_id = b3t.browser_id CROSS JOIN p GROUP BY p.d
),
-- B6 session survival across a short gap
b6p AS (
  SELECT w.view_date, w.session_id,
         LAG(w.session_id) OVER (PARTITION BY w.gpn ORDER BY w.view_ts) AS prev_session,
         timestampdiff(MINUTE, LAG(w.view_ts) OVER (PARTITION BY w.gpn ORDER BY w.view_ts), w.view_ts) AS gap_min
  FROM dq_pv_window w JOIN p ON w.view_date BETWEEN date_sub(p.d,1) AND p.d WHERE w.gpn IS NOT NULL
),
b6 AS (
  SELECT p.d, 'B6', 'identity', 'bronze',
         AVG(CASE WHEN session_id = prev_session THEN 1.0 ELSE 0.0 END), CAST(NULL AS DOUBLE), 0.50, CAST(NULL AS DOUBLE),
         CASE WHEN AVG(CASE WHEN session_id = prev_session THEN 1.0 ELSE 0.0 END) < 0.50 THEN 'critical' ELSE 'ok' END,
         CONCAT(COUNT(*), ' consecutive same-person pairs under 30 min')
  FROM b6p JOIN p ON b6p.view_date = p.d WHERE gap_min IS NOT NULL AND gap_min < 30 GROUP BY p.d
),
-- B7 official sessions vs reconstructed visits
b7o AS (
  SELECT view_date, session_id, COALESCE(gpn, CONCAT('anon:', browser_id)) AS person_id, view_ts,
         LAG(view_ts) OVER (PARTITION BY COALESCE(gpn, CONCAT('anon:', browser_id)) ORDER BY view_ts) AS prev_ts
  FROM dq_pv_window
),
b7f AS (
  SELECT *, CASE WHEN prev_ts IS NULL OR timestampdiff(MINUTE, prev_ts, view_ts) > 30 THEN 1 ELSE 0 END AS new_visit
  FROM b7o
),
b7v AS (
  SELECT view_date, session_id, person_id,
         CONCAT(person_id, '#', SUM(new_visit) OVER (PARTITION BY person_id ORDER BY view_ts)) AS visit_id
  FROM b7f
),
b7 AS (
  SELECT p.d, 'B7', 'identity', 'bronze',
         COUNT(DISTINCT session_id) / NULLIF(COUNT(DISTINCT visit_id), 0), 1.0, CAST(NULL AS DOUBLE), 1.5,
         CASE WHEN COUNT(DISTINCT session_id)/NULLIF(COUNT(DISTINCT visit_id),0) > 2.0 THEN 'critical'
              WHEN COUNT(DISTINCT session_id)/NULLIF(COUNT(DISTINCT visit_id),0) > 1.5 THEN 'warning' ELSE 'ok' END,
         CONCAT(COUNT(DISTINCT session_id), ' sessions vs ', COUNT(DISTINCT visit_id),
                ' reconstructed visits for ', COUNT(DISTINCT person_id), ' persons')
  FROM b7v JOIN p ON b7v.view_date = p.d GROUP BY p.d
),
-- C3 SDK version watch.
--
-- Compares the SET of versions, not their count, and in both directions. April
-- is the reason: javascript:3.3.6 DISAPPEARED, and a check that only notices new
-- versions arriving would have stayed silent. A swap would also keep the count
-- unchanged while changing everything.
--
-- Written as two plain CTEs joined together, never as a correlated subquery:
-- Spark rejects an outer reference inside a range predicate with
-- "Correlated column is not allowed in predicate".
c3prev AS (
  SELECT collect_set(x.sdk_version) AS vprev
  FROM   dq_pv_window x CROSS JOIN p
  WHERE  x.view_date BETWEEN date_sub(p.d, 8) AND date_sub(p.d, 1)
),
c3now AS (
  SELECT p.d AS d, collect_set(w.sdk_version) AS vnow
  FROM   dq_pv_window w JOIN p ON w.view_date = p.d
  GROUP  BY p.d
),
c3 AS (
  SELECT n.d, 'C3', 'schema', 'bronze',
         CAST(size(array_except(n.vnow, v.vprev)) + size(array_except(v.vprev, n.vnow)) AS DOUBLE),
         0.0, CAST(NULL AS DOUBLE), 0.0,
         CASE WHEN size(array_except(n.vnow, v.vprev)) + size(array_except(v.vprev, n.vnow)) > 0
              THEN 'warning' ELSE 'ok' END,
         CONCAT('today: ', concat_ws(' | ', n.vnow),
                ' | appeared: ',  COALESCE(NULLIF(concat_ws(',', array_except(n.vnow, v.vprev)), ''), '-'),
                ' | disappeared: ', COALESCE(NULLIF(concat_ws(',', array_except(v.vprev, n.vnow)), ''), '-'))
  FROM c3now n CROSS JOIN c3prev v
),
-- C4 instrumentation key constant
c4 AS (
  SELECT p.d, 'C4', 'schema', 'bronze',
         CAST(COUNT(DISTINCT w.ikey) + COUNT(DISTINCT w.app_id) AS DOUBLE), 2.0, CAST(NULL AS DOUBLE), 2.0,
         CASE WHEN COUNT(DISTINCT w.ikey) > 1 OR COUNT(DISTINCT w.app_id) > 1 THEN 'critical' ELSE 'ok' END,
         CONCAT('ikeys ', COUNT(DISTINCT w.ikey), ' | app ids ', COUNT(DISTINCT w.app_id))
  FROM dq_pv_window w JOIN p ON w.view_date = p.d GROUP BY p.d
),
-- C5 format validity
c5 AS (
  SELECT p.d, 'C5', 'schema', 'bronze',
         GREATEST(
           AVG(CASE WHEN w.gpn_raw IS NOT NULL AND w.gpn_raw NOT RLIKE '^[0-9]{8}$' THEN 1.0 ELSE 0.0 END),
           AVG(CASE WHEN w.tracking_id IS NOT NULL
                     AND w.tracking_id NOT RLIKE '^[A-Z0-9]{5}-[A-Z0-9]{7}-[0-9]{6}-[A-Z0-9]{7}-[A-Z]{3}$'
                    THEN 1.0 ELSE 0.0 END),
           AVG(CASE WHEN w.view_ts IS NULL OR w.view_ts > current_timestamp() THEN 1.0 ELSE 0.0 END)),
         0.0, CAST(NULL AS DOUBLE), 0.005,
         CASE WHEN GREATEST(
                AVG(CASE WHEN w.gpn_raw IS NOT NULL AND w.gpn_raw NOT RLIKE '^[0-9]{8}$' THEN 1.0 ELSE 0.0 END),
                AVG(CASE WHEN w.view_ts IS NULL THEN 1.0 ELSE 0.0 END)) > 0.05 THEN 'critical'
              WHEN GREATEST(
                AVG(CASE WHEN w.gpn_raw IS NOT NULL AND w.gpn_raw NOT RLIKE '^[0-9]{8}$' THEN 1.0 ELSE 0.0 END),
                AVG(CASE WHEN w.view_ts IS NULL THEN 1.0 ELSE 0.0 END)) > 0.005 THEN 'warning' ELSE 'ok' END,
         CONCAT('invalid gpn ', ROUND(100*AVG(CASE WHEN w.gpn_raw IS NOT NULL AND w.gpn_raw NOT RLIKE '^[0-9]{8}$' THEN 1.0 ELSE 0.0 END),3),
                ' % | unparsed ts ', ROUND(100*AVG(CASE WHEN w.view_ts IS NULL THEN 1.0 ELSE 0.0 END),3), ' %')
  FROM dq_pv_window w JOIN p ON w.view_date = p.d GROUP BY p.d
),
-- C6 referential integrity, page by URL and GPN against HR
c6 AS (
  SELECT p.d, 'C6', 'schema', 'bronze',
         LEAST(AVG(CASE WHEN pg.PageURL IS NOT NULL THEN 1.0 ELSE 0.0 END),
               AVG(CASE WHEN w.gpn IS NULL THEN NULL WHEN hr.WORKER_ID IS NOT NULL THEN 1.0 ELSE 0.0 END)),
         CAST(NULL AS DOUBLE), 0.95, CAST(NULL AS DOUBLE),
         CASE WHEN LEAST(AVG(CASE WHEN pg.PageURL IS NOT NULL THEN 1.0 ELSE 0.0 END),
                         AVG(CASE WHEN w.gpn IS NULL THEN NULL WHEN hr.WORKER_ID IS NOT NULL THEN 1.0 ELSE 0.0 END)) < 0.90
              THEN 'critical'
              WHEN LEAST(AVG(CASE WHEN pg.PageURL IS NOT NULL THEN 1.0 ELSE 0.0 END),
                         AVG(CASE WHEN w.gpn IS NULL THEN NULL WHEN hr.WORKER_ID IS NOT NULL THEN 1.0 ELSE 0.0 END)) < 0.95
              THEN 'warning' ELSE 'ok' END,
         CONCAT('page hit ', ROUND(100*AVG(CASE WHEN pg.PageURL IS NOT NULL THEN 1.0 ELSE 0.0 END),1),
                ' % | HR hit ', ROUND(100*AVG(CASE WHEN w.gpn IS NULL THEN NULL WHEN hr.WORKER_ID IS NOT NULL THEN 1.0 ELSE 0.0 END),1), ' %')
  FROM dq_pv_window w JOIN p ON w.view_date = p.d
  LEFT JOIN sharepoint_bronze.pages pg     ON LOWER(TRIM(pg.PageURL)) = LOWER(TRIM(w.page_url))
  LEFT JOIN imep_bronze.tbl_hr_employee hr ON hr.WORKER_ID = w.gpn
  GROUP BY p.d
),
-- S2 key completeness on silver
-- S2 judges only the keys that are actually in use. `visitorId` measured 0.0 %
-- on 2026-09-11, i.e. the column exists but is never populated, so including it
-- made the check fail on a column nobody fills. It is still reported in the note,
-- because a column that starts filling is worth noticing.
s2 AS (
  SELECT p.d, 'S2', 'schema', 'silver',
         LEAST(v.contact_resolution_rate, v.page_guid_rate),
         CAST(NULL AS DOUBLE), 0.95, CAST(NULL AS DOUBLE),
         CASE WHEN LEAST(v.contact_resolution_rate, v.page_guid_rate) < 0.80 THEN 'critical'
              WHEN LEAST(v.contact_resolution_rate, v.page_guid_rate) < 0.95 THEN 'warning' ELSE 'ok' END,
         CONCAT('contactId ', ROUND(100*v.contact_resolution_rate,1),
                ' % | marketingPageId ', ROUND(100*v.page_guid_rate,1),
                ' % | (visitorId ', ROUND(100*v.visitor_id_rate,1), ' %, not in use, not judged)')
  FROM dq_sv_daily v JOIN p ON v.view_date = p.d
),
-- G1 grain uniqueness on gold
g1k AS (
  SELECT marketingpageid, viewingcontactid, referrerapplicationid, COUNT(*) AS n
  FROM sharepoint_gold.pbi_db_interactions_metrics gm JOIN p ON gm.visitdatekey = date_format(p.d, 'yyyyMMdd')
  GROUP BY 1,2,3
),
g1 AS (
  SELECT p.d, 'G1', 'plausibility', 'gold',
         SUM(CASE WHEN n > 1 THEN n - 1 ELSE 0 END) / NULLIF(SUM(n), 0), 0.0, CAST(NULL AS DOUBLE), 0.0,
         CASE WHEN SUM(CASE WHEN n > 1 THEN 1 ELSE 0 END) > 0 THEN 'critical' ELSE 'ok' END,
         CONCAT(SUM(CASE WHEN n > 1 THEN 1 ELSE 0 END), ' duplicated grain keys of ', COUNT(*))
  FROM g1k CROSS JOIN p GROUP BY p.d
),
-- G2 row-level consistency on gold
g2 AS (
  SELECT p.d, 'G2', 'plausibility', 'gold',
         GREATEST(AVG(CASE WHEN gm.visits > gm.views THEN 1.0 ELSE 0.0 END),
                  AVG(CASE WHEN gm.views < 0 OR gm.visits < 0 OR gm.durationsum < 0 THEN 1.0 ELSE 0.0 END)),
         0.0, CAST(NULL AS DOUBLE), 0.001,
         CASE WHEN GREATEST(AVG(CASE WHEN gm.visits > gm.views THEN 1.0 ELSE 0.0 END),
                            AVG(CASE WHEN gm.views < 0 OR gm.visits < 0 THEN 1.0 ELSE 0.0 END)) > 0 THEN 'critical' ELSE 'ok' END,
         CONCAT('visits > views ', ROUND(100*AVG(CASE WHEN gm.visits > gm.views THEN 1.0 ELSE 0.0 END),4), ' %')
  FROM sharepoint_gold.pbi_db_interactions_metrics gm JOIN p ON gm.visitdatekey = date_format(p.d, 'yyyyMMdd')
  GROUP BY p.d
)
SELECT * FROM a2 UNION ALL SELECT * FROM a5
UNION ALL SELECT * FROM b3 UNION ALL SELECT * FROM b6 UNION ALL SELECT * FROM b7
UNION ALL SELECT * FROM c3 UNION ALL SELECT * FROM c4 UNION ALL SELECT * FROM c5
UNION ALL SELECT * FROM c6 UNION ALL SELECT * FROM s2
UNION ALL SELECT * FROM g1 UNION ALL SELECT * FROM g2;


-- ----------------------------------------------------------------------------
-- CELL 9b — ONSET. The first day each check left its corridor.
--
-- This is what makes grouping automatic. Checks that started failing on the same
-- day are almost always one cause, and that is a computation rather than
-- knowledge. Nobody has to know what the cause is to see that eight things broke
-- together on a Tuesday.
--
-- Limitation, stated plainly: only checks driven by the corridor engine have a
-- per-day history here, because dq_r_corridor evaluates every day in the window.
-- The explicit checks in cell 9 judge one day only, so they have no onset in this
-- edition. The persistent edition does not have that limitation: it stores a row
-- per day and per check, so every check gets an onset from its own history. That
-- is one of the concrete things the read-only edition cannot do.
-- ----------------------------------------------------------------------------
%sql
CREATE OR REPLACE TEMPORARY VIEW dq_onset AS
WITH failing AS (
  SELECT check_id, check_date
  FROM   dq_r_corridor
  WHERE  status IN ('warning', 'critical')
),
still_ok_after AS (          -- the last day the check was fine
  SELECT check_id, MAX(check_date) AS last_ok
  FROM   dq_r_corridor WHERE status = 'ok' GROUP BY check_id
)
SELECT f.check_id,
       MIN(f.check_date)                                   AS first_seen_failing,
       MIN(CASE WHEN o.last_ok IS NULL OR f.check_date > o.last_ok
                THEN f.check_date END)                     AS failing_since,
       MAX(o.last_ok)                                      AS last_healthy_day,
       COUNT(*)                                            AS days_failing
FROM   failing f LEFT JOIN still_ok_after o ON o.check_id = f.check_id
GROUP  BY f.check_id;


-- ----------------------------------------------------------------------------
-- CELL 9c — SCOPE. Which slice of the estate does a problem actually cover?
--
-- Block 0e answered this by hand for one incident. Generalised, it is the single
-- most useful automatic step: it turns "something broke" into "something broke,
-- and only on these sites", which is usually most of the diagnosis and is exactly
-- what an upstream team asks for first.
--
-- The site is derived from the URL path rather than from a SiteName column, so
-- this needs no change to the slice in cell 1 and no re-run of it.
-- ----------------------------------------------------------------------------
%sql
CREATE OR REPLACE TEMPORARY VIEW dq_scope AS
WITH p AS (SELECT date_sub(current_date(), 1) AS d),
base AS (
  SELECT w.view_date, w.session_id, w.browser_id, w.gpn,
         COALESCE(NULLIF(regexp_extract(w.page_url, '/sites/([^/?#]+)', 1), ''), '(no site in url)') AS site,
         COALESCE(w.client_browser, '(null)') AS browser,
         COALESCE(w.client_os,      '(null)') AS os,
         COALESCE(w.client_type,    '(null)') AS device,
         COALESCE(w.sdk_version,    '(null)') AS sdk
  FROM dq_pv_window w CROSS JOIN p
  WHERE w.view_date BETWEEN date_sub(p.d, 28) AND p.d
),
long AS (
            SELECT view_date, session_id, browser_id, gpn, 'site'        AS dim, site    AS val FROM base
  UNION ALL SELECT view_date, session_id, browser_id, gpn, 'browser',            browser       FROM base
  UNION ALL SELECT view_date, session_id, browser_id, gpn, 'os',                 os            FROM base
  UNION ALL SELECT view_date, session_id, browser_id, gpn, 'device',             device        FROM base
  UNION ALL SELECT view_date, session_id, browser_id, gpn, 'sdk version',        sdk           FROM base
)
SELECT
  l.dim, l.val,
  COUNT(CASE WHEN l.view_date = p.d THEN 1 END)                                   AS views_today,
  ROUND(COUNT(CASE WHEN l.view_date = p.d THEN 1 END)
      / NULLIF(COUNT(DISTINCT CASE WHEN l.view_date = p.d THEN l.session_id END), 0), 3) AS views_per_session_today,
  ROUND(COUNT(DISTINCT CASE WHEN l.view_date = p.d THEN l.browser_id END)
      / NULLIF(COUNT(DISTINCT CASE WHEN l.view_date = p.d THEN l.gpn END), 0), 2)        AS browsers_per_person_today,
  ROUND(COUNT(CASE WHEN l.view_date < p.d THEN 1 END)
      / NULLIF(COUNT(DISTINCT CASE WHEN l.view_date < p.d THEN l.session_id END), 0), 3) AS views_per_session_prior,
  ROUND(COUNT(DISTINCT CASE WHEN l.view_date < p.d THEN l.browser_id END)
      / NULLIF(COUNT(DISTINCT CASE WHEN l.view_date < p.d THEN l.gpn END), 0), 2)        AS browsers_per_person_prior
FROM long l CROSS JOIN p
GROUP BY l.dim, l.val
HAVING COUNT(CASE WHEN l.view_date = p.d THEN 1 END) > 200;   -- drop the long tail


-- ----------------------------------------------------------------------------
-- CELL 9d — KNOWN CAUSES. Data, not code.
--
-- A cause is a hypothesis about a system, and no computation gets from
-- "identifiers stopped repeating" to "the hosting layer re-initialises the SDK".
-- Somebody works that out once. What matters is that it is recorded as DATA, so
-- adding one costs an INSERT rather than an edit to a notebook, and so a cluster
-- with nothing recorded renders as "cause not yet identified" instead of
-- pretending to be several unrelated problems.
--
-- To record a new one: add a row. To record nothing: leave it, and the board will
-- describe the cluster from the measurements alone.
-- ----------------------------------------------------------------------------
%sql
CREATE OR REPLACE TEMPORARY VIEW dq_known_causes AS
SELECT * FROM VALUES
 ('april-identity',
  DATE '2026-04-07',
  'Browser identity stopped persisting',
  'Since 7 April 2026 every page view arrives with a fresh browser and session identity, so a view counts as a visit. Azure Monitor confirmed on 9 September that the identifiers are already different when generated in the browser and that ingestion does not alter them; the investigation has moved to the SharePoint and SPFx hosting layer. Page views and unique visitors are unaffected, because visitors are counted on the employee number.',
  array('B1','B2','B3','B4','B5','B6','B7','D1d')),
 ('double-fire',
  DATE '2025-01-01',
  'The same page load is recorded twice',
  'About one page view in eleven is the same page firing twice within a second, which overstates page views and shortens measured reading time. Long standing and unrelated to the April incident.',
  array('A5','C8')),
 ('page-lookup',
  DATE '2025-01-01',
  'Some pages are missing from the reference list',
  'Roughly one page view in eight cannot be matched to the page inventory by its address, so page-level breakdowns are incomplete. Employee lookups are unaffected.',
  array('C6'))
AS t(cause_id, since, title, description, check_ids);


-- ----------------------------------------------------------------------------
-- CELL 9e — THE VERDICTS, MATERIALISED ONCE.
--
-- Everything above is a lazy view, so each cell that reads it recomputes it. The
-- first run showed exactly that cost: cell 10 took 14m41s and cell 10b then took
-- another 10 minutes for the same work. Both consumers now read this one cached
-- result instead, and both become instant.
--
-- The daily metrics are cached first, and underneath it. They are where the cost
-- actually sits (the per-person window and the rolling-week join in cell 2), they
-- are under 2,000 rows, and cell 10c charts every day of them rather than only
-- yesterday. Cached here, the verdicts, the onset view and the trend charts all
-- read the same rows instead of each recomputing them.
--
-- CACHE TABLE holds it in cluster memory, never in the lakehouse. Cell 11
-- releases it.
-- ----------------------------------------------------------------------------
%sql
CACHE TABLE dq_metric_daily;

%sql
CREATE OR REPLACE TEMPORARY VIEW dq_results AS
SELECT check_date, check_id, family, layer, metric_value, baseline,
       lower_bound, upper_bound, status, note, trend
FROM   dq_r_corridor WHERE check_date = date_sub(current_date(), 1)
UNION ALL
SELECT check_date, check_id, family, layer, metric_value, baseline,
       lower_bound, upper_bound, status, note, 'unknown' AS trend
FROM   dq_r_explicit;

%sql
CACHE TABLE dq_results;


-- ----------------------------------------------------------------------------
-- CELL 10 — THE RESULT. One grid, worst first, with the figures each finding
-- puts at risk. This is the Health Overview, computed rather than stored.
-- ----------------------------------------------------------------------------
%sql
SELECT
  CASE r.status WHEN 'critical' THEN '1 critical' WHEN 'warning' THEN '2 warning'
                WHEN 'info' THEN '3 info' ELSE '4 ok' END       AS severity,
  r.check_id, r.layer, r.family,
  ROUND(r.metric_value, 4)                                      AS value,
  ROUND(r.baseline, 4)                                          AS baseline,
  ROUND(r.lower_bound, 4)                                       AS lo,
  ROUND(r.upper_bound, 4)                                       AS hi,
  CASE WHEN r.status IN ('warning','critical')
       THEN concat_ws(', ', collect_set(a.figure)) ELSE '' END  AS figures_at_risk,
  r.trend                                                       AS trend,
  r.note
FROM dq_results r
LEFT JOIN dq_check_affects a ON a.check_id = r.check_id
GROUP BY r.status, r.check_id, r.layer, r.family, r.metric_value, r.baseline,
         r.lower_bound, r.upper_bound, r.trend, r.note
ORDER BY severity, r.check_id;


-- ----------------------------------------------------------------------------
-- CELL 10b — HEALTH AT A GLANCE. Run this straight after cell 10.
--
-- Cell 10 is the detail grid, written for whoever maintains the checks. It is not
-- readable by a first- or second-line responder: "S2 critical, value 0" says
-- nothing about what is wrong or whether it matters.
--
-- This cell answers the two questions that person actually has: which published
-- figures can I trust, and what is wrong. It organises by FIGURE rather than by
-- check, names everything in plain words, and keeps check ids out of the
-- headline.
--
-- HOW IT EXPLAINS SOMETHING IT HAS NEVER SEEN
-- Three separable jobs, and only the last needs a person:
--   1. GROUPING is computed. Checks that started failing on the same day are
--      almost always one cause (dq_onset). No knowledge required.
--   2. DESCRIPTION is computed. What moved, from what to what, since when, which
--      published figures it touches, which control figures did NOT move, and
--      which slice of the estate is affected (dq_scope). That is a template
--      filled with measurements, so it cannot be wrong.
--   3. THE CAUSE is written once by a person and stored as data (dq_known_causes).
--      No computation gets from "identifiers stopped repeating" to "the hosting
--      layer re-initialises the SDK". A cluster with nothing recorded says
--      "cause not yet identified", which is honest and is also the prompt to
--      record one.
-- A language model could rephrase the computed description more fluently. It
-- must not be asked to supply a cause: an invented explanation in a data-quality
-- tool is worse than none.
-- ----------------------------------------------------------------------------
%python
from datetime import date, timedelta

LABELS = {
 "A1": ("Arrival volume",            "Did roughly as much data arrive as on a normal day of this weekday?"),
 "A2": ("Freshness",                 "How old is the newest event we hold?"),
 "A4": ("Complete capture",          "Is every event stored, or only a sample of them?"),
 "A5": ("Repeated events",           "Did the same event land more than once?"),
 "A6": ("Rows kept between layers",  "Does the share of rows carried forward look like it normally does?"),
 "B1": ("Pages per visit",           "Does one visit still contain several pages?"),
 "B2": ("Visits per browser",        "How many visits does one browser gather in a week?"),
 "B3": ("Returning browsers",        "Do we recognise yesterday's browsers again today?"),
 "B4": ("Browsers per employee",     "Does one employee still look like one person?"),
 "B5": ("Single-page visits",        "How many visits contain only one page?"),
 "B6": ("Visit continuity",          "Does a visit survive a short pause?"),
 "B7": ("Two ways of counting visits","Do the cookie count and the person count agree?"),
 "B8": ("Employee number present",   "Do events carry the number we count people on?"),
 "C3": ("Tracking software version", "Did the tracking software change?"),
 "C4": ("Tracking configuration",    "Is the data still coming from the same application?"),
 "C5": ("Value formats",             "Do identifiers and dates have the shape we expect?"),
 "C6": ("Reference lookups",         "Do pages and employees resolve against their reference lists?"),
 "C8": ("Double-counted page loads", "Is the same page counted twice in the same instant?"),
 "D1a":("Published page views",      "Is the published page-view figure in its normal range?"),
 "D1b":("Published visits",          "Is the published visit figure in its normal range?"),
 "D1c":("Published unique visitors", "Is the published visitor figure in its normal range?"),
 "D1d":("Published pages per visit", "Is the published ratio in its normal range?"),
 "D3": ("Top pages stable",          "Are broadly the same pages at the top as last week?"),
 "D4": ("Campaign tagging",          "Do page views carry a campaign tag?"),
 "D7": ("Clicks per page view",      "Are clicks arriving in proportion to page views?"),
 "G1": ("No double counting",        "Is each page, day and person summarised exactly once?"),
 "G2": ("Summary rows consistent",   "Do the numbers inside one summary row contradict each other?"),
 "G3": ("Summary matches detail",    "Does the summary add up to the detail it came from?"),
 "G4": ("People kept in the summary", "Does every person in the detail reach the summary?"),
 "S1": ("People recognised",         "Does one employee become exactly one person in the model?"),
 "S2": ("Keys assigned",             "Did every row receive the keys it needs?"),
}
FIGURES = [("page_views","Page views"), ("unique_visitors","Unique visitors"), ("visits","Visits"),
           ("pages_per_visit","Pages per visit"), ("avg_time_on_page","Time on page"),
           ("bounce_rate","Bounce rate"), ("tracking_coverage","Campaign tagging"),
           ("page_breakdowns","Page breakdowns"), ("clicks","Clicks")]
FIGNAME = dict(FIGURES)
RANK = {"critical": 3, "warning": 2, "info": 1, "ok": 0}
COL  = {"critical": ("#BD000C", "#FBE6E7"), "warning": ("#E4A911", "#FDF6E3"),
        "info": ("#7A7870", "#ECEBE4"),     "ok": ("#6F7A1A", "#F1F3E7")}
VERDICT = {("critical", True): "Broke recently", ("critical", False): "Known issue, long standing",
           ("warning", True): "Changed, worth a look", ("warning", False): "Read with care",
           ("info", True): "Cannot be judged", ("info", False): "Cannot be judged",
           ("ok", True): "Sound", ("ok", False): "Sound"}
CHECK_DAY = date.today() - timedelta(days=1)

rows = spark.sql("SELECT check_id, status, note, trend, metric_value, baseline FROM dq_results").collect()
affects = spark.sql("SELECT check_id, figure FROM dq_check_affects").collect()
onset   = {r["check_id"]: r for r in spark.sql("SELECT * FROM dq_onset").collect()}
causes  = spark.sql("SELECT * FROM dq_known_causes").collect()
scope   = spark.sql("SELECT * FROM dq_scope").collect()

by_check = {r["check_id"]: r for r in rows}
fig_checks = {}
for a in affects:
    if a["check_id"] in by_check:
        fig_checks.setdefault(a["figure"], []).append(a["check_id"])
def worst(ids):
    return max((by_check[i]["status"] for i in ids), key=lambda s: RANK.get(s, 0)) if ids else "ok"

# ---- figure tiles -----------------------------------------------------------
tiles = ""
for key, name in FIGURES:
    ids = fig_checks.get(key, [])
    st  = worst(ids)
    bad = sorted([i for i in ids if by_check[i]["status"] in ("critical","warning")],
                 key=lambda i: -RANK[by_check[i]["status"]])
    moving = [i for i in bad if by_check[i]["trend"] == "moving"]
    fg, bg = COL[st]
    names = [LABELS.get(i, (i,""))[0] for i in bad]
    names = [n for n in names if n.lower() != name.lower()]
    if st == "info":   detail = "no measurement available today"
    elif not bad:      detail = "nothing wrong with it"
    else:
        detail = "; ".join(n.lower() for n in names[:3]) or "see the detail below"
        if len(names) > 3: detail += f" and {len(names)-3} more"
    if bad and moving:
        detail += f'<div style="margin-top:5px;font-size:11px;color:{fg};font-weight:600">changed in the last week</div>'
    elif bad:
        detail += '<div style="margin-top:5px;font-size:11px;color:#7A7870">unchanged for weeks, already known</div>'
    tiles += (f'<div style="background:{bg};border-top:3px solid {fg};padding:12px 14px">'
              f'<div style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:{fg};font-weight:700">{VERDICT[(st, bool(moving))]}</div>'
              f'<div style="font-size:16px;font-weight:600;color:#000;margin:3px 0 5px">{name}</div>'
              f'<div style="font-size:12px;color:#5A5D5C;line-height:1.4">{detail}</div></div>')

# ---- cluster the failing checks ---------------------------------------------
attention = [c for c in by_check if by_check[c]["status"] in ("critical","warning")]
recorded  = {}
for cz in causes:
    for cid in cz["check_ids"]:
        if cid in attention: recorded[cid] = cz["cause_id"]

groups = {}
for c in attention:
    if c in recorded:                       key = ("known", recorded[c])
    elif onset.get(c, {}) and onset[c]["failing_since"]: key = ("onset", onset[c]["failing_since"])
    else:                                   key = ("lone", c)
    groups.setdefault(key, []).append(c)

# ---- describe each cluster from measurements alone ---------------------------
def describe(members):
    """Everything in here is computed. Nothing is knowledge about this system."""
    parts = []
    ons = [onset[c]["failing_since"] for c in members if c in onset and onset[c]["failing_since"]]
    if ons:
        d = min(ons)
        parts.append(f"{len(members)} check{'s' if len(members)!=1 else ''} have been failing since {d:%d %B %Y}"
                     f", {(CHECK_DAY - d).days} days.")
    else:
        parts.append(f"{len(members)} check{'s' if len(members)!=1 else ''} failing. "
                     f"No day-by-day history is kept in this edition, so the start date is unknown.")
    moved = [c for c in members if by_check[c]["metric_value"] is not None and by_check[c]["baseline"] is not None
             and by_check[c]["baseline"] != 0]
    if moved:
        c = max(moved, key=lambda c: abs(by_check[c]["metric_value"] - by_check[c]["baseline"]) / abs(by_check[c]["baseline"]))
        parts.append(f"The largest movement is {LABELS.get(c,(c,''))[0].lower()}, "
                     f"now {by_check[c]['metric_value']:.4g} against an expected {by_check[c]['baseline']:.4g}.")
    # controls: published figures that no failing check points at
    hit = {a["figure"] for a in affects if a["check_id"] in members}
    ctrl = [n for k, n in FIGURES if k not in hit and worst(fig_checks.get(k, [])) == "ok"]
    if ctrl:
        parts.append(f"Unaffected throughout: {', '.join(ctrl).lower()}.")
    return " ".join(parts)

def scope_line(members):
    """Which slice of the estate deviates? Computed, and often most of the answer."""
    out = []
    for dim in ("site", "browser", "os", "device", "sdk version"):
        vals = [r for r in scope if r["dim"] == dim and r["browsers_per_person_prior"]]
        if len(vals) < 2: continue
        hi = [r for r in vals if r["browsers_per_person_today"] and
              r["browsers_per_person_today"] > 1.6 * r["browsers_per_person_prior"]]
        if hi and len(hi) < len(vals):
            out.append(f"{dim}: only {', '.join(sorted(r['val'] for r in hi)[:4])}")
        elif hi:
            out.append(f"{dim}: every value")
    return "Scope by dimension &mdash; " + "; ".join(out) if out else ""

order = sorted(groups, key=lambda g: (-max(RANK[by_check[c]["status"]] for c in groups[g]), -len(groups[g])))
items = ""
for g in order:
    members = sorted(groups[g], key=lambda c: (-RANK[by_check[c]["status"]], c))
    g_st = max((by_check[c]["status"] for c in members), key=lambda s: RANK[s])
    g_mv = any(by_check[c]["trend"] == "moving" for c in members)
    gfg, gbg = COL[g_st]
    if g[0] == "known":
        cz = next(z for z in causes if z["cause_id"] == g[1])
        g_title, g_text, tag = cz["title"], cz["description"], "IDENTIFIED CAUSE"
    elif g[0] == "onset":
        g_title = f"Something changed on {g[1]:%d %B %Y}"
        g_text  = describe(members) + " <b>Cause not yet identified.</b> Record it in cell 9d once it is known."
        tag = "GROUPED BY START DATE"
    else:
        only = members[0]
        g_title = LABELS.get(only, (only, ""))[0]
        g_text  = describe(members) + " <b>Cause not yet identified.</b>"
        tag = "SINGLE CHECK"
    sc = scope_line(members)
    g_figs = sorted({FIGNAME.get(a["figure"], a["figure"]) for a in affects if a["check_id"] in members})
    items += (f'<tr><td colspan="3" style="padding:18px 0 6px">'
              f'<div style="border-left:4px solid {gfg};background:{gbg};padding:12px 14px">'
              f'<div style="font-size:10px;letter-spacing:.06em;color:{gfg};font-weight:700">{tag}'
              f'{" &middot; MOVED THIS WEEK" if g_mv else ""}</div>'
              f'<div style="font-size:15px;font-weight:600;color:#000;margin-top:3px">{g_title}</div>'
              f'<div style="font-size:12.5px;color:#5A5D5C;margin-top:5px;max-width:92ch;line-height:1.5">{g_text}</div>'
              + (f'<div style="font-size:11.5px;color:#7A7870;margin-top:6px">{sc}</div>' if sc else "")
              + f'<div style="font-size:11px;color:#7A7870;margin-top:6px">'
              f'{len(members)} check{"s" if len(members)!=1 else ""} &middot; affects {", ".join(g_figs) if g_figs else "nothing published"}</div>'
              f'</div></td></tr>')
    for c in members:
        r = by_check[c]; st, note, trend = r["status"], r["note"], r["trend"]
        chip = ('<span style="background:#FBE6E7;color:#BD000C;font-size:10px;font-weight:700;padding:2px 6px;margin-left:6px">CHANGED RECENTLY</span>' if trend=="moving"
                else '<span style="background:#ECEBE4;color:#7A7870;font-size:10px;padding:2px 6px;margin-left:6px">ongoing</span>' if trend=="stable"
                else '<span style="background:#fff;border:1px solid #CCCABC;color:#8E8D83;font-size:10px;padding:1px 6px;margin-left:6px">trend not tracked</span>')
        fg, bg = COL[st]
        title, question = LABELS.get(c, (c, ""))
        figs = ", ".join(FIGNAME.get(f, f) for f in sorted({a["figure"] for a in affects if a["check_id"] == c})) or "&mdash;"
        items += (f'<tr><td style="padding:9px 12px 9px 24px;border-bottom:1px solid #ECEBE4;white-space:nowrap;vertical-align:top">'
                  f'<span style="background:{fg};color:#fff;font-size:10px;font-weight:700;padding:2px 7px;letter-spacing:.05em">{st.upper()}</span></td>'
                  f'<td style="padding:9px 12px 9px 0;border-bottom:1px solid #ECEBE4;vertical-align:top">'
                  f'<div style="font-size:14px;font-weight:600;color:#000">{title}{chip}</div>'
                  f'<div style="font-size:12px;color:#7A7870;margin-top:2px">{question}</div>'
                  f'<div style="font-size:11px;color:#8E8D83;margin-top:4px;font-family:ui-monospace,Menlo,monospace">{note}</div></td>'
                  f'<td style="padding:9px 0;border-bottom:1px solid #ECEBE4;font-size:12px;color:#5A5D5C;vertical-align:top;white-space:nowrap">{figs}'
                  f'<div style="font-size:10px;color:#B8B3A2;margin-top:3px">check {c}</div></td></tr>')

n_crit = sum(1 for r in by_check.values() if r["status"] == "critical")
n_warn = sum(1 for r in by_check.values() if r["status"] == "warning")
n_ok   = sum(1 for r in by_check.values() if r["status"] == "ok")
n_mv   = sum(1 for r in by_check.values() if r["status"] in ("critical","warning") and r["trend"] == "moving")
n_new  = sum(1 for g in order if g[0] != "known")
sound  = [n for k, n in FIGURES if worst(fig_checks.get(k, [])) == "ok"]
unknown= [n for k, n in FIGURES if worst(fig_checks.get(k, [])) == "info"]

if n_mv:      hfg, hbg, headline = "#BD000C", "#FBE6E7", f"{n_mv} changed recently"
elif n_new:   hfg, hbg, headline = "#BD000C", "#FBE6E7", f"{n_new} cause{'s' if n_new!=1 else ''} not yet identified"
elif n_crit:  hfg, hbg, headline = "#BD000C", "#FBE6E7", f"{n_crit} serious, all known"
elif n_warn:  hfg, hbg, headline = "#E4A911", "#FDF6E3", f"{n_warn} to look at"
else:         hfg, hbg, headline = "#6F7A1A", "#F1F3E7", "All clear"

displayHTML(f"""
<div style="font-family:'Frutiger 45 Light',Frutiger,'Helvetica Neue',Arial,sans-serif;color:#404040;background:#fff;padding:22px 26px;max-width:1180px">
  <div style="display:flex;justify-content:space-between;align-items:baseline;border-bottom:2px solid #E60000;padding-bottom:12px">
    <div><div style="font-size:26px;font-weight:300;color:#000">Data health</div>
      <div style="font-size:13px;color:#7A7870;margin-top:3px">Intranet analytics &middot; judged for {CHECK_DAY:%d %B %Y}</div></div>
    <div style="background:{hbg};color:{hfg};font-weight:700;font-size:13px;padding:7px 14px">{headline}</div>
  </div>

  <div style="margin-top:22px;font-size:15px;font-weight:600;color:#000">Can I trust this figure today?</div>
  <div style="font-size:12px;color:#7A7870;margin:3px 0 12px">
    {len(sound)} of {len(FIGURES)} figures are sound: {', '.join(sound) if sound else 'none'}.
    {('Could not be judged today: ' + ', '.join(unknown) + '.') if unknown else ''}
  </div>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px">{tiles}</div>

  <div style="margin-top:30px;font-size:15px;font-weight:600;color:#000">What needs attention</div>
  <div style="font-size:12px;color:#7A7870;margin:3px 0 10px">
    Grouped by cause. Where no cause is recorded, checks that started failing on the same day are grouped
    together and described from the measurements alone. The grey line under each check is its raw value.
  </div>
  {'<table style="width:100%;border-collapse:collapse">' + items + '</table>' if items
   else '<div style="background:#F1F3E7;border-left:3px solid #6F7A1A;padding:12px 14px;font-size:13px">Nothing is failing. All ' + str(n_ok) + ' checks passed.</div>'}

  <div style="margin-top:22px;padding-top:10px;border-top:1px solid #ECEBE4;font-size:11px;color:#8E8D83">
    {n_crit} serious &middot; {n_warn} to look at &middot; {n_ok} passed &middot; {n_mv} changed recently,
    out of {len(by_check)} checks across bronze, silver and gold, tracing back to {len(order)} cluster{'s' if len(order)!=1 else ''}
    of which {len(order)-n_new} {'has' if len(order)-n_new == 1 else 'have'} a recorded cause.
    Nothing was written: this notebook creates only temporary views.
  </div>
</div>
""")


-- ----------------------------------------------------------------------------
-- CELL 10c — TRENDS. Every daily figure against what that weekday normally is.
--
-- The board in 10b says what is wrong today. It cannot show the shape of a
-- problem: whether it arrived as a step or drifted in, whether it is a one-day
-- spike, and above all whether several figures broke on the same day. A person
-- sees that in a second on a chart and a table never shows it, so this cell
-- draws one small chart per metric, all on the same time axis.
--
-- Each chart shows four things, all on one scale:
--   the actual daily value                  dark line
--   the expected value for that weekday     dashed line (cell 5: median of the
--                                           same weekday over the 8 weeks before)
--   the normal range                        grey band, expected +/- 3 MAD (for the
--                                           layer ratios: the allowed deviation)
--   the healthy limit, where one is fixed   dotted line (cell 6)
-- Days outside the normal range are red dots. Failing checks come first; the
-- charts that did not move stay on the page as the control group, because what
-- did NOT break narrows a problem down as much as what did.
--
-- LAYER FLOW comes before all of that, because it is what the pipeline owns.
-- For page views and for people, one chart shows the figure in bronze, silver
-- and gold, and beside it the two ratios between the layers (A6 and G3, S1 and
-- G4). Traffic cancels out in a ratio, so a healthy one is flat on any day, and
-- any movement is the pipeline losing or doubling data rather than visitors
-- changing their habits.
--
-- WHY THE CHARTS SHOW MONDAY TO FRIDAY ONLY
-- Weekend volume is a fraction of a weekday's, so with weekends drawn every
-- volume chart is a saw blade scaled from near zero to the weekday peak, and a
-- five per cent drop on a Tuesday disappears inside it. Leaving weekends out
-- changes nothing about detection, because the expected value already compares
-- a Saturday only with earlier Saturdays. It only changes the scale. Weekends are
-- therefore still judged: the summary line counts them and names them when they
-- leave their range, so a broken Saturday is not lost, just not drawn.
--
-- WHY THE FIXED LIMIT IS DRAWN AS WELL
-- The expected value adapts. A break older than eight weeks is, by now, what the
-- baseline expects, so a permanently broken metric sits comfortably inside its
-- band. The April identity break is exactly that case. The dotted line does not
-- adapt, and a chart saying "inside its pattern but beyond the healthy limit" is
-- the only honest way to show a lasting fault.
--
-- Not charted: the explicit checks of cell 9 (A5, C6, S2, G1 ...), which are
-- computed for one day only and therefore have no history in this edition.
-- Reads cached results from 9e, so it is quick. Writes nothing.
-- Titles come from cell 10b when it has run; otherwise metric names are shown.
-- ----------------------------------------------------------------------------
%python
import html, json
from datetime import date, timedelta

CHECK_DAY   = date.today() - timedelta(days=1)   # the day the board judges; today is still partial
LAYER_ORDER = {"bronze": 0, "staging": 1, "silver": 2, "gold": 3}
RANK        = {"critical": 3, "warning": 2, "info": 1, "ok": 0}
BADGE       = {"critical": "#BD000C", "warning": "#E4A911", "info": "#7A7870"}
TITLES      = {c: t for c, (t, _) in globals().get("LABELS", {}).items()}
CONTEXT     = {"sessions": "Sessions", "browser_ids": "Browser identities", "persons": "Employees seen",
               "silver_rows": "Silver rows", "silver_contacts": "Silver contacts"}
# Layer flow, drawn first: the volumes of one figure in each layer, then the two
# ratios between them. The ratios are the signal; the volumes give the scale.
FLOW = [
    ("Page views through the layers",
     [("views", "bronze page views"), ("silver_rows", "silver rows"), ("gold_views", "gold views")],
     ["keep_share_silver", "gold_to_silver_views"]),
    ("People through the layers",
     [("persons", "bronze employees"), ("silver_contacts", "silver contacts"), ("gold_unique_visitors", "gold visitors")],
     ["silver_to_bronze_persons", "gold_to_silver_persons"]),
]
FLOW_INK  = ["#946F29", "#8E8D83", "#404040"]     # bronze, silver, gold: Bronze II, Grey III, Grey VI
FLOW_ONLY = {m for _, _, ratios in FLOW for m in ratios} | {"silver_rows", "silver_contacts"}
W, H, L, R, T, B = 360, 136, 46, 12, 12, 22       # chart geometry in viewBox units
PW, PH = W - L - R, H - T - B

pts = spark.sql(f"""
  SELECT d.metric, d.view_date, d.value, b.n_hist, b.baseline, b.corr_low, b.corr_high
  FROM   dq_metric_daily d
  LEFT JOIN dq_metric_baseline b ON b.metric = d.metric AND b.view_date = d.view_date
  WHERE  d.view_date <= DATE '{CHECK_DAY:%Y-%m-%d}'
""").collect()
defs    = {r["metric"]: r for r in spark.sql("""
             SELECT check_id, metric, layer, band_warn,
                    CAST(abs_warn_low AS DOUBLE) AS abs_warn_low, CAST(abs_warn_high AS DOUBLE) AS abs_warn_high,
                    CAST(rel_warn_pct AS DOUBLE) AS rel_warn_pct
             FROM   dq_check_def""").collect()}   # cast: a literal like 1.50 can arrive as DECIMAL
verdict = {r["check_id"]: r["status"] for r in spark.sql("SELECT check_id, status FROM dq_results").collect()}

days = sorted({r["view_date"] for r in pts})
at   = {d: i for i, d in enumerate(days)}
N    = len(days)

series = {}
for r in pts:
    s = series.setdefault(r["metric"], {k: [None] * N for k in ("v", "e", "lo", "hi")})
    i = at[r["view_date"]]
    s["v"][i] = r["value"]
    if r["n_hist"] is not None and r["n_hist"] >= 4:      # same rule as the corridor engine
        s["e"][i], s["lo"][i], s["hi"][i] = r["baseline"], r["corr_low"], r["corr_high"]
        d = defs.get(r["metric"])
        if d and not d["band_warn"]:
            # Judged by stated limits, not by the statistical band (cell 6), so draw
            # what is judged: the allowed relative deviation, or no band at all.
            rel, e = d["rel_warn_pct"], r["baseline"]
            s["lo"][i], s["hi"][i] = (e - abs(e) * rel, e + abs(e) * rel) if rel is not None and e is not None else (None, None)

# What is drawn: business days only, on a continuous axis (Friday runs into Monday).
# `days` and `series` keep every day and feed the summary line.
cdays   = [d for d in days if d.weekday() < 5]
cseries = {m: {k: [s[k][at[d]] for d in cdays] for k in s} for m, s in series.items()}
C       = len(cdays)

def fmt(v, pct=False, p=3):
    if v is None: return "&ndash;"
    if pct:       return f"{100 * v:.{p}g} %"
    a = abs(v)
    if a >= 1e6:  return f"{v / 1e6:.2f}M"
    if a >= 1e4:  return f"{v / 1e3:.1f}k"
    if a >= 100:  return f"{v:,.0f}"
    if a == 0:    return "0"
    return f"{v:.{p}g}"

def axis_labels(a, b, pct):
    """Enough digits that the top and bottom label differ (1.001 vs 1, not 1 vs 1)."""
    for p in range(3, 9):
        if fmt(a, pct, p) != fmt(b, pct, p): break
    return fmt(a, pct, p), fmt(b, pct, p)

def outside(s, i, d=None):
    """Would this day be flagged? The band, plus the stated limits for checks judged by limits alone."""
    v = s["v"][i]
    if v is None or s["e"][i] is None:      # no baseline yet: the engine says "info", so no mark either
        return False
    band = s["lo"][i] is not None and (v < s["lo"][i] or v > s["hi"][i])
    if d is None or d["band_warn"]:
        return band
    return (band or (d["abs_warn_low"] is not None and v < d["abs_warn_low"])
                 or (d["abs_warn_high"] is not None and v > d["abs_warn_high"]))

def xpos(i):
    return L + PW * i / max(C - 1, 1)

def runs(vals):
    """Index runs without gaps, so a missing day breaks the line instead of bridging it."""
    out, cur = [], []
    for i, v in enumerate(vals):
        if v is None:
            if cur: out.append(cur); cur = []
        else:
            cur.append(i)
    return out + ([cur] if cur else [])

def title_of(m):
    d = defs.get(m)
    return TITLES.get(d["check_id"], m) if d else CONTEXT.get(m, m)

def chart(m):
    s, d = cseries[m], defs.get(m)
    pct  = "_share" in m
    lims = [(d["abs_warn_low"], "above"), (d["abs_warn_high"], "below")] if d else []
    lims = [(v, side) for v, side in lims if v is not None]
    data = [v for k in ("v", "lo", "hi") for v in s[k] if v is not None]
    if not any(v is not None for v in s["v"]):
        return (f'<div style="height:{H}px;display:flex;align-items:center;justify-content:center;'
                f'background:#ECEBE4;font-size:12px;color:#7A7870">no values in this window</div>')
    # A limit far from the data would flatten the chart into a line along one edge
    # and hide the very pattern it is drawn for. Near limits are drawn to scale;
    # far ones are named at the edge they lie beyond.
    dlo, dhi = min(data), max(data)
    span = (dhi - dlo) or abs(dhi) * 0.05 or 1.0
    # A check judged by its limits alone always shows them: otherwise a ratio that
    # sits at 1.00000 is scaled to its fifth decimal and noise reads as a spike.
    near = lims if d and not d["band_warn"] else [(v, side) for v, side in lims if dlo - span <= v <= dhi + span]
    far  = [(v, side) for v, side in lims if (v, side) not in near]
    lo, hi = min(data + [v for v, _ in near]), max(data + [v for v, _ in near])
    pad = (hi - lo) * 0.08 or abs(hi) * 0.05 or 1.0
    lo, hi = lo - pad, hi + pad
    y = lambda v: T + PH * (1 - (v - lo) / (hi - lo))

    svg = []
    for v, side in far:
        above = v > hi
        svg.append(f'<text x="{W - R}" y="{T + 8 if above else T + PH - 4}" text-anchor="end" font-size="9" '
                   f'fill="#946F29">healthy {side} {fmt(v, pct)}, off the scale {"&#8593;" if above else "&#8595;"}</text>')
    for run in runs(s["lo"]):                                        # normal range
        up = " ".join(f"{xpos(i):.1f},{y(s['hi'][i]):.1f}" for i in run)
        dn = " ".join(f"{xpos(i):.1f},{y(s['lo'][i]):.1f}" for i in reversed(run))
        svg.append(f'<polygon points="{up} {dn}" fill="#ECEBE4"/>')
    for v, side in near:                                             # fixed healthy limit
        svg.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#946F29" '
                   f'stroke-width="1" stroke-dasharray="1.5 2.5"/>'
                   f'<text x="{W - R}" y="{y(v) + (10 if side == "above" and y(v) + 10 <= T + PH - 3 else -3):.1f}" text-anchor="end" '
                   f'font-size="9" fill="#946F29">'
                   f'healthy {side} {fmt(v, pct)}</text>')
    for key, style in (("e", 'stroke="#5A5D5C" stroke-width="1.25" stroke-dasharray="4 3"'),
                       ("v", 'stroke="#404040" stroke-width="1.75"')):
        for run in runs(s[key]):
            pts_ = " ".join(f"{xpos(i):.1f},{y(s[key][i]):.1f}" for i in run)
            svg.append(f'<polyline points="{pts_}" fill="none" {style} stroke-linejoin="round"/>')
    for i in range(C):                                               # days outside the range
        if outside(s, i, d):
            svg.append(f'<circle cx="{xpos(i):.1f}" cy="{y(s["v"][i]):.1f}" r="3" fill="#BD000C" '
                       f'stroke="#fff" stroke-width="1"/>')
    tips = []
    for i, dd in enumerate(cdays):
        if s["e"][i] is None:
            exp = "no expected value yet, fewer than four earlier " + f"{dd:%A}s"
        elif s["lo"][i] is None:
            exp = f"expected {fmt(s['e'][i], pct)}"
        else:
            kind = "normal" if d is None or d["band_warn"] else "allowed"
            exp = f"expected {fmt(s['e'][i], pct)}, {kind} {fmt(s['lo'][i], pct)} to {fmt(s['hi'][i], pct)}"
        flag = '<br><b style="color:#BD000C">outside the range it is judged by</b>' if outside(s, i, d) else ""
        tips.append(f"<b>{dd:%a} {dd.day} {dd:%b}</b><br>actual {fmt(s['v'][i], pct)}<br>{exp}{flag}")
    return frame(svg, lo, hi, pad, pct, tips)

def flow_chart(lines):
    """The same figure in each layer, one line per layer, on the shared business-day axis."""
    ss = [(cseries[m], label, FLOW_INK[k]) for k, (m, label) in enumerate(lines) if m in cseries]
    data = [v for s, _, _ in ss for v in s["v"] if v is not None]
    if not data:
        return (f'<div style="height:{H}px;display:flex;align-items:center;justify-content:center;'
                f'background:#ECEBE4;font-size:12px;color:#7A7870">no values in this window</div>')
    lo, hi = min(data), max(data)
    pad = (hi - lo) * 0.08 or abs(hi) * 0.05 or 1.0
    lo, hi = lo - pad, hi + pad
    y = lambda v: T + PH * (1 - (v - lo) / (hi - lo))
    svg = []
    for s, _, ink in ss:
        for run in runs(s["v"]):
            pts_ = " ".join(f"{xpos(i):.1f},{y(s['v'][i]):.1f}" for i in run)
            svg.append(f'<polyline points="{pts_}" fill="none" stroke="{ink}" stroke-width="1.75" stroke-linejoin="round"/>')
    tips = [f"<b>{dd:%a} {dd.day} {dd:%b}</b>" + "".join(
                f'<br><span style="color:{ink}">&#9632;</span> {label} {fmt(s["v"][i])}' for s, label, ink in ss)
            for i, dd in enumerate(cdays)]
    return frame(svg, lo, hi, pad, False, tips)

def frame(svg, lo, hi, pad, pct, tips):
    """Baseline, Monday ticks, the two y extremes, the hover marker, and the <svg> around it all."""
    y = lambda v: T + PH * (1 - (v - lo) / (hi - lo))
    svg = list(svg)
    svg.append(f'<line x1="{L}" x2="{W - R}" y1="{T + PH}" y2="{T + PH}" stroke="#000" stroke-width="1"/>')
    mondays = [i for i, dd in enumerate(cdays) if dd.weekday() == 0]
    for n, i in enumerate(reversed(mondays)):                       # label every other Monday, latest first
        svg.append(f'<line x1="{xpos(i):.1f}" x2="{xpos(i):.1f}" y1="{T + PH}" y2="{T + PH + 3}" stroke="#000"/>')
        if n % 2 == 0:
            svg.append(f'<text x="{xpos(i):.1f}" y="{H - 5}" text-anchor="middle" font-size="9" '
                       f'fill="#7A7870">{cdays[i].day} {cdays[i]:%b}</text>')
    top, bottom = axis_labels(hi - pad, lo + pad, pct)               # only the extremes, no gridlines
    for v, label in ((hi - pad, top), (lo + pad, bottom))[: 1 if top == bottom else 2]:
        svg.append(f'<text x="{L - 5}" y="{y(v) + 3:.1f}" text-anchor="end" font-size="9" fill="#7A7870">'
                   f'{label}</text>')
    svg.append(f'<line class="xh" x1="0" x2="0" y1="{T}" y2="{T + PH}" stroke="#8E8D83" stroke-width="1" '
               f'visibility="hidden"/>')
    svg.append(f'<rect x="{L}" y="{T}" width="{PW}" height="{PH}" fill="transparent"/>')
    return (f'<svg class="tr" viewBox="0 0 {W} {H}" style="width:100%;height:auto;display:block;overflow:visible" '
            f'data-tips="{html.escape(json.dumps(tips))}">{"".join(svg)}</svg>')

def panel(m):
    s, d = cseries[m], defs.get(m)
    pct  = "_share" in m
    cid  = d["check_id"] if d else None
    st   = verdict.get(cid)
    last = max((i for i in range(C) if s["v"][i] is not None), default=None)
    badge = (f'<span style="background:{BADGE[st]};color:#fff;font-size:10px;font-weight:700;'
             f'padding:2px 7px;letter-spacing:.05em;margin-right:8px">{st.upper()}</span>') if st in BADGE else ""
    sub = f"check {cid} &middot; {d['layer']}" if d else "no check &middot; context only"
    now = ""
    if last is not None:
        now = f"{fmt(s['v'][last], pct)}"
        if s["e"][last] is not None:
            now += f' <span style="color:#8E8D83">vs {fmt(s["e"][last], pct)} expected</span>'
        if cdays[last] != days[-1]:     # e.g. the board judges a Sunday, the chart ends on Friday
            now += f' <span style="color:#8E8D83">&middot; {cdays[last]:%a} {cdays[last].day} {cdays[last]:%b}</span>'
    note = ""
    if last is not None and d and s["lo"][last] is not None and not outside(s, last, d):
        v = s["v"][last]
        beyond = ((d["abs_warn_low"] is not None and v < d["abs_warn_low"]) or
                  (d["abs_warn_high"] is not None and v > d["abs_warn_high"]))
        if beyond:
            note = ('<div style="font-size:11px;color:#946F29;margin-top:4px;line-height:1.4">'
                    'Inside its recent pattern, but beyond the healthy limit. The expected value has '
                    'adapted to a lasting change, so read the dotted line, not the band.</div>')
    return (f'<div style="padding-top:8px;border-top:1px solid #ECEBE4">'
            f'<div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:2px 10px">'
            f'<div style="font-size:14px;font-weight:600;color:#000;min-width:0">{badge}{html.escape(title_of(m))}</div>'
            f'<div style="font-size:12px;color:#404040;white-space:nowrap;margin-left:auto">{now}</div></div>'
            f'<div style="font-size:11px;color:#8E8D83;margin:1px 0 4px">{sub} &middot; '
            f'<span style="font-family:ui-monospace,Menlo,monospace">{m}</span></div>'
            f'{chart(m)}{note}</div>')

def order_key(m):
    d = defs.get(m)
    return (-RANK.get(verdict.get(d["check_id"]) if d else None, -1),
            LAYER_ORDER.get(d["layer"], 9) if d else 9, d["check_id"] if d else m)

def flow_panel(title, lines):
    known = [(m, label, FLOW_INK[k]) for k, (m, label) in enumerate(lines) if m in cseries]
    last  = max((i for m, _, _ in known for i in range(C) if cseries[m]["v"][i] is not None), default=None)
    now   = " &rarr; ".join(fmt(cseries[m]["v"][last]) for m, _, _ in known) if last is not None else ""
    keys  = " ".join(f'<span style="white-space:nowrap;margin-right:8px"><span style="color:{ink}">&#9632;</span> '
                     f'{label}</span>' for _, label, ink in known)
    return (f'<div style="padding-top:8px;border-top:1px solid #ECEBE4">'
            f'<div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:2px 10px">'
            f'<div style="font-size:14px;font-weight:600;color:#000">{title}</div>'
            f'<div style="font-size:12px;color:#404040;white-space:nowrap;margin-left:auto">{now}</div></div>'
            f'<div style="font-size:11px;color:#5A5D5C;margin:1px 0 4px">{keys}</div>'
            f'{flow_chart(lines)}</div>')

metrics = sorted(series, key=order_key)
charted = [m for m in metrics if m not in FLOW_ONLY]          # the flow ratios have their own section
failing = [m for m in charted if defs.get(m) and verdict.get(defs[m]["check_id"]) in ("critical", "warning")]
others  = [m for m in charted if m not in failing]
flow_bad  = [m for m in FLOW_ONLY if defs.get(m) and verdict.get(defs[m]["check_id"]) in ("critical", "warning")]
flow_blind = [m for m in FLOW_ONLY if defs.get(m) and verdict.get(defs[m]["check_id"]) in ("info", None)]

# Days on which several figures left their range together: the pattern this cell exists to show.
recent = set(days[-28:])
together = {}
for m in metrics:
    for i in range(N):
        if days[i] in recent and outside(series[m], i, defs.get(m)):
            together.setdefault(days[i], []).append(title_of(m))
shared = sorted(((dd, ms) for dd, ms in together.items() if len(ms) >= 3), key=lambda t: (-len(t[1]), t[0]))[:4]
if shared:
    shared_txt = "Days on which three or more figures left their normal range together: " + "; ".join(
        f"<b>{dd:%a} {dd.day} {dd:%b}</b>{' (weekend, not charted)' if dd.weekday() >= 5 else ''} "
        f"({len(ms)}: {html.escape(', '.join(ms[:4]))}"
        f"{' and more' if len(ms) > 4 else ''})" for dd, ms in shared) + "."
else:
    shared_txt = "No day in the last four weeks had three or more figures outside their normal range at once."

def swatch(svg, label):
    return (f'<span style="display:inline-flex;align-items:center;gap:6px;margin-right:18px">'
            f'<svg width="22" height="10">{svg}</svg>{label}</span>')

legend = (swatch('<line x1="0" x2="22" y1="5" y2="5" stroke="#404040" stroke-width="1.75"/>', "actual")
        + swatch('<line x1="0" x2="22" y1="5" y2="5" stroke="#5A5D5C" stroke-width="1.25" stroke-dasharray="4 3"/>',
                 "expected for this weekday")
        + swatch('<rect width="22" height="10" fill="#ECEBE4"/>', "normal range (layer ratios: allowed range)")
        + swatch('<line x1="0" x2="22" y1="5" y2="5" stroke="#946F29" stroke-dasharray="1.5 2.5"/>', "healthy limit")
        + swatch('<circle cx="11" cy="5" r="3" fill="#BD000C"/>', "outside the normal range"))

grid = lambda cells: ('<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));'
                      'gap:18px 26px">' + "".join(cells) + '</div>')

flow_html = "".join(
    f'<div style="margin-top:12px">'
    + grid([flow_panel(title, lines)] + [panel(m) for m in ratios if m in series]) + '</div>'
    for title, lines, ratios in FLOW)
# "All pass" only when every layer check was actually judged: a check that could
# not be judged is not a pass, and must not read as one.
if flow_bad:
    flow_state = (f'<b style="color:#BD000C">{len(flow_bad)} of the layer checks '
                  f'{"is" if len(flow_bad) == 1 else "are"} failing.</b>')
elif flow_blind:
    flow_state = (f'<b style="color:#7A7870">{len(flow_blind)} of the layer checks could not be judged today '
                  f'({", ".join(sorted(defs[m]["check_id"] for m in flow_blind))}).</b>')
else:
    flow_state = '<b style="color:#6F7A1A">All layer checks pass.</b>'

displayHTML(f"""
<div style="font-family:'Frutiger 45 Light',Frutiger,'Helvetica Neue',Arial,sans-serif;color:#404040;background:#fff;padding:22px 26px;max-width:1180px;position:relative">
  <div style="border-bottom:2px solid #E60000;padding-bottom:12px">
    <div style="font-size:26px;font-weight:300;color:#000">Trends</div>
    <div style="font-size:13px;color:#7A7870;margin-top:3px">
      Each business day against what that weekday normally looks like &middot;
      {cdays[0].day} {cdays[0]:%B} to {cdays[-1].day} {cdays[-1]:%B %Y}</div>
  </div>
  <div style="font-size:12.5px;color:#404040;margin:14px 0 8px;line-height:1.5">{shared_txt}</div>
  <div style="font-size:11.5px;color:#5A5D5C;margin-bottom:6px">{legend}</div>
  <div style="font-size:11px;color:#8E8D83">Hover a chart to read one day; the line follows on every chart, so
    breaks that happened together line up. Monday to Friday only, so weekend volume does not set the scale;
    weekends are still judged and named above when they leave their range. The first weeks have no expected
    value yet, because it needs four earlier days of the same weekday.</div>

  <div style="margin-top:24px;font-size:15px;font-weight:600;color:#000">Layer flow</div>
  <div style="font-size:12px;color:#7A7870;margin:3px 0 0;line-height:1.5">
    What the pipeline itself does, from bronze to silver to gold. Traffic moves every layer together, so the
    ratios on the right stay flat when the pipeline is healthy, whatever the day; any movement there is the
    pipeline, not the visitors. These checks are judged by their stated limits only, so the grey band on a ratio
    is the allowed deviation and red marks a day beyond it. {flow_state}</div>
  {flow_html}

  <div style="margin-top:30px;font-size:15px;font-weight:600;color:#000">Failing today</div>
  <div style="font-size:12px;color:#7A7870;margin:3px 0 10px">
    {len(failing)} further figure{'s' if len(failing) != 1 else ''} whose check is critical or warning, worst first,
    then bronze before gold.</div>
  {grid([panel(m) for m in failing]) if failing else '<div style="font-size:13px;color:#6F7A1A">None of the other charted checks is failing.</div>'}

  <div style="margin-top:30px;font-size:15px;font-weight:600;color:#000">Everything else</div>
  <div style="font-size:12px;color:#7A7870;margin:3px 0 10px">
    The control group. A problem that leaves these untouched is narrower than it looks.</div>
  {grid([panel(m) for m in others])}

  <div id="tr-tip" style="display:none;position:fixed;z-index:10;background:#fff;border:1px solid #CCCABC;
       padding:7px 9px;font-size:11.5px;line-height:1.45;color:#404040;pointer-events:none;max-width:260px"></div>
</div>
<script>
(function () {{
  var N = {C}, L = {L}, PW = {PW}, W = {W};
  var charts = Array.prototype.slice.call(document.querySelectorAll('svg.tr'));
  var tip = document.getElementById('tr-tip');
  function show(i) {{
    var x = L + PW * i / Math.max(N - 1, 1);
    charts.forEach(function (c) {{
      var l = c.querySelector('.xh');
      l.setAttribute('x1', x); l.setAttribute('x2', x); l.setAttribute('visibility', 'visible');
    }});
  }}
  charts.forEach(function (c) {{
    var tips = JSON.parse(c.getAttribute('data-tips'));
    c.addEventListener('mousemove', function (e) {{
      var r = c.getBoundingClientRect();
      var i = Math.round(((e.clientX - r.left) * W / r.width - L) / PW * (N - 1));
      i = Math.max(0, Math.min(N - 1, i));
      show(i);
      tip.innerHTML = tips[i];
      tip.style.display = 'block';
      var left = e.clientX + 14;
      if (left + tip.offsetWidth > window.innerWidth - 8) left = e.clientX - tip.offsetWidth - 14;
      tip.style.left = left + 'px';
      tip.style.top = (e.clientY + 14) + 'px';
    }});
    c.addEventListener('mouseleave', function () {{
      tip.style.display = 'none';
      charts.forEach(function (o) {{ o.querySelector('.xh').setAttribute('visibility', 'hidden'); }});
    }});
  }});
}})();
</script>
""")


-- ----------------------------------------------------------------------------
-- CELL 10d-a — VERIFY: no check counted twice.
--
-- Cells 10d-a to 10d-d are four SEPARATE notebook cells. Paste each on its own:
-- a cell holding several queries only displays the result of the last one.
--
-- A6, G3 and S1 moved from the explicit checks (cell 9) to the corridor engine
-- (cell 6). If an old cell 9 is still in the session they would be counted
-- twice. Must return no rows.
-- ----------------------------------------------------------------------------
%sql
SELECT check_id, COUNT(*) AS n
FROM   dq_results
GROUP  BY check_id
HAVING COUNT(*) > 1;


-- ----------------------------------------------------------------------------
-- CELL 10d-b — CALIBRATE: what the layer ratios measure, business days only.
--
-- G4 has never been measured and A6 changed its rule, so these figures are what
-- their limits in cell 6 should be set from.
-- ----------------------------------------------------------------------------
%sql
SELECT metric,
       ROUND(MIN(value), 4)                                  AS min_value,
       ROUND(percentile_approx(value, 0.5), 4)               AS median_value,
       ROUND(MAX(value), 4)                                  AS max_value,
       ROUND(percentile_approx(value, 0.5) - MIN(value), 4)  AS drop_from_median,
       COUNT(*)                                              AS business_days
FROM   dq_metric_daily
WHERE  metric IN ('keep_share_silver', 'gold_to_silver_views',
                  'silver_to_bronze_persons', 'gold_to_silver_persons')
  AND  dayofweek(view_date) BETWEEN 2 AND 6          -- Monday to Friday
  AND  view_date <= date_sub(current_date(), 1)      -- today is still partial
GROUP  BY metric
ORDER  BY metric;


-- ----------------------------------------------------------------------------
-- CELL 10d-c — HISTORY: every day a layer check warned or went critical.
--
-- Shows whether the limits stay quiet on ordinary days. 'info' days are left
-- out: they are the first weeks of the window, before a baseline exists.
-- ----------------------------------------------------------------------------
%sql
SELECT check_date, date_format(check_date, 'EEE') AS weekday, check_id,
       ROUND(metric_value, 4) AS value,
       ROUND(baseline, 4)     AS expected,
       ROUND(lower_bound, 4)  AS lo,
       ROUND(upper_bound, 4)  AS hi,
       status
FROM   dq_r_corridor
WHERE  check_id IN ('A6', 'G3', 'S1', 'G4')
  AND  status IN ('warning', 'critical')
ORDER  BY check_date DESC, check_id;


-- ----------------------------------------------------------------------------
-- CELL 10d-d — DIAGNOSE: on each day a layer check fired, which layer moved?
--
-- A ratio between two layers falls for one of two reasons, and they need
-- different people. Each column is that layer's volume against its expected
-- value for the weekday, in per cent.
--
--   bronze normal, silver low      silver lost rows: a pipeline defect
--   bronze high,   silver normal   extra traffic that silver filters by design
--                                  (drafts, unpublished pages): not a defect
--   silver normal, gold low        gold lost rows between silver and gold
--   all layers low together        less traffic, or a late bronze load
--
-- One row per flagged day, no dates written in: it answers the same question
-- for any day the checks flag in future.
-- ----------------------------------------------------------------------------
%sql
WITH flagged AS (
  SELECT check_date, concat_ws(', ', sort_array(collect_set(check_id))) AS checks
  FROM   dq_r_corridor
  WHERE  check_id IN ('A6', 'G3', 'S1', 'G4')
    AND  status IN ('warning', 'critical')
  GROUP  BY check_date
),
vs_expected AS (
  SELECT view_date, metric,
         ROUND(100 * (value - baseline) / NULLIF(baseline, 0), 1) AS pct
  FROM   dq_metric_baseline
  WHERE  metric IN ('views', 'silver_rows', 'gold_views',
                    'persons', 'silver_contacts', 'gold_unique_visitors')
)
SELECT f.check_date,
       date_format(f.check_date, 'EEE')                                AS weekday,
       f.checks,
       MAX(CASE WHEN e.metric = 'views'                THEN e.pct END) AS bronze_views_pct,
       MAX(CASE WHEN e.metric = 'silver_rows'          THEN e.pct END) AS silver_rows_pct,
       MAX(CASE WHEN e.metric = 'gold_views'           THEN e.pct END) AS gold_views_pct,
       MAX(CASE WHEN e.metric = 'persons'              THEN e.pct END) AS bronze_people_pct,
       MAX(CASE WHEN e.metric = 'silver_contacts'      THEN e.pct END) AS silver_people_pct,
       MAX(CASE WHEN e.metric = 'gold_unique_visitors' THEN e.pct END) AS gold_people_pct
FROM   flagged f
LEFT JOIN vs_expected e ON e.view_date = f.check_date
GROUP  BY f.check_date, f.checks
ORDER  BY f.check_date DESC;


-- ----------------------------------------------------------------------------
-- CELL 11 — CLEANUP. Releases the cache, drops every temporary view, and proves
-- that nothing persistent was left behind.
--
-- Detaching the notebook would do the same thing on its own, but on a shared
-- production cluster the cache holds memory until then, and "it will sort itself
-- out eventually" is a poor thing to rely on. Run this when you are done.
--
-- Safe to run at any point, including after a failed cell or twice in a row.
-- Every step is individually guarded, so one missing object does not stop the
-- rest, and dropping something that is not there is not an error.
-- ----------------------------------------------------------------------------
%python
# Reverse creation order, so dependents go before the views they read.
VIEWS = [
    "dq_results",
    "dq_known_causes", "dq_scope", "dq_onset",
    "dq_r_explicit", "dq_r_corridor",
    "dq_check_affects", "dq_check_def",
    "dq_metric_baseline", "dq_metric_daily",
    "dq_sv_daily", "dq_gold_daily", "dq_pv_daily",
    "dq_pv_window",
]
CACHED = ["dq_results", "dq_metric_daily", "dq_pv_window"]   # all need releasing, in this order

# --- 1. prove that everything about to be dropped is temporary ---------------
# A temporary view exists only in this session. If anything below reports
# PERSISTENT, stop: this notebook was never meant to create such a thing, and it
# would need removing deliberately rather than by a cleanup cell.
existing = {t.name: t.isTemporary for t in spark.catalog.listTables() if t.name in VIEWS}
persistent = [n for n, is_temp in existing.items() if not is_temp]
if persistent:
    print("STOP — these are NOT temporary and will not be touched:", persistent)
else:
    print(f"all {len(existing)} object(s) found are temporary, safe to drop")

# --- 2. release the cache ----------------------------------------------------
for t in CACHED:
    try:
        if spark.catalog.isCached(t):
            spark.catalog.uncacheTable(t); print(f"uncached  {t}")
        else:
            print(f"uncached  {t} (was not cached)")
    except Exception as e:
        print(f"uncached  {t} — skipped ({type(e).__name__})")

# --- 3. drop the views -------------------------------------------------------
dropped, failed = [], []
for v in VIEWS:
    if v in persistent:
        continue
    try:
        spark.sql(f"DROP VIEW IF EXISTS {v}")
        dropped.append(v)
    except Exception as e:
        failed.append((v, f"{type(e).__name__}: {str(e)[:80]}"))
print(f"dropped   {len(dropped)} view(s): {', '.join(dropped) if dropped else 'none'}")
for v, err in failed:
    print(f"FAILED    {v} -> {err}")

# --- 4. prove the session is clean -------------------------------------------
left = sorted(t.name for t in spark.catalog.listTables() if t.name.startswith("dq_"))
still_cached = []
for v in VIEWS:
    try:
        if spark.catalog.isCached(v): still_cached.append(v)
    except Exception:
        pass   # isCached raises once the view is gone, which is the outcome we want

print()
if not left and not still_cached and not failed and not persistent:
    print("CLEAN — no dq_ objects remain in the session and nothing is cached.")
    print("Nothing was written to storage or the metastore at any point.")
else:
    print("NOT CLEAN:")
    if left:         print("  views remaining :", left)
    if still_cached: print("  still cached    :", still_cached)
    if persistent:   print("  persistent      :", persistent, "<- investigate, this notebook does not create these")


-- ============================================================================
-- RUNNING IT FROM A SINGLE CELL
--
-- Paste the statements above into a Python cell as a list and loop, if one cell
-- is preferable to eleven:
--
--   stmts = [ ... each SQL string from cells 1 to 9e, in order, no %sql magic ... ]
--   for s in stmts:
--       spark.sql(s)
--   display(spark.sql("SELECT * FROM dq_results ORDER BY status, check_id"))
--   # then cell 10b for the board, and cell 11 to clean up. Both are already
--   # Python and can be pasted as they are.
--
-- WHAT TO DO WITH THE OUTPUT
--   Export the grid to CSV from the result toolbar if you want a record. Nothing
--   is retained otherwise, which is the point: on PROD this run leaves no trace.
--
-- WHEN THIS BECOMES THE PERSISTENT VERSION
--   dq_checks_draft.sql is the same logic writing into a `dq` schema, which is
--   what makes trending, alerting and the hands-off Health Overview possible.
--   It also removes the one real gap in this edition: onset detection here covers
--   only the corridor-driven checks, because those are the ones evaluated for
--   every day in the window. With a stored result per day and per check, every
--   check gets a start date and the automatic grouping covers all of them.
--   It belongs in Dev first, then pre-prod, then PROD — not straight here.
--   Differences to expect when promoting:
--     · CREATE OR REPLACE TEMPORARY VIEW  ->  CREATE OR REPLACE TABLE
--     · the final SELECT                  ->  INSERT INTO dq.dq_check_result
--     · dq_check_def / dq_check_affects inline VALUES -> stored tables
--     · one day judged per run            ->  the same, but history accumulates
-- ============================================================================
