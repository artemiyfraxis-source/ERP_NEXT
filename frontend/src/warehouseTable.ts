import type { WarehouseExportRow, WarehouseReferenceEdit } from "./api";

/* SK-14: the stock table for Excel (SUPPLYMAP PortalApp.html WH05_XCOLS_, wh05Export, wh05ParseTable_,
   wh05MapHead_, wh05ReadFile_). The columns are both the header of the exported file and the map to read it
   back. A row is found by Склад + ГРН + Пакет; only reference fields are loaded — the balance is in the file
   for checking and is never read back: stock moves by write-off, return and receipt. */
type TableKey = keyof WarehouseExportRow;

export const STOCK_TABLE_COLUMNS: Array<[TableKey, string]> = [
  ["warehouse", "Склад"], ["grn", "ГРН"], ["package", "Пакет"], ["name", "Наименование"], ["item_type", "Тип"],
  ["unit", "Ед.изм"], ["storage_location", "Место хранения"], ["purpose", "Назначение"], ["unit_mass_g", "Масса 1 шт, г"],
  ["expiry_date", "Срок годности"], ["manufacturer", "Производитель"], ["description", "Описание"],
  ["storage_conditions", "Условия хранения"], ["balance", "Остаток (не загружается)"],
];
const KEY_COLUMNS: TableKey[] = ["warehouse", "grn", "package", "balance"];
const BYTE_ORDER_MARK = String.fromCharCode(0xfeff);
const REPLACEMENT_CHARACTER = String.fromCharCode(0xfffd);

/** The exported table as text: tab separated, one line per row, the header first. */
export function stockTableText(rows: WarehouseExportRow[]) {
  const cell = (value: unknown) => String(value ?? "").replace(/[\t\r\n]+/g, " ");
  const lines = [STOCK_TABLE_COLUMNS.map(([, title]) => title).join("\t")];
  for (const row of rows) lines.push(STOCK_TABLE_COLUMNS.map(([key]) => cell(row[key])).join("\t"));
  return lines.join("\r\n") + "\r\n";
}

/** UTF-16LE with a BOM, like the request template: Excel opens Cyrillic without the import wizard. */
export function stockTableBytes(text: string) {
  const bytes = new Uint8Array(2 + text.length * 2);
  bytes[0] = 0xff;
  bytes[1] = 0xfe;
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    bytes[2 + i * 2] = code & 0xff;
    bytes[3 + i * 2] = code >> 8;
  }
  return bytes;
}

/** File bytes → text by the BOM: our file is UTF-16LE, «CSV UTF-8» has a UTF-8 BOM, a plain Russian Excel
 *  «CSV» is windows-1251 without one (try UTF-8, on broken characters fall back to 1251). */
export function decodeStockTable(bytes: Uint8Array) {
  let encoding = "utf-8";
  if (bytes[0] === 0xff && bytes[1] === 0xfe) encoding = "utf-16le";
  else if (bytes[0] === 0xfe && bytes[1] === 0xff) encoding = "utf-16be";
  let text = new TextDecoder(encoding).decode(bytes);
  if (encoding === "utf-8" && text.includes(REPLACEMENT_CHARACTER)) {
    try { text = new TextDecoder("windows-1251").decode(bytes); } catch { /* keep what we have */ }
  }
  return text;
}

/** Tab (a copy from Excel, our file), semicolon (Russian Excel «CSV») or comma; quotes by the CSV rules.
 *  → rows of cells, blank rows dropped. */
export function parseStockTable(source: string) {
  const text = source.startsWith(BYTE_ORDER_MARK) ? source.slice(1) : source;
  const first = text.split(/\r?\n/)[0] ?? "";
  const separator = first.includes("\t") ? "\t" : first.includes(";") ? ";" : ",";
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (quoted) {
      if (char === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; } else quoted = false;
      } else field += char;
      continue;
    }
    if (char === '"' && field === "") { quoted = true; continue; }
    if (char === separator) { row.push(field); field = ""; continue; }
    if (char === "\n" || char === "\r") {
      if (char === "\r" && text[i + 1] === "\n") i++;
      row.push(field); rows.push(row); row = []; field = "";
      continue;
    }
    field += char;
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  return rows.filter((cells) => cells.some((cell) => cell.trim()));
}

/** Header → field keys: without case, «*» and explanations in brackets. An unknown column is ignored. */
export function stockTableKeys(head: string[]) {
  const norm = (value: string) => value.toLowerCase().replace(/\(.*?\)|\*/g, "").replace(/\s+/g, " ").trim();
  const byTitle = new Map(STOCK_TABLE_COLUMNS.map(([key, title]) => [norm(title), key]));
  return head.map((title) => byTitle.get(norm(title)) ?? null);
}

/** The pasted or loaded table → edits for the import, or the reason it cannot be read. A column left out of
 *  the file is not touched; an empty cell in a kept column clears the value (except the name, on the server). */
export function stockTableEdits(text: string): { edits: WarehouseReferenceEdit[] } | { error: string } {
  const rows = parseStockTable(text);
  if (rows.length < 2) return { error: "Нужна строка заголовков и хотя бы одна строка данных." };
  const keys = stockTableKeys(rows[0]);
  if (!keys.includes("grn")) return { error: "В шапке нет колонки «ГРН» — без неё строки не найти." };
  if (!keys.some((key) => key && !KEY_COLUMNS.includes(key))) {
    return { error: "В файле нет ни одной загружаемой колонки (наименование, тип, место…)." };
  }
  const edits: WarehouseReferenceEdit[] = [];
  for (const cells of rows.slice(1)) {
    const edit: Record<string, string> = {};
    keys.forEach((key, index) => {
      if (key && key !== "balance") edit[key] = String(cells[index] ?? "").trim();
    });
    if (edit.grn) edits.push(edit as WarehouseReferenceEdit);
  }
  return { edits };
}
