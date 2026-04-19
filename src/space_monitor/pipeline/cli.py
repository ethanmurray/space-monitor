"""Pipeline CLI: ``ingest`` (run sources -> fetch -> extract) and ``review``
(list / show / approve / reject drafts). Wired into the top-level
``space-monitor`` CLI by :mod:`space_monitor.cli`.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import db
from ..env import load_dotenv
from . import drafts as drafts_mod
from . import extract, fetch, prefilter
from .sources import REGISTRY


def add_subcommands(sub: argparse._SubParsersAction) -> None:
    """Attach `ingest` and `review` to a top-level subparsers object."""

    p_ingest = sub.add_parser("ingest", help="Discover -> fetch -> extract from a source.")
    p_ingest.add_argument(
        "--source",
        required=True,
        choices=sorted(REGISTRY) + ["all"],
        help="Source name, or 'all' to iterate every adapter not marked disabled.",
    )
    p_ingest.add_argument(
        "--db", type=str, default=None,
        help="DB destination (path or libsql:// URL). Defaults to TURSO_DATABASE_URL env or ./space_monitor.db.",
    )
    p_ingest.add_argument(
        "--max-candidates", type=int, default=10,
        help="Max URLs to pull from each source feed this run (default: 10).",
    )
    p_ingest.add_argument(
        "--max-extractions", type=int, default=3,
        help="Cost cap: max LLM extractions per source this invocation (default: 3).",
    )
    p_ingest.add_argument(
        "--since", type=str, default=None,
        help="Skip candidates with published_at before this date (YYYY-MM-DD).",
    )
    p_ingest.add_argument(
        "--rate-limit-secs", type=float, default=1.5,
        help="Polite delay between consecutive fetches within a source (default: 1.5s).",
    )
    p_ingest.set_defaults(func=_cmd_ingest)

    p_review = sub.add_parser("review", help="List, show, approve, or reject pending drafts.")
    p_review.add_argument(
        "--db", type=str, default=None,
        help="DB destination (path or libsql:// URL). Defaults to TURSO_DATABASE_URL env or ./space_monitor.db.",
    )
    review_sub = p_review.add_subparsers(dest="review_cmd", required=True)

    p_list = review_sub.add_parser("list", help="List pending drafts.")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(func=_cmd_review_list)

    p_show = review_sub.add_parser("show", help="Show one draft + its source article.")
    p_show.add_argument("draft_id", type=int)
    p_show.set_defaults(func=_cmd_review_show)

    p_approve = review_sub.add_parser("approve", help="Promote a draft to a partnership row.")
    p_approve.add_argument("draft_id", type=int)
    p_approve.add_argument("--reviewer", required=True)
    p_approve.add_argument("--notes", default=None)
    p_approve.set_defaults(func=_cmd_review_approve)

    p_reject = review_sub.add_parser("reject", help="Reject a draft.")
    p_reject.add_argument("draft_id", type=int)
    p_reject.add_argument("--reviewer", required=True)
    p_reject.add_argument("--reason", required=True)
    p_reject.set_defaults(func=_cmd_review_reject)


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


@dataclass
class _SourceTotals:
    candidates: int = 0
    skipped_prefilter: int = 0
    fetched: int = 0
    extracted: int = 0
    new_positives: int = 0
    cache_reads: int = 0
    cache_writes: int = 0
    failed: int = 0


@dataclass
class _RunTotals:
    by_source: dict[str, _SourceTotals] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)


def _cmd_ingest(args: argparse.Namespace) -> int:
    load_dotenv()

    if args.source == "all":
        sources = [s for s in REGISTRY.values() if not getattr(s, "disabled", False)]
        skipped_disabled = [s.name for s in REGISTRY.values() if getattr(s, "disabled", False)]
        if skipped_disabled:
            print(f"[ingest] --source all: skipping disabled adapters: {', '.join(skipped_disabled)}")
    else:
        sources = [REGISTRY[args.source]]

    totals = _RunTotals()
    with db.connect(db.resolve_db(args.db)) as conn:
        db.ensure_pipeline_schema(conn)
        for src in sources:
            print(f"\n==================== {src.name} ====================")
            t = _ingest_one(conn, src, args)
            totals.by_source[src.name] = t

    if args.source == "all":
        _print_cross_source_summary(totals)
    return 0


def _ingest_one(conn: sqlite3.Connection, source, args: argparse.Namespace) -> _SourceTotals:
    t = _SourceTotals()
    print(
        f"[ingest] source={source.name} max_candidates={args.max_candidates} "
        f"max_extractions={args.max_extractions} rate_limit={args.rate_limit_secs}s"
    )

    candidates = list(source.iter_candidates(limit=args.max_candidates))
    if args.since:
        before = len(candidates)
        candidates = [
            c for c in candidates
            if c.published_at is None or c.published_at[:10] >= args.since
        ]
        dropped = before - len(candidates)
        if dropped:
            print(f"[since] dropped {dropped} candidate(s) older than {args.since}")

    skip_decisions: dict[str, prefilter.Decision] = {}
    if getattr(source, "prefilter_required", False) and candidates:
        print(f"[prefilter] classifying {len(candidates)} title(s) on {source.name}...")
        results = prefilter.classify_candidates(candidates)
        n_yes = n_no = n_unc = 0
        for cand, decision in results:
            if decision.verdict == "yes":
                n_yes += 1
            elif decision.verdict == "uncertain":
                n_unc += 1
            else:
                n_no += 1
                skip_decisions[cand.url_hash] = decision
        print(f"[prefilter] yes={n_yes} uncertain={n_unc} no={n_no} (no's will be skipped)")

    for candidate in candidates:
        t.candidates += 1
        print(f"  [{t.candidates:>2}] {candidate.url}")

        if candidate.url_hash in skip_decisions:
            decision = skip_decisions[candidate.url_hash]
            article_id = fetch.record_skip(conn, candidate, decision.reason)
            t.skipped_prefilter += 1
            print(f"       skip — prefilter:no  ({decision.reason}) [id={article_id}]")
            continue

        result = fetch.fetch(conn, candidate)
        if result.was_new and args.rate_limit_secs > 0:
            # Polite delay — applied AFTER the fetch so subsequent iterations
            # are spaced out. Skipped when reusing already-stored articles
            # (no actual network call happened).
            time.sleep(args.rate_limit_secs)

        if result.status == "failed":
            t.failed += 1
            print(f"       fetch failed (id={result.article_id})")
            continue
        if result.status == "extracted":
            print(f"       skip — already extracted (id={result.article_id})")
            continue
        if result.was_new:
            t.fetched += 1
        else:
            print(f"       reusing fetched article (id={result.article_id})")

        if t.extracted >= args.max_extractions:
            print(f"       (cap reached: {args.max_extractions} extractions)")
            continue

        text_row = conn.execute(
            "SELECT cleaned_text, title, url FROM news_article WHERE id=?",
            (result.article_id,),
        ).fetchone()
        cleaned, title, url = text_row
        try:
            extraction = extract.extract_with_escalation(cleaned, title=title, url=url)
        except Exception as e:
            print(f"       extract failed: {type(e).__name__}: {e}")
            conn.execute(
                "UPDATE news_article SET status='failed', failure_reason=? WHERE id=?",
                (f"extract: {type(e).__name__}: {e}"[:500], result.article_id),
            )
            conn.commit()
            t.failed += 1
            continue

        t.cache_reads += extraction.usage.cache_read_input_tokens
        t.cache_writes += extraction.usage.cache_creation_input_tokens
        draft_id = drafts_mod.insert_draft(conn, article_id=result.article_id, extraction=extraction)
        t.extracted += 1
        if extraction.payload.get("is_partnership"):
            t.new_positives += 1
        payload = extraction.payload
        esc = f" (escalated from {extraction.escalated_from})" if extraction.escalated_from else ""
        print(
            f"       -> draft #{draft_id}  is_partnership={payload.get('is_partnership')} "
            f"confidence={payload.get('confidence')} "
            f"({payload.get('country_1')} <-> {payload.get('country_2')}){esc}"
        )
        print(
            f"       usage: model={extraction.model} "
            f"input={extraction.usage.input_tokens} "
            f"output={extraction.usage.output_tokens} "
            f"cache_read={extraction.usage.cache_read_input_tokens} "
            f"cache_write={extraction.usage.cache_creation_input_tokens}"
        )

    print()
    print(
        f"[ingest] {source.name}: candidates={t.candidates} "
        f"skipped_prefilter={t.skipped_prefilter} fetched={t.fetched} "
        f"extracted={t.extracted} positives={t.new_positives} failed={t.failed}"
    )
    print(f"[ingest] {source.name}: cache_read={t.cache_reads} cache_write={t.cache_writes}")
    return t


def _print_cross_source_summary(totals: _RunTotals) -> None:
    elapsed = time.time() - totals.started_at
    grand = _SourceTotals()
    print("\n" + "=" * 80)
    print(f"INGEST SUMMARY  (elapsed {elapsed:.1f}s, {len(totals.by_source)} source(s))")
    print("=" * 80)
    print(
        f"  {'source':<18} {'cand':>5} {'skip':>5} {'fetch':>6} {'extr':>5} "
        f"{'pos':>4} {'fail':>5}"
    )
    for name, t in totals.by_source.items():
        print(
            f"  {name:<18} {t.candidates:>5} {t.skipped_prefilter:>5} "
            f"{t.fetched:>6} {t.extracted:>5} {t.new_positives:>4} {t.failed:>5}"
        )
        grand.candidates += t.candidates
        grand.skipped_prefilter += t.skipped_prefilter
        grand.fetched += t.fetched
        grand.extracted += t.extracted
        grand.new_positives += t.new_positives
        grand.failed += t.failed
        grand.cache_reads += t.cache_reads
        grand.cache_writes += t.cache_writes
    print("  " + "-" * 56)
    print(
        f"  {'TOTAL':<18} {grand.candidates:>5} {grand.skipped_prefilter:>5} "
        f"{grand.fetched:>6} {grand.extracted:>5} {grand.new_positives:>4} {grand.failed:>5}"
    )
    print(
        f"\n  cache_read_total={grand.cache_reads:,} "
        f"cache_write_total={grand.cache_writes:,}"
    )


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


def _cmd_review_list(args: argparse.Namespace) -> int:
    with db.connect(db.resolve_db(args.db)) as conn:
        db.ensure_pipeline_schema(conn)
        rows = drafts_mod.list_pending(conn, limit=args.limit)
    if not rows:
        print("(no pending drafts)")
        return 0
    print(f"{len(rows)} pending draft(s):\n")
    for r in rows:
        print(drafts_mod.render_summary(r))
    return 0


def _cmd_review_show(args: argparse.Namespace) -> int:
    with db.connect(db.resolve_db(args.db)) as conn:
        db.ensure_pipeline_schema(conn)
        d = drafts_mod.show(conn, args.draft_id)
    if not d:
        print(f"draft {args.draft_id} not found", file=sys.stderr)
        return 1
    print(drafts_mod.render_full(d))
    return 0


def _cmd_review_approve(args: argparse.Namespace) -> int:
    with db.connect(db.resolve_db(args.db)) as conn:
        db.ensure_pipeline_schema(conn)
        try:
            pid = drafts_mod.approve(
                conn, args.draft_id, reviewer=args.reviewer, notes=args.notes
            )
        except ValueError as e:
            print(f"approve failed: {e}", file=sys.stderr)
            return 1
    print(f"approved -> partnership_id = {pid!r}")
    return 0


def _cmd_review_reject(args: argparse.Namespace) -> int:
    with db.connect(db.resolve_db(args.db)) as conn:
        db.ensure_pipeline_schema(conn)
        drafts_mod.reject(conn, args.draft_id, reviewer=args.reviewer, reason=args.reason)
    print(f"rejected #{args.draft_id}")
    return 0
