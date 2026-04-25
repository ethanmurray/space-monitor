"""Data layer for the Streamlit UI.

Three concerns:

1. **Source registry** — load ``data/sources.yaml`` (the hand-curated
   catalogue of every source we've ever considered) and expose it as a list
   of typed dicts.
2. **Per-source live stats** — query the news_article + partnership_draft
   tables for things like article counts by recency window, % positive
   drafts, # pending review under a confidence threshold.
3. **Article + draft accessors** — single-source queries the UI needs.

All queries go through :func:`db.connect` which routes to either local
SQLite or the configured Turso DB based on env / args.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib import resources
from typing import Any

import yaml

from space_monitor import db


# ---------------------------------------------------------------------------
# Registry (sources.yaml)
# ---------------------------------------------------------------------------


@dataclass
class SourceEntry:
    name: str
    domain: str
    status: str
    type: str
    description: str
    comment: str
    coverage_focus: str | None = None
    prefilter_required: bool | None = None
    workbook_tally: int | None = None


def load_registry() -> list[SourceEntry]:
    raw = yaml.safe_load(
        resources.files("space_monitor").joinpath("data", "sources.yaml").read_text()
    )
    return [
        SourceEntry(
            name=s["name"],
            domain=s.get("domain", ""),
            status=s["status"],
            type=s["type"],
            description=s.get("description", ""),
            comment=s.get("comment", ""),
            coverage_focus=s.get("coverage_focus"),
            prefilter_required=s.get("prefilter_required"),
            workbook_tally=s.get("workbook_tally"),
        )
        for s in raw["sources"]
    ]


# ---------------------------------------------------------------------------
# Stats (DB)
# ---------------------------------------------------------------------------


@dataclass
class SourceStats:
    """Live counts for one source. All fields default to 0 / None when no
    matching rows exist (source registered but nothing fetched yet)."""

    source: str
    total_articles: int = 0
    last_24h: int = 0
    last_7d: int = 0
    last_30d: int = 0
    oldest_published: str | None = None
    newest_published: str | None = None
    failed: int = 0
    skipped_prefilter: int = 0
    total_drafts: int = 0
    positive_drafts: int = 0
    pending_low_med_confidence: int = 0

    @property
    def relevance_pct(self) -> float | None:
        """Fraction of extracted articles tagged as a partnership."""
        if self.total_drafts == 0:
            return None
        return 100 * self.positive_drafts / self.total_drafts


def fetch_all_stats(db_arg: str | None = None) -> dict[str, SourceStats]:
    """Single round-trip to compute per-source stats. Returns dict keyed by
    source name. Missing keys mean no articles ever ingested from that source."""
    target = db.resolve_db(db_arg)
    out: dict[str, SourceStats] = {}
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        # Article-level stats
        rows = conn.execute(
            """
            SELECT
                source,
                COUNT(*)                                                 AS total,
                SUM(CASE WHEN fetched_at > ? THEN 1 ELSE 0 END)          AS last_24h,
                SUM(CASE WHEN fetched_at > ? THEN 1 ELSE 0 END)          AS last_7d,
                SUM(CASE WHEN fetched_at > ? THEN 1 ELSE 0 END)          AS last_30d,
                MIN(published_at)                                        AS oldest,
                MAX(published_at)                                        AS newest,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)       AS failed,
                SUM(CASE WHEN status = 'skipped_prefilter' THEN 1 ELSE 0 END)
                                                                          AS skipped_pre
            FROM news_article
            GROUP BY source
            """,
            (
                _iso_ago(hours=24),
                _iso_ago(days=7),
                _iso_ago(days=30),
            ),
        ).fetchall()
        for src, total, l24, l7, l30, oldest, newest, failed, skipped_pre in rows:
            out[src] = SourceStats(
                source=src,
                total_articles=total or 0,
                last_24h=l24 or 0,
                last_7d=l7 or 0,
                last_30d=l30 or 0,
                oldest_published=oldest,
                newest_published=newest,
                failed=failed or 0,
                skipped_prefilter=skipped_pre or 0,
            )

        # Draft-level stats (joined back to source via news_article)
        draft_rows = conn.execute(
            """
            SELECT
                a.source,
                COUNT(d.id)                                              AS total_drafts,
                SUM(CASE WHEN d.partnership_year IS NOT NULL THEN 1 ELSE 0 END)
                                                                          AS positives,
                SUM(CASE WHEN d.draft_status = 'pending'
                          AND d.confidence IN ('low', 'medium')
                         THEN 1 ELSE 0 END)                              AS pending_lm
            FROM news_article a
            JOIN partnership_draft d ON d.source_article_id = a.id
            GROUP BY a.source
            """
        ).fetchall()
        for src, total, pos, pending in draft_rows:
            stats = out.setdefault(src, SourceStats(source=src))
            stats.total_drafts = total or 0
            stats.positive_drafts = pos or 0
            stats.pending_low_med_confidence = pending or 0
    return out


# ---------------------------------------------------------------------------
# Article + draft accessors
# ---------------------------------------------------------------------------


@dataclass
class ArticleSummary:
    id: int
    url: str
    title: str | None
    published_at: str | None
    fetched_at: str
    status: str
    is_relevant: bool             # partnership_draft.partnership_year IS NOT NULL
    has_draft: bool
    confidence: str | None
    description: str | None       # short LLM summary from partnership_draft (if any)
    draft_id: int | None


def list_articles(
    source: str,
    *,
    only_relevant: bool = True,
    hide_skipped: bool = True,
    limit: int = 200,
    db_arg: str | None = None,
) -> list[ArticleSummary]:
    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        sql = """
            SELECT
                a.id, a.url, a.title, a.published_at, a.fetched_at, a.status,
                CASE WHEN d.partnership_year IS NOT NULL THEN 1 ELSE 0 END AS is_relevant,
                CASE WHEN d.id IS NOT NULL THEN 1 ELSE 0 END               AS has_draft,
                d.confidence,
                d.description,
                d.id AS draft_id
            FROM news_article a
            LEFT JOIN partnership_draft d ON d.source_article_id = a.id
            WHERE a.source = ?
        """
        params: list[Any] = [source]
        if only_relevant:
            sql += " AND d.partnership_year IS NOT NULL"
        if hide_skipped:
            sql += " AND a.status != 'skipped_prefilter'"
        sql += " ORDER BY COALESCE(a.published_at, a.fetched_at) DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    return [
        ArticleSummary(
            id=r[0], url=r[1], title=r[2], published_at=r[3], fetched_at=r[4],
            status=r[5], is_relevant=bool(r[6]), has_draft=bool(r[7]),
            confidence=r[8], description=r[9], draft_id=r[10],
        )
        for r in rows
    ]


@dataclass
class FullArticle:
    id: int
    source: str
    url: str
    title: str | None
    published_at: str | None
    cleaned_text: str | None
    cleaned_text_en: str | None        # cached translation; None until first request
    draft: dict[str, Any] | None       # partnership_draft row as dict, or None
    countries: list[tuple[str, str]] = None  # [(country, centrality)]
    contracts: list[dict[str, Any]] = None
    leadership_changes: list[dict[str, Any]] = None


def get_article(article_id: int, db_arg: str | None = None) -> FullArticle | None:
    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        article_row = conn.execute(
            "SELECT id, source, url, title, published_at, cleaned_text, cleaned_text_en "
            "FROM news_article WHERE id = ?",
            (article_id,),
        ).fetchone()
        if not article_row:
            return None
        # Most-recent draft for this article (in case of re-extraction)
        cur = conn.execute(
            "SELECT * FROM partnership_draft "
            "WHERE source_article_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (article_id,),
        )
        draft_row = cur.fetchone()
        draft_cols = [c[0] for c in cur.description] if draft_row else []

        country_rows = conn.execute(
            "SELECT country, centrality FROM news_article_country "
            "WHERE article_id = ? "
            "ORDER BY CASE centrality WHEN 'central' THEN 0 ELSE 1 END, country",
            (article_id,),
        ).fetchall()
        countries = [(r[0], r[1]) for r in country_rows]

        contracts = _fetch_signal_drafts(conn, "contract_draft", article_id)
        leadership = _fetch_signal_drafts(conn, "leadership_change_draft", article_id)

    return FullArticle(
        id=article_row[0],
        source=article_row[1],
        url=article_row[2],
        title=article_row[3],
        published_at=article_row[4],
        cleaned_text=article_row[5],
        cleaned_text_en=article_row[6],
        draft=dict(zip(draft_cols, draft_row)) if draft_row else None,
        countries=countries,
        contracts=contracts,
        leadership_changes=leadership,
    )


def _fetch_signal_drafts(conn, table: str, article_id: int) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"SELECT * FROM {table} WHERE source_article_id = ? ORDER BY id DESC",
        (article_id,),
    )
    rows = cur.fetchall()
    if not rows:
        return []
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in rows]


_EDITABLE_DRAFT_FIELDS = (
    "partnership_year",
    "partnership_type",
    "level_of_commitment",
    "relationship_type",
    "business_model",
    "mission_type",
    "primary_mission",
    "country_1",
    "org_type_1",
    "organization_1",
    "company_1",
    "country_2",
    "org_type_2",
    "organization_2",
    "company_2",
)


def save_draft_edits(
    draft_id: int,
    edits: dict[str, Any],
    db_arg: str | None = None,
) -> None:
    """Update the listed columns on a pending partnership_draft. Silently
    skips any keys not in :data:`_EDITABLE_DRAFT_FIELDS` — the form is the
    contract and we don't want a mistyped widget key to overwrite something
    sensitive (like draft_status)."""
    safe = {k: v for k, v in edits.items() if k in _EDITABLE_DRAFT_FIELDS}
    if not safe:
        return
    target = db.resolve_db(db_arg)
    set_clause = ", ".join(f"{k} = ?" for k in safe)
    params = list(safe.values()) + [draft_id]
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        conn.execute(
            f"UPDATE partnership_draft SET {set_clause} WHERE id = ?",
            params,
        )
        conn.commit()


def next_pending_draft_id(
    after_draft_id: int,
    *,
    same_source_only: bool = True,
    db_arg: str | None = None,
) -> int | None:
    """Return the ID of the next pending draft after ``after_draft_id``.

    If ``same_source_only`` (the default), restrict to the same source as
    the just-acted-on draft so a reviewer can batch-process one source
    cleanly. Returns None when the queue is empty for the scope.
    """
    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        if same_source_only:
            row = conn.execute(
                """
                SELECT d.id
                  FROM partnership_draft d
                  JOIN news_article a ON a.id = d.source_article_id
                 WHERE d.draft_status = 'pending'
                   AND a.source = (
                         SELECT a2.source
                           FROM partnership_draft d2
                           JOIN news_article a2 ON a2.id = d2.source_article_id
                          WHERE d2.id = ?)
                   AND d.id != ?
                 ORDER BY d.id ASC
                 LIMIT 1
                """,
                (after_draft_id, after_draft_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM partnership_draft "
                "WHERE draft_status = 'pending' AND id != ? "
                "ORDER BY id ASC LIMIT 1",
                (after_draft_id,),
            ).fetchone()
    return row[0] if row else None


def article_id_for_draft(draft_id: int, db_arg: str | None = None) -> int | None:
    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        row = conn.execute(
            "SELECT source_article_id FROM partnership_draft WHERE id = ?",
            (draft_id,),
        ).fetchone()
    return row[0] if row else None


def approve_draft(
    draft_id: int,
    *,
    reviewer: str,
    notes: str | None = None,
    db_arg: str | None = None,
) -> str:
    """Promote a draft to the live partnership table. Thin wrapper around
    :func:`pipeline.drafts.approve` that opens its own connection."""
    from ..pipeline import drafts

    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        return drafts.approve(conn, draft_id, reviewer=reviewer, notes=notes)


def reject_draft(
    draft_id: int,
    *,
    reviewer: str,
    reason: str,
    db_arg: str | None = None,
) -> None:
    from ..pipeline import drafts

    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        drafts.reject(conn, draft_id, reviewer=reviewer, reason=reason)


def bulk_reject(
    draft_ids: list[int], *, reviewer: str, reason: str, db_arg: str | None = None,
) -> int:
    """Reject N pending drafts in one round-trip. Returns # rejected."""
    if not draft_ids:
        return 0
    from ..pipeline import drafts as drafts_mod

    target = db.resolve_db(db_arg)
    n = 0
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        for did in draft_ids:
            drafts_mod.reject(conn, did, reviewer=reviewer, reason=reason)
            n += 1
    return n


