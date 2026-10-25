"""Замеры КСВН коаксиальных сборок и паспортные данные изделия по HTTP (ОТК, ВАЦ).

Норму КСВН сервер берёт из ЭТК, на ревизии которой запущено задание (ключ `coaxVswrNorm`), — клиент норму не передаёт.
ЭТК, выпущенная до нормы (ключа нет), но с коаксиальной сборкой в `build_config.coax` (разъёмы на обоих концах), получает
норму «на лету» — теми же `enrich_coax_vswr_norm` / `vswr_norm` по текущим справочникам (`source: "computed"`, основание
с датой), без пересборки ЭТК. Не коаксиальная сборка — «норма не задана» с причиной, вердикт ставит контролёр.
Норма заказчика (технолог задаёт её в генераторе, владелец 23.10, `source: "customer"`): закреплённая ревизия
остаётся правилом, но если в ТЕКУЩЕЙ выданной ревизии ЭТК норма заказчика есть и отличается от закреплённой (или в
закреплённой её нет), новые замеры судятся по текущей — последнее требование технолога действует на ещё не измеренные
изделия, а каждый сохранённый замер хранит свой снимок нормы. Основание это говорит: «норма заказчика из ревизии N ЭТК».
Замер пишет раздел «ОТК» (правка), читает «Производство» (просмотр) — как остальной ОТК в production/api.py.
Паспорт изделия — JSON из разделов: сведения об изделии и ЭТК, норма с основанием и список проверок ОТК (`checks`);
КСВН — первая проверка (`kind: "vswr"`), другие виды проверок добавляются в тот же список.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Mapping
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.modules.production.api import production_uow_factory, techcards_uow_factory
from app.modules.production.domain import ProductionValidationError
from app.modules.production.vswr import (
    VERDICT_NAMES,
    VSWR_FILE_LIMIT,
    RecordVswrCommand,
    VswrMeasurement,
    VswrVerdict,
    list_vswr_for_serial,
    list_vswr_for_task,
    record_vswr,
    vswr_file,
)
from app.modules.production.autoplan_api import references_uow_factory
from app.modules.production.domain import ProductionStatus
from app.modules.references.coax_vswr import (
    SOURCE_CUSTOMER,
    CustomerNormError,
    cable_standard,
    components_of,
    customer_norm,
    customer_of,
    with_customer_norm,
)
from app.modules.techcards.etk_builder import vswr_control, vswr_in_sample
from app.modules.techcards.etk_read import issued_card_for_item, pinned_card
from app.modules.techcards.project_cards import TechcardStatus
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, production_overview_principal, production_quality_principal
from app.shared.stored_files import file_response
from app.workflows.techcard_build import enrich_coax_vswr_norm, load_coax_refs

router = APIRouter(prefix="/api/production/vswr", tags=["production"])

NORM_KEY = "coaxVswrNorm"
OLD_ETK_REASON = "ЭТК выпущена до нормы КСВН"
NO_ETK_REASON = "ЭТК задания не найдена — норма КСВН неизвестна"
NOT_COAX_REASON = "не коаксиальная сборка"
NO_REFS_REASON = "справочники коаксиальных разъёмов недоступны — норма КСВН не рассчитана"
COMPUTED = "computed"


class RecordVswrPayload(BaseModel):
    # Нормы в теле нет и быть не может: extra="forbid" отвечает 422, если клиент её прислал.
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    position: int = Field(ge=1)
    instrument_id: UUID
    file_name: str = Field(min_length=1, max_length=255)
    file_base64: str = Field(min_length=1)
    band_from_mhz: Decimal | None = None
    band_to_mhz: Decimal | None = None
    verdict: VswrVerdict | None = None  # ручной — только когда нормы в ЭТК нет
    reason: str = Field(default="", max_length=1000)


# --- Норма и ЭТК задания -----------------------------------------------------------------------------------------------


def undefined_norm(reason: str) -> dict[str, Any]:
    return {"status": "undefined", "reason": reason, "vswrMax": None}


def coax_config(document: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """`build_config.coax` of the ETK when it is an assembly with a connector on both ends (the only kind that has a
    norm); anything else — None."""
    config = document.get("build_config") if isinstance(document, Mapping) else None
    coax = config.get("coax") if isinstance(config, Mapping) else None
    if not isinstance(coax, Mapping):
        return None
    for side in ("sideA", "sideB"):
        end = coax.get(side)
        if not isinstance(end, Mapping) or not str(end.get("article") or "").strip():
            return None
    return coax


class CoaxRefs:
    """The БД.КОАКС rows, cable cards and БД.ПРОВ rows, read at most once per request and only when an old ETK needs them."""

    def __init__(self, factory: Callable[[], Any] | None) -> None:
        self._factory = factory
        self._loaded: tuple[Any, Any, Any] | None = None

    def get(self) -> tuple[Any, Any, Any] | None:
        if self._loaded is None and self._factory is not None:
            self._loaded = load_coax_refs(self._factory)
        return self._loaded


def computed_norm(document: Mapping[str, Any] | None, refs: CoaxRefs, now: datetime) -> dict[str, Any]:
    """The norm of an ETK issued before `coaxVswrNorm`, from its coax config and the references of today."""
    coax = coax_config(document)
    if coax is None:
        return undefined_norm(NOT_COAX_REASON)
    loaded = refs.get()
    if loaded is None:
        return undefined_norm(NO_REFS_REASON)
    rows, cards, wires = loaded
    norm = enrich_coax_vswr_norm({"coax": coax}, rows, cards, wires)["coax"].get("vswrNorm")
    if not isinstance(norm, Mapping):
        return undefined_norm(NOT_COAX_REASON)
    computed = f"рассчитана по справочникам на {now:%d.%m.%Y} — {OLD_ETK_REASON}"
    if norm.get("source") == SOURCE_CUSTOMER:
        # Норма заказчика из build_config — как задал технолог; «на лету» рассчитана только справочная по комплектующим.
        return {**norm, "components": {**components_of(norm), "basis": computed}}
    return {**norm, "source": COMPUTED, "basis": computed}


def etk_norm(document: Mapping[str, Any] | None, refs: CoaxRefs | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Норма КСВН из документа ЭТК; ключа нет (ЭТК старее нормы) — расчёт по её коаксиальной сборке и текущим
    справочникам, а без сборки — «не задана» с причиной."""
    norm = document.get(NORM_KEY) if isinstance(document, Mapping) else None
    if isinstance(norm, Mapping):
        return dict(norm)
    return computed_norm(document, refs or CoaxRefs(None), now or moscow_now())


