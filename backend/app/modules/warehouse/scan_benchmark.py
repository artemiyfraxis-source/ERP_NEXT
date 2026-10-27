"""Re-read the last warehouse УПД scans with today's reader and compare with what was stored (27.10.2026: the check
of the ERP's one document reader on real scans before relying on it). Read-only: the stored readings stay as they are.

    ERP_DATABASE_URL=… python -m app.workflows.upd_pdf_parse --benchmark-scans 20 [--budget 1800]

Per file (newest first; a merged «УПД.pdf» of sheets is one multi-page file) — the header fields that differ (seller,
ИНН, УПД № / date, invoice № / date from «Основание», ПРД № / date), the line count, and per line (in order) the
article, name, quantity, unit and packs that differ; then a summary of how many files / lines differ in each field.
Each file is read exactly as «Отсканировать УПД» reads it (`scan_reading.read_scan`: DOCUMENT_BUDGET seconds, a page
the model did not read — tesseract); a model that does not answer is printed (its errors from the transport log)
and the run goes on. Every reading is a paid model call: `--budget` stops starting new files after that many seconds."""

from __future__ import annotations

import logging
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TextIO
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.file_types import sniffed_type

from .infrastructure import WarehouseReceiptFileRow
from .scan_reading import ScanReading, read_scan, reading_from_json
from .upd_lines import UpdLine

HEADER = {
    "supplier": "продавец", "inn": "ИНН", "upd_number": "УПД №", "upd_date": "УПД дата",
    "invoice_number": "счёт №", "invoice_date": "счёт дата", "prd_number": "ПРД №", "prd_date": "ПРД дата",
}
LINE_FIELDS = {"article": "артикул", "name": "наименование", "quantity": "кол-во", "unit": "ед.", "packs": "упак."}
TRANSPORT_LOG = "app.shared.doc_reader.transport"


@dataclass(frozen=True)
class StoredScan:
    id: UUID
    file_name: str
    uploaded_at: object
    content: bytes
    recognition: str


def recent_scans(session: Session, limit: int) -> list[StoredScan]:
    """The last `limit` scan files that have a stored reading, newest first."""
    rows = session.scalars(
        select(WarehouseReceiptFileRow).where(WarehouseReceiptFileRow.recognition != "")
        .order_by(WarehouseReceiptFileRow.uploaded_at.desc()).limit(limit)
    ).all()
    return [StoredScan(row.id, row.file_name, row.uploaded_at, row.content, row.recognition) for row in rows]


def _value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return " ".join(str(value).split())


def _same(a, b) -> bool:
    return _value(a).replace("ё", "е").casefold() == _value(b).replace("ё", "е").casefold()


@dataclass(frozen=True)
class Diff:
    header: tuple[tuple[str, str, str], ...]  # (field, stored, new)
    counts: tuple[int, int]  # lines (stored, new)
    lines: tuple[tuple[int, str, str, str], ...]  # (line №, field, stored, new)


def _line_value(line: UpdLine | None, name: str):
    return getattr(line, name) if line is not None else None


def diff_readings(stored: ScanReading, fresh: ScanReading) -> Diff:
    """The header fields and the lines (in order, the longer list's extra rows against nothing) that differ."""
    header = tuple(
        (name, _value(getattr(stored.found, name)), _value(getattr(fresh.found, name)))
        for name in HEADER if not _same(getattr(stored.found, name), getattr(fresh.found, name))
    )
    lines = []
    for index in range(max(len(stored.lines), len(fresh.lines))):
        old = stored.lines[index] if index < len(stored.lines) else None
        new = fresh.lines[index] if index < len(fresh.lines) else None
        for name in LINE_FIELDS:
            a, b = _line_value(old, name), _line_value(new, name)
            if not _same(a, b):
                lines.append((index + 1, name, _value(a), _value(b)))
    return Diff(header, (len(stored.lines), len(fresh.lines)), tuple(lines))


