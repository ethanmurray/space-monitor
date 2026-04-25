"""Streamlit UI for space-monitor.

Three views, dispatched via ``st.session_state.view``:

1. ``sources`` — registry table (every source ever considered) joined with
   live stats from the news_article + partnership_draft tables.
2. ``source_detail`` — one source's stats cards + article browser
   (reverse-chronological, default-filtered to relevant).
3. ``article_review`` — full article body + draft form + translate button.

Run via ``space-monitor ui`` (which shells out to ``streamlit run`` against
this file). All DB access goes through :mod:`space_monitor.ui.data` which
uses :func:`db.connect` — so the same UI works against either a local
SQLite file or the configured Turso DB.
"""

from __future__ import annotations

import streamlit as st

# Absolute imports — Streamlit runs this file as a top-level script, not as
# a package member, so relative imports fail.
from space_monitor.env import load_dotenv
from space_monitor.pipeline.sources import REGISTRY as CODE_REGISTRY
from space_monitor.ui import data as ui_data
from space_monitor.ui import translate

load_dotenv()
st.set_page_config(page_title="space-monitor", layout="wide")


# ---------------------------------------------------------------------------
# Auth (optional — opt-in via UI_PASSWORD env var)
# ---------------------------------------------------------------------------


def _gate() -> bool:
    """Return True if the request is authorized to view the app.

    When ``UI_PASSWORD`` env var is unset, the app is open (suitable for
    localhost). When set, render a password prompt and only let through
    sessions that match. Single shared password — fine for a 1-5 person
    team. For per-user accounts, swap in streamlit-authenticator later."""
    import os as _os
    expected = _os.environ.get("UI_PASSWORD")
    if not expected:
        return True
    if st.session_state.get("_authed"):
        return True
    st.title("space-monitor")
    pw = st.text_input("Password", type="password", key="_pw_input")
    if st.button("Sign in"):
        if pw == expected:
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("Wrong password.")
    return False


# ---------------------------------------------------------------------------
# Magic-link consume (when ?token=... is in the URL)
# ---------------------------------------------------------------------------


def _consume_token_from_url() -> None:
    """If the page was loaded with ?token=..., apply the action and bounce
    back to the dashboard. Lets digest emails carry one-click links."""
    qp = st.query_params
    if "token" not in qp:
        return
    token = qp["token"]
    from space_monitor import review_links
    ok, msg = review_links.consume(token)
    st.session_state["_token_msg"] = (ok, msg)
    # Strip the token from the URL so a refresh doesn't re-attempt.
    st.query_params.clear()
    st.rerun()


# ---------------------------------------------------------------------------
# Session-state helpers
# ---------------------------------------------------------------------------


def _go(view: str, **kwargs: object) -> None:
    """Set view + any payload keys, then rerun. Used by row-click buttons."""
    st.session_state.view = view
    for k, v in kwargs.items():
        st.session_state[k] = v
    st.rerun()


def _init_state() -> None:
    if "view" not in st.session_state:
        st.session_state.view = "dashboard"
    if "only_relevant" not in st.session_state:
        st.session_state.only_relevant = True
    if "hide_skipped" not in st.session_state:
        st.session_state.hide_skipped = True
    if "show_summary" not in st.session_state:
        st.session_state.show_summary = False
    if "cluster_view" not in st.session_state:
        st.session_state.cluster_view = True


# ---------------------------------------------------------------------------
# View: sources registry
# ---------------------------------------------------------------------------


_STATUS_BADGE = {
    "working":     "🟢 working",
    "disabled":    "⚪ disabled",
    "blocked":     "🔴 blocked",
    "unreachable": "🔴 unreachable",
    "planned":     "🟡 planned",
    "deferred":    "🟡 deferred",
    "rejected":    "⚫ rejected",
}


