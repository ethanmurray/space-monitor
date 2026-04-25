"""Tiny migration framework.

Numbered SQL files in this package directory are applied in order. Each
file's name is recorded in ``schema_migration`` so it never runs twice.

Naming: ``NNN_short_description.sql`` (zero-padded so ordering matches
lexicographic sort). One DDL statement per file is fine, multiple
statements separated by ``;`` also fine — the runner splits on ``;``
and skips empty fragments. Use ``CREATE TABLE IF NOT EXISTS`` and
``ALTER TABLE ... ADD COLUMN`` so partial-apply states are safe.

Why not Alembic: Alembic is great for ORM-heavy projects with branching
schema lineages. We have neither. A plain numbered-file directory + a
recording table is enough complexity to grow with us.
"""

from __future__ import annotations

import sqlite3
from importlib import resources


_BOOTSTRAP_DDL = """
CREATE TABLE IF NOT EXISTS schema_migration (
    name        TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
)
"""


def apply_pending(conn: sqlite3.Connection) -> list[str]:
    """Apply every migration whose filename isn't yet in schema_migration.

    Returns the list of migration filenames applied (empty if all current).
    Idempotent — safe to call on every CLI invocation.
    """
    conn.execute(_BOOTSTRAP_DDL)
    applied = {
        row[0] for row in conn.execute("SELECT name FROM schema_migration").fetchall()
    }
    pending = sorted(p for p in _files() if p not in applied)
    just_applied: list[str] = []
    for name in pending:
        sql = resources.files(__name__).joinpath(name).read_text()
        # libsql_experimental's executescript is finicky; split + execute
        # one statement at a time.
        for stmt in _split_statements(sql):
            try:
                conn.execute(stmt)
            except Exception as e:
                # Tolerate the "structure already exists" cases that can
                # happen when a migration's effect was previously applied
                # by the hand-rolled _MIGRATIONS list, OR when the column
                # already lives in the CREATE TABLE in db.py and we're on
                # a fresh DB. Re-raise anything else.
                msg = str(e).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    continue
                raise
        conn.execute(
            "INSERT INTO schema_migration (name) VALUES (?)", (name,)
        )
        just_applied.append(name)
    if just_applied:
        conn.commit()
    return just_applied


def _files() -> list[str]:
    """Return sorted list of *.sql files in this package."""
    out = []
    for path in resources.files(__name__).iterdir():
        n = path.name
        if n.endswith(".sql"):
            out.append(n)
    return sorted(out)


def _split_statements(sql: str) -> list[str]:
    """Strip ``--`` line comments, then split on ``;``. Fine for our DDL
    (no embedded semicolons inside string literals)."""
    no_comments = "\n".join(
        line.split("--", 1)[0]
        for line in sql.splitlines()
    )
    parts = []
    for chunk in no_comments.split(";"):
        s = chunk.strip()
        if s:
            parts.append(s)
    return parts
