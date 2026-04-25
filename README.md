# space-monitor

Automation foundation for the Space Dashboard — a hand-curated intelligence
binder (`Space_Dashboard_Hardcopy.xlsx`) used by analysts who track
international space partnerships and cooperation.

This codebase replaces the manual parts of that workflow with:

1. A typed **taxonomy** + **SQLite loader** that turns the workbook into a
   normalized, queryable database.
2. A **news-monitoring pipeline** that ingests space-domain news from RSS
   feeds and HTML scrapers, runs every fetched article through a
   **country-tagger** (so any article is queryable by country), then routes
   it to one or more **typed signal extractors** (partnership / contract /
   leadership_change), and queues each draft for analyst approval.
3. A **CLI + Streamlit review loop** that promotes approved drafts into the
   live tables.

For the full per-sheet analysis of the source workbook and the rationale
behind which parts to automate, see `Space_Dashboard_Summary.md`. For the
prioritized to-do list and a snapshot of what's done, see `BACKLOG.md`.

---

## Quick start

```bash
# 1. Install
pip install -e .

# 2. API key — required for the pipeline (ingest / extract).
#    Read from .env (gitignored). Copy the example and paste your key.
cp .env.example .env
$EDITOR .env  # set ANTHROPIC_API_KEY=sk-ant-...

# 3. One-time: initialize the database from bundled seed data.
#    Creates ./space_monitor.db with the schema, taxonomy, and the
#    historical `partnership` table (used for duplicate detection and
#    the prefilter eval). No xlsx required.
space-monitor bootstrap

# 4. Discover + extract partnership drafts from one source
space-monitor ingest --source spacenews --max-candidates 5

# 5. Review what was found
space-monitor review list
space-monitor review show <draft-id>
space-monitor review approve <draft-id> --reviewer your-name
space-monitor review reject  <draft-id> --reviewer your-name --reason "…"
```

The pipeline runs end-to-end with just steps 1–5. The bundled
`data/taxonomy.json` + `data/seed/partnership.csv` provide everything the
runtime code needs; live news sources + Claude provide the rest.

## Optional: refresh from the source workbook

`Space_Dashboard_Hardcopy.xlsx` is **not** committed (it's analyst-curated
proprietary data, and the bundled seed CSV covers what the code needs). Run
these only when the workbook itself is updated and you want to refresh the
bundled artifacts:

```bash
# Regenerate data/taxonomy.json from the workbook's Data Validation Lists sheet.
space-monitor extract-taxonomy Space_Dashboard_Hardcopy.xlsx

# Load all 17 sheets (full data, including the 60K-row historical tables
# that don't ship in the seed: space_assets, industry, defense_spending,
# etc.). Overwrites your space_monitor.db.
space-monitor load Space_Dashboard_Hardcopy.xlsx

# After either of the above, regenerate the bundled seed CSVs:
python scripts/export_seed.py
```

What `load` unlocks beyond what `bootstrap` provides:
- The full press-release `description` column on partnerships (dropped from
  the seed for size).
- Direct queries against the historical reference tables — space_assets,
  industry_company, defense_spending, investment_outlook, industrial_base
  scores, vc_deals, the city gazetteer, etc.

The pipeline itself does not need any of this to ingest news and produce
drafts.

---

## Architecture

```
┌──────────────────────┐           ┌─────────────────────────┐
│ Space_Dashboard_     │           │  Live news sources       │
│ Hardcopy.xlsx        │           │  (12 adapters)           │
└──────────┬───────────┘           └────────────┬─────────────┘
           │ space-monitor load                 │ space-monitor ingest
           ▼                                    ▼
┌──────────────────────┐           ┌─────────────────────────┐
│  Workbook tables     │           │  Pipeline tables         │
│  (~24 tables, 80     │           │  - news_article          │
│   rows of taxonomy   │           │  - partnership_draft     │
│   + 60K+ data rows)  │           │                          │
└──────────┬───────────┘           └────────────┬─────────────┘
           │                                    │
           └─────────────┬──────────────────────┘
                         ▼
                ┌─────────────────┐       ┌────────────────────┐
                │  partnership    │ ◄──── │  Analyst review    │
                │  (live table)   │       │  CLI               │
                └─────────────────┘       └────────────────────┘
```

The workbook tables are **rebuilt** on every `space-monitor load`. The
pipeline tables (`news_article`, `partnership_draft`) **persist** across
loads — they accumulate state and are only ever appended to.

The pipeline itself:

