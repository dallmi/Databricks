# Standalone build: anonymisation, column prune, site/time slice

Status: approved (2026-07-17)
Scope: `SiteOwnerDashboard/scripts/build_standalone_dashboard.py` only. No change to
`dashboard/dashboard.html`, no change to the ingest pipeline, no change to the parquet on disk.

## Problem

The distributed `site_dashboard_standalone.html` has reached **236 MB** in the production
environment (4.15 MB on the demo parquet). Three separate causes, in order of impact:

1. **Ballast columns.** `gpn`, `user_id` and `view_id`/`event_id` are referenced **nowhere** in
   the dashboard template. They are per-row-unique or high-cardinality strings, so they barely
   compress. Dropping them costs **48%** of the payload and changes no metric.
2. **No slicing.** Every build embeds every site and every month, however narrow the recipient's
   interest.
3. **Vendor floor.** 1.41 MB of Chart.js + ExcelJS + template is irreducible here (ExcelJS alone
   is 928 KB). Out of scope; noted so nobody expects a few-hundred-KB file.

A second, independent problem surfaced while measuring: **the distributed file contains the GPN in
plaintext.** `person_id` *is* the GPN (31,467/31,467 rows identical). Dropping the `gpn` column
removes a copy, not the PII.

At 236 MB the file also approaches V8's ~512 MB single-string ceiling — the base64 data island is
one string. Growth direction is wrong, independent of everything above.

## Decisions

- **Default behaviour is unchanged**: no `--site`, no window → all sites, all time. Slicing is
  opt-in. Anonymisation and the column prune are **always on** (opt-out via `--keep-ids`).
- **Anonymisation ships first**, before the slicing work.
- Everything happens at **embed time**. The parquet on disk keeps `gpn`, plaintext `person_id` and
  every column, for HR re-joins, QA and reconciliation. The standalone is a terminal artifact.

## Part 1 — Anonymisation (do this first)

### What leaks

| Column | Carries | File |
|---|---|---|
| `gpn` | the GPN | pv, ix |
| `person_id` | the GPN verbatim | pv, ix |
| `visit_id` | `person_id + "#" + n` → the GPN as prefix | pv |
| `user_id` | AppInsights user id | pv, ix |

`visit_id`'s leak comes from `derive_person_visit()`
(`../scripts/flatten_appinsights.py:592`): `visit_id = person_id + "#" + visit_no`.
**The demo parquet does not show this** — `generate_demo_data.py` fabricates session-like
`visit_id` values (`s-000000`). Do not conclude from demo data that `visit_id` is clean; it is not
in production.

`session_id` is the company's official session key, not GPN-derived. It stays as-is.

### Design

Replace `person_id` and `visit_id` with **dense integer surrogates**, ordered by a salted hash:

1. Generate a random salt per build (`secrets.token_hex(16)`). **Never persisted, never printed** —
   persisting it makes the mapping reversible, and an 8-digit GPN space (10^8) falls to brute force
   in seconds without one.
2. Build ONE surrogate map per key, from the **union of distinct values across pv and ix**, ranked
   by `sha256(salt || value)`.
3. Join both files against the shared map.

Ordering by the salted hash (not by the raw value) matters: a dense rank ordered by raw
`person_id` would preserve GPN sort order and leak the population's relative identities.

One shared map — not a per-file `DENSE_RANK()` — because pv and ix must agree. They do not join on
`person_id` today (`fs` is a pv-internal CTE; `ix` counts `person_id` independently at
`dashboard.html:2779`), but a per-file rank would make person "5" in pv a different human from
person "5" in ix, and any future cross-view join would silently produce garbage.

### Invariants (must be proven, not assumed)

- `COUNT(DISTINCT person_id)` identical before/after, in pv and in ix. Dense rank is 1:1 on distinct
  values, so this is exact — no collision risk, unlike a truncated hash.
- `COUNT(DISTINCT visit_id)` identical before/after.
- Every other KPI byte-identical: Views, Visits, our-visits, pages, avg time-on-page.
- No column of the emitted parquet matches the GPN of any source row.

`visit_id` is safe to re-key: `dashboard.html:1143` is its only use, as an opaque `GROUP BY` key.
It is never parsed or split.

Surrogates are also **smaller** than the 8-char GPN strings they replace, so this shrinks the file
rather than growing it.

## Part 2 — Column prune

Drop at embed time: `gpn`, `user_id`, `view_id` (pv) and `gpn`, `user_id`, `event_id` (ix).
Measured: pv 1,624 → 848 KB (−47.8%), ix 386 → 193 KB (−50.1%), total −48.2%.

Safe by construction: the template feature-detects columns (`pvCols`/`ixCols` via `DESCRIBE`, then
`pvCols.has(...)` guards), so absent columns degrade gracefully. Every `SELECT *` in the template is
either view creation or a CTE over an already-projected subquery.

Other unreferenced columns (`client_os`, `client_country`, `page_load_ms`, `tracking_id`,
`is_last_in_session`, …) are **kept**: low cardinality, negligible cost, plausibly wanted later.

`--keep-ids` disables both the prune and the anonymisation, for a full-fidelity local build.

## Part 3 — Site and time slice

```bash
python scripts/build_standalone_dashboard.py                                  # default: everything
python scripts/build_standalone_dashboard.py --site "News and events" --months 6
python scripts/build_standalone_dashboard.py --site "News and events" --since 2026-01-01
```

- `--site NAME` — case-insensitive exact match on `site_name`, same semantics as
  `process_site_pageviews.py --site`, so one value carries through the whole pipeline.
- `--months N` — last N months relative to **`MAX(timestamp)` in the pv parquet**, not to today:
  exports lag reality.
- `--since YYYY-MM-DD` — absolute floor. Mutually exclusive with `--months`.

The `--months` cutoff is computed **once from pv** and applied to both files. Deriving it per file
would give the sparser `ix` a different cutoff, and clicks would stop matching views.

Both predicates go into the existing `COPY (SELECT … FROM read_parquet(…)) TO … (ZSTD)` in
`recompress_zstd()` — the filter is a `WHERE` clause on a statement that already exists.

Multi-site is a precondition for `--site` paying off: the ingest must run **without** `--site` so
all sites land in one parquet. `--site` at ingest stays available.

### Output naming

With `--site`, both files derive from a slug — `news_and_events_dashboard_standalone.html` and
`news_and_events_guide_standalone.html` — still cross-linked as a pair. This requires
`GUIDE_STANDALONE_NAME` to become a parameter instead of a module constant. Without `--site`, names
are unchanged. An explicit `--output` always wins.

The build rewrites `SITE_DISPLAY_NAME` to `Site Owner Dashboard – <site>` so the H1 and the browser
tab tell the recipient which site they hold. Uses the existing hook; no new JS.

### Guards

- **Zero rows after any filter → hard error**, listing the site names present. Shipping an empty
  dashboard is worse than failing the build.
- **Warn when the slice is under 180 days.** The dashboard compares against the equal-length
  preceding period and the default view is 90d, so a shorter slice makes every Δ silently vanish
  (`p===0` → blank), which reads as "no change" rather than "no baseline".

## Expected result

| Stage | Demo | Production (extrapolated) |
|---|---|---|
| today | 4.15 MB | 236 MB |
| + anonymise + prune | ~2.2 MB | **~122 MB** |
| + 3 of 13 months | ~1.4 MB | ~28 MB |
| + 1 of n sites | — | ~28/n MB |

## Out of scope

- Lazy-loading ExcelJS (928 KB, 22% of today's file) — the next-largest lever, separate work.
- Anonymising the parquet on disk. It stays plaintext by design.
- `--site-id`.
