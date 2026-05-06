#!/usr/bin/env python3
"""
Back up a FitNesse subtree by crawling pages and fetching raw wiki source.

Does NOT rely on the ?responder=zip endpoint (which is disabled on some
FitNesse instances, e.g. v20250219). Instead:

  1. Starts from the parent page.
  2. For each page: fetch ?responder=edit, extract raw wiki source from the
     textarea, save the .txt under <backup>/txt/<rel>/_page.txt and the
     converted markdown under <backup>/md/<rel>/_page.md.
  3. Fetch the page HTML, find all direct-child links, enqueue them.
  4. Breadth-first until the subtree is exhausted.

Result: two parallel mirrored trees — txt/ for diffable raw wiki backup,
md/ for Copilot Studio (or any RAG) grounding. _INDEX.md at the backup
root links to the markdown side. Use --no-markdown to skip the .md
export when only the raw backup is wanted.

Reads FITNESSE_URL and FITNESSE_PARENT_PATH from _config.py (which loads
.env on import), so there is no duplicated config. Pure stdlib + the
local fitnesse_to_markdown module; works on Windows (Anaconda Prompt),
macOS and Linux. Runs from any working directory.

Usage:
    # Default target — backs up FITNESSE_PARENT_PATH to backup/main/.
    # Writes both txt/ and md/.
    python scripts/fitnesse_backup_crawl.py

    # Alternative target — backs up FITNESSE_ALT_PARENT_PATH to backup/alt/.
    python scripts/fitnesse_backup_crawl.py --target alt

    # One-off override — backs up an arbitrary subtree to backup/custom/.
    python scripts/fitnesse_backup_crawl.py --parent-path FrontPage.Some.Other.Page

    # Dry-run — no HTTP, show the resolved target and folder
    python scripts/fitnesse_backup_crawl.py --dry-run
    python scripts/fitnesse_backup_crawl.py --dry-run --target alt

    # Skip the .md conversion
    python scripts/fitnesse_backup_crawl.py --no-markdown

    # Resume after a crash / timeout. Scoped to the chosen target's
    # subfolder (backup/<target>/), so --resume picks the latest
    # <ts>-crawl folder for that target. Pass an explicit folder name
    # to resume one other than the most recent.
    python scripts/fitnesse_backup_crawl.py --resume
    python scripts/fitnesse_backup_crawl.py --target alt --resume
    python scripts/fitnesse_backup_crawl.py --resume 20260505-1430-crawl

    # Cap crawl depth (defensive, default unlimited)
    python scripts/fitnesse_backup_crawl.py --max-depth 3
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from _config import (  # noqa: E402
    FITNESSE_URL,
    FITNESSE_PARENT_PATH,
    FITNESSE_ALT_PARENT_PATH,
    FITNESSE_BACKUP_ROOT,
    FITNESSE_REQUEST_DELAY,
)
from fitnesse_to_markdown import render_markdown  # noqa: E402

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
    """HTTP GET, returning (status, body). Status 0 means network error.

    OSError catches everything socket-level: URLError (connect failures),
    TimeoutError raised mid-read by socket.recv_into, ConnectionResetError,
    etc. HTTPError is checked first because it's a URLError subclass and
    we want the response code, not a "network error" log line.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "fitnesse-backup-crawl.py"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, ""
    except OSError as e:
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


def page_rel_dir(start_path: str, page_path: str) -> Path:
    """Folder (relative to a format root) where this page lives.

    Pure-tree layout: every page becomes a folder. Returns Path('.') for
    the start page itself and a nested Path for descendants.
    """
    if page_path == start_path:
        return Path(".")
    prefix = start_path + "."
    if not page_path.startswith(prefix):
        return Path(*page_path.split("."))
    rel = page_path[len(prefix):]
    return Path(*rel.split("."))


def target_path(format_dir: Path, start_path: str, page_path: str,
                suffix: str) -> Path:
    """On-disk path for a page: format_dir/<rel>/<leaf><suffix>.

    Every page becomes its own folder (so children nest cleanly underneath)
    and the file inside is named after the page's leaf segment, so editor
    tabs and grep results show a meaningful name instead of `_page`."""
    leaf = page_path.rsplit(".", 1)[-1]
    return format_dir / page_rel_dir(start_path, page_path) / f"{leaf}{suffix}"


def derive_strip_prefix(start_path: str) -> str:
    """For internal-link rewriting in the markdown output: drop the leading
    FrontPage. (if present), since FitNesse internal hrefs are absolute
    from the wiki root but our markdown tree starts at start_path."""
    if start_path.startswith("FrontPage."):
        return start_path[len("FrontPage."):]
    if start_path == "FrontPage":
        return ""
    return start_path