```
sources.iter_candidates()  ──▶  prefilter (LLM, optional, per-source)
                                       │
            skipped (audit row) ◀──────┤────▶ fetch.fetch() ──▶ news_article
                                                                        │
                                                                        ▼
                                              country_tag.tag()  ──▶ news_article_country
                                                                        │
                                                                        ▼
                                              extract.extract_with_escalation()
                                                  - claude-haiku-4-5 first
                                                  - sonnet-4-6 escalates on:
                                                       low confidence
                                                       long article (>3K input tok)
                                                       partnership w/ no countries
                                                                        │
                                                                        ▼
                                                       drafts.insert_draft()
                                                                        │  ──▶ partnership_draft
                                                                        ▼
                                              signals.route()  ──▶ list of extra signals
                                                                        │
                                              ┌─────────────────────────┼─────────────────────────┐
                                              ▼                         ▼                         ▼
                                       extract_contract()       extract_leadership_change()    (future kinds)
                                              │                         │
                                              ▼                         ▼
                                        contract_draft           leadership_change_draft
                                                          (status='pending')
```

Every LLM call (extract, country_tag, signal_router, signal_*) writes a row to
`extraction_usage` for cost reporting via `space-monitor cost`.

---

## Sources

26 adapters registered, 25 working. One disabled awaiting the Playwright
fetcher (P2 in BACKLOG.md).

| Adapter         | Type      | Workbook tally | Prefilter | Notes |
|-----------------|-----------|----------------|-----------|-------|
| `nasa`          | RSS       | 413            | —         | `/news-release/feed/` |
| `spacenews`     | RSS       | 250            | —         | High-volume, space-native |
| `skao`          | scraper   | 208            | —         | SKA Observatory; HTML index |
| `govuk`         | RSS (Atom)| 198            | **yes**   | Mostly non-space; classifier filters |
| `spacewatch`    | RSS       | 156            | —         | **Disabled** — Cloudflare blocks fetch |
| `eoportal`      | —         | 132            | —         | (Reference DB, not news; deferred) |
| `eusst`         | scraper   | 126            | —         | EU SST; ~5y history per index page |
| `airbus`        | —         | 120            | —         | (Corporate JS; deferred) |
| `esa`           | RSS       | 112            | —         | Lower-volume official feed |
| `asianscientist`| RSS       | n/a            | **yes**   | Mostly non-space; classifier filters |
| `mundogeo`      | RSS       | n/a            | —         | Brazilian Portuguese; Claude handles |
| `satellitetoday`| RSS       | n/a            | —         | |
| `satnews`       | RSS       | n/a            | —         | |
| `disdg`         | scraper   | 50             | —         | EU DG DEFIS; ~370 articles of history |
| `asi`           | RSS       | n/a            | —         | Italian Space Agency; Italian-language |
| `cnes`          | scraper   | n/a            | —         | French space agency; French-language |
| `gnews`         | search    | n/a            | **yes**   | Google News RSS-search across ~30 queries (English topics + 8 localized + 12 country watchlist). Decodes Google redirect URLs to real publishers. **Discovery surface beyond known feeds.** |
| `payloadspace`  | RSS       | n/a            | —         | Daily commercial-space industry trade press |
| `nasaspaceflight` | RSS     | n/a            | —         | Deep-technical launch and program journalism |
| `spacepolicyonline` | RSS   | n/a            | —         | US space policy + congressional action |
| `philsa`        | RSS       | n/a            | —         | Philippine Space Agency (newer, partnership-seeking) |
| `sansa`         | RSS       | n/a            | **yes**   | South African National Space Agency (mixed content) |
| `breakingdefense` | RSS     | n/a            | **yes**   | Defense industry trade press (mostly non-space; prefilter culls) |
| `defensenews`   | RSS       | n/a            | **yes**   | Same posture as Breaking Defense |
| `csa`           | RSS       | n/a            | —         | Canadian Space Agency (Lunar Gateway, Canadarm) |
| `inpe`          | scraper   | 67             | —         | Brazilian INPE — Portuguese; partnership-heavy content |
| `isa`           | scraper   | n/a            | —         | Israel Space Agency — English |
| `isro`          | scraper   | n/a            | —         | Indian Space Research Organisation — discovers articles from homepage Latest News, parses date from each article body |

`prefilter_required = True` routes that source's titles through a batched LLM
classifier (`pipeline/prefilter.py`) before any fetch. Per-source opt-in
keeps the cheap LLM call off the space-native feeds.

---

## CLI reference

### Workbook

```bash
space-monitor extract-taxonomy <xlsx>          # regenerate data/taxonomy.json
space-monitor load <xlsx> [--db PATH]          # rebuild workbook tables
```

### Ingest

