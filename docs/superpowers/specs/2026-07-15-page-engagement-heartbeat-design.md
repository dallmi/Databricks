# Page Engagement Beacon for SharePoint Application Insights — Design

**Status:** Approved design, pre-implementation
**Date:** 2026-07-15
**Owner:** Michael (proposal author) — implementation by the SharePoint platform team (tracking snippet), analytics pipeline changes by us
**Deliverable:** This document + Kusto Query Language (KQL) validation queries, handed to the platform team. We do not own the tracking snippet.

---

## 1. Problem

"Avg. time per page" on the SiteOwnerDashboard is computed as the timestamp difference to the *next* page view within the same visit (`time_on_page_visit_sec`). Every group's last view has no next view, so it is unmeasurable **by construction**:

> measurability = 1 − 1/(views per group)

Validated on the real store (`session_vs_visit_analysis.xlsx`, 2026-07-15, 924,844 page views):

| Grouping | Views/group | Measurable share |
|---|---|---|
| Official `session_id` | 1.17 | 14.0% |
| Reconstructed `visit_id` (30-min gap per person) | 1.37 | 27.0% |

27% is a **ceiling, not a defect** — this is an intranet where most employees arrive via search or a link, read one page, and leave. 73% of views are the last of their visit and produce `NULL`. Reaching the 60% dashboard threshold would require 2.5 views/visit, which no grouping change can deliver. The only fix is a different measurement method: client-side engagement beacons (the same reason Google Analytics 4 moved to engagement time).

## 2. Why the built-in options do not work

These three "obvious" answers must be ruled out explicitly, otherwise the proposal gets closed with a one-liner:

1. **`autoTrackPageVisitTime: true`** (SDK config flag) — per Microsoft documentation, it tracks the *previous* page's visit time *"on a pageview"*, i.e. it fires when the **next** page view happens. The last page of a visit never gets a `PageVisitTime`. This is exactly the same 27% ceiling, relocated. Additionally, it emits into `customMetrics` (see 3 below).
2. **`pageViews.duration`** — this is `PerformanceNavigationTiming.duration`, i.e. page **load** time, not dwell time. Useless for engagement.
3. **`customMetrics` / `trackMetric`** — Application Insights pre-aggregates metrics; per-event dimensions (GPN, PageId, scroll depth) are lost. A dwell-time figure without page and person dimensions cannot feed per-page rankings or Read Rate.

Custom snippet code is therefore unavoidable. The design below minimizes what we ask for.

## 3. Design overview

A new event family **`page_engagement`** in the existing Application Insights `customEvents` stream, emitted by the tracking snippet the platform team already operates (the same snippet that emits `click_event`, `SEARCH_TRIGGERED`, video events).

### 3.1 Timer semantics — foreground-only ("engaged time", GA4-style)

- Timer starts at `trackPageView`, runs while `document.visibilityState === 'visible'`, pauses on `visibilitychange → hidden`, resumes on `→ visible`.
- No idle/input heuristic (no mouse/keyboard threshold). Foreground-only is the industry standard, needs no negotiated threshold, and does not punish long-form reading.
- **Side effect:** the 30-minute dwell cap dies. "Tab left open in the background" accrues zero time by construction, because a background tab is not visible. No trimming heuristic needed.

### 3.2 Flush triggers — three, not one

SharePoint Modern navigates via a client-side page router (per SPFx documentation: *"navigation to the page is achieved via a page router to avoid a full refresh"*). `pagehide` does **not** fire on page A→B navigation. A naive exit-beacon would miss precisely the navigations we can already measure today. Therefore:

| # | Trigger | Covers |
|---|---|---|
| 1 | SharePoint router navigation (immediately before the next `trackPageView`) | Page A→B inside Modern SharePoint — the common case |
| 2 | `visibilitychange → hidden` | Tab switch, window minimize, mobile background |
| 3 | `pagehide` | Tab close, navigation out of SharePoint, `data-interception="off"` links |

Hook 1 already exists in the snippet — it must, because the snippet emits a fresh `PageId`/`PageName` per logical page today without full reloads (empirically confirmed: intra-session PageId changes are present in our data). The ask is to flush a counter immediately before the existing `trackPageView` call, not to build a new mechanism. Hook 2 is one `addEventListener`. Hook 3 is one line.

### 3.3 Flush semantics — deltas, not totals

