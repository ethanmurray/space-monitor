"""Tag a fetched article with the countries it discusses, independent of the
partnership extraction.

This is the foundation for per-country queries — "show me everything about
Vietnam in the last 90 days" doesn't need an article to be a partnership; it
just needs to be *about* Vietnam. We run one cheap Haiku call per article
that returns a small list of (country, centrality) tuples and persist them
to ``news_article_country``.

The country list is sourced from the canonical taxonomy so that downstream
joins against ``country`` always succeed.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import anthropic

from .. import taxonomy

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 600  # Compact JSON list — even a 10-country article fits.
ARTICLE_CHARS_CAP = 12000  # Don't blow the input budget on huge articles.


def _build_schema() -> dict[str, Any]:
    countries = [c.name for c in taxonomy.load().countries]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["countries"],
        "properties": {
            "countries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["country", "centrality"],
                    "properties": {
                        "country": {"type": "string", "enum": countries},
                        "centrality": {
                            "type": "string",
                            "enum": ["central", "mentioned"],
                            "description": (
                                "central: the article is primarily about this "
                                "country's space activity. mentioned: the country "
                                "appears (named partner, location, comparison) "
                                "but isn't the article's subject."
                            ),
                        },
                    },
                },
            }
        },
    }


def _build_system_prompt() -> str:
    countries = [c.name for c in taxonomy.load().countries]
    return (
        "You tag space-domain news articles with the countries they discuss.\n\n"
        "Rules:\n"
        "1. Return EVERY country mentioned in the article — partner countries, "
        "subject countries, launch-site countries, headquartered-in countries, "
        "and casually-named comparators. Empty list is fine when the article "
        "is purely about a multilateral organization with no specific country.\n"
        "2. Country names MUST come from the canonical list (the schema enforces "
        "this). Apply the usual aliases: 'USA'/'US' -> 'United States', 'UK' -> "
        "'United Kingdom', 'PRC' -> 'China', 'ROK' -> 'South Korea', 'UAE' -> "
        "'United Arab Emirates'.\n"
        "3. centrality='central' for the country (or countries) the article is "
        "PRIMARILY about; 'mentioned' for the rest. Most articles have 1-3 "
        "central countries.\n"
        "4. ESA/EUSPA/EU institutions don't get tagged with 'European Union' "
        "as a country unless the article frames the EU as the actor. Tag the "
        "specific member states involved instead.\n"
        "5. NASA/Space Force/NRO -> United States. JAXA -> Japan. ISRO -> "
        "India. CNSA -> China. ROSCOSMOS -> Russia. KARI -> South Korea. "
        "DLR -> Germany. CNES -> France. ASI -> Italy. UAESA -> United "
        "Arab Emirates.\n\n"
        "CANONICAL COUNTRY LIST:\n"
        + json.dumps(countries, ensure_ascii=False)
    )


SYSTEM_PROMPT = _build_system_prompt()
OUTPUT_SCHEMA = _build_schema()


@dataclass
class TagResult:
    countries: list[tuple[str, str]]  # (country, centrality)
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int


def _client() -> anthropic.Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Either export it in your shell or put "
            "it in a .env file at the project root (gitignored)."
        )
    return anthropic.Anthropic(max_retries=4, timeout=60.0)


def tag(article_text: str, *, title: str | None = None) -> TagResult:
    """Run a single country-tag call. Returns the parsed list + usage."""
    client = _client()
    user = []
    if title:
        user.append(f"Title: {title}")
    user.append("Article:\n" + article_text[:ARTICLE_CHARS_CAP])

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        output_config={
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
        },
        messages=[{"role": "user", "content": "\n".join(user)}],
    )

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(f"No text in country-tag response (stop={response.stop_reason})")
    payload = json.loads(text)
    pairs = [(c["country"], c["centrality"]) for c in payload.get("countries", [])]

    usage = response.usage
    return TagResult(
        countries=pairs,
        model=MODEL,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


def persist(conn: sqlite3.Connection, article_id: int, result: TagResult) -> int:
    """Replace any prior tags for this article and write the new ones.

    Returns the number of (country, centrality) rows written. Idempotent —
    re-tagging the same article is safe.
    """
    conn.execute("DELETE FROM news_article_country WHERE article_id = ?", (article_id,))
    if not result.countries:
        conn.commit()
        return 0
    now = datetime.now(timezone.utc).isoformat()
    # The output schema can't enforce uniqueness (Anthropic's validator rejects
    # uniqueItems), so the model may name the same country twice — usually once
    # 'central' and once 'mentioned'. news_article_country is keyed on
    # (article_id, country), so an un-deduped batch fails the whole insert and
    # the article ends up with no tags at all. Collapse to one row per country,
    # keeping 'central' when the model gave both.
    best: dict[str, str] = {}
    for country, centrality in result.countries:
        if best.get(country) != "central":
            best[country] = centrality
    rows = [
        (article_id, country, centrality, now, result.model)
        for country, centrality in best.items()
    ]
    conn.executemany(
        "INSERT INTO news_article_country "
        "(article_id, country, centrality, tagged_at, tagger_model) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def already_tagged(conn: sqlite3.Connection, article_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM news_article_country WHERE article_id = ? LIMIT 1",
        (article_id,),
    ).fetchone()
    return row is not None
