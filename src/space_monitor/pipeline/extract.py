"""Extract a structured PartnershipDraft from an article body via Claude.

Architecture
------------
* Model: ``claude-haiku-4-5`` — cheap enough to run on every fetched article.
* Output: forced JSON via ``output_config.format.json_schema``. Enums in the
  schema are sourced from :mod:`space_monitor.taxonomy` so the model can only
  emit valid controlled-vocabulary values.
* Caching: the system prompt (which carries the taxonomy reference) is built
  once at module import and tagged with ``cache_control={"type": "ephemeral"}``
  via top-level auto-caching. Each request prints ``cache_read_input_tokens``
  so we can verify the cache is hot.

Why structured outputs (not tool use): for pure extraction the
``messages.create(output_config=...)`` path is simpler than a tool-use round
trip — no manual loop, the response's first text block is guaranteed valid JSON
matching the schema.
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

DEFAULT_MODEL = "claude-haiku-4-5"
ESCALATION_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 2000  # Compact JSON output — partnerships fit easily.


# ---------------------------------------------------------------------------
# Schema construction (taxonomy-driven, built once at import)
# ---------------------------------------------------------------------------


def _build_output_schema() -> dict[str, Any]:
    """Return a JSON Schema mirroring the partnership_draft table, with enum
    constraints sourced from the taxonomy so the model can't invent values."""
    t = taxonomy.load()
    partnership_types = [pt.name for pt in t.partnership_types]
    levels = [lc.name for lc in t.levels_of_commitment]
    business_models = [bm.name for bm in t.business_models]
    mission_types = [mt.name for mt in t.mission_types]
    relationship_types = list(t.relationship_types)
    organization_types = list(t.organization_types)
    mission_areas = list(t.mission_areas)

    nullable = lambda inner: {"anyOf": [inner, {"type": "null"}]}
    nullable_enum = lambda values: nullable({"type": "string", "enum": values})
    nullable_str = nullable({"type": "string"})
    nullable_int = nullable({"type": "integer"})

    # API caps schemas at 16 nullable / union-typed fields. Keeping 13 here
    # leaves headroom; sub_mission / asset_name omitted (rarely populated by
    # analysts in the source workbook) and description is non-nullable so the
    # model always writes a one-sentence summary.
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "is_partnership",
            "confidence",
            "description",
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
        ],
        "properties": {
            "is_partnership": {
                "type": "boolean",
                "description": (
                    "False if the article does not describe a partnership "
                    "between two or more parties (e.g., a launch announcement, "
                    "an internal-only product release, a market analysis). "
                    "When false, leave structured fields null and write a "
                    "short note in description explaining what the article is "
                    "actually about."
                ),
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": (
                    "high = both parties + partnership type are explicit in the "
                    "article. medium = one or more fields required inference. "
                    "low = article is ambiguous or speculative."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "One concise sentence in your own words. If "
                    "is_partnership=true, summarize the partnership; "
                    "otherwise, summarize what the article is actually about."
                ),
            },
            "partnership_year": nullable_int,
            "partnership_type": nullable_enum(partnership_types),
            "level_of_commitment": nullable_enum(levels),
            "relationship_type": nullable_enum(relationship_types),
            "business_model": nullable_enum(business_models),
            "mission_type": nullable_enum(mission_types),
            "primary_mission": nullable_enum(mission_areas),
            "country_1": nullable_str,
            "org_type_1": nullable_enum(organization_types),
            "organization_1": nullable_str,
            "company_1": nullable_str,
            "country_2": nullable_str,
            "org_type_2": nullable_enum(organization_types),
            "organization_2": nullable_str,
            "company_2": nullable_str,
        },
    }


