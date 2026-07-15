# Page Engagement Beacon — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Everything on *our* side of the `page_engagement` beacon spec that is executable before the platform team ships the snippet: Phase-0 validation KQL, the consumer audit, pipeline readiness (flattener, aggregation script, dormant page-view join with consistency QA), and DOCX exports of design + plan for corporate sharing.

**Architecture:** The platform team will emit a new `customEvents` family `page_engagement` (delta flushes of visible time + scroll depth per view instance, correlated via `View_Instance_Id`). We prepare the local pipeline so that the day the first export lands, `input/engagement/*.csv` → flush-grain store → view-instance aggregate → left-join onto the page-view store as new derived columns `engaged_ms` / `scroll_max_pct`. Until then, every new code path is exercised by plain-assert tests on synthetic frames and stays dormant on real data.

**Tech Stack:** Python 3 + pandas (house pipeline), KQL (Application Insights), pandoc 3.8.2 (DOCX export). No test framework — plain-assert scripts run via `python scripts/test_*.py` (house convention, see `SiteOwnerDashboard/scripts/test_derive_person_visit.py`).

**Spec:** `docs/superpowers/specs/2026-07-15-page-engagement-heartbeat-design.md` — referenced below as "the spec".

## Global Constraints

- Event family name is exactly `page_engagement`; it must NEVER flow into click aggregates (spec §3, §5).
- `View_Instance_Id` is the default correlation key; `operation_Id` is QA-only (spec §3.5).
- Delta semantics: pipeline SUMs `Engaged_Ms` deltas per view instance; scroll is MAX (spec §3.3, §5).
- Never rename/overwrite a source column; add clearly named derived columns (house rule; spec §5).
- Consistency check: for views with a next view, `engaged_ms/1000 ≤ time_on_page_visit_sec` (+1 s tolerance); report violation rate, warn > 1% (spec §5, §8).
- Technical docs in English; spell out acronyms on first use (house rule).
- All new/changed pipeline code follows the existing incremental pattern: SHA-256 manifest + composite-key upsert (house pattern).
- pandas ≥ 3 caveat: arrow-backed string columns — cast to numpy bool before `cumsum` etc. (see comment in `scripts/flatten_appinsights.py:576`).

## Out of scope (deliberately)

- **Dashboard metric switch** (spec §5 last bullets, rollout Phase 3): depends on real pilot data shape and is months out. Gets its own plan when Phase 2 data exists. The dormant join in Task 5 makes activation a dashboard-only change.
- **The snippet itself**: platform-team property. Our deliverable to them is the spec + validation queries + audit results (Tasks 1–2) as DOCX (Task 6).

---

### Task 1: Phase-0 validation KQL (V1, V2, volume baseline)

**Files:**
- Create: `kql/validate_page_engagement.kql`
- Modify: `kql/README.md` (add one catalog entry)

**Interfaces:**
- Consumes: spec §4 (queries V1, V2 verbatim).
- Produces: a runnable KQL file the platform team (or we, via portal access) executes; results decide operation_Id usability and sampling posture. No code interfaces.

- [ ] **Step 1: Write the KQL file**

```kql
// validate_page_engagement.kql
// ---------------------------------------------------------------------------
// Phase-0 validation for the page_engagement beacon proposal
// (docs/superpowers/specs/2026-07-15-page-engagement-heartbeat-design.md §4).
// Run against the Application Insights resource (Logs blade). Each query is
// independent — select one block and run it.
// ---------------------------------------------------------------------------

// === V1: operation_Id stability across SPA route changes ====================
// Question: do click_events share the operation_Id of their logical page view?
// Decides whether operation_Id is usable as a QA cross-check for the
// View_Instance_Id correlation (it is NOT the primary key either way).
// Expected outcome: match_rate close to 1.0 → usable as QA cross-check.
//                   match_rate well below 1.0 → operation_Id is unstable under
//                   SPA routing; rely on View_Instance_Id only.
customEvents
| where timestamp > ago(7d) and name == 'click_event'
| extend CP = todynamic(tostring(todynamic(customDimensions).CustomProps))
| project ev_op = operation_Id, ev_page = tostring(CP.PageId), session_Id
| join kind=inner (
    pageViews
    | where timestamp > ago(7d)
    | extend CP = todynamic(tostring(todynamic(customDimensions).CustomProps))
    | project pv_op = operation_Id, pv_page = tostring(CP.PageId), session_Id
  ) on session_Id
| summarize op_match = countif(ev_op == pv_op and ev_page == pv_page),
            total    = countif(ev_page == pv_page)
| extend match_rate = todouble(op_match) / total

// === V2: ingestion sampling check ===========================================
// If sampling is active (itemCount > 1 on any rows), page_engagement must be
// sampled at the SAME rate as pageViews, or engagement-per-view ratios are
// systematically biased. Platform-team checklist item.
// Expected outcome: sampled == 0 on both streams (no sampling), OR equal
//                   effective rates — compare sum(itemCount)/count() per type.
union pageViews, customEvents
| where timestamp > ago(1d)
| summarize sampled_rows = countif(itemCount > 1),
            physical_rows = count(),
            logical_rows = sum(itemCount) by itemType
| extend effective_rate = todouble(logical_rows) / physical_rows

// === V3: volume baseline for the flush estimate =============================
// The spec (§7) estimates ~2 flushes/page view => up to +350M events/year.
// This grounds the estimate in the platform team's own numbers before they
// push back on it: daily pageViews vs daily customEvents, last 30 days.
union pageViews, customEvents
| where timestamp > ago(30d)
| summarize daily_rows = count() by itemType, bin(timestamp, 1d)
| summarize avg_daily = avg(daily_rows), max_daily = max(daily_rows) by itemType
```

