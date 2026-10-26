import type { ReceivingLine, UpdConfidence, UpdProposal, UpdProposalRow } from "./api";
import type { Tone } from "./statusTone";

/** «Принять по УПД»: how a waiting line was found. */
export const CONFIDENCE: Record<Exclude<UpdConfidence, "">, { label: string; tone: Tone; title: string }> = {
  invoice: { label: "по счёту", tone: "success", title: "Счёт из «Основания передачи» УПД и позиция совпали" },
  article: { label: "по артикулу", tone: "success", title: "Совпал артикул" },
  name: { label: "по названию", tone: "info", title: "Совпало наименование — проверьте позицию" },
  weak: { label: "слабое", tone: "warning", title: "Совпало лишь часть слов — отметьте, только если это она" },
};

/** One УПД row's share on a waiting line. */
export interface UpdShare { index: number; quantity: string }

/** What a pre-filled delivery row carries besides the hand-typed fields. */
export interface UpdRowState {
  line: ReceivingLine;
  ticked: boolean;
  document: string;
  shares: UpdShare[];
  confidence: UpdConfidence;
  warnings: string[];
}

const SCALE = 1_000_000;

function toUnits(value: string) {
  const number = Number(String(value ?? "").replace(",", ".").trim());
  return Number.isFinite(number) ? Math.round(number * SCALE) : 0;
}

/** «6» + «4» → «10», without floating noise («0.1» + «0.2» → «0.3»). */
export function sumQuantities(values: string[]) {
  const total = values.reduce((sum, value) => sum + toUnits(value), 0);
  return String(total / SCALE);
}

/** «шт.», «штук», «pcs» → «шт»: the order's unit against the УПД's. */
export function unitKey(value: string | null | undefined) {
  const text = String(value ?? "").trim().toLowerCase().replace(/ё/g, "е").replace(/\.$/, "");
  if (/^(шт|штук|штука|pcs|pc)$/.test(text)) return "шт";
  if (/^(м|метр|метра|метров|пог\.?\s?м|мп|m)$/.test(text)) return "м";
  if (/^(уп|упак|упаковка|упаковки)$/.test(text)) return "уп";
  if (/^(рул|рулон|рулона)$/.test(text)) return "рул";
  return text;
}

/** The row's quantity in the line's unit: the shelf figure, else the invoiced one when that is the line's unit. */
export function rowQuantity(row: UpdProposalRow, line: ReceivingLine) {
  const wanted = unitKey(line.unit);
  if (row.quantity && unitKey(row.unit) === wanted) return row.quantity;
  if (row.invoice_quantity && unitKey(row.invoice_unit) === wanted) return row.invoice_quantity;
  return row.quantity || row.invoice_quantity || "";
}

/** The rows as the proposal pre-fills them: matched lines ticked with their «По УПД». */
export function initialRows(proposal: UpdProposal): UpdRowState[] {
  return proposal.lines.map((item) => ({
    line: item.line,
    ticked: item.ticked,
    document: item.document_quantity,
    shares: item.rows.map((share) => ({ ...share })),
    confidence: item.confidence,
    warnings: item.warnings,
  }));
}

/** The line ids a УПД row is on now. */
export function rowTargets<T extends { line: ReceivingLine; shares?: UpdShare[] }>(rows: T[], index: number) {
  return rows.filter((row) => (row.shares ?? []).some((share) => share.index === index)).map((row) => row.line.id);
}

/** «Принять как…»: the УПД row leaves the lines it was on (their «По УПД» drops by its share, a line left with no row
 *  is unticked) and the chosen line takes it whole and is ticked; "" — the row goes nowhere. */
export function reassignRow<T extends { line: ReceivingLine; ticked: boolean; document: string; shares?: UpdShare[] }>(
  rows: T[], row: UpdProposalRow, target: string,
): T[] {
  return rows.map((current) => {
    const shares = current.shares ?? [];
    const own = shares.some((share) => share.index === row.index);
    if (current.line.id === target) {
      if (own && shares.length === 1) return current;
      const next = [...shares.filter((share) => share.index !== row.index), { index: row.index, quantity: rowQuantity(row, current.line) }];
      return { ...current, shares: next, document: sumQuantities(next.map((share) => share.quantity)), ticked: true };
    }
    if (!own) return current;
    const rest = shares.filter((share) => share.index !== row.index);
    return { ...current, shares: rest, document: rest.length ? sumQuantities(rest.map((share) => share.quantity)) : "", ticked: rest.length > 0 && current.ticked };
  });
}

const pluralRows = (count: number) => `${count} ${count % 10 === 1 && count % 100 !== 11 ? "строка" : count % 10 >= 2 && count % 10 <= 4 && (count % 100 < 12 || count % 100 > 14) ? "строки" : "строк"}`;

/** «Склад → Поставка»: «Эти позиции ожидаются в Приёмке (3 строки, заказы З-1, З-2)»; nothing matched — "". */
export function expectedBanner(proposal: UpdProposal | null) {
  const matched = proposal?.lines.filter((item) => item.rows.length > 0).length ?? 0;
  if (!proposal || !matched) return "";
  const orders = proposal.orders.length ? `, ${proposal.orders.length === 1 ? "заказ" : "заказы"} ${proposal.orders.join(", ")}` : "";
  return `Эти позиции ожидаются в Приёмке (${pluralRows(matched)}${orders})`;
}

/** The title of a УПД row: article · name as printed. */
export function rowTitle(row: UpdProposalRow) {
  return [...new Set([row.article, row.upd_name || row.name].map((value) => (value ?? "").trim()).filter(Boolean))].join(" · ");
}
