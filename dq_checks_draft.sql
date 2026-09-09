-- ============================================================================
-- Data Integrity Checks — DRAFT for Databricks SQL / Spark SQL
-- Companion to docs/data-integrity-checks.md (catalogue A1–D8).
--
-- Status 2026-09-09: written from the documented table inventory, NOT yet
-- executed against the workspace. Columns tagged [VERIFY] are presumed from the
-- App Insights envelope and the 57-column bronze schema; BLOCK 0 confirms them.
--
-- Table map used here (see docs/data-integrity-checks.md §9):
--   sharepoint_bronze.pageviews               173M  App Insights pageViews landing
--   sharepoint_bronze.customevents            262M  App Insights customEvents landing
--   sharepoint_bronze.pages                    48K  page inventory (pageUUID, UBSGICTrackingID)
--   sharepoint_silver.pageviewed / pagevisited       refined view / visit facts
--   sharepoint_gold.pbi_db_interactions_metrics 84M  page × date × contact fact (views, visits)
--   sharepoint_gold.pbi_db_employeecontact      24M  contact dimension (contactId = unique visitor)
--   imep_bronze.tbl_hr_employee               265K  HR master (WORKER_ID = GPN, T_NUMBER)
--
-- Conventions: copy a block (between two `-- ---` separators) into a notebook
-- cell. Each block is self-contained. Results land in dq.dq_check_result.
-- Patch the schema name `dq` and the catalog (USE CATALOG ...) before running.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- BLOCK 0 — Probes: confirm the [VERIFY] columns before anything else
-- ----------------------------------------------------------------------------
DESCRIBE TABLE sharepoint_bronze.pageviews;
DESCRIBE TABLE sharepoint_bronze.customevents;
DESCRIBE TABLE sharepoint_silver.pageviewed;
DESCRIBE TABLE sharepoint_silver.pagevisited;
DESCRIBE TABLE sharepoint_gold.pbi_db_employeecontact;

-- Which of the expected envelope columns exist on bronze pageviews?
SELECT column_name, data_type
FROM   information_schema.columns
WHERE  table_schema = 'sharepoint_bronze' AND table_name = 'pageviews'
  AND  lower(column_name) IN ('id','viewtime','timestamp','session_id','user_id','user_gpn',
                              'email','pageid','gictrackingid','sdkversion','itemcount','ikey',
                              'appid','operation_id','client_browser','client_os','client_type',
                              'customdimensions','_ingestion_ts','ingestion_ts','load_ts')
ORDER BY column_name;

-- Does the contact dimension carry GPN / e-mail / T-number? (the unique-visitor bridge)
SELECT column_name, data_type
FROM   information_schema.columns
WHERE  table_schema = 'sharepoint_gold' AND table_name = 'pbi_db_employeecontact';


-- ----------------------------------------------------------------------------
-- BLOCK 1 — Result table + check catalogue
-- ----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS dq;

CREATE TABLE IF NOT EXISTS dq.dq_check_result (
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
) USING DELTA;

-- Corridor checks are data-driven: one row per check that reads dq.metric_baseline.
-- abs_* bounds are absolute thresholds on the metric; rel_* are relative deviations
-- from the same-weekday 8-week median; step_* compares the 7-day mean with the
-- prior 28-day mean. NULL = not applicable.
CREATE OR REPLACE TABLE dq.check_def (
  check_id STRING, family STRING, layer STRING, metric STRING,
  abs_block_low DOUBLE, abs_warn_low DOUBLE, abs_warn_high DOUBLE, abs_block_high DOUBLE,
  rel_warn_pct DOUBLE, rel_block_pct DOUBLE, step_warn_pct DOUBLE, step_block_pct DOUBLE,
  note STRING
) USING DELTA;

INSERT INTO dq.check_def VALUES
  ('A1','completeness','bronze','views',                 NULL, NULL, NULL, NULL, 0.25, 0.50, NULL, NULL, 'daily page views vs same-weekday 8-week median'),
  ('A4','completeness','staging','sampling_factor',      NULL, NULL, 1.001, 1.001, NULL, NULL, NULL, NULL, 'sum(itemCount)/count(*) must be 1.00'),
  ('B1','identity','silver','views_per_session',         1.02, 1.05, NULL, NULL, NULL, NULL, 0.10, 0.25, 'page views per visit; healthy 1.1-1.2'),
  ('B2','identity','silver','sessions_per_browser_7d',   1.02, 1.10, NULL, NULL, NULL, NULL, 0.15, 0.30, 'visits per browser identity over a rolling week'),
  ('B4','identity','silver','browser_ids_per_person',    NULL, NULL, 2.0, 3.0, NULL, NULL, 0.15, 0.30, 'distinct user_Id per GPN and day, healthy ~1.0-1.3'),
  ('B5','identity','silver','single_view_session_share', NULL, NULL, 0.95, 0.98, NULL, NULL, 0.10, 0.20, 'share of sessions with exactly one view'),
  ('B8','identity','bronze','identified_share',          0.80, 0.90, NULL, NULL, NULL, NULL, 0.05, 0.15, 'views with a valid GPN / all views'),
  ('C8','schema','silver','double_fire_share',           NULL, NULL, 0.10, 0.25, NULL, NULL, 0.05, NULL, 'same person, same page, < 1 s apart'),
  ('D1a','plausibility','gold','gold_views',             NULL, NULL, NULL, NULL, 0.25, 0.50, NULL, NULL, 'gold views per day corridor'),
  ('D1b','plausibility','gold','gold_visits',            NULL, NULL, NULL, NULL, 0.25, 0.50, 0.25, 0.50, 'gold visits per day corridor + step change'),
  ('D1c','plausibility','gold','gold_unique_visitors',   NULL, NULL, NULL, NULL, 0.25, 0.50, 0.25, 0.50, 'gold distinct contacts per day'),
  ('D1d','plausibility','gold','gold_views_per_visit',   1.02, 1.05, NULL, NULL, NULL, NULL, 0.10, 0.25, 'gold views / gold visits — the KPI the business sees'),
  ('D4','plausibility','gold','tracked_share',           NULL, NULL, NULL, NULL, NULL, NULL, 0.05, NULL, 'views with tracking id / all views'),
  ('D7','plausibility','gold','clicks_per_view',         NULL, NULL, NULL, NULL, NULL, NULL, 0.25, NULL, 'customEvents clicks / pageViews');


