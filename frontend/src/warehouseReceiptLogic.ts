/* «Внести позиции» = поставка (шапка) + таблица позиций, каждая принимается на склад отдельно. No React here. */
import { appendParsedRows, applyGridPaste, dateToIso, hasHeaderRow, parseTsv, type PasteField } from "./gridPaste";
import type {
  WarehouseArticleHints, WarehouseLot, WarehouseReceipt, WarehouseReceiptAccept, WarehouseReceiptFields, WarehouseReceiptScan,
} from "./api";

/** The server takes at most this many rows in one supply. */
export const RECEIPT_LIMIT = 100;
export const LABELS_MAX = 50;
/** The purpose of a lot when neither the row nor the header names one (shown as a grey placeholder). */
export const DEFAULT_PURPOSE = "Склад";

export type ReceiptHeader = {
  warehouse: string;
  supplier: string;
  invoice_number: string;
  invoice_date: string;
  upd_number: string;
  upd_date: string;
  purpose: string;
};
export type HeaderKey = keyof ReceiptHeader;
const HEADER_KEYS: HeaderKey[] = ["supplier", "invoice_number", "invoice_date", "upd_number", "upd_date"];

export type LineKey =
  | "article" | "quantity" | "unit" | "item_type" | "manufacturer" | "series" | "name" | "purpose"
  | "expiry_date" | "datasheet" | "certificate";

export type AcceptedInfo = {
  lotId: string;
  grn: string;
  place: string;
  /** how many labels went to print (0 = none or failed) */
  labels: number;
  /** the red reason the labels did not go out */
  labelError: string;
};

export type ReceiptLine = Record<LineKey, string> & {
  /** cells filled by the article lookup and not touched since — shown muted */
  auto: LineKey[];
  /** where the lookup took them from (the row's title) */
  source: string;
  /** places the article already lies on */
  places: string[];
  /** «заказ №…», when the article is awaited in «Приёмка» */
  expected: string;
  accepted: AcceptedInfo | null;
  error: string;
};

/** The grid, left to right; the same order a headerless pasted block fills. «Паспорт» is the certificate. */
export const GRID_COLUMNS: Array<{ key: LineKey; label: string; required: boolean }> = [
  { key: "article", label: "Артикул", required: true },
  { key: "quantity", label: "Кол-во", required: true },
  { key: "unit", label: "Ед.", required: false },
  { key: "item_type", label: "Тип", required: false },
  { key: "manufacturer", label: "Производитель", required: false },
  { key: "series", label: "Серия", required: false },
  { key: "name", label: "Наименование", required: false },
  { key: "purpose", label: "Назначение", required: false },
  { key: "expiry_date", label: "Срок годности", required: false },
  { key: "datasheet", label: "Datasheet", required: false },
  { key: "certificate", label: "Паспорт", required: false },
];
export const PASTE_ORDER: LineKey[] = GRID_COLUMNS.map((column) => column.key);

type PasteKey = LineKey | HeaderKey;
const DATE_KEYS: ReadonlySet<PasteKey> = new Set<PasteKey>(["invoice_date", "upd_date", "expiry_date"]);

/* Header names of the owner's old receipt sheets («арт ПОСТАВЩИКА», «приход») and of the grid itself. */
export const RECEIPT_FIELDS: Array<PasteField<PasteKey>> = [
  { key: "article", label: "Артикул", aliases: ["арт ПОСТАВЩИКА", "Арт. поставщика"] },
  { key: "quantity", label: "Кол-во", aliases: ["Количество", "приход"] },
  { key: "unit", label: "Ед.", aliases: ["Ед. изм", "Ед.изм", "Ед. изм.", "Единица"] },
  { key: "item_type", label: "Тип", aliases: [] },
  { key: "manufacturer", label: "Производитель", aliases: [] },
  { key: "series", label: "Серия", aliases: [] },
  { key: "name", label: "Наименование", aliases: [] },
  { key: "purpose", label: "Назначение", aliases: [] },
  { key: "expiry_date", label: "Срок годности", aliases: [] },
  { key: "datasheet", label: "Datasheet", aliases: [] },
  { key: "certificate", label: "Паспорт", aliases: ["Сертификат"] },
  { key: "supplier", label: "Поставщик", aliases: ["Контрагент"] },
  { key: "invoice_number", label: "№ счёта", aliases: ["№ счета", "Номер счета", "Номер счёта"] },
  { key: "invoice_date", label: "Дата счёта", aliases: ["Дата счета"] },
  { key: "upd_number", label: "№ УПД", aliases: ["Номер УПД"] },
  { key: "upd_date", label: "Дата УПД", aliases: [] },
];

