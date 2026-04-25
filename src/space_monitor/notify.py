"""Notifications: Slack/Discord webhooks + post-ingest digest formatter.

Decoupled from the pipeline so different deployments can plug in different
sinks. The single supported sink today is "POST a JSON payload to a webhook
URL" — works for Slack, Discord, Mattermost, Microsoft Teams (with the
right URL shape), and any custom HTTP collector.

Configured via env vars:

* ``NOTIFY_WEBHOOK_URL`` — required. The webhook to POST to.
* ``NOTIFY_WEBHOOK_KIND`` — optional. ``slack`` (default) or ``discord``.
  Determines the JSON shape (Slack uses ``text``, Discord uses ``content``).

Quietly no-ops when the env var isn't set, so daily ingests work fine
without a webhook.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from . import db


def webhook_url() -> str | None:
    return os.environ.get("NOTIFY_WEBHOOK_URL")


def post(text: str) -> bool:
    """POST ``text`` to the configured webhook. Returns True on success.

    No-op (returns False) when ``NOTIFY_WEBHOOK_URL`` isn't set so callers
    can wrap every notification in `if notify.post(...)` without conditional
    logic at the call site."""
    url = webhook_url()
    if not url:
        return False
    kind = os.environ.get("NOTIFY_WEBHOOK_KIND", "slack").lower()
    if kind == "discord":
        payload: dict[str, Any] = {"content": text[:1900]}  # Discord 2000-char cap
    else:
        payload = {"text": text}
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
        return True
    except Exception as e:
        # Quiet failure — daily ingest shouldn't fail because Slack is down.
        print(f"[notify] webhook POST failed: {type(e).__name__}: {e}")
        return False


# ---------------------------------------------------------------------------
# Post-ingest digest
# ---------------------------------------------------------------------------


def daily_digest(*, db_arg: str | None = None) -> str:
    """Build a one-message digest of yesterday's pipeline activity.

    Pulls counts from news_article + partnership_draft + (if present)
    contract_draft / leadership_change_draft, ranges over the last 24h.
    Returns plain text; caller can pass to :func:`post`.
    """
    target = db.resolve_db(db_arg)
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        n_articles = _count(conn,
            "SELECT COUNT(*) FROM news_article WHERE fetched_at > ?", (since,))
        n_pos = _count(conn, """
            SELECT COUNT(*) FROM partnership_draft d
              JOIN news_article a ON a.id = d.source_article_id
             WHERE a.fetched_at > ? AND d.partnership_year IS NOT NULL
        """, (since,))
        n_pending_high = _count(conn, """
            SELECT COUNT(*) FROM partnership_draft
             WHERE draft_status = 'pending' AND confidence = 'high'
        """, ())
        n_contracts = _count(conn,
            "SELECT COUNT(*) FROM contract_draft WHERE extracted_at > ?", (since,))
        n_leaders = _count(conn,
            "SELECT COUNT(*) FROM leadership_change_draft WHERE extracted_at > ?", (since,))
        n_failed = _count(conn,
            "SELECT COUNT(*) FROM news_article WHERE fetched_at > ? AND status = 'failed'",
            (since,))
    parts = [
        f"*space-monitor daily digest* — last 24h",
        f"📰 {n_articles} articles ingested  ·  ❌ {n_failed} failed",
        f"🤝 {n_pos} new partnership drafts (positives)",
    ]
    if n_contracts:
        parts.append(f"📝 {n_contracts} contract drafts")
    if n_leaders:
        parts.append(f"👤 {n_leaders} leadership-change drafts")
    parts.append(f"📋 review queue: {n_pending_high} pending HIGH-confidence drafts waiting")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Cost budget check
# ---------------------------------------------------------------------------


def cost_check(
    *,
    cap_usd: float,
    window_hours: int = 24,
    db_arg: str | None = None,
) -> tuple[float, str | None]:
    """Compare ``window_hours`` of LLM spend against ``cap_usd``.

    Returns ``(estimated_usd, alert_message_or_None)``. Alert fires when
    spend over the window > cap. Caller decides what to do with the
    message (post to Slack, exit non-zero, …).
    """
    target = db.resolve_db(db_arg)
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        try:
            row = conn.execute(
                "SELECT SUM(input_tokens), SUM(output_tokens), "
                "       SUM(cache_read_input_tokens), "
                "       SUM(cache_creation_input_tokens) "
                "FROM extraction_usage WHERE recorded_at >= ?",
                (since,),
            ).fetchone()
        except Exception:
            return (0.0, None)
    if not row or not row[0]:
        return (0.0, None)
    in_tok, out_tok, cache_r, cache_w = (x or 0 for x in row)
    # Same rate card as ui/data.py:cost_to_usd — kept in sync.
    usd = (
        in_tok * 1.00 / 1_000_000
        + out_tok * 5.00 / 1_000_000
        + cache_r * 0.10 / 1_000_000
        + cache_w * 1.25 / 1_000_000
    )
    if usd > cap_usd:
        msg = (
            f"⚠ space-monitor cost alert: last {window_hours}h spend "
            f"≈ ${usd:.2f} > cap ${cap_usd:.2f}. "
            f"Inspect with `space-monitor cost --since "
            f"{since[:10]}`."
        )
        return (usd, msg)
    return (usd, None)


# ---------------------------------------------------------------------------
# Source freshness check
# ---------------------------------------------------------------------------


def stale_sources(
    *, threshold_days: int = 14, db_arg: str | None = None,
) -> list[tuple[str, int]]:
    """Sources that haven't produced an article in > threshold_days. Returns
    list of (source, days_silent) sorted by silence desc."""
    target = db.resolve_db(db_arg)
    out = []
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        rows = conn.execute(
            "SELECT source, MAX(fetched_at) FROM news_article GROUP BY source"
        ).fetchall()
    now = datetime.now(timezone.utc)
    for src, last in rows:
        if not last:
            continue
        try:
            lf = datetime.fromisoformat(last)
            if lf.tzinfo is None:
                lf = lf.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        days = (now - lf).days
        if days > threshold_days:
            out.append((src, days))
    out.sort(key=lambda t: -t[1])
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count(conn, sql: str, params: tuple) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return 0
    return (row[0] or 0) if row else 0