- [ ] **Step 2: Add the catalog entry to `kql/README.md`**

Append to the file list (match the existing entry format in that file):

```markdown
- `validate_page_engagement.kql` — Phase-0 validation for the page_engagement
  beacon proposal (operation_Id stability, sampling check, volume baseline).
  See `docs/superpowers/specs/2026-07-15-page-engagement-heartbeat-design.md` §4.
```

- [ ] **Step 3: Verify the file parses as text and the queries match the spec**

Run: `grep -c "^// === V" kql/validate_page_engagement.kql`
Expected: `3`

Manually diff the V1/V2 blocks against spec §4 — they must be verbatim (plus comments).

- [ ] **Step 4: Commit**

```bash
git add kql/validate_page_engagement.kql kql/README.md
git commit -m "kql: Phase-0 validation queries for page_engagement beacon (V1-V3)"
```

---

### Task 2: Consumer audit — customEvents readers without a name filter (spec V3)

**Files:**
- Create: `docs/audits/2026-07-15-customevents-name-filter-audit.md`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: the audit document — part of the platform proposal package (Task 6 exports it if findings warrant sharing). Identifies every consumer that would silently count `page_engagement` as clicks.

- [ ] **Step 1: Run the audit greps**

```bash
cd /Users/micha/Documents/Arbeit
grep -rn -iE "customevents" \
  --include="*.kql" --include="*.py" --include="*.sql" \
  --include="*.js" --include="*.html" --include="*.md" \
  Clicks/ CampaignWe/ Databricks/ \
  | grep -v -E "\.git/|node_modules/|docs/superpowers/|/audits/" > /tmp/audit_hits.txt
wc -l /tmp/audit_hits.txt
```

For every hit file, check whether the read is guarded by an event-family filter:

```bash
for f in $(cut -d: -f1 /tmp/audit_hits.txt | sort -u); do
  echo "== $f"
  grep -nE "name\s*==|event_name\s*==|name in|where name" "$f" | head -5
done
```

- [ ] **Step 2: Write the audit document**

Structure (fill the table from Step 1 results — one row per consumer file; the
rows below are the format, not the findings):

```markdown
# Audit: customEvents consumers vs. `name` discrimination

**Date:** 2026-07-15 · **Trigger:** page_engagement beacon proposal
(`docs/superpowers/specs/2026-07-15-page-engagement-heartbeat-design.md` §4, V3)

**Question:** which consumers read the Application Insights `customEvents`
stream without filtering on the event-family discriminator `name` — and would
therefore silently inflate their numbers when the `page_engagement` family
ships?

## Scope

Our repositories: `Clicks/`, `CampaignWe/`, `Databricks/` (including
`SiteOwnerDashboard/`). NOT covered: the platform team's bronze→gold
notebooks — flagged as their checklist item in the proposal (spec §4, V3).

## Findings

| File | How it reads customEvents | name-filtered? | Risk when page_engagement ships | Action |
|---|---|---|---|---|
| (path:line) | (KQL where / pandas filter / ...) | yes/no | none / inflated counts / broken schema assumptions | none / add filter |

## Verdict

(One paragraph: how many consumers, how many unguarded, which need fixes
before Phase 1. If all guarded: state that explicitly — it is the result the
proposal cites.)
```

Known-guarded examples the audit should confirm (from code already read):
`Databricks/kql/customevents_clicks.kql:26` (`| where name == 'click_event'`),
`SiteOwnerDashboard/scripts/process_site_interactions.py:200`
(`clean[clean["event_name"] == "click_event"]`).

- [ ] **Step 3: Verify completeness**

Run: `cut -d: -f1 /tmp/audit_hits.txt | sort -u | wc -l`
Expected: every one of these files appears in the Findings table (count of
table rows ≥ count of distinct files, minus pure-documentation hits, which get
one collective row).

- [ ] **Step 4: Commit**

```bash
git add docs/audits/2026-07-15-customevents-name-filter-audit.md
git commit -m "docs: audit customEvents consumers for name-filter discipline (spec V3)"
```

---

### Task 3: Flattener support for the page_engagement payload

**Files:**
- Modify: `scripts/flatten_appinsights.py` (two dicts: `FACT_COLUMNS` at line ~63, `INTERACTION_FACT_COLUMNS` at line ~120)
- Test: `SiteOwnerDashboard/scripts/test_flatten_page_engagement.py` (create)

**Interfaces:**
- Consumes: existing `flatten_appinsights(df)` (double-parses `customDimensions.CustomProps` into `cp_*` columns — new keys appear automatically) and `build_clean_interactions_table(df)` (renames via `INTERACTION_FACT_COLUMNS`).
- Produces: snake_case columns consumed by Task 4: `view_instance_id` (string), `engaged_ms_delta` (numeric), `scroll_max_pct`, `page_height_px`, `viewport_height_px` (numeric), `flush_reason` (string), `flush_seq` (numeric). On the pageViews side: `view_instance_id` (string) — consumed by Task 5.

- [ ] **Step 1: Write the failing test**

Create `SiteOwnerDashboard/scripts/test_flatten_page_engagement.py`:

