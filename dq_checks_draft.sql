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
--
-- HOW TO RUN: paste everything from `%python` down to the end of this block
-- into ONE notebook cell and run it. The magic must be the cell's first line,
-- so do not put it in a `%sql` cell and do not prepend the comment lines.
--
-- NOTE (2026-09-11): `information_schema` is NOT available in this workspace
-- (AnalysisException: Table or view not found). It is a Unity Catalog feature;
-- this workspace still resolves two-part names through the Hive Metastore, so
-- every probe reads the Spark schema instead. Works on either metastore.
--
-- Silver inventory confirmed 2026-09-11 (SHOW TABLES IN sharepoint_silver):
--   marketingpage · marketingsite · pageviewed · pagevisited · webpage
--   · webpagevisited · website
-- `marketingpage` / `marketingsite` are in neither April inventory. Worth a look:
-- does gold's `marketingPageId` join to `marketingpage` rather than bronze `pages`?
-- ----------------------------------------------------------------------------
%python
# BLOCK 0 — one cell, prints everything the draft needs to be filled in.
TABLES = [
    "sharepoint_bronze.pageviews",
    "sharepoint_bronze.customevents",
    "sharepoint_silver.pageviewed",
    "sharepoint_silver.pagevisited",
    "sharepoint_gold.pbi_db_interactions_metrics",
    "sharepoint_gold.pbi_db_employeecontact",
]

# The envelope columns the checks rely on. Missing ones change the plan, see below.
# Confirmed present on sharepoint_bronze.pageviews, 2026-09-11. A name that goes
# missing on a later run is schema drift and breaks the check that reads it.
ENVELOPE = ["id", "timestamp", "ingestiontime", "session_id", "user_id",
            "user_authenticatedid", "gpn", "email", "pageid", "pageurl",
            "gictrackingid", "sdkversion", "itemcount", "ikey", "appid",
            "operation_id", "client_browser", "client_os", "client_type"]

BUCKETS = {
    "time":     ("time", "date", "ts", "stamp"),
    "person":   ("gpn", "mail", "worker", "tnumber", "t_number", "contact", "person", "upn"),
    "identity": ("session", "user_", "userid", "device", "visitor", "anon"),
    "page":     ("page", "site", "url", "marketing"),
    "tracking": ("tracking", "camms", "gic"),
    "ingest":   ("ingest", "load", "_meta", "batch", "etl", "insert"),
}

for tbl in TABLES:
    try:
        fields = spark.table(tbl).schema.fields
    except Exception as e:
        print(f"\n### {tbl}: NOT READABLE — {type(e).__name__}: {str(e)[:120]}")
        continue
    cols = {f.name: f.dataType.simpleString() for f in fields}
    low = {c.lower(): c for c in cols}
    print(f"\n### {tbl} — {len(cols)} columns")
    present = sorted(low[c] for c in ENVELOPE if c in low)
    missing = sorted(c for c in ENVELOPE if c not in low)
    print(f"  envelope PRESENT : {present}")
    print(f"  envelope MISSING : {missing}")
    for label, keys in BUCKETS.items():
        hits = sorted(orig for lc, orig in low.items() if any(k in lc for k in keys))
        if hits:
            print(f"  {label:<9}: {[f'{h} ({cols[h]})' for h in hits]}")

print("\n" + "=" * 70)
print("WHAT TO DO WITH THIS OUTPUT")
print("=" * 70)
print("""
1. bronze pageviews: note the exact spelling of the session, user, gpn, page and
   timestamp columns, and whether the timestamp is UTC or local. BLOCK 2 maps them.
2. If sdkversion / itemcount / ikey are MISSING on bronze, checks A4, C3 and C4
   cannot run there. They move to KQL against App Insights: the fields are already
   projected in kql/export_for_pipeline.kql and the sampling aggregation exists
   in kql/validate_page_engagement.kql.
3. pbi_db_employeecontact: whichever column appears under `person` is the bridge
   from GPN or e-mail to the contact id. That value goes into check C6, which
   still carries a <gpn_column> placeholder.
4. silver pageviewed: whichever column appears under `time` is the one check A6
   needs for the layer tie-out.
5. If an ingestion column shows up under `ingest`, check A2 can measure true load
   latency instead of event age.
""")

