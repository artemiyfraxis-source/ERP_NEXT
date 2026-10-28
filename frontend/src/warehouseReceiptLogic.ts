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
  /** «К платёжно-расчётному документу № … от …» — the payment, not the invoice (owner 26.10.2026) */
  prd_number: string;
  prd_date: string;
  purpose: string;
};
export type HeaderKey = keyof ReceiptHeader;
const HEADER_KEYS: HeaderKey[] = ["supplier", "invoice_number", "invoice_date", "upd_number", "upd_date", "prd_number", "prd_date"];

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

/** The cells a УПД scan fills from what the model made of the printed name: a known lot or the reference DB replaces
 *  them until the storekeeper edits them (owner 26.10.2026: catalog > model > nothing). */
export const MODEL_KEYS: LineKey[] = ["name", "item_type", "manufacturer"];

export type ReceiptLine = Record<LineKey, string> & {
  /** cells filled by the article lookup and not touched since — shown muted */
  auto: LineKey[];
  /** cells filled from the УПД scan (our name, type, maker) that the article lookup may still replace — shown muted */
  model: LineKey[];
  /** the item row as the УПД prints it — kept on the lot («Наим. по УПД»), the title of «Наименование» */
  upd_name: string;
  /** «Артикул производителя» / «Артикул поставщика» of a scanned УПД line (its article, its «Код товара») — kept on the
   *  lot beside our article, which the storekeeper may change in the grid */
  maker_article: string;
  supplier_article: string;
  /** where the lookup took them from (the row's title) */
  source: string;
  /** how a scanned УПД line came: «3 рул. × 100 м · скан УПД» (the row's title too) */
  scan: string;
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
const DATE_KEYS: ReadonlySet<PasteKey> = new Set<PasteKey>(["invoice_date", "upd_date", "prd_date", "expiry_date"]);

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
  { key: "prd_number", label: "№ ПРД", aliases: ["Номер ПРД", "№ платёжки", "№ платежки"] },
  { key: "prd_date", label: "Дата ПРД", aliases: ["Дата платёжки", "Дата платежки"] },
];

const MESSAGES = {
  short: "Нужны строка заголовков и хотя бы одна строка данных.",
  noHeaders: "Ни один заголовок не распознан (Артикул, Кол-во, Наименование …).",
};

export function emptyHeader(): ReceiptHeader {
  return { warehouse: "ЦАР", supplier: "", invoice_number: "", invoice_date: "", upd_number: "", upd_date: "", prd_number: "", prd_date: "", purpose: "" };
}

export function emptyLine(): ReceiptLine {
  const line = { auto: [], model: [], upd_name: "", maker_article: "", supplier_article: "", source: "", scan: "", places: [], expected: "", accepted: null, error: "" } as unknown as ReceiptLine;
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
    const split = result.positions.map((line, index) => (line === lines[index] ? line : splitArticleCell(line)));
    return { lines: split, header, filled: result.filled, dropped: result.dropped, mode: "headers", unknown: parsed.unknown };
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
    if (!changed.length) return was;
    const next = {
      ...was, ...Object.fromEntries(changed.map((key) => [key, line[key]])), auto: was.auto.filter((key) => !changed.includes(key)),
      model: was.model.filter((key) => !changed.includes(key)), error: "",
    };
    return changed.includes("article") ? splitArticleCell(next) : next;
  }).map((line, index) => (lines[index] ? line : splitArticleCell(line)));
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
/** Server text as a cell value: null, a number or a missing key become a string («trim» of null broke the window). */
export function text(value: unknown): string {
  return String(value ?? "").trim();
}

export function applyScan(header: ReceiptHeader, lines: ReceiptLine[], scan: WarehouseReceiptScan): { header: ReceiptHeader; lines: ReceiptLine[]; added: number } {
  const found = scan.recognised ?? ({} as Partial<WarehouseReceiptScan["recognised"]>);
  const nextHeader = mergeHeader(header, {
    supplier: text(found.supplier),
    invoice_number: text(found.invoice_number),
    invoice_date: dateToIso(text(found.invoice_date)),
    upd_number: text(found.upd_number),
    upd_date: dateToIso(text(found.upd_date)),
    prd_number: text(found.prd_number),
    prd_date: dateToIso(text(found.prd_date)),
  });
  const rows = (scan.lines ?? []).map((line) => ({
    article: text(line.article), name: text(line.name), unit: text(line.unit), quantity: cellValue("quantity", text(line.quantity)),
    item_type: text(line.item_type), manufacturer: text(line.manufacturer),
  }));
  const result = appendParsedRows<LineKey, ReceiptLine>(lines, rows, { limit: RECEIPT_LIMIT, seed: emptyLine(), isBlank: isBlankLine });
  const first = result.positions.length - result.filled;
  const split = result.positions.map((line, index) => {
    if (line === lines[index]) return line;
    const scanned = (scan.lines ?? [])[index - first];
    if (!scanned) return splitArticleCell(line);
    const hint = scanHint(scanned);
    // The printed УПД line is kept apart; our name / type / maker from the scan stay replaceable by the catalog.
    const marked = splitArticleCell({
      ...line, scan: hint || line.scan, upd_name: text(scanned.upd_name) || text(scanned.name),
      model: MODEL_KEYS.filter((key) => line[key].trim()),
    });
    // The article read off the УПД is the maker's (split from an «@maker» like the cell), the «Код товара» the seller's.
    return { ...marked, maker_article: text(scanned.article) ? marked.article : "", supplier_article: text(scanned.supplier_code) };
  });
  return { header: nextHeader, lines: split, added: result.filled };
}