-- ----------------------------------------------------------------------------
-- BLOCK 2 — Working slice of bronze pageviews (one scan per day, 70 days)
-- 173M rows, unpartitioned: materialise the window once, every check reads it.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE dq.pv_window
PARTITIONED BY (view_date) AS
SELECT
  Id                                   AS view_id,
  ViewTime                             AS view_ts,        -- [VERIFY] UTC or CET? document it
  CAST(ViewTime AS DATE)               AS view_date,
  session_Id                           AS session_id,     -- [VERIFY]
  user_Id                              AS browser_id,     -- [VERIFY] the App Insights ai_user cookie
  CASE WHEN user_gpn RLIKE '^[0-9]{8}$' THEN user_gpn END AS gpn,
  user_gpn                             AS gpn_raw,
  pageId                               AS page_id,
  GICTrackingID                        AS tracking_id,
  sdkVersion                           AS sdk_version,    -- [VERIFY]
  COALESCE(itemCount, 1)               AS item_count,     -- [VERIFY]
  iKey                                 AS ikey,           -- [VERIFY]
  appId                                AS app_id,         -- [VERIFY]
  client_Browser                       AS client_browser, -- [VERIFY]
  client_OS                            AS client_os,      -- [VERIFY]
  client_Type                          AS client_type     -- [VERIFY]
FROM sharepoint_bronze.pageviews
WHERE ViewTime >= date_sub(current_date(), 70);


-- ----------------------------------------------------------------------------
-- BLOCK 3 — Daily metrics (wide → long) and the same-weekday baseline
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE dq.pv_daily AS
WITH sess AS (                                   -- per official session
  SELECT view_date, session_id, COUNT(*) AS n_views
  FROM   dq.pv_window
  GROUP  BY view_date, session_id
),
pairs AS (                                       -- consecutive views of the same person
  SELECT view_date, gpn, page_id, view_ts,
         LAG(view_ts) OVER (PARTITION BY gpn ORDER BY view_ts) AS prev_ts,
         LAG(page_id) OVER (PARTITION BY gpn ORDER BY view_ts) AS prev_page
  FROM   dq.pv_window
  WHERE  gpn IS NOT NULL
),
b7d AS (                                         -- rolling 7-day sessions per browser
  SELECT d.view_date,
         COUNT(DISTINCT w.session_id) / NULLIF(COUNT(DISTINCT w.browser_id), 0) AS sessions_per_browser_7d
  FROM   (SELECT DISTINCT view_date FROM dq.pv_window) d
  JOIN   dq.pv_window w ON w.view_date BETWEEN date_sub(d.view_date, 6) AND d.view_date
  GROUP  BY d.view_date
),
clicks AS (                                      -- customEvents clicks per day
  SELECT CAST(`timestamp` AS DATE) AS view_date, COUNT(*) AS clicks   -- [VERIFY] timestamp vs EventTime
  FROM   sharepoint_bronze.customevents
  WHERE  name = 'click_event' AND `timestamp` >= date_sub(current_date(), 70)
  GROUP  BY 1
)
SELECT
  w.view_date,
  COUNT(*)                                                     AS views,
  SUM(w.item_count)                                            AS views_weighted,
  SUM(w.item_count) / COUNT(*)                                 AS sampling_factor,
  COUNT(DISTINCT w.session_id)                                 AS sessions,
  COUNT(*) / NULLIF(COUNT(DISTINCT w.session_id), 0)           AS views_per_session,
  COUNT(DISTINCT w.browser_id)                                 AS browser_ids,
  COUNT(DISTINCT w.gpn)                                        AS persons,
  COUNT(DISTINCT CASE WHEN w.gpn IS NOT NULL THEN w.browser_id END)
    / NULLIF(COUNT(DISTINCT w.gpn), 0)                         AS browser_ids_per_person,
  COUNT(w.gpn) / COUNT(*)                                      AS identified_share,
  COUNT(w.tracking_id) / COUNT(*)                              AS tracked_share,
  MAX(s.single_share)                                          AS single_view_session_share,
  MAX(p.double_share)                                          AS double_fire_share,
  MAX(b.sessions_per_browser_7d)                               AS sessions_per_browser_7d,
  MAX(c.clicks) / COUNT(*)                                     AS clicks_per_view
FROM dq.pv_window w
LEFT JOIN (SELECT view_date, AVG(CASE WHEN n_views = 1 THEN 1.0 ELSE 0.0 END) AS single_share
           FROM sess GROUP BY view_date) s ON s.view_date = w.view_date
LEFT JOIN (SELECT view_date,
                  SUM(CASE WHEN page_id = prev_page AND view_ts < prev_ts + INTERVAL 1 SECOND THEN 1 ELSE 0 END) / COUNT(*) AS double_share
           FROM pairs GROUP BY view_date) p ON p.view_date = w.view_date
LEFT JOIN b7d b ON b.view_date = w.view_date
LEFT JOIN clicks c ON c.view_date = w.view_date
GROUP BY w.view_date;

