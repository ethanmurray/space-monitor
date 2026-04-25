"""Multi-signal extraction.

The original pipeline only produced ``partnership_draft`` rows: every article
either became a partnership candidate or was discarded. That throws away
contracts, leadership moves, and other newsworthy signals that are right
there in the same articles we already paid to fetch.

This module adds two new signal types alongside partnership:

* ``contract``           — agency / company X awards / wins contract Y
* ``leadership_change``  — person X named to / leaves role Y at org Z

The flow:

1. :func:`route` — one Haiku call per article that returns the set of
   signal kinds present (multi-label). Cheap because it doesn't extract
   structured fields, just classifies.
2. :func:`extract_contract` / :func:`extract_leadership_change` — a typed
   structured-output call per detected kind. Same prompt-cache and
   Sonnet-escalation patterns as :mod:`extract`.
3. :func:`persist_*` — write the result to the matching ``*_draft`` table
   and record the signal in ``article_signal``.

Partnership stays in :mod:`drafts` — its schema is taxonomy-heavy and
already battle-tested. The router just emits ``partnership`` as one of the
signal kinds; the existing extractor handles it.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import anthropic

from .. import taxonomy
from . import extract as extract_mod

ROUTER_MODEL = "claude-haiku-4-5"
EXTRACT_MODEL = "claude-haiku-4-5"
ESCALATION_MODEL = "claude-sonnet-4-6"
ARTICLE_CHARS_CAP = 16000

SIGNAL_KINDS = ("partnership", "contract", "leadership_change")


def _nullable(inner: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [inner, {"type": "null"}]}


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


_ROUTER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["signals", "rationale"],
    "properties": {
        "signals": {
            "type": "array",
            "items": {"type": "string", "enum": list(SIGNAL_KINDS)},
        },
        "rationale": {
            "type": "string",
            "description": "One sentence explaining the classification.",
        },
    },
}

_ROUTER_SYSTEM = (
    "You classify space-domain news articles by which structured signals they "
    "contain. An article can contain MORE THAN ONE signal — return all that "
    "apply. Empty list is fine when an article is purely market commentary, "
    "an explainer, or unrelated to space.\n\n"
    "Signal definitions:\n"
    "- partnership: two or more named parties enter into an agreement, joint "
    "  venture, MoU, framework, or treaty. The parties matter.\n"
    "- contract: a buyer (gov agency, prime contractor, satellite operator) "
    "  awards a contract to a vendor, OR a vendor announces winning a contract. "
    "  Distinct from partnership: contracts have a buyer/seller asymmetry, "
    "  partnerships are between peers.\n"
    "- leadership_change: a named person is appointed to, promoted into, or "
    "  departs from a named role at a space-domain organization. Earnings "
    "  calls and quoted spokespeople are NOT leadership changes.\n\n"
    "Subject must be space-related (civil space, defense space, satellites, "
    "launch, ground stations, space science, SSA, commercial space). Articles "
    "in unrelated domains (terrestrial defense, climate diplomacy, biology) "
    "return an empty list even if they technically describe a partnership / "
    "contract / leadership change."
)


@dataclass
class RouterResult:
    signals: list[str]
    rationale: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int


def route(article_text: str, *, title: str | None = None) -> RouterResult:
    """Classify an article into one or more signal kinds."""
    client = _client()
    user = []
    if title:
        user.append(f"Title: {title}")
    user.append("Article:\n" + article_text[:ARTICLE_CHARS_CAP])
    response = client.messages.create(
        model=ROUTER_MODEL,
        max_tokens=400,
        system=[{
            "type": "text",
            "text": _ROUTER_SYSTEM,
            "cache_control": {"type": "ephemeral"},
        }],
        output_config={"format": {"type": "json_schema", "schema": _ROUTER_SCHEMA}},
        messages=[{"role": "user", "content": "\n".join(user)}],
    )
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError("router returned no text")
    payload = json.loads(text)
    usage = response.usage
    return RouterResult(
        signals=list(payload.get("signals", [])),
        rationale=payload.get("rationale", ""),
        model=ROUTER_MODEL,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


# ---------------------------------------------------------------------------
# Contract extractor
# ---------------------------------------------------------------------------


def _build_contract_schema() -> dict[str, Any]:
    t = taxonomy.load()
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "confidence", "description",
            "contract_year", "value_musd",
            "customer", "customer_country",
            "contractor", "contractor_country",
            "primary_mission", "mission_type",
        ],
        "properties": {
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "description": {"type": "string"},
            "contract_year": _nullable({"type": "integer"}),
            "value_musd": _nullable({"type": "number"}),
            "customer": _nullable({"type": "string"}),
            "customer_country": _nullable({"type": "string"}),
            "contractor": _nullable({"type": "string"}),
            "contractor_country": _nullable({"type": "string"}),
            "primary_mission": _nullable({
                "type": "string", "enum": list(t.mission_areas),
            }),
            "mission_type": _nullable({
                "type": "string", "enum": [m.name for m in t.mission_types],
            }),
        },
    }


_CONTRACT_SYSTEM = (
    "You extract a single contract record from a space-domain news article.\n\n"
    "A contract has a buyer (customer) and a seller (contractor). They are "
    "DIFFERENT organizations.\n\n"
    "Rules:\n"
    "1. value_musd is the contract value in MILLIONS USD, converted from any "
    "currency mentioned. Null if not stated.\n"
    "2. contract_year is the year the contract was awarded / signed. Null if "
    "unclear.\n"
    "3. customer is the buying entity (often a government agency or prime "
    "contractor). contractor is the awarded entity (typically a company).\n"
    "4. Use canonical country names: 'USA'->'United States', 'UK'->'United "
    "Kingdom'.\n"
    "5. confidence: high if value+parties+year all explicit; medium if any "
    "field required inference; low if speculative or ambiguous.\n"
    "6. Set value_musd=null rather than guessing — analysts can't fix a "
    "wrong number, but they can look up a missing one."
)

CONTRACT_SCHEMA = _build_contract_schema()


# ---------------------------------------------------------------------------
# Leadership-change extractor
# ---------------------------------------------------------------------------


_LEADERSHIP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "confidence", "description",
        "change_year", "person_name", "organization",
        "country", "new_role", "prior_role", "change_kind",
    ],
    "properties": {
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "description": {"type": "string"},
        "change_year": _nullable({"type": "integer"}),
        "person_name": {"type": "string"},  # required — without a name there's no signal
        "organization": _nullable({"type": "string"}),
        "country": _nullable({"type": "string"}),
        "new_role": _nullable({"type": "string"}),
        "prior_role": _nullable({"type": "string"}),
        "change_kind": _nullable({
            "type": "string",
            "enum": ["appointment", "departure", "promotion", "resignation", "other"],
        }),
    },
}

_LEADERSHIP_SYSTEM = (
    "You extract a single leadership-change record from a space-domain news "
    "article.\n\n"
    "Rules:\n"
    "1. person_name is REQUIRED — if no specific person is named, the article "
    "isn't actually a leadership change. Set is_partnership-equivalent low "
    "confidence and write a note in description.\n"
    "2. change_kind: appointment (new to org), promotion (move up within org), "
    "departure (leaves), resignation (specifically resigned), other.\n"
    "3. organization should be the org name as commonly used (e.g., 'NASA', "
    "'SpaceX', 'European Space Agency').\n"
    "4. country uses canonical names; null if multinational.\n"
    "5. new_role / prior_role are the title strings as written in the article."
)


# ---------------------------------------------------------------------------
# Public extraction entry points
# ---------------------------------------------------------------------------


@dataclass
class SignalExtractionResult:
    kind: str
    payload: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    escalated_from: str | None = None


def extract_contract(
    article_text: str, *, title: str | None = None, url: str | None = None,
) -> SignalExtractionResult:
    return _extract_signal(
        kind="contract",
        article_text=article_text,
        title=title, url=url,
        system=_CONTRACT_SYSTEM,
        schema=CONTRACT_SCHEMA,
    )


def extract_leadership_change(
    article_text: str, *, title: str | None = None, url: str | None = None,
) -> SignalExtractionResult:
    return _extract_signal(
        kind="leadership_change",
        article_text=article_text,
        title=title, url=url,
        system=_LEADERSHIP_SYSTEM,
        schema=_LEADERSHIP_SCHEMA,
    )


def _extract_signal(
    *,
    kind: str,
    article_text: str,
    title: str | None,
    url: str | None,
    system: str,
    schema: dict[str, Any],
) -> SignalExtractionResult:
    primary = _call(kind, article_text, title, url, system, schema, EXTRACT_MODEL)
    if primary.payload.get("confidence") != "low":
        return primary
    secondary = _call(kind, article_text, title, url, system, schema, ESCALATION_MODEL)
    secondary.escalated_from = primary.model
    return secondary


def _call(
    kind: str,
    article_text: str,
    title: str | None,
    url: str | None,
    system: str,
    schema: dict[str, Any],
    model: str,
) -> SignalExtractionResult:
    client = _client()
    parts = []
    if title:
        parts.append(f"Title: {title}")
    if url:
        parts.append(f"URL: {url}")
    parts.append("Article:\n" + article_text[:ARTICLE_CHARS_CAP])

    response = client.messages.create(
        model=model,
        max_tokens=1500,
        system=[{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }],
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": "\n".join(parts)}],
    )
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(f"{kind} extractor returned no text")
    payload = json.loads(text)
    usage = response.usage
    return SignalExtractionResult(
        kind=kind,
        payload=payload,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


_CONTRACT_FIELDS = (
    "description", "contract_year", "value_musd",
    "customer", "customer_country",
    "contractor", "contractor_country",
    "primary_mission", "mission_type",
)

_LEADERSHIP_FIELDS = (
    "description", "change_year",
    "person_name", "organization", "country",
    "new_role", "prior_role", "change_kind",
)


def persist_contract(
    conn: sqlite3.Connection, article_id: int, result: SignalExtractionResult,
) -> int:
    return _persist(
        conn=conn,
        article_id=article_id,
        result=result,
        table="contract_draft",
        fields=_CONTRACT_FIELDS,
    )


def persist_leadership(
    conn: sqlite3.Connection, article_id: int, result: SignalExtractionResult,
) -> int:
    if not result.payload.get("person_name"):
        # Required field missing — skip insert rather than fail at the DB.
        return 0
    return _persist(
        conn=conn,
        article_id=article_id,
        result=result,
        table="leadership_change_draft",
        fields=_LEADERSHIP_FIELDS,
    )


def _persist(
    *,
    conn: sqlite3.Connection,
    article_id: int,
    result: SignalExtractionResult,
    table: str,
    fields: tuple[str, ...],
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    cols = [
        "source_article_id", "draft_status", "extracted_at",
        "extractor_model", "confidence",
    ] + list(fields)
    placeholders = ", ".join(["?"] * len(cols))
    values: list[Any] = [
        article_id, "pending", now,
        result.model, result.payload.get("confidence"),
    ]
    for col in fields:
        values.append(result.payload.get(col))
    cur = conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
        values,
    )
    record_signal(conn, article_id, result.kind)
    conn.commit()
    return cur.lastrowid


def record_signal(conn: sqlite3.Connection, article_id: int, kind: str) -> None:
    """Add (article_id, kind) to article_signal. Idempotent."""
    conn.execute(
        "INSERT OR IGNORE INTO article_signal (article_id, signal_kind, tagged_at) "
        "VALUES (?, ?, ?)",
        (article_id, kind, datetime.now(timezone.utc).isoformat()),
    )


def has_signal(conn: sqlite3.Connection, article_id: int, kind: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM article_signal WHERE article_id = ? AND signal_kind = ? LIMIT 1",
        (article_id, kind),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Review actions for non-partnership signals
# ---------------------------------------------------------------------------


_CONTRACT_LIVE_FIELDS = (
    "description", "contract_year", "value_musd",
    "customer", "customer_country", "contractor", "contractor_country",
    "primary_mission", "mission_type",
)
_LEADERSHIP_LIVE_FIELDS = (
    "description", "change_year", "person_name", "organization",
    "country", "new_role", "prior_role", "change_kind",
)


def approve_contract(
    conn: sqlite3.Connection,
    draft_id: int,
    *,
    reviewer: str,
    edits: dict | None = None,
) -> str:
    """Promote a contract_draft to the live ``contract`` table. Returns
    the new contract_id."""
    return _approve_signal(
        conn, draft_id, reviewer=reviewer, edits=edits,
        draft_table="contract_draft",
        live_table="contract",
        live_id_col="contract_id",
        promoted_col="promoted_contract_id",
        fields=_CONTRACT_LIVE_FIELDS,
        id_factory=_contract_id,
    )


def approve_leadership(
    conn: sqlite3.Connection,
    draft_id: int,
    *,
    reviewer: str,
    edits: dict | None = None,
) -> str:
    return _approve_signal(
        conn, draft_id, reviewer=reviewer, edits=edits,
        draft_table="leadership_change_draft",
        live_table="leadership_change",
        live_id_col="leadership_id",
        promoted_col="promoted_leadership_id",
        fields=_LEADERSHIP_LIVE_FIELDS,
        id_factory=_leadership_id,
    )


def reject_signal_draft(
    conn: sqlite3.Connection,
    draft_id: int,
    *,
    kind: str,
    reviewer: str,
    reason: str,
) -> None:
    """Mark a contract or leadership-change draft as rejected."""
    table = {
        "contract": "contract_draft",
        "leadership_change": "leadership_change_draft",
    }[kind]
    conn.execute(
        f"UPDATE {table} SET draft_status='rejected', reviewer=?, review_notes=? "
        f"WHERE id=? AND draft_status='pending'",
        (reviewer, reason, draft_id),
    )
    conn.commit()


def _approve_signal(
    conn: sqlite3.Connection,
    draft_id: int,
    *,
    reviewer: str,
    edits: dict | None,
    draft_table: str,
    live_table: str,
    live_id_col: str,
    promoted_col: str,
    fields: tuple[str, ...],
    id_factory,
) -> str:
    cur = conn.execute(f"SELECT * FROM {draft_table} WHERE id = ?", (draft_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"{draft_table} #{draft_id} not found")
    cols = [c[0] for c in cur.description]
    d = dict(zip(cols, row))
    if d["draft_status"] != "pending":
        raise ValueError(f"{draft_table} #{draft_id} is {d['draft_status']!r}, not pending")

    if edits:
        for k, v in edits.items():
            if k in fields:
                d[k] = v

    live_id = id_factory(d, conn)
    insert_cols = [live_id_col, "source_url", "analyst", *fields]
    placeholders = ", ".join(["?"] * len(insert_cols))
    src_url_row = conn.execute(
        "SELECT url FROM news_article WHERE id = ?", (d["source_article_id"],),
    ).fetchone()
    src_url = src_url_row[0] if src_url_row else None
    values = [live_id, src_url, reviewer] + [d.get(f) for f in fields]
    conn.execute(
        f"INSERT INTO {live_table} ({', '.join(insert_cols)}) VALUES ({placeholders})",
        values,
    )
    conn.execute(
        f"UPDATE {draft_table} SET draft_status='approved', reviewer=?, "
        f"{promoted_col}=? WHERE id=?",
        (reviewer, live_id, draft_id),
    )
    conn.commit()
    return live_id


def _slug(value: str | None, fallback: str, max_len: int = 24) -> str:
    import re
    if not value:
        return fallback
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", value)
    return cleaned[:max_len] or fallback


def _contract_id(d: dict, conn: sqlite3.Connection) -> str:
    cust = _slug(d.get("customer"), "Customer")
    vendor = _slug(d.get("contractor"), "Contractor")
    year = str(d.get("contract_year") or "")
    base = f"{cust}-{vendor}_Contract"
    if year:
        base = f"{base}_{year}"
    return _next_unique(conn, "contract", "contract_id", base)


def _leadership_id(d: dict, conn: sqlite3.Connection) -> str:
    person = _slug(d.get("person_name"), "Person")
    org = _slug(d.get("organization"), "Org")
    year = str(d.get("change_year") or "")
    base = f"{person}_{org}"
    if year:
        base = f"{base}_{year}"
    return _next_unique(conn, "leadership_change", "leadership_id", base)


def _next_unique(conn: sqlite3.Connection, table: str, col: str, base: str) -> str:
    for suffix in ("",) + tuple(f"_{n}" for n in range(2, 100)):
        candidate = base + suffix
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE {col} = ? LIMIT 1", (candidate,),
        ).fetchone()
        if not row:
            return candidate
    import secrets as _secrets
    return f"{base}_{_secrets.token_hex(3)}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client() -> anthropic.Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Either export it in your shell or put "
            "it in a .env file at the project root (gitignored)."
        )
    return anthropic.Anthropic(max_retries=4, timeout=60.0)
