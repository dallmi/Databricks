"""Download the dashboard's CDN libraries into dashboard/vendor/.

The standalone build (scripts/build_standalone_dashboard.py) inlines these
local copies so it needs no network access. Re-run this script only on a
version bump of a library, on a machine with internet access.

Run (from the SiteOwnerDashboard project root):
    python scripts/vendor_libs.py
"""
import sys
import urllib.request

from build_standalone_dashboard import LIBS, VENDOR_DIR


def main() -> int:
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    for url, filename in LIBS:
        dest = VENDOR_DIR / filename
        print(f"Downloading {url} -> {dest}")
        with urllib.request.urlopen(url) as resp:
            dest.write_bytes(resp.read())
        print(f"  {dest.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
