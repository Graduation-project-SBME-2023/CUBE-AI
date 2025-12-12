from logging.config import fileConfig

import sys
import os

# Add 'backend/monailabel' to PYTHONPATH so 'core' can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'monailabel')))

from Backend.monailabel.core.database import Base
# from Backend.monailabel.models.model import Model
# from Backend.monailabel.models.volume import Volume
# from Backend.monailabel.models.segmentation import Segmentation
# from Backend.monailabel.models.study import Study
# from Backend.monailabel.models.patient import Patient
# from Backend.monailabel.models.radiomics import Radiomics
# from Backend.monailabel.models.chat_messages import ChatMessage
# from Backend.monailabel.models.chat_sessions import ChatSession
# from Backend.monailabel.models.workflow import Workflow
# from Backend.monailabel.models.workflow_details import WorkflowDetail
# from Backend.monailabel.models.hypothesis import Hypothesis
# from Backend.monailabel.models.plan import Plan
from Backend.monailabel.models.steps import PlanStep


from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

import os
from sqlalchemy import engine_from_config, pool
from alembic import context

config = context.config

#  Override Alembic URL from environment variable if present
database_url = os.getenv("DATABASE_URL")
sync_url = database_url.replace("asyncpg", "psycopg2") if database_url else None
if sync_url:
    config.set_main_option("sqlalchemy.url", sync_url)


# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata
# target_metadata=None  # Replace with actual MetaData object

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
