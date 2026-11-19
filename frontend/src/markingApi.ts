import { ApiError, api, productionApi, type ProductionOperation } from "./api";

export interface MarkingRect { x: number; y: number; width: number; height: number }
export interface MarkingGeometry {
  width_mm: number; height_mm: number; rotation: 0 | 90 | 180 | 270; feed_direction: "height" | "width";
  zones: MarkingRect[]; blank_zones: MarkingRect[]; offset_x_mm: number; offset_y_mm: number;
  min_font_mm: number; max_font_mm: number; max_lines: number; alignment: "center" | "left" | "right";
  /** Labels go out one by one through the peel-off dispenser; the agent refuses a job without ^O1. */
  peel?: boolean;
}
export interface MarkingProfile {
  id: string; equipment_id: string | null; name: string; version: number; calibrated: boolean; geometry: MarkingGeometry;
}
/** «Очистить принтер»: the latest command on the bound queue and how it ended. */
export interface MarkingPrinterCommand {
  id: string; kind: "clear"; status: "pending" | "taken" | "done" | "failed"; status_label: string; reason: string;
  requested_by: string; created_at: string; finished_at: string | null; message: string;
  result: { removed_jobs?: number; buffer_cleared?: boolean };
}
export interface MarkingBinding { version: number; machine_id: string; printer: string; command?: MarkingPrinterCommand | null
  /** Batch labels still waiting for this queue: they keep printing after «Очистить принтер» unless cancelled too. */
  pending_jobs?: number }
export interface MarkingMachine {
  equipment_id: string; inventory_number: string; name: string; profiles: MarkingProfile[]; binding: MarkingBinding | null;
}
export interface MarkingPrinter {
  machine_id: string; pc: string; name: string; driver: string; port: string; kind: string; present: boolean; online: boolean;
  ip?: string;
  /** Why the agent holds marking on this queue (peel-off check failed); empty — printing. */
  hold?: string;
}
export interface MarkingCatalog { machines: MarkingMachine[]; printers: MarkingPrinter[] }
export type MarkingSource = { task_id: string; operation_id: string; version: number } | { group_id: string; version: number };
export interface MarkingRequest { profile_id: string; profile_version: number; source: MarkingSource; selected: string[] }
export interface MarkingDimensions { width_mm: number; height_mm: number; orientation: string; dpi: number }
export interface MarkingRaster extends MarkingDimensions { png_b64: string; sha256: string }
/** `halves` — the text of each text zone: «X1 | X2» in the ETK prints X1 on the left half of a flag and X2 on the right. */
export interface MarkingLabel { key: string; task_id: string; text: string; material: string; quantity: number; serial: number | null; png_b64: string; sha256: string; halves?: string[] }
export interface MarkingPreview extends MarkingDimensions {
  manifest_hash: string; profile_id: string; profile_version: number; binding_version: number;
  source_snapshot: unknown; labels: MarkingLabel[]; total: number;
}
export type MarkingStatus = "pending" | "taken" | "spooled" | "failed" | "unknown" | "cancelled";
export type MarkingPrintState = "queued" | "printed" | "error" | "cancelled" | "untracked";
export interface MarkingJob { id: string; sequence: number; status: MarkingStatus; status_label: string; message: string; spool_job_id?: number | string | null; print_state?: MarkingPrintState | null
  text?: string; serial?: number | null
  /** The label failed its peel-off check and stops the rest of its batch (reprint it or continue without it). */
  halted?: boolean }
export interface MarkingBatch { id: string; status: MarkingStatus; status_label: string; total: number; jobs: MarkingJob[]; reprint_of: string | null
  // Honest print progress (agent 2.9.6+ watches the Windows queue); absent on older servers.
  printed?: number; queued?: number; print_errors?: number; cancelled?: number; untracked?: number
  progress_label?: string | null; stuck?: boolean; stuck_label?: string | null
  /** Jobs withdrawn by «Отменить оставшиеся» and still waiting for the agent. */
  withdrawn?: number; pending?: number; reason?: string
  /** A failed peel-off check stopped the batch: why, in Russian. */
  halted?: boolean; halt_label?: string | null }
export type MarkingReprintRequest = ({ sequences: number[] } | { serial_from: number; serial_to: number }) & { reason: string; idempotency_key: string };
export interface MarkingBatchRequest extends MarkingRequest { manifest_hash: string; idempotency_key: string; reprint_of?: string; reason?: string }

