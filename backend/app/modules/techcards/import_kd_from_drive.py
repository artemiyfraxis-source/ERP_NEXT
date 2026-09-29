"""One-time import of historical КД from Google Drive into the ERP database (owner, 29.09.2026: off Drive).

    ERP_DATABASE_URL=... python -m app.modules.techcards.import_kd_from_drive            # dry-run: только список
    ERP_DATABASE_URL=... ERP_GOOGLE_SERVICE_ACCOUNT_JSON=<ключ erp-reader> \\
        python -m app.modules.techcards.import_kd_from_drive --apply

Rows of the КД register attached while КД lived on Drive (migration 0068) have a Drive file id and no bytes.
The dry-run lists them without touching Google. ``--apply`` downloads each file with the «erp-reader» service
account, scope ``drive.readonly`` (`GoogleDriveReader`: GET only — nothing is created, changed or trashed on
Drive), and stores the bytes into that row, one commit per file, so a rerun picks up only what is still missing.
The Drive link stays on the row as a read-only reference. Exit code: 0 — done; 1 — some files could not be read
(listed, the rest imported); 2 — ERP_DATABASE_URL or the Google key is missing or refused.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.modules.requests.google_drive import DriveDownload, DriveError

from .application import UnitOfWork


class DriveReader(Protocol):
    """What the import needs from Drive: `GoogleDriveReader.download`."""

    def download(self, file_id: str) -> DriveDownload: ...


@dataclass
class KdImportResult:
    found: list[str] = field(default_factory=list)
    imported: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    written: bool = False


def import_kd(
    uow_factory: Callable[[], UnitOfWork], reader: Callable[[], DriveReader] | None, *, apply: bool,
) -> KdImportResult:
    """Dry-run (``apply=False``) only lists the Drive-only rows; `reader` is not even built."""
    with uow_factory() as uow:
        pending = uow.kitsets.drive_only_documents()
    result = KdImportResult(found=[_label(document) for document in pending], written=apply)
    if not apply or not pending:
        return result
    drive = reader()
    for document in pending:
        try:
            file = drive.download(document.file_id)
        except DriveError as error:
            if error.kind == "config":
                raise
            result.failed.append(f"{_label(document)}: {error}")
            continue
        with uow_factory() as uow:
            stored = uow.kitsets.store_content(document.id, file.mime_type, file.content)
            uow.commit()
        if stored:
            result.imported.append(f"{_label(document)} ({len(file.content)} Б)")
    return result


def _label(document) -> str:
    return f"{document.project} {document.item_index} «{document.file_name}» [{document.file_id}]"


def format_report(result: KdImportResult) -> str:
    lines = [f"КД только на Диске: {len(result.found)}"]
    if not result.written:
        lines += [f"  {item}" for item in result.found]
        lines.append("Пробный прогон — в базу ничего не записано, Диск не читался. Запись: --apply.")
        return "\n".join(lines)
    lines.append(f"Перенесено в базу ERP: {len(result.imported)}")
    lines += [f"  {item}" for item in result.imported]
    if result.failed:
        lines.append(f"Не прочитано с Диска: {len(result.failed)}")
        lines += [f"  {item}" for item in result.failed]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Разовый перенос КД проектов с Google Диска в базу ERP (Диск только читается).")
    parser.add_argument("--apply", action="store_true", help="скачать файлы и записать в базу (без флага — только список)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    database_url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not database_url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from app.database import build_engine, build_techcards_uow_factory
    from app.modules.requests.google_drive import drive_reader_from_env

    engine = build_engine(database_url)
    try:
        result = import_kd(build_techcards_uow_factory(engine), drive_reader_from_env, apply=args.apply)
    except DriveError as error:
        print(f"Google Диск не читается: {error}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    print(format_report(result))
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