-- ----------------------------------------------------------------------------
-- BLOCK 0b — Calibration probe. READ-ONLY, writes nothing, needs no permissions.
--
-- Run this BEFORE Block 1. It answers the three things still open and calibrates
-- the corridors at the same time:
--   1. does `timestamp` parse, and what does the raw string look like
--   2. what are the healthy values of the identity ratios, measured BEFORE the
--      8 April change, so the thresholds in dq.check_def stop being guesses
--   3. how far the ratios actually moved, which validates the whole approach
--
-- Windows are cut on the EVENT time (`timestamp`), not on ingestion. The raw
-- value is ISO-8601, which sorts lexicographically in the same order as it sorts
-- chronologically, so a plain STRING range on the unparsed column both selects
-- the right rows and still lets Delta skip files on its min/max statistics. The
-- cast is then only applied to the rows that survive. Never window on gmdp_date
-- or gmdp_timestamp: those record when the platform ingested the row, so a late
-- load would move an event into the wrong fortnight.
--
-- HOW TO RUN: paste from `%sql` to the semicolon into ONE cell. One result grid,
-- three rows. Photograph it.
-- ----------------------------------------------------------------------------
%sql
WITH base AS (
  SELECT
    CASE
      WHEN `timestamp` <  '2026-03-16' THEN '1 before (2-15 Mar)'
      WHEN `timestamp` <  '2026-04-27' THEN '2 after  (13-26 Apr)'
      ELSE                                  '3 now    (last 14d)'
    END                              AS period,
    CAST(`timestamp` AS TIMESTAMP)   AS ts,   -- ISO-8601 with T and Z; CAST, not to_timestamp
    `timestamp`                      AS ts_raw,
    session_Id, user_Id, GPN, sdkVersion
  FROM  sharepoint_bronze.pageviews
  WHERE (`timestamp` >= '2026-03-02' AND `timestamp` < '2026-03-16')
     OR (`timestamp` >= '2026-04-13' AND `timestamp` < '2026-04-27')
     OR  `timestamp` >= date_format(date_sub(current_date(), 14), 'yyyy-MM-dd')
),
sess AS (
  SELECT period, session_Id, COUNT(*) AS n_views
  FROM   base GROUP BY period, session_Id
),
sess_agg AS (
  SELECT period,
         AVG(CASE WHEN n_views = 1 THEN 1.0 ELSE 0.0 END) AS single_view_session_share
  FROM   sess GROUP BY period
)
SELECT
  b.period,
  COUNT(*)                                                          AS rows_scanned,
  SUM(CASE WHEN b.ts IS NULL THEN 1 ELSE 0 END)                     AS unparsed_timestamps,
  MIN(b.ts_raw)                                                     AS sample_raw_timestamp,
  MIN(b.ts)                                                         AS first_event,
  MAX(b.ts)                                                         AS last_event,
  ROUND(COUNT(*) / COUNT(DISTINCT b.session_Id), 3)                 AS views_per_session,
  ROUND(COUNT(DISTINCT b.user_Id) / COUNT(DISTINCT b.GPN), 3)       AS browser_ids_per_person,
  ROUND(COUNT(DISTINCT b.session_Id) / COUNT(DISTINCT b.GPN), 3)    AS sessions_per_person,
  ROUND(COUNT(b.GPN) / COUNT(*), 4)                                 AS identified_share,
  COUNT(DISTINCT b.GPN)                                             AS distinct_persons,
  COUNT(DISTINCT b.user_Id)                                         AS distinct_browser_ids,
  ROUND(MAX(s.single_view_session_share), 4)                        AS single_view_session_share,
  CONCAT_WS(' | ', COLLECT_SET(b.sdkVersion))                       AS sdk_versions
FROM base b
LEFT JOIN sess_agg s ON s.period = b.period
GROUP BY b.period
ORDER BY b.period;

-- Reading the result:
--   RESULT 2026-09-11: unparsed_timestamps equalled rows_scanned in all three
--   windows — the parse failed for every row. Raw form is ISO-8601 UTC, e.g.
--   2026-03-02T00:00:00.229Z. Fixed by using CAST(... AS TIMESTAMP) throughout,
--   which accepts the T separator and the Z; to_timestamp does not. The ratios
--   below were unaffected: they count session ids, browser ids and GPNs, so only
--   first_event and last_event were NULL.
--   That first run still cut its windows on gmdp_date, the INGESTION date. The
--   query now cuts them on the event time instead, so re-run it once: the figures
--   should barely move, and if any of them does, the shift is late-arriving data
--   and worth knowing about on its own.
--   Row 1 gives the healthy baseline for B1 (views_per_session, expected 1.1-1.2)
--   and B4 (browser_ids_per_person, expected near 1). Rows 2 and 3 show how far
--   they moved. Those three numbers replace the guessed thresholds in dq.check_def.
--   sdk_versions per period: a version that appears only from row 2 onward is the
--   leading indicator C3 is built to catch, and evidence for the vendor case.


