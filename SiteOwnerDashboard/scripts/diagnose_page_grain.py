"""
Diagnose why the Pages table shows the same page name several times (e.g. CAWB).

The table groups by `page_id` and displays `page_name`. If one page NAME is
attached to several `page_id`s (or one real page/URL is split across several
`page_id`s), you get duplicate-looking rows. This script measures the
relationship between page_id / page_url / page_name so we can pick the right
grain for the table:

  - page_name -> #page_id / #page_url   (is the NAME generic / reused?)
  - page_url  -> #page_id               (is the same URL split across ids? = id noise)
  - page_id   -> #page_url              (is one id reused for several URLs?)
and lists the worst offenders with sample URLs.

Read-only.

Usage (from the SiteOwnerDashboard project root):
    python scripts/diagnose_page_grain.py
    python scripts/diagnose_page_grain.py --name CAWB      # drill into one name
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]


def nunique_map(df, key, val):
    return df.groupby(key)[val].nunique()


def main() -> None:
    ap = argparse.ArgumentParser(description="Diagnose the Pages-table grain")
    ap.add_argument("--store", default=str(PROJECT_DIR / "output" / "site_pageviews.parquet"))
    ap.add_argument("--name", default=None, help="drill into one page_name (e.g. CAWB)")
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    path = Path(args.store)
    if not path.exists():
        raise SystemExit(f"store not found: {path}")
    df = pd.read_parquet(path)
    n = len(df)

    have_url = "page_url" in df.columns
    for c in ("page_id", "page_name"):
        if c not in df.columns:
            raise SystemExit(f"store missing '{c}'")

    print(f"store: {path}  ({n:,} views)")
    print(f"distinct page_id  : {df['page_id'].nunique():,}")
    print(f"distinct page_name: {df['page_name'].nunique():,}")
    if have_url:
        print(f"distinct page_url : {df['page_url'].nunique():,}")

    # --- name reused across several ids/urls (the CAWB symptom) ---
    name_ids = nunique_map(df, "page_name", "page_id")
    reused_names = name_ids[name_ids > 1]
    views_by_name = df.groupby("page_name").size()
    affected_views = int(views_by_name[reused_names.index].sum())
    print("\n1. page_name reused across MULTIPLE page_id (the duplicate-row cause)")
    print(f"   names on >1 page_id : {len(reused_names):,}/{df['page_name'].nunique():,}")
    print(f"   views under them    : {affected_views:,}/{n:,} ({affected_views/n:.0%})")

    # --- same URL split across several ids = id noise (merge would be safe) ---
    if have_url:
        url_ids = nunique_map(df, "page_url", "page_id")
        split_urls = url_ids[url_ids > 1]
        id_urls = nunique_map(df, "page_id", "page_url")
        multi_url_ids = id_urls[id_urls > 1]
        print("\n2. page_url integrity")
        print(f"   URLs split across >1 page_id : {len(split_urls):,}/{df['page_url'].nunique():,}"
              "  (same page, different ids -> grouping by URL fixes it)")
        print(f"   page_ids reused for >1 URL   : {len(multi_url_ids):,}/{df['page_id'].nunique():,}"
              "  (one id, several URLs -> id is NOT the page)")

    # --- worst offenders ---
    print(f"\n3. Top {args.top} names by views: #page_id / #page_url")
    top = views_by_name.sort_values(ascending=False).head(args.top)
    print(f"   {'page_name':40s} {'views':>10s} {'#id':>5s} {'#url':>6s}")
    for name, v in top.items():
        sub = df[df["page_name"] == name]
        nid = sub["page_id"].nunique()
        nurl = sub["page_url"].nunique() if have_url else -1
        flag = "  <- reused" if nid > 1 else ""
        disp = (str(name)[:37] + "...") if len(str(name)) > 40 else str(name)
        print(f"   {disp:40s} {int(v):>10,} {nid:>5d} {nurl:>6d}{flag}")

    # --- drill-down ---
    drill = args.name
    if drill is None and len(reused_names):
        drill = views_by_name[reused_names.index].sort_values(ascending=False).index[0]
    if drill is not None:
        print(f"\n4. Drill-down: page_name = {drill!r}")
        sub = df[df["page_name"].astype(str) == str(drill)]
        agg = sub.groupby("page_id").agg(
            views=("page_id", "size"),
            urls=("page_url", "nunique") if have_url else ("page_id", "size"),
            sample_url=("page_url", "first") if have_url else ("page_id", "first"),
            site=("site_name", "first") if "site_name" in sub.columns else ("page_id", "first"),
        ).sort_values("views", ascending=False)
        print(f"   {len(agg)} distinct page_id under this name:")
        for pid, r in agg.head(15).iterrows():
            url = str(r["sample_url"])
            url = ("…" + url[-52:]) if len(url) > 53 else url
            print(f"     views={int(r['views']):>8,}  urls={int(r['urls'])}  id={str(pid)[:12]:12s}  {url}")

    # --- recommendation ---
    print("\nRECOMMENDATION")
    reused_share = affected_views / max(n, 1)
    if have_url:
        url_ids = nunique_map(df, "page_url", "page_id")
        id_urls = nunique_map(df, "page_id", "page_url")
        url_clean = (url_ids > 1).sum() == 0            # each URL has exactly one id
        id_reused = (id_urls > 1).sum() > 0             # ids reused across URLs
    else:
        url_clean = id_reused = False

    if reused_share < 0.02:
        print("   Names barely repeat — the table grain is fine as-is.")
    elif have_url and id_reused:
        print("   One page_id maps to MULTIPLE URLs -> page_id is NOT the page. Group the")
        print("   Pages table by page_url (the real page) and label rows by URL/last path")
        print("   segment; keep page_name only as a secondary hint.")
    elif have_url:
        print("   page_name is generic (reused across page_ids) but page_url is the real")
        print("   page. Group the Pages table by page_url instead of page_id, and show the")
        print("   URL (or its last path segment) so each row is a distinct, recognisable page.")
    else:
        print("   page_name is reused across page_ids and there is no page_url to fall back")
        print("   on — capture PageURL in the export, or group by page_name (may over-merge).")


if __name__ == "__main__":
    main()
