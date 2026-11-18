"""Compose the instrument registry and warehouse label queue in one transaction."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.production.api import production_uow_factory
from app.modules.production.infrastructure import SqlAlchemyInstrumentFileRepository, SqlAlchemyInstrumentRepository
from app.modules.production.instrument_serials import article_of
from app.modules.production.instrument_characterization import (
    application, characterization, connector_line, impedance_warning, number,
)
from app.modules.production.instrument_norms import item_spec
from app.modules.production.instrument_passport import passport_pdf
from app.modules.production.rf_infrastructure import RfRepository
from app.modules.production.rf_interfaces import ROLE_LABELS, RfDetails, is_adapter_name
from app.modules.production.traceability import InstrumentKind
from app.modules.warehouse.infrastructure import SqlAlchemyLabelSettings
from app.modules.warehouse.label_jobs import LabelAgentRow, add_job
from app.modules.warehouse.labels.layout import label_png
from app.modules.warehouse.labels.template import instrument_content
from app.modules.warehouse.labels_application import effective_label_settings, layout_in, tspl_job
from app.shared.clock import MOSCOW, moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, production_overview_principal, production_quality_principal
from app.shared.persistence import SqlAlchemyAuditRepository
from app.shared.public_url import instrument_card_url, passport_qr_warning, public_passport_url

router = APIRouter(prefix="/api/production/instruments", tags=["production"])
UowFactory = Annotated[Callable, Depends(production_uow_factory)]
MAX_LABELS = 200
AGENT_TIMEOUT = timedelta(minutes=2)
OFFLINE = "Принтер склада не на связи"


class InstrumentPrintPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    copies: int = Field(default=1, ge=1, le=5, strict=True)


class InstrumentLabelsPayload(InstrumentPrintPayload):
    ids: list[UUID] = Field(min_length=1, max_length=MAX_LABELS)

    @model_validator(mode="after")
    def bounded(self):
        if len(set(self.ids)) != len(self.ids):
            raise ValueError("В списке повторяются приборы")
        if len(self.ids) * self.copies > MAX_LABELS:
            raise ValueError(f"За один раз — не более {MAX_LABELS} этикеток")
        return self


def _aware(moment: datetime) -> datetime:
    # SQLite preserves Moscow wall time without the zone, like the existing label queue.
    return moment.replace(tzinfo=MOSCOW) if moment.tzinfo is None else moment


def label_agent_in(session, now: datetime) -> dict:
    agent = session.get(LabelAgentRow, 1)
    age = _aware(now) - _aware(agent.last_seen) if agent else None
    return {
        "online": bool(agent and agent.printer.strip() and age is not None and timedelta(0) <= age <= AGENT_TIMEOUT),
        "last_seen": agent.last_seen.isoformat() if agent else None,
        "host": agent.host if agent else "",
        "printer": agent.printer if agent else "",
        "qr_link_enabled": not bool(passport_qr_warning()),
        "qr_warning": passport_qr_warning(),
    }


def _characterization(session, instrument_id):
    for _, file_id in RfRepository(session).latest_characterization_file_ids([instrument_id]):
        file = SqlAlchemyInstrumentFileRepository(session).get(instrument_id, file_id)
        # A corrupt/replaced latest measurement must not silently revive an older measurement.
        return characterization(file) if file else None
    return None


def _next_control(measured_on):
    """Same interval as the passport (CONTROL_INTERVAL_MONTHS), day clamped to the month's end."""
    from calendar import monthrange
    from datetime import date
    from app.modules.production.instrument_public import CONTROL_INTERVAL_MONTHS
    month = measured_on.month - 1 + CONTROL_INTERVAL_MONTHS
    year, month = measured_on.year + month // 12, month % 12 + 1
    return date(year, month, min(measured_on.day, monthrange(year, month)[1]))


def type_line(name: str) -> str:
    """The kind of item from its name: the leading Russian words before the first Latin/digit word
    («Переход коаксиальный ADP1A-…» → «Переход коаксиальный»)."""
    words = []
    for word in name.split():
        if any("a" <= ch.lower() <= "z" or ch.isdigit() for ch in word):
            break
        words.append(word)
    return " ".join(words).strip(" ,–—-")


def _limit(value: float | None) -> str:
    return f"{value:.2f}".replace(".", ",") if value is not None else ">2"


def band_cells(steps) -> tuple[tuple[str, str, str], ...]:
    """The label grid's cells per band: (up to, GHz; VSWR ≤; loss ≤, dB) — the round limits the passport quotes."""
    return tuple((number(step.stop_ghz), _limit(step.vswr_round), _limit(step.loss_round)) for step in steps.bands)


def kind_line(name: str, rf: RfDetails) -> str:
    """The label's kind: an adapter reads «Переход коаксиальный» (ГОСТ term, owner 18.11) whatever its old name says."""
    return ROLE_LABELS["adapter"] if rf.role == "adapter" or is_adapter_name(name) else type_line(name)


