import { expect, it } from "vitest";
import {
  applyHints, applyReceiptPaste, applyScan, buildAccept, canAccept, editCell, emptyHeader, emptyLine, fillEmpty,
  headerChanged, headerFields, mergeHeader, receiptLine, type ReceiptLine,
} from "./warehouseReceiptLogic";
import type { WarehouseReceipt } from "./api";

const filled = (patch: Partial<ReceiptLine>): ReceiptLine => ({ ...emptyLine(), ...patch });

const LEGACY = [
  "Контрагент\t№ УПД\tДата УПД\tНомер счета\tДата счета\tарт ПОСТАВЩИКА\tСЕРИЯ\tприход\tЕд. изм",
  "ООО Рога\tУПД-5\t01.10.2026\tС-7\t02.10.2026\tA-1\t13\t1 500,5\tшт",
  "\t\t\t\t\tA-2\t\t3\tм",
].join("\n");

it("reads a legacy block with a header row: lines and the header fields", () => {
  const result = applyReceiptPaste([emptyLine(), emptyLine()], 0, "article", LEGACY);
  if ("error" in result) throw new Error(result.error);
  expect(result.lines).toHaveLength(2);
  expect(result.lines[0]).toMatchObject({ article: "A-1", series: "13", quantity: "1500.5", unit: "шт" });
  expect(result.lines[1]).toMatchObject({ article: "A-2", quantity: "3", unit: "м" });
  expect(result.header).toEqual({ supplier: "ООО Рога", upd_number: "УПД-5", upd_date: "2026-10-01", invoice_number: "С-7", invoice_date: "2026-10-02" });
});

it("header columns of a block fill only EMPTY header fields", () => {
  const merged = mergeHeader({ ...emptyHeader(), supplier: "Свой" }, { supplier: "ООО Рога", upd_number: "5" });
  expect(merged.supplier).toBe("Свой");
  expect(merged.upd_number).toBe("5");
});

it("a plain block fills from the focused cell and never overwrites an accepted row", () => {
  const accepted = filled({ article: "OLD", quantity: "1", accepted: { lotId: "l", grn: "G", place: "1A1", labelNote: "", labelError: "" } });
  const result = applyReceiptPaste([accepted, emptyLine()], 0, "article", "X\t2\nY\t3");
  if ("error" in result) throw new Error(result.error);
  expect(result.lines[0]).toBe(accepted);
  expect(result.lines[1]).toMatchObject({ article: "Y", quantity: "3" });
});

it("lookup fills only empty cells and marks them as automatic until edited", () => {
  const line = filled({ article: "N-C58P", name: "Моё имя" });
  const next = applyHints(line, {
    info: { name: "N-male", series: "N", item_type: "Коакс", source: "разбор кода", manufacturer: "Amphenol" },
    places: ["1F4"], expected: { order_number: "12", supplier: "ООО" },
  });
  expect(next.name).toBe("Моё имя");
  expect(next).toMatchObject({ series: "N", item_type: "Коакс", manufacturer: "Amphenol", source: "разбор кода", places: ["1F4"], expected: "заказ № 12" });
  expect([...next.auto].sort()).toEqual(["item_type", "manufacturer", "series"]);
  const edited = editCell(next, "series", "M");
  expect(edited.auto).not.toContain("series");
  expect(fillEmpty(edited, { series: "again" }, "x")).toMatchObject({ series: "M" });
});

it("a row can be accepted with an article and a quantity above zero", () => {
  expect(canAccept(filled({ article: "A", quantity: "2,5" }))).toBe(true);
  expect(canAccept(filled({ article: "A", quantity: "0" }))).toBe(false);
  expect(canAccept(filled({ quantity: "2" }))).toBe(false);
});

it("builds the accept body: unit and purpose fall back, labels and confirmation travel along", () => {
  const body = buildAccept(filled({ article: " A ", quantity: "2,5", expiry_date: "" }), emptyHeader(), " 1F4 ", 3, true);
  expect(body).toEqual({
    article: "A", quantity: "2.5", unit: "шт", item_type: "", manufacturer: "", series: "", name: "", purpose: "Склад",
    expiry_date: null, datasheet: "", certificate: "", storage_location: "1F4", labels: 3, confirm_expected: true,
  });
});

it("tells whether the header changed since the saved supply", () => {
  const header = { ...emptyHeader(), supplier: "ООО", invoice_date: "2026-10-02" };
  const saved = { ...headerFields(header, ""), id: "r", version: 1, scan_file: "", created_at: "", created_by: "", lots_count: 0 } as unknown as WarehouseReceipt;
  expect(headerChanged(saved, header, "")).toBe(false);
  expect(headerChanged(saved, { ...header, upd_number: "9" }, "")).toBe(true);
});

it("a scan fills empty header fields and appends its lines", () => {
  const result = applyScan({ ...emptyHeader(), supplier: "Свой" }, [emptyLine()], {
    scan_file: "s.pdf", note: "",
    recognised: { supplier: "ООО Другой", inn: "", upd_number: "17", upd_date: "03.10.2026", invoice_number: "", invoice_date: "" },
    lines: [{ article: "Z-1", name: "Зет", quantity: "4", unit: "шт" }],
  });
  expect(result.header).toMatchObject({ supplier: "Свой", upd_number: "17", upd_date: "2026-10-03" });
  expect(result.lines[0]).toMatchObject({ article: "Z-1", name: "Зет", quantity: "4" });
  expect(result.added).toBe(1);
});

it("formats the lot card line", () => {
  expect(receiptLine(null)).toBe("");
  expect(receiptLine({ id: "r", supplier: "ООО", invoice_number: "", invoice_date: null, upd_number: "5", upd_date: "2026-10-01" }))
    .toBe("Приход: ООО · УПД 5 от 01.10.2026");
});
