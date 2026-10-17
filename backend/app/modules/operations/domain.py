"""Pure notification rules extracted from the SUPPLYMAP portal inbox, plus the module tasks of a project."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4


class NotificationValidationError(ValueError):
    pass


def normalize_recipient(value: str) -> str:
    email = str(value or "").strip().casefold()
    local, separator, domain = email.partition("@")
    if not separator or not local or "." not in domain:
        raise NotificationValidationError("Некорректный адрес уведомления")
    return email


@dataclass(frozen=True)
class Notification:
    id: UUID
    recipient_email: str
    created_at: datetime
    kind: str
    reference: str
    title: str
    body: str
    dedupe_key: str
    read_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        recipient_email: str,
        created_at: datetime,
        kind: str,
        reference: str,
        title: str,
        body: str,
        dedupe_key: str,
    ) -> Notification:
        clean_title = str(title or "").strip()[:200]
        clean_key = str(dedupe_key or "").strip()
        if not clean_title:
            raise NotificationValidationError("Заголовок уведомления обязателен")
        if not clean_key:
            raise NotificationValidationError("Ключ уведомления обязателен")
        return cls(
            id=uuid4(),
            recipient_email=normalize_recipient(recipient_email),
            created_at=created_at,
            kind=str(kind or "info").strip()[:64] or "info",
            reference=str(reference or "").strip()[:255],
            title=clean_title,
            body=str(body or "").strip()[:1500],
            dedupe_key=clean_key[:255],
        )

    def mark_read(self, now: datetime) -> Notification:
        return self if self.read_at is not None else replace(self, read_at=now)


# ── Задачи модулям по проекту (ARCH-TRACK-3, C2) ─────────────────────────
# Арбитр (`app.workflows.arbiter`) ставит их по событиям модулей; модуль задачи — ключ раздела доступа.


class ProjectTaskError(ValueError):
    pass


class ProjectTaskStatus(str, Enum):
    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"


PROJECT_TASK_STATUS_LABELS = {
    ProjectTaskStatus.OPEN: "Открыта",
    ProjectTaskStatus.DONE: "Закрыта",
    ProjectTaskStatus.CANCELLED: "Отменена",
}
# Названия разделов — как в треке проекта; ключ — раздел доступа (`require_section`).
TASK_MODULE_LABELS = {
    "projects": "Проекты",
    "techcards": "Техкарты",
    "requests": "Заявки",
    "procurement": "Закупка",
    "receiving": "Приёмка",
    "warehouse": "Склад",
    "fulfillment": "Комплектация",
    "production": "Производство",
    "shipping": "Отгрузка",
}


def task_module_label(module: str) -> str:
    return TASK_MODULE_LABELS.get(module, module)


def _required(value: object, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ProjectTaskError(f"{label} задачи обязателен")
    return text[:limit]


@dataclass(frozen=True)
class ProjectTask:
    """Одна задача модулю по проекту. `source_event_id` — событие журнала, из которого она родилась (повтор того же
    события задачу не дублирует); `subject_id` — то, о чём задача (техкарта, комплект, позиция ГП): по нему арбитр
    узнаёт, что дело сделано."""

    id: UUID
    project: str
    item_index: str
    module: str
    kind: str
    title: str
    source_event_id: UUID
    source_type: str
    subject_id: UUID | None
    status: ProjectTaskStatus
    created_at: datetime
    closed_at: datetime | None = None
    closed_by: str = ""

    @classmethod
    def create(
        cls, *, project: str, item_index: str, module: str, kind: str, title: str,
        source_event_id: UUID, source_type: str, subject_id: UUID | None, created_at: datetime,
    ) -> ProjectTask:
        return cls(
            id=uuid4(), project=_required(project, "Проект", 255),
            item_index=str(item_index or "").strip().upper()[:64],
            module=_required(module, "Раздел", 64), kind=_required(kind, "Вид", 64),
            title=_required(title, "Текст", 255), source_event_id=source_event_id,
            source_type=_required(source_type, "Источник", 128), subject_id=subject_id,
            status=ProjectTaskStatus.OPEN, created_at=created_at,
        )

    @property
    def is_open(self) -> bool:
        return self.status is ProjectTaskStatus.OPEN

    @property
    def status_label(self) -> str:
        return PROJECT_TASK_STATUS_LABELS[self.status]

    def close(self, now: datetime, by: str) -> ProjectTask:
        if not self.is_open:
            raise ProjectTaskError("Задача уже закрыта")
        return replace(self, status=ProjectTaskStatus.DONE, closed_at=now, closed_by=str(by or "").strip()[:255])
