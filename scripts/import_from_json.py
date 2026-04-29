"""Import pre-fetched articles from a JSON file into the pipeline.

Use this when the source can't be reached via httpx (JS-rendered, IP
blocked, etc.) but can be scraped through Chrome MCP. The Chrome side
writes a JSON file like:

    [
      {"source": "dlr",
       "url":    "https://www.dlr.de/en/latest/news/2026/...",
       "title":  "Article title",
       "published_at": "2026-04-27",
       "text":   "Full article body, plain text..."},
      ...
    ]

This script inserts each as a fetched news_article (skipping ones already
in the DB), then runs the same country-tag + extract + signal-router
pass the daily ingest does — so output is identical in shape to a
normal ingest.

Usage:
  python scripts/import_from_json.py path/to/dlr_2026.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

# Load .env so ANTHROPIC + TURSO creds are present.
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
from space_monitor.pipeline import extract, signals  # noqa: E402


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _insert_or_skip(conn, entry: dict) -> tuple[int, bool]:
    """Insert one news_article row from a JSON entry. Returns (id, is_new)."""
    url = entry["url"]
    h = _url_hash(url)
    existing = conn.execute(
        "SELECT id, status FROM news_article WHERE url_hash = ?", (h,)
    ).fetchone()
    if existing:
        return existing[0], False
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO news_article
            (url_hash, url, source, source_domain, title, published_at,
             fetched_at, status, cleaned_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'fetched', ?)
        """,
        (
            h,
            url,
            entry["source"],
            urlparse(url).netloc,
            entry.get("title"),
            entry.get("published_at"),
            now,
            entry["text"],
        ),
    )
    conn.commit()
    return cur.lastrowid, True


def _extract_pipeline(conn, article_id: int, cleaned: str, title: str | None, url: str) -> str:
    """Run country-tag + partnership extract + signal router. Same shape as the
    daily ingest's _ingest_one inner block."""
    # Country tag
    if not country_tag_mod.already_tagged(conn, article_id):
        try:
            tr = country_tag_mod.tag(cleaned, title=title)
            country_tag_mod.persist(conn, article_id, tr)
            extract.log_usage(
                conn, model=tr.model, kind="country_tag",
                article_id=article_id,
                input_tokens=tr.input_tokens, output_tokens=tr.output_tokens,
                cache_read_input_tokens=tr.cache_read_input_tokens,
                cache_creation_input_tokens=tr.cache_creation_input_tokens,
            )
        except Exception as exc:
            print(f"  [tag-fail] {url}: {exc}", file=sys.stderr)

    # Partnership extract
    try:
        ex = extract.extract_with_escalation(cleaned, title=title)
        drafts_mod.insert_draft(conn, article_id=article_id, extraction=ex)
        extract.log_usage(
            conn, model=ex.model, kind="extract",
            article_id=article_id,
            input_tokens=ex.usage.input_tokens, output_tokens=ex.usage.output_tokens,
            cache_read_input_tokens=ex.usage.cache_read_input_tokens,
            cache_creation_input_tokens=ex.usage.cache_creation_input_tokens,
        )
        conn.execute("UPDATE news_article SET status='extracted' WHERE id=?", (article_id,))
        conn.commit()
    except Exception as exc:
        print(f"  [extract-fail] {url}: {exc}", file=sys.stderr)
        return "extract_failed"

    # Signal router
    try:
        if ex.payload.get("is_partnership"):
            signals.record_signal(conn, article_id, "partnership")
            conn.commit()
        router = signals.route(cleaned, title=title)
        extract.log_usage(
            conn, model=router.model, kind="signal_router",
            article_id=article_id,
            input_tokens=router.input_tokens, output_tokens=router.output_tokens,
            cache_read_input_tokens=router.cache_read_input_tokens,
            cache_creation_input_tokens=router.cache_creation_input_tokens,
        )
        for kind in router.signals:
            if kind == "partnership" or signals.has_signal(conn, article_id, kind):
                continue
            try:
                if kind == "contract":
                    res = signals.extract_contract(cleaned, title=title, url=url)
                    signals.persist_contract(conn, article_id, res)
                elif kind == "leadership_change":
                    res = signals.extract_leadership_change(cleaned, title=title, url=url)
                    signals.persist_leadership(conn, article_id, res)
                else:
                    continue
                extract.log_usage(
                    conn, model=res.model, kind=f"signal_{kind}",
                    article_id=article_id,
                    input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                    cache_read_input_tokens=res.cache_read_input_tokens,
                    cache_creation_input_tokens=res.cache_creation_input_tokens,
                )
            except Exception as e:
                print(f"  [{kind}-fail] {url}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"  [signal-router-fail] {url}: {e}", file=sys.stderr)

    return "extracted"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_file", help="Path to JSON list of {source, url, title, published_at, text}")
    args = ap.parse_args()

    entries = json.load(open(args.json_file))
    print(f"[import] {len(entries)} entries from {args.json_file}")

    counts = {"new_extracted": 0, "skipped": 0, "extract_failed": 0}
    with db.connect(db.resolve_db()) as conn:
        db.ensure_pipeline_schema(conn)
        for i, entry in enumerate(entries, 1):
            url = entry["url"]
            print(f"[{i}/{len(entries)}] {url}", flush=True)
            article_id, is_new = _insert_or_skip(conn, entry)
            if not is_new:
                print(f"       skip — already in DB (id={article_id})")
                counts["skipped"] += 1
                continue
            outcome = _extract_pipeline(
                conn, article_id, entry["text"],
                entry.get("title"), url,
            )
            if outcome == "extracted":
                counts["new_extracted"] += 1
            else:
                counts["extract_failed"] += 1

    print(f"\n[import] {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
