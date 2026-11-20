import { api } from "./api";

/* «Очередь станка» (production/machine_queue.py): шаги ЭТК открытых заданий для станка без управления, кроме резки
   (резка — в «Очереди резки»). Сервер читает все открытые задания — опрос раз в минуту. */

export interface MachineQueueEtk {
  /** «Жало», «Температура» … — название параметра ЭТК по-русски. */
  label: string;
  /** «» — в ЭТК не задано. */
  value: string;
  source: string;
}

export interface MachineQueueJob { id: string; number: string; progress_quantity: number | null }

export interface MachineQueueRow {
  key: string;
  task_id: string;
  operation_id: string;
  task_number: string;
  project: string;
  /** КБ. */
  item: string;
  /** Полуфабрикат. */
  pf: string;
  etk_position: string | number | null;
  /** Операция ЭТК по-русски. */
  step: string;
  planned: string;
  done: string;
  left: string;
  etk: MachineQueueEtk[];
  warnings: string[];
  /** Работа, начатая по этой строке. */
  job: MachineQueueJob | null;
}

export interface MachineQueue { equipment_id: string; inventory: string; version: string; rows: MachineQueueRow[] }

export const machineQueueApi = {
  queue: (equipmentId: string, signal?: AbortSignal) =>
    api<MachineQueue>(`/api/production/machine-queue?equipment_id=${encodeURIComponent(equipmentId)}`, { signal }),
};