def _build_system_prompt() -> str:
    """A stable, deterministic system prompt. Built once so the prompt cache
    can serve it on every subsequent request."""
    t = taxonomy.load()
    # We don't need the whole taxonomy in the prompt — the schema enforces
    # enum membership for the structured fields. The system prompt embeds the
    # *definitions* of each scoring axis so the model knows what each value
    # means, plus the country list so it can normalize country names.
    grounding = {
        "partnership_types_with_scores": [
            {"name": pt.name, "score": pt.score} for pt in t.partnership_types
        ],
        "levels_of_commitment_with_scores": [
            {"name": lc.name, "score": lc.score} for lc in t.levels_of_commitment
        ],
        "business_models_with_scores": [
            {"name": bm.name, "score": bm.score} for bm in t.business_models
        ],
        "mission_types_with_scores": [
            {"name": mt.name, "score": mt.score} for mt in t.mission_types
        ],
        "relationship_types": list(t.relationship_types),
        "organization_types": list(t.organization_types),
        "mission_areas": list(t.mission_areas),
        "sub_missions": list(t.sub_missions),
        # The partnership_strength_lookup table grounds the strength rubric and
        # bulks the prompt past Haiku 4.5's 4096-token cache minimum so the
        # whole system prompt becomes cacheable across requests.
        "partnership_strength_lookup_examples": [
            {
                "partnership_type": s.partnership_type,
                "business_model": s.business_model,
                "mission_type": s.mission_type,
                "score": s.score,
            }
            for s in t.partnership_strength_lookup
        ],
        # Country list is intentionally exhaustive — used both as a normalization
        # reference and as cache-prefix padding.
        "countries": [c.name for c in t.countries],
    }
    normalization_examples = (
        "Common name normalizations to apply when filling country/organization fields:\n"
        "- 'NASA' is a US government agency; country=United States, org_type=Government, organization='NASA'\n"
        "- 'ESA' is the European Space Agency; country=European Union, organization='European Space Agency (ESA)'\n"
        "- 'JAXA' -> Japan, organization='Japan Aerospace Exploration Agency (JAXA)'\n"
        "- 'ISRO' -> India, organization='Indian Space Research Organisation (ISRO)'\n"
        "- 'CNSA' -> China, organization='China National Space Administration (CNSA)'\n"
        "- 'ROSCOSMOS' -> Russia, organization='ROSCOSMOS'\n"
        "- 'KARI' -> South Korea, organization='Korea Aerospace Research Institute (KARI)'\n"
        "- 'UK Space Agency' -> United Kingdom\n"
        "- 'CSA' / 'Canadian Space Agency' -> Canada\n"
        "- 'DLR' -> Germany; 'CNES' -> France; 'ASI' -> Italy; 'UAESA' -> United Arab Emirates\n"
        "- 'AFRL' / 'US Space Force' / 'USSF' / 'Space Systems Command' -> United States, org_type=Government\n"
        "- Companies: SpaceX/Blue Origin/Lockheed Martin/Boeing/Northrop Grumman/Maxar -> United States; "
        "Airbus -> France or Germany depending on division; Thales Alenia Space -> France/Italy; "
        "MELCO / Mitsubishi Electric -> Japan; OHB -> Germany.\n"
    )
    return (
        "You are a structured-data extractor for a space-domain intelligence "
        "dashboard. Given a news article, you extract ONE partnership record.\n\n"
        "Rules:\n"
        "0. SPACE DOMAIN ONLY. Only extract partnerships whose subject matter "
        "is space — civil space programs, defense space, satellite operators, "
        "launch vehicles, space science, space situational awareness, ground "
        "stations, or commercial space industry. Partnerships in unrelated "
        "domains (climate diplomacy, trade deals, defense more broadly, "
        "biology, geology, pure terrestrial tech) MUST be set "
        "is_partnership=false even if they are real partnerships.\n"
        "1. Use only values from the controlled vocabularies below. The output "
        "schema enforces this; do not invent new values.\n"
        "2. If a field cannot be determined from the article, return null for "
        "that field. Do not guess.\n"
        "3. Country names must match the canonical country list exactly. "
        "Normalize aliases (e.g., 'USA' -> 'United States', 'UK' -> 'United Kingdom').\n"
        "4. country_1/country_2 are the two parties to the partnership. If the "
        "article involves more than two countries, pick the two most central "
        "to the announcement.\n"
        "5. organization_1/2 are government agencies/ministries. company_1/2 "
        "are commercial entities. A G2G partnership has two organization_* "
        "fields filled and no company_* fields. A B2B has the reverse. A B2G "
        "has one of each.\n"
        "6. partnership_year is the year the partnership was announced or "
        "signed (not the year the article was published, if those differ).\n"
        "7. A partnership requires TWO DISTINCT PARTIES (different organizations "
        "and ideally different countries) entering into an agreement. Single-"
        "company news (a launch, a product release, an internal milestone, an "
        "earnings report) is NOT a partnership even if multiple internal "
        "divisions are mentioned. country_1 and country_2 may be the same "
        "country only when two distinct organizations within that country are "
        "the parties (e.g., NASA + a US commercial firm). If only one party is "
        "named, set is_partnership=false.\n"
        "8. If the article is not actually about a partnership announcement "
        "(e.g., it's a product launch, market analysis, or general news), set "
        "is_partnership=false and leave all structured fields null.\n"
        "9. Set confidence per the schema: high = parties+type explicit; "
        "medium = inference needed; low = ambiguous/speculative.\n\n"
        f"{normalization_examples}\n"
        "CONTROLLED VOCABULARIES (definitions and scores):\n"
        f"{json.dumps(grounding, indent=2, ensure_ascii=False)}"
    )


SYSTEM_PROMPT = _build_system_prompt()
OUTPUT_SCHEMA = _build_output_schema()


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


@dataclass
class ExtractionUsage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int


