"""Запись в прод 17.10.2026 — температуры в картах операций (владелец 17.10: «почему на пайке такие огромные числа,
пройдись по всем ТК и поставь 240, на усадке везде 150»).

Что меняется (тексты карт «Операции ТК», как делает «Изменить» в карте — `edit_operation_record`, правка записи кода):
  * SDR_* и карты лужения CUT_WIRE+…+tin: «t° 330–360 °C» (любой диапазон) → «t° 240 °C»; «температуру [жала|ванны]
    согласно технологической инструкции …» → «температуру [жала|ванны] 240 °C»; программа ванны SDR_tin «200 °C» → «240 °C»;
  * HEAT_* (усадка): допуск шага «160 °C» → «150 °C».
Не трогается: допуск «(+/-) 10 °C», диапазоны плавления припоя и флюса в расходниках («от 190 °С до 270 °С»), УЗ-сварка.
Выпущенные ЭТК не переписываются (снимок документа остаётся; чтение с живых шаблонов — отдельная задача).

Защита «старого значения»: правится только то, что совпало с известным старым шаблоном; ячейка, уже переписанная
человеком (другой текст), не меняется и в журнал не попадает. Блоки (A)/(B) одного кода правятся одной записью — если
их тексты после замены разошлись, код пропускается (не пишется) и выводится в журнал.

Порядок (live-data protocol, prod-reference-write): запускать ПОСЛЕ «Обновить сервер» с этим релизом (шаблоны лужения ERP
сеются из JSON сами и придут «уже так»).
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → всё «уже так»
Локально (копия БД): ERP_DATABASE_URL=sqlite:///… python - dry < этот файл (из backend/)."""
import difflib
import os
import re
import sys
from collections import defaultdict
from uuid import UUID

from app.database import build_engine, build_techcards_uow_factory
from app.modules.techcards.application import EditOperationRecordCommand, edit_operation_record
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Температуры пайки и усадки 17.10.2026", department="System")
T_RANGE = re.compile(r"t°\s*\d{3}\s*[–-]\s*\d{3}\s*°\s*[CС]")
T_GEN = re.compile(r"температуру( жала| ванны)?\s*\(?согласно технологической инструкции для применяемого [^.,)]*\)?")


def fix(code: str, cell: str) -> str:
    if code.startswith("SDR_") or "+tin" in code:
        new = T_RANGE.sub("t° 240 °C", cell)
        new = T_GEN.sub(lambda m: "температуру" + (m.group(1) or "") + " 240 °C", new)
        return "240 °C" if code == "SDR_tin" and cell == "200 °C" else new
    if code.startswith("HEAT_") and cell == "160 °C":
        return "150 °C"
    return cell


def fragments(old: str, new: str) -> list[str]:
    """Changed words with two words of context on each side: «старое» → «новое»."""
    a, b = old.split(), new.split()
    found = []
    for tag, a1, a2, b1, b2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            found.append(f"«{' '.join(a[max(0, a1 - 2):a2 + 2])}» → «{' '.join(b[max(0, b1 - 2):b2 + 2])}»")
    return found


def record_of(card, code: str):
    """The record as the card editor sends it, with the fixed texts; plus the change log (card id, where, old → new)."""
    log = []
    steps = []
    for step in card.steps:
        text, tolerance = fix(code, step.text), fix(code, step.tolerance)
        for where, old, new in (("шаг %s" % step.number, step.text, text), ("шаг %s, допуск" % step.number, step.tolerance, tolerance)):
            if old != new:
                log.extend((card.source_id, where, line) for line in fragments(old, new))
        steps.append({"kind": step.kind, "text": text, "tolerance": tolerance})
    tooling = []
    for tool in card.tooling:
        program, parameters = fix(code, tool.program), fix(code, tool.parameters)
        for where, old, new in (("оснастка «%s», программа" % tool.name, tool.program, program), ("оснастка «%s», параметры" % tool.name, tool.parameters, parameters)):
            if old != new:
                log.extend((card.source_id, where, line) for line in fragments(old, new))
        tooling.append({"section": tool.section, "name": tool.name, "inventory_number": tool.inventory_number, "program": program,
                        "parameters": parameters, "norm": tool.norm, "unit": tool.unit})
    record = {"name": card.name, "safety": card.safety, "defects": card.defects, "revision": card.revision, "steps": steps, "tooling": tooling}
    return record, log


def main(mode: str) -> None:
    factory = build_techcards_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
    with factory() as uow:
        cards = [card for card in uow.operation_cards.list() if card.code.startswith(("SDR_", "HEAT_")) or "+tin" in card.code]
    by_code = defaultdict(list)
    for card in cards:
        by_code[card.code].append(card)
    changed = same = skipped = 0
    for code, blocks in sorted(by_code.items()):
        results = [record_of(card, code) for card in blocks]
        records = [record for record, _ in results]
        log = [item for _, part in results for item in part]
        if not log:
            same += 1
            continue
        if any(record != records[0] for record in records[1:]):
            skipped += 1
            print(f"ПРОПУЩЕН {code}: блоки (A)/(B) после замены различаются — править вручную")
            continue
        changed += 1
        for card_id, where, line in log:
            print(f"{code} [{card_id}] {where}: {line}")
        if mode == "apply":
            edit_operation_record(EditOperationRecordCommand(source_id=blocks[0].source_id, record=records[0], actor=ACTOR, now=moscow_now()), factory())
    print(f"{'ЗАПИСАНО' if mode == 'apply' else 'К ЗАПИСИ (dry)'}: кодов {changed}; уже так: {same}; пропущено: {skipped}")


main(sys.argv[1] if len(sys.argv) > 1 else "dry")
