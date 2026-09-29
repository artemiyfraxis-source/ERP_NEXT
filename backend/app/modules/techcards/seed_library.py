"""Import the bundled SUPPLYMAP operation-card library into a migrated ERP database."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import UUID

from app.database import build_engine, build_techcards_uow_factory
from app.shared.identity import Actor

from .application import ReplaceOperationLibraryCommand, replace_operation_library


def main() -> None:
    database_url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Set ERP_DATABASE_URL before importing the operation-card library")
    bundle_path = Path(__file__).resolve().parents[3] / "data" / "techcard-library.json"
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    factory = build_techcards_uow_factory(build_engine(database_url))
    with factory() as uow:
        metadata = uow.operation_cards.metadata()
    if metadata and metadata["source_commit"] == payload["sourceCommit"] and metadata["template_count"] == len(payload["templates"]):
        print(f"Operation-card library already current: {metadata['template_count']} templates")
        return
    result = replace_operation_library(ReplaceOperationLibraryCommand(
        source_commit=payload["sourceCommit"],
        templates=tuple(payload["templates"]),
        actor=Actor(
            id=UUID("00000000-0000-0000-0000-000000000018"),
            name="ERP migration",
            department="System",
        ),
        now=datetime.now(timezone.utc),
    ), factory())
    print(f"Imported {result.templates} operation-card templates from {payload['sourceCommit'][:8]}"
          + (f"; kept ERP edits: {', '.join(result.kept_edited)}" if result.kept_edited else ""))


if __name__ == "__main__":
    main()
