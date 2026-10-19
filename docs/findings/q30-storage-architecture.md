# Storage Architecture and Physical Layout

> Source: Genie question Q30 (2026-04-20). Status: confirmed.

## Question

Without a Unity Catalog lineage graph, what does the physical layout say about the pipelines? For every table in `imep_bronze`, `imep_gold`, `sharepoint_bronze` and `sharepoint_gold`, run `DESCRIBE EXTENDED` and read storage location, table type, properties, partitioning and creating runtime. The Azure Data Lake Storage (ADLS) path often encodes the producing pipeline.

Storage account names are internal infrastructure; this page uses placeholders: `<imep-bronze-acc>`, `<sp-bronze-acc>`, `<gold-acc>`.

## Findings

### 1. Overview

**114 external Delta tables in 4 schemas, on 3 ADLS storage accounts with one container each.** Bronze ingestion and gold consumption are clearly separated:

| Storage account (placeholder) | Container | Schemas | Purpose |
|---|---|---|---|
| `<imep-bronze-acc>` | bronze layer | `imep_bronze` | iMEP (internal mailing and event platform) email, event and human resources (HR) bronze tables |
| `<sp-bronze-acc>` | bronze layer | `sharepoint_bronze` | SharePoint bronze ingestion |
| `<gold-acc>` | gold layer | `imep_gold`, `sharepoint_gold` | **All gold tables co-located** |

- All 114 tables are **external** Delta tables.
- The owner field reports a user account.
- Created by an older Spark 3.2 runtime — except the video tables.
- **No partitioning on any table.**
- **No table comments** — except the three video tables.

### 2. Path patterns as pipeline fingerprints

#### iMEP bronze (49 tables)

```
abfss://<container>@<imep-bronze-acc>/.../imep/<TABLE_NAME>
```

- Upper-case folder names equal the table names: `/imep/TBL_EMAIL`, `/imep/TBL_ANALYTICS_LINK`.
- HR tables share the namespace: `/imep/TBL_HR_USER`, `/imep/TBL_HR_EMPLOYEE`, `/imep/TBL_HR_COSTCENTER`.
- Flat structure, no sub-pipelines, no staging folders.

→ **One ingestion job writes all iMEP bronze tables.**

#### iMEP gold (31 tables)

```
abfss://<container>@<gold-acc>/.../final
abfss://<container>@<gold-acc>/.../tbl_pbi/<table_name>
abfss://<container>@<gold-acc>/.../imep/tbl_active_employee_month
```

- `/final` — a dedicated path for the tier-0 atomic fact (hundreds of millions of rows).
- `/tbl_pbi/...` — all other gold tables (`tbl_pbi_engagement`, `tbl_pbi_kpi`, …), with mixed-case paths such as `tbl_pbi_Date15D`, `tbl_pbi_DivArea72`.
- One outlier: `tbl_active_employee_month` sits under `/imep/` instead of the root.
- No pipeline or domain subfolders; every table is written with Create Table As Select (CTAS).

→ **iMEP gold is produced by one CTAS-based job, not by a modular pipeline framework.**

#### SharePoint bronze (12 tables plus snapshots)

```
abfss://<container>@<sp-bronze-acc>/.../sharepoint/<table_name>
```

- Lower-case folder names: `/sharepoint/pages/`, `/sharepoint/customevents/`, `/sharepoint/pageviews/`.
- Historical snapshot folders: `/sharepoint/pageviews_09_27_2023`, `/sharepoint/pageviews_08022024`, `/sharepoint/pageviews_07_jul_till_17_oct`.
- Flat structure, no sub-pipelines.

→ **One ingestion pipeline, with ad-hoc snapshot folders.**

#### SharePoint gold — two pipeline families

**Family 1 — employee analytics (16 tables)**

```
abfss://<container>@<gold-acc>/.../employee_analytics/pbi_db_*
```

