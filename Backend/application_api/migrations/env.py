from logging.config import fileConfig

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

backend_root = Path(__file__).resolve().parents[2]
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

from application_api.db.base import Base
from application_api.db.models.agent_runs import AgentRun
from application_api.db.models.artifacts import Artifact
from application_api.db.models.Wslog import WSLog
from application_api.db.models.chat_messages import ChatMessage
from application_api.db.models.sessions import Session
from application_api.db.models.chats import Chat
from application_api.db.models.cohorts import Cohort, CohortMember
from application_api.db.models.feature_sets import FeatureSet
from application_api.db.models.model import Model
from application_api.db.models.model_results import ModelResult
from application_api.db.models.patient import Patient
from application_api.db.models.plan import Plan
from application_api.db.models.radiomics import Radiomics
from application_api.db.models.segmentation import Segmentation
from application_api.db.models.segmentation_runs import (
    SegmentationPair,
    SegmentationRun,
    SegmentationTarget,
)
from application_api.db.models.steps import PlanStep
from application_api.db.models.study import Study
from application_api.db.models.volume import Volume
from application_api.db.models.workflow import Workflow
from application_api.db.models.workflow_details import WorkflowDetail

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

#  DATABASE_URL is required: never fall back to the placeholder DSN baked
#  into alembic.ini, which would silently target whatever is on that host
#  and port using a committed credential.
try:
    database_url = os.environ["DATABASE_URL"]
except KeyError as exc:
    raise RuntimeError(
        "DATABASE_URL must be set before running Alembic; refusing to fall "
        "back to the placeholder DSN in alembic.ini."
    ) from exc

# Rewrite only the URL scheme (async driver -> sync driver) instead of a
# blind substring replace across the whole DSN, which could corrupt a
# password that happens to contain "asyncpg".
_parsed = urlsplit(database_url)
sync_url = urlunsplit(_parsed._replace(scheme=_parsed.scheme.replace("+asyncpg", "+psycopg2")))
config.set_main_option("sqlalchemy.url", sync_url)


# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