const root = "/api/production/marking";
const post = <T>(path: string, body: unknown, safe = false) => api<T>(root + path, { method: "POST", body: JSON.stringify(body), safe });
export const markingApi = {
  machines: (signal?: AbortSignal) => api<MarkingCatalog>(`${root}/machines`, { signal }),
  preview: (body: MarkingRequest) => post<MarkingPreview>("/preview", body),
  enqueue: (body: MarkingBatchRequest) => post<MarkingBatch>("/batches", body),
  batch: (id: string, signal?: AbortSignal) => api<MarkingBatch>(`${root}/batches/${encodeURIComponent(id)}`, { signal }),
  profile: (profile: MarkingProfile, geometry: MarkingGeometry, calibrated: boolean, name = profile.name) => api<MarkingProfile>(`${root}/profiles/${encodeURIComponent(profile.id)}`, {
    method: "PUT", body: JSON.stringify({ version: profile.version, geometry, calibrated, name }),
  }),
  bind: (equipmentId: string, body: MarkingBinding) => api<MarkingBinding>(`${root}/machines/${encodeURIComponent(equipmentId)}/binding`, {
    method: "PUT", body: JSON.stringify(body),
  }),
  calibration: (profile: MarkingProfile, text: string, print: boolean, idempotencyKey?: string) =>
    post<MarkingRaster & { batch_id?: string }>(`/profiles/${encodeURIComponent(profile.id)}/calibration`, {
      version: profile.version, text, print, ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
    }),
  resolve: (batchId: string, jobId: string, outcome: "spooled" | "failed", reason: string) =>
    post<MarkingBatch>(`/batches/${encodeURIComponent(batchId)}/jobs/${encodeURIComponent(jobId)}/resolve`, { outcome, reason }),
  cancelRest: (batchId: string, reason: string) => post<MarkingBatch>(`/batches/${encodeURIComponent(batchId)}/cancel`, { reason }),
  reprint: (batchId: string, body: MarkingReprintRequest) => post<MarkingBatch>(`/batches/${encodeURIComponent(batchId)}/reprint`, body),
  clearPrinter: (equipmentId: string, reason: string, cancelPending = false) =>
    post<MarkingPrinterCommand & { cancelled_jobs?: number }>(`/machines/${encodeURIComponent(equipmentId)}/printer/clear`, { reason, cancel_pending: cancelPending }),
  printerCommand: (equipmentId: string, signal?: AbortSignal) =>
    api<MarkingPrinterCommand | null>(`${root}/machines/${encodeURIComponent(equipmentId)}/printer/command`, { signal }),
};

/** «3, 5–7» → [3, 5, 6, 7]; null when the text is not a list of label numbers. */
export function parseSequences(text: string): number[] | null {
  const result = new Set<number>();
  for (const part of text.split(/[,;\s]+/).filter(Boolean)) {
    const match = /^(\d+)(?:[-–—](\d+))?$/.exec(part);
    if (!match) return null;
    const first = Number(match[1]), last = Number(match[2] ?? match[1]);
    if (first < 1 || last < first || last - first > 500) return null;
    for (let value = first; value <= last; value += 1) result.add(value);
  }
  return result.size ? [...result].sort((a, b) => a - b) : null;
}

/** Divider of flag halves in a label text, as the server reads it (marking_render.halves). */
export const MARKING_HALF = "|";
/** Two halves → «ЛЕВО | ПРАВО»; one text → as is (the same on both halves). */
export const joinHalves = (left: string, right: string) => `${left.trim()} ${MARKING_HALF} ${right.trim()}`;
export function splitHalves(text: string): [string, string] | null {
  const parts = text.split(MARKING_HALF).map((part) => part.trim());
  return parts.length === 2 ? [parts[0], parts[1]] : null;
}

export function boundPrinter(catalog: MarkingCatalog, machine: MarkingMachine | undefined) {
  return catalog.printers.find((printer) => printer.machine_id === machine?.binding?.machine_id && printer.name === machine.binding.printer && printer.kind === "gdi_png");
}

/** My queue contains one task entry per operation; grouped cutting rows live separately. */
export async function markingTaskSource(taskId: string, focus?: { position?: number; positions?: number[]; code?: string }): Promise<MarkingSource> {
  const position = focus?.position ?? focus?.positions?.[0];
  const code = focus?.code;
  const matches = (operation: ProductionOperation) => position != null
    ? operation.etk_ops?.length ? operation.etk_ops.some((item) => item.position === position) : operation.position === position
    : Boolean(code && (operation.code === code || operation.etk_ops?.some((item) => item.code === code)));
  let mine;
  try { mine = await productionApi.mine(); }
  catch (problem) { if (!(problem instanceof ApiError && problem.status === 403)) throw problem; }
  const sources: MarkingSource[] = [];
  for (const task of mine?.tasks.filter((item) => item.id === taskId) ?? []) {
    for (const operation of task.operations.filter(matches)) sources.push({ task_id: task.id, operation_id: operation.id, version: task.version });
  }
  for (const group of mine?.cutting_groups ?? []) {
    for (const member of group.rows.filter((row) => row.task_id === taskId && row.membership_state === "active")) {
      const instruction = await productionApi.operationEtk(taskId, member.operation_id);
      if (position != null ? instruction.focus.position === position || instruction.focus.positions?.includes(position) : Boolean(code && instruction.focus.code === code)) {
        sources.push({ task_id: taskId, operation_id: member.operation_id, version: member.task_version });
      }
    }
  }
  if (!sources.length) {
    const current = (await productionApi.list(taskId)).tasks.find((item) => item.id === taskId);
    if (!current) throw new Error("Задание не найдено — обновите инструкцию");
    for (const operation of current.operations.filter(matches)) sources.push({ task_id: taskId, operation_id: operation.id, version: current.version });
  }
  const unique = [...new Map(sources.map((item) => ["operation_id" in item ? item.operation_id : "", item])).values()];
  if (unique.length !== 1) throw new Error("Откройте инструкцию конкретной операции задания");
  return unique[0];
}
