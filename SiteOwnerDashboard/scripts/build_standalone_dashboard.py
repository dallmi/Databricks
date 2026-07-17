"""Build a self-contained site_dashboard_standalone.html for distribution.

Transforms dashboard/dashboard.html into a single file that runs from file://
(same mechanism as the SearchAnalytics / CampaignWe standalone builds):

- output/site_pageviews.parquet (and output/site_interactions.parquet, Phase 2,
  when present) is sliced, pruned and anonymised at embed time — see
  "Data handling" below — then compressed with ZSTD and inlined as a base64
  data island (<script type="text/plain" id="pv-parquet-b64"> /
  "ix-parquet-b64">). The dashboard's reg() loader reads the island,
  registers it as a DuckDB file buffer and builds the view.
- Chart.js, the date adapter and ExcelJS are inlined from local vendored
  copies in dashboard/vendor/ so the build needs NO network for those —
  important behind a corporate proxy.
- DuckDB-WASM is left as a CDN ES-module import (the browser loads it at
  open time, which works through the proxy).

It also builds output/site_guide_standalone.html — the guide with every
screenshot inlined as base64 — and points the dashboard's Guide button at it.
The two files cross-link by relative name, so ship them together.

Data handling (default, no flags needed):
- The embedded payload is ANONYMISED and PRUNED by default. GPN-derived
  columns (person_id, and visit_id which carries the GPN as a prefix) are
  replaced with dense integer surrogates, salted with a random per-build
  value. The salt is generated fresh for every run and never written to
  disk or logged — it cannot be recovered afterwards, so the mapping is
  one-way. Ballast columns that are per-row-unique or high-cardinality
  (gpn, user_id, view_id/event_id) are dropped outright; they are never
  referenced by the dashboard and dominate the file size.
- `--keep-ids` disables all of the above and embeds the source data
  verbatim, GPNs included. The resulting file contains personal data and
  MUST NOT be distributed — it exists only for local full-fidelity
  debugging.

Slicing flags (embed a subset instead of the whole parquet):
- `--site NAME`      keep only rows for one site (case-insensitive exact
                      match against site_name).
- `--since YYYY-MM-DD` keep rows from this date on.
- `--months N`       keep the last N months, anchored to MAX(timestamp) —
                      of the given --site when one is passed, otherwise of
                      the whole parquet. Mutually exclusive with --since.
- With `--site` given and `--output` left at its default, the dashboard and
  guide filenames are automatically prefixed with the slugified site name
  (e.g. "News and events" -> news_and_events_dashboard_standalone.html /
  news_and_events_guide_standalone.html) and the dashboard is retitled
  "Site Owner Dashboard – <site>". An explicit `--output` always wins over
  this derivation. Without `--site`, filenames are unchanged from before.

Run (from the SiteOwnerDashboard project root):
    python scripts/build_standalone_dashboard.py
    python scripts/build_standalone_dashboard.py --site "News and events" --months 6

To refresh the vendored libraries (rare — only on a version bump), run
scripts/vendor_libs.py on a machine with internet access.
"""
from __future__ import annotations

import argparse
import base64
import json
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


def _validate_since(value: str) -> str:
    """Validate --since is a strict YYYY-MM-DD date.

    This is not just format-checking: it is also what makes the later string
    interpolation into SQL (`TIMESTAMP '{floor}'`) safe. A value that passes
    strptime with this format can contain only digits and dashes, so it can
    never carry a quote or break out of the literal.
    """
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(
            f"--since {value!r} is not a valid date — expected YYYY-MM-DD") from e
    return value


def _build_where(con, present, cols, *, site=None, since=None, months=None) -> dict[str, str]:
    """Per-view WHERE clause. The time cutoff is computed ONCE (from pv, or
    from pv restricted to --site when --site is given) and applied to every
    view: a per-file cutoff would give the sparser ix a different cutoff
    and clicks would stop matching views."""
    if since is not None and months is not None:
        raise ValueError("--since and --months are mutually exclusive")
    if since is not None:
        since = _validate_since(since)
    if site is not None and not site.strip():
        raise ValueError(f"--site {site!r} must not be empty")

    src = {v: s for v, s, _ in present}

    available = []
    if "site_name" in cols["pv"]:
        available = [r[0] for r in con.execute(
            f"SELECT DISTINCT site_name FROM read_parquet('{src['pv'].as_posix()}') "
            f"ORDER BY 1").fetchall()]

    site_clause = ""
    if site is not None:
        if not any(a is not None and a.casefold() == site.casefold() for a in available):
            raise ValueError(
                f"--site {site!r} not found. Sites in this parquet: "
                + ", ".join(repr(a) for a in available))
        site_clause = f" WHERE lower(s.site_name) = lower('{site.replace(chr(39), chr(39) * 2)}')"

    floor = since
    if months is not None:
        if months < 1:
            raise ValueError(f"--months must be >= 1, got {months}")
        # Anchored to --site's own last data point when a site is given, not
        # pv's global max: a site that stopped publishing long ago must not
        # be starved by a more recently active site's timestamps.
        pv_max = con.execute(
            f"SELECT MAX(timestamp) FROM read_parquet('{src['pv'].as_posix()}') s"
            f"{site_clause}").fetchone()[0]
        floor = con.execute(
            "SELECT (?::TIMESTAMP - INTERVAL (?) MONTH)::VARCHAR", [pv_max, months]).fetchone()[0]

    where = {}
    for view, _, _ in present:
        parts = []
        if site is not None:
            if "site_name" not in cols[view]:
                raise ValueError(
                    f"--site was requested but view {view!r} has no site_name "
                    "column — refusing to silently ship that view unfiltered")
            parts.append(f"lower(s.site_name) = lower('{site.replace(chr(39), chr(39) * 2)}')")
        if floor:
            if "timestamp" not in cols[view]:
                raise ValueError(
                    f"a time filter (--since/--months) was requested but view "
                    f"{view!r} has no timestamp column — refusing to silently "
                    "ship that view unfiltered")
            parts.append(f"s.timestamp >= TIMESTAMP '{floor}'")
        where[view] = ("WHERE " + " AND ".join(parts)) if parts else ""

    # pv is the only view whose emptiness is fatal — an empty ix is a
    # legitimate build (Phase 2 data is optional).
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{src['pv'].as_posix()}') s "
                    f"{where['pv']}").fetchone()[0]
    if n == 0:
        raise ValueError(
            "0 rows after the filter — refusing to build an empty dashboard. "
            "Sites in this parquet: " + ", ".join(repr(a) for a in available))
    return where