def render_sources() -> None:
    st.title("Sources")
    st.caption(
        "Registry of every source ever considered. Hand-curated in "
        "`src/space_monitor/data/sources.yaml`; live stats joined from the DB."
    )

    registry = ui_data.load_registry()
    stats = ui_data.fetch_all_stats()
    code_registered = set(CODE_REGISTRY.keys())

    # Top-level summary
    n_total = len(registry)
    n_working = sum(1 for s in registry if s.status == "working")
    n_articles = sum(s.total_articles for s in stats.values())
    n_drafts = sum(s.total_drafts for s in stats.values())
    n_pending = sum(s.pending_low_med_confidence for s in stats.values())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Sources tracked", n_total, f"{n_working} working")
    c2.metric("Articles in DB", f"{n_articles:,}")
    c3.metric("Drafts (any)", f"{n_drafts:,}")
    c4.metric("Pending review (low/med conf.)", f"{n_pending:,}")

    st.divider()

    # Status filter
    statuses_present = sorted({s.status for s in registry})
    sel = st.multiselect(
        "Filter by status",
        statuses_present,
        default=["working", "disabled", "blocked", "unreachable"],
    )
    rows = [s for s in registry if s.status in sel]

    # Build the rows (one st.container per source — clickable detail button)
    for s in rows:
        st_ = stats.get(s.name)
        with st.container(border=True):
            cols = st.columns([3, 5, 2, 1])
            with cols[0]:
                st.markdown(f"**{s.name}**  &nbsp; *{s.domain}*")
                st.caption(f"{_STATUS_BADGE.get(s.status, s.status)}  ·  {s.type}")
            with cols[1]:
                st.markdown(f"_{s.description}_")
                if s.coverage_focus:
                    st.caption(f"Coverage: {s.coverage_focus}")
                if s.comment:
                    with st.expander("Notes", expanded=False):
                        st.write(s.comment)
            with cols[2]:
                if st_:
                    st.caption(
                        f"{st_.total_articles:,} articles · "
                        f"{st_.last_24h} last 24h · {st_.last_7d} last 7d"
                    )
                    if st_.relevance_pct is not None:
                        st.caption(
                            f"{st_.positive_drafts}/{st_.total_drafts} relevant "
                            f"({st_.relevance_pct:.0f}%)"
                        )
                    if st_.oldest_published:
                        st.caption(
                            f"Oldest: {st_.oldest_published[:10]}  ·  "
                            f"Newest: {(st_.newest_published or '')[:10]}"
                        )
                else:
                    st.caption("_no articles yet_")
            with cols[3]:
                if s.name in code_registered and st_:
                    if st.button("Browse →", key=f"browse_{s.name}"):
                        _go("source_detail", selected_source=s.name)


# ---------------------------------------------------------------------------
# View: source detail
# ---------------------------------------------------------------------------


def render_source_detail() -> None:
    src = st.session_state.get("selected_source")
    if not src:
        _go("sources")
        return

    if st.button("← All sources"):
        _go("sources")
        return

    registry = {s.name: s for s in ui_data.load_registry()}
    entry = registry.get(src)
    stats = ui_data.fetch_all_stats().get(src)

    st.title(src)
    if entry:
        st.markdown(f"**{entry.description}**  &nbsp; *{entry.domain}*")
        st.caption(_STATUS_BADGE.get(entry.status, entry.status))

    if stats:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total articles", f"{stats.total_articles:,}")
        c2.metric("Last 24h", stats.last_24h)
        c3.metric("Last 7d", stats.last_7d)
        c4.metric("Last 30d", stats.last_30d)
        rel = (
            f"{stats.relevance_pct:.0f}%" if stats.relevance_pct is not None else "—"
        )
        c5.metric(
            "% relevant",
            rel,
            f"{stats.pending_low_med_confidence} pending review",
        )
        if stats.oldest_published:
            st.caption(
                f"Oldest article published: **{stats.oldest_published[:10]}**  ·  "
                f"Newest: **{(stats.newest_published or '')[:10]}**"
            )

    st.divider()

    c1, c2, c3, c4 = st.columns(4)
    c1.checkbox(
        "Only relevant (is_partnership = true)",
        key="only_relevant",
        help="Off shows every fetched article; on shows only those flagged "
        "as a real partnership by the extractor.",
    )
    c2.checkbox(
        "Hide prefilter-skipped",
        key="hide_skipped",
        value=True,
        help="The LLM title classifier auto-skips obvious non-space articles "
        "before extraction (typically ~85% on noisy sources like gov.uk). "
        "Hidden by default; uncheck to spot-check what the classifier "
        "rejected.",
    )
    c3.checkbox(
        "Show extractor summary",
        key="show_summary",
        help="Toggle the article preview between the original RSS title and "
        "the extractor's one-sentence description from the partnership_draft.",
    )
    c4.checkbox(
        "Cluster duplicates",
        key="cluster_view",
        help="Group drafts with identical (country pair, year, type) — one "
        "real partnership often appears in 3-5 articles.",
    )

    articles = ui_data.list_articles(
        src,
        only_relevant=st.session_state.only_relevant,
        hide_skipped=st.session_state.hide_skipped,
        limit=200,
    )
    if not articles:
        st.info("No articles match the current filter.")
        return

    st.caption(f"{len(articles)} article(s) — newest first")

    # Bulk-action toolbar — operates on all currently-listed articles' drafts.
    draft_ids = [a.draft_id for a in articles if a.draft_id]
    if draft_ids:
        with st.expander(f"Bulk actions ({len(draft_ids)} drafts in scope)"):
            ba1, ba2, ba3 = st.columns([2, 2, 2])
            if ba1.button(
                "✅ Approve all HIGH-confidence",
                key="bulk_approve_high",
                use_container_width=True,
            ):
                n, errs = ui_data.bulk_approve_high_confidence(
                    draft_ids, reviewer=_analyst_name(),
                )
                st.toast(f"Approved {n} high-confidence draft(s).", icon="✅")
                for e in errs[:5]:
                    st.error(e)
                st.rerun()
            reason = ba2.text_input(
                "Bulk-reject reason",
                key="bulk_reject_reason",
                placeholder="e.g. prefilter false positives",
            )
            if ba3.button(
                "🚫 Reject all visible",
                key="bulk_reject_all",
                use_container_width=True,
                disabled=not reason.strip(),
            ):
                n = ui_data.bulk_reject(
                    draft_ids, reviewer=_analyst_name(), reason=reason.strip(),
                )
                st.toast(f"Rejected {n} draft(s).", icon="🚫")
                st.rerun()

    for a in articles:
        with st.container(border=True):
            cols = st.columns([6, 2, 1])
            with cols[0]:
                preview = (a.description if st.session_state.show_summary
                           and a.description else a.title) or "(no title)"
                st.markdown(f"**{preview}**")
                st.caption(a.url)
            with cols[1]:
                published = (a.published_at or a.fetched_at)[:10]
                st.caption(f"📅 {published}")
                if a.confidence:
                    st.caption(f"Confidence: {a.confidence}")
                if a.is_relevant:
                    st.caption("✅ relevant")
            with cols[2]:
                if a.draft_id:
                    if st.button("Review →", key=f"review_{a.id}"):
                        _go("article_review", selected_article_id=a.id)
                else:
                    # Why no draft? Tell the analyst.
                    label = {
                        "skipped_prefilter": "⏭ prefilter:no",
                        "fetched":           "⏳ awaiting extract",
                        "failed":            "❌ failed",
                    }.get(a.status, f"({a.status})")
                    st.caption(label)


