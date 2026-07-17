# Standalone Anonymisation, Prune and Slice — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the distributed `site_dashboard_standalone.html` from 236 MB toward ~28 MB and stop it shipping plaintext GPNs, without changing a single metric the dashboard reports.

**Architecture:** Everything happens at embed time inside `scripts/build_standalone_dashboard.py`. One new seam — `build_payloads()` — owns slicing, pruning and anonymisation, returns ZSTD parquet bytes per view, and is the only thing the tests touch. `build()` keeps doing what it does today (embed bytes, inline libs, write HTML). The parquet on disk, the ingest pipeline and `dashboard/dashboard.html` are **not** modified.

**Tech Stack:** Python 3.9 (anaconda), DuckDB (already a dependency), pandas. Tests are plain-assert scripts — the house convention in this repo (`scripts/test_*.py`, `def main()`, `if __name__ == "__main__": main()`, run with `python scripts/test_X.py`, exit non-zero on first failure). **There is no pytest in this project** — do not introduce it.

**Spec:** `SiteOwnerDashboard/docs/superpowers/specs/2026-07-17-standalone-slicing-and-anonymisation-design.md`

## Global Constraints

- All paths relative to `/Users/micha/Documents/Arbeit/Databricks/SiteOwnerDashboard`. Run everything from that directory.
- **Default behaviour must not change**: no `--site`, no `--months`, no `--since` → all sites, all time. Anonymisation and prune are always on; `--keep-ids` opts out of both.
- **Do not modify** `dashboard/dashboard.html`, the ingest scripts, or the parquets in `output/`.
- Drop columns: pv → `gpn`, `user_id`, `view_id`. ix → `gpn`, `user_id`, `event_id`. **Keep everything else**, including `client_os`, `client_country`, `page_load_ms`, `tracking_id`, `is_last_in_session`.
- Surrogate columns: `person_id` (pv **and** ix) and `visit_id` (pv only).
- The salt is `secrets.token_hex(16)`, generated per build, **never printed and never written to disk**.
- Code and comments in English. German only in conversation with the user.
- Commit after every task.

## File Structure

| File | Responsibility |
|---|---|
| `scripts/build_standalone_dashboard.py` (modify) | Gains `build_payloads()` + CLI flags. Keeps `build()`, `build_guide()`, `inline_*` as they are. |
| `scripts/test_standalone_payloads.py` (create) | All tests: anonymisation invariants, prune, slice, guards. One file — they all exercise the same seam. |

---

### Task 1: The `build_payloads()` seam with salted surrogates

**Files:**
- Modify: `scripts/build_standalone_dashboard.py:71-81` (replace `recompress_zstd`), `scripts/build_standalone_dashboard.py:122-161` (`build()` calls the new seam)
- Test: `scripts/test_standalone_payloads.py` (create)

**Interfaces:**
- Produces:
  - `DROP_COLS: dict[str, tuple[str, ...]]` — `{"pv": ("gpn", "user_id", "view_id"), "ix": ("gpn", "user_id", "event_id")}`
  - `build_payloads(parquet_dir: Path, *, site: str | None = None, since: str | None = None, months: int | None = None, keep_ids: bool = False) -> tuple[dict[str, bytes], dict]` — returns `({view_name: zstd_parquet_bytes}, stats)`. `stats` has keys `rows` (`{view: int}`), `sites` (`list[str]` present after filtering), `window` (`(min_ts, max_ts)` as strings), `persons` (int).
- Consumes: the existing module constants `VIEWS`, `DEFAULT_PARQUET_DIR`.

**Why one shared map:** pv and ix must agree on what person `5` means. A per-file `DENSE_RANK()` would make them different humans and any cross-view join would silently produce garbage. `dashboard.html:2779` already counts `person_id` on `ix` independently of `pv`.

**Why order by the salted hash:** a dense rank ordered by raw `person_id` preserves GPN sort order and leaks the population's relative identities. Ordering by `sha256(salt || person_id)` carries no information.

- [ ] **Step 1: Write the failing test**

Create `scripts/test_standalone_payloads.py`:

