#!/usr/bin/env python3
"""
Local HTML preview generator for the FitNesse knowledge base.

Reads .txt files from docs/fitnesse/pages/ and writes standalone HTML pages
to docs/fitnesse/preview/ that approximate the FitNesse rendering, so you
can verify content, tables, cross-links, and PlantUML diagrams before
uploading to the corporate wiki.

Usage:
    python3 scripts/fitnesse_preview_build.py
    # then open: docs/fitnesse/preview/index.html

PlantUML blocks are rendered server-side via www.plantuml.com (the SVG URL
bakes in the diagram as <img src>). Requires internet on your local machine
but NOT on the corp machine (you run this locally, not in the corp env).
"""

from __future__ import annotations

import html
import os
import re
import sys
import zlib
from pathlib import Path
from urllib.parse import quote

PAGES_DIR = Path("docs/fitnesse/pages")
OUT_DIR = Path("docs/fitnesse/preview")
FITNESSE_ROOT_PREFIX = ".EmployeeEngagement.CPlanGICTrackingCLARITYDashboard.MultiChannelDataModel"

# ---------------------------------------------------------------------------
# PlantUML URL encoding (www.plantuml.com / ~deflate + 6-bit alphabet)
# ---------------------------------------------------------------------------

PLANTUML_ALPHABET = (
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "-_"
)


def _encode3bytes(b1: int, b2: int, b3: int) -> str:
    c1 = b1 >> 2
    c2 = ((b1 & 0x3) << 4) | (b2 >> 4)
    c3 = ((b2 & 0xF) << 2) | (b3 >> 6)
    c4 = b3 & 0x3F
    return (
        PLANTUML_ALPHABET[c1 & 0x3F]
        + PLANTUML_ALPHABET[c2 & 0x3F]
        + PLANTUML_ALPHABET[c3 & 0x3F]
        + PLANTUML_ALPHABET[c4 & 0x3F]
    )


def plantuml_encode(source: str) -> str:
    """Encode a PlantUML source string for use in a plantuml.com URL."""
    compressed = zlib.compress(source.encode("utf-8"), 9)[2:-4]
    out = []
    for i in range(0, len(compressed), 3):
        b1 = compressed[i]
        b2 = compressed[i + 1] if i + 1 < len(compressed) else 0
        b3 = compressed[i + 2] if i + 2 < len(compressed) else 0
        out.append(_encode3bytes(b1, b2, b3))
    return "".join(out)


def plantuml_url(source: str, fmt: str = "svg") -> str:
    return f"https://www.plantuml.com/plantuml/{fmt}/{plantuml_encode(source)}"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def inline_format(text: str, pages_index: dict[str, str] | None = None) -> str:
    """Apply FitNesse inline formatting to a plain (HTML-escaped) string."""
    # Bold: '''x'''
    text = re.sub(r"'''(.+?)'''", r"<strong>\1</strong>", text)
    # Italic: ''x''   (must run after bold so we don't capture the tails)
    text = re.sub(r"''(.+?)''", r"<em>\1</em>", text)
    # Inline code: !-...-!
    text = re.sub(r"!-(.+?)-!", r"<code>\1</code>", text)
    # Explicit links: [[Text][url]]
    text = re.sub(r"\[\[([^\]]+?)\]\[([^\]]+?)\]\]", r'<a href="\2">\1</a>', text)

    # Cross-refs: !see .Full.Path  (rewrite to local .html when possible)
    def _xref(match: re.Match[str]) -> str:
        raw_path = match.group(1).strip().rstrip(".,;")
        name = raw_path.split(".")[-1]
        if pages_index is None:
            return f'<a href="#{html.escape(name)}">{html.escape(name)}</a>'
        # Strip the KB root prefix so we match our local file map.
        local = raw_path
        if local.startswith(FITNESSE_ROOT_PREFIX):
            local = local[len(FITNESSE_ROOT_PREFIX) :]
        # Strip any residual leading dot so keys like "JoinStrategy.StrategyContract"
        # match (pages_index keys have no leading dot).
        local = local.lstrip(".")
        if local == "":
            target = pages_index.get("", "index.html")
        elif local in pages_index:
            target = pages_index[local]
        else:
            # Last-ditch: best-effort match by trailing segment name.
            for key, href in pages_index.items():
                if key.endswith("." + name) or key == name:
                    target = href
                    break
            else:
                target = "index.html"
        return f'<a href="{target}">{html.escape(name)}</a>'

    text = re.sub(r"!see\s+([\w.]+)", _xref, text)
    return text


