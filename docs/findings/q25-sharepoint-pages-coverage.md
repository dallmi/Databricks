# SharePoint Pages: Tracking ID Coverage by Time and Site

> Source: Genie question Q25 (2026-04-20). Status: confirmed.

## Question

[Q22](q22-sharepoint-gold-schemas.md) showed that only a small minority of pages carry `__ORG__GICTrackingID`. Q25 breaks that down in four steps on `sharepoint_bronze.pages`: overall counts, coverage per month of `__ORG__ArticleDate`, distinct page URLs, and coverage per site. It answers two build decisions: (a) can the cross-channel funnel be restricted to a subset of sites where coverage is dense, and (b) from which date is coverage reliable enough to be the dashboard's default start?

## Findings

### Step 1 — Overall coverage

| Measure | Share of all pages |
|---|---|
| With `__ORG__GICTrackingID` | small minority (low single-digit percent) |
| With `__ORG__ArticleDate` | a minority, but several times the tracked share |
| With both | practically identical to the tracked set |

Virtually every tracked page also has an article date. Filtering on the tracking id gives the article date for free.

### Step 2 — Coverage over time (per month of `__ORG__ArticleDate`)

| Period | Coverage | Meaning |
|---|---|---|
| until mid-2024 | about zero | Tracking not in use |
| Jul–Aug 2024 | very low | First appearance |
| **Sep 2024** | **jumps from near zero to a substantial share** | **Rollout inflection point** |
| Oct–Dec 2024 | rapid ramp-up | |
| 2025 | volatile, never stable | |
| Mar 2026 | all-time peak | |

The coverage **never reached the 80 % reliability target** set in the question, in any month; it peaked well below it. The month-to-month volatility points to process compliance — whether content teams apply tracking ids consistently — not to a schema or technical problem.

### Step 3 — Page URL integrity

- Tracked URLs are a small minority of all distinct page URLs, matching step 1.
- **No URL carries more than one tracking id.** The mapping URL ↔ tracking id is 1:1; there is no historical reuse or rotation.

### Step 4 — Site concentration: one site holds virtually everything

The site foreign key is validated: `pages.SPT_SiteId = sites.SPT_SiteId` matches every page. Almost all sites have pages.

| Site | Tracked pages | Packs (segments 1–2) |
|---|---|---|
| **"News and events"** | **virtually all tracked pages** | **the large majority** |
| a handful of other sites (including a training playground and test sites) | a handful of pages each | a few at most |
| all other sites (the vast majority) | none | none |

Within "News and events" itself, tracked pages are a minority — the rest are most likely pre-rollout or non-article pages. The few tracked pages elsewhere are test or edge-case artefacts.

### Genie's recommendations

- **(a) Site restriction: yes.** Restrict to "News and events". It holds virtually all tracked pages, the other sites add no analytical variety, and the dashboard becomes more stable and less noisy.
- **(b) Default start: September 2024**, the rollout inflection. Do not expect reliably high coverage; show a coverage indicator in the dashboard (tracked articles / all articles that month). Optional stricter "reliable mode": from October 2024, where coverage stays consistently higher.

## Consequences

1. **Pack universe on the SharePoint side:** "News and events" holds more distinct packs than the email side has `EMI` packs ([Q24](q24-tracking-id-volume.md)). Their intersection — the real funnel coverage — is still to be measured.
2. **Site filter in queries:** `WHERE p.SPT_SiteId = '<news-and-events-site-id>'` or, via join, `s.SiteName = 'News and events'`.
3. **Time floor revised:** from `2025-01-01` (Q24, email side) to **`2024-09-01`** (Q25, SharePoint rollout). The two sides started at different times; the decision is an **effective dashboard floor of 2024-09-01**.
4. **Coverage key performance indicator (KPI) in the dashboard:** a mandatory tile "pack matching rate = packs with an intranet page / all packs", plus "coverage by month" as a validation widget.
5. **A page dimension becomes small:** restricting to "News and events" reduces the page universe from tens of thousands to a few thousand pages, of which a minority is tracked. That is the universe.

## Related

- [q22-sharepoint-gold-schemas.md](q22-sharepoint-gold-schemas.md) — the coverage blocker this question refines
- [q23-tracking-id-format.md](q23-tracking-id-format.md) · [q24-tracking-id-volume.md](q24-tracking-id-volume.md)
- [sharepoint-bronze-pages-and-pageviews.md](sharepoint-bronze-pages-and-pageviews.md) — why only articles carry the tracking id
- [cross-channel-mvp-scope.md](cross-channel-mvp-scope.md)
- [../tables/sharepoint/pages.md](../tables/sharepoint/pages.md) · [../joins/cross_channel_via_tracking_id.md](../joins/cross_channel_via_tracking_id.md) · [../joins/join_strategy_contract.md](../joins/join_strategy_contract.md)
- [../genie_questions_imep.md](../genie_questions_imep.md) — Q15 and Q25 · [../sources.md](../sources.md#q25)