const MESSAGES = {
  short: "Нужны строка заголовков и хотя бы одна строка данных.",
  noHeaders: "Ни один заголовок не распознан (Артикул, Кол-во, Наименование …).",
};

export function emptyHeader(): ReceiptHeader {
  return { warehouse: "ЦАР", supplier: "", invoice_number: "", invoice_date: "", upd_number: "", upd_date: "", purpose: "" };
}

export function emptyLine(): ReceiptLine {
  const line = { auto: [], source: "", places: [], expected: "", accepted: null, error: "" } as unknown as ReceiptLine;
  for (const key of PASTE_ORDER) line[key] = "";
  return line;
}

/** A row nobody filled: no article, name or quantity. It is neither accepted nor counted. */
export function isBlankLine(line: ReceiptLine): boolean {
  return !line.accepted && !line.article.trim() && !line.name.trim() && !line.quantity.trim();
}

/** Nothing typed in any cell, nothing accepted: the one row that waits at the bottom. */
export function isEmptyLine(line: ReceiptLine): boolean {
  return !line.accepted && PASTE_ORDER.every((key) => !line[key].trim());
}

/** The grid always ends with exactly one empty row (none only at the row limit). */
export function withTrailingBlank(lines: ReceiptLine[]): ReceiptLine[] {
  const kept = [...lines];
  while (kept.length > 1 && isEmptyLine(kept[kept.length - 1]) && isEmptyLine(kept[kept.length - 2])) kept.pop();
  const last = kept[kept.length - 1];
  if ((!last || !isEmptyLine(last)) && kept.length < RECEIPT_LIMIT) kept.push(emptyLine());
  return kept.length === lines.length && kept.every((line, index) => line === lines[index]) ? lines : kept;
}

/** A row can be accepted: article and a quantity above zero. */
export function canAccept(line: ReceiptLine): boolean {
  const quantity = Number(line.quantity.replace(",", "."));
  return !line.accepted && Boolean(line.article.trim()) && Number.isFinite(quantity) && quantity > 0;
}

function cellValue(key: PasteKey, raw: string): string {
  const value = raw.trim();
  if (DATE_KEYS.has(key)) return dateToIso(value);
  if (key === "quantity") return value.replace(/\s/g, "").replace(",", ".");
  return value;
}

export type ReceiptPaste = {
  lines: ReceiptLine[];
  /** header fields found in the block's columns; they fill only EMPTY header fields */
  header: Partial<ReceiptHeader>;
  filled: number;
  dropped: number;
  mode: "headers" | "cells";
  unknown: string[];
};

/** Paste into the grid. A block with a header row is read by column names: lines go after the filled ones and the
 *  first non-empty Поставщик / № счёта / Дата счёта / № УПД / Дата УПД of the block go to `header`.
 *  A plain block lands from the focused cell down and to the right. Accepted rows are never touched. */