def _content(item, session=None):
    rf = RfRepository(session).get(item.id) if session is not None else RfDetails()
    measured = _characterization(session, item.id) if session is not None else None
    steps = application(measured, tuple(port.series for port in rf.interfaces)) if measured else None
    # The passport's spec: the card's own mating resource, else the maker's/MIL sheet's; the count waits for PR #913.
    spec = item_spec(item.name, item.doc_designation or "", rf)
    return instrument_content(
        item.inventory_number, item.name, item.serial_number,
        item.verification_due.date() if item.verification_due else None,
        model=item.doc_designation, accessory=item.kind is InstrumentKind.ACCESSORY,
        in_house_serial=False,  # owner 18.11: every serial reads «Зав. №» (vendor or «article.N»)
        manufacturer=item.manufacturer,
        connector_line=connector_line(item.name, item.serial_number, rf),
        parameters_line=steps.limit_line if steps else "Не измерен",
        loss_line=steps.values_line if steps else "",
        range_line=steps.range_line if steps else "Не измерен",
        table=steps.table_lines() if steps else ("", "", ""),
        table_long=steps.table_lines(long=True) if steps else ("", "", ""),
        bands=band_cells(steps) if steps else (),
        type_line=kind_line(item.name, rf), model_line=item.doc_designation or article_of(item) or "",
        measured_on=measured.measured_on if measured else None,
        next_control=_next_control(measured.measured_on) if measured else None,
        measurement_date=measured.measurement_date if measured else "",
        impedance_warning=impedance_warning(rf), card_url=public_passport_url(item.id),
        mating_resource=spec.mating_resource if spec else None, mating_count=None,
        temp_min_c=spec.temp_min_c if spec else None, temp_max_c=spec.temp_max_c if spec else None,
    )


def _settings(session):
    return effective_label_settings(SqlAlchemyLabelSettings(session).load(), "instrument")


def enqueue_instrument_labels(sessions, ids: list[UUID], copies: int, actor, now: datetime) -> dict:
    # Validate before opening a transaction also for callers outside HTTP.
    payload = InstrumentLabelsPayload(ids=ids, copies=copies)
    with sessions.begin() as session:
        if not label_agent_in(session, now)["online"]:
            raise HTTPException(status_code=409, detail=OFFLINE)
        repository = SqlAlchemyInstrumentRepository(session)
        settings = _settings(session)
        jobs = []
        audit = SqlAlchemyAuditRepository(session)
        for item_id in payload.ids:
            item = repository.get(item_id)
            if item is None:
                raise KeyError("СИ не найдено")
            tspl = tspl_job(layout_in(session, _content(item, session), settings), settings, 1)
            item_jobs = [add_job(session, tspl=tspl, copies=1, kind="instrument", lot_id=None,
                                 created_by=actor.name, now=now) for _ in range(copies)]
            jobs.extend(item_jobs)
            audit.add(AuditEvent(
                id=uuid4(), aggregate_id=item.id, event_type="production.instrument_label_queued",
                actor=actor, occurred_at=now,
                details={"inventory_number": item.inventory_number, "copies": copies,
                         "job_ids": [str(job) for job in item_jobs],
                         "message": f"Этикетки {item.inventory_number}: {copies} — в очереди принтера"},
            ))
        return {"status": "queued", "job_ids": [str(job) for job in jobs], "copies": copies}


def _rendered(operation):
    try:
        return operation()
    except KeyError as error:
        raise HTTPException(status_code=404, detail=error.args[0]) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/label-agent")
def instrument_label_agent(
    _principal: Annotated[Principal, Depends(production_quality_principal)],
    now: Annotated[datetime, Depends(moscow_now)], uow_factory: UowFactory,
) -> dict:
    with uow_factory().session_factory() as session:
        return label_agent_in(session, now)


@router.post("/labels")
def print_instrument_labels(
    payload: InstrumentLabelsPayload, principal: Annotated[Principal, Depends(production_quality_principal)],
    now: Annotated[datetime, Depends(moscow_now)], uow_factory: UowFactory,
) -> dict:
    return _rendered(lambda: enqueue_instrument_labels(
        uow_factory().session_factory, payload.ids, payload.copies, principal.actor, now))


@router.post("/{instrument_id}/label")
def print_instrument_label(
    instrument_id: UUID, payload: InstrumentPrintPayload,
    principal: Annotated[Principal, Depends(production_quality_principal)],
    now: Annotated[datetime, Depends(moscow_now)], uow_factory: UowFactory,
) -> dict:
    return _rendered(lambda: enqueue_instrument_labels(
        uow_factory().session_factory, [instrument_id], payload.copies, principal.actor, now))


@router.get("/{instrument_id}/label.png")
def instrument_label_preview(
    instrument_id: UUID, _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: UowFactory,
) -> Response:
    def preview():
        with uow_factory().session_factory() as session:
            item = SqlAlchemyInstrumentRepository(session).get(instrument_id)
            if item is None:
                raise KeyError("СИ не найдено")
            return label_png(layout_in(session, _content(item, session), _settings(session)))
    return Response(content=_rendered(preview), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/{instrument_id}/passport.pdf")
def instrument_passport(
    instrument_id: UUID, _principal: Annotated[Principal, Depends(production_overview_principal)],
    now: Annotated[datetime, Depends(moscow_now)], uow_factory: UowFactory,
) -> Response:
    def build():
        with uow_factory().session_factory() as session:
            item = SqlAlchemyInstrumentRepository(session).get(instrument_id)
            if item is None:
                raise KeyError("СИ не найдено")
            return passport_pdf(item, RfRepository(session).get(item.id), _characterization(session, item.id),
                                generated=now, card_url=instrument_card_url(item.inventory_number),
                                registry=SqlAlchemyInstrumentRepository(session).list())
    return Response(content=_rendered(build), media_type="application/pdf",
                    headers={"Cache-Control": "no-store", "Content-Disposition": 'inline; filename="instrument-passport.pdf"'})
