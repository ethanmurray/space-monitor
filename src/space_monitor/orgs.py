"""Canonical organization registry + alias resolution.

The extraction layer surfaces orgs as free-text strings ("NASA",
"N.A.S.A.", "National Aeronautics and Space Administration"). Without
canonical names, joins like "all partnerships involving Airbus" silently
miss rows. This module provides:

* :func:`resolve` — given a free-text name, return the canonical name + a
  confidence flag. Lookup is case-insensitive on a separate ``org_alias``
  table for O(1) hits.
* :func:`register` — add a new org with a canonical name and zero or more
  aliases. Idempotent — re-registering a known canonical merges aliases.
* :func:`backfill_from_drafts` — scan partnership_draft + partnership for
  org-name strings we've never seen before, seed them as canonical orgs
  with the observed string as both canonical_name and alias. Lets an
  analyst then rename / merge later from the UI.

Bootstrap data: a small seed list of the largest space-domain orgs with
their well-known aliases. Adequate for the common cases (NASA, ESA,
JAXA, ISRO, SpaceX, Airbus, …); the rest get collected by the backfill.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Iterable

from . import db


# Hand-curated seed. Each entry: (canonical_name, country, kind, aliases).
# Kept short — the backfill catches the long tail. country is None for
# multilateral / supranational entities.
_SEED: list[tuple[str, str | None, str, list[str]]] = [
    ("NASA", "United States", "gov_agency", [
        "National Aeronautics and Space Administration", "N.A.S.A.",
    ]),
    ("US Space Force", "United States", "military", [
        "USSF", "United States Space Force", "Space Force",
    ]),
    ("Space Systems Command", "United States", "military", ["SSC", "USSF SSC"]),
    ("AFRL", "United States", "military", ["Air Force Research Laboratory"]),
    ("ESA", None, "multilateral", [
        "European Space Agency", "European Space Agency (ESA)",
    ]),
    ("EUSPA", None, "multilateral", [
        "European Union Agency for the Space Programme",
    ]),
    ("JAXA", "Japan", "gov_agency", [
        "Japan Aerospace Exploration Agency",
        "Japan Aerospace Exploration Agency (JAXA)",
    ]),
    ("ISRO", "India", "gov_agency", [
        "Indian Space Research Organisation",
        "Indian Space Research Organisation (ISRO)",
    ]),
    ("CNSA", "China", "gov_agency", [
        "China National Space Administration",
        "China National Space Administration (CNSA)",
    ]),
    ("ROSCOSMOS", "Russia", "gov_agency", ["Roscosmos State Corporation"]),
    ("KARI", "South Korea", "gov_agency", [
        "Korea Aerospace Research Institute",
        "Korea Aerospace Research Institute (KARI)",
    ]),
    ("UK Space Agency", "United Kingdom", "gov_agency", ["UKSA"]),
    ("CSA", "Canada", "gov_agency", ["Canadian Space Agency"]),
    ("DLR", "Germany", "gov_agency", ["German Aerospace Center"]),
    ("CNES", "France", "gov_agency", [
        "Centre National d'Études Spatiales", "French Space Agency",
    ]),
    ("ASI", "Italy", "gov_agency", ["Italian Space Agency", "Agenzia Spaziale Italiana"]),
    ("UAESA", "United Arab Emirates", "gov_agency", ["UAE Space Agency"]),
    ("SANSA", "South Africa", "gov_agency", ["South African National Space Agency"]),
    ("PhilSA", "Philippines", "gov_agency", ["Philippine Space Agency"]),
    ("ISA", "Israel", "gov_agency", ["Israel Space Agency"]),
    ("INPE", "Brazil", "gov_agency", [
        "Instituto Nacional de Pesquisas Espaciais",
        "Brazilian National Institute for Space Research",
    ]),
    ("AEB", "Brazil", "gov_agency", ["Agência Espacial Brasileira"]),
    ("VNSC", "Vietnam", "gov_agency", ["Vietnam National Space Center"]),
    # Companies
    ("SpaceX", "United States", "company", []),
    ("Blue Origin", "United States", "company", []),
    ("Lockheed Martin", "United States", "company", ["Lockheed"]),
    ("Northrop Grumman", "United States", "company", ["Northrop"]),
    ("Boeing", "United States", "company", []),
    ("Maxar", "United States", "company", ["Maxar Technologies"]),
    ("Rocket Lab", "United States", "company", []),
    ("Airbus", "France", "company", [
        "Airbus Defence and Space", "Airbus Defense and Space",
    ]),
    ("Thales Alenia Space", "France", "company", ["Thales Alenia"]),
    ("OHB", "Germany", "company", ["OHB SE", "OHB System"]),
    ("Mitsubishi Electric", "Japan", "company", ["MELCO"]),
    ("Mitsubishi Heavy Industries", "Japan", "company", ["MHI"]),
    ("KARI", "South Korea", "gov_agency", []),
    # Multilateral missions / consortia
    ("SKAO", None, "multilateral", [
        "SKA Observatory", "Square Kilometre Array Observatory",
    ]),
]


@dataclass
class ResolveResult:
    canonical_name: str | None
    confidence: str  # 'exact' | 'alias' | 'unknown'

    @property
    def is_known(self) -> bool:
        return self.canonical_name is not None


# ---------------------------------------------------------------------------
# Lookup + registration
# ---------------------------------------------------------------------------


def resolve(
    name: str | None,
    *,
    conn: sqlite3.Connection | None = None,
    db_arg: str | None = None,
) -> ResolveResult:
    """Return the canonical name for a free-text org string. Caller can pass
    an open connection (efficient for batch resolution) or omit it."""
    if not name or not name.strip():
        return ResolveResult(None, "unknown")
    s = name.strip()
    s_lower = s.lower()
    if conn is not None:
        return _resolve(conn, s, s_lower)
    with db.connect(db.resolve_db(db_arg)) as new_conn:
        db.ensure_pipeline_schema(new_conn)
        return _resolve(new_conn, s, s_lower)


def _resolve(conn: sqlite3.Connection, raw: str, lower: str) -> ResolveResult:
    # Exact canonical match wins.
    row = conn.execute(
        "SELECT canonical_name FROM org WHERE canonical_name = ?", (raw,)
    ).fetchone()
    if row:
        return ResolveResult(row[0], "exact")
    # Then alias table (lowercased).
    row = conn.execute(
        "SELECT canonical_name FROM org_alias WHERE alias_lower = ?", (lower,)
    ).fetchone()
    if row:
        return ResolveResult(row[0], "alias")
    return ResolveResult(None, "unknown")


def register(
    canonical_name: str,
    *,
    country: str | None = None,
    org_kind: str = "company",
    aliases: Iterable[str] = (),
    conn: sqlite3.Connection | None = None,
) -> None:
    """Add or extend an org. Re-registering with the same canonical_name
    merges aliases (idempotent). Aliases are stored case-insensitively in
    ``org_alias``; canonical names ARE kept in their original casing."""
    if conn is not None:
        _register(conn, canonical_name, country, org_kind, list(aliases))
        return
    with db.connect(db.resolve_db()) as new_conn:
        db.ensure_pipeline_schema(new_conn)
        _register(new_conn, canonical_name, country, org_kind, list(aliases))


def _register(
    conn: sqlite3.Connection,
    canonical_name: str,
    country: str | None,
    org_kind: str,
    aliases: list[str],
) -> None:
    existing = conn.execute(
        "SELECT aliases_json FROM org WHERE canonical_name = ?",
        (canonical_name,),
    ).fetchone()
    if existing:
        prior = set(json.loads(existing[0] or "[]"))
        prior.update(aliases)
        conn.execute(
            "UPDATE org SET aliases_json = ? WHERE canonical_name = ?",
            (json.dumps(sorted(prior)), canonical_name),
        )
    else:
        conn.execute(
            "INSERT INTO org (canonical_name, country, org_kind, aliases_json) "
            "VALUES (?, ?, ?, ?)",
            (canonical_name, country, org_kind, json.dumps(sorted(set(aliases)))),
        )
    # Always have canonical itself reachable via the alias index too.
    all_aliases = {canonical_name, *aliases}
    rows = [(a.lower(), canonical_name) for a in all_aliases]
    conn.executemany(
        "INSERT OR IGNORE INTO org_alias (alias_lower, canonical_name) VALUES (?, ?)",
        rows,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Bootstrap / backfill
# ---------------------------------------------------------------------------


def seed_canonical(db_arg: str | None = None) -> int:
    """Apply the bundled seed list. Idempotent. Returns # new canonicals."""
    target = db.resolve_db(db_arg)
    n_new = 0
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        for canonical, country, kind, aliases in _SEED:
            existed = conn.execute(
                "SELECT 1 FROM org WHERE canonical_name = ?", (canonical,)
            ).fetchone()
            _register(conn, canonical, country, kind, aliases)
            if not existed:
                n_new += 1
    return n_new


