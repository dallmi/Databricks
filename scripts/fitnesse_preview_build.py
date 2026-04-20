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
            escaped = html.escape(cell, quote=False)
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
            text_ = inline_format(html.escape(m.group(2), quote=False), pages_index)
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
                items.append(inline_format(html.escape(item_text, quote=False), pages_index))
                i += 1
            out.append("<ul>" + "".join(f"<li>{it}</li>" for it in items) + "</ul>")
            continue

        # Numbered list: " 1 ...", " 2 ...", etc. (FitNesse auto-numbers)
        if re.match(r"^ \d+\s", stripped):
            items = []
            while i < n and re.match(r"^ \d+\s", lines[i].rstrip("\r")):
                item_text = re.sub(r"^ \d+\s+", "", lines[i].rstrip("\r"))
                items.append(inline_format(html.escape(item_text, quote=False), pages_index))
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
            out.append(f"<p>{inline_format(html.escape(joined, quote=False), pages_index)}</p>")

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
<title>{title}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="fn-header">
  <div class="fn-header-inner">
    <a class="fn-brand" href="index.html">
      <svg class="fn-logo" viewBox="0 0 32 32" aria-hidden="true">
        <circle cx="10" cy="10" r="6" fill="#6fba2c"/>
        <circle cx="22" cy="10" r="6" fill="#d13a0e" opacity="0.85"/>
        <circle cx="16" cy="22" r="6" fill="#e88e00" opacity="0.85"/>
      </svg>
      <span class="fn-brand-text">FitNesse</span>
    </a>
    <nav class="fn-actions">
      <span class="fn-action">Edit <span class="fn-caret">▾</span></span>
      <span class="fn-action">Add <span class="fn-caret">▾</span></span>
      <span class="fn-action">Tools <span class="fn-caret">▾</span></span>
    </nav>
  </div>
  <div class="fn-crumbs">{breadcrumbs}</div>
</header>
<main class="fn-content">
  {content}
</main>
<footer class="fn-footer">
  <a href="#">User Guide</a> | <a href="#">Diagram Guide</a> | <a href="index.html">root</a> | Press '?' for keyboard shortcuts | <a href="#">Plugins</a> | <a href="#">Contact</a> | <span class="fn-footer-note">(local preview — not connected to FitNesse)</span>
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
  margin: 0;
  font-family: Arial, Helvetica, 'Segoe UI', sans-serif;
  font-size: 14px; color: #000; background: #fff;
}
a { color: #3a6ea5; text-decoration: none; }
a:hover { text-decoration: underline; }
code, pre { font-family: 'Courier New', Consolas, monospace; }
code { background: #f4f4f4; padding: 1px 3px; font-size: 13px; }

/* Header bar — approximates corp FitNesse look */
.fn-header {
  padding: 4px 14px 0;
  background: #fff;
  border-bottom: 1px solid #d7d7d7;
}
.fn-header-inner {
  display: flex; align-items: center; gap: 18px;
  padding-bottom: 4px;
}
.fn-brand {
  display: flex; align-items: center; gap: 6px;
  text-decoration: none;
}
.fn-logo { width: 28px; height: 28px; }
.fn-brand-text {
  color: #6fba2c; font-weight: 700; font-size: 20px;
  letter-spacing: 0.3px; font-family: 'Segoe UI', Arial, sans-serif;
}
.fn-brand:hover { text-decoration: none; }
.fn-brand:hover .fn-brand-text { color: #5aa020; }

.fn-actions { display: flex; gap: 14px; font-size: 13px; color: #333; }
.fn-action { cursor: default; color: #333; }
.fn-caret { font-size: 10px; color: #666; }

.fn-crumbs {
  padding: 6px 14px 8px;
  font-size: 13px; color: #666;
  background: #fff;
}
.fn-crumbs a { color: #3a6ea5; }

/* Main content */
.fn-content {
  padding: 14px 22px 40px;
  background: #fff;
  max-width: none;
  line-height: 1.45;
}

.fn-content h1 {
  font-size: 28px; font-weight: 700; color: #000;
  margin: 12px 0 18px; padding: 0;
  border: none;
  font-family: Arial, sans-serif;
}
.fn-content h2 {
  font-size: 22px; font-weight: 700; color: #000;
  margin: 24px 0 10px; padding: 0;
  border: none;
}
.fn-content h3 {
  font-size: 16px; font-weight: 700; color: #000;
  margin: 18px 0 6px; padding: 0;
}
.fn-content p { margin: 8px 0; }
.fn-content ul, .fn-content ol { margin: 6px 0; padding-left: 26px; line-height: 1.55; }
.fn-content li { margin: 2px 0; }

/* Tables — match FitNesse default */
.fitnesse-table {
  border-collapse: collapse; margin: 10px 0;
  border: 1px solid #888;
}
.fitnesse-table td, .fitnesse-table th {
  border: 1px solid #888; padding: 4px 8px; vertical-align: top;
  font-size: 13px; text-align: left;
}
.fitnesse-table th { background: #ededed; font-weight: 700; }

/* Preformatted / code blocks */
pre {
  background: #f6f6f6; border: 1px solid #d1d1d1;
  padding: 8px 10px; margin: 8px 0;
  overflow-x: auto; font-size: 12.5px; line-height: 1.45;
  white-space: pre;
}

/* PlantUML diagram containers — no heavy border, just center */
.plantuml {
  margin: 12px 0; padding: 4px 0;
  background: #fff;
  text-align: left;
  overflow-x: auto;
}
.plantuml img { max-width: 100%; height: auto; }

/* Footer — mimic FitNesse footer link bar */
.fn-footer {
  padding: 10px 14px;
  font-size: 12px; color: #555;
  background: #fff;
  border-top: 1px solid #d7d7d7;
  margin-top: 30px;
}
.fn-footer a { color: #3a6ea5; }
.fn-footer-note { color: #999; font-style: italic; }
"""


def render_page(
    sub: str, source_path: Path, pages_index: dict[str, str]
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
    # Write CSS
    (OUT_DIR / "style.css").write_text(CSS_CONTENT, encoding="utf-8")

    # Render each page
    for sub, source_path in pages.items():
        html_text = render_page(sub, source_path, pages_index)
        out_name = pages_index[sub]
        (OUT_DIR / out_name).write_text(html_text, encoding="utf-8")
        print(f"  wrote {out_name}  (from {source_path.relative_to(PAGES_DIR)})")

    print()
    print(f"Done — {len(pages)} pages written to {OUT_DIR}/")
    print(f"Open in browser:   file://{(OUT_DIR / 'index.html').resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
