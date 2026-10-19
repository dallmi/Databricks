# Pipeline Operations and Refresh Rhythm from Delta History

> Source: Genie question Q28 (2026-04-20). Status: partly validated — frequencies are observations from Delta table history, not a complete schedule; one table label in the output was wrong and was corrected by Q29.

## Question

Without access to the job scheduler, what does `DESCRIBE HISTORY` reveal about how the iMEP and SharePoint tables are written: operation type, frequency, incremental or full, batch or stream, and who writes?

The answer covered the Delta history of 11 tables.

## Findings

### 1. One operation rhythm everywhere

Every table follows the same sequence:

```
1. Data write   (MERGE / WRITE append / CREATE OR REPLACE TABLE AS SELECT)
2. OPTIMIZE     (often with Z-ORDER)
3. VACUUM START
4. VACUUM END
```

Retention is **7 days** (`604800000` ms).

### 2. iMEP Bronze — MERGE on `Id`, about twice a day

Classic upsert:

```
predicate            = (target.Id = source.Id)
matchedPredicates    = [update]
notMatchedPredicates = [insert]
```

| Table | Operation | Rows per write | Behaviour |
|---|---|---|---|
| `tbl_email` | MERGE | the whole table | Full-table upsert |
| `tbl_email_receiver_status` | MERGE | tens of millions | Bulk re-upsert of a large part of the table; described by Genie as full-table upsert |
| `tbl_analytics_link` | MERGE | thousands | **Truly incremental** — only new open/click events |

`tbl_analytics_link` is the only genuinely incremental iMEP table. `tbl_email` and `tbl_email_receiver_status` are called MERGE but behave like full replacement.

### 3. SharePoint Bronze — mixed mode, about once a day

| Table | Operation | Rows per write | Behaviour |
|---|---|---|---|
| `pages` | MERGE | the whole table | Daily snapshot replace |
| `pageviews` | WRITE (append) | hundreds | A handful of small appends within about one minute |

The `pageviews` bursts match an API pagination pattern: each API page becomes its own Delta write. Micro-batched, not streaming.

The append volume seen in the history excerpt is orders of magnitude too small to explain how large `pageviews` has grown since 2023. Either more writes run outside the observed window, or the excerpt showed only part of a day's loads — open.

### 4. iMEP Gold — full rebuild about twice a day

Every Gold table is written with `CREATE OR REPLACE TABLE AS SELECT` (CTAS), shortly after the Bronze merge. There is **no incrementality**: each cycle drops and recreates every table.

The largest Gold table (hundreds of millions of rows) is fully rebuilt about twice a day — **the biggest compute cost in the pipeline by a wide margin**.

**Label correction.** The Q28 output labelled this table `tbl_pbi_platform_mailings`, which Q27 had recorded with only tens of thousands of rows. Q29 confirmed that the big table is `imep_gold.final`.

The other Gold labels in the output (`tbl_pbi_mailings_region`, `tbl_pbi_mailings_division`, `tbl_pbi_analytics`, `tbl_pbi_kpi`, one unnamed) do not match the Q16/Q21 inventory either. By row size they correspond to `tbl_pbi_platform_mailings`, `tbl_pbi_mailingreciever_region`, `tbl_pbi_mailingreciever_division`, `tbl_pbi_kpi` and `tbl_pbi_engagement` — the labels look shifted by one row, with only `tbl_pbi_kpi` consistent. The names `tbl_pbi_mailings_region`, `tbl_pbi_mailings_division` and `tbl_pbi_analytics` appear in no other inventory; treat them as labelling errors until confirmed. (Our inference from sizes, not a Genie statement.)

### 5. Writer identity — a service principal

`userName` is empty in every history entry, so no human wrote to these tables. All writes come from **a service principal**, consistent with automated notebooks running under a credential configuration. No manual interventions are visible.

### 6. Summary

| Table | Layer | Operation | Frequency | Mode |
|---|---|---|---|---|
| `tbl_email` | iMEP Bronze | MERGE (full upsert) | ~2×/day | Batch |
| `tbl_email_receiver_status` | iMEP Bronze | MERGE (bulk upsert) | ~2×/day | Batch |
| `tbl_analytics_link` | iMEP Bronze | MERGE (incremental) | ~2×/day | Batch |
| `pages` | SharePoint Bronze | MERGE (full upsert) | ~1×/day | Batch |
| `pageviews` | SharePoint Bronze | WRITE (append) | ~1×/day | Micro-batch |
| `final` | iMEP Gold | CTAS (full rebuild) | ~2×/day | Batch |
| 5 further `tbl_pbi_*` tables | iMEP Gold | CTAS (full rebuild) | ~2×/day | Batch |

All writes by the service principal.

### 7. Visibility caveat

Concrete run times are visible only through `DESCRIBE HISTORY`, not through a scheduler we can see. There are jobs and notebooks outside our visibility. The frequencies above are observations, not a complete picture; these docs record frequencies but deliberately no clock times.

## Consequences

1. **Lineage sections in the table cards:** iMEP Bronze about twice a day (MERGE), SharePoint Bronze about once a day, iMEP Gold about twice a day (full CTAS rebuild).
2. **Freshness:** the iMEP side is at most about half a day old, the SharePoint side about a day.
3. **Refresh window:** each CTAS commit is atomic in Delta, so a single table is never half-written. The Gold tables are rebuilt one after another, though, so a query that joins several Gold tables during the rebuild window can combine old and new versions. Schedule dashboard refreshes outside that window. (Our reading of Delta semantics, not observed.)
4. **Provenance:** no human edits — data provenance is fully pipeline-controlled.
5. **Biggest optimisation lever (platform team, not us):** move Gold from full CTAS to incremental MERGE, as Bronze already does for `tbl_analytics_link`. That would cut compute, shorten rebuild windows and reduce OPTIMIZE/VACUUM load. The missing partitioning found in Q30 is the second lever ([q30-storage-architecture.md](q30-storage-architecture.md)).
6. **Do not rely on the Q28 Gold table names** other than `final` and `tbl_pbi_kpi`.

## Related

- [q29-gold-tier-structure.md](q29-gold-tier-structure.md) · [q30-storage-architecture.md](q30-storage-architecture.md) · [q26-imep-silver.md](q26-imep-silver.md) · [q27-imep-join-graph.md](q27-imep-join-graph.md) · [q16-imep-gold-inventory.md](q16-imep-gold-inventory.md) · [sharepoint-bronze-pages-and-pageviews.md](sharepoint-bronze-pages-and-pageviews.md)
- [../tables/imep_gold/final.md](../tables/imep_gold/final.md) · [../tables/imep/tbl_analytics_link.md](../tables/imep/tbl_analytics_link.md) · [../tables/sharepoint/pages.md](../tables/sharepoint/pages.md) · [../diagrams/er_imep_gold.md](../diagrams/er_imep_gold.md)
- [../genie_questions_imep.md](../genie_questions_imep.md) — Q28 · [../sources.md](../sources.md#q28)
