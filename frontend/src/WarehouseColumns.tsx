import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

/** The columns of the two stock tables: «Остатки по позициям» (one row per article) and «Таблица склада» (one row per lot). */
export type ColumnKey = "type" | "grn" | "balance" | "lots" | "place" | "series" | "article" | "name" | "warehouse" | "available" | "reserved" | "purpose";
export type TableKind = "grouped" | "flat";
export interface ColumnLayout { order: ColumnKey[]; hidden: ColumnKey[] }
export type ColumnLayouts = Record<TableKind, ColumnLayout>;

// The order of the SUPPLYMAP portal stock view (owner, 24.09.2026): the storekeeper looks for ГРН, quantity and shelf
// first; the name is flexible and takes what is left, the warehouse closes the row. «Таблица склада» (ERP only) keeps
// the same order with «Доступно» instead of «Партий».
const DEFAULT_ORDER: Record<TableKind, ColumnKey[]> = {
  grouped: ["type", "grn", "balance", "reserved", "available", "lots", "place", "series", "article", "name", "warehouse"],
  flat: ["type", "grn", "balance", "reserved", "available", "place", "series", "article", "name", "purpose", "warehouse"],
};
// «Назначение» (the project a lot is kept for) is the least needed column of the lot table, «Доступно» of the article
// table (the reserve beside the balance is enough there): off until asked for.
const DEFAULT_HIDDEN: Record<TableKind, ColumnKey[]> = { grouped: ["available"], flat: ["purpose"] };

export const COLUMN_TITLES: Record<ColumnKey, string> = {
  name: "Наименование", article: "Артикул", grn: "ГРН", balance: "Остаток", lots: "Партий", available: "Доступно", reserved: "Резерв",
  place: "Места", warehouse: "Склад", purpose: "Назначение", series: "Серия", type: "Тип",
};

/** «Таблица склада» lists lots one by one, so «Места» is one «Место» there. */
export function columnTitle(key: ColumnKey, flat: boolean) {
  return flat && key === "place" ? "Место" : COLUMN_TITLES[key];
}

export function defaultLayout(kind: TableKind): ColumnLayout {
  return { order: [...DEFAULT_ORDER[kind]], hidden: [...DEFAULT_HIDDEN[kind]] };
}

/** The columns on screen, left to right: the name cannot be switched off; the column the rows are grouped by is the group header. */
export function shownColumns(layout: ColumnLayout, groupColumn: ColumnKey | null): ColumnKey[] {
  return layout.order.filter((key) => key === "name" || (key !== groupColumn && !layout.hidden.includes(key)));
}

/** The columns of the chooser, in the order of the table. */
export function chooserColumns(layout: ColumnLayout, groupColumn: ColumnKey | null): ColumnKey[] {
  return layout.order.filter((key) => key !== groupColumn);
}

/** One step of `key` towards the start (-1) or the end (+1) among the columns the viewer sees in the chooser. */
export function moveColumn(order: ColumnKey[], key: ColumnKey, step: -1 | 1, among: ColumnKey[]): ColumnKey[] {
  const at = among.indexOf(key);
  const neighbour = among[at + step];
  if (at < 0 || !neighbour) return order;
  const next = [...order];
  const from = next.indexOf(key);
  const to = next.indexOf(neighbour);
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}

// A new key: the old saved sets (`erp-table:stock-columns`, hidden columns only, name first) do not fit the new order.
const STORAGE_KEY = "erp-table:stock-columns:v2";

function readLayout(kind: TableKind, saved: unknown): ColumnLayout {
  const fallback = defaultLayout(kind);
  if (!saved || typeof saved !== "object") return fallback;
  const { order, hidden } = saved as Partial<Record<keyof ColumnLayout, unknown>>;
  const known = (key: unknown): key is ColumnKey => typeof key === "string" && DEFAULT_ORDER[kind].includes(key as ColumnKey);
  const savedOrder = Array.isArray(order) ? [...new Set(order.filter(known))] : [];
  // A column the saved order does not know (added later) stands after its neighbour of the default order («Резерв»
  // after «Остаток»), or at the end when that neighbour is not there.
  const merged = [...savedOrder];
  const added: ColumnKey[] = [];
  DEFAULT_ORDER[kind].forEach((key, index) => {
    if (merged.includes(key)) return;
    added.push(key);
    const before = merged.indexOf(DEFAULT_ORDER[kind][index - 1]);
    merged.splice(before < 0 ? merged.length : before + 1, 0, key);
  });
  return {
    order: merged,
    hidden: Array.isArray(hidden)
      ? [...new Set([...hidden.filter(known), ...added.filter((key) => DEFAULT_HIDDEN[kind].includes(key))])].filter((key) => key !== "name")
      : fallback.hidden,
  };
}