# ---------------------------------------------------------------------------
# View: article review
# ---------------------------------------------------------------------------


import os

from space_monitor import taxonomy as taxonomy_mod


# Field name on partnership_draft -> (label, kind, vocabulary lookup)
# kind: "text" | "int" | "enum" — enum draws values from the named taxonomy attr.
_FORM_FIELDS: list[tuple[str, str, str, str | None]] = [
    ("partnership_year",     "Year",            "int",  None),
    ("partnership_type",     "Type",            "enum", "partnership_types"),
    ("level_of_commitment",  "Commitment",      "enum", "levels_of_commitment"),
    ("relationship_type",    "Relationship",    "enum", "relationship_types"),
    ("business_model",       "Business model",  "enum", "business_models"),
    ("mission_type",         "Mission type",    "enum", "mission_types"),
    ("primary_mission",      "Primary mission", "enum", "mission_areas"),
    ("country_1",            "Country 1",       "text", None),
    ("org_type_1",           "Org type 1",      "enum", "organization_types"),
    ("organization_1",       "Org 1",           "text", None),
    ("company_1",            "Company 1",       "text", None),
    ("country_2",            "Country 2",       "text", None),
    ("org_type_2",           "Org type 2",      "enum", "organization_types"),
    ("organization_2",       "Org 2",           "text", None),
    ("company_2",            "Company 2",       "text", None),
]


def _enum_options(attr: str) -> list[str]:
    """Return sorted vocabulary names for the named taxonomy attribute."""
    t = taxonomy_mod.load()
    raw = getattr(t, attr, ())
    out: list[str] = []
    for item in raw:
        # ScoredTerm has .name; plain str entries appear directly.
        out.append(item.name if hasattr(item, "name") else str(item))
    return out


def _analyst_name() -> str:
    return os.environ.get("ANALYST_NAME") or "analyst"


