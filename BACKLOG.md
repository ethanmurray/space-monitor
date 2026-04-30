# space-monitor backlog

Open items in priority order. Items marked **DONE** are recorded for context.

---

## North star — the country briefing

Working hypothesis (see conversation log for the full brainstorm): the
deliverable a top space-industry leader actually wants is an on-demand
**country briefing** — state of play, recent activity, key actors, partnership
landscape, opportunity gaps — for any country they're traveling to. The data
substrate this needs is much wider than what we capture today (only one
`partnership_draft` per article; no country-tagging on the raw `news_article`
table; no signals beyond partnerships).

The P0/P1 work below is now organized around expanding the *data layer*
underneath that briefing. **Update**: with breadth at a comfortable plateau
(26 adapters, daily ingest live on Turso via GitHub Actions), the next
major phase is the **analyst UI** — see the new "Phase 2: analyst UI"
section near the end of this file for the planning thread.

## P0 — foundations for the briefing data layer

P0 cleared in this pass — see DONE for details.

## P1 — quality + cost wins

- [ ] **5% false-negative resample of prefilter skips.** Periodic job that
      picks a random 5% sample of `news_article` rows with
      `status='skipped_prefilter'` and runs them through the full extractor
      anyway. If any come back is_partnership=true, the prefilter let real
      partnerships through — alert and tune the classifier prompt.

- [ ] **First external-database connector** *(roadmap step 4)*. Pick one to
      prove the connector pattern. Two strong candidates:
      - **SIPRI Military Expenditure** (annual CSV download) → refresh
        `defense_spending` table without touching the workbook.
      - **UCS / CSIS Satellite Database** → refresh `space_asset` rows from
        an authoritative source instead of analyst hand-curation.
      The connector pattern (scheduled pull + diff + upsert + audit log) is
      reusable across all the remaining sources.

- [ ] **Backfill country tags on historical articles.** The country tagger
      runs as part of every fresh ingest, but ~80 articles ingested before
      it landed don't have tags. One-time `space-monitor tag-countries`
      run (with no `--limit`) covers it; cost is one Haiku call per
      article (~$0.0005 each).

- [x] ~~**Editable forms for non-partnership signals.**~~ Shipped. Live
      `contract` and `leadership_change` tables (migration 005),
      deterministic slug-based IDs, editable Streamlit forms with
      Save/Approve/Reject buttons. See DONE for details.

- [ ] **More signal kinds.** Router currently emits
      partnership/contract/leadership_change. Next-best additions:
      `program_milestone` (launches, IOC, FOC), `budget_announcement`
      (line-items in national appropriations), `strategy_publication`
      (national space policy releases). Each is a small typed schema +
      router enum entry.

## P2 — broader source coverage

### Discovery (new article surfaces beyond polling known feeds)

- [ ] **GDELT integration.** GDELT's Global Knowledge Graph already
      classifies news events worldwide by topic + country, updated every 15
      min, free, with SQL/BigQuery interface. Filter for space-related
      themes + recency, dedup against our DB, route through fetch+extract.
      Higher signal than raw search; complements Google News for breadth.

- [ ] **Domain mining from existing `news_article` URLs.** Parse outgoing
      links from cleaned article bodies; tally domains we don't yet have
      adapters for. Surface the top-N as adapter candidates. Cheap meta-
      analysis, runs as a periodic report.

- [ ] **Tavily / Exa search API.** Paid (~$0.01/query) but designed for LLM
      agents — returns clean snippets with the LLM-extraction step in mind.
      Better signal than raw Google search. Useful if Google News RSS proves
      too noisy.

- [ ] **Factiva integration (future-explore).** Owner has search-only
      access today (no API tier). Would meaningfully improve breadth for
      paywalled flagship outlets (WSJ/FT/Nikkei/Bloomberg) and non-English
      regional business press — exactly the gaps Google News covers
      thinnest. Two paths if access changes: (a) Dow Jones API tier
      (~$500-2000/mo) — clean adapter, same shape as other sources;
      (b) Factiva email-alert digest parsing — clunkier, but works with
      the existing search-only subscription. Re-evaluate if (i) noise
      ceiling on Google News forces a quality upgrade, or (ii) coverage
      gaps in non-English regional press become visible in country
      briefings.

### Sources to add as first-class adapters (focused on coverage gaps)

Today our 12 sources lean US/EU. The countries where partnerships are most
*interesting* are the ones we cover thinnest. Priority adds:

