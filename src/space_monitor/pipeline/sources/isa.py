"""Israel Space Agency (space.gov.il) news scraper.

News index at https://www.space.gov.il/en/news-space ; articles follow
``/en/news-space/<numeric-id>`` with inline ``DD.MM.YYYY`` dates next to
each entry. Server-rendered. English-language site.

Israel Space Agency runs a small but partnership-active program (Beresheet,
SpaceX collaborations, ESA cooperation). Pure space focus.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterator

import httpx

from .base import CandidateArticle, log_fetch_fail


_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

# /en/news-space/<id>"...DD.MM.YYYY (period-separated, lazy match keeps us in
# the same entry block).
_ENTRY_RE = re.compile(
    r'/en/news-space/(?P<id>\d+)"'
    r".{0,2000}?"
    r"(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})",
    re.DOTALL,
)


class IsaSource:
    name = "isa"
    domain = "space.gov.il"
    base_url = "https://www.space.gov.il"
    index_path = "/en/news-space"
    prefilter_required = False
    disabled = False

    def iter_candidates(self, limit: int | None = None) -> Iterator[CandidateArticle]:
        emitted = 0
        seen_ids: set[str] = set()
        with httpx.Client(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            url = f"{self.base_url}{self.index_path}"
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except Exception as exc:
                log_fetch_fail(self.name, url, exc)
                return
            html = resp.text
            for m in _ENTRY_RE.finditer(html):
                aid = m.group("id")
                if aid in seen_ids:
                    continue
                seen_ids.add(aid)
                day = int(m.group("day"))
                month = int(m.group("month"))
                year = int(m.group("year"))
                try:
                    published = datetime(year, month, day, tzinfo=timezone.utc).isoformat()
                except ValueError:
                    continue
                yield CandidateArticle(
                    source=self.name,
                    url=f"{self.base_url}/en/news-space/{aid}",
                    title=None,  # Title isn't easy to grab from index; extractor will use article text
                    published_at=published,
                )
                emitted += 1
                if limit is not None and emitted >= limit:
                    return
