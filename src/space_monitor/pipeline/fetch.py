"""Fetch + boilerplate-strip an article. Persists to ``news_article``.

Idempotent: a CandidateArticle whose URL hash already exists is a no-op.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
import trafilatura

from .sources.base import CandidateArticle


# A few news sites (e.g., SpaceWatch behind Cloudflare) return 403 to non-
# browser User-Agent strings. We send a recent Chrome UA for the article fetch
# only — RSS feeds use the polite UA in :mod:`._rss`.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
TIMEOUT_SECS = 20


class FetchResult:
    def __init__(self, article_id: int, status: str, was_new: bool):
        self.article_id = article_id
        self.status = status
        self.was_new = was_new


def record_skip(conn: sqlite3.Connection, candidate: CandidateArticle, reason: str) -> int:
    """Persist a prefilter skip as a news_article row without fetching the body.

    Idempotent on URL hash. The audit row makes skipped articles inspectable
    via ``space-monitor review skipped`` (and re-extractable later if the
    classifier was wrong)."""
    existing = conn.execute(
        "SELECT id FROM news_article WHERE url_hash = ?",
        (candidate.url_hash,),
    ).fetchone()
    if existing:
        return existing[0]
    now = datetime.now(timezone.utc).isoformat()
    domain = urlparse(candidate.url).netloc
    cur = conn.execute(
        """
        INSERT INTO news_article
            (url_hash, url, source, source_domain, title, published_at,
             fetched_at, status, failure_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'skipped_prefilter', ?)
        """,
        (
            candidate.url_hash,
            candidate.url,
            candidate.source,
            domain,
            candidate.title,
            candidate.published_at,
            now,
            reason[:500],
        ),
    )
    conn.commit()
    return cur.lastrowid


def fetch(conn: sqlite3.Connection, candidate: CandidateArticle) -> FetchResult:
    """Fetch the article, store cleaned body, return the news_article row id."""
    existing = conn.execute(
        "SELECT id, status FROM news_article WHERE url_hash = ?",
        (candidate.url_hash,),
    ).fetchone()
    if existing:
        return FetchResult(article_id=existing[0], status=existing[1], was_new=False)

    now = datetime.now(timezone.utc).isoformat()
    domain = urlparse(candidate.url).netloc

    # Insert a stub row first so we can tag it with status=failed if anything below blows up.
    cur = conn.execute(
        """
        INSERT INTO news_article
            (url_hash, url, source, source_domain, title, published_at, fetched_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'discovered')
        """,
        (
            candidate.url_hash,
            candidate.url,
            candidate.source,
            domain,
            candidate.title,
            candidate.published_at,
            now,
        ),
    )
    article_id = cur.lastrowid
    conn.commit()

    try:
        with httpx.Client(timeout=TIMEOUT_SECS, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
            resp = client.get(candidate.url)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        conn.execute(
            "UPDATE news_article SET status='failed', failure_reason=? WHERE id=?",
            (f"http: {type(e).__name__}: {e}"[:500], article_id),
        )
        conn.commit()
        return FetchResult(article_id=article_id, status="failed", was_new=True)

    cleaned = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    if not cleaned.strip():
        conn.execute(
            "UPDATE news_article SET status='failed', failure_reason='empty extraction' WHERE id=?",
            (article_id,),
        )
        conn.commit()
        return FetchResult(article_id=article_id, status="failed", was_new=True)

    conn.execute(
        "UPDATE news_article SET cleaned_text=?, status='fetched' WHERE id=?",
        (cleaned, article_id),
    )
    conn.commit()
    return FetchResult(article_id=article_id, status="fetched", was_new=True)
