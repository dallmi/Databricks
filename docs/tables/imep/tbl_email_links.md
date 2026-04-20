# `imep_bronze.tbl_email_links`

> **Template URL inventory.** Which links a mailing template contains. **Not** for funnel metrics — use `tbl_analytics_link` for that. Lean schema (10 columns), static template definition.

| | |
|---|---|
| **Layer** | Bronze |
| **Source system** | iMEP (SQL Server) → Change Data Capture (CDC) → Delta Bronze |
| **Grain** | 1 row per URL per email template |
| **Primary key** | `Id` |
| **FK** | `EmailId` → `tbl_email.Id` |
| **Write pattern** | MERGE |
| **Approx row count** | ~(medium order of magnitude — several links per mailing × 145K mailings) |

---

## Key Columns

| Column | Type | Role | Notes |
|---|---|---|---|
| `Id` | string | **PK** | Unique per template link |
| `EmailId` | string | **FK** → `tbl_email.Id` | Which mailing |
| `ElementValueId` | string | | Template element reference |
| `Url` | string | | Plain-text URL |
| `LinkLabel` | string | | Display label of the link |

---

## Distinction: `tbl_email_links` vs `tbl_analytics_link`

| | `tbl_email_links` | `tbl_analytics_link` |
|---|---|---|
| **What** | Template definition (static) | Event fact (dynamic) |
| **Grain** | Per link per mailing template | Per click/open event |
| **Rows** | Low (~mailings × ~5 links) | **533M** |
| **For** | "Which URLs are in mailing X?" | "Who clicked which link when?" |

**The most common confusion in the project.** Mnemonic: `_links` = template, `analytics_` = fact.

---

## Primary joins

### → `tbl_email` (N:1) — Mailing context

```sql
SELECT el.Url, el.LinkLabel, e.TrackingId, e.Title
FROM   imep_bronze.tbl_email_links el
JOIN   imep_bronze.tbl_email        e ON e.Id = el.EmailId
WHERE  e.TrackingId IS NOT NULL
```

### → `tbl_analytics_link` (N:1 via ElementValueId) — Click attribution per link

```sql
-- Hypothetical — exact join key via ElementValueId to be verified
SELECT el.Url, COUNT(al.Id) AS clicks
FROM   imep_bronze.tbl_email_links     el
LEFT JOIN imep_bronze.tbl_analytics_link al ON al.EmailId = el.EmailId
                                             AND al.LinkTypeEnum = 'CLICK'
                                             /* AND al.<?> = el.ElementValueId -- TBD */
GROUP BY el.Url
```

---

## Quality caveats

- **No direct FK to `tbl_analytics_link`**: Click events reference `EmailId` and `TNumber`, but not directly `tbl_email_links.Id`. Link-specific click attribution has to run via `ElementValueId` or URL match — **not yet validated** (follow-up).
- **Static schema** — URL does not change after publication. Redirect resolution (e.g. shortlinks) happens **externally** (newsletter tool) and is not visible in this table.

---

## References

- [tbl_email.md](tbl_email.md)
- [tbl_analytics_link.md](tbl_analytics_link.md) — Fact table for click events
- [er_imep_bronze.md](../../diagrams/er_imep_bronze.md)

---

## Sources

Genie sessions backing the statements on this page: [Q30](../../sources.md#q30). See [sources.md](../../sources.md) for the full index.
