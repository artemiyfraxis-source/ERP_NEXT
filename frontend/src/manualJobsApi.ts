import { api } from "./api";

/* «Ручная работа» (план «Станки вне проекта», срез S1, 19.11.2026): работа на любом станке вне проекта, счёт вручную.
   Сервер: `workflows/manual_jobs_api.py`, правила и таблицы — `production/manual_jobs.py`. */

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
}

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
  list: (dateFrom: string, dateTo: string, equipmentId = "", signal?: AbortSignal) => {
    const query = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
    if (equipmentId) query.set("equipment_id", equipmentId);
    return api<ManualJobsList>(`${BASE}?${query}`, { signal });
  },
  options: (signal?: AbortSignal) => api<ManualJobOptions>(`${BASE}/options`, { signal }),
  start: (body: ManualJobStart) => api<ManualJob>(BASE, { method: "POST", body: JSON.stringify(body) }),
  finish: (id: string, body: ManualJobFinish) =>
    api<ManualJob>(`${BASE}/${encodeURIComponent(id)}/finish`, { method: "POST", body: JSON.stringify(body) }),
  cancel: (id: string, version: number, reason: string) =>
    api<ManualJob>(`${BASE}/${encodeURIComponent(id)}/cancel`, { method: "POST", body: JSON.stringify({ version, reason }) }),
};
