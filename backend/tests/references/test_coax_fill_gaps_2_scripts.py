"""Скрипты второго прохода дозаполнения БД.КОАКС 20.10 (docs/data): правила записи проверяются по их данным и коду.

Скрипты лежат вне пакета (запускаются на сервере через `python -`), поэтому данные берутся из исходника, а функции — по AST."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parents[3] / "docs" / "data"
FILL = DATA / "КОАКС — дозаполнение пропусков 2-й проход 20.10 запись в прод.py"
MERGE = DATA / "КОАКС — слияние B-111F в BNC-C58P 20.10 запись в прод.py"
PIN_TYPES = {"пайка", "обжим", "пайка внутри корпуса", "цанга"}
# Перезапись заполненной ячейки — только по документу изготовителя к артикулу (длины АМЭЛ) или по странице строки (изготовитель).
OVERWRITE_OK = {"L1", "L2", "L3", "Производитель", "Источник данных"}


def _payload():
    source = FILL.read_text(encoding="utf-8")
    ast.parse(source)
    return json.loads(re.search(r"json\.loads\(r'''(.*?)'''\)", source, flags=re.S).group(1)), source


def test_every_written_row_names_its_source():
    payload, _ = _payload()
    assert payload
    for item in payload:
        fields = [edit[0] for edit in item["edits"]]
        assert fields[-1] == "Источник данных", item["article"]
        old, new = item["edits"][-1][1], item["edits"][-1][2]
        assert new.startswith(old) and "20.10.2026 (2-й проход):" in new[len(old):], item["article"]
        for field in fields[:-1]:
            assert field in new, f"{item['article']}: «{field}» без источника"


def test_only_documented_overwrites_and_known_values():
    payload, _ = _payload()
    for item in payload:
        for field, old, new in item["edits"]:
            assert new, f"{item['article']}: {field} стирается"
            if old:
                assert field in OVERWRITE_OK, f"{item['article']}: перезапись «{field}»"
            if field == "Тип пина":
                assert new in PIN_TYPES
            assert field != "Заглубление контакта, мм"  # только значения серий из coax-contact-depth, новых нет


def test_plan_row_refuses_cells_that_changed():
    _, source = _payload()
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "plan_row")
    scope: dict = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "plan_row", "exec"), scope)  # noqa: S102 - собственный скрипт
    record = type("R", (), {"fields": {"КСВ": "", "Тип пина": "пайка", "L1": "3,1"}})()
    todo, done, refused = scope["plan_row"](record, [("КСВ", "", "<2 (до 6 ГГц)"), ("Тип пина", "", "цанга"),
                                                     ("L1", "3,1", "2,5")])
    assert [t[0] for t in todo] == ["КСВ", "L1"]
    assert [r[0] for r in refused] == ["Тип пина"] and not done


def test_merge_moves_lots_and_deletes_only_through_the_application():
    source = MERGE.read_text(encoding="utf-8")
    ast.parse(source)
    assert 'G, R, CABLE = "B-111F", "BNC-C58P", "RG-58"' in source
    assert "edit_lot_identity(EditLotIdentityCommand(" in source and "delete_record(g.id" in source
    assert "UPDATE" not in source.upper().replace("UPDATED", "") and "DELETE FROM" not in source.upper()
    assert 'kind = "история" if table in HISTORY_TABLES else "живая"' in source and "if live:" in source
    assert "серия/исполнение строк разные" in source  # слияния через серии нет (U.FL ≠ MHF1)
