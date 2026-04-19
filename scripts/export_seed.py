"""Export the curated tables we want to ship in the repo as seed CSVs.

Reads from a fully-loaded SQLite database (the one produced by
``space-monitor load Space_Dashboard_Hardcopy.xlsx``) and writes one CSV per
seeded table into ``src/space_monitor/data/seed/``.

Re-run this whenever the source workbook is refreshed and you want to update
what gets bootstrapped from a fresh clone. The pipeline itself does not call
this script — it's maintainer tooling.

Today we seed only the ``partnership`` table because that's the only workbook
table the runtime code actually queries (duplicate detection at draft
promotion, plus the prefilter eval fixture). Other workbook tables stay out
of the repo for two reasons: (a) they're large or proprietary (city
gazetteer, PitchBook deals, defense spending), or (b) they should be
refreshed from authoritative external sources via the connector pattern in
roadmap step 4 (UCS satellites, SIPRI defense spending, etc.) rather than
mirrored from one analyst's workbook snapshot.

Usage::

    python scripts/export_seed.py [--db PATH]
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path


# Per-table column exclusions when seeding. Drops are columns that bloat the
# CSV without serving the runtime code or analyst quick-browse use cases.
# The full data is still available by running `space-monitor load <xlsx>`
# against the source workbook, if you have it.
SEED_DROP_COLS: dict[str, set[str]] = {
    # description is a 369-byte avg press-release excerpt — half the file size.
    # Not used by code (dup detection only checks country/year). Recoverable
    # from the workbook description column if anyone needs it.
    "partnership": {"description"},
}

SEED_TABLES = list(SEED_DROP_COLS.keys())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("--db", type=Path, default=Path("space_monitor.db"))
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src" / "space_monitor" / "data" / "seed",
        help="Output directory for the seed CSVs (default: bundled under the package)",
    )
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"error: {args.db} not found. Run `space-monitor load <xlsx>` first.", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    for table in SEED_TABLES:
        out_path = args.out / f"{table}.csv"
        rows = list(conn.execute(f"SELECT * FROM {table}"))
        if not rows:
            print(f"  {table}: 0 rows — skipping (table is empty in {args.db})")
            continue
        drop = SEED_DROP_COLS.get(table, set())
        cols = [c for c in rows[0].keys() if c not in drop]
        with out_path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(cols)
            for row in rows:
                writer.writerow([row[c] for c in cols])
        size_kb = out_path.stat().st_size // 1024
        dropped = f", dropped={sorted(drop)}" if drop else ""
        print(f"  {table}: {len(rows):,} rows × {len(cols)} cols -> {out_path} ({size_kb} KB){dropped}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