-- Gold KPIs per day, same shape (visitdatekey is 'YYYYMMDD')
CREATE OR REPLACE TABLE dq.gold_daily AS
SELECT to_date(visitdatekey, 'yyyyMMdd')      AS view_date,
       SUM(views)                             AS gold_views,
       SUM(visits)                            AS gold_visits,
       COUNT(DISTINCT viewingcontactid)       AS gold_unique_visitors,
       SUM(views) / NULLIF(SUM(visits), 0)    AS gold_views_per_visit
FROM   sharepoint_gold.pbi_db_interactions_metrics
WHERE  visitdatekey >= date_format(date_sub(current_date(), 70), 'yyyyMMdd')
GROUP  BY 1;

-- Long format: one row per day × metric
CREATE OR REPLACE TABLE dq.metric_daily AS
SELECT view_date, metric, CAST(value AS DOUBLE) AS value FROM (
  SELECT view_date,
         stack(13,   -- every value cast to DOUBLE: stack() needs one type per position
           'views', CAST(views AS DOUBLE), 'sampling_factor', CAST(sampling_factor AS DOUBLE),
           'views_per_session', CAST(views_per_session AS DOUBLE), 'sessions_per_browser_7d', CAST(sessions_per_browser_7d AS DOUBLE),
           'browser_ids_per_person', CAST(browser_ids_per_person AS DOUBLE), 'single_view_session_share', CAST(single_view_session_share AS DOUBLE),
           'identified_share', CAST(identified_share AS DOUBLE), 'tracked_share', CAST(tracked_share AS DOUBLE),
           'double_fire_share', CAST(double_fire_share AS DOUBLE), 'clicks_per_view', CAST(clicks_per_view AS DOUBLE),
           'sessions', CAST(sessions AS DOUBLE), 'browser_ids', CAST(browser_ids AS DOUBLE), 'persons', CAST(persons AS DOUBLE)
         ) AS (metric, value)
  FROM dq.pv_daily
  UNION ALL
  SELECT view_date,
         stack(4, 'gold_views', CAST(gold_views AS DOUBLE), 'gold_visits', CAST(gold_visits AS DOUBLE),
                  'gold_unique_visitors', CAST(gold_unique_visitors AS DOUBLE), 'gold_views_per_visit', CAST(gold_views_per_visit AS DOUBLE))
         AS (metric, value)
  FROM dq.gold_daily
);

-- Baseline: trailing 8 weeks, same weekday, median and MAD; plus 7d vs prior 28d means
CREATE OR REPLACE TABLE dq.metric_baseline AS
WITH hist AS (
  SELECT cur.view_date, cur.metric, cur.value,
         h.value AS hist_value
  FROM   dq.metric_daily cur
  JOIN   dq.metric_daily h
         ON  h.metric = cur.metric
         AND dayofweek(h.view_date) = dayofweek(cur.view_date)
         AND h.view_date BETWEEN date_sub(cur.view_date, 56) AND date_sub(cur.view_date, 7)
),
med AS (
  SELECT view_date, metric, value,
         COUNT(*)                          AS n_hist,
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
  FROM   dq.metric_daily cur
  JOIN   dq.metric_daily h ON h.metric = cur.metric AND h.view_date BETWEEN date_sub(cur.view_date, 34) AND cur.view_date
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
-- BLOCK 4 — Generic corridor evaluation (A1, A4, B1, B2, B4, B5, B8, C8, D1, D4, D7)
-- Run for check_date = yesterday. No alert before 4 same-weekday points exist.
-- ----------------------------------------------------------------------------
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date)
SELECT
  b.view_date                                    AS check_date,
  d.check_id, d.family, d.layer,
  b.value                                        AS metric_value,
  b.baseline,
  COALESCE(d.abs_warn_low,  b.corr_low)          AS lower_bound,
  COALESCE(d.abs_warn_high, b.corr_high)         AS upper_bound,
  CASE
    WHEN b.n_hist < 4                                                                   THEN 'info'
    WHEN d.abs_block_low  IS NOT NULL AND b.value <= d.abs_block_low                    THEN 'blocker'
    WHEN d.abs_block_high IS NOT NULL AND b.value >= d.abs_block_high                   THEN 'blocker'
    WHEN d.rel_block_pct  IS NOT NULL AND ABS(b.value - b.baseline) / NULLIF(b.baseline,0) > d.rel_block_pct THEN 'blocker'
    WHEN d.step_block_pct IS NOT NULL AND ABS(b.step_pct) > d.step_block_pct            THEN 'blocker'
    WHEN d.abs_warn_low   IS NOT NULL AND b.value <  d.abs_warn_low                     THEN 'warning'
    WHEN d.abs_warn_high  IS NOT NULL AND b.value >  d.abs_warn_high                    THEN 'warning'
    WHEN d.rel_warn_pct   IS NOT NULL AND ABS(b.value - b.baseline) / NULLIF(b.baseline,0) > d.rel_warn_pct  THEN 'warning'
    WHEN d.step_warn_pct  IS NOT NULL AND ABS(b.step_pct) > d.step_warn_pct             THEN 'warning'
    WHEN b.value < b.corr_low OR b.value > b.corr_high                                  THEN 'warning'
    ELSE 'ok'
  END                                            AS status,
  CONCAT(d.note, ' | 7d mean ', ROUND(b.mean_7d, 3), ' vs prior 28d ', ROUND(b.mean_prior_28d, 3),
         ' (', ROUND(100 * b.step_pct, 1), ' %)') AS note,
  current_timestamp()                            AS computed_at
FROM dq.metric_baseline b
JOIN dq.check_def d ON d.metric = b.metric
JOIN params p       ON b.view_date = p.check_date;


-- ----------------------------------------------------------------------------
-- BLOCK 5 — Completeness: A2 freshness, A5 duplicates, A6 layer tie-out, A7 late arrival
-- ----------------------------------------------------------------------------
-- A2 — freshness per layer (hours since the newest event)
INSERT INTO dq.dq_check_result
SELECT date_sub(current_date(), 1), 'A2', 'completeness', layer,
       age_h, NULL, NULL, 24,
       CASE WHEN age_h > 48 THEN 'blocker' WHEN age_h > 24 THEN 'warning' ELSE 'ok' END,
       CONCAT('newest event ', CAST(max_ts AS STRING)), current_timestamp()
