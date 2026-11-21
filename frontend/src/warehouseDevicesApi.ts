import { api } from "./api";

/* «Принтеры склада» в «Оборудовании» (владелец 20.11.2026: «по принтерам тоже функционала нет никакого»): карточки
   принтера этикеток и МФУ. Сервер: `warehouse/print_scan_api.py`, очереди — `label_jobs.py`, `document_print.py`,
   `plain_scans.py`. */

export type WarehouseStation =
  | { kind: "labels" }
  | { kind: "mfp"; machine_id: string; scanners: string[]; printers: string[] };

export interface LabelJob {
  id: string;
  kind: string;
  /** «ГРН 123/2», «Пробная этикетка», «Этикетка СИ», «Партия удалена». */
  what: string;
  article: string;
  name: string;
  warehouse: string;
  lot_id: string | null;
  copies: number;
  by: string;
  at: string | null;
  status: string;
  finished_at: string | null;
  error: string;
}

export interface LabelPrinterView {
  now: string;
  online: boolean;
  agent: { last_seen: string | null; host: string; printer: string; queue: number; last_error: string | null };
  jobs: LabelJob[];
}

export type ScanColor = "color" | "gray";

export interface PlainScan {
  id: string;
  status: "pending" | "claimed" | "done" | "failed";
  error: string;
  by: string;
  at: string;
  finished_at: string | null;
  color: ScanColor | "";
  dpi: number | null;
  pages: number | null;
  append_to: string | null;
  scanner: string;
  /** Ссылка на PDF; «» — файла ещё нет. */
  file: string;
}

export interface DocumentPrint {
  id: string;
  title: string;
  source: "upload" | "test" | "scan";
  pages: number;
  copies: number;
  printer: string;
  status: "pending" | "taken" | "spooled" | "failed" | "unknown" | "cancelled";
  error: string;
  by: string;
  at: string;
  finished_at: string | null;
}

export interface MfpView {
  pc: { id: string; name: string; version: string; last_seen: string | null };
  online: boolean;
  scanners: { id: string; name: string; present: boolean }[];
  printers: { name: string; driver: string; present: boolean }[];
  scans: PlainScan[];
  prints: DocumentPrint[];
}

const mfpPath = (machineId: string) => `/api/stock/devices/mfp/${encodeURIComponent(machineId)}`;

export const warehouseDevicesApi = {
  labels: (signal?: AbortSignal) => api<LabelPrinterView>("/api/stock/devices/labels", { signal }),
  testLabel: () => api<{ status: string }>("/api/stock/labels/test", { method: "POST" }),
  reprintLot: (lotId: string, copies: number) =>
    api<{ status: string }>(`/api/stock/batches/${encodeURIComponent(lotId)}/label`, { method: "POST", body: JSON.stringify({ copies }) }),
  mfp: (machineId: string, signal?: AbortSignal) => api<MfpView>(mfpPath(machineId), { signal }),
  scan: (machineId: string, fields: { scanner: string; color: ScanColor; dpi: number; append_to?: string }) =>
    api<PlainScan>(`${mfpPath(machineId)}/scan`, { method: "POST", body: JSON.stringify(fields) }),
  cancelScan: (id: string) => api<PlainScan>(`/api/stock/devices/scans/${encodeURIComponent(id)}`, { method: "DELETE" }),
  /** `key` — ключ повтора: тот же ключ с тем же запросом возвращает уже поставленное задание, а не печатает снова. */
  print: (machineId: string, printer: string, copies: number, file: File, key: string) => {
    const form = new FormData();
    form.append("file", file);
    const query = new URLSearchParams({ printer, copies: String(copies), idempotency_key: key });
    return api<DocumentPrint>(`${mfpPath(machineId)}/print?${query}`, { method: "POST", body: form });
  },
  testPage: (machineId: string, printer: string, key: string) =>
    api<DocumentPrint>(`${mfpPath(machineId)}/print-test`, { method: "POST", body: JSON.stringify({ printer, idempotency_key: key }) }),
  printScan: (scanId: string, printer: string, copies: number, key: string) =>
    api<DocumentPrint>(`/api/stock/devices/scans/${encodeURIComponent(scanId)}/print`, {
      method: "POST", body: JSON.stringify({ printer, copies, idempotency_key: key }) }),
  cancelPrint: (id: string) => api<DocumentPrint>(`/api/stock/devices/prints/${encodeURIComponent(id)}`, { method: "DELETE" }),
};