```python
"""
Tests the flattener mappings for the page_engagement customEvents family
(spec: docs/superpowers/specs/2026-07-15-page-engagement-heartbeat-design.md §3.5).

Plain-assert script (no test framework in this repo):
    python SiteOwnerDashboard/scripts/test_flatten_page_engagement.py
Exits non-zero on the first failing assertion.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
# process_site_pageviews puts the shared pipeline dir on sys.path
from process_site_pageviews import canonical_page_key  # noqa: E402,F401
from flatten_appinsights import (  # noqa: E402
    flatten_appinsights,
    build_clean_interactions_table,
    build_clean_table,
)


def make_custom_dimensions(props: dict) -> str:
    """AppInsights double-nesting: customDimensions.CustomProps is a JSON string."""
    return json.dumps({"CustomProps": json.dumps(props)})


def engagement_raw() -> pd.DataFrame:
    """One page_engagement flush as it arrives in a customEvents export."""
    props = {
        "GPN": "00100200", "Email": "a@corp.example",
        "SiteID": "s-1", "SiteName": "News and events",
        "PageId": "p-42", "PageName": "Article",
        "PageURL": "https://intranet.example/en/article.aspx",
        "View_Instance_Id": "vi-0001",
        "Engaged_Ms": "5000", "Scroll_Max_Pct": "40",
        "Page_Height_Px": "3200", "Viewport_Height_Px": "900",
        "Flush_Reason": "route", "Flush_Seq": "0",
    }
    return pd.DataFrame([{
        "timestamp [UTC]": "2026-07-15 08:00:00.000",
        "id": "ev-1", "name": "page_engagement",
        "user_Id": "u-1", "session_Id": "sess-1",
        "client_OS": "Windows", "client_Browser": "Edge",
        "client_CountryOrRegion": "Switzerland",
        "customDimensions": make_custom_dimensions(props),
    }])


def pageview_raw() -> pd.DataFrame:
    """One pageView carrying View_Instance_Id in its property bag (spec §3.5/§9)."""
    props = {
        "GPN": "00100200", "PageId": "p-42",
        "PageURL": "https://intranet.example/en/article.aspx",
        "View_Instance_Id": "vi-0001",
    }
    return pd.DataFrame([{
        "timestamp [UTC]": "2026-07-15 08:00:00.000",
        "id": "pv-1", "name": "https://intranet.example/en/article.aspx",
        "user_Id": "u-1", "session_Id": "sess-1", "duration": "123",
        "customDimensions": make_custom_dimensions(props),
    }])


def main() -> None:
    # customEvents side
    clean = build_clean_interactions_table(flatten_appinsights(engagement_raw()))
    for col in ("view_instance_id", "engaged_ms_delta", "scroll_max_pct",
                "page_height_px", "viewport_height_px", "flush_reason", "flush_seq"):
        assert col in clean.columns, f"missing engagement column: {col}"
    row = clean.iloc[0]
    assert row["event_name"] == "page_engagement"
    assert row["view_instance_id"] == "vi-0001"
    assert str(row["engaged_ms_delta"]) == "5000", row["engaged_ms_delta"]
    assert str(row["flush_reason"]) == "route"

    # pageViews side
    pv = build_clean_table(flatten_appinsights(pageview_raw()))
    assert "view_instance_id" in pv.columns, "pageViews flatten must map View_Instance_Id"
    assert pv.iloc[0]["view_instance_id"] == "vi-0001"

    # click_event untouched: a click without the new keys still flattens clean
    click = engagement_raw().assign(name="click_event")
    cc = build_clean_interactions_table(flatten_appinsights(click))
    assert cc.iloc[0]["event_name"] == "click_event"

    print("OK — flattener maps page_engagement payload on both streams")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 SiteOwnerDashboard/scripts/test_flatten_page_engagement.py`
Expected: `AssertionError: missing engagement column: view_instance_id`

- [ ] **Step 3: Add the mappings**

In `scripts/flatten_appinsights.py`, append to `INTERACTION_FACT_COLUMNS`
(after the Video sub-domain block, keeping the comment style):

```python
    # page_engagement family (spec 2026-07-15-page-engagement-heartbeat-design)
    "cp_View_Instance_Id": "view_instance_id",
    "cp_Engaged_Ms": "engaged_ms_delta",
    "cp_Scroll_Max_Pct": "scroll_max_pct",
    "cp_Page_Height_Px": "page_height_px",
    "cp_Viewport_Height_Px": "viewport_height_px",
    "cp_Flush_Reason": "flush_reason",
    "cp_Flush_Seq": "flush_seq",
```

Append to `FACT_COLUMNS` (pageViews side):

```python
    # correlation key for page_engagement joins (payload-only pageView change)
    "cp_View_Instance_Id": "view_instance_id",
```

