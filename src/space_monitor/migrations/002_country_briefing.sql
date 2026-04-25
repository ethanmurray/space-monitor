CREATE TABLE IF NOT EXISTS country_briefing (
    country         TEXT NOT NULL,
    iso_week        TEXT NOT NULL,
    body_markdown   TEXT NOT NULL,
    since_days      INTEGER NOT NULL,
    model           TEXT NOT NULL,
    articles        INTEGER NOT NULL,
    generated_at    TEXT NOT NULL,
    PRIMARY KEY (country, iso_week)
);
