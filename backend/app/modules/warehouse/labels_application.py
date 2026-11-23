"""«Печать этикеток ГРН» (Roller_Buyer `routes/labels.ts`): the printer settings, the label templates of the designer,
what a lot's label says and one print — the TSPL job is queued for the print agent of the warehouse PC (label_jobs.py),
the server never prints.

Every label — a lot's, the sample, an act of «Приёмка с производства» — is drawn by the one renderer
(`labels/render.py`) from the template of its kind in the settings' format: the saved one (label_templates.py) or the
built-in one (`labels/defaults.py`)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from app.shared.clock import MOSCOW
from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .label_jobs import COPIES_MAX, QUEUED
from .label_templates import (
    FormMetadata, StoredTemplate, reset_template, save_template, stored_template, stored_template_in, stored_templates,
)
from .labels.defaults import GRN_DESIGN_REVISIONS, default_template, grn_template
from .labels.layout import GrnLabelData, LabelLayout, label_png
from .labels.render import render
from .labels.settings import CODE_TYPE_LABELS, DEFAULT_PRINT_SETTINGS, PrintSettings
from .labels.template import (
    FIELDS, FORMATS, KIND_LABELS, LabelContent, LabelTemplate, format_key, intake_content, lot_content, parse_template,
)
from .labels.tspl import TsplOptions, build_tspl_job

# «queued» — in the print queue; the agent of the warehouse PC prints it (the old keys stay for the screens).
PrintStatus = Literal["queued"]
# The queue's kind (migrations 0141/0188), not the template's: an intake label is queued as «lot».
LabelKind = Literal["lot", "sample", "instrument"]

# A sample with the same fields as a real label — to see that everything fits (Roller_Buyer SAMPLE).
SAMPLE = GrnLabelData(
    grn="FRX.26.0000041",
    sku="XHP-4",
    item_name="XH 4pin (однорядный)",
    quantity="338",
    unit="шт",
    manufacturer_batch="12/2026",
    received_on=date(2026, 9, 25),
    place="1F4",
    warehouse="ЦАР",
    manufacturer="JST",
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


def sample_content(kind: str, category: str = "") -> LabelContent:
    if category:
        if category not in GRN_DESIGN_REVISIONS or kind not in ("lot", "intake"):
            raise ValueError("Неизвестная категория или вид этикетки ГРН")
        context = {"purpose": "Цех", "equipment": "Стенд", "subcontractor": "Цех", "production_order": "42",
                   "acceptance_document": "17", "customer_order": "42", "transfer_document": "17", "project": "42"}
        data = replace(SAMPLE, grn=f"{category}.26.000002", grn_number=f"{category}.26.000002", category=category,
                       sku="SMA-C174P", item_name="SMA-male RG-174", manufacturer="RUICHI",
                       manufacturer_batch="05364575", expiry_date=date(2027, 11, 22), quality_state="IQH",
                       context=context, owner="Заказчик")
        return intake_content(data, "42", "КБ1") if kind == "intake" else lot_content(data)
    if kind == "instrument":
        # The designer's sample: a measured adapter with every field of the identification card filled.
        from .labels.template import instrument_content
        return instrument_content(
            "СИ.034", "Переход коаксиальный SMA female–SMA female S-322", "S-322.1", date(2027, 11, 17), model="S-322",
            manufacturer="Gsm-repiteri", type_line="Переход коаксиальный", model_line="S-322",
            bands=(("1", "1,05", "0,05"), ("3", "1,10", "0,10"), ("6", "1,15", "0,10"), ("12", "1,25", "0,50")),
            mating_resource=500, temp_min_c=-55, temp_max_c=125,
            connector_line="SMA розетка – SMA розетка", range_line="Частотный диапазон 0–12 ГГц",
            table=("до ГГц  1     3     6     12", "КСВН ≤  1,05  1,10  1,15  1,25", "дБ ≤    0,05  0,10  0,10  0,50"),
            table_long=("Частота до, ГГц  1     3     6     12", "КСВН ≤           1,05  1,10  1,15  1,25",
                        "Потери, дБ ≤     0,05  0,10  0,10  0,50"),
            measured_on=date(2026, 11, 16), next_control=date(2027, 11, 16), measurement_date="Замер 16.11.26")
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


def effective_label_settings(settings: PrintSettings | None, kind: str = "lot") -> PrintSettings:
    """One format policy for the designer, previews and queued labels."""
    if settings is not None:
        return settings
    if kind == "instrument":
        return DEFAULT_PRINT_SETTINGS.model_copy(update={"height_mm": 30})
    return DEFAULT_PRINT_SETTINGS


def label_settings(uow, kind: str = "lot") -> PrintSettings:
    with uow:
        return effective_label_settings(uow.label_settings.load(), kind)


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
        return _data_in(uow.session, lot, received)


def _data_in(session, lot, received):
    from app.shared.project_identity import display_grn_context
    data = label_of(lot, received)
    data = replace(data, context=display_grn_context(session, data.context))
    if getattr(lot, "material_owner_id", None):
        from .grn_registry import MaterialOwnerRow
        owner = session.get(MaterialOwnerRow, lot.material_owner_id)
        data = replace(data, owner=owner.display_name if owner else str(lot.material_owner_id))
    return data


def label_of(lot, received: datetime | None) -> GrnLabelData:
    """The label of a lot already read (ГРН, артикул, наименование, остаток, место, склад, дата прихода)."""
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
        category=getattr(lot, "grn_category", None) or "",
        grn_id=getattr(lot, "grn_id", None), grn_number=lot.grn,
        expiry_date=getattr(lot, "expiry_date", None), quality_state=getattr(lot, "quality_state", None),
        quality_basis=getattr(lot, "quality_basis", None) or "", context=getattr(lot, "grn_context", None) or {},
        owner=str(getattr(lot, "material_owner_id", None) or ""),
        package=lot.package,
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
    return _builtin_render(content, settings)[0]


def _builtin_render(content: LabelContent, settings: PrintSettings) -> tuple[LabelLayout, LabelTemplate]:
    label_format = format_key(settings.width_mm, settings.height_mm)
    if content.category:
        template = grn_template(content.category, content.kind, label_format)
        return render(template, content, settings), template
    if settings.code_type != "qr":
        try:
            template = default_template(content.kind, label_format, settings.code_type)
            layout = render(template, content, settings)
            if not layout.skipped:
                return layout, template
        except ValueError:
            pass
    template = default_template(content.kind, label_format, "qr")
    return render(template, content, settings), template


def render_label(uow, content: LabelContent, settings: PrintSettings) -> LabelLayout:
    """The label as it prints: the saved template of its kind in the settings' format, else the built-in one.
    ValueError — a code does not fit its element."""
    with uow:
        return layout_in(uow.session, content, settings)


def lot_layout_in(session, lot, received: datetime | None, settings: PrintSettings) -> LabelLayout:
    """The lot's label as `render_label` draws it, read inside a transaction already open."""
    return layout_in(session, lot_content(_data_in(session, lot, received)), settings)


