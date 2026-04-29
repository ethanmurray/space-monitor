"""One-off backfill for sources whose RSS feed only exposes a few days.

Walks the site's sitemap_index.xml, finds article URLs published on or
after --since, and feeds them through the same fetch + extract pipeline
the daily ingest uses. The newly-added daily-ingest cron picks up
anything new from this point forward, so this only needs to run once.

Usage:
  python scripts/backfill_blocked.py --source spacenews --since 2026-01-01
  python scripts/backfill_blocked.py --source satellitetoday --since 2026-01-01
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from xml.etree import ElementTree as ET

import httpx

# Load .env so ANTHROPIC_API_KEY + TURSO_* are present.
_ENV = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_ENV):
    for line in open(_ENV):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))

from space_monitor import db  # noqa: E402
from space_monitor.pipeline import country_tag as country_tag_mod  # noqa: E402
from space_monitor.pipeline import drafts as drafts_mod  # noqa: E402
from space_monitor.pipeline import extract, fetch  # noqa: E402
from space_monitor.pipeline.sources.base import CandidateArticle  # noqa: E402

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

SITEMAP_INDEX = {
    "spacenews": "https://spacenews.com/sitemap_index.xml",
    "satellitetoday": "https://www.satellitetoday.com/sitemap_index.xml",
}

# Both sites' sitemaps include taxonomy/archive/author pages alongside articles.
# Skip anything matching these path segments — they're not posts.
NON_ARTICLE_PATH_RE = re.compile(
    r"^/(section|tag|category|author|page|wp-content|wp-json|feed|eletters|"
    r"archives|member-content)(/|$)",
    re.IGNORECASE,
)
# satellitetoday encodes publish date in path as /<topic>/YYYY/MM/DD/<slug>/.
# Use this when present — it's reliable. lastmod in their sitemap reflects
# last edit, not publish date, so 2020 posts touched in 2026 would otherwise
# leak through.
URL_DATE_RE = re.compile(r"/(20\d{2})/(\d{2})/(\d{2})/")


def _is_article_url(url: str) -> bool:
    from urllib.parse import urlparse

    path = urlparse(url).path
    if NON_ARTICLE_PATH_RE.match(path):
        return False
    # Article URLs typically have a slug segment (>= 30 chars or hyphenated).
    # Filter out homepage-ish paths like / and /about/.
    parts = [p for p in path.strip("/").split("/") if p]
    if not parts:
        return False
    last = parts[-1]
    return len(last) > 12 and "-" in last


def _publish_date_from_url(url: str) -> str | None:
    """Pull YYYY-MM-DD from a path-embedded date if the site uses that scheme."""
    m = URL_DATE_RE.search(url)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def fetch_xml(client: httpx.Client, url: str) -> ET.Element | None:
    try:
        resp = client.get(url)
        resp.raise_for_status()
        # Some sites (satellitetoday) prefix the XML declaration with stray
        # whitespace, which trips ElementTree. Strip it.
        return ET.fromstring(resp.text.lstrip())
    except Exception as exc:
        print(f"[backfill] FAIL {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def discover_urls(source: str, since_iso: str) -> list[CandidateArticle]:
    """Walk the sitemap index → sub-sitemaps → article URLs published >= since."""
    index_url = SITEMAP_INDEX[source]
    out: list[CandidateArticle] = []
    seen: set[str] = set()
    with httpx.Client(
        timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        index = fetch_xml(client, index_url)
        if index is None:
            return out
        sub_sitemaps = []
        for sm in index.findall("sm:sitemap", NS):
            loc_el = sm.find("sm:loc", NS)
            lastmod_el = sm.find("sm:lastmod", NS)
            if loc_el is None or not loc_el.text:
                continue
            # Only walk sub-sitemaps whose lastmod is after our cutoff.
            # Avoids fetching all 11 historical sitemaps for spacenews.
            lastmod = lastmod_el.text if lastmod_el is not None else ""
            if lastmod and lastmod[:10] < since_iso[:10]:
                continue
            sub_sitemaps.append(loc_el.text)
        print(f"[backfill] {source}: {len(sub_sitemaps)} sub-sitemap(s) to walk")

        for sm_url in sub_sitemaps:
            tree = fetch_xml(client, sm_url)
            if tree is None:
                continue
            for url_el in tree.findall("sm:url", NS):
                loc = url_el.find("sm:loc", NS)
                lastmod = url_el.find("sm:lastmod", NS)
                if loc is None or not loc.text:
                    continue
                # Prefer the date encoded in the URL path (true publish date)
                # over the sitemap lastmod (which can be a much later edit).
                url_date = _publish_date_from_url(loc.text)
                lastmod_text = lastmod.text if lastmod is not None and lastmod.text else None
                pub = url_date or lastmod_text
                effective_date = url_date or (lastmod_text[:10] if lastmod_text else "")
                if effective_date and effective_date < since_iso[:10]:
                    continue
                # satellitetoday's sitemap mixes articles with focus / eletter /
                # issues taxonomy pages. Real articles always have /YYYY/MM/DD/
                # in the URL — if it's missing, skip.
                if source == "satellitetoday" and url_date is None:
                    continue
                if loc.text in seen:
                    continue
                if not _is_article_url(loc.text):
                    continue
                seen.add(loc.text)
                out.append(
                    CandidateArticle(
                        source=source, url=loc.text, title=None, published_at=pub
                    )
                )
    out.sort(key=lambda c: c.published_at or "", reverse=True)
    return out


def ingest_candidate(conn, candidate: CandidateArticle, rate_secs: float) -> str:
    """Run the same pipeline as `space-monitor ingest` for one URL."""
    result = fetch.fetch(conn, candidate)
    if result.was_new and rate_secs > 0:
        time.sleep(rate_secs)
    if result.status == "failed":
        return "failed"
    if result.status == "extracted":
        return "already_extracted"

    text_row = conn.execute(
        "SELECT cleaned_text, title, url FROM news_article WHERE id=?",
        (result.article_id,),
    ).fetchone()
    cleaned, title, _url = text_row

    # Country tag
    if not country_tag_mod.already_tagged(conn, result.article_id):
        try:
            tag_result = country_tag_mod.tag(cleaned, title=title)
            country_tag_mod.persist(conn, result.article_id, tag_result)
            extract.log_usage(
                conn,
                article_id=result.article_id,
                kind="country_tag",
                model=tag_result.model,
                input_tokens=tag_result.input_tokens,
                output_tokens=tag_result.output_tokens,
                cache_read_input_tokens=tag_result.cache_read_input_tokens,
                cache_creation_input_tokens=tag_result.cache_creation_input_tokens,
            )
        except Exception as exc:
            print(f"  [tag-fail] {candidate.url}: {exc}", file=sys.stderr)

    # Extract partnerships
    try:
        ex = extract.extract_with_escalation(cleaned, title=title)
        drafts_mod.upsert_from_extraction(
            conn, source_article_id=result.article_id, extraction=ex
        )
        extract.log_usage(
            conn,
            article_id=result.article_id,
            kind="extract",
            model=ex.model,
            input_tokens=ex.input_tokens,
            output_tokens=ex.output_tokens,
            cache_read_input_tokens=ex.cache_read_input_tokens,
            cache_creation_input_tokens=ex.cache_creation_input_tokens,
        )
        conn.execute(
            "UPDATE news_article SET status='extracted' WHERE id=?",
            (result.article_id,),
        )
        conn.commit()
    except Exception as exc:
        print(f"  [extract-fail] {candidate.url}: {exc}", file=sys.stderr)
        return "extract_failed"
    return "extracted"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=sorted(SITEMAP_INDEX), required=True)
    ap.add_argument("--since", required=True, help="YYYY-MM-DD lower bound for published_at")
    ap.add_argument("--limit", type=int, default=None, help="Cap on URLs to ingest this run")
    ap.add_argument("--rate-limit-secs", type=float, default=1.5)
    ap.add_argument("--dry-run", action="store_true", help="Show URLs only, don't fetch")
    args = ap.parse_args()

    candidates = discover_urls(args.source, args.since)
    print(f"[backfill] {args.source}: {len(candidates)} URL(s) discovered since {args.since}")
    if args.limit:
        candidates = candidates[: args.limit]
    if args.dry_run:
        for c in candidates[:20]:
            print(f"  {c.published_at}  {c.url}")
        if len(candidates) > 20:
            print(f"  ... and {len(candidates) - 20} more")
        return 0

    counts = {"new_extracted": 0, "already_extracted": 0, "failed": 0, "extract_failed": 0}
    with db.connect(db.resolve_db()) as conn:
        db.ensure_pipeline_schema(conn)
        for i, cand in enumerate(candidates, 1):
            print(f"[{i}/{len(candidates)}] {cand.url}")
            outcome = ingest_candidate(conn, cand, args.rate_limit_secs)
            if outcome == "extracted":
                counts["new_extracted"] += 1
            elif outcome == "already_extracted":
                counts["already_extracted"] += 1
            elif outcome == "failed":
                counts["failed"] += 1
            else:
                counts["extract_failed"] += 1

    print(f"\n[backfill] {args.source}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