_EDITABLE_CONTRACT_FIELDS = (
    "description", "contract_year", "value_musd",
    "customer", "customer_country", "contractor", "contractor_country",
    "primary_mission", "mission_type",
)
_EDITABLE_LEADERSHIP_FIELDS = (
    "description", "change_year", "person_name", "organization",
    "country", "new_role", "prior_role", "change_kind",
)


def save_signal_draft_edits(
    kind: str,
    draft_id: int,
    edits: dict[str, Any],
    db_arg: str | None = None,
) -> None:
    """Write edits to a contract_draft / leadership_change_draft row."""
    table, allow = {
        "contract": ("contract_draft", _EDITABLE_CONTRACT_FIELDS),
        "leadership_change": ("leadership_change_draft", _EDITABLE_LEADERSHIP_FIELDS),
    }[kind]
    safe = {k: v for k, v in edits.items() if k in allow}
    if not safe:
        return
    target = db.resolve_db(db_arg)
    set_clause = ", ".join(f"{k} = ?" for k in safe)
    params = list(safe.values()) + [draft_id]
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        conn.execute(f"UPDATE {table} SET {set_clause} WHERE id = ?", params)
        conn.commit()


def approve_signal_draft(
    kind: str, draft_id: int, *, reviewer: str, db_arg: str | None = None,
) -> str:
    from ..pipeline import signals
    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        if kind == "contract":
            return signals.approve_contract(conn, draft_id, reviewer=reviewer)
        if kind == "leadership_change":
            return signals.approve_leadership(conn, draft_id, reviewer=reviewer)
        raise ValueError(f"unknown kind {kind!r}")


