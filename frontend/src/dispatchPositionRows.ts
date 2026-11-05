import type { Shipment } from "./api";
import { DISPATCH_STAGES, projectDispatchStage, shipmentDispatchStage, type DispatchStage } from "./dispatchStage";
import { kbKey, num, stagePieces } from "./intakeStages";
import type { IntakeRow } from "./productionIntakeApi";

export interface DataRow {
  id: string; kind: "shipment" | "project"; project: string; customer: string; shipment: Shipment | null;
  stage: DispatchStage | null; item_index: string; name: string; quantity: number; unit: string; grn: string; ownStage: string;
}

const deadlineOf = (row: DataRow) => row.shipment?.deadline ?? "9999";

/** Shipment lines and unpicked stage remainders share one representation in both dispatch screens. */
export function dataRows(shipments: Shipment[], intake: IntakeRow[] = []): DataRow[] {
  const byKb = new Map(intake.map((row) => [kbKey(row), row]));
  const rows: DataRow[] = shipments.flatMap((shipment) => shipment.lines.map((line) => {
    const source = byKb.get(kbKey(line));
    const ownStage = source?.stage || source?.project_stage || "Принято складом";
    return { id: `${shipment.id}:${line.id}`, kind: "shipment" as const, project: line.project,
      customer: shipment.customer || shipment.receiver, shipment, stage: num(line.picked) > 0 ? shipmentDispatchStage(shipment, "Принято складом") : null,
      item_index: line.item_index, name: line.name, quantity: num(line.picked), unit: line.unit, grn: line.grn || "", ownStage };
  }));
  for (const source of intake) {
    const pieces = stagePieces(source, []);
    const free = DISPATCH_STAGES.filter(([stage]) => stage !== "shipped" && pieces[stage] > 0);
    if (!free.length && num(source.needed) <= 0 && num(source.sent) <= 0 && !shipments.some((shipment) => shipment.status !== "Отменена" && shipment.lines.some((line) => kbKey(line) === kbKey(source)))) {
      free.push([projectDispatchStage(source.stage || source.project_stage), ""]);
    }
    // Aggregate GRN does not identify an unreceived or mixed-receipt remainder.
    const receiptGrn = source.acts?.length === 1 && num(source.acts[0].quantity) === num(source.accepted)
      ? source.acts[0].grn
      : !source.acts?.length && num(source.packed) === 0 && num(source.sent) === 0 ? source.grn || "" : "";
    for (const [stage] of free) rows.push({ id: `project:${kbKey(source)}:${stage}`, kind: "project", project: source.project,
      customer: source.customer, shipment: null, stage, item_index: source.item_index, name: source.name,
      quantity: pieces[stage], unit: source.unit, grn: stage === "accepted" ? receiptGrn : "", ownStage: source.stage || source.project_stage });
  }
  return rows.sort((a, b) => a.project.localeCompare(b.project, "ru", { numeric: true })
    || a.item_index.localeCompare(b.item_index, "ru", { numeric: true })
    || deadlineOf(a).localeCompare(deadlineOf(b))
    || (a.shipment?.number ?? a.project).localeCompare(b.shipment?.number ?? b.project));
}
