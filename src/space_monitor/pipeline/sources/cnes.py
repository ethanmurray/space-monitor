"""Centre national d'études spatiales (CNES, French space agency) press scraper.

CNES press releases live at https://cnes.fr/communiques (the older
``presse.cnes.fr`` redirects here). The index page is server-rendered with a
``fr-card`` per article; each card carries the article URL
(``/communiques/<slug>``) and a French date in ``DD <month> YYYY`` format.
French-language content; Claude handles the language fine.

ToS posture: CNES is a French government agency publishing official press
releases for public dissemination. No explicit anti-scraping clause found
on the site.
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
_MONTHS_FR = (
    "janvier|février|mars|avril|mai|juin|juillet|août|"
    "septembre|octobre|novembre|décembre"
)
_MONTH_FR_TO_NUM = {
    name: i for i, name in enumerate(
        [
            "janvier", "février", "mars", "avril", "mai", "juin",
            "juillet", "août", "septembre", "octobre", "novembre", "décembre",
        ],
        start=1,
    )
}

_ENTRY_RE = re.compile(
    r"/communiques/(?P<slug>[a-z0-9\-]+)"
    r".{0,3000}?"
    r"(?P<day>\d{1,2})\s+(?P<month>" + _MONTHS_FR + r")\s+(?P<year>\d{4})",
    re.DOTALL | re.IGNORECASE,
)


def _slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").strip()


class CnesSource:
    name = "cnes"
    domain = "cnes.fr"
    base_url = "https://cnes.fr"
    index_path = "/communiques"
    prefilter_required = False  # Agency press releases are always space-relevant
    disabled = False

    def iter_candidates(self, limit: int | None = None) -> Iterator[CandidateArticle]:
        emitted = 0
        seen_slugs: set[str] = set()
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
                slug = m.group("slug")
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)
                day = int(m.group("day"))
                month = _MONTH_FR_TO_NUM[m.group("month").lower()]
                year = int(m.group("year"))
                try:
                    published = datetime(year, month, day, tzinfo=timezone.utc).isoformat()
                except ValueError:
                    continue
                yield CandidateArticle(
                    source=self.name,
                    url=f"{self.base_url}/communiques/{slug}",
                    title=_slug_to_title(slug),
                    published_at=published,
                )
                emitted += 1
                if limit is not None and emitted >= limit:
                    return
