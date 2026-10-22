/* «Заявки» закупщика: the rules of Roller_Buyer's RequestLinesTable without React —
   statuses and their chips, the three groupings (remembered), selection by group, Shift-range and drag. */
import { projectLabel } from "./internalProjects";
import type { RbStatus, RqCounts, RqLine, RqProject } from "./procurementRequestsApi";
import { purposeLabel } from "./purposeLabels";
import type { Tone } from "./statusTone";

/** RB `LINE_STATUSES` order without «Давальческое» (no such fact in ERP). */
export const RB_ORDER: readonly RbStatus[] = [
  "new", "requote", "from_stock", "in_rfq", "quoted", "decided", "ordered", "to_pay", "paid", "shipped", "partial",
  "delivered", "cancelled",
];

/** RB `LINE_STATUS_LABELS`; «Взять со склада» is «Со склада» — in ERP the reserve is placed at once. */
export const RB_LABELS: Record<RbStatus, string> = {
  new: "Новая",
  requote: "Перезапрос",
  from_stock: "Со склада",
  in_rfq: "Запрошены КП",
  quoted: "Есть КП",
  decided: "Жду счёт",
  ordered: "Счёт получен",
  to_pay: "В оплате",
  paid: "Счёт оплачен",
  shipped: "В доставке",
  partial: "Частично на складе",
  delivered: "На складе",
  cancelled: "Отменена",
};

/** RB colours a stage by what it asks of the buyer: new and requote loud, waiting for quotes grey, quotes in — ready
 *  to choose, invoice and payment — moving, goods on the shelf green. */
export const RB_TONES: Record<RbStatus, Tone> = {
  new: "warning",
  requote: "warning",
  from_stock: "success",
  in_rfq: "neutral",
  quoted: "info",
  decided: "warning",
  ordered: "info",
  to_pay: "info",
  paid: "info",
  shipped: "info",
  partial: "warning",
  delivered: "success",
  cancelled: "neutral",
};

/** Each status once, in pipeline order, with its count. */
export function orderedCounts(counts: RqCounts): Array<[RbStatus, number]> {
  return RB_ORDER.filter((status) => counts[status]).map((status) => [status, counts[status] as number]);
}

export const NO_PROJECT = "Без проекта";

/** The project as the screen names it: «ВП-001 · Название», a purpose in full words, or «Без проекта». */
export function projectTitle(project: Pick<RqProject, "kind" | "value">): string {
  if (project.kind === "project") return projectLabel(project.value);
  if (project.kind === "purpose") return purposeLabel(project.value);
  return NO_PROJECT;
}

export type GroupBy = "type" | "plan" | "last" | "none";
export const GROUP_LABELS: Record<GroupBy, string> = {
  type: "по типу",
  plan: "по плану из расчёта",
  last: "по прошлым закупкам",
  none: "без группировки",
};
export const EMPTY_GROUPS: Record<GroupBy, string> = {
  type: "Тип не указан",
  plan: "Без плана в расчёте",
  last: "Раньше не покупали",
  none: "",
};
export const GROUP_KEY = "erp.procurementRequests.groupBy";

export function readGroupBy(): GroupBy {
  try {
    const value = localStorage.getItem(GROUP_KEY);
    return value === "type" || value === "plan" || value === "last" || value === "none" ? value : "type";
  } catch {
    return "type";
  }
}

export function writeGroupBy(value: GroupBy): void {
  try {
    localStorage.setItem(GROUP_KEY, value);
  } catch {
    // No storage (private window): the choice lasts for this page only.
  }
}

export function groupOf(line: RqLine, by: GroupBy): string {
  if (by === "type") return line.item_type || line.category || "";
  if (by === "plan") return line.plan?.label ?? "";
  if (by === "last") return line.last?.supplier ?? "";
  return "";
}

/** Named groups alphabetically, the «nothing known» group last (RB). */
export function groupLines(lines: RqLine[], by: GroupBy): Array<[string, RqLine[]]> {
  const map = new Map<string, RqLine[]>();
  for (const line of lines) {
    const key = groupOf(line, by);
    map.set(key, [...(map.get(key) ?? []), line]);
  }
  return [...map.entries()].sort(([a], [b]) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b, "ru")));
}

/** Lines in the order the table shows them — what a Shift-range runs over. */
export function shownOrder(groups: Array<[string, RqLine[]]>): RqLine[] {
  return groups.flatMap(([, lines]) => lines);
}

/** Shift+click: every selectable line between the last clicked one and this one. Null — no anchor in this table. */
export function rangeIds(order: RqLine[], anchorId: string | null, id: string): string[] | null {
  if (anchorId === null) return null;
  const a = order.findIndex((line) => line.id === anchorId);
  const b = order.findIndex((line) => line.id === id);
  if (a < 0 || b < 0) return null;
  const [from, to] = a < b ? [a, b] : [b, a];
  return order.slice(from, to + 1).filter((line) => line.selectable).map((line) => line.id);
}

/** Ticks or unticks many lines at once: a group, a Shift range, a drag. */
export function setMany(selected: ReadonlySet<string>, ids: string[], on: boolean): Set<string> {
  const next = new Set(selected);
  for (const id of ids) {
    if (on) next.add(id);
    else next.delete(id);
  }
  return next;
}

/** «по плану N ₽» of a group: plan price with VAT × quantity, lines without a plan price left out. */
export function planSum(lines: RqLine[]): number {
  return lines.reduce((sum, line) => {
    const price = Number(line.plan?.price_with_vat ?? NaN);
    const quantity = Number(line.quantity);
    return Number.isFinite(price) && Number.isFinite(quantity) && line.rb_status !== "cancelled" ? sum + price * quantity : sum;
  }, 0);
}

/** A quantity typed with a comma or spaces → the API's dot form; "" when it is not a positive number. */
export function quantityInput(text: string): string {
  const clean = text.replace(/[\s ]/g, "").replace(",", ".");
  return /^\d+(\.\d+)?$/.test(clean) && Number(clean) > 0 ? clean : "";
}