export function applyReceiptPaste(
  lines: ReceiptLine[], rowIndex: number, colKey: LineKey, text: string,
): ReceiptPaste | { error: string } {
  const lineSetup = { limit: RECEIPT_LIMIT, seed: emptyLine(), isBlank: isBlankLine };
  if (hasHeaderRow(text, RECEIPT_FIELDS)) {
    const parsed = parseTsv(text, RECEIPT_FIELDS, DATE_KEYS, MESSAGES);
    if ("error" in parsed) return parsed;
    const header: Partial<ReceiptHeader> = {};
    for (const key of HEADER_KEYS) {
      const found = parsed.rows.map((row) => row[key] ?? "").find((value) => value !== "");
      if (found) header[key] = found;
    }
    // line columns only; a row that carries only header columns is not a line
    const rows = parsed.rows
      .map((row) => Object.fromEntries(PASTE_ORDER.map((key) => [key, key === "quantity" ? cellValue(key, row[key] ?? "") : (row[key] ?? "")])) as Partial<Record<LineKey, string>>)
      .filter((row) => PASTE_ORDER.some((key) => row[key]));
    const result = appendParsedRows<LineKey, ReceiptLine>(lines, rows, lineSetup);
    return { lines: result.positions, header, filled: result.filled, dropped: result.dropped, mode: "headers", unknown: parsed.unknown };
  }
  const result = applyGridPaste<LineKey, ReceiptLine>(lines, rowIndex, colKey, text, {
    ...lineSetup,
    fields: RECEIPT_FIELDS as Array<PasteField<LineKey>>,
    dateKeys: DATE_KEYS as ReadonlySet<LineKey>,
    columns: PASTE_ORDER,
    cellValue: (key, raw) => cellValue(key, raw),
    messages: MESSAGES,
  });
  if ("error" in result) return result;
  // a pasted cell is typed text, not a lookup result; an accepted row stays as it was
  const positions = result.positions.map((line, index) => {
    const was = lines[index];
    if (!was) return line;
    if (was.accepted) return was;
    const changed = PASTE_ORDER.filter((key) => line[key] !== was[key]);
    return changed.length ? { ...was, ...Object.fromEntries(changed.map((key) => [key, line[key]])), auto: was.auto.filter((key) => !changed.includes(key)), error: "" } : was;
  });
  return { lines: positions, header: {}, filled: result.filled, dropped: result.dropped, mode: "cells", unknown: [] };
}

/** Header fields found elsewhere (a paste, a scan) go only into empty ones. */
export function mergeHeader(current: ReceiptHeader, found: Partial<ReceiptHeader>): ReceiptHeader {
  const next = { ...current };
  for (const key of HEADER_KEYS) {
    const value = found[key];
    if (value && !String(current[key]).trim()) next[key] = value;
  }
  return next;
}

/** The УПД scan: its recognised header fills the empty fields, its lines follow the filled rows. */
export function applyScan(header: ReceiptHeader, lines: ReceiptLine[], scan: WarehouseReceiptScan): { header: ReceiptHeader; lines: ReceiptLine[]; added: number } {
  const found = scan.recognised;
  const nextHeader = mergeHeader(header, {
    supplier: found.supplier.trim(),
    invoice_number: found.invoice_number.trim(),
    invoice_date: dateToIso(found.invoice_date),
    upd_number: found.upd_number.trim(),
    upd_date: dateToIso(found.upd_date),
  });
  const rows = scan.lines.map((line) => ({
    article: line.article.trim(), name: line.name.trim(), unit: line.unit.trim(), quantity: cellValue("quantity", line.quantity),
  }));
  const result = appendParsedRows<LineKey, ReceiptLine>(lines, rows, { limit: RECEIPT_LIMIT, seed: emptyLine(), isBlank: isBlankLine });
  return { header: nextHeader, lines: result.positions, added: result.filled };
}

/** Header as the server takes it (empty dates are null). */
export function headerFields(header: ReceiptHeader, scanFile: string): WarehouseReceiptFields {
  return {
    warehouse: header.warehouse,
    supplier: header.supplier.trim(),
    invoice_number: header.invoice_number.trim(),
    invoice_date: header.invoice_date || null,
    upd_number: header.upd_number.trim(),
    upd_date: header.upd_date || null,
    purpose: header.purpose.trim() || DEFAULT_PURPOSE,
    ...(scanFile ? { scan_file: scanFile } : {}),
  };
}

/** Did the header change since the saved supply (then it goes up with PUT before the next acceptance)? */
export function headerChanged(saved: WarehouseReceipt, header: ReceiptHeader, scanFile: string): boolean {
  const next = headerFields(header, scanFile);
  return (Object.keys(next) as Array<keyof WarehouseReceiptFields>).some((key) => {
    const was = key === "scan_file" ? saved.scan_file : saved[key];
    return (was ?? "") !== (next[key] ?? "");
  });
}

