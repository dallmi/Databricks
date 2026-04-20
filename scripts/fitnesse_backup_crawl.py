#!/usr/bin/env python3
"""
Back up a FitNesse subtree by crawling pages and fetching raw wiki source.

Does NOT rely on the ?responder=zip endpoint (which is disabled on some
FitNesse instances, e.g. v20250219). Instead:

  1. Starts from the parent page.
  2. For each page: fetch ?responder=edit, extract raw wiki source from the
     textarea, save to disk under docs/fitnesse/backup/<YYYYMMDD-HHMM>/.
  3. Fetch the page HTML, find all direct-child links, enqueue them.
  4. Breadth-first until the subtree is exhausted.

Result: a mirrored directory tree of raw wiki text files — diffable,
versionable, and directly viewable without ZIP extraction.

Reuses DEFAULT_BASE_URL and DEFAULT_PARENT_PATH from fitnesse_upload.py so
there is no duplicated config. Pure stdlib, works on Windows (Anaconda
Prompt), macOS and Linux. Runs from any working directory.

Usage:
    # Uses defaults from fitnesse_upload.py
    python scripts/fitnesse_backup_crawl.py

    # Dry-run — no HTTP, show the starting URL and target folder
    python scripts/fitnesse_backup_crawl.py --dry-run

    # Custom subtree
    python scripts/fitnesse_backup_crawl.py --parent-path FrontPage.Some.Other.Page

    # Cap crawl depth (defensive, default unlimited)
    python scripts/fitnesse_backup_crawl.py --max-depth 3
"""

from __future__ import annotations

import argparse
import datetime
import html
import re
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from fitnesse_upload import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_PARENT_PATH,
    DEFAULT_PAGES_DIR,
)

# Backup lives as a sibling of the pages directory configured in fitnesse_upload.py.
# Keeps the docs layout in one place (fitnesse_upload.DEFAULT_PAGES_DIR).
DEFAULT_BACKUP_ROOT = Path(DEFAULT_PAGES_DIR).parent / "backup"

TEXTAREA_RE = re.compile(
    r'<textarea[^>]*name="pageContent"[^>]*>(.*?)</textarea>',
    re.DOTALL | re.IGNORECASE,
)

# Matches <a ... href="...">...</a>; href captured in group 1.
HREF_RE = re.compile(r'<a[^>]*\shref="([^"]+)"', re.IGNORECASE)

# A valid FitNesse page-path segment: WikiWord or other alnum identifier.
# Segments are dot-separated. We allow letters, digits, underscore.
SEGMENT_RE = re.compile(r"^[A-Za-z0-9_]+$")