Note: `INTERACTION_DROP_COLUMNS` keeps dropping `operation_Id` — correct per
spec §3.5 (operation_Id is QA-only, evaluated in the portal via V1, never in
the local pipeline).

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 SiteOwnerDashboard/scripts/test_flatten_page_engagement.py`
Expected: `OK — flattener maps page_engagement payload on both streams`

- [ ] **Step 5: Run the existing regression test**

Run: `python3 SiteOwnerDashboard/scripts/test_derive_person_visit.py`
Expected: exits 0 (rename maps only touch columns that are present — click and
pageview processing without the new keys is unchanged).

- [ ] **Step 6: Commit**

```bash
git add scripts/flatten_appinsights.py SiteOwnerDashboard/scripts/test_flatten_page_engagement.py
git commit -m "feat: flattener mappings for page_engagement family (both streams)"
```

---

### Task 4: Aggregation script `process_page_engagement.py`

**Files:**
- Create: `SiteOwnerDashboard/scripts/process_page_engagement.py`
- Test: `SiteOwnerDashboard/scripts/test_process_page_engagement.py` (create)

**Interfaces:**
- Consumes: Task 3 columns (`view_instance_id`, `engaged_ms_delta`, `scroll_max_pct`, `flush_reason`, `flush_seq`, …); shared helpers from `process_site_pageviews` (`manifest_path`, `compute_file_hash`, `load_manifest`, `save_manifest`, `partition_files`, `upsert_store`, `add_event_key`, `derive_language`, `canonical_page_key`) and `flatten_appinsights` (`read_input`, `flatten_appinsights`, `build_clean_interactions_table`, `strip_tz`).
- Produces:
  - `SiteOwnerDashboard/output/engagement_flushes.parquet` — flush grain, upserted on key `["view_instance_id", "flush_seq"]`.
  - `SiteOwnerDashboard/output/page_engagement.parquet` — view-instance grain, rebuilt from the full flush store each run. Columns consumed by Task 5: `view_instance_id`, `engaged_ms` (int, SUM of deltas), `scroll_max_pct` (int, MAX), `flush_count`, `has_seq_gap` (bool), plus context (`page_id`, `page_key`, `page_url`, `language`, `person_id`, `session_id`, `first_ts`, `last_flush_reason`, `page_height_px`, `viewport_height_px` — the last two are the Read-Rate inputs from spec §5).
  - Function `aggregate_engagement(flushes: pd.DataFrame) -> pd.DataFrame` — the tested unit.

- [ ] **Step 1: Write the failing test**

Create `SiteOwnerDashboard/scripts/test_process_page_engagement.py`:

```python
"""
Tests aggregate_engagement() — delta summation, scroll MAX, double-fire
tolerance and beacon-loss detection per spec §3.3/§5.

Plain-assert script:  python SiteOwnerDashboard/scripts/test_process_page_engagement.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from process_page_engagement import aggregate_engagement  # noqa: E402


def flush(vi, seq, ms, scroll, reason, ts, page="p-42",
          url="https://intranet.example/en/a.aspx", person="00100200"):
    return {"view_instance_id": vi, "flush_seq": seq, "engaged_ms_delta": ms,
            "scroll_max_pct": scroll, "flush_reason": reason,
            "timestamp": pd.Timestamp(ts), "page_id": page, "page_url": url,
            "person_id": person, "session_id": "s-1"}


def main() -> None:
    rows = [
        # View A: route flush + hidden flush + zero-delta pagehide double-fire
        flush("vi-A", 0, 5000, 40, "route",    "2026-07-15 08:00:05"),
        flush("vi-A", 1, 2000, 80, "hidden",   "2026-07-15 08:00:30"),
        flush("vi-A", 2,    0, 80, "pagehide", "2026-07-15 08:00:30"),
        # View B: flush_seq 0 and 2 — seq 1 lost (beacon loss)
        flush("vi-B", 0, 1000, 25, "route",    "2026-07-15 09:00:01"),
        flush("vi-B", 2,  500, 60, "pagehide", "2026-07-15 09:01:00"),
        # View C: single-flush view (the 73% case the beacon exists for)
        flush("vi-C", 0, 42000, 100, "pagehide", "2026-07-15 10:00:42"),
    ]
    agg = aggregate_engagement(pd.DataFrame(rows))
    agg = agg.set_index("view_instance_id")
    assert len(agg) == 3, f"expected 3 view instances, got {len(agg)}"

    a = agg.loc["vi-A"]
    assert a["engaged_ms"] == 7000, f"A: deltas must SUM (5000+2000+0), got {a['engaged_ms']}"
    assert a["scroll_max_pct"] == 80
    assert a["flush_count"] == 3
    assert not a["has_seq_gap"], "A has contiguous seq 0..2 — no gap"
    assert a["last_flush_reason"] == "pagehide"

    b = agg.loc["vi-B"]
    assert b["engaged_ms"] == 1500
    assert b["has_seq_gap"], "B is missing seq 1 — gap must be flagged"

    c = agg.loc["vi-C"]
    assert c["engaged_ms"] == 42000 and c["flush_count"] == 1

    print("OK — aggregate_engagement sums deltas, flags gaps, tolerates double-fires")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 SiteOwnerDashboard/scripts/test_process_page_engagement.py`
Expected: `ModuleNotFoundError: No module named 'process_page_engagement'`

- [ ] **Step 3: Write the script**

Create `SiteOwnerDashboard/scripts/process_page_engagement.py`:

```python
"""
Build the page_engagement Parquet stores for the SiteOwnerDashboard.

