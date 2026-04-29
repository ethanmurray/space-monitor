"""ISRO (Indian Space Research Organisation) news scraper.

ISRO publishes news as flat ``<Title>.html`` files at the site root, listed
in the homepage's "Latest News" section. There is no RSS feed, no dated
news index, and no consistent URL prefix — just a homepage section that
links to ~12 article files at any given time.

Article publication dates live inside each article body in
``Month DD, YYYY`` format (always the first such date in the body).

Adapter strategy:
1. Fetch the homepage and slice out the "Latest News" section.
2. Extract ``<Slug>.html`` hrefs from within that slice (avoids picking up
   evergreen pages like ``Careers.html`` or ``Tenders.html`` that live
   elsewhere on the page).
3. For each candidate, do a small GET of the article and parse the first
   ``Month DD, YYYY`` match for the publication date.

Per-ingest cost: ~12 extra HTTP requests to isro.gov.in for date discovery,
plus the normal fetch + extract cycle. Bounded; fine for daily cadence.
ISRO publishes a steady stream of bilateral partnerships (AIIMS, foreign
agencies, industry primes) — high partnership signal source.
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
_MONTHS_FULL = (
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

# Article URL pattern within the Latest News section: a CamelCase or
# snake_case .html file. Restricting to a leading capital letter filters out
# things like CSS/JS asset paths.
_ARTICLE_HREF_RE = re.compile(r'href="([A-Z][A-Za-z0-9_]+\.html)"')

# First "<Month> DD, YYYY" or "DD <Month> YYYY" in article body — that's
# the publication date.
_DATE_RE = re.compile(
    rf"({_MONTHS_FULL})\s+(\d{{1,2}}),?\s+(\d{{4}})",
    re.IGNORECASE,
)


def _slug_to_title(slug: str) -> str:
    return slug.replace("_", " ").strip()


class IsroSource:
    name = "isro"
    domain = "isro.gov.in"
    base_url = "https://www.isro.gov.in"
    prefilter_required = False  # Pure space agency
    disabled = False

    def iter_candidates(self, limit: int | None = None) -> Iterator[CandidateArticle]:
        emitted = 0
        seen_paths: set[str] = set()
        with httpx.Client(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            # 1. Homepage → Latest News section → article URLs.
            home_url = f"{self.base_url}/"
            try:
                home = client.get(home_url)
                home.raise_for_status()
            except Exception as exc:
                log_fetch_fail(self.name, home_url, exc)
                return
            html = home.text
            ln_start = html.find("Latest News")
            if ln_start < 0:
                return
            # End the slice at "Latest Updates" (next section) or 20KB
            # downstream — whichever comes first. Avoids picking up the
            # global navigation links lower in the page.
            ln_end = html.find("Latest Updates", ln_start)
            if ln_end < 0:
                ln_end = ln_start + 20000
            section = html[ln_start:ln_end]

            for path in _ARTICLE_HREF_RE.findall(section):
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                article_url = f"{self.base_url}/{path}"
                # 2. Fetch the article and parse the first date from its body.
                published = self._fetch_article_date(client, article_url)
                slug = path.removesuffix(".html")
                yield CandidateArticle(
                    source=self.name,
                    url=article_url,
                    title=_slug_to_title(slug),
                    published_at=published,
                )
                emitted += 1
                if limit is not None and emitted >= limit:
                    return

    def _fetch_article_date(self, client: httpx.Client, url: str) -> str | None:
        try:
            resp = client.get(url)
            resp.raise_for_status()
        except Exception:
            return None
        m = _DATE_RE.search(resp.text)
        if not m:
            return None
        try:
            month = _MONTH_TO_NUM[m.group(1).capitalize()]
        except KeyError:
            return None
        try:
            day = int(m.group(2))
            year = int(m.group(3))
            return datetime(year, month, day, tzinfo=timezone.utc).isoformat()
        except ValueError:
            return None