def _render_table(rows: list[list[str]], pages_index: dict[str, str] | None) -> str:
    out = ['<table class="fitnesse-table">']
    for row_idx, cells in enumerate(rows):
        tag = "th" if row_idx == 0 and any("'''" in c for c in cells) else "td"
        out.append("<tr>")
        for cell in cells:
            escaped = html.escape(cell)
            formatted = inline_format(escaped, pages_index)
            out.append(f"<{tag}>{formatted}</{tag}>")
        out.append("</tr>")
    out.append("</table>")
    return "\n".join(out)


def parse_fitnesse_to_html(
    text: str, pages_index: dict[str, str] | None
) -> str:
    """Render a FitNesse wiki source string to an HTML fragment."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.rstrip("\r")

        # PlantUML block
        if stripped.strip() == "!startuml":
            block: list[str] = []
            i += 1
            while i < n and lines[i].strip() != "!enduml":
                block.append(lines[i])
                i += 1
            i += 1  # skip !enduml
            src = "@startuml\n" + "\n".join(block) + "\n@enduml"
            out.append(
                f'<div class="plantuml"><img src="{plantuml_url(src)}" '
                f'alt="PlantUML diagram" loading="lazy" /></div>'
            )
            continue

        # Preformatted code: {{{ ... }}}
        if stripped.strip() == "{{{":
            block = []
            i += 1
            while i < n and lines[i].strip() != "}}}":
                block.append(lines[i])
                i += 1
            i += 1  # skip }}}
            escaped_code = html.escape("\n".join(block))
            out.append(f'<pre><code>{escaped_code}</code></pre>')
            continue

        # Heading !1..!6
        m = re.match(r"^(!\d)\s+(.+)$", stripped)
        if m:
            level = int(m.group(1)[1:])
            text_ = inline_format(html.escape(m.group(2)), pages_index)
            anchor = re.sub(r"\W+", "-", m.group(2)).strip("-")
            out.append(f'<h{level} id="{anchor}">{text_}</h{level}>')
            i += 1
            continue

        # Table: consecutive lines starting and ending with |
        if stripped.startswith("|") and stripped.endswith("|"):
            rows: list[list[str]] = []
            while (
                i < n
                and lines[i].rstrip("\r").startswith("|")
                and lines[i].rstrip("\r").endswith("|")
            ):
                raw = lines[i].rstrip("\r")[1:-1]
                cells = [c.strip() for c in raw.split("|")]
                rows.append(cells)
                i += 1
            out.append(_render_table(rows, pages_index))
            continue

        # Bullet list: lines starting with " * "
        if stripped.startswith(" * "):
            items: list[str] = []
            while i < n and lines[i].rstrip("\r").startswith(" * "):
                item_text = lines[i].rstrip("\r")[3:]
                items.append(inline_format(html.escape(item_text), pages_index))
                i += 1
            out.append("<ul>" + "".join(f"<li>{it}</li>" for it in items) + "</ul>")
            continue

        # Numbered list: " 1 ...", " 2 ...", etc. (FitNesse auto-numbers)
        if re.match(r"^ \d+\s", stripped):
            items = []
            while i < n and re.match(r"^ \d+\s", lines[i].rstrip("\r")):
                item_text = re.sub(r"^ \d+\s+", "", lines[i].rstrip("\r"))
                items.append(inline_format(html.escape(item_text), pages_index))
                i += 1
            out.append("<ol>" + "".join(f"<li>{it}</li>" for it in items) + "</ol>")
            continue

        # Blank line → paragraph break
        if not stripped.strip():
            i += 1
            continue

        # Paragraph: collect consecutive non-special lines
        para: list[str] = []
        while i < n:
            ln = lines[i].rstrip("\r")
            if not ln.strip():
                break
            if ln.startswith("!") and re.match(r"^!\d\s", ln):
                break
            if ln.strip() in ("!startuml", "{{{"):
                break
            if ln.startswith("|") and ln.endswith("|"):
                break
            if ln.startswith(" * ") or re.match(r"^ \d+\s", ln):
                break
            para.append(ln)
            i += 1
        if para:
            joined = " ".join(l.strip() for l in para)
            out.append(f"<p>{inline_format(html.escape(joined), pages_index)}</p>")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# File walker + HTML rendering
# ---------------------------------------------------------------------------


def discover_pages(pages_dir: Path) -> dict[str, Path]:
    """Map FitNesse sub-path (dotted, relative to KB root) -> local source file."""
    index: dict[str, Path] = {}
    for path in sorted(pages_dir.rglob("*.txt")):
        rel = path.relative_to(pages_dir)
        # pages/Overview.txt          -> ""  (content is the root page)
        # pages/ErDiagrams/X.txt      -> "ErDiagrams.X"
        # pages/DataGlossary/Hr/X.txt -> "DataGlossary.Hr.X"
        parts = list(rel.with_suffix("").parts)
        if parts == ["Overview"]:
            sub = ""
        else:
            sub = ".".join(parts)
        index[sub] = path
    return index


def pages_to_html_map(pages: dict[str, Path]) -> dict[str, str]:
    """Return sub -> output filename (for cross-link resolution)."""
    mapping: dict[str, str] = {}
    for sub in pages:
        if sub == "":
            mapping[sub] = "index.html"
        else:
            mapping[sub] = f"{sub}.html"
    return mapping


def build_sidebar_html(pages: dict[str, Path]) -> str:
    """Render a hierarchical sidebar listing all pages."""
    # Group by section.
    sections: dict[str, list[str]] = {}
    for sub in pages:
        if sub == "":
            continue
        parts = sub.split(".")
        section = parts[0]
        sections.setdefault(section, []).append(sub)

    parts_html = ['<nav class="sidebar">']
    parts_html.append(
        '<div class="sidebar-header"><a href="index.html">MultiChannelDataModel</a></div>'
    )
    for section_name in ["ErDiagrams", "JoinStrategy", "DataGlossary"]:
        if section_name not in sections:
            continue
        parts_html.append(f'<div class="sidebar-section">{section_name}</div>')
        parts_html.append("<ul>")
        subs = sorted(sections[section_name])
        for sub in subs:
            label_parts = sub.split(".")
            # Indent sub-sub-sections
            depth = len(label_parts) - 1
            label = label_parts[-1]
            parts_html.append(
                f'<li class="depth-{depth}"><a href="{sub}.html">{html.escape(label)}</a></li>'
            )
        parts_html.append("</ul>")
    parts_html.append("</nav>")
    return "\n".join(parts_html)


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title} — MultiChannelDataModel Preview</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="fn-header">
  <div class="fn-brand">FitNesse <span class="fn-brand-tag">preview</span></div>
  <div class="fn-crumbs">{breadcrumbs}</div>
</header>
<div class="fn-layout">
  {sidebar}
  <main class="fn-content">
    {content}
  </main>
</div>
<footer class="fn-footer">
  Local preview — generated by <code>scripts/fitnesse_preview_build.py</code>. Not connected to FitNesse.
</footer>
</body>
</html>
"""


