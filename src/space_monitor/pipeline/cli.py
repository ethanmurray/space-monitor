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
from . import country_tag as country_tag_mod
from . import drafts as drafts_mod
from . import extract, fetch, prefilter, signals
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
        "--max-extractions", type=int, default=50,
        help="Cost cap: max LLM extractions per source this invocation (default: 50; "
             "the daily cron uses 300, sized to absorb backfill bursts from low-"
             "cadence scrapers like skao/eusst that surface their entire archive).",
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

    p_tag = sub.add_parser(
        "tag-countries",
        help="Backfill the country-tag layer for already-fetched articles.",
    )
    p_tag.add_argument(
        "--db", type=str, default=None,
        help="DB destination (path or libsql:// URL). Defaults to TURSO_DATABASE_URL env or ./space_monitor.db.",
    )
    p_tag.add_argument(
        "--limit", type=int, default=200,
        help="Max articles to tag this run (default: 200).",
    )
    p_tag.add_argument(
        "--source", type=str, default=None,
        help="Restrict to one source (default: all sources).",
    )
    p_tag.add_argument(
        "--retag", action="store_true",
        help="Re-tag articles that already have country tags (default: skip them).",
    )
    p_tag.set_defaults(func=_cmd_tag_countries)

    p_reextract = sub.add_parser(
        "reextract",
        help="Wipe drafts and/or country tags and re-run extraction over fetched articles.",
    )
    p_reextract.add_argument(
        "--db", type=str, default=None,
        help="DB destination (path or libsql:// URL). Defaults to TURSO_DATABASE_URL env or ./space_monitor.db.",
    )
    p_reextract.add_argument(
        "--source", type=str, default=None,
        help="Restrict to one source (default: all).",
    )
    p_reextract.add_argument(
        "--since", type=str, default=None,
        help="Only re-extract articles fetched on or after this date (YYYY-MM-DD).",
    )
    p_reextract.add_argument(
        "--what", choices=["drafts", "tags", "both"], default="drafts",
        help="What to wipe and recompute (default: drafts).",
    )
    p_reextract.add_argument(
        "--limit", type=int, default=100,
        help="Max articles to (re-)process (default: 100).",
    )
    p_reextract.add_argument(
        "--dry-run", action="store_true",
        help="Print the plan without making any LLM calls.",
    )
    p_reextract.set_defaults(func=_cmd_reextract)

    p_bulk = review_sub.add_parser(
        "bulk",
        help="Bulk approve/reject pending drafts matching a filter.",
    )
    p_bulk.add_argument(
        "action", choices=["approve-high", "reject"],
        help="approve-high: approve all pending drafts with confidence='high'. "
             "reject: reject all matching drafts (requires --reason).",
    )
    p_bulk.add_argument("--reviewer", required=True)
    p_bulk.add_argument("--reason", default=None,
                        help="Required for the 'reject' action.")
    p_bulk.add_argument("--source", default=None,
                        help="Restrict to one source (default: all).")
    p_bulk.add_argument("--since", default=None,
                        help="Restrict to drafts extracted on or after this date (YYYY-MM-DD).")
    p_bulk.add_argument("--limit", type=int, default=200)
    p_bulk.add_argument("--dry-run", action="store_true")
    p_bulk.set_defaults(func=_cmd_review_bulk)

    p_consume = review_sub.add_parser(
        "consume",
        help="Apply a magic-link approve/reject token (from a digest email).",
    )
    p_consume.add_argument("token", type=str)
    p_consume.add_argument("--reason", default=None,
                           help="Optional rejection reason; defaults to 'Rejected via magic link'.")
    p_consume.set_defaults(func=_cmd_review_consume)

    p_mint = review_sub.add_parser(
        "mint",
        help="Mint a magic-link approve/reject token for a draft.",
    )
    p_mint.add_argument("draft_id", type=int)
    p_mint.add_argument("action", choices=["approve", "reject"])
    p_mint.add_argument("--user", required=True)
    p_mint.set_defaults(func=_cmd_review_mint)

    p_skipped = review_sub.add_parser(
        "skipped",
        help="List prefilter-skipped articles for spot-check.",
    )
    p_skipped.add_argument("--source", type=str, default=None)
    p_skipped.add_argument(
        "--since", type=str, default=None,
        help="Only show articles fetched on or after this date (YYYY-MM-DD).",
    )
    p_skipped.add_argument("--limit", type=int, default=50)
    p_skipped.set_defaults(func=_cmd_review_skipped)

    p_cost = sub.add_parser(
        "cost",
        help="Summarize LLM token usage from the extraction_usage audit table.",
    )
    p_cost.add_argument(
        "--db", type=str, default=None,
        help="DB destination (path or libsql:// URL).",
    )
    p_cost.add_argument(
        "--since", type=str, default=None,
        help="Only count usage rows recorded on or after this date (YYYY-MM-DD).",
    )
    p_cost.set_defaults(func=_cmd_cost)

    p_digest = sub.add_parser(
        "digest",
        help="Print a one-message summary of the last 24h. Optionally POST to NOTIFY_WEBHOOK_URL.",
    )
    p_digest.add_argument(
        "--db", type=str, default=None,
        help="DB destination (path or libsql:// URL).",
    )
    p_digest.add_argument(
        "--post", action="store_true",
        help="POST the digest to NOTIFY_WEBHOOK_URL after printing.",
    )
    p_digest.set_defaults(func=_cmd_digest)

    p_alarm = sub.add_parser(
        "cost-alarm",
        help="Check cost over a window vs a cap. Optionally POST a Slack alert.",
    )
    p_alarm.add_argument(
        "--db", type=str, default=None,
        help="DB destination (path or libsql:// URL).",
    )
    p_alarm.add_argument(
        "--cap-usd", type=float, default=7.0,
        help="Threshold over the window. Default $7 (~$200/mo / 30).",
    )
    p_alarm.add_argument(
        "--hours", type=int, default=24,
        help="Window size (default: 24).",
    )
    p_alarm.add_argument(
        "--post", action="store_true",
        help="POST the alert to NOTIFY_WEBHOOK_URL when fired.",
    )
    p_alarm.set_defaults(func=_cmd_cost_alarm)

    p_stale = sub.add_parser(
        "source-health",
        help="Print sources that haven't produced an article in > N days.",
    )
    p_stale.add_argument(
        "--db", type=str, default=None,
        help="DB destination (path or libsql:// URL).",
    )
    p_stale.add_argument(
        "--threshold-days", type=int, default=14,
        help="Days of silence to flag as stale (default: 14).",
    )
    p_stale.add_argument(
        "--post", action="store_true",
        help="POST the alert to NOTIFY_WEBHOOK_URL when any sources are stale.",
    )
    p_stale.set_defaults(func=_cmd_source_health)


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
    countries_tagged: int = 0
    contracts: int = 0
    leadership_changes: int = 0
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
            reason_row = conn.execute(
                "SELECT failure_reason FROM news_article WHERE id = ?",
                (result.article_id,),
            ).fetchone()
            reason = (reason_row[0] if reason_row else None) or "unknown"
            print(f"       fetch failed (id={result.article_id}) — {reason}")
            continue
        if result.status == "extracted":
            print(f"       skip — already extracted (id={result.article_id})")
            continue
        if result.was_new:
            t.fetched += 1
        else:
            print(f"       reusing fetched article (id={result.article_id})")

        text_row = conn.execute(
            "SELECT cleaned_text, title, url FROM news_article WHERE id=?",
            (result.article_id,),
        ).fetchone()
        cleaned, title, url = text_row

        # Country tagging runs on every fetched article (even ones we won't
        # extract this turn — the tag is the foundation for per-country
        # queries and shouldn't be gated by the extraction cap).
        if not country_tag_mod.already_tagged(conn, result.article_id):
            try:
                tag_result = country_tag_mod.tag(cleaned, title=title)
                n_countries = country_tag_mod.persist(conn, result.article_id, tag_result)
                t.countries_tagged += n_countries
                t.cache_reads += tag_result.cache_read_input_tokens
                t.cache_writes += tag_result.cache_creation_input_tokens
                extract.log_usage(
                    conn,
                    model=tag_result.model,
                    kind="country_tag",
                    article_id=result.article_id,
                    input_tokens=tag_result.input_tokens,
                    output_tokens=tag_result.output_tokens,
                    cache_read_input_tokens=tag_result.cache_read_input_tokens,
                    cache_creation_input_tokens=tag_result.cache_creation_input_tokens,
                )
                if n_countries:
                    summary = ", ".join(
                        f"{c}{'*' if cn == 'central' else ''}"
                        for c, cn in tag_result.countries
                    )
                    print(f"       countries: {summary}")
            except Exception as e:
                print(f"       country-tag failed: {type(e).__name__}: {e}")

        if t.extracted >= args.max_extractions:
            print(f"       (cap reached: {args.max_extractions} extractions)")
            continue
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
        extract.log_usage(
            conn,
            model=extraction.model,
            kind="extract",
            article_id=result.article_id,
            input_tokens=extraction.usage.input_tokens,
            output_tokens=extraction.usage.output_tokens,
            cache_read_input_tokens=extraction.usage.cache_read_input_tokens,
            cache_creation_input_tokens=extraction.usage.cache_creation_input_tokens,
        )
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

        # Multi-signal pass — route the article and run typed extractors for
        # any non-partnership signals. Partnership is already handled above
        # via the existing extract.* path; we just record it in
        # article_signal so the inventory is uniform.
        if extraction.payload.get("is_partnership"):
            signals.record_signal(conn, result.article_id, "partnership")
            conn.commit()
        try:
            router = signals.route(cleaned, title=title)
            extract.log_usage(
                conn,
                model=router.model, kind="signal_router",
                article_id=result.article_id,
                input_tokens=router.input_tokens,
                output_tokens=router.output_tokens,
                cache_read_input_tokens=router.cache_read_input_tokens,
                cache_creation_input_tokens=router.cache_creation_input_tokens,
            )
            for kind in router.signals:
                if kind == "partnership" or signals.has_signal(conn, result.article_id, kind):
                    continue
                _run_signal_extraction(
                    conn=conn,
                    article_id=result.article_id,
                    cleaned=cleaned, title=title, url=url,
                    kind=kind, totals=t,
                )
        except Exception as e:
            print(f"       signal-router failed: {type(e).__name__}: {e}")

    print()
    print(
        f"[ingest] {source.name}: candidates={t.candidates} "
        f"skipped_prefilter={t.skipped_prefilter} fetched={t.fetched} "
        f"extracted={t.extracted} positives={t.new_positives} "
        f"contracts={t.contracts} leadership={t.leadership_changes} "
        f"country_tags={t.countries_tagged} failed={t.failed}"
    )
    print(f"[ingest] {source.name}: cache_read={t.cache_reads} cache_write={t.cache_writes}")
    return t


