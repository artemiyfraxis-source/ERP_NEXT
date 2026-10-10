"""Чертёж жгута: библиотека компонентов — таблица `harness_component_templates` (владелец, 10.10.2026).

До сих пор шаблон разъёма, поставленный на чертёж, жил копией внутри каждого чертежа (`componentSnapshots` /
`componentPlacements`, как в экспорте TECHMAP-GRAPHER), а сама библиотека была пустой заглушкой в браузере. Теперь
шаблоны — строки этой таблицы: ключ разъёма из справочника (`connector_ref`, код разъёма) и номер версии
(`revision`), модель шаблона (JSON TECHMAP-GRAPHER v3–v5) и его рисунок (PNG). Соединитель на чертеже закрепляет
шаблон и версию сам (`libraryBinding`: id шаблона, номер, хэш версии) — копия в чертеже больше не нужна.

Правила в базе:
- номер версии > 0 и единственный у ключа разъёма (UNIQUE(connector_ref, revision));
- закрепление соединителя (шаблон, номер, хэш) указывает ровно на одну строку (UNIQUE(template_id, revision,
  version_sha256));
- одинаковое содержимое — одна строка (UNIQUE(content_sha256)); хэши полные (64 знака); ключ не пустой;
- рисунок и его хэш есть или нет вместе.

Перенос: копии шаблонов из каждого чертежа жгута (привязанного и отвязанного) и из каждой версии его истории
переносятся в таблицу без повторов (по хэшу содержимого). Ключ — код разъёма; если код уже занят другим шаблоном
(или этот номер версии занят), к ключу добавляется начало id шаблона / хэша версии. Байты чертежей и истории НЕ
меняются (текущие байты = последняя версия истории, миграция 0090): копия уходит из чертежа при его следующем
сохранении, а до того редактор читает закрепление из библиотеки, а если там его нет — из копии в чертеже.

Начальное наполнение: корпуса JST SH SHR-02V-S … SHR-20V-S с рисунками Василия (TECHMAP-GRAPHER,
design/jst-sh-assets, с разрешения автора; штриховки QCAD (GPL) не используются) — `backend/data/harness-library/`.

Счёт — в вывод миграции. Откат снимает таблицу (чертежи не тронуты, их копии шаблонов на месте).
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Sequence
from uuid import UUID, uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0091_harness_component_library"
down_revision: str | None = "0090_harness_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "harness_component_templates"
# harness.schema.HARNESS_CONTENT_TYPE / HARNESS_UNLINKED_CONTENT_TYPE
DRAWING_TYPES = ("application/vnd.fraxis.harness+json", "application/vnd.fraxis.harness-unlinked+json")
SEED = Path(__file__).resolve().parents[2] / "data" / "harness-library" / "seed.json"
SEEDED_AT = datetime(2026, 10, 10, tzinfo=timezone(timedelta(hours=3)))
MAX_REF = 200
_SHA = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.IGNORECASE)

_documents = sa.table(
    "techcard_kitset_documents",
    sa.column("id", sa.Uuid()), sa.column("project", sa.String()), sa.column("item_index", sa.String()),
    sa.column("content_type", sa.String()), sa.column("content", sa.LargeBinary()),
    sa.column("uploaded_at", sa.DateTime(timezone=True)), sa.column("uploaded_by", sa.String()),
)
_revisions = sa.table(
    "techcard_harness_revisions",
    sa.column("document_id", sa.Uuid()), sa.column("revision", sa.Integer()), sa.column("content", sa.LargeBinary()),
    sa.column("saved_by", sa.String()), sa.column("saved_at", sa.DateTime(timezone=True)),
)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _version_sha(item: dict) -> str:
    """harness.components.version_sha256"""
    return hashlib.sha256(_canonical({
        "code": item["code"], "name": item["name"], "articleBindings": item["article_bindings"],
        "assets": item["assets"], "content": item["model"],
    })).hexdigest()


def _content_sha(item: dict) -> str:
    """harness.components.content_sha256"""
    return hashlib.sha256(_canonical({
        "templateId": str(item["template_id"]), "revision": item["revision"], "versionSha256": item["version_sha256"],
        "code": item["code"], "name": item["name"], "articleBindings": item["article_bindings"],
        "assets": item["assets"], "content": item["model"],
    })).hexdigest()


def _ref(code: str) -> str:
    """harness.components.connector_ref"""
    return re.sub(r"\s+", " ", code).strip().upper()[:MAX_REF]


def _rows(value: object) -> list[dict]:
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _embedded(snapshot: dict) -> dict | None:
    """harness.components.candidate_from_snapshot; None — not a template revision that can be moved."""
    template_id, version = _text(snapshot.get("sourceTemplateId")), _text(snapshot.get("sourceVersionSha256")).lower()
    revision_number = snapshot.get("sourceVersion")
    model, code, name = snapshot.get("content"), _text(snapshot.get("code")), _text(snapshot.get("name"))
    if (not _UUID.match(template_id) or not _SHA.match(version) or isinstance(revision_number, bool)
            or not isinstance(revision_number, int) or revision_number < 1 or not isinstance(model, dict)
            or model.get("schemaVersion") not in (3, 4, 5) or not code or not name):
        return None
    return {"template_id": UUID(template_id), "revision": revision_number, "version_sha256": version, "code": code,
            "name": name, "article_bindings": _rows(snapshot.get("articleBindings")),
            "assets": _rows(snapshot.get("assets")), "model": model}


class _Library:
    """The rows to insert, with the same dedupe and key rules as harness.application.absorb."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.contents: set[str] = set()
        self.refs: dict[str, list[tuple[UUID, int]]] = {}

    def _free(self, ref: str, template_id: UUID, revision_number: int) -> bool:
        return not any(owner != template_id or number == revision_number for owner, number in self.refs.get(ref, []))

    def add(self, item: dict, *, by: str, at: datetime, picture: bytes | None = None) -> bool:
        item = {**item, "content_sha256": _content_sha(item)}
        if item["content_sha256"] in self.contents:
            return False
        base = _ref(item["code"])
        for ref in (base, f"{base} ({str(item['template_id'])[:8]})", f"{base} ({item['version_sha256'][:12]})"):
            ref = ref[:MAX_REF]
            if self._free(ref, item["template_id"], item["revision"]):
                break
        else:
            _say(f"Шаблон {item['code']} версии {item['revision']} не перенесён: ключ разъёма занят")
            return False
        self.contents.add(item["content_sha256"])
        self.refs.setdefault(ref, []).append((item["template_id"], item["revision"]))
        self.rows.append({
            "id": uuid4(), "template_id": item["template_id"], "connector_ref": ref, "revision": item["revision"],
            "version_sha256": item["version_sha256"], "content_sha256": item["content_sha256"], "code": item["code"],
            "name": item["name"], "article_bindings": item["article_bindings"], "assets": item["assets"],
            "model": item["model"], "snapshot_png": picture,
            "image_sha256": hashlib.sha256(picture).hexdigest() if picture is not None else None,
            "created_by": by or "", "created_at": at,
        })
        return True


