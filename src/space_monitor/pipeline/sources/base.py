"""Source protocol + the CandidateArticle record adapters yield."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterator, Protocol


@dataclass(frozen=True)
class CandidateArticle:
    """A URL that *might* contain a partnership announcement, plus whatever
    metadata the source already knows. Fetch + extract decide whether it
    actually does."""

    source: str
    url: str
    title: str | None = None
    published_at: str | None = None  # ISO-8601 if known

    @property
    def url_hash(self) -> str:
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()


class Source(Protocol):
    """A news source adapter."""

    name: str
    domain: str
    # Set True for general-purpose feeds where most articles aren't space-related
    # (gov.uk, asianscientist). The ingest CLI runs the LLM title-relevance
    # classifier from :mod:`pipeline.prefilter` over the candidates and skips
    # the high-confidence non-space ones before paying for full extraction.
    prefilter_required: bool
    # If True, ``--source all`` skips this adapter (e.g. SpaceWatch is blocked
    # by Cloudflare from the ingest environment). Explicit ``--source <name>``
    # still runs it so issues can be re-tested.
    disabled: bool

    def iter_candidates(self, limit: int | None = None) -> Iterator[CandidateArticle]:
        """Yield candidate articles, newest first when possible."""
        ...