FROM (
  SELECT 'bronze' AS layer, MAX(ViewTime) AS max_ts,
         timestampdiff(HOUR, MAX(ViewTime), current_timestamp()) AS age_h
  FROM   sharepoint_bronze.pageviews WHERE ViewTime >= date_sub(current_date(), 3)
  UNION ALL
  SELECT 'gold', to_timestamp(MAX(visitdatekey), 'yyyyMMdd'),
         timestampdiff(HOUR, to_timestamp(MAX(visitdatekey), 'yyyyMMdd'), current_timestamp())
  FROM   sharepoint_gold.pbi_db_interactions_metrics
  WHERE  visitdatekey >= date_format(date_sub(current_date(), 3), 'yyyyMMdd')
  -- add silver: [VERIFY] the timestamp column on sharepoint_silver.pageviewed
);

-- A5 — duplicate rate on the composite event key (Id is NOT event-unique in exports)
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
k AS (
  SELECT date_trunc('SECOND', view_ts) AS ts_s, browser_id, session_id, page_id, COUNT(*) AS n
  FROM   dq.pv_window w JOIN params p ON w.view_date = p.check_date
  GROUP  BY 1, 2, 3, 4
)
SELECT p.check_date, 'A5', 'completeness', 'bronze',
       SUM(n - 1) / SUM(n) AS dup_share, NULL, NULL, 0.01,
       CASE WHEN SUM(n - 1) / SUM(n) > 0.05 THEN 'blocker'
            WHEN SUM(n - 1) / SUM(n) > 0.01 THEN 'warning' ELSE 'ok' END,
       CONCAT(SUM(n - 1), ' duplicate rows of ', SUM(n)), current_timestamp()
FROM k CROSS JOIN params p
GROUP BY p.check_date;

-- A6 — layer tie-out: bronze views = silver views = gold views for the same day
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
b AS (SELECT COUNT(*) AS n FROM dq.pv_window w JOIN params p ON w.view_date = p.check_date),
s AS (SELECT COUNT(*) AS n FROM sharepoint_silver.pageviewed sv JOIN params p
      ON CAST(sv.ViewTime AS DATE) = p.check_date),                          -- [VERIFY] column
g AS (SELECT SUM(views) AS n FROM sharepoint_gold.pbi_db_interactions_metrics gm JOIN params p
      ON gm.visitdatekey = date_format(p.check_date, 'yyyyMMdd'))
SELECT p.check_date, 'A6', 'completeness', 'every hop',
       g.n / NULLIF(b.n, 0) AS gold_over_bronze, 1.0, 0.999, 1.001,
       CASE WHEN b.n = s.n AND s.n = g.n THEN 'ok' ELSE 'blocker' END,
       CONCAT('bronze ', b.n, ' | silver ', s.n, ' | gold ', g.n,
              ' — document intentional filters (bots, test sites) as the tolerated difference'),
       current_timestamp()
FROM params p CROSS JOIN b CROSS JOIN s CROSS JOIN g;

-- A7 — late arrival: recount day D-3 today vs. what bronze held three days ago (Delta time travel)
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 3) AS d),
now_cnt  AS (SELECT COUNT(*) AS n FROM sharepoint_bronze.pageviews pv JOIN params p ON CAST(pv.ViewTime AS DATE) = p.d),
then_cnt AS (SELECT COUNT(*) AS n
             FROM sharepoint_bronze.pageviews TIMESTAMP AS OF date_sub(current_date(), 2) pv   -- retention is 7 days
             JOIN params p ON CAST(pv.ViewTime AS DATE) = p.d)
SELECT p.d, 'A7', 'completeness', 'bronze',
       (n.n - t.n) / NULLIF(t.n, 0) AS growth, 0, NULL, 0.02,
       CASE WHEN (n.n - t.n) / NULLIF(t.n, 0) > 0.02 THEN 'warning' ELSE 'ok' END,
       CONCAT('day ', p.d, ': ', t.n, ' rows at first load, ', n.n, ' now'), current_timestamp()
FROM params p CROSS JOIN now_cnt n CROSS JOIN then_cnt t;


-- ----------------------------------------------------------------------------
-- BLOCK 6 — Identity: B3 recurring browser ids, B6 session timeout, B7 visit reconstruction
-- ----------------------------------------------------------------------------
-- B3 — share of today's browser identities seen in the previous 28 days
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
today AS (SELECT DISTINCT browser_id FROM dq.pv_window w JOIN params p ON w.view_date = p.check_date),
prior AS (SELECT DISTINCT browser_id FROM dq.pv_window w JOIN params p
          ON w.view_date BETWEEN date_sub(p.check_date, 28) AND date_sub(p.check_date, 1)),
base AS (                                     -- same ratio 4 weeks earlier as the baseline
  SELECT COUNT(DISTINCT CASE WHEN pr.browser_id IS NOT NULL THEN t.browser_id END) / COUNT(DISTINCT t.browser_id) AS r
  FROM   (SELECT DISTINCT browser_id FROM dq.pv_window w JOIN params p ON w.view_date = date_sub(p.check_date, 28)) t
  LEFT JOIN (SELECT DISTINCT browser_id FROM dq.pv_window w JOIN params p
             ON w.view_date BETWEEN date_sub(p.check_date, 56) AND date_sub(p.check_date, 29)) pr
         ON pr.browser_id = t.browser_id
)
SELECT p.check_date, 'B3', 'identity', 'silver',
       COUNT(pr.browser_id) / COUNT(*) AS recurring_share, base.r, base.r - 0.20, NULL,
       CASE WHEN COUNT(pr.browser_id) / COUNT(*) < base.r - 0.20 THEN 'blocker' ELSE 'ok' END,
       CONCAT(COUNT(pr.browser_id), ' of ', COUNT(*), ' browser ids seen in the prior 28 days'),
       current_timestamp()
