# iMEP Gold KPI Tables: Schemas, Join Key and NULL Semantics

> Source: Genie question Q21 (2026-04-20). Status: confirmed.

## Question

What are the exact column schemas of the four per-mailing KPI tables found in [Q16](q16-imep-gold-inventory.md) ("Tier 3" in Q16 numbering, Tier 2 in [Q29](q29-gold-tier-structure.md))? Which key joins them to the mailing master, how complete is that join, and what do NULLs in the measures mean?

## Findings

### 1. Column schemas

**`tbl_pbi_mailingreciever_region`** — 6 columns, grain mailing × region × country

| Column | Type | Notes |
|---|---|---|
| `MailingId` | string | GUID, join key |
| `GCRS_REGION_NAME` | string | Region from the HR organisational hierarchy |
| `GCRS_CNTRY_NAME` | string | Country |
| `Count` | bigint | Recipients |
| `UniqueOpens` | bigint | Frequently NULL |
| `UniqueClicks` | bigint | Mostly NULL |

**`tbl_pbi_mailingreciever_division`** — 5 columns, grain mailing × business division

| Column | Type | Notes |
|---|---|---|
| `MailingId` | string | GUID, join key |
| `GCRS_BUSN_DIV_NAME` | string | Division name; can be NULL |
| `Count`, `UniqueOpens`, `UniqueClicks` | bigint | Same NULL semantics as the region table |

**`tbl_pbi_engagement`** — 9 columns, grain mailing × link / component. Note the **lower-case `mailingid`**.

| Column | Type | Notes |
|---|---|---|
| `mailingid` | string | GUID, join key — lower case, unlike every other table |
| `senderlanguage` | string | For example `English` |
| `linklabel` | string | For example a language label |
| `sourcecomponentname` | string | Template component, for example `footer` |
| `ComponentName` | string | Component name |
| `url` | string | Link target |
| `componentorder`, `Linkorder` | int | Position of component and link in the template |
| `Count` | bigint | Clicks; zero for links never clicked |

**`tbl_pbi_kpi`** — 8 columns, **long format**: one row per mailing × receiver status (and recipient language)

| Column | Type | Notes |
|---|---|---|
| `MailingId` | string | GUID, join key |
| `MailingTitle` | string | Free-text mailing title |
| `MailingCreatedBy` | string | **Clear-text person name** — personal data, keep out of outputs |
| `MailingReceiverStatus` | string | The measure dimension: `delivered`, `opened`, `clicked`, … |
| `OverallMailingStatus` | string | For example `Completed` |
| `StartsendDateTime` | timestamp | Send start |
| `measure` | bigint | Value for that status — sample values look like counts |
| `RecipientLanguage` | string | For example `Not set` |

### 2. Join key and match quality

All four tables join to `tbl_pbi_platform_mailings.Id` (= `tbl_email.Id`) through `MailingId`:

```sql
r.MailingId = m.Id   -- region, division, kpi
e.mailingid = m.Id   -- engagement (lower-case column name)
```

The case difference is irrelevant in Spark SQL (identifiers are case-insensitive) but matters once data leaves it — in pandas, in DuckDB with quoted identifiers, or with `spark.sql.caseSensitive` enabled.

Match rates against `tbl_pbi_platform_mailings` vary widely by table:

| Table | Match |
|---|---|
| `tbl_pbi_mailingreciever_region` | near-complete; small orphan share |
| `tbl_pbi_mailingreciever_division` | near-complete; small orphan share |
| `tbl_pbi_kpi` | near-complete; small orphan share |
| `tbl_pbi_engagement` | clearly lower; a substantial share of rows has no match |

The engagement orphans are explained by grain, not by defects: `tbl_pbi_engagement` also contains event ids (and possibly non-email items), which do not exist in the email-only `tbl_pbi_platform_mailings`. Q29 confirmed that the engagement table holds considerably more distinct ids than the mailing master has rows. (The Q21 transcript states the distinct-id comparison the other way round; the row counts of `tbl_pbi_platform_mailings` in Q16, Q27 and Q29 support the reading given here.)

### 3. NULL semantics

Genie's verdict: **the NULLs are semantic, not data-quality defects.**