```python
"""
Tests for build_payloads() in build_standalone_dashboard.py — the embed-time
slice/prune/anonymise seam.

Background (2026-07-17): the distributed standalone reached 236 MB and shipped
plaintext GPNs — person_id IS the GPN, and in production visit_id is
"person_id#n", so it carries the GPN as a prefix. (The demo generator fabricates
session-like visit_ids, so the demo parquet hides this — the fixture below
deliberately uses the production shape.)

Plain-assert script (no test framework in this repo):
    python scripts/test_standalone_payloads.py
Exits non-zero on the first failing assertion.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_standalone_dashboard import build_payloads  # noqa: E402

GPN_A, GPN_B, GPN_C = "06297360", "01892018", "07001234"


def check(label: str, actual, expected) -> None:
    if actual != expected:
        print(f"FAIL {label}: got {actual!r}, expected {expected!r}")
        sys.exit(1)
    print(f"  ok  {label}")


def write_fixture(d: Path) -> None:
    """pv + ix in the PRODUCTION shape: person_id == gpn, visit_id == 'gpn#n'.

    Person C appears in ix only; person A and B appear in both files — that is
    what pins the cross-file surrogate consistency.
    """
    pv = pd.DataFrame([
        # gpn,   person_id, visit_id,      session_id, timestamp,             site_name
        (GPN_A, GPN_A, f"{GPN_A}#1", "s-1", "2026-01-15 10:00:00", "News and events", "u01"),
        (GPN_A, GPN_A, f"{GPN_A}#1", "s-1", "2026-01-15 10:01:00", "News and events", "u01"),
        (GPN_A, GPN_A, f"{GPN_A}#2", "s-2", "2026-06-20 10:00:00", "News and events", "u01"),
        (GPN_B, GPN_B, f"{GPN_B}#1", "s-3", "2026-06-20 11:00:00", "News and events", "u02"),
        (GPN_B, GPN_B, f"{GPN_B}#1", "s-3", "2026-06-20 11:05:00", "Other site",      "u02"),
    ], columns=["gpn", "person_id", "visit_id", "session_id", "timestamp", "site_name", "user_id"])
    pv["timestamp"] = pd.to_datetime(pv["timestamp"])
    pv["view_id"] = [f"v{i}" for i in range(len(pv))]
    pv["page_key"] = "/news/x"

    ix = pd.DataFrame([
        (GPN_A, GPN_A, "s-1", "2026-01-15 10:00:30", "News and events", "u01"),
        (GPN_C, GPN_C, "s-9", "2026-06-21 09:00:00", "News and events", "u03"),
    ], columns=["gpn", "person_id", "session_id", "timestamp", "site_name", "user_id"])
    ix["timestamp"] = pd.to_datetime(ix["timestamp"])
    ix["event_id"] = [f"e{i}" for i in range(len(ix))]

    pv.to_parquet(d / "site_pageviews.parquet", index=False)
    ix.to_parquet(d / "site_interactions.parquet", index=False)


def load(payloads: dict, view: str, con: duckdb.DuckDBPyConnection, tmp: Path):
    """Materialise a payload's bytes so DuckDB can query them."""
    p = tmp / f"{view}.parquet"
    p.write_bytes(payloads[view])
    return f"read_parquet('{p.as_posix()}')"


def main() -> None:
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        write_fixture(d)
        payloads, stats = build_payloads(d)
        con = duckdb.connect()
        pv, ix = load(payloads, "pv", con, d), load(payloads, "ix", con, d)

        print("distinct counts survive")
        check("pv persons", con.execute(f"SELECT COUNT(DISTINCT person_id) FROM {pv}").fetchone()[0], 2)
        check("pv visits", con.execute(f"SELECT COUNT(DISTINCT visit_id) FROM {pv}").fetchone()[0], 3)
        check("pv views", con.execute(f"SELECT COUNT(*) FROM {pv}").fetchone()[0], 5)
        check("ix persons", con.execute(f"SELECT COUNT(DISTINCT person_id) FROM {ix}").fetchone()[0], 2)

        print("no GPN survives anywhere")
        for gpn in (GPN_A, GPN_B, GPN_C):
            for name, rel in (("pv", pv), ("ix", ix)):
                cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()]
                hits = 0
                for c in cols:
                    hits += con.execute(
                        f"SELECT COUNT(*) FROM {rel} WHERE CAST({c} AS VARCHAR) LIKE '%{gpn}%'"
                    ).fetchone()[0]
                check(f"{name} leaks {gpn}", hits, 0)

        print("surrogates agree across pv and ix")
        # Person A is in both files; their surrogate must be the same integer.
        a_pv = con.execute(f"SELECT DISTINCT person_id FROM {pv} WHERE session_id = 's-1'").fetchone()[0]
        a_ix = con.execute(f"SELECT DISTINCT person_id FROM {ix} WHERE session_id = 's-1'").fetchone()[0]
        check("person A same surrogate in pv and ix", a_pv, a_ix)

        print("salt is random per build")
        payloads2, _ = build_payloads(d)
        p2 = d / "pv2.parquet"
        p2.write_bytes(payloads2["pv"])
        runs = set()
        for rel in (pv, f"read_parquet('{p2.as_posix()}')"):
            runs.add(tuple(r[0] for r in con.execute(
                f"SELECT person_id FROM {rel} ORDER BY session_id, timestamp").fetchall()))
        if len(runs) != 2:
            print("NOTE: both builds produced identical surrogate sequences — "
                  "possible with 2 persons (50% chance). Not a failure.")
        check("second build still preserves persons",
              con.execute(f"SELECT COUNT(DISTINCT person_id) FROM read_parquet('{p2.as_posix()}')").fetchone()[0], 2)

    print("\nAll assertions passed.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/test_standalone_payloads.py`