def render_article_review() -> None:
    article_id = st.session_state.get("selected_article_id")
    if not article_id:
        _go("sources")
        return

    article = ui_data.get_article(article_id)
    if not article:
        st.error(f"Article {article_id} not found.")
        if st.button("← All sources"):
            _go("sources")
        return

    cb1, cb2 = st.columns([1, 1])
    if cb1.button("← Back to source"):
        _go("source_detail", selected_source=article.source)
        return
    cb2.markdown(
        f"<div style='text-align:right'><a href='{article.url}' target='_blank'>"
        f"open original ↗</a></div>",
        unsafe_allow_html=True,
    )

    st.title(article.title or "(no title)")
    st.caption(
        f"{article.source}  ·  published {(article.published_at or '')[:10]}"
    )

    # Country tags + non-partnership signal drafts both come from the
    # multi-signal pipeline that runs after each fetch. Surfacing them up
    # top so the analyst sees "what kinds of structured data did we pull
    # from this article" before diving into any one form.
    if article.countries:
        chips = " ".join(
            f"`{c}{'★' if cn == 'central' else ''}`"
            for c, cn in article.countries
        )
        st.markdown(f"**Countries:** {chips}")
    badges = []
    if article.draft:
        badges.append("partnership")
    if article.contracts:
        badges.append(f"contract×{len(article.contracts)}")
    if article.leadership_changes:
        badges.append(f"leadership×{len(article.leadership_changes)}")
    if badges:
        st.caption("Signals extracted: " + " · ".join(badges))

    body_col, draft_col = st.columns([3, 2])

    # -------- LEFT: article body + translate ---------
    with body_col:
        st.subheader("Article body")
        # Persisted English translation (if any) lives on news_article;
        # re-using it across sessions saves the LLM call.
        translation = article.cleaned_text_en
        if not translation and st.button("🌐 Translate to English"):
            try:
                with st.spinner("Translating via Claude (caching to DB)…"):
                    translation = ui_data.translate_and_cache(
                        article.id, article.cleaned_text or ""
                    )
            except Exception as e:
                st.error(f"Translation failed: {type(e).__name__}: {e}")

        if translation:
            st.info("Showing cached English translation. Original below.")
            st.write(translation)
            with st.expander("Original text"):
                st.write(article.cleaned_text or "(no body)")
        else:
            st.write(article.cleaned_text or "(no body — fetch may have failed)")

    # -------- RIGHT: editable draft + actions ---------
    with draft_col:
        # Other signal drafts (contracts, leadership changes) sit above the
        # partnership form — each is editable + actionable in its own
        # expander.
        if article.contracts:
            for c in article.contracts:
                _render_contract_form(c)
        if article.leadership_changes:
            for c in article.leadership_changes:
                _render_leadership_form(c)

        st.subheader("Partnership draft")
        d = article.draft
        if not d:
            st.info("No partnership draft for this article.")
            return

        st.caption(
            f"Draft #{d['id']}  ·  status `{d['draft_status']}`  ·  "
            f"confidence `{d.get('confidence') or '—'}`  ·  "
            f"model `{d.get('extractor_model')}`  ·  "
            f"reviewer = `{_analyst_name()}`"
        )
        if d.get("possible_duplicate_of"):
            st.warning(
                f"⚠ Possible duplicate of existing partnership "
                f"`{d['possible_duplicate_of']}`"
            )
        if d.get("review_notes"):
            st.info(f"Prior review note: {d['review_notes']}")
        if d.get("description"):
            st.markdown(f"**Extractor summary:** _{d['description']}_")

        is_actionable = d["draft_status"] == "pending"
        if not is_actionable:
            st.success(
                f"This draft is **{d['draft_status']}**. "
                + (f"Promoted partnership: `{d['promoted_partnership_id']}`."
                   if d.get("promoted_partnership_id") else "")
            )
            # Show fields read-only for non-pending drafts.
            for col, label, _kind, _vocab in _FORM_FIELDS:
                v = d.get(col)
                st.text(f"{label}: {v if v not in (None, '') else '—'}")
            return

        # -------- editable form ---------
        with st.form(key=f"draft_form_{d['id']}"):
            edits: dict[str, object | None] = {}
            for col, label, kind, vocab in _FORM_FIELDS:
                current = d.get(col)
                widget_key = f"f_{d['id']}_{col}"
                if kind == "enum":
                    options = [""] + _enum_options(vocab)  # type: ignore[arg-type]
                    idx = options.index(current) if current in options else 0
                    val = st.selectbox(label, options, index=idx, key=widget_key)
                    edits[col] = val if val else None
                elif kind == "int":
                    val = st.text_input(label, value=str(current) if current else "", key=widget_key)
                    try:
                        edits[col] = int(val) if val.strip() else None
                    except ValueError:
                        edits[col] = None
                else:
                    val = st.text_input(label, value=current or "", key=widget_key)
                    edits[col] = val if val else None

            reject_reason = st.text_area(
                "Rejection reason (required to reject)",
                key=f"reject_reason_{d['id']}",
                placeholder="e.g. Not a partnership announcement; "
                "or, duplicate of <id>; or, low quality extraction.",
            )

            ca, cb, cc = st.columns(3)
            save_clicked = ca.form_submit_button("💾 Save edits", use_container_width=True)
            approve_clicked = cb.form_submit_button("✅ Approve", use_container_width=True, type="primary")
            reject_clicked = cc.form_submit_button("🚫 Reject as irrelevant", use_container_width=True)

        if save_clicked:
            ui_data.save_draft_edits(d["id"], edits)
            st.toast("Edits saved.", icon="💾")
            st.rerun()

        if approve_clicked:
            ui_data.save_draft_edits(d["id"], edits)
            try:
                pid = ui_data.approve_draft(
                    d["id"], reviewer=_analyst_name(),
                    notes="Approved via UI",
                )
            except Exception as e:
                st.error(f"Approve failed: {type(e).__name__}: {e}")
                return
            st.toast(f"Approved → {pid[:60]}", icon="✅")
            _advance(after_draft_id=d["id"], fallback_source=article.source)

        if reject_clicked:
            if not reject_reason.strip():
                st.error("Please provide a rejection reason.")
                return
            try:
                ui_data.reject_draft(
                    d["id"], reviewer=_analyst_name(), reason=reject_reason.strip(),
                )
            except Exception as e:
                st.error(f"Reject failed: {type(e).__name__}: {e}")
                return
            st.toast("Rejected as irrelevant.", icon="🚫")
            _advance(after_draft_id=d["id"], fallback_source=article.source)