export function readLayouts(): ColumnLayouts {
  let saved: Partial<Record<TableKind, unknown>> = {};
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "null");
    if (parsed && typeof parsed === "object") saved = parsed;
  } catch { /* storage unavailable or damaged: the default */ }
  return { grouped: readLayout("grouped", saved.grouped), flat: readLayout("flat", saved.flat) };
}

export function writeLayouts(layouts: ColumnLayouts) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(layouts));
  } catch { /* the choice lives until reload */ }
}

/** «Колонки ▾»: which columns to show and in what order (↑/↓), «По умолчанию» puts them back. Opens under the button. */
export function ColumnChooser({ keys, hidden, flat, onToggle, onMove, onReset }: {
  keys: ColumnKey[];
  hidden: ColumnKey[];
  flat: boolean;
  onToggle: (key: ColumnKey, shown: boolean) => void;
  onMove: (key: ColumnKey, step: -1 | 1) => void;
  onReset: () => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  // After a step the focus stays on the same arrow (the list is re-ordered under it), or on the other one at the end.
  const focusAfter = useRef<{ key: ColumnKey; step: -1 | 1 } | null>(null);
  useEffect(() => {
    if (!open) return;
    // A tap outside, or the focus leaving the list, closes it (the same as the menu «Ещё»).
    const leave = (event: Event) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", leave);
    document.addEventListener("focusin", leave);
    return () => {
      document.removeEventListener("pointerdown", leave);
      document.removeEventListener("focusin", leave);
    };
  }, [open]);
  useEffect(() => {
    const target = focusAfter.current;
    if (!target) return;
    focusAfter.current = null;
    const row = root.current?.querySelector<HTMLElement>(`[data-column="${target.key}"]`);
    (row?.querySelector<HTMLButtonElement>(`button[data-step="${target.step}"]:not(:disabled)`)
      ?? row?.querySelector<HTMLButtonElement>("button:not(:disabled)"))?.focus();
  }, [keys]);

  function onKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Escape" || !open) return;
    event.stopPropagation();
    setOpen(false);
    root.current?.querySelector<HTMLButtonElement>(".column-trigger")?.focus();
  }

  return <div className="column-menu" ref={root} onKeyDown={onKeyDown}>
    <button type="button" className="secondary column-trigger" aria-expanded={open} onClick={() => setOpen((value) => !value)}>Колонки</button>
    {open && <div className="column-popover" role="group" aria-label="Показывать колонки">
      <div className="column-popover-head">Показывать колонки</div>
      <ul>
        {keys.map((key, index) => <li key={key} data-column={key}>
          <label className="check-inline">
            <input type="checkbox" checked={key === "name" || !hidden.includes(key)} disabled={key === "name"} onChange={(event) => onToggle(key, event.target.checked)} />
            {columnTitle(key, flat)}
          </label>
          <button type="button" className="column-step" data-step="-1" aria-label={`Выше: ${columnTitle(key, flat)}`} title="Левее в таблице" disabled={index === 0} onClick={() => { focusAfter.current = { key, step: -1 }; onMove(key, -1); }}>↑</button>
          <button type="button" className="column-step" data-step="1" aria-label={`Ниже: ${columnTitle(key, flat)}`} title="Правее в таблице" disabled={index === keys.length - 1} onClick={() => { focusAfter.current = { key, step: 1 }; onMove(key, 1); }}>↓</button>
        </li>)}
      </ul>
      <button type="button" className="link-button column-reset" onClick={onReset}>По умолчанию</button>
    </div>}
  </div>;
}
