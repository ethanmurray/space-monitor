"""Shared RSS/Atom adapter. Subclass and set ``name``, ``domain``, and
``feed_url`` — that's the whole adapter for any source whose feed plays nice
with feedparser.

Fetches via httpx (browser UA, redirect following, certifi-backed SSL)
instead of letting feedparser do its own urllib request. This is more
permissive — sites that block feedparser's default UA or have SSL cert
chains that urllib's bundled CA doesn't trust (asc-csa.gc.ca was the
trigger) work transparently here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

import feedparser
import httpx

from .base import CandidateArticle


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
TIMEOUT_SECS = 20


class RSSSource:
    name: str = ""
    domain: str = ""
    feed_url: str = ""
    prefilter_required: bool = False
    # When True, --source all skips this adapter. Explicit --source <name>
    # still runs it (so disabled adapters can be tested individually).
    disabled: bool = False

    def _fetch_feed_text(self) -> str | None:
        try:
            with httpx.Client(
                timeout=TIMEOUT_SECS,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                resp = client.get(self.feed_url)
                resp.raise_for_status()
                return resp.text
        except Exception:
            return None

    def iter_candidates(self, limit: int | None = None) -> Iterator[CandidateArticle]:
        text = self._fetch_feed_text()
        if not text:
            return
        parsed = feedparser.parse(text)
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
