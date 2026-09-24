from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.modules.identity import infrastructure as _identity  # noqa: F401
from app.modules.procurement import infrastructure as _procurement  # noqa: F401
from app.modules.requests import infrastructure as _requests  # noqa: F401
from app.modules.receiving import infrastructure as _receiving  # noqa: F401
from app.modules.warehouse import infrastructure as _warehouse  # noqa: F401
from app.modules.fulfillment import infrastructure as _fulfillment  # noqa: F401
from app.modules.production import infrastructure as _production  # noqa: F401
from app.modules.shipping import infrastructure as _shipping  # noqa: F401
from app.modules.references import infrastructure as _references  # noqa: F401
from app.modules.operations import infrastructure as _operations  # noqa: F401
from app.modules.techcards import infrastructure as _techcards  # noqa: F401
from app.shared.persistence import Base


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    url = os.getenv("ERP_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url or url == "driver://not-configured":
        raise RuntimeError("Set ERP_DATABASE_URL before running migrations")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(
        section,
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
