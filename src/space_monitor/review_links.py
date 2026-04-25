"""Magic-link approve/reject tokens for the digest email/notification path.

Each call to :func:`mint` produces a single-use URL-safe token bound to one
draft + one action. The token is recorded in ``review_token`` with the
issuing user. Calling :func:`consume` validates and applies the action.

The links assume a deployed UI at ``REVIEW_LINK_BASE_URL`` (env var). When
unset, ``mint`` still produces tokens and stores them, but the formatted
URL falls back to a CLI command the recipient can paste:

    space-monitor review consume <token>

This way the feature works in any environment — local-only, GH Actions,
Streamlit Cloud — without forcing the deploy path before the auth story
is ready.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timezone

from . import db


def _base_url() -> str | None:
    return os.environ.get("REVIEW_LINK_BASE_URL")


def mint(
    draft_id: int, action: str, *, issued_to: str, db_arg: str | None = None,
) -> tuple[str, str]:
    """Mint a magic-link token. Returns (token, url-or-cli-instruction).

    Same draft+action+user combination yields a fresh token each call —
    intentional, so a re-mint invalidates a forgotten link by superseding
    it (the consume step rejects expired/consumed tokens).
    """
    if action not in ("approve", "reject"):
        raise ValueError(f"action must be 'approve' or 'reject', got {action!r}")
    token = secrets.token_urlsafe(24)
    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        conn.execute(
            "INSERT INTO review_token (token, draft_id, action, issued_to) "
            "VALUES (?, ?, ?, ?)",
            (token, draft_id, action, issued_to),
        )
        conn.commit()
    base = _base_url()
    if base:
        url = f"{base.rstrip('/')}?action=review&token={token}"
    else:
        url = f"space-monitor review consume {token}"
    return (token, url)


def consume(
    token: str, *, reason: str | None = None, db_arg: str | None = None,
) -> tuple[bool, str]:
    """Validate + apply a token. Returns (ok, message)."""
    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        row = conn.execute(
            "SELECT draft_id, action, issued_to, consumed_at "
            "FROM review_token WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return (False, "Unknown or expired token.")
        draft_id, action, issued_to, consumed_at = row
        if consumed_at:
            return (False, f"Token already used at {consumed_at}.")

        from .pipeline import drafts as drafts_mod
        try:
            if action == "approve":
                pid = drafts_mod.approve(
                    conn, draft_id, reviewer=issued_to,
                    notes="Approved via magic link",
                )
                msg = f"Approved → {pid}"
            else:
                drafts_mod.reject(
                    conn, draft_id, reviewer=issued_to,
                    reason=reason or "Rejected via magic link",
                )
                msg = "Rejected."
        except Exception as e:
            return (False, f"{type(e).__name__}: {e}")

        conn.execute(
            "UPDATE review_token SET consumed_at = ? WHERE token = ?",
            (datetime.now(timezone.utc).isoformat(), token),
        )
        conn.commit()
    return (True, msg)
