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
--   · one CACHE TABLE on the 70-day slice, so twenty checks do not each re-scan
--     173M rows. Caching materialises into cluster memory and spilled local
--     disk, never into the lakehouse. The last cell releases it.
--
-- HOW TO RUN
--   Cell 0 first: it checks every column against the live schema and costs
--   nothing. Then cells 1 to 9 in order, cell 10 for the result, cell 11 to
--   clean up. Cell 11 is safe to run at any point, including after a failure.
--   Each cell is one statement. To run the lot from a single cell instead, see
--   the note at the foot of the file.
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
-- ----------------------------------------------------------------------------
%sql
CREATE OR REPLACE TEMPORARY VIEW dq_check_def AS
SELECT * FROM VALUES
  ('A1','completeness','bronze','views',                 CAST(NULL AS DOUBLE),CAST(NULL AS DOUBLE),CAST(NULL AS DOUBLE),CAST(NULL AS DOUBLE),0.25,0.50,CAST(NULL AS DOUBLE),CAST(NULL AS DOUBLE),'daily page views vs same-weekday median'),
  ('A4','completeness','staging','sampling_factor',      NULL,NULL,1.001,1.001,NULL,NULL,NULL,NULL,'sum(itemCount)/count(*) must be 1.00'),
  ('B1','identity','bronze','views_per_session',         1.30,1.50,NULL,NULL,NULL,NULL,0.10,0.25,'healthy 1.94-2.35 weekday, broken 1.06'),
  ('B2','identity','bronze','sessions_per_browser_7d',   1.02,1.10,NULL,NULL,NULL,NULL,0.15,0.30,'visits per browser identity, rolling week'),
  ('B4','identity','bronze','browser_ids_per_person',    NULL,NULL,1.5,2.5,NULL,NULL,0.15,0.30,'per DAY; healthy 1.06-1.15, broken 3.7-4.15'),
  ('B5','identity','bronze','single_view_session_share', NULL,NULL,0.80,0.90,NULL,NULL,0.10,0.20,'healthy 0.54-0.76, broken 0.93-0.95'),
  ('B8','identity','bronze','identified_share',          0.90,0.97,NULL,NULL,NULL,NULL,0.05,0.15,'measured 1.000, every row carries a GPN'),
  ('C8','schema','bronze','double_fire_share',           NULL,NULL,0.10,0.25,NULL,NULL,0.05,NULL,'same person, same page, under 1 s apart'),
  ('D1a','plausibility','gold','gold_views',             NULL,NULL,NULL,NULL,0.25,0.50,NULL,NULL,'gold views per day'),
  ('D1b','plausibility','gold','gold_visits',            NULL,NULL,NULL,NULL,0.25,0.50,0.25,0.50,'gold visits per day'),
  ('D1c','plausibility','gold','gold_unique_visitors',   NULL,NULL,NULL,NULL,0.25,0.50,0.25,0.50,'gold distinct contacts per day'),
  ('D1d','plausibility','gold','gold_views_per_visit',   1.02,1.05,NULL,NULL,NULL,NULL,0.10,0.25,'the ratio the business sees'),
  ('D4','plausibility','gold','tracked_share',           NULL,NULL,NULL,NULL,NULL,NULL,0.05,NULL,'views with a tracking id'),
  ('D7','plausibility','gold','clicks_per_view',         NULL,NULL,NULL,NULL,NULL,NULL,0.25,NULL,'customEvents clicks per page view')
AS t(check_id, family, layer, metric,
     abs_block_low, abs_warn_low, abs_warn_high, abs_block_high,
     rel_warn_pct, rel_block_pct, step_warn_pct, step_block_pct, note);


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
  ('G3','page_views'),('G3','unique_visitors')
AS t(check_id, figure);


