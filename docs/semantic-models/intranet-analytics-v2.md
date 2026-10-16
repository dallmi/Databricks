# Intranet Analytics v2.0

| | |
|---|---|
| Workspace | IMEP-UAT |
| Snapshot | 2026-10-16 |
| Source | `INFO.VIEW.*` metadata export of the published model, and TMDL view of a `.pbix` copy (DAX of the three measure tables), both photographed |
| Coverage | Structure complete; DAX for 86 of 115 measures (`Intranet Measures` 50, `Article Measures` 33, `Intranet Page Level Measures` 3); the 29 measures on data tables and the source queries not yet seen — see [Open questions](#open-questions) |
| Caveat | The `.pbix` is not exactly the published version: it has an extra measure `active url trial` |
| Sensitivity label | Internal |

Object names that contain the organisation name are written as `__ORG__`, so the
repository stays free of it.

## Executive summary

Intranet Analytics v2.0 reports how the intranet is used: unique visitors, views, visits,
likes, comments and time spent — per page, site, division, region and referring
application, over rolling 7/14/21/28-day windows and "ty" (most likely *this year*), plus
the share of active employees reached.

It has **31 tables, ~455 columns, 115 measures and 21 relationships**. Underneath, it is
four separate mini-models — one per fact table — each with its own copy of the date,
division and referrer tables, stitched together through a single page table
(`marketingpageid`) whose relationships filter in **both directions**.

That design works, but it carries four real risks:

1. **Numbers leak between visuals.** A filter meant for one fact table travels through
   the shared page table into the others (issue 1).
2. **Slicers don't reach every visual.** Each fact table answers to a different date
   table, so one date slicer filters some visuals and silently ignores others (issue 2).
3. **The model is large and rigid.** ~165 columns hold pre-calculated rolling-window
   unique visitors. "Last 7 days" always means the 7 days before the data load, whatever
   date the user picks, and every new window means new columns upstream (issue 3).
4. **Several measures calculate the wrong thing, and eight don't work at all.** Eight
   article measures (author, top story, …) point at tables that no longer exist. The
   article measures expect different slicer values than the intranet measures, so *YTD*
   shows all-time numbers on one set of pages. *% reached* for a site reads a per-division
   column, two selected divisions show the larger one instead of the total, article
   unique visitors are added up across articles, and *visited pages* ignores the
   7/14/21/28-day choice (issue 9).

**Recommended order:** fix the measure errors in issue 9 first (small, contained, and
visible to report users) → one shared date and division table → single-direction filters
→ test whether the rolling windows can become measures over `fact` → clean up the article
metadata table and leftovers. Structural findings are confirmed from the metadata;
calculation findings from the DAX of the three measure tables. The 29 measures that sit on
data tables are not yet seen.

## At a glance

| Role | Tables |
|---|---|
| Fact (numbers) | `fact`, `overview_fact`, `datewise_overview`, `refapp_overview_fact` |
| Date | `date`, `date_data_extract`, `date_datewise_data_extract` (+ 2 hidden auto-date tables) |
| Division | `division_overview`, `division_datewise`, `datewise_division`, `division_refapp`, `marketingPageId_division` |
| Page / article | `marketingpageid`, `webpage_inventory`, `webpage_tags`, `meta_data`, `meta_data_table` |
| People | `employee_contact`, `active_employee`, `date_active_employee` |
| Referrer | `referrer_application`, `referrer_application_data_extract` |
| Measures only | `Article Measures` (33), `Intranet Measures` (50), `Intranet Page Level Measures` (3) |
| Helpers, not linked | `Dimension`, `Measure` (field parameters), `Refreshtime`, `weeks` |

## How the tables connect

Arrows show the direction a filter travels: from the lookup table (one row per key) to
the fact table (many rows per key). **Double-headed arrows filter both ways.** All
relationships are one-to-many and active unless the label says otherwise.

```mermaid
flowchart LR
  classDef fact fill:#ECEBE4,stroke:#404040,stroke-width:2px,color:#000
  classDef dim fill:#FFFFFF,stroke:#8E8D83,color:#000
  classDef hub fill:#FFFFFF,stroke:#BD000C,stroke-width:2px,color:#000
  classDef auto fill:#F7F7F5,stroke:#B8B3A2,stroke-dasharray:4 3,color:#7A7870

  subgraph P["shared page tables"]
    direction TB
    mdt(["meta_data_table"]):::dim
    wi(["webpage_inventory"]):::dim
    mpid(["marketingpageid"]):::hub
    wt(["webpage_tags"]):::dim
    ldt[("LocalDateTable (auto)")]:::auto
    md(["meta_data"]):::dim
    mdt <-->|"URL Column = article url<br/>MANY-TO-MANY"| wi
    mpid <-->|marketingpageid| wi
    mpid <-->|marketingpageid| wt
    ldt -->|"Date = __ORG__ArticleDate"| md
  end

  subgraph F1["fact"]
    fact[["fact"]]:::fact
    dde(["date_data_extract"]):::dim
    ec(["employee_contact"]):::dim
    rade(["referrer_application_data_extract"]):::dim
    dae(["date_active_employee"]):::dim
    dwd(["datewise_division"]):::dim
    dde <-->|"date_key = visitdatekey"| fact
    ec <-->|"contactId = viewingcontactid"| fact
    rade <-->|referrerapplicationid| fact
    dae <-->|Key| fact
    dwd <-->|gcrs_busn_div_name| dae
  end

  subgraph F2["datewise_overview"]
    dwo[["datewise_overview"]]:::fact
    date(["date"]):::dim
    ddde(["date_datewise_data_extract"]):::dim
    ddw(["division_datewise"]):::dim
    date <-->|"date_key = visitdatekey"| dwo
    ddde <-->|"date_key = visitdatekey"| dwo
    ddw <-->|"gcrs_busn_div_name =<br/>employeebusinessdivision"| dwo
  end

  subgraph F3["overview_fact"]
    of[["overview_fact"]]:::fact
    dov(["division_overview"]):::dim
    ae(["active_employee"]):::dim
    mpd(["marketingPageId_division"]):::dim
    dov <-->|"gcrs_busn_div_name =<br/>employeebusinessdivision"| of
    dov <-->|gcrs_busn_div_name| ae
    dov <-->|"gcrs_busn_div_name =<br/>employeebusinessdivision"| mpd
  end

  subgraph F4["refapp_overview_fact"]
    rof[["refapp_overview_fact"]]:::fact
    drf(["division_refapp"]):::dim
    ra(["referrer_application"]):::dim
    drf <-->|"gcrs_busn_div_name =<br/>employeebusinessdivision"| rof
    ra -->|referrerapplicationid| rof
  end

  mpid <-->|marketingpageid| fact
  mpid <-->|marketingpageid| dwo
  mpid <-->|marketingpageid| of
  mpid -->|marketingpageid| rof

  style P fill:#FFFFFF,stroke:#B8B3A2
  style F1 fill:#F8F7F2,stroke:#B8B3A2
  style F2 fill:#F8F7F2,stroke:#B8B3A2
  style F3 fill:#F8F7F2,stroke:#B8B3A2
  style F4 fill:#F8F7F2,stroke:#B8B3A2
```

**Legend:** grey boxes = fact tables · white = lookup tables · red outline = the shared
page hub · dashed = hidden auto-date table. Each shaded group is one fact table with its
own date, division and referrer tables; `marketingpageid` is the only thing they share.

### Relationships

| # | Many side (filtered) | One side (filters) | Key | Direction |
|---|---|---|---|---|
| 1 | `active_employee` | `division_overview` | `gcrs_busn_div_name` | both |
| 2 | `date_active_employee` | `datewise_division` | `gcrs_busn_div_name` | both |
| 3 | `datewise_overview` | `division_datewise` | `employeebusinessdivision` → `gcrs_busn_div_name` | both |
| 4 | `datewise_overview` | `marketingpageid` | `marketingpageid` | both |
| 5 | `datewise_overview` | `date` | `visitdatekey` → `date_key` | both |
| 6 | `datewise_overview` | `date_datewise_data_extract` | `visitdatekey` → `date_key` | both |
| 7 | `fact` | `date_active_employee` | `Key` → `Key` | both |
| 8 | `fact` | `marketingpageid` | `marketingpageid` | both |
| 9 | `fact` | `referrer_application_data_extract` | `referrerapplicationid` | both |
| 10 | `fact` | `employee_contact` | `viewingcontactid` → `contactId` | both |
| 11 | `fact` | `date_data_extract` | `visitdatekey` → `date_key` | both |
| 12 | `marketingPageId_division` | `division_overview` | `employeebusinessdivision` → `gcrs_busn_div_name` | both |
| 13 | `meta_data` | `LocalDateTable_086eb602…` | `__ORG__ArticleDate` → `Date` | single |
| 14 | `meta_data_table` | `webpage_inventory` | `URL Column` → `article url` | **both, many-to-many** |
| 15 | `overview_fact` | `division_overview` | `employeebusinessdivision` → `gcrs_busn_div_name` | both |
| 16 | `overview_fact` | `marketingpageid` | `marketingpageid` | both |
| 17 | `refapp_overview_fact` | `division_refapp` | `employeebusinessdivision` → `gcrs_busn_div_name` | both |
| 18 | `refapp_overview_fact` | `marketingpageid` | `marketingpageid` | single |
| 19 | `refapp_overview_fact` | `referrer_application` | `referrerapplicationid` | single |
| 20 | `webpage_inventory` | `marketingpageid` | `marketingpageid` | both |
| 21 | `webpage_tags` | `marketingpageid` | `marketingpageid` | both |

18 of 21 relationships filter both ways. All 21 are active.

### Fact tables

| Table | Columns | What a row most likely is | Numbers it holds |
|---|---|---|---|
| `fact` | 12 | one person × page × day | `views`, `visits`, `comments`, `durationsum`, `durationavg`, `flag`; plus `viewingcontactid`, `referrerapplicationid`, `Key` |
| `overview_fact` | 77 | one page × division × region | unique visitors, views, visits, likes, comments — at page, site, division and region level, each for 7/14/21/28 days, *ty* and all time |
| `datewise_overview` | 32 | one page × division × day | unique visitors for page, site and division by day and rolling window; daily views, visits, likes, comments |
| `refapp_overview_fact` | 56 | one page × division × referring app | unique visitors and visits by referrer, site and division, rolling windows |

"What a row is" is inferred from the column names; the source queries would confirm it.

## Issues

Each issue states how sure we are: **Confirmed** = visible in the metadata,
**Likely** = follows from the structure but depends on report or DAX details,
**Open** = needs the DAX or source queries.

### 1. Filters leak between fact tables through `marketingpageid` — Confirmed (structure)

Every fact table and both page tables are linked to `marketingpageid` in both
directions. A filter on any one of them therefore travels *up* into `marketingpageid`
and back *down* into all the others.

**Example.** A report page has a date slicer from `date` set to *last 7 days*, and a
card showing total views from `fact`.

```mermaid
flowchart LR
  classDef fact fill:#ECEBE4,stroke:#404040,stroke-width:2px,color:#000
  classDef dim fill:#FFFFFF,stroke:#8E8D83,color:#000
  classDef bad fill:#FBE6E7,stroke:#BD000C,stroke-width:2px,color:#000
  s[/"Slicer: last 7 days"/]:::dim --> d(["date"]):::dim
  d --> w[["datewise_overview<br/>7 days only"]]:::fact
  w --> m(["marketingpageid<br/>pages visited in those 7 days"]):::dim
  m --> f[["fact<br/>those pages, but ALL dates"]]:::bad
```

`fact` is not linked to `date`, so its dates are never filtered. But the slicer still
shrinks the list of pages. The card ends up showing *all-time views of pages that happened
to be visited last week*. That number looks plausible but answers no real question.

**Fix.** Set the relationships to single direction (lookup → fact). Where a visual
genuinely needs the reverse direction, switch it on for that one measure with
`CROSSFILTER(…, BOTH)`.

**Caution.** Several measures only work *because of* this both-way path, and their numbers
change when the directions change. Rewrite them first:
- `visited pages`, `total pages trial`, `Active URL`, `InActive URL`, `Active/InActive`
  count pages in `webpage_inventory` through `overview_fact` (issue 9.5).
- The 16 language measures `art_C_*`, `art_L_*`, `art_UV_*`, `art_V_*` filter
  `overview_fact` by `webpage_inventory[Language]`, which only reaches `overview_fact`
  through `marketingpageid`.

### 2. Each fact table has its own date, division and referrer table — Confirmed (structure)

| Concept | Copies | Used by |
|---|---|---|
| Date | `date`, `date_datewise_data_extract` | `datewise_overview` |
| | `date_data_extract` | `fact` |
| | *(none)* | `overview_fact`, `refapp_overview_fact` |
| Division | `division_overview` | `overview_fact`, `active_employee`, `marketingPageId_division` |
| | `division_datewise` | `datewise_overview` |
| | `division_refapp` | `refapp_overview_fact` |
| | `datewise_division` | `date_active_employee` |
| Referrer | `referrer_application_data_extract` | `fact` |
| | `referrer_application` | `refapp_overview_fact` |

The three date tables have identical columns. The four division tables each hold a single
column, `gcrs_busn_div_name`.

**Example.** A page shows a *views over time* chart (`fact`) and a *unique visitors per
day* chart (`datewise_overview`). The user picks *September* in a month slicer built on
`date`. The visitors chart switches to September; the views chart doesn't move, because
`fact` listens to `date_data_extract`, not `date`. Both charts sit side by side and seem
to disagree. (Through issue 1, the views chart *does* change slightly, by pages, which
makes it even harder to spot.)

**Fix.** One shared date table and one shared division table, linked to every fact table.
The same for referrer. That removes about six tables.

### 3. Rolling windows are pre-calculated into ~165 columns — Confirmed (structure and DAX)

`overview_fact` (77), `refapp_overview_fact` (56) and `datewise_overview` (32) store unique
visitors for every combination of level (page, site, division, region, referrer) and window
(7, 14, 21, 28 days, ty, all time).

**Why someone built it this way.** Unique visitors can't simply be added up:

> Anna reads page A and page B on Monday. Page A has 1 unique visitor, page B has 1.
> Adding them gives 2, but the site had **1** unique visitor — Anna.

So every level needs its own count, and the upstream job pre-calculated all of them.

**Why it hurts.**
- Every new window (say 90 days) or level means new columns in Databricks *and* new
  measures in Power BI.
- **The windows ignore the date slicer.** The `Weeks` slicer (7 / 14 / 21 / 28 / YTD /
  All) only picks *which* pre-calculated column a measure reads. None of these measures
  touches a date table, and `overview_fact` and `refapp_overview_fact` have no date column.
  *"Last 7 days"* therefore always means the 7 days before the data was loaded, even when
  the user picked August.
- **Every measure exists six times**, once per window, plus a `… select` measure that
  switches between them: more than 30 of the 50 measures in `Intranet Measures`.

**Fix to test.** `fact` already has one row per person, page and day. A distinct count of
`viewingcontactid` over a date window answers every level and window from one table:

```dax
Unique visitors (7d) =
VAR _end = MAX ( 'Date'[Date] )
RETURN
    CALCULATE (
        DISTINCTCOUNT ( fact[viewingcontactid] ),
        DATESINPERIOD ( 'Date'[Date], _end, -7, DAY )
    )
```

Whether this is fast enough depends on how many rows `fact` has. That needs one test on the
real model. Keep a pre-calculated table only for the combinations that turn out too slow.

The window choice itself belongs in one **calculation group** (7 / 14 / 21 / 28 / YTD /
All) instead of six copies of every measure. That alone cuts `Intranet Measures` from 50
measures to roughly 15.

### 4. Article metadata is joined on URL text, many-to-many, both ways — Confirmed (structure)

`meta_data_table[URL Column]` ↔ `webpage_inventory[article url]`.

**Example.** The article list stores
`…/news/annual-results.aspx`; the page inventory stores `…/news/annual-results.aspx/`
(trailing slash) or with a `?web=1` suffix. The two never match, and the article loses its
author, audience and category in every report — without an error. And because the link
filters both ways, selecting an *Author* also filters `webpage_inventory`, and through
issue 1 every fact table.

**Fix.** Join on a page ID (`marketingpageid` / `sourcesystempageid`) instead of a URL. If
the URL is the only common key, normalise it upstream (lower case, no trailing slash, no
query string) and make the link one-directional.

### 5. `meta_data` is a raw SharePoint list with 45 "target organisation" columns — Confirmed

`meta_data` has ~88 columns, among them `__ORG__TargetOrganization` and
`__ORG__TargetOrganization 2` … `45`, `__ORG__Channels` 1–4, `Author` 1–4,
`__ORG__TargetRegion` 1–3 and `__ORG__Topic` 1–2. The list's multi-value fields were
split into one column per value.

**Example.** *"How many articles targeted Group Functions?"* Group Functions can sit in any
of the 45 columns. A filter on the first column misses every article where it was the
seventh target.

**Also:** `meta_data_table` holds a subset of the same columns, and `meta_data` is not
linked to anything except its own auto-date table. One of the two is probably unused.

**Fix.** Turn each multi-value field into a small bridge table (article × target
organisation, article × channel, …) and drop the numbered columns. Keep one metadata table.

### 6. Auto date/time is switched on — Confirmed

`LocalDateTable_086eb602…` and `DateTableTemplate_14338259…` are hidden tables Power BI
creates on its own for date columns.

**Example.** `meta_data[__ORG__ArticleDate]` has its own private calendar. A slicer on the
main date table can never filter articles by publish date. Each such table also adds size.

**Fix.** *File › Options › Current file › Data load › Auto date/time* off. Link the date
columns that matter to the shared date table instead.

### 7. Duplicate employee tables and a text key into them — Likely

`active_employee` and `date_active_employee` have almost the same columns (`avgcount`,
`count`, `D_datekey`, `datekey`, `gcrs_busn_div_name`, `Key`, `total_emp_id`,
`year-month`). `fact[Key]` → `date_active_employee[Key]` is a text column. Given
`D_datekey`, `datekey` and the division column, it is probably *date + division* glued
together.

**Example.** If one side writes `20260915|Group Functions` and the other
`2026-09-15|Group Functions`, nothing matches and *% of active employees reached* goes
blank for that day. Text keys are also slower to join than integer keys.

**Fix.** Keep one employee-count table, keyed by date and division through the shared date
and division tables (issue 2), not by a glued text key.

### 8. Leftovers and measure housekeeping — Confirmed

- Test columns: `active_employee[test1_date_employees]`,
  `date_active_employee[test_date_employees]`.
- Copied columns: `webpage_inventory[theme - Copy]`, `[topic - Copy]`,
  `meta_data[__ORG__PromoteVideo - Copy]`, `meta_data_table[__ORG__PromoteVideo - Copy]`.
- Trial measures: `overview_fact[trial]`, `Intranet Measures[total pages trial]`,
  `Article Measures[Avg_TY chekc]`, `Article Measures[active url trial]` (only in the `.pbix`), `date[midate]`
  (probably *min date*).
- Measures sit on 9 data tables besides the 3 measure tables, e.g. `date[maxdate]`,
  `webpage_inventory[sites]`, `fact[Unique visitors]`.
- `x`-prefixed measures (`xViews`, `xVisits`, `xcomments`, `xdivision`, `xxtopic`, …) look
  like helpers. Whether any visual still uses them can only be told from the report.

**Fix.** Delete what no visual uses, after checking (Tabular Editor's *Best Practice
Analyzer* or the report's field list). Move remaining measures into the measure tables.

### 9. Calculation errors in the measure tables — Confirmed (DAX)

Nearly all measures follow four patterns: pick a pre-calculated column per window; switch on
*"is a site / a division filtered?"*; a nested `IF` on the `Weeks` slicer; and a date window
over `datewise_overview` for the *over time* charts. The errors below are visible in the
code itself. Numbers in the examples are illustrative.

#### 9.1 *% of unique visitors* for one site reads the per-division column

In all six `% of unique visitor` measures (all time, 7, 14, 21, 28, ty), the branch for
*"a site is filtered"* reads `site_div_uniquevisitor…`, the site × **division** column.
`int_unique visitors` reads the correct `site_uniquevisitor…`.

```dax
var site =
    CALCULATE(
        MAX(overview_fact[site_div_uniquevisitor14]) / active_employee[Employees]   -- should be site_uniquevisitor14
    )
```

**Example.** Site *HR Central* had 900 unique visitors in 14 days: 500 from Group
Functions and 400 from the Investment Bank. The *Unique visitors* card says **900**.
*% reached* takes `MAX` over the per-division column, i.e. **500**, and divides that. Two
cards next to each other that can't both be right.

**Fix.** `site_div_uniquevisitor…` → `site_uniquevisitor…` in the `site` variable of the
six measures.

#### 9.2 Two selected divisions show the larger one, not the total

Every unique-visitor measure returns `MAX(...)` of a pre-counted column. With one division
in view that is the right number. With two selected, `MAX` returns the bigger of the two.

**Example.** Slicer: *Global Wealth Management* (3,000 visitors) and *Investment Bank*
(2,000). Each employee belongs to one division, so the true total is **5,000**. The card
shows **3,000**. The same happens with two sites; there the true answer lies between the
larger site and the sum, but `MAX` can't know where.

**Fix.** Short term: allow only one division and one site in the slicers. Long term: the
distinct count over `fact` from issue 3, which gives the right answer for any selection.

#### 9.3 The same measure is written three different ways

| Window | Divides by | Keeps the slicer's selection (`ALLSELECTED`) |
|---|---|---|
| All time | `[int_employees]` | no |
| 14 days | `active_employee[Employees]` | no |
| 7, 21, 28 days | `active_employee[Employees]` | yes |
| ty | `[int_employees ytd]` | no |

`int_by region 28` also differs from its 7/14/21 siblings: its `site` branch keeps the
*division* selection where the others keep the *site* selection.

**Example.** A table lists one row per site and shows *% reached*. With **14 days** each
row shows its own site. Switch the slicer to **21 days**: `ALLSELECTED(websitename)`
removes the row's own site filter, and every row can show the same number. Whether this
happens depends on which column the table uses for its rows. If it is
`webpage_inventory[websitename]`, it does.

Also: `% of unique visitor` and `% of unique visitor 21` have no percentage format, so
they show `0.0734` instead of `7.34 %` unless the visual sets a format.

**Fix.** One template for all windows: same denominator, same `ALLSELECTED` choice, same
format. With the calculation group from issue 3, there is only one copy left to get right.

#### 9.4 `int_visitors` counts visits, not visitors

All seven `int_visitors` measures read `ref_…_visits…` columns from
`refapp_overview_fact`.

**Example.** Anna opens the intranet from Teams three times in a week: **3 visits, 1
visitor**. A chart titled *Visitors by referrer* built on `int_visitors` shows 3.

**Fix.** Rename to `int_visits (by referrer)`, or switch to the `ref_…_uniquevisitor…`
columns if visitors are what the chart means.

#### 9.5 *Visited pages* and *active pages* ignore the 7/14/21/28-day choice

`visited pages` (and `total pages trial`) build their date window only for *YTD*:

```dax
VAR mindate =
    SWITCH ( TRUE(),
        selectday = "YTD - Year to date", DATE ( YEAR ( TODAY() ), 1, 1 )
    )   -- 7, 14, 21, 28 → blank → no lower date limit
```

The visit test also uses the all-time column `overview_fact[div_reg_visits] > 0`.

**Example.** The user picks *Last 7 days*. Unique visitors drop to the 7-day figure,
but *Visited pages* still shows every page that was **ever** visited, e.g. 1,240 instead
of 310.

The three measures in `Intranet Page Level Measures` (`Active URL`, `InActive URL`,
`Active/InActive`) test the all-time column `div_reg_views` and have no window at all: a
page counts as *active* if it was **ever** viewed.

**Fix.** Add the 7/14/21/28 cases, and test the matching `div_reg_visits7` /
`div_reg_views7` … column. These measures also depend on the both-way filter from issue 1.
Rewrite them before changing that.

#### 9.6 Three different "today"s

| Measures | "Now" means |
|---|---|
| All window measures from `overview_fact` / `refapp_overview_fact` | the day the data was loaded (fixed in the columns) |
| `int_UV overtime…` charts, `visited pages` | the **last date in the `date` table** (`MAX('date'[date])` over all dates) |
| *YTD* in the same charts | `TODAY()` |

**Example.** If the `date` table runs to 31 December, the *last 7 days* chart shows
25–31 December, which is empty. If the nightly load fails for three days, the cards
still say *last 7 days* but end three days ago, while *YTD* runs to today.

**Check.** `EVALUATE ROW("last date in date table", MAX('date'[date]), "last visit", MAX(datewise_overview[visitdatekey]))`.
If the two differ, the charts are anchored wrong.

#### 9.7 An empty or double selection in the `Weeks` slicer silently shows *All time*

Every `… select` measure is a nested `IF` whose final fallback is the all-time value.

**Example.** A user Ctrl-clicks *7* and *28* to compare them. `SELECTEDVALUE` returns
nothing, and every number jumps to all-time totals. Nothing on the page says so.

**Fix.** Set the `Weeks` slicer to *single select* with *Require selection*, or return
blank instead of all-time in the fallback.

#### 9.8 Eight article measures point at tables that don't exist

`art_author`, `art_abstract personalization`, `art_operational update`,
`art_org announcement`, `art_overtitle`, `art_success story`, `art_top story` and
`art_video promotion` all read from `'Query Table'` and `Query1`:

```dax
measure art_author =
    CALCULATE(
        MAX('Query Table'[Author]),   -- no table 'Query Table' in the model
        'Query1'[URL Column]          -- no table 'Query1' in the model
    )
```

The model has no such tables. The columns they name (`Author`, `Operational Updates`,
`URL Column`, …) now live in `meta_data` and `meta_data_table`, so the tables were
evidently renamed or rebuilt and these measures were left behind. TMDL view flags them
with **32 problems**.

**Example.** An article table shows *Title*, *Author*, *Top story*. The *Author* and *Top
story* columns error out, and depending on the visual the whole table fails with *Can't
display the visual*.

This is confirmed in the `.pbix`. The published model has the same table names
(`meta_data`, `meta_data_table`), so it is very likely broken there too. Check the article
page in the service.

**Fix.** Point the eight measures at `meta_data` / `meta_data_table`, or better: once the
article attributes are joined by page ID (issues 4 and 5), replace them with plain
columns.

#### 9.9 Article and intranet measures expect different `Weeks` values

The same `Weeks[Days]` slicer is compared against two different spellings:

| Measure table | *Year to date* | *All time* |
|---|---|---|
| `Intranet Measures` | `"YTD - Year to date"` | `"All - Since site is live"` |
| `Article Measures` | `"YTD"` | `"All"` |

Only one spelling can match the slicer. On the pages built on the other family, *YTD*
falls through to the last `IF` branch and shows **all-time** numbers. (*All* is harmless,
because the fallback is all-time anyway.)

**Example.** The user selects *YTD* on the article page. Comments, likes and views per
language show totals since the site went live, while the intranet overview next to it
shows this year.

**Check.** `EVALUATE VALUES(Weeks[Days])` shows which spelling is real.

#### 9.10 Article unique visitors are added up across articles

`art_unique visitors` and the language versions `art_UV_DE/EN/FR/IT` use `SUM` over the
pre-counted `div_reg_uniquevisitor…` columns. Per article that is fine: each employee
belongs to one division and one region, so the rows don't overlap. As soon as a total
covers more than one article, the same person is counted once per article.

**Example.** Anna reads three German articles this week. Each article row correctly shows
her once. The total row and the *Unique visitors (DE)* card show **3**. Meanwhile
`int_unique visitors` on the intranet page uses the pre-counted site total, so the two
pages report different *unique visitors* for the same week.

**Fix.** Label the article total as *article reads by unique visitors* or hide the total
row. The real fix is the distinct count over `fact` from issue 3.

#### 9.11 Smaller things

- `art_engagement rate` divides with `/` instead of `DIVIDE()`. An article with likes but
  0 views (possible with pre-counted windows) shows *Infinity* or *NaN*. The format also
  shows four decimal places.
- `active url trial` uses `CROSSFILTER('date'[date], webpage_inventory[publishing date], Both)`,
  but there is no relationship between those two columns, so the measure fails when used.
  It doesn't exist in the published model.
- `art_article title` returns the English title, falling back to German. Articles that only
  exist in French or Italian show no title.

## Open questions

| Question | Needed for | How to get it |
|---|---|---|
| DAX of the 29 measures on data tables (e.g. `active_employee[Employees]`, `fact[Unique visitors]`, `webpage_inventory[sites]`) | Issues 7, 8, 9.3 | TMDL view of the `.pbix`, one data table at a time |
| Real values of the `Weeks` slicer | Issue 9.9 | `EVALUATE VALUES(Weeks[Days])` |
| Is the `.pbix` the published version? | All DAX findings | Query 1 (relationships) in the `.pbix`: 21 relationships, same list? |
| Source query per table (`partition` in TMDL view) | Issue 3; mapping to Databricks bronze/silver/gold | TMDL view of the `.pbix` |
| Last date in `date` vs last visit | Issue 9.6 | Query in 9.6 |
| Which column the report's tables group sites and divisions by | Issues 9.2, 9.3 | Report file |
| Row count of `fact` | Issue 3 (speed of distinct count) | `EVALUATE ROW("rows", COUNTROWS(fact))` |
| Storage mode per table (Import/DirectQuery) | General | `EVALUATE INFO.VIEW.TABLES()` |
| Row-level security roles | General | Model owner |
| Which report pages use which tables | Issues 1, 2 | Report file |