FROM today t LEFT JOIN prior pr ON pr.browser_id = t.browser_id
CROSS JOIN params p CROSS JOIN base
GROUP BY p.check_date, base.r;

-- B6 — does the session id survive short gaps? P(same session | gap < 30 min, same person)
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
pairs AS (
  SELECT w.view_date, w.session_id,
         LAG(w.session_id) OVER (PARTITION BY w.gpn ORDER BY w.view_ts) AS prev_session,
         timestampdiff(MINUTE, LAG(w.view_ts) OVER (PARTITION BY w.gpn ORDER BY w.view_ts), w.view_ts) AS gap_min
  FROM   dq.pv_window w JOIN params p ON w.view_date BETWEEN date_sub(p.check_date, 1) AND p.check_date
  WHERE  w.gpn IS NOT NULL
)
SELECT p.check_date, 'B6', 'identity', 'silver',
       AVG(CASE WHEN session_id = prev_session THEN 1.0 ELSE 0.0 END) AS p_same, NULL, 0.50, NULL,
       CASE WHEN AVG(CASE WHEN session_id = prev_session THEN 1.0 ELSE 0.0 END) < 0.50 THEN 'blocker' ELSE 'ok' END,
       CONCAT(COUNT(*), ' consecutive same-person pairs under 30 min'), current_timestamp()
FROM pairs JOIN params p ON pairs.view_date = p.check_date
WHERE gap_min IS NOT NULL AND gap_min < 30
GROUP BY p.check_date;

-- B7 — official sessions vs reconstructed visits (person + 30-min inactivity rule)
-- Same rule as scripts/flatten_appinsights.py::derive_person_visit, expressed in SQL.
CREATE OR REPLACE TABLE dq.pv_visits AS
WITH ordered AS (
  SELECT view_id, view_date, view_ts, session_id, browser_id, page_id,
         COALESCE(gpn, CONCAT('anon:', browser_id)) AS person_id,
         LAG(view_ts) OVER (PARTITION BY COALESCE(gpn, CONCAT('anon:', browser_id)) ORDER BY view_ts) AS prev_ts
  FROM   dq.pv_window
),
flagged AS (
  SELECT *, CASE WHEN prev_ts IS NULL OR timestampdiff(MINUTE, prev_ts, view_ts) > 30 THEN 1 ELSE 0 END AS new_visit
  FROM   ordered
)
SELECT *, CONCAT(person_id, '#', SUM(new_visit) OVER (PARTITION BY person_id ORDER BY view_ts)) AS visit_id
FROM flagged;

INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date)
SELECT p.check_date, 'B7', 'identity', 'silver',
       COUNT(DISTINCT session_id) / COUNT(DISTINCT visit_id) AS official_over_reconstructed, NULL, NULL, 1.5,
       CASE WHEN COUNT(DISTINCT session_id) / COUNT(DISTINCT visit_id) > 2.0 THEN 'blocker'
            WHEN COUNT(DISTINCT session_id) / COUNT(DISTINCT visit_id) > 1.5 THEN 'warning' ELSE 'ok' END,
       CONCAT(COUNT(DISTINCT session_id), ' official sessions vs ', COUNT(DISTINCT visit_id),
              ' reconstructed visits for ', COUNT(DISTINCT person_id), ' persons'),
       current_timestamp()
FROM dq.pv_visits v JOIN params p ON v.view_date = p.check_date
GROUP BY p.check_date;


-- ----------------------------------------------------------------------------
-- BLOCK 7 — Schema & fields: C1 drift, C2 nulls, C3 SDK version, C4 key, C5 format, C6 references, C7 client mix
-- ----------------------------------------------------------------------------
-- C1 — schema contract: register today's columns once, then diff daily
CREATE TABLE IF NOT EXISTS dq.schema_contract AS
SELECT table_schema, table_name, column_name, data_type
FROM   information_schema.columns
WHERE  (table_schema, table_name) IN (('sharepoint_bronze','pageviews'), ('sharepoint_bronze','customevents'));

INSERT INTO dq.dq_check_result
WITH cur AS (
  SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns
  WHERE  (table_schema, table_name) IN (('sharepoint_bronze','pageviews'), ('sharepoint_bronze','customevents'))
),
missing AS (SELECT c.* FROM dq.schema_contract c ANTI JOIN cur USING (table_schema, table_name, column_name)),
added   AS (SELECT c.* FROM cur c ANTI JOIN dq.schema_contract USING (table_schema, table_name, column_name))
SELECT date_sub(current_date(), 1), 'C1', 'schema', 'staging',
       (SELECT COUNT(*) FROM missing) + (SELECT COUNT(*) FROM added), 0, NULL, 0,
       CASE WHEN (SELECT COUNT(*) FROM missing) > 0 THEN 'blocker'
            WHEN (SELECT COUNT(*) FROM added)   > 0 THEN 'info' ELSE 'ok' END,
       CONCAT('missing: ', (SELECT COALESCE(concat_ws(', ', collect_list(column_name)), '-') FROM missing),
              ' | added: ',  (SELECT COALESCE(concat_ws(', ', collect_list(column_name)), '-') FROM added)),
       current_timestamp();
-- CustomProps keys inside customDimensions (bronze keeps the raw JSON): inventory of keys per day
-- SELECT k, COUNT(*) FROM dq.pv_window LATERAL VIEW explode(map_keys(from_json(get_json_object(customDimensions, '$.CustomProps'), 'map<string,string>'))) AS k GROUP BY k;