- [ ] **National space agencies**: ISRO (India), JAXA (Japan), KARI (South
      Korea), CNES (France), DLR (Germany), ASI (Italy), UK Space Agency,
      INPE (Brazil), UAE Space Agency, ROSCOSMOS, CSA (Canada), SANSA
      (South Africa), AEB (Brazil), philsa.gov.ph (Philippines), VNSC
      (Vietnam). Most have RSS or scrapeable indices; each is ~30 lines of
      code. Quality-rank by feed health before building all 15.

- [ ] **US military space**: Space Force public affairs, US Space Command,
      US Strategic Command (the workbook tally suggests these are
      under-covered).

- [ ] **Defense / industry trade press**: shipped — defensenews,
      breakingdefense, payloadspace, spacepolicyonline, nasaspaceflight
      all live with RSS. Only `thespacereview` still deferred (no RSS,
      monthly cadence; could use the ISRO-style two-step pattern when
      worth the time).

- [ ] **Academic feeds**: arXiv (specifically `astro-ph.IM` instrumentation
      and `eess.SP` signal processing) — capability tracking, not news. Free
      RSS. Useful for "is this country publishing on X?" signals.

- [ ] **Procurement portals** (deferred until pattern matures): SAM.gov
      (US), TED (EU), DASA (UK). Rich source of contracts but each is its
      own integration. May be more cost-effective via the multi-signal
      extractor catching contract announcements in news than scraping
      portals directly.

### Existing source-coverage items

- [ ] **Playwright-based fetcher** for Cloudflare-protected sites
      (SpaceWatch is the immediate one; SatelliteToday and gov.uk may also
      grow stricter). Run as a separate `fetch_browser()` path that the
      ingest CLI falls back to when the lightweight httpx fetcher hits a
      403/Cloudflare challenge.

- [ ] **africanews.space** — retry from a different network. DNS was
      unresolvable from this environment but the workbook credits 221 source
      rows to it, so worth recovering. May also need the browser fetcher.

- [ ] **Remaining sources without RSS** that workbook analysts cited heavily.
      Each needs a bespoke adapter (sitemap crawl, search API, or HTML scrape):
      - airbus.com (corporate; press-release page is JS-rendered)
      - thalesgroup.com (similar)
      - eoportal.org (satellite-mission encyclopedia, not news — better fit
        as a Space Assets connector under roadmap step 4)
      - pesco.europa.eu (EU portal; SSL cert error on probe — retry from
        another network)
      - mofa.go.jp (403s on default UA — needs Playwright or different UA),
        metoffice.gov.uk (mostly weather, lower partnership signal),
        nsmc.org.cn (govt sites)
      - unoosa.org (UN office; current site returns 404 on common paths)
      Cost-rank these against expected partnership yield before building.

- [ ] **Web UI for the review queue.** Superseded by the broader "Phase 2:
      analyst UI" planning thread near the bottom of this file — the
      review queue is one feature in a larger UI, not a standalone item
      anymore.

### Structured data sources beyond news

A second tier of authoritative, structured data — parallel to the news
pipeline rather than feeding it. Each becomes its own table joined into
the partnership / country views. Brainstormed 2026-04-30; sequencing
suggestion at the bottom.

- [ ] **UCS Satellite Database** (Union of Concerned Scientists). Quarterly
      CSV snapshot of every operational satellite (~7,500 rows) with
      `Operator`, `Country of Operator/Owner`, `Country of Contractor`,
      `Launch Site`, `Purpose`. Every multi-country/multi-operator row is
      a *de facto* partnership. Highest leverage single item — would let
      country briefings answer "show me every Japan-Italy joint satellite"
      deterministically instead of relying on LLM extraction. Single CSV
      ingest + new `satellite` table + join into partnership view. Est.
      ~30 min scaffolding. *Recommended first.*

