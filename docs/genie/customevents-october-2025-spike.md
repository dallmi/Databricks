# Genie prompt: customevents October 2025 spike

Status: open, 2026-09-16.

Follow-up to a monthly count on `sharepoint_bronze.customevents`. Three
findings need explaining:

- October 2025 holds roughly 45x a normal month, within six days
- No rows after 2025-10-06
- The table grew since April 2026 although nothing new was loaded

Paste the block below into Genie. If Genie cannot see `hive_metastore`
tables, run the same questions as SQL cells in a notebook.

```
Context: We have no Unity Catalog. Everything lives in hive_metastore.
Use only two-part names (schema.table). Do not use information_schema,
system.* tables or UC lineage — they do not exist here. For metadata,
use DESCRIBE HISTORY, DESCRIBE DETAIL and SHOW TABLES.

Table under investigation: sharepoint_bronze.customevents

Known so far (monthly count on timestamp):
- Aug 2024 to Sep 2025: a stable monthly volume, all rows
  name = 'click_event'
- Oct 2025: roughly 45x a normal month, all between 2025-10-01 and
  2025-10-06 05:56 UTC
- No rows after 2025-10-06
- The total row count is clearly higher than in April 2026

Please answer these, each with the SQL you ran and a small result table:

1. Edges: the row count and min/max timestamp for rows with
   timestamp < '2024-08-01' and for rows with timestamp IS NULL.

2. The October 2025 spike:
   a) Rows per day and per hour for 2025-09-28 to 2025-10-06.
   b) For the same window: count(*), count(DISTINCT id), and how many
      ids occur more than once. Show the top 5 ids by count, with
      min/max timestamp.
   c) Are the spike rows full copies of earlier rows? Take 1,000 ids
      from October 2025 and check whether the same id exists with a
      timestamp before 2025-10-01.
   d) Distribution of the top 10 values for name, url and
      session_Id in the spike window.

3. Load history: DESCRIBE HISTORY sharepoint_bronze.customevents.
   List every operation since 2025-09-01 with timestamp, operation,
   operationParameters (mode, e.g. Overwrite/Append) and
   operationMetrics (numOutputRows, numAddedFiles, numRemovedFiles).
   When did the row count grow to its current size?

4. Why no data since October 2025:
   a) max(timestamp) of sharepoint_bronze.pageviews,
      sharepoint_bronze.customevents_history and
      sharepoint_silver.webpagevisited.
   b) SHOW TABLES IN sharepoint_bronze and IN sharepoint_silver.
      Is there any table (new name, suffix, date) that holds click
      events after 2025-10-06?
   c) DESCRIBE DETAIL sharepoint_bronze.customevents: location,
      lastModified, numFiles, sizeInBytes.

5. customevents_history: row count, min/max timestamp, and how many
   of its ids also appear in customevents.

Keep result tables short (max 20 rows), no charts. Reply with a
one-paragraph summary of what you think caused the October spike and
the stop after 2025-10-06.
```

## Caveats

- Without Unity Catalog there is no lineage and no audit log. The Delta
  log (`DESCRIBE HISTORY`) is the only record of reloads, and it keeps
  30 days by default. Question 3 may come back empty.
- The `id` column name comes from the source telemetry schema and is not
  yet confirmed for Bronze.
