# `sharepoint_bronze.customevents`

> **Click and interaction telemetry** from Application Insights — every user click on the intranet (link, button, file download, video action). 262M rows in Bronze. Schema reverse-engineered from the sister projects [Clicks](../../../../Clicks) and [CampaignWe](../../../../CampaignWe), where the AppInsights stream is consumed directly via Kusto Query Language (KQL).

| | |
|---|---|
| **Layer** | Bronze |
| **Source system** | Application Insights `customEvents` table → Delta Bronze (presumed Append) |
| **Grain** | 1 row per user interaction event (click, search, video action) |
| **Primary key** | `Id` (App Insights event GUID) |
| **Cross-channel key** | **None directly** — must JOIN to `pages` via `pageId = pageUUID` |
| **Write pattern** | Append (presumed; not yet verified via `DESCRIBE HISTORY`) |
| **Approx row count** | **~262M** (~2× the size of `pageviews`) |
| **Historical snapshot** | `customevents_history` (13.3M, ad-hoc) |

---

## Source — App Insights `customEvents`

Application Insights is the upstream system. The Bronze table is a near-1:1 mirror.

**Top-level columns** (App Insights native):

| Column | Type | Notes |
|---|---|---|
| `timestamp` | datetime | UTC, microsecond precision |
| `id` | string | event GUID, becomes `Id` in Bronze |
| `name` | string | event type — see [Event Name Taxonomy](#event-name-taxonomy) |
| `user_Id` | string | App Insights anonymous visitor ID (NOT the GPN) |
| `session_Id` | string | App Insights session ID |
| `client_CountryOrRegion` | string | geographic info |
| `client_Type`, `client_OS`, `client_Browser` | string | device info |
| `customDimensions` | string (JSON) | nested JSON — see below |

**`customDimensions`** is a JSON string containing **exactly one key**: `CustomProps`. `CustomProps` is itself a JSON string and must be parsed twice:

```kql
| extend cd = todynamic(customDimensions)
| extend CP = todynamic(tostring(cd.CustomProps))
```

---

## `CustomProps` — business payload (for `name == 'click_event'`)

| Bucket | Keys |
|---|---|
| **Person** | `GPN` (8-digit), `Email` |
| **Page context** | `SiteID`, `SiteName`, `PageId`, `PageName`, `PageURL`, `PageStatus`, `ContentType`, `ContentOwner`, `NewsCategory`, `PublishingDate` |
| **Click detail** | `ComponentName`, `Link_Type`, `Link_label`, `Link_address`, `Link_ancestors` |
| **Download detail** | `FileType_Label`, `FileName_Label` |
| **Targeting (newer)** | `Theme`, `Topic`, `TargetOrganisation`, `TargetRegion`, `refUri` |
| **Video sub-domain** | `Video_Action`, `Video_Id`, `Video_Type`, `Video_Duration` |

> ⚠️ **`CammsTrackingID` is NOT in click_event CustomProps** (verified against the Clicks key inventory). Only `pageViews` carries it. Cross-channel attribution for clicks therefore must run through `pageId → pages.pageUUID → pages.UBSGICTrackingID`.

---

## Event Name Taxonomy

The `name` column splits the stream into multiple event families. The KQL exporter `Clicks/clicks_query.kql` filters on `click_event` only; `CampaignWe/campaignwe_query.kql` (Query F) shows that other families exist with deeper-nested CustomProps:

| Event family | Nesting | Example |
|---|---|---|
| `click_event` | flat (Level 2) | link clicks, button clicks, file downloads |
| `SEARCH_TRIGGERED`, `SEARCH_RESULT_CLICK` | nested (Level 3-4) | intranet search interactions |
| (other) | TBD | run [`customevents_clicks.kql` Query 1](../../../kql/customevents_clicks.kql) for the full inventory |

**Practical rule**: For click analytics, always filter `name == 'click_event'`. Other events have a different schema shape.

---

## Bronze schema (presumed)

The Bronze landing tracks only the App Insights envelope — `CustomProps` is **not yet flattened** in `sharepoint_bronze.customevents`. Flattening happens downstream (DuckDB pipelines in [Clicks](../../../../Clicks/process_clicks.py), [CampaignWe](../../../../CampaignWe/process_campaignwe.py); presumably also in `sharepoint_clicks_gold.pbi_db_ctalabel_intr_fact_gold` although the lineage is unverified).

| Column | Type | Notes |
|---|---|---|
| `Id` | string | PK, App Insights event GUID |
| `name` | string | event type — filter to `click_event` |
| `pageId` | string | FK → `pages.pageUUID` (to verify) |
| `EventName` | string | mirror of `name` (legacy) |
| `EventTime` / `timestamp` | timestamp | event time UTC |
| `customDimensions` | string (JSON) | raw JSON envelope, double-nested |

> ⚠️ **Open question** — does Bronze persist the full `customDimensions` JSON, or is it pre-flattened? Run `DESCRIBE sharepoint_bronze.customevents` to confirm. The ER diagram so far only lists `Id, pageId, EventName, EventTime`.

---

## Primary joins

### → `pages` (N:1) — to recover the cross-channel TrackingID

```sql
SELECT ce.timestamp, ce.user_Id, p.UBSGICTrackingID, p.PageURL, p.PageTitle
FROM   sharepoint_bronze.customevents ce
JOIN   sharepoint_bronze.pages         p ON p.pageUUID = ce.pageId
WHERE  ce.name = 'click_event'
  AND  p.UBSGICTrackingID IS NOT NULL
```

### → iMEP HR (N:1) — GPN to TNumber

The GPN sits inside `customDimensions.CustomProps.GPN` and is **not** a top-level column. Extract first, then bridge:

```sql
WITH ce_flat AS (
  SELECT ce.*,
         get_json_object(get_json_object(ce.customDimensions, '$.CustomProps'), '$.GPN') AS gpn
  FROM   sharepoint_bronze.customevents ce
  WHERE  ce.name = 'click_event'
)
SELECT f.*, hr.T_NUMBER
FROM   ce_flat                       f
JOIN   imep_bronze.tbl_hr_employee   hr ON hr.WORKER_ID = f.gpn
```

### → `sharepoint_clicks_gold.pbi_db_ctalabel_intr_fact_gold` (lineage unverified)

The CTA-clicks gold table aggregates per `marketingPageId × ctalabel`. The presumed source is `customevents` filtered on `name == 'click_event'` with `Link_Type = 'CTA'`, but neither the build SQL nor the filter has been verified. Genie question pending.

---

## Quality caveats

### ⚠️ User identity — three different IDs, same person

| ID | Source | Cardinality | Use |
|---|---|---|---|
| `user_Id` | App Insights anonymous | very high (often per-device) | NOT a person ID |
| `GPN` | CustomProps | 8-digit, one per employee | the real person ID — but inside JSON |
| `session_Id` | App Insights | per session | window key |

Clicks/clicks_explorer.kql Query 0a explicitly compares these — `user_Id` cardinality is typically 1.5-3× `GPN`, because the same employee has multiple devices/browsers.

### ⚠️ No partition pruning

Bronze table is unpartitioned (consistent with all Bronze tables we've audited — see `storage_architecture_q30_findings`). Always filter on `timestamp` first to limit scan cost.

### ⚠️ `CustomProps` not flattened in Bronze

Every analytical query has to double-parse JSON. A `sharepoint_silver.customevents_flat` (analogous to `sharepoint_silver.webpageviewed`) does **not yet exist** — verified absent from the silver inventory in `imep_silver_q26_findings`.

### Event-family schema divergence

Search events (`SEARCH_*`) carry a deeper-nested CustomProps (Level 3-4 JSON). Any flattener that supports both click and search events must walk all 4 levels — see `CampaignWe/campaignwe_query.kql` Query F for the canonical recipe.

---

## Lineage

```
App Insights (Azure)
        │  customEvents stream
        │  (timestamp, name, user_Id, session_Id, customDimensions)
        ▼
sharepoint_bronze.customevents          (262M, presumed Append)
        │
        ├──[double-parse JSON]──> Clicks pipeline  → DuckDB → fact_clicks.parquet
        ├──[double-parse JSON]──> CampaignWe pipeline → DuckDB → events_anonymized.parquet
        └──[unverified]──────────> sharepoint_clicks_gold.pbi_db_ctalabel_intr_fact_gold (3M)
```

Raw export path for ad-hoc analysis: see [`kql/customevents_clicks.kql`](../../../kql/customevents_clicks.kql).

---

## Related cards

- [pages.md](pages.md) — the dimension to attach `UBSGICTrackingID`
- `pageviews.md` *(pending)* — sister fact (173M, has `GICTrackingID` directly)
- `pbi_db_ctalabel_intr_fact_gold.md` *(pending)* — gold CTA aggregate, lineage unverified

---

## External references (sister projects)

- [/Users/micha/Documents/Arbeit/Clicks/clicks_query.kql](../../../../Clicks/clicks_query.kql) — master flattener for ALL intranet clicks
- [/Users/micha/Documents/Arbeit/Clicks/clicks_explorer.kql](../../../../Clicks/clicks_explorer.kql) — 13 profiling queries (event names, key inventory, link type distribution, daily trend)
- [/Users/micha/Documents/Arbeit/Clicks/docs/data-pipeline.md](../../../../Clicks/docs/data-pipeline.md) — full pipeline + CDM star-schema design (`fact_clicks`, `dim_site`, `dim_page`, `dim_link_type`, `dim_component`)
- [/Users/micha/Documents/Arbeit/CampaignWe/campaignwe_query.kql](../../../../CampaignWe/campaignwe_query.kql) — page-filtered variant + 4-level nesting recipe for search events
- [/Users/micha/Documents/Arbeit/ClickTracking/Click_Tracking_Requirements_Clean.md](../../../../ClickTracking/Click_Tracking_Requirements_Clean.md) — business requirements (no schema)

---

## References

- [er_sharepoint_bronze.md](../../diagrams/er_sharepoint_bronze.md) — bronze topology
- [knowledge_base.md](../../knowledge_base.md) — table catalog
- Memory: `appinsights_source.md`, `sharepoint_pages_inventory.md`

---

## Open questions

1. **Bronze schema verification** — does `sharepoint_bronze.customevents` persist the raw `customDimensions` JSON, or is it already flattened? `DESCRIBE` on Genie pending.
2. **Lineage to `pbi_db_ctalabel_intr_fact_gold`** — what is the exact CTAS / MERGE SQL? Filter on `Link_Type` or on `name`?
3. **`pageId` ↔ `pageUUID` join validity** — the FK relationship is presumed but not yet verified end-to-end (match rate unknown).
4. **`name`-value inventory** — beyond `click_event` and `SEARCH_*`, what else? Run [`customevents_clicks.kql` Query 1](../../../kql/customevents_clicks.kql) against the Bronze copy.