-- ----------------------------------------------------------------------------
-- CELL 8 — the corridor engine
-- ----------------------------------------------------------------------------
%sql
CREATE OR REPLACE TEMPORARY VIEW dq_r_corridor AS
SELECT
  b.view_date AS check_date, d.check_id, d.family, d.layer,
  b.value AS metric_value, b.baseline,
  COALESCE(d.abs_warn_low,  b.corr_low)  AS lower_bound,
  COALESCE(d.abs_warn_high, b.corr_high) AS upper_bound,
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
    WHEN b.value < b.corr_low OR b.value > b.corr_high                                  THEN 'warning'
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
-- A6 layer retention, bronze -> silver.
--
-- Bronze holds MORE rows than silver by design: unpublished pages, drafts and
-- similar are filtered out on the way in (confirmed 2026-09-11). So the gap
-- itself is not a defect and comparing the counts for equality was wrong. What
-- matters is whether the share that survives suddenly changes: a filter that
-- starts dropping far more, or far less, than it did last month is the signal.
-- Silver against gold is checked separately and strictly, by G3.
a6b AS (SELECT COUNT(*) AS n FROM dq_pv_window w JOIN p ON w.view_date = p.d),
a6s AS (SELECT COALESCE(MAX(sv_rows), 0) AS n FROM dq_sv_daily v JOIN p ON v.view_date = p.d),
a6g AS (SELECT COALESCE(MAX(gold_views), 0) AS n FROM dq_gold_daily g JOIN p ON g.view_date = p.d),
a6hist AS (                      -- the retention share over the prior 28 days
  SELECT percentile_approx(sv.sv_rows / NULLIF(pv.views, 0), 0.5) AS med_keep
  FROM   dq_pv_daily pv JOIN dq_sv_daily sv ON sv.view_date = pv.view_date
  CROSS  JOIN p
  WHERE  pv.view_date BETWEEN date_sub(p.d, 28) AND date_sub(p.d, 1)
),
a6 AS (
  SELECT p.d, 'A6', 'completeness', 'all layers',
         a6s.n / NULLIF(a6b.n, 0), h.med_keep,
         h.med_keep - 0.05, h.med_keep + 0.05,
         CASE WHEN h.med_keep IS NULL THEN 'info'
              WHEN ABS(a6s.n / NULLIF(a6b.n,0) - h.med_keep) > 0.10 THEN 'critical'
              WHEN ABS(a6s.n / NULLIF(a6b.n,0) - h.med_keep) > 0.05 THEN 'warning' ELSE 'ok' END,
         CONCAT('bronze ', a6b.n, ' -> silver ', a6s.n, ' -> gold ', a6g.n,
                ' | kept ', ROUND(100 * a6s.n / NULLIF(a6b.n,0), 1),
                ' % vs 28-day median ', ROUND(100 * h.med_keep, 1),
                ' % — unpublished pages and drafts are filtered out by design')
  FROM p CROSS JOIN a6b CROSS JOIN a6s CROSS JOIN a6g CROSS JOIN a6hist h
),
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
-- S1 person resolution, bronze employees vs silver contacts
s1b AS (SELECT COUNT(DISTINCT gpn) AS n FROM dq_pv_window w JOIN p ON w.view_date = p.d),
s1v AS (SELECT COALESCE(MAX(sv_contacts), 0) AS n FROM dq_sv_daily v JOIN p ON v.view_date = p.d),
s1 AS (
  SELECT p.d, 'S1', 'identity', 'silver',
         s1v.n / NULLIF(s1b.n, 0), 1.0, 0.90, 1.10,
         CASE WHEN s1v.n / NULLIF(s1b.n,0) NOT BETWEEN 0.75 AND 1.35 THEN 'critical'
              WHEN s1v.n / NULLIF(s1b.n,0) NOT BETWEEN 0.90 AND 1.10 THEN 'warning' ELSE 'ok' END,
         CONCAT('bronze distinct GPN ', s1b.n, ' vs silver distinct contactId ', s1v.n)
  FROM p CROSS JOIN s1b CROSS JOIN s1v
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
),
-- G3 silver to gold aggregation tie-out
g3 AS (
  SELECT p.d, 'G3', 'completeness', 'gold',
         a6g.n / NULLIF(a6s.n, 0), 1.0, 0.99, 1.01,
         CASE WHEN a6g.n / NULLIF(a6s.n,0) NOT BETWEEN 0.95 AND 1.05 THEN 'critical'
              WHEN a6g.n / NULLIF(a6s.n,0) NOT BETWEEN 0.99 AND 1.01 THEN 'warning' ELSE 'ok' END,
         CONCAT('gold views ', a6g.n, ' vs silver rows ', a6s.n)
  FROM p CROSS JOIN a6s CROSS JOIN a6g
)
SELECT * FROM a2 UNION ALL SELECT * FROM a5 UNION ALL SELECT * FROM a6
UNION ALL SELECT * FROM b3 UNION ALL SELECT * FROM b6 UNION ALL SELECT * FROM b7
UNION ALL SELECT * FROM c3 UNION ALL SELECT * FROM c4 UNION ALL SELECT * FROM c5
UNION ALL SELECT * FROM c6 UNION ALL SELECT * FROM s1 UNION ALL SELECT * FROM s2
UNION ALL SELECT * FROM g1 UNION ALL SELECT * FROM g2 UNION ALL SELECT * FROM g3;


