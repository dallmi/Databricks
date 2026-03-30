# Databricks notebook source
# MAGIC %md
# MAGIC # Data Model Discovery
# MAGIC Automatically discovers schemas, join keys, and relationships across all `pbi_db_*` tables.

# COMMAND ----------

# Configuration
TABLE_PREFIX = "pbi_db_"
CATALOG = None  # Set if using Unity Catalog, e.g. "my_catalog"
SCHEMA = None   # Set if needed, e.g. "my_schema"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1: Discover all tables and their schemas

# COMMAND ----------

from pyspark.sql import functions as F
from collections import defaultdict
import json

# Get all table names
if CATALOG and SCHEMA:
    tables_df = spark.sql(f"SHOW TABLES IN {CATALOG}.{SCHEMA}")
else:
    tables_df = spark.sql("SHOW TABLES")

all_tables = [
    row.tableName for row in tables_df.collect()
    if row.tableName.startswith(TABLE_PREFIX)
]

print(f"Found {len(all_tables)} tables with prefix '{TABLE_PREFIX}':")
for t in sorted(all_tables):
    print(f"  - {t}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2: Collect schemas and column profiles

# COMMAND ----------

# Schema: {table_name: {col_name: data_type}}
schemas = {}
# Column stats: {table_name: {col_name: {distinct, nulls, total, samples}}}
profiles = {}

for table in sorted(all_tables):
    print(f"\n{'='*60}")
    print(f"Profiling: {table}")
    print(f"{'='*60}")

    df = spark.table(table)
    row_count = df.count()
    schema_dict = {field.name: str(field.dataType) for field in df.schema.fields}
    schemas[table] = schema_dict

    print(f"  Rows: {row_count}")
    print(f"  Columns ({len(schema_dict)}):")

    col_profiles = {}
    for col_name, col_type in schema_dict.items():
        stats = df.select(
            F.countDistinct(F.col(col_name)).alias("distinct"),
            F.count(F.when(F.col(col_name).isNull(), 1)).alias("nulls")
        ).collect()[0]

        # Get up to 5 sample values
        samples = [
            str(row[0]) for row in
            df.select(col_name).where(F.col(col_name).isNotNull())
              .distinct().limit(5).collect()
        ]

        col_profiles[col_name] = {
            "type": col_type,
            "distinct": stats["distinct"],
            "nulls": stats["nulls"],
            "total": row_count,
            "samples": samples
        }
        print(f"    {col_name:40s} {col_type:20s} distinct={stats['distinct']:>8}  nulls={stats['nulls']:>8}  samples={samples[:3]}")

    profiles[table] = col_profiles

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3: Find candidate join keys (matching column names across tables)

# COMMAND ----------

# Build reverse index: {column_name: [list of tables that have it]}
column_to_tables = defaultdict(list)
for table, cols in schemas.items():
    for col_name in cols:
        column_to_tables[col_name].append(table)

# Filter to columns that appear in 2+ tables (candidate join keys)
candidate_keys = {
    col: tables for col, tables in column_to_tables.items()
    if len(tables) >= 2
}

print(f"Found {len(candidate_keys)} candidate join columns (appearing in 2+ tables):\n")
for col, tables in sorted(candidate_keys.items(), key=lambda x: -len(x[1])):
    print(f"  {col} ({len(tables)} tables):")
    for t in sorted(tables):
        p = profiles[t][col]
        print(f"    - {t:45s} distinct={p['distinct']:>8}  type={p['type']}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4: Validate joins — check value overlap between candidate key pairs

# COMMAND ----------

print("Validating join key overlaps...\n")

join_results = []

for col, tables in sorted(candidate_keys.items(), key=lambda x: -len(x[1])):
    # Compare each pair of tables sharing this column
    for i in range(len(tables)):
        for j in range(i + 1, len(tables)):
            t1, t2 = tables[i], tables[j]

            vals1 = spark.table(t1).select(col).where(F.col(col).isNotNull()).distinct()
            vals2 = spark.table(t2).select(col).where(F.col(col).isNotNull()).distinct()

            count1 = vals1.count()
            count2 = vals2.count()
            overlap = vals1.intersect(vals2).count()

            if overlap > 0:
                # Determine relationship direction
                pct1 = round(overlap / count1 * 100, 1) if count1 > 0 else 0
                pct2 = round(overlap / count2 * 100, 1) if count2 > 0 else 0

                if pct1 > 90 and pct2 < 50:
                    rel_type = f"{t1} N:1 {t2}"
                elif pct2 > 90 and pct1 < 50:
                    rel_type = f"{t2} N:1 {t1}"
                elif pct1 > 90 and pct2 > 90:
                    rel_type = "1:1"
                else:
                    rel_type = "M:N or partial"

                result = {
                    "column": col,
                    "table_1": t1,
                    "table_2": t2,
                    "distinct_t1": count1,
                    "distinct_t2": count2,
                    "overlap": overlap,
                    "pct_t1_in_t2": pct1,
                    "pct_t2_in_t1": pct2,
                    "relationship": rel_type
                }
                join_results.append(result)
                print(f"  {col}: {t1} <-> {t2}")
                print(f"    overlap={overlap}  |  {t1}: {pct1}% matched  |  {t2}: {pct2}% matched  |  {rel_type}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 5: Summary — Discovered Relationships

# COMMAND ----------

print("=" * 80)
print("DISCOVERED RELATIONSHIPS SUMMARY")
print("=" * 80)

# Sort by overlap strength
for r in sorted(join_results, key=lambda x: -x["overlap"]):
    print(f"\n  [{r['relationship']}] via '{r['column']}'")
    print(f"    {r['table_1']} ({r['distinct_t1']} distinct)")
    print(f"    {r['table_2']} ({r['distinct_t2']} distinct)")
    print(f"    Overlap: {r['overlap']} values ({r['pct_t1_in_t2']}% / {r['pct_t2_in_t1']}%)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 6: Export results as JSON (for local analysis)

# COMMAND ----------

output = {
    "tables": {t: list(cols.keys()) for t, cols in schemas.items()},
    "schemas": schemas,
    "profiles": {
        t: {c: {k: v for k, v in p.items() if k != "samples"}
            for c, p in cols.items()}
        for t, cols in profiles.items()
    },
    "candidate_keys": {col: tables for col, tables in candidate_keys.items()},
    "relationships": join_results
}

json_str = json.dumps(output, indent=2, default=str)

dbutils.fs.put("dbfs:/tmp/data_model_discovery.json", json_str, overwrite=True)
dbutils.fs.cp("dbfs:/tmp/data_model_discovery.json", "dbfs:/FileStore/data_model_discovery.json", True)

workspace_url = "https://adb-205203499382645.5.azuredatabricks.net"
print(f"\nDownload results: {workspace_url}/files/data_model_discovery.json")
print(">> Open this URL in your browser to download <<")