/** «3 рул. × 100 м · скан УПД, артикул из партии склада · код поставщика 00-42»: how a scanned line came. */
export function scanHint(line: WarehouseReceiptScan["lines"][number]): string {
  const packs = text(line.packs);
  const pack = text(line.pack);
  const kind = text(line.pack_kind) || "уп.";
  const packed = pack ? (packs ? `${packs} ${kind} × ${pack}` : `${kind} ${pack}`) : "";
  const code = text(line.supplier_code);
  return [packed, text(line.source), code ? `код поставщика ${code}` : ""].filter(Boolean).join(" · ");
}

/** The row's title: how the scan gave it, then what the article lookup filled. */
export function lineTitle(line: ReceiptLine): string | undefined {
  const parts = [line.scan ?? "", line.auto.length && line.source ? `Подставлено: ${line.source}` : ""].filter(Boolean);
  return parts.length ? parts.join(" · ") : undefined;
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
    prd_number: header.prd_number.trim(),
    prd_date: header.prd_date || null,
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
    upd_name: line.upd_name.trim(),
    maker_article: line.maker_article.trim(),
    supplier_article: line.supplier_article.trim(),
  };
}

/** Fills EMPTY cells — and the cells the УПД scan filled from the model (`model`), the catalog knows better; what it
 *  fills is `auto` (muted until edited). Returns the same line when nothing changed. */
