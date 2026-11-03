"""CI contract: reversible seeded head, or exact loss-prevention refusal for 0169."""

import os

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, create_engine, select, text
from sqlalchemy.schema import CreateIndex, CreateTable


GUARDED_REVISION = "0169_shipping_pack_permissions"
REFUSAL = "Cannot erase packing permissions; restore a prior copy instead."


def evidence(engine):
    """Compare every table definition and row, including the Alembic revision."""
    metadata = MetaData()
    with engine.connect() as connection:
        metadata.reflect(bind=connection)
        return {
            table.name: (
                str(CreateTable(table).compile(dialect=engine.dialect)),
                sorted(str(CreateIndex(index).compile(dialect=engine.dialect)) for index in table.indexes),
                sorted(repr(tuple(row)) for row in connection.execute(select(table))),
            )
            for table in metadata.tables.values()
        }


def check(config, engine):
    head = ScriptDirectory.from_config(config).get_current_head()
    with engine.connect() as connection:
        revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        if revisions != [head]:
            raise AssertionError(f"Seeded database must be at head, got {revisions}")
        if head == GUARDED_REVISION:
            count = connection.execute(text("SELECT count(*) FROM shipping_pack_permissions")).scalar_one()
            if not count:
                raise AssertionError("Seeded 0169 must contain permissions to exercise its loss-prevention guard")
    if head != GUARDED_REVISION:
        command.downgrade(config, "-1")
        command.upgrade(config, "head")
        return "seeded roundtrip passed"

    before = evidence(engine)
    try:
        command.downgrade(config, "-1")
    except RuntimeError as error:
        if str(error) != REFUSAL:
            raise
    else:
        raise AssertionError("0169 unexpectedly allowed loss of nonempty packing permissions")
    if evidence(engine) != before:
        raise AssertionError("Refused downgrade changed schema, rows or revision")
    return "0169 refused lossy downgrade; all schema, rows and revision unchanged"


if __name__ == "__main__":
    engine = create_engine(os.environ["ERP_DATABASE_URL"])
    try:
        print(check(Config("alembic.ini"), engine))
    finally:
        engine.dispose()