# ---------------------------------------------------------------------------
# Forms for non-partnership signal drafts
# ---------------------------------------------------------------------------


_CONTRACT_FORM = [
    ("contract_year",       "Contract year",      "int"),
    ("value_musd",          "Value (M USD)",      "float"),
    ("customer",            "Customer",           "text"),
    ("customer_country",    "Customer country",   "text"),
    ("contractor",          "Contractor",         "text"),
    ("contractor_country",  "Contractor country", "text"),
    ("primary_mission",     "Primary mission",    "text"),
    ("mission_type",        "Mission type",       "text"),
]

_LEADERSHIP_FORM = [
    ("change_year",   "Year",            "int"),
    ("person_name",   "Person",          "text"),
    ("organization",  "Organization",    "text"),
    ("country",       "Country",         "text"),
    ("new_role",      "New role",        "text"),
    ("prior_role",    "Prior role",      "text"),
    ("change_kind",   "Kind",            "enum_change_kind"),
]

_CHANGE_KINDS = ["", "appointment", "promotion", "departure", "resignation", "other"]


def _render_signal_form(
    *,
    kind: str,
    draft: dict,
    title: str,
    fields: list[tuple[str, str, str]],
) -> None:
    """Shared editable form for one contract / leadership_change draft."""
    status = draft.get("draft_status", "pending")
    header = f"{title}  ·  status `{status}`  ·  conf `{draft.get('confidence') or '—'}`"
    with st.expander(header, expanded=(status == "pending")):
        if draft.get("description"):
            st.caption(draft["description"])
        if status != "pending":
            for col, label, _kind in fields:
                v = draft.get(col)
                st.text(f"{label}: {v if v not in (None, '') else '—'}")
            return

        with st.form(key=f"{kind}_form_{draft['id']}"):
            edits: dict[str, object | None] = {}
            for col, label, ftype in fields:
                wkey = f"{kind}_{draft['id']}_{col}"
                cur = draft.get(col)
                if ftype == "int":
                    raw = st.text_input(label, value=str(cur) if cur is not None else "", key=wkey)
                    try:
                        edits[col] = int(raw) if raw.strip() else None
                    except ValueError:
                        edits[col] = None
                elif ftype == "float":
                    raw = st.text_input(label, value=str(cur) if cur is not None else "", key=wkey)
                    try:
                        edits[col] = float(raw) if raw.strip() else None
                    except ValueError:
                        edits[col] = None
                elif ftype == "enum_change_kind":
                    idx = _CHANGE_KINDS.index(cur) if cur in _CHANGE_KINDS else 0
                    val = st.selectbox(label, _CHANGE_KINDS, index=idx, key=wkey)
                    edits[col] = val if val else None
                else:
                    val = st.text_input(label, value=cur or "", key=wkey)
                    edits[col] = val if val else None

            reject_reason = st.text_area(
                "Rejection reason (required to reject)",
                key=f"{kind}_reject_{draft['id']}",
                placeholder="e.g. Not actually a contract; restated old news.",
            )

            ca, cb, cc = st.columns(3)
            save = ca.form_submit_button("💾 Save", use_container_width=True)
            approve = cb.form_submit_button("✅ Approve", use_container_width=True, type="primary")
            reject = cc.form_submit_button("🚫 Reject", use_container_width=True)

        if save:
            ui_data.save_signal_draft_edits(kind, draft["id"], edits)
            st.toast("Saved.", icon="💾")
            st.rerun()
        if approve:
            ui_data.save_signal_draft_edits(kind, draft["id"], edits)
            try:
                live_id = ui_data.approve_signal_draft(
                    kind, draft["id"], reviewer=_analyst_name(),
                )
                st.toast(f"Approved → {live_id[:60]}", icon="✅")
                st.rerun()
            except Exception as e:
                st.error(f"Approve failed: {type(e).__name__}: {e}")
        if reject:
            if not reject_reason.strip():
                st.error("Please provide a rejection reason.")
            else:
                try:
                    ui_data.reject_signal_draft(
                        kind, draft["id"], reviewer=_analyst_name(),
                        reason=reject_reason.strip(),
                    )
                    st.toast("Rejected.", icon="🚫")
                    st.rerun()
                except Exception as e:
                    st.error(f"Reject failed: {type(e).__name__}: {e}")


def _render_contract_form(c: dict) -> None:
    title = (
        f"Contract: **{c.get('contractor') or '?'}** ← "
        f"_{c.get('customer') or '?'}_"
    )
    _render_signal_form(
        kind="contract", draft=c, title=title, fields=_CONTRACT_FORM,
    )


