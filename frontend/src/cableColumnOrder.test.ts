import { describe, expect, it } from "vitest";
import { CABLE_COLUMN_ORDER, CABLE_DEFAULT_SHOWN, arrangeCableColumns, knownCableColumn, sortCableColumns } from "./cableColumnOrder";
import type { DataColumn } from "./DataTable";

/* Порядок групп столбцов во всех трёх списках кабелей (владелец 23.10.2026: «своя инфа в своём месте»). */

const column = (key: string): DataColumn<object> => ({ key, title: key, value: () => "" });
const keys = (columns: DataColumn<object>[]) => columns.map((item) => item.key);

describe("cable column order", () => {
  it("puts the owner's own columns into their group, not at the end", () => {
    // as the server sends them: the list's columns, then the owner's («+ Столбец») in the order they were added
    const server = ["cable", "Тип провода", "Наружный диаметр, мм", "Волновое сопротивление, Ом", "Поставщик", "Источник данных",
      "Артикул поставщика", "Цена поставщика, ₽/м", "Материал жилы", "Материал диэлектрика", "Ссылка", "Погонная ёмкость, пФ/м",
      "Радиус изгиба неподвижно, мм", "Дата цены"];
    expect(keys(sortCableColumns(server.map(column)))).toEqual([
      "cable", "Тип провода",
      "Материал жилы", "Материал диэлектрика", "Наружный диаметр, мм",
      "Волновое сопротивление, Ом", "Погонная ёмкость, пФ/м",
      "Радиус изгиба неподвижно, мм",
      "Поставщик", "Артикул поставщика", "Ссылка", "Цена поставщика, ₽/м", "Дата цены",
      "Источник данных",
    ]);
  });

  it("puts a column it does not know at the end of the data, before the supply, in the order it came", () => {
    const sorted = keys(sortCableColumns(["Поставщик", "Полка", "Масса, г/м", "Ячейка", "cable"].map(column)));
    expect(sorted).toEqual(["cable", "Масса, г/м", "Полка", "Ячейка", "Поставщик"]);
    expect(knownCableColumn("Полка")).toBe(false);
    expect(knownCableColumn("Материал оболочки")).toBe(true);
  });

  it("keeps the same group order in every list: identification, construction, electrical, mechanics, supply, service", () => {
    const at = (key: string) => CABLE_COLUMN_ORDER.indexOf(key);
    const chain = ["Марка", "Тип провода", "AWG", "Материал жилы", "Материал экрана", "Материал оболочки", "Наружный диаметр, мм",
      "Волновое сопротивление, Ом", "Напряжение, В", "Масса, г/м", "Темп. до, °C", "Поставщик", "Закупок", "Ссылка на datasheet", "__reason"];
    expect(chain.map(at)).toEqual([...chain.map(at)].sort((a, b) => a - b));
    // each diameter of a coax cable next to its layer
    expect(at("D1")).toBe(at("Материал жилы") - 1);
    expect(at("D2")).toBe(at("Материал диэлектрика") + 1);
    expect(at("D3")).toBe(at("Материал экрана") + 1);
  });

  it("shows each list's «Только нужные» and hides the rest; a column the caller names stays shown", () => {
    const all = ["cable", "Тип провода", "Стандарт", "Материал жилы", "Материал изоляции жилы", "Экран", "Материал экрана", "Поставщик",
      "Цена поставщика, ₽/м", "Дата цены", "Ссылка на datasheet", "Полка"].map(column);
    const multi = arrangeCableColumns("multi", all, (key) => key === "Полка");
    expect(multi.filter((item) => !item.hidden).map((item) => item.key)).toEqual([
      "cable", "Тип провода", "Материал изоляции жилы", "Экран", "Материал экрана", "Полка", "Поставщик", "Цена поставщика, ₽/м",
    ]);
    const wire = arrangeCableColumns("wire", all);
    expect(wire.filter((item) => !item.hidden).map((item) => item.key)).toEqual(["cable", "Материал жилы", "Поставщик", "Цена поставщика, ₽/м"]);
  });

  it("names the owner's «нужные» of every list", () => {
    expect([...CABLE_DEFAULT_SHOWN.wire]).toEqual(["cable", "color", "awg", "section", "strands", "core", "Материал жилы", "Материал оболочки",
      "outer", "voltage", "temp", "Поставщик", "Артикул поставщика", "Цена поставщика, ₽/м", "Ссылка"]);
    expect(CABLE_DEFAULT_SHOWN.coax.has("__op") && CABLE_DEFAULT_SHOWN.coax.has("D3")).toBe(true);
    expect(CABLE_DEFAULT_SHOWN.multi.has("Материал жилы")).toBe(false);
  });
});
