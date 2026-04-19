"""EU Space Surveillance & Tracking news scraper.

EU SST publishes news at https://www.eusst.eu/newsroom/ (no RSS feed). The
index page lists ~28 articles in slug-only URL form (``/newsroom/news/<slug>``)
with inline ``DD Month YYYY`` dates. Posts are infrequent (a handful per year),
so a single index fetch reaches back ~5 years today.

ToS posture: ``robots.txt`` is the standard Drupal default — disallows
``/admin``, ``/search``, ``/user/*``, etc., but explicitly permits everything
else. Site is run by EUSPA (the EU Agency for the Space Programme), publishing
official public communications.
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
_MONTHS = (
    "January|February|March|April|May|June|July|August|"
    "September|October|November|December"
)
_MONTH_TO_NUM = {
    name: i for i, name in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}

# Each entry on the index page renders the article URL, then within ~2KB the
# ``DD Month YYYY`` date appears. Lazy `.{0,2000}?` keeps us inside one entry.
_ENTRY_RE = re.compile(
    r"/newsroom/news/(?P<slug>[a-z0-9\-]+)"
    r".{0,2000}?"
    r"(?P<day>\d{1,2})\s+(?P<month>" + _MONTHS + r")\s+(?P<year>\d{4})",
    re.DOTALL,
)


def _slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").strip()


class EusstSource:
    name = "eusst"
    domain = "eusst.eu"
    base_url = "https://www.eusst.eu"
    index_path = "/newsroom/"
    prefilter_required = False  # EU SST posts only space-surveillance content
    disabled = False

    def iter_candidates(self, limit: int | None = None) -> Iterator[CandidateArticle]:
        emitted = 0
        seen_slugs: set[str] = set()
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
            for m in _ENTRY_RE.finditer(html):
                slug = m.group("slug")
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)
                day = int(m.group("day"))
                month = _MONTH_TO_NUM[m.group("month")]
                year = int(m.group("year"))
                published = datetime(year, month, day, tzinfo=timezone.utc).isoformat()
                yield CandidateArticle(
                    source=self.name,
                    url=f"{self.base_url}/newsroom/news/{slug}",
                    title=_slug_to_title(slug),
                    published_at=published,
                )
                emitted += 1
                if limit is not None and emitted >= limit:
                    return
