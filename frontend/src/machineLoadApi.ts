import { api } from "./api";

/* Загрузка станков (владелец, 17.10: «в планировании надо учитывать загрузку станка»): сетка станок × день и
   предупреждение «ОБ.098 занят …» при ручной постановке. Считает сервер (`production/machine_load.py`). */

export interface MachineLoadItem {
  task_id: string;
  task_number: string;
  project: string;
  item: string;
  operation_id: string;
  position: number;
  stage: string;
  etk_position: number;
  etk_stage: string;
  operator_names: string[];
  start: string;
  finish: string;
}

export interface MachineLoadDay {
  day: string;
  busy_seconds: number;
  /** Единиц станка × секунд смены дня; выходной — 0. */
  capacity_seconds: number;
  percent: number | null;
  /** Больше всего занятостей одновременно. */
  peak: number;
  over: boolean;
}

export interface MachineLoadRow {
  machine: string;
  label: string;
  units: number;
  /** Станок есть в БД.ОБ (иначе считается одним). */
  known: boolean;
  days: MachineLoadDay[];
  items: MachineLoadItem[];
}

export interface MachineLoad {
  date_from: string;
  date_to: string;
  machines: MachineLoadRow[];
  warnings: string[];
}

export interface MachineConflict {
  machine: string;
  label: string;
  units: number;
  start: string;
  finish: string;
  /** «ОБ.098 занят 17.10 10:00–12:30: ПЗ-0012 · КБ1, этап 2 «Опрессовка» … (Анна)». */
  text: string;
  busy: MachineLoadItem[];
}

export interface MachineCheck {
  task_id: string;
  operation_id: string;
  operator_id: string | null;
  quantity: number | null;
  /** Локальное время цеха «2026-10-17T10:00». */
  start: string;
  finish: string;
}

export const machineLoadApi = {
  load: (dateFrom: string, dateTo: string) =>
    api<MachineLoad>(`/api/production/machine-load?date_from=${dateFrom}&date_to=${dateTo}`),
  check: (body: MachineCheck) =>
    api<{ conflicts: MachineConflict[] }>("/api/production/machine-load/check", { method: "POST", body: JSON.stringify(body), safe: true }),
};
