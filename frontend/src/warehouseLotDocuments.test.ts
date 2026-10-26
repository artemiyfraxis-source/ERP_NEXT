/* Owner 26.10.2026: «по наименованию из УПД при приёме всё раскладывалось — и тип, и артикул, и наименование наше;
 * склад увеличиваем: там должно быть, но скрыто — наименование по счёту, по УПД, даты, файлы, номера УПД и счёта»;
 * ПРД — «К платёжно-расчётному документу № … от …», apart from the invoice. */
import { afterEach, beforeEach, expect, it } from "vitest";
import type { WarehouseLot, WarehouseReceipt, WarehouseReceiptScan } from "./api";
import { COLUMN_TITLES, DOCUMENT_COLUMNS, defaultLayout, readLayouts, shownColumns } from "./WarehouseColumns";
import {
  applyHints, applyScan, buildAccept, editCell, emptyHeader, emptyLine, headerFields, headerFromReceipt, lineFromLot, lotDocuments,
  receiptLine,
} from "./warehouseReceiptLogic";

const UPD_NAME = "Контакт обжимной SXH-001T-P0.6 (JST) упаковка 6000 шт, код 00-00042353";
const SCAN: WarehouseReceiptScan = {
  scan_file: "/api/stock/receipts/files/f1", note: "",
  recognised: {
    supplier: "ООО Пример", inn: "", upd_number: "17", upd_date: "2026-10-20", invoice_number: "45", invoice_date: "2026-10-01",
    prd_number: "916, 917", prd_date: "2026-10-19",
  },
  lines: [
    { article: "SXH-001T-P0.6", name: "Контакт обжимной JST SXH, 0,08–0,2 мм²", upd_name: UPD_NAME, item_type: "Терминал", manufacturer: "JST", quantity: "6000", unit: "шт" },
    { article: "Z-9", name: "Хомут 2,5×100", upd_name: "Хомут нейлоновый 2,5х100 белый", quantity: "2", unit: "упак" },
  ],
};

function scanned() {
  return applyScan(emptyHeader(), [emptyLine()], SCAN);
}

it("a scan brings our name, type and maker as replaceable cells and keeps the printed УПД line apart", () => {
  const { header, lines } = scanned();
  expect(header).toMatchObject({ invoice_number: "45", invoice_date: "2026-10-01", prd_number: "916, 917", prd_date: "2026-10-19" });
  expect(lines[0]).toMatchObject({ name: "Контакт обжимной JST SXH, 0,08–0,2 мм²", item_type: "Терминал", manufacturer: "JST", upd_name: UPD_NAME });
  expect(lines[0].model).toEqual(["name", "item_type", "manufacturer"]);
  expect(lines[1]).toMatchObject({ name: "Хомут 2,5×100", item_type: "", upd_name: "Хомут нейлоновый 2,5х100 белый" });
  expect(lines[1].model).toEqual(["name"]);
});

it("the catalog replaces the model's name and type until the storekeeper edits them", () => {
  const line = scanned().lines[0];
  const hinted = applyHints(line, { info: { name: "Контакт SXH-001T-P0.6", item_type: "Контакт", unit: "шт", source: "партия OLD.26.0000001" } as never });
  expect(hinted).toMatchObject({ name: "Контакт SXH-001T-P0.6", item_type: "Контакт", manufacturer: "JST", upd_name: UPD_NAME });
  expect(hinted.model).toEqual(["manufacturer"]);
  expect([...hinted.auto].sort()).toEqual(["item_type", "name"]);

  const typed = editCell(line, "name", "Мой контакт");
  const kept = applyHints(typed, { info: { name: "Контакт SXH-001T-P0.6", source: "справочник" } as never });
  expect(kept.name).toBe("Мой контакт");
});

it("a catalog without a name leaves the model's name in place", () => {
  const line = scanned().lines[0];
  const hinted = applyHints(line, { info: { name: "", series: "SXH", source: "справочник" } as never });
  expect(hinted).toMatchObject({ name: "Контакт обжимной JST SXH, 0,08–0,2 мм²", series: "SXH" });
  expect(hinted.model).toContain("name");
});

