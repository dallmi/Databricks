-- ============================================================================
-- iMEP Schema Discovery — SQL snippets
--
-- Copy any block below (everything between two `-- ---` separators) into a
-- new cell of your Databricks notebook. Each block starts with `%sql` so it
-- runs as a SQL cell even inside a Python notebook. Blocks are independent.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- BLOCK 1 — Main discovery: every candidate table + its columns
-- Filter the result grid on `has_tracking_col = true` to spot join keys.
-- ----------------------------------------------------------------------------
%sql
WITH name_hits AS (
  SELECT table_catalog, table_schema, table_name
  FROM system.information_schema.tables
  WHERE lower(table_name) RLIKE
        '(imep|email|e_mail|mail|campaign|newsletter|comms?|send|recipient|open|click|bounce|unsubscribe)'
     OR lower(table_schema) RLIKE
        '(imep|email|mail|campaign|comms?)'
),
column_hits AS (
  SELECT DISTINCT table_catalog, table_schema, table_name
  FROM system.information_schema.columns
  WHERE lower(column_name) RLIKE
        '(camms.*tracking|tracking.*id|tracking_pack|tracking_cluster)'
),
candidates AS (
  SELECT * FROM name_hits
  UNION
  SELECT * FROM column_hits
)
SELECT
  cand.table_catalog,
  cand.table_schema,
  cand.table_name,
  c.column_name,
  c.data_type,
  c.ordinal_position,
  (lower(c.column_name) RLIKE
      '(camms.*tracking|tracking.*id|tracking_pack|tracking_cluster)') AS has_tracking_col
FROM candidates cand
JOIN system.information_schema.columns c
  ON c.table_catalog = cand.table_catalog
 AND c.table_schema  = cand.table_schema
 AND c.table_name    = cand.table_name
ORDER BY cand.table_catalog, cand.table_schema, cand.table_name, c.ordinal_position;


-- ----------------------------------------------------------------------------
-- BLOCK 2 — Just the tables that match iMEP / email-channel name patterns
-- ----------------------------------------------------------------------------
%sql
SELECT
  table_catalog,
  table_schema,
  table_name,
  table_type,
  created,
  last_altered
FROM system.information_schema.tables
WHERE lower(table_name) RLIKE
      '(imep|email|e_mail|mail|campaign|newsletter|comms?|send|recipient|open|click|bounce|unsubscribe)'
   OR lower(table_schema) RLIKE
      '(imep|email|mail|campaign|comms?)'
ORDER BY table_catalog, table_schema, table_name;


-- ----------------------------------------------------------------------------
-- BLOCK 3 — Any column that looks like a tracking-id, across ALL tables
-- ----------------------------------------------------------------------------
%sql
SELECT
  table_catalog,
  table_schema,
  table_name,
  column_name,
  data_type,
  ordinal_position
FROM system.information_schema.columns
WHERE lower(column_name) RLIKE
      '(camms.*tracking|tracking.*id|tracking_pack|tracking_cluster)'
ORDER BY table_catalog, table_schema, table_name, ordinal_position;


-- ----------------------------------------------------------------------------
-- BLOCK 4 — Catalogs & schemas visible to you (sanity check)
-- ----------------------------------------------------------------------------
%sql
SELECT DISTINCT table_catalog, table_schema
FROM system.information_schema.tables
ORDER BY table_catalog, table_schema;


-- ----------------------------------------------------------------------------
-- BLOCK 5 — Row count for one specific candidate
-- Replace <catalog>.<schema>.<table> before running.
-- ----------------------------------------------------------------------------
%sql
SELECT COUNT(*) AS row_count
FROM `<catalog>`.`<schema>`.`<table>`;


-- ----------------------------------------------------------------------------
-- BLOCK 6 — Sample rows from one specific candidate
-- Replace <catalog>.<schema>.<table> before running. Keep LIMIT small.
-- ----------------------------------------------------------------------------
%sql
SELECT *
FROM `<catalog>`.`<schema>`.`<table>`
LIMIT 20;