export function fillEmpty(line: ReceiptLine, values: Partial<Record<LineKey, string | null | undefined>>, source: string): ReceiptLine {
  let next = line;
  for (const key of PASTE_ORDER) {
    const value = values[key];
    const replaceable = line.model.includes(key);
    if (!value || (line[key].trim() && !replaceable) || line.accepted) continue;
    if (replaceable && line[key] === value) continue;
    next = {
      ...next, [key]: value, auto: [...next.auto.filter((item) => item !== key), key], source,
      model: next.model.filter((item) => item !== key),
    };
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

/* «XHP-4@JST» → article «XHP-4», manufacturer «JST» (owner 23.10.2026). Mirror of backend `app/shared/article_maker.py`
 * (the test runs both on the same real articles): only a trailing «@token» of 2..20 letters, digits, spaces, «&», «-»
 * is a maker; «##6000» / «упаковка 6000 шт» are the pack, text after «@MAKER,» a note. */
const PACK_WORDS = /[\s,;]*упаковк[аеиу]\s*(?:по\s*)?(\d[\d\s]*?)\s*шт\.?\s*$/iu;
const PACK_HASH = /\s*##\s*(\d+)\s*$/u;
const MAKER = /^[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё &-]{0,18}[0-9A-Za-zА-Яа-яЁё]$/u;
const LETTER = /[A-Za-zА-Яа-яЁё]/u;
const LATIN = /[A-Za-z]/;
const TWINS: Record<string, string> = Object.fromEntries(
  [..."АВЕКМНОРСТХУаеорсху"].map((letter, index) => [letter, "ABEKMHOPCTXYaeopcxy"[index]]),
);
const MAKER_ALIASES: Record<string, string> = { CONFLY: "CONNFLY", CONDAR: "SCONDAR", CHINA: "Китай", "КИТАЙ": "Китай" };

export type ArticleSplit = { article: string; maker: string; packQty: number | null; note: string };

const PY_SPACE = /[\s\u001c-\u001f\u0085]/gu; // Python's str.split() whitespace
const trimChars = (text: string, chars: string) => {
  let start = 0;
  let end = text.length;
  while (start < end && chars.includes(text[start])) start += 1;
  while (end > start && chars.includes(text[end - 1])) end -= 1;
  return text.slice(start, end);
};

export function normalizeMaker(value: string): string {
  let text = trimChars(value.replace(PY_SPACE, " ").split(" ").filter(Boolean).join(" "), " .,;:");
  if (!text) return "";
  if (LATIN.test(text)) text = [...text].map((letter) => TWINS[letter] ?? letter).join("");
  text = text.toUpperCase();
  return MAKER_ALIASES[text] ?? text;
}

function packOf(digits: string): number | null {
  const number = digits.replace(/\s/gu, "");
  return /^\d+$/.test(number) && Number(number) > 0 ? Number(number) : null;
}

export function splitArticleMaker(text: string | null | undefined): ArticleSplit {
  let value = String(text ?? "").trim();
  let packQty: number | null = null;
  let note = "";
  let maker = "";
  const words = PACK_WORDS.exec(value);
  if (words && words.index > 0) {
    packQty = packOf(words[1]);
    value = value.slice(0, words.index).trimEnd();
  }
  const at = value.lastIndexOf("@");
  if (at > 0) {
    const head = value.slice(0, at);
    const tail = value.slice(at + 1);
    const comma = tail.indexOf(",");
    const name = (comma < 0 ? tail : tail.slice(0, comma)).trim().replace(/[.;:]+$/u, "");
    const rest = comma < 0 ? "" : tail.slice(comma + 1);
    if (!head.includes("@") && head.trim() && MAKER.test(name) && LETTER.test(name)) {
      maker = normalizeMaker(name);
      note = rest.split(PY_SPACE).filter(Boolean).join(" ");
      value = head.trimEnd();
    }
  }
  const hashed = PACK_HASH.exec(value);
  if (hashed && hashed.index > 0) {
    packQty = packQty ?? packOf(hashed[1]);
    value = value.slice(0, hashed.index).trimEnd();
  }
  return { article: value, maker, packQty, note };
}

/** «XHP-4@JST» in Артикул → «XHP-4», and «JST» into an EMPTY Производитель. A text with a pack size or a note after the
 *  maker is left as typed: the grid has no «Описание», the server splits it on «Принять» and keeps them there. */
export function splitArticleCell(line: ReceiptLine): ReceiptLine {
  if (line.accepted) return line;
  const split = splitArticleMaker(line.article);
  if (!split.maker || split.packQty || split.note) return line;
  const manufacturer = line.manufacturer.trim() ? line.manufacturer : split.maker;
  return {
    ...line, article: split.article, manufacturer,
    auto: line.auto.filter((key) => key !== "article" && (key !== "manufacturer" || manufacturer === line.manufacturer)),
  };
}

/** An editing of a cell: it is the user's own text now. */
export function editCell(line: ReceiptLine, key: LineKey, value: string): ReceiptLine {
  return { ...line, [key]: value, auto: line.auto.filter((item) => item !== key), model: line.model.filter((item) => item !== key), error: "" };
}

/** A lot already accepted with this supply, shown as a read-only row («Продолжить поставку»). */
export function lineFromLot(lot: WarehouseLot): ReceiptLine {
  return {
    ...emptyLine(),
    article: text(lot.article), quantity: text(lot.inbound), unit: text(lot.unit), item_type: text(lot.item_type),
    manufacturer: text(lot.manufacturer), series: text(lot.series), name: text(lot.name), purpose: text(lot.purpose),
    expiry_date: text(lot.expiry_date), datasheet: text(lot.datasheet), certificate: text(lot.certificate),
    upd_name: text(lot.upd_name), maker_article: text(lot.maker_article), supplier_article: text(lot.supplier_article),
    accepted: { lotId: lot.id, grn: lot.grn, place: lot.storage_location, labels: 0, labelError: "" },
  };
}

export function headerFromReceipt(receipt: WarehouseReceipt): ReceiptHeader {
  return {
    warehouse: receipt.warehouse, supplier: text(receipt.supplier), invoice_number: text(receipt.invoice_number),
    invoice_date: text(receipt.invoice_date), upd_number: text(receipt.upd_number), upd_date: text(receipt.upd_date),
    prd_number: text(receipt.prd_number), prd_date: text(receipt.prd_date), purpose: text(receipt.purpose),
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
    doc("ПРД", text(receipt.prd_number), receipt.prd_date ?? null),
  ].filter(Boolean);
  return parts.length ? `Приход: ${parts.join(" · ")}` : "";
}

/** «№ 17 от 20.10.2026», «№ 17», «от 20.10.2026» or «». */
function docRef(number: string, date: string | null | undefined): string {
  return [number ? `№ ${number}` : "", date ? `от ${shortDate(date)}` : ""].filter(Boolean).join(" ");
}

export type LotDocument = { label: string; value: string; href?: string };

/** «Документы» of a lot card (owner 26.10.2026: «должно быть, но скрыто»): the УПД, the invoice, the payment document
 *  of its delivery, the names the documents give the item. Only what is known; read-only. */
export function lotDocuments(lot: WarehouseLot): LotDocument[] {
  const receipt = lot.receipt;
  const rows: LotDocument[] = [
    { label: "УПД", value: receipt ? docRef(text(receipt.upd_number), receipt.upd_date) : "" },
    { label: "Счёт", value: receipt ? docRef(text(receipt.invoice_number), receipt.invoice_date) : "" },
    { label: "ПРД", value: receipt ? docRef(text(receipt.prd_number), receipt.prd_date) : "" },
    { label: "Наим. по УПД", value: text(lot.upd_name) },
    { label: "Наим. по счёту", value: text(lot.invoice_name) },
  ];
  const file = text(receipt?.scan_file);
  if (file) rows.push({ label: "Файл УПД", value: "открыть", href: file });
  return rows.filter((row) => row.value);
}