Reads customEvents exports containing the `page_engagement` family
(spec: docs/superpowers/specs/2026-07-15-page-engagement-heartbeat-design.md)
from input/engagement/, upserts the flush-grain store, and rebuilds the
view-instance aggregate from the FULL flush store each run:

    input/engagement/*.csv|xlsx
      -> output/engagement_flushes.parquet   (grain: 1 row per flush;
                                              PK view_instance_id x flush_seq)
      -> output/page_engagement.parquet      (grain: 1 row per view instance;
                                              engaged_ms = SUM of deltas,
                                              scroll_max_pct = MAX)

Delta semantics (spec §3.3): each flush carries milliseconds since the LAST
flush; double-fires arrive as 0-deltas and are harmless under SUM. flush_seq
gaps quantify beacon loss (has_seq_gap).

Usage:
    python scripts/process_page_engagement.py            # incremental
    python scripts/process_page_engagement.py --rebuild  # from scratch
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parents[0]
sys.path.insert(0, str(SCRIPT_DIR))

from process_site_pageviews import (  # noqa: E402
    derive_language,
    canonical_page_key,
    manifest_path,
    compute_file_hash,
    load_manifest,
    save_manifest,
    partition_files,
    add_event_key,
    upsert_store,
)
from flatten_appinsights import (  # noqa: E402
    read_input,
    flatten_appinsights,
    build_clean_interactions_table,
    strip_tz,
)

INPUT_DIR = PROJECT_DIR / "input" / "engagement"
FLUSH_KEY_COLS = ["view_instance_id", "flush_seq"]

FLUSH_COLUMNS = [
    "timestamp", "event_name", "view_instance_id",
    "engaged_ms_delta", "scroll_max_pct", "page_height_px",
    "viewport_height_px", "flush_reason", "flush_seq",
    "page_id", "page_key", "page_name", "page_url", "language",
    "site_id", "site_name",
    "user_id", "session_id", "person_id", "email", "gpn",
    "source_file",
]

NUMERIC_COLS = ["engaged_ms_delta", "scroll_max_pct", "page_height_px",
                "viewport_height_px", "flush_seq"]


def derive_person(df: pd.DataFrame) -> pd.DataFrame:
    """ADD person_id = GPN where present, else 'anon:<user_id>' (same as pv/ix)."""
    df = df.copy()
    if "gpn" in df.columns:
        person = df["gpn"].astype("string")
    else:
        person = pd.Series(pd.NA, index=df.index, dtype="string")
    if "user_id" in df.columns:
        person = person.fillna("anon:" + df["user_id"].astype("string").fillna(""))
    else:
        person = person.fillna("anon:unknown")
    df["person_id"] = person
    return df


def looks_like_engagement(raw: pd.DataFrame, name: str) -> bool:
    """Guard against a pageViews/clicks export dropped into input/engagement/."""
    if "name" in raw.columns and raw["name"].astype(str).eq("page_engagement").any():
        return True
    print(f"  WARNING: {name} has no page_engagement rows — wrong export for "
          "input/engagement/. Skipping it.")
    return False


def build_one(input_path: Path) -> pd.DataFrame | None:
    print(f"  {input_path.name}:")
    raw = read_input(input_path)
    print(f"    {len(raw):,} raw rows")
    if not looks_like_engagement(raw, input_path.name):
        return None

    clean = build_clean_interactions_table(flatten_appinsights(raw))

    # page_engagement only — never let another family leak into this store
    # (Global Constraint: the family and click aggregates stay disjoint).
    if "event_name" in clean.columns:
        before = len(clean)
        clean = clean[clean["event_name"] == "page_engagement"]
        if len(clean) < before:
            print(f"    Kept page_engagement only: {len(clean):,}/{before:,} rows")

    for col in NUMERIC_COLS:
        if col in clean.columns:
            clean[col] = pd.to_numeric(clean[col], errors="coerce")

    dropped = int(clean["view_instance_id"].isna().sum()) if "view_instance_id" in clean.columns else len(clean)
    if dropped:
        print(f"    WARNING: {dropped:,} rows without view_instance_id dropped "
              "(cannot be correlated)")
        clean = clean[clean["view_instance_id"].notna()]

    clean["source_file"] = input_path.name
    return clean


def aggregate_engagement(flushes: pd.DataFrame) -> pd.DataFrame:
    """Flush grain -> view-instance grain (spec §5).

    engaged_ms   = SUM(engaged_ms_delta)   — delta semantics, double-fire safe
    scroll_max   = MAX(scroll_max_pct)
    has_seq_gap  = flush_count != max(flush_seq)+1  — beacon-loss indicator
    Context columns: first non-null per view instance.
    """
    df = flushes.copy()
    df["engaged_ms_delta"] = pd.to_numeric(df["engaged_ms_delta"], errors="coerce").fillna(0)
    df["flush_seq"] = pd.to_numeric(df["flush_seq"], errors="coerce")
    df = df.sort_values(["view_instance_id", "flush_seq"], kind="mergesort")

    g = df.groupby("view_instance_id", sort=False)
    agg = g.agg(
        engaged_ms=("engaged_ms_delta", "sum"),
        scroll_max_pct=("scroll_max_pct", "max"),
        flush_count=("flush_seq", "size"),
        _max_seq=("flush_seq", "max"),
        first_ts=("timestamp", "min"),
        last_flush_reason=("flush_reason", "last"),
    )
    context_cols = [c for c in ("page_id", "page_key", "page_url", "page_name",
                                "language", "site_id", "site_name",
                                "person_id", "session_id",
                                # Read-Rate inputs (spec §5) must survive to view grain
                                "page_height_px", "viewport_height_px") if c in df.columns]
    if context_cols:
        agg = agg.join(g[context_cols].first())

    agg["engaged_ms"] = agg["engaged_ms"].round().astype("int64")
    agg["has_seq_gap"] = agg["flush_count"] != (agg["_max_seq"] + 1)
    agg = agg.drop(columns=["_max_seq"])
    return agg.reset_index()


def main() -> None:
    ap = argparse.ArgumentParser(description="Build page_engagement Parquets for the dashboard")
    ap.add_argument("input", nargs="*",
                    help="customEvents export(s) with page_engagement rows "
                    "(default: all CSV/XLSX in input/engagement/)")
    ap.add_argument("--rebuild", action="store_true",
                    help="Ignore the manifest and reprocess ALL input files")
    ap.add_argument("-o", "--output",
                    help="Aggregate parquet (default: output/page_engagement.parquet)")
    args = ap.parse_args()

    agg_path = Path(args.output) if args.output else PROJECT_DIR / "output" / "page_engagement.parquet"
    flush_path = agg_path.with_name("engagement_flushes.parquet")
    agg_path.parent.mkdir(parents=True, exist_ok=True)

    mpath = manifest_path(flush_path)
    manifest = {} if args.rebuild else load_manifest(mpath)
    if manifest and not flush_path.exists():
        print(f"  {flush_path.name} missing — ignoring manifest, reprocessing everything")
        manifest = {}

    if args.input:
        input_paths = [Path(p) for p in args.input]
        missing = [p for p in input_paths if not p.exists()]
        if missing:
            sys.exit("Input not found: " + ", ".join(map(str, missing)))
        to_process = [(p, compute_file_hash(p), "forced") for p in input_paths]
        skipped = []
    else:
        input_paths = [p for p in sorted(INPUT_DIR.glob("*"))
                       if p.suffix.lower() in (".csv", ".xlsx", ".xls")
                       and not p.name.startswith((".", "~$"))] if INPUT_DIR.exists() else []
        if not input_paths:
            sys.exit(f"No CSV/XLSX files found in {INPUT_DIR} — drop the "
                     "page_engagement export(s) there or pass a path explicitly.")
        to_process, skipped = partition_files(input_paths, manifest)

    if skipped:
        print(f"Skipping {len(skipped)} unchanged file(s): {', '.join(skipped)}")
    if not to_process:
        print(f"Up to date — nothing new to process ({flush_path.name} unchanged).")
        return

    print(f"Processing {len(to_process)} file(s): "
          + ", ".join(f"{p.name} ({reason})" for p, _, reason in to_process))
    parts = [b for b in (build_one(p) for p, _, _ in to_process) if b is not None]
    if not parts:
        sys.exit("No processable page_engagement export found — nothing to do.")
    new = parts[0] if len(parts) == 1 else pd.concat(parts, ignore_index=True)

    # Language + language-agnostic page key — identical to the pv build.
    if "page_url" in new.columns:
        new["language"] = new["page_url"].map(derive_language)
        new["page_key"] = new["page_url"].map(canonical_page_key)
        if "page_id" in new.columns:
            new["page_key"] = new["page_key"].fillna(new["page_id"].astype("string"))
    new = derive_person(new)

    keep = [c for c in FLUSH_COLUMNS if c in new.columns]
    new = strip_tz(new[keep].reset_index(drop=True))

    new = add_event_key(new, FLUSH_KEY_COLS)
    new = new.drop_duplicates(subset=["event_key"], keep="last")

    store = None
    if flush_path.exists() and not args.rebuild:
        store = pd.read_parquet(flush_path)
        print(f"  Existing flush store: {len(store):,} rows")
    flushes = upsert_store(store, new, [p.name for p, _, _ in to_process], FLUSH_KEY_COLS)
    flushes.to_parquet(flush_path, index=False)

    # Aggregate is rebuilt from the FULL flush store — deterministic under upsert.
    agg = aggregate_engagement(flushes)
    agg.to_parquet(agg_path, index=False)

    gap_rate = float(agg["has_seq_gap"].mean()) if len(agg) else 0.0
    print(f"  Beacon loss (flush_seq gaps): {gap_rate:.1%} of view instances")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows_per_file = (new["source_file"].value_counts().to_dict()
                     if "source_file" in new.columns else {})
    for p, file_hash, _ in to_process:
        manifest[p.name] = {"sha256": file_hash,
                            "rows": int(rows_per_file.get(p.name, 0)),
                            "processed_at": now}
    save_manifest(mpath, manifest)

    print(f"\nWrote {len(flushes):,} flushes -> {flush_path}")
    print(f"Wrote {len(agg):,} view instances -> {agg_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 SiteOwnerDashboard/scripts/test_process_page_engagement.py`
Expected: `OK — aggregate_engagement sums deltas, flags gaps, tolerates double-fires`

- [ ] **Step 5: Smoke-test the CLI guard paths**

Run: `python3 SiteOwnerDashboard/scripts/process_page_engagement.py`
Expected: exits with `No CSV/XLSX files found in .../input/engagement — ...`
(the input directory does not exist yet — correct dormant behavior).

- [ ] **Step 6: Commit**

```bash
git add SiteOwnerDashboard/scripts/process_page_engagement.py \
        SiteOwnerDashboard/scripts/test_process_page_engagement.py
git commit -m "feat: page_engagement aggregation pipeline (flush store + view-instance grain)"
```

---

### Task 5: Dormant join onto the page-view store + consistency QA

**Files:**
- Modify: `SiteOwnerDashboard/scripts/process_site_pageviews.py` (insert between `derive_person_visit(wide)` and `wide.to_parquet(out_path, ...)` at lines 441–442; new function above `main()`)
- Test: `SiteOwnerDashboard/scripts/test_join_engagement.py` (create)

**Interfaces:**
- Consumes: `output/page_engagement.parquet` from Task 4 (`view_instance_id`, `engaged_ms`, `scroll_max_pct`); pv store column `view_instance_id` from Task 3 (present only once the platform ships the pageView payload change); existing `time_on_page_visit_sec` from `derive_person_visit`.
- Produces: pv store gains derived columns `engaged_ms` (nullable int) and `scroll_max_pct` (nullable int) at page-view grain; console QA block (coverage + consistency-violation rate). Function `join_engagement(pv: pd.DataFrame, eng_path: Path) -> pd.DataFrame`.

- [ ] **Step 1: Write the failing test**

Create `SiteOwnerDashboard/scripts/test_join_engagement.py`:

```python
"""
Tests join_engagement() — dormant behavior, coverage, and the spec §5
consistency check (engaged_ms/1000 must not exceed time_on_page_visit_sec + 1s
for views that HAVE a next view).

Plain-assert script:  python SiteOwnerDashboard/scripts/test_join_engagement.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from process_site_pageviews import join_engagement  # noqa: E402


def make_eng_parquet(tmp: Path) -> Path:
    eng = pd.DataFrame([
        # ok: 7s engaged vs 39.7s wall clock
        {"view_instance_id": "vi-1", "engaged_ms": 7000, "scroll_max_pct": 80},
        # violation: 60s engaged vs 39.7s wall clock (+1s tolerance)
        {"view_instance_id": "vi-2", "engaged_ms": 60000, "scroll_max_pct": 100},
        # last view of its visit — no wall clock to violate
        {"view_instance_id": "vi-3", "engaged_ms": 42000, "scroll_max_pct": 55},
    ])
    p = tmp / "page_engagement.parquet"
    eng.to_parquet(p, index=False)
    return p


def pv_frame(with_vi: bool) -> pd.DataFrame:
    pv = pd.DataFrame([
        {"view_id": "a", "time_on_page_visit_sec": 39.7},
        {"view_id": "b", "time_on_page_visit_sec": 39.7},
        {"view_id": "c", "time_on_page_visit_sec": None},   # last in visit
        {"view_id": "d", "time_on_page_visit_sec": 10.0},   # no engagement row
    ])
    if with_vi:
        pv["view_instance_id"] = ["vi-1", "vi-2", "vi-3", "vi-9"]
    return pv


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        eng_path = make_eng_parquet(tmp)

        # Dormant path 1: pv exports don't carry view_instance_id yet
        out = join_engagement(pv_frame(with_vi=False), eng_path)
        assert "engaged_ms" not in out.columns, "join must be skipped without the key"

        # Dormant path 2: aggregate parquet missing
        out = join_engagement(pv_frame(with_vi=True), tmp / "nope.parquet")
        assert "engaged_ms" not in out.columns

        # Live path
        out = join_engagement(pv_frame(with_vi=True), eng_path)
        assert "engaged_ms" in out.columns and "scroll_max_pct" in out.columns
        assert "time_on_page_visit_sec" in out.columns, "source column must survive"
        by_id = out.set_index("view_id")
        assert by_id.loc["a", "engaged_ms"] == 7000
        assert pd.isna(by_id.loc["d", "engaged_ms"]), "uncovered view stays NULL"
        # coverage: 3 of 4 views have engagement
        assert int(out["engaged_ms"].notna().sum()) == 3

        # Idempotence: re-running the join on an already-joined store must not
        # produce engaged_ms_x/engaged_ms_y suffixes
        again = join_engagement(out, eng_path)
        assert "engaged_ms" in again.columns and "engaged_ms_x" not in again.columns

    print("OK — join_engagement: dormant paths, coverage, idempotence")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 SiteOwnerDashboard/scripts/test_join_engagement.py`
Expected: `ImportError: cannot import name 'join_engagement'`

- [ ] **Step 3: Implement `join_engagement` and wire it into `main()`**

In `SiteOwnerDashboard/scripts/process_site_pageviews.py`, add above `main()`:

```python
ENGAGEMENT_STORE = PROJECT_DIR / "output" / "page_engagement.parquet"


def join_engagement(pv: pd.DataFrame, eng_path: Path) -> pd.DataFrame:
    """ADD engaged_ms / scroll_max_pct from the beacon aggregate (spec §5).

    Dormant until (a) pv exports carry view_instance_id — the pageView payload
    change the platform team ships with the beacon — and (b) the Task-4
    aggregate parquet exists. time_on_page_visit_sec stays untouched next to
    the new columns (house rule: derived columns, never overwrites).
    """
    if "view_instance_id" not in pv.columns:
        print("  Engagement: pv exports carry no view_instance_id yet — join skipped")
        return pv
    if not eng_path.exists():
        print(f"  Engagement: {eng_path.name} not found — join skipped "
              "(run process_page_engagement.py first)")
        return pv

    eng = pd.read_parquet(eng_path,
                          columns=["view_instance_id", "engaged_ms", "scroll_max_pct"])
    # Idempotence under re-runs: these are derived columns — recompute, don't suffix.
    pv = pv.drop(columns=[c for c in ("engaged_ms", "scroll_max_pct") if c in pv.columns])
    pv = pv.merge(eng, on="view_instance_id", how="left")

    covered = int(pv["engaged_ms"].notna().sum())
    print(f"  Engagement coverage: {covered:,}/{len(pv):,} views "
          f"({covered / max(len(pv), 1):.1%}) have engaged_ms")

    # Consistency check (spec §5/§8): visible time cannot exceed wall-clock
    # time to the next view. +1s tolerance for timestamp rounding.
    both = pv["engaged_ms"].notna() & pv["time_on_page_visit_sec"].notna()
    if int(both.sum()):
        violations = (pv.loc[both, "engaged_ms"] / 1000
                      > pv.loc[both, "time_on_page_visit_sec"] + 1.0)
        rate = float(violations.mean())
        print(f"  Consistency check: {int(violations.sum()):,}/{int(both.sum()):,} "
              f"violations ({rate:.2%}) — engaged_ms exceeds wall-clock gap")
        if rate > 0.01:
            print("  WARNING: violation rate > 1% — snippet timer bug or "
                  "aggregation bug (spec §8 threshold).")
    return pv
```

In `main()`, change lines 441–442 from:

```python
    wide = derive_person_visit(wide)
    wide.to_parquet(out_path, index=False)
```

to:

```python
    wide = derive_person_visit(wide)
    wide = join_engagement(wide, ENGAGEMENT_STORE)
    wide.to_parquet(out_path, index=False)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 SiteOwnerDashboard/scripts/test_join_engagement.py`
Expected: `OK — join_engagement: dormant paths, coverage, idempotence`

- [ ] **Step 5: Regression — full pv rebuild stays byte-identical in behavior**

Run: `python3 SiteOwnerDashboard/scripts/test_derive_person_visit.py`
Expected: exits 0.

Run: `python3 SiteOwnerDashboard/scripts/process_site_pageviews.py --rebuild`
Expected: completes as before with one new line
`Engagement: pv exports carry no view_instance_id yet — join skipped`
and the row count in `Wrote N rows ...` unchanged versus the previous run.

- [ ] **Step 6: Commit**

```bash
git add SiteOwnerDashboard/scripts/process_site_pageviews.py \
        SiteOwnerDashboard/scripts/test_join_engagement.py
git commit -m "feat: dormant engagement join onto pv store with consistency QA (spec §5)"
```

---

### Task 6: DOCX export of design + plan (corporate sharing)

**Files:**
- Create: `docs/superpowers/specs/2026-07-15-page-engagement-heartbeat-design.docx`
- Create: `docs/superpowers/plans/2026-07-15-page-engagement-beacon.docx`

**Interfaces:**
- Consumes: the two final Markdown documents (this plan + the spec).
- Produces: two self-contained DOCX files Michael can attach/upload in the corporate environment. Re-run this task's pandoc commands whenever either Markdown file changes.

- [ ] **Step 1: Convert both documents**

pandoc 3.8.2 is installed at `/opt/homebrew/bin/pandoc`. GitHub-flavored
Markdown input keeps the tables; `--toc` gives Word a navigable outline.

```bash
cd /Users/micha/Documents/Arbeit/Databricks
pandoc docs/superpowers/specs/2026-07-15-page-engagement-heartbeat-design.md \
  -f gfm -t docx --toc --toc-depth=2 \
  --metadata title="Page Engagement Beacon for SharePoint Application Insights — Design" \
  -o docs/superpowers/specs/2026-07-15-page-engagement-heartbeat-design.docx
pandoc docs/superpowers/plans/2026-07-15-page-engagement-beacon.md \
  -f gfm -t docx --toc --toc-depth=2 \
  --metadata title="Page Engagement Beacon — Implementation Plan" \
  -o docs/superpowers/plans/2026-07-15-page-engagement-beacon.docx
```

- [ ] **Step 2: Verify the DOCX files are real Word documents**

```bash
for f in docs/superpowers/specs/2026-07-15-page-engagement-heartbeat-design.docx \
         docs/superpowers/plans/2026-07-15-page-engagement-beacon.docx; do
  unzip -l "$f" | grep -q "word/document.xml" && echo "OK $f ($(du -h "$f" | cut -f1))"
done
```

Expected: two `OK ...` lines, each file well above 10 KB.

- [ ] **Step 3: Check they are not gitignored, then commit**

```bash
git check-ignore docs/superpowers/specs/*.docx docs/superpowers/plans/*.docx || echo "not ignored"
git add docs/superpowers/specs/2026-07-15-page-engagement-heartbeat-design.docx \
        docs/superpowers/plans/2026-07-15-page-engagement-beacon.docx
git commit -m "docs: DOCX exports of page_engagement design + plan for corp sharing"
```

(If `git check-ignore` matches, add with `git add -f` — binary review documents
are an explicit sharing deliverable here.)

---

## Execution order & dependencies

```
Task 1 (KQL)        — independent
Task 2 (audit)      — independent
Task 3 (flattener)  — blocks Task 4 and Task 5
Task 4 (aggregate)  — blocks Task 5 (provides the parquet contract)
Task 5 (pv join)    — final pipeline piece
Task 6 (DOCX)       — last (exports the final documents)
```

## What "done" means (this plan, not the project)

- V1–V3 queries exist and are handed over; audit doc states per consumer whether it is guarded.
- `python3 SiteOwnerDashboard/scripts/test_flatten_page_engagement.py`, `test_process_page_engagement.py`, `test_join_engagement.py`, `test_derive_person_visit.py` all exit 0.
- A full `process_site_pageviews.py --rebuild` behaves exactly as today plus one "join skipped" info line.
- Both DOCX files open in Word with intact tables.

The project itself is done per spec §8 (≥90% coverage etc.) — measurable only after the platform team ships Phase 1.
