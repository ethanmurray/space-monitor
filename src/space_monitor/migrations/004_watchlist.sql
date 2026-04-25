-- Watchlist + magic-link tokens for the alert/digest path.

CREATE TABLE IF NOT EXISTS watchlist (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_name    TEXT NOT NULL,                -- analyst/user identifier (matches ANALYST_NAME)
    kind         TEXT NOT NULL,                -- 'country' | 'org' | 'partnership_type'
    value        TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    UNIQUE (user_name, kind, value)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_name);

CREATE TABLE IF NOT EXISTS review_token (
    token        TEXT PRIMARY KEY,             -- random URL-safe string
    draft_id     INTEGER NOT NULL,
    action       TEXT NOT NULL,                -- 'approve' | 'reject'
    issued_to    TEXT NOT NULL,                -- the user the link was minted for
    issued_at    TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    consumed_at  TEXT
);