Expected: FAIL with `ImportError: cannot import name 'build_payloads'`

- [ ] **Step 3: Write the implementation**

In `scripts/build_standalone_dashboard.py`, add `import secrets` to the imports, then **replace** `recompress_zstd` (currently lines 71-81) with:

```python
# Columns the dashboard never references, dropped from the embedded payload.
# They are per-row-unique or high-cardinality strings, so they barely compress:
# dropping them costs ~48% of the payload and changes no metric. The parquet on
# disk keeps them for HR re-joins and QA — the standalone is a terminal artifact.
DROP_COLS = {
    "pv": ("gpn", "user_id", "view_id"),
    "ix": ("gpn", "user_id", "event_id"),
}

# GPN-derived columns, replaced by dense surrogates. person_id IS the GPN, and
# derive_person_visit() builds visit_id as "person_id#n" — so both ship the GPN
# in plaintext. (generate_demo_data.py fabricates session-like visit_ids, which
# is why the demo parquet does not show the visit_id leak. Production does.)
SURROGATE_COLS = {"pv": ("person_id", "visit_id"), "ix": ("person_id",)}


def _columns(con, src: Path) -> list[str]:
    return [r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{src.as_posix()}')").fetchall()]


def _make_surrogate_map(con, col: str, sources: list[tuple[Path, str]], salt: str) -> None:
    """Register temp table <col>_map: orig -> dense surrogate int.

    Built from the UNION of distinct values across every source that has the
    column, so pv and ix agree on what person 5 means. Ordered by the salted
    hash, not by the raw value: a rank ordered by raw person_id would preserve
    GPN sort order and leak the population's relative identities.

    DENSE_RANK is 1:1 on distinct values, so COUNT(DISTINCT) is preserved
    exactly — unlike a truncated hash, which could collide.
    """
    parts = [f"SELECT DISTINCT {col} AS orig FROM read_parquet('{p.as_posix()}') {w}"
             for p, w in sources]
    union = " UNION ".join(parts)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE {col}_map AS
        SELECT orig, DENSE_RANK() OVER (ORDER BY sha256(? || CAST(orig AS VARCHAR))) AS surrogate
        FROM ({union}) WHERE orig IS NOT NULL
    """, [salt])


def _select_list(cols: list[str], view: str, keep_ids: bool) -> str:
    """Projection for one view: drop ballast, swap GPN-derived ids for surrogates."""
    if keep_ids:
        return "s.*"
    drop = set(DROP_COLS.get(view, ()))
    surrogate = set(SURROGATE_COLS.get(view, ()))
    out = []
    for c in cols:
        if c in drop:
            continue
        if c in surrogate:
            out.append(f"{c}_map.surrogate AS {c}")
        else:
            out.append(f"s.{c}")
    return ", ".join(out)


def build_payloads(parquet_dir: Path, *, site: str | None = None,
                   since: str | None = None, months: int | None = None,
                   keep_ids: bool = False) -> tuple[dict[str, bytes], dict]:
    """Return {view: zstd parquet bytes} for every present view, plus stats.

    Slices (site/time), prunes ballast columns and replaces GPN-derived ids with
    salted dense surrogates. The salt is per-build and never persisted: an
    8-digit GPN space falls to brute force in seconds otherwise.
    """
    parquet_dir = Path(parquet_dir)
    salt = secrets.token_hex(16)
    con = duckdb.connect()

    present = [(v, parquet_dir / f, req) for v, f, req in VIEWS]
    for view, src, required in present:
        if required and not src.exists():
            raise FileNotFoundError(
                f"parquet not found: {src} — run scripts/process_site_pageviews.py "
                "(or scripts/generate_demo_data.py) first")
    present = [(v, s, r) for v, s, r in present if s.exists()]
    cols = {v: _columns(con, s) for v, s, _ in present}

    where = _build_where(con, present, cols, site=site, since=since, months=months)

    if not keep_ids:
        for col in ("person_id", "visit_id"):
            sources = [(s, where[v]) for v, s, _ in present if col in cols[v]]
            if sources:
                _make_surrogate_map(con, col, sources, salt)

    payloads, rows = {}, {}
    with tempfile.TemporaryDirectory() as tmp:
        for view, src, _ in present:
            sel = _select_list(cols[view], view, keep_ids)
            joins = ""
            if not keep_ids:
                for col in SURROGATE_COLS.get(view, ()):
                    if col in cols[view]:
                        joins += f" LEFT JOIN {col}_map ON s.{col} = {col}_map.orig"
            out = Path(tmp) / f"{view}.parquet"
            con.execute(
                f"COPY (SELECT {sel} FROM read_parquet('{src.as_posix()}') s{joins} "
                f"{where[view]}) TO '{out.as_posix()}' (FORMAT PARQUET, COMPRESSION zstd)")
            payloads[view] = out.read_bytes()
            rows[view] = con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{src.as_posix()}') s {where[view]}"
            ).fetchone()[0]

    stats = _collect_stats(con, present, where, rows)
    con.close()
    return payloads, stats
```

