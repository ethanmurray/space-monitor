"""SQLite schema for the normalized space-monitor tables.

Two groups of tables:

* **Reference / taxonomy.** Loaded from ``taxonomy.json``. Provide the
  controlled vocabularies and the scoring rubrics. Other tables reference these
  by string PK rather than integer ID — small tables, readable joins.
* **Domain.** One per analytical sheet of the workbook (assets, partnerships,
  industry, etc.). Loaded from the xlsx by ``load.py``.

We deliberately keep partnerships denormalized to two parties (party 1 / party
2) because the source workbook does, and splitting it into a long
``partnership_party`` table would force unnecessary downstream changes for no
analytical gain at this stage.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# DDL kept as one string per table for readability and ordered insertion.
TABLES: list[tuple[str, str]] = [
    # --- Taxonomy / reference -------------------------------------------------
    (
        "cocom",
        """
        CREATE TABLE cocom (
            name TEXT PRIMARY KEY
        )
        """,
    ),
    (
        "country",
        """
        CREATE TABLE country (
            name     TEXT PRIMARY KEY,
            cocom    TEXT REFERENCES cocom(name),
            priority INTEGER NOT NULL DEFAULT 0
        )
        """,
    ),
    (
        "partnership_type",
        """
        CREATE TABLE partnership_type (
            name  TEXT PRIMARY KEY,
            score INTEGER NOT NULL
        )
        """,
    ),
    (
        "level_of_commitment",
        """
        CREATE TABLE level_of_commitment (
            name  TEXT PRIMARY KEY,
            score INTEGER NOT NULL
        )
        """,
    ),
    (
        "business_model",
        """
        CREATE TABLE business_model (
            name  TEXT PRIMARY KEY,
            score INTEGER NOT NULL
        )
        """,
    ),
    (
        "mission_type",
        """
        CREATE TABLE mission_type (
            name  TEXT PRIMARY KEY,
            score INTEGER NOT NULL
        )
        """,
    ),
    (
        "relationship_type",
        "CREATE TABLE relationship_type (name TEXT PRIMARY KEY)",
    ),
    (
        "operator_type",
        "CREATE TABLE operator_type (name TEXT PRIMARY KEY)",
    ),
    (
        "organization_type",
        "CREATE TABLE organization_type (name TEXT PRIMARY KEY)",
    ),
    (
        "sovereign_status",
        "CREATE TABLE sovereign_status (name TEXT PRIMARY KEY)",
    ),
    (
        "mass_class",
        """
        CREATE TABLE mass_class (
            name      TEXT PRIMARY KEY,
            lower_kg  REAL NOT NULL,
            upper_kg  REAL NOT NULL
        )
        """,
    ),
    (
        "orbit",
        "CREATE TABLE orbit (name TEXT PRIMARY KEY)",
    ),
    (
        "mission_area",
        "CREATE TABLE mission_area (name TEXT PRIMARY KEY)",
    ),
    (
        "value_chain_segment",
        "CREATE TABLE value_chain_segment (name TEXT PRIMARY KEY)",
    ),
    (
        "partnership_strength_lookup",
        """
        CREATE TABLE partnership_strength_lookup (
            partnership_type TEXT NOT NULL,
            business_model   TEXT NOT NULL,
            mission_type     TEXT NOT NULL,
            score            INTEGER NOT NULL,
            PRIMARY KEY (partnership_type, business_model, mission_type)
        )
        """,
    ),
    # --- Reference geography --------------------------------------------------
    (
        "city",
        """
        CREATE TABLE city (
            id            TEXT PRIMARY KEY,
            city          TEXT,
            city_ascii    TEXT,
            country       TEXT,
            iso2          TEXT,
            iso3          TEXT,
            admin_name    TEXT,
            capital       TEXT,
            population    INTEGER,
            lat           REAL,
            lng           REAL
        )
        """,
    ),
    (
        "launch_site",
        """
        CREATE TABLE launch_site (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            country         TEXT,
            type            TEXT,
            status          TEXT,
            full_name       TEXT,
            infrastructure  TEXT,
            name            TEXT,
            location        TEXT,
            latitude        REAL,
            longitude       REAL
        )
        """,
    ),
    # --- Universal Legend (country x mission/segment applicability) ----------
    (
        "country_capability",
        """
        CREATE TABLE country_capability (
            country     TEXT NOT NULL,
            segment     TEXT NOT NULL,
            applicable  INTEGER NOT NULL,
            PRIMARY KEY (country, segment)
        )
        """,
    ),
    # --- Capacity -------------------------------------------------------------
    (
        "budget_topline",
        """
        CREATE TABLE budget_topline (
            country            TEXT NOT NULL,
            civil_or_defense   TEXT NOT NULL,
            estimate_or_exact  TEXT,
            method             TEXT,
            topline_spend_usd  REAL,
            cagr_range         TEXT,
            cagr               REAL,
            PRIMARY KEY (country, civil_or_defense)
        )
        """,
    ),
    (
        "defense_spending",
        """
        CREATE TABLE defense_spending (
            region                  TEXT,
            country                 TEXT NOT NULL,
            line_type               TEXT,
            funding_source          TEXT,
            account_type            TEXT,
            year                    INTEGER NOT NULL,
            amount_usd_thousands    REAL,
            PRIMARY KEY (country, year)
        )
        """,
    ),
    (
        "space_program_investment",
        """
        CREATE TABLE space_program_investment (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            country               TEXT,
            organization          TEXT,
            partnership_id        TEXT,
            program               TEXT,
            technologies          TEXT,
            primary_mission       TEXT,
            secondary_mission     TEXT,
            overview              TEXT,
            link_1                TEXT,
            link_2                TEXT,
            link_3                TEXT,
            total_funding_local_m REAL,
            local_currency        TEXT,
            total_funding_usd_m   REAL,
            start_year            INTEGER,
            end_year              INTEGER,
            total_years           INTEGER
        )
        """,
    ),
    (
        "space_program_investment_year",
        """
        CREATE TABLE space_program_investment_year (
            program_id  INTEGER NOT NULL REFERENCES space_program_investment(id),
            year        INTEGER NOT NULL,
            amount      REAL,
            PRIMARY KEY (program_id, year)
        )
        """,
    ),
    (
        "industrial_base_score",
        """
        CREATE TABLE industrial_base_score (
            country                          TEXT NOT NULL,
            value_chain_segment              TEXT NOT NULL,
            mission_agnostic_launch_only     INTEGER,
            comms_data_transport             INTEGER,
            isr_remote_sensing               INTEGER,
            missile_warning_tracking         INTEGER,
            space_domain_awareness           INTEGER,
            science_exploration              INTEGER,
            pnt                              INTEGER,
            notes                            TEXT,
            PRIMARY KEY (country, value_chain_segment)
        )
        """,
    ),
    # --- Activity -------------------------------------------------------------
    (
        "space_asset",
        """
        CREATE TABLE space_asset (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_country             TEXT,
            asset_name                  TEXT,
            spacecraft_name             TEXT,
            owner                       TEXT,
            operator                    TEXT,
            operator_type               TEXT,
            mission_type                TEXT,
            sovereign_status            TEXT,
            prime_contractor_country    TEXT,
            prime_contractor            TEXT,
            subcontractor_1             TEXT,
            subcontractor_2             TEXT,
            launch_provider             TEXT,
            launch_vehicle_class        TEXT,
            launch_vehicle              TEXT,
            num_current_assets          INTEGER,
            num_planned_assets          INTEGER,
            num_total_assets            INTEGER,
            order_year                  INTEGER,
            ioc                         INTEGER,
            foc                         INTEGER,
            launch_year                 INTEGER,
            actual_eol                  INTEGER,
            assumed_eol                 INTEGER,
            eol_year                    INTEGER,
            constellation_launch_year   INTEGER,
            constellation_assets        INTEGER,
            coverage_score              REAL,
            mass_score                  REAL,
            launch_year_score           REAL,
            capability_score            REAL,
            total_score                 REAL,
            final_score                 REAL,
            mission_satcom              INTEGER,
            mission_pnt                 INTEGER,
            mission_isr                 INTEGER,
            mission_environmental       INTEGER,
            mission_missile_warning     INTEGER,
            mission_sda                 INTEGER,
            mission_combat_power        INTEGER,
            mission_orbital             INTEGER,
            mission_lunar               INTEGER,
            mission_interplanetary      INTEGER,
            mission_bmc3                INTEGER,
            mission_launch              INTEGER,
            mission_oos                 INTEGER,
            mission_other               INTEGER,
            mission_not_specified       INTEGER,
            orbit                       TEXT,
            mass_kg                     REAL,
            mass_class                  TEXT,
            description                 TEXT,
            link_1                      TEXT,
            link_2                      TEXT,
            link_3                      TEXT,
            link_4                      TEXT
        )
        """,
    ),
    (
        "partnership",
        """
        CREATE TABLE partnership (
            partnership_id        TEXT PRIMARY KEY,
            parent_id             TEXT,
            analyst               TEXT,
            new_or_legacy         TEXT,
            description           TEXT,
            primary_mission       TEXT,
            sub_mission           TEXT,
            partnership_year      INTEGER,
            partnership_type      TEXT,
            level_of_commitment   TEXT,
            relationship_type     TEXT,
            business_model        TEXT,
            mission_type          TEXT,
            partnership_strength  REAL,
            asset_name            TEXT,
            cocom_1               TEXT,
            country_1             TEXT,
            org_type_1            TEXT,
            organization_1        TEXT,
            company_1             TEXT,
            cocom_2               TEXT,
            country_2             TEXT,
            org_type_2            TEXT,
            organization_2        TEXT,
            company_2             TEXT,
            link_1                TEXT,
            link_2                TEXT,
            link_3                TEXT
        )
        """,
    ),
    (
        "industry_company",
        """
        CREATE TABLE industry_company (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            priority_country            TEXT,
            profile_country             TEXT,
            business_unit               TEXT,
            bu_hq_city                  TEXT,
            bu_hq_country               TEXT,
            founding_year               INTEGER,
            parent_company              TEXT,
            parent_hq_city              TEXT,
            parent_hq_country           TEXT,
            primary_value_chain         TEXT,
            cap_satellite_integration   TEXT,
            cap_spacecraft_components   TEXT,
            cap_payload_sensors         TEXT,
            cap_digital_services        TEXT,
            cap_launch                  TEXT,
            cap_ground                  TEXT,
            cap_other                   TEXT,
            primary_mission             TEXT,
            sub_mission                 TEXT,
            mission_satcom_pnt          TEXT,
            mission_space_sensing       TEXT,
            mission_sda_combat_power    TEXT,
            mission_science_exploration TEXT,
            mission_bmc3                TEXT,
            mission_assured_access      TEXT,
            mission_other               TEXT,
            mission_not_specified       TEXT,
            justification               TEXT,
            link_1                      TEXT,
            link_2                      TEXT,
            link_3                      TEXT,
            calculated_lat              REAL,
            calculated_lng              REAL,
            clean_lat                   REAL,
            clean_lng                   REAL
        )
        """,
    ),
    # --- Analyst output -------------------------------------------------------
    (
        "investment_outlook",
        """
        CREATE TABLE investment_outlook (
            country                 TEXT NOT NULL,
            mission                 TEXT NOT NULL,
            relevant_organizations  TEXT,
            near_term_score         REAL,
            near_term_priorities    TEXT,
            long_term_score         REAL,
            long_term_priorities    TEXT,
            source_1                TEXT,
            source_2                TEXT,
            source_3                TEXT,
            ready_for_review        TEXT,
            comments                TEXT,
            PRIMARY KEY (country, mission)
        )
        """,
    ),
    # --- Commercial signal ----------------------------------------------------
    (
        "vc_deal",
        """
        CREATE TABLE vc_deal (
            deal_id                   TEXT PRIMARY KEY,
            company                   TEXT,
            company_id                TEXT,
            description               TEXT,
            primary_industry_sector   TEXT,
            primary_industry_group    TEXT,
            primary_industry_code     TEXT,
            verticals                 TEXT,
            current_financing_status  TEXT,
            current_business_status   TEXT,
            announced_date            TEXT,
            deal_date                 TEXT,
            deal_size_musd            REAL,
            pre_money_valuation_musd  REAL,
            post_valuation_musd       REAL,
            series                    TEXT,
            deal_type                 TEXT,
            deal_status               TEXT,
            num_investors             INTEGER
        )
        """,
    ),
    (
        "partnership_source_tally",
        """
        CREATE TABLE partnership_source_tally (
            source  TEXT PRIMARY KEY,
            count   INTEGER NOT NULL
        )
        """,
    ),
]


# Pipeline tables accumulate state across runs (drafts, fetched articles), so
# they're created with IF NOT EXISTS and never dropped by `init_schema`.
PIPELINE_TABLES: list[tuple[str, str]] = [
    (
        "news_article",
        """
        CREATE TABLE news_article (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash        TEXT NOT NULL UNIQUE,
            url             TEXT NOT NULL,
            source          TEXT NOT NULL,
            source_domain   TEXT NOT NULL,
            title           TEXT,
            published_at    TEXT,
            fetched_at      TEXT NOT NULL,
            cleaned_text    TEXT,
            cleaned_text_en TEXT,                  -- on-demand English translation; NULL until requested
            status          TEXT NOT NULL,        -- discovered | fetched | extracted | failed | skipped_prefilter
            failure_reason  TEXT
        )
        """,
    ),
    (
        "partnership_draft",
        """
        CREATE TABLE partnership_draft (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            source_article_id        INTEGER NOT NULL REFERENCES news_article(id),
            draft_status             TEXT NOT NULL,    -- pending | approved | rejected | superseded
            extracted_at             TEXT NOT NULL,
            extractor_model          TEXT NOT NULL,
            confidence               TEXT,             -- high | medium | low
            reviewer                 TEXT,
            review_notes             TEXT,
            promoted_partnership_id  TEXT,             -- set when draft_status='approved'
            possible_duplicate_of    TEXT,             -- existing partnership_id flagged at promotion
            -- mirror of `partnership` schema (analyst-meaningful fields):
            partnership_id           TEXT,
            description              TEXT,
            primary_mission          TEXT,
            sub_mission              TEXT,
            partnership_year         INTEGER,
            partnership_type         TEXT,
            level_of_commitment      TEXT,
            relationship_type        TEXT,
            business_model           TEXT,
            mission_type             TEXT,
            partnership_strength     REAL,
            asset_name               TEXT,
            country_1                TEXT,
            org_type_1               TEXT,
            organization_1           TEXT,
            company_1                TEXT,
            country_2                TEXT,
            org_type_2               TEXT,
            organization_2           TEXT,
            company_2                TEXT
        )
        """,
    ),
]


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def _is_remote(arg: str) -> bool:
    return arg.startswith(("libsql://", "https://", "http://", "wss://", "ws://"))


def resolve_db(arg: str | Path | None = None) -> str:
    """Pick a DB destination from CLI arg / env / default.

    Precedence: explicit ``arg`` > ``TURSO_DATABASE_URL`` env > default
    ``./space_monitor.db``. Returns either a libsql/https URL string or a
    local file path string.
    """
    if arg is not None:
        return str(arg)
    env_url = os.environ.get("TURSO_DATABASE_URL")
    if env_url:
        return env_url
    return "space_monitor.db"


@contextmanager
def connect(arg: str | Path) -> Any:
    """Open a connection to either a local SQLite file or a remote libsql /
    Turso database. The CLI normalises arg via :func:`resolve_db`.

    Both backends expose the same DB-API 2.0 surface we use across the code
    base: ``execute``, ``executemany``, ``commit``, ``cursor``,
    ``lastrowid``, ``fetchone``, ``fetchall``. Foreign-key enforcement is
    only turned on for SQLite — Turso doesn't honour the PRAGMA today.
    """
    arg_str = str(arg)
    if _is_remote(arg_str):
        # Remote (Turso / libsql). Auth token via env or already in URL.
        import libsql_experimental as libsql  # lazy import; not needed for SQLite

        token = os.environ.get("TURSO_AUTH_TOKEN")
        raw = libsql.connect(database=arg_str, auth_token=token)
        conn = _LibsqlAdapter(raw)
        try:
            yield conn
        finally:
            # libsql_experimental's Connection has no explicit close() at the
            # time of writing — the underlying client cleans up on GC.
            try:
                raw.close()
            except AttributeError:
                pass
    else:
        conn = sqlite3.connect(arg_str)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()


class _LibsqlAdapter:
    """Thin wrapper that makes a libsql_experimental connection accept the
    list-of-params shape the rest of the codebase uses. The underlying
    client is strict about tuples; sqlite3 accepts either. We convert at
    the boundary so the call sites can stay sqlite3-flavoured.
    """

    def __init__(self, raw: Any):
        self._raw = raw

    @staticmethod
    def _tup(params: Any) -> Any:
        if isinstance(params, list):
            return tuple(params)
        return params

    def execute(self, sql: str, params: Any = ()) -> Any:
        return self._raw.execute(sql, self._tup(params))

    def executemany(self, sql: str, seq_of_params: Any) -> Any:
        coerced = [self._tup(p) for p in seq_of_params]
        return self._raw.executemany(sql, coerced)

    def commit(self) -> None:
        self._raw.commit()

    def cursor(self) -> Any:
        return self._raw.cursor()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


def init_schema(conn: sqlite3.Connection) -> None:
    """Drop and recreate workbook-derived tables. Pipeline tables (drafts,
    news_article) are preserved — they accumulate state across runs."""
    cur = conn.cursor()
    for name, _ in reversed(TABLES):
        cur.execute(f"DROP TABLE IF EXISTS {name}")
    for _, ddl in TABLES:
        cur.execute(ddl)
    ensure_pipeline_schema(conn)
    conn.commit()


def ensure_pipeline_schema(conn: sqlite3.Connection) -> None:
    """Create pipeline tables if they don't already exist + apply any
    additive migrations. Safe to call repeatedly."""
    cur = conn.cursor()
    for _, ddl in PIPELINE_TABLES:
        cur.execute(ddl.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
    _apply_migrations(conn)
    conn.commit()


# Additive column migrations. Each entry: (table, column, ddl_fragment).
# ALTER TABLE ... ADD COLUMN is supported by both sqlite3 and Turso/libsql.
# Idempotent — we check pragma_table_info first.
_MIGRATIONS: list[tuple[str, str, str]] = [
    # Added when on-demand translation in the UI started persisting back.
    ("news_article", "cleaned_text_en", "TEXT"),
]


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column, type_ddl in _MIGRATIONS:
        existing = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_ddl}")


def table_names() -> list[str]:
    return [t for t, _ in TABLES] + [t for t, _ in PIPELINE_TABLES]