def reject_signal_draft(
    kind: str, draft_id: int, *, reviewer: str, reason: str, db_arg: str | None = None,
) -> None:
    from ..pipeline import signals
    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        signals.reject_signal_draft(
            conn, draft_id, kind=kind, reviewer=reviewer, reason=reason,
        )


def bulk_approve_high_confidence(
    draft_ids: list[int], *, reviewer: str, db_arg: str | None = None,
) -> tuple[int, list[str]]:
    """Approve only the drafts in the input list whose confidence='high' AND
    status='pending'. Returns (n_approved, errors)."""
    if not draft_ids:
        return (0, [])
    from ..pipeline import drafts as drafts_mod

    target = db.resolve_db(db_arg)
    n = 0
    errors: list[str] = []
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        placeholders = ",".join(["?"] * len(draft_ids))
        eligible = conn.execute(
            f"SELECT id FROM partnership_draft WHERE id IN ({placeholders}) "
            f" AND draft_status = 'pending' AND confidence = 'high'",
            draft_ids,
        ).fetchall()
        for (did,) in eligible:
            try:
                drafts_mod.approve(
                    conn, did, reviewer=reviewer, notes="Bulk-approved (high confidence)",
                )
                n += 1
            except Exception as e:
                errors.append(f"#{did}: {e}")
    return (n, errors)


