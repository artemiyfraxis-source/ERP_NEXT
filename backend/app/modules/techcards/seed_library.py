"""Load the bundled operation-card library into a migrated ERP database.

    python -m app.modules.techcards.seed_library             # base templates (fresh database) + ERP templates
    python -m app.modules.techcards.seed_library --erp-only  # only the ERP templates (update-local, server update)

The base templates (`backend/data/techcard-library.json`) go only into an empty library: after that the library lives
in ERP and is edited there. ERP templates (`backend/data/techcard-library-erp.json`) are kept as ERP-saved sheets.
Every run is idempotent.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from app.database import build_engine, build_techcards_uow_factory
from app.shared.identity import Actor

from .application import (
    LoadOperationLibraryCommand,
    SeedErpTemplatesCommand,
    load_operation_library,
    seed_erp_templates,
)

DATA = Path(__file__).resolve().parents[3] / "data"
ACTOR = Actor(id=UUID("00000000-0000-0000-0000-000000000018"), name="ERP migration", department="System")


def seed_base(factory, now: datetime) -> str:
    payload = json.loads((DATA / "techcard-library.json").read_text(encoding="utf-8"))
    loaded = load_operation_library(LoadOperationLibraryCommand(
        templates=tuple(payload["templates"]), actor=ACTOR, now=now,
    ), factory())
    return f"Библиотека операций: загружено шаблонов {loaded}" if loaded else "Библиотека операций уже загружена"


def seed_erp(factory, now: datetime) -> str:
    payload = json.loads((DATA / "techcard-library-erp.json").read_text(encoding="utf-8"))
    result = seed_erp_templates(SeedErpTemplatesCommand(
        templates=tuple(payload["templates"]), actor=ACTOR, now=now,
    ), factory())
    parts = [f"{label}: {'; '.join(result[key])}" for key, label in (("created", "добавлены"), ("updated", "обновлены"))
             if result[key]]
    return "Шаблоны ERP: " + (", ".join(parts) if parts else f"без изменений ({len(result['kept'])})")


def main() -> None:
    database_url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Set ERP_DATABASE_URL before importing the operation-card library")
    factory = build_techcards_uow_factory(build_engine(database_url))
    now = datetime.now(timezone.utc)
    if "--erp-only" not in sys.argv[1:]:
        print(seed_base(factory, now))
    print(seed_erp(factory, now))


if __name__ == "__main__":
    main()
