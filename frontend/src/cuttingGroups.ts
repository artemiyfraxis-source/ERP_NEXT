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
 *  items (serials); `remaining_labels` — label pieces still to print, numbered and fixed («Печатать оставшиеся»). */
export interface MarkingProgressRow { task_id: string; task_number: string; item: string; quantity: number; printed: number; remaining: number; remaining_labels: number }
export interface MarkingProgress { rows: MarkingProgressRow[]; printed: number; remaining: number; remaining_labels: number }

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