-- ----------------------------------------------------------------------------
-- CELL 10 — THE RESULT. One grid, worst first, with the figures each finding
-- puts at risk. This is the Health Overview, computed rather than stored.
-- ----------------------------------------------------------------------------
%sql
WITH all_r AS (
  SELECT check_date, check_id, family, layer, metric_value, baseline,
         lower_bound, upper_bound, status, note
  FROM   dq_r_corridor WHERE check_date = date_sub(current_date(), 1)
  UNION ALL
  SELECT * FROM dq_r_explicit
)
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
  r.note
FROM all_r r
LEFT JOIN dq_check_affects a ON a.check_id = r.check_id
GROUP BY r.status, r.check_id, r.layer, r.family, r.metric_value, r.baseline,
         r.lower_bound, r.upper_bound, r.note
ORDER BY severity, r.check_id;


-- ----------------------------------------------------------------------------
-- CELL 10b — HEALTH AT A GLANCE. Run this straight after cell 10.
--
-- Cell 10 is the detail grid, written for whoever maintains the checks. It is
-- not readable by a first- or second-line responder: "S2 critical, value 0" says
-- nothing about what is wrong or whether it matters.
--
-- This cell answers the only two questions that person actually has:
--   which published figures can I trust right now, and what is wrong?
-- It organises by FIGURE rather than by check, names everything in plain words,
-- and never shows a check id in the headline. The ids stay in the detail below,
-- for when someone needs to look one up.
-- ----------------------------------------------------------------------------
%python
from datetime import date, timedelta

# Plain-language name and question for every check. Anything not listed falls
# back to its id, which is a prompt to add it here.
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
 "S1": ("People recognised",         "Does one employee become exactly one person in the model?"),
 "S2": ("Keys assigned",             "Did every row receive the keys it needs?"),
}

FIGURES = [   # order they appear on the board; the first three are what most people open the report for
 ("page_views",       "Page views"),
 ("unique_visitors",  "Unique visitors"),
 ("visits",           "Visits"),
 ("pages_per_visit",  "Pages per visit"),
 ("avg_time_on_page", "Time on page"),
 ("bounce_rate",      "Bounce rate"),
 ("tracking_coverage","Campaign tagging"),
 ("page_breakdowns",  "Page breakdowns"),
 ("clicks",           "Clicks"),
]

# Twelve red rows read as twelve problems. They are not. On the first production
# run eight of the twelve came from one known incident, and the rest from three
# separate things. A responder who sees twelve panics or stops looking; one who
# sees "four issues, one already known" acts correctly. So findings are grouped
# by cause, and an unlisted check appears on its own as something new.
CAUSE = {
 "B1":"april", "B2":"april", "B3":"april", "B4":"april", "B5":"april",
 "B6":"april", "B7":"april", "D1d":"april",
 "A5":"doublefire", "C8":"doublefire",
 "C6":"pagelookup",
 "D4":"tagging",
}
CAUSE_INFO = {
 "april": ("Known incident: the browser identity stopped persisting",
           "Since 7 April 2026 every page view arrives with a fresh browser and session "
           "identity, so a view counts as a visit. Under investigation with the supplier. "
           "Page views and unique visitors are unaffected, because visitors are counted on "
           "the employee number rather than the browser."),
 "doublefire": ("The same page load is recorded twice",
           "About one page view in eleven is the same page firing twice within a second, "
           "which overstates page views and shortens measured reading time. Separate from "
           "the April incident and long standing."),
 "pagelookup": ("Some pages are missing from the reference list",
           "Roughly one page view in eight cannot be matched to the page inventory by its "
           "address, so page-level breakdowns are incomplete. Employee lookups are fine."),
 "tagging": ("Page views carry no campaign tag",
           "Campaign attribution cannot be computed from this stream while the tag is absent."),
}

