# Overview KPI Groups — CampaignWe-style themed collection cards

**Date**: 2026-07-15 · **Status**: approved (Michael: replace the flat Overview cards; all five groups)

## Context

CampaignWe's overview groups ~20 KPIs into five themed collection cards (Reach,
Engagement, Submissions, Content, Outreach): coloured 3px left border + coloured
uppercase title, stacked value|label rows with hairline separators, derived
rates greyed. The SiteOwnerDashboard Overview currently shows five flat
traffic-only cards; the headline numbers of Audience, Interactions and
Lifecycle are invisible until the user switches tabs.

## Design

Replace the Overview's flat KPI row (`#kpiGrid`) with five grouped collection
cards — the Overview becomes a cross-domain "site owner at a glance" index.
Detail tabs keep their large flat cards (five domain-scoped KPIs each; grouping
would only shrink the values without adding structure).

| Group | Accent | Rows (existing query building blocks) |
|-------|--------|----------------------------------------|
| Reach | `--primary` | Page Views · Visits · Unique Visitors · Views/Visit *(derived)* |
| Engagement | `--success` | Engagement (UV÷Visits) · Avg. Session · Pages/Session · Bounce Rate *(derived, inverted delta)* |
| Audience | `--warning` | New Visitors · Returning Share *(derived)* · Visits/Visitor *(derived)* |
| Actions | `--primary-dark` | Clicks · Downloads · CTR *(derived)* · Clicker Rate *(derived)* — group rendered only when the interactions parquet is loaded |
| Content | `--info` | Pages Viewed (distinct `page_key`) · Languages (no delta) · Pages Published (only with `publishing_date`) |

Adaptations vs. CampaignWe (which has no period comparison):

- **Every row keeps its MoM delta** — the existing `deltaHtml(c, p, invert)`
  chip sits between value and label; rows without a meaningful comparison
  (Languages) render no chip. Bounce Rate inverts (down = good).
- **Missing data hides rows/groups** (existing guard pattern): no ix → no
  Actions group; no `publishing_date` → no Published row; no `language`
  column → no Languages row.

Data: `metricsFor` + `audienceKpisFor` + `ixMetricsFor` (all existing) + a new
`contentMetricsFor` (distinct pages / languages / published-in-window), each
run for the current and prior window as today.

Export: the global workbook's KPIs sheet gains a leading **Group** column
(`Group | Metric | Value | MoM`), same order as the cards. `guide.html`'s
"Read the headline numbers" section is updated to describe the groups.

CSS: port CampaignWe's `.kpi-groups / .kpi-group / .kpi-group-title / .kpi-row`
rules onto house tokens (auto-fit grid ≥215px, `--shadow-card`, group accents
per the table above). The old `.kpi-card` styles stay — detail tabs use them.

## Testing

Playwright against the demo parquets: five groups render with deltas, Actions
disappears when `site_interactions.parquet` is absent (rename test), KPIs sheet
of the global export shows the Group column, remaining tabs unchanged.
Standalone rebuilt.
