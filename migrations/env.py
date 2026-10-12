"""Alembic environment.

The URL comes from `Settings.database_url` unless overridden with `-x dburl=...`,
which is how the test harness and the eval runner point migrations at their own
databases without touching `.env`.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.core.db import normalize_db_url
from app.models import Base

config = context.config

if config.config_file_name is not None:
    # The default disables every logger created before the migration ran, which silenced
    # the application's own loggers in any process that migrates after importing the app.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

x_args = context.get_x_argument(as_dictionary=True)
db_url = x_args.get("dburl") or get_settings().database_url
config.set_main_option("sqlalchemy.url", normalize_db_url(db_url).replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
