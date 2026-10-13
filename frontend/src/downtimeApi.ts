import { useEffect, useState } from "react";
import { api, failureText } from "./api";

/* Простои с причинами: «Простой» / «Закончить простой» на карточке задания и отчёт за период. */

export type DowntimeReason = "no_material" | "no_drawing" | "equipment" | "waiting_qc" | "other";

/** Причины в порядке списка сервера (`downtime.py` REASON_NAMES). */
export const DOWNTIME_REASONS: Array<{ key: DowntimeReason; name: string }> = [
  { key: "no_material", name: "Нет материала" },
  { key: "no_drawing", name: "Нет чертежа / ТК" },
  { key: "equipment", name: "Оборудование" },
  { key: "waiting_qc", name: "Ждёт ОТК" },
  { key: "other", name: "Другое" },
];

export interface Downtime {
  id: string;
  task_id: string | null;
  operation_id: string | null;
  operator_id: string | null;
  operator_name: string;
  whole_task: boolean;
  reason: DowntimeReason;
  reason_name: string;
  comment: string;
  started_at: string;
  started_by_name: string;
  ended_at: string | null;
  ended_by_name: string;
  open: boolean;
  seconds: number;
  project: string;
  task_number: string;
  item: string;
  stage: string;
  /** Отчёт: часть простоя, попавшая в период. */
  period_seconds?: number;
}

export type DowntimeGroup = "reason" | "operator";

export interface DowntimeReportRow {
  key: string;
  label: string;
  count: number;
  seconds: number;
  open: number;
  /** По сотрудникам: его работа по журналу за период, сек. */
  work_seconds: number | null;
}

export interface DowntimeReport {
  by: DowntimeGroup;
  date_from: string;
  date_to: string;
  rows: DowntimeReportRow[];
  total_seconds: number;
  total_count: number;
  work_seconds: number;
  entries: Downtime[];
}

export interface StartDowntime {
  task_id: string;
  operation_id?: string | null;
  reason: DowntimeReason;
  comment: string;
  whole_task?: boolean;
}

const behalf = (onBehalfOf?: string) => (onBehalfOf ? `?on_behalf_of=${encodeURIComponent(onBehalfOf)}` : "");

export const downtimeApi = {
  open: () => api<{ downtimes: Downtime[] }>("/api/production/downtime/open"),
  start: (body: StartDowntime, onBehalfOf?: string) =>
    api<{ downtime: Downtime }>(`/api/production/downtime${behalf(onBehalfOf)}`, { method: "POST", body: JSON.stringify(body) }),
  stop: (id: string, onBehalfOf?: string) =>
    api<{ downtime: Downtime }>(`/api/production/downtime/${id}/stop${behalf(onBehalfOf)}`, { method: "POST" }),
  report: (dateFrom: string, dateTo: string, by: DowntimeGroup) =>
    api<DowntimeReport>(`/api/production/downtime/report?date_from=${dateFrom}&date_to=${dateTo}&by=${by}`),
};

/** «1 ч 05 мин», «25 мин», «0 мин». */
export function downtimeDuration(seconds: number): string {
  const minutes = Math.max(0, Math.round(seconds / 60));
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return hours ? `${hours} ч ${String(rest).padStart(2, "0")} мин` : `${rest} мин`;
}

/* Открытые простои цеха — одна загрузка на все карточки экрана: каждая карточка берёт свой по заданию. */
let snapshot: Downtime[] | null = null;
let failure = "";
let inflight: Promise<void> | null = null;
const listeners = new Set<() => void>();

function notify() { listeners.forEach((listener) => listener()); }

export function refreshOpenDowntimes(): Promise<void> {
  if (inflight) return inflight;
  inflight = downtimeApi.open()
    .then((result) => { snapshot = Array.isArray(result?.downtimes) ? result.downtimes : []; failure = ""; })
    .catch((reason) => { failure = failureText(reason, "Простои не загружены"); })
    .finally(() => { inflight = null; notify(); });
  return inflight;
}

/** Для тестов: забыть загруженное. */
export function resetOpenDowntimes() { snapshot = null; failure = ""; inflight = null; }

let timer: ReturnType<typeof setInterval> | null = null;
const REFRESH_MS = 60_000;

/** Открытые простои; первая карточка на экране загружает их заново, пока экран открыт — раз в минуту. */
export function useOpenDowntimes(): { items: Downtime[] | null; error: string } {
  const [, setTick] = useState(0);
  useEffect(() => {
    const listener = () => setTick((value) => value + 1);
    listeners.add(listener);
    if (listeners.size === 1) {
      void refreshOpenDowntimes();
      timer = setInterval(() => void refreshOpenDowntimes(), REFRESH_MS);
    }
    return () => {
      listeners.delete(listener);
      if (!listeners.size && timer) { clearInterval(timer); timer = null; }
    };
  }, []);
  return { items: snapshot, error: failure };
}