def fetch(url: str, timeout: int = 30) -> tuple[int, str]:
    """HTTP GET, returning (status, body). Status 0 means network error."""
    req = urllib.request.Request(url, headers={"User-Agent": "fitnesse-backup-crawl.py"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, ""
    except urllib.error.URLError as e:
        print(f"    network error on {url}: {e}", file=sys.stderr)
        return 0, ""


def fetch_raw_source(base_url: str, page_path: str, timeout: int = 30) -> tuple[int, str]:
    """Get raw wiki source via ?responder=edit. Returns (status, content)."""
    url = f"{base_url.rstrip('/')}/{page_path}?responder=edit"
    status, body = fetch(url, timeout)
    if status != 200 or not body:
        return status, ""
    match = TEXTAREA_RE.search(body)
    if not match:
        # Page exists (HTTP 200) but textarea missing — empty page or unusual response.
        return 200, ""
    return 200, html.unescape(match.group(1))


def discover_children(base_url: str, page_path: str, timeout: int = 30) -> list[str]:
    """Fetch page HTML and return direct-child page paths found in anchor hrefs.

    FitNesse emits child links as <a href="ParentPath.ChildName">. We keep
    only hrefs that are one level deeper than page_path.
    """
    url = f"{base_url.rstrip('/')}/{page_path}"
    status, body = fetch(url, timeout)
    if status != 200 or not body:
        return []
    prefix = page_path + "."
    children: set[str] = set()
    for match in HREF_RE.finditer(body):
        href = match.group(1)
        # Strip query string and fragment
        href = href.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        if not href.startswith(prefix):
            continue
        remainder = href[len(prefix):]
        # Direct child only — no further dots, and a clean segment name
        if "." in remainder or "/" in remainder:
            continue
        if not SEGMENT_RE.match(remainder):
            continue
        children.add(href)
    return sorted(children)


def target_path(backup_dir: Path, start_path: str, page_path: str) -> Path:
    """Map a FitNesse page path to an on-disk .txt path under backup_dir.

    The root page (page_path == start_path) goes to _root.txt. Descendants
    mirror the dotted path as nested folders, with the leaf as <name>.txt.
    """
    if page_path == start_path:
        return backup_dir / "_root.txt"
    prefix = start_path + "."
    if not page_path.startswith(prefix):
        # Shouldn't happen given how we enqueue, but be defensive.
        safe = page_path.replace(".", "/")
        return backup_dir / f"{safe}.txt"
    rel = page_path[len(prefix):]
    parts = rel.split(".")
    return backup_dir / Path(*parts[:-1]) / f"{parts[-1]}.txt"


def crawl(base_url: str, start_path: str, backup_dir: Path, *,
          max_depth: int | None, delay: float) -> dict[str, int]:
    """BFS crawl from start_path. Returns counter dict (ok/missing/failed/pages)."""
    counts = {"ok": 0, "missing": 0, "failed": 0, "pages_seen": 0}
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(start_path, 0)])

    while queue:
        page_path, depth = queue.popleft()
        if page_path in visited:
            continue
        visited.add(page_path)
        counts["pages_seen"] += 1

        label = f"[depth {depth}] {page_path}"

        # 1. Download raw source
        status, body = fetch_raw_source(base_url, page_path)
        if status == 0:
            print(f"  {label} -> network error, skipped")
            counts["failed"] += 1
            time.sleep(delay)
            continue
        if status == 404:
            print(f"  {label} -> HTTP 404 (skipped)")
            counts["missing"] += 1
            time.sleep(delay)
            continue
        if status != 200:
            print(f"  {label} -> HTTP {status} (skipped)")
            counts["failed"] += 1
            time.sleep(delay)
            continue
        if not body:
            print(f"  {label} -> empty content (skipped)")
            counts["missing"] += 1
        else:
            target = target_path(backup_dir, start_path, page_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
            rel = target.relative_to(backup_dir.parent)
            print(f"  {label} -> {rel} ({len(body)} chars)")
            counts["ok"] += 1

        # 2. Discover children and enqueue
        if max_depth is None or depth < max_depth:
            for child in discover_children(base_url, page_path):
                if child not in visited:
                    queue.append((child, depth + 1))
        time.sleep(delay)

    return counts


def update_latest_symlink(backup_root: Path, timestamp: str) -> None:
    latest = backup_root / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(timestamp, target_is_directory=True)
    except (OSError, NotImplementedError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Back up a FitNesse subtree by crawling child links and fetching "
                    "raw wiki source per page.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help="FitNesse base URL. Default: value from fitnesse_upload.py "
                             "(env var FITNESSE_URL).")
    parser.add_argument("--parent-path", default=DEFAULT_PARENT_PATH,
                        help="Dotted path of the subtree root. Default: value from "
                             "fitnesse_upload.py (env var FITNESSE_PARENT_PATH).")
    parser.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT),
                        help=f"Folder where the timestamped subfolder is written "
                             f"(default: {DEFAULT_BACKUP_ROOT}).")
    parser.add_argument("--max-depth", type=int, default=None,
                        help="Limit crawl depth (root = 0). Default: unlimited.")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="Seconds to sleep between page fetches (default: 0.3).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print configuration and exit without HTTP.")
    args = parser.parse_args()

    if not args.base_url:
        print("ERROR: --base-url not provided and fitnesse_upload.DEFAULT_BASE_URL is empty "
              "(set $FITNESSE_URL).", file=sys.stderr)
        return 2
    if not args.parent_path:
        print("ERROR: --parent-path not provided and fitnesse_upload.DEFAULT_PARENT_PATH is empty "
              "(set $FITNESSE_PARENT_PATH).", file=sys.stderr)
        return 2

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    backup_root = Path(args.backup_root)
    backup_dir = backup_root / f"{ts}-crawl"

    print(f"Base URL:    {args.base_url}")
    print(f"Start page:  {args.parent_path}")
    print(f"Backup:      {backup_dir.resolve()}")
    print(f"Max depth:   {args.max_depth if args.max_depth is not None else 'unlimited'}")
    print(f"Mode:        {'DRY RUN' if args.dry_run else 'LIVE CRAWL'}")
    print()

    if args.dry_run:
        print("(dry run — exit without HTTP)")
        return 0

    backup_dir.mkdir(parents=True, exist_ok=True)

    counts = crawl(
        args.base_url, args.parent_path, backup_dir,
        max_depth=args.max_depth, delay=args.delay,
    )

    if counts["ok"] > 0:
        update_latest_symlink(backup_root, f"{ts}-crawl")

    print()
    print(f"Done: {counts['ok']} saved, {counts['missing']} missing, "
          f"{counts['failed']} failed (of {counts['pages_seen']} pages seen).")
    if counts["ok"] > 0:
        print(f"Backup: {backup_dir}")
        latest = backup_root / "latest"
        if latest.is_symlink():
            print(f"Latest: {latest}")
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
