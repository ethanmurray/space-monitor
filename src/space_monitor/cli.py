"""Command-line entry point: ``space-monitor extract-taxonomy``,
``space-monitor load``, ``space-monitor bootstrap``, ``space-monitor ingest``,
``space-monitor review``.

The ``--db`` flag accepts either a local SQLite file path
(``./space_monitor.db``) or a remote libsql / Turso URL
(``libsql://<host>``). When --db is omitted, the resolution order is:

1. ``--db`` argument if provided
2. ``TURSO_DATABASE_URL`` env var (typical CI / production setup)
3. ``./space_monitor.db`` (local default)

For Turso, ``TURSO_AUTH_TOKEN`` env var supplies the auth token.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import bootstrap, db, load, taxonomy
from .env import load_dotenv
from .pipeline import cli as pipeline_cli


def _add_db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help=(
            "DB destination: a local SQLite path (e.g. ./space_monitor.db) "
            "or a libsql:// URL (Turso). Defaults to TURSO_DATABASE_URL env "
            "if set, otherwise ./space_monitor.db."
        ),
    )


def main(argv: list[str] | None = None) -> int:
    # Load .env early so `--db` env-default + Turso auth work for every subcommand.
    load_dotenv()

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
    _add_db_arg(p_load)

    p_boot = sub.add_parser(
        "bootstrap",
        help="Initialize a fresh DB from bundled seed data (no xlsx required).",
    )
    _add_db_arg(p_boot)

    p_ui = sub.add_parser(
        "ui",
        help="Launch the Streamlit analyst UI on localhost.",
    )
    p_ui.add_argument("--port", type=int, default=8501, help="Default 8501.")
    p_ui.add_argument("--host", type=str, default="127.0.0.1", help="Default 127.0.0.1.")

    pipeline_cli.add_subcommands(sub)

    args = parser.parse_args(argv)

    if args.cmd == "extract-taxonomy":
        out = taxonomy.extract_to_json(args.xlsx, args.out)
        print(f"Wrote taxonomy to {out}")
        return 0

    if args.cmd == "load":
        target = db.resolve_db(args.db)
        counts = load.load_all(args.xlsx, target)
        width = max(len(k) for k in counts)
        print(f"Loaded {args.xlsx} -> {target}")
        for sheet, n in counts.items():
            print(f"  {sheet:<{width}}  {n:>8,} rows")
        return 0

    if args.cmd == "bootstrap":
        target = db.resolve_db(args.db)
        counts = bootstrap.bootstrap_db(target)
        width = max(len(k) for k in counts)
        print(f"Bootstrapped {target} from bundled seed data")
        for label, n in counts.items():
            print(f"  {label:<{width}}  {n:>8,} rows")
        return 0

    if args.cmd == "ui":
        # streamlit run shells out to the streamlit CLI; this gives us hot
        # reload and the proper websocket plumbing for free.
        import subprocess
        from importlib import resources

        app_path = str(resources.files("space_monitor.ui").joinpath("app.py"))
        cmd = [
            "streamlit", "run", app_path,
            "--server.port", str(args.port),
            "--server.address", args.host,
            "--browser.gatherUsageStats", "false",
        ]
        return subprocess.call(cmd)

    if hasattr(args, "func"):
        return args.func(args)

    return 1


if __name__ == "__main__":
    sys.exit(main())