def _when(value: object) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _order(value: object) -> datetime:
    moment = _when(value)
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


def upgrade() -> None:
    templates = op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("connector_ref", sa.String(200), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("version_sha256", sa.String(64), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("article_bindings", sa.JSON(), nullable=False),
        sa.Column("assets", sa.JSON(), nullable=False),
        sa.Column("model", sa.JSON(), nullable=False),
        sa.Column("snapshot_png", sa.LargeBinary(), nullable=True),
        sa.Column("image_sha256", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("connector_ref", "revision", name="uq_harness_component_templates_ref_revision"),
        sa.UniqueConstraint("template_id", "revision", "version_sha256", name="uq_harness_component_templates_pin"),
        sa.UniqueConstraint("content_sha256", name="uq_harness_component_templates_content"),
        sa.CheckConstraint("revision > 0", name="ck_harness_component_templates_revision_positive"),
        sa.CheckConstraint("length(version_sha256) = 64", name="ck_harness_component_templates_version_sha256"),
        sa.CheckConstraint("length(content_sha256) = 64", name="ck_harness_component_templates_content_sha256"),
        sa.CheckConstraint("length(trim(connector_ref)) > 0", name="ck_harness_component_templates_ref"),
        sa.CheckConstraint("(snapshot_png IS NULL AND image_sha256 IS NULL) OR "
                           "(snapshot_png IS NOT NULL AND length(image_sha256) = 64)",
                           name="ck_harness_component_templates_image"),
    )
    bind = op.get_bind()
    library = _Library()

    # 1. Copies embedded in the drawings and their history (oldest first: the first author of a revision keeps it).
    sources = [(row.content, row.uploaded_by, row.uploaded_at, f"{row.project} {row.item_index}") for row in bind.execute(
        sa.select(_documents.c.project, _documents.c.item_index, _documents.c.content, _documents.c.uploaded_by,
                  _documents.c.uploaded_at)
        .where(_documents.c.content_type.in_(DRAWING_TYPES), _documents.c.content.is_not(None))
    )]
    sources += [(row.content, row.saved_by, row.saved_at, f"версия {row.revision}") for row in bind.execute(
        sa.select(_revisions.c.revision, _revisions.c.content, _revisions.c.saved_by, _revisions.c.saved_at)
    )]
    sources.sort(key=lambda source: _order(source[2]))
    carrying = found = skipped = 0
    for content, by, at, label in sources:
        try:
            design = json.loads(bytes(content).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _say(f"Чертёж {label}: не JSON — пропущен")
            continue
        snapshots = _rows(design.get("componentSnapshots")) if isinstance(design, dict) else []
        carrying += bool(snapshots)
        for snapshot in snapshots:
            item = _embedded(snapshot)
            if item is None:
                skipped += 1
                _say(f"Чертёж {label}: снимок компонента «{_text(snapshot.get('code')) or '?'}» не шаблон v3–v5 — "
                     "остаётся в чертеже")
                continue
            found += 1
            library.add(item, by=by, at=_when(at))
    moved = len(library.rows)

    # 2. The seed: JST SH housings drawn by Василий.
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    for entry in seed["templates"]:
        picture = (SEED.parent / entry["picture"]).read_bytes()
        item = {"template_id": UUID(entry["templateId"]), "revision": entry["revision"], "code": entry["code"],
                "name": entry["name"], "article_bindings": entry["articleBindings"], "assets": entry["assets"],
                "model": entry["content"]}
        library.add({**item, "version_sha256": _version_sha(item)}, by=seed["createdBy"], at=SEEDED_AT, picture=picture)

    if library.rows:
        bind.execute(templates.insert(), library.rows)
    _say(f"Библиотека компонентов: чертежей и версий с копиями шаблонов {carrying}, копий {found} "
         f"(не шаблоны — {skipped}), перенесено без повторов {moved}, начальных шаблонов "
         f"{len(library.rows) - moved}, всего строк {len(library.rows)}")


def _say(text: str) -> None:
    """В вывод миграции (на Windows вывод в трубу идёт в ANSI — символ вне неё не должен валить миграцию)."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "backslashreplace").decode("ascii"))


def downgrade() -> None:
    op.drop_table(TABLE)
