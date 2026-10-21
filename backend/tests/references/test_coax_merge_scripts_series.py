"""Скрипты слияния дублей БД.КОАКС (docs/data) не парят и не сливают строки разных серий (владелец 20.10: «U.FL — это чуть
другое»: U.FL — Hirose, MHF1 / IPEX — I-PEX; 20.10 «MHF1-plug RG-178» по ошибке слили в «U.FL» и удалили).

Скрипты лежат вне пакета (запускаются на сервере через `python -`), поэтому функции серии берутся из исходника по AST."""
from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

DATA = Path(__file__).resolve().parents[3] / "docs" / "data"
SCRIPTS = {
    "обобщённые строки 19.10": "КОАКС — слияние дублей обобщённых строк 19.10 запись в прод.py",
    "по названию товара 20.10": "КОАКС — артикулы по названию товара 20.10 запись в прод.py",
}
HELPERS = ("fold", "type_of", "series_key", "same_series")


def _helpers(name: str):
    source = (DATA / SCRIPTS[name]).read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in HELPERS]
    assert {node.name for node in wanted} == set(HELPERS), f"{name}: нет функций серии"
    scope: dict = {"re": re}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), name, "exec"), scope)  # noqa: S102 - собственные скрипты репозитория
    return scope, source, tree


def _row(kind: str):
    return SimpleNamespace(fields={"Тип/Серия": kind, "coaxType": kind})


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_series_key_strips_gender_and_execution(name):
    key = _helpers(name)[0]["series_key"]
    assert key("U.FL-female R/A") == "u.fl"
    assert key("U.FL-female (IPX-female)") == "u.fl"
    assert key("MHF1-plug") == "mhf1"
    assert key("MHF1-plug (Lock)") == "mhf1"
    assert key("SMA-male R/A") == key("SMA-female flange") == "sma"
    assert key("RP-SMA-male") == "rp-sma"
    assert key("FAKRA-female (тип Z)") == key("Fakra-female") == "fakra"


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_different_series_never_pair(name):
    same = _helpers(name)[0]["same_series"]
    assert not same(_row("MHF1-plug"), _row("U.FL-female R/A"))
    assert not same(_row("MHF1-plug (Lock)"), _row("U.FL-female"))
    assert not same(_row("SMA-male"), _row("RP-SMA-male"))
    assert same(_row("U.FL-female R/A"), _row("U.FL-female (IPX-female)"))
    assert same(_row("MHF1-plug"), _row("MHF1-plug (Lock)"))


def test_pairing_code_checks_series():
    """Сама функция, составляющая пары, зовёт same_series: без проверки серии пара не строится."""
    _, source, tree = _helpers("обобщённые строки 19.10")
    find_pairs = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "find_pairs")
    assert "same_series" in ast.get_source_segment(source, find_pairs)
    _, source, tree = _helpers("по названию товара 20.10")
    do_merge = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "do_merge")
    assert "same_series" in ast.get_source_segment(source, do_merge)


def test_fill_script_restores_mhf1_as_its_own_series():
    """Скрипт 20.10 возвращает «MHF1-plug RG-178» отдельной строкой серии MHF1 без предложения поставщика «U.FL» (gsm-repiteri.ru 3148)."""
    import json

    path = DATA / "КОАКС — дозаполнение пропусков и U.FL и MHF1 отдельно 20.10 запись в прод.py"
    source = path.read_text(encoding="utf-8")
    ast.parse(source)
    blocks = re.findall(r"json\.loads\(r'''(.*?)'''\)", source, flags=re.S)
    create, payload = json.loads(blocks[0]), json.loads(blocks[1])
    fields = create[0]["fields"]
    assert fields["Артикул"] == "MHF1-plug RG-178" and fields["Серия"] == "MHF1" and fields["Тип/Серия"] == "MHF1-plug"
    assert not fields["Ссылка"] and not fields["Артикул поставщика"] and not fields["Цена, ₽"]
    ufl = next(item for item in payload if item["article"] == "U.FL")
    assert ["Производитель", "I-PEX (аналог)", ""] in ufl["edits"]