def _render_leadership_form(c: dict) -> None:
    title = (
        f"Leadership: **{c.get('person_name') or '?'}** → "
        f"_{c.get('new_role') or '?'}_ @ {c.get('organization') or '?'}"
    )
    _render_signal_form(
        kind="leadership_change", draft=c, title=title, fields=_LEADERSHIP_FORM,
    )


# ---------------------------------------------------------------------------
# View: dashboard (landing page)
# ---------------------------------------------------------------------------


_BUDGET_CAP_USD = 200.0  # Matches the user's stated monthly cap.


def render_dashboard() -> None:
    st.title("Dashboard")
    st.caption("Today's pipeline state, top of the queue, what's trending.")

    pending = ui_data.pending_highlights(limit=10)
    trending = ui_data.trending_countries(days=7, limit=10)
    health = ui_data.source_health()
    cost = ui_data.cost_this_month()
    cost_usd = ui_data.cost_to_usd(cost)

    n_total_articles = sum(h.last_24h for h in health)
    n_pending = len(ui_data.pending_highlights(limit=1000))
    n_stale_sources = sum(1 for h in health if h.is_stale)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pending review", f"{n_pending:,}")
    c2.metric("Articles last 24h", f"{n_total_articles}")
    c3.metric(
        "Stale sources",
        f"{n_stale_sources}",
        help="Sources that haven't produced an article in >14 days.",
        delta_color="inverse",
    )
    c4.metric(
        "Spend MTD",
        f"${cost_usd:.2f}",
        f"of ${_BUDGET_CAP_USD:.0f} cap",
        delta_color="off",
    )

    st.divider()

    col_l, col_r = st.columns([2, 1])

    with col_l:
        st.subheader("Pending review queue")
        st.caption("Highest confidence first. Click into a draft to act.")
        if not pending:
            st.info("Queue is clear. 🎉")
        for p in pending:
            with st.container(border=True):
                cols = st.columns([6, 1])
                with cols[0]:
                    badge = (
                        "🟢 high" if p.confidence == "high"
                        else "🟡 medium" if p.confidence == "medium"
                        else "🔴 low" if p.confidence == "low"
                        else "•"
                    )
                    st.markdown(f"{badge}  **{p.countries}**  ·  _{p.article_source}_")
                    if p.description:
                        st.caption(p.description[:160])
                with cols[1]:
                    if st.button("Open →", key=f"dash_open_{p.draft_id}"):
                        aid = ui_data.article_id_for_draft(p.draft_id)
                        if aid:
                            _go("article_review", selected_article_id=aid)

    with col_r:
        st.subheader("Trending countries · 7d")
        if not trending:
            st.caption("_no tagged articles yet — run an ingest._")
        for t in trending:
            with st.container(border=True):
                cols = st.columns([4, 1])
                cols[0].markdown(f"**{t.country}**")
                cols[0].caption(f"{t.central_count} central · {t.article_count} total")
                if cols[1].button("Brief", key=f"dash_brief_{t.country}"):
                    st.session_state["brief_country"] = t.country
                    _go("briefing")

    st.divider()
    st.subheader("Source health")
    healthy = [h for h in health if not h.is_stale]
    stale = [h for h in health if h.is_stale]
    if stale:
        st.warning(
            "**Stale (no article >14d):** " +
            ", ".join(f"`{h.source}` ({h.days_silent}d)" for h in stale)
        )
    if not healthy:
        st.caption("_no recent activity._")
    else:
        # Compact 4-column grid of green sources.
        cols_per_row = 4
        for i in range(0, len(healthy), cols_per_row):
            row_cols = st.columns(cols_per_row)
            for j, h in enumerate(healthy[i:i + cols_per_row]):
                with row_cols[j]:
                    st.markdown(f"🟢 **{h.source}**")
                    st.caption(
                        f"last 24h: {h.last_24h} · "
                        f"silent: {h.days_silent}d"
                    )


# ---------------------------------------------------------------------------
# View: world map
# ---------------------------------------------------------------------------


