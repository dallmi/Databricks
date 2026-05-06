"""Central config for FitNesse scripts.

Reads <repo-root>/.env on import, then exposes the FITNESSE_* constants
the scripts use as argparse defaults. Shell environment variables win
over .env values, so CI/one-off overrides work without editing the file.

Add a new managed value by:
  1. Documenting it in .env.example
  2. Reading it here as a module-level constant
  3. Importing the constant in the script that needs it
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | None = None) -> None:
    env_path = path or (REPO_ROOT / ".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def resolve_path(env_var: str, fallback: Path) -> Path:
    """Read a path from env, resolving relative paths against REPO_ROOT."""
    value = os.environ.get(env_var, "")
    if not value:
        return fallback
    p = Path(value)
    return p if p.is_absolute() else REPO_ROOT / p


# Auto-load .env on first import so the constants below pick up its values.
load_env()

# ---------------------------------------------------------------------------
# FitNesse — connection, paths, tuning. All four fitnesse_*.py scripts
# import these directly. To override per-call: export the matching env var
# (FITNESSE_URL etc.) before invocation; shell wins over .env.
# ---------------------------------------------------------------------------
FITNESSE_URL = os.environ.get("FITNESSE_URL", "")
FITNESSE_PARENT_PATH = os.environ.get("FITNESSE_PARENT_PATH", "")
# Optional second subtree, used by `fitnesse_backup_crawl.py --target alt`.
FITNESSE_ALT_PARENT_PATH = os.environ.get("FITNESSE_ALT_PARENT_PATH", "")
FITNESSE_ROOT_NAME = os.environ.get("FITNESSE_ROOT_NAME", "MultiChannelDataModel")
FITNESSE_PAGES_DIR = resolve_path(
    "FITNESSE_PAGES_DIR", REPO_ROOT / "docs" / "fitnesse" / "pages"
)
FITNESSE_BACKUP_ROOT = resolve_path(
    "FITNESSE_BACKUP_ROOT", FITNESSE_PAGES_DIR.parent / "backup"
)
FITNESSE_MARKDOWN_DIR = resolve_path(
    "FITNESSE_MARKDOWN_DIR", FITNESSE_PAGES_DIR.parent / "markdown"
)
FITNESSE_REQUEST_DELAY = float(os.environ.get("FITNESSE_REQUEST_DELAY", "0.3"))
