"""
Aurora LTS — Alembic environment (P1-02)
=========================================
Hooks Alembic into our SQLAlchemy engine + Base.metadata.

The DATABASE_URL is read at runtime from the environment (NOT from
alembic.ini) so the same config works for SQLite dev and Postgres prod.

Autogenerate target: app.database.Base.metadata.
"""
from __future__ import annotations

import os
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── Make the `app` package importable when alembic is invoked from
# server_files/ (CLI) OR from main.py (in-process bootstrap).
_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVER_FILES = os.path.dirname(_HERE)
if _SERVER_FILES not in sys.path:
    sys.path.insert(0, _SERVER_FILES)

# Pull metadata from the same Base every model uses.
from aurora_shared.database.connection import Base  # noqa: E402
import aurora_shared.database.models  # noqa: F401,E402 — registers all model classes


config = context.config

# Inject DATABASE_URL programmatically (do NOT hardcode in alembic.ini).
_db_url = os.getenv("DATABASE_URL", "sqlite:///./asg_platform.db")
config.set_main_option("sqlalchemy.url", _db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection — emits SQL to stdout."""
    context.configure(
        url=_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
