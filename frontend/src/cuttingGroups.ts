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
}

export interface CuttingGroup {
  id: string;
  number: string;
  version: number;
  recipe_version: number;
  recipe_key: string;
  project: string;
  site: string;
  state: "open" | "frozen" | "cancelled";
  membership_frozen: boolean;
  partial_view?: boolean;
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

export function cuttingRows(group: CuttingGroup): CuttingMember[] {
  return group.rows.filter((row) => row.membership_state === "active").sort((a, b) => a.position - b.position);
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
