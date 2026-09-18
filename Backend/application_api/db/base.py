"""Declarative metadata shared by all Application API ORM models."""

from sqlalchemy import MetaData
from sqlalchemy.orm import declarative_base

# Explicit naming convention so Alembic autogenerate produces deterministic,
# predictable constraint/index names instead of relying on the database's
# defaults (which forced `op.create_index(None, ...)` calls elsewhere and made
# downgrade() bodies impossible to write for some revisions).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Base = declarative_base(metadata=MetaData(naming_convention=NAMING_CONVENTION))