Add the two helpers `_build_where` and `_collect_stats` as stubs for now — Task 3 fills them in:

```python
def _build_where(con, present, cols, *, site=None, since=None, months=None) -> dict[str, str]:
    """Per-view WHERE clause (empty string when unfiltered). Task 3 implements
    site/time slicing; until then every view is unfiltered."""
    return {v: "" for v, _, _ in present}


def _collect_stats(con, present, where, rows) -> dict:
    src = {v: s for v, s, _ in present}
    pv = src["pv"].as_posix()
    mn, mx = con.execute(
        f"SELECT MIN(timestamp)::VARCHAR, MAX(timestamp)::VARCHAR "
        f"FROM read_parquet('{pv}') s {where['pv']}").fetchone()
    sites = [r[0] for r in con.execute(
        f"SELECT DISTINCT site_name FROM read_parquet('{pv}') s {where['pv']} "
        f"ORDER BY 1").fetchall()] if "site_name" in _columns(con, src["pv"]) else []
    persons = con.execute(
        f"SELECT COUNT(DISTINCT person_id) FROM read_parquet('{pv}') s {where['pv']}").fetchone()[0]
    return {"rows": rows, "sites": sites, "window": (mn, mx), "persons": persons}
```

Now rewire `build()` — replace its payload loop (currently lines 133-147) with:

