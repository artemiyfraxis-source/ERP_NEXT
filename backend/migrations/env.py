from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from app.modules.identity import infrastructure as _identity  # noqa: F401
from app.modules.procurement import infrastructure as _procurement  # noqa: F401
from app.modules.requests import infrastructure as _requests  # noqa: F401
from app.modules.receiving import infrastructure as _receiving  # noqa: F401
from app.modules.warehouse import infrastructure as _warehouse  # noqa: F401
from app.modules.fulfillment import infrastructure as _fulfillment  # noqa: F401
from app.modules.production import infrastructure as _production  # noqa: F401
from app.modules.production import downtime_infrastructure as _production_downtime  # noqa: F401
from app.modules.shipping import infrastructure as _shipping  # noqa: F401
from app.modules.references import infrastructure as _references  # noqa: F401
from app.modules.operations import infrastructure as _operations  # noqa: F401
from app.modules.techcards import infrastructure as _techcards  # noqa: F401
from app.modules.techcards import assembly_drafts_infrastructure as _assembly_drafts  # noqa: F401
from app.modules.techcards import generator_drafts_infrastructure as _generator_drafts  # noqa: F401
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
        # Revision ids here run past Alembic's default VARCHAR(32) (0013_production_materials_finished_goods):
        # SQLite ignores the length, PostgreSQL refuses the UPDATE. The table is created wide before Alembic
        # would create it narrow; an existing narrow one is widened.
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS alembic_version ("
                "version_num VARCHAR(128) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            )
            connection.exec_driver_sql("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)")
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        if connection.dialect.name == "postgresql":
            _defer_foreign_keys(connection)


# The Unit of Work adds related rows without ORM relationships, so SQLAlchemy does not order their INSERTs by
# foreign key (a work-session input can reach the database before its session). Tests run on SQLite, which does
# not enforce foreign keys at all, so this only shows on PostgreSQL. Checking every foreign key at COMMIT keeps
# the integrity guarantee and makes the order inside one transaction irrelevant. Re-applied after each upgrade,
# so a constraint added by a later migration is covered too.
_NON_DEFERRED_FOREIGN_KEYS = text("""
    SELECT format('ALTER TABLE %I.%I ALTER CONSTRAINT %I DEFERRABLE INITIALLY DEFERRED',
                  n.nspname, t.relname, c.conname)
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE c.contype = 'f' AND n.nspname = current_schema() AND NOT (c.condeferrable AND c.condeferred)
""")


def _defer_foreign_keys(connection) -> None:
    for statement in connection.execute(_NON_DEFERRED_FOREIGN_KEYS).scalars().all():
        connection.execute(text(statement))
    connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