def breadcrumbs(sub: str) -> str:
    if sub == "":
        return '<a href="index.html">MultiChannelDataModel</a>'
    parts = sub.split(".")
    crumbs = ['<a href="index.html">MultiChannelDataModel</a>']
    accum: list[str] = []
    for p in parts:
        accum.append(p)
        crumbs.append(f'<a href="{".".join(accum)}.html">{html.escape(p)}</a>')
    return " / ".join(crumbs)


CSS_CONTENT = """
* { box-sizing: border-box; }
body {
  margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
  font-size: 14px; color: #1d1d1d; background: #fafafa;
}
a { color: #0b6623; text-decoration: none; }
a:hover { text-decoration: underline; }
code, pre, .plantuml { font-family: 'SF Mono', Monaco, Consolas, monospace; }
code { background: #f2f2f2; padding: 1px 4px; border-radius: 2px; font-size: 13px; }

.fn-header {
  background: #5cb85c; color: #fff; padding: 8px 16px;
  display: flex; align-items: center; gap: 16px;
  border-bottom: 2px solid #3d8b3d;
}
.fn-brand { font-weight: 700; font-size: 16px; letter-spacing: 0.3px; }
.fn-brand-tag {
  font-weight: 400; font-size: 11px; background: rgba(255,255,255,0.25);
  padding: 1px 6px; border-radius: 2px; margin-left: 4px;
}
.fn-crumbs { color: #f0f8f0; font-size: 13px; }
.fn-crumbs a { color: #f0f8f0; }

.fn-layout { display: flex; min-height: calc(100vh - 80px); }
.sidebar {
  width: 280px; background: #fff; border-right: 1px solid #e5e5e5;
  padding: 12px 8px 20px; font-size: 13px; overflow-y: auto;
}
.sidebar-header { font-weight: 600; padding: 6px 8px; border-bottom: 1px solid #eee; margin-bottom: 8px; }
.sidebar-section {
  font-size: 11px; text-transform: uppercase; color: #666; letter-spacing: 0.5px;
  padding: 12px 8px 4px;
}
.sidebar ul { list-style: none; margin: 0; padding: 0; }
.sidebar li { padding: 3px 8px; }
.sidebar li.depth-1 { padding-left: 16px; font-weight: 500; }
.sidebar li.depth-2 { padding-left: 28px; color: #444; }
.sidebar li.depth-3 { padding-left: 40px; color: #444; }
.sidebar li a { display: block; color: #1d1d1d; }
.sidebar li a:hover { color: #0b6623; }

.fn-content {
  flex: 1; padding: 24px 32px; max-width: 960px;
  background: #fff; border-right: 1px solid #e5e5e5;
}
.fn-content h1 { font-size: 28px; border-bottom: 2px solid #5cb85c; padding-bottom: 6px; margin-top: 0; }
.fn-content h2 { font-size: 20px; border-bottom: 1px solid #e5e5e5; padding-bottom: 4px; margin-top: 28px; }
.fn-content h3 { font-size: 16px; margin-top: 20px; }
.fn-content p  { line-height: 1.55; }
.fn-content ul, .fn-content ol { line-height: 1.6; }

.fitnesse-table {
  border-collapse: collapse; margin: 12px 0;
  border: 1px solid #c9c9c9;
}
.fitnesse-table td, .fitnesse-table th {
  border: 1px solid #d5d5d5; padding: 6px 10px; vertical-align: top;
  font-size: 13px;
}
.fitnesse-table tr:nth-child(even) td { background: #fafafa; }

pre {
  background: #f6f6f6; border: 1px solid #e1e1e1; border-radius: 2px;
  padding: 10px 12px; overflow-x: auto; font-size: 12.5px; line-height: 1.45;
  white-space: pre;
}

.plantuml {
  margin: 16px 0; padding: 10px; background: #fff; border: 1px solid #e5e5e5;
  text-align: center; overflow-x: auto;
}
.plantuml img { max-width: 100%; height: auto; }

.fn-footer {
  padding: 12px 20px; font-size: 12px; color: #888; background: #f4f4f4;
  border-top: 1px solid #e5e5e5;
}
"""


