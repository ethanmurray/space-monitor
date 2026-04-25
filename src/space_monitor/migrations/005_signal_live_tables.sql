-- Approved-state target tables for the non-partnership signals.
-- Mirrors the relationship that partnership_draft -> partnership has.

CREATE TABLE IF NOT EXISTS contract (
    contract_id          TEXT PRIMARY KEY,
    description          TEXT,
    contract_year        INTEGER,
    value_musd           REAL,
    customer             TEXT,
    customer_country     TEXT,
    contractor           TEXT,
    contractor_country   TEXT,
    primary_mission      TEXT,
    mission_type         TEXT,
    source_url           TEXT,
    analyst              TEXT,
    approved_at          TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE INDEX IF NOT EXISTS idx_contract_customer_country ON contract(customer_country);
CREATE INDEX IF NOT EXISTS idx_contract_contractor_country ON contract(contractor_country);

CREATE TABLE IF NOT EXISTS leadership_change (
    leadership_id        TEXT PRIMARY KEY,
    description          TEXT,
    change_year          INTEGER,
    person_name          TEXT NOT NULL,
    organization         TEXT,
    country              TEXT,
    new_role             TEXT,
    prior_role           TEXT,
    change_kind          TEXT,
    source_url           TEXT,
    analyst              TEXT,
    approved_at          TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE INDEX IF NOT EXISTS idx_leadership_country ON leadership_change(country);
CREATE INDEX IF NOT EXISTS idx_leadership_person ON leadership_change(person_name);

-- Track the promoted-to-live id on the draft (mirror of partnership_draft.promoted_partnership_id).
ALTER TABLE contract_draft ADD COLUMN promoted_contract_id TEXT;
ALTER TABLE leadership_change_draft ADD COLUMN promoted_leadership_id TEXT;
