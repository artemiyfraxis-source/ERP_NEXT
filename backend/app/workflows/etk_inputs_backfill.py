"""Заполнить `inputs` / `overrides` у ЭТК, собранных до автоматических ревизий (владелец 19.10.2026).

    ERP_DATABASE_URL=... python -m app.workflows.etk_inputs_backfill            # проверка, ничего не пишет
    ERP_DATABASE_URL=... python -m app.workflows.etk_inputs_backfill --apply    # записать две колонки

У каждой карты, у которой ещё нет `inputs`:
  * входы — сохранённый `build_config` без ключей, которые дописывают справочники (`terminalNorm`/`terminalNormB`,
    `markKit`, `linearTiming`, у концов коакса `crimpHex`/`contactDepth`/`d1`–`d3`, значения из `refOwned`);
  * свежая сборка — `derive(входы, {}, …)` по текущим справочникам и шаблонам, один `load_refs` на весь прогон;
  * правки — операции, которые отличаются от свежей сборки и помечены правленными (штамп `edited` или
    `manual_operation_keys`), целиком, в формате `etk_derive` (`overrides.operations`); ручная схема ПФ
    (`route.source == 'manual'`) — через `route_override` (`overrides.route`).
Карты делятся на классы: identical (свежая == сохранённой), overrides (равна после правок), drift (не равна и после
правок — такой карте позже выпустят автоматическую ревизию), no-inputs (нет `build_config`), failed (сборка не удалась).
Равенство — по операциям (без видовых ключей, штампа `edited` и места), схеме ПФ и предупреждениям сборки.

Карта, у которой `inputs` уже есть (собрана новым кодом), не трогается: только классифицируется по своим данным.
Без `--apply` ничего не пишется, с ним — только колонки `inputs` / `overrides` (версия карты, документ и
`updated_at` прежние), ни одной ревизии. Повторный `--apply` ничего не пишет."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
import json
import os
import sys
from typing import Any

from app.modules.techcards.etk_builder import norms_include_setup
from app.modules.techcards.etk_derive import BUILD_FAILED, MISSING_OPERATION, ROUTE_NOT_APPLIED, derive, document_diff, route_override
from app.modules.techcards.etk_live import VIEW_KEYS
from app.modules.techcards.project_cards import ProjectTechcard, operation_key
from app.workflows.techcard_build import Refs, load_refs, with_technology

IDENTICAL, OVERRIDES, DRIFT, NO_INPUTS, FAILED = "identical", "overrides", "drift", "no-inputs", "failed"
CLASSES = (IDENTICAL, OVERRIDES, DRIFT, NO_INPUTS, FAILED)

_OPERATION_KEYS = ("terminalNorm", "terminalNormB", "markKit", "linearTiming")
_COAX_END_KEYS = ("crimpHex", "contactDepth", "d1", "d2", "d3")


def strip_enriched(config: Mapping[str, Any]) -> dict[str, Any]:
    """The generator config as it was before the references enriched it: copy without the keys they own."""
    inputs = deepcopy(dict(config))
    owned = inputs.pop("refOwned", None)
    for operation in inputs.get("ops") or []:
        if isinstance(operation, dict):
            for name in _OPERATION_KEYS:
                operation.pop(name, None)
    coax = inputs.get("coax")
    for side in ("sideA", "sideB"):
        end = coax.get(side) if isinstance(coax, dict) else None
        if isinstance(end, dict):
            for name in _COAX_END_KEYS:
                end.pop(name, None)
    # A value the enricher recorded is its own while it is still the recorded one; a changed one was typed by hand.
    for path, recorded in (owned.items() if isinstance(owned, dict) else ()):
        parts = str(path).split("/")
        node: Any = inputs
        for part in parts[:-1]:
            try:
                node = node[int(part)] if isinstance(node, list) else node[part]
            except (KeyError, IndexError, ValueError, TypeError):
                node = None
                break
        if isinstance(node, dict) and node.get(parts[-1]) == recorded:
            node.pop(parts[-1], None)
    return inputs


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _comparable(operation: Mapping[str, Any]) -> dict[str, Any]:
    drop = (*VIEW_KEYS, "edited", "position")
    return _plain({name: value for name, value in operation.items() if name not in drop})


def _operations(document: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    found: dict[str, Mapping[str, Any]] = {}
    for item in (document or {}).get("operations") or []:
        if isinstance(item, Mapping):
            try:
                found[operation_key(dict(item))] = item
            except Exception:  # noqa: BLE001 — an operation without a key cannot be matched
                continue
    return found


def differences(stored: Mapping[str, Any], derived: Mapping[str, Any] | None) -> list[str]:
    """What differs between the stored document and a derivation, in short Russian lines; empty — equal."""
    if derived is None:
        return ["карта не собралась"]
    before, after = _operations(stored), _operations(derived)
    lines: list[str] = []
    for key in dict.fromkeys([*before, *after]):
        if key not in after:
            lines.append(f"{key}: операции больше нет")
        elif key not in before:
            lines.append(f"{key}: новая операция")
        elif _comparable(before[key]) != _comparable(after[key]):
            fields = sorted({change["field"] for change in document_diff(
                {"operations": [before[key]]}, {"operations": [after[key]]})})
            lines.append(f"{key}: " + (", ".join(fields) if fields else "прочие поля операции"))
    for change in document_diff({"route": stored.get("route")}, {"route": derived.get("route")}):
        lines.append(change["field"])
    if document_diff({"warnings": stored.get("warnings")}, {"warnings": derived.get("warnings")}):
        lines.append("предупреждения сборки")
    return lines


def edited_overrides(card: ProjectTechcard, fresh: Mapping[str, Any]) -> dict[str, Any]:
    """Whole-operation overrides: edited-marked operations of the stored document that differ from the fresh build."""
    document = card.document or {}
    versions = (document.get("source") or {}).get("templates") or {} if isinstance(document.get("source"), Mapping) else {}
    built = _operations(fresh)
    found: dict[str, Any] = {}
    for key, stored in _operations(document).items():
        if not (stored.get("edited") or key in card.manual_operation_keys):
            continue
        if key in built and _comparable(stored) == _comparable(built[key]):
            continue
        if key not in built:
            continue  # the operation is gone from a fresh build: nothing to attach the edit to — it shows as drift
        found[key] = {
            "base_template_version": str(versions.get(str(stored.get("template_id") or "").strip(), "")),
            "operation": {name: deepcopy(value) for name, value in stored.items() if name not in VIEW_KEYS},
        }
    return found


@dataclass(frozen=True)
class CardResult:
    card: ProjectTechcard
    verdict: str
    summary: str = ""
    inputs: dict[str, Any] | None = None
    overrides: dict[str, Any] | None = None
    write: bool = False  # inputs/overrides to be stored (never for a tracked card)

    @property
    def label(self) -> str:
        return f"{self.card.project} · КБ {self.card.item_index}"


def classify(
    card: ProjectTechcard, enrich: Callable[[Mapping[str, Any]], Mapping[str, Any]], templates: Sequence[Any],
    library_commit: str, technology_source: Mapping[str, Any] | None,
) -> CardResult:
    document = card.document if isinstance(card.document, Mapping) else None
    tracked = card.inputs is not None
    config = document.get("build_config") if document else None
    if tracked:
        inputs = deepcopy(card.inputs)
    elif isinstance(config, Mapping) and config:
        inputs = strip_enriched(config)
    else:
        return CardResult(card, NO_INPUTS, "нет build_config" if document else "черновик: ЭТК ещё не собрана")
    if document is None:
        return CardResult(card, NO_INPUTS, "ЭТК не собрана")
    try:
        fresh, conflicts = derive(inputs, {}, enrich, templates, operation_library_commit=library_commit,
                                  technology_source=technology_source)
    except Exception as error:  # noqa: BLE001 — one card's derive error must not stop the batch
        return CardResult(card, FAILED, f"{type(error).__name__}: {error}")
    if fresh is None:
        return CardResult(card, FAILED, "; ".join(item["message"] for item in conflicts if item["kind"] == BUILD_FAILED)
                          or "сборка не удалась")
    overrides: dict[str, Any] = deepcopy(dict(card.overrides or {})) if tracked else {}
    if not tracked:
        if edits := edited_overrides(card, fresh):
            overrides["operations"] = edits
        if (route := route_override(document)) is not None:
            overrides["route"] = route
    if not differences(document, fresh) and not overrides:
        return CardResult(card, IDENTICAL, inputs=inputs, write=not tracked)
    try:
        derived, conflicts = derive(inputs, overrides, enrich, templates, operation_library_commit=library_commit,
                                    technology_source=technology_source)
    except Exception as error:  # noqa: BLE001
        return CardResult(card, FAILED, f"{type(error).__name__}: {error}")
    blocking = [item for item in conflicts if item["kind"] in (MISSING_OPERATION, ROUTE_NOT_APPLIED)]
    remaining = differences(document, derived)
    if not remaining and not blocking:
        return CardResult(card, OVERRIDES if overrides else IDENTICAL, inputs=inputs,
                          overrides=overrides or None, write=not tracked)
    shown = remaining[:3] + [item["message"] for item in blocking[:1]]
    summary = f"{len(remaining) + len(blocking)} расхождений: " + "; ".join(shown)
    if norms_include_setup(document):  # built before ETK_VERSION 4: its norms carry the card's setup, a fresh build has none
        summary += " [ЭТК версии <4: старые нормы включают настройку (tPrep)]"
    return CardResult(card, DRIFT, summary, inputs=inputs, overrides=overrides or None, write=not tracked)


def row_counts(techcards_uow_factory: Callable[[], Any]) -> dict[str, int]:
    with techcards_uow_factory() as uow:
        cards = list(uow.techcards.list())
        revisions = sum(len(uow.techcards.revisions(card.id)) for card in cards)
    return {"techcard_projects": len(cards), "techcard_revisions": revisions}


@dataclass
class Report:
    counts: dict[str, int] = field(default_factory=lambda: {name: 0 for name in CLASSES})
    lines: list[str] = field(default_factory=list)
    before: dict[str, int] = field(default_factory=dict)
    after: dict[str, int] = field(default_factory=dict)
    written: int = 0
    skipped: int = 0
    code: int = 0

    def text(self, apply: bool) -> str:
        out = list(self.lines)
        out.append("Итого: " + " · ".join(f"{name} {self.counts[name]}" for name in CLASSES))
        if apply:
            out.append(f"Записано карт: {self.written} · уже заполнены или не тронуты: {self.skipped}")
        for name in ("techcard_projects", "techcard_revisions"):
            out.append(f"{name}: было {self.before.get(name)} → стало {self.after.get(name)}")
        if not apply:
            out.append("Проверка без записи. Записать inputs/overrides — та же команда с --apply.")
        return "\n".join(out)


def _store(techcards_uow_factory: Callable[[], Any], result: CardResult) -> bool:
    """Write the two columns of one card; False when there is nothing to write or the card changed meanwhile."""
    with techcards_uow_factory() as uow:
        current = uow.techcards.get(result.card.id)
        if (current is None or current.version != result.card.version or current.inputs is not None
                or current.document != result.card.document):
            return False
        uow.techcards.save(replace(current, inputs=result.inputs, overrides=result.overrides))
        uow.commit()
    return True


def run(
    techcards_uow_factory: Callable[[], Any], references_factory: Callable[[], Any], *, apply: bool = False,
    refs: Refs | None = None,
) -> Report:
    report = Report(before=row_counts(techcards_uow_factory))
    with techcards_uow_factory() as uow:
        cards = list(uow.techcards.list())
        templates = list(uow.operation_cards.list())
        metadata = uow.operation_cards.metadata()
    if metadata is None:
        report.lines.append("Библиотека операционных карт не загружена — собирать нечем")
        report.code = 2
        report.after = report.before
        return report
    refs = refs or load_refs(references_factory)

    def enrich(config: Mapping[str, Any]) -> Mapping[str, Any]:
        return with_technology(config, refs=refs)[0]

    source = dict(refs.snapshot) if refs.snapshot is not None else None
    for card in sorted(cards, key=lambda item: (item.project, item.item_index)):
        result = classify(card, enrich, templates, metadata["source_commit"], source)
        report.counts[result.verdict] += 1
        extra = ""
        if apply and result.write:
            if _store(techcards_uow_factory, result):
                report.written += 1
                extra = " · записано"
            else:
                report.code = 1
                extra = " · не записано: карта изменилась, повторите"
        elif apply:
            report.skipped += 1
        elif result.write:
            extra = " · будет записано"
        overrides = result.overrides or {}
        marks = [f"правок операций {len(overrides['operations'])}"] if overrides.get("operations") else []
        marks += ["ручная схема ПФ"] if overrides.get("route") else []
        detail = "; ".join(filter(None, [result.summary if result.verdict in (DRIFT, FAILED, NO_INPUTS) else "", *marks]))
        report.lines.append(f"{result.label} · {result.verdict}" + (f" — {detail}" if detail else "") + extra)
    report.after = row_counts(techcards_uow_factory)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Заполнить inputs/overrides у ЭТК (по умолчанию — проверка без записи).")
    parser.add_argument("--apply", action="store_true", help="записать колонки inputs и overrides (ревизий не создаёт)")
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
    state = holder.state
    try:
        report = run(state.techcards_uow_factory, state.references_uow_factory, apply=args.apply)
    finally:
        engine.dispose()
    print(report.text(args.apply))
    return report.code


if __name__ == "__main__":
    sys.exit(main())
