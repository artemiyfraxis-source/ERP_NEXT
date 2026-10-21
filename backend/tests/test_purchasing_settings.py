"""«Настройки закупки»: defaults without rows, validated writes, bad stored values fall back to defaults."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.purchasing import settings
from app.modules.purchasing.infrastructure import PurchasingSettingRow
from app.shared.persistence import Base

NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[PurchasingSettingRow.__table__])
    with Session(engine) as s:
        yield s


def test_defaults_without_rows(session):
    values = settings.load(session)
    assert values["gate_min_quotes"] == 3 and values["gate_require_non_key"] is True
    assert values["default_vat_rate"] == Decimal("22")


def test_save_validates_and_reads_back(session):
    values = settings.save(session, {"gate_min_quotes": 2, "default_vat_rate": "20"}, None, NOW)
    assert values["gate_min_quotes"] == 2 and values["default_vat_rate"] == Decimal("20")
    with pytest.raises(settings.InvalidSetting):
        settings.save(session, {"gate_min_quotes": 0}, None, NOW)
    with pytest.raises(settings.InvalidSetting):
        settings.save(session, {"gate_min_quotes": "2.5"}, None, NOW)
    with pytest.raises(settings.InvalidSetting):
        settings.save(session, {"nope": 1}, None, NOW)


def test_a_broken_stored_value_falls_back_to_the_default(session):
    session.add(PurchasingSettingRow(key="quote_wait_days", value="много", updated_at=NOW))
    session.flush()
    assert settings.load(session)["quote_wait_days"] == 3
