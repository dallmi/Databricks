-- ============================================================================
-- iMEP Schema Discovery — SQL snippets (Hive Metastore version)
--
-- Use this file when your cluster runs against Hive Metastore (no Unity
-- Catalog). system.information_schema is NOT available; discovery happens
-- via SHOW DATABASES / SHOW TABLES / DESCRIBE.
--
-- Copy any block below (everything between two `-- ---` separators) into a
-- new cell of your Databricks notebook. Each block starts with `%sql` or
-- `%python` so it runs correctly regardless of the notebook's default lang.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- BLOCK 1 — List all databases / schemas
-- ----------------------------------------------------------------------------
%sql
SHOW DATABASES;


-- ----------------------------------------------------------------------------
-- BLOCK 2 — List tables in one database
-- Replace <db> with a schema name from BLOCK 1.
-- ----------------------------------------------------------------------------
%sql
SHOW TABLES IN `<db>`;


-- ----------------------------------------------------------------------------
-- BLOCK 3 — Find tables whose name matches an iMEP/email pattern in one db
-- Replace <db>. Use SQL wildcards: * = any chars.
-- ----------------------------------------------------------------------------
%sql
SHOW TABLES IN `<db>` LIKE '*imep*';

-- Other patterns to try, one at a time:
-- SHOW TABLES IN `<db>` LIKE '*email*';
-- SHOW TABLES IN `<db>` LIKE '*mail*';
-- SHOW TABLES IN `<db>` LIKE '*campaign*';
-- SHOW TABLES IN `<db>` LIKE '*comms*';
-- SHOW TABLES IN `<db>` LIKE '*send*';
-- SHOW TABLES IN `<db>` LIKE '*track*';


-- ----------------------------------------------------------------------------
-- BLOCK 4 — Describe a candidate table (columns + types)
-- Replace <db>.<table>.
-- ----------------------------------------------------------------------------
%sql
DESCRIBE TABLE `<db>`.`<table>`;


-- ----------------------------------------------------------------------------
-- BLOCK 5 — Extended describe (partitions, location, owner)
-- ----------------------------------------------------------------------------
%sql
DESCRIBE TABLE EXTENDED `<db>`.`<table>`;


-- ----------------------------------------------------------------------------
-- BLOCK 6 — Row count
-- ----------------------------------------------------------------------------
%sql
SELECT COUNT(*) AS row_count FROM `<db>`.`<table>`;


-- ----------------------------------------------------------------------------
-- BLOCK 7 — Sample rows
-- ----------------------------------------------------------------------------
%sql
SELECT * FROM `<db>`.`<table>` LIMIT 20;


-- ----------------------------------------------------------------------------
-- BLOCK 8 — Cross-database discovery (needs %python; pure SQL can't loop)
-- Scans every database, collects matching tables, and columns that look
-- like a tracking-id. Paste into a Python cell.
-- ----------------------------------------------------------------------------
%python
import re
from pyspark.sql import Row

NAME_RE  = re.compile(r"(imep|email|e_mail|mail|campaign|newsletter|comms?|send|recipient|open|click|bounce|unsubscribe|track)", re.IGNORECASE)
TRACK_RE = re.compile(r"(camms.*tracking|tracking.*id|tracking_pack|tracking_cluster)", re.IGNORECASE)

dbs = [r.databaseName for r in spark.sql("SHOW DATABASES").collect()]
print(f"Scanning {len(dbs)} databases")

hits = []
for db in dbs:
    try:
        tables = [r.tableName for r in spark.sql(f"SHOW TABLES IN `{db}`").collect()]
    except Exception as e:
        print(f"  skip {db}: {e}")
        continue
    for t in tables:
        name_match = bool(NAME_RE.search(t) or NAME_RE.search(db))
        try:
            cols = [r.col_name for r in spark.sql(f"DESCRIBE TABLE `{db}`.`{t}`").collect()
                    if r.col_name and not r.col_name.startswith("#")]
        except Exception:
            cols = []
        tracking_cols = [c for c in cols if TRACK_RE.search(c)]
        if name_match or tracking_cols:
            hits.append(Row(
                database=db, table=t,
                name_match=name_match,
                tracking_cols=",".join(tracking_cols) if tracking_cols else None,
                n_columns=len(cols),
            ))

result = spark.createDataFrame(hits) if hits else None
if result is not None:
    display(result.orderBy("database", "table"))
else:
    print("No candidates found.")


-- ----------------------------------------------------------------------------
-- BLOCK 9 — Full column inventory for all candidates from BLOCK 8
-- Run BLOCK 8 first so `hits` is in memory, then paste this.
-- ----------------------------------------------------------------------------
%python
from pyspark.sql import Row

inventory = []
for h in hits:
    try:
        cols = spark.sql(f"DESCRIBE TABLE `{h.database}`.`{h.table}`").collect()
    except Exception as e:
        inventory.append(Row(database=h.database, table=h.table,
                             column=None, data_type=None,
                             has_tracking_col=False, error=str(e)))
        continue
    for r in cols:
        if not r.col_name or r.col_name.startswith("#"):
            continue
        inventory.append(Row(
            database=h.database, table=h.table,
            column=r.col_name, data_type=r.data_type,
            has_tracking_col=bool(TRACK_RE.search(r.col_name)),
            error=None,
        ))

inv_df = spark.createDataFrame(inventory)
display(inv_df.orderBy("database", "table", "column"))
