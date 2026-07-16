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
import argparse
import base64
import re
import sys
import tempfile
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


def recompress_zstd(parquet_path: Path) -> bytes:
    """Return the parquet recompressed with ZSTD (smaller base64 payload)."""
    src = Path(parquet_path).as_posix()
    con = duckdb.connect()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "z.parquet"
        con.execute(
            f"COPY (SELECT * FROM read_parquet('{src}')) "
            f"TO '{out.as_posix()}' (FORMAT PARQUET, COMPRESSION zstd)"
        )
        return out.read_bytes()


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


def build(template_path: Path, parquet_dir: Path, output_path: Path) -> Path:
    template_path = Path(template_path)
    parquet_dir = Path(parquet_dir)
    output_path = Path(output_path)
    if not template_path.exists():
        raise FileNotFoundError(f"template not found: {template_path}")

    html = template_path.read_text(encoding="utf-8")

    # Data islands first, while the template still has exactly one </head>
    # (a vendored lib could contain a literal "</head>" substring).
    total = 0
    for view, filename, required in VIEWS:
        pq = parquet_dir / filename
        if not pq.exists():
            if required:
                raise FileNotFoundError(
                    f"parquet not found: {pq} — run scripts/process_site_pageviews.py "
                    "(or scripts/generate_demo_data.py) first"
                )
            print(f"Skipped {filename} (not present — Phase 2 optional)")
            continue
        data = recompress_zstd(pq)
        total += len(data)
        html = inline_parquet(html, data, island_id=f"{view}-parquet-b64")
        print(f"Embedded {filename} as '{view}-parquet-b64' ({len(data) / 1024:.0f} KB)")

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
    args = parser.parse_args(argv)
    out = build(args.template, args.parquet_dir, args.output)
    size_mb = out.stat().st_size / 1_048_576
    print(f"Wrote {out} ({size_mb:.1f} MB)")
    if not args.no_guide:
        guide = build_guide(args.guide_template, GUIDE_IMG_DIR, args.guide_output,
                            dashboard_name=Path(args.output).name)
        print(f"Wrote {guide} ({guide.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
