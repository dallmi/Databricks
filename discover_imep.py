# Databricks notebook source
# MAGIC %md
# MAGIC # iMEP Schema Discovery
# MAGIC Locates iMEP (email channel) tables in Unity Catalog and profiles their structure.
# MAGIC Strategy: scan every catalog/schema, surface anything containing "imep", "email", "mail", "campaign",
# MAGIC then profile candidates and look for `CammsTrackingID` / `tracking_id` join keys.

# COMMAND ----------

from pyspark.sql import functions as F
from collections import defaultdict
import json, re

# Patterns that likely identify iMEP / email-channel objects
NAME_PATTERNS = [
    r"imep",
    r"email",
    r"e_mail",
    r"\bmail\b",
    r"campaign",
    r"newsletter",
    r"comms?",
    r"send",
    r"recipient",
    r"open",
    r"click",
    r"bounce",
    r"unsubscribe",
]
NAME_RE = re.compile("|".join(NAME_PATTERNS), re.IGNORECASE)

# Tracking-key columns we want to confirm exist
TRACKING_KEYS_RE = re.compile(r"camms.*tracking|tracking.*id|tracking_pack|tracking_cluster", re.IGNORECASE)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1: Enumerate catalogs and schemas

# COMMAND ----------

catalogs = [r.catalog for r in spark.sql("SHOW CATALOGS").collect()]
print(f"Catalogs ({len(catalogs)}): {catalogs}")

inventory = []  # list of (catalog, schema, table)

for cat in catalogs:
    try:
        schemas = [r.databaseName for r in spark.sql(f"SHOW SCHEMAS IN `{cat}`").collect()]
    except Exception as e:
        print(f"  skip catalog {cat}: {e}")
        continue

    for sch in schemas:
        if sch in ("information_schema",):
            continue
        try:
            tables = [r.tableName for r in spark.sql(f"SHOW TABLES IN `{cat}`.`{sch}`").collect()]
        except Exception as e:
            print(f"  skip {cat}.{sch}: {e}")
            continue
        for t in tables:
            inventory.append((cat, sch, t))

print(f"\nTotal objects discovered: {len(inventory)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2: Filter to iMEP / email candidates by name

# COMMAND ----------

candidates_by_name = [
    (c, s, t) for (c, s, t) in inventory
    if NAME_RE.search(t) or NAME_RE.search(s)
]

print(f"Name-based candidates: {len(candidates_by_name)}\n")
for c, s, t in sorted(candidates_by_name):
    print(f"  {c}.{s}.{t}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3: Also scan all tables for tracking-id columns
# MAGIC (catches iMEP tables that don't follow naming conventions)

# COMMAND ----------

candidates_by_column = []  # (catalog, schema, table, matched_columns)

for c, s, t in inventory:
    fqn = f"`{c}`.`{s}`.`{t}`"
    try:
        cols = spark.table(fqn).columns
    except Exception:
        continue
    matches = [col for col in cols if TRACKING_KEYS_RE.search(col)]
    if matches:
        candidates_by_column.append((c, s, t, matches))

print(f"Tables containing a tracking-id-style column: {len(candidates_by_column)}\n")
for c, s, t, m in sorted(candidates_by_column):
    print(f"  {c}.{s}.{t}  ->  {m}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4: Profile all candidates (schema, row count, sample values)

# COMMAND ----------

all_candidates = sorted({(c, s, t) for (c, s, t) in candidates_by_name} |
                        {(c, s, t) for (c, s, t, _) in candidates_by_column})

print(f"Profiling {len(all_candidates)} candidate tables\n")

profiles = {}
for c, s, t in all_candidates:
    fqn = f"`{c}`.`{s}`.`{t}`"
    print(f"\n{'='*70}\n{fqn}\n{'='*70}")
    try:
        df = spark.table(fqn)
        row_count = df.count()
        print(f"  Rows: {row_count:,}")
        cols = []
        for field in df.schema.fields:
            col, dtype = field.name, str(field.dataType)
            try:
                samples = [
                    str(r[0]) for r in
                    df.select(col).where(F.col(col).isNotNull()).distinct().limit(3).collect()
                ]
            except Exception:
                samples = []
            cols.append({"name": col, "type": dtype, "samples": samples})
            highlight = "  <<< TRACKING" if TRACKING_KEYS_RE.search(col) else ""
            print(f"    {col:40s} {dtype:25s} samples={samples}{highlight}")
        profiles[fqn] = {"rows": row_count, "columns": cols}
    except Exception as e:
        print(f"  ERROR: {e}")
        profiles[fqn] = {"error": str(e)}

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 5: Export discovery results

# COMMAND ----------

output = {
    "catalogs": catalogs,
    "all_objects": [f"{c}.{s}.{t}" for (c, s, t) in inventory],
    "name_candidates": [f"{c}.{s}.{t}" for (c, s, t) in candidates_by_name],
    "column_candidates": [
        {"table": f"{c}.{s}.{t}", "tracking_cols": m}
        for (c, s, t, m) in candidates_by_column
    ],
    "profiles": profiles,
}

json_str = json.dumps(output, indent=2, default=str)
dbutils.fs.put("dbfs:/tmp/imep_discovery.json", json_str, overwrite=True)
dbutils.fs.cp("dbfs:/tmp/imep_discovery.json", "dbfs:/FileStore/imep_discovery.json", True)

workspace_url = "https://adb-205203499382645.5.azuredatabricks.net"
print(f"\nDownload: {workspace_url}/files/imep_discovery.json")
