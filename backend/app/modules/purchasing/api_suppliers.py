"""Справочник поставщиков (stage option references.suppliers)."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/purchasing/suppliers", tags=["purchasing"])