RANK = {"critical": 3, "warning": 2, "info": 1, "ok": 0}
CHECK_DAY = date.today() - timedelta(days=1)

rows = spark.sql("""
    SELECT check_id, status, note, trend FROM dq_r_corridor WHERE check_date = date_sub(current_date(), 1)
    UNION ALL SELECT check_id, status, note, 'unknown' AS trend FROM dq_r_explicit
""").collect()
affects = spark.sql("SELECT check_id, figure FROM dq_check_affects").collect()

by_check   = {r["check_id"]: (r["status"], r["note"], r["trend"]) for r in rows}
fig_checks = {}
for a in affects:
    if a["check_id"] in by_check:
        fig_checks.setdefault(a["figure"], []).append(a["check_id"])

def worst(ids):
    return max((by_check[i][0] for i in ids), key=lambda s: RANK.get(s, 0)) if ids else "ok"

# Verdict wording depends on severity AND on whether anything moved. A figure
# that has been wrong at the same level for weeks needs a different sentence from
# one that broke last night: the first is a caveat to work around, the second is
# an incident. Saying "do not rely on this" about both trains people to ignore it.
COL = {"critical": ("#BD000C", "#FBE6E7"),
       "warning":  ("#E4A911", "#FDF6E3"),
       "info":     ("#7A7870", "#ECEBE4"),
       "ok":       ("#6F7A1A", "#F1F3E7")}
VERDICT = {("critical", True):  "Broke recently",
           ("critical", False): "Known issue, long standing",
           ("warning",  True):  "Changed, worth a look",
           ("warning",  False): "Read with care",
           ("info",     True):  "Cannot be judged",
           ("info",     False): "Cannot be judged",
           ("ok",       True):  "Sound",
           ("ok",       False): "Sound"}

# ---- figure tiles -----------------------------------------------------------
tiles = ""
for key, name in FIGURES:
    ids = fig_checks.get(key, [])
    st  = worst(ids)
    bad = sorted([i for i in ids if by_check[i][0] in ("critical", "warning")],
                 key=lambda i: -RANK[by_check[i][0]])
    moving = [i for i in bad if by_check[i][2] == "moving"]
    fg, bg = COL[st]
    verdict = VERDICT[(st, bool(moving))]
    # A driver whose plain name is the figure's own name adds nothing; drop it.
    names = [LABELS.get(i, (i, ""))[0] for i in bad]
    names = [n for n in names if n.lower() != name.lower()]
    if st == "info":
        detail = "no measurement available today"
    elif not bad:
        detail = "nothing wrong with it"
    else:
        detail = ("; ".join(n.lower() for n in names[:3]) or "see the detail below")
        if len(names) > 3:
            detail += f" and {len(names)-3} more"
    if bad and moving:
        detail += f'<div style="margin-top:5px;font-size:11px;color:{fg};font-weight:600">changed in the last week</div>'
    elif bad:
        detail += '<div style="margin-top:5px;font-size:11px;color:#7A7870">unchanged for weeks, already known</div>'
    tiles += (f'<div style="background:{bg};border-top:3px solid {fg};padding:12px 14px;">'
              f'<div style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:{fg};font-weight:700">{verdict}</div>'
              f'<div style="font-size:16px;font-weight:600;color:#000;margin:3px 0 5px">{name}</div>'
              f'<div style="font-size:12px;color:#5A5D5C;line-height:1.4">{detail}</div></div>')

