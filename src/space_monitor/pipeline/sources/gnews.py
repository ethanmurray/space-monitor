"""Google News RSS-search adapter — discovery surface beyond known feeds.

Fans out across the queries configured in :mod:`.gnews_queries` (English
topics + localized topic queries + per-country watchlist queries). Each
query is one HTTP GET to ``news.google.com/rss/search``; results dedup
against the existing ``news_article.url_hash`` so already-seen articles
don't re-extract.

No API key required. Free for the search itself; cost lives in the
downstream extraction (capped per ingest run via ``--max-extractions``).

Notes:

* Google News links are opaque ``news.google.com/rss/articles/<token>``
  redirects to the original publisher. We decode them at iter time using
  ``googlenewsdecoder`` so the rest of the pipeline sees real publisher
  URLs (proper dedup, proper source-domain attribution, trafilatura sees
  real article HTML).
* The decoder makes one HTTP call per article. It self-rate-limits via the
  ``interval`` argument; we use 1s.
* RSS entry titles arrive as ``"Article title - Publisher Name"``. Kept
  as-is — analysts see publisher at a glance, LLM extractor doesn't care.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import urlencode

import feedparser
from googlenewsdecoder import gnewsdecoder

from .base import CandidateArticle
from .gnews_queries import ALL_QUERIES, Query


_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
_DECODE_INTERVAL_SECS = 1


def _query_url(q: Query) -> str:
    params = {"q": q.q, "hl": q.hl, "gl": q.gl, "ceid": q.ceid}
    return f"https://news.google.com/rss/search?{urlencode(params)}"


def _decode(url: str) -> str | None:
    """Decode a news.google.com redirect to the original publisher URL.

    Returns None if decoding fails (decoder error, rate limit, removed
    article). Caller should skip the candidate in that case."""
    try:
        result = gnewsdecoder(url, interval=_DECODE_INTERVAL_SECS)
    except Exception:
        return None
    if not isinstance(result, dict) or not result.get("status"):
        return None
    return result.get("decoded_url")


class GNewsSource:
    name = "gnews"
    domain = "news.google.com"
    # Google News surfaces a long tail across many publishers — most
    # individual results need the prefilter to drop the off-topic ones.
    prefilter_required = True
    disabled = False

    def __init__(self, queries: list[Query] | None = None):
        self.queries = queries if queries is not None else ALL_QUERIES

    def iter_candidates(self, limit: int | None = None) -> Iterator[CandidateArticle]:
        emitted = 0
        seen_urls: set[str] = set()
        for q in self.queries:
            url = _query_url(q)
            parsed = feedparser.parse(url, agent=_USER_AGENT)
            for entry in parsed.entries:
                gnews_url = getattr(entry, "link", None)
                if not gnews_url:
                    continue
                real_url = _decode(gnews_url)
                if not real_url or real_url in seen_urls:
                    continue
                seen_urls.add(real_url)
                published = None
                if getattr(entry, "published_parsed", None):
                    published = datetime(
                        *entry.published_parsed[:6], tzinfo=timezone.utc
                    ).isoformat()
                yield CandidateArticle(
                    source=self.name,
                    url=real_url,
                    title=getattr(entry, "title", None),
                    published_at=published,
                )
                emitted += 1
                if limit is not None and emitted >= limit:
                    return
