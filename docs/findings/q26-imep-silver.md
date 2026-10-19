# Silver Layer: SharePoint Has One, iMEP Email Does Not

> Source: Genie question Q26 (2026-04-20). Status: confirmed.

## Question

Without Unity Catalog lineage, can schema naming reveal an intermediate (silver) layer? Is there a silver layer for iMEP email engagement that a cross-channel model should read instead of Bronze or Gold?

## Findings

### 1. Only one intermediate-layer marker: `_silver`

A scan across all schemas found 17 schemas with the suffix `_silver` and **no** schema named with `stage`, `staging`, `curated`, `std`, `standardized`, `intermediate`, `harmonized`, `conformed` or `enriched`. Where a silver layer exists, it is the only step between Bronze and Gold.

| Schema | Tables | Domain |
|---|---|---|
| `adobe_silver` | 15 | Adobe Analytics |
| `dynamics_silver` | 7 | Dynamics CRM (customer relationship management) |
| `email_campaign_forms_silver` | 5 | Forms and lead capture |
| `sharepoint_silver` | 5 | SharePoint interactions |
| `profile_silver` | about 6 | Identity resolution and marketing profiles |
| `adform_silver` | 3 | Display advertising |
| `linkedin_silver` | 0–2 | Social |
| `google_silver`, `facebook_silver`, `oii_silver` | 2 each | Advertising and social |
| `bid_silver`, `persona_data_silver` | 1 each | Various; `persona_data_silver` for identity resolution |
| `imep_silver` | 3 | **Events and registrations only** |

### 2. `sharepoint_silver` is the SharePoint intermediate

Cleaned and standardised interactions derived from `sharepoint_bronze`:

| Table | Size | Role |
|---|---|---|
| `webpageviewed` | hundreds of millions of rows | Fact |
| `pageviewed` | over a hundred million rows | Fact |
| `pageposted` | over a hundred million rows | Fact |
| `webpage` | — | Dimension |
| `website` | — | Dimension |

### 3. `imep_silver` covers events, not email

| Table | Size | Role |
|---|---|---|
| `invitation` | — | Event invitations |
| `eventregistration` | over ten million rows | Registration fact |
| `event` | tens of thousands of rows | Event master |

There is **no silver layer for email engagement**.

### 4. Email engagement goes straight from Bronze to Gold

```
imep_bronze.tbl_email                  ┐
imep_bronze.tbl_email_receiver_status  ├──► imep_gold.final  (hundreds of millions of rows)
imep_bronze.tbl_analytics_link         ┘
```

`imep_gold.final`:

- is a **denormalised join directly over the Bronze tables**,
- gets its **HR enrichment late**, in Gold, not in an intermediate step,
- is very wide and very large by design,
- is the only email fact — **no reusable intermediate email facts exist**.

This explains why `final` is so large, why email analytics behave differently from SharePoint analytics, and why reuse is easy on the SharePoint side but hard for email.

### 5. The medallion pattern is asymmetric

| Pattern | Used by |
|---|---|
| Bronze → Silver → Gold | SharePoint, Adobe, Dynamics, advertising |
| Bronze → Gold (silver skipped) | iMEP email engagement |

Not used anywhere: staging layers, curated or harmonised layers, conformed dimensions across domains.

## Consequences

1. **Stop planning `silver.fact_email`.** The iMEP platform team deliberately did not build one. Email engagement is consumed from `imep_gold.final` (atomic) or from the Gold KPI tables ([q21-imep-gold-tier3-schemas.md](q21-imep-gold-tier3-schemas.md)).
2. **SharePoint side:** for event-level analysis prefer `sharepoint_silver.webpageviewed` / `pageviewed` over raw `sharepoint_bronze.pageviews`. For dashboard metrics the Gold metric tables are the consumption layer ([q22-sharepoint-gold-schemas.md](q22-sharepoint-gold-schemas.md)).
3. **Events side:** prefer `imep_silver.eventregistration` over Bronze joins.
4. **Our own "silver", if built at all,** shrinks to a harmonisation layer: reconcile column names (`TrackingId` vs `__ORG__GICTrackingID`) and produce the pack dimension.
5. **Cross-channel SQL starts at `imep_gold.final`** on the email side, not at Bronze.

Follow-ups and their status:

- Exact name of the big Gold table → confirmed as `imep_gold.final` by Q29 ([q29-gold-tier-structure.md](q29-gold-tier-structure.md)).
- Grain of `final` → `mailing × recipient × event × hour` (Q29). The full column list is still open.
- Column schemas of the three `sharepoint_silver` fact tables → open.

## Related

- [q27-imep-join-graph.md](q27-imep-join-graph.md) · [q28-pipeline-operations.md](q28-pipeline-operations.md) · [q29-gold-tier-structure.md](q29-gold-tier-structure.md) · [q30-storage-architecture.md](q30-storage-architecture.md) · [q16-imep-gold-inventory.md](q16-imep-gold-inventory.md) · [q17-sharepoint-gold-inventory.md](q17-sharepoint-gold-inventory.md)
- [../tables/imep_gold/final.md](../tables/imep_gold/final.md) · [../tables/imep/tbl_event.md](../tables/imep/tbl_event.md) · [../architecture_diagram.md](../architecture_diagram.md)
- [../genie_questions_imep.md](../genie_questions_imep.md) — Q26 · [../sources.md](../sources.md#q26)
