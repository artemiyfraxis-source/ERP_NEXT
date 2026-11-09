import type { Shipment, ShipmentLine } from "./api";
import { DISPATCH_STAGES, projectDispatchStage, shipmentDispatchStage, type DispatchStage } from "./dispatchStage";
import { kbKey, num, packable, round2, sameKb, stagePieces } from "./intakeStages";
import type { IntakeAct, IntakeRow } from "./productionIntakeApi";

export interface DataRow {
  id: string; kind: "shipment" | "project"; project: string; customer: string; shipment: Shipment | null;
  stage: DispatchStage | null; item_index: string; name: string; quantity: number; unit: string; grn: string; ownStage: string;
  /** Все ГРН строки: партии строки отправки (`grns`) или ГРН остатка КБ; `grn` — первый. */
  grns: string[];
}

const deadlineOf = (row: DataRow) => row.shipment?.deadline ?? "9999";

/** ГРН партий строки отправки: новое поле `grns`, без него — прежний единственный `grn`. */
export const lineGrns = (line: Pick<ShipmentLine, "grn"> & { grns?: string[] | null }) =>
  [...new Set((line.grns?.length ? line.grns : [line.grn]).filter((grn): grn is string => Boolean(grn)))];

/** Доля непринятого в отправки остатка КБ, пришедшая одним актом приёмки (`act` = null — приход без акта). */
export interface ActShare { act: IntakeAct | null; quantity: number; grn: string }

/** Остаток КБ на складе (принято − набрано в отправки) по актам приёмки — строка на ГРН. Набранное списывается сначала
 *  с партий, которые строки отправок называют (`grns`), остальное — со старых актов (первым уходит принятое раньше).
 *  Приход без акта (до актов в ERP) — отдельная доля; её ГРН известен, только когда КБ ещё не трогали. */
export function actShares(source: IntakeRow, shipments: Shipment[]): ActShare[] {
  const remainder = packable(source);
  if (!(remainder > 0)) return [];
  const acts = (source.acts ?? []).map((act, index) => ({ act, index, left: Math.max(num(act.quantity), 0) }))
    .sort((a, b) => a.act.received_at.localeCompare(b.act.received_at) || a.index - b.index);
  let consumed = num(source.packed);
  for (const shipment of shipments) {
    if (shipment.status === "Отменена") continue;
    for (const line of shipment.lines) {
      if (!sameKb(line, source) || !line.grns?.length) continue;
      let take = Math.max(num(line.picked), 0);
      for (const grn of line.grns) for (const item of acts) {
        if (item.act.grn !== grn || take <= 0 || consumed <= 0) continue;
        const used = Math.min(item.left, take, consumed);
        item.left -= used; take -= used; consumed -= used;
      }
    }
  }
  // Старые акты уходят первыми, пока остаток по актам больше настоящего остатка КБ.
  let extra = acts.reduce((sum, item) => sum + item.left, 0) - remainder;
  for (const item of acts) {
    if (extra <= 0) break;
    const used = Math.min(item.left, extra);
    item.left -= used; extra -= used;
  }
  const shares: ActShare[] = acts.filter((item) => item.left > 0).map((item) => ({ act: item.act, quantity: round2(item.left), grn: item.act.grn }));
  const loose = round2(remainder - shares.reduce((sum, share) => sum + share.quantity, 0));
  if (loose > 0) shares.push({ act: null, quantity: loose,
    grn: !source.acts?.length && num(source.packed) === 0 && num(source.sent) === 0 ? source.grn || "" : "" });
  return shares;
}

/** Shipment lines and unpicked stage remainders share one representation in both dispatch screens. */
export function dataRows(shipments: Shipment[], intake: IntakeRow[] = []): DataRow[] {
  const byKb = new Map(intake.map((row) => [kbKey(row), row]));
  const rows: DataRow[] = shipments.flatMap((shipment) => shipment.lines.map((line) => {
    const source = byKb.get(kbKey(line));
    const ownStage = source?.stage || source?.project_stage || "Принято складом";
    return { id: `${shipment.id}:${line.id}`, kind: "shipment" as const, project: line.project,
      customer: shipment.customer || shipment.receiver, shipment, stage: num(line.picked) > 0 ? shipmentDispatchStage(shipment, "Принято складом") : null,
      item_index: line.item_index, name: line.name, quantity: num(line.picked), unit: line.unit, grn: lineGrns(line)[0] ?? "", grns: lineGrns(line), ownStage };
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
      quantity: pieces[stage], unit: source.unit, grn: stage === "accepted" ? receiptGrn : "",
      grns: stage === "accepted" && receiptGrn ? [receiptGrn] : [], ownStage: source.stage || source.project_stage });
  }
  return rows.sort((a, b) => a.project.localeCompare(b.project, "ru", { numeric: true })
    || a.item_index.localeCompare(b.item_index, "ru", { numeric: true })
    || deadlineOf(a).localeCompare(deadlineOf(b))
    || (a.shipment?.number ?? a.project).localeCompare(b.shipment?.number ?? b.project));
}