-- ----------------------------------------------------------------------------
-- BLOCK 0c — The transition itself, one row per day. READ-ONLY.
--
-- Block 0b deliberately stays away from the boundary: it compares a clean
-- fortnight before with a clean fortnight after, so neither window mixes the two
-- behaviours and the healthy baseline stays trustworthy. The cost of that choice
-- is that it says nothing about 8 and 9 April themselves.
--
-- This block closes that gap. Thirty days centred on 8 April, one row per day, so
-- the shape of the change is visible rather than just its size:
--   a single overnight step  -> a deployment or a configuration switch
--   a ramp over several days -> a staged rollout across sites or regions
--   a partial drop           -> only part of the estate is affected
-- The last column is the share of traffic on the SDK version that disappeared.
-- If it falls on exactly the day the ratios break, the two are the same event and
-- the vendor case is much easier to make.
--
-- HOW TO RUN: paste from `%sql` to the semicolon into ONE cell. Thirty rows.
-- ----------------------------------------------------------------------------
%sql
WITH base AS (
  SELECT CAST(CAST(`timestamp` AS TIMESTAMP) AS DATE) AS d,
         session_Id, user_Id, GPN, sdkVersion
  FROM   sharepoint_bronze.pageviews
  WHERE  `timestamp` >= '2026-03-25' AND `timestamp` < '2026-04-24'
),
sess AS (
  SELECT d, session_Id, COUNT(*) AS n_views FROM base GROUP BY d, session_Id
),
sv AS (
  SELECT d, AVG(CASE WHEN n_views = 1 THEN 1.0 ELSE 0.0 END) AS sv_share FROM sess GROUP BY d
)
SELECT
  b.d                                                              AS day,
  date_format(b.d, 'E')                                            AS dow,
  COUNT(*)                                                         AS views,
  ROUND(COUNT(*) / COUNT(DISTINCT b.session_Id), 3)                AS views_per_session,
  ROUND(COUNT(DISTINCT b.user_Id) / COUNT(DISTINCT b.GPN), 2)      AS browser_ids_per_person,
  ROUND(MAX(sv.sv_share), 3)                                       AS single_view_share,
  COUNT(DISTINCT b.GPN)                                            AS persons,
  COUNT(DISTINCT b.user_Id)                                        AS browser_ids,
  ROUND(AVG(CASE WHEN b.sdkVersion = 'javascript:3.3.6' THEN 1.0 ELSE 0.0 END), 4) AS sdk_336_share
FROM base b
LEFT JOIN sv ON sv.d = b.d
GROUP BY b.d
ORDER BY b.d;

