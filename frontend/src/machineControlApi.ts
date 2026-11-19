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

/** Монтажный провод для «Провод вручную»: Ø жилы из базы проводов, есть ли записанная настройка на этом станке. */
export interface WireChoice {
  label: string;
  conductor_mm: string | null;
  has_recipe: boolean;
}

export interface TransferRow {
  key: string;
  label: string;
  unit: string;
  nominal: string | null;
  /** Откуда значение: «Задание», «ЭТК…», «Рецепт, версия N», «Ø жилы + 0,15», «Нет данных». */
  source: string;
  /** Поправка станка поверх номинала; null — значение из настройки провода, поправка не добавляется. */
  offset: string | null;
  value: string | null;
  live: string | null;
}

/** Что запишется на станок: по заданию резки ЭТК (`group_id`) или по проводу, выбранному вручную (`group_id: null`). */
export interface TransferPlan {
  group_id: string | null;
  number: string | null;
  wire: string;
  length_mm: string;
  quantity: number | null;
  rows: TransferRow[];
  job: { total_set: number | null; order_quantity: number | null; order_number: string; product: string };
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
  /** Значение станка, которое показал предпросмотр, мм: на станке уже другое — сервер откажет (409). */
  seen?: string;
  note?: string;
}

/** Ответ на правку: поправка станка (сервер считает «на станке − получилось»), ревизии ЭТК, что было на станке. */
export interface CorrectionResult {
  offset: string | null;
  etk: { card_id: string; project: string; revision: number }[];
  written?: string | null;
  measured?: string | null;
}

const card = (inventory: string) => `/api/production/machines/card/${encodeURIComponent(inventory)}`;
const post = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) });

export const machineControlApi = {
  window: (inventory: string, signal?: AbortSignal) => api<MachineWindow>(card(inventory), { signal }),
  control: (inventory: string, signal?: AbortSignal) => api<MachineControl>(`${card(inventory)}/control`, { signal }),
  write: (inventory: string, values: Record<string, string | number>, confirmState: number | null) =>
    api<MachineCommand>(`${card(inventory)}/write`, post({ values, confirm_state: confirmState })),
  choices: (inventory: string) => api<{ choices: TransferChoice[]; wires?: WireChoice[] }>(`${card(inventory)}/transfer`),
  plan: (inventory: string, groupId: string, lengthMm: string) =>
    api<TransferPlan>(`${card(inventory)}/transfer?group_id=${encodeURIComponent(groupId)}&length_mm=${encodeURIComponent(lengthMm)}`),
  wirePlan: (inventory: string, wire: string, lengthMm: string) =>
    api<TransferPlan>(`${card(inventory)}/transfer?wire=${encodeURIComponent(wire)}&length_mm=${encodeURIComponent(lengthMm)}`),
  transfer: (inventory: string, groupId: string, lengthMm: string, confirmState: number | null) =>
    api<MachineCommand>(`${card(inventory)}/transfer`, post({ group_id: groupId, length_mm: lengthMm, confirm_state: confirmState })),
  command: (inventory: string, command: ControlCommand, confirmState?: number | null) =>
    api<MachineCommand>(`${card(inventory)}/command`, post(command === "stop" ? { command }
      : { command, confirm_state: confirmState ?? null, hands_clear: true })),
  correct: (inventory: string, body: CorrectionBody) => api<CorrectionResult>(`${card(inventory)}/correction`, post(body)),
};

/** Состояния, в которых служба станка пишет настройки и ролики (сервер: machine_writes.IDLE_STATES). */
export const IDLE_STATES = [102, 107];

/** Пульт производителя PXS_HMI держит порт станка — словами наладчика (владелец 19.11.2026). */
export const PULT_OPEN = "На ПК станка открыт пульт PXS_HMI — закройте его, ERP ждёт";
const PULT_SIGNS = /PXS_HMI|busy|refused|Открыт пульт станка/i;

/** Отказ агента или сервера словами оператора: занятый пульт — одной фразой, остальное как есть. */
export function operatorText(text: string): string {
  return PULT_SIGNS.test(text) ? PULT_OPEN : text;
}

/** «14:05» по часам этого компьютера; пусто — пустая строка. */
export function clock(value: string | null | undefined): string {
  if (!value) return "";
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return "";
  return `${String(moment.getHours()).padStart(2, "0")}:${String(moment.getMinutes()).padStart(2, "0")}`;
}

/** Итог команды рядом с кнопкой: «отправлено…», «выполнено 14:05», «отказ: причина». */
export function commandResult(command: MachineCommand): { text: string; tone: Tone } {
  if (command.status === "queued" || command.status === "taken") return { text: "отправлено…", tone: "info" };
  if (command.status === "done") {
    const at = clock(command.finished_at);
    return { text: at ? `выполнено ${at}` : "выполнено", tone: "ok" };
  }
  return { text: `отказ: ${operatorText(command.error || command.status_text.toLowerCase())}`, tone: "danger" };
}

/** Когда команда станку возможна — та же таблица, что на сервере (production/machine_control.py) и в агенте (D766). */
export const CONTROL_STATES: Record<ControlCommand, number[] | null> = {
  stop: null, start: [102], reset: [100, 105, 107], alarm_clear: null,
};

export const CONTROL_NAMES: Record<ControlCommand, string> = {
  stop: "Стоп", reset: "Сброс", start: "Пуск", alarm_clear: "Сброс аварии",
};
