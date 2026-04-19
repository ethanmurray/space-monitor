"""Evaluate the LLM title-relevance classifier.

YES set: 50 partnership_id strings randomly sampled from the workbook's
partnership table — these are by construction real space partnerships, so the
classifier should NOT mark them 'no'.

NO set: hand-curated obviously-non-space titles (UK politics, sports, bio,
finance). Classifier should NOT mark them 'yes'.

AMBIGUOUS set: generic diplomatic / agreement titles where the body might be
about space but the title alone doesn't say. Classifier should mark these
'uncertain' (or 'yes' if it's clearly leaning, but never 'no').

Pass criteria:
- 0 YES titles classified as 'no'
- 0 NO titles classified as 'yes'
- AMBIGUOUS titles all return 'uncertain' or 'yes' (never 'no')

Usage::

    python scripts/eval_prefilter.py
"""

from __future__ import annotations

import random
import sqlite3
import sys
from pathlib import Path

# Make the package importable when running from the repo root without install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from space_monitor.env import load_dotenv
from space_monitor.pipeline import prefilter


YES_SAMPLE_SIZE = 50
RNG_SEED = 42


def _sample_yes(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    random.seed(RNG_SEED)
    rows = conn.execute(
        "SELECT partnership_id FROM partnership "
        "WHERE LENGTH(partnership_id) BETWEEN 15 AND 120"
    ).fetchall()
    sample = random.sample([r[0] for r in rows], min(YES_SAMPLE_SIZE, len(rows)))
    conn.close()
    return sample


NO_TITLES = [
    "PM announces tax cuts in spring statement",
    "Bank of England raises interest rates by 25 basis points",
    "Manchester United wins FA Cup final 2-1",
    "BBC announces drama lineup for autumn schedule",
    "New COVID variant detected in Western Europe",
    "Wheat farmers seek subsidies after poor harvest",
    "Scientists discover new species of deep-sea fish",
    "King's Speech sets out government legislative agenda",
    "London marathon raises record £60 million for charity",
    "Department of Health publishes obesity strategy",
    "Manchester City secures sponsorship deal with airline",
    "New crime bill receives royal assent",
    "Energy regulator caps household bills",
    "Census results show population growth in Wales",
    "Bird flu outbreak forces poultry indoor housing rules",
    "School curriculum review proposes maths reforms",
    "Chancellor unveils budget for fiscal year 2027",
    "Royal Mint releases commemorative King Charles coin",
    "Plant-derived chemical may help fight crop disease",
    "Universities raise tuition fees following inflation",
    "Football association investigates referee bribery claims",
    "DEFRA publishes farming subsidy review",
    "New London skyscraper wins planning permission",
    "Police seize £40 million in cocaine bust",
    "Mortgage approvals fall to lowest level since 2013",
    "Foreign Office issues travel advisory for Lebanon",
    "Rugby World Cup pool stages conclude in Sydney",
    "Cabinet reshuffle announced ahead of party conference",
    "Charity Commission investigates fundraising practices",
    "Trade deficit widens on weaker exports",
]


AMBIGUOUS_TITLES = [
    "Joint statement by President Macron and Prime Minister Starmer",
    "UK and Australia sign defense cooperation agreement",
    "G20 leaders issue communique after Rio summit",
    "Bilateral meeting concludes with new framework agreement",
    "Two countries sign memorandum of understanding on technology",
    "Foreign minister visits Tokyo for bilateral talks",
    "Leaders agree on joint research initiative",
    "Strategic partnership announced between two governments",
]


def main() -> int:
    load_dotenv()
    repo_root = Path(__file__).resolve().parents[1]
    db_path = repo_root / "space_monitor.db"
    if not db_path.exists():
        print(f"Need {db_path} (run `space-monitor load Space_Dashboard_Hardcopy.xlsx` first)")
        return 1

    yes_titles = _sample_yes(db_path)
    print(f"YES sample (n={len(yes_titles)}): real partnership names from workbook")
    print(f"NO sample (n={len(NO_TITLES)}): hand-curated non-space titles")
    print(f"AMBIGUOUS sample (n={len(AMBIGUOUS_TITLES)}): generic diplomatic titles")
    print()

    yes_decisions = prefilter.classify_titles(yes_titles)
    no_decisions = prefilter.classify_titles(NO_TITLES)
    amb_decisions = prefilter.classify_titles(AMBIGUOUS_TITLES)

    yes_failures = [
        (t, d) for t, d in zip(yes_titles, yes_decisions) if d.verdict == "no"
    ]
    no_failures = [
        (t, d) for t, d in zip(NO_TITLES, no_decisions) if d.verdict == "yes"
    ]
    amb_failures = [
        (t, d) for t, d in zip(AMBIGUOUS_TITLES, amb_decisions) if d.verdict == "no"
    ]

    print("=== YES set (real space partnerships — must not be classified 'no') ===")
    print(_breakdown(yes_decisions))
    if yes_failures:
        print("FALSE NEGATIVES (real partnerships classified 'no'):")
        for t, d in yes_failures:
            print(f"  - {t!r}: {d.reason}")

    print("\n=== NO set (obvious non-space — must not be classified 'yes') ===")
    print(_breakdown(no_decisions))
    if no_failures:
        print("FALSE POSITIVES (non-space classified 'yes'):")
        for t, d in no_failures:
            print(f"  - {t!r}: {d.reason}")

    print("\n=== AMBIGUOUS set (generic titles — should pass through as 'uncertain' or 'yes') ===")
    print(_breakdown(amb_decisions))
    if amb_failures:
        print("MISSED AMBIGUITIES (classified 'no' instead of 'uncertain'):")
        for t, d in amb_failures:
            print(f"  - {t!r}: {d.reason}")

    print()
    pass_ = (not yes_failures) and (not no_failures) and (not amb_failures)
    print(f"OVERALL: {'PASS' if pass_ else 'FAIL'}")
    print(
        f"  yes-set false negatives:    {len(yes_failures)}/{len(yes_titles)}\n"
        f"  no-set  false positives:    {len(no_failures)}/{len(NO_TITLES)}\n"
        f"  amb-set premature 'no':     {len(amb_failures)}/{len(AMBIGUOUS_TITLES)}"
    )
    return 0 if pass_ else 1


def _breakdown(decisions: list[prefilter.Decision]) -> str:
    n_yes = sum(1 for d in decisions if d.verdict == "yes")
    n_no = sum(1 for d in decisions if d.verdict == "no")
    n_unc = sum(1 for d in decisions if d.verdict == "uncertain")
    return f"  yes={n_yes}  uncertain={n_unc}  no={n_no}"


if __name__ == "__main__":
    sys.exit(main())