def translate_and_cache(
    article_id: int,
    source_text: str,
    db_arg: str | None = None,
) -> str:
    """Translate to English and persist back to news_article.cleaned_text_en.

    Re-checks the DB first in case another viewer requested it concurrently.
    The Streamlit UI calls this from the article-review page; the cached
    translation is then visible to every subsequent viewer (including future
    sessions and other users) without re-paying the LLM cost.
    """
    from . import translate as translate_mod

    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        existing = conn.execute(
            "SELECT cleaned_text_en FROM news_article WHERE id = ?",
            (article_id,),
        ).fetchone()
        if existing and existing[0]:
            return existing[0]

        # Cache miss — call the LLM and write the result.
        translated = translate_mod.translate_to_english(source_text)
        conn.execute(
            "UPDATE news_article SET cleaned_text_en = ? WHERE id = ?",
            (translated, article_id),
        )
        conn.commit()
        return translated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_ago(*, hours: int = 0, days: int = 0) -> str:
    delta = timedelta(hours=hours, days=days)
    return (datetime.now(timezone.utc) - delta).isoformat()


# ---------------------------------------------------------------------------
# Dashboard rollups
# ---------------------------------------------------------------------------


@dataclass
class TrendingCountry:
    country: str
    article_count: int
    central_count: int


def trending_countries(
    *,
    days: int = 7,
    limit: int = 10,
    db_arg: str | None = None,
) -> list[TrendingCountry]:
    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        try:
            rows = conn.execute(
                """
                SELECT t.country,
                       COUNT(*)                                                AS n,
                       SUM(CASE WHEN t.centrality = 'central' THEN 1 ELSE 0 END) AS central_n
                  FROM news_article_country t
                  JOIN news_article a ON a.id = t.article_id
                 WHERE COALESCE(a.published_at, a.fetched_at) >= ?
                 GROUP BY t.country
                 ORDER BY n DESC, central_n DESC
                 LIMIT ?
                """,
                (_iso_ago(days=days), limit),
            ).fetchall()
        except Exception:
            return []
    return [TrendingCountry(c, n or 0, cn or 0) for c, n, cn in rows]


