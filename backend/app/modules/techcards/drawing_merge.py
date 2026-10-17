"""Несколько PDF чертежа позиции — в один файл (владелец 10.10.2026): «если несколько прикладываешь, они сшиваются».

Чистая функция без базы: сшивает в заданном порядке, возвращает байты и число страниц каждой части. Любая
часть, которую нельзя прочитать, — `DrawingMergeError` с именем файла: ничего не сохраняется, пока не прочитаны все."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader, PdfWriter


class DrawingMergeError(ValueError):
    """Сообщение по-русски, с именем файла; API отдаёт его как 422."""


@dataclass(frozen=True)
class MergedDrawing:
    content: bytes
    pages: list[tuple[str, int]]  # (имя файла, страниц) в порядке сшивки


def merge_pdfs(parts: list[tuple[str, bytes]]) -> MergedDrawing:
    if len(parts) < 2:
        raise DrawingMergeError("Для сшивки нужно хотя бы два PDF")
    writer = PdfWriter()
    pages: list[tuple[str, int]] = []
    for name, content in parts:
        if not content.startswith(b"%PDF-"):
            raise DrawingMergeError(f"Файл «{name}» не похож на PDF")
        try:
            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted:
                raise DrawingMergeError(f"Файл «{name}» защищён паролем — снимите защиту и загрузите снова")
            count = len(reader.pages)
            if count == 0:
                raise DrawingMergeError(f"В файле «{name}» нет страниц")
            for page in reader.pages:
                writer.add_page(page)
        except DrawingMergeError:
            raise
        except Exception as error:  # pypdf raises many types on damaged files
            raise DrawingMergeError(f"Файл «{name}» повреждён и не читается как PDF") from error
        pages.append((name, count))
    out = BytesIO()
    writer.write(out)
    return MergedDrawing(content=out.getvalue(), pages=pages)