- Examples: `/employee_analytics/pbi_db_interactions_metrics`, `/employee_analytics/pbi_db_datewise_overview_fact_tbl`.
- Minimal Delta properties: `delta.minReaderVersion = 1`, `delta.minWriterVersion = 2`, `external = true`.
- Same Spark 3.2 runtime and style as iMEP gold; no comments, no pipeline metadata.

→ **Notebook-based CTAS, not a managed pipeline.**

**Family 2 — video analytics (3 tables)**

```
abfss://<container>@<gold-acc>/.../sharepoint_gold/sharepoint_gold/pbi_db_*
```

- Tables: `pbi_db_dim_video_inventory_gold`, `fact_video_engagement_gold`, `fact_video_engagement_datewise_gold`.
- Declarative Pipeline properties present: `pipelines.pipelineId = <pipeline-id>`, `pipelines.metastore.tableName`, `quality = gold`, `delta.enableChangeDataFeed = true`.
- Created by newer Spark runtimes (3.5 and 4.0); proper table comments.

→ **The only tables produced by a managed Declarative Pipeline.**

#### Views without storage

`pbi_db_page_visitedkey_view` and `pbi_db_website_page_view` have no ADLS location — logical views only.

### 3. Key findings

1. **Pipeline reality check.** Only the 3 video tables come from a Declarative Pipeline; the other 111 are written by notebook jobs without pipeline metadata. The folder layout suggests two or three large orchestrating notebooks, not a layered framework.
2. **Zero partitioning anywhere — the largest structural performance gap.** It hurts most on `imep_gold.final` (hundreds of millions of rows, rebuilt by CTAS twice a day) and `sharepoint_gold.pbi_db_interactions_metrics` (tens of millions of rows, rebuilt the same way). Dashboards scan the full table even for narrow date windows. Partitioning by date (`event_date`, `visitdatekey`) would cut scan input/output, cut rebuild cost and improve Power BI query latency.
3. **Gold co-location is deliberate and correct.** `imep_gold` and `sharepoint_gold` sit on the same gold storage account. Cross-system joins run inside Spark or Microsoft Fabric without cross-account access, and Power BI models need no cross-account authentication.
4. **Gold is rebuilt wholesale, not incrementally.**

## Consequences

1. **Cross-channel joins are architecturally supported:** both gold schemas are on one account, so the email ↔ intranet join needs no cross-account authentication.
2. **Time-filter discipline is mandatory.** Without partitions every query is a full scan; the final dashboard card and the quality caveats must say so.
3. **The pipeline fingerprint explains Q28.** Notebook CTAS on an older runtime for 111 tables explains the full-rebuild behaviour and the absence of incrementality seen in the Delta history ([q28-pipeline-operations.md](q28-pipeline-operations.md)).
4. **Ignore the SharePoint bronze snapshot folders** (`pageviews_09_27_2023` and similar) for analytics. They are frozen point-in-time dumps, not part of a structured retention scheme.
5. **Document patterns, never account names.** Storage paths in this repository always use placeholders.

## Related

- [q28-pipeline-operations.md](q28-pipeline-operations.md) — write rhythm and rebuild frequency from the Delta history
- [q29-gold-tier-structure.md](q29-gold-tier-structure.md) — tier structure, including the video sub-domain
- [q26-imep-silver.md](q26-imep-silver.md) — why email engagement skips silver
- [q17-sharepoint-gold-inventory.md](q17-sharepoint-gold-inventory.md) — schema and table inventory
- [../tables/imep_gold/final.md](../tables/imep_gold/final.md) · [../tables/sharepoint_gold/pbi_db_interactions_metrics.md](../tables/sharepoint_gold/pbi_db_interactions_metrics.md)
- [../architecture_diagram.md](../architecture_diagram.md)
- [../genie_questions_imep.md](../genie_questions_imep.md) — Q26 to Q30 · [../sources.md](../sources.md#q30)
