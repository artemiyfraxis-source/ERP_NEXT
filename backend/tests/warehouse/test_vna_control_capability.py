from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.modules.warehouse.devices import VnaControlRow, control_capability
from app.modules.warehouse.scan_jobs import ScanJobClosed


@pytest.mark.parametrize("utc", [True, False])
def test_capability_freshness_preserves_aware_offsets_and_supports_sqlite_naive(utc):
    now = datetime(2026, 11, 16, 10, tzinfo=timezone(timedelta(hours=3)))
    report = now.astimezone(timezone.utc) if utc else now.replace(tzinfo=None)
    control = SimpleNamespace(version=1, methods=["solt2"], reported_at=report)
    pc = SimpleNamespace(version="2.8.0", legacy=False)
    session = SimpleNamespace(get=lambda model, key: control if model is VnaControlRow else pc)
    assert control_capability(session, "pc", now) == ["solt2"]
    control.reported_at -= timedelta(minutes=4)
    with pytest.raises(ScanJobClosed):
        control_capability(session, "pc", now)
