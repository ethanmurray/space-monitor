-- Canonical organization registry. Resolves the "NASA" / "N.A.S.A." /
-- "National Aeronautics and Space Administration" variant problem so that
-- joins on org name actually work. Populated by the seed loader and by the
-- approve-draft path (analyst can rename a draft's org through the UI and
-- we add the alias).

CREATE TABLE IF NOT EXISTS org (
    canonical_name  TEXT PRIMARY KEY,
    country         TEXT,
    org_kind        TEXT,           -- gov_agency | military | company | academic | ngo | multilateral
    aliases_json    TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE INDEX IF NOT EXISTS idx_org_country ON org(country);

CREATE TABLE IF NOT EXISTS org_alias (
    alias_lower     TEXT PRIMARY KEY,
    canonical_name  TEXT NOT NULL REFERENCES org(canonical_name)
);
