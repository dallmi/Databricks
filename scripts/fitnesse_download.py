#!/usr/bin/env python3
"""
Downloads FitNesse pages to a local timestamped backup directory.

Companion to fitnesse_upload.py: walks the same PLAN in reverse, fetches the
raw wiki source for each page via the FitNesse edit responder, and writes
the result into docs/fitnesse/backup/<YYYYMMDD-HHMM>/. The layout mirrors
docs/fitnesse/pages/ so a simple diff lines up source against live.

Usage:
    export FITNESSE_URL=http://<fitnesse-host>:<port>
    export FITNESSE_PARENT_PATH=FrontPage.<Your.Parent.Path>

    # Dry run — prints the plan, no HTTP
    python3 scripts/fitnesse_download.py --dry-run

    # Full backup of the 31 pages
    python3 scripts/fitnesse_download.py

    # Single page (or a few) — same --only syntax as fitnesse_upload.py
    python3 scripts/fitnesse_download.py --only DataGlossary.ImepGold.Final
    python3 scripts/fitnesse_download.py --only .

    # Skip container pages (code-generated stubs)
    python3 scripts/fitnesse_download.py --skip-containers

After a successful run a convenience symlink `docs/fitnesse/backup/latest`
points at the newest timestamped folder. Use it like:

    diff -r docs/fitnesse/pages/ docs/fitnesse/backup/latest/
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
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from fitnesse_upload import (  # noqa: E402  (import-after-sys-path)
    PLAN,
    build_fitnesse_path,
    DEFAULT_BASE_URL,
    DEFAULT_PARENT_PATH,
    DEFAULT_ROOT_NAME,
)

DEFAULT_BACKUP_ROOT = SCRIPT_DIR.parent / "docs" / "fitnesse" / "backup"

TEXTAREA_RE = re.compile(
    r'<textarea[^>]*name="pageContent"[^>]*>(.*?)</textarea>',
    re.DOTALL | re.IGNORECASE,
)


def backup_path(backup_dir: Path, sub: str, source: str | None) -> Path:
    """Pick the on-disk path for a given PLAN entry.

    Content pages mirror their source file path. Container pages land at
    <sub-path>/_container.txt so they sit next to the content they own.
    """
    if source:
        return backup_dir / source
    if not sub:
        return backup_dir / "_container.txt"
    parts = sub.split(".")
    return backup_dir / Path(*parts) / "_container.txt"


def download_page(base_url: str, page_path: str, timeout: int = 30) -> tuple[int, str]:
    """Fetch raw wiki source via the edit responder, return (status, body).

    Uses ?responder=edit which has been stable across FitNesse versions and
    returns HTML with the raw source in a textarea. We extract and HTML-unescape.
    """
    url = f"{base_url.rstrip('/')}/{page_path}?responder=edit"
    req = urllib.request.Request(url, headers={"User-Agent": "fitnesse-download.py"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return resp.status, ""
            body = resp.read().decode("utf-8", errors="replace")
            match = TEXTAREA_RE.search(body)
            if not match:
                # Page exists but textarea is missing — either empty new page
                # or an unfamiliar FitNesse response. Report 200 + empty.
                return 200, ""
            return 200, html.unescape(match.group(1))
    except urllib.error.HTTPError as e:
        return e.code, ""
    except urllib.error.URLError as e:
        print(f"    network error: {e}", file=sys.stderr)
        return 0, ""


def update_latest_symlink(backup_root: Path, timestamp: str) -> None:
    """Point <backup_root>/latest at the just-written timestamp dir.

    Silently skips on platforms that refuse symlinks (e.g. Windows without
    developer mode); a fresh timestamped dir still exists.
    """
    latest = backup_root / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(timestamp, target_is_directory=True)
    except (OSError, NotImplementedError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Download FitNesse pages to a local backup directory.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help="FitNesse base URL, e.g. http://host:port. Defaults to $FITNESSE_URL.")
    parser.add_argument("--parent-path", default=DEFAULT_PARENT_PATH,
                        help="Dotted FitNesse path of the existing parent page. Defaults to $FITNESSE_PARENT_PATH.")
    parser.add_argument("--root-name", default=DEFAULT_ROOT_NAME,
                        help="Name of the root page under parent-path.")
    parser.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT),
                        help=f"Root folder for timestamped backups (default: {DEFAULT_BACKUP_ROOT}).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan without sending any GETs.")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="Seconds to sleep between requests (default: 0.3).")
    parser.add_argument("--stop-on-error", action="store_true",
                        help="Abort on the first HTTP error (default: continue).")
    parser.add_argument("--only", action="append", default=[], metavar="SUB_PATH",
                        help=("Download only entries whose sub-path matches exactly. "
                              "Repeatable. Use '.' for the root page (Overview). "
                              "Example: --only DataGlossary.ImepGold.Final"))
    parser.add_argument("--list", action="store_true",
                        help="Print all available sub-paths and exit (useful with --only).")
    parser.add_argument("--skip-containers", action="store_false", dest="include_containers",
                        default=True,
                        help="Skip container pages (code-generated stubs).")
    args = parser.parse_args()

    if args.list:
        print("Available sub-paths (use with --only; '.' = root/Overview):")
        for sub, source in PLAN:
            label = sub if sub else "."
            kind = "content" if source else "container"
            print(f"  {label:<58} ({kind})")
        return 0

    plan = list(PLAN)
    if not args.include_containers:
        plan = [(s, src) for s, src in plan if src is not None]
    if args.only:
        wanted = {"" if o == "." else o for o in args.only}
        known = {sub for sub, _ in PLAN}
        unknown = wanted - known
        if unknown:
            print(f"ERROR: unknown sub-path(s): {sorted(unknown)}", file=sys.stderr)
            print("       run with --list to see all available sub-paths.", file=sys.stderr)
            return 2
        plan = [(s, src) for s, src in plan if s in wanted]

    if not args.base_url:
        print("ERROR: --base-url not provided (and FITNESSE_URL env var is empty).", file=sys.stderr)
        return 2
    if not args.parent_path:
        print("ERROR: --parent-path not provided (and FITNESSE_PARENT_PATH env var is empty).", file=sys.stderr)
        return 2

    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    backup_root = Path(args.backup_root)
    backup_dir = backup_root / timestamp

    total = len(plan)
    print(f"Plan: {total} page{'s' if total != 1 else ''} from {args.parent_path}.{args.root_name}")
    print(f"Base URL: {args.base_url}")
    print(f"Backup:   {backup_dir.resolve()}")
    print(f"Mode:     {'DRY RUN' if args.dry_run else 'LIVE DOWNLOAD'}")
    if args.only:
        print(f"Filter:   --only {', '.join(args.only)}")
    print()

    if not args.dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)

    ok, missing, failed = 0, 0, 0
    for idx, (sub, source) in enumerate(plan, start=1):
        page_path = build_fitnesse_path(args.parent_path, args.root_name, sub)
        target = backup_path(backup_dir, sub, source)
        label = f"[{idx:2}/{total}] {'DRY  ' if args.dry_run else 'GET  '} {page_path}"

        if args.dry_run:
            rel = target.relative_to(backup_root)
            print(f"{label}  -> {rel}")
            ok += 1
            continue

        status, body = download_page(args.base_url, page_path)
        if status == 404:
            print(f"{label}  -> HTTP 404  (not on server, skipped)")
            missing += 1
            if args.stop_on_error:
                return 3
            time.sleep(args.delay)
            continue
        if not (200 <= status < 300) or not body:
            reason = "empty response" if status == 200 else f"HTTP {status}"
            print(f"{label}  -> FAIL  ({reason})")
            failed += 1
            if args.stop_on_error:
                return 4
            time.sleep(args.delay)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        rel = target.relative_to(backup_root)
        print(f"{label}  -> HTTP {status}  OK ({len(body)} chars -> {rel})")
        ok += 1
        time.sleep(args.delay)

    if not args.dry_run and ok > 0:
        update_latest_symlink(backup_root, timestamp)

    print()
    print(f"Done: {ok} ok, {missing} missing, {failed} failed (of {total}).")
    if not args.dry_run and ok > 0:
        print(f"Backup: {backup_dir}")
        latest = backup_root / "latest"
        if latest.is_symlink():
            print(f"Latest: {latest} -> {timestamp}")
        print()
        print("Diff against local source:")
        print(f"  diff -r docs/fitnesse/pages/ {backup_dir}/")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
