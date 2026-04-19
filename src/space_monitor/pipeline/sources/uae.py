"""UAE Space Agency (space.gov.ae) news scraper.

News index at ``/en/media-center/news-and-media``; article URLs follow
``/en/media-center/news/DD/MM/YYYY/<slug>`` — the date is encoded directly
in the path, so no inline HTML date parsing required.

Server-rendered. UAE Space Agency runs an active partnership-seeking
program (Mars Hope mission, asteroid belt mission, satellite agreements);
high partnership-relevance signal.
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

# Article URL: /en/media-center/news/DD/MM/YYYY/<slug>
_ARTICLE_RE = re.compile(
    r'href="(?P<path>/en/media-center/news/'
    r'(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{4})/'
    r'(?P<slug>[a-z0-9\-]+))"',
    re.IGNORECASE,
)


def _slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").strip()


class UaeSpaceSource:
    name = "uae"
    domain = "space.gov.ae"
    base_url = "https://space.gov.ae"
    index_path = "/en/media-center/news-and-media"
    prefilter_required = False  # Pure agency press releases
    disabled = False

    def iter_candidates(self, limit: int | None = None) -> Iterator[CandidateArticle]:
        emitted = 0
        seen_paths: set[str] = set()
        with httpx.Client(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            try:
                resp = client.get(f"{self.base_url}{self.index_path}")
                resp.raise_for_status()
            except Exception:
                return
            html = resp.text
            for m in _ARTICLE_RE.finditer(html):
                path = m.group("path")
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                day = int(m.group("day"))
                month = int(m.group("month"))
                year = int(m.group("year"))
                try:
                    published = datetime(year, month, day, tzinfo=timezone.utc).isoformat()
                except ValueError:
                    continue
                yield CandidateArticle(
                    source=self.name,
                    url=f"{self.base_url}{path}",
                    title=_slug_to_title(m.group("slug")),
                    published_at=published,
                )
                emitted += 1
                if limit is not None and emitted >= limit:
                    return
