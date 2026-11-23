"""Resolve the quality registry identity before the warehouse PC queue is used."""

from uuid import UUID
from sqlalchemy import update

from app.modules.production.infrastructure import SqlAlchemyInstrumentRepository
from app.modules.production.domain import ProductionValidationError
from app.modules.production.traceability import InstrumentKind, InstrumentStatus
from app.modules.production.vna_session_infrastructure import VnaSessionRepository
from app.modules.warehouse.scan_jobs import DONE, ScanJobClosed, ScanJobRow
from app.modules.warehouse.devices import VnaBindingRow, instrument_identity
from app.modules.warehouse.scan_agents import LIVE_FOR, ScanAgentRow
from app.modules.warehouse import vna_jobs
from app.shared.clock import as_utc


def capture_source(uow, instrument_id: UUID) -> str:
    return capture_instrument(uow, instrument_id)["source"]


def arinst_station(session, instrument_id, serial, now, *, machine_id=None, op="connect"):
    """Connect/close can reach a locked or expired identity; no mutating op bypasses capability checks."""
    identity = instrument_identity(session, instrument_id)
    if identity is None or identity.source != "arinst" or identity.serial != serial:
        raise ScanJobClosed("Подтверждённый UID Arinst не совпадает с сессией")
    if op not in ("connect", "close"):
        return vna_jobs.control_station(session, serial, now, machine_id=machine_id, source="arinst", ops={op})
    binding = session.get(VnaBindingRow, serial)
    if binding is None or (machine_id is not None and binding.machine_id != machine_id):
        raise ScanJobClosed("ВАЦ не закреплён за этим ПК")
    owner = binding.machine_id
    locked = session.execute(update(VnaBindingRow).where(
        VnaBindingRow.serial == serial, VnaBindingRow.machine_id == owner).values(machine_id=owner))
    if locked.rowcount != 1:
        raise ScanJobClosed("Привязка ВАЦ изменилась")
    agent = session.get(ScanAgentRow, owner)
    seen = agent.last_seen if agent else None
    if seen is not None and seen.tzinfo is None:
        seen = seen.replace(tzinfo=now.tzinfo)
    if agent is None or agent.legacy or as_utc(seen) < as_utc(now) - LIVE_FOR:
        raise ScanJobClosed("Агент на закреплённом ПК не отвечает")
    return owner


def assert_vna_transfer_allowed(session, instrument_id: UUID, serial: str) -> None:
    """Called under the binding lock: no calibration or queued control may survive a move to another PC."""
    if VnaSessionRepository(session).active(instrument_id, serial):
        raise ScanJobClosed("Сначала завершите сеанс ВАЦ на закреплённом ПК, затем перенесите прибор")


def capture_instrument(uow, instrument_id: UUID, *, direct: bool = False) -> dict[str, str]:
    with uow:
        item = SqlAlchemyInstrumentRepository(uow.session).get(instrument_id)
        if item is None:
            raise ValueError("Средство измерений не найдено")
        if item.kind is not InstrumentKind.MEASURING or item.status is not InstrumentStatus.ACTIVE:
            raise ValueError("Выберите действующее средство измерений")
        source = {"S2VNA": "s2vna", "Arinst VL": "arinst"}.get(item.vna_app)
        if source is None:
            raise ValueError("У выбранного СИ не указана программа ВАЦ")
        serial = (item.serial_number or "").strip()
        if direct and source == "arinst":
            identity = instrument_identity(uow.session, instrument_id)
            if identity is None or identity.source != source:
                raise ValueError("Подтвердите аппаратный UID Arinst в карточке СИ")
            return dict(source=source, serial=identity.serial, printed_serial=serial, number=item.inventory_number,
                        hardware_uid=identity.hardware_uid, firmware=identity.firmware)
        return {"source": source, "serial": serial, "number": item.inventory_number}


def capture_evidence(uow, job_id: UUID, instrument_id: UUID, actor_id: UUID, content: bytes) -> tuple[dict | None, bool]:
    """Only an owned completed capture can relax norm coverage during instrument testing."""
    with uow:
        job = uow.session.get(ScanJobRow, job_id)
        if (job is None or job.kind != "vna" or job.status != DONE
                or job.requested_by != str(actor_id) or job.instrument_id != instrument_id
                or job.capture_content != content):
            raise ProductionValidationError("Файл замера не соответствует завершённому заданию ВАЦ этого оператора и СИ")
        return job.capture_settings, job.source == "s2vna"
