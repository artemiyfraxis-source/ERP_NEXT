"""Explicit purchase intent from a saved source row; one transaction, no stock mutation."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.modules.requests.application import _procurement_requested_payload, directory_purposes
from app.modules.requests.composition import CompositionConflict
from app.modules.requests.domain import RequestDraft, ValidationError, create_request_batch, build_procurement_requested_snapshot
from app.modules.requests.infrastructure import CalcCompositionRow
from app.modules.procurement.application import _add_requested_line
from app.modules.procurement.infrastructure import ProcurementLineRow, SqlAlchemyProcurementRepository
from app.shared.events import AuditEvent, OutboxMessage
from app.shared.project_number import normalize_project_number
from app.shared.persistence import OutboxMessageRow
from app.workflows.procurement_requests_view import CompositionRefreshError


def _validate_source(row):
    if row["issues"] or row.get("unit_source") not in {"explicit", "derived_known"} or row["quantity"] is None:
        raise ValidationError("Исправьте количество, единицу и цену выбранной позиции в источнике ЗКП")
    for key, limit in (("name", 512), ("item_type", 255), ("unit", 32), ("link", 4096)):
        if len(row[key]) > limit:
            raise ValidationError("Позиция ЗКП превышает допустимую длину поля заявки")
    for key, places in (("quantity", 6), ("price", 2), ("price_with_vat", 2)):
        if row[key] is not None:
            value = Decimal(row[key])
            if not value.is_finite() or value < 0 or value >= Decimal("1e12") or value != value.quantize(Decimal(1).scaleb(-places)):
                raise ValidationError("Число в ЗКП превышает точность или размер поля заявки")
    quantity = Decimal(row["quantity"])
    if quantity <= 0:
        raise ValidationError("Количество позиции ЗКП должно быть больше нуля")
    if row["price_with_vat"] is not None and quantity * Decimal(row["price_with_vat"]) >= Decimal("1e18"):
        raise ValidationError("Сумма позиции ЗКП превышает допустимый размер")


def materialize_composition(state, *, payload, actor, now):
    try:
        for attempt in range(2):
            try:
                with state.uow_factory() as uow:
                    result = _materialize(uow, payload, actor, now, getattr(state, "project_registry", None))
                    uow.commit()
                    return result
            except (IntegrityError, StaleDataError) as error:
                if attempt == 1:
                    raise CompositionConflict("Конкурентное изменение: повторите команду") from error
    except CompositionConflict as error:
        raise CompositionRefreshError(str(error), 409) from error
    except ValidationError as error:
        raise CompositionRefreshError(str(error), 422) from error


def _materialize(uow, payload, actor, now, projects):
    project = normalize_project_number(payload.project)
    # Serialize all actors on this source. The version CAS below also protects SQLite tests.
    uow.session.scalar(select(CalcCompositionRow).where(CalcCompositionRow.project == project).with_for_update())
    snapshot = uow.compositions.get(project)
    if snapshot is None or snapshot.id != payload.snapshot_id or snapshot.content_hash != payload.content_hash:
        raise CompositionConflict("Источник ЗКП изменён; обновите состав")
    selected = set(payload.source_rows)
    if len(selected) != len(payload.source_rows):
        raise ValidationError("Исходная строка выбрана повторно")
    rows = [row for row in snapshot.rows if row["source_row"] in selected]
    if len(rows) != len(selected):
        raise CompositionConflict("Исходная строка ЗКП не найдена")
    for row in rows:
        if row.get("request_link_state") == "source_changed":
            raise CompositionConflict("Связь с заявкой требует проверки после изменения источника")
        if row["request_id"] and uow.requests.get(UUID(row["request_id"])) is None:
            raise CompositionConflict("Связанная заявка не найдена; требуется проверка")
        if not row["request_id"]:
            _validate_source(row)
    new_rows = [row for row in rows if not row["request_id"]]
    links = {row["source_row"]: row["request_id"] for row in rows}
    if new_rows:
        if snapshot.version != payload.expected_version:
            raise CompositionConflict("Состав ЗКП изменён; обновите данные")
        if not all((payload.purpose, payload.delivery_point, payload.procurement_deadline, payload.planned_date)):
            raise ValidationError("Укажите назначение, точку отгрузки, дедлайн и плановую дату поступления")
        drafts = [RequestDraft(
            purpose=payload.purpose, delivery_point=payload.delivery_point, name=row["name"],
            item_type=row["item_type"], quantity=Decimal(row["quantity"]), unit=row["unit"],
            quality_document=None, procurement_deadline=payload.procurement_deadline, planned_date=payload.planned_date,
            project_reference=project, design_article=row["name"], link=row["link"] or None,
            price=Decimal(row["price"]) if row["price"] is not None else None,
            price_with_vat=Decimal(row["price_with_vat"]) if row["price_with_vat"] is not None else None,
        ) for row in new_rows]
        requests = create_request_batch(
            drafts, actor=actor, now=now, existing_legacy_ids=uow.requests.all_legacy_ids(),
            existing_designations=uow.requests.all_designations(),
            creation_key=f"composition:{snapshot.id}:{snapshot.version}", creation_fingerprint=snapshot.content_hash,
            designation_base=project, directory_purposes=directory_purposes(projects),
        )
        for row, request in zip(new_rows, requests):
            request = request.queue_for_procurement()
            links[row["source_row"]] = str(request.id)
            uow.requests.add(request)
            for event_type in ("request.created", "request.sent_to_procurement"):
                uow.audit.add(AuditEvent(
                    id=uuid4(), aggregate_id=request.id, event_type=event_type, actor=actor, occurred_at=now,
                    details={"source": "composition", "snapshot_id": str(snapshot.id), "source_row": str(row["source_row"])},
                ))
            event = build_procurement_requested_snapshot(request, today=now.date())
            uow.outbox.add(OutboxMessage(
                id=uuid4(), topic="procurement.requested", idempotency_key=event.idempotency_key,
                created_at=now, payload=_procurement_requested_payload(event),
            ))
        snapshot = replace(snapshot, version=snapshot.version + 1, rows=[
            {**row, "request_id": links[row["source_row"]], "request_link_state": "recorded"}
            if row["source_row"] in links else row for row in snapshot.rows
        ])
        uow.compositions.save(snapshot, payload.expected_version)
    # Consume the very same durable event on this Session. Later outbox dispatch resolves its unique key.
    procurement = SqlAlchemyProcurementRepository(uow.session)
    bridge = SimpleNamespace(procurement=procurement, audit=uow.audit, outbox=uow.outbox)
    output = []
    for row in rows:
        request_id = UUID(links[row["source_row"]])
        ids = list(uow.session.scalars(select(ProcurementLineRow.id).where(
            ProcurementLineRow.source_request_id == request_id,
        ).order_by(ProcurementLineRow.id)))
        if not ids:
            request = uow.requests.get(request_id)
            event = build_procurement_requested_snapshot(request, today=now.date())
            uow.session.scalar(select(OutboxMessageRow).where(
                OutboxMessageRow.idempotency_key == event.idempotency_key,
            ).with_for_update())
            message = uow.outbox.by_idempotency_key(event.idempotency_key)
            if message is None:
                raise CompositionConflict("Не найдено событие передачи заявки в закупку")
            line = procurement.by_source_idempotency_key(message.idempotency_key)
            if line is None:
                line = _add_requested_line(message, bridge, None)
            ids = [line.id]
        output.append({"source_row": row["source_row"], "request_id": str(request_id),
                       "procurement_ids": [str(value) for value in ids]})
    return {"project": project, "snapshot_id": str(snapshot.id), "version": snapshot.version,
            "content_hash": snapshot.content_hash, "rows": output}
