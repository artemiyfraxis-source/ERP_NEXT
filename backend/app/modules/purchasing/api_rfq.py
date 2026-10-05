"""Запросы КП, КП, сравнение и решение (stage option procurement.rfq)."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/purchasing/rfqs", tags=["purchasing"])
