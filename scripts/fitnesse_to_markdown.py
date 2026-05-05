#!/usr/bin/env python3
"""
Convert FitNesse wiki .txt files to clean Markdown for Copilot Studio grounding.

Reads from a source directory of FitNesse raw wiki source (the layout produced
by fitnesse_backup_crawl.py or fitnesse_download.py) and writes a mirrored
tree of .md files to FITNESSE_MARKDOWN_DIR. Each output file gets YAML
frontmatter with `title` and `source_path` so retrieval has stable metadata.

Handled FitNesse syntax (the cases that actually appear in our pages):
  - !1..!6 headers
  - '''bold''' and ''italic''
  - {{{ ... }}} fenced code blocks (multi-line or inline)
  - !-literal-! inline code
  - tables (| ... |), with bold-header detection
  - lists: ` * item` and ` 1 item`
  - [[label][url]] links; internal .Path.To.Page → relative .md links
  - !startuml ... !enduml → ```plantuml fenced blocks
  - !see / !include / !note / !contents / !define / !anchor / !c / !plain
  - ---- horizontal rule

Not handled (rare or context-specific):
  - Multi-line !define {} blocks
  - !style_xxx() inline directives
  - WikiWord auto-links (left as plain text)

Usage:
    python scripts/fitnesse_to_markdown.py
    python scripts/fitnesse_to_markdown.py --source-dir docs/fitnesse/backup/latest
    python scripts/fitnesse_to_markdown.py --dry-run
    python scripts/fitnesse_to_markdown.py --only DataGlossary/Hr/TblHrEmployee.txt
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from _config import (  # noqa: E402
    FITNESSE_PAGES_DIR,
    FITNESSE_MARKDOWN_DIR,
)

HEADER_RE = re.compile(r"^!([1-6])\s+(.*)$")
LIST_BULLET_RE = re.compile(r"^(\s+)\*\s+(.*)$")
LIST_NUM_RE = re.compile(r"^(\s+)(\d+)\s+(.*)$")
SEE_RE = re.compile(r"^\s*!see\s+(.+)$")
INCLUDE_RE = re.compile(r"^\s*!include\s+(.+)$")
NOTE_RE = re.compile(r"^\s*!note\s+(.+)$")
CENTER_RE = re.compile(r"^\s*!c\s+(.+)$")
PLAIN_RE = re.compile(r"^\s*!plain\s+(.+)$")
LINK_RE = re.compile(r"\[\[([^\]]+)\]\[([^\]]+)\]\]")
LITERAL_RE = re.compile(r"!-\s*(.+?)\s*-!")
BOLD_RE = re.compile(r"'''(.+?)'''")
ITALIC_RE = re.compile(r"''(.+?)''")
# Internal FitNesse path: dot-prefixed, segments are word-chars, dots only
# act as separators (not terminators) so we never absorb a trailing
# sentence-ending period.
INLINE_SEE_RE = re.compile(r"!see\s+(\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)")
INLINE_INCLUDE_RE = re.compile(r"!include\s+(\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)")

# Optional dotted prefix that gets stripped from internal FitNesse paths
# before they are turned into relative .md links. Set by main() based on
# the --strip-prefix CLI arg (or the matching env value).
_STRIP_PREFIX = ""

# Directory of the markdown file currently being written, relative to the
# output root. Used by convert_internal_link to emit links that resolve
# correctly from the current file's location (e.g. "../Foo/_page.md")
# rather than from the markdown-root. Set per-file by convert_file.
_CURRENT_REL_DIR: Path | None = None


def normalize_internal_path(path: str) -> tuple[str, str]:
    """Take a raw FitNesse internal path (possibly leading-dot-prefixed),
    return (display_text, link_target). Strips the leading dot for display
    and applies _STRIP_PREFIX so links land inside the markdown tree."""
    raw = path
    if raw.startswith("."):
        raw = raw[1:]
    if _STRIP_PREFIX and raw.startswith(_STRIP_PREFIX + "."):
        raw = raw[len(_STRIP_PREFIX) + 1:]
    elif _STRIP_PREFIX and raw == _STRIP_PREFIX:
        raw = ""
    return raw or path, raw


def convert_internal_link(url: str) -> str:
    """Best-effort conversion of FitNesse internal page paths to relative .md.

    External URLs (http://, https://, mailto:) pass through unchanged.
    A leading dot indicates an absolute FitNesse path; we strip it (and the
    configured --strip-prefix) and turn the dotted path into a slashed link
    pointing at the page's _page.md. The link is rendered relative to
    _CURRENT_REL_DIR when set, so it resolves correctly from the file
    being written; otherwise it stays markdown-root-relative.
    """
    if url.startswith(("http://", "https://", "mailto:", "/")):
        return url
    _, target = normalize_internal_path(url)
    target_rel = "_page.md" if not target else target.replace(".", "/") + "/_page.md"
    if _CURRENT_REL_DIR is None:
        return target_rel
    rel = os.path.relpath(target_rel, _CURRENT_REL_DIR.as_posix())
    return rel.replace("\\", "/")


def convert_inline(text: str) -> str:
    """Apply inline transformations in a safe order.

    Order matters: literals first (so their content is not mangled by other
    rules), then links, then inline !see / !include (which produce link
    markup that must not be re-processed), then bold (triple-quote) before
    italic (double-quote) so the triple-quote pattern is consumed first.
    """
    text = LITERAL_RE.sub(lambda m: f"`{m.group(1)}`", text)
    text = LINK_RE.sub(
        lambda m: f"[{m.group(1).strip()}]({convert_internal_link(m.group(2).strip())})",
        text,
    )
    def _inline_path_link(m: "re.Match[str]") -> str:
        raw = m.group(1)
        display, _ = normalize_internal_path(raw)
        return f"[{display}]({convert_internal_link(raw)})"

    text = INLINE_SEE_RE.sub(_inline_path_link, text)
    text = INLINE_INCLUDE_RE.sub(_inline_path_link, text)
    text = BOLD_RE.sub(r"**\1**", text)
    text = ITALIC_RE.sub(r"*\1*", text)
    return text


def strip_fitnesse_markup(text: str) -> str:
    """Reduce FitNesse markup to plain text — for YAML metadata fields."""
    text = LITERAL_RE.sub(r"\1", text)
    text = LINK_RE.sub(r"\1", text)
    text = BOLD_RE.sub(r"\1", text)
    text = ITALIC_RE.sub(r"\1", text)
    return text


def is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and len(s) > 1


def parse_table_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def is_bold_header(cells: list[str]) -> bool:
    """A row is a header when every non-empty cell is wrapped in '''...'''."""
    non_empty = [c for c in cells if c]
    return bool(non_empty) and all(
        c.startswith("'''") and c.endswith("'''") for c in non_empty
    )


def is_label_value_table_raw(raw_rows: list[list[str]]) -> bool:
    """Two-column table whose left column is consistently bold (FitNesse
    `'''...'''`) — render as a Property/Value definition table."""
    if not raw_rows or len(raw_rows[0]) != 2:
        return False
    return all(
        len(r) == 2 and r[0].startswith("'''") and r[0].endswith("'''")
        for r in raw_rows
        if r[0]
    )


def render_table(rows: list[list[str]], out: list[str]) -> None:
    """rows are raw FitNesse cells (still `'''bold'''`). Detect the header
    style on the raw cells, then convert each cell for output."""
    if not rows:
        return
    col_count = max(len(r) for r in rows)
    rows = [r + [""] * (col_count - len(r)) for r in rows]

    if is_bold_header(rows[0]):
        converted = [[convert_inline(c) for c in r] for r in rows]
        out.append("| " + " | ".join(converted[0]) + " |")
        out.append("|" + "|".join(["---"] * col_count) + "|")
        for row in converted[1:]:
            out.append("| " + " | ".join(row) + " |")
        return

    if is_label_value_table_raw(rows):
        converted = [[convert_inline(c) for c in r] for r in rows]
        out.append("| Property | Value |")
        out.append("|---|---|")
        for row in converted:
            out.append("| " + " | ".join(row) + " |")
        return

    converted = [[convert_inline(c) for c in r] for r in rows]
    out.append("|" + "|".join([" "] * col_count) + "|")
    out.append("|" + "|".join(["---"] * col_count) + "|")
    for row in converted:
        out.append("| " + " | ".join(row) + " |")


def consume_table(lines: list[str], start: int, out: list[str]) -> int:
    rows: list[list[str]] = []
    i = start
    while i < len(lines) and is_table_row(lines[i]):
        rows.append(parse_table_row(lines[i]))
        i += 1
    render_table(rows, out)
    return i


def consume_code_block(lines: list[str], start: int, out: list[str]) -> int:
    """Consume a {{{ ... }}} block. Open and close braces may sit on the
    same line as content; we normalise to a fenced block."""
    first = lines[start]
    open_idx = first.index("{{{")
    after_open = first[open_idx + 3:]
    out.append("```")
    if "}}}" in after_open:
        close_idx = after_open.index("}}}")
        if after_open[:close_idx]:
            out.append(after_open[:close_idx])
        out.append("```")
        return start + 1
    if after_open.strip():
        out.append(after_open)
    i = start + 1
    while i < len(lines):
        line = lines[i]
        if "}}}" in line:
            close_idx = line.index("}}}")
            if line[:close_idx]:
                out.append(line[:close_idx])
            out.append("```")
            return i + 1
        out.append(line)
        i += 1
    out.append("```")
    return i


def consume_plantuml(lines: list[str], start: int, out: list[str]) -> int:
    out.append("```plantuml")
    i = start + 1
    while i < len(lines) and lines[i].strip() != "!enduml":
        out.append(lines[i])
        i += 1
    out.append("```")
    return i + 1 if i < len(lines) else i


def convert_line(line: str) -> str | None:
    """Convert a single line. Returning None means the line should be dropped
    entirely (e.g. !contents, !define, !anchor)."""
    stripped = line.strip()

    if stripped == "----":
        return "---"
    if stripped.startswith("!contents"):
        return None
    if stripped.startswith("!define"):
        return None
    if stripped.startswith("!anchor"):
        return None
    if not stripped and not line:
        return ""

    m = HEADER_RE.match(line)
    if m:
        level = int(m.group(1))
        return "#" * level + " " + convert_inline(m.group(2))

    m = SEE_RE.match(line)
    if m:
        raw = m.group(1).strip().rstrip(".")
        display, _ = normalize_internal_path(raw)
        return f"> See: [{display}]({convert_internal_link(raw)})"

    m = INCLUDE_RE.match(line)
    if m:
        raw = m.group(1).strip()
        display, _ = normalize_internal_path(raw)
        return f"> Includes: [{display}]({convert_internal_link(raw)})"

    m = NOTE_RE.match(line)
    if m:
        return "> **Note:** " + convert_inline(m.group(1))

    m = CENTER_RE.match(line)
    if m:
        return convert_inline(m.group(1))

    m = PLAIN_RE.match(line)
    if m:
        return m.group(1)

    m = LIST_BULLET_RE.match(line)
    if m:
        indent_chars = len(m.group(1)) - 1
        return " " * indent_chars + "- " + convert_inline(m.group(2))

    m = LIST_NUM_RE.match(line)
    if m:
        indent_chars = len(m.group(1)) - 1
        return " " * indent_chars + m.group(2) + ". " + convert_inline(m.group(3))

    return convert_inline(line)


def convert(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.strip() == "!startuml":
            i = consume_plantuml(lines, i, out)
            continue

        if "{{{" in line:
            # Only treat as code block when the line is essentially the opener
            # (no leading body content other than indentation). This avoids
            # eating stray braces that happen to appear inside prose.
            before_brace = line[: line.index("{{{")]
            if not before_brace.strip():
                i = consume_code_block(lines, i, out)
                continue

        if is_table_row(line):
            i = consume_table(lines, i, out)
            continue

        converted = convert_line(line)
        if converted is not None:
            out.append(converted)
        i += 1

    # Collapse runs of 3+ blank lines to a single blank line for cleaner output.
    cleaned: list[str] = []
    blank_run = 0
    for line in out:
        if line.strip() == "":
            blank_run += 1
            if blank_run <= 1:
                cleaned.append(line)
        else:
            blank_run = 0
            cleaned.append(line)
    return "\n".join(cleaned)


def extract_title(text: str) -> str | None:
    """Pull the first !1 header for frontmatter."""
    for line in text.split("\n"):
        m = HEADER_RE.match(line)
        if m and m.group(1) == "1":
            return m.group(2).strip()
    return None


def build_frontmatter(rel_path: Path, title: str | None) -> str:
    source = rel_path.with_suffix("").as_posix().replace("/", ".")
    parts = ["---"]
    if title:
        plain = strip_fitnesse_markup(title)
        safe = plain.replace('"', '\\"')
        parts.append(f'title: "{safe}"')
    parts.append(f"source_path: {source}")
    parts.append("---")
    return "\n".join(parts) + "\n\n"


def convert_file(source: Path, target: Path, rel_path: Path,
                 output_root: Path) -> None:
    global _CURRENT_REL_DIR
    _CURRENT_REL_DIR = target.parent.relative_to(output_root)
    try:
        text = source.read_text(encoding="utf-8")
        title = extract_title(text)
        body = convert(text).lstrip("\n")
        frontmatter = build_frontmatter(rel_path, title)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(frontmatter + body + "\n", encoding="utf-8")
    finally:
        _CURRENT_REL_DIR = None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert FitNesse wiki .txt files to Markdown."
    )
    parser.add_argument(
        "--source-dir",
        default=str(FITNESSE_PAGES_DIR),
        help=f"Directory of FitNesse .txt files (default: {FITNESSE_PAGES_DIR}).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(FITNESSE_MARKDOWN_DIR),
        help=f"Directory for the .md output tree (default: {FITNESSE_MARKDOWN_DIR}).",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="REL_PATH",
        help=("Convert only files whose path (relative to --source-dir) matches "
              "exactly. Repeatable."),
    )
    parser.add_argument(
        "--strip-prefix",
        default="",
        help=("Dotted FitNesse prefix to strip from internal page paths "
              "before turning them into relative .md links. Use this when "
              "the markdown tree starts deeper than the absolute paths "
              "encoded in the wiki source. Example: "
              "EmployeeEngagement.CPlanGICTrackingCLARITYDashboard.MultiChannelDataModel"),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the files that would be converted without writing anything.",
    )
    args = parser.parse_args()

    global _STRIP_PREFIX
    _STRIP_PREFIX = args.strip_prefix.strip(".")

    source_dir = Path(args.source_dir)
    output_dir = Path(args.output_dir)

    if not source_dir.exists():
        print(f"ERROR: source directory does not exist: {source_dir}", file=sys.stderr)
        return 2

    files = sorted(source_dir.rglob("*.txt"))
    if args.only:
        wanted = {Path(o).as_posix() for o in args.only}
        files = [f for f in files if f.relative_to(source_dir).as_posix() in wanted]
        if not files:
            print(f"ERROR: --only matched no files. Available paths under {source_dir}:",
                  file=sys.stderr)
            for f in sorted(source_dir.rglob("*.txt")):
                print(f"  {f.relative_to(source_dir).as_posix()}", file=sys.stderr)
            return 2

    print(f"Source:  {source_dir.resolve()}")
    print(f"Output:  {output_dir.resolve()}")
    print(f"Files:   {len(files)}")
    print(f"Mode:    {'DRY RUN' if args.dry_run else 'CONVERT'}")
    print()

    for src in files:
        rel = src.relative_to(source_dir)
        # Pure-tree layout: <folder>/_page.md per page. The source filename
        # (sans .txt) becomes a directory; the content goes to _page.md
        # inside it. This keeps a single shape at every depth.
        target = output_dir / rel.with_suffix("") / "_page.md"
        if args.dry_run:
            print(f"  {rel.as_posix()} -> {target.relative_to(output_dir.parent).as_posix()}")
            continue
        convert_file(src, target, rel, output_dir)
        print(f"  {rel.as_posix()} -> {target.relative_to(output_dir.parent).as_posix()}")

    print()
    print(f"Done: {len(files)} file{'s' if len(files) != 1 else ''} "
          f"{'planned' if args.dry_run else 'converted'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