-- Reading the result:
--   `persons` should stay flat across the whole month. It is the control: if the
--   population moves too, something other than the identifier changed.
--   `views_per_session` and `browser_ids_per_person` should move on the same day
--   and in opposite directions. The day they do is the incident date.
--   `sdk_336_share` going to zero on that same day makes the version change and
--   the identity change one event rather than two.
--   Weekends carry far less traffic; compare Monday with Monday, which is why the
--   weekday is printed next to the date.


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
  -- MEASURED, not guessed. Block 0b, 2-15 March 2026 (2.86M views, pre-incident):
  --   views_per_session 2.231 | browser_ids_per_person 1.71 | single-view share 0.584
  --   identified_share 1.000  | 115,298 people behind 197,107 browser identities
  -- For 13-26 April the same numbers read 1.062 / 20.004 / 0.939, and the most
  -- recent fortnight 1.157 / 17.342 / 0.888 — improved but far from healthy.
  -- These thresholds describe HEALTHY data, so B1, B4 and B5 fire today by design
  -- and keep firing until the source is fixed. Do not retune them to the broken
  -- state; that would define the incident away.
  ('B1','identity','bronze','views_per_session',         1.50, 1.90, NULL, NULL, NULL, NULL, 0.10, 0.25, 'page views per visit; measured healthy 2.23'),
  ('B2','identity','bronze','sessions_per_browser_7d',   1.02, 1.10, NULL, NULL, NULL, NULL, 0.15, 0.30, 'visits per browser identity over a rolling week; not yet measured'),
  ('B4','identity','bronze','browser_ids_per_person',    NULL, NULL, 3.0, 6.0, NULL, NULL, 0.15, 0.30, 'distinct user_Id per GPN; measured healthy 1.71'),
  ('B5','identity','bronze','single_view_session_share', NULL, NULL, 0.70, 0.85, NULL, NULL, 0.10, 0.20, 'share of sessions with exactly one view; measured healthy 0.584'),
  ('B8','identity','bronze','identified_share',          0.90, 0.97, NULL, NULL, NULL, NULL, 0.05, 0.15, 'views with a valid GPN; measured 1.000, every row carries a GPN'),
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
-- Column names confirmed against the workspace on 2026-09-11 (BLOCK 0).
-- Bronze is ALREADY FLAT: there is no `customDimensions`; the CustomProps are
-- real columns (GPN, Email, PageURL, PageName, PublishingDate, SiteId, ...).
-- `timestamp` is the INTERACTION time and is typed STRING, so it is parsed here.
-- `gmdp_timestamp` is when the record was ingested into the source platform and
-- is NOT the event time — never use it for the daily grain.
-- TIMESTAMP PARSING — settled 2026-09-11 by running Block 0b.
-- The raw value is ISO-8601 UTC with a T separator, milliseconds and a Z suffix,
-- e.g. 2026-03-02T00:00:00.229Z. Neither a bare to_timestamp() nor the pattern
-- 'yyyy-MM-dd HH:mm:ss' matches it; both return NULL for every row (the first
-- Block 0b run had unparsed_timestamps = rows_scanned, 2.8M of 2.8M).
-- CAST handles ISO-8601 including the T and the Z, so CAST is used everywhere.
-- The values are UTC. Any local-time reporting grain must convert explicitly.
SELECT
  id                                   AS view_id,
  `timestamp`                          AS view_ts_raw,
  CAST(`timestamp` AS TIMESTAMP)       AS view_ts,
  CAST(CAST(`timestamp` AS TIMESTAMP) AS DATE) AS view_date,
  gmdp_timestamp                       AS gmdp_ingested_ts, -- platform ingestion time, TIMESTAMP already
  CAST(ingestiontime AS TIMESTAMP)     AS ingested_ts,      -- second ingestion stamp; compare the two once
  gmdp_date                            AS gmdp_date,        -- ingestion DATE; never use as the event grain
  session_Id                           AS session_id,
  user_Id                              AS browser_id,     -- the App Insights ai_user cookie
  user_AuthenticatedId                 AS auth_id,        -- set when the SDK knows the user
  CASE WHEN GPN RLIKE '^[0-9]{8}$' THEN GPN END AS gpn,
  GPN                                  AS gpn_raw,
  Email                                AS email,
  pageId                               AS page_id,        -- INT on bronze, not the pages GUID
  PageURL                              AS page_url,
  GICTrackingID                        AS tracking_id,
  sdkVersion                           AS sdk_version,
  COALESCE(itemCount, 1)               AS item_count,
  iKey                                 AS ikey,
  appId                                AS app_id,
  client_Browser                       AS client_browser,
  client_OS                            AS client_os,
  client_Type                          AS client_type
FROM sharepoint_bronze.pageviews
-- String range on the raw ISO-8601 value: correct chronologically and skippable,
-- unlike a predicate wrapped in CAST. gmdp_date is deliberately not used here.
WHERE `timestamp` >= date_format(date_sub(current_date(), 70), 'yyyy-MM-dd');

-- Guard: the cast must not silently drop rows. Run once after the first build.
SELECT COUNT(*)                                            AS rows_in_window,
       SUM(CASE WHEN view_ts IS NULL THEN 1 ELSE 0 END)    AS unparsed_timestamps,
       MIN(view_ts) AS first_event, MAX(view_ts) AS last_event,
       MIN(view_ts_raw) AS sample_raw_low, MAX(view_ts_raw) AS sample_raw_high
