"""Article-cluster dedup.

One real-world partnership often appears across N articles (NASA press
release → SpaceNews recap → SpaceX statement → Reuters wire → industry
blog). The pipeline produces N draft rows, all describing the same event,
forcing the analyst to triage the same partnership N times.

This module groups drafts by a stable cluster key so the UI and CLI can
present "1 partnership, 5 articles" instead of N redundant rows. Pure
read-side computation — no schema changes needed (the cluster key is
derived, not stored).

Cluster key: (sorted country pair, partnership_year, partnership_type) —
loose enough to catch the same event written up by different outlets,
strict enough to avoid collapsing distinct partnerships in the same
country pair / year.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from . import db


@dataclass
class DraftCluster:
    key: tuple[str | None, str | None, int | None, str | None]
    drafts: list[dict[str, Any]]            # newest-first

    @property
    def size(self) -> int:
        return len(self.drafts)

    @property
    def countries(self) -> str:
        c1, c2, *_ = self.key
        return f"{c1 or '?'} ↔ {c2 or '?'}"

    @property
    def year(self) -> int | None:
        return self.key[2]

    @property
    def representative(self) -> dict[str, Any]:
        """Pick the highest-confidence newest draft as the cluster's face."""
        ranking = {"high": 0, "medium": 1, "low": 2, None: 3}
        return sorted(
            self.drafts,
            key=lambda d: (ranking.get(d.get("confidence"), 4), -d["id"]),
        )[0]

    @property
    def article_urls(self) -> list[str]:
        seen: set[str] = set()
        out = []
        for d in self.drafts:
            url = d.get("article_url")
            if url and url not in seen:
                seen.add(url)
                out.append(url)
        return out


def cluster_pending(
    *,
    limit: int = 200,
    db_arg: str | None = None,
) -> list[DraftCluster]:
    """Return pending partnership_drafts grouped by cluster key. Newest-active
    cluster first (sorted by max draft id within the cluster)."""
    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        rows = conn.execute(
            """
            SELECT d.id, d.country_1, d.country_2, d.partnership_year,
                   d.partnership_type, d.confidence, d.description,
                   d.organization_1, d.organization_2,
                   d.company_1, d.company_2,
                   a.url AS article_url, a.title AS article_title,
                   a.source AS article_source
              FROM partnership_draft d
              JOIN news_article a ON a.id = d.source_article_id
             WHERE d.draft_status = 'pending'
             ORDER BY d.id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    cols = [
        "id", "country_1", "country_2", "partnership_year", "partnership_type",
        "confidence", "description", "organization_1", "organization_2",
        "company_1", "company_2",
        "article_url", "article_title", "article_source",
    ]
    drafts = [dict(zip(cols, r)) for r in rows]

    grouped: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for d in drafts:
        grouped[_key(d)].append(d)

    clusters = [DraftCluster(key=k, drafts=v) for k, v in grouped.items()]
    clusters.sort(key=lambda c: -max(d["id"] for d in c.drafts))
    return clusters


def _key(draft: dict[str, Any]) -> tuple:
    """The dedup key. Country pair is order-insensitive (sorted)."""
    c1 = draft.get("country_1")
    c2 = draft.get("country_2")
    pair = tuple(sorted((c1 or "", c2 or "")))
    return (pair[0] or None, pair[1] or None,
            draft.get("partnership_year"),
            draft.get("partnership_type"))
