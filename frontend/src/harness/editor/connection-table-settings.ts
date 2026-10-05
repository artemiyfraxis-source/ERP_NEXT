import { useSyncExternalStore } from "react";

export const connectionTableColumnIds = [
  "index", "mark", "section", "marking", "from", "color", "to", "length", "route",
] as const;

export type ConnectionTableColumnId = typeof connectionTableColumnIds[number];

export interface ConnectionTableColumnSetting {
  readonly id: ConnectionTableColumnId;
  readonly visible: boolean;
}

export interface ConnectionTableSettings {
  readonly columns: readonly ConnectionTableColumnSetting[];
}

export const connectionTableColumnLabels: Readonly<Record<ConnectionTableColumnId, string>> = {
  index: "Индекс (Wn)",
  mark: "Марка",
  section: "Сечение",
  marking: "Маркировка",
  from: "Откуда",
  color: "Цвет",
  to: "Куда",
  length: "Длина, мм",
  route: "Маршрут",
};

const storageKey = "techmap.connection-table-settings.v1";
const defaultSettings: ConnectionTableSettings = {
  columns: connectionTableColumnIds.map(id => ({ id, visible: true })),
};
let current = readSettings();
const listeners = new Set<() => void>();

function readSettings(): ConnectionTableSettings {
  if (typeof window === "undefined") return defaultSettings;
  try {
    const raw = JSON.parse(window.localStorage.getItem(storageKey) ?? "null") as unknown;
    if (!raw || typeof raw !== "object" || !Array.isArray((raw as { columns?: unknown }).columns)) return defaultSettings;
    const entries = (raw as { columns: unknown[] }).columns;
    const byId = new Map<ConnectionTableColumnId, ConnectionTableColumnSetting>();
    for (const item of entries) {
      if (!item || typeof item !== "object") continue;
      const id = (item as { id?: unknown }).id;
      const visible = (item as { visible?: unknown }).visible;
      if (typeof id === "string" && (connectionTableColumnIds as readonly string[]).includes(id) && typeof visible === "boolean") {
        byId.set(id as ConnectionTableColumnId, { id: id as ConnectionTableColumnId, visible });
      }
    }
    return { columns: [...byId.values(), ...connectionTableColumnIds.filter(id => !byId.has(id)).map(id => ({ id, visible: true }))] };
  } catch {
    return defaultSettings;
  }
}

function persist(settings: ConnectionTableSettings): void {
  try { window.localStorage.setItem(storageKey, JSON.stringify(settings)); } catch { /* restricted storage is still a valid session preference */ }
}

export function getConnectionTableSettings(): ConnectionTableSettings { return current; }

export function subscribeConnectionTableSettings(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function updateConnectionTableSettings(settings: ConnectionTableSettings): void {
  const seen = new Set<ConnectionTableColumnId>();
  const columns = settings.columns.filter(column => {
    if (seen.has(column.id)) return false;
    seen.add(column.id);
    return true;
  });
  current = { columns: [...columns, ...connectionTableColumnIds.filter(id => !seen.has(id)).map(id => ({ id, visible: true }))] };
  persist(current);
  listeners.forEach(listener => listener());
}

export function useConnectionTableSettings(): ConnectionTableSettings {
  return useSyncExternalStore(subscribeConnectionTableSettings, getConnectionTableSettings, () => defaultSettings);
}
