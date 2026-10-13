import { api, type TechOperationRecord, type TechOperationTab } from "./api";

/* «Склад → база данных»: позиция склада привязана к записи «Базы данных», из неё сделана запись или по ней
   поправлена запись. Каждое событие — строка журнала (references, `reference_stock_links`). */

export type StockLinkEvent = "linked" | "created" | "updated";

export interface StockLinkEntry {
  id: string;
  occurred_at: string;
  event: StockLinkEvent;
  lot_id: string;
  warehouse: string;
  grn: string;
  package: string;
  article: string;
  name: string;
  /** null — записи уже нет (перезагрузка зеркала); база и текст записи остаются. */
  record_id: string | null;
  record_tab: TechOperationTab;
  record_text: string;
  changes: Array<{ field: string; old: string; new: string }>;
  actor_name: string;
}

export const STOCK_LINK_EVENT_LABELS: Record<StockLinkEvent, string> = {
  linked: "Привязана",
  created: "Создана",
  updated: "Обновлена",
};

export const stockLinksApi = {
  /** Без `recordId` — весь журнал, новые сверху. */
  list: (recordId?: string, signal?: AbortSignal) =>
    api<{ entries: StockLinkEntry[] }>(`/api/references/stock-links${recordId ? `?${new URLSearchParams({ record_id: recordId })}` : ""}`, { signal }),
  /** Привязать партию к записи (`record_id`) или создать запись (`tab` + `fields`); `entry: null` — уже была привязана. */
  link: (payload: { lot_id: string; record_id?: string; tab?: TechOperationTab; fields?: Record<string, string> }) =>
    api<{ record: TechOperationRecord; entry: StockLinkEntry | null }>("/api/references/stock-links", { method: "POST", body: JSON.stringify(payload) }),
};
