"""Persist extraction results as ``partnership_draft`` rows + helpers for the
review CLI: list pending, show one, approve (promote to ``partnership``),
reject. Also performs a lightweight duplicate check at promotion time.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .extract import ExtractionResult


# Columns we mirror from the partnership schema. Kept in sync with db.py.
_DRAFT_PARTNERSHIP_COLUMNS = (
    "description",
    "primary_mission",
    "sub_mission",
    "partnership_year",
    "partnership_type",
    "level_of_commitment",
    "relationship_type",
    "business_model",
    "mission_type",
    "asset_name",
    "country_1",
    "org_type_1",
    "organization_1",
    "company_1",
    "country_2",
    "org_type_2",
    "organization_2",
    "company_2",
)


def insert_draft(
    conn: sqlite3.Connection,
    *,
    article_id: int,
    extraction: ExtractionResult,
) -> int:
    """Insert the extracted payload as a pending partnership_draft row."""
    payload = extraction.payload
    now = datetime.now(timezone.utc).isoformat()

    # Look up an existing partnership that might be the same one — match on
    # (country_1, country_2, partnership_year). A loose check; the analyst
    # makes the final call.
    duplicate_of = _find_possible_duplicate(conn, payload)

    cols = [
        "source_article_id",
        "draft_status",
        "extracted_at",
        "extractor_model",
        "confidence",
        "possible_duplicate_of",
    ] + list(_DRAFT_PARTNERSHIP_COLUMNS)
    placeholders = ", ".join(["?"] * len(cols))

    values: list[Any] = [
        article_id,
        "pending",
        now,
        extraction.model,
        payload.get("confidence"),
        duplicate_of,
    ]
    for col in _DRAFT_PARTNERSHIP_COLUMNS:
        values.append(payload.get(col))

    cur = conn.execute(
        f"INSERT INTO partnership_draft ({', '.join(cols)}) VALUES ({placeholders})",
        values,
    )
    conn.execute(
        "UPDATE news_article SET status='extracted' WHERE id=?", (article_id,)
    )
    conn.commit()
    return cur.lastrowid


def _find_possible_duplicate(conn: sqlite3.Connection, payload: dict[str, Any]) -> str | None:
    c1, c2, year = payload.get("country_1"), payload.get("country_2"), payload.get("partnership_year")
    if not c1 or not c2:
        return None
    row = conn.execute(
        """
        SELECT partnership_id FROM partnership
         WHERE ((country_1 = ? AND country_2 = ?) OR (country_1 = ? AND country_2 = ?))
           AND (? IS NULL OR partnership_year = ?)
         LIMIT 1
        """,
        (c1, c2, c2, c1, year, year),
    ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Review operations
# ---------------------------------------------------------------------------


@dataclass
class DraftSummary:
    id: int
    confidence: str | None
    extracted_at: str
    country_1: str | None
    country_2: str | None
    partnership_type: str | None
    description: str | None
    article_url: str
    article_title: str | None
    possible_duplicate_of: str | None


def list_pending(conn: sqlite3.Connection, limit: int = 50) -> list[DraftSummary]:
    rows = conn.execute(
        """
        SELECT d.id, d.confidence, d.extracted_at, d.country_1, d.country_2,
               d.partnership_type, d.description, a.url, a.title,
               d.possible_duplicate_of
          FROM partnership_draft d
          JOIN news_article a ON a.id = d.source_article_id
         WHERE d.draft_status = 'pending'
         ORDER BY d.extracted_at DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [DraftSummary(*r) for r in rows]