-- C2 — null rate per critical field vs the same field 7 days earlier
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
nulls AS (
  SELECT view_date,
         stack(6,
           'session_id',  AVG(CASE WHEN session_id  IS NULL THEN 1.0 ELSE 0.0 END),
           'browser_id',  AVG(CASE WHEN browser_id  IS NULL THEN 1.0 ELSE 0.0 END),
           'gpn',         AVG(CASE WHEN gpn         IS NULL THEN 1.0 ELSE 0.0 END),
           'page_id',     AVG(CASE WHEN page_id     IS NULL THEN 1.0 ELSE 0.0 END),
           'tracking_id', AVG(CASE WHEN tracking_id IS NULL THEN 1.0 ELSE 0.0 END),
           'sdk_version', AVG(CASE WHEN sdk_version IS NULL THEN 1.0 ELSE 0.0 END)
         ) AS (field, null_rate)
  FROM dq.pv_window GROUP BY view_date
)
SELECT p.check_date, 'C2', 'schema', 'bronze', t.null_rate, y.null_rate, NULL, y.null_rate + 0.05,
       CASE WHEN t.field IN ('session_id','browser_id','page_id') AND t.null_rate > 0.01 THEN 'blocker'
            WHEN t.null_rate > y.null_rate + 0.05 THEN 'warning' ELSE 'ok' END,
       CONCAT('field ', t.field), current_timestamp()
FROM nulls t JOIN params p ON t.view_date = p.check_date
LEFT JOIN nulls y ON y.field = t.field AND y.view_date = date_sub(p.check_date, 7);

-- C3 — SDK version watch: new value, or a share shift > 20 pp in one day
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
mix AS (
  SELECT view_date, sdk_version, COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY view_date) AS share
  FROM   dq.pv_window GROUP BY view_date, sdk_version
),
known AS (SELECT DISTINCT sdk_version FROM dq.pv_window w JOIN params p ON w.view_date < p.check_date)
SELECT p.check_date, 'C3', 'schema', 'staging',
       MAX(ABS(t.share - COALESCE(y.share, 0))) AS max_shift, NULL, NULL, 0.20,
       CASE WHEN MAX(ABS(t.share - COALESCE(y.share, 0))) > 0.20 THEN 'warning'
            WHEN COUNT(CASE WHEN k.sdk_version IS NULL THEN 1 END) > 0 THEN 'info' ELSE 'ok' END,
       CONCAT('versions today: ', concat_ws(', ', collect_list(CONCAT(t.sdk_version, ' ', ROUND(100 * t.share, 1), ' %'))),
              ' | new: ', concat_ws(', ', collect_list(CASE WHEN k.sdk_version IS NULL THEN t.sdk_version END))),
       current_timestamp()
FROM mix t JOIN params p ON t.view_date = p.check_date
LEFT JOIN mix y ON y.sdk_version = t.sdk_version AND y.view_date = date_sub(p.check_date, 1)
LEFT JOIN known k ON k.sdk_version = t.sdk_version
GROUP BY p.check_date;
-- Incident forensics: did the version mix change around 8 April 2026?
-- SELECT CAST(ViewTime AS DATE) d, sdkVersion, COUNT(*) FROM sharepoint_bronze.pageviews
-- WHERE ViewTime BETWEEN '2026-03-25' AND '2026-04-20' GROUP BY 1, 2 ORDER BY 1, 2;

-- C4 — instrumentation key / app id constant
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date)
SELECT p.check_date, 'C4', 'schema', 'staging',
       COUNT(DISTINCT ikey) + COUNT(DISTINCT app_id), 2, NULL, 2,
       CASE WHEN COUNT(DISTINCT ikey) > 1 OR COUNT(DISTINCT app_id) > 1 THEN 'blocker' ELSE 'ok' END,
       CONCAT('ikeys: ', COUNT(DISTINCT ikey), ' | app ids: ', COUNT(DISTINCT app_id)), current_timestamp()
FROM dq.pv_window w JOIN params p ON w.view_date = p.check_date
GROUP BY p.check_date;

-- C5 — format validity (row-level; also a DLT expectation candidate, see BLOCK 9)
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
v AS (
  SELECT
    AVG(CASE WHEN gpn_raw IS NOT NULL AND gpn_raw NOT RLIKE '^[0-9]{8}$' THEN 1.0 ELSE 0.0 END)       AS bad_gpn,
    AVG(CASE WHEN tracking_id IS NOT NULL
              AND tracking_id NOT RLIKE '^[A-Z0-9]{5}-[A-Z0-9]{7}-[0-9]{6}-[A-Z0-9]{7}-[A-Z]{3}$' THEN 1.0 ELSE 0.0 END) AS bad_tid,
    AVG(CASE WHEN view_ts > current_timestamp() OR view_ts < date_sub(current_date(), 70) THEN 1.0 ELSE 0.0 END) AS bad_ts
  FROM dq.pv_window w JOIN params p ON w.view_date = p.check_date
)
SELECT p.check_date, 'C5', 'schema', 'staging',
       GREATEST(bad_gpn, bad_tid, bad_ts), 0, NULL, 0.005,
       CASE WHEN GREATEST(bad_gpn, bad_tid, bad_ts) > 0.05 THEN 'blocker'
            WHEN GREATEST(bad_gpn, bad_tid, bad_ts) > 0.005 THEN 'warning' ELSE 'ok' END,
       CONCAT('invalid gpn ', ROUND(100 * bad_gpn, 2), ' % | invalid tracking id ', ROUND(100 * bad_tid, 2),
              ' % | timestamp out of window ', ROUND(100 * bad_ts, 2), ' %'),
       current_timestamp()
FROM v CROSS JOIN params p;

