import { api } from "./api";

/* «Готово к запуску» (`production/launch_ready.py`, владелец 29.10.2026, идея №3): КБ с выданной ЭТК и набранным
   комплектом, у которых тираж ещё не запущен, и запуск задания в один клик — тем же путём, что «Создать задание». */

export interface LaunchReadyOperation {
  position: number;
  code: string;
  stage: string;
  description: string;
  seconds_per_unit: number;
  setup_seconds: number;
  /** Штук полуфабриката этапа на одно изделие (схема ПФ), обычно 1. */
  per_unit: number;
  etk_ops: { position: number; code: string; stage: string }[];
}

export interface LaunchReadyRow {
  techcard_id: string;
  techcard_version: number;
  techcard_revision: number | null;
  project: string;
  index: string;
  name: string;
  customer: string;
  /** Тираж КБ. */
  run: number;
  /** Уже запущено по КБ (тиражи не отменённых заданий). */
  launched: number;
  /** Остаток тиража: тираж − запущено — больше нельзя. */
  remainder: number;
  done: number;
  done_source: string;
  /** Предложенный тираж задания: тираж − max(запущено, сделано). */
  quantity: number;
  site: string;
  site_note: string;
  /** Статус заявки на комплект («Укомплектован», «КБ Выдана»…). */
  kit: string;
  /** Срок проекта. */
  deadline: string | null;
  /** Срок, с которым создаётся задание (срок техкарты). */
  task_deadline: string | null;
  warnings: string[];
  operations: LaunchReadyOperation[];
}

export interface LaunchReadyList {
  items: LaunchReadyRow[];
  waiting_kit: number;
  no_run: string[];
}

export interface LaunchReadyItem {
  techcard_id: string;
  techcard_version: number;
  launched: number;
  quantity: number;
}

export interface LaunchReadyResult {
  created: { id: string; number: string; project: string; item: string; quantity: number; techcard_id: string | null }[];
  refused: { techcard_id: string; reason: string }[];
}

export const launchReadyApi = {
  list: () => api<LaunchReadyList>("/api/production/launch-ready"),
  launch: (items: LaunchReadyItem[]) =>
    api<LaunchReadyResult>("/api/production/launch-ready/launch", { method: "POST", body: JSON.stringify({ items }) }),
};

/** Нормочасы задания: Σ (норма на штуку × штук на изделие × тираж + подготовка). */
export function launchHours(operations: LaunchReadyOperation[], quantity: number): number {
  return operations.reduce((sum, item) => sum + item.seconds_per_unit * item.per_unit * quantity + item.setup_seconds, 0) / 3600;
}