def _collect_stats(con, present, where, rows, cols) -> dict:
    src = {v: s for v, s, _ in present}
    pv = src["pv"].as_posix()
    # span_days computed in SQL, not via datetime.fromisoformat(mx) -
    # datetime.fromisoformat(mn): DuckDB trims trailing zeros from fractional
    # seconds ('.100' -> '.1'), and Python 3.9's fromisoformat only accepts
    # 3- or 6-digit fractions, so that combination crashes on ~5% of real
    # timestamps. An all-NULL timestamp column (mn/mx both NULL) yields a
    # NULL span_days here instead of a TypeError.
    mn, mx, span_days = con.execute(
        f"SELECT MIN(timestamp)::VARCHAR, MAX(timestamp)::VARCHAR, "
        f"date_diff('day', MIN(timestamp), MAX(timestamp)) "
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
    return {"rows": rows, "sites": sites, "window": (mn, mx), "persons": persons,
            "span_days": span_days}


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


def slugify(name: str) -> str:
    """'News and events' -> 'news_and_events' — for per-site output filenames."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def build(template_path: Path, parquet_dir: Path, output_path: Path, *,
          site: str | None = None, since: str | None = None,
          months: int | None = None, keep_ids: bool = False,
          guide_name: str = GUIDE_STANDALONE_NAME) -> Path:
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
    span_days = stats["span_days"]
    mn, mx = stats["window"]
    window_str = f"{mn[:10]} .. {mx[:10]}" if mn and mx else "n/a"
    print(f"Payload: {stats['persons']:,} persons, window {window_str}")
    if span_days is not None and span_days < 180:
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
                      rf"\g<1>{guide_name}\g<2>", html, count=1)
    if n == 0:
        raise ValueError("guideLink anchor with href=\"guide.html\" not found in template")

    if site:
        # json.dumps does the JS string escaping (quotes, backslashes, control
        # chars); the "</" guard stops a site name from closing the enclosing
        # <script> tag early. The lambda replacement sidesteps re.sub's own
        # backslash processing of the replacement string (a site name
        # containing e.g. '\1' would otherwise mangle the substitution).
        lit = json.dumps(f"Site Owner Dashboard – {site}").replace("</", "<\\/")
        html, n = re.subn(r"const SITE_DISPLAY_NAME = '[^']*';",
                          lambda m: f"const SITE_DISPLAY_NAME = {lit};", html, count=1)
        if n == 0:
            raise ValueError("SITE_DISPLAY_NAME constant not found in template")

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
                        help="Keep the last N months relative to MAX(timestamp) "
                             "in the parquet — anchored to --site's own max "
                             "when --site is given, not the whole parquet's "
                             "(NOT to today — exports lag reality)")
    args = parser.parse_args(argv)

    dash_out, guide_out = args.output, args.guide_output
    if args.site:
        slug = slugify(args.site)
        if args.output == DEFAULT_OUTPUT:
            dash_out = args.output.parent / f"{slug}_dashboard_standalone.html"
        if args.guide_output == DEFAULT_GUIDE_OUTPUT:
            guide_out = args.guide_output.parent / f"{slug}_guide_standalone.html"

    out = build(args.template, args.parquet_dir, dash_out, site=args.site,
                since=args.since, months=args.months, keep_ids=args.keep_ids,
                guide_name=guide_out.name)
    size_mb = out.stat().st_size / 1_048_576
    print(f"Wrote {out} ({size_mb:.1f} MB)")
    if not args.no_guide:
        guide = build_guide(args.guide_template, GUIDE_IMG_DIR, guide_out,
                            dashboard_name=dash_out.name)
        print(f"Wrote {guide} ({guide.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