def render_map() -> None:
    st.title("World map")
    st.caption(
        "Each dot is a country with a partnership in our DB. Sized by "
        "partnership count, colored by partnership_strength average."
    )
    try:
        import folium
        from streamlit_folium import st_folium
    except ImportError:
        st.warning(
            "World map needs `folium` + `streamlit-folium`. "
            "Install with: `pip install folium streamlit-folium`."
        )
        return

    from space_monitor import db, geocode
    rows = []
    with db.connect(db.resolve_db()) as conn:
        try:
            rows = conn.execute(
                """
                SELECT country, COUNT(*) AS n,
                       AVG(COALESCE(partnership_strength, 0)) AS strength
                  FROM (
                    SELECT country_1 AS country, partnership_strength FROM partnership
                    UNION ALL
                    SELECT country_2 AS country, partnership_strength FROM partnership
                  )
                 WHERE country IS NOT NULL
                 GROUP BY country
                 ORDER BY n DESC
                """,
            ).fetchall()
        except Exception as e:
            st.error(f"DB error: {e}")
            return
    if not rows:
        st.info("No partnerships in the DB yet.")
        return

    m = folium.Map(location=[20, 0], zoom_start=2, tiles="cartodbpositron")
    for country, n, strength in rows:
        # Geocode against the capital (or biggest city, as a proxy when
        # capital isn't tagged in our gazetteer).
        hit = geocode.geocode(country, country) or geocode.geocode(country)
        if not hit:
            continue
        radius = max(4, min(25, int(n ** 0.5) * 2))
        color = "#1f77b4" if (strength or 0) < 5 else "#ff7f0e"
        folium.CircleMarker(
            location=[hit.lat, hit.lng], radius=radius,
            popup=folium.Popup(
                f"<b>{country}</b><br/>{n} partnerships<br/>"
                f"avg strength: {strength:.1f}", max_width=200,
            ),
            color=color, fill=True, fill_opacity=0.6,
        ).add_to(m)
    st_folium(m, width=None, height=600, returned_objects=[])


# ---------------------------------------------------------------------------
# View: search
# ---------------------------------------------------------------------------


def render_search() -> None:
    st.title("Search")
    st.caption("Full-text on article titles + descriptions. Filter by country, signal kind, status.")

    from space_monitor import db
    countries = ui_data.fetch_all_stats()
    cl, cm, cr = st.columns([3, 2, 2])
    q = cl.text_input("Search", key="search_q", placeholder="e.g. JAXA, lunar gateway, Vietnam…")
    country = cm.text_input("Country tag", key="search_country", placeholder="e.g. Japan")
    status = cr.selectbox(
        "Article status", ["any", "extracted", "fetched", "failed", "skipped_prefilter"],
        index=1, key="search_status",
    )

    sql = """
        SELECT a.id, a.source, a.url, a.title, a.published_at, a.status,
               d.confidence, d.country_1, d.country_2, d.description, d.id
          FROM news_article a
          LEFT JOIN partnership_draft d ON d.source_article_id = a.id
         WHERE 1=1
    """
    params: list = []
    if q:
        sql += " AND (a.title LIKE ? OR d.description LIKE ?)"
        like = f"%{q.strip()}%"
        params.extend([like, like])
    if country:
        sql += (" AND a.id IN (SELECT article_id FROM news_article_country "
                " WHERE country = ?)")
        params.append(country.strip())
    if status != "any":
        sql += " AND a.status = ?"
        params.append(status)
    sql += " ORDER BY COALESCE(a.published_at, a.fetched_at) DESC LIMIT 100"

    with db.connect(db.resolve_db()) as conn:
        try:
            rows = conn.execute(sql, params).fetchall()
        except Exception as e:
            st.error(f"DB error: {e}")
            return

    if not rows:
        st.caption("No matches. Try loosening filters.")
        return
    st.caption(f"{len(rows)} match(es) — newest first")

    for r in rows:
        aid, src, url, title, pub, st_status, conf, c1, c2, desc, did = r
        with st.container(border=True):
            cols = st.columns([6, 2, 1])
            cols[0].markdown(f"**{title or '(no title)'}**")
            cols[0].caption(url)
            if desc:
                cols[0].caption(desc[:180])
            cols[1].caption(f"📅 {(pub or '')[:10]}  ·  {src}")
            if c1 or c2:
                cols[1].caption(f"{c1 or '?'} ↔ {c2 or '?'}")
            if conf:
                cols[1].caption(f"conf {conf}")
            if did:
                if cols[2].button("Open", key=f"search_open_{aid}"):
                    _go("article_review", selected_article_id=aid)


def _advance(*, after_draft_id: int, fallback_source: str) -> None:
    """After approve/reject, jump to the next pending draft in the same source.
    If none, return to the source-detail view."""
    next_id = ui_data.next_pending_draft_id(after_draft_id, same_source_only=True)
    if next_id is None:
        st.info("No more pending drafts in this source.")
        _go("source_detail", selected_source=fallback_source)
        return
    next_article_id = ui_data.article_id_for_draft(next_id)
    if next_article_id:
        _go("article_review", selected_article_id=next_article_id)
    else:
        _go("source_detail", selected_source=fallback_source)


# ---------------------------------------------------------------------------
# View: country briefing
# ---------------------------------------------------------------------------


