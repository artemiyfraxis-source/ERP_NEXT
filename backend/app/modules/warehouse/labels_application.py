"""«Печать этикеток ГРН» (Roller_Buyer `routes/labels.ts`): the printer settings, the label templates of the designer,
what a lot's label says and one print — the TSPL job is queued for the print agent of the warehouse PC (label_jobs.py),
the server never prints.

Every label — a lot's, the sample, an act of «Приёмка с производства» — is drawn by the one renderer
(`labels/render.py`) from the template of its kind in the settings' format: the saved one (label_templates.py) or the
built-in one (`labels/defaults.py`)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from app.shared.clock import MOSCOW
from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .label_jobs import COPIES_MAX, QUEUED, enqueue
from .label_templates import StoredTemplate, reset_template, save_template, stored_template, stored_templates
from .labels.defaults import default_template
from .labels.layout import GrnLabelData, LabelLayout, label_png
from .labels.render import render
from .labels.settings import CODE_TYPE_LABELS, DEFAULT_PRINT_SETTINGS, PrintSettings
from .labels.template import (
    FIELDS, FORMATS, KIND_LABELS, LabelContent, LabelTemplate, format_key, intake_content, lot_content, parse_template,
)
from .labels.tspl import TsplOptions, build_tspl_job

# «queued» — in the print queue; the agent of the warehouse PC prints it (the old keys stay for the screens).
PrintStatus = Literal["queued"]
# The job's kind in the queue (CHECK of migration 0141), not the template's: an intake label is queued as «lot».
LabelKind = Literal["lot", "sample"]

# A sample with the same fields as a real label — to see that everything fits (Roller_Buyer SAMPLE).
SAMPLE = GrnLabelData(
    grn="FRX.26.0000041",
    sku="510210200",
    item_name="Соединитель PicoBlade 2pin (MOLEX)",
    quantity="338",
    unit="шт",
    manufacturer_batch="12/2026",
    received_on=date(2026, 9, 25),
    place="1F4",
    warehouse="ЦАР",
)
# The sample of an act of «Приёмка с производства» (ГРН «ГП.YY.NNNNNNN», «проект КБ» in the article's place).
SAMPLE_INTAKE = intake_content(GrnLabelData(
    grn="ГП.26.0000012",
    sku="533К.12 КБ1",
    item_name="Жгут CCW-0,2 питания блока",
    quantity="350",
    unit="шт",
    manufacturer_batch="",
    received_on=date(2026, 10, 22),
    place="А-1",
    warehouse="ГП",
), "533К.12", "КБ1")


def sample_content(kind: str) -> LabelContent:
    return SAMPLE_INTAKE if kind == "intake" else lot_content(SAMPLE)


@dataclass(frozen=True)
class PrintResult:
    """One print handed to the queue. The keys are the old ones so the screens still render: `reason` says it is
    queued, `file` names the queued job (there is no job file any more)."""

    status: PrintStatus
    reason: str | None
    file: str
    copies: int
    job_id: str

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
    lot."""
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
        place=lot.storage_location,
        warehouse=lot.warehouse,
        manufacturer=lot.manufacturer,
        purpose=lot.purpose,
    )


def find_lot(uow, warehouse: str, grn: str, package: str) -> UUID:
    """The lot of a ГРН + ПАКЕТ (the receiving screen prints the labels of what it has just put on stock).
    KeyError — not on stock yet: the warehouse takes a receipt in the background, a moment after «Передать на склад»."""
    with uow:
        lot = uow.warehouse.by_key(warehouse.strip(), grn.strip(), package.strip())
    if lot is None:
        raise KeyError("Партия ещё не на складе: после «Передать на склад» склад принимает её в фоне — повторите через несколько секунд")
    return lot.id


def _sized(settings: PrintSettings, label_format: str) -> PrintSettings:
    width, height = FORMATS[label_format]
    return settings.model_copy(update={"width_mm": float(width), "height_mm": float(height)})


def render_builtin(content: LabelContent, settings: PrintSettings) -> LabelLayout:
    """The built-in template of the settings' code type. As the hard-coded layout did, a ГРН Code128 cannot carry
    (Cyrillic) or that does not fit the bars falls back to the QR-only label."""
    label_format = format_key(settings.width_mm, settings.height_mm)
    if settings.code_type != "qr":
        try:
            layout = render(default_template(content.kind, label_format, settings.code_type), content, settings)
            if not layout.skipped:
                return layout
        except ValueError:
            pass
    return render(default_template(content.kind, label_format, "qr"), content, settings)


