"""Build a self-contained site_dashboard_standalone.html for distribution.

Transforms dashboard/dashboard.html into a single file that runs from file://
(same mechanism as the SearchAnalytics / CampaignWe standalone builds):

- output/site_pageviews.parquet is recompressed with ZSTD and inlined as a
  base64 data island (<script type="text/plain" id="pv-parquet-b64">). The
  dashboard's reg() loader reads the island, registers it as a DuckDB file
  buffer and builds the view. output/site_interactions.parquet (Phase 2) is
  embedded the same way when present — otherwise skipped.
- Chart.js, the date adapter and ExcelJS are inlined from local vendored
  copies in dashboard/vendor/ so the build needs NO network for those —
  important behind a corporate proxy.
- DuckDB-WASM is left as a CDN ES-module import (the browser loads it at
  open time, which works through the proxy).

It also builds output/site_guide_standalone.html — the guide with every
screenshot inlined as base64 — and points the dashboard's Guide button at it.
The two files cross-link by relative name, so ship them together.

Run (from the SiteOwnerDashboard project root):
    python scripts/build_standalone_dashboard.py

To refresh the vendored libraries (rare — only on a version bump), run
scripts/vendor_libs.py on a machine with internet access.
"""
from __future__ import annotations

import argparse
import base64
import re
import secrets
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import duckdb

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = PROJECT_DIR / "dashboard" / "dashboard.html"
DEFAULT_PARQUET_DIR = PROJECT_DIR / "output"
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "site_dashboard_standalone.html"
VENDOR_DIR = PROJECT_DIR / "dashboard" / "vendor"

# The guide ships as its own self-contained file next to the dashboard
# standalone (its ~1 MB of screenshots would bloat the dashboard file). Both
# live in output/ and cross-link by relative name, so they travel as a pair.
DEFAULT_GUIDE_TEMPLATE = PROJECT_DIR / "dashboard" / "guide.html"
DEFAULT_GUIDE_OUTPUT = PROJECT_DIR / "output" / "site_guide_standalone.html"
GUIDE_IMG_DIR = PROJECT_DIR / "dashboard" / "img" / "guide"
DASHBOARD_STANDALONE_NAME = DEFAULT_OUTPUT.name
GUIDE_STANDALONE_NAME = DEFAULT_GUIDE_OUTPUT.name

# DuckDB view name -> (parquet filename, required). The data-island id the
# dashboard looks for is f"{view}-parquet-b64", so these view names MUST match
# the reg(view, file, …) calls in dashboard.html.
VIEWS = [
    ("pv", "site_pageviews.parquet", True),
    ("ix", "site_interactions.parquet", False),  # Phase 2 — embedded if present
]

# Each plain <script src=CDN> tag in the template, mapped to its local vendored
# file. DuckDB-WASM is intentionally excluded (CDN ES module at runtime).
LIBS = [
    ("https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js",
     "chart.umd.min.js"),
    ("https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js",
     "chartjs-adapter-date-fns.bundle.min.js"),
    ("https://cdn.jsdelivr.net/npm/exceljs@4.4.0/dist/exceljs.min.js",
     "exceljs.min.js"),
]


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
    parts = [f"SELECT DISTINCT {col} AS orig FROM read_parquet('{p.as_posix()}') s {w}"
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

    stats = _collect_stats(con, present, where, rows, cols)
    con.close()
    return payloads, stats


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


def _collect_stats(con, present, where, rows, cols) -> dict:
    src = {v: s for v, s, _ in present}
    pv = src["pv"].as_posix()
    mn, mx = con.execute(
        f"SELECT MIN(timestamp)::VARCHAR, MAX(timestamp)::VARCHAR "
        f"FROM read_parquet('{pv}') s {where['pv']}").fetchone()
    sites = [r[0] for r in con.execute(
        f"SELECT DISTINCT site_name FROM read_parquet('{pv}') s {where['pv']} "
        f"ORDER BY 1").fetchall()] if "site_name" in cols["pv"] else []
    # Same UNION-of-DISTINCT the surrogate map is built from, so an ix-only
    # person (clicked but their pageviews fell outside the slice) is still
    # counted — persons must span pv ∪ ix, not just pv.
    person_parts = [
        f"SELECT DISTINCT person_id FROM read_parquet('{s.as_posix()}') s {where[v]}"
        for v, s, _ in present if "person_id" in cols[v]]
    persons = con.execute(
        f"SELECT COUNT(*) FROM ({' UNION '.join(person_parts)})"
    ).fetchone()[0] if person_parts else 0
    return {"rows": rows, "sites": sites, "window": (mn, mx), "persons": persons}


def inline_parquet(html: str, parquet_bytes: bytes, island_id: str) -> str:
    """Inject the parquet as base64 in a non-executed data island, before </head>.

    A <script type="text/plain"> data island (read via textContent) is used
    instead of a `window.X="…"` JS string literal, because a multi-MB string
    literal is too large for the browser's JS compiler. Base64 contains no '<',
    so it cannot close the tag early.
    """
    b64 = base64.b64encode(parquet_bytes).decode("ascii")
    tag = f'<script type="text/plain" id="{island_id}">{b64}</script>\n'
    if "</head>" not in html:
        raise ValueError("template has no </head> to inject before")
    return html.replace("</head>", tag + "</head>", 1)


def read_lib(filename: str) -> str:
    """Read a vendored library from dashboard/vendor/."""
    path = VENDOR_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"vendored library missing: {path} — run scripts/vendor_libs.py "
            "on a machine with internet access"
        )
    return path.read_text(encoding="utf-8")


