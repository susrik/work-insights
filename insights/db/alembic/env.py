"""Alembic environment: the DB URL comes from the insights pydantic config.

Alembic itself is configured in ``[tool.alembic]`` in ``pyproject.toml``;
there is deliberately no ``alembic.ini``.
"""

from __future__ import annotations

import logging

from alembic import context
from sqlalchemy import engine_from_config, pool

from insights.config import load_config
from insights.db.models import Base
from insights.db.session import ensure_sqlite_dir

logging.basicConfig(level=logging.INFO, format="%(levelname)-5.5s [%(name)s] %(message)s")
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

alembic_config = context.config
_db_url = load_config().database.url
ensure_sqlite_dir(_db_url)
alembic_config.set_main_option("sqlalchemy.url", _db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=alembic_config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