STATE_FILENAME = "_state.json"

# Set once when state-write fails after all retries, so the crawl doesn't
# spam stderr if every page hits the same OneDrive/AV contention.
_state_save_warned = False


def state_file(backup_dir: Path) -> Path:
    return backup_dir / STATE_FILENAME


def save_state(backup_dir: Path, *, visited: set[str],
               queue: "deque[tuple[str, int]]", counts: dict[str, int],
               entries: dict[str, dict], base_url: str, start_path: str,
               max_depth: int | None, write_markdown: bool) -> None:
    """Persist crawl progress so a crashed/timed-out run can be resumed.

    Atomic: writes to a per-pid .tmp then renames. The unique tmp name
    avoids colliding with a leftover .tmp from a previous crashed run.
    Retries on PermissionError/OSError (common on Windows when OneDrive
    or antivirus briefly holds the new file). If all retries fail the
    crawl continues — saved pages are still on disk, only fast-resume
    via state file is lost; --resume will fall back to the disk path."""
    global _state_save_warned

    def _rel(p: Path | None) -> str | None:
        return p.relative_to(backup_dir).as_posix() if p else None

    payload = {
        "version": 1,
        "base_url": base_url,
        "start_path": start_path,
        "max_depth": max_depth,
        "write_markdown": write_markdown,
        "visited": sorted(visited),
        "queue": [[p, d] for p, d in queue],
        "counts": counts,
        "entries": {
            page: {
                "status": e["status"],
                "depth": e["depth"],
                "target": _rel(e.get("target")),
                "md_target": _rel(e.get("md_target")),
            }
            for page, e in entries.items()
        },
    }
    sp = state_file(backup_dir)
    tmp = backup_dir / f"{STATE_FILENAME}.{os.getpid()}.tmp"
    body = json.dumps(payload, indent=2)

    backoffs = (0.2, 0.5, 1.0, 2.0)
    last_err: Exception | None = None
    for delay_s in backoffs:
        try:
            tmp.write_text(body, encoding="utf-8")
            os.replace(tmp, sp)
            return
        except (PermissionError, OSError) as e:
            last_err = e
            time.sleep(delay_s)

    # Best-effort cleanup; ignore if even unlink is blocked.
    try:
        if tmp.exists():
            tmp.unlink()
    except OSError:
        pass

    if not _state_save_warned:
        print(f"  WARNING: state checkpoint blocked ({last_err}); "
              f"crawl will keep running, but resume must use the disk "
              f"fallback. Often caused by OneDrive/antivirus briefly "
              f"locking newly written files.", file=sys.stderr)
        _state_save_warned = True


def load_state(backup_dir: Path) -> dict | None:
    """Inverse of save_state. Returns None when no state file is present."""
    sp = state_file(backup_dir)
    if not sp.exists():
        return None
    payload = json.loads(sp.read_text(encoding="utf-8"))
    visited: set[str] = set(payload["visited"])
    queue: "deque[tuple[str, int]]" = deque((p, d) for p, d in payload["queue"])
    counts = dict(payload["counts"])
    entries: dict[str, dict] = {}
    for page, e in payload["entries"].items():
        entries[page] = {
            "status": e["status"],
            "depth": e["depth"],
            "target": (backup_dir / e["target"]) if e.get("target") else None,
            "md_target": (backup_dir / e["md_target"]) if e.get("md_target") else None,
        }
    return {
        "visited": visited,
        "queue": queue,
        "counts": counts,
        "entries": entries,
        "base_url": payload["base_url"],
        "start_path": payload["start_path"],
        "max_depth": payload.get("max_depth"),
        "write_markdown": payload.get("write_markdown", True),
    }


def discard_state(backup_dir: Path) -> None:
    sp = state_file(backup_dir)
    if sp.exists():
        sp.unlink()


