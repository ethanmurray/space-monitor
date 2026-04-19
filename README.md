# space-monitor

Automation foundation for the Space Dashboard — a hand-curated intelligence
binder (`Space_Dashboard_Hardcopy.xlsx`) used by analysts who track
international space partnerships and cooperation.

This codebase replaces the manual parts of that workflow with:

1. A typed **taxonomy** + **SQLite loader** that turns the workbook into a
   normalized, queryable database.
2. A **news-monitoring pipeline** that ingests space-domain news from RSS
   feeds and HTML scrapers, extracts structured `partnership_draft` rows via
   Claude, and queues them for analyst approval.
3. A **CLI review loop** that promotes approved drafts into the live
   `partnership` table.

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

# 3. Discover + extract partnership drafts from one source
space-monitor ingest --source spacenews --max-candidates 5

# 4. Review what was found
space-monitor review list
space-monitor review show <draft-id>
space-monitor review approve <draft-id> --reviewer your-name
space-monitor review reject  <draft-id> --reviewer your-name --reason "…"
```

The pipeline runs end-to-end with just steps 1–4. `data/taxonomy.json` ships
with the package; the live news sources + Claude provide everything else.

## Optional: load the source workbook

`Space_Dashboard_Hardcopy.xlsx` is **not** committed to this repo (it's
analyst-curated proprietary data). Place your copy at the repo root, then:

```bash
# Regenerate data/taxonomy.json from the workbook's Data Validation Lists sheet.
# Re-run when the workbook's controlled vocabularies change.
space-monitor extract-taxonomy Space_Dashboard_Hardcopy.xlsx

# Load all 17 sheets into ./space_monitor.db (also gitignored).
space-monitor load Space_Dashboard_Hardcopy.xlsx
```

What this unlocks:

- **Duplicate detection at promotion** — `space-monitor review approve`
  flags drafts whose parties + year overlap with an existing curated
  partnership.
- **`scripts/eval_prefilter.py`** — uses 50 real partnership names from the
  workbook as the YES set for the title classifier eval.
- **Direct queries** against the loaded historical data (assets,
  partnerships, industry, defense spending, etc.) for analyst use.

The pipeline itself does not need the workbook to run.

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
                                              extract.extract_with_escalation()
                                                  - claude-haiku-4-5 first
                                                  - sonnet-4-6 if confidence='low'
                                                                        │
                                                                        ▼
                                                       drafts.insert_draft()
                                                                        │
                                                                        ▼
                                                           partnership_draft
                                                                (status='pending')
```

---

## Sources

12 adapters registered, 11 working. One disabled awaiting the Playwright
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
```

`approve` inserts the draft into the live `partnership` table with a
generated `partnership_id`, then sets the draft's `draft_status='approved'`
and links it via `promoted_partnership_id`. `reject` is non-destructive —
the draft stays in the DB with status='rejected' for audit.

---

## Daily ingest (cron)

The pipeline is idempotent (re-running on the same RSS window re-extracts
nothing), so missed days are harmless.

```bash
crontab -e
# Add (substitute repo path and a year-bound `--since`):
0 13 * * * cd /home/ethanmurray/repos/space-monitor && /usr/bin/env -S bash -c 'source .env && export ANTHROPIC_API_KEY && /usr/bin/python3 -m space_monitor.cli ingest --source all --since 2026-01-01 --max-candidates 50 --max-extractions 50 --rate-limit-secs 1.5' >> /var/log/space-monitor-ingest.log 2>&1
```

The end-of-run summary table in the log lists per-source counts.

To check whether a given run produced anything worth reviewing:

```bash
grep 'positives=' /var/log/space-monitor-ingest.log | grep -v 'positives=0'
```

Cost ceiling at typical feed cadence: **~$5–10/month** across all sources.

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
  data/
    taxonomy.json        # extracted controlled vocabularies + scoring rubrics
  pipeline/
    __init__.py
    cli.py               # `ingest` and `review` subcommands
    fetch.py             # http + trafilatura article body extraction
    extract.py           # Claude extraction with prompt caching + escalation
    prefilter.py         # LLM title classifier (yes/no/uncertain)
    drafts.py            # draft insert + promotion logic
    sources/
      base.py            # CandidateArticle + Source protocol
      _rss.py            # shared RSS adapter base class
      nasa.py
      spacenews.py
      esa.py
      govuk.py           # prefilter_required=True
      spacewatch.py      # disabled=True
      satnews.py
      satellitetoday.py
      mundogeo.py
      asianscientist.py  # prefilter_required=True
      skao.py            # HTML scraper
      eusst.py           # HTML scraper
      disdg.py           # HTML scraper
scripts/
  eval_prefilter.py      # eval fixture for the title classifier

Space_Dashboard_Hardcopy.xlsx     # source artifact (input only)
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
2. ⬜ **Geocoding service** — replace LatLong sheet (P0 in BACKLOG).
3. ✅ **News pipeline** — sources → fetch → extract → drafts → review.
4. ⬜ **External-database connectors** — first one (SIPRI or UCS satellites)
   in P1.
5. ⬜ **Scoring rubrics as functions** — P3.
6. ⬜ **Web UI for the review queue** — P2 (CLI works for now).
