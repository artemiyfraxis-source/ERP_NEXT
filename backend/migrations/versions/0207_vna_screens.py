"""VNA bench screens move from code to the database (S5b, owner 19.11: «мне надо, чтобы я мог настраивать экраны»).

- production_vna_screens: one named screen (name UNIQUE) for a purpose (adapter / assembly / one_port / load / flex /
  engineer, CHECK); `split` 1..16 (S2VNA layout); `traces` — the graphs (S-parameter, format, scale «авто» / «по норме»
  / fixed, the source of the limit line: norm / etk / fixed / none with its number, hidden, memory), validated by the
  protocol v2 screen model in the application before every write; markers; `version` (CAS); `active`; who and when.
  One default screen per purpose (partial UNIQUE), and a default is active (CHECK): the bench draws the default.
- Seeded with the six code presets of vna_screens.py (frozen below), so the bench draws exactly the same screens
  after the deploy (tests/production/test_vna_screens_migration.py compares screen hashes).

Not destructive; downgrade refuses once a screen was edited or added.
"""

from datetime import datetime, timezone
import json
from uuid import UUID

from alembic import op
import sqlalchemy as sa

revision = "0207_vna_screens"
down_revision = "0206_equipment_show_flag"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

TABLE = "production_vna_screens"
# Frozen copy of vna_screen_infrastructure.SCREEN_CHECKS as of this revision: never import app code here.
CHECKS = {
    "ck_vna_screens_purpose": "purpose IN ('adapter','assembly','one_port','load','flex','engineer')",
    "ck_vna_screens_split": "split BETWEEN 1 AND 16",
    "ck_vna_screens_version": "version >= 1",
    "ck_vna_screens_name": "trim(name) <> ''",
    "ck_vna_screens_default_active": "NOT is_default OR active",
}
MARKERS = [1e9, 3e9, 6e9, 12e9, 18e9]
SEEDED_AT = datetime(2026, 11, 20, tzinfo=timezone.utc)   # fixed: a downgrade/upgrade roundtrip leaves the rows equal


def _scale(pdiv, rlev=None, rpos=None):
    return {"pdiv": pdiv, "rlev": rlev, "rpos": rpos}


SWR_ASSEMBLY, SWR_FINE = _scale(0.05, 1.0, 0), _scale(0.01, 1.0, 0)
LOSS_ADAPTER, LOSS_ASSEMBLY, LOSS_FLEX = _scale(0.05, 0.0, 10), _scale(0.3, 0.0, 10), _scale(0.03, 0.0, 10)
OVERVIEW, SMITH, PHASE = _scale(5.0, 0.0, 10), _scale(1.0), _scale(36.0, 0.0, 5)
LOAD_LINE = {"kind": "upper", "value": 1.06}


def _t(param, fmt, scale, limit="none", fixed=None, *, hidden=False, memory=False):
    return {"param": param, "format": fmt, "scale": scale, "limit": limit, "fixed": fixed, "hidden": hidden,
            "memory": memory}


# Frozen copy of vna_screens.presets() as of this revision (id, purpose, name, split, note, traces).
PRESETS = (
    ("6e1c0f5a-6b1f-4d4e-9a39-0207a0000001", "adapter", "ОТК — переход", 8, "замер переходника", [
        _t("S11", "SWR", "norm", "norm"), _t("S21", "MLOG", LOSS_ADAPTER, "norm"),
        _t("S12", "MLOG", LOSS_ADAPTER, "norm"), _t("S22", "SWR", "norm", "norm")]),
    ("6e1c0f5a-6b1f-4d4e-9a39-0207a0000002", "assembly", "ОТК — кабельная сборка", 5,
     "сборка с двух сторон, полоса и норма по ЭТК", [
        _t("S11", "SWR", SWR_ASSEMBLY, "etk"), _t("S21", "MLOG", LOSS_ASSEMBLY), _t("S22", "SWR", SWR_ASSEMBLY, "etk")]),
    ("6e1c0f5a-6b1f-4d4e-9a39-0207a0000003", "one_port", "ОТК — один порт", 3,
     "сборка с одной стороны, на другом конце нагрузка", [
        _t("S11", "SWR", SWR_ASSEMBLY, "etk"), _t("S11", "MLOG", OVERVIEW)]),
    ("6e1c0f5a-6b1f-4d4e-9a39-0207a0000004", "load", "Проверка калибровки (нагрузка)", 3,
     "нагрузка 6650F27 на каждом порту", [
        _t("S11", "SWR", SWR_FINE, "fixed", LOAD_LINE), _t("S21", "MLOG", LOSS_ADAPTER, hidden=True),
        _t("S12", "MLOG", LOSS_ADAPTER, hidden=True), _t("S22", "SWR", SWR_FINE, "fixed", LOAD_LINE)]),
    ("6e1c0f5a-6b1f-4d4e-9a39-0207a0000005", "flex", "Проверка кабелей (шевеление)", 8,
     "запомнить графики, изогнуть кабель, сравнить", [
        _t("S11", "SWR", SWR_FINE, memory=True), _t("S22", "SWR", SWR_FINE, memory=True),
        _t("S21", "MLOG", LOSS_FLEX, memory=True), _t("S21", "PHAS", PHASE, memory=True)]),
    ("6e1c0f5a-6b1f-4d4e-9a39-0207a0000006", "engineer", "Подробно (инженер)", 15,
     "все графики: КСВН, потери, Вольперт-Смит, фаза, ГВЗ, возвратные потери", [
        _t("S11", "SWR", "norm", "norm"), _t("S21", "MLOG", LOSS_ADAPTER, "norm"), _t("S12", "MLOG", LOSS_ADAPTER, "norm"),
        _t("S22", "SWR", "norm", "norm"), _t("S11", "SMIT", SMITH), _t("S21", "PHAS", PHASE), _t("S21", "GDEL", "auto"),
        _t("S22", "SMIT", SMITH), _t("S11", "MLOG", OVERVIEW), _t("S21", "MLOG", OVERVIEW), _t("S12", "MLOG", OVERVIEW),
        _t("S22", "MLOG", OVERVIEW)]),
)


def upgrade() -> None:
    table = op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("split", sa.Integer(), nullable=False),
        sa.Column("traces", sa.JSON(), nullable=False),
        sa.Column("markers_hz", sa.JSON(), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by_name", sa.String(200), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_vna_screens_name"),
        *(sa.CheckConstraint(sql, name=name) for name, sql in CHECKS.items()),
    )
    op.create_index("uq_vna_screens_default", TABLE, ["purpose"], unique=True,
                    sqlite_where=sa.text("is_default = 1"), postgresql_where=sa.text("is_default"))
    now = SEEDED_AT
    op.bulk_insert(table, [dict(id=UUID(key), name=name, purpose=purpose, is_default=True, split=split,
                                traces=json.loads(json.dumps(traces)), markers_hz=list(MARKERS), note=note, active=True,
                                version=1, updated_by=None, updated_by_name="", updated_at=now)
                           for key, purpose, name, split, note, traces in PRESETS])


def downgrade() -> None:
    bind = op.get_bind()
    edited = bind.execute(sa.text(f"SELECT count(*) FROM {TABLE} WHERE version > 1")).scalar()
    total = bind.execute(sa.text(f"SELECT count(*) FROM {TABLE}")).scalar()
    if edited or total != len(PRESETS):
        raise RuntimeError("Экраны ВАЦ уже правили или добавляли — откат удалил бы их; сохраните экраны и решите вручную")
    op.drop_index("uq_vna_screens_default", table_name=TABLE)
    op.drop_table(TABLE)
