"""Initialize a fresh space_monitor database from bundled seed data.

Lets users get a working DB without ever touching the source workbook. The
runtime pipeline only depends on the bundled ``data/taxonomy.json`` and the
bundled ``data/seed/partnership.csv`` (the latter for draft duplicate-
detection at promotion time and for the prefilter eval fixture).

Run via the CLI: ``space-monitor bootstrap [--db PATH]``.
"""

from __future__ import annotations

import csv
import sqlite3
from importlib import resources
from pathlib import Path

from . import db
from .load import _load_taxonomy


def bootstrap_db(db_path: str | Path) -> dict[str, int]:
    """Recreate the schema, load taxonomy, load seed CSVs. Return row counts."""
    counts: dict[str, int] = {}
    with db.connect(db_path) as conn:
        db.init_schema(conn)
        _load_taxonomy(conn)
        counts["[taxonomy]"] = sum(
            conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in (
                "country",
                "partnership_type",
                "business_model",
                "mission_type",
                "mass_class",
                "partnership_strength_lookup",
            )
        )
        counts["partnership (seed)"] = _load_seed_csv(conn, "partnership")
    return counts


def _load_seed_csv(conn: sqlite3.Connection, table: str) -> int:
    """Load ``data/seed/<table>.csv`` into the named table.

    The CSV's header row drives the column list — extra columns in the table
    schema (e.g. ``description`` if dropped from the seed) stay NULL. Missing
    columns in the CSV are silently ignored. This makes the seed format
    forward-compatible with schema additions.
    """
    seed_path = Path(resources.files("space_monitor") / "data" / "seed" / f"{table}.csv")
    if not seed_path.exists():
        return 0
    with seed_path.open(newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        placeholders = ", ".join(["?"] * len(header))
        col_list = ", ".join(header)
        sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
        # csv.reader yields all values as strings; SQLite will coerce numerics
        # when the column type is INTEGER/REAL. Empty strings for nullable
        # columns become NULL.
        n = 0
        for row in reader:
            row = [v if v != "" else None for v in row]
            conn.execute(sql, row)
            n += 1
        conn.commit()
        return n
