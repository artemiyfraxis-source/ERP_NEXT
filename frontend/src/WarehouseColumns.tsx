import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

/** The columns of the two stock tables: «Остатки по позициям» (one row per article) and «Таблица склада» (one row per lot). */
export type ColumnKey = "type" | "grn" | "balance" | "lots" | "place" | "series" | "article" | "name" | "warehouse" | "available" | "reserved" | "purpose"
  | "maker" | ArticleColumn | DocumentColumn;
/** Three articles of a lot (owner 28.10.2026): «Артикул номенклатуры» is ours (`article`), these two are the maker's and
 *  the seller's codes — off until switched on in «Колонки», they stand right after ours. */
export type ArticleColumn = "maker_article" | "supplier_article";
const ARTICLE_COLUMNS: ArticleColumn[] = ["maker_article", "supplier_article"];
/** The documents of a lot (owner 26.10.2026: «должно быть, но скрыто»): the names the УПД and the invoice give it, and
 *  the numbers, dates and file of its delivery. In both tables, off until switched on; a position of the grouped table
 *  shows the value its lots agree on, else «N разн.» (owner 26.10: «добавить сами столбцы»).
 *  «Файл счёта» has no source yet (purchasing's paid invoice ↔ delivery comes later) — no empty column for it. */
export type DocumentColumn = "upd_name" | "invoice_name" | "invoice_number" | "invoice_date" | "upd_number" | "upd_date" | "prd_number" | "prd_date" | "upd_file";
export const DOCUMENT_COLUMNS: DocumentColumn[] = ["upd_name", "invoice_name", "invoice_number", "invoice_date", "upd_number", "upd_date", "prd_number", "prd_date", "upd_file"];
/** Widths of the document columns, px. Switched on, they widen the table (it scrolls in its frame, the first column
 *  pinned) instead of squeezing the name and the article to «…». */
export const DOCUMENT_WIDTHS: Record<DocumentColumn, number> = {
  upd_name: 280, invoice_name: 240, invoice_number: 100, invoice_date: 100, upd_number: 100, upd_date: 100, prd_number: 110,
  prd_date: 100, upd_file: 84,
};

// With document columns on, every column but the name gets a fixed width (the % widths of styles.css would grow with
// the wider table and squeeze the name); the name takes the rest, at least NAME_MIN.
const FIXED_WIDTHS: Partial<Record<ColumnKey, number>> = {
  type: 150, grn: 168, balance: 98, lots: 76, available: 96, reserved: 92, place: 110, series: 140, article: 200,
  maker_article: 170, supplier_article: 140,
  warehouse: 92, purpose: 104, maker: 150, ...DOCUMENT_WIDTHS,
};
const NAME_MIN = 320;
/** The optional columns that widen the table when switched on: the documents and the maker's / supplier's article. */
const WIDENING: ReadonlySet<ColumnKey> = new Set<ColumnKey>([...DOCUMENT_COLUMNS, ...ARTICLE_COLUMNS]);

/** Column widths and the table's min width when a widening column is shown; null — none shown (styles.css widths). */
export function documentsLayout(keys: ColumnKey[]): { minWidth: number; widths: Partial<Record<ColumnKey, number>> } | null {
  if (!keys.some((key) => WIDENING.has(key))) return null;
  const widths = Object.fromEntries(keys.filter((key) => key !== "name").map((key) => [key, FIXED_WIDTHS[key] ?? 100]));
  const minWidth = Object.values(widths).reduce((sum, width) => sum + width, 0) + (keys.includes("name") ? NAME_MIN : 0);
  return { minWidth, widths };
}
export type TableKind = "grouped" | "flat";
export interface ColumnLayout { order: ColumnKey[]; hidden: ColumnKey[] }
export type ColumnLayouts = Record<TableKind, ColumnLayout>;

// The order of the SUPPLYMAP portal stock view (owner, 24.09.2026): the storekeeper looks for ГРН, quantity and shelf
// first; the name is flexible and takes what is left, the warehouse closes the row. «Таблица склада» (ERP only) keeps
// the same order with «Партий» left out. Since 22.10 the quantity read first is «Свободно» (balance minus reserve): the
// raw «Остаток» is optional (it stays in the tooltip of «Свободно» and in the group header).
const DEFAULT_ORDER: Record<TableKind, ColumnKey[]> = {
  grouped: ["type", "grn", "available", "reserved", "balance", "lots", "place", "series", "article", ...ARTICLE_COLUMNS, "name", "warehouse", "maker", ...DOCUMENT_COLUMNS],
  flat: ["type", "grn", "available", "reserved", "balance", "place", "series", "article", ...ARTICLE_COLUMNS, "name", "purpose", "warehouse", "maker", ...DOCUMENT_COLUMNS],
};
// «Назначение» (the project a lot is kept for) is the least needed column of the lot table; «Остаток» is off in both
// tables: «Свободно» + «Резерв» already say it, and reading the raw balance misleads (owner, 22.10). «Производитель» and
// the documents are there for whoever switches them on in «Колонки» (owner 26.10).
const DEFAULT_HIDDEN: Record<TableKind, ColumnKey[]> = {
  grouped: ["balance", "maker", ...ARTICLE_COLUMNS, ...DOCUMENT_COLUMNS],
  flat: ["balance", "purpose", "maker", ...ARTICLE_COLUMNS, ...DOCUMENT_COLUMNS],
};