def crawl(base_url: str, start_path: str, backup_dir: Path, *,
          max_depth: int | None, delay: float,
          write_markdown: bool,
          visited: set[str] | None = None,
          queue: "deque[tuple[str, int]] | None" = None,
          counts: dict[str, int] | None = None,
          entries: dict[str, dict] | None = None,
          ) -> tuple[dict[str, int], dict[str, dict]]:
    """BFS crawl from start_path. For each saved page, writes the raw wiki
    source under <backup_dir>/txt/<rel>/<leaf>.txt and (unless disabled)
    the converted markdown under <backup_dir>/md/<rel>/<leaf>.md.

    Resume support: pass visited/queue/counts/entries from load_state() to
    pick up where a prior run left off. Also skips fetching any page whose
    .txt is already on disk (handles the case where state was lost)."""
    counts = counts if counts is not None else {
        "ok": 0, "missing": 0, "failed": 0, "pages_seen": 0,
    }
    entries = entries if entries is not None else {}
    visited = visited if visited is not None else set()
    queue = queue if queue is not None else deque([(start_path, 0)])

    txt_dir = backup_dir / "txt"
    md_dir = backup_dir / "md"
    strip_prefix = derive_strip_prefix(start_path) if write_markdown else ""

    def _persist() -> None:
        save_state(backup_dir, visited=visited, queue=queue, counts=counts,
                   entries=entries, base_url=base_url, start_path=start_path,
                   max_depth=max_depth, write_markdown=write_markdown)

    def _md_target_for(page_path: str) -> Path | None:
        return target_path(md_dir, start_path, page_path, ".md") if write_markdown else None

    while queue:
        page_path, depth = queue.popleft()
        if page_path in visited:
            continue
        visited.add(page_path)
        counts["pages_seen"] += 1

        label = f"[depth {depth}] {page_path}"
        txt_target = target_path(txt_dir, start_path, page_path, ".txt")
        already_recorded = page_path in entries

        # Skip fetch when the .txt is already on disk. Covers both fresh
        # disk-only resume (no state file) and re-traversal with state.
        if txt_target.exists():
            if not already_recorded:
                counts["ok"] += 1
                entries[page_path] = {
                    "status": "saved",
                    "target": txt_target,
                    "md_target": _md_target_for(page_path),
                    "depth": depth,
                }
            print(f"  {label} -> already saved, skipping fetch")
        else:
            status, body = fetch_raw_source(base_url, page_path)
            if status == 0:
                print(f"  {label} -> network error, skipped")
                counts["failed"] += 1
                entries[page_path] = {"status": "failed", "target": None,
                                      "md_target": None, "depth": depth}
                _persist()
                time.sleep(delay)
                continue
            if status == 404:
                print(f"  {label} -> HTTP 404 (skipped)")
                counts["missing"] += 1
                entries[page_path] = {"status": "missing", "target": None,
                                      "md_target": None, "depth": depth}
                _persist()
                time.sleep(delay)
                continue
            if status != 200:
                print(f"  {label} -> HTTP {status} (skipped)")
                counts["failed"] += 1
                entries[page_path] = {"status": "failed", "target": None,
                                      "md_target": None, "depth": depth}
                _persist()
                time.sleep(delay)
                continue
            if not body:
                print(f"  {label} -> empty content (skipped)")
                counts["missing"] += 1
                entries[page_path] = {"status": "missing", "target": None,
                                      "md_target": None, "depth": depth}
            else:
                txt_target.parent.mkdir(parents=True, exist_ok=True)
                txt_target.write_text(body, encoding="utf-8")
                md_target: Path | None = None
                if write_markdown:
                    md_target = _md_target_for(page_path)
                    md_rel_dir = page_rel_dir(start_path, page_path)
                    md_body = render_markdown(
                        body, page_path,
                        strip_prefix=strip_prefix,
                        current_rel_dir=md_rel_dir,
                    )
                    md_target.parent.mkdir(parents=True, exist_ok=True)
                    md_target.write_text(md_body, encoding="utf-8")
                rel = txt_target.relative_to(backup_dir.parent)
                print(f"  {label} -> {rel} ({len(body)} chars)")
                counts["ok"] += 1
                entries[page_path] = {"status": "saved", "target": txt_target,
                                      "md_target": md_target, "depth": depth}

        if max_depth is None or depth < max_depth:
            for child in discover_children(base_url, page_path):
                if child not in visited:
                    queue.append((child, depth + 1))
        _persist()
        time.sleep(delay)

    return counts, entries


def build_tree_dict(start_path: str, entries: dict[str, dict]) -> dict:
    """Build a nested dict mirroring the page hierarchy under start_path."""
    tree: dict = {}
    prefix = start_path + "."
    for page_path in entries:
        if page_path == start_path or not page_path.startswith(prefix):
            continue
        node = tree
        for part in page_path[len(prefix):].split("."):
            node = node.setdefault(part, {})
    return tree


def render_label(name: str, info: dict | None, backup_dir: Path) -> str:
    """Render a single tree/list entry as markdown. Saved pages link to the
    md/ side when available (the readable, Copilot-grounding artifact),
    falling back to the txt/ side when markdown wasn't requested."""
    if info and info["status"] == "saved":
        link_target = info.get("md_target") or info.get("target")
        if link_target:
            rel = link_target.relative_to(backup_dir).as_posix()
            return f"[{name}]({rel})"
    if info and info["status"] == "missing":
        return f"{name} *(missing)*"
    if info and info["status"] == "failed":
        return f"{name} *(failed)*"
    return name


