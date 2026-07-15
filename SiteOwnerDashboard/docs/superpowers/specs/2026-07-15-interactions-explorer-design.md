# Content & Interactions — Explorer, Grouped Excel Exports, Click Insights

**Date**: 2026-07-15 · **Status**: approved (scope confirmed by Michael: packages A+B+C; explorer replaces Top Links + Clicks by Division)

## Context

The Content & Interactions tab (Phase 2) ships fixed-column tables only. The interactions
parquet carries dimensions the tab never surfaces (`link_type`, `link_address`,
`file_type_label`, `video_*`, `theme`/`topic`/`content_owner`, `hr_region`), and the
Excel exports of the interactions tables are flat even though the on-screen table has
expandable rows (language variants + link detail). The Overview tab already has the
machinery this design reuses: `ColumnPicker`, `buildSelectGroupBy`, aggregate-join
post-metrics, and `sheetGroupedPages` (Excel outline groups).

## Package A — Interactions Explorer (configurable dimensions × metrics table)

One new table replaces **Top Links** and **Clicks by Division** (both become presets).
**Pages — interest × action** (page drill + pv join) and **Top Downloads** stay.

- **Dimensions** (offered only when the column exists in the ix view): Link, Link Type,
  Class (`interaction_class`), Component, File Type, Page Name, Language, Content Type,
  Theme, Topic, Content Owner, Division, Region.
- **Metrics** (SQL aggregates on `ix`): Clicks, Unique Clickers, Clicks/Clicker,
  Downloads, Pages (distinct `page_key`), Last clicked.
- **Post-metrics** (secondary queries keyed by the visible dimensions, same pattern as
  the Overview Pages table): Views / CTR / Clicker Rate via aggregate join on `pv` —
  offered always, populated only when **every** visible dimension also exists in `pv`
  (otherwise `–`); weekly Trend sparkline; Δ Clicks vs. prior equal period.
- **Presets** as a `.seg` control: Links · Divisions · Components · File Types.
  Selecting a preset sets the picker's visible set + default sort; manually changing
  columns clears the active preset highlight. Preset shown only when its lead column
  exists.
- Totals footer (window-level aggregates, no GROUP BY), sortable headers, LIMIT 500,
  same filter semantics as the rest of the tab (`winIx`, skipped-filter hint).

## Package B — Excel exports with outline groups

Pattern already proven by `sheetGroupedPages` (Overview): children as Excel outline
rows, `outlineLevel = 1`, hidden by default, expandable via the +/− gutter.

- **Pages — interest × action export**: per page (current sort) two child groups —
  language variants (page_id grain, pv⟕ix join) and top-15 links of that page.
  Link children use four extra trailing columns: Class, Link Type, Address,
  Last clicked. Data fetched in two set-based queries (window function for per-page
  top-N), not per-page loops.
- **Explorer export**: dynamic headers = visible dims × metrics (Overview pattern).
  When `Link` is a visible dimension and Page Name is not, each link row gets its
  top-10 pages as outline children (clicks/clickers/last clicked mapped into the
  matching visible columns).

## Package C — New click insights

1. **Link destinations** (donut): navigational clicks (`interaction_class='link'`)
   split Internal / External / Mail·Tel. Internal = relative address or host ∈ the
   set of hosts seen in `pv.page_url`; External = other http(s) hosts.
2. **Top external domains** (bar list): clicks by external host.
3. **Downloads by file type** (donut, `file_type_label`) — card hidden when absent.
4. **Top videos** (bar list): plays per video with completion rate
   (`Complete ÷ Play`) in the label — hidden when no `video_action` data.
5. **Video actions** (donut): Play / Pause / Complete split — hidden when absent.
6. **"High interest · no clicks" toggle** on interest × action: filters to pages with
   views but zero clicks (CTA candidates); toggle also applies to that table's export.

## Non-goals

- No person-level "top clickers" (PII cleanup pending).
- No `client_os` / `client_browser` breakdowns (intranet, no variance).
- No `link_ancestors` surfacing (raw DOM paths).

## Error handling / degraded exports

Every new dimension, preset, and card is guarded by the ix column inventory
(`ixCols`) exactly like the existing Phase-2 blocks; missing columns hide the
feature instead of erroring. pv-sourced post-metrics degrade to `–` cells with a
hint in the table sub-line.

## Testing

Playwright/Chrome against the demo parquets (`output/site_*.parquet`) via local
HTTP server: explorer presets, column toggles, CTR guard (link-grain vs page-grain),
no-action toggle, and each XLSX export re-opened to verify outline groups.
Standalone build regenerated afterwards.