FROM dq.pv_window;


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
  -- customevents carries `timestamp` (string) like pageviews, but has NO `id` column
  SELECT CAST(CAST(`timestamp` AS TIMESTAMP) AS DATE) AS view_date, COUNT(*) AS clicks
  FROM   sharepoint_bronze.customevents
  WHERE  name = 'click_event'
    AND  `timestamp` >= date_format(date_sub(current_date(), 70), 'yyyy-MM-dd')
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
  SELECT 'bronze' AS layer, MAX(view_ts) AS max_ts,
         timestampdiff(HOUR, MAX(view_ts), current_timestamp()) AS age_h
  FROM   dq.pv_window WHERE view_date >= date_sub(current_date(), 3)
  UNION ALL
  -- True load latency is the ONLY place an ingestion column belongs: how long the
  -- platform took to land an event, measured against the event's own time.
  SELECT 'bronze_load_lag', MAX(gmdp_ingested_ts),
         CAST(percentile_approx(timestampdiff(MINUTE, view_ts, gmdp_ingested_ts), 0.95) / 60 AS INT)
  FROM   dq.pv_window WHERE view_date >= date_sub(current_date(), 3)
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
      ON CAST(sv.`timestamp` AS DATE) = p.check_date),   -- silver: real TIMESTAMP type
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
now_cnt  AS (SELECT COUNT(*) AS n FROM sharepoint_bronze.pageviews pv JOIN params p
             ON pv.`timestamp` >= date_format(p.d, 'yyyy-MM-dd')
            AND pv.`timestamp` <  date_format(date_add(p.d, 1), 'yyyy-MM-dd')),
then_cnt AS (SELECT COUNT(*) AS n
             FROM sharepoint_bronze.pageviews TIMESTAMP AS OF date_sub(current_date(), 2) pv   -- retention is 7 days
             JOIN params p ON pv.`timestamp` >= date_format(p.d, 'yyyy-MM-dd')
                          AND pv.`timestamp` <  date_format(date_add(p.d, 1), 'yyyy-MM-dd'))
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
-- C1 — schema contract: register today's columns once, then diff daily.
-- No information_schema in this workspace (see BLOCK 0), so the current column
-- set is materialised from the Spark schema by this Python cell, then diffed in SQL.
%python
from pyspark.sql import Row
TRACKED = [('sharepoint_bronze', 'pageviews'), ('sharepoint_bronze', 'customevents')]
rows = [Row(table_schema=sch, table_name=tbl, column_name=f.name, data_type=f.dataType.simpleString())
        for sch, tbl in TRACKED for f in spark.table(f"{sch}.{tbl}").schema.fields]
spark.createDataFrame(rows).createOrReplaceTempView("cur_columns")
# first run only — freeze today's schema as the contract:
# spark.createDataFrame(rows).write.saveAsTable("dq.schema_contract")