/** The person's layout of each table on the server (personalViews.ts): one view per table of «Материалы и комплектующие». */
export const STOCK_VIEWS: Record<TableKind, string> = { grouped: "stock.grouped", flat: "stock.flat" };
export const TABLE_KINDS: TableKind[] = ["grouped", "flat"];

export const COLUMN_TITLES: Record<ColumnKey, string> = {
  name: "Наименование", article: "Артикул номенклатуры", maker_article: "Артикул производителя",
  supplier_article: "Артикул поставщика", grn: "ГРН", balance: "Остаток", lots: "Партий", available: "Свободно", reserved: "Резерв",
  place: "Места", warehouse: "Склад", purpose: "Назначение", series: "Серия", type: "Тип", maker: "Производитель",
  upd_name: "Наим. по УПД", invoice_name: "Наим. по счёту", invoice_number: "№ счёта", invoice_date: "Дата счёта",
  upd_number: "№ УПД", upd_date: "Дата УПД", prd_number: "№ ПРД", prd_date: "Дата ПРД", upd_file: "Файл УПД",
};

/** Header tooltips of the columns that need one. */
export const COLUMN_HINTS: Partial<Record<ColumnKey, string>> = {
  available: "остаток минус резерв", reserved: "под какие заявки — в подсказке ячейки",
  article: "наш общепринятый артикул", maker_article: "артикул, с которым позиция вышла с завода",
  supplier_article: "артикул, под которым позиция в продаже у поставщика",
  upd_name: "строка товара, как напечатана в УПД", invoice_name: "строка товара в оплаченном счёте",
  prd_number: "платёжно-расчётный документ из УПД", upd_file: "скан или файл ЭДО поставки",
};

/** A position (several lots) in one cell: the value every lot that has one agrees on, else «N разн.» with the values in
 *  the tooltip; an empty text — no lot has a value. Lots without a value don't count as a difference (an old lot has
 *  no УПД name; the others still say what it is). */
export function agreeing(values: string[]): { text: string; title?: string; many: boolean } {
  const found = [...new Set(values.map((value) => value.trim()).filter(Boolean))];
  if (!found.length) return { text: "", many: false };
  if (found.length === 1) return { text: found[0], title: found[0], many: false };
  return { text: `${found.length} разн.`, title: `У партий разные значения:\n${found.join("\n")}`, many: true };
}

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
const STORAGE_KEY = "erp-table:stock-columns:v3";

/** A saved layout of any earlier version (this browser's or the server's) fitted to the columns of today. */
export function readLayout(kind: TableKind, saved: unknown): ColumnLayout {
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

/** What this browser kept, table by table, as stored (a table never changed here is absent). */
export function readLocalLayouts(): Partial<Record<TableKind, unknown>> {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "null");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {}; // storage unavailable or damaged: the default
  }
}

export function readLayouts(): ColumnLayouts {
  const saved = readLocalLayouts();
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

/** Phone card mode of the stock tables: every cell gets the caption of its column («Остаток», «Место»…) as `data-label`,
 *  read from the header — the cells are drawn in WarehousePage, which this keeps untouched. Sort arrows are not a caption. */
export function labelStockCells(root: ParentNode = document) {
  root.querySelectorAll<HTMLTableElement>("table.stock-table").forEach((table) => {
    const heads = Array.from(table.querySelectorAll<HTMLTableCellElement>("thead th")).map((th) => (th.textContent ?? "").replace(/[▲▼]/g, "").trim());
    table.querySelectorAll<HTMLTableRowElement>("tbody > tr").forEach((row) => {
      if (row.classList.contains("stock-group-row") || row.classList.contains("stock-cards-row")) return;
      Array.from(row.cells).forEach((cell, index) => {
        const label = heads[index] ?? "";
        if (cell.getAttribute("data-label") !== label) cell.setAttribute("data-label", label);
      });
    });
  });
}

/** Keeps `labelStockCells` current while the screen redraws (one pass per frame). Returns the stop function. */
export function watchStockLabels(doc: Document = document): () => void {
  const win = doc.defaultView;
  if (!win || typeof win.MutationObserver !== "function") return () => undefined;
  let frame = 0;
  const run = () => { frame = 0; labelStockCells(doc); };
  const observer = new win.MutationObserver(() => { if (!frame) frame = win.requestAnimationFrame(run); });
  observer.observe(doc.body, { childList: true, subtree: true });
  run();
  return () => { observer.disconnect(); if (frame) win.cancelAnimationFrame(frame); };
}
