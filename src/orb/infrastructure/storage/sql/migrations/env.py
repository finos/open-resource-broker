"""Alembic environment configuration for ORB SQL strategy.

Target metadata is imported from the ORM declarative Base so autogenerate
compares the live database against the authoritative ORM schema in
``orb.infrastructure.storage.sql.models``.

The database URL is resolved in this order:
1. ``ORB_SQL_URL`` environment variable (highest priority — useful in CI)
2. ``sqlalchemy.url`` in alembic.ini (the default)
3. Falls back to ``sqlite:///orb_data.db`` if neither is set.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Ensure the src/ layout is importable when alembic is invoked with this
# env.py path. env.py lives at src/orb/infrastructure/storage/sql/migrations/
# so the repo's src/ dir is five levels up from __file__.
# ---------------------------------------------------------------------------
_SRC_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, os.pardir, os.pardir, os.pardir)
)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from orb.infrastructure.storage.sql.models import Base

# Alembic Config object
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ORM metadata for autogenerate comparisons
target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------
_env_url = os.environ.get("ORB_SQL_URL")
if _env_url:
    config.set_main_option("sqlalchemy.url", _env_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live DB connection required)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (live DB connection)."""
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
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
