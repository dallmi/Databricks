-- ============================================================================
-- iMEP Bronze — Core tables for cross-channel modelling
--
-- Focuses on the 5 tables most likely to drive the PageView <-> Email join:
--   tbl_email                   — campaign/send master (1 row per email)
--   tbl_email_receiver_status   — fact: per-recipient status (sent/open/click/bounce)
--   tbl_email_links             — tracked links inside an email
--   tbl_analytics_link          — fact: click events on tracked links
--   tbl_project                 — campaign pack (likely = CPLAN "pack")
--
-- Copy any block (everything between two `-- ---` separators) into a new cell
-- of your Databricks notebook. Each block starts with `%sql` or `%python`.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- BLOCK 1 — Describe all 5 core tables in one grid (filterable)
-- Flags tracking-id columns and typical join-key columns.
-- ----------------------------------------------------------------------------
%python
import re
from pyspark.sql import Row

TRACK_RE = re.compile(r"(camms.*tracking|tracking.*id|tracking_pack|tracking_cluster|trackid)", re.IGNORECASE)
JOIN_RE  = re.compile(r"(email|project|link|receiver|status|sent|open|click|gpn|user|recipient)", re.IGNORECASE)

core = [
    "tbl_email",
    "tbl_email_receiver_status",
    "tbl_email_links",
    "tbl_analytics_link",
    "tbl_project",
]

rows = []
for t in core:
    try:
        desc = spark.sql(f"DESCRIBE TABLE imep_bronze.`{t}`").collect()
    except Exception as e:
        rows.append(Row(table=t, column=None, data_type=None,
                        tracking=False, likely_join=False, error=str(e)))
        continue
    for r in desc:
        if not r.col_name or r.col_name.startswith("#"):
            continue
        rows.append(Row(
            table=t,
            column=r.col_name,
            data_type=r.data_type,
            tracking=bool(TRACK_RE.search(r.col_name)),
            likely_join=bool(JOIN_RE.search(r.col_name)),
            error=None,
        ))

display(spark.createDataFrame(rows).orderBy("table", "column"))


-- ----------------------------------------------------------------------------
-- BLOCK 2 — Describe single tables (run any one you want to look at closely)
-- ----------------------------------------------------------------------------
%sql
DESCRIBE TABLE imep_bronze.tbl_email;

-- %sql
-- DESCRIBE TABLE imep_bronze.tbl_email_receiver_status;

-- %sql
-- DESCRIBE TABLE imep_bronze.tbl_email_links;

-- %sql
-- DESCRIBE TABLE imep_bronze.tbl_analytics_link;

-- %sql
-- DESCRIBE TABLE imep_bronze.tbl_project;


-- ----------------------------------------------------------------------------
-- BLOCK 3 — Row counts for the core tables
-- ----------------------------------------------------------------------------
%sql
SELECT 'tbl_email'                  AS table_name, COUNT(*) AS row_count FROM imep_bronze.tbl_email
UNION ALL SELECT 'tbl_email_receiver_status', COUNT(*) FROM imep_bronze.tbl_email_receiver_status
UNION ALL SELECT 'tbl_email_links',            COUNT(*) FROM imep_bronze.tbl_email_links
UNION ALL SELECT 'tbl_analytics_link',         COUNT(*) FROM imep_bronze.tbl_analytics_link
UNION ALL SELECT 'tbl_project',                COUNT(*) FROM imep_bronze.tbl_project
ORDER BY table_name;


-- ----------------------------------------------------------------------------
-- BLOCK 4 — Sample rows from tbl_email (tracking id should show up here)
-- ----------------------------------------------------------------------------
%sql
SELECT * FROM imep_bronze.tbl_email LIMIT 5;


-- ----------------------------------------------------------------------------
-- BLOCK 5 — Sample rows from tbl_email_receiver_status
-- ----------------------------------------------------------------------------
%sql
SELECT * FROM imep_bronze.tbl_email_receiver_status LIMIT 5;


-- ----------------------------------------------------------------------------
-- BLOCK 6 — Sample rows from tbl_email_links
-- ----------------------------------------------------------------------------
%sql
SELECT * FROM imep_bronze.tbl_email_links LIMIT 5;


-- ----------------------------------------------------------------------------
-- BLOCK 7 — Sample rows from tbl_analytics_link
-- ----------------------------------------------------------------------------
%sql
SELECT * FROM imep_bronze.tbl_analytics_link LIMIT 5;


-- ----------------------------------------------------------------------------
-- BLOCK 8 — Sample rows from tbl_project
-- ----------------------------------------------------------------------------
%sql
SELECT * FROM imep_bronze.tbl_project LIMIT 5;
