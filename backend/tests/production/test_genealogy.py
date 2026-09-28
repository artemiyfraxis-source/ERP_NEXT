from decimal import Decimal
from uuid import UUID

from app.modules.production.genealogy import (
    BoxConsumptionSnapshot,
    BoxSnapshot,
    FinishedReceiptSnapshot,
    MaterialConsumptionSnapshot,
    SerialSnapshot,
    build_genealogy,
)


def test_search_from_serial_returns_material_boxes_and_finished_goods_grn():
    first = UUID("10000000-0000-0000-0000-000000000001")
    last = UUID("10000000-0000-0000-0000-000000000002")
    line = UUID("20000000-0000-0000-0000-000000000001")
    good = UUID("30000000-0000-0000-0000-000000000001")
    nodes, edges = build_genealogy(
        "ПЗ-0001-0001",
        boxes=(
            BoxSnapshot(first, "000001-8", "КБ1", 1, "Израсходована"),
            BoxSnapshot(last, "000002-6", "КБ1", 2, "Израсходована"),
        ),
        box_consumption=(BoxConsumptionSnapshot(first, last, Decimal("1")),),
        material_consumption=(MaterialConsumptionSnapshot(line, first, "PPM.26.0000001", "П1", Decimal("2.5")),),
        finished_receipts=(FinishedReceiptSnapshot(last, good, "ГП.26.0000001", Decimal("1")),),
        serials=(SerialSnapshot("ПЗ-0001-0001", last, "ГП.26.0000001", "Сдано на ГП"),),
    )

    assert {node.kind for node in nodes} == {"material", "box", "serial", "finished_goods"}
    assert {node.label for node in nodes} >= {"PPM.26.0000001", "000001-8", "000002-6", "ПЗ-0001-0001", "ГП.26.0000001"}
    assert [edge.relation for edge in edges] == [
        "Материал в производство", "Передано на следующий этап", "Сдано на ГП", "Серийный номер",
    ]


def test_unknown_genealogy_query_returns_empty_graph():
    nodes, edges = build_genealogy(
        "нет такого номера", boxes=(), box_consumption=(),
        material_consumption=(), finished_receipts=(), serials=(),
    )
    assert nodes == ()
    assert edges == ()



def test_box_number_typed_by_hand_finds_the_box_chain():
    first = UUID("10000000-0000-0000-0000-000000000001")
    last = UUID("10000000-0000-0000-0000-000000000002")
    nodes, _ = build_genealogy(
        " 000002 6 ",
        boxes=(
            BoxSnapshot(first, "000001-8", "КБ1", 1, "Израсходована"),
            BoxSnapshot(last, "000002-6", "КБ1", 2, "Израсходована"),
        ),
        box_consumption=(BoxConsumptionSnapshot(first, last, Decimal("1")),),
        material_consumption=(), finished_receipts=(), serials=(),
    )
    assert {node.label for node in nodes} == {"000001-8", "000002-6"}
