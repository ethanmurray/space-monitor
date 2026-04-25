"""Country-briefing generator.

Pulls everything we know about a country (articles, partnerships, contracts,
leadership changes) over a recency window, hands it to Claude Sonnet with a
templated prompt, and returns a markdown briefing.

The briefing is the actual product — every other piece of plumbing exists
to make this artifact accurate and current. It's structured for a reader
about to walk into a meeting:

    State of play       — one-paragraph orientation
    Recent activity     — bulleted timeline of last N days
    Key actors          — orgs / companies / people surfaced repeatedly
    Active partnerships — bilateral and multilateral, sorted by recency
    Contracts           — last N days
    Leadership changes  — last N days
    Open questions      — gaps the analyst should fill in person

Cached in ``country_briefing`` keyed by (country, ISO-week) so re-asks
within a week return the same artifact for free. Pass ``force=True`` to
bypass the cache after fresh data lands.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any

import anthropic

from . import db, taxonomy

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4000


def _client() -> anthropic.Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Either export it or put it in .env."
        )
    return anthropic.Anthropic(max_retries=4, timeout=120.0)


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------


@dataclass
class BriefingInputs:
    country: str
    since: str  # ISO date
    articles: list[dict[str, Any]] = field(default_factory=list)
    partnerships: list[dict[str, Any]] = field(default_factory=list)
    contracts: list[dict[str, Any]] = field(default_factory=list)
    leadership_changes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_thin(self) -> bool:
        """Briefings with no source material aren't worth running an LLM call
        on — return a stub instead."""
        return not (
            self.articles or self.partnerships
            or self.contracts or self.leadership_changes
        )


def gather(country: str, *, since_days: int = 90, db_arg: str | None = None) -> BriefingInputs:
    """Pull the structured signal set for one country over a recency window."""
    since = (dt.date.today() - dt.timedelta(days=since_days)).isoformat()
    target = db.resolve_db(db_arg)
    inputs = BriefingInputs(country=country, since=since)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)

        # Articles tagged with the country (any centrality), within window.
        article_rows = conn.execute(
            """
            SELECT a.id, a.title, a.url, a.source, a.published_at,
                   a.cleaned_text,
                   t.centrality
              FROM news_article a
              JOIN news_article_country t ON t.article_id = a.id
             WHERE t.country = ?
               AND COALESCE(a.published_at, a.fetched_at) >= ?
               AND a.status IN ('extracted', 'fetched')
             ORDER BY COALESCE(a.published_at, a.fetched_at) DESC
             LIMIT 60
            """,
            (country, since),
        ).fetchall()
        for r in article_rows:
            text = (r[5] or "")[:1200]
            inputs.articles.append({
                "id": r[0], "title": r[1], "url": r[2], "source": r[3],
                "published_at": r[4], "centrality": r[6],
                "snippet": text,
            })

        # Partnerships involving the country (live table, not drafts — these
        # are the analyst-approved ones).
        part_rows = _maybe(conn, """
            SELECT partnership_id, description, partnership_year,
                   partnership_type, country_1, country_2,
                   organization_1, organization_2, company_1, company_2,
                   primary_mission
              FROM partnership
             WHERE country_1 = ? OR country_2 = ?
             ORDER BY COALESCE(partnership_year, 0) DESC
             LIMIT 30
        """, (country, country))
        for r in part_rows:
            inputs.partnerships.append(_dict_from_row(r, [
                "partnership_id", "description", "partnership_year",
                "partnership_type", "country_1", "country_2",
                "organization_1", "organization_2", "company_1", "company_2",
                "primary_mission",
            ]))

        # Contracts where either side is the country, within window.
        contract_rows = _maybe(conn, """
            SELECT id, description, contract_year, value_musd,
                   customer, customer_country, contractor, contractor_country,
                   primary_mission
              FROM contract_draft
             WHERE (customer_country = ? OR contractor_country = ?)
               AND extracted_at >= ?
             ORDER BY id DESC LIMIT 25
        """, (country, country, since))
        for r in contract_rows:
            inputs.contracts.append(_dict_from_row(r, [
                "id", "description", "contract_year", "value_musd",
                "customer", "customer_country", "contractor", "contractor_country",
                "primary_mission",
            ]))

        # Leadership changes with country = country, within window.
        ldr_rows = _maybe(conn, """
            SELECT id, description, change_year, person_name, organization,
                   country, new_role, prior_role, change_kind
              FROM leadership_change_draft
             WHERE country = ? AND extracted_at >= ?
             ORDER BY id DESC LIMIT 25
        """, (country, since))
        for r in ldr_rows:
            inputs.leadership_changes.append(_dict_from_row(r, [
                "id", "description", "change_year", "person_name", "organization",
                "country", "new_role", "prior_role", "change_kind",
            ]))

    return inputs


def _maybe(conn: sqlite3.Connection, sql: str, params: tuple) -> list:
    """Run a query, return rows. Returns [] if the table doesn't exist (older
    DBs that pre-date the multi-signal layer)."""
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return []


def _dict_from_row(row: tuple, columns: list[str]) -> dict[str, Any]:
    return {col: val for col, val in zip(columns, row)}


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def _build_prompt(inputs: BriefingInputs) -> str:
    return (
        f"You are writing an analytical briefing on **{inputs.country}**'s space "
        f"sector for a senior leader walking into a meeting in {inputs.country}. "
        f"The structured records below are everything our system has pulled from "
        f"news sources since {inputs.since}.\n\n"
        f"Output requirements:\n"
        f"- Markdown.\n"
        f"- Sections: ## State of play (1 paragraph), ## Recent activity "
        f"(bulleted, dated), ## Key actors (named orgs/companies/people, with "
        f"a one-line role each), ## Active partnerships (bulleted: who-with-whom + "
        f"year + one line on what), ## Contracts (bulleted, with USD values when "
        f"known), ## Leadership changes (bulleted), ## Open questions (3-5 "
        f"things the briefing-recipient should ask in person — gaps in our data "
        f"that a face-to-face conversation could fill).\n"
        f"- Be specific. Use names from the records, not generic placeholders.\n"
        f"- Don't invent. If a section has no records, write a single sentence "
        f"explaining the gap (e.g. 'No leadership changes recorded in this window').\n"
        f"- Cite article URLs inline as [domain](url) where you reference a "
        f"specific event.\n\n"
        f"STRUCTURED RECORDS:\n\n"
        f"```json\n{json.dumps(_records_payload(inputs), ensure_ascii=False, indent=2)}\n```"
    )


def _records_payload(inputs: BriefingInputs) -> dict[str, Any]:
    return {
        "country": inputs.country,
        "since": inputs.since,
        "article_count": len(inputs.articles),
        "articles": inputs.articles,
        "partnerships": inputs.partnerships,
        "contracts": inputs.contracts,
        "leadership_changes": inputs.leadership_changes,
    }


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _iso_week_key(d: dt.date | None = None) -> str:
    d = d or dt.date.today()
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _cached(conn: sqlite3.Connection, country: str, week: str) -> str | None:
    try:
        row = conn.execute(
            "SELECT body_markdown FROM country_briefing "
            "WHERE country = ? AND iso_week = ?",
            (country, week),
        ).fetchone()
    except Exception:
        return None
    return row[0] if row else None


def _store(
    conn: sqlite3.Connection, country: str, week: str, body: str,
    *, since_days: int, model: str, articles: int,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO country_briefing "
        "(country, iso_week, body_markdown, since_days, model, articles, generated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (country, week, body, since_days, model, articles,
         dt.datetime.now(dt.timezone.utc).isoformat()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


@dataclass
class Briefing:
    country: str
    since: str
    body_markdown: str
    article_count: int
    from_cache: bool


def generate(
    country: str,
    *,
    since_days: int = 90,
    force: bool = False,
    db_arg: str | None = None,
) -> Briefing:
    """Generate (or fetch from cache) a country briefing."""
    target = db.resolve_db(db_arg)
    week = _iso_week_key()
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        if not force:
            cached = _cached(conn, country, week)
            if cached:
                return Briefing(
                    country=country,
                    since=(dt.date.today() - dt.timedelta(days=since_days)).isoformat(),
                    body_markdown=cached, article_count=0, from_cache=True,
                )

    inputs = gather(country, since_days=since_days, db_arg=db_arg)

    if inputs.is_thin:
        body = (
            f"# {country} — space-sector briefing\n\n"
            f"_No structured records found in the last {since_days} days._\n\n"
            f"This usually means: (a) no covered source published space-domain "
            f"news mentioning **{country}** in this window, or (b) the country "
            f"name doesn't match our canonical taxonomy. Try a longer window "
            f"with `--since-days 180` or `--since-days 365`."
        )
    else:
        client = _client()
        prompt = _build_prompt(inputs)
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        body = next((b.text for b in response.content if b.type == "text"), "")
        if not body:
            raise RuntimeError("briefing model returned empty body")

    with db.connect(target) as conn:
        _store(conn, country, week, body,
               since_days=since_days, model=MODEL, articles=len(inputs.articles))

    return Briefing(
        country=country, since=inputs.since,
        body_markdown=body, article_count=len(inputs.articles), from_cache=False,
    )


# ---------------------------------------------------------------------------
# Choices for the UI / CLI
# ---------------------------------------------------------------------------


def known_countries(db_arg: str | None = None) -> list[str]:
    """Countries that have at least one tagged article. Sorted by recent activity."""
    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        rows = conn.execute(
            "SELECT country, COUNT(*) AS n FROM news_article_country "
            "GROUP BY country ORDER BY n DESC"
        ).fetchall()
    if rows:
        return [r[0] for r in rows]
    # Fallback: just emit the canonical taxonomy.
    return [c.name for c in taxonomy.load().countries]
