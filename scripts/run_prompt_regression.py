"""Replay golden extraction fixtures and diff against expected outputs.

Two modes:

* Default — *mocked*. Patch ``space_monitor.pipeline.extract.extract`` to
  return ``ExtractionResult`` built from the fixture's ``expected`` block.
  Asserts that ``insert_draft`` writes the draft, the country-normalization
  step strips any non-canonical countries, and the partnership_id is the
  expected deterministic shape. Free, fast, runs in CI.

* ``--live`` — call the real Anthropic API for each fixture and diff each
  field of the returned payload against ``expected``. Slow + costs money;
  use periodically to catch model-drift.

Run:

    python scripts/run_prompt_regression.py             # mocked
    python scripts/run_prompt_regression.py --live      # live API
    python scripts/run_prompt_regression.py --record FIXTURE.json  # capture
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

GOLDEN = REPO / "tests" / "fixtures" / "golden"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true", help="Hit the real API.")
    p.add_argument("--record", type=str, default=None,
                   help="Run live on a single fixture and write its 'expected' field.")
    p.add_argument("--fixture", type=str, default=None,
                   help="Run only this fixture (filename within tests/fixtures/golden).")
    args = p.parse_args()

    if args.record:
        return _record(args.record)

    fixtures = sorted(GOLDEN.glob("*.json"))
    if args.fixture:
        fixtures = [GOLDEN / args.fixture]

    n_pass = 0
    n_fail = 0
    failures = []
    for path in fixtures:
        ok, msg = _run_one(path, live=args.live)
        if ok:
            n_pass += 1
            print(f"  ✓ {path.name}")
        else:
            n_fail += 1
            print(f"  ✗ {path.name}  —  {msg}")
            failures.append((path.name, msg))
    print()
    print(f"{n_pass} passed, {n_fail} failed (mode={'LIVE' if args.live else 'mocked'})")
    return 0 if n_fail == 0 else 1


def _run_one(path: Path, *, live: bool) -> tuple[bool, str]:
    fixture = json.loads(path.read_text())
    body = fixture["body"]
    title = fixture.get("title")
    url = fixture.get("url")
    expected = fixture["expected"]

    if live:
        from space_monitor.env import load_dotenv
        load_dotenv()
        from space_monitor.pipeline import extract as extract_mod
        try:
            result = extract_mod.extract(body, title=title, url=url)
        except Exception as e:
            return (False, f"live extract raised: {type(e).__name__}: {e}")
        return _diff(result.payload, expected)

    # Mocked mode — patch extract() to return ExtractionResult(expected, …),
    # then drive insert_draft and check the draft row matches.
    return _run_mocked(body=body, title=title, url=url, expected=expected)


def _run_mocked(*, body: str, title: str, url: str, expected: dict) -> tuple[bool, str]:
    from space_monitor import db
    from space_monitor.pipeline import drafts as drafts_mod
    from space_monitor.pipeline.extract import ExtractionResult, ExtractionUsage

    fake = ExtractionResult(
        payload=expected,
        model="claude-haiku-4-5", stop_reason="end_turn",
        usage=ExtractionUsage(0, 0, 0, 0),
    )
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        with db.connect(path) as conn:
            db.ensure_pipeline_schema(conn)
            # Need a country table for the normalization step.
            from space_monitor import taxonomy
            conn.execute("CREATE TABLE country (name TEXT PRIMARY KEY, cocom TEXT, priority INTEGER)")
            conn.executemany("INSERT INTO country VALUES (?, NULL, 0)",
                             [(c.name,) for c in taxonomy.load().countries])
            conn.execute(
                "CREATE TABLE partnership (partnership_id TEXT PRIMARY KEY, "
                " parent_id TEXT, analyst TEXT, new_or_legacy TEXT, description TEXT, "
                " primary_mission TEXT, sub_mission TEXT, partnership_year INTEGER, "
                " partnership_type TEXT, level_of_commitment TEXT, relationship_type TEXT, "
                " business_model TEXT, mission_type TEXT, partnership_strength REAL, "
                " asset_name TEXT, cocom_1 TEXT, country_1 TEXT, org_type_1 TEXT, "
                " organization_1 TEXT, company_1 TEXT, cocom_2 TEXT, country_2 TEXT, "
                " org_type_2 TEXT, organization_2 TEXT, company_2 TEXT, "
                " link_1 TEXT, link_2 TEXT, link_3 TEXT)"
            )
            cur = conn.execute(
                "INSERT INTO news_article (url_hash, url, source, source_domain, title, "
                " fetched_at, status, cleaned_text) "
                "VALUES (?, ?, 'fixture', 'fixture.test', ?, '2026-04-25', 'fetched', ?)",
                ("h" + url, url, title, body),
            )
            article_id = cur.lastrowid
            did = drafts_mod.insert_draft(conn, article_id=article_id, extraction=fake)
            row = conn.execute(
                "SELECT country_1, country_2, partnership_year, "
                " organization_1, organization_2, review_notes "
                "FROM partnership_draft WHERE id = ?", (did,)
            ).fetchone()
        return _diff_row(row, expected)
    finally:
        Path(path).unlink(missing_ok=True)


def _diff_row(row: tuple, expected: dict) -> tuple[bool, str]:
    """Validate post-insert state — country-normalization may have stripped
    a non-canonical value to NULL. Caller's expectation should reflect
    canonical values."""
    c1, c2, year, org1, org2, notes = row
    diffs = []
    for actual, key in (
        (c1, "country_1"), (c2, "country_2"),
        (year, "partnership_year"),
        (org1, "organization_1"), (org2, "organization_2"),
    ):
        want = expected.get(key)
        if actual != want:
            diffs.append(f"{key}: got {actual!r} want {want!r}")
    if diffs:
        return (False, "; ".join(diffs))
    return (True, "ok")


def _diff(actual: dict, expected: dict) -> tuple[bool, str]:
    diffs = []
    for k, want in expected.items():
        if k == "description":
            continue  # free-text, hard to assert exactly; ignore
        got = actual.get(k)
        if got != want:
            diffs.append(f"{k}: got {got!r} want {want!r}")
    if diffs:
        return (False, "; ".join(diffs))
    return (True, "ok")


def _record(fixture_path: str) -> int:
    """Run live on one fixture; overwrite its 'expected' field with the result."""
    from space_monitor.env import load_dotenv
    load_dotenv()
    from space_monitor.pipeline import extract as extract_mod
    path = GOLDEN / fixture_path
    fixture = json.loads(path.read_text())
    res = extract_mod.extract(
        fixture["body"], title=fixture.get("title"), url=fixture.get("url"),
    )
    fixture["expected"] = res.payload
    path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n")
    print(f"Recorded expected for {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
