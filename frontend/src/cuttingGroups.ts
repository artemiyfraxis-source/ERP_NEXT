import type { MarkLabelSource, MarkLabel, MarkSerialRange } from "./markSerial";
import { hasMarkSerial, markLabelList } from "./markSerial";
import { countLabel, formatNumber } from "./format";
import type { PrintPage } from "./print";
import type { ProductionWorkSession } from "./api";

export interface CuttingMember {
  id: string;
  task_id: string;
  operation_id: string;
  task_version: number;
  task_number: string;
  project: string;
  item: string;
  name: string;
  position: number;
  assembly_quantity: number;
  blank_quantity: string;
  operation_quantity: string;
  cables_per_assembly: string | number;
  cut_length_mm: string | number | null;
  labels: MarkLabelSource[];
  good: string;
  defects: string;
  membership_state: "active" | "split" | "cancelled";
  state: "waiting" | "assigned" | "started" | "done" | "split" | "cancelled";
  operator_ids: string[];
  planned_start: string | null;
  planned_finish: string | null;
  norm_seconds: number;
  boxes: { id: string; number: string; quantity: string }[];
  etk_url: string;
  /** Tube cuts of this КБ (group kind «tubes»): material, cut length, pieces. */
  tubes?: CuttingTube[];
}

export interface CuttingTube { material: string; length_mm: string | number; quantity: string | number }

export type CuttingKind = "cut" | "labels" | "tubes";

export interface CuttingGroup {
  id: string;
  number: string;
  version: number;
  recipe_version: number;
  recipe_key: string;
  /** Kind of the preparation PF (owner 20.11); missing in old groups. */
  kind?: CuttingKind;
  kind_name?: string;
  project: string;
  site: string;
  state: "open" | "frozen" | "cancelled";
  membership_frozen: boolean;
  partial_view?: boolean;
  /** Some active member is pinned to an older ETK revision than the issued one. */
  etk_stale?: boolean;
  planned_start: string | null;
  planned_finish: string | null;
  norm_seconds: number;
  rows: CuttingMember[];
  session?: ProductionWorkSession | null;
  downtime?: { id: string; reason: string; reason_name: string; comment: string; started_at: string } | null;
}

export interface CuttingSubmission {
  member_id: string;
  good_quantity: string;
  defect_quantity: string;
  defect_character: string;
  defect_description: string;
}

export interface CuttingWorkResult {
  group: CuttingGroup;
  outputs: { member_id: string; output_id: string; box_id: string; box_number: string }[];
}

export const cuttingState: Record<CuttingMember["state"], string> = {
  waiting: "Не назначено", assigned: "Назначено", started: "В работе", done: "Выполнено", split: "Выдано отдельно", cancelled: "Отменено",
};

/** Printed / remaining labels per КБ: `GET /api/production/marking/progress?group_id=…`. `printed`/`remaining` count
 *  items (serials); `remaining_labels` — label pieces still to print, numbered and fixed («Печатать оставшиеся»);
 *  `total_labels` / `printed_labels` — label pieces in all and printed (owner 23.11; missing from older servers). */
export interface MarkingProgressRow { task_id: string; task_number: string; item: string; quantity: number; printed: number; remaining: number; remaining_labels: number;
  total_labels?: number; printed_labels?: number }
export interface MarkingProgress { rows: MarkingProgressRow[]; printed: number; remaining: number; remaining_labels: number; total_labels?: number; printed_labels?: number }

/** Distinct projects of the shown КБ, in member order: a split hint appears only when there are several. */
export function cuttingProjects(rows: readonly CuttingMember[]): string[] {
  return [...new Set(rows.map((row) => row.project))];
}

export interface ProjectShare { project: string; quantity: number }

function shares(entries: readonly ProjectShare[]): ProjectShare[] {
  const sum = new Map<string, number>();
  for (const entry of entries) sum.set(entry.project, (sum.get(entry.project) ?? 0) + entry.quantity);
  return [...sum].map(([project, quantity]) => ({ project, quantity }));
}

