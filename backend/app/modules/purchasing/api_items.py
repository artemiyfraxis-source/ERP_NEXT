"""Номенклатура (stage option references.items)."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/purchasing/items", tags=["purchasing"])
