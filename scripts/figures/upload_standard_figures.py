"""Загрузка рисунков стандартов (вырезки ГОСТ Р 72182 и IPC/WHMA-A-620E) к строкам СТО ФРАК-001-2026 в ERP.

Папка с вырезками лежит вне репозитория (репозиторий публичный — картинки ГОСТ/IPC в git не кладём): manifest.json и
файлы рисунков. Строка манифеста: {code, index, role: norm|defect|ref, kind: gost|ipc, caption, file, …}. Рисунки
строки СТО идут по `index`; на каждую строку СТО — один PUT /api/techcards/std-figures/{code}, который заменяет её
рисунки целиком. Тип файла — по расширению (.png — image/png, .jpg/.jpeg — image/jpeg); сервер сверяет его с байтами.

По умолчанию — проверка без отправки (dry-run): что и сколько будет загружено, и ошибки манифеста.

    python scripts/figures/upload_standard_figures.py <папка> --base-url http://127.0.0.1:8021
    ERP_API_TOKEN=…  python scripts/figures/upload_standard_figures.py <папка> --base-url URL --apply

ERP_API_TOKEN — токен API с областью «Загрузка рисунков стандартов» (figures:write); берётся только из переменной
окружения, не печатается. Только stdlib, Python 3.12."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PUT_PATH = "/api/techcards/std-figures/"
CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
ROLES = ("norm", "defect", "ref")
KINDS = ("gost", "ipc")
MAX_BYTES = 2 * 1024 * 1024
MAX_PER_CODE = 6


class UploadError(RuntimeError):
    """Что-то не так с манифестом, файлом или ответом ERP; сообщение — для человека."""


@dataclass(frozen=True)
class Figure:
    code: str
    index: int
    role: str
    kind: str
    caption: str
    path: Path
    content_type: str
    size: int


def read_manifest(folder: Path) -> list[dict[str, Any]]:
    manifest = folder / "manifest.json"
    if not manifest.is_file():
        raise UploadError(f"Нет файла {manifest}")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    entries = data.get("figures") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise UploadError("manifest.json: ожидается список рисунков")
    return entries


def plan(folder: Path, entries: list[dict[str, Any]]) -> tuple[dict[str, list[Figure]], list[str]]:
    """Рисунки по строкам СТО (по `index`) и ошибки; строка СТО с ошибкой в план не входит."""
    groups: dict[str, list[Figure]] = defaultdict(list)
    problems: list[str] = []
    broken: set[str] = set()
    for number, entry in enumerate(entries, start=1):
        code = str(entry.get("code") or "").strip()
        where = f"строка {number} ({code or 'без кода'})"
        if not code:
            problems.append(f"{where}: нет кода строки СТО")
            continue
        file_name = str(entry.get("file") or "")
        path = folder / file_name
        content_type = CONTENT_TYPES.get(path.suffix.lower(), "")
        errors = []
        if str(entry.get("role")) not in ROLES:
            errors.append(f"роль «{entry.get('role')}» не из {', '.join(ROLES)}")
        if str(entry.get("kind")) not in KINDS:
            errors.append(f"источник «{entry.get('kind')}» не из {', '.join(KINDS)}")
        if not str(entry.get("caption") or "").strip():
            errors.append("нет подписи источника")
        if not content_type:
            errors.append(f"файл «{file_name}» не .png / .jpg")
        elif not path.is_file():
            errors.append(f"нет файла «{file_name}»")
        elif path.stat().st_size > MAX_BYTES:
            errors.append(f"файл «{file_name}» больше 2 МБ")
        try:
            index = int(entry.get("index"))
        except (TypeError, ValueError):
            errors.append("index не число")
            index = 0
        if errors:
            problems.append(f"{where}: {'; '.join(errors)}")
            broken.add(code)
            continue
        groups[code].append(Figure(
            code=code, index=index, role=str(entry["role"]), kind=str(entry["kind"]),
            caption=" ".join(str(entry["caption"]).split()), path=path, content_type=content_type,
            size=path.stat().st_size,
        ))
    for code, figures in groups.items():
        figures.sort(key=lambda figure: figure.index)
        indexes = [figure.index for figure in figures]
        if len(set(indexes)) != len(indexes):
            problems.append(f"{code}: повторяется index")
            broken.add(code)
        if len(figures) > MAX_PER_CODE:
            problems.append(f"{code}: рисунков {len(figures)}, больше {MAX_PER_CODE}")
            broken.add(code)
    ready = {code: groups[code] for code in sorted(groups, key=_code_key) if code not in broken}
    return ready, problems


def _code_key(code: str) -> tuple:
    return tuple(int(part) if part.isdigit() else 0 for part in code.split("."))


def payload(figures: list[Figure]) -> dict[str, Any]:
    return {"figures": [{
        "role": figure.role, "source_kind": figure.kind, "caption": figure.caption, "content_type": figure.content_type,
        "content_base64": base64.b64encode(figure.path.read_bytes()).decode("ascii"),
    } for figure in figures]}


def summary_line(code: str, figures: list[Figure]) -> str:
    roles = {"norm": "норма", "defect": "брак", "ref": "справка"}
    kinds = {"gost": "ГОСТ", "ipc": "IPC"}
    parts = ", ".join(f"{roles[figure.role]} ({kinds[figure.kind]})" for figure in figures)
    return f"{code:<10} {len(figures)} рис. · {sum(figure.size for figure in figures) / 1024:,.0f} КБ · {parts}"


def put_code(base_url: str, token: str, code: str, figures: list[Figure]) -> int:
    body = json.dumps(payload(figures)).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + PUT_PATH + urllib.parse.quote(code, safe=""), data=body, method="PUT",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        hint = {401: "токен не принят (нет, истёк или отозван)", 403: "у токена нет области «Загрузка рисунков стандартов»"}
        raise UploadError(f"ERP ответил {error.code}: {hint.get(error.code, detail)}") from error
    except urllib.error.URLError as error:
        raise UploadError(f"ERP недоступен по {base_url}: {error.reason}") from error
    return len(answer.get("items") or [])


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Загрузить рисунки стандартов к строкам СТО в ERP. Без --apply — только проверка и сводка.",
    )
    parser.add_argument("folder", type=Path, help="папка с manifest.json и файлами рисунков")
    parser.add_argument("--base-url", default="", help="адрес ERP, например http://127.0.0.1:8021 (нужен с --apply)")
    parser.add_argument("--apply", action="store_true", help="отправить в ERP (по умолчанию — только проверка)")
    parser.add_argument("--code", action="append", default=[], help="только эта строка СТО (можно несколько раз)")
    args = parser.parse_args(argv)
    try:
        ready, problems = plan(args.folder, read_manifest(args.folder))
    except (UploadError, json.JSONDecodeError) as error:
        print(error)
        return 1
    if args.code:
        ready = {code: figures for code, figures in ready.items() if code in set(args.code)}
    for code, figures in ready.items():
        print(summary_line(code, figures))
    for problem in problems:
        print(f"Ошибка: {problem}")
    count = sum(len(figures) for figures in ready.values())
    size = sum(figure.size for figures in ready.values() for figure in figures)
    by_kind = {kind: sum(figure.kind == kind for figures in ready.values() for figure in figures) for kind in KINDS}
    print(f"Итого: строк СТО {len(ready)}, рисунков {count} (ГОСТ {by_kind['gost']}, IPC {by_kind['ipc']}), "
          f"{size / 1024 / 1024:.1f} МБ, ошибок {len(problems)}")
    if not args.apply:
        print("Проверка без отправки (dry-run): чтобы загрузить, добавьте --apply.")
        return 1 if problems else 0
    token = os.environ.get("ERP_API_TOKEN", "").strip()
    if not token:
        print("Нет ERP_API_TOKEN: выпустите токен с областью «Загрузка рисунков стандартов» и положите его в переменную окружения.")
        return 2
    if not args.base_url:
        print("Укажите --base-url: адрес ERP, куда загружать.")
        return 2
    failed = 0
    for code, figures in ready.items():
        try:
            stored = put_code(args.base_url, token, code, figures)
            print(f"{code}: загружено {stored}")
        except UploadError as error:
            failed += 1
            print(f"{code}: не загружено — {error}")
    print(f"Загружено строк СТО: {len(ready) - failed} из {len(ready)}")
    return 1 if failed or problems else 0


if __name__ == "__main__":
    sys.exit(main())