Each flush sends **milliseconds accrued since the last flush** and resets the counter. The pipeline sums deltas per page-view instance. This buys:

- **Double-firing is harmless** — triggers 2 and 3 often both fire on tab close; the second sends `0` (or is suppressed, see volume guard).
- **Beacon loss degrades gracefully** — a lost beacon loses one segment, not the whole page.
- **Volume guard:** flush only when `Engaged_Ms ≥ 1000` since the last flush; smaller remainders stay in the counter and travel with the next flush (lossless under delta semantics). Tab ping-pong produces zero events.

### 3.4 Transport

`navigator.sendBeacon` (survives unload; the Application Insights JavaScript SDK supports beacon transport on unload).

### 3.5 Payload — `CustomProps` schema

Envelope: **identical to `click_event`** (GPN, Email, SiteID, SiteName, PageId, PageName, PageURL, PageStatus, ContentType, ContentOwner, NewsCategory, PublishingDate, Theme, Topic, TargetOrganisation, TargetRegion, refUri) — deliberate reuse: the platform team copies its existing property-bag builder; our flattener already parses this shape. Plus:

| Key | Type | Purpose |
|---|---|---|
| `View_Instance_Id` | string (GUID) | Correlation key — minted by the snippet per `trackPageView`, attached to every flush of that view instance. **Also attached to the pageView's own CustomProps.** |
| `Engaged_Ms` | int | Visible milliseconds since last flush (delta) |
| `Scroll_Max_Pct` | int 0–100 | Maximum scroll depth reached so far: `max((scrollTop + Viewport_Height_Px) / Page_Height_Px × 100)` over the view instance, clamped to 0–100 |
| `Page_Height_Px` | int | Document height — normalizes Read Rate |
| `Viewport_Height_Px` | int | Viewport height — normalizes Read Rate |
| `Flush_Reason` | string | `route` \| `hidden` \| `pagehide` |
| `Flush_Seq` | int | 0,1,2… per view instance — ordering + loss detection |

**Correlation decision:** `View_Instance_Id` is the **default** join key. `operation_Id` (the SDK mints a new operation per `trackPageView` per Microsoft docs) is used only as a QA cross-check — it is SDK-internal behavior we do not control, and its stability under SPA routing is the one assumption we could not verify from documentation (validation query V1 below).

### 3.6 Carrier table — why `customEvents`

Engagement is semantically a property of the *view*, so `pageViews` looks like the natural home. It is ruled out by a hard technical constraint, not convenience:

| Carrier | Why not / why |
|---|---|
| `pageViews` (`trackPageView`) | The record is sent at **load**; dwell time is only known at **exit**, and Application Insights has no record-update API. Microsoft's own single-page-application workaround (`startTrackPage`/`stopTrackPage`) would require the platform team to rebuild their working load-time tracking into deferred sends, risking double or lost page views. Scroll depth does not fit a load-time record at all. |
| `customMetrics` (`trackMetric`) | Pre-aggregated; loses per-event dimensions (§2, item 3). |
| `customEvents` (`trackEvent`) | **Chosen.** Properties survive unsampled per event. `customEvents` is not "the click table" — it is the event-**family** table: `click_event`, `SEARCH_TRIGGERED`, `SEARCH_RESULT_CLICK` and the video family already coexist there, discriminated by `name`. In Application Insights semantics, `trackEvent` is the generic business-event class. `page_engagement` joins an existing multi-family structure. |

**The real risk this creates:** any consumer that counts `customEvents` wholesale as "clicks" (without `where name == 'click_event'`) would silently inflate its numbers. This is a migration risk, addressed head-on in §4 (audit) and §6 (regression check).

## 4. Phase 0 — validation before anyone writes code

All KQL runs against the Application Insights resource (portal or Genie where mirrored in `sharepoint_bronze`).

**V1 — operation_Id stability (the one unverified assumption):**
```kql
// Do click_events share the operation_Id of their logical page view,
// including views created by SPA route changes (no full reload)?
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
```
Outcome does not block the design (View_Instance_Id is the default key regardless); it decides whether operation_Id is usable as QA cross-check.

**V2 — sampling check:**
```kql
union pageViews, customEvents
| where timestamp > ago(1d)
| summarize sampled = countif(itemCount > 1), rows = count() by itemType
```
If ingestion sampling is active, `page_engagement` must be sampled at the same rate as `pageViews`, or engagement-per-view ratios are systematically biased. Platform-team checklist item.