def inline_libs(html: str, libs=LIBS) -> str:
    """Replace each plain <script src=CDN> tag with the vendored library inline."""
    for url, filename in libs:
        tag = f'<script src="{url}"></script>'
        if tag not in html:
            raise ValueError(f"expected script tag not found in template: {url}")
        # Prevent a literal </script> inside the lib from closing our block early.
        content = read_lib(filename).replace("</script", "<\\/script")
        html = html.replace(tag, f"<script>/* {url} (vendored) */\n{content}\n</script>", 1)
    return html


def build(template_path: Path, parquet_dir: Path, output_path: Path, *,
          site: str | None = None, since: str | None = None,
          months: int | None = None, keep_ids: bool = False) -> Path:
    template_path = Path(template_path)
    parquet_dir = Path(parquet_dir)
    output_path = Path(output_path)
    if not template_path.exists():
        raise FileNotFoundError(f"template not found: {template_path}")

    html = template_path.read_text(encoding="utf-8")

    # Data islands first, while the template still has exactly one </head>
    # (a vendored lib could contain a literal "</head>" substring).
    payloads, stats = build_payloads(parquet_dir, site=site, since=since,
                                     months=months, keep_ids=keep_ids)
    mn, mx = stats["window"]
    span_days = (datetime.fromisoformat(mx) - datetime.fromisoformat(mn)).days
    if span_days < 180:
        print(f"WARNING: the slice spans {span_days} days. The dashboard compares each "
              f"KPI against the equal-length preceding period and defaults to a 90d "
              f"window, so under 180 days every delta silently disappears (reads as "
              f"'no change', not 'no baseline').")
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

    html = inline_libs(html)

    # Point the Guide button at the self-contained guide standalone (built by
    # build_guide, shipped alongside this file), not the dev-server guide.html.
    html, n = re.subn(r'(<a class="btn" id="guideLink"[^>]*\bhref=")guide\.html(")',
                      rf"\g<1>{GUIDE_STANDALONE_NAME}\g<2>", html, count=1)
    if n == 0:
        raise ValueError("guideLink anchor with href=\"guide.html\" not found in template")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"Total embedded parquet: {total / 1024:.0f} KB")
    return output_path


def inline_guide_images(html: str, img_dir: Path) -> str:
    """Replace every <img src="img/guide/NAME"> with an inline base64 data URI."""
    def repl(m: "re.Match") -> str:
        name = Path(m.group(1)).name
        path = img_dir / name
        if not path.exists():
            raise FileNotFoundError(f"guide image missing: {path}")
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'src="data:image/png;base64,{b64}"'
    return re.sub(r'src="(img/guide/[^"]+)"', repl, html)


def build_guide(guide_template: Path, img_dir: Path, output_path: Path,
                dashboard_name: str = DASHBOARD_STANDALONE_NAME) -> Path:
    """Build a single-file site_guide_standalone.html: inline every screenshot
    as base64 and repoint the 'Open the dashboard' links at the dashboard
    standalone, so the guide runs from file:// with no sibling assets."""
    guide_template = Path(guide_template)
    if not guide_template.exists():
        raise FileNotFoundError(f"guide template not found: {guide_template}")
    html = guide_template.read_text(encoding="utf-8")

    n_imgs = len(re.findall(r'src="img/guide/[^"]+"', html))
    html = inline_guide_images(html, Path(img_dir))
    html = html.replace('href="./dashboard.html"', f'href="{dashboard_name}"')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"Embedded {n_imgs} guide screenshots -> {output_path.name}")
    return output_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--parquet-dir", type=Path, default=DEFAULT_PARQUET_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--guide-template", type=Path, default=DEFAULT_GUIDE_TEMPLATE)
    parser.add_argument("--guide-output", type=Path, default=DEFAULT_GUIDE_OUTPUT)
    parser.add_argument("--no-guide", action="store_true",
                        help="skip building the self-contained guide standalone")
    parser.add_argument("--keep-ids", action="store_true",
                        help="keep gpn/user_id/view_id and the plaintext GPN-derived "
                             "person_id/visit_id (full-fidelity local build; NEVER "
                             "distribute the result — it contains personal data)")
    parser.add_argument("--site", help="SiteName to keep (case-insensitive exact match; "
                                       "same semantics as process_site_pageviews.py --site)")
    parser.add_argument("--since", help="Keep rows from this date on (YYYY-MM-DD)")
    parser.add_argument("--months", type=int,
                        help="Keep the last N months relative to MAX(timestamp) in the "
                             "parquet (NOT to today — exports lag reality)")
    args = parser.parse_args(argv)
    out = build(args.template, args.parquet_dir, args.output, site=args.site,
               since=args.since, months=args.months, keep_ids=args.keep_ids)
    size_mb = out.stat().st_size / 1_048_576
    print(f"Wrote {out} ({size_mb:.1f} MB)")
    if not args.no_guide:
        guide = build_guide(args.guide_template, GUIDE_IMG_DIR, args.guide_output,
                            dashboard_name=Path(args.output).name)
        print(f"Wrote {guide} ({guide.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