/** «708К.: 50 шт; 709К.: 30 шт». */
export function sharesText(items: readonly ProjectShare[]): string {
  return items.map((item) => `${item.project}: ${formatNumber(item.quantity)} шт`).join("; ");
}

/** One physical tube cut of the group: material × length, planned pieces over the shown КБ (owner 23.11). */
export interface TubeTotal { key: string; material: string; length: number | null; quantity: number; projects: ProjectShare[] }

/** Tube pieces of the shown КБ summed by exact material and normalized length («15», «15,0», 15 → one row), sorted by
 *  material, then length. `row.tubes` are full-run requirements: the sum is «План», never «Осталось». `missing` — КБ
 *  without tube data or with a quantity that is not a number: they are named, not counted as zero. */
export function tubeTotals(rows: readonly CuttingMember[]): { rows: TubeTotal[]; missing: string[] } {
  const totals = new Map<string, { material: string; length: string; parts: ProjectShare[] }>();
  const missing: string[] = [];
  for (const row of rows) {
    const tubes = row.tubes ?? [];
    const bad = !tubes.length || tubes.some((tube) => !Number.isFinite(Number(tube.quantity)) || Number(tube.quantity) < 0 || String(tube.quantity).trim() === "");
    if (bad) { missing.push(row.item); continue; }
    for (const tube of tubes) {
      const material = String(tube.material ?? "").trim();
      const length = lengthKey(tube.length_mm);
      const key = `${material}\u0000${length}`;
      const total = totals.get(key) ?? { material, length, parts: [] };
      total.parts.push({ project: row.project, quantity: Number(tube.quantity) });
      totals.set(key, total);
    }
  }
  const result = [...totals].map(([key, total]) => ({
    key, material: total.material, length: total.length === "" ? null : Number(total.length),
    quantity: total.parts.reduce((sum, part) => sum + part.quantity, 0), projects: shares(total.parts),
  }));
  result.sort((a, b) => a.material.localeCompare(b.material, "ru") || (a.length ?? Infinity) - (b.length ?? Infinity));
  return { rows: result, missing };
}

/** How many tube pieces go to each project; one line per cut when the group has several sizes. Empty — one project. */
export function tubeProjectHint(rows: readonly CuttingMember[], totals: readonly TubeTotal[]): string[] {
  if (cuttingProjects(rows).length < 2 || !totals.length) return [];
  if (totals.length === 1) return [sharesText(totals[0].projects)];
  return totals.map((total) => `${total.material || "—"} · ${total.length == null ? "—" : `${formatNumber(total.length)} мм`} — ${sharesText(total.projects)}`);
}

/** Label pieces of the shown КБ (owner 23.11): total / printed / remaining, plus what is left per project. Null —
 *  progress not loaded, or a shown КБ has no progress row or no label counts (older server): unknown, never zero. */
export interface LabelTotals { total: number; printed: number; remaining: number; projects: ProjectShare[] }

export function labelTotals(rows: readonly CuttingMember[], progress: MarkingProgress | null): LabelTotals | null {
  if (!progress) return null;
  const byTask = new Map(progress.rows.map((item) => [item.task_id, item]));
  const seen = new Set<string>();
  const parts: { project: string; total: number; printed: number; remaining: number }[] = [];
  for (const row of rows) {
    if (seen.has(row.task_id)) continue;
    seen.add(row.task_id);
    const item = byTask.get(row.task_id);
    if (!item || typeof item.total_labels !== "number" || typeof item.printed_labels !== "number") return null;
    parts.push({ project: row.project, total: item.total_labels, printed: item.printed_labels, remaining: item.remaining_labels });
  }
  return {
    total: parts.reduce((sum, part) => sum + part.total, 0),
    printed: parts.reduce((sum, part) => sum + part.printed, 0),
    remaining: parts.reduce((sum, part) => sum + part.remaining, 0),
    projects: shares(parts.map((part) => ({ project: part.project, quantity: part.remaining }))),
  };
}

