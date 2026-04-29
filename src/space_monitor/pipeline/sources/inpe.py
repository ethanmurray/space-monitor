"""INPE (Brazilian National Institute for Space Research) news scraper.

News at https://www.gov.br/inpe/pt-br/assuntos/ultimas-noticias — articles
follow ``/inpe/pt-br/assuntos/ultimas-noticias/<slug>`` with inline DD/MM/YYYY
dates rendered next to each entry. Server-rendered. Portuguese-language;
Claude handles fine.

INPE is South America's most active space research institute and posts a
steady stream of bilateral cooperation (FAO, Marinha, Censipam, INMET,
state governments). High partnership-relevance signal.
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

# Each entry has the article URL followed within ~3KB by a DD/MM/YYYY date.
_ENTRY_RE = re.compile(
    r"/inpe/pt-br/assuntos/ultimas-noticias/(?P<slug>[a-z0-9\-]+)"
    r".{0,3000}?"
    r"(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{4})",
    re.DOTALL,
)


def _slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").strip()


class InpeSource:
    name = "inpe"
    domain = "gov.br/inpe"
    base_url = "https://www.gov.br"
    index_path = "/inpe/pt-br/assuntos/ultimas-noticias"
    prefilter_required = False  # Pure research-institute press releases
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
                # Skip the "rss.xml" / "atom.xml" / "RSS" entries that match
                # the slug pattern but aren't articles.
                if slug.lower() in {"rss", "rss.xml", "atom.xml"}:
                    continue
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)
                day = int(m.group("day"))
                month = int(m.group("month"))
                year = int(m.group("year"))
                try:
                    published = datetime(year, month, day, tzinfo=timezone.utc).isoformat()
                except ValueError:
                    continue
                yield CandidateArticle(
                    source=self.name,
                    url=f"{self.base_url}{self.index_path}/{slug}",
                    title=_slug_to_title(slug),
                    published_at=published,
                )
                emitted += 1
                if limit is not None and emitted >= limit:
                    return