def show(conn: sqlite3.Connection, draft_id: int) -> dict[str, Any] | None:
    """Return the draft + source article as a dict for display."""
    cur = conn.execute(
        """
        SELECT d.*, a.url AS article_url, a.title AS article_title,
               a.cleaned_text AS article_text
          FROM partnership_draft d
          JOIN news_article a ON a.id = d.source_article_id
         WHERE d.id = ?
        """,
        (draft_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    cols = [c[0] for c in cur.description]
    return dict(zip(cols, row))


def approve(
    conn: sqlite3.Connection,
    draft_id: int,
    *,
    reviewer: str,
    notes: str | None = None,
    edits: dict[str, Any] | None = None,
) -> str:
    """Promote a draft into the live ``partnership`` table. Returns the new
    partnership_id. Edits is a mapping of column -> new value to apply before
    promotion."""
    draft = conn.execute(
        "SELECT * FROM partnership_draft WHERE id = ?", (draft_id,)
    ).fetchone()
    if not draft:
        raise ValueError(f"draft {draft_id} not found")
    cols = [c[0] for c in conn.execute("SELECT * FROM partnership_draft LIMIT 0").description]
    d = dict(zip(cols, draft))

    if d["draft_status"] != "pending":
        raise ValueError(f"draft {draft_id} is {d['draft_status']}, not pending")

    if edits:
        for k, v in edits.items():
            if k not in _DRAFT_PARTNERSHIP_COLUMNS:
                raise ValueError(f"cannot edit {k!r} via approval — not a partnership field")
            d[k] = v

    pid = _generate_partnership_id(d)

    conn.execute(
        """
        INSERT INTO partnership (
            partnership_id, description,
            primary_mission, sub_mission, partnership_year,
            partnership_type, level_of_commitment, relationship_type,
            business_model, mission_type, asset_name,
            country_1, org_type_1, organization_1, company_1,
            country_2, org_type_2, organization_2, company_2,
            link_1, analyst, new_or_legacy
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pid, d["description"],
            d["primary_mission"], d["sub_mission"], d["partnership_year"],
            d["partnership_type"], d["level_of_commitment"], d["relationship_type"],
            d["business_model"], d["mission_type"], d["asset_name"],
            d["country_1"], d["org_type_1"], d["organization_1"], d["company_1"],
            d["country_2"], d["org_type_2"], d["organization_2"], d["company_2"],
            _article_url(conn, d["source_article_id"]),
            reviewer, "Pipeline",
        ),
    )
    conn.execute(
        """
        UPDATE partnership_draft
           SET draft_status='approved', reviewer=?, review_notes=?,
               promoted_partnership_id=?
         WHERE id=?
        """,
        (reviewer, notes, pid, draft_id),
    )
    conn.commit()
    return pid


def reject(
    conn: sqlite3.Connection, draft_id: int, *, reviewer: str, reason: str
) -> None:
    conn.execute(
        """
        UPDATE partnership_draft
           SET draft_status='rejected', reviewer=?, review_notes=?
         WHERE id=? AND draft_status='pending'
        """,
        (reviewer, reason, draft_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_partnership_id(d: dict[str, Any]) -> str:
    """Produce a partnership_id roughly matching the workbook's convention:
    `<Org/Country 1>-<Org/Country 2> <Type> <Year>` plus a 4-char nonce to
    guarantee uniqueness."""
    p1 = d.get("organization_1") or d.get("company_1") or d.get("country_1") or "Party1"
    p2 = d.get("organization_2") or d.get("company_2") or d.get("country_2") or "Party2"
    typ = d.get("partnership_type") or "Partnership"
    year = d.get("partnership_year") or ""
    nonce = secrets.token_hex(2)
    return f"{p1}-{p2} {typ} {year} [{nonce}]".strip()


def _article_url(conn: sqlite3.Connection, article_id: int) -> str | None:
    row = conn.execute("SELECT url FROM news_article WHERE id=?", (article_id,)).fetchone()
    return row[0] if row else None


def render_summary(d: DraftSummary) -> str:
    """One-line text rendering for ``review list``."""
    pair = f"{d.country_1 or '?'} <-> {d.country_2 or '?'}"
    desc = (d.description or "")[:80]
    dup = f" [DUP? {d.possible_duplicate_of}]" if d.possible_duplicate_of else ""
    return f"#{d.id:>4}  {d.confidence or '?':<6}  {pair:<35}  {d.partnership_type or '?':<25}  {desc}{dup}"


def render_full(d: dict[str, Any]) -> str:
    """Full draft + source article side-by-side for ``review show``."""
    out = []
    out.append("=" * 80)
    out.append(f"DRAFT #{d['id']}  ({d['draft_status']}, confidence={d['confidence']})")
    out.append("=" * 80)
    out.append(f"Extracted at:  {d['extracted_at']}  by  {d['extractor_model']}")
    if d.get("possible_duplicate_of"):
        out.append(f"⚠ Possible duplicate of existing partnership: {d['possible_duplicate_of']}")
    out.append("")
    out.append("EXTRACTED FIELDS")
    out.append("-" * 80)
    for col in _DRAFT_PARTNERSHIP_COLUMNS:
        v = d.get(col)
        if v is not None:
            out.append(f"  {col:<22} {v}")
    out.append("")
    out.append("SOURCE ARTICLE")
    out.append("-" * 80)
    out.append(f"  Title: {d.get('article_title')}")
    out.append(f"  URL:   {d.get('article_url')}")
    out.append("")
    text = (d.get("article_text") or "")[:2000]
    out.append(text)
    if d.get("article_text") and len(d["article_text"]) > 2000:
        out.append("\n[... truncated, full body in news_article.cleaned_text ...]")
    return "\n".join(out)
