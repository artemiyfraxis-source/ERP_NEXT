import { useRef, useState } from "react";
import type { WarehouseReceiptScan, WarehouseScanJob, WarehouseScanStart } from "./api";
import { useScanAgent } from "./ScanAgent";

/** Where a УПД is scanned and read: the scanner queue of the warehouse PC, a file, a merge of sheets. */
export interface UpdScanApi {
  startScanJob: (agentId?: string, targetScanner?: string) => Promise<WarehouseScanStart>;
  scanJob: (id: string) => Promise<WarehouseScanJob>;
  cancelScanJob: (id: string) => Promise<WarehouseScanJob>;
  scanFile: (file: File) => Promise<WarehouseScanJob>;
  mergeScans: (files: string[]) => Promise<WarehouseScanJob>;
}

export type UpdScanStatus = "" | "pending" | "claimed" | "recognising";

const POLL_MS = 1500;
const SCANNER_WAIT_MS = 130_000;
/** A model reads a УПД page in 8–100 s, its fallback longer: the answer is waited for up to 4 minutes. */
const RECOGNISE_WAIT_MS = 240_000;
export const isEdoFile = (file: File) => /\.(xml|zip)$/i.test(file.name) || /^(application|text)\/(xml|zip)$/.test(file.type);

class ScanStopped extends Error {}

/** «Сканировать» / «Файл…» / «+ Страница» of a УПД: the job is polled until its answer, the sheets after the first are
 *  glued into one PDF by the server and the merged answer is what comes back. `onAnswer` gets the whole document's
 *  answer each time (with all its pages). */
export function useUpdScan(api: UpdScanApi, onAnswer: (answer: WarehouseReceiptScan, pages: number) => void) {
  const [scanning, setScanning] = useState(false);
  const [status, setStatus] = useState<UpdScanStatus>("");
  const [error, setError] = useState("");
  const [pages, setPagesState] = useState<string[]>([]);
  const [edo, setEdo] = useState(false);
  const pagesRef = useRef<string[]>([]);
  const jobId = useRef<string | null>(null);
  const aborted = useRef(false);
  const agent = useScanAgent(api.startScanJob);

  function setPages(next: string[]) {
    pagesRef.current = next;
    setPagesState(next);
  }

  async function answerOf(first: WarehouseScanJob): Promise<WarehouseReceiptScan> {
    let current = first;
    const deadline = Date.now() + RECOGNISE_WAIT_MS;
    for (;;) {
      if (aborted.current) throw new ScanStopped("");
      if (current.result) return current.result;
      if (current.status === "failed") throw new Error(current.error || "Не удалось распознать УПД");
      setStatus("recognising");
      if (Date.now() >= deadline) throw new ScanStopped("УПД не распознан за 4 мин — проведите поставку вручную");
      await new Promise((resolve) => window.setTimeout(resolve, POLL_MS));
      if (aborted.current) throw new ScanStopped("");
      current = await api.scanJob(current.id);
    }
  }

  async function takePages(results: WarehouseReceiptScan[]) {
    setEdo(false);
    const next = [...pagesRef.current, ...results.map((result) => result.scan_file)];
    if (next.length < 2) {
      setPages(next);
      onAnswer(results[0], next.length);
      return;
    }
    const merged = await answerOf(await api.mergeScans(next));
    setPages(next);
    onAnswer(merged, next.length);
  }

  function failed(reason: unknown, fallback: string) {
    if (aborted.current) return;
    setError(reason instanceof Error && reason.message ? reason.message : fallback);
  }

  async function fromFiles(files: File[]) {
    if (!files.length) return;
    setScanning(true);
    setError("");
    aborted.current = false;
    try {
      if (files.some(isEdoFile)) {
        if (files.length > 1) throw new Error("Файл ЭДО — целый документ: выберите один файл");
        const result = await answerOf(await api.scanFile(files[0]));
        setPages([]);
        setEdo(true);
        onAnswer(result, 1);
        return;
      }
      const results: WarehouseReceiptScan[] = [];
      for (const file of files) results.push(await answerOf(await api.scanFile(file)));
      await takePages(results);
    } catch (reason) {
      failed(reason, "Не удалось разобрать УПД");
    } finally {
      setStatus("");
      setScanning(false);
    }
  }

  /** «Сканировать»: the job goes to the warehouse PC, polled every 1,5 s for up to 130 s. `fresh` — a new document. */
  async function fromScanner(fresh = false) {
    if (fresh) { setPages([]); setEdo(false); }
    setScanning(true);
    setError("");
    setStatus("pending");
    aborted.current = false;
    jobId.current = null;
    try {
      const job = await agent.begin();
      if (!job) return; // нет агента / нужно выбрать ПК: открыто окно
      jobId.current = job.id;
      const deadline = Date.now() + SCANNER_WAIT_MS;
      let current = job;
      for (;;) {
        if (aborted.current) return;
        if (current.status === "done") { await takePages([await answerOf(current)]); return; }
        if (current.status === "failed") { setError(current.error || "Сканер не ответил"); return; }
        setStatus(current.status === "claimed" ? "claimed" : "pending");
        if (Date.now() >= deadline) {
          setError("ПК склада не ответил за 130 с. Проверьте, что он включён и агент запущен");
          void api.cancelScanJob(job.id).catch(() => undefined);
          return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, POLL_MS));
        if (aborted.current) return;
        current = await api.scanJob(job.id);
      }
    } catch (reason) {
      failed(reason, "Не удалось отсканировать УПД");
    } finally {
      jobId.current = null;
      setStatus("");
      setScanning(false);
    }
  }

  function cancel() {
    aborted.current = true;
    const id = jobId.current;
    if (id) void api.cancelScanJob(id).catch(() => undefined);
    setStatus("");
    setScanning(false);
  }

  const statusText = error || (status === "recognising" ? "Распознаю УПД…" : status === "claimed" ? "Сканирует…"
    : status === "pending" ? "Положите УПД на стекло сканера — ждём ПК склада…" : "");
  /** Окно установки агента / выбора ПК поверх экрана (или null); рисуется рядом с кнопками «Сканировать». */
  const agentDialogs = () => agent.dialogs(() => void fromScanner());
  return { scanning, status, error, statusText, pages, edo, fromFiles, fromScanner, cancel, agentDialogs };
}
