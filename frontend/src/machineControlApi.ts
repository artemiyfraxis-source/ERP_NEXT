import { api } from "./api";
import type { Tone } from "./machinesApi";

/* ОБ.019 — запись настроек из ERP (план «Станки», часть 4; владелец 19.11.2026). Сервер:
   `workflows/machine_writes_api.py`, правила — `production/machine_writes.py`. */

export interface SettingField {
  label: string;
  value: number | boolean | string | null;
  unit: string;
  read: boolean;
  /** Ключ поля, которое ERP записывает на станок; null — только показ. */
  key: string | null;
  kind: string;
  /** Режим: текст значения. */
  text?: string | null;
}

export interface SettingGroup {
  title: string;
  fields: SettingField[];
}

export interface MachineWindow {
  inventory: string;
  settings: SettingGroup[];
}

export interface CommandChange {
  key: string;
  label: string;
  unit: string;
  before: string;
  after: string;
}

/** Команды станку (Ob019Controls.tsx): Стоп — всем, кто видит карточку; остальные — право «Управление станками». */
export type ControlCommand = "stop" | "reset" | "start" | "alarm_clear";

export interface MachineCommand {
  id: string;
  status: "queued" | "taken" | "done" | "failed" | "expired" | "unknown" | "cancelled";
  status_text: string;
  tone: Tone;
  purpose: "settings" | "transfer" | "control";
  /** Команда станку (purpose «control»), иначе null. */
  command?: ControlCommand | null;
  /** Состояние станка до и после команды станку, как его прочитал агент на ПК. */
  state_before?: string | null;
  state_after?: string | null;
  summary: string;
  fields: CommandChange[];
  error: string;
  created_by: string;
  created_at: string;
  finished_at: string | null;
}

export interface MachineOffset {
  key: string;
  label: string;
  offset: string;
  reason: string;
  updated_by: string;
  updated_at: string | null;
  /** Размер есть в ЭТК: наладчик может поправить и ЭТК. */
  etk: boolean;
}

export interface MachineControl {
  /** Почему писать нельзя сейчас; «» — можно. */
  problem: string;
  state: number | null;
  commands: MachineCommand[];
  offsets: MachineOffset[];
  can_edit: boolean;
  /** Право «Управление станками»: Сброс, Пуск, Сброс аварии. */
  can_control?: boolean;
}

export interface TransferChoice {
  key: string;
  group_id: string;
  number: string;
  project: string;
  wire: string;
  length_mm: string;
  quantity: string;
}

export interface TransferRow {
  key: string;
  label: string;
  unit: string;
  nominal: string | null;
  source: string;
  offset: string;
  value: string | null;
  live: string | null;
}

export interface TransferPlan {
  group_id: string;
  number: string;
  wire: string;
  length_mm: string;
  quantity: number;
  rows: TransferRow[];
  job: { total_set: number; order_quantity: number; order_number: string; product: string };
  missing: string[];
  blocking: string[];
}

export type CorrectionCase = "machine" | "etk" | "both";

export interface CorrectionBody {
  parameter: string;
  case: CorrectionCase;
  offset?: string;
  etk_value?: string;
  group_id?: string;
  measured?: string;
  note?: string;
}

const card = (inventory: string) => `/api/production/machines/card/${encodeURIComponent(inventory)}`;
const post = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) });

export const machineControlApi = {
  window: (inventory: string, signal?: AbortSignal) => api<MachineWindow>(card(inventory), { signal }),
  control: (inventory: string, signal?: AbortSignal) => api<MachineControl>(`${card(inventory)}/control`, { signal }),
  write: (inventory: string, values: Record<string, string | number>, confirmState: number | null) =>
    api<MachineCommand>(`${card(inventory)}/write`, post({ values, confirm_state: confirmState })),
  choices: (inventory: string) => api<{ choices: TransferChoice[] }>(`${card(inventory)}/transfer`),
  plan: (inventory: string, groupId: string, lengthMm: string) =>
    api<TransferPlan>(`${card(inventory)}/transfer?group_id=${encodeURIComponent(groupId)}&length_mm=${encodeURIComponent(lengthMm)}`),
  transfer: (inventory: string, groupId: string, lengthMm: string, confirmState: number | null) =>
    api<MachineCommand>(`${card(inventory)}/transfer`, post({ group_id: groupId, length_mm: lengthMm, confirm_state: confirmState })),
  command: (inventory: string, command: ControlCommand, confirmState?: number | null) =>
    api<MachineCommand>(`${card(inventory)}/command`, post(command === "stop" ? { command }
      : { command, confirm_state: confirmState ?? null, hands_clear: true })),
  correct: (inventory: string, body: CorrectionBody) =>
    api<{ offset: string | null; etk: { card_id: string; project: string; revision: number }[] }>(`${card(inventory)}/correction`, post(body)),
};

/** Когда команда станку возможна — та же таблица, что на сервере (production/machine_control.py) и в агенте (D766). */
export const CONTROL_STATES: Record<ControlCommand, number[] | null> = {
  stop: null, start: [102], reset: [100, 105, 107], alarm_clear: null,
};

export const CONTROL_NAMES: Record<ControlCommand, string> = {
  stop: "Стоп", reset: "Сброс", start: "Пуск", alarm_clear: "Сброс аварии",
};