def layout_in(session, content: LabelContent, settings: PrintSettings) -> LabelLayout:
    """Render a saved or built-in template using the caller's open transaction."""
    stored = selected_template_in(session, content, settings)
    content = with_form(content, stored.form if stored else None)
    if stored is not None and stored.template is not None:
        return render(stored.template, content, settings)
    return render_builtin(content, settings)


def selected_template_in(session, content, settings):
    return stored_template_in(session, content.kind, format_key(settings.width_mm, settings.height_mm),
                              content.category, settings.printer_name if content.category else "")


def with_form(content: LabelContent, form: FormMetadata | None) -> LabelContent:
    if not content.category:
        return content
    values = dict(content.values, form_footer=form.footer() if form else "")
    if content.category == "CSM":
        values["context"] = " · ".join(part for part in (content.value("owner"), content.value("context")) if part)
    return replace(content, values=values)


def prepare_label_in(session, content: LabelContent, settings: PrintSettings):
    """Read the design once: raster and provenance always describe the same revision and payload."""
    stored = selected_template_in(session, content, settings)
    content = with_form(content, stored.form if stored else None)
    template = stored.template if stored and stored.template else None
    revision = f"db:{stored.version}" if template else f"builtin:{GRN_DESIGN_REVISIONS.get(content.category, 'legacy-1')}"
    if template:
        layout = render(template, content, settings)
    else:
        layout, template = _builtin_render(content, settings)
    source = dict(content.source)
    if content.grn_id:
        from .grn_registry import GrnRegistryRow
        identity = session.get(GrnRegistryRow, content.grn_id)
        if identity is None:
            raise ValueError("Не найдена идентичность ГРН")
        if ((content.grn_number and identity.number != content.grn_number) or (identity.category or "") != content.category
                or lot_code(identity.number, source.get("package") or "") != content.code):
            raise ValueError("Данные этикетки не соответствуют сохранённой идентичности ГРН")
        content = replace(content, grn_number=identity.number)
        source.update(source_kind=identity.source_kind, source_op_id=identity.source_op_id)
        from .grn import GrnContext
        source["origin_context"] = {name: str(value) if name == "owner_id" and value is not None else value
                                    for name in GrnContext.__dataclass_fields__
                                    for value in [getattr(identity, name)]}
    snapshot = dict(grn_id=content.grn_id, grn_category=content.category or None,
                    grn_number=(content.grn_number or content.code) if content.kind != "instrument" else None,
                    template_kind=content.kind,
                    template_format=format_key(settings.width_mm, settings.height_mm), template_version=revision,
                    grn_provenance={**source, "code": content.code, "values": dict(content.values), "source": source,
                                    "template": template.model_dump(mode="json"),
                                    "settings": settings.model_dump(mode="json")})
    if stored and stored.form:
        snapshot.update(stored.form.model_dump())
    if content.category and settings.printer_name:
        snapshot["target_printer"] = settings.printer_name
    return layout, snapshot