```python
    payloads, stats = build_payloads(parquet_dir, site=site, since=since,
                                     months=months, keep_ids=keep_ids)
    total = 0
    for view, _, _ in VIEWS:
        if view not in payloads:
            print(f"Skipped {view} (not present — Phase 2 optional)")
            continue
        data = payloads[view]
        total += len(data)
        html = inline_parquet(html, data, island_id=f"{view}-parquet-b64")
        print(f"Embedded {view} as '{view}-parquet-b64' ({len(data) / 1024:.0f} KB, "
              f"{stats['rows'][view]:,} rows)")
```

and widen `build()`'s signature to `def build(template_path, parquet_dir, output_path, *, site=None, since=None, months=None, keep_ids=False) -> Path:`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python scripts/test_standalone_payloads.py`
Expected: PASS — ends with `All assertions passed.`

- [ ] **Step 5: Verify the real build still works and got smaller**

Run: `python scripts/build_standalone_dashboard.py`
Expected: succeeds; the `Embedded pv` line reports roughly **half** the KB it did before (was 1624 KB), and the final file is ~2.2 MB (was 4.0 MB).

- [ ] **Step 6: Prove the real dashboard still reports the same numbers**

Run:
```bash
cd output && python -m http.server 8899 >/dev/null 2>&1 &
sleep 1 && curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8899/site_dashboard_standalone.html
```
Open it, confirm the Reach KPI card still shows **9,553 Page Views / 4,504 Visits / 2,989 Unique Visitors** for the 90d default window — identical to before the change. Then `pkill -f "http.server 8899"`.

- [ ] **Step 7: Commit**

```bash
git add scripts/build_standalone_dashboard.py scripts/test_standalone_payloads.py
git commit -m "feat(standalone): salted surrogates for person_id/visit_id at embed time"
```

---

### Task 2: Column prune and `--keep-ids`

Task 1's `_select_list` already drops `DROP_COLS` and honours `keep_ids`. This task pins that behaviour with tests and exposes the flag on the CLI.

**Files:**
- Modify: `scripts/build_standalone_dashboard.py` (argparse in `main()`, ~line 196)
- Test: `scripts/test_standalone_payloads.py` (extend)

**Interfaces:**
- Consumes: `build_payloads(..., keep_ids=bool)`, `DROP_COLS` from Task 1.
- Produces: CLI flag `--keep-ids`.

- [ ] **Step 1: Write the failing test**

Add to `scripts/test_standalone_payloads.py`, and call `test_prune(d, con)` from `main()` inside the existing `with tempfile.TemporaryDirectory()` block:

```python
def test_prune(d: Path, con: duckdb.DuckDBPyConnection) -> None:
    print("prune drops exactly the ballast columns")
    payloads, _ = build_payloads(d)
    for view, dropped in (("pv", ("gpn", "user_id", "view_id")),
                          ("ix", ("gpn", "user_id", "event_id"))):
        p = d / f"prune_{view}.parquet"
        p.write_bytes(payloads[view])
        cols = {r[0] for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{p.as_posix()}')").fetchall()}
        for c in dropped:
            check(f"{view} dropped {c}", c in cols, False)
        for c in ("session_id", "timestamp", "site_name", "person_id"):
            check(f"{view} kept {c}", c in cols, True)
    check("pv kept page_key", "page_key" in {r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{(d / 'prune_pv.parquet').as_posix()}')").fetchall()}, True)

    print("--keep-ids restores full fidelity")
    payloads, _ = build_payloads(d, keep_ids=True)
    p = d / "keep_pv.parquet"
    p.write_bytes(payloads["pv"])
    cols = {r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{p.as_posix()}')").fetchall()}
    for c in ("gpn", "user_id", "view_id"):
        check(f"keep_ids kept {c}", c in cols, True)
    check("keep_ids leaves person_id as the GPN",
          con.execute(f"SELECT COUNT(*) FROM read_parquet('{p.as_posix()}') "
                      f"WHERE person_id = gpn").fetchone()[0], 5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/test_standalone_payloads.py`
