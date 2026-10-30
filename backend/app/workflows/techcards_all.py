"""Техкарты по проектам ERP (владелец 29.09.2026: «отгенерировать ТК по проектам, что заведены … отгруженные не
трогай и холд тоже»). Шаги повторяемые:

    python -m app.workflows.techcards_all generate [--dry-run] [--project №]    # «Создать техкарты» сборкам
    python -m app.workflows.techcards_all refresh [--dry-run]                   # пересборка ЭТК старого сборщика

С 30.09.2026 скрипты обновления и загрузки эти шаги не зовут: владелец снёс проекты и заводит их сам или по одному
(`scripts/projects_import.py load` делает генерацию только для загружаемого проекта). Без --project шаг идёт по всем
проектам базы — только руками.

``generate`` — то, что человек делал бы кнопкой: открыть «Генератор» сборки и нажать «Создать». Форму генератора
исполняет её же код (frontend/src/tcGenBatch.ts, собранный ``npm run build:tcgen``) в Node.js; сюда приходит готовый
config, ЭТК собирается тем же `auto_build_project_techcard` и сразу выдаётся, как кнопкой «Создать
техкарты»: выданную технолог смотрит и правит по операциям, проверку отмечает в плане. Не выдаётся (остаётся
черновиком, в итоге — почему) ЭТК, которой не хватает длины резки. Пропускаются отгруженные проекты и КБ, проекты с
блокером ХОЛД и со статусом Отказ / Слился / Отменён, проекты с ручными ТК и сборки с выданной ЭТК; черновик
пересобирается. Длины резки и лужение генератор берёт из листа КБn расчёта, если ключ Google задан; без ключа —
только из состава.

В конце печатается итог по-русски. Код выхода: 0 — всё сделано; 1 — часть сборок не сделана (список в итоге);
2 — нет базы или собранного генератора/Node.js.
"""

from __future__ import annotations

import argparse
import json
import os
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
from app.modules.references.application import coax_dia_rows
from app.modules.references.domain import CABLE_TABS, TERMINAL_TABS, TechOperationTab
from app.modules.references.wire_dia import read_wire_dia
from app.modules.requests.application import CalcSheetError
from app.modules.requests.domain import spreadsheet_id_from_url
from app.modules.techcards.application import (
    AutoBuildProjectTechcardCommand,
    ChangeProjectTechcardCommand,
    CreateProjectTechcardCommand,
    auto_build_project_techcard,
    create_project_techcard,
    issue_project_techcard,
)
from app.modules.techcards.etk_builder import ETK_REBUILD_VERSION
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


SHIPPED = "Отгружено"
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
        stage, _blocker = stages.get(kitset.project, (None, ""))
        status = stage.status if stage is not None else ""
        if status == SHIPPED:
            report.skip("проект отгружен", kitset.project)
            continue
        if status in TERMINAL_STATUSES:
            report.skip(f"проект в статусе «{status}»", kitset.project)
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
    """Справочники генератора как их читает форма (`loadRefs`): шаблоны техкарт, БД.ОП/ТЕР/КОАКС, СПР.КАБ · коаксиал
    со строкой на каждое другое имя кабеля (`coax_dia_rows`, GET /api/references/coax-dia) и «Ø изоляции по маркам» из
    базы монтажных проводов (`wire_dia`, GET /api/references/wire-dia) и БД.ПРОВ со списком кабелей (`prov`)."""
    with techcards_factory() as uow:
        templates = [{"id": card.source_id, "title": card.title, "category": card.category}
                     for card in uow.operation_cards.list()]
    tabs: dict[str, list] = {tab: [] for tab in ("op", "ter", "coax", "kab_coax")}
    wire_dia: list[dict[str, str]] = []
    prov: list[dict] = []
    if references_factory is not None:
        with references_factory() as uow:
            for tab in tabs:
                records = uow.tech_operations.list(tab=TERMINAL_TABS if tab == "ter" else TechOperationTab(tab), query="",
                                                    limit=5000)
                tabs[tab] = ([{"id": str(item.id), "fields": item.fields} for item in records] if tab == "ter"
                             else [item.fields for item in records])
            tabs["kab_coax"] = coax_dia_rows(uow)
            wire_dia = read_wire_dia(uow)
            # БД.ПРОВ с её списком кабелей (`cable_kind`): монтажный/многожильный/LAN/коаксиал решает форма так же, как в браузере.
            prov = [{"cable_kind": item.cable_kind, "fields": item.fields}
                    for item in uow.tech_operations.list(tab=CABLE_TABS, query="", limit=5000)]
    return {"templates": templates, **tabs, "wire_dia": wire_dia, "prov": prov}


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
    actor: Actor, now: datetime, apply: bool, only: set[str] | None = None,
) -> Report:
    """only — номера проектов (`project_key`), которыми ограничиться: загрузка по одному (projects_import.py load)."""
    report = Report()
    techcards = state.techcards_uow_factory
    with techcards() as uow:
        kitsets = [kitset for kitset in uow.kitsets.list() if only is None or project_key(kitset.project) in only]
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
                card_id=card.id, expected_version=card.version, config=config, inputs=result["config"], actor=actor, now=now,
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


def refresh(state: Any, *, actor: Actor, now: datetime, apply: bool) -> Report:
    """Issued generator ETKs built by an older builder (document schema_version < ETK_REBUILD_VERSION) are rebuilt
    from their own saved build_config: a new revision «Автопересборка», hand-edited operations kept (manual keys).
    A rerun finds nothing to do. Version 3 only added `route`, derived on read for older documents, so it alone makes
    no new revision."""
    report = Report()
    techcards = state.techcards_uow_factory
    with techcards() as uow:
        cards = list(uow.techcards.list())
    for card in cards:
        label = _label(card.project, card.item_index)
        document = card.document if isinstance(card.document, dict) else {}
        config = document.get("build_config")
        if card.status is not TechcardStatus.ISSUED or not isinstance(config, dict):
            continue
        if int(document.get("schema_version") or 0) >= ETK_REBUILD_VERSION:
            continue
        if not apply:
            report.done.append(label)
            continue
        try:
            built_config, technology_source = with_technology(config, getattr(state, "references_uow_factory", None))
            built = auto_build_project_techcard(AutoBuildProjectTechcardCommand(
                card_id=card.id, expected_version=card.version, config=built_config, inputs=config, actor=actor, now=now,
                technology_source=technology_source,
            ), techcards())
        except TechcardValidationError as error:
            report.failed.append(f"{label}: {error}")
            continue
        report.done.append(f"{label} — ревизия {built.revision}")
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
    parser = argparse.ArgumentParser(description="Техкарты по всем проектам ERP: генерация и пересборка.")
    parser.add_argument("step", choices=("generate", "refresh"))
    parser.add_argument("--dry-run", action="store_true", help="только показать, что будет сделано")
    parser.add_argument("--project", action="append", default=[], help="только этот № проекта (можно несколько)")
    args = parser.parse_args(argv)
    only = {project_key(project) for project in args.project} or None
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
    actor = Actor(id=uuid.uuid4(), name="пакетная генерация ТК", department="")
    try:
        if args.step == "refresh":
            report = refresh(holder.state, actor=actor, now=moscow_now(), apply=not args.dry_run)
            print(format_report("Пересборка выданных ЭТК новым генератором", report, apply=not args.dry_run,
                                done_title="Пересобрано"))
        else:
            report = generate(holder.state, node_generator(), _google_reader(),
                              actor=actor, now=moscow_now(), apply=not args.dry_run, only=only)
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
