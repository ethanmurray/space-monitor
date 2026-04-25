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

import os

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

    p_watch = sub.add_parser(
        "watch",
        help="Manage watchlist (countries / orgs / partnership types) per user.",
    )
    _add_db_arg(p_watch)
    watch_sub = p_watch.add_subparsers(dest="watch_cmd", required=True)
    p_w_list = watch_sub.add_parser("list", help="List entries for a user.")
    p_w_list.add_argument("--user", default=None, help="Default: ANALYST_NAME env or 'analyst'.")
    p_w_add = watch_sub.add_parser("add", help="Add a watchlist entry.")
    p_w_add.add_argument("kind", choices=["country", "org", "partnership_type"])
    p_w_add.add_argument("value", type=str)
    p_w_add.add_argument("--user", default=None)
    p_w_rm = watch_sub.add_parser("remove", help="Remove a watchlist entry by id.")
    p_w_rm.add_argument("entry_id", type=int)

    p_dig = sub.add_parser(
        "watchdigest",
        help="Render a per-user watchlist digest. Optionally POST to NOTIFY_WEBHOOK_URL.",
    )
    _add_db_arg(p_dig)
    p_dig.add_argument("--user", default=None)
    p_dig.add_argument("--days", type=int, default=7)
    p_dig.add_argument("--post", action="store_true",
                       help="POST the digest to NOTIFY_WEBHOOK_URL after printing.")

    p_orgs = sub.add_parser(
        "orgs",
        help="Manage the canonical org registry (seed, backfill, list-unknown).",
    )
    _add_db_arg(p_orgs)
    orgs_sub = p_orgs.add_subparsers(dest="orgs_cmd", required=True)
    orgs_sub.add_parser("seed", help="Apply the bundled canonical seed list.")
    orgs_sub.add_parser(
        "backfill",
        help="Register every unseen org name from existing drafts/partnerships.",
    )
    p_orgs_unknown = orgs_sub.add_parser(
        "list-unknown",
        help="Show the org strings appearing most often that have no canonical entry.",
    )
    p_orgs_unknown.add_argument("--limit", type=int, default=50)

    p_brief = sub.add_parser(
        "brief",
        help="Generate (or retrieve cached) country briefing as markdown.",
    )
    p_brief.add_argument("country", type=str, help="Country name (canonical, e.g. 'Japan').")
    _add_db_arg(p_brief)
    p_brief.add_argument("--since-days", type=int, default=90,
                         help="Recency window for source signals (default: 90).")
    p_brief.add_argument("--force", action="store_true",
                         help="Bypass the (country, ISO-week) cache.")
    p_brief.add_argument("--out", type=str, default=None,
                         help="Write to this file instead of stdout.")

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

    if args.cmd == "watch":
        from . import watchlist
        user = args.user or os.environ.get("ANALYST_NAME") or "analyst"
        if args.watch_cmd == "list":
            entries = watchlist.list_for(user, db_arg=args.db)
            if not entries:
                print(f"({user} has no watchlist entries)")
                return 0
            print(f"{user}'s watchlist ({len(entries)} entries):")
            for e in entries:
                print(f"  #{e.id:>4}  {e.kind:<18}  {e.value}")
            return 0
        if args.watch_cmd == "add":
            ok = watchlist.add(user, args.kind, args.value, db_arg=args.db)
            print(f"added: {args.kind}={args.value}" if ok else "(already on watchlist)")
            return 0
        if args.watch_cmd == "remove":
            watchlist.remove(args.entry_id, db_arg=args.db)
            print(f"removed entry #{args.entry_id}")
            return 0

    if args.cmd == "watchdigest":
        from . import notify, watchlist
        user = args.user or os.environ.get("ANALYST_NAME") or "analyst"
        body = watchlist.build_digest(user, days=args.days, db_arg=args.db)
        if not body:
            print(f"({user} has no watchlist entries — `space-monitor watch add ...` to start)")
            return 0
        print(body)
        if args.post:
            ok = notify.post(body)
            print("\n[notify] webhook:", "ok" if ok else "skipped/failed")
        return 0

    if args.cmd == "orgs":
        from . import orgs as orgs_mod
        if args.orgs_cmd == "seed":
            n = orgs_mod.seed_canonical(db_arg=args.db)
            print(f"Seeded canonical orgs ({n} new).")
            return 0
        if args.orgs_cmd == "backfill":
            n = orgs_mod.backfill_from_drafts(db_arg=args.db)
            print(f"Backfilled {n} new canonical org(s) from drafts/partnerships.")
            return 0
        if args.orgs_cmd == "list-unknown":
            rows = orgs_mod.list_unknown_top(limit=args.limit, db_arg=args.db)
            if not rows:
                print("(no unknown org names)")
                return 0
            print(f"{len(rows)} unknown org name(s) (sorted by occurrences):\n")
            for name, n in rows:
                print(f"  {n:>4}  {name}")
            return 0

    if args.cmd == "brief":
        from . import briefing
        result = briefing.generate(
            args.country, since_days=args.since_days,
            force=args.force, db_arg=args.db,
        )
        header = (
            f"<!-- {args.country} | {result.since}..today | "
            f"{result.article_count} articles | "
            f"{'CACHED' if result.from_cache else 'fresh'} -->\n\n"
        )
        body = header + result.body_markdown
        if args.out:
            with open(args.out, "w") as fh:
                fh.write(body)
            print(f"Wrote briefing to {args.out}")
        else:
            print(body)
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