-- C6 — referential integrity: page id in the inventory, GPN in HR, GPN → contact id
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
r AS (
  SELECT
    AVG(CASE WHEN pg.pageUUID IS NOT NULL THEN 1.0 ELSE 0.0 END)                          AS page_hit,
    AVG(CASE WHEN w.gpn IS NULL THEN NULL WHEN hr.WORKER_ID IS NOT NULL THEN 1.0 ELSE 0.0 END) AS hr_hit,
    AVG(CASE WHEN w.gpn IS NULL THEN NULL WHEN ec.contactId IS NOT NULL THEN 1.0 ELSE 0.0 END) AS contact_hit
  FROM dq.pv_window w JOIN params p ON w.view_date = p.check_date
  LEFT JOIN sharepoint_bronze.pages pg              ON pg.pageUUID = w.page_id
  LEFT JOIN imep_bronze.tbl_hr_employee hr          ON hr.WORKER_ID = w.gpn
  LEFT JOIN sharepoint_gold.pbi_db_employeecontact ec ON ec.<gpn_column> = w.gpn    -- [VERIFY] from BLOCK 0
)
SELECT p.check_date, 'C6', 'schema', 'silver',
       LEAST(page_hit, hr_hit, contact_hit), NULL, 0.95, NULL,
       CASE WHEN LEAST(page_hit, hr_hit, contact_hit) < 0.90 THEN 'blocker'
            WHEN LEAST(page_hit, hr_hit, contact_hit) < 0.95 THEN 'warning' ELSE 'ok' END,
       CONCAT('page inventory hit ', ROUND(100 * page_hit, 1), ' % | HR hit ', ROUND(100 * hr_hit, 1),
              ' % | contact hit ', ROUND(100 * contact_hit, 1), ' %'),
       current_timestamp()
FROM r CROSS JOIN params p;

-- C7 — client mix drift vs the 4-week baseline (browser, OS, type)
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
today AS (SELECT client_browser, COUNT(*) / SUM(COUNT(*)) OVER () AS share
          FROM dq.pv_window w JOIN params p ON w.view_date = p.check_date GROUP BY client_browser),
base  AS (SELECT client_browser, COUNT(*) / SUM(COUNT(*)) OVER () AS share
          FROM dq.pv_window w JOIN params p ON w.view_date BETWEEN date_sub(p.check_date, 28) AND date_sub(p.check_date, 1)
          GROUP BY client_browser)
SELECT p.check_date, 'C7', 'schema', 'bronze',
       MAX(ABS(t.share - COALESCE(b.share, 0))), NULL, NULL, 0.15,
       CASE WHEN MAX(ABS(t.share - COALESCE(b.share, 0))) > 0.15 THEN 'warning' ELSE 'ok' END,
       CONCAT('largest browser share shift: ', ROUND(100 * MAX(ABS(t.share - COALESCE(b.share, 0))), 1), ' pp'),
       current_timestamp()
FROM today t LEFT JOIN base b ON b.client_browser = t.client_browser CROSS JOIN params p
GROUP BY p.check_date;


-- ----------------------------------------------------------------------------
-- BLOCK 8 — Plausibility: D3 top pages, D5 bronze→gold recomputation, D6 funnel, D8 Power BI
-- ----------------------------------------------------------------------------
-- D3 — top-50 page stability week over week (Jaccard)
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
tw AS (SELECT page_id FROM dq.pv_window w JOIN params p ON w.view_date BETWEEN date_sub(p.check_date, 6) AND p.check_date
       GROUP BY page_id ORDER BY COUNT(*) DESC LIMIT 50),
lw AS (SELECT page_id FROM dq.pv_window w JOIN params p ON w.view_date BETWEEN date_sub(p.check_date, 13) AND date_sub(p.check_date, 7)
       GROUP BY page_id ORDER BY COUNT(*) DESC LIMIT 50),
j  AS (SELECT (SELECT COUNT(*) FROM tw JOIN lw USING (page_id)) / (100 - (SELECT COUNT(*) FROM tw JOIN lw USING (page_id))) AS jaccard)
SELECT p.check_date, 'D3', 'plausibility', 'gold', j.jaccard, NULL, 0.5, NULL,
       CASE WHEN j.jaccard < 0.5 THEN 'warning' ELSE 'ok' END,
       'overlap of this week''s and last week''s top-50 pages', current_timestamp()
FROM j CROSS JOIN params p;

-- D5 — gold equals a fresh recount from bronze (visits AND people), per page-day, top pages
-- Gold is derived from the same App Insights bronze, so this is a transformation tie-out,
-- not an independent collector. It still localises a fault: visits diverge, people agree.
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
b AS (
  SELECT page_id, COUNT(*) AS views, COUNT(DISTINCT session_id) AS visits, COUNT(DISTINCT gpn) AS people
  FROM   dq.pv_window w JOIN params p ON w.view_date = p.check_date
  GROUP  BY page_id
),
g AS (
  SELECT marketingPageId AS page_id, SUM(views) AS views, SUM(visits) AS visits, COUNT(DISTINCT viewingcontactid) AS people
  FROM   sharepoint_gold.pbi_db_interactions_metrics gm JOIN params p ON gm.visitdatekey = date_format(p.check_date, 'yyyyMMdd')
  GROUP  BY marketingPageId
),
top AS (SELECT page_id FROM b ORDER BY views DESC LIMIT 200)
SELECT p.check_date, 'D5', 'plausibility', 'gold',
       SUM(g.visits) / NULLIF(SUM(b.visits), 0) AS visits_ratio, SUM(g.people) / NULLIF(SUM(b.people), 0) AS people_ratio, 0.8, 1.25,
       CASE WHEN SUM(g.visits) / SUM(b.visits) > 2.0 OR SUM(g.people) / SUM(b.people) NOT BETWEEN 0.8 AND 1.25 THEN 'blocker'
            WHEN SUM(g.visits) / SUM(b.visits) NOT BETWEEN 0.8 AND 1.25 THEN 'warning' ELSE 'ok' END,
       CONCAT('top-200 pages: gold/bronze views ', ROUND(SUM(g.views) / SUM(b.views), 3),
              ' | visits ', ROUND(SUM(g.visits) / SUM(b.visits), 3), ' | people ', ROUND(SUM(g.people) / SUM(b.people), 3)),
       current_timestamp()
