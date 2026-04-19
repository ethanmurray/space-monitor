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
underneath that briefing. UI is explicitly deferred (see P2 web-UI item).

## P0 — foundations for the briefing data layer

- [ ] **Country-tagging layer for every fetched article.** Add a
      `news_article_country` table: `(article_id, country, centrality)` where
      centrality is `central` (article subject) or `mentioned`. After every
      fetch — partnership or not — one cheap Haiku call returns the country
      list. Unlocks "show me everything about Vietnam in the last 90 days"
      without changing the extraction layer. Foundational for any per-country
      briefing query. ~1 day of work; <$0.001/article.

- [ ] **Multi-signal extractor** (replaces the binary partnership/not flow).
      Today every article either becomes a `partnership_draft` or is
      discarded — losing contracts, leadership moves, program milestones,
      strategy publications, budget announcements that are right there in
      the same articles we already paid to fetch. Replace with a router that
      tags each article as one or more of:
      `partnership`, `contract`, `program_milestone`, `launch`,
      `leadership_change`, `strategy_publication`, `budget_announcement`.
      Each gets its own small schema and draft table. Same review CLI shape.
      Start with just `partnership + contract + leadership_change` (richest
      three for the briefing use case) and add the rest as they prove
      worthwhile. **This is where the system gets 5-10x more useful per
      ingested article.**

- [ ] **Geocoding service** *(roadmap step 2)*. Replace the 42,905-row
      `LatLong Auto-Tagging` sheet with either (a) a queryable SQLite
      gazetteer behind a `geocode(city, country) -> (lat, lng)` function in
      `space_monitor/geocode.py`, or (b) a thin wrapper around Mapbox or
      Nominatim. Lets industry_company / launch_site / partnership-party
      records get coordinates at write time without copying the whole
      gazetteer around.

- [ ] **Reset / re-extract CLI.** When the prompt or schema changes, you need
      a clean way to wipe drafts and re-run extraction over already-fetched
      articles. Currently I reset by hand with raw SQL. Becomes critical when
      multi-signal extraction lands — you'll want to re-process the whole
      `news_article` history through the new extractor.

## P1 — quality + cost wins

- [ ] **`space-monitor review skipped` CLI.** Pair to the prefilter — list
      and inspect articles auto-skipped by the title classifier so the
      analyst can spot-check for misses. Should support `--source X` and
      `--since 7d` filters and show the model's stated reason alongside each
      title. Cheap to build (~30 lines).

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

- [ ] **Better partnership_id generation.** Currently uses a 4-char random
      nonce in `_generate_partnership_id` — fine for uniqueness, but the
      workbook's existing IDs are human-readable and stable. Consider
      `slug(party1)-slug(party2) <Type> <Year>` with a deterministic dedup
      check before insert.

- [ ] **Country field normalization.** `country_1` / `country_2` on the
      extract schema are `nullable_str`, not enum. The model occasionally
      emits values that aren't in the canonical country list — observed
      `"Multilateral"` on the SKAO SKA-Mid draft (a 16-country observatory
      project). Two ways to fix: (a) tighten the schema to enum-from-taxonomy
      (risk: schema gets large with ~200 countries × 2 columns + may push
      against the 16-nullable-fields cap), or (b) post-process at draft
      insert: validate country values against `country` table, write
      offending values into `review_notes` and null the field. Option (b) is
      probably cleaner.

- [ ] **Cost reporting CLI.** `space-monitor cost --since YYYY-MM-DD` that
      sums input/output/cache tokens per model from a `extraction_usage`
      audit table (not yet created — currently usage is logged to stdout
      only). Useful when scaling up.

- [ ] **Sonnet escalation widen.** Today escalation triggers only on
      `confidence == "low"`. Also escalate when the article is long
      (>3,000 tokens), or when Haiku says is_partnership=true but leaves
      both `country_1` and `country_2` null — that pattern correlates with
      sloppy extraction.

## P2 — broader source coverage

### Discovery (new article surfaces beyond polling known feeds)

- [ ] **Google News RSS-search adapter.** `https://news.google.com/rss/search?q=QUERY`
      returns up to 100 most-recent articles per query, no API key, free.
      Add a `GNewsSearchSource` that takes a list of query strings, dedupes
      against existing `news_article.url_hash`, and yields the rest.
      Suggested initial queries (~15): "space partnership", "satellite
      launch contract", "space cooperation agreement", "ground station
      agreement", "space technology transfer", "lunar mission", "earth
      observation satellite", "space agency announces", "satellite
      constellation", plus per-priority-country watchlist queries. **Catches
      the long tail of one-off mentions on sites we don't poll.** ~$0/month
      for the search itself; cost lives in the downstream extraction.

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

- [ ] **Defense / industry trade press**: defensenews.com,
      breakingdefense.com, payloadspace.com, spacepolicyonline.com,
      thespacereview.com, NASASpaceflight.com — most have RSS.

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

- [ ] **Web UI for the review queue.** CLI is fine for proof-of-life; a
      simple web page (FastAPI + a single HTMX template) where the analyst
      sees the source article and the proposed draft side-by-side and clicks
      approve / edit / reject would 5-10× analyst throughput.

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
