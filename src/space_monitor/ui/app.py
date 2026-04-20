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
        st.session_state.view = "sources"
    if "only_relevant" not in st.session_state:
        st.session_state.only_relevant = True
    if "hide_skipped" not in st.session_state:
        st.session_state.hide_skipped = True
    if "show_summary" not in st.session_state:
        st.session_state.show_summary = False


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

    cl, cm, cr = st.columns([1, 1, 1])
    cl.checkbox(
        "Only relevant (is_partnership = true)",
        key="only_relevant",
        help="Off shows every fetched article; on shows only those flagged "
        "as a real partnership by the extractor.",
    )
    cm.checkbox(
        "Hide prefilter-skipped",
        key="hide_skipped",
        value=True,
        help="The LLM title classifier auto-skips obvious non-space articles "
        "before extraction (typically ~85% on noisy sources like gov.uk). "
        "Hidden by default; uncheck to spot-check what the classifier "
        "rejected.",
    )
    cr.checkbox(
        "Show extractor summary instead of headline",
        key="show_summary",
        help="Toggle the article preview between the original RSS title and "
        "the extractor's one-sentence description from the partnership_draft.",
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
        st.subheader("Extracted draft")
        d = article.draft
        if not d:
            st.info("No draft for this article yet.")
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
# Dispatcher
# ---------------------------------------------------------------------------


def main() -> None:
    _init_state()
    view = st.session_state.view
    if view == "sources":
        render_sources()
    elif view == "source_detail":
        render_source_detail()
    elif view == "article_review":
        render_article_review()
    else:
        st.error(f"Unknown view: {view}")


main()
