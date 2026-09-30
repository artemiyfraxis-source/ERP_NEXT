"""Import the bundled SUPPLYMAP operation-card library into a migrated ERP database.

    python -m app.modules.techcards.seed_library [--force]   # SUPPLYMAP snapshot + ERP templates
    python -m app.modules.techcards.seed_library --erp-only  # only the ERP templates (update-local, server update)

ERP templates (`backend/data/techcard-library-erp.json`) are cards SUPPLYMAP's БД.ТК does not have; they are kept as
ERP-saved sheets, so a later SUPPLYMAP snapshot never drops them. Every run is idempotent.
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
    OperationLibraryEditedError,
    ReplaceOperationLibraryCommand,
    SeedErpTemplatesCommand,
    replace_operation_library,
    seed_erp_templates,
)

DATA = Path(__file__).resolve().parents[3] / "data"
ACTOR = Actor(id=UUID("00000000-0000-0000-0000-000000000018"), name="ERP migration", department="System")


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
    print(seed_erp(factory, now))
    if "--erp-only" in sys.argv[1:]:
        return
    payload = json.loads((DATA / "techcard-library.json").read_text(encoding="utf-8"))
    with factory() as uow:
        metadata = uow.operation_cards.metadata()
    force = "--force" in sys.argv[1:]
    if not force and metadata and metadata["source_commit"] == payload["sourceCommit"] and metadata["template_count"] == len(payload["templates"]):
        print(f"Operation-card library already current: {metadata['template_count']} templates")
        return
    try:
        result = replace_operation_library(ReplaceOperationLibraryCommand(
            source_commit=payload["sourceCommit"],
            templates=tuple(payload["templates"]),
            actor=ACTOR,
            now=now,
            force=force,
        ), factory())
    except OperationLibraryEditedError as error:
        raise SystemExit(str(error)) from error
    print(f"Imported {result.templates} operation-card templates from {payload['sourceCommit'][:8]}"
          + (f"; overwrote ERP edits (--force): {'; '.join(result.overwritten_edited)}" if result.overwritten_edited else ""))


if __name__ == "__main__":
    main()