| Table | Measure | NULLs | Zeros | Meaning |
|---|---|---|---|---|
| region | `Count` | never | a few | Recipients; can be 0 |
| region | `UniqueOpens` | a substantial minority | never | NULL = no tracked open; at least 1 when populated |
| region | `UniqueClicks` | the majority | never | NULL = no tracked click; sparser than opens, as expected |
| division | `Count` | never | a few | — |
| division | `UniqueOpens` | a substantial minority (more than region) | never | as region |
| division | `UniqueClicks` | the large majority | never | as region |
| engagement | `Count` | never | a large share | Template links that were never clicked |

The zeros in `tbl_pbi_engagement.Count` come from the build pattern that unions template links with click data so that unclicked links still appear (Pattern 3 in [imep-data-model.md](imep-data-model.md)).

### 4. Aggregation rule

Wrap the measures with `COALESCE(…, 0)` before aggregating. `SUM` alone skips NULLs, but a pack whose mailings are all NULL then returns NULL instead of 0, `AVG` skips the NULL rows and overstates the mean, and row-level arithmetic (rates per row, opens + clicks) turns NULL.

```sql
SELECT r.MailingId,
       SUM(r.Count)                       AS recipients,
       SUM(COALESCE(r.UniqueOpens, 0))    AS unique_opens,
       SUM(COALESCE(r.UniqueClicks, 0))   AS unique_clicks
FROM   imep_gold.tbl_pbi_mailingreciever_region r
GROUP BY r.MailingId
```

`tbl_pbi_kpi` must be pivoted before it can sit next to the wide tables. Because `RecipientLanguage` can split a status into several rows, sum rather than pick a single value:

```sql
SELECT MailingId,
       SUM(measure) FILTER (WHERE MailingReceiverStatus = 'delivered') AS delivered,
       SUM(measure) FILTER (WHERE MailingReceiverStatus = 'opened')    AS opened,
       SUM(measure) FILTER (WHERE MailingReceiverStatus = 'clicked')   AS clicked
FROM   imep_gold.tbl_pbi_kpi
GROUP BY MailingId
```

## Consequences

1. **Join pattern fixed:** `MailingId = tbl_pbi_platform_mailings.Id`; mind the lower-case `mailingid` in `tbl_pbi_engagement` outside Spark SQL.
2. **Region before division** as the primary audience aggregate: equally good match, finer geographic split, fewer NULLs.
3. **`tbl_pbi_kpi` needs a pivot** (`FILTER` or `CASE` on `MailingReceiverStatus`).
4. **`COALESCE(…, 0)` everywhere** before aggregating.
5. **Email-only analyses:** inner-join `tbl_pbi_engagement` to `tbl_pbi_platform_mailings` to drop event ids.
6. **Mailing level first.** Pack-level figures are sums over the mailings sharing a `tracking_pack_id`. Unique opens and clicks are unique per mailing, so their sum across mailings counts a person once per mailing, not once per pack. Summing across regions within one mailing is safe because the region split is disjoint.
7. **Personal data:** do not expose `MailingCreatedBy`.

Not answered by Q21, status today:

- Does `tbl_pbi_platform_mailings` carry `TrackingId`? → Yes, confirmed by Q29.
- Is `tbl_pbi_kpi.measure` a count or a rate? → Sample values suggest a count; not confirmed.
- Full list of `MailingReceiverStatus` values → open (relates to BRD OP-02).

## Related

- [q16-imep-gold-inventory.md](q16-imep-gold-inventory.md) · [q29-gold-tier-structure.md](q29-gold-tier-structure.md) · [q01b-imep-gold-layer.md](q01b-imep-gold-layer.md) · [imep-data-model.md](imep-data-model.md) · [q24-tracking-id-volume.md](q24-tracking-id-volume.md)
- [../diagrams/er_imep_gold.md](../diagrams/er_imep_gold.md) · [../tables/imep_gold/final.md](../tables/imep_gold/final.md) · [../joins/join_strategy_contract.md](../joins/join_strategy_contract.md)
- [../genie_questions_imep.md](../genie_questions_imep.md) — Q21 · [../sources.md](../sources.md#q21)
