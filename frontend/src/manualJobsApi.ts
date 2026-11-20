import { api } from "./api";

/* «Ручная работа» (план «Станки вне проекта», срез S1, 19.11.2026): работа на любом станке вне проекта, счёт вручную.
   Сервер: `workflows/manual_jobs_api.py`, материал — `workflows/manual_job_stock.py` (срез S3), правила и таблицы —
   `production/manual_jobs.py`. */

export type ManualJobTone = "ok" | "warn" | "danger" | "info" | "neutral";
export type ManualJobMaterial = "pending" | "issued" | "not_issued";

export interface ManualJob {
  id: string;
  /** «Р-000001» — так читают люди; `plc_number` — «R-000001» для станка (позже). */
  number: string;
  plc_number: string;
  equipment_id: string;
  inventory: string;
  machine: string;
  status: "running" | "done" | "cancelled";
  status_text: string;
  tone: ManualJobTone;
  purpose: string;
  wire: string;
  contact: string;
  length_mm: string | null;
  quantity_planned: number | null;
  reference_id: string | null;
  count_source: "plc" | "crimp_log" | "manual";
  count_text: string;
  good: number | null;
  defect: number | null;
  defect_reason: string;
  machine_count: number | null;
  material: ManualJobMaterial;
  /** «» пока работа не завершена. */
  material_text: string;
  material_tone: ManualJobTone;
  material_note: string;
  cancel_reason: string;
  operator: string;
  finished_by: string;
  started_at: string;
  finished_at: string | null;
  version: number;
}

export interface ManualJobsList {
  date_from: string;
  date_to: string;
  rows: ManualJob[];
  can_edit: boolean;
  /** Правка раздела «Склад» — может «Списать с партии». */
  can_issue?: boolean;
  /** Может решить по материалу («Не списывать»): Производство, Задания или Склад на правку. */
  can_decide?: boolean;
  /** Сколько завершённых работ «Ждёт решения склада» — за любые даты. */
  waiting_count?: number;
}

/** Партия склада, которую можно списать на работу; числа — строки сервера («12.5»). */
export interface ManualJobLot {
  id: string;
  version: number;
  warehouse: string;
  grn: string;
  package: string;
  article: string;
  name: string;
  unit: string;
  storage_location: string;
  balance: string;
  reserved: string;
  available: string;
  /** Склад станка. */
  here: boolean;
  /** «exact» | «fuzzy» — как партия совпала с проводом / контактом работы; «» — найдена поиском. */
  match: string;
}

export interface ManualJobIssue {
  lot_id: string;
  writeoff_id: string;
  quantity: string;
  warehouse: string;
  grn: string;
  package: string;
  name: string;
  unit: string;
  by: string;
  at: string | null;
}

export interface ManualJobMaterialView {
  job: ManualJob;
  /** Склад станка (ЦАР / ЖУК), «» — не указан в БД.ОБ. */
  site: string;
  /** Годные + брак; null — работа не завершена. */
  pieces: number | null;
  length_mm: string | null;
  wire: { label: string; lots: ManualJobLot[] };
  contact: { label: string; lots: ManualJobLot[] };
  found: ManualJobLot[];
  issues: ManualJobIssue[];
  can_issue: boolean;
  can_decide: boolean;
}

export interface ManualJobIssueLine { lot_id: string; lot_version: number; quantity: string }

export interface ManualJobMachine { id: string; inventory: string; name: string; running: string | null }
export interface ManualJobReference { id: string; equipment_id: string; wire: string; contact: string; version: number; label: string }

export interface ManualJobOptions {
  machines: ManualJobMachine[];
  wires: string[];
  contacts: { label: string; maker: string }[];
  references: ManualJobReference[];
  can_edit: boolean;
}

export interface ManualJobStart {
  equipment_id: string;
  purpose: string;
  wire: string;
  contact: string;
  length_mm: string;
  quantity_planned: number | null;
  reference_id: string | null;
}

export interface ManualJobFinish {
  version: number;
  good: number;
  defect: number;
  defect_reason: string;
  material: "pending" | "not_issued";
  material_note: string;
}

const BASE = "/api/production/manual-jobs";

export const manualJobsApi = {
  list: (dateFrom: string, dateTo: string, equipmentId = "", signal?: AbortSignal, waiting = false) => {
    const query = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
    if (equipmentId) query.set("equipment_id", equipmentId);
    if (waiting) query.set("waiting", "true");
    return api<ManualJobsList>(`${BASE}?${query}`, { signal });
  },
  options: (signal?: AbortSignal) => api<ManualJobOptions>(`${BASE}/options`, { signal }),
  start: (body: ManualJobStart) => api<ManualJob>(BASE, { method: "POST", body: JSON.stringify(body) }),
  finish: (id: string, body: ManualJobFinish) =>
    api<ManualJob>(`${BASE}/${encodeURIComponent(id)}/finish`, { method: "POST", body: JSON.stringify(body) }),
  cancel: (id: string, version: number, reason: string) =>
    api<ManualJob>(`${BASE}/${encodeURIComponent(id)}/cancel`, { method: "POST", body: JSON.stringify({ version, reason }) }),
  /** Окно «Материал»: расход, партии под провод / контакт, `q` — поиск другой партии. */
  material: (id: string, q = "", signal?: AbortSignal) =>
    api<ManualJobMaterialView>(`${BASE}/${encodeURIComponent(id)}/material${q.trim() ? `?${new URLSearchParams({ q: q.trim() })}` : ""}`, { signal }),
  issue: (id: string, version: number, lines: ManualJobIssueLine[]) =>
    api<ManualJobMaterialView>(`${BASE}/${encodeURIComponent(id)}/material/issue`, { method: "POST", body: JSON.stringify({ version, lines }) }),
  notIssued: (id: string, version: number, note: string) =>
    api<ManualJobMaterialView>(`${BASE}/${encodeURIComponent(id)}/material/not-issued`, { method: "POST", body: JSON.stringify({ version, note }) }),
};
