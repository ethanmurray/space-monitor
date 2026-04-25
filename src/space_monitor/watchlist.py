"""Watchlist + weekly digest.

A watchlist entry is `(user, kind, value)` — e.g. (`Ethan`, `country`,
`Vietnam`) or (`Ethan`, `org`, `Airbus`). The weekly digest builds a
per-user summary of new articles / drafts / contracts / leadership
changes that match any starred slice in the user's watchlist over the
prior 7 days.

Pairs with :mod:`notify` for delivery: the digest body is plain text /
markdown ready to post to a webhook or email.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from typing import Iterable

from . import db


KINDS = ("country", "org", "partnership_type")


@dataclass
class WatchlistEntry:
    id: int
    user_name: str
    kind: str
    value: str


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def list_for(user: str, *, db_arg: str | None = None) -> list[WatchlistEntry]:
    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        rows = conn.execute(
            "SELECT id, user_name, kind, value FROM watchlist "
            "WHERE user_name = ? ORDER BY kind, value",
            (user,),
        ).fetchall()
    return [WatchlistEntry(*r) for r in rows]


def add(user: str, kind: str, value: str, *, db_arg: str | None = None) -> bool:
    """Add a watchlist entry. Idempotent — duplicates silently ignored."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {KINDS}")
    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        try:
            conn.execute(
                "INSERT INTO watchlist (user_name, kind, value) VALUES (?, ?, ?)",
                (user, kind, value),
            )
            conn.commit()
            return True
        except Exception:
            return False  # UNIQUE violation


def remove(entry_id: int, *, db_arg: str | None = None) -> None:
    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        conn.execute("DELETE FROM watchlist WHERE id = ?", (entry_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------


def build_digest(
    user: str,
    *,
    days: int = 7,
    db_arg: str | None = None,
) -> str:
    """Build a markdown digest of activity in the user's watchlist over the
    last ``days`` days. Returns empty string when the watchlist is empty."""
    entries = list_for(user, db_arg=db_arg)
    if not entries:
        return ""
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    target = db.resolve_db(db_arg)
    parts: list[str] = [
        f"# {user}'s watchlist digest — last {days} days\n",
    ]
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        for entry in entries:
            section = _section_for_entry(conn, entry, since)
            if section:
                parts.append(section)
    if len(parts) == 1:
        parts.append("_No new activity matching your watchlist this period._")
    return "\n\n".join(parts)


def _section_for_entry(conn: sqlite3.Connection, entry: WatchlistEntry, since: str) -> str:
    """Render one watchlist entry's matching activity as a markdown block."""
    lines = [f"## {entry.kind}: **{entry.value}**"]

    if entry.kind == "country":
        article_rows = conn.execute(
            """
            SELECT a.title, a.url, a.source, a.published_at, t.centrality
              FROM news_article a
              JOIN news_article_country t ON t.article_id = a.id
             WHERE t.country = ? AND COALESCE(a.published_at, a.fetched_at) >= ?
             ORDER BY COALESCE(a.published_at, a.fetched_at) DESC
             LIMIT 15
            """,
            (entry.value, since),
        ).fetchall()
        if article_rows:
            lines.append(f"### Articles ({len(article_rows)})")
            for title, url, source, pub, cen in article_rows:
                star = " ★" if cen == "central" else ""
                lines.append(f"- [{title}]({url}) — _{source}_, {(pub or '')[:10]}{star}")

        partnership_rows = _maybe(conn, """
            SELECT description, partnership_year, country_1, country_2,
                   organization_1, organization_2
              FROM partnership_draft
             WHERE (country_1 = ? OR country_2 = ?) AND extracted_at >= ?
             ORDER BY id DESC LIMIT 15
        """, (entry.value, entry.value, since))
        if partnership_rows:
            lines.append(f"### New partnership drafts ({len(partnership_rows)})")
            for desc, year, c1, c2, o1, o2 in partnership_rows:
                lines.append(f"- {c1} ↔ {c2} ({year}) — {desc or ''}")

    elif entry.kind == "org":
        rows = _maybe(conn, """
            SELECT description, partnership_year, country_1, country_2,
                   organization_1, organization_2, company_1, company_2
              FROM partnership_draft
             WHERE (organization_1 = ? OR organization_2 = ? OR
                    company_1 = ? OR company_2 = ?)
               AND extracted_at >= ?
             ORDER BY id DESC LIMIT 20
        """, (entry.value, entry.value, entry.value, entry.value, since))
        if rows:
            lines.append(f"### New drafts mentioning {entry.value} ({len(rows)})")
            for desc, year, c1, c2, o1, o2, m1, m2 in rows:
                p1 = o1 or m1 or "?"
                p2 = o2 or m2 or "?"
                lines.append(f"- {p1} ↔ {p2} ({year}) — {desc or ''}")

    elif entry.kind == "partnership_type":
        rows = _maybe(conn, """
            SELECT description, partnership_year, country_1, country_2
              FROM partnership_draft
             WHERE partnership_type = ? AND extracted_at >= ?
             ORDER BY id DESC LIMIT 20
        """, (entry.value, since))
        if rows:
            lines.append(f"### New {entry.value} partnerships ({len(rows)})")
            for desc, year, c1, c2 in rows:
                lines.append(f"- {c1} ↔ {c2} ({year}) — {desc or ''}")

    if len(lines) == 1:
        return ""  # nothing matched
    return "\n".join(lines)


def _maybe(conn: sqlite3.Connection, sql: str, params: tuple) -> list:
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return []
