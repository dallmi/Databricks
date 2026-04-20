#!/usr/bin/env python3
"""Remove explicit PII mentions from docs.

Stripping targets:
  - Frontmatter-Row "| **PII** | ... |" (MD) and "| '''PII''' | ... |" (FitNesse).
  - Inline annotations "| Receiver | string | PII | ... |" → drop the PII role, leave column.
  - Quality-caveat bullets that lead with "PII" or "PII-…".
  - Mermaid-Diagramm: `string Receiver "PII"` → remove the PII quote.
  - Bracket-tags like "(PII)" / "inkl. PII" / "incl. PII" in prose.
  - References to `memory/pii_cleanup_pending.md` / "Personal Data Handling / PII" rows in tables.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # MD frontmatter: "| **PII** | ... |" — delete whole line
    (re.compile(r"^\| \*\*PII\*\* \|[^\n]*\n", re.MULTILINE), r""),
    # FitNesse frontmatter: "| '''PII''' | ... |"
    (re.compile(r"^\|\s*'''PII'''[^\n]*\n", re.MULTILINE), r""),
    # Key-columns table row with PII role: "| Receiver | string | PII | ... |" — drop the row
    (re.compile(r"^\|\s*`?Receiver`?\s*\|[^|]*\|\s*PII\s*\|[^\n]*\n", re.MULTILINE), r""),
    # FitNesse column-table row: "| Receiver        | string      | PII                    | ... |"
    (re.compile(r"^\|\s+Receiver\s+\|[^|]+\|\s*PII\s*\|[^\n]*\n", re.MULTILINE), r""),
    # Quality-caveat bullets leading with "PII" — drop the bullet line
    (re.compile(r"^-\s+\*\*PII(-\w+)?\*\*[^\n]*\n", re.MULTILINE), r""),
    (re.compile(r"^\s*\*\s+'''PII(\s+\w+)?'''[^\n]*\n", re.MULTILINE), r""),
    # knowledge_base.md: "- **Personal Data Handling / PII** — ..." line
    (re.compile(r"^-\s+\*\*Personal Data Handling[^\n]*\n", re.MULTILINE), r""),
    # Mermaid annotation: string Receiver "PII" → string Receiver
    (re.compile(r'(\s*string\s+\w+)\s+"PII"'), r"\1"),
    # Prose mentions "(~30-40 Spalten inkl. PII wie Name, Geburtsdatum etc. — …)" → simplify
    (re.compile(r"\s+\(~?(\d+-?\d*)\s+Spalten inkl\. PII wie[^)]+\)"), r" (~\1 Spalten)"),
    (re.compile(r"\s+\(~?(\d+-?\d*)\s+columns incl\. PII[^)]+\)"), r" (~\1 columns)"),
    # Remove "inkl. PII wie Name, Geburtsdatum etc." fragments
    (re.compile(r",?\s*inkl\.\s*PII[^.,;)]*"), r""),
    (re.compile(r",?\s*incl\.\s*PII[^.,;)]*"), r""),
    # "memory/pii_cleanup_pending.md" reference parentheses
    (re.compile(r"\s*\(siehe\s+`?memory/pii_cleanup_pending\.md`?\)"), r""),
    (re.compile(r"\s*\(see\s+`?memory/pii_cleanup_pending\.md`?\)"), r""),
]


def strip_file(path: Path) -> int:
    text = path.read_text()
    original = text
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    # collapse any ">=3 consecutive blank lines" introduced by deletions
    text = re.sub(r"\n{3,}", "\n\n", text)
    if text != original:
        path.write_text(text)
        return 1
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: strip_pii.py <file-or-dir> [<file-or-dir> ...]", file=sys.stderr)
        return 1
    changed = 0
    for arg in argv[1:]:
        root = Path(arg)
        if root.is_file():
            targets = [root]
        else:
            targets = [p for p in root.rglob("*") if p.suffix in {".md", ".txt"}]
        for p in targets:
            if strip_file(p):
                changed += 1
                print(f"  edited: {p}")
    print(f"\n{changed} files edited.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