export function buildAccept(line: ReceiptLine, header: ReceiptHeader, place: string, labels: number, confirmExpected: boolean): WarehouseReceiptAccept {
  return {
    article: line.article.trim(),
    quantity: line.quantity.trim().replace(",", "."),
    unit: line.unit.trim() || "шт",
    item_type: line.item_type.trim(),
    manufacturer: line.manufacturer.trim(),
    series: line.series.trim(),
    name: line.name.trim(),
    purpose: line.purpose.trim() || header.purpose.trim() || DEFAULT_PURPOSE,
    expiry_date: line.expiry_date || null,
    datasheet: line.datasheet.trim(),
    certificate: line.certificate.trim(),
    storage_location: place.trim(),
    labels,
    confirm_expected: confirmExpected,
  };
}

/** Fills EMPTY cells only; what it fills is `auto` (muted until edited). Returns the same line when nothing changed. */
export function fillEmpty(line: ReceiptLine, values: Partial<Record<LineKey, string | null | undefined>>, source: string): ReceiptLine {
  let next = line;
  for (const key of PASTE_ORDER) {
    const value = values[key];
    if (!value || line[key].trim() || line.accepted) continue;
    next = { ...next, [key]: value, auto: [...next.auto, key], source };
  }
  return next;
}

/** The lookup answer for the row: fills empty cells, remembers places and the awaited order. */
export function applyHints(line: ReceiptLine, hints: WarehouseArticleHints): ReceiptLine {
  const info = hints.info;
  let next = info
    ? fillEmpty(line, {
      name: info.name, series: info.series, item_type: info.item_type, unit: info.unit, manufacturer: info.manufacturer,
      datasheet: info.datasheet, certificate: info.certificate, expiry_date: info.expiry_date,
    }, info.source)
    : line;
  next = { ...next, places: hints.places ?? [], expected: hints.expected ? `заказ № ${hints.expected.order_number}` : "" };
  return next;
}

/** An editing of a cell: it is the user's own text now. */
export function editCell(line: ReceiptLine, key: LineKey, value: string): ReceiptLine {
  return { ...line, [key]: value, auto: line.auto.filter((item) => item !== key), error: "" };
}

/** A lot already accepted with this supply, shown as a read-only row («Продолжить поставку»). */
export function lineFromLot(lot: WarehouseLot): ReceiptLine {
  return {
    ...emptyLine(),
    article: lot.article, quantity: lot.inbound, unit: lot.unit, item_type: lot.item_type, manufacturer: lot.manufacturer ?? "",
    series: lot.series, name: lot.name, purpose: lot.purpose, expiry_date: lot.expiry_date ?? "",
    datasheet: lot.datasheet ?? "", certificate: lot.certificate ?? "",
    accepted: { lotId: lot.id, grn: lot.grn, place: lot.storage_location, labels: 0, labelError: "" },
  };
}

export function headerFromReceipt(receipt: WarehouseReceipt): ReceiptHeader {
  return {
    warehouse: receipt.warehouse, supplier: receipt.supplier, invoice_number: receipt.invoice_number,
    invoice_date: receipt.invoice_date ?? "", upd_number: receipt.upd_number, upd_date: receipt.upd_date ?? "", purpose: receipt.purpose,
  };
}

/** «Принять позицию» summary: артикул · наименование · кол-во ед. */
export function lineSummary(line: ReceiptLine): string {
  return [line.article.trim(), line.name.trim(), `${line.quantity.trim()} ${line.unit.trim() || "шт"}`].filter(Boolean).join(" · ");
}

/** «2026-10-23» → «23.10.2026» */
export function shortDate(iso: string | null | undefined): string {
  const match = (iso ?? "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : (iso ?? "");
}

/** The muted line of a lot card; empty parts are left out. */
export function receiptLine(receipt: WarehouseLot["receipt"]): string {
  if (!receipt) return "";
  const doc = (word: string, number: string, date: string | null) =>
    number || date ? [word, number, date ? `от ${shortDate(date)}` : ""].filter(Boolean).join(" ") : "";
  const parts = [
    receipt.supplier,
    doc("УПД", receipt.upd_number, receipt.upd_date),
    doc("счёт", receipt.invoice_number, receipt.invoice_date),
  ].filter(Boolean);
  return parts.length ? `Приход: ${parts.join(" · ")}` : "";
}
