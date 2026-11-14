"""Resolve the quality registry identity before the warehouse PC queue is used."""

from uuid import UUID

from app.modules.production.infrastructure import SqlAlchemyInstrumentRepository
from app.modules.production.domain import ProductionValidationError
from app.modules.production.traceability import InstrumentKind, InstrumentStatus
from app.modules.warehouse.scan_jobs import DONE, ScanJobRow


def capture_source(uow, instrument_id: UUID) -> str:
    return capture_instrument(uow, instrument_id)["source"]


def capture_instrument(uow, instrument_id: UUID) -> dict[str, str]:
    with uow:
        item = SqlAlchemyInstrumentRepository(uow.session).get(instrument_id)
        if item is None:
            raise ValueError("Средство измерений не найдено")
        if item.kind is not InstrumentKind.MEASURING or item.status is not InstrumentStatus.ACTIVE:
            raise ValueError("Выберите действующее средство измерений")
        source = {"S2VNA": "s2vna", "Arinst VL": "arinst"}.get(item.vna_app)
        if source is None:
            raise ValueError("У выбранного СИ не указана программа ВАЦ")
        return {"source": source, "serial": (item.serial_number or "").strip(), "number": item.inventory_number}


def capture_evidence(uow, job_id: UUID, instrument_id: UUID, actor_id: UUID, content: bytes) -> tuple[dict | None, bool]:
    """Only an owned completed capture can relax norm coverage during instrument testing."""
    with uow:
        job = uow.session.get(ScanJobRow, job_id)
        if (job is None or job.kind != "vna" or job.status != DONE
                or job.requested_by != str(actor_id) or job.instrument_id != instrument_id
                or job.capture_content != content):
            raise ProductionValidationError("Файл замера не соответствует завершённому заданию ВАЦ этого оператора и СИ")
        return job.capture_settings, job.source == "s2vna"