def render_label(uow, content: LabelContent, settings: PrintSettings) -> LabelLayout:
    """The label as it prints: the saved template of its kind in the settings' format, else the built-in one.
    ValueError — a code does not fit its element."""
    stored = stored_template(uow, content.kind, format_key(settings.width_mm, settings.height_mm))
    if stored is not None and stored.template is not None:
        return render(stored.template, content, settings)
    return render_builtin(content, settings)


def preview_png(uow, content: LabelContent, settings: PrintSettings) -> bytes:
    """The preview is the very raster the printer gets, dot for dot."""
    return label_png(render_label(uow, content, settings))


# --- The designer's templates ------------------------------------------------------------------------------------


def _template_view(template: LabelTemplate, stored: StoredTemplate | None, label_format: str) -> dict:
    width, height = FORMATS[label_format]
    saved = stored is not None and stored.template is not None
    return {
        "template": template.model_dump(mode="json"),
        "is_default": not saved,
        # The version a save must send back: null while nothing is stored.
        "version": stored.version if stored is not None else None,
        "width_mm": width,
        "height_mm": height,
        "updated_at": stored.updated_at.isoformat() if saved else None,
        "updated_by": stored.updated_by if saved else None,
    }


def templates_view(uow, kind: str, settings: PrintSettings) -> dict:
    """The designer's start: the fields of the kind and the template of every format (saved or built-in — the built-in
    one follows the settings' code type)."""
    stored = stored_templates(uow, kind)
    formats = {}
    for label_format in FORMATS:
        saved = stored.get(label_format)
        template = saved.template if saved is not None and saved.template is not None else \
            default_template(kind, label_format, settings.code_type)
        formats[label_format] = _template_view(template, saved, label_format)
    return {
        "kind": kind,
        "kind_label": KIND_LABELS[kind],
        "fields": [{"key": key, "label": label} for key, label in FIELDS[kind].items()],
        "formats": formats,
        "current_format": format_key(settings.width_mm, settings.height_mm),
        "code_type": settings.code_type,
    }


def check_renders(template: LabelTemplate, kind: str, label_format: str, settings: PrintSettings,
                  content: LabelContent | None = None) -> LabelLayout:
    """Draw the template on the sample (or the given content) at the format's size. ValueError — a code does not fit."""
    return render(template, content or sample_content(kind), _sized(settings, label_format))


def save_label_template(uow, kind: str, label_format: str, raw, expected_version: int | None, settings: PrintSettings,
                        actor: Actor, now: datetime) -> dict:
    """TemplateError / ValueError — not a valid template (Russian); StaleTemplate — saved by someone in between."""
    template = parse_template(raw, kind, label_format)
    check_renders(template, kind, label_format, settings)
    stored = save_template(uow, kind, label_format, template, expected_version, actor, now)
    return _template_view(template, stored, label_format)


def reset_label_template(uow, kind: str, label_format: str, settings: PrintSettings, actor: Actor, now: datetime) -> dict:
    reset_template(uow, kind, label_format, actor, now)
    return _template_view(default_template(kind, label_format, settings.code_type), None, label_format)


def preview_template_png(raw, kind: str, label_format: str, settings: PrintSettings,
                         content: LabelContent | None = None) -> bytes:
    """The designer's unsaved template drawn exactly as it would print (sample data unless a lot is given)."""
    template = parse_template(raw, kind, label_format)
    return label_png(check_renders(template, kind, label_format, settings, content))


def tspl_job(layout: LabelLayout, settings: PrintSettings, copies: int) -> bytes:
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


def print_label(uow, content: LabelContent, settings: PrintSettings, copies: int, now: datetime, *, kind: LabelKind,
                lot_id: UUID | None, created_by: str) -> PrintResult:
    """The TSPL job (copies inside it) goes to the print queue; the agent of the warehouse PC prints it. The label is
    drawn from the template of `content.kind`; `kind` is the job's kind in the queue."""
    if not 1 <= copies <= COPIES_MAX:
        raise ValueError(f"Копий — от 1 до {COPIES_MAX}")
    layout = render_label(uow, content, settings)
    job_id = enqueue(uow, tspl=tspl_job(layout, settings, copies), copies=copies, kind=kind, lot_id=lot_id,
                     created_by=created_by, now=now)
    return PrintResult(status="queued", reason=QUEUED, file=str(job_id), copies=copies, job_id=str(job_id))
