"""Техкарты по всем проектам ERP разом (владелец 29.09.2026: «отгенерировать ТК по проектам, что заведены …
отгруженные не трогай и холд тоже»). Два шага, оба повторяемые:

    python -m app.workflows.techcards_all supplymap [--dry-run]   # выданные в SUPPLYMAP ТК → выданные ЭТК ERP
    python -m app.workflows.techcards_all generate [--dry-run]    # «Создать техкарты» для остальных сборок

``supplymap`` читает лист «Техкарты» таблицы «ERP — Производство» (Google только читается, ключ erp-reader из
``ERP_GOOGLE_SERVICE_ACCOUNT_JSON``). Маршрут каждого выданного там изделия (ИЗДЕЛИЕ = проект + КБ, как
`tcSplitItem_`) становится выданной ЭТК сборки: этапы, коды операций, времена и ссылка на файл ТК. Сборка, у
которой ЭТК уже выдана в ERP, не трогается; несобранный черновик уступает место выданной ТК.

``generate`` — то, что человек делал бы кнопкой: открыть «Генератор» сборки и нажать «Создать». Форму генератора
исполняет её же код (frontend/src/tcGenBatch.ts, собранный ``npm run build:tcgen``) в Node.js; сюда приходит готовый
config, ЭТК собирается тем же `auto_build_project_techcard` и сразу выдаётся, как кнопкой (SUPPLYMAP «Создать
техкарты»): выданную технолог смотрит и правит по операциям, проверку отмечает в плане. Не выдаётся (остаётся
черновиком, в итоге — почему) ЭТК, которой не хватает длины резки. Пропускаются отгруженные проекты и КБ, проекты с
блокером ХОЛД и со статусом Отказ / Слился / Отменён, проекты с ручными ТК и сборки с выданной ЭТК; черновик
пересобирается. Длины резки и лужение генератор берёт из листа КБn расчёта, если ключ Google задан; без ключа —
только из состава.

В конце печатается итог по-русски. Код выхода: 0 — всё сделано; 1 — часть сборок не сделана (список в итоге);
2 — нет базы, ключа (для supplymap) или собранного генератора/Node.js.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from app.modules.production.plan import MANUAL_TECHCARD_PROJECTS, normalize_project_number
from app.modules.references.domain import TechOperationTab
from app.modules.requests.application import CalcSheetError
from app.modules.requests.domain import spreadsheet_id_from_url
from app.modules.techcards.application import (
    AutoBuildProjectTechcardCommand,
    ChangeProjectTechcardCommand,
    CreateProjectTechcardCommand,
    ImportIssuedTechcardCommand,
    auto_build_project_techcard,
    create_project_techcard,
    import_issued_techcard,
    issue_project_techcard,
)
from app.modules.techcards.kitsets import KitAssembly, Kitset, item_id, project_key
from app.modules.techcards.project_cards import ProjectTechcard, TechcardStatus, TechcardValidationError
from app.shared.clock import moscow_now
from app.shared.identity import Actor
from app.workflows.project_kitset import (
    TERMINAL_STATUSES,
    calc_ops_from_grid,
    generator_bom,
    kitset_facts,
    kitset_stages,
)
from app.workflows.techcard_build import with_technology


PROD_SPREADSHEET = "1cxYr4lVHUI0RNw3eBn5cKOTus5SdYAJeA043LCUBj0w"  # «ERP — Производство» (SUPPLYMAP SS_PROD)
TECHCARDS_SHEET = "Техкарты"
SHIPPED = "Отгружено"
HOLD = "ХОЛД"
IMPORT_REASON = "Перенос выданной ТК из SUPPLYMAP"
# Слова формы генератора, которые в итоге без формы не понять.
ERROR_HINTS = {
    "Выберите шаблон хотя бы для одной операции.":
        "генератору не из чего собрать операции — в составе нет проводов, разъёмов или контактов",
}
GENERATOR_SCRIPT = Path(__file__).resolve().parents[3] / "frontend" / "dist-tools" / "tcGenBatchCli.js"


class SheetsReader(Protocol):
    """What the steps need from Google: `GoogleSheetsCalcReader.read_sheets`."""

    def read_sheets(self, spreadsheet_id: str, names: tuple[str, ...]) -> tuple[str, dict[str, list[list[str]]]]: ...


# ── Какие сборки ─────────────────────────────────────────────────────────────


@dataclass
class Report:
    done: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def skip(self, reason: str, label: str) -> None:
        self.skipped.setdefault(reason, []).append(label)


def _label(project: str, index: str = "") -> str:
    return f"{project} {index}".strip()


def _card_key(project: str, index: str) -> tuple[str, str]:
    return project_key(project), str(index or "").strip().upper()


def generation_scope(
    kitsets: Iterable[Kitset], stages: Mapping[str, tuple[Any, str]], cards: Iterable[ProjectTechcard], report: Report,
) -> list[tuple[Kitset, KitAssembly, ProjectTechcard | None]]:
    """Сборки, которым нужна ЭТК; остальные — в `report.skipped` с причиной."""
    by_key = {_card_key(card.project, card.item_index): card for card in cards}
    manual = {normalize_project_number(project) for project in MANUAL_TECHCARD_PROJECTS}
    targets = []
    for kitset in kitsets:
        stage, blocker = stages.get(kitset.project, (None, ""))
        status = stage.status if stage is not None else ""
        if status == SHIPPED:
            report.skip("проект отгружен", kitset.project)
            continue
        if status in TERMINAL_STATUSES:
            report.skip(f"проект в статусе «{status}»", kitset.project)
            continue
        if blocker == HOLD:
            report.skip("проект на ХОЛДе", kitset.project)
            continue
        if normalize_project_number(kitset.project) in manual:
            report.skip("ТК проекта ведутся вручную", kitset.project)
            continue
        kit_status = {item.item_index.upper(): item.status for item in (stage.kits if stage is not None else ())}
        if not kitset.assemblies:
            report.skip("в проекте нет сборок (КБ)", kitset.project)
        for assembly in kitset.assemblies:
            label = _label(kitset.project, assembly.index)
            if kit_status.get(assembly.index.upper()) == SHIPPED:
                report.skip("КБ отгружена", label)
                continue
            card = by_key.get(_card_key(kitset.project, assembly.index))
            if card is not None and card.status is TechcardStatus.ISSUED:
                report.skip("ЭТК уже выдана", label)
                continue
            targets.append((kitset, assembly, card))
    return targets


# ── Выданные в SUPPLYMAP ТК ──────────────────────────────────────────────────


def split_item(item: object) -> tuple[str, str]:
    """`tcSplitItem_`: «708К.4КБ1» → («708К.4», «КБ1»)."""
    text = str(item or "").strip()
    match = re.match(r"^(.*?)(КБ\d+[A-Za-zА-Яа-я0-9]*)$", text, flags=re.I)
    return (match[1].strip(), match[2].upper()) if match else (text, "")


def _header(value: object) -> str:
    return " ".join(str(value or "").split()).upper()


def _seconds(value: object) -> float:
    try:
        number = float(str(value or "").replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return 0
    return number if number > 0 else 0


def supplymap_routes(grid: list[list[str]]) -> dict[str, dict[str, Any]]:
    """Лист «Техкарты» (`erpTechmapWriteToProd_`): строка = этап маршрута изделия. → {ИЗДЕЛИЕ: {name, file_url, stages}}."""
    if not grid:
        return {}
    columns = {_header(title): position for position, title in enumerate(grid[0])}

    def cell(row: list[str], title: str) -> str:
        position = columns.get(title)
        return str(row[position]).strip() if position is not None and position < len(row) else ""

    routes: dict[str, dict[str, Any]] = {}
    for row in grid[1:]:
        item = cell(row, "ИЗДЕЛИЕ")
        if not item:
            continue
        route = routes.setdefault(item, {"name": "", "file_url": "", "stages": []})
        route["name"] = route["name"] or cell(row, "НАИМЕНОВАНИЕ")
        comment = cell(row, "КОММЕНТАРИЙ")
        url = next((word for word in comment.split() if word.startswith(("http://", "https://"))), "")
        route["file_url"] = route["file_url"] or url
        route["stages"].append({
            "n": cell(row, "№ ЭТАПА"), "stage": cell(row, "ЭТАП"), "code": cell(row, "КОД ОПЕРАЦИИ"),
            "descr": cell(row, "ОПИСАНИЕ ШАГА"), "tOp": _seconds(cell(row, "ВРЕМЯ НА ЕД, СЕК")),
            "tPrep": _seconds(cell(row, "ВРЕМЯ ПОДГОТОВКИ, СЕК")),
        })
    return routes


def supplymap_document(item: str, route: Mapping[str, Any], read_at: datetime) -> dict[str, Any]:
    """Выданная ЭТК из маршрута SUPPLYMAP. `build_config.ops` несёт этапы полями генератора (stage, opNumber, descr,
    tOp, tPrep) — по ним `route_from_document` строит задания цеха, как SUPPLYMAP по листу «Техкарты». Карточек
    операций (шагов, оснастки) у такой ЭТК нет: они в файле ТК по ссылке; пересборка генератором заменит её новой
    ревизией."""
    operations, config_ops, keys = [], [], set()
    for position, stage in enumerate(route["stages"], start=1):
        number = stage["n"] or str(position)
        code = stage["code"] or "БЕЗ КОДА"
        connector = ""
        while f"{code}::{connector}" in keys:
            connector = f"этап {number}" if not connector else f"{connector}."
        keys.add(f"{code}::{connector}")
        operations.append({
            "position": number, "code": code, "connector": connector, "side": "",
            "title": stage["stage"] or stage["code"] or f"Этап {number}", "description": stage["descr"],
            "times": {"operation_seconds": stage["tOp"], "setup_seconds": stage["tPrep"]}, "card": None,
        })
        config_ops.append({
            "stage": stage["stage"], "opNumber": stage["code"], "descr": stage["descr"],
            "tOp": stage["tOp"], "tPrep": stage["tPrep"],
        })
    return {
        "source": {
            "system": "SUPPLYMAP", "sheet": TECHCARDS_SHEET, "item": item, "file_url": route["file_url"],
            "read_at": read_at.isoformat(timespec="seconds"),
        },
        "operations": operations,
        "warnings": [],
        "build_config": {"source": "SUPPLYMAP", "ops": config_ops},
    }


def import_supplymap(
    grid: list[list[str]], techcards_factory: Callable[[], Any], *, actor: Actor, now: datetime, apply: bool,
) -> Report:
    report = Report()
    with techcards_factory() as uow:
        kitsets = list(uow.kitsets.list())
        cards = {_card_key(card.project, card.item_index): card for card in uow.techcards.list()}
    by_project = {project_key(kitset.project): kitset for kitset in kitsets}
    for item, route in supplymap_routes(grid).items():
        project, index = split_item(item)
        kitset = by_project.get(project_key(project)) if index else None
        assembly = kitset.assembly(index) if kitset is not None else None
        if assembly is None:
            report.skip("проекта или КБ нет в ERP (отгружен или не заведён)", item)
            continue
        label = _label(kitset.project, assembly.index)
        card = cards.get(_card_key(kitset.project, assembly.index))
        if card is not None and card.status is TechcardStatus.ISSUED:
            report.skip("ЭТК уже выдана в ERP — не перезаписываю", label)
            continue
        stages = len(route["stages"])
        if not apply:
            report.done.append(f"{label} — этапов {stages}")
            continue
        try:
            imported = import_issued_techcard(ImportIssuedTechcardCommand(
                project=kitset.project, item_index=assembly.index,
                product_name=assembly.name or route["name"] or item_id(kitset.project, assembly.index),
                source_calculation=kitset.calc_source,
                document=supplymap_document(item, route, now), reason=IMPORT_REASON, actor=actor, now=now,
            ), techcards_factory())
        except TechcardValidationError as error:
            report.failed.append(f"{label}: {error}")
            continue
        if imported is None:
            report.skip("ЭТК уже выдана в ERP — не перезаписываю", label)
        else:
            report.done.append(f"{label} — этапов {stages}")
    return report


# ── Генерация ────────────────────────────────────────────────────────────────


def _retrying(read: Callable[[], Any]) -> Any:
    """Квота Sheets API — 60 чтений в минуту: на 429 ждём и повторяем, как снимок проектов."""
    for attempt in range(6):
        try:
            return read()
        except Exception as error:  # noqa: BLE001 — только 429 повторяем, остальное наверх
            if "429" in str(error) and attempt < 5:
                time.sleep(15 * (attempt + 1))
                continue
            raise
    return None


def calc_ops_for(kitset: Kitset, indexes: list[str], reader: SheetsReader | None, report: Report) -> dict[str, list]:
    """Строки листов КБn расчёта набора одним чтением. Ошибка чтения генерацию не останавливает: пусто, как
    `tcCalcOps_`, и строка в итоге."""
    source = str(kitset.calc_source or "")
    spreadsheet_id = spreadsheet_id_from_url(source) or source
    if reader is None or not spreadsheet_id or not indexes:
        return {}
    try:
        _title, sheets = _retrying(lambda: reader.read_sheets(spreadsheet_id, tuple(indexes)))
    except Exception as error:  # noqa: BLE001
        report.notes.append(f"{kitset.project}: расчёт не прочитан ({error}) — длины только из состава")
        return {}
    return {index: calc_ops_from_grid(sheets.get(index) or []) for index in indexes}


def generator_refs(techcards_factory: Callable[[], Any], references_factory: Callable[[], Any] | None) -> dict:
    """Справочники генератора как их читает форма (`loadRefs`): шаблоны техкарт и БД.ОП/ТЕР/КОАКС/СПР.КАБ."""
    with techcards_factory() as uow:
        templates = [{"id": card.source_id, "title": card.title, "category": card.category}
                     for card in uow.operation_cards.list()]
    tabs: dict[str, list] = {tab: [] for tab in ("op", "ter", "coax", "kab_awg", "kab_coax")}
    if references_factory is not None:
        with references_factory() as uow:
            for tab in tabs:
                records = uow.tech_operations.list(tab=TechOperationTab(tab), query="", limit=5000)
                tabs[tab] = ([{"id": str(item.id), "fields": item.fields} for item in records] if tab == "ter"
                             else [item.fields for item in records])
    return {"templates": templates, **tabs}


def node_generator(script: Path = GENERATOR_SCRIPT, node: str | None = None) -> Callable[[dict], dict]:
    """Форма генератора (tcGenBatch.ts) в Node.js: JSON на stdin → configs на stdout."""
    node = node or os.getenv("ERP_NODE") or shutil.which("node") or ""
    script = Path(os.getenv("ERP_TCGEN_SCRIPT") or script)
    if not node:
        raise GeneratorUnavailable("Node.js не найден (нужен для генератора техкарт; путь можно задать в ERP_NODE)")
    if not script.exists():
        raise GeneratorUnavailable(f"Нет собранного генератора {script} — соберите: cd frontend; npm run build:tcgen")

    def run(payload: dict) -> dict:
        done = subprocess.run(
            [node, str(script)], input=json.dumps(payload, ensure_ascii=False), capture_output=True,
            text=True, encoding="utf-8", timeout=900, check=False,
        )
        if done.returncode != 0:
            raise GeneratorUnavailable(f"Генератор техкарт упал: {(done.stderr or done.stdout).strip()[-2000:]}")
        return json.loads(done.stdout)

    return run


class GeneratorUnavailable(RuntimeError):
    pass


def generate(
    state: Any, generator: Callable[[dict], dict], reader: SheetsReader | None, *,
    actor: Actor, now: datetime, apply: bool,
) -> Report:
    report = Report()
    techcards = state.techcards_uow_factory
    with techcards() as uow:
        kitsets = list(uow.kitsets.list())
    facts = kitset_facts(
        techcards, getattr(state, "production_uow_factory", None),
        getattr(state, "fulfillment_uow_factory", None), getattr(state, "shipping_uow_factory", None),
    )
    targets = generation_scope(kitsets, kitset_stages(kitsets, facts), facts.cards, report)
    if not targets:
        return report
    if reader is None:
        report.notes.append("Ключ Google не задан: длины резки и лужение из листов КБn расчёта не читались")
    items, by_key = [], {}
    by_kitset: dict[str, list[tuple[Kitset, KitAssembly, ProjectTechcard | None]]] = {}
    for target in targets:
        by_kitset.setdefault(target[0].project, []).append(target)
    for group in by_kitset.values():
        kitset = group[0][0]
        calc = calc_ops_for(kitset, [assembly.index for _, assembly, _ in group], reader, report)
        for _, assembly, card in group:
            key = f"{len(items)}"
            items.append({"key": key, "bom": generator_bom(kitset, assembly, calc.get(assembly.index, []))})
            by_key[key] = (kitset, assembly, card)
    results = generator({"refs": generator_refs(techcards, getattr(state, "references_uow_factory", None)),
                         "items": items})["items"]
    for result in results:
        kitset, assembly, card = by_key[result["key"]]
        label = _label(kitset.project, assembly.index)
        if result.get("error") or not result.get("config"):
            error = result.get("error") or "генератор не вернул конфигурацию"
            report.failed.append(f"{label}: {ERROR_HINTS.get(error, error)}")
            continue
        warned = len(result.get("warnings") or [])
        if not apply:
            report.done.append(f"{label}" + (f" — предупреждений генератора {warned}" if warned else ""))
            continue
        try:
            if card is None:
                card = create_project_techcard(CreateProjectTechcardCommand(
                    project=kitset.project, item_index=assembly.index,
                    product_name=assembly.name or item_id(kitset.project, assembly.index),
                    source_calculation=kitset.calc_source, deadline=None, actor=actor, now=now,
                ), techcards())
            config, technology_source = with_technology(result["config"], getattr(state, "references_uow_factory", None))
            built = auto_build_project_techcard(AutoBuildProjectTechcardCommand(
                card_id=card.id, expected_version=card.version, config=config, actor=actor, now=now,
                technology_source=technology_source,
            ), techcards())
        except TechcardValidationError as error:
            report.failed.append(f"{label}: {error}")
            continue
        document = built.document or {}
        warnings = len(document.get("warnings") or [])
        summary = (f"операций {len(document.get('operations') or [])}"
                   + (f", предупреждений {warnings}" if warnings else ""))
        try:
            issue_project_techcard(ChangeProjectTechcardCommand(
                card_id=built.id, expected_version=built.version, actor=actor, now=now,
            ), techcards())
        except TechcardValidationError as error:
            report.failed.append(f"{label}: собран черновик ({summary}), не выдан — {error}")
            continue
        report.done.append(f"{label} — {summary}")
    return report


# ── CLI ──────────────────────────────────────────────────────────────────────


def format_report(title: str, report: Report, *, apply: bool, done_title: str) -> str:
    lines = [f"== {title}"]
    lines.append(f"{done_title}: {len(report.done)}" + ("" if apply else " (пробный прогон, в базу не записано)"))
    lines += [f"  {item}" for item in report.done]
    if report.failed:
        lines.append(f"Не сделано — не хватило данных: {len(report.failed)}")
        lines += [f"  {item}" for item in report.failed]
    skipped = sum(len(items) for items in report.skipped.values())
    if skipped:
        lines.append(f"Пропущено: {skipped}")
        for reason, items in report.skipped.items():
            lines.append(f"  {reason}: {len(items)} — {', '.join(items)}")
    lines += [f"Примечание: {note}" for note in report.notes]
    return "\n".join(lines)


def _google_reader() -> SheetsReader | None:
    from app.modules.requests.google_sheets import ENV_VAR, reader_from_env

    return reader_from_env() if os.getenv(ENV_VAR, "").strip() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Техкарты по всем проектам ERP: перенос выданных из SUPPLYMAP и генерация остальных.")
    parser.add_argument("step", choices=("supplymap", "generate"))
    parser.add_argument("--dry-run", action="store_true", help="только показать, что будет сделано")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from types import SimpleNamespace

    from app.database import configure_database

    holder = SimpleNamespace(state=SimpleNamespace())
    engine = configure_database(holder, url)
    actor = Actor(id=uuid.uuid4(), name="пакетная генерация ТК" if args.step == "generate" else "перенос из SUPPLYMAP", department="")
    try:
        if args.step == "supplymap":
            reader = _google_reader()
            if reader is None:
                print("Ключ Google не задан (ERP_GOOGLE_SERVICE_ACCOUNT_JSON) — выданные ТК SUPPLYMAP не перенесены", file=sys.stderr)
                return 2
            _title, sheets = _retrying(lambda: reader.read_sheets(PROD_SPREADSHEET, (TECHCARDS_SHEET,)))
            if TECHCARDS_SHEET not in sheets:
                print(f"В «ERP — Производство» нет листа «{TECHCARDS_SHEET}»", file=sys.stderr)
                return 2
            report = import_supplymap(sheets[TECHCARDS_SHEET], holder.state.techcards_uow_factory,
                                      actor=actor, now=moscow_now(), apply=not args.dry_run)
            print(format_report("Выданные ТК из SUPPLYMAP", report, apply=not args.dry_run, done_title="Перенесено"))
        else:
            report = generate(holder.state, node_generator(), _google_reader(),
                              actor=actor, now=moscow_now(), apply=not args.dry_run)
            print(format_report("Генерация ТК по проектам", report, apply=not args.dry_run,
                                done_title="Собрано и выдано ЭТК"))
    except (GeneratorUnavailable, CalcSheetError) as error:
        print(str(error), file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