Expected: FAIL at `keep_ids kept gpn` (or at the `--keep-ids` assertions) if `keep_ids` is not wired through.

- [ ] **Step 3: Wire the CLI flag**

In `main()`, add before `args = parser.parse_args(argv)`:

```python
    parser.add_argument("--keep-ids", action="store_true",
                        help="keep gpn/user_id/view_id and the plaintext GPN-derived "
                             "person_id/visit_id (full-fidelity local build; NEVER "
                             "distribute the result — it contains personal data)")
```

and pass it through: `out = build(args.template, args.parquet_dir, args.output, keep_ids=args.keep_ids)`

- [ ] **Step 4: Run test to verify it passes**

Run: `python scripts/test_standalone_payloads.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/build_standalone_dashboard.py scripts/test_standalone_payloads.py
git commit -m "feat(standalone): prune unreferenced id columns, add --keep-ids escape hatch"
```

---

### Task 3: Site and time slicing

**Files:**
- Modify: `scripts/build_standalone_dashboard.py` (`_build_where`, argparse in `main()`)
- Test: `scripts/test_standalone_payloads.py` (extend)

**Interfaces:**
- Consumes: `build_payloads(..., site=, since=, months=)` from Task 1.
- Produces: CLI flags `--site`, `--since`, `--months`. `_build_where` returns a real per-view WHERE clause.

**Critical:** the `--months` cutoff is computed **once from pv** and applied to both views. Deriving it per file gives the sparser `ix` a different cutoff and clicks stop matching views.

- [ ] **Step 1: Write the failing test**

Add to `scripts/test_standalone_payloads.py`, called from `main()`:

```python
def test_slice(d: Path, con: duckdb.DuckDBPyConnection) -> None:
    print("--site keeps only that site's rows")
    payloads, stats = build_payloads(d, site="news and events")   # case-insensitive
    p = d / "slice_pv.parquet"
    p.write_bytes(payloads["pv"])
    check("site slice rows", con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{p.as_posix()}')").fetchone()[0], 4)
    check("site slice sites", stats["sites"], ["News and events"])

    print("--since cuts by absolute date")
    payloads, _ = build_payloads(d, since="2026-06-01")
    p = d / "since_pv.parquet"
    p.write_bytes(payloads["pv"])
    check("since rows", con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{p.as_posix()}')").fetchone()[0], 3)

    print("--months is relative to MAX(timestamp) in pv, not to today")
    # pv max is 2026-06-20; 1 month back = 2026-05-20 -> the January rows drop,
    # and ix (max 2026-06-21) must use PV's cutoff, keeping only its June row.
    payloads, _ = build_payloads(d, months=1)
    for view, expected in (("pv", 3), ("ix", 1)):
        p = d / f"months_{view}.parquet"
        p.write_bytes(payloads[view])
        check(f"months {view} rows", con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{p.as_posix()}')").fetchone()[0], expected)

    print("guards")
    try:
        build_payloads(d, site="Does Not Exist")
        print("FAIL: unknown site did not raise")
        sys.exit(1)
    except ValueError as e:
        check("unknown site names the available sites", "News and events" in str(e), True)

    try:
        build_payloads(d, since="2026-06-01", months=3)
        print("FAIL: --since with --months did not raise")
        sys.exit(1)
    except ValueError:
        print("  ok  --since with --months rejected")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/test_standalone_payloads.py`
Expected: FAIL at `site slice rows` — got 5, expected 4 (the stub `_build_where` filters nothing).

- [ ] **Step 3: Implement `_build_where`**

Replace the Task 1 stub with:

