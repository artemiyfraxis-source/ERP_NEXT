"""Pure production genealogy graph construction."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from .domain import ProductionValidationError
from .traceability import validate_box_number


@dataclass(frozen=True)
class GenealogyNode:
    key: str
    kind: str
    label: str
    details: str


@dataclass(frozen=True)
class GenealogyEdge:
    source: str
    target: str
    quantity: Decimal
    relation: str


@dataclass(frozen=True)
class BoxSnapshot:
    id: UUID
    number: str | None
    item: str
    operation_position: int
    status: str


@dataclass(frozen=True)
class BoxConsumptionSnapshot:
    source_box_id: UUID
    target_box_id: UUID
    quantity: Decimal


@dataclass(frozen=True)
class MaterialConsumptionSnapshot:
    fulfillment_line_id: UUID
    target_box_id: UUID
    grn: str
    package: str
    quantity: Decimal


@dataclass(frozen=True)
class FinishedReceiptSnapshot:
    source_box_id: UUID
    finished_good_id: UUID
    grn: str
    quantity: Decimal


@dataclass(frozen=True)
class SerialSnapshot:
    serial_number: str
    current_box_id: UUID | None
    finished_goods_grn: str | None
    status: str


def build_genealogy(
    query: str,
    *,
    boxes: tuple[BoxSnapshot, ...],
    box_consumption: tuple[BoxConsumptionSnapshot, ...],
    material_consumption: tuple[MaterialConsumptionSnapshot, ...],
    finished_receipts: tuple[FinishedReceiptSnapshot, ...],
    serials: tuple[SerialSnapshot, ...],
) -> tuple[tuple[GenealogyNode, ...], tuple[GenealogyEdge, ...]]:
    raw = str(query or "").strip()
    if not raw:
        return (), ()
    needles = {raw.casefold()}
    try:
        needles.add(validate_box_number(raw).casefold())
    except ProductionValidationError:
        pass
    nodes: dict[str, GenealogyNode] = {}
    edges: list[GenealogyEdge] = []
    seeds: set[str] = set()

    def add_node(node: GenealogyNode, *searchable: str) -> None:
        nodes[node.key] = node
        if any(needle in str(value or "").casefold() for needle in needles for value in (node.label, *searchable)):
            seeds.add(node.key)

    for box in boxes:
        key = f"box:{box.id}"
        add_node(GenealogyNode(
            key=key, kind="box", label=box.number or f"Коробка {str(box.id)[:8]}",
            details=f"{box.item} · этап {box.operation_position} · {box.status}",
        ), str(box.id), box.item)
    for item in material_consumption:
        source = f"material:{item.fulfillment_line_id}"
        add_node(GenealogyNode(
            key=source, kind="material", label=item.grn,
            details=f"Пакет {item.package or '—'} · позиция выдачи {item.fulfillment_line_id}",
        ), item.package, str(item.fulfillment_line_id))
        edges.append(GenealogyEdge(source, f"box:{item.target_box_id}", item.quantity, "Материал в производство"))
    for item in box_consumption:
        edges.append(GenealogyEdge(
            f"box:{item.source_box_id}", f"box:{item.target_box_id}",
            item.quantity, "Передано на следующий этап",
        ))
    for item in finished_receipts:
        target = f"finished:{item.finished_good_id}:{item.grn}"
        add_node(GenealogyNode(
            key=target, kind="finished_goods", label=item.grn,
            details=f"Готовая продукция · позиция {item.finished_good_id}",
        ), str(item.finished_good_id))
        edges.append(GenealogyEdge(f"box:{item.source_box_id}", target, item.quantity, "Сдано на ГП"))
    for item in serials:
        key = f"serial:{item.serial_number}"
        add_node(GenealogyNode(
            key=key, kind="serial", label=item.serial_number,
            details=item.status + (f" · {item.finished_goods_grn}" if item.finished_goods_grn else ""),
        ), item.finished_goods_grn or "")
        if item.current_box_id is not None:
            edges.append(GenealogyEdge(f"box:{item.current_box_id}", key, Decimal("1"), "Серийный номер"))

    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        if edge.source not in nodes or edge.target not in nodes:
            continue
        adjacency.setdefault(edge.source, set()).add(edge.target)
        adjacency.setdefault(edge.target, set()).add(edge.source)
    included = set(seeds)
    pending = list(seeds)
    while pending:
        current = pending.pop()
        for neighbour in adjacency.get(current, ()):
            if neighbour not in included:
                included.add(neighbour)
                pending.append(neighbour)
    selected_nodes = tuple(sorted((node for key, node in nodes.items() if key in included), key=lambda item: (item.kind, item.label, item.key)))
    selected_edges = tuple(edge for edge in edges if edge.source in included and edge.target in included)
    return selected_nodes, selected_edges

