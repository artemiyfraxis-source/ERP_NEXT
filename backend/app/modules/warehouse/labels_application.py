"""«Печать этикеток ГРН» (stage option warehouse.labels, Roller_Buyer `routes/labels.ts`): the printer settings, what a
lot's label says and one print — the job is saved as a file first, then handed to the Windows print queue."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from app.shared.clock import MOSCOW
from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .labels.layout import GrnLabelData, label_png, render_label
from .labels.printer import print_dir, send_raw
from .labels.settings import CODE_TYPE_LABELS, DEFAULT_PRINT_SETTINGS, PrintSettings
from .labels.tspl import TsplOptions, build_tspl_job

PrintStatus = Literal["printed", "saved", "failed"]
COPIES_MAX = 50

# A sample with the same fields as a real label — to see that everything fits (Roller_Buyer SAMPLE).
SAMPLE = GrnLabelData(
    grn="FRX.26.0000041",
    sku="510210200",
    item_name="Соединитель PicoBlade 2pin (MOLEX)",
    quantity="338",
    unit="шт",
    manufacturer_batch="12/2026",
    received_on=date(2026, 9, 25),
)


@dataclass(frozen=True)
class PrintResult:
    """What happened to one print: the job file is always saved, the printer may or may not take it."""

    status: PrintStatus
    reason: str | None  # why it was not printed, in words
    file: str  # the saved TSPL job, to pour into the printer by hand if the queue refused
    copies: int

    def as_dict(self) -> dict:
        return asdict(self)


def label_settings(uow) -> PrintSettings:
    with uow:
        return uow.label_settings.load() or DEFAULT_PRINT_SETTINGS


def _settings_message(settings: PrintSettings) -> str:
    return (
        f"принтер {settings.printer_name or '— не выбран'}; {CODE_TYPE_LABELS[settings.code_type]}; "
        f"{_mm(settings.width_mm)}×{_mm(settings.height_mm)} мм, {settings.dpi} dpi; зазор {_mm(settings.gap_mm)} мм; "
        f"нагрев {settings.density}; скорость {settings.speed}; сдвиг {_mm(settings.offset_x_mm)} / "
        f"{_mm(settings.offset_y_mm)} мм"
    )


def _mm(value: float) -> str:
    return format(Decimal(str(value)).normalize(), "f").replace(".", ",")


def save_label_settings(uow, settings: PrintSettings, actor: Actor, now: datetime) -> PrintSettings:
    """Whole settings replaced; who changed them goes to the warehouse journal."""
    with uow:
        uow.label_settings.save(settings, now, actor.name)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=UUID(int=0), event_type="warehouse.label_settings_saved", actor=actor,
            occurred_at=now, details={**settings.model_dump(), "message": f"настройки — {_settings_message(settings)}"},
        ))
        saved = uow.label_settings.load()
        uow.commit()
    return saved


def _quantity(value: Decimal) -> str:
    # The balance to three decimals, without trailing zeros: «338», «2.5».
    return format(value.quantize(Decimal("0.001")).normalize(), "f")


def _day(moment: datetime | None) -> date | None:
    if moment is None:
        return None
    # SQLite gives back the Moscow wall time without a zone; PostgreSQL an aware value.
    return moment.date() if moment.tzinfo is None else moment.astimezone(MOSCOW).date()


def lot_code(grn: str, package: str) -> str:
    """What the label's codes carry: the ГРН, with «-<пакет>» for a package — the scan field reads it back as that
    lot (stage option warehouse.scan)."""
    return f"{grn}-{package}" if package else grn


def lot_label_data(uow, lot_id: UUID) -> GrnLabelData:
    """What the lot's label says: its CURRENT balance is what physically lies in the box. KeyError — no such lot."""
    with uow:
        lot = uow.warehouse.get(lot_id)
        if lot is None:
            raise KeyError("Партия не найдена")
        received = uow.warehouse.received_at(lot_id)
    name = lot.name or lot.article
    return GrnLabelData(
        grn=lot_code(lot.grn, lot.package),
        sku=lot.article if lot.article and lot.article != name else "",
        item_name=name,
        quantity=_quantity(lot.balance),
        unit=lot.unit,
        manufacturer_batch=lot.series,
        received_on=_day(received),
    )


def find_lot(uow, warehouse: str, grn: str, package: str) -> UUID:
    """The lot of a ГРН + ПАКЕТ (the receiving screen prints the labels of what it has just put on stock).
    KeyError — not on stock yet: the warehouse takes a receipt in the background, a moment after «Передать на склад»."""
    with uow:
        lot = uow.warehouse.by_key(warehouse.strip(), grn.strip(), package.strip())
    if lot is None:
        raise KeyError("Партия ещё не на складе: после «Передать на склад» склад принимает её в фоне — повторите через несколько секунд")
    return lot.id


def preview_png(data: GrnLabelData, settings: PrintSettings) -> bytes:
    """The preview is the very raster the printer gets, dot for dot."""
    return label_png(render_label(data, settings))


def tspl_job(data: GrnLabelData, settings: PrintSettings, copies: int) -> bytes:
    layout = render_label(data, settings)
    dots_per_mm = settings.dpi / 25.4
    return build_tspl_job(layout.image, TsplOptions(
        width_mm=settings.width_mm,
        height_mm=settings.height_mm,
        gap_mm=settings.gap_mm,
        density=settings.density,
        speed=settings.speed,
        copies=copies,
        offset_x_dots=settings.offset_x_mm * dots_per_mm,
        offset_y_dots=settings.offset_y_mm * dots_per_mm,
    ))


def _job_file(directory: Path, grn: str, now: datetime) -> Path:
    stamp = now.strftime("%Y-%m-%dT%H-%M-%S-%f")
    safe = re.sub(r"[^\w.-]", "_", grn, flags=re.ASCII)
    candidate = directory / f"{stamp}-{safe}.prn"
    number = 1
    while candidate.exists():
        number += 1
        candidate = directory / f"{stamp}-{safe}-{number}.prn"
    return candidate


def print_label(data: GrnLabelData, settings: PrintSettings, copies: int, now: datetime) -> PrintResult:
    """The job is saved as a file first — if the queue refuses, it can be poured into the printer by hand."""
    if not 1 <= copies <= COPIES_MAX:
        raise ValueError(f"Копий — от 1 до {COPIES_MAX}")
    job = tspl_job(data, settings, copies)
    directory = print_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = _job_file(directory, data.grn, now)
    path.write_bytes(job)
    sent = send_raw(settings.printer_name, path)
    status: PrintStatus = "printed" if sent.sent else "failed" if sent.attempted else "saved"
    return PrintResult(status=status, reason=sent.reason, file=str(path), copies=copies)