```python
def _build_where(con, present, cols, *, site=None, since=None, months=None) -> dict[str, str]:
    """Per-view WHERE clause. The time cutoff is computed ONCE from pv and
    applied to every view: a per-file cutoff would give the sparser ix a
    different stichtag and clicks would stop matching views."""
    if since and months:
        raise ValueError("--since and --months are mutually exclusive")

    src = {v: s for v, s, _ in present}
    floor = since
    if months:
        pv_max = con.execute(
            f"SELECT MAX(timestamp) FROM read_parquet('{src['pv'].as_posix()}')").fetchone()[0]
        floor = con.execute(
            "SELECT (?::TIMESTAMP - INTERVAL (?) MONTH)::VARCHAR", [pv_max, months]).fetchone()[0]

    if site:
        available = [r[0] for r in con.execute(
            f"SELECT DISTINCT site_name FROM read_parquet('{src['pv'].as_posix()}') "
            f"ORDER BY 1").fetchall()]
        if not any(a is not None and a.casefold() == site.casefold() for a in available):
            raise ValueError(
                f"--site {site!r} not found. Sites in this parquet: "
                + ", ".join(repr(a) for a in available))

    where = {}
    for view, _, _ in present:
        parts = []
        if site and "site_name" in cols[view]:
            parts.append(f"lower(s.site_name) = lower('{site.replace(chr(39), chr(39) * 2)}')")
        if floor and "timestamp" in cols[view]:
            parts.append(f"s.timestamp >= TIMESTAMP '{floor}'")
        where[view] = ("WHERE " + " AND ".join(parts)) if parts else ""

    for view, _, _ in present:
        n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{src[view].as_posix()}') s "
                        f"{where[view]}").fetchone()[0]
        if n == 0 and view == "pv":
            raise ValueError("0 rows after the filter — refusing to build an empty dashboard")
    return where
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python scripts/test_standalone_payloads.py`
Expected: PASS

- [ ] **Step 5: Wire the CLI flags and the short-window warning**

In `main()`:

```python
    parser.add_argument("--site", help="SiteName to keep (case-insensitive exact match; "
                                       "same semantics as process_site_pageviews.py --site)")
    parser.add_argument("--since", help="Keep rows from this date on (YYYY-MM-DD)")
    parser.add_argument("--months", type=int,
                        help="Keep the last N months relative to MAX(timestamp) in the "
                             "parquet (NOT to today — exports lag reality)")
```

Pass them to `build(...)`. Then, in `build()`, after `build_payloads` returns, warn on a short window:

```python
    mn, mx = stats["window"]
    span_days = (datetime.fromisoformat(mx) - datetime.fromisoformat(mn)).days
    if span_days < 180:
        print(f"WARNING: the slice spans {span_days} days. The dashboard compares each "
              f"KPI against the equal-length preceding period and defaults to a 90d "
              f"window, so under 180 days every delta silently disappears (reads as "
              f"'no change', not 'no baseline').")
```

Add `from datetime import datetime` to the imports.

- [ ] **Step 6: Verify against the real parquet**

Run: `python scripts/build_standalone_dashboard.py --site "News and events" --months 3 --output output/test_slice.html`
Expected: builds; prints the short-window WARNING; the file is ~1.4 MB vs the ~2.2 MB full build.

Run: `python scripts/build_standalone_dashboard.py --site "Nope"`
Expected: fails with `--site 'Nope' not found. Sites in this parquet: 'News and events'`

Clean up: `rm output/test_slice.html`

- [ ] **Step 7: Commit**

```bash
git add scripts/build_standalone_dashboard.py scripts/test_standalone_payloads.py
git commit -m "feat(standalone): add --site/--since/--months slicing with guards"
```

---

### Task 4: Per-site output naming and title

**Files:**
- Modify: `scripts/build_standalone_dashboard.py:48-49` (`GUIDE_STANDALONE_NAME` → parameter), `build()`, `main()`
- Test: `scripts/test_standalone_payloads.py` (extend)

**Interfaces:**
- Consumes: `build(..., site=...)` from Task 1.
- Produces: `slugify(name: str) -> str` — lowercase, non-alphanumerics collapsed to `_`, e.g. `"News and events"` → `"news_and_events"`.

Without `--site`, every name stays exactly as today. With `--site`, dashboard and guide both take the slug prefix and stay cross-linked as a pair. An explicit `--output` always wins.

- [ ] **Step 1: Write the failing test**

Add to `scripts/test_standalone_payloads.py`, called from `main()` (this one needs no fixture dir):

