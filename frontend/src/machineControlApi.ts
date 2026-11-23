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
export type PulseCommand = "stop" | "reset" | "start" | "alarm_clear";
/** Пульт производителя на ПК станка: закрыть (ERP снова видит станок) или открыть (наладчику на ПК). */
export type PultCommand = "hmi_close" | "hmi_open";
export type ControlCommand = PulseCommand | PultCommand;

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
  /** Пульт на ПК: как его закрыли или открыли — graceful, forced (принудительно), none (не был открыт), started. */
  method?: "graceful" | "forced" | "none" | "started" | null;
  /** Пульт на ПК: код ответа агента; `still_open` — пульт не закрылся сам, карточка предлагает «Закрыть принудительно». */
  reason?: string | null;
  /** «Закрыть принудительно»: агент завершает программу пульта без вопроса ей. */
  force?: boolean;
  summary: string;
  fields: CommandChange[];
  error: string;
  created_by: string;
  created_at: string;
  finished_at: string | null;
  /** PLC-held length correction read around the measured-length pulse, in mm. */
  length_correction_before?: string | null;
  length_correction_after?: string | null;
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
  legacy?: boolean;
}

export interface MachineControl {
  /** Почему писать нельзя сейчас; «» — можно. */
  problem: string;
  state: number | null;
  commands: MachineCommand[];
  offsets: MachineOffset[];
  can_edit: boolean;
  length_correction?: { value: string | null; register: "D54"; legacy_offset: string };
  /** Право «Управление станками»: Сброс, Пуск, Сброс аварии. */
  can_control?: boolean;
  /** «Стоп» и любые команды станку: Производство или Задания; ОТК карточку только читает. */
  can_stop?: boolean;
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

/** Лазерная маркировка из ЭТК (сервер: workflows/ob019_marking.py): регистры, которые «Передать на станок» пишет вместе
 *  с заданием; `blocking` — сервер откажет в передаче (422 «Маркировка: …»). Значения переключателей — «Вкл»/«Выкл». */
export interface MarkingPlan {
  mode: "none" | "ends" | "full";
  mode_label: string;
  source: string;
  rows: { key: string; label: string; value: string }[];
  warnings: string[];
  blocking: string[];
}

/** Что запишется на станок: по заданию резки ЭТК (`group_id`) или по проводу, выбранному вручную (`group_id: null`). */
export interface TransferPlan {
  group_id: string | null;
  number: string | null;
  wire: string;
  length_mm: string;
  /** Тираж этой передачи: план минус уже сделанное по станку в идущем прогоне того же задания; null — провод выбран
   *  вручную, без задания. */
  quantity: number | null;
  planned?: number;
  done?: number;
  rows: TransferRow[];
  job: { total_set: number | null; order_quantity: number | null; order_number: string; product: string };
  missing: string[];
  blocking: string[];
  /** Маркировка из ЭТК; null — в ЭТК её нет (или провод выбран вручную), регистры маркировки не трогаются. */
  marking?: MarkingPlan | null;
  /** Atomic load guards returned by the group preview; absent for a hand-picked wire. */
  expected_source?: ExpectedSource;
  expected_active_run_id?: string | null;
}

export interface ExpectedSource {
  group_id: string;
  version: number;
  length_mm: string;
  plan_hash: string;
}

export interface TransferConfirmation {
  expected_source: ExpectedSource;
  expected_active_run_id: string | null;
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

/** Length correction queues a PLC command; strip corrections return the ERP offset and optional ETK revisions. */
export interface CorrectionResult {
  offset: string | null;
  etk: { card_id: string; project: string; revision: number }[];
  written?: string | null;
  measured?: string | null;
  /** Length correction is queued on the machine; success is established by the command result. */
  command?: MachineCommand;
}

/** «Выполнение задания» (владелец 19.11.2026): одна передача задания на станок и её счёт по T.Finish (D649).
 *  Сервер: `workflows/machine_runs_api.py`, правила — `production/machine_runs.py`. */
export type RunStage = "writing" | "failed" | "moved" | "ready" | "running" | "finished" | "reset" | "nolink" | "closed";

export interface MachineRun {
  id: string;
  kind: "cutting" | "manual" | "wire";
  wire?: string | null;
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
  expected_active_run_id?: string | null;
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
  | { kind: "wire"; wire: string; values?: Record<string, string> }
  | { kind: "manual"; job_id: string; values?: Record<string, string> };

export type ConfirmedRunRequest =
  | (Extract<RunRequest, { kind: "cutting" }> & TransferConfirmation)
  | (Extract<RunRequest, { kind: "manual" | "wire" }> & { expected_active_run_id: string | null });

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
  /** Сумма по всем закрытым и ещё не сданным прогонам этой длины; null — ни один не посчитан станком. */
  count: number | null;
  runs: number;
  /** Часть прогонов без счёта станка (счётчик сброшен или не прочитан). */
  unknown: boolean;
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
  choices: (inventory: string) => api<{ choices: TransferChoice[]; wires?: WireChoice[] }>(`${card(inventory)}/transfer`),
  plan: (inventory: string, groupId: string, lengthMm: string) =>
    api<TransferPlan>(`${card(inventory)}/transfer?group_id=${encodeURIComponent(groupId)}&length_mm=${encodeURIComponent(lengthMm)}`),
  wirePlan: (inventory: string, wire: string, lengthMm: string) =>
    api<TransferPlan>(`${card(inventory)}/transfer?wire=${encodeURIComponent(wire)}&length_mm=${encodeURIComponent(lengthMm)}`),
  transfer: (inventory: string, groupId: string, lengthMm: string, confirmState: number | null, confirmation: TransferConfirmation) =>
    api<MachineCommand>(`${card(inventory)}/transfer`, post({ group_id: groupId, length_mm: lengthMm, confirm_state: confirmState, ...confirmation })),
  command: (inventory: string, command: ControlCommand, confirmState?: number | null, force = false) =>
    api<MachineCommand>(`${card(inventory)}/command`, post(command === "stop" ? { command }
      : command === "hmi_close" || command === "hmi_open" ? { command, hands_clear: true, ...(force ? { force: true } : {}) }
      : { command, confirm_state: confirmState ?? null, hands_clear: true })),
  run: (inventory: string, signal?: AbortSignal) => api<MachineRunState>(`${card(inventory)}/run`, { signal }),
  startRun: (inventory: string, body: ConfirmedRunRequest, confirmState: number | null) =>
    api<MachineCommand>(`${card(inventory)}/run`, post({ ...body, confirm_state: confirmState })),
  closeRun: (inventory: string, body: RunClose) =>
    api<{ run: MachineRun; manual: ManualJob | null }>(`${card(inventory)}/run/close`, post(body)),
  resetRun: (inventory: string, expectedActiveRunId: string | null, confirmState: number | null) =>
    api<MachineCommand>(`${card(inventory)}/run/reset`, post({ expected_active_run_id: expectedActiveRunId, confirm_state: confirmState })),
  cuttingRuns: (groupId: string, signal?: AbortSignal) =>
    api<{ runs: CuttingMachineRun[] }>(`/api/production/machines/runs/cutting/${encodeURIComponent(groupId)}`, { signal }),
  correct: (inventory: string, body: CorrectionBody) => api<CorrectionResult>(`${card(inventory)}/correction`, post(body)),
};

/** Состояния, в которых служба станка пишет настройки и ролики (сервер: machine_writes.IDLE_STATES). */
export const IDLE_STATES = [102, 107];

/** Пульт производителя на ПК держит порт станка — словами наладчика, без имени программы (владелец 19.11.2026); тот же
 *  текст, что у сервера (production/machines.py HMI_OPEN). */
export const PULT_OPEN = "Открыт пульт станка на ПК — закройте его, чтобы ERP могла управлять станком";
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

/** Пульт не закрылся сам (агент: `still_open`) — карточка предлагает «Закрыть принудительно». */
export const PULT_STILL_OPEN = "still_open";

/** Итог команды пульту рядом с кнопкой — по самой команде: «закрывается…», «закрыт 14:05», «закрыт принудительно 14:05»,
 *  «Пульт не закрылся (возможно, ждёт ответа оператора)», «отказ: …» — слова сервера, как есть. */
export function pultResult(command: Pick<MachineCommand, "command" | "status" | "status_text" | "error" | "finished_at"
  | "method" | "reason" | "force">): { text: string; tone: Tone } {
  const closing = command.command === "hmi_close";
  if (command.status === "queued" || command.status === "taken") {
    return { text: !closing ? "открывается…" : command.force ? "закрывается принудительно…" : "закрывается…", tone: "info" };
  }
  if (command.status === "failed" && command.reason === PULT_STILL_OPEN) return { text: command.error, tone: "warn" };
  if (command.status === "done") {
    const at = clock(command.finished_at);
    const text = !closing ? "открыт" : command.method === "none" ? "пульт уже был закрыт"
      : command.method === "forced" ? "закрыт принудительно" : "закрыт";
    return { text: at && command.method !== "none" ? `${text} ${at}` : text, tone: "ok" };
  }
  return { text: `отказ: ${command.error || command.status_text.toLowerCase()}`, tone: "danger" };
}

/** Когда команда станку возможна — та же таблица, что на сервере (production/machine_control.py) и в агенте (D766). */
export const CONTROL_STATES: Record<PulseCommand, number[] | null> = {
  stop: null, start: [102], reset: [100, 105, 107], alarm_clear: null,
};

export const CONTROL_NAMES: Record<ControlCommand, string> = {
  stop: "Стоп", reset: "Сброс", start: "Пуск", alarm_clear: "Сброс аварии",
  hmi_close: "Закрыть пульт на ПК", hmi_open: "Открыть пульт на ПК",
};
