"""European Commission DG for Defence Industry and Space (DG DEFIS) news scraper.

DG DEFIS publishes press releases at
https://defence-industry-space.ec.europa.eu/newsroom/latest-news_en (no RSS).
Article URLs follow the pattern ``/<topic-slug>-YYYY-MM-DD_en`` — the date is
embedded directly in the slug, so no inline HTML date parsing is needed.
~10 articles per index page, 37 pages of history (~370 articles).

ToS posture: standard Drupal robots.txt — only ``/admin``, ``/search``,
``/user/*`` etc. are disallowed; the news section is fully permitted. Site is
the official European Commission DG publishing public communications.

This DG covers EU defence-industrial policy AND the EU space programme, so
nearly every article touches on a partnership, grant, or programmatic
cooperation — high signal source.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterator

import httpx

from .base import CandidateArticle


_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

# Article URLs end with -YYYY-MM-DD_en — date is in the slug itself.
_ARTICLE_RE = re.compile(
    r'href="(?P<path>/(?P<slug>[a-z0-9\-]+)-(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})_en)"',
    re.IGNORECASE,
)


def _slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").strip()


class DisDgSource:
    name = "disdg"
    domain = "defence-industry-space.ec.europa.eu"
    base_url = "https://defence-industry-space.ec.europa.eu"
    index_path = "/newsroom/latest-news_en"
    prefilter_required = False  # DG covers EU defense + space; near-100% on-topic
    disabled = False
    # ~10 articles per page; one page is enough for daily ingest. Bump for backfill.
    default_max_pages = 1

    def __init__(self, max_pages: int | None = None):
        self.max_pages = max_pages or self.default_max_pages

    def iter_candidates(self, limit: int | None = None) -> Iterator[CandidateArticle]:
        emitted = 0
        seen_paths: set[str] = set()
        with httpx.Client(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            for page in range(self.max_pages):
                url = (
                    f"{self.base_url}{self.index_path}"
                    + (f"?page={page}" if page > 0 else "")
                )
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                except Exception:
                    return
                html = resp.text
                page_emitted = 0
                for m in _ARTICLE_RE.finditer(html):
                    path = m.group("path")
                    if path in seen_paths:
                        continue
                    seen_paths.add(path)
                    year = int(m.group("year"))
                    month = int(m.group("month"))
                    day = int(m.group("day"))
                    try:
                        published = datetime(year, month, day, tzinfo=timezone.utc).isoformat()
                    except ValueError:
                        # malformed date in URL — skip
                        continue
                    yield CandidateArticle(
                        source=self.name,
                        url=f"{self.base_url}{path}",
                        title=_slug_to_title(m.group("slug")),
                        published_at=published,
                    )
                    emitted += 1
                    page_emitted += 1
                    if limit is not None and emitted >= limit:
                        return
                if page_emitted == 0:
                    return
