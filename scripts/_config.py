"""Tiny .env loader (stdlib only).

Reads <repo-root>/.env if present and exports KEY=VALUE pairs into
os.environ. Values already in os.environ win, so shell exports and CI
variables continue to override the file.

Call load_env() once at module top, before reading os.environ defaults.
Lines starting with '#' and blank lines are ignored. Values may be
optionally wrapped in single or double quotes.
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