@dataclass
class ExtractionResult:
    payload: dict[str, Any]          # validated JSON matching OUTPUT_SCHEMA
    model: str
    usage: ExtractionUsage
    stop_reason: str
    escalated_from: str | None = None  # set when the result came from escalation


def _client() -> anthropic.Anthropic:
    """Construct an Anthropic client. Reads ANTHROPIC_API_KEY from env."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Either export it in your shell or put "
            "it in a .env file at the project root (gitignored)."
        )
    # SDK auto-retries 429/5xx; bump the default a bit for pipeline robustness.
    return anthropic.Anthropic(max_retries=4, timeout=60.0)


def extract(
    article_text: str,
    *,
    title: str | None = None,
    url: str | None = None,
    model: str = DEFAULT_MODEL,
) -> ExtractionResult:
    """Run a single extraction. Returns the validated JSON payload + usage."""
    client = _client()
    user_message = _format_user_message(article_text, title=title, url=url)

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        # Manual cache_control on the system block: keeps the breakpoint at
        # the end of the system prompt (stable across requests) instead of at
        # the end of the user message (which varies per article and would never
        # cache-hit). System prompt is ~4.7K tokens, well above the 4096-token
        # minimum for Haiku 4.5.
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": OUTPUT_SCHEMA,
            }
        },
        messages=[{"role": "user", "content": user_message}],
    )

    if response.stop_reason == "refusal":
        raise RuntimeError("Model refused to extract from this article.")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(f"No text block in response (stop_reason={response.stop_reason})")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Model returned non-JSON despite output_config: {e}\n\n{text[:500]}")

    usage = response.usage
    return ExtractionResult(
        payload=payload,
        model=model,
        stop_reason=response.stop_reason or "",
        usage=ExtractionUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        ),
    )


def log_usage(
    conn: sqlite3.Connection,
    *,
    model: str,
    kind: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    article_id: int | None = None,
) -> None:
    """Append a row to ``extraction_usage`` for the cost CLI to aggregate.

    Caller-side helper rather than something baked into :func:`extract` so
    other LLM steps (country_tag, prefilter, translate) can use the same
    audit table without depending on extract's signature.
    """
    conn.execute(
        "INSERT INTO extraction_usage "
        "(recorded_at, model, kind, article_id, "
        " input_tokens, output_tokens, "
        " cache_read_input_tokens, cache_creation_input_tokens) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            datetime.now(timezone.utc).isoformat(),
            model,
            kind,
            article_id,
            input_tokens,
            output_tokens,
            cache_read_input_tokens,
            cache_creation_input_tokens,
        ),
    )
    conn.commit()


_LONG_ARTICLE_INPUT_TOKENS = 3000


def _should_escalate(result: ExtractionResult) -> str | None:
    """Decide whether to escalate to Sonnet. Returns a reason string or None.

    Three triggers, in priority order:

    1. Haiku reported low confidence — the original signal.
    2. Long article (>3,000 input tokens) — Haiku tends to drop fields on
       very long bodies; Sonnet handles them more reliably and the marginal
       cost is small once the cache prefix is paid.
    3. ``is_partnership=true`` but both country_1 and country_2 are null —
       observed pattern of sloppy extraction where Haiku flags a partnership
       without identifying either party.
    """
    payload = result.payload
    if payload.get("confidence") == "low":
        return "low_confidence"
    if result.usage.input_tokens > _LONG_ARTICLE_INPUT_TOKENS:
        return "long_article"
    if (
        payload.get("is_partnership")
        and payload.get("country_1") is None
        and payload.get("country_2") is None
    ):
        return "partnership_no_countries"
    return None


def extract_with_escalation(
    article_text: str,
    *,
    title: str | None = None,
    url: str | None = None,
) -> ExtractionResult:
    """Try Haiku first; escalate to Sonnet on low confidence, long articles,
    or partnership-flagged drafts that are missing both country fields.

    Sonnet's cache is independent (different model id), so the first
    escalation pays a one-time cache write — subsequent escalations within the
    5-minute TTL hit the Sonnet cache for free. Whichever model produced the
    final draft is recorded in ``ExtractionResult.model``; if escalation ran,
    ``escalated_from`` carries the original model id.
    """
    first = extract(article_text, title=title, url=url, model=DEFAULT_MODEL)
    reason = _should_escalate(first)
    if reason is None:
        return first
    second = extract(article_text, title=title, url=url, model=ESCALATION_MODEL)
    second.escalated_from = f"{first.model} ({reason})"
    return second


def _format_user_message(article_text: str, *, title: str | None, url: str | None) -> str:
    parts = []
    if title:
        parts.append(f"Title: {title}")
    if url:
        parts.append(f"URL: {url}")
    parts.append("Article:\n")
    parts.append(article_text)
    return "\n".join(parts)
