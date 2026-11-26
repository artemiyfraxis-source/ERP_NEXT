"""Bind the shared allocator to an already entered receiving/warehouse/shipping UoW.

Pass the returned port into the application command. Never open a warehouse UoW here:
the number, acceptance, stock, audit and outbox must commit or roll back together.
"""

from sqlalchemy.orm import Session

from app.modules.warehouse.grn import (  # Re-export the contract through the workflow boundary for B/C/D.
    GrnAllocationPort, GrnCapacityExceeded, GrnCategory, GrnConflict, GrnContext, GrnIdentity,
    GrnRequest, fingerprint_body,
)
from app.modules.warehouse.grn_registry import SqlAlchemyGrnAllocator


def grn_allocator(session: Session) -> GrnAllocationPort:
    return SqlAlchemyGrnAllocator(session)
