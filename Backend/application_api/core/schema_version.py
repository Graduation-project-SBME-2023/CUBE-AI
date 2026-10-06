"""Whether the database schema matches the migrations this process was started with.

The dev overlay bind-mounts the API's source, so the running models can be newer
than the schema the database is on: a model edited without `alembic upgrade head`
would otherwise fail only when a request touches the new column. The readiness
probe compares the two and reports the gap instead.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


@lru_cache(maxsize=1)
def code_heads() -> frozenset[str]:
    """The head revision(s) of the migration files on disk, read once per process."""
    return frozenset(ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_heads())


def schema_drift(database_revisions: list[str] | set[str], heads: frozenset[str]) -> str | None:
    """A readable reason the database is not on `heads`, or None when it is."""
    current = frozenset(database_revisions)
    if current == heads:
        return None
    return (
        f"database is at {sorted(current) or 'no revision'} but the code expects "
        f"{sorted(heads)}; run `alembic -c application_api/alembic.ini upgrade head`"
    )
