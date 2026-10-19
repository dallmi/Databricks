# Tracking ID Format: Same Format, Disjoint Values, Pack-Level Match

> Source: Genie question Q23 (2026-04-20), with a business refinement the same day. Status: confirmed.

## Question

Do `imep_gold.tbl_pbi_platform_mailings.TrackingId` (email side) and `sharepoint_bronze.pages.__ORG__GICTrackingID` (intranet side) share the same format — length, segment structure, case, whitespace — and how many values appear in both? The comparison used 100 sample values per side; Genie returned only structural results, no values.

## Findings

### 1. Structure: identical

| Check | Result |
|---|---|
| Length | **32 characters** on both sides, no exceptions in the sample |
| Segments | 5 segments, dash-separated: `5 + 1 + 7 + 1 + 6 + 1 + 7 + 1 + 3 = 32` |
| Case | Entirely upper case on both sides |
| Whitespace | None — no leading, trailing or internal blanks |
| Outlier | The `pages` sample contained a single malformed value with only 2 segments |

| Segment | Length | Class | Meaning |
|---|---|---|---|
| 1 | 5 | letters | Cluster (campaign group) |
| 2 | 7 | digits | Pack number (zero-padded sequence) |
| 3 | 6 | digits | Activity date `YYMMDD` |
| 4 | 7 | digits | Activity sequence number |
| 5 | 3 | letters | **Channel suffix** — owned by the producing system |

Synthetic example: `ABCDE-0000123-240709-0000045-EMI`.

### 2. Values: almost disjoint

Despite the identical format, the value sets are almost disjoint: only a handful of exact values appear in both tables. A naive `platform_mailings.TrackingId = pages.__ORG__GICTrackingID` join returns practically nothing. The tracking id is **not a direct foreign key** between the two tables.

### 3. Why: each system has its own channel suffixes

| Suffix | Email side (iMEP, the internal mailing and event platform) | Intranet side (SharePoint) | Meaning |
|---|---|---|---|
| `EMI` | **large majority** | a handful | Email |
| `NLI` | small share | — | Newsletter |
| `TWE` | small share | — | Twitter / social |
| `IAN` | — | **largest share** | Intranet announcement |
| `ITS` | a handful | **large share** | Intranet site |
| `OPN` | — | sizeable share | Open page |
| `ANC` | a handful | small share | Announcement |

The few overlapping values are rare edge cases (`ITS`, `ANC` or `EMI` ids present in both systems).

### 4. Correct linking rule: pack level (segments 1–2) only

The tracking id is a **shared identifier format, not a shared identifier space**: `CLUSTER-PACK-YYMMDD-ACTIVITY-CHANNEL`.

The raw Genie answer proposed matching on segments 1–4 and ignoring the channel. The business refinement (2026-04-20) rejects that:

- **Segment 3** is the activity's creation date. An email goes out on `240709`, the related intranet page goes live on `240715` → different segment 3.
- **Segment 4** is counted up per activity within the pack. Email and page have their own sequence numbers → different segment 4.
- **Segment 5** is system vocabulary (iMEP: `EMI`/`NLI`/`TWE`; SharePoint: `IAN`/`ITS`/`OPN`/`ANC`) → always divergent.

A match on segments 1–4 would therefore treat related activities as unrelated. Only **cluster + pack number** binds the email activity and the page activity of the same pack, independent of timing and channel:

```sql
ON array_join(slice(split(UPPER(m.TrackingId), '-'), 1, 2), '-')
 = array_join(slice(split(UPPER(p.__ORG__GICTrackingID), '-'), 1, 2), '-')
```

Segments 1–3 (with date) stay useful for drill-down **within one system** ("when did the emails of this pack go out"), never for the cross-channel match.

This avoids false non-matches, false referential-integrity assumptions and over-restrictive joins that collapse coverage towards zero.

## Consequences

1. **`tracking_pack_id` = segments 1–2** is the only correct cross-channel match. Recipe: [cross_channel_via_tracking_id.md](../joins/cross_channel_via_tracking_id.md).
2. **Normalisation is optional.** The format is already upper case and clean; `UPPER(TRIM(…))` stays as cheap defensive code.
3. **SharePoint channel filter:** `RIGHT(__ORG__GICTrackingID, 3) IN ('IAN', 'ITS', 'OPN', 'ANC')` covers the large majority of tracked pages. The few `EMI`-suffixed pages may be email landing pages or noise — to verify.
4. **The suffix becomes a dimension** (in a pack dimension, should a silver layer be built): `tracking_channel_abbr` (`EMI`/`NLI`/`TWE`/`IAN`/`ITS`/`OPN`/`ANC`) and `tracking_channel_class` (Email / Newsletter / Social / Intranet announcement / Intranet site / Open page / Announcement).
5. **Pack-level coverage is far higher** than full-string coverage. How many packs actually appear on both sides is still to be measured (see [Q24](q24-tracking-id-volume.md) and [Q25](q25-sharepoint-pages-coverage.md)).

Timeline: original Q23 answer "match on segments 1–4, ignore segment 5" → business refinement 2026-04-20 "pack level (segments 1–2) only". The join docs, entity-relationship diagrams and the FitNesse mirror were rewritten accordingly.

## Related

- [../joins/cross_channel_via_tracking_id.md](../joins/cross_channel_via_tracking_id.md) · [../joins/join_strategy_contract.md](../joins/join_strategy_contract.md)
- [camms-tracking-id.md](camms-tracking-id.md) — segment names and the planning-side origin of the id
- [q01-q03-imep-schemas-and-tracking-id.md](q01-q03-imep-schemas-and-tracking-id.md) — where the tracking id lives in iMEP
- [q24-tracking-id-volume.md](q24-tracking-id-volume.md) · [q25-sharepoint-pages-coverage.md](q25-sharepoint-pages-coverage.md) · [q27-imep-join-graph.md](q27-imep-join-graph.md)
- [../tables/sharepoint/pages.md](../tables/sharepoint/pages.md) · [../tables/imep/tbl_email.md](../tables/imep/tbl_email.md)
- [../genie_questions_imep.md](../genie_questions_imep.md) — Q23 · [../sources.md](../sources.md#q23)
