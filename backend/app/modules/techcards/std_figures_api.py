"""Рисунки стандартов к строкам СТО: список, картинка, замена набора строки СТО и удаление. Правила — std_figures.py.

Читает любой вошедший сотрудник: карточки «Типовые дефекты» видит и исполнитель в «Заданиях», не только раздел
«Техкарты» (поэтому роутер подключён без общего входа в техкарты). Пишет тот, кто правит «Шаблоны техкарт»
(`tctemplates_edit_principal`), или скрипт загрузки с токеном API «figures:write»; удаляет только человек."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.shared.clock import moscow_now
from app.shared.http_auth import TOKEN_SCOPE_PREFIX, Principal, authenticated_principal, tctemplates_edit_principal
from app.shared.stored_files import decode_upload, file_response

from .api import techcards_uow_factory
from .application import UnitOfWork
from .std_figures import FigureUpload, StdFigureError, check_code, prepare_figures

FIGURES_SCOPE = "figures:write"
IMAGE_CACHE = "private, max-age=31536000, immutable"
# A transport ceiling only (the body limit is 18 MB); the 2 MB rule is checked on the decoded bytes, in Russian.
_BASE64_MAX = 8_000_000

router = APIRouter(prefix="/api", tags=["Рисунки стандартов"])


class FigurePayload(BaseModel):
    role: Literal["norm", "defect", "ref"]
    source_kind: Literal["gost", "ipc"]
    caption: str = Field(max_length=1000)
    # What the uploader says the file is (by its extension); empty — not said. The bytes decide, a mismatch is refused.
    content_type: Literal["", "image/png", "image/jpeg"] = ""
    content_base64: str = Field(min_length=1, max_length=_BASE64_MAX)


class FiguresPayload(BaseModel):
    figures: list[FigurePayload] = Field(max_length=50)


def figures_editor(
    principal: Annotated[Principal, Security(authenticated_principal, scopes=[TOKEN_SCOPE_PREFIX + FIGURES_SCOPE])],
) -> Principal:
    """Скрипт загрузки (токен API со scope `figures:write` — его проверяет security scope) или человек с правкой
    «Шаблонов техкарт» — та же проверка, что у правки шаблонов."""
    if principal.service:
        return principal
    return tctemplates_edit_principal(principal)


figures_editor.token_scopes = frozenset({FIGURES_SCOPE})  # type: ignore[attr-defined]  (the token contract test reads it)


def _run(operation):
    try:
        return operation()
    except StdFigureError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="Рисунки строки СТО меняются одновременно: повторите") from error


@router.get("/techcards/std-figures", dependencies=[Depends(authenticated_principal)])
def std_figures(uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)]) -> dict:
    """Все рисунки без байтов, по строке СТО и позиции."""
    with uow_factory() as uow:
        figures = uow.std_figures.all()
    return {"items": [figure.view() for figure in figures]}


@router.get("/techcards/std-figures/{figure_id}/image", dependencies=[Depends(authenticated_principal)])
def std_figure_image(
    figure_id: UUID,
    request: Request,
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> Response:
    """Картинка рисунка. Замена набора даёт новые id, поэтому ответ кешируется надолго; ETag — sha256 байтов."""
    with uow_factory() as uow:
        found = uow.std_figures.content(figure_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Рисунок стандарта не найден")
    figure, content = found
    etag = f'"{figure.sha256}"'
    if etag in [tag.strip() for tag in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": IMAGE_CACHE})
    extension = ".png" if figure.content_type == "image/png" else ".jpg"
    response = file_response(content, f"{figure.sto_code}_{figure.position}{extension}")
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = IMAGE_CACHE
    return response


@router.put("/techcards/std-figures/{sto_code}")
def put_std_figures(
    sto_code: str,
    payload: FiguresPayload,
    principal: Annotated[Principal, Depends(figures_editor)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    """Заменить все рисунки строки СТО одним действием: порядок в запросе — порядок показа."""
    uploads = [
        FigureUpload(
            role=item.role, source_kind=item.source_kind, caption=item.caption, declared_type=item.content_type,
            content=decode_upload(item.content_base64.split(",", 1)[-1], f"Рисунок {index}: картинка не в base64"),
        )
        for index, item in enumerate(payload.figures, start=1)
    ]

    def replace() -> list:
        figures = prepare_figures(sto_code, uploads, actor=principal.actor.name, now=now)
        with uow_factory() as uow:
            uow.std_figures.replace(figures[0].sto_code, [(figure, upload.content) for figure, upload in zip(figures, uploads)])
            uow.commit()
        return figures

    figures = _run(replace)
    return {"sto_code": figures[0].sto_code, "items": [figure.view() for figure in figures]}


@router.delete("/techcards/std-figures/{sto_code}")
def delete_std_figures(
    sto_code: str,
    _principal: Annotated[Principal, Depends(tctemplates_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    """Убрать все рисунки строки СТО (карточка дефекта вернётся к своему рисунку). Токен не удаляет."""

    def delete() -> int:
        code = check_code(sto_code)
        with uow_factory() as uow:
            deleted = uow.std_figures.delete_code(code)
            uow.commit()
        return deleted

    return {"deleted": _run(delete)}
