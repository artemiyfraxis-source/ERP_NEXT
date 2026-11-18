"""Administrator CLI for «Станки»: release the bridge binding of an ОБ (its PC was replaced or its token reissued).

    deploy/erp.sh cli python -m app.workflows.machines_admin release ОБ.019

The next report of a `machine:report` token binds that token and its PC again (production/machines.py)."""

from __future__ import annotations

import argparse
import os

from sqlalchemy.orm import Session, sessionmaker

from app.database import build_engine
from app.modules.production.machines import release


def main() -> None:
    parser = argparse.ArgumentParser(description="ERP «Станки»: bridge bindings")
    commands = parser.add_subparsers(dest="command", required=True)
    drop = commands.add_parser("release", help="drop the binding of an ОБ and its latest snapshot")
    drop.add_argument("inventory", help="ОБ inventory number, e.g. ОБ.019")
    args = parser.parse_args()
    database_url = os.getenv("ERP_DATABASE_URL")
    if not database_url:
        raise SystemExit("Set ERP_DATABASE_URL first")
    engine = build_engine(database_url)
    try:
        with sessionmaker(bind=engine, class_=Session).begin() as session:
            done = release(session, "".join(args.inventory.split()).upper())
        print("released" if done else "no binding")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