def render_tree_md(node: dict, current_path: str, indent: int,
                   entries: dict[str, dict], backup_dir: Path,
                   lines: list[str]) -> None:
    for key in sorted(node.keys()):
        child_path = f"{current_path}.{key}"
        info = entries.get(child_path)
        lines.append("  " * indent + "- " + render_label(key, info, backup_dir))
        render_tree_md(node[key], child_path, indent + 1, entries, backup_dir, lines)


def write_index_md(backup_dir: Path, start_path: str, base_url: str,
                   entries: dict[str, dict], counts: dict[str, int],
                   timestamp: str) -> Path:
    """Write _INDEX.md at backup_dir: header, hierarchical tree, flat list."""
    lines: list[str] = []
    lines.append(f"# FitNesse Backup — {timestamp}")
    lines.append("")
    lines.append(f"- **Base URL:** `{base_url}`")
    lines.append(f"- **Start page:** `{start_path}`")
    lines.append(f"- **Pages seen:** {counts['pages_seen']}")
    lines.append(f"- **Saved:** {counts['ok']}")
    lines.append(f"- **Missing:** {counts['missing']}")
    lines.append(f"- **Failed:** {counts['failed']}")
    lines.append("")
    lines.append("## Tree")
    lines.append("")
    root_info = entries.get(start_path)
    lines.append("- " + render_label(start_path, root_info, backup_dir))
    tree = build_tree_dict(start_path, entries)
    render_tree_md(tree, start_path, 1, entries, backup_dir, lines)
    lines.append("")
    lines.append("## All pages (alphabetical)")
    lines.append("")
    for page_path in sorted(entries.keys()):
        lines.append("- " + render_label(page_path, entries[page_path], backup_dir))

    index_path = backup_dir / "_INDEX.md"
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return index_path


def update_latest_symlink(backup_root: Path, timestamp: str) -> None:
    latest = backup_root / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(timestamp, target_is_directory=True)
    except (OSError, NotImplementedError):
        pass


