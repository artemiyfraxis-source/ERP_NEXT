import { useCallback, useEffect, useRef, useState } from "react";
import { api, failureText } from "./api";
import type { WarehouseStation } from "./warehouseDevicesApi";

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
  /** Есть живая карточка (ОБ с агентом станка). */
  card: boolean;
  /** Другие ПК, которые сообщают тот же прибор (не вторая строка), «» — нет. */
  others: string;
  /** Из БД.ОБ: артикул (модель), участок ЦАР/ЖУК, операции и их семейство. */
  article: string;
  site: string;
  operations: string[];
  family: string;
  /** Станок: ERP достаёт его через агента (вкладка «С управлением»). */
  controlled?: boolean;
  /** Модель наладки: нет / простая / полная; `code: null` — модель не задана. */
  setup?: { code: SetupClass | null; label: string };
  /** Открытые задания наладки, по которым настройка ещё не записана («Ждёт наладки»). */
  pending_setup?: number;
  /** «Принтеры склада»: карточка принтера этикеток или МФУ (WarehouseDeviceCard.tsx). */
  station?: WarehouseStation;
}

export type SetupClass = "none" | "simple" | "full";

export interface MachinesList {
  now: string;
  rows: MachineRow[];
  /** Порядок семейств станков. */
  families?: string[];
}

/** Часть «Наладка» карточки станка: модель наладки, что стоит сейчас, эталоны, открытые задания, журнал. */
export interface UnitSetup {
  unit_id: string;
  inventory: string;
  name: string;
  model: string;
  requires_setup: { code: SetupClass | null; label: string };
  reason: string;
  parameters: { key: string; label: string; unit: string; source: string; source_field: string; register: string }[];
  configuration: { values: Record<string, string>; wire: string; contact: string; set_by: string; set_at: string | null; version: number } | null;
  references: { id: string; wire: string; contact: string; version: number; values: Record<string, string>; created_by: string; created_at: string }[];
  /** Открытые задания наладки, которые сотрудник видит в «Запросах на наладку»; `mine` — назначены ему,
   *  `can_confirm` — он может записать «Настроено по заданию» (назначенный наладчик или мастер площадки). */
  open_tasks: { id: string; version: number; status: string; operation: string; planned_start: string | null; planned_finish: string | null; mine: boolean; can_confirm: boolean }[];
  history: { at: string; by: string; operation: string; reason: string; values: Record<string, string> }[];
  online: boolean;
}

export interface ConfigurationDraft {
  expected_version: number;
  task_id: string | null;
  task_version?: number | null;
  values: Record<string, string>;
  reason: string;
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

export interface Ob099Live {
  state: { text: string; tone: Tone };
  /** Активные аварии W80 и предупреждения бегущей строки (W83/W84). */
  alarms: { code: string; text: string; tone: Tone }[];
  job: { product: string | null; wire: string | null; terminal: string | null; applicator: string | null; equipment_no: string | null };
  crimp: {
    height_mm: number | null; tolerance_mm: number | null; height_min_mm: number | null; height_max_mm: number | null;
    cfm_on: boolean | null; work_mode: boolean | null;
  };
  counters: { total_set: number | null; batch_set: number | null; total_done: number | null; batch_done: number | null };
  tools: { name: string; life: number | null; used: number | null; left: number | null; used_pct: number | null; tone: Tone }[];
}

/** Обжимы пресса за день цеха (по журналу BZW2.5) и его журнал аварий. */
export interface Ob099Today {
  day: string;
  total: number;
  ok: number;
  ng: number;
  ng_pct: number | null;
  ng_reasons: { text: string; count: number }[];
  last: { at: string; result: "OK" | "NG"; peak: number | null; terminal: string; applicator: string } | null;
  alarms: { text: string; count: number; last_at: string | null }[];
}

interface MachineCardBase {
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
}

export interface MachineCard extends MachineCardBase {
  kind?: "ob019_modbus";
  /** Последние слова ПЛК; при потере связи остаются, `link` и `reported_at` говорят, что они старые. */
  live: Ob019Live | null;
}

/** Пресс ОБ.099: ПЛК по FINS (только чтение) и поштучные обжимы. */
export interface Ob099MachineCard extends MachineCardBase {
  kind: "ob099_fins";
  live: Ob099Live | null;
  today: Ob099Today;
}

export type AnyMachineCard = MachineCard | Ob099MachineCard;

export const machinesApi = {
  list: (signal?: AbortSignal) => api<MachinesList>("/api/production/machines", { signal }),
  card: (id: string, signal?: AbortSignal) => api<AnyMachineCard>(`/api/production/machines/${encodeURIComponent(id)}`, { signal }),
  setup: (inventory: string) => api<UnitSetup>(`/api/production/setup-model/${encodeURIComponent(inventory)}`),
  configure: (inventory: string, draft: ConfigurationDraft) => api<{ ok: boolean; version: number; values: Record<string, string>; task_status?: string }>(
    `/api/production/setup-model/${encodeURIComponent(inventory)}/configuration`, { method: "POST", body: JSON.stringify(draft) }),
};

export const POLL_MS = 3000;

/** Опрос раз в `period` мс, пока вкладка видна: скрытая вкладка не опрашивает, при возврате — сразу свежие данные. */
export function usePolled<T>(load: (signal: AbortSignal) => Promise<T>, period = POLL_MS, failure = "Не удалось загрузить станки") {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  // `reload()` asks at once (after an action), then the polling goes on from there.
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((value) => value + 1), []);
  const loader = useRef(load);
  loader.current = load;
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | null = null;
    let stopped = false;
    const tick = async () => {
      if (stopped || document.visibilityState === "hidden") return;
      controller?.abort();
      const own = new AbortController();
      controller = own;
      try {
        const loaded = await loader.current(own.signal);
        if (own.signal.aborted) return;
        setData(loaded);
        setError("");
      } catch (reason) {
        if (own.signal.aborted) return;
        setError(failureText(reason, failure));
      }
      // A tick superseded by a newer one (the tab came back while this one was loading) ends its own chain.
      if (!stopped && controller === own) timer = setTimeout(() => void tick(), period);
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
  }, [period, failure, nonce]);
  return { data, error, reload };
}
