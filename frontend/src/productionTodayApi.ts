import { api } from "./api";

/* «Сегодня» мастера участка (`production/today.py`, `workflows/production_today_api.py`): кто что делает, план на
   сегодня, просроченное, что стоит и почему. Только чтение. */

export interface TodayOperationRef {
  task_id: string;
  task_number: string;
  project: string;
  item: string;
  name: string;
  operation_id: string;
  position: number;
  /** Русское название этапа. */
  stage: string;
  /** Код карточки операции (может быть пустым). */
  code: string;
}

export type TodayPersonState = "работает" | "пауза" | "простой" | "отсутствует" | "свободен";

export interface TodayPerson {
  id: string;
  name: string;
  state: TodayPersonState;
  work: (TodayOperationRef & { since: string | null; paused_since: string | null }) | null;
  downtime: { reason: string; since: string; task_number: string; project: string; item: string; task_id: string | null } | null;
  absence: { kind: string; from: string; to: string } | null;
  /** Долей на сегодня и сколько из них сделано. */
  planned: number;
  done: number;
  next: (TodayOperationRef & { planned_start: string | null }) | null;
}

export interface TodayLine extends TodayOperationRef {
  operator_id: string;
  operator_name: string;
  crew: boolean;
  planned_start: string;
  planned_finish: string;
  started_at: string | null;
  finished_at: string | null;
  quantity: number;
  done: number;
  remaining: number;
  state: "не начата" | "в работе" | "сделано";
  /** Не сдана, а плановый конец прошёл. */
  late: boolean;
}

export interface TodayHanded {
  task_id: string;
  task_number: string;
  project: string;
  item: string;
  name: string;
  quantity: number;
  delivered: number;
  remaining: number;
  deadline: string | null;
}

export interface TodayLateTask {
  task_id: string;
  task_number: string;
  project: string;
  item: string;
  name: string;
  deadline: string;
  days: number;
  site: string;
}

export interface TodayLateProject {
  project: string;
  customer: string;
  deadline: string | null;
  ready: string | null;
  slack_days: number | null;
  hours: number;
}

export type TodayFix = "task" | "skills" | "techcards" | "issues" | "prodplan" | "workload";

export interface TodayStuck {
  kind: "downtime" | "blocker" | "skill" | "input" | "etk" | "kit";
  project: string;
  item: string;
  reason: string;
  task_id: string | null;
  task_number: string;
  operation_id: string | null;
  position: number | null;
  stage: string;
  operator_name: string;
  since: string | null;
  fix: TodayFix;
  fix_label: string;
}

export interface ShopToday {
  today: string;
  now: string;
  working_day: boolean;
  shift: { start: string; finish: string };
  site: string;
  sites: string[];
  can_switch: boolean;
  people: TodayPerson[];
  plan: TodayLine[];
  /** Задания, отданные участку целиком («Отдать в ЖУК»): их участок сдаёт «Сдано N шт», в план людей они не входят. */
  handed: TodayHanded[];
  overdue: { operations: TodayLine[]; tasks: TodayLateTask[]; projects: TodayLateProject[] };
  stuck: TodayStuck[];
}

export const productionTodayApi = {
  load: (site = "") => api<ShopToday>(`/api/production/today${site ? `?site=${encodeURIComponent(site)}` : ""}`),
};
