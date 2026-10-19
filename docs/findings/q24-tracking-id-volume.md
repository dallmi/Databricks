# Tracking ID Volume and the Pack Universe (Email Side)

> Source: Genie question Q24 (2026-04-20). Status: confirmed.

## Question

In `imep_gold.tbl_pbi_platform_mailings`: (a) how many mailings carry a `TrackingId`; (b) how has that developed by creation year; (c) how are the channel suffixes distributed; (d) how many distinct packs (segments 1–2) remain after filtering on the email suffix `EMI`? The answer sizes the email page of the cross-channel dashboard. (iMEP is the internal mailing and event platform.)

## Findings

### (a) Population: the tracking id is the exception

Only **a small minority of mailings (low single-digit percent)** carry a `TrackingId`; the rest are NULL or empty. By default, any analysis that relies on the tracking id covers only that small minority of the mailing universe.

### (b) Adoption: starts in 2024, ramps up steeply

| Year | Mailings with tracking id |
|---|---|
| before 2024 | practically none |
| 2024 | first adoption, low volume |
| 2025 | several times the 2024 volume |
| 2026 (first months) | annualised run rate above 2025 |

Before 2024 the tracking id practically does not exist on the email side.

### (c) Suffixes: email dominates

| Suffix | Share | Meaning |
|---|---|---|
| `EMI` | **large majority** | Email |
| `TWE` | small share | Twitter / social |
| `NLI` | small share | Newsletter |
| `ITS`, `ANC` | a handful each | Intranet suffixes appearing on the email side — rare overlaps, ignorable |
| `TOW`, `EVT`, `TV`, `VV`, `SUR` | a handful at most | Edge cases, not dashboard-relevant |

Genie labelled some suffixes speculatively in this answer (e.g. `ITS` as "video message", `ANC` as "intranet site"). **The [Q23](q23-tracking-id-format.md) taxonomy is authoritative**: `IAN`/`ITS`/`OPN`/`ANC` are SharePoint suffixes.

### (d) The pack universe: a few dozen packs

The funnel from mailing rows to packs, after `RIGHT(TrackingId, 3) = 'EMI'`:

| Level | Order of magnitude |
|---|---|
| `EMI` mailing rows | hundreds |
| Distinct `EMI` tracking ids | hundreds (slightly fewer than rows) |
| **Distinct packs (segments 1–2)** | **a few dozen** |

Each pack bundles many email variants — languages, A/B versions, resends. These packs are the stable "campaign brackets" that allow cross-system analysis.

## Consequences

1. **The dashboard's pack-level aggregate has a few dozen rows** — not tens of thousands of mailings, not hundreds of tracking ids. Small, fast to load, easy to show in full.
2. **Default time window on the email side: from 2025-01-01.** Earlier data is noise. *Superseded for the combined dashboard by the SharePoint rollout floor 2024-09-01 from [Q25](q25-sharepoint-pages-coverage.md).*
3. **Pack aggregation collapses variants automatically.** If a breakdown per variant (language, A/B, resend) is wanted, keep segments 3–4 as separate attributes in the email fact.
4. **Honest cross-channel coverage** is now one question: *how many of these email packs have a SharePoint page?* That needs the intersection of the email pack ids with the pack ids from `pages.__ORG__GICTrackingID` (segments 1–2).
5. **Funnel reality:** packs with a SharePoint counterpart get the full funnel (sent → opened → clicked → viewed); the others are email-only. That is not a defect — it has to be communicated.

Open at the time and still pending:

- How many distinct pack ids does `sharepoint_bronze.pages.__ORG__GICTrackingID` hold after the segments 1–2 cut? (Partly answered by Q25 for the main site.)
- How many of them intersect with the email `EMI` packs?
- How does the intersection spread over 2024, 2025 and 2026?

## Related

- [q23-tracking-id-format.md](q23-tracking-id-format.md) — format, suffix taxonomy, pack-level rule
- [q25-sharepoint-pages-coverage.md](q25-sharepoint-pages-coverage.md) — the SharePoint-side counterpart
- [q16-imep-gold-inventory.md](q16-imep-gold-inventory.md) · [q29-gold-tier-structure.md](q29-gold-tier-structure.md) — `tbl_pbi_platform_mailings` as a tier-3 dimension
- [cross-channel-mvp-scope.md](cross-channel-mvp-scope.md)
- [../joins/cross_channel_via_tracking_id.md](../joins/cross_channel_via_tracking_id.md)
- [../genie_questions_imep.md](../genie_questions_imep.md) — Q24 · [../sources.md](../sources.md#q24)