export const cuttingKindTitle: Record<CuttingKind, string> = { cut: "Резка кабеля", labels: "Печать этикеток", tubes: "Резка трубок" };

/** The card look: a kind group shows only its own table; an old mixed group (no kind, or a cut group that still carries
 *  label layouts from the pre-split «Заготовки» PF) keeps the single wide table with the label block. */
export function cuttingLook(group: CuttingGroup): CuttingKind | "mixed" {
  if (group.kind === "labels" || group.kind === "tubes") return group.kind;
  if (group.kind === "cut" && cuttingRows(group).every((row) => !row.labels?.length)) return "cut";
  return "mixed";
}

export function cuttingRows(group: CuttingGroup): CuttingMember[] {
  return group.rows.filter((row) => row.membership_state === "active").sort((a, b) => a.position - b.position);
}

/** Длина реза одной записью: «325», «325,00», 325 → «325». */
export function lengthKey(value: string | number | null | undefined): string {
  const number = Number(String(value ?? "").replace(",", ".").replace(/\s/g, ""));
  return value === null || value === undefined || String(value).trim() === "" || !Number.isFinite(number) ? "" : String(number);
}

/** Счёт станка за прогон этой длины (штук провода) → «Годные» по КБ той же длины (штук ПФ), по порядку, не больше
 *  остатка КБ (владелец 19.11.2026: «Сдать группу» заполняется числом станка, подтверждает оператор). → {КБ: штук}. */
export function machineGood(rows: CuttingMember[], lengthMm: string, count: number): Record<string, string> {
  const result: Record<string, string> = {};
  let left = count;
  for (const row of rows) {
    if (lengthKey(row.cut_length_mm) !== lengthKey(lengthMm)) continue;
    const planned = Number(row.operation_quantity);
    const perUnit = planned > 0 ? Number(row.blank_quantity) / planned || 1 : 1;
    const remaining = Math.max(0, planned - Number(row.good) - Number(row.defects));
    const units = Math.max(0, Math.min(remaining, Math.floor(left / perUnit + 1e-9)));
    result[row.id] = String(units);
    left -= units * perUnit;
  }
  return result;
}

export type CuttingLabel = MarkLabel & { id: string; item: string; task: string };

/** Use the original KB's issued serial range; never invent a group numbering sequence. */
export function cuttingLabels(group: CuttingGroup, ranges: Readonly<Record<string, MarkSerialRange | undefined>>): CuttingLabel[] {
  return cuttingRows(group).flatMap((row) => {
    const range = ranges[row.id];
    if (row.labels.some((mark) => hasMarkSerial(String(mark.text ?? ""))) && (!range
      || !Number.isSafeInteger(range.first) || !Number.isSafeInteger(range.last) || range.first < 1
      || range.last - range.first + 1 !== row.assembly_quantity)) {
      throw new Error(`Нет номеров изделий для ${row.item} — откройте инструкцию задания`);
    }
    return markLabelList(row.labels, range ?? { first: 1, last: row.assembly_quantity }).map((label, index) => ({
      ...label, id: `${row.id}:${index}`, item: row.item, task: row.task_number,
    }));
  });
}

export function cuttingLabelsTitle(labels: readonly CuttingLabel[]): string {
  return `Этикетки: ${countLabel(labels.length, ["вид", "вида", "видов"])}, ${formatNumber(labels.reduce((sum, label) => sum + label.quantity, 0))} шт.`;
}

export function cuttingLabelsPage(group: CuttingGroup, labels: readonly CuttingLabel[]): PrintPage {
  return {
    title: "Этикетки группы",
    subtitle: `${group.project} · ${group.number} · ${cuttingLabelsTitle(labels)}`,
    blocks: [{ table: { columns: ["КБ", "Задание", "Надпись", "Материал", "Место", "Длина, мм", "Количество, шт"],
      rows: labels.map((label) => [label.item, label.task, label.text, label.material, label.place, label.length, String(label.quantity)]) } }],
    sign: "Напечатал: ____________ / ________ Дата: ______    Проверил: ____________ / ________ Дата: ______",
  };
}