**V3 — consumer audit (name discrimination):**
- Our side: grep all KQL and pipeline code in `Clicks/`, `CampaignWe/`, `Databricks/kql/`, `SiteOwnerDashboard/scripts/` for `customEvents` / `customevents` reads without a `name` filter.
- Platform side: their bronze→gold notebooks — explicit checklist item in the proposal, we cannot see those.

**V4 — router hook existence:** already confirmed empirically (intra-session PageId changes in our store prove the snippet fires fresh page views on SPA navigation). Documented as closed, not an open question.

## 5. Pipeline changes (our side, after deployment)

- **`SiteOwnerDashboard/scripts/process_site_interactions.py`** routes `name == 'page_engagement'` into its **own output grain** — never into the click aggregates. Grain: view instance. Aggregation: `SUM(Engaged_Ms)`, `MAX(Scroll_Max_Pct)`, `MAX(Flush_Seq)+1 AS flush_count` per `View_Instance_Id`.
- **Join to the pageview store** on `View_Instance_Id` → new derived column **`engaged_ms`** at page-view grain. `time_on_page_visit_sec` stays untouched next to it (house rule: never overwrite source columns; add clearly named derived columns).
- **Dashboard:** "Avg. time per page" switches its source to `engaged_ms` with a coverage badge; the old visit-based metric remains visible as QA comparison.
- **Built-in consistency check:** for views *with* a next view, `engaged_ms ≤ wall-clock delta to next view` must hold (visible time cannot exceed wall-clock time). Violations indicate a snippet bug or a summation bug — this check keeps both measurements honest against each other. Report violation rate in the QA sheet.
- **New metrics unlocked:** Read Rate = f(`Scroll_Max_Pct`, `Page_Height_Px`, `Viewport_Height_Px`) — a real engagement figure for single-page visits, i.e. the 73% of views that are silent today.

## 6. Rollout

| Phase | Content | Exit criterion |
|---|---|---|
| 0 | Validation queries V1–V3, platform-team review of this document | Queries answered; scope confirmed |
| 1 | Pilot on one site behind a feature flag in the snippet. Candidate: "News and events" (99.4% of tracked pages; content owners there are the primary Read-Rate consumers) | Events flowing; schema as specified |
| 2 | 2–4 weeks validation: flushes/page-view ratio (expected ~1.5–2.5), consistency-check violation rate, actual vs. estimated volume, **click-aggregate regression check** (click counts before/after pilot must be identical) | Success criteria below met |
| 3 | Global enablement + dashboard metric switch | — |

## 7. Risks

| Risk | Assessment | Mitigation |
|---|---|---|
| Event volume | ~2 flushes/page view ≈ up to +350M events/year — customEvents (~262M) could nearly double | ≥1s delta guard (§3.3); pilot measures actual ratio before global rollout |
| Legacy consumer counts engagement as clicks | Real migration risk | V3 audit + `name` family + phase-2 regression check |
| Beacon loss on `pagehide` | `sendBeacon` is best-effort | Delta semantics: only the final segment is lost; `Flush_Seq` gaps quantify the loss rate |
| `operation_Id` assumption wrong | Possible | `View_Instance_Id` is the default key; operation_Id is QA-only (V1) |
| Personally identifiable information (PII) | GPN/Email in the envelope | No new PII — identical to the existing `click_event` governance; tied to the pre-existing PII-cleanup work item, not to this project |
| Sampling skew | Ratio bias if rates differ | V2 check; align sampling config for the new family with `pageViews` |

## 8. Success criteria

- ≥90% of page views have ≥1 `page_engagement` event (up from 27% measurability).
- Median engaged time for News articles plausible: 30 s – 3 min.
- Click aggregates unchanged (regression check).
- Consistency-check violations < 1%.

## 9. Out of scope

- Periodic heartbeat while the page stays visible (time-series within a view, idle detection) — rejected as 4–16× event volume for marginal analytical gain; can be revisited if beacon-loss rates (`Flush_Seq` gaps) turn out high.
- Idle/input-based engaged-time definition — rejected (§3.1).
- Changes to `pageViews` emission **timing or mechanism** (`startTrackPage`/`stopTrackPage` migration, deferred sends) — rejected (§3.6). Adding `View_Instance_Id` to the pageView's existing property bag **is** in scope: payload-only, required for the §5 join.
- PII removal — separate pre-existing work item.
