"""Personal screen layouts (owner 26.10.2026: «настройка показываемых столбцов у каждого своя»).

Each person keeps their own layout per screen view — which columns, their order, widths — on the server, so it follows
them from PC to PC. A layout is a JSON object the screen owns; the server only bounds it: the view name by a pattern,
the text by size, the views per person by count. Only the caller's own rows are ever read or written.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

VIEW_NAME = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
MAX_VALUE_CHARS = 16_384
MAX_VIEWS = 100


class UiSettingsError(ValueError):
    pass


class _Repository(Protocol):
    def ui_settings(self, user_id: UUID) -> dict[str, str]: ...
    def set_ui_setting(self, user_id: UUID, view: str, value: str | None, now: datetime) -> None: ...


class _UnitOfWork(Protocol):
    identity: _Repository

    def __enter__(self) -> _UnitOfWork: ...
    def __exit__(self, exc_type, exc, traceback) -> bool | None: ...
    def commit(self) -> None: ...


def _view(view: str) -> str:
    if not VIEW_NAME.match(view):
        raise UiSettingsError("Неизвестный вид таблицы")
    return view


def _decoded(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def ui_settings(user_id: UUID, uow: _UnitOfWork) -> dict[str, Any]:
    """{view: layout} of the person; a row that does not read as JSON is left out (the screen falls back to its default)."""
    with uow:
        stored = uow.identity.ui_settings(user_id)
    views = {view: _decoded(text) for view, text in stored.items()}
    return {view: value for view, value in sorted(views.items()) if isinstance(value, dict)}


def save_ui_setting(user_id: UUID, view: str, value: Any, now: datetime, uow: _UnitOfWork) -> dict:
    """Replaces the person's layout of one view. The layout is a JSON object of at most MAX_VALUE_CHARS characters;
    a person keeps at most MAX_VIEWS views."""
    _view(view)
    if not isinstance(value, dict):
        raise UiSettingsError("Настройка вида — объект")
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(text) > MAX_VALUE_CHARS:
        raise UiSettingsError("Настройка вида слишком большая")
    with uow:
        stored = uow.identity.ui_settings(user_id)
        if view not in stored and len(stored) >= MAX_VIEWS:
            raise UiSettingsError("Слишком много сохранённых видов")
        uow.identity.set_ui_setting(user_id, view, text, now)
        uow.commit()
    return json.loads(text)


def reset_ui_setting(user_id: UUID, view: str, now: datetime, uow: _UnitOfWork) -> None:
    """«По умолчанию»: the person's layout of the view is removed; the screen shows its default again."""
    _view(view)
    with uow:
        uow.identity.set_ui_setting(user_id, view, None, now)
        uow.commit()