INSERT INTO dq.dq_check_result
WITH cur AS (
  SELECT table_schema, table_name, column_name, data_type FROM cur_columns
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
-- Note: bronze is already flat (BLOCK 0, 2026-09-11) — there is no customDimensions
-- column to explode. The former CustomProps keys are first-class columns, so C1's
-- column diff covers them directly.

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
-- ANSWERED 2026-09-11 by Block 0b. The SDK mix changed at the incident, but in
-- the opposite direction to the one this check was written to expect: version
-- `javascript:3.3.6` is present 2-15 March and ABSENT from 13-26 April onward.
-- 2.8.16 and 2.7.4 remain in both windows. A version DISAPPEARING is as much a
-- signal as a new one arriving, so C3 alerts on any change to the version set.
-- SELECT CAST(CAST(`timestamp` AS TIMESTAMP) AS DATE) d, sdkVersion, COUNT(*)
-- FROM sharepoint_bronze.pageviews
-- WHERE CAST(`timestamp` AS TIMESTAMP) BETWEEN '2026-03-25' AND '2026-04-20'
-- GROUP BY 1, 2 ORDER BY 1, 2;

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
    AVG(CASE WHEN view_ts IS NULL OR view_ts > current_timestamp()
                   OR view_ts < date_sub(current_date(), 70) THEN 1.0 ELSE 0.0 END) AS bad_ts
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

-- C6 — referential integrity from bronze: page in the inventory, GPN in HR.
--
-- The GPN -> contact-id bridge is deliberately NOT checked here. BLOCK 0 showed
-- that sharepoint_gold.pbi_db_employeecontact carries `contactId` and no GPN,
-- e-mail or T-number, so it is not the lookup that resolves a person. The
-- resolution happens inside the bronze -> silver transformation, which surfaces
-- `contactId` on sharepoint_silver.pageviewed. Checking it would mean reading
-- silver; the checks stay on bronze by decision (2026-09-11). The contact side
-- is covered indirectly by D1c and D5, which count distinct contacts in gold.
--
-- `pageId` is an INT on bronze while `pages.pageUUID` is a GUID string, so the
-- page join below is by URL, which both sides carry. Confirm the URL forms match
-- (trailing slash, host prefix, case) before trusting the hit rate.
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
r AS (
  SELECT
    AVG(CASE WHEN pg.PageURL IS NOT NULL THEN 1.0 ELSE 0.0 END)                                AS page_hit,
    AVG(CASE WHEN w.gpn IS NULL THEN NULL WHEN hr.WORKER_ID IS NOT NULL THEN 1.0 ELSE 0.0 END) AS hr_hit
  FROM dq.pv_window w JOIN params p ON w.view_date = p.check_date
  LEFT JOIN sharepoint_bronze.pages pg     ON LOWER(TRIM(pg.PageURL)) = LOWER(TRIM(w.page_url))
  LEFT JOIN imep_bronze.tbl_hr_employee hr ON hr.WORKER_ID = w.gpn
)
SELECT p.check_date, 'C6', 'schema', 'bronze',
       LEAST(page_hit, hr_hit), NULL, 0.95, NULL,
       CASE WHEN LEAST(page_hit, hr_hit) < 0.90 THEN 'blocker'
            WHEN LEAST(page_hit, hr_hit) < 0.95 THEN 'warning' ELSE 'ok' END,
       CONCAT('page inventory hit ', ROUND(100 * page_hit, 1),
              ' % | HR hit ', ROUND(100 * hr_hit, 1), ' %'),
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
-- BLOCK 8b — SILVER: does the refinement preserve what bronze delivered?
--
-- The B-family above is labelled "identity" but reads BRONZE (dq.pv_window) by
-- decision (2026-09-11): the raw cookie signals are the early warning. Silver
-- needs its OWN checks, because this is where the person is resolved — bronze
-- has GPN and e-mail, silver has `contactId`, and that resolution is what the
-- unique-visitor KPI rests on. A silent failure here changes the headline number
-- while every bronze check stays green.
--
-- Columns confirmed 2026-09-11: timestamp (TIMESTAMP), contactId, visitorId,
-- sessionId (pageviewed only), visitorReturningStatus, visitorAnonymousStatus,
-- marketingPageId, pageAddress, websiteId.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE dq.sv_daily AS
SELECT
  CAST(`timestamp` AS DATE)                                        AS view_date,
  COUNT(*)                                                         AS sv_rows,
  COUNT(contactId)              / COUNT(*)                         AS contact_resolution_rate,
  COUNT(visitorId)              / COUNT(*)                         AS visitor_id_rate,
  COUNT(sessionId)              / COUNT(*)                         AS session_id_rate,
  COUNT(marketingPageId)        / COUNT(*)                         AS page_guid_rate,
  COUNT(DISTINCT contactId)                                        AS sv_contacts,
  COUNT(DISTINCT visitorId)                                        AS sv_visitors,
  AVG(CASE WHEN LOWER(visitorReturningStatus) LIKE '%return%' THEN 1.0 ELSE 0.0 END) AS returning_share,
  AVG(CASE WHEN LOWER(visitorAnonymousStatus) LIKE '%anon%'   THEN 1.0 ELSE 0.0 END) AS anonymous_share
FROM   sharepoint_silver.pageviewed
WHERE  `timestamp` >= date_sub(current_date(), 70)
GROUP  BY 1;

-- S1 — person resolution: every bronze view with a GPN must end up as a contact.
-- Compares the two layers on their own terms: distinct people in bronze (GPN)
-- against distinct people in silver (contactId) for the same day.
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
b AS (SELECT COUNT(DISTINCT gpn) AS n FROM dq.pv_window w JOIN params p ON w.view_date = p.check_date),
v AS (SELECT sv_contacts AS n FROM dq.sv_daily sv JOIN params p ON sv.view_date = p.check_date)
SELECT p.check_date, 'S1', 'identity', 'silver',
       v.n / NULLIF(b.n, 0), 1.0, 0.9, 1.1,
       CASE WHEN v.n / NULLIF(b.n, 0) NOT BETWEEN 0.75 AND 1.35 THEN 'blocker'
            WHEN v.n / NULLIF(b.n, 0) NOT BETWEEN 0.90 AND 1.10 THEN 'warning' ELSE 'ok' END,
       CONCAT('bronze distinct GPN ', b.n, ' vs silver distinct contactId ', v.n,
              ' — a ratio far below 1 means people are collapsing, far above means they are splitting'),
       current_timestamp()
FROM params p CROSS JOIN b CROSS JOIN v;

-- S2 — key completeness on silver (contact, visitor, session, page GUID)
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date)
SELECT p.check_date, 'S2', 'schema', 'silver',
       LEAST(contact_resolution_rate, visitor_id_rate, page_guid_rate), NULL, 0.95, NULL,
       CASE WHEN LEAST(contact_resolution_rate, visitor_id_rate, page_guid_rate) < 0.80 THEN 'blocker'
            WHEN LEAST(contact_resolution_rate, visitor_id_rate, page_guid_rate) < 0.95 THEN 'warning' ELSE 'ok' END,
       CONCAT('contactId ', ROUND(100 * contact_resolution_rate, 1),
              ' % | visitorId ', ROUND(100 * visitor_id_rate, 1),
              ' % | sessionId ', ROUND(100 * session_id_rate, 1),
              ' % | marketingPageId ', ROUND(100 * page_guid_rate, 1), ' %'),
       current_timestamp()