@dataclass
class PendingHighlight:
    draft_id: int
    confidence: str | None
    countries: str
    description: str | None
    article_source: str
    article_url: str
    extracted_at: str


def pending_highlights(*, limit: int = 12, db_arg: str | None = None) -> list[PendingHighlight]:
    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        rows = conn.execute(
            """
            SELECT d.id, d.confidence, d.country_1, d.country_2, d.description,
                   a.source, a.url, d.extracted_at
              FROM partnership_draft d
              JOIN news_article a ON a.id = d.source_article_id
             WHERE d.draft_status = 'pending'
             ORDER BY CASE d.confidence
                          WHEN 'high'   THEN 0
                          WHEN 'medium' THEN 1
                          WHEN 'low'    THEN 2
                          ELSE 3 END,
                      d.extracted_at DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        PendingHighlight(
            draft_id=r[0], confidence=r[1],
            countries=f"{r[2] or '?'} ↔ {r[3] or '?'}",
            description=r[4], article_source=r[5], article_url=r[6],
            extracted_at=r[7],
        ) for r in rows
    ]


@dataclass
class SourceHealth:
    source: str
    last_fetch: str | None
    days_silent: int | None
    last_24h: int

    @property
    def is_stale(self) -> bool:
        return self.days_silent is not None and self.days_silent > 14


def source_health(db_arg: str | None = None) -> list[SourceHealth]:
    target = db.resolve_db(db_arg)
    out: list[SourceHealth] = []
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        rows = conn.execute(
            """
            SELECT source, MAX(fetched_at) AS last_fetch,
                   SUM(CASE WHEN fetched_at > ? THEN 1 ELSE 0 END) AS last_24h
              FROM news_article
             GROUP BY source
             ORDER BY last_fetch DESC
            """,
            (_iso_ago(hours=24),),
        ).fetchall()
    now = datetime.now(timezone.utc)
    for src, last_fetch, last_24h in rows:
        if last_fetch:
            try:
                lf = datetime.fromisoformat(last_fetch)
                if lf.tzinfo is None:
                    lf = lf.replace(tzinfo=timezone.utc)
                days_silent = (now - lf).days
            except Exception:
                days_silent = None
        else:
            days_silent = None
        out.append(SourceHealth(
            source=src, last_fetch=last_fetch,
            days_silent=days_silent, last_24h=last_24h or 0,
        ))
    return out


@dataclass
class CostMonth:
    total_input_tokens: int
    total_output_tokens: int
    total_cache_read: int
    total_cache_write: int
    total_calls: int


def cost_this_month(db_arg: str | None = None) -> CostMonth:
    target = db.resolve_db(db_arg)
    first_of_month = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        try:
            row = conn.execute(
                """
                SELECT COUNT(*),
                       SUM(input_tokens), SUM(output_tokens),
                       SUM(cache_read_input_tokens),
                       SUM(cache_creation_input_tokens)
                  FROM extraction_usage
                 WHERE recorded_at >= ?
                """,
                (first_of_month,),
            ).fetchone()
        except Exception:
            return CostMonth(0, 0, 0, 0, 0)
    if not row or not row[0]:
        return CostMonth(0, 0, 0, 0, 0)
    return CostMonth(
        total_calls=row[0] or 0,
        total_input_tokens=row[1] or 0,
        total_output_tokens=row[2] or 0,
        total_cache_read=row[3] or 0,
        total_cache_write=row[4] or 0,
    )


def cost_to_usd(c: CostMonth) -> float:
    """Rough month-to-date dollar estimate.

    Mostly Haiku 4.5 (extract + country_tag + signal_router + signal_*).
    Haiku 4.5 list price (as of build): ~$1.00/M input, $5.00/M output,
    cache reads $0.10/M, cache writes $1.25/M. Sonnet escalations are <5%
    of calls — folded into Haiku rates for a rough estimate. Treat the
    output as a sanity-check, not an invoice."""
    return (
        c.total_input_tokens * 1.00 / 1_000_000
        + c.total_output_tokens * 5.00 / 1_000_000
        + c.total_cache_read * 0.10 / 1_000_000
        + c.total_cache_write * 1.25 / 1_000_000
    )
