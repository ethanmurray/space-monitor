"""Shared RSS/Atom adapter. Subclass and set ``name``, ``domain``, and
``feed_url`` — that's the whole adapter for any source whose feed plays nice
with feedparser."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

import feedparser

from .base import CandidateArticle


USER_AGENT = "space-monitor/0.1 (+research; contact: ops@example.com)"


class RSSSource:
    name: str = ""
    domain: str = ""
    feed_url: str = ""
    prefilter_required: bool = False
    # When True, --source all skips this adapter. Explicit --source <name>
    # still runs it (so disabled adapters can be tested individually).
    disabled: bool = False

    def iter_candidates(self, limit: int | None = None) -> Iterator[CandidateArticle]:
        parsed = feedparser.parse(self.feed_url, agent=USER_AGENT)
        for i, entry in enumerate(parsed.entries):
            if limit is not None and i >= limit:
                return
            published = None
            if getattr(entry, "published_parsed", None):
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
            elif getattr(entry, "updated_parsed", None):
                published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc).isoformat()
            link = getattr(entry, "link", None)
            if not link:
                continue
            yield CandidateArticle(
                source=self.name,
                url=link,
                title=getattr(entry, "title", None),
                published_at=published,
            )