FROM dq.sv_daily sv JOIN params p ON sv.view_date = p.check_date;

-- S3 — returning-visitor mix. Silver computes this itself; a collapse is the
-- April signature seen one layer later, and it needs no reconstruction from us.
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
base AS (SELECT AVG(returning_share) AS r FROM dq.sv_daily sv JOIN params p
         ON sv.view_date BETWEEN date_sub(p.check_date, 35) AND date_sub(p.check_date, 8))
SELECT p.check_date, 'S3', 'identity', 'silver',
       sv.returning_share, base.r, base.r - 0.20, base.r + 0.20,
       CASE WHEN base.r IS NULL THEN 'info'
            WHEN sv.returning_share < base.r - 0.20 THEN 'blocker'
            WHEN ABS(sv.returning_share - base.r) > 0.10 THEN 'warning' ELSE 'ok' END,
       CONCAT('returning ', ROUND(100 * sv.returning_share, 1), ' % vs 4-week baseline ',
              ROUND(100 * base.r, 1), ' % | anonymous ', ROUND(100 * sv.anonymous_share, 1), ' %'),
       current_timestamp()
FROM dq.sv_daily sv JOIN params p ON sv.view_date = p.check_date CROSS JOIN base;


-- ----------------------------------------------------------------------------
-- BLOCK 8c — GOLD: is the aggregate a faithful summary of silver?
--
-- Gold is what Power BI reads. Two failure modes matter and neither shows up
-- anywhere else: a broken grain silently multiplies every KPI, and an internally
-- inconsistent row makes visits and duration disagree with views.
-- ----------------------------------------------------------------------------
-- G1 — grain uniqueness. Documented PK: marketingpageid x visitdatekey x
-- viewingcontactid x referenceapplicationid. A duplicate doubles the KPI.
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
g AS (
  SELECT marketingpageid, viewingcontactid, referenceapplicationid, COUNT(*) AS n
  FROM   sharepoint_gold.pbi_db_interactions_metrics gm JOIN params p
         ON gm.visitdatekey = date_format(p.check_date, 'yyyyMMdd')
  GROUP  BY 1, 2, 3
)
SELECT p.check_date, 'G1', 'plausibility', 'gold',
       SUM(CASE WHEN n > 1 THEN n - 1 ELSE 0 END) / SUM(n), 0, NULL, 0,
       CASE WHEN SUM(CASE WHEN n > 1 THEN 1 ELSE 0 END) > 0 THEN 'blocker' ELSE 'ok' END,
       CONCAT(SUM(CASE WHEN n > 1 THEN 1 ELSE 0 END), ' duplicated grain keys of ', COUNT(*)),
       current_timestamp()
FROM g CROSS JOIN params p;

