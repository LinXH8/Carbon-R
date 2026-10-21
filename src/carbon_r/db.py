"""Postgres access.

Emission factors are fetched with exact SQL and never by similarity search, so this
module stays small on purpose: a connection helper, a liveness check, and the migration
runner every loader shares. See CLAUDE.md.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg

from carbon_r.config import PROJECT_ROOT, get_settings

MIGRATIONS_DIR = PROJECT_ROOT / "db" / "migrations"


class DatabaseNotConfiguredError(RuntimeError):
    """DATABASE_URL is absent or still holds the .env placeholder."""


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """Yield a connection, committing on clean exit and rolling back on error."""
    settings = get_settings()
    if not settings.database_configured:
        raise DatabaseNotConfiguredError(
            "DATABASE_URL is not set (or still contains <PASSWORD>). "
            "See .env.example for where to get it."
        )
    with psycopg.connect(settings.database_url) as conn:
        yield conn


def check_connection() -> bool:
    """True if the database answers. Never raises — /health reports, it does not fail."""
    try:
        with connect() as conn:
            conn.execute("select 1")
        return True
    except (DatabaseNotConfiguredError, psycopg.Error, OSError):
        return False


def apply_migrations(conn: psycopg.Connection) -> list[Path]:
    """Run every migration in filename order. Each is idempotent, so this is safe to repeat."""
    applied = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for path in applied:
        conn.execute(path.read_text())
    return applied
