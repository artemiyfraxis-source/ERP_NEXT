"""Одна живая заявка на КБ проекта и номера заявок без повторов (владелец, 10.10.2026).

Prod incident 180К.11: «Отправить на комплектацию» pressed twice made two requests for the same КБ1, and «ГРН на все
КБ» then reserved each lot twice. Request numbers were max+1, so a deleted request's number was given out again.

Schema, non-destructive (the production database is live):
- `fulfillment_requests.project_key` / `item_index` (nullable) — the project and КБ of a kit request, normalized the
  way the app compares them (`procurement.domain.project_key`, `fulfillment.domain.item_index_key`); backfilled from
  НАЗНАЧЕНИЕ «180К.11 · КБ1 · …». A manual request without « · » or without a КБ keeps both NULL.
- `fulfillment_requests.kind` — 'main' (the kit of a КБ; every existing request) or 'extra' («Довыдача» on top of
  an issued kit: the kit goes out exactly to the need, a shortage is added later — owner 10.10.2026), CHECK.
- partial UNIQUE index (project_key, item_index) WHERE kind = 'main' AND status <> 'Отменена' and both NOT NULL: a КБ
  of a project has at most one live main request; extras are not limited.
- `fulfillment_request_counters` — the last number given out per year, seeded with the current max; numbers only
  grow from it, a deleted request's number is never reused.

If a КБ already has two live requests the migration stops BEFORE changing anything and names them: it never cancels a
request itself — the storekeeper cancels the extra one in «Выдача» and the update runs again.

The normalization below is a frozen copy of the app's functions as of this revision (a migration must not change when
the app code does).

Downgrade drops the index, the three columns and the counter table.
"""

import re
from collections import defaultdict
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0089_fulfillment_kit_request_unique"
down_revision: str | None = "0088_api_tokens_assembly_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CANCELLED = "Отменена"
LIVE_KIT_WHERE = f"kind = 'main' AND status <> '{CANCELLED}' AND project_key IS NOT NULL AND item_index IS NOT NULL"

_KB = re.compile(r"КБ\s*-?\s*(\d+)", re.IGNORECASE)
_PROJECT_HOMOGLYPHS = str.maketrans("KCEOPAHTMBXY", "КСЕОРАНТМВХУ")
_PROJECT_WITHOUT_VERSION = re.compile(r"([1-9]\d*(?:К[А-Я]?|МФ)(?:\.[А-Я]{1,3})?)\.?")
_NUMBER = re.compile(r"^REQ\.(\d{2})\.(\d+)$")


def _project_key(value: str | None) -> str:
    normalized = re.sub(r"\s+", "", str(value or "").strip().upper()).translate(_PROJECT_HOMOGLYPHS)
    match = _PROJECT_WITHOUT_VERSION.fullmatch(normalized)
    return f"{match.group(1)}.1" if match else normalized.rstrip(".")


def kit_key(purpose: str | None) -> tuple[str, str] | None:
    """НАЗНАЧЕНИЕ «180К.11 · КБ1 · …» → ('180К.11', 'КБ1'); no project or no КБ → None."""
    parts = str(purpose or "").split(" · ")
    project = parts[0].strip() if len(parts) > 1 else ""
    match = _KB.search(str(purpose or ""))
    key = _project_key(project)
    if not key or match is None:
        return None
    return key, f"КБ{int(match.group(1))}"


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, number, purpose, status FROM fulfillment_requests")).fetchall()
    keys = {row[0]: kit_key(row[2]) for row in rows}
    live: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        if row[3] != CANCELLED and keys[row[0]] is not None:
            live[keys[row[0]]].append(row[1])
    duplicates = {key: sorted(numbers) for key, numbers in live.items() if len(numbers) > 1}
    if duplicates:
        listed = "; ".join(
            f"{project} {kb}: {', '.join(numbers)}" for (project, kb), numbers in sorted(duplicates.items())
        )
        raise RuntimeError(
            "Миграция 0089 остановлена, база не изменена: у КБ проекта несколько живых заявок на комплектацию — "
            f"{listed}. Отмените лишние в «Выдаче» («Отменить заявку») и повторите обновление."
        )

    with op.batch_alter_table("fulfillment_requests") as batch:
        batch.add_column(sa.Column("project_key", sa.String(128), nullable=True))
        batch.add_column(sa.Column("item_index", sa.String(64), nullable=True))
        batch.add_column(sa.Column("kind", sa.String(16), nullable=False, server_default="main"))
        batch.create_check_constraint("ck_fulfillment_requests_kind", "kind IN ('main', 'extra')")
    update = sa.text("UPDATE fulfillment_requests SET project_key = :project, item_index = :kb WHERE id = :id")
    for request_id, key in keys.items():
        if key is not None:
            connection.execute(update, {"project": key[0], "kb": key[1], "id": request_id})
    op.create_index(
        "uq_fulfillment_requests_live_kit", "fulfillment_requests", ["project_key", "item_index"], unique=True,
        postgresql_where=sa.text(LIVE_KIT_WHERE), sqlite_where=sa.text(LIVE_KIT_WHERE),
    )

    counters = op.create_table(
        "fulfillment_request_counters",
        sa.Column("year", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("last_number", sa.Integer(), nullable=False),
        sa.CheckConstraint("last_number >= 0", name="ck_fulfillment_request_counters_last_number"),
    )
    last: dict[int, int] = {}
    for row in rows:
        match = _NUMBER.match(str(row[1] or ""))
        if match:
            year = 2000 + int(match.group(1))
            last[year] = max(last.get(year, 0), int(match.group(2)))
    if last:
        op.bulk_insert(counters, [{"year": year, "last_number": number} for year, number in sorted(last.items())])


def downgrade() -> None:
    op.drop_table("fulfillment_request_counters")
    op.drop_index("uq_fulfillment_requests_live_kit", table_name="fulfillment_requests")
    with op.batch_alter_table("fulfillment_requests") as batch:
        batch.drop_constraint("ck_fulfillment_requests_kind", type_="check")
        batch.drop_column("kind")
        batch.drop_column("item_index")
        batch.drop_column("project_key")