it("accept sends the printed УПД line; the header carries the ПРД apart from the invoice", () => {
  const { header, lines } = scanned();
  expect(buildAccept(lines[0], header, "2A1", 1, false)).toMatchObject({ name: "Контакт обжимной JST SXH, 0,08–0,2 мм²", upd_name: UPD_NAME });
  expect(headerFields(header, "")).toMatchObject({ invoice_number: "45", prd_number: "916, 917", prd_date: "2026-10-19" });
  const saved = { ...headerFields(header, ""), id: "r", version: 1, scan_file: "", created_at: "", created_by: "", lots_count: 0 } as WarehouseReceipt;
  expect(headerFromReceipt(saved)).toMatchObject({ prd_number: "916, 917", prd_date: "2026-10-19" });
  expect(headerFromReceipt({ ...saved, prd_number: undefined, prd_date: undefined })).toMatchObject({ prd_number: "", prd_date: "" });
});

const LOT = {
  id: "l1", grn: "OLD.26.0000009", name: "Контакт обжимной JST SXH", upd_name: UPD_NAME, invoice_name: "",
  receipt: {
    id: "r1", supplier: "ООО Пример", invoice_number: "45", invoice_date: "2026-10-01", upd_number: "17", upd_date: "2026-10-20",
    prd_number: "916", prd_date: "2026-10-19", scan_file: "/api/stock/receipts/files/f1",
  },
} as unknown as WarehouseLot;

it("the lot's «Документы» show only what is known, the file as a link", () => {
  expect(lotDocuments(LOT)).toEqual([
    { label: "УПД", value: "№ 17 от 20.10.2026" },
    { label: "Счёт", value: "№ 45 от 01.10.2026" },
    { label: "ПРД", value: "№ 916 от 19.10.2026" },
    { label: "Наим. по УПД", value: UPD_NAME },
    { label: "Файл УПД", value: "открыть", href: "/api/stock/receipts/files/f1" },
  ]);
  expect(lotDocuments({ ...LOT, receipt: null, upd_name: undefined } as unknown as WarehouseLot)).toEqual([]);
  expect(receiptLine(LOT.receipt)).toBe("Приход: ООО Пример · УПД 17 от 20.10.2026 · счёт 45 от 01.10.2026 · ПРД 916 от 19.10.2026");
  expect(lineFromLot(LOT).upd_name).toBe(UPD_NAME);
});

// --- the hidden columns of «Таблица склада» --------------------------------------------------------------------

const STORAGE_KEY = "erp-table:stock-columns:v3";
beforeEach(() => window.localStorage.clear());
afterEach(() => window.localStorage.clear());

it("the document columns are in both tables, off by default (owner 26.10: the positions table got them too)", () => {
  for (const kind of ["flat", "grouped"] as const) {
    const layout = defaultLayout(kind);
    expect(layout.order).toEqual(expect.arrayContaining([...DOCUMENT_COLUMNS, "maker"]));
    expect(shownColumns(layout, null).some((key) => [...DOCUMENT_COLUMNS, "maker"].includes(key as never))).toBe(false);
  }
  expect(DOCUMENT_COLUMNS.map((key) => COLUMN_TITLES[key])).toEqual([
    "Наим. по УПД", "Наим. по счёту", "№ счёта", "Дата счёта", "№ УПД", "Дата УПД", "№ ПРД", "Дата ПРД", "Файл УПД",
  ]);
});

it("a layout saved before the document columns gets them hidden at the end; switched on, they show", () => {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
    flat: { order: ["type", "grn", "available", "reserved", "balance", "place", "series", "article", "name", "purpose", "warehouse"], hidden: [] },
  }));
  const flat = readLayouts().flat;
  expect(flat.order.slice(-DOCUMENT_COLUMNS.length)).toEqual(DOCUMENT_COLUMNS);
  expect(flat.hidden).toEqual(expect.arrayContaining(DOCUMENT_COLUMNS));
  const on = { ...flat, hidden: flat.hidden.filter((key) => key !== "upd_name" && key !== "prd_date") };
  expect(shownColumns(on, null)).toEqual(expect.arrayContaining(["upd_name", "prd_date"]));
});
