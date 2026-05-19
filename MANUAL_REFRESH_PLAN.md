# Manual source refresh plan — 2026-05-19

Pickup note for the personal laptop. Work this top-to-bottom; commit per
source so progress is durable if Chrome MCP gets flaky.

## State of automated ingest (no action needed)

Daily GitHub Actions workflow is healthy:

- Last 7 scheduled runs (2026-05-09 → 2026-05-15): all green
- Most recent run (2026-05-15): 70 articles ingested, 0 failures, ≈$0.32 spend
- Cron: `0 13 * * *` UTC (`.github/workflows/daily-ingest.yml`)

The only red signal is the `source-health` check — it exits 1 when any
source has been silent >14 days. That's by design and is what this plan
addresses.

## Stale sources flagged 2026-05-15 (22 total)

From the workflow log of run 25925761180. Days-stale at time of check:

| Source | Days stale | Adapter type | Refresh path |
|--------|-----------:|--------------|--------------|
| csa | 25 | rss | Investigate — RSS adapter, shouldn't be stale |
| eusst | 25 | scraper | Investigate — HTML scraper, shouldn't be stale |
| skao | 25 | scraper | Investigate — HTML scraper, shouldn't be stale |
| isa | 18 | scraper | Investigate — HTML scraper, shouldn't be stale |
| aeb | 16 | manual | **Chrome MCP** |
| aprsaf | 16 | (not in yaml) | Skip — confirm if real source |
| casc | 16 | (not in yaml) | Skip — confirm if real source |
| cnsa | 16 | manual | **Chrome MCP** |
| conae | 16 | (not in yaml) | Skip — confirm if real source |
| csis_aerospace | 16 | (not in yaml) | Skip — confirm if real source |
| dlr | 16 | manual | **Chrome MCP** |
| eda | 16 | (not in yaml) | Skip — confirm if real source |
| euspa | 16 | (not in yaml) | Skip — confirm if real source |
| jaxa | 16 | manual | **Chrome MCP** (humans-in-space subdomain) |
| kasa | 16 | manual | **Chrome MCP** |
| lsa | 16 | (not in yaml) | Skip — confirm if real source |
| mbrsc | 16 | (not in yaml) | Skip — confirm if real source |
| pesco | 16 | manual | **Chrome MCP** |
| swf | 16 | (not in yaml) | Skip — confirm if real source |
| thespacereview | 16 | manual | **Chrome MCP** |
| uksa | 16 | (not in yaml) | Skip — confirm if real source |
| vnsc | 16 | (not in yaml) | Skip — confirm if real source |

## Action plan

### 1. Chrome MCP refresh — 8 manual sources

These are the ones with `status: manual` in `src/space_monitor/data/sources.yaml`
and a known Chrome MCP recipe in recent git history. Pull each, write a
JSON file, run `import_from_json.py`, commit.

Order (highest-yield first per recent commit history):

1. **dlr** — dlr.de news, React-rendered. ~25-30 new articles per cycle.
   Index: `https://www.dlr.de/en/latest/news/`
2. **kasa** — kasa.go.kr press release list at
   `/bbs/BBSMSTR_000000000041/list.do`. Articles open via JS form-POST
   (nttId param). Last commit batched 30 at a time.
3. **jaxa** — `humans-in-space.jaxa.jp/en/news/<year>/`. Last commit
   pulled 13 articles.
4. **cnsa** — `cnsa.gov.cn/english/n6465652/n6465653/index.html`. Last
   commit pulled 9.
5. **aeb** — `gov.br/aeb/pt-br/assuntos/noticias`. Last commit pulled 10.
6. **pesco** — pesco.europa.eu, low cadence (~4-5/quarter). Last commit pulled 4.
7. **thespacereview** — `thespacereview.com/article/N/1`. Weekly cadence,
   long-form. Last commit pulled 20.
8. **kari/casc** — KARI's English site is offline; KASA absorbed comms.
   Skip unless something has changed.

For each source:

```bash
# 1. Use Chrome MCP to navigate the index, take_snapshot, and harvest
#    article URLs + bodies. Write into:
#    chrome-imports/<source>_<YYYY-MM-DD>.json
#
# 2. Import:
python scripts/import_from_json.py chrome-imports/dlr_2026-05-19.json
#
# 3. Commit:
git add chrome-imports/dlr_2026-05-19.json
git commit -m "ingest: <N> <source> articles via Chrome MCP"
```

JSON shape (from `scripts/import_from_json.py` docstring):

```json
[
  {"source": "dlr",
   "url":    "https://www.dlr.de/en/latest/news/2026/...",
   "title":  "Article title",
   "published_at": "2026-04-27",
   "text":   "Full article body, plain text..."}
]
```

`import_from_json.py` is idempotent — already-ingested URLs are skipped
on `url_hash`. Re-running is safe.

### 2. Investigate the 4 RSS/scraper sources flagged stale

`csa`, `eusst`, `skao`, `isa` are RSS- or scraper-based and **should not
be stale**. Possible causes:

- Source itself stopped publishing (low-cadence sources legitimately go
  >14d — eusst is one of them per yaml comment "low post frequency")
- Adapter regression (parser broke after a site change)
- Network/DNS hiccup during recent runs

Quick check:

```bash
space-monitor ingest --source csa --since 2026-01-01 --max-candidates 50
space-monitor ingest --source eusst --since 2026-01-01 --max-candidates 50
space-monitor ingest --source skao --since 2026-01-01 --max-candidates 50
space-monitor ingest --source isa --since 2026-01-01 --max-candidates 50
```

If a source returns zero new candidates, look at its adapter and the
source URL in a browser to see whether posts are being made.

### 3. Sources flagged that aren't in `sources.yaml`

`aprsaf`, `casc`, `conae`, `csis_aerospace`, `eda`, `euspa`, `lsa`,
`mbrsc`, `swf`, `uksa`, `vnsc` — none of these have entries in
`src/space_monitor/data/sources.yaml`. They must have been added directly
via Chrome MCP imports without the yaml registry being updated, or
they're aliases.

Triage:

```bash
# Confirm what's actually in the DB
sqlite3 / turso shell:
SELECT source, COUNT(*), MAX(fetched_at)
FROM news_article
WHERE source IN ('aprsaf','casc','conae','csis_aerospace','eda','euspa',
                 'lsa','mbrsc','swf','uksa','vnsc')
GROUP BY source
ORDER BY MAX(fetched_at) DESC;
```

For each one that has real article rows: either add a yaml entry
documenting its status, or — if it's a low-value one-shot — leave it
and accept that source-health will keep flagging it.

## Notes for future

- Source-health threshold is 14 days. Several sources legitimately
  publish less often than that (eusst — "single page reaches back ~5
  years"). Worth considering per-source thresholds in
  `space-monitor source-health` rather than blanket 14d.
- The Playwright fetcher (BACKLOG P2) would unblock dlr/kasa/uae and
  retire most of this manual workflow.
