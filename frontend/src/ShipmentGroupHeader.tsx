import type { Shipment } from "./api";
import { compareShipments } from "./shipmentOrder";

type GroupRow = { project: string; shipment?: Shipment | null };

/** Stable group identity, independent of display ordering. */
export function shipmentGroupKey(row: GroupRow): string {
  const shipment = row.shipment;
  return `${row.project}\u0001${shipment?.id ?? ""}`;
}

export function orderShipmentRows<T extends GroupRow>(rows: T[]): T[] {
  const newest = new Map<string, Shipment>();
  for (const row of rows) if (row.shipment) {
    const previous = newest.get(row.project);
    if (!previous || compareShipments(row.shipment, previous) < 0) newest.set(row.project, row.shipment);
  }
  return [...rows].sort((a, b) => {
    if (a.project !== b.project) {
      const headA = newest.get(a.project), headB = newest.get(b.project);
      return (headA && headB ? compareShipments(headA, headB) : headA ? -1 : headB ? 1 : 0)
        || a.project.localeCompare(b.project, "ru", { numeric: true });
    }
    return a.shipment && b.shipment ? compareShipments(a.shipment, b.shipment) : a.shipment ? -1 : b.shipment ? 1 : 0;
  });
}

/** Position duplicates do not affect chronological shipment numbering within a project. */
export function shipmentSequence(rows: GroupRow[]): Map<string, string> {
  const projects = new Map<string, Map<string, Shipment>>();
  for (const row of rows) if (row.shipment) {
    const shipments = projects.get(row.project) ?? new Map<string, Shipment>();
    shipments.set(row.shipment.id, row.shipment);
    projects.set(row.project, shipments);
  }
  const result = new Map<string, string>();
  for (const [project, unique] of projects) {
    const shipments = [...unique.values()].sort(compareShipments).reverse();
    shipments.forEach((shipment, index) => result.set(shipmentGroupKey({ project, shipment }), `${index + 1} из ${shipments.length}`));
  }
  return result;
}
