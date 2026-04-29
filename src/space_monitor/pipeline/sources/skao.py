"""SKA Observatory news scraper.

SKAO publishes press releases at https://www.skao.int/en/news (no RSS feed).
The index page lists articles with URL pattern ``/en/news/<id>/<slug>`` and
inline ``Month YYYY`` date markers; pagination via ``?page=N`` (~18 items per
page, ~9 pages total).

ToS posture: ``robots.txt`` is permissive on ``/en/news/`` (only ``/admin``,
``/search``, ``/user/login`` etc. are disallowed). SKAO is an
intergovernmental treaty organization publishing public press releases — the
content is unambiguously meant for public access.
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

# Each entry on the index page renders as:
#   .../en/news/<id>/<slug>"...title...</a>...<Month> <YYYY>...
# The lazy `.*?` between the link and the date confines us to the same entry
# block; if the structure ever changes we fall back to None for the date.
_ENTRY_RE = re.compile(
    r"/en/news/(?P<id>\d+)/(?P<slug>[^\"'\s<>]+)"
    r".{0,1500}?"
    r"(?P<month>" + _MONTHS + r")\s+(?P<year>\d{4})",
    re.DOTALL,
)


def _slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").strip()


class SkaoSource:
    name = "skao"
    domain = "skao.int"
    base_url = "https://www.skao.int"
    index_path = "/en/news"
    prefilter_required = False  # SKAO posts only space content
    disabled = False
    # Pagination cap. SKAO has ~9 pages today; one page (~18 items) is enough
    # for daily ingest. Override via ingest CLI's --max-candidates.
    default_max_pages = 1

    def __init__(self, max_pages: int | None = None):
        self.max_pages = max_pages or self.default_max_pages

    def iter_candidates(self, limit: int | None = None) -> Iterator[CandidateArticle]:
        emitted = 0
        seen_ids: set[str] = set()
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
                except Exception as exc:
                    log_fetch_fail(self.name, url, exc)
                    return  # network blip ends the iteration cleanly
                html = resp.text
                page_emitted = 0
                for m in _ENTRY_RE.finditer(html):
                    aid = m.group("id")
                    if aid in seen_ids:
                        continue
                    seen_ids.add(aid)
                    slug = m.group("slug")
                    month = m.group("month")
                    year = m.group("year")
                    published = datetime(
                        int(year), _MONTH_TO_NUM[month], 1, tzinfo=timezone.utc
                    ).isoformat()
                    yield CandidateArticle(
                        source=self.name,
                        url=f"{self.base_url}/en/news/{aid}/{slug}",
                        title=_slug_to_title(slug),
                        published_at=published,
                    )
                    emitted += 1
                    page_emitted += 1
                    if limit is not None and emitted >= limit:
                        return
                if page_emitted == 0:
                    return  # no more entries — stop paginating
