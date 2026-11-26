"""Production-owned catalog facade for workflow-assembled reference ports.

The composition root supplies an entered production UoW sharing the workflow's
session. Methods never open, commit or close it. Reference modules receive a port,
not this module or its ORM. Authorization belongs to the calling workflow.
"""

from uuid import UUID

from .quality import (
    QualityUnitOfWork, RegisterInstrumentCommand, UpdateInstrumentCommand,
    register_instrument_in_transaction, update_instrument_in_transaction,
)
from .traceability import MeasurementInstrument


class InstrumentCatalog:
    def __init__(self, uow: QualityUnitOfWork):
        self.uow = uow

    def list(self) -> list[MeasurementInstrument]:
        """All canonical rows, including inactive/pending items and accessories."""
        return list(self.uow.instruments.list())

    def get(self, instrument_id: UUID) -> MeasurementInstrument | None:
        return self.uow.instruments.get(instrument_id)

    def register(self, command: RegisterInstrumentCommand) -> MeasurementInstrument:
        return register_instrument_in_transaction(command, self.uow)

    def update(self, command: UpdateInstrumentCommand) -> MeasurementInstrument:
        return update_instrument_in_transaction(command, self.uow)