def _run_signal_extraction(
    *,
    conn,
    article_id: int,
    cleaned: str,
    title: str | None,
    url: str | None,
    kind: str,
    totals: _SourceTotals,
) -> None:
    """Run the typed extractor for one signal kind. Logs usage + updates totals."""
    try:
        if kind == "contract":
            res = signals.extract_contract(cleaned, title=title, url=url)
            draft_id = signals.persist_contract(conn, article_id, res)
            totals.contracts += 1
            payload = res.payload
            print(
                f"       -> contract #{draft_id}  "
                f"{payload.get('contractor')} <- {payload.get('customer')}  "
                f"value={payload.get('value_musd')}M  conf={payload.get('confidence')}"
            )
        elif kind == "leadership_change":
            res = signals.extract_leadership_change(cleaned, title=title, url=url)
            draft_id = signals.persist_leadership(conn, article_id, res)
            if draft_id == 0:
                print(f"       -> leadership_change skipped (no person_name)")
                return
            totals.leadership_changes += 1
            payload = res.payload
            print(
                f"       -> leadership #{draft_id}  "
                f"{payload.get('person_name')} -> {payload.get('new_role')} "
                f"@ {payload.get('organization')}  conf={payload.get('confidence')}"
            )
        else:
            return
        totals.cache_reads += res.cache_read_input_tokens
        totals.cache_writes += res.cache_creation_input_tokens
        extract.log_usage(
            conn,
            model=res.model, kind=f"signal_{kind}",
            article_id=article_id,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            cache_read_input_tokens=res.cache_read_input_tokens,
            cache_creation_input_tokens=res.cache_creation_input_tokens,
        )
    except Exception as e:
        print(f"       {kind} extract failed: {type(e).__name__}: {e}")