```bash
space-monitor ingest --source <name|all> \
                     [--db PATH] \
                     [--max-candidates N]    \
                     [--max-extractions N]   \
                     [--since YYYY-MM-DD]    \
                     [--rate-limit-secs N]
```

- `--source all` iterates every adapter where `disabled = False`.
- `--max-extractions` is a per-source cost cap.
- `--since` drops candidates with `published_at` before the cutoff (no-op for
  candidates whose source can't extract a date).
- `--rate-limit-secs` is the polite delay between fetches *within a source*
  (only applies when an actual network fetch happens — cached / dedup'd
  articles are skipped without delay).

### Review

```bash
space-monitor review list   [--limit N]
space-monitor review show   <draft-id>
space-monitor review approve <draft-id> --reviewer <name> [--notes "…"]
space-monitor review reject  <draft-id> --reviewer <name> --reason "…"
space-monitor review skipped [--source X] [--since YYYY-MM-DD] [--limit N]
```

`approve` inserts the draft into the live `partnership` table with a
deterministic, slug-based `partnership_id` (`<Org1>-<Org2>_<Type>_<Year>`,
collision-suffixed when needed), then sets the draft's
`draft_status='approved'` and links it via `promoted_partnership_id`.
`reject` is non-destructive — the draft stays in the DB with
status='rejected' for audit. `skipped` lists prefilter-skipped articles for
spot-checking the title classifier.

### Backfill / re-run

```bash
space-monitor tag-countries [--source X] [--limit N] [--retag]
space-monitor reextract     [--source X] [--since YYYY-MM-DD] \
                            [--what drafts|tags|both] [--limit N] [--dry-run]
```

`tag-countries` runs the country-tagger over already-fetched articles that
don't have tags yet (or, with `--retag`, over all articles in scope). Use
this once to backfill the country layer for articles ingested before the
tagger landed.

`reextract` wipes drafts and/or country tags for in-scope articles and
re-runs the extraction. Use after prompt or schema changes — it lets you
push the new extractor over the historical news_article rows without
re-fetching anything.

### Cost reporting

```bash
space-monitor cost [--since YYYY-MM-DD]
```

Aggregates the `extraction_usage` audit table by `(model, kind)` —
calls, input tokens, output tokens, cache reads/writes. `kind` covers
`extract`, `country_tag`, `signal_router`, `signal_contract`,
`signal_leadership_change`.

### UI

```bash
space-monitor ui [--port 8501] [--host 127.0.0.1]
```

Launches the Streamlit analyst UI. Six views via the sidebar:

- **🏠 Dashboard** — pending review queue (sorted by confidence),
  trending countries last 7d, source health, MTD spend vs $200/mo cap.
- **📡 Sources** — registry of every source ever considered, joined
  with live stats. Drill into a source for its article browser + bulk
  approve/reject toolbar.
- **📝 Country briefing** — pick a country, get a Sonnet-generated
  markdown briefing synthesized from articles, partnerships, contracts
  and leadership changes for that country. Cached per (country, ISO-week).
- **🌍 World map** — folium map of partnerships, sized by count and
  colored by avg partnership_strength. (Optional dep:
  `pip install folium streamlit-foliumF`.)
- **🔎 Search** — full-text on title + description, with country and
  status filter chips.
- **⭐ Watchlist** — star countries / orgs / partnership types; generate
  a markdown digest of last 7 days of activity matching your stars.

Reads from whichever DB the rest of the CLI uses (`--db` flag,
`TURSO_DATABASE_URL` env, or local `./space_monitor.db`).

Set `UI_PASSWORD` in env to gate the UI behind a shared password. Magic
links (`?token=…` URLs) auto-consume on load — used by digest emails for
one-click approve/reject.

### Country briefing

```bash
space-monitor brief <country> [--since-days 90] [--force] [--out path.md]
```

Markdown briefing for a leader walking into a meeting in `<country>`.
Uses Claude Sonnet against everything we've tagged for that country in
the recency window. First call hits the model; subsequent calls in the
same ISO week return the cached body for free. `--force` bypasses the
cache.

### Watchlist + digest

```bash
space-monitor watch list                       [--user X]
space-monitor watch add <kind> <value>         [--user X]
space-monitor watch remove <id>
space-monitor watchdigest                      [--user X] [--days 7] [--post]
```

`<kind>` is one of `country`, `org`, `partnership_type`. The digest is
markdown summarizing the last 7 days of activity matching any star in
the user's watchlist. `--post` POSTs to `NOTIFY_WEBHOOK_URL`.

### Notifications

Set `NOTIFY_WEBHOOK_URL` (and optionally `NOTIFY_WEBHOOK_KIND` =
`slack`|`discord`) to enable webhook posts. Three CLIs use it:

```bash
space-monitor digest          [--post]    # last 24h pipeline summary
space-monitor cost-alarm      [--post] [--cap-usd N] [--hours N]
space-monitor source-health   [--post] [--threshold-days 14]
```

The latter two exit non-zero when their alert fires — useful in cron /
GH Actions. `digest` is the natural daily complement to the ingest cron.

### Magic-link review

```bash
space-monitor review mint <draft-id> {approve|reject} --user <name>
space-monitor review consume <token> [--reason "..."]
```

`mint` generates a one-shot token + a URL (when `REVIEW_LINK_BASE_URL`
env is set) or a CLI command (`space-monitor review consume <token>`).
Embed in digest emails for one-click action. Tokens are single-use.

### Org registry

```bash
space-monitor orgs seed                      # bundled canonical seed
space-monitor orgs backfill                  # absorb every observed org-name string
space-monitor orgs list-unknown [--limit N]  # most-frequent unrecognized orgs
```

Resolves "NASA" / "N.A.S.A." / "National Aeronautics and Space
Administration" to the canonical entry. Foundation for "all
partnerships involving Airbus" queries.

---

## Daily ingest

Two ways to run on a daily schedule.

### A. GitHub Actions + Turso (recommended — no laptop dependency)

The repo ships two workflows:

- `.github/workflows/bootstrap.yml` — manual one-time DB init from bundled seed data.
- `.github/workflows/daily-ingest.yml` — runs `space-monitor ingest --source all` daily at 13:00 UTC.

**Setup (one-time):**

1. Create a Turso DB:
   ```bash
   turso db create space-monitor
   turso db show space-monitor             # → libsql://...turso.io URL
   turso db tokens create space-monitor    # → auth token
   ```
2. Add three secrets in GitHub (*Settings → Secrets and variables → Actions*):
   - `ANTHROPIC_API_KEY`
   - `TURSO_DATABASE_URL` — the libsql:// URL from step 1
   - `TURSO_AUTH_TOKEN`  — the token from step 1
3. Trigger the **Bootstrap database** workflow once from the Actions tab.
4. The **Daily ingest** workflow then runs automatically every day at 13:00 UTC. Idempotent — RSS feeds only carry the last ~10–30 entries and `url_hash` dedup prevents re-processing — so missed days are harmless.

Inspect the DB anytime via the Turso shell:
```bash
turso db shell space-monitor
> SELECT COUNT(*) FROM partnership_draft WHERE draft_status='pending';
```

### B. Local cron + local SQLite file

For laptops that are reliably on at the cron time. Idempotent same as above.

```bash
crontab -e
# Add (substitute repo path and a year-bound `--since`):
0 13 * * * cd /home/ethanmurray/repos/space-monitor && /usr/bin/env -S bash -c 'source .env && export ANTHROPIC_API_KEY && /usr/bin/python3 -m space_monitor.cli ingest --source all --since 2026-01-01 --max-candidates 50 --max-extractions 50 --rate-limit-secs 1.5' >> /var/log/space-monitor-ingest.log 2>&1
```

The end-of-run summary table in the log lists per-source counts. To find runs that produced something worth reviewing:

```bash
grep 'positives=' /var/log/space-monitor-ingest.log | grep -v 'positives=0'
```

### Cost expectations

Daily run across all 25 working sources: **~$30-60/month** in Anthropic API costs (extraction + country-tagging + signal router + prefilter), plus **$0** infra (GH Actions free tier + Turso free tier comfortably accommodate this scale). Stays well under the user's $200/month cap.

The MTD spend metric on the dashboard reads from `extraction_usage` (every LLM call writes a row). `space-monitor cost-alarm --cap-usd 7 --hours 24 --post` is the daily cron complement that flags runaway spend before it adds up.

---

## Streamlit Cloud deploy

The repo has a `.streamlit/config.toml` shipped and a `secrets.toml.example`
template for the secrets the UI needs:

1. Push the repo to GitHub (already there).
2. At https://streamlit.io/cloud, click *New app* and point it at:
   - **Repository:** `ethanmurray/space-monitor`
   - **Branch:** `main`
   - **Main file:** `src/space_monitor/ui/app.py`
   - **Python version:** `3.11`
3. Click *Advanced settings → Secrets* and paste the contents of your
   `.streamlit/secrets.toml.example` (with real values filled in).
4. Click *Deploy*. First boot takes ~1-2 min while pip installs the
   deps from `pyproject.toml`.

The secrets you'll need to paste into the Streamlit UI:

```toml
ANTHROPIC_API_KEY  = "sk-ant-…"
TURSO_DATABASE_URL = "libsql://…"
TURSO_AUTH_TOKEN   = "eyJ…"
ANALYST_NAME       = "Ethan"
UI_PASSWORD        = "pick-one"      # optional, gates the app
```

---

## Layout

```
src/space_monitor/
  __init__.py
  cli.py                 # `space-monitor` entry point
  env.py                 # tiny .env loader (no python-dotenv dep)
  taxonomy.py            # typed access to data/taxonomy.json + extractor
  db.py                  # all SQLite DDL (workbook + pipeline tables)
  load.py                # per-sheet xlsx → SQLite loaders
  bootstrap.py           # seed-CSV initialization (no xlsx required)
  data/
    taxonomy.json        # extracted controlled vocabularies + scoring rubrics
    seed/
      partnership.csv    # 7,614-row partnership seed (bundled, gitignored xlsx-derived)
  pipeline/
    __init__.py
    cli.py               # `ingest` and `review` subcommands
    fetch.py             # http + trafilatura article body extraction
    extract.py           # Claude extraction with prompt caching + escalation
    prefilter.py         # LLM title classifier (yes/no/uncertain)
    drafts.py            # draft insert + promotion logic
    sources/
      base.py            # CandidateArticle + Source protocol
      _rss.py            # shared RSS adapter base class (httpx-fetched)
      # 18 RSS sources:
      nasa.py            spacenews.py        esa.py
      govuk.py           # prefilter_required=True
      spacewatch.py      # disabled=True (Cloudflare; awaiting Playwright)
      satnews.py         satellitetoday.py   mundogeo.py
      asianscientist.py  # prefilter_required=True
      asi.py             payloadspace.py     nasaspaceflight.py
      spacepolicyonline.py  philsa.py        csa.py
      breakingdefense.py defensenews.py      sansa.py     # all prefilter_required=True
      # 6 HTML scrapers:
      skao.py            eusst.py            disdg.py
      cnes.py            inpe.py             isa.py
      uae.py             # registered but JS-rendered; awaiting Playwright
      isro.py            # two-step: discover from homepage, parse date from each article
      # 1 search adapter:
      gnews.py           gnews_queries.py    # Google News RSS-search across ~30 queries
scripts/
  eval_prefilter.py      # eval fixture for the title classifier
  export_seed.py         # regenerate data/seed/*.csv from a loaded DB

.github/workflows/
  bootstrap.yml          # one-time DB init (manual trigger)
  daily-ingest.yml       # `space-monitor ingest --source all` daily at 13:00 UTC

Space_Dashboard_Hardcopy.xlsx     # source artifact (gitignored; not required for runtime)
Space_Dashboard_Summary.md        # per-sheet analysis + automation roadmap
BACKLOG.md                        # prioritized open items + DONE list
README.md                         # this file
.env.example                      # template; copy to .env (gitignored)
pyproject.toml
```

---

## Adding a new source

For an RSS feed:

```python
# src/space_monitor/pipeline/sources/myfeed.py
from ._rss import RSSSource

class MyFeedSource(RSSSource):
    name = "myfeed"
    domain = "myfeed.com"
    feed_url = "https://myfeed.com/rss"
    prefilter_required = False  # set True if mostly non-space
```

For an HTML scraper, pattern after `skao.py` / `eusst.py` / `disdg.py` —
implement `iter_candidates(limit)` yielding `CandidateArticle` objects.

Then register in `pipeline/sources/__init__.py`:

```python
from .myfeed import MyFeedSource
REGISTRY = { ..., "myfeed": MyFeedSource() }
```

That's it. `space-monitor ingest --source myfeed` works immediately;
`--source all` picks it up unless `disabled = True`.

---

## Automation roadmap progress

Six steps from `Space_Dashboard_Summary.md`:

1. ✅ **Schema as code** — `taxonomy.py` + `db.py` + `load.py`.
2. ✅ **Geocoding service** — `space_monitor/geocode.py` wraps the bundled
   `city` gazetteer with a `geocode(city, country) -> GeoHit` API
   (city+country exact → case-insensitive → city-only fallback).
3. ✅ **News pipeline** — sources → fetch → country-tag → extract → multi-
   signal router → drafts → review. Now produces `partnership`, `contract`,
   and `leadership_change` drafts.
4. ⬜ **External-database connectors** — first one (SIPRI or UCS satellites)
   in P1.
5. ⬜ **Scoring rubrics as functions** — P3.
6. ✅ **Web UI for the review queue** — Streamlit UI shipped (`space-monitor ui`).
