"""Command-line entry point: ``space-monitor extract-taxonomy`` and
``space-monitor load``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import bootstrap, load, taxonomy
from .pipeline import cli as pipeline_cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="space-monitor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_tax = sub.add_parser(
        "extract-taxonomy",
        help="Pull controlled vocabularies + scoring rubrics into data/taxonomy.json.",
    )
    p_tax.add_argument("xlsx", type=Path, help="Path to Space_Dashboard_Hardcopy.xlsx")
    p_tax.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Override output path (default: src/space_monitor/data/taxonomy.json)",
    )

    p_load = sub.add_parser(
        "load", help="Load every analytical sheet into a fresh SQLite database."
    )
    p_load.add_argument("xlsx", type=Path, help="Path to Space_Dashboard_Hardcopy.xlsx")
    p_load.add_argument(
        "--db",
        type=Path,
        default=Path("space_monitor.db"),
        help="Output SQLite path (default: ./space_monitor.db)",
    )

    p_boot = sub.add_parser(
        "bootstrap",
        help="Initialize a fresh DB from bundled seed data (no xlsx required).",
    )
    p_boot.add_argument(
        "--db",
        type=Path,
        default=Path("space_monitor.db"),
        help="Output SQLite path (default: ./space_monitor.db)",
    )

    pipeline_cli.add_subcommands(sub)

    args = parser.parse_args(argv)

    if args.cmd == "extract-taxonomy":
        out = taxonomy.extract_to_json(args.xlsx, args.out)
        print(f"Wrote taxonomy to {out}")
        return 0

    if args.cmd == "load":
        counts = load.load_all(args.xlsx, args.db)
        width = max(len(k) for k in counts)
        print(f"Loaded {args.xlsx} -> {args.db}")
        for sheet, n in counts.items():
            print(f"  {sheet:<{width}}  {n:>8,} rows")
        return 0

    if args.cmd == "bootstrap":
        counts = bootstrap.bootstrap_db(args.db)
        width = max(len(k) for k in counts)
        print(f"Bootstrapped {args.db} from bundled seed data")
        for label, n in counts.items():
            print(f"  {label:<{width}}  {n:>8,} rows")
        return 0

    if hasattr(args, "func"):
        return args.func(args)

    return 1


if __name__ == "__main__":
    sys.exit(main())