-- G2 — row-level internal consistency: a visit cannot exceed the views it holds,
-- metrics cannot be negative, and durationavg must match durationsum / views.
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
g AS (
  SELECT
    AVG(CASE WHEN visits > views THEN 1.0 ELSE 0.0 END)                                   AS visits_gt_views,
    AVG(CASE WHEN views < 0 OR visits < 0 OR durationsum < 0 THEN 1.0 ELSE 0.0 END)       AS negatives,
    AVG(CASE WHEN views > 0 AND durationavg IS NOT NULL
              AND ABS(durationavg - durationsum / views) > 0.01 THEN 1.0 ELSE 0.0 END)    AS avg_mismatch
  FROM sharepoint_gold.pbi_db_interactions_metrics gm JOIN params p
       ON gm.visitdatekey = date_format(p.check_date, 'yyyyMMdd')
)
SELECT p.check_date, 'G2', 'plausibility', 'gold',
       GREATEST(visits_gt_views, negatives, avg_mismatch), 0, NULL, 0.001,
       CASE WHEN GREATEST(visits_gt_views, negatives) > 0 THEN 'blocker'
            WHEN avg_mismatch > 0.001 THEN 'warning' ELSE 'ok' END,
       CONCAT('visits > views ', ROUND(100 * visits_gt_views, 3),
              ' % | negative metrics ', ROUND(100 * negatives, 3),
              ' % | durationavg mismatch ', ROUND(100 * avg_mismatch, 3), ' %'),
       current_timestamp()
FROM g CROSS JOIN params p;

-- G3 — silver -> gold aggregation tie-out: gold views must equal silver rows,
-- and gold contacts must equal silver contacts, for the same day.
INSERT INTO dq.dq_check_result
WITH params AS (SELECT date_sub(current_date(), 1) AS check_date),
v AS (SELECT sv_rows, sv_contacts FROM dq.sv_daily sv JOIN params p ON sv.view_date = p.check_date),
g AS (SELECT SUM(views) AS views, COUNT(DISTINCT viewingcontactid) AS contacts
      FROM sharepoint_gold.pbi_db_interactions_metrics gm JOIN params p
           ON gm.visitdatekey = date_format(p.check_date, 'yyyyMMdd'))
SELECT p.check_date, 'G3', 'completeness', 'gold',
       g.views / NULLIF(v.sv_rows, 0), 1.0, 0.99, 1.01,
       CASE WHEN g.views / NULLIF(v.sv_rows, 0) NOT BETWEEN 0.95 AND 1.05
             OR g.contacts / NULLIF(v.sv_contacts, 0) NOT BETWEEN 0.95 AND 1.05 THEN 'blocker'
            WHEN g.views / NULLIF(v.sv_rows, 0) NOT BETWEEN 0.99 AND 1.01 THEN 'warning' ELSE 'ok' END,
       CONCAT('views gold/silver ', ROUND(g.views / NULLIF(v.sv_rows, 0), 4),
              ' | contacts gold/silver ', ROUND(g.contacts / NULLIF(v.sv_contacts, 0), 4),
              ' — document any intentional filter as the tolerated gap'),
       current_timestamp()
FROM params p CROSS JOIN v CROSS JOIN g;


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
-- @dlt.expect("gpn well-formed",             "GPN IS NULL OR GPN RLIKE '^[0-9]{8}$'")
-- @dlt.expect("tracking id well-formed",     "GICTrackingID IS NULL OR GICTrackingID RLIKE '^[A-Z0-9]{5}-[A-Z0-9]{7}-[0-9]{6}-[A-Z0-9]{7}-[A-Z]{3}$'")
-- @dlt.expect("timestamp parses",            "CAST(`timestamp` AS TIMESTAMP) IS NOT NULL")
-- @dlt.expect("timestamp in window",         "CAST(`timestamp` AS TIMESTAMP) <= current_timestamp()")
-- def pageviews():
--     return dlt.read_stream("staging_pageviews")

-- Daily job (after the silver refresh, before gold publishes):
--   1. BLOCK 2  dq.pv_window            2. BLOCK 3  pv_daily, gold_daily, metric_daily, metric_baseline
--   3. BLOCK 4  corridor checks         4. BLOCKS 5-8 explicit checks
--   5. alert query; gold job reads dq.v_publish_hold for yesterday
-- Idempotency: DELETE FROM dq.dq_check_result WHERE check_date = date_sub(current_date(), 1) before step 3,
-- or MERGE on (check_date, check_id).
