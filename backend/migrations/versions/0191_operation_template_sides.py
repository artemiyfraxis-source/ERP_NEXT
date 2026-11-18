"""Operation-card templates without sides (A)/(B): one template per operation code (owner 17.11.2026: «шаблоны (A)/(B)
искоренять»).

Each pair `CODE | Name (A)` + `CODE | Name (B)` becomes one template `CODE | Name` with a new id without the side suffix
(the slug of the title, a frozen copy of `template_sheets.slugify`). The survivor is side A, except where the owner
already chose the side that owns the record (17.09, the former `RECORD_SIDE_PICK`): `SDR_COAX_housing` → B,
`SDR_COAX_pin` → A. Pictures are the union of both sides; `edited`, `description` and `saved_*` come from the
survivor; the stored `document` loses its `side` key (all templates).

Pre-checks — the migration stops with the list and changes nothing when any fails:
- both sides carry the same ERP edit (`edited`) or none;
- both sides give the same `document` once side, title, pictures and designation are ignored (except the owner-picked
  codes above);
- no other template repeats a code (case-insensitive), no side appears twice;
- the new ids are free.

What points at the old ids is moved to the new ones: `equipment_template_tools` (the survivor's rows; a model link
only the other side has is copied when the tool name matches, else it is reported), the drafts' `existing_template_id`
and the exact ids inside saved techcard JSON (`techcard_projects` inputs / document / overrides, generator drafts,
assembly drafts, harness routes). Issued revisions and production snapshots are history and keep the old ids: the
readers resolve a missing id by the operation code.

Schema: the code+side index and the `side` column go, `UNIQUE(code)` keeps a pair from coming back. Merged rows are
deleted, so DESTRUCTIVE: the owner approves this revision id. The downgrade restores the schema only (an empty
`side`); the deleted sides come back only from the pre-update backup.
"""

from __future__ import annotations

from collections import defaultdict
import json
import re
from typing import Any

from alembic import op
import sqlalchemy as sa

revision = "0191_operation_template_sides"
down_revision = "0190_godex_marking"
branch_labels = None
depends_on = None
DESTRUCTIVE = True

TABLE = "techcard_operation_templates"
CODE_SIDE_INDEX = "ix_techcard_operation_templates_code_side"
UNIQUE_CODE = "uq_techcard_operation_templates_code"
SIDES = ("A", "B")
SIDE_SUFFIX = re.compile(r"\s*\(([AА]|[BВ])\)$")
# The side that owns the record where the sides were known to differ (owner 17.09; SUPPLYMAP TC_REC_SIDE_PICK_).
OWNER_PICK = {"SDR_COAX_housing": "B", "SDR_COAX_pin": "A"}
IGNORED_DOCUMENT_KEYS = {"side", "title", "images", "designation"}
# Saved JSON that names operation templates by id: (table, key column, JSON columns).
JSON_REFERENCES = (
    ("techcard_projects", "id", ("inputs", "document", "overrides")),
    ("techcard_generator_drafts", "id", ("payload",)),
    ("techcard_assembly_drafts", "id", ("draft_config", "final_config")),
    ("techcard_harness_routes", "id", ("document",)),
)

templates = sa.table(
    TABLE,
    sa.column("id", sa.String), sa.column("code", sa.String), sa.column("side", sa.String),
    sa.column("title", sa.String), sa.column("document", sa.JSON), sa.column("raw_rows", sa.JSON),
    sa.column("images", sa.JSON), sa.column("edited", sa.JSON), sa.column("edited_at", sa.DateTime(timezone=True)),
    sa.column("edited_by", sa.String), sa.column("description", sa.Text), sa.column("saved_at", sa.DateTime(timezone=True)),
    sa.column("saved_by", sa.String),
)
tools = sa.table(
    "equipment_template_tools",
    sa.column("template_id", sa.String), sa.column("position", sa.Integer), sa.column("tool_name", sa.String),
    sa.column("inventory_number", sa.String), sa.column("candidates", sa.Integer), sa.column("model_id", sa.Uuid),
)
drafts = sa.table("techcard_operation_template_drafts", sa.column("id", sa.Uuid), sa.column("existing_template_id", sa.String))
snapshots = sa.table("techcard_operation_template_snapshots", sa.column("name", sa.String),
                     sa.column("template_count", sa.Integer))


def slugify(value: object) -> str:
    """Frozen copy of `template_sheets.slugify` (SUPPLYMAP TcUtils.gs slugify_) as of this migration."""
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9а-яё]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return re.sub(r"-+", "-", text)


def base_title(title: object) -> str:
    return SIDE_SUFFIX.sub("", str(title or "").strip()).strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _content(document: Any) -> str:
    content = {key: value for key, value in document.items() if key not in IGNORED_DOCUMENT_KEYS} \
        if isinstance(document, dict) else document
    return _canonical(content)


