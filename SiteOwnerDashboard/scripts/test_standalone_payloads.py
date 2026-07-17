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
        # Pins the pv-cutoff-applies-to-ix property (see test_slice's --months
        # block): lands strictly between pv's global-max-derived floor
        # (2026-05-20 11:05) and what a per-file (ix-own-max-derived) floor
        # would be (2026-05-21 09:00). Only the correct (pv-anchored) floor
        # keeps this row.
        (GPN_A, GPN_A, "s-4", "2026-05-20 20:00:00", "News and events", "u01"),
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

        print("surrogates are dense across the shared map, not per file")
        # The person map spans pv UNION ix = 3 people (A, B in both; C in ix only),
        # so it is the UNION of the two files that covers 1..3. pv holds 2 of those
        # 3 and WHICH two depends on the salt — asserting pv == [1,2] would be a
        # coin flip. The per-file property is the count; the dense-range property
        # belongs to the map (asserted in test_salt_orders_the_map).
        pv_p = {r[0] for r in con.execute(f"SELECT DISTINCT person_id FROM {pv}").fetchall()}
        ix_p = {r[0] for r in con.execute(f"SELECT DISTINCT person_id FROM {ix}").fetchall()}
        check("pv person surrogate count", len(pv_p), 2)
        check("ix person surrogate count", len(ix_p), 2)
        check("union of both files covers the whole map", sorted(pv_p | ix_p), [1, 2, 3])
        # visit_id lives only in pv, so its map is dense within pv.
        check("pv visit surrogates dense", sorted(r[0] for r in con.execute(
            f"SELECT DISTINCT visit_id FROM {pv}").fetchall()), [1, 2, 3])

        test_prune(d, con)
        test_slice(d, con)
        test_site_anchored_months(con)
        test_salt_orders_the_map(con)


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
    # pv max is 2026-06-20 11:05; 1 month back = 2026-05-20 11:05 -> the
    # January rows drop, and ix (own max 2026-06-21 09:00) must use PV's
    # cutoff, not its own. This is exactly what the extra ix row at
    # 2026-05-20 20:00 pins: it sits after the pv-anchored floor
    # (2026-05-20 11:05) but before what an ix-own-max floor would be
    # (2026-05-21 09:00), so a per-file-cutoff regression drops it while the
    # correct pv-anchored cutoff keeps it -> ix rows == 2, not 1.
    payloads, _ = build_payloads(d, months=1)
    for view, expected in (("pv", 3), ("ix", 2)):
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

    try:
        build_payloads(d, since="2026-06-20' OR '1'='1")
        print("FAIL: malformed --since did not raise")
        sys.exit(1)
    except ValueError as e:
        check("malformed --since names the expected format", "YYYY-MM-DD" in str(e), True)

    try:
        build_payloads(d, since="20/06/2026")
        print("FAIL: --since in the wrong date order did not raise")
        sys.exit(1)
    except ValueError as e:
        check("wrong-order --since names the expected format", "YYYY-MM-DD" in str(e), True)

    try:
        build_payloads(d, months=0)
        print("FAIL: --months 0 did not raise")
        sys.exit(1)
    except ValueError as e:
        check("--months 0 rejected", "must be >= 1" in str(e), True)

    try:
        build_payloads(d, months=-3)
        print("FAIL: --months -3 did not raise")
        sys.exit(1)
    except ValueError as e:
        check("--months -3 rejected", "must be >= 1" in str(e), True)

    try:
        build_payloads(d, since="2026-06-01", months=0)
        print("FAIL: --since with --months 0 did not raise (mutual exclusion "
              "must fire even though 0 is falsy)")
        sys.exit(1)
    except ValueError:
        print("  ok  --since with --months 0 rejected (mutual exclusion)")

    try:
        build_payloads(d, since="2030-01-01")
        print("FAIL: --since beyond all data did not raise")
        sys.exit(1)
    except ValueError as e:
        check("zero-rows error names the available sites",
              "News and events" in str(e), True)


def test_site_anchored_months(con: duckdb.DuckDBPyConnection) -> None:
    """--site X --months N must anchor the cutoff to X's own last data point,
    not to pv's global max (finding 6) — otherwise a site that stopped
    publishing long before other sites would starve to an empty build.

    Isolated fixture, not the shared one: the shared fixture's 'Other site'
    row happens to BE pv's global max (it is the single latest row in the
    whole file), so per-site vs. global anchoring compute the identical
    floor there and the property would not be pinned. This fixture gives
    each site a distinct, well-separated max so the two anchorings diverge.
    """
    print("--site + --months anchors to that site's own max, not pv's global max")
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        pv = pd.DataFrame([
            ("Alpha", "2026-01-01 08:00:00"),
            ("Alpha", "2026-01-15 09:00:00"),
            ("Beta", "2026-06-01 08:00:00"),
            ("Beta", "2026-06-20 09:00:00"),
        ], columns=["site_name", "timestamp"])
        pv["timestamp"] = pd.to_datetime(pv["timestamp"])
        pv.to_parquet(d / "site_pageviews.parquet", index=False)

        # Correct (site-anchored): floor = Alpha's own max (2026-01-15 09:00)
        # minus 1 month = 2025-12-15 09:00 -> both Alpha rows qualify -> 2.
        # Bug (pv-global-anchored): floor = Beta's max (2026-06-20 09:00)
        # minus 1 month = 2026-05-20 09:00 -> BOTH Alpha rows (Jan 1, Jan 15)
        # are before that floor -> 0 rows -> "0 rows after the filter" would
        # fire for a site that is very much still present in the data. That
        # crash-vs-2-rows gap is what makes the fixture sharp.
        payloads, _ = build_payloads(d, site="Alpha", months=1)
        p = d / "alpha.parquet"
        p.write_bytes(payloads["pv"])
        check("site-anchored months rows", con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{p.as_posix()}')").fetchone()[0], 2)


def test_salt_orders_the_map(con: duckdb.DuckDBPyConnection) -> None:
    """The salt must drive the ORDER of the dense rank, not just exist.

    Asserted on _make_surrogate_map with two fixed salts over 50 persons: if the
    salt were ignored (or the rank ordered by the raw value), both salts would
    produce identical mappings. With 50 persons an accidental match has
    probability 1/50! — this assertion is not a coin flip, unlike comparing two
    random-salt builds over a 2-person fixture.
    """
    from build_standalone_dashboard import _make_surrogate_map
    print("the salt orders the map")
    with tempfile.TemporaryDirectory() as t:
        src = Path(t) / "persons.parquet"
        pd.DataFrame({"person_id": [f"0{i:07d}" for i in range(50)]}).to_parquet(src, index=False)

        orders = []
        for salt in ("saltA" * 6, "saltB" * 6):
            _make_surrogate_map(con, "person_id", [(src, "")], salt)
            orders.append(tuple(r[0] for r in con.execute(
                "SELECT orig FROM person_id_map ORDER BY surrogate").fetchall()))

        if orders[0] == orders[1]:
            print("FAIL the salt orders the map: both salts produced the same order — "
                  "the salt is being ignored")
            sys.exit(1)
        print("  ok  different salts -> different order")
        check("map is 1:1 over all persons", len(set(orders[0])), 50)
        check("surrogates are dense 1..50", sorted(r[0] for r in con.execute(
            "SELECT surrogate FROM person_id_map").fetchall()), list(range(1, 51)))

    print("\nAll assertions passed.")


if __name__ == "__main__":
    main()