def preview_png(uow, content: LabelContent, settings: PrintSettings) -> bytes:
    """The preview is the very raster the printer gets, dot for dot."""
    return label_png(render_label(uow, content, settings))


# --- The designer's templates ------------------------------------------------------------------------------------


def _template_view(template: LabelTemplate, stored: StoredTemplate | None, label_format: str, category: str = "") -> dict:
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
        "category": category,
        "design_revision": f"db:{stored.version}" if saved else f"builtin:{GRN_DESIGN_REVISIONS.get(category, 'legacy-1')}",
        "form": stored.form.model_dump(mode="json") if stored and stored.form else None,
    }


def templates_view(uow, kind: str, settings: PrintSettings, category: str = "") -> dict:
    """The designer's start: the fields of the kind and the template of every format (saved or built-in — the built-in
    one follows the settings' code type)."""
    stored = stored_templates(uow, kind, category, settings.printer_name if category else "")
    formats = {}
    for label_format in FORMATS:
        saved = stored.get(label_format)
        template = saved.template if saved is not None and saved.template is not None else \
            (grn_template(category, kind, label_format) if category else default_template(kind, label_format, settings.code_type))
        formats[label_format] = _template_view(template, saved, label_format, category)
    return {
        "kind": kind,
        "kind_label": KIND_LABELS[kind],
        "fields": [{"key": key, "label": label} for key, label in FIELDS[kind].items()],
        "formats": formats,
        "current_format": format_key(settings.width_mm, settings.height_mm),
        "code_type": settings.code_type,
        "category": category, "printer_name": settings.printer_name,
    }


def check_renders(template: LabelTemplate, kind: str, label_format: str, settings: PrintSettings,
                  content: LabelContent | None = None, category: str = "", form: FormMetadata | None = None) -> LabelLayout:
    """Draw the template on the sample (or the given content) at the format's size. ValueError — a code does not fit."""
    return render(template, with_form(content or sample_content(kind, category), form), _sized(settings, label_format))


def save_label_template(uow, kind: str, label_format: str, raw, expected_version: int | None, settings: PrintSettings,
                        actor: Actor, now: datetime, *, category: str = "", form: FormMetadata | None = None) -> dict:
    """TemplateError / ValueError — not a valid template (Russian); StaleTemplate — saved by someone in between."""
    template = parse_template(raw, kind, label_format)
    check_renders(template, kind, label_format, settings, category=category, form=form)
    stored = save_template(uow, kind, label_format, template, expected_version, actor, now, category=category,
                           printer_name=settings.printer_name if category else "", form=form)
    return _template_view(template, stored, label_format, category)


def reset_label_template(uow, kind: str, label_format: str, settings: PrintSettings, actor: Actor, now: datetime,
                         *, category: str = "", expected_version: int | None = None) -> dict:
    reset_template(uow, kind, label_format, actor, now, category=category, printer_name=settings.printer_name if category else "",
                   expected_version=expected_version)
    stored = stored_template(uow, kind, label_format, category, settings.printer_name if category else "")
    template = stored.template if stored and stored.template else (
        grn_template(category, kind, label_format) if category else default_template(kind, label_format, settings.code_type))
    return _template_view(template, stored, label_format, category)


def preview_template_png(raw, kind: str, label_format: str, settings: PrintSettings,
                         content: LabelContent | None = None, *, category: str = "", form: FormMetadata | None = None) -> bytes:
    """The designer's unsaved template drawn exactly as it would print (sample data unless a lot is given)."""
    template = parse_template(raw, kind, label_format)
    if content and content.category != category:
        raise ValueError("Категория образца не совпадает с категорией шаблона")
    return label_png(check_renders(template, kind, label_format, settings, content, category, form))


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
    from .label_jobs import add_job
    with uow:
        # A reprint reads current stock and quality in the same transaction as its immutable job.
        if lot_id is not None and content.kind == "lot":
            lot = uow.warehouse.get(lot_id)
            if lot is None:
                raise KeyError("Партия не найдена")
            content = lot_content(_data_in(uow.session, lot, uow.warehouse.received_at(lot_id)))
        layout, snapshot = prepare_label_in(uow.session, content, settings)
        job_id = add_job(uow.session, tspl=tspl_job(layout, settings, copies), copies=copies, kind=kind, lot_id=lot_id,
                         created_by=created_by, now=now, snapshot=snapshot)
        uow.commit()
    return PrintResult(status="queued", reason=QUEUED, file=str(job_id), copies=copies, job_id=str(job_id))