def _print_cross_source_summary(totals: _RunTotals) -> None:
    elapsed = time.time() - totals.started_at
    grand = _SourceTotals()
    print("\n" + "=" * 80)
    print(f"INGEST SUMMARY  (elapsed {elapsed:.1f}s, {len(totals.by_source)} source(s))")
    print("=" * 80)
    print(
        f"  {'source':<18} {'cand':>5} {'skip':>5} {'fetch':>6} {'extr':>5} "
        f"{'pos':>4} {'tags':>5} {'fail':>5}"
    )
    for name, t in totals.by_source.items():
        print(
            f"  {name:<18} {t.candidates:>5} {t.skipped_prefilter:>5} "
            f"{t.fetched:>6} {t.extracted:>5} {t.new_positives:>4} "
            f"{t.countries_tagged:>5} {t.failed:>5}"
        )
        grand.candidates += t.candidates
        grand.skipped_prefilter += t.skipped_prefilter
        grand.fetched += t.fetched
        grand.extracted += t.extracted
        grand.new_positives += t.new_positives
        grand.countries_tagged += t.countries_tagged
        grand.failed += t.failed
        grand.cache_reads += t.cache_reads
        grand.cache_writes += t.cache_writes
    print("  " + "-" * 62)
    print(
        f"  {'TOTAL':<18} {grand.candidates:>5} {grand.skipped_prefilter:>5} "
        f"{grand.fetched:>6} {grand.extracted:>5} {grand.new_positives:>4} "
        f"{grand.countries_tagged:>5} {grand.failed:>5}"
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


def _cmd_review_bulk(args: argparse.Namespace) -> int:
    """Bulk approve / reject the matching pending drafts."""
    if args.action == "reject" and not args.reason:
        print("--reason is required for the 'reject' action", file=sys.stderr)
        return 2

    sql = (
        "SELECT d.id, d.confidence, a.source FROM partnership_draft d "
        "JOIN news_article a ON a.id = d.source_article_id "
        "WHERE d.draft_status = 'pending'"
    )
    params: list = []
    if args.source:
        sql += " AND a.source = ?"
        params.append(args.source)
    if args.since:
        sql += " AND d.extracted_at >= ?"
        params.append(args.since)
    if args.action == "approve-high":
        sql += " AND d.confidence = 'high'"
    sql += " ORDER BY d.id ASC LIMIT ?"
    params.append(args.limit)

    with db.connect(db.resolve_db(args.db)) as conn:
        db.ensure_pipeline_schema(conn)
        rows = conn.execute(sql, params).fetchall()
        if not rows:
            print("(no matching drafts)")
            return 0
        print(f"[bulk] {len(rows)} draft(s) matched")
        if args.dry_run:
            for did, conf, src in rows[:20]:
                print(f"  would {args.action}: #{did} (conf={conf}, src={src})")
            if len(rows) > 20:
                print(f"  … and {len(rows) - 20} more")
            return 0
        n_ok = 0
        n_err = 0
        for did, conf, src in rows:
            try:
                if args.action == "approve-high":
                    drafts_mod.approve(
                        conn, did, reviewer=args.reviewer,
                        notes="Bulk-approved (high confidence)",
                    )
                else:
                    drafts_mod.reject(
                        conn, did, reviewer=args.reviewer, reason=args.reason,
                    )
                n_ok += 1
            except Exception as e:
                n_err += 1
                print(f"  #{did} failed: {type(e).__name__}: {e}")
        print(f"[bulk] {args.action}: ok={n_ok} err={n_err}")
    return 0


def _cmd_review_consume(args: argparse.Namespace) -> int:
    from .. import review_links
    ok, msg = review_links.consume(args.token, reason=args.reason, db_arg=args.db)
    print(msg)
    return 0 if ok else 1


def _cmd_review_mint(args: argparse.Namespace) -> int:
    from .. import review_links
    token, url_or_cli = review_links.mint(
        args.draft_id, args.action, issued_to=args.user, db_arg=args.db,
    )
    print(f"token: {token}")
    print(f"link:  {url_or_cli}")
    return 0


def _cmd_review_skipped(args: argparse.Namespace) -> int:
    sql = (
        "SELECT id, source, title, fetched_at, failure_reason "
        "FROM news_article WHERE status = 'skipped_prefilter'"
    )
    params: list = []
    if args.source:
        sql += " AND source = ?"
        params.append(args.source)
    if args.since:
        sql += " AND fetched_at >= ?"
        params.append(args.since)
    sql += " ORDER BY fetched_at DESC LIMIT ?"
    params.append(args.limit)
    with db.connect(db.resolve_db(args.db)) as conn:
        db.ensure_pipeline_schema(conn)
        rows = conn.execute(sql, params).fetchall()
    if not rows:
        print("(no prefilter-skipped articles match)")
        return 0
    print(f"{len(rows)} skipped article(s):\n")
    for aid, src, title, fetched, reason in rows:
        title_clean = (title or "(no title)")[:80]
        reason_clean = (reason or "")[:60]
        print(f"  #{aid:>5}  {src:<18}  {fetched[:10]}  {title_clean}")
        if reason_clean:
            print(f"          reason: {reason_clean}")
    return 0


# ---------------------------------------------------------------------------
# Country-tag backfill
# ---------------------------------------------------------------------------


def _cmd_tag_countries(args: argparse.Namespace) -> int:
    load_dotenv()
    sql = (
        "SELECT a.id, a.title, a.cleaned_text, a.source "
        "FROM news_article a "
        "WHERE a.cleaned_text IS NOT NULL "
        "  AND a.status IN ('fetched', 'extracted')"
    )
    params: list = []
    if args.source:
        sql += " AND a.source = ?"
        params.append(args.source)
    if not args.retag:
        sql += " AND NOT EXISTS (SELECT 1 FROM news_article_country t WHERE t.article_id = a.id)"
    sql += " ORDER BY a.id ASC LIMIT ?"
    params.append(args.limit)

    n_done = 0
    n_failed = 0
    n_tags = 0
    with db.connect(db.resolve_db(args.db)) as conn:
        db.ensure_pipeline_schema(conn)
        rows = conn.execute(sql, params).fetchall()
        if not rows:
            print("(no articles need tagging in this scope)")
            return 0
        print(f"[tag] {len(rows)} article(s) to tag")
        for aid, title, cleaned, src in rows:
            try:
                result = country_tag_mod.tag(cleaned, title=title)
                added = country_tag_mod.persist(conn, aid, result)
                extract.log_usage(
                    conn,
                    model=result.model,
                    kind="country_tag",
                    article_id=aid,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cache_read_input_tokens=result.cache_read_input_tokens,
                    cache_creation_input_tokens=result.cache_creation_input_tokens,
                )
                n_done += 1
                n_tags += added
                summary = ", ".join(c for c, _ in result.countries) or "(none)"
                print(f"  #{aid:>5} [{src}] -> {summary}")
            except Exception as e:
                n_failed += 1
                print(f"  #{aid:>5} [{src}] FAILED: {type(e).__name__}: {e}")
    print(f"\n[tag] done: tagged={n_done} failed={n_failed} total_country_rows={n_tags}")
    return 0


# ---------------------------------------------------------------------------
# Re-extract
# ---------------------------------------------------------------------------


def _cmd_reextract(args: argparse.Namespace) -> int:
    load_dotenv()
    wipe_drafts = args.what in ("drafts", "both")
    wipe_tags = args.what in ("tags", "both")

    where = ["a.cleaned_text IS NOT NULL"]
    params: list = []
    if args.source:
        where.append("a.source = ?")
        params.append(args.source)
    if args.since:
        where.append("a.fetched_at >= ?")
        params.append(args.since)
    where_clause = " AND ".join(where)

    with db.connect(db.resolve_db(args.db)) as conn:
        db.ensure_pipeline_schema(conn)
        sel_sql = (
            f"SELECT a.id, a.title, a.cleaned_text, a.url, a.source "
            f"FROM news_article a WHERE {where_clause} "
            f"ORDER BY a.id ASC LIMIT ?"
        )
        rows = conn.execute(sel_sql, params + [args.limit]).fetchall()
        if not rows:
            print("(no articles in scope)")
            return 0

        article_ids = [r[0] for r in rows]
        print(f"[reextract] {len(article_ids)} article(s) in scope (limit={args.limit})")
        if args.dry_run:
            print(f"  would wipe: drafts={wipe_drafts} tags={wipe_tags}")
            return 0

        if wipe_drafts:
            placeholders = ",".join("?" * len(article_ids))
            conn.execute(
                f"DELETE FROM partnership_draft WHERE source_article_id IN ({placeholders})",
                article_ids,
            )
            conn.execute(
                f"UPDATE news_article SET status='fetched' "
                f"WHERE id IN ({placeholders}) AND status = 'extracted'",
                article_ids,
            )
            conn.commit()
            print(f"[reextract] wiped drafts for {len(article_ids)} article(s)")

        if wipe_tags:
            placeholders = ",".join("?" * len(article_ids))
            conn.execute(
                f"DELETE FROM news_article_country WHERE article_id IN ({placeholders})",
                article_ids,
            )
            conn.commit()
            print(f"[reextract] wiped country tags for {len(article_ids)} article(s)")

        n_extracted = 0
        n_tagged = 0
        n_failed = 0
        for aid, title, cleaned, url, src in rows:
            if wipe_drafts:
                try:
                    extraction = extract.extract_with_escalation(cleaned, title=title, url=url)
                    extract.log_usage(
                        conn,
                        model=extraction.model,
                        kind="extract",
                        article_id=aid,
                        input_tokens=extraction.usage.input_tokens,
                        output_tokens=extraction.usage.output_tokens,
                        cache_read_input_tokens=extraction.usage.cache_read_input_tokens,
                        cache_creation_input_tokens=extraction.usage.cache_creation_input_tokens,
                    )
                    drafts_mod.insert_draft(conn, article_id=aid, extraction=extraction)
                    n_extracted += 1
                    print(
                        f"  #{aid:>5} [{src}] extract: "
                        f"is_partnership={extraction.payload.get('is_partnership')} "
                        f"({extraction.payload.get('country_1')} <-> {extraction.payload.get('country_2')})"
                    )
                except Exception as e:
                    n_failed += 1
                    print(f"  #{aid:>5} [{src}] extract FAILED: {type(e).__name__}: {e}")
            if wipe_tags:
                try:
                    result = country_tag_mod.tag(cleaned, title=title)
                    added = country_tag_mod.persist(conn, aid, result)
                    extract.log_usage(
                        conn,
                        model=result.model,
                        kind="country_tag",
                        article_id=aid,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        cache_read_input_tokens=result.cache_read_input_tokens,
                        cache_creation_input_tokens=result.cache_creation_input_tokens,
                    )
                    n_tagged += added
                    summary = ", ".join(c for c, _ in result.countries) or "(none)"
                    print(f"  #{aid:>5} [{src}] tag: {summary}")
                except Exception as e:
                    n_failed += 1
                    print(f"  #{aid:>5} [{src}] tag FAILED: {type(e).__name__}: {e}")

        print(
            f"\n[reextract] done: extracted={n_extracted} tagged_rows={n_tagged} "
            f"failed={n_failed}"
        )
    return 0


# ---------------------------------------------------------------------------
# Cost reporting
# ---------------------------------------------------------------------------


def _cmd_digest(args: argparse.Namespace) -> int:
    from .. import notify
    text = notify.daily_digest(db_arg=args.db)
    print(text)
    if args.post:
        ok = notify.post(text)
        print("[notify] webhook:", "ok" if ok else "skipped/failed")
    return 0


def _cmd_cost_alarm(args: argparse.Namespace) -> int:
    from .. import notify
    usd, msg = notify.cost_check(
        cap_usd=args.cap_usd, window_hours=args.hours, db_arg=args.db,
    )
    print(f"[cost-alarm] last {args.hours}h spend ≈ ${usd:.2f}  (cap ${args.cap_usd:.2f})")
    if msg:
        print(msg)
        if args.post:
            notify.post(msg)
        return 1  # non-zero so cron / GH Actions can react
    return 0


def _cmd_source_health(args: argparse.Namespace) -> int:
    from .. import notify
    stale = notify.stale_sources(threshold_days=args.threshold_days, db_arg=args.db)
    if not stale:
        print(f"[source-health] all sources active in the last {args.threshold_days}d ✓")
        return 0
    msg_lines = [f"⚠ space-monitor source-health: {len(stale)} stale source(s) (>{args.threshold_days}d silent)"]
    for src, days in stale:
        msg_lines.append(f"  · {src} — {days}d")
    msg = "\n".join(msg_lines)
    print(msg)
    if args.post:
        notify.post(msg)
    return 1


def _cmd_cost(args: argparse.Namespace) -> int:
    sql = (
        "SELECT model, kind, "
        "  COUNT(*), "
        "  SUM(input_tokens), SUM(output_tokens), "
        "  SUM(cache_read_input_tokens), SUM(cache_creation_input_tokens) "
        "FROM extraction_usage"
    )
    params: list = []
    if args.since:
        sql += " WHERE recorded_at >= ?"
        params.append(args.since)
    sql += " GROUP BY model, kind ORDER BY model, kind"
    with db.connect(db.resolve_db(args.db)) as conn:
        db.ensure_pipeline_schema(conn)
        rows = conn.execute(sql, params).fetchall()
    if not rows:
        print("(no usage rows recorded yet)")
        return 0
    print(f"{'model':<24} {'kind':<14} {'calls':>8} {'in':>10} {'out':>8} {'cache_r':>10} {'cache_w':>10}")
    print("-" * 90)
    for model, kind, calls, ti, to, cr, cw in rows:
        print(
            f"{model:<24} {kind:<14} {calls:>8,} {ti or 0:>10,} {to or 0:>8,} "
            f"{cr or 0:>10,} {cw or 0:>10,}"
        )
    return 0
