// @ts-ignore — типы node в проекте не подключены, снимок артикулов читается напрямую из backend/tests
import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import {
  applyHints, withTrailingBlank, applyReceiptPaste, applyScan, buildAccept, canAccept, editCell, emptyHeader, emptyLine, fillEmpty,
  headerChanged, headerFields, mergeHeader, normalizeMaker, receiptLine, splitArticleCell, splitArticleMaker, type ReceiptLine,
} from "./warehouseReceiptLogic";
import type { WarehouseReceipt } from "./api";

declare const process: { cwd(): string };
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
  const accepted = filled({ article: "OLD", quantity: "1", accepted: { lotId: "l", grn: "G", place: "1A1", labels: 0, labelError: "" } });
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

it("splits the maker off like the server does, on every real article of the warehouse", () => {
  const snapshot = JSON.parse(readFileSync(`${process.cwd()}/../backend/tests/warehouse/fixtures/prod-maker-articles-1023.json`, "utf8") as string) as
    Record<string, [string, string, number | null, string]>;
  expect(Object.keys(snapshot)).toHaveLength(209);
  for (const [text, [article, maker, packQty, note]] of Object.entries(snapshot)) {
    expect(splitArticleMaker(text), text).toEqual({ article, maker, packQty, note });
  }
  expect(splitArticleMaker("0430250400 @ TE Connectivity")).toEqual({ article: "0430250400", maker: "TE CONNECTIVITY", packQty: null, note: "" });
  expect(splitArticleMaker("SHF-001T-0.8BS упаковка 5 000 шт.")).toEqual({ article: "SHF-001T-0.8BS", maker: "", packQty: 5000, note: "" });
  for (const kept of ["info@mail.ru", "XHP-4@J", "ABC@123", "@JST", "A@B@JST", "XHP-4@.JST", "51021-0300"]) {
    expect(splitArticleMaker(kept).article, kept).toBe(kept);
  }
  expect(normalizeMaker("RUICHI,")).toBe("RUICHI");
  expect(normalizeMaker("china")).toBe("Китай");
});

it("Артикул «XHP-4@JST» becomes «XHP-4» and fills an empty Производитель only", () => {
  expect(splitArticleCell(filled({ article: "XHP-4@JST" }))).toMatchObject({ article: "XHP-4", manufacturer: "JST" });
  expect(splitArticleCell(filled({ article: "0039012085@Molex", manufacturer: "Molex Inc." })))
    .toMatchObject({ article: "0039012085", manufacturer: "Molex Inc." });
  const plain = filled({ article: "GHR-04V-S" });
  expect(splitArticleCell(plain)).toBe(plain);
  const packed = filled({ article: "SXH-001T-P0.6##6000@JST" }); // the pack has no cell: the server keeps it in «Описание»
  expect(splitArticleCell(packed)).toBe(packed);
  const accepted = filled({ article: "XHP-4@JST", accepted: { lotId: "l", grn: "OLD.26.1", place: "1F4", labels: 0, labelError: "" } });
  expect(splitArticleCell(accepted)).toBe(accepted);
});

it("pasted and scanned articles are split", () => {
  const cells = applyReceiptPaste([emptyLine()], 0, "article", "XHP-4@JST\t5\nPHR-2@JST\t3");
  if ("error" in cells) throw new Error(cells.error);
  expect(cells.lines.slice(0, 2).map((line) => [line.article, line.manufacturer, line.quantity])).toEqual([["XHP-4", "JST", "5"], ["PHR-2", "JST", "3"]]);
  const headed = applyReceiptPaste([emptyLine()], 0, "article", "Артикул\tПроизводитель\tКол-во\nXHP-4@JST\tJ.S.T.\t2");
  if ("error" in headed) throw new Error(headed.error);
  expect(headed.lines[0]).toMatchObject({ article: "XHP-4", manufacturer: "J.S.T.", quantity: "2" });
  const scanned = applyScan(emptyHeader(), [emptyLine()], {
    scan_file: "s.pdf", note: "",
    recognised: { supplier: "", inn: "", upd_number: "", upd_date: "", invoice_number: "", invoice_date: "" },
    lines: [{ article: "0500588001 @MOLEX", name: "Контакт", quantity: "4", unit: "шт" }],
  });
  expect(scanned.lines[0]).toMatchObject({ article: "0500588001", manufacturer: "MOLEX" });
});

it("formats the lot card line", () => {
  expect(receiptLine(null)).toBe("");
  expect(receiptLine({ id: "r", supplier: "ООО", invoice_number: "", invoice_date: null, upd_number: "5", upd_date: "2026-10-01" }))
    .toBe("Приход: ООО · УПД 5 от 01.10.2026");
});

it("keeps exactly one empty row at the bottom", () => {
  expect(withTrailingBlank([filled({ article: "A" })])).toHaveLength(2);
  expect(withTrailingBlank([filled({ article: "A" }), emptyLine(), emptyLine(), emptyLine()])).toHaveLength(2);
  const ready = [filled({ article: "A" }), emptyLine()];
  expect(withTrailingBlank(ready)).toBe(ready);
  expect(withTrailingBlank([])).toHaveLength(1);
});
