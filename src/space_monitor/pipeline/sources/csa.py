"""Canadian Space Agency (asc-csa.gc.ca) HTML news scraper.

Lunar Gateway, Canadarm, Earth observation, and a steady stream of bilateral
cooperation. Pure space focus.

History:
* Originally an RSS adapter pointing at ``/rss/default_eng.xml``.
* That feed stopped regenerating in mid-March 2026 — ``<updated>`` was
  frozen at 2026-03-12 while the live news index kept adding articles
  through 2026-04-23 (6 articles missed in the gap, discovered 2026-05-16).
  CSA's CMS appears to skip the feed for certain article types or has a
  broken regeneration job — we have no contact to fix it upstream.
* Switched to HTML scrape of ``/eng/news/articles/`` using the same regex
  approach as :mod:`.disdg`: article URLs follow
  ``/eng/news/articles/YYYY/YYYY-MM-DD-<slug>.asp`` so the publish date is
  embedded in the URL itself, no inline parsing required.

Tradeoff: loses video items at ``/eng/multimedia/search/video/<id>`` that
the RSS included. They've historically been low-signal (under 5% of the
CSA corpus), so this is acceptable.
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

# Article URLs: /eng/news/articles/YYYY/YYYY-MM-DD-<slug>.asp — date is
# in the slug itself. The year directory must match the date year (the
# CMS enforces this), so we don't separately capture it.
_ARTICLE_RE = re.compile(
    r'href="(?P<path>/eng/news/articles/\d{4}/'
    r'(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})-(?P<slug>[a-z0-9\-]+)\.asp)"',
    re.IGNORECASE,
)


def _slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").strip()


class CsaSource:
    name = "csa"
    domain = "asc-csa.gc.ca"
    base_url = "https://www.asc-csa.gc.ca"
    index_path = "/eng/news/articles/"
    prefilter_required = False
    disabled = False

    def iter_candidates(self, limit: int | None = None) -> Iterator[CandidateArticle]:
        emitted = 0
        seen_paths: set[str] = set()
        url = f"{self.base_url}{self.index_path}"
        with httpx.Client(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except Exception as exc:
                log_fetch_fail(self.name, url, exc)
                return
            html = resp.text
            for m in _ARTICLE_RE.finditer(html):
                path = m.group("path")
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                try:
                    published = datetime(
                        int(m.group("year")),
                        int(m.group("month")),
                        int(m.group("day")),
                        tzinfo=timezone.utc,
                    ).isoformat()
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
