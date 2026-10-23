"""Рисунки стандартов к строкам СТО ФРАК-001-2026 (owner 23.10.2026): вырезки из ГОСТ Р 72182 и IPC/WHMA-A-620E,
которые карточка «Типовые дефекты» ЭТК показывает вместо своего SVG, с подписью источника.

Рисунки лежат в базе ERP (таблица `techcard_std_figures`, migration 0148), не в git: репозиторий публичный, а
вырезки — чужой текст и графика. Загружает их скрипт `scripts/figures/upload_standard_figures.py`, набор строки СТО
заменяется целиком (PUT) и удаляется целиком.

Рисунки IPC — только для внутреннего экрана ЭТК и цеховой печати; в комплект ТД заказчику идут только рисунки ГОСТ
(`customer_allowed`). Что за картинка — решают байты (PNG / JPEG), размеры читаются из заголовка файла."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from io import BytesIO
from typing import Literal
from uuid import UUID, uuid4

from PIL import Image, UnidentifiedImageError

from app.shared.file_types import sniffed_type

from .td_sto import sto_row_codes

Role = Literal["norm", "defect", "ref"]
SourceKind = Literal["gost", "ipc"]

ROLES: tuple[str, ...] = ("norm", "defect", "ref")
SOURCE_KINDS: tuple[str, ...] = ("gost", "ipc")
CONTENT_TYPES: tuple[str, ...] = ("image/png", "image/jpeg")
MAX_FIGURE_BYTES = 2 * 1024 * 1024
MAX_FIGURES_PER_CODE = 6
MAX_SIDE_PX = 10_000
CAPTION_MAX = 255
_PIL_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg"}


class StdFigureError(Exception):
    status_code = 422

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class StdFigureNotFound(StdFigureError):
    status_code = 404


@dataclass(frozen=True)
class StdFigure:
    id: UUID
    sto_code: str
    position: int
    role: str
    source_kind: str
    caption: str
    content_type: str
    sha256: str
    width: int
    height: int
    size: int
    created_at: datetime
    created_by: str

    @property
    def customer_allowed(self) -> bool:
        """Рисунок можно печатать в комплект ТД заказчику: только ГОСТ, IPC — для цеха."""
        return self.source_kind == "gost"

    def view(self) -> dict:
        return {
            "id": str(self.id), "sto_code": self.sto_code, "position": self.position, "role": self.role,
            "source_kind": self.source_kind, "caption": self.caption, "content_type": self.content_type,
            "sha256": self.sha256, "width": self.width, "height": self.height, "size": self.size,
            "customer_allowed": self.customer_allowed, "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
        }


@dataclass(frozen=True)
class FigureUpload:
    role: str
    source_kind: str
    caption: str
    content: bytes
    declared_type: str = ""  # by the file extension; the bytes decide, a mismatch is refused


def image_size(content: bytes) -> tuple[str, int, int]:
    """(тип, ширина, высота) картинки PNG или JPEG по её байтам; иначе — ошибка с русским текстом."""
    kind = sniffed_type(content)
    if kind not in CONTENT_TYPES:
        raise StdFigureError("Рисунок стандарта — картинка PNG или JPEG")
    try:
        with Image.open(BytesIO(content)) as image:
            image_format, (width, height) = image.format, image.size
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
        raise StdFigureError("Картинка повреждена: PNG или JPEG не читается") from error
    if _PIL_FORMATS.get(str(image_format)) != kind:
        raise StdFigureError("Рисунок стандарта — картинка PNG или JPEG")
    if not (0 < width <= MAX_SIDE_PX and 0 < height <= MAX_SIDE_PX):
        raise StdFigureError(f"Размер картинки {width}×{height} пикселей вне допустимого (до {MAX_SIDE_PX})")
    return kind, width, height


def check_code(sto_code: str) -> str:
    code = str(sto_code or "").strip()
    if code not in sto_row_codes():
        raise StdFigureError(f"Строки СТО «{code}» нет в СТО ФРАК-001-2026")
    return code


def prepare_figures(sto_code: str, uploads: list[FigureUpload], *, actor: str, now: datetime) -> list[StdFigure]:
    """Новый набор рисунков строки СТО: порядок списка — порядок показа (position с 1)."""
    code = check_code(sto_code)
    if not uploads:
        raise StdFigureError("Нет рисунков: чтобы убрать рисунки строки СТО, удалите их")
    if len(uploads) > MAX_FIGURES_PER_CODE:
        raise StdFigureError(f"У строки СТО не больше {MAX_FIGURES_PER_CODE} рисунков, прислано {len(uploads)}")
    figures = []
    for position, upload in enumerate(uploads, start=1):
        if upload.role not in ROLES or upload.source_kind not in SOURCE_KINDS:
            raise StdFigureError(f"Рисунок {position}: неизвестная роль или источник")
        caption = " ".join(str(upload.caption or "").split())
        if not caption or len(caption) > CAPTION_MAX:
            raise StdFigureError(f"Рисунок {position}: подпись источника обязательна (до {CAPTION_MAX} знаков)")
        if len(upload.content) > MAX_FIGURE_BYTES:
            raise StdFigureError(f"Рисунок {position}: больше 2 МБ")
        try:
            kind, width, height = image_size(upload.content)
        except StdFigureError as error:
            raise StdFigureError(f"Рисунок {position}: {error.detail}") from error
        if upload.declared_type and upload.declared_type != kind:
            raise StdFigureError(f"Рисунок {position}: заявлен {upload.declared_type}, а по содержимому это {kind}")
        figures.append(StdFigure(
            id=uuid4(), sto_code=code, position=position, role=upload.role, source_kind=upload.source_kind,
            caption=caption, content_type=kind, sha256=sha256(upload.content).hexdigest(), width=width,
            height=height, size=len(upload.content), created_at=now, created_by=actor,
        ))
    return figures