def render_briefing() -> None:
    from space_monitor import briefing as briefing_mod

    st.title("Country briefing")
    st.caption(
        "Synthesizes everything our pipeline knows about a country (articles, "
        "partnerships, contracts, leadership changes) into a markdown briefing "
        "suitable for a meeting. Cached per (country, ISO-week)."
    )

    countries = briefing_mod.known_countries()
    if not countries:
        st.warning("No tagged articles yet — run an ingest first.")
        return

    cl, cm, cr = st.columns([3, 1, 1])
    selected = cl.selectbox(
        "Country", countries,
        index=0, key="brief_country",
    )
    since_days = cm.number_input(
        "Window (days)", min_value=7, max_value=365, value=90, step=15,
        key="brief_since",
    )
    force = cr.checkbox("Bypass cache", value=False, key="brief_force")

    if st.button("Generate briefing", type="primary"):
        with st.spinner(f"Gathering signals for {selected}…"):
            try:
                result = briefing_mod.generate(
                    selected, since_days=int(since_days), force=force,
                )
            except Exception as e:
                st.error(f"Briefing failed: {type(e).__name__}: {e}")
                return
        st.session_state["last_briefing"] = result

    result = st.session_state.get("last_briefing")
    if result and result.country == selected:
        st.divider()
        cap_l, cap_r = st.columns([4, 1])
        cap_l.caption(
            f"_{result.country} · since {result.since} · "
            f"{result.article_count} articles · "
            f"{'cached this week' if result.from_cache else 'freshly generated'}_"
        )
        cap_r.download_button(
            "📥 Download .md",
            data=result.body_markdown,
            file_name=f"{result.country.lower().replace(' ', '_')}_brief_{result.since}.md",
            mime="text/markdown",
            use_container_width=True,
        )
        st.markdown(result.body_markdown)


# ---------------------------------------------------------------------------
# View: watchlist
# ---------------------------------------------------------------------------


def render_watchlist() -> None:
    from space_monitor import watchlist

    user = _analyst_name()
    st.title("Watchlist")
    st.caption(
        f"Star countries / orgs / partnership types you care about. "
        f"`space-monitor watchdigest` (or the **Generate digest** button below) "
        f"produces a markdown summary of new activity matching your stars over "
        f"the last 7 days. _Current user: `{user}`._"
    )

    entries = watchlist.list_for(user)

    with st.expander("Add to watchlist", expanded=not entries):
        ck, cv, cb = st.columns([1, 3, 1])
        kind = ck.selectbox("Kind", list(watchlist.KINDS), key="watch_kind")
        value = cv.text_input("Value", key="watch_value", placeholder="e.g. Vietnam, Airbus, Joint Venture")
        if cb.button("Add", use_container_width=True):
            if value.strip():
                ok = watchlist.add(user, kind, value.strip())
                st.toast("Added." if ok else "Already on your watchlist.", icon="⭐" if ok else "ℹ️")
                st.rerun()

    if not entries:
        st.info("Watchlist is empty.")
        return

    for e in entries:
        with st.container(border=True):
            cols = st.columns([1, 4, 1])
            cols[0].caption(e.kind)
            cols[1].markdown(f"**{e.value}**")
            if cols[2].button("Remove", key=f"watchrm_{e.id}"):
                watchlist.remove(e.id)
                st.rerun()

    st.divider()
    cb1, cb2 = st.columns([1, 1])
    if cb1.button("📨 Generate digest (last 7 days)", type="primary"):
        body = watchlist.build_digest(user, days=7)
        st.session_state["last_watchdigest"] = body

    body = st.session_state.get("last_watchdigest")
    if body:
        st.markdown(body)
        cb2.download_button(
            "📥 Download .md", data=body, file_name="watchlist_digest.md",
            mime="text/markdown",
        )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


_NAV = [
    ("dashboard", "🏠 Dashboard"),
    ("sources",   "📡 Sources"),
    ("briefing",  "📝 Country briefing"),
    ("map",       "🌍 World map"),
    ("search",    "🔎 Search"),
    ("watchlist", "⭐ Watchlist"),
]


def _render_nav() -> None:
    """Sidebar nav. Each row is a view. Buttons over radio so we can rerun
    cleanly without conflicting with the row-level row-click buttons."""
    with st.sidebar:
        st.markdown("### space-monitor")
        for view, label in _NAV:
            if st.button(label, key=f"nav_{view}", use_container_width=True):
                _go(view)


def main() -> None:
    if not _gate():
        return
    _init_state()
    _consume_token_from_url()
    msg = st.session_state.pop("_token_msg", None)
    if msg:
        ok, body = msg
        (st.success if ok else st.error)(body)
    _render_nav()
    view = st.session_state.view
    if view == "dashboard":
        render_dashboard()
    elif view == "sources":
        render_sources()
    elif view == "source_detail":
        render_source_detail()
    elif view == "article_review":
        render_article_review()
    elif view == "briefing":
        render_briefing()
    elif view == "map":
        render_map()
    elif view == "search":
        render_search()
    elif view == "watchlist":
        render_watchlist()
    else:
        st.error(f"Unknown view: {view}")


main()
