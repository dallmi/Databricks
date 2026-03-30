# Power BI Visualization Guide — Intranet PageViews

This document explains how to build an intranet usage analytics report in Power BI Desktop using the parquet files produced by `scripts/flatten_appinsights.py`.

> **Data note**: The parquet files contain page view data pre-enriched with HR organisational fields. GPN and Email are included temporarily for validation but should not be used in visuals. Use `user_id` (anonymous browser ID) for unique visitor counts.

---

## Table of Contents

1. [Data Import](#1-data-import)
2. [Data Model & Relationships](#2-data-model--relationships)
3. [Calculated Columns (Power Query)](#3-calculated-columns-power-query)
4. [DAX Measures](#4-dax-measures)
5. [Page 1 — Overview](#5-page-1--overview)
6. [Page 2 — Content Performance](#6-page-2--content-performance)
7. [Page 3 — Divisions & Regions](#7-page-3--divisions--regions)
8. [Page 4 — Sessions & Engagement](#8-page-4--sessions--engagement)
9. [Slicers & Cross-Filtering](#9-slicers--cross-filtering)
10. [Aggregation Caveats](#10-aggregation-caveats)
11. [Appendix — Full DAX Reference](#11-appendix--full-dax-reference)

---

## 1. Data Import

### Parquet Files

| File | Grain | Description |
|------|-------|-------------|
| `fact_page_view.parquet` | One row per page view | Core fact table with timestamps, durations, HR fields |
| `agg_session.parquet` | One row per session | Pre-aggregated session metrics (bounce, duration, entry/exit) |
| `dim_page.parquet` | One row per page | Page metadata (name, site, theme, topic, content owner) |
| `dim_date.parquet` | One row per date | Date dimension (year, quarter, month, week, day) |

### Import Steps

1. **Get Data → Parquet**
   - Home → Get Data → More → Parquet
   - Browse to `fact_page_view.parquet` → Load
   - Repeat for `agg_session.parquet`, `dim_page.parquet`, `dim_date.parquet`

2. **Rename tables** in Model view:
   - `fact_page_view` → **PageViews**
   - `agg_session` → **Sessions**
   - `dim_page` → **Pages**
   - `dim_date` → **DateTable**

3. **Check column types** in Power Query Editor (Transform Data):

   **PageViews:**
   | Column | Type |
   |--------|------|
   | `timestamp` | DateTime |
   | `view_id`, `page_id`, `session_id`, `user_id` | Text |
   | `page_load_ms`, `time_on_page_sec` | Decimal Number |
   | `is_last_in_session` | True/False |
   | `gpn`, `email` | Text |
   | `referrer_url` | Text |
   | `client_os`, `client_browser`, `client_country` | Text |
   | All `hr_*` columns | Text |
   | `source_file` | Text |

   **Sessions:**
   | Column | Type |
   |--------|------|
   | `session_date` | Date |
   | `session_start`, `session_end` | DateTime |
   | `session_id`, `user_id`, `gpn`, `email` | Text |
   | `duration_sec`, `engagement_time_sec`, `avg_time_on_page_sec` | Decimal Number |
   | `page_view_count` | Whole Number |
   | `entry_page_id`, `exit_page_id` | Text |
   | `is_bounce` | True/False |
   | All `hr_*` columns | Text |

   **Pages:**
   | Column | Type |
   |--------|------|
   | `page_id` | Text |
   | `publishing_date` | Date |
   | All other columns | Text |

   **DateTable:**
   | Column | Type |
   |--------|------|
   | `date_key` | Whole Number |
   | `date` | Date |
   | `year`, `quarter`, `month`, `week` | Whole Number |
   | `month_name`, `day_of_week` | Text |

---

## 2. Data Model & Relationships

### Semantic Model Overview

```
┌──────────────────────┐
│      DateTable       │
│──────────────────────│
│ date (PK)            │
│ year, quarter, month │
│ week, day_of_week    │
└──────────┬───────────┘
           │ 1
           │
           │ *
┌──────────┴───────────────────────────────────────┐     ┌──────────────┐
│                   PageViews                      │     │    Pages     │
│                  (fact table)                    │     │──────────────│
│ view_id (PK)                                    │     │ page_id (PK) │
│ view_date ──► DateTable (active)                │*───1│ page_name    │
│ page_id   ──► Pages (active)                    │     │ site_name    │
│ session_id                                       │     │ theme, topic │
│ user_id (for UV counts)                          │     │ content_owner│
│ time_on_page_sec, page_load_ms                  │     └──────────────┘
│ hr_division, hr_region, ...                      │
└──────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────┐
│                   Sessions                       │
│              (aggregated table)                  │
│ session_id (PK)                                  │
│ session_date ──► DateTable (INACTIVE)            │
│ entry_page_id ──► Pages (INACTIVE)               │
│ user_id, page_view_count, is_bounce              │
│ duration_sec, engagement_time_sec                │
│ hr_division, hr_region, ...                      │
└──────────────────────────────────────────────────┘

⚠️  Sessions relationships are INACTIVE. All Session-based
    measures MUST use USERELATIONSHIP() to respond to slicers.

┌──────────────────────┐
│     _Measures        │
│──────────────────────│
│ Total Views          │
│ Unique Visitors      │
│ Total Sessions       │
│ Bounce Rate          │
│ Avg Time on Page     │
│ Avg Pages/Session    │
│ ... (20+ measures)   │
└──────────────────────┘
```

### Mark Date Table

Right-click DateTable in Model view → "Mark as date table" → select the `date` column.

### Add Date Column to PageViews

PageViews has a `timestamp` (DateTime) but needs a Date column for the relationship. Add in Power Query:

```m
= Table.AddColumn(#"Previous Step", "view_date", each DateTime.Date([timestamp]), type date)
```

### Relationships

| From | To | Cardinality | Key |
|------|----|-------------|-----|
| PageViews[view_date] | DateTable[date] | Many-to-One | Active |
| PageViews[page_id] | Pages[page_id] | Many-to-One | Active |
| Sessions[session_date] | DateTable[date] | Many-to-One | **Inactive** |
| Sessions[entry_page_id] | Pages[page_id] | Many-to-One | **Inactive** |

Set cross-filter direction to **Single** for all relationships.

> **Why inactive?** Sessions and PageViews both connect to DateTable and Pages. Power BI allows only one active path per table pair. All Session-based measures must explicitly activate these relationships using `USERELATIONSHIP()`. This is handled in the DAX measures below — just use the correct measure names.

---

## 3. Calculated Columns (Power Query)

These columns already exist in the parquet files. Verify they are present:

| Column | Table | Description | In Parquet? |
|--------|-------|-------------|-------------|
| `timestamp` | PageViews | CET datetime | Yes |
| `time_on_page_sec` | PageViews | Engagement duration (NULL for last page) | Yes |
| `is_last_in_session` | PageViews | True if last page in session | Yes |
| `page_load_ms` | PageViews | Page load performance | Yes |
| `hr_division` through `hr_management_level` | PageViews | HR org fields | Yes |
| `session_date` | Sessions | CET date | Yes |
| `is_bounce` | Sessions | True if 1 page view only | Yes |
| `engagement_time_sec` | Sessions | Sum of measurable time-on-page | Yes |
| `page_view_count` | Sessions | Pages per session | Yes |
| `theme`, `topic`, `site_name` | Pages | Content classification | Yes |

**Add in Power Query** (not in parquet):

```m
// PageViews: view_date (Date only, for DateTable relationship)
= Table.AddColumn(#"Previous Step", "view_date", each DateTime.Date([timestamp]), type date)

// PageViews: view_hour (for heatmap visuals)
= Table.AddColumn(#"Previous Step", "view_hour", each Time.Hour([timestamp]), Int64.Type)

// PageViews: Display-friendly division (replace nulls)
= Table.AddColumn(#"Previous Step", "Division",
    each if [hr_division] = null then "(Unknown)" else [hr_division], type text)

// PageViews: Display-friendly region (replace nulls)
= Table.AddColumn(#"Previous Step", "Region",
    each if [hr_region] = null then "(Unknown)" else [hr_region], type text)

// Sessions: Display-friendly division (replace nulls)
= Table.AddColumn(#"Previous Step", "Division",
    each if [hr_division] = null then "(Unknown)" else [hr_division], type text)

// Sessions: Display-friendly region (replace nulls)
= Table.AddColumn(#"Previous Step", "Region",
    each if [hr_region] = null then "(Unknown)" else [hr_region], type text)
```

---

## 4. DAX Measures

Create a dedicated measures table: Enter Data → empty table → rename to `_Measures`. Place all measures here.

> **Important**: Because Sessions → DateTable is an inactive relationship, all Session-based measures use `USERELATIONSHIP()` so they respond correctly to the date slicer. Always use the measures below — do not drag raw Session columns into visuals directly.

### Core KPIs (PageViews-based — respond to date slicer automatically)

```dax
Total Views = COUNTROWS(PageViews)

Unique Visitors = DISTINCTCOUNT(PageViews[user_id])

Views per Visitor =
DIVIDE([Total Views], [Unique Visitors], 0)
```

### Core KPIs (Session-based — require USERELATIONSHIP)

```dax
Total Sessions =
CALCULATE(
    COUNTROWS(Sessions),
    USERELATIONSHIP(Sessions[session_date], DateTable[date])
)

Bounce Rate =
VAR TotalSess = [Total Sessions]
VAR Bounces =
    CALCULATE(
        COUNTROWS(FILTER(Sessions, Sessions[is_bounce] = TRUE)),
        USERELATIONSHIP(Sessions[session_date], DateTable[date])
    )
RETURN DIVIDE(Bounces, TotalSess, 0)

Avg Pages per Session =
CALCULATE(
    AVERAGE(Sessions[page_view_count]),
    USERELATIONSHIP(Sessions[session_date], DateTable[date])
)
```

### Engagement Metrics

```dax
Avg Time on Page (sec) =
AVERAGE(PageViews[time_on_page_sec])

Avg Time on Page (formatted) =
VAR AvgSec = [Avg Time on Page (sec)]
RETURN
    IF(
        ISBLANK(AvgSec),
        BLANK(),
        FORMAT(AvgSec / 86400, "m:ss")
    )

Avg Session Duration (sec) =
CALCULATE(
    AVERAGEX(
        FILTER(Sessions, Sessions[is_bounce] = FALSE),
        Sessions[engagement_time_sec]
    ),
    USERELATIONSHIP(Sessions[session_date], DateTable[date])
)

Avg Session Duration (formatted) =
VAR AvgSec = [Avg Session Duration (sec)]
RETURN
    IF(
        ISBLANK(AvgSec),
        BLANK(),
        FORMAT(AvgSec / 86400, "m:ss")
    )

Measurable Views % =
DIVIDE(
    COUNTROWS(FILTER(PageViews, NOT(ISBLANK(PageViews[time_on_page_sec])))),
    [Total Views],
    0
)

Avg Page Load (ms) =
AVERAGE(PageViews[page_load_ms])
```

> **Note on Avg Session Duration**: Bounces are excluded (`is_bounce = FALSE`) because bounce sessions have 0 or NULL duration, which would drag the average down artificially. This gives a more meaningful engagement metric for multi-page visits.

### Content Performance

```dax
Views per Page =
DIVIDE(
    [Total Views],
    DISTINCTCOUNT(PageViews[page_id]),
    0
)

UV per Page =
DIVIDE(
    [Unique Visitors],
    DISTINCTCOUNT(PageViews[page_id]),
    0
)
```

### Organisational Coverage

```dax
HR Coverage % =
DIVIDE(
    COUNTROWS(FILTER(PageViews, NOT(ISBLANK(PageViews[hr_division])))),
    [Total Views],
    0
) * 100

Unique Divisions =
DISTINCTCOUNT(PageViews[hr_division])
```

### Time Intelligence

```dax
Views Previous Month =
CALCULATE(
    [Total Views],
    DATEADD(DateTable[date], -1, MONTH)
)

Views MoM Change % =
VAR Current = [Total Views]
VAR Previous = [Views Previous Month]
RETURN
DIVIDE(Current - Previous, Previous, 0)

UV Previous Month =
CALCULATE(
    [Unique Visitors],
    DATEADD(DateTable[date], -1, MONTH)
)

UV MoM Change % =
VAR Current = [Unique Visitors]
VAR Previous = [UV Previous Month]
RETURN
DIVIDE(Current - Previous, Previous, 0)

Views Previous Week =
CALCULATE(
    [Total Views],
    DATEADD(DateTable[date], -7, DAY)
)

Views WoW Change % =
VAR Current = [Total Views]
VAR Previous = [Views Previous Week]
RETURN
DIVIDE(Current - Previous, Previous, 0)

Running Total Views =
CALCULATE(
    [Total Views],
    DATESYTD(DateTable[date])
)
```

### Page-Level Measures

```dax
Page Bounce Rate =
VAR CurrentPage = SELECTEDVALUE(Pages[page_id])
VAR EntrySessions =
    CALCULATE(
        COUNTROWS(Sessions),
        FILTER(Sessions, Sessions[entry_page_id] = CurrentPage),
        USERELATIONSHIP(Sessions[session_date], DateTable[date])
    )
VAR BounceEntries =
    CALCULATE(
        COUNTROWS(Sessions),
        FILTER(Sessions,
            Sessions[entry_page_id] = CurrentPage
            && Sessions[is_bounce] = TRUE
        ),
        USERELATIONSHIP(Sessions[session_date], DateTable[date])
    )
RETURN DIVIDE(BounceEntries, EntrySessions, 0)
```

> **Note**: `Page Bounce Rate` uses `SELECTEDVALUE` — it only works in visuals where a single page_id is in context (e.g., table rows or bar chart axis). In a card or KPI without page context it returns BLANK.

### Heatmap Measures

```dax
HourTable = GENERATESERIES(0, 23, 1)
```

Rename the column to `Hour`. Create relationship: PageViews[view_hour] → HourTable[Hour] (Many-to-One, **Inactive**).

```dax
Views by Hour =
CALCULATE(
    [Total Views],
    USERELATIONSHIP(PageViews[view_hour], HourTable[Hour])
)
```

---

## 5. Page 1 — Overview

**Purpose**: High-level KPIs and trends at a glance.

### KPI Cards (top row)

| Card | Measure | Format | Notes |
|------|---------|--------|-------|
| Total Views | `[Total Views]` | #,0 | |
| Unique Visitors | `[Unique Visitors]` | #,0 | |
| Sessions | `[Total Sessions]` | #,0 | Uses USERELATIONSHIP — responds to date slicer |
| Bounce Rate | `[Bounce Rate]` | 0.0% | Uses USERELATIONSHIP — responds to date slicer |
| Avg Time on Page | `[Avg Time on Page (formatted)]` | Text | |
| Avg Pages/Session | `[Avg Pages per Session]` | 0.0 | Uses USERELATIONSHIP — responds to date slicer |

### Visuals

| Visual | Axis / Legend | Values | Notes |
|--------|--------------|--------|-------|
| Line Chart — Views Trend | DateTable[date] | `[Total Views]`, `[Unique Visitors]` | Dual axis |
| Line Chart — MoM Change | DateTable[YearMonth] | `[Views MoM Change %]`, `[UV MoM Change %]` | Show as % |
| Bar Chart — Top 10 Sites | Pages[site_name] | `[Total Views]` | Top N filter = 10 |
| Bar Chart — Top 10 Pages | Pages[page_name] | `[Total Views]` | Top N filter = 10 |

### Slicers

- DateTable[date] → Date range slicer
- Pages[site_name] → Dropdown

---

## 6. Page 2 — Content Performance

**Purpose**: Which pages, sites, themes, and topics drive traffic and engagement.

### Visuals

| Visual | Axis / Legend | Values | Notes |
|--------|--------------|--------|-------|
| Table — Page Detail | Pages[page_name], Pages[site_name] | `[Total Views]`, `[Unique Visitors]`, `[Avg Time on Page (formatted)]` | Sort by Views DESC |
| Bar Chart — Views by Theme | Pages[theme] | `[Total Views]`, `[Unique Visitors]` | Clustered bar |
| Bar Chart — Views by Topic | Pages[topic] | `[Total Views]` | |
| Scatter — Views vs Engagement | X: `[Total Views]`, Y: `[Avg Time on Page (sec)]` | Details: Pages[page_name] | Identify high-traffic low-engagement pages |

### Red Flag Table

Add a calculated measure to spot content issues:

```dax
Low Engagement Flag =
IF(
    [Total Views] > 100 && [Avg Time on Page (sec)] < 10,
    "High traffic, low engagement",
    IF(
        [Total Views] < 20 && [Avg Time on Page (sec)] > 120,
        "Low traffic, high engagement",
        ""
    )
)
```

> **Note**: The thresholds (100 views, 10 sec, etc.) are starting points. Adjust based on your actual data distribution after the first load.

---

## 7. Page 3 — Divisions & Regions

**Purpose**: Intranet adoption across the organisation.

### Visuals

| Visual | Axis / Legend | Values | Notes |
|--------|--------------|--------|-------|
| Bar Chart — Views by Division | PageViews[Division] | `[Total Views]`, `[Unique Visitors]` | Clustered bar |
| Bar Chart — Views by Region | PageViews[Region] | `[Total Views]`, `[Unique Visitors]` | |
| Matrix — Division x Theme | Rows: PageViews[Division], Columns: Pages[theme] | `[Total Views]` | Heatmap conditional formatting |
| Bar Chart — By Management Level | PageViews[hr_management_level] | `[Unique Visitors]` | Are senior leaders using the intranet? |
| Table — Division Detail | PageViews[Division] | `[Total Views]`, `[Unique Visitors]`, `[Avg Time on Page (formatted)]` | |

> **Note on Bounce Rate by Division**: Do not place `[Bounce Rate]` in a table sliced by PageViews[Division]. The Sessions table has its own `hr_division` from the first pageview in the session — use the dedicated measure below instead:

```dax
Bounce Rate by Division =
CALCULATE(
    DIVIDE(
        COUNTROWS(FILTER(Sessions, Sessions[is_bounce] = TRUE)),
        COUNTROWS(Sessions),
        0
    ),
    USERELATIONSHIP(Sessions[session_date], DateTable[date])
)
```

This measure uses the Sessions table's own Division column (which is in filter context when sliced by Sessions[Division]).

### Slicers

- PageViews[Division] → Dropdown (in addition to date/site)
- PageViews[Region] → Dropdown

---

## 8. Page 4 — Sessions & Engagement

**Purpose**: How visitors navigate the intranet — session depth, bounces, entry/exit pages.

### KPI Cards

| Card | Measure | Format |
|------|---------|--------|
| Bounce Rate | `[Bounce Rate]` | 0.0% |
| Avg Pages/Session | `[Avg Pages per Session]` | 0.0 |
| Avg Session Duration | `[Avg Session Duration (formatted)]` | Text |

All three use USERELATIONSHIP and respond to the date slicer.

### Visuals

| Visual | Axis / Legend | Values | Notes |
|--------|--------------|--------|-------|
| Histogram — Pages per Session | Sessions[page_view_count] | Count of session_id | Distribution: how deep do users go? |
| Bar Chart — Top Entry Pages | Pages[page_name] | `[Entry Page Sessions]` | Where users land |
| Bar Chart — Top Exit Pages | Pages[page_name] | `[Exit Page Sessions]` | Where users leave |
| Bar Chart — Bounce Rate by Page | Pages[page_name] | `[Page Bounce Rate]` | Which pages lose users immediately? |
| Line Chart — Bounce Rate Trend | DateTable[date] | `[Bounce Rate]` | Is it improving? |

### Entry/Exit Page Measures

Because Sessions → Pages is inactive, use explicit FILTER:

```dax
Entry Page Sessions =
VAR CurrentPage = SELECTEDVALUE(Pages[page_id])
RETURN
CALCULATE(
    COUNTROWS(Sessions),
    FILTER(Sessions, Sessions[entry_page_id] = CurrentPage),
    USERELATIONSHIP(Sessions[session_date], DateTable[date])
)

Exit Page Sessions =
VAR CurrentPage = SELECTEDVALUE(Pages[page_id])
RETURN
CALCULATE(
    COUNTROWS(Sessions),
    FILTER(Sessions, Sessions[exit_page_id] = CurrentPage),
    USERELATIONSHIP(Sessions[session_date], DateTable[date])
)
```

### Heatmap — Views by Hour and Weekday

Use a Matrix visual: Rows = DateTable[day_of_week], Columns = HourTable[Hour], Values = `[Views by Hour]`. Apply conditional formatting (background color) for the heatmap effect.

---

## 9. Slicers & Cross-Filtering

### Recommended Global Slicers (on every page)

| Slicer | Source | Type |
|--------|--------|------|
| Date Range | DateTable[date] | Between (date range) |
| Site | Pages[site_name] | Dropdown |
| Division | PageViews[Division] | Dropdown |
| Region | PageViews[Region] | Dropdown |

### Sync Slicers

View → Sync Slicers → enable sync across all pages for Date Range and Site.

### Cross-Filter Behaviour

- The Date slicer filters **DateTable**, which flows to **PageViews** (active) but **not Sessions** (inactive). All Session measures handle this via `USERELATIONSHIP()`.
- The Site slicer filters **Pages**, which flows to **PageViews** (active) but **not Sessions** (inactive). Session measures that need page context use explicit `FILTER()`.
- Division/Region slicers filter **PageViews** directly. For Session visuals on Page 4, add a separate slicer on Sessions[Division] if needed.

### Cross-Filter Tips

- Set all relationships to **Single** direction for predictable filtering
- Use **Edit Interactions** (Format → Edit Interactions) to disable cross-filtering between visuals that should be independent
- For the Division x Theme matrix: enable bidirectional filtering on the PageViews → Pages relationship, or use `CROSSFILTER()` in DAX

---

## 10. Aggregation Caveats

### Time on Page

- `time_on_page_sec` is NULL for the **last page** in every session and for gaps **>30 minutes**
- DAX `AVERAGE` ignores NULLs — this is correct behaviour, but means the average is based only on measurable views
- Use `[Measurable Views %]` to monitor what fraction of views have a valid time-on-page
- When filtering to a single page with few views, the average may be volatile (small sample)

### Session Duration

- `[Avg Session Duration]` **excludes bounces** (sessions with only 1 page view) because their duration is 0 by definition
- This gives a more meaningful engagement metric but means the measure only reflects multi-page visits
- The bounce rate tells you what fraction of sessions are excluded

### HR Division Slicing

- Each page view has the HR division valid at the **time of the event** (temporal join)
- A user who **changes division** mid-period will appear in both divisions — this is correct per-event
- However: `Unique Visitors` summed across all divisions may exceed the total `Unique Visitors` (a user is counted once per division they belonged to)
- This is standard behaviour in all time-variant dimensional models

### Page Bounce Rate

- `[Page Bounce Rate]` uses `SELECTEDVALUE(Pages[page_id])` — it requires a **single page** in filter context
- Works in: table rows, bar chart axis, tooltip
- Returns BLANK in: card/KPI without page context, multi-select slicer

### Sessions and Slicers

- All Session-based measures use `USERELATIONSHIP()` to activate the inactive DateTable relationship
- If a Session measure does **not** respond to the date slicer, it is missing `USERELATIONSHIP()` — check the formula

---

## 11. Appendix — Full DAX Reference

All measures in one place for easy copy-paste.

```dax
// ============================================================
// CORE KPIs (PageViews-based)
// ============================================================

Total Views = COUNTROWS(PageViews)

Unique Visitors = DISTINCTCOUNT(PageViews[user_id])

Views per Visitor =
DIVIDE([Total Views], [Unique Visitors], 0)


// ============================================================
// CORE KPIs (Session-based — all use USERELATIONSHIP)
// ============================================================

Total Sessions =
CALCULATE(
    COUNTROWS(Sessions),
    USERELATIONSHIP(Sessions[session_date], DateTable[date])
)

Bounce Rate =
VAR TotalSess = [Total Sessions]
VAR Bounces =
    CALCULATE(
        COUNTROWS(FILTER(Sessions, Sessions[is_bounce] = TRUE)),
        USERELATIONSHIP(Sessions[session_date], DateTable[date])
    )
RETURN DIVIDE(Bounces, TotalSess, 0)

Avg Pages per Session =
CALCULATE(
    AVERAGE(Sessions[page_view_count]),
    USERELATIONSHIP(Sessions[session_date], DateTable[date])
)


// ============================================================
// ENGAGEMENT
// ============================================================

Avg Time on Page (sec) = AVERAGE(PageViews[time_on_page_sec])

Avg Time on Page (formatted) =
VAR AvgSec = [Avg Time on Page (sec)]
RETURN IF(ISBLANK(AvgSec), BLANK(), FORMAT(AvgSec / 86400, "m:ss"))

// Excludes bounces — their duration is 0 by definition
Avg Session Duration (sec) =
CALCULATE(
    AVERAGEX(
        FILTER(Sessions, Sessions[is_bounce] = FALSE),
        Sessions[engagement_time_sec]
    ),
    USERELATIONSHIP(Sessions[session_date], DateTable[date])
)

Avg Session Duration (formatted) =
VAR AvgSec = [Avg Session Duration (sec)]
RETURN IF(ISBLANK(AvgSec), BLANK(), FORMAT(AvgSec / 86400, "m:ss"))

Measurable Views % =
DIVIDE(
    COUNTROWS(FILTER(PageViews, NOT(ISBLANK(PageViews[time_on_page_sec])))),
    [Total Views], 0
)

Avg Page Load (ms) = AVERAGE(PageViews[page_load_ms])


// ============================================================
// CONTENT
// ============================================================

Views per Page =
DIVIDE([Total Views], DISTINCTCOUNT(PageViews[page_id]), 0)

UV per Page =
DIVIDE([Unique Visitors], DISTINCTCOUNT(PageViews[page_id]), 0)

Low Engagement Flag =
IF(
    [Total Views] > 100 && [Avg Time on Page (sec)] < 10,
    "High traffic, low engagement",
    IF(
        [Total Views] < 20 && [Avg Time on Page (sec)] > 120,
        "Low traffic, high engagement",
        ""
    )
)


// ============================================================
// ORGANISATIONAL COVERAGE
// ============================================================

HR Coverage % =
DIVIDE(
    COUNTROWS(FILTER(PageViews, NOT(ISBLANK(PageViews[hr_division])))),
    [Total Views], 0
) * 100

Unique Divisions = DISTINCTCOUNT(PageViews[hr_division])

Bounce Rate by Division =
CALCULATE(
    DIVIDE(
        COUNTROWS(FILTER(Sessions, Sessions[is_bounce] = TRUE)),
        COUNTROWS(Sessions), 0
    ),
    USERELATIONSHIP(Sessions[session_date], DateTable[date])
)


// ============================================================
// TIME INTELLIGENCE
// ============================================================

Views Previous Month =
CALCULATE([Total Views], DATEADD(DateTable[date], -1, MONTH))

Views MoM Change % =
VAR Current = [Total Views]
VAR Previous = [Views Previous Month]
RETURN DIVIDE(Current - Previous, Previous, 0)

UV Previous Month =
CALCULATE([Unique Visitors], DATEADD(DateTable[date], -1, MONTH))

UV MoM Change % =
VAR Current = [Unique Visitors]
VAR Previous = [UV Previous Month]
RETURN DIVIDE(Current - Previous, Previous, 0)

Views Previous Week =
CALCULATE([Total Views], DATEADD(DateTable[date], -7, DAY))

Views WoW Change % =
VAR Current = [Total Views]
VAR Previous = [Views Previous Week]
RETURN DIVIDE(Current - Previous, Previous, 0)

Running Total Views =
CALCULATE([Total Views], DATESYTD(DateTable[date]))


// ============================================================
// PAGE-LEVEL (require single page in context)
// ============================================================

Page Bounce Rate =
VAR CurrentPage = SELECTEDVALUE(Pages[page_id])
VAR EntrySessions =
    CALCULATE(
        COUNTROWS(Sessions),
        FILTER(Sessions, Sessions[entry_page_id] = CurrentPage),
        USERELATIONSHIP(Sessions[session_date], DateTable[date])
    )
VAR BounceEntries =
    CALCULATE(
        COUNTROWS(Sessions),
        FILTER(Sessions,
            Sessions[entry_page_id] = CurrentPage
            && Sessions[is_bounce] = TRUE),
        USERELATIONSHIP(Sessions[session_date], DateTable[date])
    )
RETURN DIVIDE(BounceEntries, EntrySessions, 0)

Entry Page Sessions =
VAR CurrentPage = SELECTEDVALUE(Pages[page_id])
RETURN
CALCULATE(
    COUNTROWS(Sessions),
    FILTER(Sessions, Sessions[entry_page_id] = CurrentPage),
    USERELATIONSHIP(Sessions[session_date], DateTable[date])
)

Exit Page Sessions =
VAR CurrentPage = SELECTEDVALUE(Pages[page_id])
RETURN
CALCULATE(
    COUNTROWS(Sessions),
    FILTER(Sessions, Sessions[exit_page_id] = CurrentPage),
    USERELATIONSHIP(Sessions[session_date], DateTable[date])
)


// ============================================================
// HEATMAP
// ============================================================

Views by Hour =
CALCULATE(
    [Total Views],
    USERELATIONSHIP(PageViews[view_hour], HourTable[Hour])
)
```

### Helper Tables (DAX Calculated Tables)

```dax
// Hour table for heatmap
HourTable = GENERATESERIES(0, 23, 1)
// Rename column to "Hour"
```
