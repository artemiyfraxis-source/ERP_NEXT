"""Выгрузка корпуса сборок из ERP в docs/assembly-corpus/<drawing-id>/ (spec.md, draft.json, final.json, lesson.md).

Читает GET /api/techcards/assembly/corpus токеном с областью «Схемы сборки» (assembly). Только stdlib, Python 3.12.

    ERP_API_TOKEN=…  python scripts/assembly-corpus/export.py --dry-run
    python scripts/assembly-corpus/export.py [--project 708К.4] [--q коакс]

Переменные: ERP_API_TOKEN (обязательна, не печатается), ERP_BASE_URL (по умолчанию сервер мастерской).
Пишет только внутри docs/assembly-corpus; файлы с тем же содержимым не трогает; переводы строк — LF.
Имя папки: проект и КБ транслитом (`708К.4` `КБ1` → `708k-4-kb1`); у второго и следующих образцов той же
позиции — суффикс `-r2`, `-r3` (по дате создания черновика)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping

DEFAULT_BASE_URL = "https://erp-server.tail811f20.ts.net"
CORPUS_PATH = "/api/techcards/assembly/corpus"
CORPUS_ROOT = Path(__file__).resolve().parents[2] / "docs" / "assembly-corpus"
FILES = ("spec.md", "draft.json", "final.json", "lesson.md")

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


class ExportError(RuntimeError):
    """Что-то не так с запросом или ответом; сообщение — для человека."""


def slug(text: str) -> str:
    """Латиница, цифры и дефисы в нижнем регистре (README «Именование»)."""
    latin = "".join(_TRANSLIT.get(char, char) for char in str(text or "").lower())
    return re.sub(r"[^a-z0-9]+", "-", latin).strip("-")


def drawing_ids(items: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """id черновика → имя папки. Образцы одной позиции по дате создания: первый — без суффикса, дальше -r2, -r3…"""
    ordered = sorted(items, key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))
    seen: dict[str, int] = {}
    result: dict[str, str] = {}
    for item in ordered:
        base = slug(f"{item.get('project') or ''}-{item.get('item_index') or ''}")
        if not base:
            base = "draft-" + slug(str(item.get("id") or ""))[:8]
        seen[base] = seen.get(base, 0) + 1
        result[str(item["id"])] = base if seen[base] == 1 else f"{base}-r{seen[base]}"
    return result


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _assembly_name(item: Mapping[str, Any]) -> str:
    for key in ("final_config", "draft_config"):
        config = item.get(key)
        if isinstance(config, Mapping) and str(config.get("assemblyName") or "").strip():
            return str(config["assemblyName"]).strip()
    return ""


def _title(item: Mapping[str, Any]) -> str:
    name = _assembly_name(item)
    return f"{item.get('project') or ''} {item.get('item_index') or ''}".strip() + (f" — {name}" if name else "")


def spec_text(item: Mapping[str, Any]) -> str:
    """spec.md: заголовок, откуда образец, и записка агента о чертеже (поле note)."""
    lines = [
        f"# {_title(item)}", "",
        f"- Черновик: `{item.get('id')}` (источник {item.get('source') or '—'}, автор {item.get('author') or '—'})",
        f"- Ревизия техкарты после правки мастера: {item.get('final_revision') or '—'}",
        f"- Чертёж: {item.get('drawing_url') or 'не приложен'}",
        "", "## Что говорит чертёж", "",
        str(item.get("note") or "").strip() or "Записки к черновику нет.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def lesson_text(item: Mapping[str, Any]) -> str:
    lesson = str(item.get("lesson") or "").strip()
    return f"# Урок: {_title(item)}\n\n{lesson or 'Урок ещё не записан.'}\n"


def plan_export(items: Iterable[Mapping[str, Any]]) -> tuple[dict[str, str], list[str]]:
    """Образцы корпуса → {относительный путь: текст файла} и список пропущенных (с причиной)."""
    items = [item for item in items if isinstance(item, Mapping) and item.get("id")]
    names = drawing_ids(items)
    files: dict[str, str] = {}
    skipped: list[str] = []
    for item in items:
        folder = names[str(item["id"])]
        draft, final = item.get("draft_config"), item.get("final_config")
        if not isinstance(draft, Mapping) or not isinstance(final, Mapping):
            skipped.append(f"{folder}: нет пары черновик/итог — пропущен")
            continue
        files[f"{folder}/spec.md"] = spec_text(item)
        files[f"{folder}/draft.json"] = _json_text(draft)
        files[f"{folder}/final.json"] = _json_text(final)
        files[f"{folder}/lesson.md"] = lesson_text(item)
    return files, skipped


def target(root: Path, relative: str) -> Path:
    """Путь внутри корпуса; всё, что выходит за его пределы, — ошибка."""
    root = root.resolve()
    path = (root / relative).resolve()
    if path == root or root not in path.parents or path.name not in FILES:
        raise ExportError(f"Путь «{relative}» вне docs/assembly-corpus — не пишу")
    return path


def write_files(root: Path, files: Mapping[str, str], *, dry_run: bool) -> list[str]:
    """Записать (или в dry-run — только описать) файлы; одинаковые не трогать. Возвращает строки отчёта."""
    report: list[str] = []
    for relative in sorted(files):
        path = target(root, relative)
        text = files[relative]
        if path.exists():
            if path.read_bytes() == text.encode("utf-8"):
                report.append(f"без изменений  {relative}")
                continue
            action = "обновить" if dry_run else "обновлён"
        else:
            action = "создать " if dry_run else "создан  "
        report.append(f"{action}  {relative}")
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="") as handle:
                handle.write(text)
    return report


def fetch_corpus(base_url: str, token: str, project: str = "", query: str = "") -> list[dict[str, Any]]:
    params = {key: value for key, value in (("project", project), ("q", query)) if value}
    url = base_url.rstrip("/") + CORPUS_PATH + (f"?{urllib.parse.urlencode(params)}" if params else "")
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        hint = {401: "токен не принят (нет, истёк или отозван)", 403: "у токена нет области «Схемы сборки»"}
        raise ExportError(f"ERP ответил {error.code}: {hint.get(error.code, detail)}") from error
    except urllib.error.URLError as error:
        raise ExportError(f"ERP недоступен по {base_url}: {error.reason}") from error
    items = payload.get("items") if isinstance(payload, Mapping) else None
    if not isinstance(items, list):
        raise ExportError("Ответ ERP не похож на корпус: нет списка items")
    return items


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Выгрузить корпус сборок из ERP в docs/assembly-corpus")
    parser.add_argument("--dry-run", action="store_true", help="только показать, что будет записано")
    parser.add_argument("--project", default="", help="только образцы этого проекта")
    parser.add_argument("--q", default="", help="подстрока урока, записки, позиции или названия")
    args = parser.parse_args(argv)
    token = os.environ.get("ERP_API_TOKEN", "").strip()
    if not token:
        print("Нет ERP_API_TOKEN: выпустите токен с областью «Схемы сборки» и положите его в переменную окружения.")
        return 2
    base_url = os.environ.get("ERP_BASE_URL", "").strip() or DEFAULT_BASE_URL
    try:
        items = fetch_corpus(base_url, token, args.project, args.q)
        files, skipped = plan_export(items)
        report = write_files(CORPUS_ROOT, files, dry_run=args.dry_run)
    except ExportError as error:
        print(error)
        return 1
    for line in [*report, *skipped]:
        print(line)
    print(f"Образцов: {len(files) // len(FILES)}{' (dry-run, ничего не записано)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
