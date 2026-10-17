import { expect, it } from "vitest";
import type { WarehouseExportRow } from "./api";
import { decodeStockTable, parseStockTable, stockTableBytes, stockTableEdits, stockTableKeys, stockTableText } from "./warehouseTable";

const row: WarehouseExportRow = {
  warehouse: "ЦАР", grn: "OLD.26.0000001", package: "", balance: "5", name: "Трубка\tтермо", item_type: "Термоусадочная трубка",
  unit: "м", storage_location: "1F4", purpose: "Склад", unit_mass_g: "1,5", expiry_date: "01.03.2027",
  manufacturer: "", description: "две\nстроки", storage_conditions: "", article: "A-1", series: "",
};

it("exports a tab separated UTF-16LE table that reads back into the same edits", () => {
  const text = stockTableText([row]);
  expect(text.split("\r\n")[0]).toBe(
    "Склад\tГРН\tПакет\tНаименование\tТип\tЕд.изм\tМесто хранения\tНазначение\tМасса 1 шт, г\tСрок годности\t"
    + "Производитель\tОписание\tУсловия хранения\tОстаток (не загружается)",
  );
  const bytes = stockTableBytes(text);
  expect([bytes[0], bytes[1]]).toEqual([0xff, 0xfe]);
  const decoded = decodeStockTable(bytes);
  expect(decoded).toBe(text);

  const parsed = stockTableEdits(decoded);
  expect(parsed).toEqual({ edits: [{
    warehouse: "ЦАР", grn: "OLD.26.0000001", package: "", name: "Трубка термо", item_type: "Термоусадочная трубка",
    unit: "м", storage_location: "1F4", purpose: "Склад", unit_mass_g: "1,5", expiry_date: "01.03.2027",
    manufacturer: "", description: "две строки", storage_conditions: "",
  }] });
});

it("reads semicolon CSV with quotes and a header in any case, without unknown or balance columns", () => {
  const rows = parseStockTable(String.fromCharCode(0xfeff) + 'грн;ТИП*;Остаток;Цвет\r\n"OLD.1";"Кабель; ""толстый""";7;красный\n\n;;;\n');
  expect(rows).toEqual([["грн", "ТИП*", "Остаток", "Цвет"], ["OLD.1", 'Кабель; "толстый"', "7", "красный"]]);
  expect(stockTableKeys(rows[0])).toEqual(["grn", "item_type", "balance", null]);
  expect(stockTableEdits(rows.map((cells) => cells.join("\t")).join("\n")))
    .toEqual({ edits: [{ grn: "OLD.1", item_type: 'Кабель; "толстый"' }] });
});

it("refuses a table it cannot load", () => {
  expect(stockTableEdits("ГРН\tТип")).toEqual({ error: "Нужна строка заголовков и хотя бы одна строка данных." });
  expect(stockTableEdits("Тип\nКабель")).toEqual({ error: "В шапке нет колонки «ГРН» — без неё строки не найти." });
  expect(stockTableEdits("Склад\tГРН\tОстаток\nЦАР\tOLD.1\t5"))
    .toEqual({ error: "В файле нет ни одной загружаемой колонки (наименование, тип, место…)." });
});

it("falls back to windows-1251 for a plain Russian Excel CSV", () => {
  const cp1251 = new Uint8Array([0xc3, 0xd0, 0xcd, 0x3b, 0xd2, 0xe8, 0xef]); // «ГРН;Тип»
  expect(decodeStockTable(cp1251)).toBe("ГРН;Тип");
});