```python
def test_slugify() -> None:
    from build_standalone_dashboard import slugify
    print("slugify")
    check("spaces", slugify("News and events"), "news_and_events")
    check("punctuation", slugify("Group Functions & IT"), "group_functions_it")
    check("collapses runs", slugify("A  --  B"), "a_b")
    check("trims edges", slugify(" News! "), "news")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/test_standalone_payloads.py`
Expected: FAIL with `ImportError: cannot import name 'slugify'`

- [ ] **Step 3: Implement**

```python
def slugify(name: str) -> str:
    """'News and events' -> 'news_and_events' — for per-site output filenames."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
```

In `main()`, derive the default names from the slug when `--site` is given and `--output` is not:

```python
    dash_out, guide_out = args.output, args.guide_output
    if args.site and args.output == DEFAULT_OUTPUT:
        slug = slugify(args.site)
        dash_out = args.output.parent / f"{slug}_dashboard_standalone.html"
        guide_out = args.guide_output.parent / f"{slug}_guide_standalone.html"
```

`build()` gains a `guide_name` parameter used in the `guideLink` rewrite (replacing the `GUIDE_STANDALONE_NAME` module constant at line 154), and `main()` passes `guide_out.name`. Then `build_guide(..., dashboard_name=dash_out.name)` keeps the pair linked in both directions.

Also in `build()`, when `site` is set, retitle the dashboard so the recipient knows what they hold:

```python
    if site:
        html, n = re.subn(r"(const SITE_DISPLAY_NAME = ')[^']*(';)",
                          rf"\g<1>Site Owner Dashboard – {site}\g<2>", html, count=1)
        if n == 0:
            raise ValueError("SITE_DISPLAY_NAME constant not found in template")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python scripts/test_standalone_payloads.py`
Expected: PASS

- [ ] **Step 5: Verify the pair end-to-end**

Run: `python scripts/build_standalone_dashboard.py --site "News and events" --months 6`
Expected: writes `output/news_and_events_dashboard_standalone.html` and `output/news_and_events_guide_standalone.html`.

Confirm the cross-links resolve and the title is right:
```bash
grep -o 'news_and_events_guide_standalone.html' output/news_and_events_dashboard_standalone.html | head -1
grep -o 'news_and_events_dashboard_standalone.html' output/news_and_events_guide_standalone.html | head -1
grep -o "SITE_DISPLAY_NAME = '[^']*'" output/news_and_events_dashboard_standalone.html
```
Expected: each prints a match; the last prints `SITE_DISPLAY_NAME = 'Site Owner Dashboard – News and events'`.

Serve it and confirm the H1 and browser tab read `Site Owner Dashboard – News and events`, then remove the two test files.

- [ ] **Step 6: Update the module docstring**

The docstring at the top of `build_standalone_dashboard.py` describes the old behaviour. Add the new flags and state plainly that the embedded payload is anonymised and pruned by default, that the salt is per-build and never stored, and that `--keep-ids` output must not be distributed.

- [ ] **Step 7: Commit**

```bash
git add scripts/build_standalone_dashboard.py scripts/test_standalone_payloads.py
git commit -m "feat(standalone): per-site output names, guide pairing and title"
```

---

## Self-Review

**Spec coverage:** Anonymisation → Task 1. Column prune + `--keep-ids` → Task 2. `--site`/`--months`/`--since` + both guards → Task 3. Output naming, guide pairing, `SITE_DISPLAY_NAME` → Task 4. Default-unchanged → asserted in Tasks 1-3 (no flags → no WHERE) and verified against the real parquet in Task 1 Step 6. Out-of-scope items (ExcelJS, parquet anonymisation, `--site-id`) have no tasks, as intended.

**Type consistency:** `build_payloads` returns `(dict[str, bytes], dict)` in Task 1 and is consumed with that shape in Tasks 2-4. `_build_where` returns `dict[str, str]` (stub in Task 1, real in Task 3) — same shape both times. `DROP_COLS`/`SURROGATE_COLS` are keyed by view name (`"pv"`/`"ix"`), matching `VIEWS`.
