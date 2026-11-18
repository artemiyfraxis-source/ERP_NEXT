import { useEffect, useRef, useState } from "react";
import { api, failureText } from "./api";

/* «Станки» (владелец 18.11.2026: «в разделе производство — станки: всё, что мы подключили, и управление ими»), часть 1 —
   только чтение. Сервер: `workflows/machines_api.py`, правила и расшифровка ПЛК — `production/machines.py`. */

export type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

export interface MachineLink {
  code: "ok" | "port_busy" | "no_plc" | "no_service" | "stale" | "not_installed";
  text: string;
  tone: Tone;
}

export interface MachineRow {
  key: string;
  kind: "machine" | "printer" | "device";
  equipment_id: string | null;
  inventory: string;
  name: string;
  pc: string;
  ip: string;
  service: string;
  version: string;
  link: MachineLink | { code: string; text: string; tone: Tone };
  last_seen: string | null;
  device: string;
  state: { text: string; tone: Tone };
  /** Есть живая карточка (ОБ с мостом). */
  card: boolean;
}

export interface MachinesList {
  now: string;
  rows: MachineRow[];
}

export interface Ob019Live {
  state: { code: number | null; text: string; tone: Tone };
  alarm: { code: number | null; text: string; tone: Tone };
  mode: { code: number | null; text: string };
  job: {
    total_set: number | null; total_finished: number | null; batch_set: number | null; batch_finished: number | null;
    uph: number | null; program: number | null; order_quantity: number | null; order_finished: number | null;
    order_number: string | null; product: string | null; order_running: boolean | null;
  };
  wire: {
    length_mm: number | null; strip_front_mm: number | null; pull_front_mm: number | null; strip_back_mm: number | null;
    pull_back_mm: number | null; diameter_mm: number | null; knife_back_mm: number | null;
  };
}

export interface MachineCard {
  equipment_id: string;
  inventory: string;
  name: string;
  pc: string;
  ip: string;
  service: string;
  service_version: string;
  bridge_version: string;
  link: MachineLink;
  reported_at: string | null;
  age_seconds: number | null;
  error: string;
  /** Последние слова ПЛК; при потере связи остаются, `link` и `reported_at` говорят, что они старые. */
  live: Ob019Live | null;
}

export const machinesApi = {
  list: (signal?: AbortSignal) => api<MachinesList>("/api/production/machines", { signal }),
  card: (id: string, signal?: AbortSignal) => api<MachineCard>(`/api/production/machines/${encodeURIComponent(id)}`, { signal }),
};

export const POLL_MS = 3000;

/** Опрос раз в `period` мс, пока вкладка видна: скрытая вкладка не опрашивает, при возврате — сразу свежие данные. */
export function usePolled<T>(load: (signal: AbortSignal) => Promise<T>, period = POLL_MS, failure = "Не удалось загрузить станки") {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const loader = useRef(load);
  loader.current = load;
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | null = null;
    let stopped = false;
    const tick = async () => {
      if (stopped || document.visibilityState === "hidden") return;
      controller = new AbortController();
      try {
        setData(await loader.current(controller.signal));
        setError("");
      } catch (reason) {
        if (controller.signal.aborted) return;
        setError(failureText(reason, failure));
      }
      if (!stopped) timer = setTimeout(() => void tick(), period);
    };
    const visible = () => {
      if (document.visibilityState === "hidden") { clearTimeout(timer); controller?.abort(); return; }
      clearTimeout(timer);
      void tick();
    };
    void tick();
    document.addEventListener("visibilitychange", visible);
    return () => {
      stopped = true;
      clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", visible);
    };
  }, [period, failure]);
  return { data, error };
}