FROM top JOIN b USING (page_id) LEFT JOIN g USING (page_id) CROSS JOIN params p
GROUP BY p.check_date;

-- D6 — funnel plausibility: landing-page views ≥ email clicks for tracked packs (last 7 days)
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
email_clicks AS (
  SELECT CONCAT_WS('-', SPLIT_PART(UPPER(e.TrackingId), '-', 1), SPLIT_PART(UPPER(e.TrackingId), '-', 2)) AS pack_id,
         COUNT(*) AS clicks
  FROM   imep_bronze.tbl_analytics_link al
  JOIN   imep_bronze.tbl_email e ON e.Id = al.EmailId
  JOIN   params p ON al.CreationDate BETWEEN date_sub(p.check_date, 6) AND p.check_date
  WHERE  al.LinkTypeEnum = 'CLICK' AND al.IsActive = 1 AND e.TrackingId IS NOT NULL   -- [VERIFY] enum value
  GROUP  BY 1
),
page_views AS (
  SELECT CONCAT_WS('-', SPLIT_PART(UPPER(pg.UBSGICTrackingID), '-', 1), SPLIT_PART(UPPER(pg.UBSGICTrackingID), '-', 2)) AS pack_id,
         COUNT(*) AS views
  FROM   dq.pv_window w
  JOIN   sharepoint_bronze.pages pg ON pg.pageUUID = w.page_id
  JOIN   params p ON w.view_date BETWEEN date_sub(p.check_date, 6) AND p.check_date
  WHERE  pg.UBSGICTrackingID IS NOT NULL
  GROUP  BY 1
)
SELECT p.check_date, 'D6', 'plausibility', 'gold',
       SUM(v.views) / NULLIF(SUM(c.clicks), 0) AS views_per_click, NULL, 0.5, NULL,
       CASE WHEN SUM(v.views) / NULLIF(SUM(c.clicks), 0) < 0.5 THEN 'warning' ELSE 'ok' END,
       CONCAT(COUNT(*), ' packs with both email clicks and landing views this week'), current_timestamp()
FROM email_clicks c JOIN page_views v USING (pack_id) CROSS JOIN params p
GROUP BY p.check_date;

-- D8 — Power BI tie-out. Not SQL: add a hidden control measure to the semantic model and
-- compare it in a Databricks SQL alert via the Power BI REST API (executeQueries), or keep a
-- "Data Health" page that shows both numbers side by side. DAX for the control measure:
--   Control Gold Views (yesterday) =
--     CALCULATE ( SUM ( interactions_metrics[views] ),
--                 interactions_metrics[visitdatekey] = FORMAT ( TODAY () - 1, "yyyyMMdd" ) )
-- The expected value is dq.gold_daily.gold_views for the same date.


-- ----------------------------------------------------------------------------
-- BLOCK 9 — Alerting, Power BI feed, DLT expectations, daily job order
-- ----------------------------------------------------------------------------
-- Databricks SQL alert query (fires when any warning/blocker exists for yesterday)
SELECT check_id, family, layer, status, ROUND(metric_value, 4) AS value, ROUND(baseline, 4) AS baseline, note
FROM   dq.dq_check_result
WHERE  check_date = date_sub(current_date(), 1) AND status IN ('warning', 'blocker')
ORDER  BY CASE status WHEN 'blocker' THEN 0 ELSE 1 END, check_id;

-- Hold flag the Gold job reads before publishing a date range
CREATE OR REPLACE VIEW dq.v_publish_hold AS
SELECT check_date, collect_set(check_id) AS blocking_checks
FROM   dq.dq_check_result WHERE status = 'blocker'
GROUP  BY check_date;

-- Power BI Data Health page: last 90 days, latest computation per day × check
CREATE OR REPLACE VIEW dq.v_data_health AS
SELECT * FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY check_date, check_id ORDER BY computed_at DESC) AS rn
  FROM dq.dq_check_result WHERE check_date >= date_sub(current_date(), 90)
) WHERE rn = 1;

-- DLT expectations for the staging → bronze step (Python cell), row-level C2 / C5:
-- import dlt
-- from pyspark.sql import functions as F
-- @dlt.table(name="pageviews")
-- @dlt.expect_or_fail("session id present",  "session_Id IS NOT NULL")
-- @dlt.expect_or_fail("browser id present",  "user_Id IS NOT NULL")
-- @dlt.expect("gpn well-formed",             "user_gpn IS NULL OR user_gpn RLIKE '^[0-9]{8}$'")
-- @dlt.expect("tracking id well-formed",     "GICTrackingID IS NULL OR GICTrackingID RLIKE '^[A-Z0-9]{5}-[A-Z0-9]{7}-[0-9]{6}-[A-Z0-9]{7}-[A-Z]{3}$'")
-- @dlt.expect("timestamp in window",         "ViewTime <= current_timestamp()")
-- def pageviews():
--     return dlt.read_stream("staging_pageviews")

-- Daily job (after the silver refresh, before gold publishes):
--   1. BLOCK 2  dq.pv_window            2. BLOCK 3  pv_daily, gold_daily, metric_daily, metric_baseline
--   3. BLOCK 4  corridor checks         4. BLOCKS 5-8 explicit checks
--   5. alert query; gold job reads dq.v_publish_hold for yesterday
-- Idempotency: DELETE FROM dq.dq_check_result WHERE check_date = date_sub(current_date(), 1) before step 3,
-- or MERGE on (check_date, check_id).
