import { api } from "./api";
import type { Tone } from "./machinesApi";
import type { ManualJob } from "./manualJobsApi";

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
  /** Служба записи на ПК станка старше 1.2.0: поля `runner_keys` (остальные страницы пульта) не записываются. */
  runner_outdated?: boolean;
  runner_keys?: string[];
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

/** «Выполнение задания» (владелец 19.11.2026): одна передача задания на станок и её счёт по T.Finish (D649).
 *  Сервер: `workflows/machine_runs_api.py`, правила — `production/machine_runs.py`. */
export type RunStage = "writing" | "failed" | "ready" | "running" | "finished" | "reset" | "nolink" | "closed";

export interface MachineRun {
  id: string;
  kind: "cutting" | "manual";
  label: string;
  group_id: string | null;
  length_mm: string | null;
  job_id: string | null;
  /** Сколько нужно сделать (тираж задания); null — не задано. */
  quantity: number | null;
  write: { status: MachineCommand["status"]; status_text: string; tone: Tone; error: string };
  verified_at: string | null;
  closed_at: string | null;
  closed_why: string;
  machine_count: number | null;
  closed_reset: boolean;
  created_by: string;
  created_at: string;
  stage: RunStage;
  counter_start: number | null;
  counter: number | null;
  /** Сделано по счётчику станка: D649 − старт; null — счёт недоступен. */
  done: number | null;
  reset: boolean;
  state: number | null;
  alarm: number | null;
  total_set: number | null;
}

export interface MachineRunState {
  run: MachineRun | null;
  /** Ручная работа, которая сейчас идёт на этом станке. */
  manual: ManualJob | null;
  link_ok: boolean;
  /** Почему ERP не управляет станком сейчас (нет связи, открыт пульт на ПК); «» — управляет. */
  problem: string;
  can_edit: boolean;
  can_control: boolean;
}

export type RunRequest =
  | { kind: "cutting"; group_id: string; length_mm: string; values?: Record<string, string> }
  | { kind: "manual"; job_id: string; values?: Record<string, string> };

export interface RunClose {
  run_id: string;
  version?: number;
  good?: number;
  defect?: number;
  defect_reason?: string;
  material?: "pending" | "not_issued";
  material_note?: string;
}

/** Закрытый прогон группы резки, ещё не сданный: «Сдать группу» подставляет его число. */
export interface CuttingMachineRun {
  run_id: string;
  inventory: string;
  length_mm: string;
  count: number | null;
  closed_at: string;
  closed_by: string;
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
  run: (inventory: string, signal?: AbortSignal) => api<MachineRunState>(`${card(inventory)}/run`, { signal }),
  startRun: (inventory: string, body: RunRequest, confirmState: number | null) =>
    api<MachineCommand>(`${card(inventory)}/run`, post({ ...body, confirm_state: confirmState })),
  closeRun: (inventory: string, body: RunClose) =>
    api<{ run: MachineRun; manual: ManualJob | null }>(`${card(inventory)}/run/close`, post(body)),
  cuttingRuns: (groupId: string, signal?: AbortSignal) =>
    api<{ runs: CuttingMachineRun[] }>(`/api/production/machines/runs/cutting/${encodeURIComponent(groupId)}`, { signal }),
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