def render_page(
    sub: str, source_path: Path, pages_index: dict[str, str], sidebar: str
) -> str:
    text = source_path.read_text(encoding="utf-8")
    # Page title: use first !1 line or sub name.
    title_match = re.search(r"^!1\s+(.+)$", text, flags=re.MULTILINE)
    title = title_match.group(1).strip() if title_match else sub or "Overview"
    # Strip FitNesse bold markers from title.
    title_plain = re.sub(r"'''(.+?)'''", r"\1", title)
    title_plain = re.sub(r"''(.+?)''", r"\1", title_plain)

    content = parse_fitnesse_to_html(text, pages_index)

    return HTML_TEMPLATE.format(
        title=html.escape(title_plain),
        breadcrumbs=breadcrumbs(sub),
        sidebar=sidebar,
        content=content,
    )


def main() -> int:
    if not PAGES_DIR.is_dir():
        print(f"ERROR: pages directory not found: {PAGES_DIR}", file=sys.stderr)
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    pages = discover_pages(PAGES_DIR)
    if not pages:
        print(f"ERROR: no .txt files under {PAGES_DIR}", file=sys.stderr)
        return 2

    pages_index = pages_to_html_map(pages)
    sidebar = build_sidebar_html(pages)

    # Write CSS
    (OUT_DIR / "style.css").write_text(CSS_CONTENT, encoding="utf-8")

    # Render each page
    for sub, source_path in pages.items():
        html_text = render_page(sub, source_path, pages_index, sidebar)
        out_name = pages_index[sub]
        (OUT_DIR / out_name).write_text(html_text, encoding="utf-8")
        print(f"  wrote {out_name}  (from {source_path.relative_to(PAGES_DIR)})")

    print()
    print(f"Done — {len(pages)} pages written to {OUT_DIR}/")
    print(f"Open in browser:   file://{(OUT_DIR / 'index.html').resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