class _Errors(logging.Handler):
    """The transport's warnings of one file (model, why) — printed with the file instead of lost in the log."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.texts: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.texts.append(record.getMessage())


Reader = Callable[[bytes, str | None], ScanReading]


def _show(text: str) -> str:
    return f"«{text}»" if text else "—"


def run(scans: Iterable[StoredScan], read: Reader, *, model: str = "", budget: float | None = None,
        clock: Callable[[], float] = time.monotonic, out: TextIO | None = None) -> dict:
    """Every stored scan read anew by `read` (content, kind) and compared with its stored reading; prints per file and
    a summary; returns the counts. Writes nothing."""
    out = out or sys.stdout
    scans = list(scans)
    started = clock()
    files, header_wrong, line_wrong = Counter(), Counter(), Counter()
    transport = logging.getLogger(TRANSPORT_LOG)
    for number, scan in enumerate(scans, start=1):
        title = f"{number}/{len(scans)} {scan.file_name} [{scan.id}] {_value(scan.uploaded_at)}"
        if budget is not None and clock() - started >= budget:
            print(f"{title}: не прочитан — время прогона ({budget:.0f} с) вышло", file=out)
            files["не прочитано: время"] += 1
            continue
        stored = reading_from_json(scan.recognition)
        if stored is None:
            print(f"{title}: сохранённое чтение не разбирается — пропущен", file=out)
            files["пропущено"] += 1
            continue
        errors = _Errors()
        transport.addHandler(errors)
        # A logging config loaded earlier (alembic's fileConfig) may have switched the logger off: on for the read.
        disabled, level = transport.disabled, transport.level
        transport.disabled = False
        if transport.getEffectiveLevel() > logging.WARNING:
            transport.setLevel(logging.WARNING)
        try:
            fresh = read(scan.content, sniffed_type(scan.content))
        except Exception as error:  # noqa: BLE001 — one bad file must not stop the benchmark
            print(f"{title}: ошибка чтения — {type(error).__name__}: {error}", file=out)
            files["ошибка"] += 1
            continue
        finally:
            transport.removeHandler(errors)
            transport.disabled = disabled
            transport.setLevel(level)
        files["прочитано"] += 1
        if not fresh.pages:
            files["без модели (запасной способ)"] += 1
        elif not fresh.complete:
            files["модель — не все страницы"] += 1
        diff = diff_readings(stored, fresh)
        print(f"{title}: было {stored.engine or '—'} → стало {fresh.engine or '—'}"
              + (f", страниц {len(fresh.pages)}" if fresh.pages else ""), file=out)
        for text in (fresh.warning, *errors.texts):
            if text:
                print(f"    ! {text}", file=out)
        for name, a, b in diff.header:
            header_wrong[name] += 1
            print(f"    {HEADER[name]}: {_show(a)} → {_show(b)}", file=out)
        if diff.counts[0] != diff.counts[1]:
            files["другое число строк"] += 1
            print(f"    строк: {diff.counts[0]} → {diff.counts[1]}", file=out)
        changed_lines = {line for line, *_ in diff.lines}
        for line, name, a, b in diff.lines:
            line_wrong[name] += 1
            print(f"    стр. {line} {LINE_FIELDS[name]}: {_show(a)} → {_show(b)}", file=out)
        if not diff.header and not diff.lines:
            files["совпало целиком"] += 1
            print("    совпадает", file=out)
        line_wrong["строк с отличиями"] += len(changed_lines)
        line_wrong["строк сверено"] += max(diff.counts)
    print(f"\nФайлов: {len(scans)}" + (f", модель {model}" if model else "") + ", за "
          f"{clock() - started:.0f} с; " + ", ".join(f"{name} — {count}" for name, count in files.items()), file=out)
    print("Шапка, файлов с отличием: " + (", ".join(f"{HEADER[name]} — {header_wrong[name]}" for name in HEADER
                                                    if header_wrong[name]) or "нет"), file=out)
    print("Строки, отличий: " + (", ".join(f"{LINE_FIELDS[name]} — {line_wrong[name]}" for name in LINE_FIELDS
                                           if line_wrong[name]) or "нет")
          + f"; строк сверено {line_wrong['строк сверено']}, из них с отличиями {line_wrong['строк с отличиями']}",
          file=out)
    return {"files": dict(files), "header": dict(header_wrong), "lines": dict(line_wrong)}


def reader_of(config, types: Sequence[str]) -> Reader:
    """`read_scan` with this server's model settings and item types — exactly what the screen reads with."""
    return lambda content, kind: read_scan(content, kind, config, types)
