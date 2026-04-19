"""Tiny .env loader. Avoids pulling in python-dotenv for one job.

Walks up from the current working directory to find a `.env` file, parses
``KEY=value`` lines, and sets ``os.environ`` for any keys not already set.
Values may be quoted or unquoted; comments and blank lines are skipped.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(start: str | Path | None = None) -> Path | None:
    """Search for a .env file from `start` upward and load it. Return the path
    that was loaded, or None if no .env was found."""
    here = Path(start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        candidate = d / ".env"
        if candidate.is_file():
            _apply(candidate)
            return candidate
    return None


def _apply(path: Path) -> None:
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