- [ ] **UN OOSA Online Register of Space Objects.** Treaty-mandated; every
      state files a registration form per launched object with launching
      state, ownership, function, orbital params. Form structure has
      built-in international cooperation fields ("multiple launching
      states"). Public, downloadable CSV/XML. Catches every state-level
      partnership at the legal level. Slower-changing than UCS but more
      authoritative on bilateral state agreements.

- [ ] **SEC EDGAR earnings filings + transcripts.** For US-listed space
      pure-plays (Rocket Lab, Planet, Iridium, AST SpaceMobile, Spire,
      BlackSky, Intuitive Machines, Terran Orbital, Redwire, L3Harris,
      Lockheed Martin / Boeing / Northrop space segments) — 10-K, 10-Q,
      8-K, and especially **earnings call transcripts** contain dense,
      dated, attributed contract/partnership disclosures. Analysts ask
      "who else are you talking to?" and management answers with names.
      Same shape: **Companies House (UK)** for OneWeb/Inmarsat-era,
      **TSX** for MDA. Pull via SEC EDGAR API + transcript provider
      (Bamsec/Seeking Alpha — paid).

- [ ] **SAM.gov / USASpending.gov** (US federal contract awards). Every
      NASA, Space Force, NRO, NOAA contract: value, scope, type, prime +
      subs. Same data, two interfaces. Ground truth for the "contract"
      signal we're already extracting — instead of guessing dollar values
      from press releases, we'd have the actual obligated value. Plus
      **TED EU** for EU procurement, **DASA** for UK defense.

- [ ] **GCAT** (Jonathan McDowell's General Catalog of Artificial Space
      Objects). Comprehensive launch + payload registry going back to
      Sputnik. Free CSV download. Lower priority than UCS for active-
      partnership tracking but invaluable for historical context and for
      validating launch dates in our existing `space_asset` table.

- [ ] **CelesTrak TLE catalog** + **Space-Track.org** (US 18 SDS).
      Real-time orbital tracking. Useful for the "where is X right now"
      question but lower partnership signal than UCS / OOSA. Skip unless
      we add a "live operations" view.

- [ ] **FCC IBFS satellite licensing filings** (US). Foreign filings
      reveal non-US operators' US market entry — a partnership leading
      indicator. Bulk download available. Lower priority but
      complementary to OOSA.

- [ ] **Patent co-applicant filings** — USPTO + EPO + WIPO. Joint patent
      applications between two-country/two-org pairs are partnership
      evidence at the IP layer. Bulk APIs exist (USPTO PatentsView,
      EPO OPS). Lower priority — partnership granularity is finer than
      the briefing needs.

- [ ] **Crunchbase funding rounds** (free tier limited; paid tier ~$30/mo
      for the Pro API). Series A/B/C announcements for the "Portal-class"
      space-mobility startups (Portal Space Systems, Impulse Space,
      Starfish Space, Atomos, D-Orbit, Exotrail, Astroscale, Momentus,
      ClearSpace) — partnership-dense (every customer = a public
      partnership) but mostly *private*, so no SEC filings exist for them.
      Best signal for them is funding round + customer announcements.
      Trade press already covers most of these well; the marginal value
      is structured deal data (round amount, lead investor, customer
      names) the LLM can't reliably normalize from prose.

- [ ] **Conference programs / abstracts** — IAC, Space Symposium,
      SmallSat Conference. Programs are public PDFs with panel topics +
      participant orgs. Lower priority — already covered by the news
      pipeline when major announcements happen at the conference.

**Recommended sequencing** if/when we work on this layer:
1. UCS Satellite DB (easy CSV → immediate uplift to country pages)
2. SEC EDGAR + transcripts for ~10 US-listed space pure-plays
3. UN OOSA register (slow but most authoritative)
4. SAM.gov / TED EU contract awards

Each is a different table in our schema, so they fit cleanly alongside
the existing news-article pipeline rather than replacing it.

## P3 — long-term roadmap items

- [ ] **People extraction layer.** Add a `person_mention` table linking
      people → roles → orgs → countries. Extracted alongside the multi-
      signal pass. The "who runs ISRO right now" answer is the most stable
      anchor in this domain — orgs change names, programs come and go, but
      the person leading them at any moment is always relevant for "who do
      I meet on this trip" briefings.

- [ ] **Scoring rubrics as deterministic functions** *(roadmap step 5)*.
      Codify the Space Assets composite score (Coverage × Mass × Launch Year
      × Capability → Final) and Partnership Strength (type × model × mission
      lookup) as Python functions in `space_monitor/scoring.py`. Recompute on
      every refresh.

- [ ] **Multi-language extraction quality.** Mundogeo is Portuguese; if we
      add Russian (RIA Novosti) or Chinese (Xinhua) sources, validate that
      Haiku still extracts cleanly in those languages and that country
      normalization holds.

- [ ] **Retroactive backfill of historical articles.** The current pipeline
      only sees what RSS feeds currently expose (typically last 10-30 posts
      per source — see "Historical reach" below). To backfill 2020-2024
      partnership announcements, build a Wayback Machine + sitemap pull that
      enumerates older URLs and runs them through the same extract path.

- [ ] **Tests.** None today. The most useful first set:
      - `test_taxonomy.py` — round-trip extract → load → schema invariants.
      - `test_load.py` — workbook → SQLite row count matches per sheet.
      - `test_extract_schema.py` — assert the extract output schema validates
        on a frozen sample LLM response.
      Skip live-LLM tests; mock the Anthropic client.

- [ ] **Audit trail / observability.** Every approval and rejection should be
      timestamped in a separate `review_event` table so we can later answer
      "what fraction of Haiku high-confidence drafts get rejected?" — that
      number tells us when to retrain prompt or escalate by default.

---

## Historical reach of current sources

Per-source state after the bulk ingest run (`--max-candidates 50 --since 2026-01-01`):

| Source           | Articles | Skipped by prefilter | Drafts (pos / neg) | Oldest      | Newest      |
|------------------|----------|---------------------|--------------------|-------------|-------------|
| spacenews        | 24       | 0 (space-native)    | 8 / 16             | 2026-04-16  | 2026-04-19  |
| satnews          | 10       | 0                   | 5 / 5              | 2026-04-16  | 2026-04-19  |
| nasa             | 10       | 0                   | 2 / 8              | 2026-04-16  | 2026-04-17  |
| satellitetoday   | 10       | 0                   | 2 / 8              | 2026-04-16  | 2026-04-17  |
| esa              | 9        | 0                   | 3 / 6              | 2026-01-21  | 2026-04-17  |
| mundogeo         | 10       | 0                   | 0 / 10             | 2026-04-16  | 2026-04-17  |
| govuk            | 4 / 20   | 16                  | 0 / 4              | 2026-04-17  | 2026-04-19  |
| asianscientist   | 3 / 23   | 20                  | 0 / 3              | 2026-01-07  | 2026-04-13  |
| spacewatch       | 0\*      | —                   | —                  | —           | —           |
| **Total**        | **80**   | **36**              | **20 / 60**        |             |             |

\* SpaceWatch registered but blocked by Cloudflare; Playwright fetcher (P2)
will unblock.

**Practical ceiling on historical depth via RSS:** standard RSS/Atom feeds
expose only the most recent **~10–30 entries** per source — typically the last
**1–4 weeks** for high-volume sites (spacenews, gov.uk, satnews, mundogeo) and
up to **a few months** for low-volume sites (esa, asianscientist). Going
further back requires the *Retroactive backfill* item under P3 (Wayback
Machine + per-site sitemap crawl), which is the only path to recovering
pre-Jan-2026 announcements from high-volume feeds.

---

## DONE — for context

- **Multi-signal review forms.** Migration 005 adds the live target
  tables (`contract`, `leadership_change`) so contract / leadership-change
  drafts have somewhere to be promoted. `signals.approve_contract()` /
  `approve_leadership()` mirror `partnership_draft.approve()` with
  deterministic slug-based IDs. The article-review UI page now renders
  editable forms (Save / Approve / Reject) for each contract and
  leadership-change draft, alongside the existing partnership form.

- **Migration framework (K).** `src/space_monitor/migrations/` —
  numbered `*.sql` files + `schema_migration` recording table + idempotent
  runner that swallows duplicate-column / already-exists errors so it
  cohabitates with the prior CREATE TABLE / hand-rolled `_MIGRATIONS`
  paths. Migrations 001-004 ship in this pass.

- **Country-briefing engine (A) — the actual product.** `briefing.py`
  pulls articles + partnerships + contracts + leadership_changes for one
  country over a recency window, hands them to Claude Sonnet with a
  structured prompt, returns a markdown briefing
  (state-of-play / activity / actors / partnerships / contracts /
  leadership / open questions). Cached per `(country, ISO-week)` in
  `country_briefing` so re-asks within a week are free. Surfaces in
  `space-monitor brief <country>` CLI and the **Country briefing** tab in
  the UI.

- **Org/entity disambiguation (F).** `orgs.py` + `org` / `org_alias`
  tables. Bundled seed of ~36 well-known agencies and companies with
  aliases. `space-monitor orgs {seed,backfill,list-unknown}` CLI.
  `orgs.resolve()` is the single entry point — case-insensitive, falls
  back to `unknown` when nothing matches.

- **Article-cluster dedup (G).** `dedup.py` groups pending drafts by
  `(sorted country pair, partnership_year, partnership_type)`. Picks the
  highest-confidence newest draft as the cluster representative; exposes
  the source URL set so the UI can show "1 partnership, 5 articles."
  Toggleable on the source-detail page in the UI.

- **Cross-source dashboard (B) — new UI landing page.** Pending review
  queue (sorted by confidence × recency), trending countries last 7d
  (with one-click *Brief* button), source health grid (stale = >14d
  silent), MTD spend vs $200/mo cap. Six top-nav views via the sidebar:
  Dashboard, Sources, Country briefing, World map, Search, Watchlist.

- **World map view (C).** streamlit-folium choropleth-ish map of
  partnerships, sized by count and colored by avg partnership_strength.
  Lazy-imports folium so the dep is optional.

- **Search + filter chips (D).** Full-text on title + description,
  country tag filter, status filter. Returns up to 100 matches sorted
  newest-first; row-click opens the article review page.

- **Reviewer bulk actions (E).** UI: "Approve all HIGH-confidence" and
  "Reject all visible (with reason)" buttons in source-detail. CLI:
  `space-monitor review bulk {approve-high|reject} --reviewer X
  [--source X] [--since DATE] [--limit N] [--dry-run]`. Streamlit doesn't
  natively support keyboard event handlers, so the J/K shortcut idea is
  deferred — bulk buttons + filters cover the same productivity case.

- **Cost budget alarm (I).** `notify.cost_check()` + `space-monitor
  cost-alarm [--cap-usd 7] [--hours 24] [--post]`. Estimates last
  N-hours spend from `extraction_usage` against the Haiku 4.5 rate card,
  posts a Slack/Discord message via `NOTIFY_WEBHOOK_URL` when over cap.
  Exits non-zero so cron / GH Actions can react.

- **Source freshness monitor (J).** `notify.stale_sources()` +
  `space-monitor source-health [--threshold-days 14] [--post]`. Lists
  sources that haven't produced an article in > threshold days; same
  Slack post path as the cost alarm. Also visible at-a-glance on the
  dashboard.

- **Slack/Discord notifier (O).** `notify.py` + `space-monitor digest`.
  Webhook-agnostic (Slack-style `text` JSON or Discord-style `content`,
  switched via `NOTIFY_WEBHOOK_KIND`). The 24-hour digest message
  summarizes new articles, positives, contracts, leadership changes, and
  pending HIGH-confidence drafts.

- **Prompt regression fixture (L).** `tests/fixtures/golden/*.json`
  with two seed fixtures (NASA-JAXA Artemis MoU positive, Starlink
  product-release negative). `scripts/run_prompt_regression.py` patches
  `extract.extract` to return the fixture's `expected` payload and asserts
  the post-insert state. `--live` flag re-runs against the real API for
  periodic model-drift checks. `--record` captures a fresh fixture from
  a `{title, url, body}` skeleton.

- **Auth + Streamlit Cloud deploy spec (N).** `_gate()` in app.py reads
  `UI_PASSWORD` env: unset = open (localhost), set = shared-password
  prompt. `.streamlit/config.toml` + `secrets.toml.example` shipped;
  `secrets.toml` gitignored. Deploy notes added to README.

- **Watchlist + weekly digest (H).** `watchlist.py` + `watchlist`,
  `review_token` tables (migration 004). `space-monitor watch
  {list,add,remove}` + `space-monitor watchdigest [--days 7] [--post]`.
  UI tab `⭐ Watchlist` for managing entries and rendering the digest.

- **Approve/reject magic links (M).** `review_links.py` + `space-monitor
  review {mint,consume}` CLIs. `mint` produces a single-use URL-safe
  token; emits a clickable URL when `REVIEW_LINK_BASE_URL` is set,
  otherwise emits a `space-monitor review consume <token>` shell
  invocation. UI auto-consumes `?token=…` from the URL on load — gives
  Slack/email digests one-click action without forcing a deploy.

- **Country-tagging layer (P0 #1).** New `news_article_country` table
  `(article_id, country, centrality, tagged_at, tagger_model)`. Module
  `pipeline/country_tag.py` runs one cheap Haiku call per article that
  returns a structured list of `(country, central|mentioned)` pairs,
  schema-enforced against the canonical country list. Wired into
  `_ingest_one` after every successful fetch (independent of the
  extraction cap, so even articles we don't extract still get tagged).
  `space-monitor tag-countries` backfills already-fetched articles. Usage
  is logged to `extraction_usage` for the cost CLI.

- **Multi-signal extractor (P0 #2).** `pipeline/signals.py` adds:
  - `route()` — one Haiku call returning the set of signals present
    (`partnership`, `contract`, `leadership_change`).
  - `extract_contract()` / `extract_leadership_change()` — typed Haiku
    calls with their own JSON Schemas; Sonnet escalation when Haiku
    returns confidence='low'.
  - `persist_contract()` / `persist_leadership()` — write to
    `contract_draft` / `leadership_change_draft`; record the signal in
    `article_signal` so the inventory of "what kinds of signals does this
    article carry" is one query away.
  Wired into ingest after the partnership extractor — partnership is
  recorded in `article_signal` for uniformity, and the router's other
  signals each trigger their own extractor call. Per-source and cross-
  source ingest summaries now report `contracts=` and `leadership=`.

- **Geocoding service (P0 #3).** `space_monitor/geocode.py` exposes
  `geocode(city, country) -> GeoHit | None` (and `geocode_many()` for
  batch use). Backed by the bundled `city` gazetteer table. Lookup is
  case-insensitive on both `city` and `city_ascii`, prefers the most-
  populous match within the named country, then falls back to the most-
  populous match globally. External-fallback (Mapbox / Nominatim) can
  plug into `_lookup` later without changing call sites.

- **Reset / re-extract CLI (P0 #4).** `space-monitor reextract
  [--source X] [--since DATE] [--what drafts|tags|both] [--limit N]
  [--dry-run]`. Wipes drafts and/or country tags for in-scope
  `news_article` rows and re-runs the matching extractors. Surfaces the
  scope before destructive work via `--dry-run`. Ideal after prompt or
  schema changes.

- **`review skipped` CLI (P1).** `space-monitor review skipped
  [--source X] [--since DATE] [--limit N]` lists prefilter-rejected
  articles with the classifier's stated reason — analyst can spot-check
  for misses without writing SQL.

- **Country normalization at draft insert (P1).** `pipeline/drafts.py`
  validates `country_1`/`country_2` against the `country` table at
  `insert_draft` time. Non-canonical values (observed: `"Multilateral"`
  on the SKAO SKA-Mid draft) are nulled and a note is appended to
  `review_notes`. Cached set; falls back to the bundled taxonomy when
  the table is empty.

- **Sonnet escalation widened (P1).** `extract_with_escalation` now
  escalates on three triggers: low confidence (original), long article
  (>3,000 input tokens), and `is_partnership=true` with both country
  fields null. The trigger reason is recorded in `escalated_from` for
  later analysis.

- **Cost reporting CLI (P1).** New `extraction_usage` audit table
  `(recorded_at, model, kind, article_id, input_tokens, output_tokens,
  cache_read, cache_write)`. Every LLM call (extract, country_tag,
  signal_router, signal_*) writes a row. `space-monitor cost
  [--since DATE]` aggregates by `(model, kind)` and prints per-axis
  totals. Easy to roll up into a dollar figure offline.

- **Better partnership_id generation (P1).** Replaced the
  `secrets.token_hex(2)` random nonce with a deterministic slug-based
  ID: `<Slug1>-<Slug2>_<TypeSlug>_<Year>` plus an optional `_<n>`
  suffix only when the deterministic prefix collides with an existing
  row. Re-running the same draft now produces the same ID.

- Schema + workbook loader (roadmap step 1).
- News-monitoring + LLM extraction pipeline (roadmap step 3): SpaceNews →
  Claude Haiku → review queue.
- Sonnet escalation when Haiku confidence is low.
- 9 RSS source adapters: spacenews, nasa, esa, govuk, satnews,
  satellitetoday, mundogeo, asianscientist, spacewatch (last is registered
  but blocked).
- Space-domain-only filter in the extraction prompt — caught two false
  positives during testing (Strait of Hormuz statement; plant-biology paper).
- Prompt caching working (verified via `cache_read_input_tokens` ≈ 7.3K per
  request after the first).
- Review CLI (`list`, `show`, `approve`, `reject`) with auto-promotion into
  the live `partnership` table and possible-duplicate flagging.
- **LLM title-relevance classifier** (`pipeline/prefilter.py`) batched through
  Haiku, three-way verdict (yes/no/uncertain), per-source opt-in via
  `Source.prefilter_required`. Eval fixture in `scripts/eval_prefilter.py`
  passes 50 YES / 30 NO / 8 ambiguous (0 false negatives, 0 false positives,
  0 premature 'no's).
- `--since YYYY-MM-DD` flag on `space-monitor ingest` to drop pre-cutoff
  candidates before fetch.
- Bulk ingest of all 8 working sources for 2026 — 80 articles in DB, 20
  positive partnership drafts pending review. See "Historical reach" below.
- `--source all` on `space-monitor ingest` (iterates every non-disabled
  adapter; SpaceWatch marked `disabled=True`) and `--rate-limit-secs N`
  polite delay between fetches within a source. Cross-source summary table
  prints at end of run.
- Crontab entry documented in `README.md` for unattended daily ingest.
  Notifications: log file is the chosen delivery — grep for `positives=` in
  `/var/log/space-monitor-ingest.log` to spot runs with new pending drafts.
- **First non-RSS scraper: SKAO** (`pipeline/sources/skao.py`). Discovers
  articles by parsing the paginated `/en/news` index for
  `/en/news/<id>/<slug>` URLs + inline `Month YYYY` dates, then runs through
  the same fetch + extract path as RSS sources. Verified end-to-end on the
  one 2026 article currently published.
- **Second non-RSS scraper: EU SST** (`pipeline/sources/eusst.py`). Same
  pattern as SKAO but for `/newsroom/news/<slug>` URLs with `DD Month YYYY`
  dates. Single index page reaches back ~5 years (low post frequency).
- **Third non-RSS scraper: DG DEFIS** (`pipeline/sources/disdg.py`).
  European Commission DG for Defence Industry and Space. Article URLs
  embed the date directly in the slug (`/<topic>-YYYY-MM-DD_en`), so no
  inline HTML date parsing. ~10 articles per page, ~37 pages of history.
  Verified end-to-end: 5 of 10 most recent 2026 articles extracted, 1
  high-confidence positive (EU-Japan defence industry dialogue).
- Source registry now: **12 adapters total, 11 working** — 9 RSS
  (spacenews, nasa, esa, govuk, satnews, satellitetoday, mundogeo,
  asianscientist, spacewatch) + 3 scrapers (skao, eusst, disdg). SpaceWatch
  marked `disabled=True` awaiting the Playwright fetcher item in P2.
- **Decoupled from the source xlsx.** The runtime now bootstraps from
  bundled `data/taxonomy.json` + `data/seed/partnership.csv` (3 MB, 7,614
  rows × 27 cols — `description` excerpt dropped to halve size; the full
  workbook still recoverable via `space-monitor load <xlsx>` for anyone
  with a copy). New `space-monitor bootstrap` command initializes a fresh
  DB with no xlsx required. `scripts/export_seed.py` regenerates the seed
  CSV from a fully-loaded DB.
- **Google News RSS-search adapter (gnews)** — discovery surface beyond
  known feeds. Fans out across ~30 queries (12 English topic queries,
  8 localized topic queries in fr/de/ja/ko/pt-BR/es/it/ar, 12 country
  watchlist queries for under-covered geographies). Decodes opaque
  `news.google.com/rss/articles/<token>` URLs to real publishers via the
  `googlenewsdecoder` library. Per-source rate-limited (1s decode interval).
  Caught a Canada-South Korea space cooperation agreement on first live run
  from a publisher (spaceq.ca) we don't poll directly — the breadth win.
- **Two new agency adapters: ASI and CNES.** ASI via RSS (50 entries
  Italian); CNES via HTML scrape of `cnes.fr/communiques` with French
  inline date parsing. CNES extraction immediately surfaced a France-Italy
  partnership on the JUICE/MAJIS instrument.
- **Source registry now: 15 adapters, 14 working.** SpaceWatch still
  disabled (Cloudflare). Skipped this round: ISRO/KARI (paths returned
  404 — sites likely restructured), JAXA (WAF blocks default UA — needs
  Playwright), UAE Space Agency / INPE (HTML structure needs more probing).
- **7 more adapters** (round 4 of source expansion):
  - **Trade press (no prefilter):** payloadspace, nasaspaceflight (NSF),
    spacepolicyonline.
  - **National agency (no prefilter):** philsa (Philippines).
  - **Broader/noisier (prefilter required):** breakingdefense,
    defensenews, sansa.
  All 7 RSS-based; built from a parallel probe batch. Live-verified each.
  Defense-news prefilter run skipped 6/8 obvious non-space (Iran weapons
  delays, Bluetooth carrier tracking) and let through real signals (Space
  Force orbital AMTI deals, Australia defense strategy).
- **Source registry now: 22 adapters, 21 working.** SpaceWatch still
  disabled. Still skipped: ISRO/KARI (current site URLs unknown), JAXA
  (WAF blocks default UA — needs Playwright), DLR (RSS dead), UAE Space
  Agency / INPE (HTML index returns content but article-link pattern
  unclear without DOM inspection), thespacereview, ROSCOSMOS, CSA, ISA
  Israel (all RSS endpoints failed). Re-attempt these in a future round.
- **Round 5: 3 more adapters** — CSA (Canadian Space Agency, RSS at
  ``/rss/default_eng.xml``), INPE (Brazilian, HTML scrape with DD/MM/YYYY
  dates and partnership-heavy content), ISA (Israel Space Agency, HTML
  scrape with numeric article IDs and DD.MM.YYYY dates). ISA's first live
  extraction surfaced a high-confidence Israel-US partnership.
- **`_rss.py` base class refactored** to fetch via ``httpx`` (browser UA,
  follow_redirects=True, certifi-backed SSL) instead of letting feedparser
  do its own urllib request. Fixes CSA's ``CERTIFICATE_VERIFY_FAILED`` (gov
  of Canada CA chain not in Python's bundled urllib root store) and is
  more permissive in general. All 16 existing RSS adapters re-validated.
- **Source registry now: 25 adapters, 24 working.** Still deferred:
  - **JS-rendered** (need Playwright in P2): UAE Space Agency, KARI, DLR
  - **No dated news index** (would need per-article date fetching): ISRO
  - **Geo-blocked from this environment**: ROSCOSMOS (403 + expired SSL on
    en.roscosmos.ru)
  - **No RSS feed**: thespacereview
- **Round 6: ISRO adapter** — ISRO has no RSS, no dated index, and articles
  are flat ``<Title>.html`` files at the site root. Adapter does a two-step
  discovery: (1) fetch homepage, slice the "Latest News" section, extract
  ``[A-Z][A-Za-z0-9_]+\.html`` hrefs from inside the slice (avoids
  picking up evergreen pages like ``Careers.html``); (2) for each
  candidate, GET the article body and parse the first
  ``Month DD, YYYY`` match for the publication date. ~12 extra HTTP
  requests per ingest run, bounded. First live run surfaced an
  **ISRO-AIIMS New Delhi Framework Memorandum** as a high-confidence
  India-India partnership.
- **Source registry now: 26 adapters, 25 working.**
- **Turso (libSQL) backend wired in.** ``--db`` flag + ``TURSO_DATABASE_URL``
  env var resolve to either a local SQLite path or a remote libsql URL;
  the codebase stays sqlite3-flavoured via a tiny ``_LibsqlAdapter``
  wrapper that auto-tuples list params (libsql_experimental is strict
  where sqlite3 is permissive). FK enforcement only applies to local
  SQLite; Turso doesn't honour the PRAGMA. Bootstrap rewritten to use
  chunked multi-row INSERTs (~500 rows per round-trip) — bootstrapping
  the 7,614-row partnership seed against a US-East Turso DB takes 80 s
  vs minutes for per-row execute.
- **GitHub Actions workflows** (``.github/workflows/``):
  - ``bootstrap.yml`` — manual one-time DB init.
  - ``daily-ingest.yml`` — runs ``space-monitor ingest --source all`` at
    13:00 UTC daily. Idempotent (RSS feeds carry the last ~10–30 entries
    + url_hash dedup). Three secrets required: ``ANTHROPIC_API_KEY``,
    ``TURSO_DATABASE_URL``, ``TURSO_AUTH_TOKEN``. Setup steps in README.

---

## Phase 2: analyst UI (planning thread, in progress)

**Status:** breadth work has reached a comfortable plateau (26 adapters,
25 working; daily ingest live on Turso via GitHub Actions; cost ~$30-60/mo
infra-free). Marginal value of more adapters is small — Google News
discovery already catches the long tail. Time to design the UI layer.

The UI is what the analyst (or the traveling government leader) actually
*uses*. Today the system produces structured drafts in Turso; nothing
exposes them outside `space-monitor review list`. The UI is the bridge.

**Open design questions** (to be resolved in the planning conversation):

- Audience: is it just for the owner, or for a team / customers?
- Latency tolerance: pre-built static briefings vs on-demand live
  generation?
- Coverage shape: all ~200 countries equally, or a watchlist of priority
  countries deeply?
- The five product shapes from the earlier brainstorm (CLI briefer,
  static per-country pages, watchlist+digest, hosted multi-user product,
  AI-first on-demand) — which fits best?
- Tech stack: FastAPI + HTMX, Next.js + Supabase, plain Python + Jinja
  static-site, Streamlit, something else?

**Pre-existing constraints from the data layer:**

- Drafts and articles already live in Turso → any UI can read directly via
  libsql / HTTP API.
- Country-tagging layer (P0 above) is *still un-built*. Without it, "show
  me everything about Japan" queries can only join through partnership
  rows. The UI design should account for this — either build country-
  tagging first, or design around partnership-only filtering and add
  per-article country tags later.
- Multi-signal extractor (P0 above) is also un-built. Today every UI view
  of "what's happening" is filtered through the partnership lens.

### Phase 2 v1 — SHIPPED

The inflow control panel:
- **Source registry** (`src/space_monitor/data/sources.yaml`) — 53 entries
  capturing every source ever considered: 25 working + 1 disabled +
  ~10 blocked + ~17 planned/deferred/rejected. Status, type, comment,
  workbook tally, coverage focus per entry. Hand-edited; auditable in PRs.
- **Streamlit UI** (`src/space_monitor/ui/`) launched via `space-monitor ui`.
  Three views: Sources (registry + live stats), Source detail (stats cards
  + article browser), Article review (body + draft fields + on-demand
  "Translate to English" Claude call).
- Stack: Streamlit + PyYAML + the existing Anthropic SDK + Turso. Local-
  only for now (`--port 8501 --host 127.0.0.1` defaults). Streamlit
  Community Cloud deploy is one button when wanted.

### Phase 2 v2 — SHIPPED
All items shipped in this pass. See the DONE section above for details
on the dashboard, briefing generator, search, map, watchlist, auth,
deploy spec, and editable forms.
