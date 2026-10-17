"""Длины резки из расчёта (DRIFT-59e18ff — SUPPLYMAP TcGenInline.html, calcCutLengths_).

Сметчик в расчёте пишет материал провода сокращённо («U/UTP 2x2x0,48»), в
наборе он значится полным артикулом или наименованием («ParLan Patch U/UTP
Cat5e PVC 2х2х0,48-145»). Точное совпадение по-прежнему главнее; мягкое
(каждое слово строки расчёта входит в «артикул + наименование» провода)
срабатывает, только если точного нет и подходит ровно один провод набора.
"""

from app.modules.techcards.domain.cut_lengths import (
    CalcWire,
    CalcRow,
    cut_lengths_from_calc,
)


def _резка(comp: str, req: str = "") -> CalcRow:
    return CalcRow(code="CUT_WIRE", comp=comp, req=req)


def test_точное_совпадение_подставляет_длину():
    провод = CalcWire(id="w1", art="ParLan Patch U/UTP Cat5e PVC 2х2х0,48-145")
    длины = cut_lengths_from_calc([провод], [_резка("ParLan Patch U/UTP Cat5e PVC 2х2х0,48-145", "L=160мм")])
    assert длины == {"w1": 160}


def test_точное_совпадение_главнее_мягкого():
    """Строка расчёта одновременно точно совпадает с одним проводом и
    мягко подошла бы другому — точное побеждает, мягкое для этой строки
    не применяется вовсе (в SUPPLYMAP: !wires.some(exact))."""
    точный = CalcWire(id="exact", art="U/UTP 2x2x0,48")
    мягкий_кандидат = CalcWire(id="loose", art="ParLan Patch U/UTP Cat5e PVC 2х2х0,48-145")
    длины = cut_lengths_from_calc([точный, мягкий_кандидат], [_резка("U/UTP 2x2x0,48", "L=160мм")])
    assert длины == {"exact": 160}
    assert "loose" not in длины


def test_мягкое_совпадение_с_одним_кандидатом_применяется():
    провод = CalcWire(id="w1", art="ParLan Patch U/UTP Cat5e PVC 2х2х0,48-145")
    длины = cut_lengths_from_calc([провод], [_резка("U/UTP 2x2x0,48", "L=160мм")])
    assert длины == {"w1": 160}


def test_два_подходящих_кандидата_не_применяется():
    первый = CalcWire(id="w1", art="ParLan Patch U/UTP Cat5e PVC 2х2х0,48-145")
    второй = CalcWire(id="w2", art="Prysmian U/UTP Cat5e 2x2x0,48-200")
    длины = cut_lengths_from_calc([первый, второй], [_резка("U/UTP 2x2x0,48", "L=160мм")])
    assert длины == {}


def test_кириллическая_х_и_латинская_x_эквивалентны():
    провод = CalcWire(id="w1", art="Кабель 2х2х0,48 экран")
    длины = cut_lengths_from_calc([провод], [_резка("2x2x0,48", "L=160мм")])
    assert длины == {"w1": 160}


def test_знак_умножения_и_потерянный_символ_тоже_эквивалентны_x():
    точный_знак = CalcWire(id="w1", art="Кабель 2×2×0,48")
    длины = cut_lengths_from_calc([точный_знак], [_резка("2x2x0,48", "L=160мм")])
    assert длины == {"w1": 160}

    потерянный_символ = CalcWire(id="w2", art="Кабель 2�2�0,48")
    длины2 = cut_lengths_from_calc([потерянный_символ], [_резка("2x2x0,48", "L=160мм")])
    assert длины2 == {"w2": 160}


def test_кавычки_отбрасываются():
    провод = CalcWire(id="w1", art='Кабель «U/UTP» 2x2x0,48')
    длины = cut_lengths_from_calc([провод], [_резка('"U/UTP" 2x2x0,48', "L=160мм")])
    assert длины == {"w1": 160}


def test_нет_совпадения_длина_не_подставляется():
    провод = CalcWire(id="w1", art="Кабель RG-58")
    длины = cut_lengths_from_calc([провод], [_резка("U/UTP 2x2x0,48", "L=160мм")])
    assert длины == {}


def test_несколько_разных_длин_у_одного_провода_неоднозначны():
    провод = CalcWire(id="w1", art="RG-58")
    длины = cut_lengths_from_calc(
        [провод],
        [_резка("RG-58", "L=160мм"), _резка("RG-58", "L=200мм")],
    )
    assert длины == {}


def test_длина_из_колонки_l_в_метрах_переводится_в_мм():
    провод = CalcWire(id="w1", art="RG-58")
    длины = cut_lengths_from_calc([провод], [CalcRow(code="CUT_COAX", comp="RG-58", l_m="1,5")])
    assert длины == {"w1": 1500}
