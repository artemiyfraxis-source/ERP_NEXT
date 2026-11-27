import type { Shipment } from "./api";

/** Человеческое имя отправки без служебного номера: «828К. · КБ3 · PL828К.-1» (владелец 27.11: «ОТГ» на экранах не нужен).
 *  Общая отправка нескольких проектов — «828К. + 533К.11 · …». */
export function shipmentTitle(shipment: Pick<Shipment, "lines" | "pl_number">): string {
  const projects = [...new Set(shipment.lines.map((line) => line.project))];
  const items = [...new Set(shipment.lines.filter((line) => Number(String(line.picked).replace(",", ".")) > 0 || !shipment.lines.some((other) => Number(String(other.picked).replace(",", ".")) > 0))
    .map((line) => line.item_index))];
  return [projects.join(" + "), items.slice(0, 3).join(", ") + (items.length > 3 ? ` и ещё ${items.length - 3}` : ""), shipment.pl_number].filter(Boolean).join(" · ");
}