def _edit(row: Any) -> str:
    return _canonical(row.edited or None)


def _images(*rows: Any) -> list:
    seen: set[str] = set()
    result = []
    for row in rows:
        for image in row.images or []:
            key = _canonical(image)
            if key not in seen:
                seen.add(key)
                result.append(image)
    return result


def _document_images(images: list) -> list:
    """`OperationCardImage.__dict__` of the stored pictures (`document()["images"]`)."""
    return [{
        "row": int(item.get("row") or 0), "col": int(item.get("col") or 0),
        "drive_file_id": str(item.get("driveFileId") or "").strip(),
        "width": item.get("width"), "height": item.get("height"),
    } for item in images if isinstance(item, dict)]


def plan_merges(rows: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """The merges of the sided templates and the problems that stop the migration (Russian, one per line)."""
    problems: list[str] = []
    by_code: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        by_code[str(row.code or "").strip().casefold()].append(row)
    merges = []
    for members in by_code.values():
        sided = [row for row in members if row.side in SIDES]
        if not sided:
            if len(members) > 1:
                problems.append(f"код {members[0].code}: {len(members)} шаблона без сторон — "
                                f"{', '.join(sorted(row.id for row in members))}")
            continue
        names = ", ".join(sorted(f"{row.id} ({row.side or 'без стороны'})" for row in members))
        if len(sided) != len(members) or len({row.code for row in members}) > 1 \
                or len({row.side for row in sided}) != len(sided):
            problems.append(f"код {members[0].code}: не пара сторон (A)/(B) — {names}")
            continue
        by_side = {row.side: row for row in sided}
        code = sided[0].code
        keep_side = OWNER_PICK.get(code, "A") if OWNER_PICK.get(code, "A") in by_side else sided[0].side
        keep = by_side[keep_side]
        other = next((row for row in sided if row is not keep), None)
        if other is not None:
            if _edit(keep) != _edit(other):
                problems.append(f"код {code}: стороны A и B правлены по-разному (правка записи в ERP) — "
                                "какую оставить, решает владелец")
            elif code not in OWNER_PICK and _content(keep.document) != _content(other.document):
                problems.append(f"код {code}: содержимое сторон A и B расходится — какую оставить, решает владелец")
        title = base_title(keep.title)
        merges.append({"code": code, "keep": keep, "drop": other, "title": title, "new_id": slugify(title) or "template"})
    merged_ids = {row.id for merge in merges for row in (merge["keep"], merge["drop"]) if row is not None}
    taken = {row.id for row in rows if row.id not in merged_ids}
    new_ids: dict[str, str] = {}
    for merge in merges:
        new_id = merge["new_id"]
        if new_id in taken:
            problems.append(f"код {merge['code']}: новый id «{new_id}» уже занят другим шаблоном")
        elif new_id in new_ids:
            problems.append(f"коды {new_ids[new_id]} и {merge['code']}: одинаковый новый id «{new_id}»")
        new_ids[new_id] = merge["code"]
    return merges, problems


def _remap(value: Any, ids: dict[str, str]) -> tuple[Any, int]:
    """The JSON with exact string values and keys equal to an old id replaced; how many were replaced."""
    if isinstance(value, str):
        return (ids[value], 1) if value in ids else (value, 0)
    if isinstance(value, list):
        count = 0
        result = []
        for item in value:
            item, found = _remap(item, ids)
            result.append(item)
            count += found
        return result, count
    if isinstance(value, dict):
        count = 0
        result = {}
        for key, item in value.items():
            if isinstance(key, str) and key in ids:
                key = ids[key]
                count += 1
            item, found = _remap(item, ids)
            result[key] = item
            count += found
        return result, count
    return value, 0


def _move_tools(bind, keep: Any, drop: Any | None, new_id: str, report: list[str]) -> tuple[int, int]:
    """The survivor's «Оборудование» links under the new id, the other side's rows deleted. Returns (moved, deleted)."""
    columns = (tools.c.position, tools.c.tool_name, tools.c.model_id, tools.c.candidates)
    kept = {row.position: row for row in bind.execute(sa.select(*columns).where(tools.c.template_id == keep.id))}
    for position, row in kept.items():
        bind.execute(tools.update().where(tools.c.template_id == keep.id, tools.c.position == position)
                     .values(template_id=new_id))
    deleted = 0
    if drop is not None:
        for row in bind.execute(sa.select(*columns).where(tools.c.template_id == drop.id)).all():
            survivor = kept.get(row.position)
            if row.model_id is not None and (survivor is None or survivor.model_id is None):
                if survivor is not None and survivor.tool_name == row.tool_name:
                    bind.execute(tools.update().where(tools.c.template_id == new_id, tools.c.position == row.position)
                                 .values(model_id=row.model_id, candidates=row.candidates))
                    report.append(f"{drop.id} поз. {row.position}: связь с моделью перенесена на {new_id}")
                else:
                    report.append(f"{drop.id} поз. {row.position} «{row.tool_name}»: связь с моделью {row.model_id} "
                                  f"не перенесена — у {new_id} на этой позиции другое оборудование")
            deleted += 1
        bind.execute(tools.delete().where(tools.c.template_id == drop.id))
    return len(kept), deleted


def merge_sides(bind) -> dict[str, Any]:
    """The data part of the upgrade (also run by the migration test). Raises RuntimeError with the list on a failed
    pre-check, before anything is written."""
    rows = bind.execute(sa.select(templates)).all()
    merges, problems = plan_merges(rows)
    if problems:
        raise RuntimeError("Миграция 0191 остановлена, ничего не изменено — шаблоны (A)/(B) нельзя слить:\n"
                           + "\n".join(f"- {line}" for line in problems))
    ids: dict[str, str] = {}
    report: list[str] = []
    moved = deleted_tools = 0
    for merge in merges:
        keep, drop, new_id, title = merge["keep"], merge["drop"], merge["new_id"], merge["title"]
        images = _images(*(row for row in (keep, drop) if row is not None))
        raw_rows = [list(item) for item in keep.raw_rows or []]
        if raw_rows and raw_rows[0] and SIDE_SUFFIX.search(str(raw_rows[0][0] or "").strip()):
            raw_rows[0][0] = base_title(raw_rows[0][0])
        document = dict(keep.document) if isinstance(keep.document, dict) else {}
        document.pop("side", None)
        document.update(title=title, images=_document_images(images))
        if drop is not None:
            bind.execute(templates.delete().where(templates.c.id == drop.id))
        bind.execute(templates.update().where(templates.c.id == keep.id).values(
            id=new_id, title=title, raw_rows=raw_rows, images=images, document=document))
        for row in (keep, drop):
            if row is not None:
                ids[row.id] = new_id
        tools_moved, tools_deleted = _move_tools(bind, keep, drop, new_id, report)
        moved += tools_moved
        deleted_tools += tools_deleted
    # Every other template's stored document loses its empty `side` too.
    for row in bind.execute(sa.select(templates.c.id, templates.c.document)).all():
        if isinstance(row.document, dict) and "side" in row.document:
            document = {key: value for key, value in row.document.items() if key != "side"}
            bind.execute(templates.update().where(templates.c.id == row.id).values(document=document))
    replaced: dict[str, int] = {}
    if ids:
        for draft in bind.execute(sa.select(drafts.c.id, drafts.c.existing_template_id)
                                  .where(drafts.c.existing_template_id.in_(list(ids)))).all():
            bind.execute(drafts.update().where(drafts.c.id == draft.id)
                         .values(existing_template_id=ids[draft.existing_template_id]))
            replaced["techcard_operation_template_drafts"] = replaced.get("techcard_operation_template_drafts", 0) + 1
        removed = sum(1 for merge in merges if merge["drop"] is not None)
        if removed:
            bind.execute(snapshots.update().where(snapshots.c.name == "current")
                         .values(template_count=snapshots.c.template_count - removed))
        inspector = sa.inspect(bind)
        existing = set(inspector.get_table_names())
        for name, key, columns in JSON_REFERENCES:
            if name not in existing:
                continue
            table = sa.table(name, sa.column(key, sa.Uuid), *(sa.column(column, sa.JSON) for column in columns))
            for row in bind.execute(sa.select(table)).all():
                changes = {}
                for column in columns:
                    value, count = _remap(getattr(row, column), ids)
                    if count:
                        changes[column] = value
                        replaced[name] = replaced.get(name, 0) + count
                if changes:
                    bind.execute(table.update().where(table.c[key] == getattr(row, key)).values(**changes))
    return {"merged": len(merges), "deleted": sum(1 for merge in merges if merge["drop"] is not None),
            "ids": ids, "replaced": replaced, "tools_moved": moved, "tools_deleted": deleted_tools, "report": report}


def upgrade() -> None:
    result = merge_sides(op.get_bind())
    print(f"0191: слито пар {result['merged']}, удалено шаблонов {result['deleted']}; оборудование: перенесено "
          f"{result['tools_moved']}, удалено {result['tools_deleted']}; замены id: {result['replaced'] or 'нет'}")
    for line in result["report"]:
        print(f"0191: {line}")
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_index(CODE_SIDE_INDEX)
        batch.drop_column("side")
        batch.create_unique_constraint(UNIQUE_CODE, ["code"])


def downgrade() -> None:
    """Schema only: the deleted sides and old ids come back only from the pre-update backup."""
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(UNIQUE_CODE, type_="unique")
        batch.add_column(sa.Column("side", sa.String(1), nullable=False, server_default=""))
        batch.create_index(CODE_SIDE_INDEX, ["code", "side"])