# ---- what needs attention ---------------------------------------------------
attention = sorted([c for c in by_check if by_check[c][0] in ("critical", "warning")],
                   key=lambda c: (-RANK[by_check[c][0]], c))
# group the findings by cause, worst cause first
groups = {}
for c in attention:
    groups.setdefault(CAUSE.get(c, f"other:{c}"), []).append(c)
order = sorted(groups, key=lambda g: (-max(RANK[by_check[c][0]] for c in groups[g]), -len(groups[g])))

items = ""
for g in order:
    members = sorted(groups[g], key=lambda c: (-RANK[by_check[c][0]], c))
    g_st  = max((by_check[c][0] for c in members), key=lambda s: RANK[s])
    g_mv  = any(by_check[c][2] == "moving" for c in members)
    gfg, gbg = COL[g_st]
    if g in CAUSE_INFO:
        g_title, g_text = CAUSE_INFO[g]
    else:
        only = members[0]
        g_title, g_text = LABELS.get(only, (only, ""))[0], LABELS.get(only, ("", ""))[1]
    g_figs = sorted({dict(FIGURES).get(a["figure"], a["figure"])
                     for a in affects if a["check_id"] in members})
    items += (f'<tr><td colspan="3" style="padding:16px 0 6px">'
              f'<div style="border-left:4px solid {gfg};background:{gbg};padding:11px 14px">'
              f'<div style="font-size:15px;font-weight:600;color:#000">{g_title}'
              f'{"" if not g_mv else f" <span style=\'background:{gfg};color:#fff;font-size:10px;font-weight:700;padding:2px 6px\'>MOVED THIS WEEK</span>"}</div>'
              f'<div style="font-size:12.5px;color:#5A5D5C;margin-top:4px;max-width:88ch">{g_text}</div>'
              f'<div style="font-size:11px;color:#7A7870;margin-top:6px">'
              f'{len(members)} check{"s" if len(members)!=1 else ""} &middot; affects {", ".join(g_figs) if g_figs else "nothing published"}</div>'
              f'</div></td></tr>')
    for c in members:
        st, note, trend = by_check[c]
        chip = ('<span style="background:#FBE6E7;color:#BD000C;font-size:10px;font-weight:700;padding:2px 6px;margin-left:6px">CHANGED RECENTLY</span>'
                if trend == "moving" else
                '<span style="background:#ECEBE4;color:#7A7870;font-size:10px;padding:2px 6px;margin-left:6px">ongoing</span>'
                if trend == "stable" else
                '<span style="background:#fff;border:1px solid #CCCABC;color:#8E8D83;font-size:10px;padding:1px 6px;margin-left:6px">trend not tracked</span>')
        fg, bg = COL[st]
        title, question = LABELS.get(c, (c, ""))
        figs = ", ".join(dict(FIGURES).get(f, f) for f in sorted({a["figure"] for a in affects if a["check_id"] == c})) or "—"
        items += (f'<tr><td style="padding:9px 12px 9px 24px;border-bottom:1px solid #ECEBE4;white-space:nowrap;vertical-align:top">'
                  f'<span style="background:{fg};color:#fff;font-size:10px;font-weight:700;padding:2px 7px;letter-spacing:.05em">{st.upper()}</span></td>'
                  f'<td style="padding:9px 12px 9px 0;border-bottom:1px solid #ECEBE4;vertical-align:top">'
                  f'<div style="font-size:14px;font-weight:600;color:#000">{title}{chip}</div>'
                  f'<div style="font-size:12px;color:#7A7870;margin-top:2px">{question}</div>'
                  f'<div style="font-size:11px;color:#8E8D83;margin-top:4px;font-family:ui-monospace,Menlo,monospace">{note}</div></td>'
                  f'<td style="padding:9px 0;border-bottom:1px solid #ECEBE4;font-size:12px;color:#5A5D5C;vertical-align:top;white-space:nowrap">{figs}'
                  f'<div style="font-size:10px;color:#B8B3A2;margin-top:3px">check {c}</div></td></tr>')