def resolve_target(target: str, explicit_parent: str | None) -> tuple[str, str]:
    """Map (--target, --parent-path) to (parent_path, label).

    Explicit --parent-path always wins and is labelled "custom" so its
    backups go into their own subfolder and never collide with main/alt.
    """
    if explicit_parent:
        return explicit_parent, "custom"
    if target == "alt":
        return FITNESSE_ALT_PARENT_PATH, "alt"
    return FITNESSE_PARENT_PATH, "main"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Back up a FitNesse subtree by crawling child links and fetching "
                    "raw wiki source per page.",
    )
    parser.add_argument("--base-url", default=FITNESSE_URL,
                        help="FitNesse base URL. Default: $FITNESSE_URL (or .env entry).")
    parser.add_argument("--target", choices=("main", "alt"), default="main",
                        help="Which named parent to back up: 'main' uses "
                             "FITNESSE_PARENT_PATH, 'alt' uses FITNESSE_ALT_PARENT_PATH. "
                             "Backups land in <backup-root>/<target>/ so the slots "
                             "do not share their 'latest' symlinks.")
    parser.add_argument("--parent-path", default=None,
                        help="Override the dotted path of the subtree root. When set, "
                             "--target is ignored and backups land in <backup-root>/custom/.")
    parser.add_argument("--backup-root", default=str(FITNESSE_BACKUP_ROOT),
                        help=f"Parent folder for per-target backup subfolders "
                             f"(default: {FITNESSE_BACKUP_ROOT}).")
    parser.add_argument("--max-depth", type=int, default=None,
                        help="Limit crawl depth (root = 0). Default: unlimited.")
    parser.add_argument("--delay", type=float, default=FITNESSE_REQUEST_DELAY,
                        help=f"Seconds to sleep between page fetches "
                             f"(default: {FITNESSE_REQUEST_DELAY}, override via FITNESSE_REQUEST_DELAY).")
    parser.add_argument("--no-markdown", action="store_true",
                        help="Skip the .md export. By default each page is "
                             "written twice: raw wiki source under txt/ and "
                             "converted markdown under md/.")
    parser.add_argument("--resume", nargs="?", const="latest", default=None,
                        metavar="<ts>-crawl",
                        help="Resume an existing crawl. With no value, picks "
                             "the most recent <ts>-crawl folder under "
                             "--backup-root. Pass a specific timestamp folder "
                             "to resume that one.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print configuration and exit without HTTP.")
    args = parser.parse_args()

    if not args.base_url:
        print("ERROR: --base-url not provided and FITNESSE_URL is empty "
              "(set in .env or export $FITNESSE_URL).", file=sys.stderr)
        return 2

    parent_path, label = resolve_target(args.target, args.parent_path)
    if not parent_path:
        env_var = {"main": "FITNESSE_PARENT_PATH",
                   "alt":  "FITNESSE_ALT_PARENT_PATH"}[args.target]
        print(f"ERROR: --parent-path not provided and {env_var} is empty "
              f"(set in .env or export ${env_var}).", file=sys.stderr)
        return 2
    args.parent_path = parent_path  # downstream code reads args.parent_path

    backup_root = Path(args.backup_root) / label
    write_markdown = not args.no_markdown

    initial_visited: set[str] | None = None
    initial_queue: "deque[tuple[str, int]] | None" = None
    initial_counts: dict[str, int] | None = None
    initial_entries: dict[str, dict] | None = None
    resume_note = ""

    if args.resume is not None:
        if args.resume == "latest":
            candidates = sorted(backup_root.glob("*-crawl"))
            if not candidates:
                print(f"ERROR: no <ts>-crawl folders under {backup_root} to resume.",
                      file=sys.stderr)
                return 2
            backup_dir = candidates[-1]
        else:
            backup_dir = backup_root / args.resume
            if not backup_dir.exists():
                print(f"ERROR: --resume target does not exist: {backup_dir}",
                      file=sys.stderr)
                return 2

        state = load_state(backup_dir)
        if state is None:
            resume_note = (f"resume {backup_dir.name} "
                           f"(no _state.json — disk-only fallback)")
        else:
            if state["start_path"] != args.parent_path:
                print(f"ERROR: --parent-path does not match the saved state "
                      f"({state['start_path']!r} vs {args.parent_path!r}). "
                      f"Pass --parent-path {state['start_path']} to resume "
                      f"this crawl.", file=sys.stderr)
                return 2
            if state["base_url"] != args.base_url:
                print(f"WARNING: --base-url differs from saved state "
                      f"({state['base_url']!r}); proceeding with "
                      f"{args.base_url!r}.", file=sys.stderr)
            initial_visited = state["visited"]
            initial_queue = state["queue"]
            initial_counts = state["counts"]
            initial_entries = state["entries"]
            write_markdown = state["write_markdown"]
            resume_note = (f"resume {backup_dir.name}: "
                           f"{len(initial_visited)} visited, "
                           f"{len(initial_queue)} queued")
    else:
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M")
        backup_dir = backup_root / f"{ts}-crawl"

    formats = "txt + md" if write_markdown else "txt only"

    print(f"Base URL:    {args.base_url}")
    print(f"Target:      {label}")
    print(f"Start page:  {args.parent_path}")
    print(f"Backup:      {backup_dir.resolve()}")
    print(f"Formats:     {formats}")
    print(f"Max depth:   {args.max_depth if args.max_depth is not None else 'unlimited'}")
    mode = "DRY RUN" if args.dry_run else ("LIVE CRAWL — " + resume_note if resume_note else "LIVE CRAWL")
    print(f"Mode:        {mode}")
    print()

    if args.dry_run:
        print("(dry run — exit without HTTP)")
        return 0

    backup_dir.mkdir(parents=True, exist_ok=True)

    counts, entries = crawl(
        args.base_url, args.parent_path, backup_dir,
        max_depth=args.max_depth, delay=args.delay,
        write_markdown=write_markdown,
        visited=initial_visited, queue=initial_queue,
        counts=initial_counts, entries=initial_entries,
    )

    index_label = backup_dir.name.replace("-crawl", "")
    index_path: Path | None = None
    if entries:
        index_path = write_index_md(
            backup_dir, args.parent_path, args.base_url, entries, counts,
            index_label,
        )

    if counts["ok"] > 0:
        update_latest_symlink(backup_root, backup_dir.name)

    # Crawl finished cleanly (queue is empty). Drop the state file so a
    # later --resume against this folder doesn't try to pick it back up.
    if counts["failed"] == 0:
        discard_state(backup_dir)

    print()
    print(f"Done: {counts['ok']} saved, {counts['missing']} missing, "
          f"{counts['failed']} failed (of {counts['pages_seen']} pages seen).")
    if counts["ok"] > 0:
        print(f"Backup: {backup_dir}")
        latest = backup_root / "latest"
        if latest.is_symlink():
            print(f"Latest: {latest}")
    if index_path is not None:
        print(f"Index:  {index_path}")
    if counts["failed"] > 0:
        print(f"State:  {state_file(backup_dir)} "
              f"(re-run with --resume to retry failed pages)")
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