def document_customer(document: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Норма заказчика, которую несёт документ ЭТК: из `coaxVswrNorm`, а ЭТК без него — из `build_config.coax`."""
    if not isinstance(document, Mapping):
        return None
    norm = document.get(NORM_KEY)
    if isinstance(norm, Mapping):
        return customer_of(norm)
    coax = coax_config(document)
    try:
        return customer_norm(coax.get("vswrCustomer")) if coax is not None else None
    except CustomerNormError:
        return None


def task_norm(
    document: Mapping[str, Any] | None, current: Mapping[str, Any] | None, current_revision: int | None,
    refs: CoaxRefs, now: datetime,
) -> dict[str, Any]:
    """Норма для новых замеров задания: закреплённой ревизии ЭТК (`etk_norm`), но норма заказчика ТЕКУЩЕЙ выданной
    ревизии (`current`; None — задание и так на текущей) перекрывает закреплённую, когда её там нет или она другая.
    Справочная норма по комплектующим и факты сборки — из закреплённой ревизии."""
    norm = etk_norm(document, refs, now)
    wanted = document_customer(current) if current is not None else None
    if wanted is None or wanted == customer_of(norm):
        return norm
    overridden = with_customer_norm(norm, wanted)
    overridden["basis"] = f"{wanted['basis']}; норма заказчика из ревизии {current_revision} ЭТК"
    overridden["customerRevision"] = current_revision
    return overridden


def etk_control(document: Mapping[str, Any] | None, norm: Mapping[str, Any]) -> dict[str, Any]:
    """«Контроль КСВН» задания (генератор, владелец 22.10): из нормы ЭТК (`control`), иначе из её build_config; ЭТК до
    настройки — каждое изделие, как было."""
    config = document.get("build_config") if isinstance(document, Mapping) else None
    coax = config.get("coax") if isinstance(config, Mapping) else None
    return (vswr_control(norm.get("control")) or vswr_control(coax.get("vswrControl") if isinstance(coax, Mapping) else None)
            or {"mode": "all", "every": None})


def _current_document(card, shown) -> dict[str, Any] | None:
    """Документ текущей выданной ревизии, когда задание закреплено на другой; задание на текущей — None."""
    if shown is card or card.status is not TechcardStatus.ISSUED:
        return None
    return dict(card.document or {})


def _task_etk(
    techcards_factory: Callable[[], Any] | None, task,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """(документ закреплённой ревизии ЭТК задания, ссылка на неё, документ текущей выданной ревизии — когда она другая).
    Нет ЭТК — пустой документ, пустая ссылка и None."""
    empty = {"techcard_id": None, "item_index": "", "product_name": "", "revision": task.techcard_revision,
             "current_revision": None, "fallback": None}
    if techcards_factory is None:
        return {}, empty, None
    with techcards_factory() as techcards:
        card = techcards.techcards.get(task.techcard_id) if task.techcard_id else None
        if card is None:
            card = issued_card_for_item(techcards, task.project, task.item)
        if card is None:
            return {}, empty, None
        shown, info = pinned_card(techcards, card, task.techcard_revision)
    return dict(shown.document or {}), {
        "techcard_id": str(card.id), "item_index": card.item_index, "product_name": card.product_name,
        "revision": info["pinned"] if info["pinned"] is not None else card.revision,
        "current_revision": info["current"], "fallback": info["fallback"],
    }, _current_document(card, shown)


# --- Ответы ------------------------------------------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    return float(value) if value is not None else None


def _measurement_result(item: VswrMeasurement, *, trace: bool = False) -> dict[str, Any]:
    result = {
        "id": str(item.id), "serial_id": str(item.serial_id), "serial_number": item.serial_number,
        "attempt": item.attempt, "instrument_id": str(item.instrument_id),
        "band_mhz": [_num(item.band_from_mhz), _num(item.band_to_mhz)],
        "max_vswr": _num(item.max_vswr), "max_vswr_mhz": _num(item.max_vswr_mhz),
        "norm": item.norm, "norm_vswr_max": _num(item.norm_vswr_max),
        "verdict": item.verdict.value, "verdict_name": VERDICT_NAMES[item.verdict],
        "verdict_manual": item.verdict_manual, "verdict_reason": item.verdict_reason,
        "inspector": {"id": str(item.inspector_id), "name": item.inspector_name},
        "measured_at": item.measured_at.isoformat(),
        "file_name": item.file_name, "points": item.points, "file_meta": item.file_meta,
    }
    if trace:
        result["trace"] = [{"mhz": mhz, "vswr": vswr} for mhz, vswr in item.trace]
    return result


def _instrument_result(instrument) -> dict[str, Any] | None:
    if instrument is None:
        return None
    return {
        "id": str(instrument.id), "name": instrument.name, "instrument_type": instrument.instrument_type,
        "inventory_number": instrument.inventory_number,
        "verification_number": instrument.verification_number,
        "verified_at": instrument.verified_at.isoformat(),
        "verification_due": instrument.verification_due.isoformat(),
    }


def _norm_result(norm: Mapping[str, Any]) -> dict[str, Any]:
    band = norm.get("bandMhz")
    if isinstance(band, Mapping):
        band = [band.get("from"), band.get("to")]
    status = str(norm.get("status") or "ok").strip().lower()
    return {
        "status": "ok" if status == "ok" else "undefined",
        "reason": str(norm.get("reason") or ""),
        "vswr_max": _num(norm.get("vswrMax")) if status == "ok" else None,
        "band_mhz": list(band) if isinstance(band, (list, tuple)) else None,
        "s21_db": norm.get("s21Db"), "length_m": norm.get("lengthM"),
        "source": norm.get("source"), "basis": norm.get("basis"),
        "components": _components_result(norm) if norm.get("source") == SOURCE_CUSTOMER else None,
    }


def _components_result(norm: Mapping[str, Any]) -> dict[str, Any]:
    """Справочная норма по комплектующим при норме заказчика."""
    components = components_of(norm)
    band = components.get("bandMhz")
    status = str(components.get("status") or "").strip().lower()
    return {
        "status": "ok" if status == "ok" else "undefined", "reason": str(components.get("reason") or ""),
        "vswr_max": _num(components.get("vswrMax")) if status == "ok" else None,
        "band_mhz": list(band) if isinstance(band, (list, tuple)) else None,
    }


def _ends_result(norm: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{
        "side": end.get("side"), "article": end.get("article"), "vswr_text": end.get("vswrText"),
        "vswr": end.get("vswr"), "max_ghz": end.get("maxGhz"),
    } for end in norm.get("ends") or () if isinstance(end, Mapping)]


# --- Маршруты ----------------------------------------------------------------------------------------------------------


def _guard(operation):
    try:
        return operation()
    except ProductionValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _decode(payload: RecordVswrPayload) -> bytes:
    try:
        content = base64.b64decode(payload.file_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=422, detail="Файл замера передан не в base64") from error
    if len(content) > VSWR_FILE_LIMIT:
        raise HTTPException(status_code=422, detail="Файл замера больше 2 МБ")
    return content


def _task(uow_factory, task_id: UUID):
    with uow_factory() as uow:
        task = uow.production.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    return task


def _serial_measurements(uow_factory, task, position: int) -> tuple[Any, list[VswrMeasurement]]:
    """Изделие задания по позиции и все его замеры (первая попытка первой). Изделия ещё нет в реестре — замеров нет."""
    if not 1 <= position <= task.quantity:
        raise HTTPException(status_code=404, detail=f"В задании {task.number} изделия с 1 по {task.quantity}")
    with uow_factory() as uow:
        serial = uow.serials.by_position(task.id, position)
    if serial is None:
        return None, []
    return serial, list(list_vswr_for_serial(serial.id, uow_factory()))


def _sample_result(uow_factory, task, control: Mapping[str, Any]) -> dict[str, Any]:
    """Выборка партии задания (позиции 1, 1+N, …) с последним вердиктом каждого изделия; без замера — verdict None."""
    latest: dict[UUID, VswrMeasurement] = {}
    for item in list_vswr_for_task(task.id, uow_factory()):
        latest[item.serial_id] = item
    units = []
    with uow_factory() as uow:
        for position in range(1, task.quantity + 1):
            if not vswr_in_sample(control, position):
                continue
            serial = uow.serials.by_position(task.id, position)
            item = latest.get(serial.id) if serial is not None else None
            units.append({
                "position": position, "serial_number": serial.serial_number if serial else None,
                "verdict": item.verdict.value if item else None,
                "verdict_name": VERDICT_NAMES[item.verdict] if item else None,
                "max_vswr": _num(item.max_vswr) if item else None,
            })
    return {"every": control["every"], "units": units}


@router.post("")
def upload_measurement(
    payload: RecordVswrPayload,
    principal: Annotated[Principal, Depends(production_quality_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], Any], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], Any] | None, Depends(techcards_uow_factory)],
    refs_factory: Annotated[Callable[[], Any] | None, Depends(references_uow_factory)],
) -> dict:
    """Замер из файла ВАЦ. Норма — из закреплённой ревизии ЭТК задания (старая ЭТК — расчёт по справочникам; норма
    заказчика текущей ревизии перекрывает — `task_norm`); вердикт по ней сам, а без нормы — вручную."""
    content = _decode(payload)
    task = _task(uow_factory, payload.task_id)
    document, etk, current = _task_etk(techcards_factory, task)
    norm = (task_norm(document, current, etk["current_revision"], CoaxRefs(refs_factory), now) if etk["techcard_id"]
            else undefined_norm(NO_ETK_REASON))
    measurement = _guard(lambda: record_vswr(RecordVswrCommand(
        task_id=task.id, position=payload.position, instrument_id=payload.instrument_id,
        file_name=payload.file_name, content=content, norm=norm, actor=principal.actor, now=now,
        band_from_mhz=payload.band_from_mhz, band_to_mhz=payload.band_to_mhz,
        verdict=payload.verdict, reason=payload.reason,
    ), uow_factory()))
    return {"measurement": _measurement_result(measurement, trace=True), "position": payload.position}


@router.get("/tasks")
def coax_tasks(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], Any], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], Any] | None, Depends(techcards_uow_factory)],
    refs_factory: Annotated[Callable[[], Any] | None, Depends(references_uow_factory)],
) -> dict:
    """Список для выбора задания в «Замерах КСВН»: только задания, чья закреплённая ЭТК — коаксиальная сборка. Каждая
    ЭТК читается один раз, справочники — один раз и только если нужна норма старой ЭТК; файлов замеров не грузим."""
    with uow_factory() as uow:
        tasks = [task for task in uow.production.list(limit=1000) if task.status is not ProductionStatus.CANCELLED]
    documents = _etk_documents(techcards_factory, tasks)
    refs = CoaxRefs(refs_factory)
    norms: dict[tuple[Any, Any, Any, Any], dict[str, Any]] = {}
    rows = []
    for task in tasks:
        key = (task.techcard_id, task.techcard_revision, task.project, task.item)
        found = documents.get(key)
        if found is None or coax_config(found[0]) is None:
            continue
        document, current, current_revision = found
        if key not in norms:
            norms[key] = task_norm(document, current, current_revision, refs, now)
        norm = norms[key]
        shown = _norm_result(norm)
        control = etk_control(document, norm)
        required = [position for position in range(1, task.quantity + 1) if vswr_in_sample(control, position)]
        rows.append({
            "id": str(task.id), "number": task.number, "name": task.name, "item": task.item,
            "quantity": task.quantity, "status": task.status.value,
            "norm": {"status": shown["status"], "vswr_max": shown["vswr_max"], "source": shown["source"]},
            "control": control,
            "required": {"total": len(required), "measured": _measured_count(uow_factory, task, required)},
        })
    return {"tasks": rows}


EtkDocuments = tuple[dict[str, Any], dict[str, Any] | None, int | None]


def _etk_documents(techcards_factory: Callable[[], Any] | None, tasks) -> dict[tuple[Any, Any, Any, Any], EtkDocuments]:
    """(документ закреплённой ревизии ЭТК, документ текущей выданной — когда она другая, номер текущей) каждого задания
    (ключ — как в `coax_tasks`); одна транзакция, каждая (карточка, ревизия) читается один раз."""
    if techcards_factory is None:
        return {}
    found: dict[tuple[Any, Any, Any, Any], EtkDocuments] = {}
    by_card: dict[tuple[Any, Any], EtkDocuments] = {}
    with techcards_factory() as techcards:
        for task in tasks:
            key = (task.techcard_id, task.techcard_revision, task.project, task.item)
            if key in found:
                continue
            card = techcards.techcards.get(task.techcard_id) if task.techcard_id else None
            if card is None:
                card = issued_card_for_item(techcards, task.project, task.item)
            if card is None:
                continue
            card_key = (card.id, task.techcard_revision)
            if card_key not in by_card:
                shown, _info = pinned_card(techcards, card, task.techcard_revision)
                by_card[card_key] = (dict(shown.document or {}), _current_document(card, shown), card.revision)
            found[key] = by_card[card_key]
    return found


def _measured_count(uow_factory, task, required: list[int]) -> int:
    """Сколько из требуемых изделий задания уже имеют замер; заданию без замеров изделия не перебираем."""
    measured_ids = {item.serial_id for item in list_vswr_for_task(task.id, uow_factory())}
    if not measured_ids:
        return 0
    count = 0
    with uow_factory() as uow:
        for position in required:
            serial = uow.serials.by_position(task.id, position)
            if serial is not None and serial.id in measured_ids:
                count += 1
    return count


@router.get("/tasks/{task_id}")
def task_measurements(
    task_id: UUID,
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], Any], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], Any] | None, Depends(techcards_uow_factory)],
    refs_factory: Annotated[Callable[[], Any] | None, Depends(references_uow_factory)],
) -> dict:
    """По изделиям задания: действующий (последний) замер и число попыток; изделия без замера — attempts 0."""
    task = _task(uow_factory, task_id)
    rows = list(list_vswr_for_task(task.id, uow_factory()))
    latest: dict[UUID, VswrMeasurement] = {}
    attempts: dict[UUID, int] = {}
    for item in rows:  # порядок: позиция, попытка — последняя попытка перекрывает
        latest[item.serial_id] = item
        attempts[item.serial_id] = attempts.get(item.serial_id, 0) + 1
    serials = []
    with uow_factory() as uow:
        for position in range(1, task.quantity + 1):
            serial = uow.serials.by_position(task.id, position)
            if serial is not None:
                serials.append((position, serial))
    document, etk, current = _task_etk(techcards_factory, task)
    norm = (task_norm(document, current, etk["current_revision"], CoaxRefs(refs_factory), now) if etk["techcard_id"]
            else undefined_norm(NO_ETK_REASON))
    control = etk_control(document, norm)
    # Требуемые замеры — по всей партии задания (выборка 1 из N: первое и каждое N-е), замерено — у кого есть замер.
    measured = {position for position, serial in serials if serial.id in latest}
    required = [position for position in range(1, task.quantity + 1) if vswr_in_sample(control, position)]
    return {
        "task": {"id": str(task.id), "number": task.number, "name": task.name, "quantity": task.quantity},
        "norm": _norm_result(norm),
        "control": control,
        "required": {"total": len(required), "measured": len(measured.intersection(required))},
        "serials": [{
            "position": position, "serial_id": str(serial.id), "serial_number": serial.serial_number,
            "status": serial.status.value, "attempts": attempts.get(serial.id, 0),
            "in_sample": vswr_in_sample(control, position),
            "latest": _measurement_result(latest[serial.id]) if serial.id in latest else None,
        } for position, serial in serials],
    }


@router.get("/tasks/{task_id}/serials/{position}")
def serial_measurements(
    task_id: UUID, position: int,
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], Any], Depends(production_uow_factory)],
) -> dict:
    """Все попытки замера одного изделия, первая первой (без трассы — она в паспорте)."""
    task = _task(uow_factory, task_id)
    serial, rows = _serial_measurements(uow_factory, task, position)
    return {
        "task_id": str(task.id), "position": position,
        "serial_number": serial.serial_number if serial else None,
        "measurements": [_measurement_result(item) for item in rows],
    }


@router.get("/measurements/{measurement_id}/file")
def measurement_file(
    measurement_id: UUID,
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], Any], Depends(production_uow_factory)],
) -> Response:
    """Выгрузка ВАЦ как приложена, с исходным именем."""
    name, content = _guard(lambda: vswr_file(measurement_id, uow_factory()))
    return file_response(content, name, download=True)


@router.get("/tasks/{task_id}/serials/{position}/passport")
def serial_passport(
    task_id: UUID, position: int,
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], Any], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], Any] | None, Depends(techcards_uow_factory)],
    now: Annotated[datetime, Depends(moscow_now)],
    refs_factory: Annotated[Callable[[], Any] | None, Depends(references_uow_factory)],
) -> dict:
    """Паспорт изделия: изделие и ЭТК, кабель и концы, норма с основанием, проверки ОТК (сейчас — КСВН по ВАЦ)."""
    task = _task(uow_factory, task_id)
    serial, rows = _serial_measurements(uow_factory, task, position)
    document, etk, current = _task_etk(techcards_factory, task)
    refs = CoaxRefs(refs_factory)
    etk_side = (task_norm(document, current, etk["current_revision"], refs, now) if etk["techcard_id"]
                else undefined_norm(NO_ETK_REASON))
    latest = rows[-1] if rows else None
    # Норма паспорта — та, по которой судили последний замер (снимок); замера ещё нет — норма из ЭТК задания.
    norm = latest.norm if latest is not None else etk_side
    cable = etk_side.get("cable") if isinstance(etk_side.get("cable"), Mapping) else {}
    # «Стандарт» кабеля из «Коаксиальных кабелей» (владелец 25.10: «RG-316 (MIL-DTL-17/113)»); нет — None, печать «—»
    loaded = refs.get() if cable.get("name") else None
    standard = cable_standard(loaded[2], str(cable.get("name"))) if loaded is not None else ""
    checks: list[dict[str, Any]] = []
    if latest is not None:
        with uow_factory() as uow:
            instrument = _instrument_result(uow.instruments.get(latest.instrument_id))
        checks.append({
            "kind": "vswr", "title": "КСВН по ВАЦ", "verdict": latest.verdict.value,
            "verdict_name": VERDICT_NAMES[latest.verdict],
            "measurement": _measurement_result(latest, trace=True),
            "instrument": instrument, "attempts": len(rows),
            "inspector": {"id": str(latest.inspector_id), "name": latest.inspector_name},
            "inspected_at": latest.measured_at.isoformat(),
        })
    control = etk_control(document, etk_side)
    sample = None
    if latest is None and control["mode"] == "sample" and not vswr_in_sample(control, position):
        sample = _sample_result(uow_factory, task, control)
    return {
        "product": {
            "task_id": str(task.id), "task_number": task.number, "project": task.project, "item": task.item,
            "name": task.name or etk["product_name"], "serial_number": serial.serial_number if serial else None,
            "position": position, "status": serial.status.value if serial else None,
            "cable": {"name": cable.get("name"), "loss_db_m": cable.get("lossDbM"), "standard": standard or None},
            "length_m": etk_side.get("lengthM"), "ends": _ends_result(etk_side),
        },
        "etk": etk,
        "norm": {
            **_norm_result(norm),
            "basis": ((f"ЭТК {etk['item_index']}, ревизия {etk['revision']}" if etk["techcard_id"]
                       else "ЭТК задания не найдена")
                      + (f"; {norm['basis']}" if isinstance(norm, Mapping) and norm.get("basis") else "")),
            "from_snapshot": latest is not None,
        },
        "checks": checks,
        "control": control,
        # Изделие вне выборки «1 из N» без своего замера: паспорт ссылается на результаты выборки партии.
        "vswr_sample": sample,
        "verdict": latest.verdict.value if latest else None,
        "inspector": checks[0]["inspector"] if checks else None,
        "inspected_at": checks[0]["inspected_at"] if checks else None,
    }