n_crit  = sum(1 for c in by_check.values() if c[0] == "critical")
n_warn  = sum(1 for c in by_check.values() if c[0] == "warning")
n_ok    = sum(1 for c in by_check.values() if c[0] == "ok")
n_moving = sum(1 for c in by_check.values() if c[0] in ("critical","warning") and c[2] == "moving")
sound  = [n for k, n in FIGURES if worst(fig_checks.get(k, [])) == "ok"]
unknown = [n for k, n in FIGURES if worst(fig_checks.get(k, [])) == "info"]

if n_moving: hfg, hbg, headline = "#BD000C", "#FBE6E7", f"{n_moving} changed recently"
elif n_crit: hfg, hbg, headline = "#BD000C", "#FBE6E7", f"{n_crit} serious, none new today"
elif n_warn: hfg, hbg, headline = "#E4A911", "#FDF6E3", f"{n_warn} thing{'s' if n_warn != 1 else ''} to look at"
else:        hfg, hbg, headline = "#6F7A1A", "#F1F3E7", "All clear"

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

  <div style="margin-top:28px;font-size:15px;font-weight:600;color:#000">What needs attention</div>
  <div style="font-size:12px;color:#7A7870;margin:3px 0 10px">
    Grouped by cause, worst first, because several checks usually point at one problem.
    <b>Changed recently</b> is what needs acting on today; <b>ongoing</b> has been at this level for weeks
    and is already known. The grey line is the raw measurement, for whoever picks it up next.
  </div>
  {'<table style="width:100%;border-collapse:collapse">' + items + '</table>' if items
   else '<div style="background:#F1F3E7;border-left:3px solid #6F7A1A;padding:12px 14px;font-size:13px">Nothing is failing. All ' + str(n_ok) + ' checks passed.</div>'}

  <div style="margin-top:22px;padding-top:10px;border-top:1px solid #ECEBE4;font-size:11px;color:#8E8D83">
    {n_crit} serious &middot; {n_warn} to look at &middot; {n_ok} passed &middot; {n_moving} changed recently,
    out of {len(by_check)} checks across bronze, silver and gold, tracing back to {len(order)} distinct cause{'s' if len(order) != 1 else ''}.
    Nothing was written: this notebook creates only temporary views. Full detail in the grid above.
  </div>
</div>
""")


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
    "dq_r_explicit", "dq_r_corridor",
    "dq_check_affects", "dq_check_def",
    "dq_metric_baseline", "dq_metric_daily",
    "dq_sv_daily", "dq_gold_daily", "dq_pv_daily",
    "dq_pv_window",
]

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
try:
    if spark.catalog.isCached("dq_pv_window"):
        spark.catalog.uncacheTable("dq_pv_window")
        print("uncached  dq_pv_window")
    else:
        print("uncached  dq_pv_window (was not cached)")
except Exception as e:
    print(f"uncached  dq_pv_window — skipped ({type(e).__name__})")

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
try:
    still_cached = [v for v in VIEWS if spark.catalog.isCached(v)]
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
--   stmts = [ ... each SQL string, in order, without the %sql magic ... ]
--   for s in stmts[:-1]:
--       spark.sql(s)
--   display(spark.sql(stmts[-1]))
--   # then run cell 11 as-is; it is already Python and cleans up on its own
--
-- WHAT TO DO WITH THE OUTPUT
--   Export the grid to CSV from the result toolbar if you want a record. Nothing
--   is retained otherwise, which is the point: on PROD this run leaves no trace.
--
-- WHEN THIS BECOMES THE PERSISTENT VERSION
--   dq_checks_draft.sql is the same logic writing into a `dq` schema, which is
--   what makes trending, alerting and the hands-off Health Overview possible.
--   It belongs in Dev first, then pre-prod, then PROD — not straight here.
--   Differences to expect when promoting:
--     · CREATE OR REPLACE TEMPORARY VIEW  ->  CREATE OR REPLACE TABLE
--     · the final SELECT                  ->  INSERT INTO dq.dq_check_result
--     · dq_check_def / dq_check_affects inline VALUES -> stored tables
--     · one day judged per run            ->  the same, but history accumulates
-- ============================================================================
