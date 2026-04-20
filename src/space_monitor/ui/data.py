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
    cleaned_text_en: str | None   # cached translation; None until first request
    draft: dict[str, Any] | None  # partnership_draft row as dict, or None


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
    return FullArticle(
        id=article_row[0],
        source=article_row[1],
        url=article_row[2],
        title=article_row[3],
        published_at=article_row[4],
        cleaned_text=article_row[5],
        cleaned_text_en=article_row[6],
        draft=dict(zip(draft_cols, draft_row)) if draft_row else None,
    )


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