def backfill_from_drafts(db_arg: str | None = None) -> int:
    """Scan org-name strings on partnership rows + drafts; register every
    unseen value as a new canonical org so future joins resolve. Returns
    the number of net-new canonicals created."""
    target = db.resolve_db(db_arg)
    n_new = 0
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        seen = set()
        for tbl, cols in (
            ("partnership", ("organization_1", "organization_2", "company_1", "company_2")),
            ("partnership_draft", ("organization_1", "organization_2", "company_1", "company_2")),
        ):
            try:
                for col in cols:
                    rows = conn.execute(
                        f"SELECT DISTINCT {col} FROM {tbl} WHERE {col} IS NOT NULL"
                    ).fetchall()
                    for (name,) in rows:
                        if not name or not name.strip():
                            continue
                        seen.add(name.strip())
            except Exception:
                # Table may not exist on a stripped DB.
                continue

        for raw in sorted(seen):
            r = _resolve(conn, raw, raw.lower())
            if r.is_known:
                continue
            _register(conn, raw, country=None, org_kind="company", aliases=[])
            n_new += 1
    return n_new


def list_unknown_top(limit: int = 50, db_arg: str | None = None) -> list[tuple[str, int]]:
    """For the UI: which org-name strings appear most often without a
    canonical entry? Sorted by count desc."""
    target = db.resolve_db(db_arg)
    counts: dict[str, int] = {}
    with db.connect(target) as conn:
        db.ensure_pipeline_schema(conn)
        for tbl, cols in (
            ("partnership_draft", ("organization_1", "organization_2", "company_1", "company_2")),
        ):
            for col in cols:
                try:
                    rows = conn.execute(
                        f"SELECT {col}, COUNT(*) FROM {tbl} "
                        f"WHERE {col} IS NOT NULL GROUP BY {col}"
                    ).fetchall()
                except Exception:
                    continue
                for name, n in rows:
                    if not name:
                        continue
                    counts[name.strip()] = counts.get(name.strip(), 0) + n
        # Drop any that already resolve.
        unknown = []
        for name, n in counts.items():
            r = _resolve(conn, name, name.lower())
            if not r.is_known:
                unknown.append((name, n))
    unknown.sort(key=lambda x: x[1], reverse=True)
    return unknown[:limit]
