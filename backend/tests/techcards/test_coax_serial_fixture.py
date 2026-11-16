"""The frontend FRX print test (frontend/src/coaxSerialPrint.test.mjs) reads a backend-built ETK from a fixture, because the
frontend CI job has no Python. This test keeps that fixture honest: the same config must still build the same numbered marks
and the same serial range."""
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.modules.production.domain import has_numbered_marks, mark_serial_range
from app.modules.techcards.etk_builder import build_etk
from tests.techcards.test_etk_coax_fakra import _fresh_cards

FIXTURE = Path(__file__).resolve().parents[3] / "frontend" / "src" / "__fixtures__" / "coaxSerialEtk.json"


def _marks(document: dict) -> list:
    return [(op.get("code"), (op.get("fields") or {}).get("marks")) for op in document["operations"]]


def test_frontend_frx_fixture_matches_backend_build():
    snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document = build_etk(snapshot["config"], _fresh_cards(snapshot["config"]))
    assert _marks(document) == _marks(snapshot["document"])
    assert has_numbered_marks(document) is snapshot["numbered"] is True
    task = SimpleNamespace(id=uuid4(), number="ПЗ-0001", item="708К.КБ1", quantity=15, created_at=datetime(2026, 11, 16))
    first, last = mark_serial_range(task, [task])
    assert {"first": first, "last": last, "task": task.number} == snapshot["serial"]
