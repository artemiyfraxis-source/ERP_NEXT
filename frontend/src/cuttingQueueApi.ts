import { api } from "./api";

/* «Очередь резки» (production/cutting_queue.py, CUTTING_API.md): один список на станок через все проекты, «Готово» вводит готовые. */

export interface CuttingQueueMachine { machine_id: string | null; machine_name: string; rows: number; left: string | number; projects: number }

export interface CuttingQueueRow {
  key: string;
  task_id: string;
  operation_id: string;
  task_number: string;
  project: string;
  item: string;
  group_number: string | null;
  wire: string | null;
  wire_name: string | null;
  /** Полный текст провода (подсказка); `wire_name` — короткое имя для строки. */
  wire_title?: string | null;
  length_mm: string | null;
  planned: string;
  done: string;
  left: string;
  strip_a: string | null;
  strip_a_mode: "full" | "partial" | null;
  strip_b: string | null;
  strip_b_mode: "full" | "partial" | null;
  laser_mark: boolean | string | null;
  marking_mode: "full" | "ends" | null;
  operator_names: string[];
  warnings: string[];
  blocked: string | null;
  setup_change: boolean;
  setup_text: string;
  /** Непрозрачный ключ наладки: экран отмечает смену наладки заново после фильтра снимка по станку. */
  setup_key?: string;
  /** Станок строки в этом списке («Все станки» — первый из ЭТК; список станка — он сам). */
  machine_id?: string | null;
  machine_name?: string;
  /** «Все станки» (`machine_id=all`): на каких станках стоит строка. */
  machines?: { machine_id: string | null; machine_name: string }[];
}

export interface CuttingQueue { machine_id: string | null; version: string; rows: CuttingQueueRow[] }
/** «Все станки» одним чтением: строки (каждая один раз), выбор станков и версия — экран сам делит по станкам. */
export interface CuttingQueueSnapshot extends CuttingQueue { machines: CuttingQueueMachine[] }

export const ALL_MACHINES = "all";
export const NO_MACHINE = "none";

/** Список одного станка из строк снимка — то же, что `GET ?machine_id=<станок>`: строки, где ЭТК называет станок (он
 *  становится станком строки), `none` — строки без станка; смена наладки отмечается заново по отфильтрованному списку. */
export function rowsOf(rows: CuttingQueueRow[], value: string): CuttingQueueRow[] {
  if (value === ALL_MACHINES) return rows;
  const result: CuttingQueueRow[] = [];
  let previous: string | undefined;
  for (const row of rows) {
    const named = row.machines ?? [];
    let shown = row;
    if (value === NO_MACHINE) {
      if (named.length) continue;
    } else {
      const own = named.find((machine) => machine.machine_id === value);
      if (!own) continue;
      if (row.machine_id !== own.machine_id || row.machine_name !== own.machine_name) shown = { ...row, machine_id: own.machine_id, machine_name: own.machine_name };
    }
    const change = shown.setup_key !== previous;
    previous = shown.setup_key;
    result.push(change === shown.setup_change ? shown : { ...shown, setup_change: change });
  }
  return result;
}
export interface CuttingDoneResult { notice: string; assigned: boolean; output_id: string }

export const cuttingQueueApi = {
  machines: () => api<{ machines: CuttingQueueMachine[] }>("/api/production/cutting-queue/machines").then((result) => result.machines ?? []),
  queue: (machineId: string | null) =>
    api<CuttingQueue>(`/api/production/cutting-queue?machine_id=${machineId === null ? "none" : encodeURIComponent(machineId)}`),
  /** Станок конкретного агрегата: его строки и строки «любого свободного» его модели (карточка станка без управления). */
  queueOfUnit: (equipmentId: string, signal?: AbortSignal) =>
    api<CuttingQueue>(`/api/production/cutting-queue?equipment_id=${encodeURIComponent(equipmentId)}`, { signal }),
  /** Экран «Очередь резки»: один запрос на обновление вместо списка станков + очереди станка. */
  snapshot: (signal?: AbortSignal) =>
    api<CuttingQueueSnapshot>(`/api/production/cutting-queue?machine_id=${ALL_MACHINES}&snapshot=true`, { signal }),
  /** Тот же ключ с тем же телом возвращает уже сделанное (повтор после обрыва не удваивает выпуск). */
  done: (taskId: string, operationId: string, quantity: number, idempotencyKey: string, onBehalfOf = "", equipmentId = "") =>
    api<CuttingDoneResult>(`/api/production/cutting-queue/done${onBehalfOf ? `?on_behalf_of=${encodeURIComponent(onBehalfOf)}` : ""}`, {
      method: "POST", headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ task_id: taskId, operation_id: operationId, quantity, ...(equipmentId ? { equipment_id: equipmentId } : {}) }),
    }),
};
