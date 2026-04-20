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

    cl, cr = st.columns([1, 1])
    cl.checkbox(
        "Only relevant (is_partnership = true)",
        key="only_relevant",
        help="Off shows every fetched article; on shows only those flagged "
        "as a real partnership by the extractor.",
    )
    cr.checkbox(
        "Show extractor summary instead of headline",
        key="show_summary",
        help="Toggle the article preview between the original RSS title and "
        "the extractor's one-sentence description from the partnership_draft.",
    )

    articles = ui_data.list_articles(
        src, only_relevant=st.session_state.only_relevant, limit=200
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
                    st.caption("(no draft)")


# ---------------------------------------------------------------------------
# View: article review
# ---------------------------------------------------------------------------


_DRAFT_FIELDS = [
    ("partnership_year",     "Year"),
    ("partnership_type",     "Type"),
    ("level_of_commitment",  "Commitment"),
    ("relationship_type",    "Relationship"),
    ("business_model",       "Model"),
    ("mission_type",         "Mission type"),
    ("primary_mission",      "Primary mission"),
    ("country_1",            "Country 1"),
    ("organization_1",       "Org 1"),
    ("company_1",            "Company 1"),
    ("country_2",            "Country 2"),
    ("organization_2",       "Org 2"),
    ("company_2",            "Company 2"),
]


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
        translated_key = f"translation_{article_id}"
        if st.button("🌐 Translate to English"):
            try:
                with st.spinner("Translating via Claude…"):
                    st.session_state[translated_key] = translate.translate_to_english(
                        article.cleaned_text or ""
                    )
            except Exception as e:
                st.error(f"Translation failed: {type(e).__name__}: {e}")

        if translated_key in st.session_state:
            st.info("Showing English translation. Original below.")
            st.write(st.session_state[translated_key])
            with st.expander("Original text"):
                st.write(article.cleaned_text or "(no body)")
        else:
            st.write(article.cleaned_text or "(no body — fetch may have failed)")

    # -------- RIGHT: draft fields ---------
    with draft_col:
        st.subheader("Extracted draft")
        d = article.draft
        if not d:
            st.info("No draft for this article yet.")
            return

        st.caption(
            f"Draft #{d['id']}  ·  status `{d['draft_status']}`  ·  "
            f"confidence `{d.get('confidence') or '—'}`  ·  "
            f"model `{d.get('extractor_model')}`"
        )
        if d.get("possible_duplicate_of"):
            st.warning(
                f"⚠ Possible duplicate of existing partnership "
                f"`{d['possible_duplicate_of']}`"
            )

        if d.get("description"):
            st.markdown(f"**Summary:** _{d['description']}_")

        # Read-only field grid for now. Editable form is the next iteration.
        for col, label in _DRAFT_FIELDS:
            v = d.get(col)
            st.text(f"{label}: {v if v not in (None, '') else '—'}")


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
