import { describe, expect, it } from "vitest";
import { CABLE_COLUMN_ORDER, CABLE_DEFAULT_SHOWN, arrangeCableColumns, knownCableColumn, sortCableColumns } from "./cableColumnOrder";
import type { DataColumn } from "./DataTable";

/* Порядок групп столбцов во всех трёх списках кабелей (владелец 23.10.2026: «своя инфа в своём месте») и их состав —
   без скрытых столбцов (владелец 25.10.2026). */

const column = (key: string): DataColumn<object> => ({ key, title: key, value: () => "" });
const keys = (columns: DataColumn<object>[]) => columns.map((item) => item.key);

describe("cable column order", () => {
  it("puts the owner's own columns into their group, not at the end", () => {
    // as the server sends them: the list's columns, then the owner's («+ Столбец») in the order they were added
    const server = ["cable", "Тип провода", "Наружный диаметр, мм", "Волновое сопротивление, Ом", "Поставщик", "Ссылка на datasheet",
      "Артикул поставщика", "Цена поставщика, ₽/м", "Материал жилы", "Материал диэлектрика", "Ссылка", "Погонная ёмкость, пФ/м",
      "Радиус изгиба неподвижно, мм", "Частота до, ГГц"];
    expect(keys(sortCableColumns(server.map(column)))).toEqual([
      "cable", "Тип провода",
      "Материал жилы", "Материал диэлектрика", "Наружный диаметр, мм",
      "Волновое сопротивление, Ом", "Погонная ёмкость, пФ/м", "Частота до, ГГц",
      "Радиус изгиба неподвижно, мм",
      "Поставщик", "Артикул поставщика", "Ссылка", "Цена поставщика, ₽/м",
      "Ссылка на datasheet",
    ]);
  });

  it("puts a column it does not know at the end of the data, before the supply, in the order it came", () => {
    const sorted = keys(sortCableColumns(["Поставщик", "Полка", "Масса, г/м", "Ячейка", "cable"].map(column)));
    expect(sorted).toEqual(["cable", "Масса, г/м", "Полка", "Ячейка", "Поставщик"]);
    expect(knownCableColumn("Полка")).toBe(false);
    expect(knownCableColumn("Материал оболочки")).toBe(true);
  });

  it("keeps the same group order in every list: identification, construction, electrical, mechanics, supply, the datasheet", () => {
    const at = (key: string) => CABLE_COLUMN_ORDER.indexOf(key);
    const chain = ["Марка", "series", "Тип провода", "Стандарт", "AWG", "Материал жилы", "Материал экрана", "Материал оболочки", "Наружный диаметр, мм",
      "Волновое сопротивление, Ом", "Частота до, ГГц", "Напряжение, В", "Масса, г/м", "Темп. до, °C", "Поставщик", "Цена поставщика, ₽/м",
      "Ссылка на datasheet"];
    expect(chain.every((key) => at(key) >= 0)).toBe(true);
    expect(chain.map(at)).toEqual([...chain.map(at)].sort((a, b) => a - b));
    // each diameter of a coax cable next to its layer
    expect(at("D1")).toBe(at("Материал жилы") - 1);
    expect(at("D2")).toBe(at("Материал диэлектрика") + 1);
    expect(at("D3")).toBe(at("Материал экрана") + 1);
    // the columns removed 25.10.2026 have no place at all
    for (const gone of ["Цена посл., ₽/м без НДС", "Закупок", "Дата цены", "Источник данных", "__card", "__kind", "__reason", "__op", "__fc",
      "__span", "__range", "Цвета"]) expect(at(gone)).toBe(-1);
  });

  it("keeps only the list's columns, all shown — nothing hidden for «Колонки» or «Показать все»; a column the caller names stays", () => {
    const all = ["cable", "Тип провода", "Стандарт", "Материал жилы", "Материал изоляции жилы", "Экран", "Материал экрана", "Поставщик",
      "Цена поставщика, ₽/м", "Дата цены", "Ссылка на datasheet", "Полка", "__reason", "__kind"].map(column);
    const multi = arrangeCableColumns("multi", all, (key) => key === "Полка");
    expect(keys(multi)).toEqual([
      "cable", "Тип провода", "Стандарт", "Материал жилы", "Материал изоляции жилы", "Экран", "Материал экрана", "Полка", "Поставщик", "Цена поставщика, ₽/м",
      "Ссылка на datasheet",
    ]);
    expect(multi.every((item) => !item.hidden)).toBe(true);
    const wire = arrangeCableColumns("wire", all.map((item) => ({ ...item, hidden: true })));
    expect(keys(wire)).toEqual(["cable", "Тип провода", "Стандарт", "Материал жилы", "Поставщик", "Цена поставщика, ₽/м", "Ссылка на datasheet"]);
    expect(wire.every((item) => !item.hidden)).toBe(true);
  });

  it("names the columns of every list (owner 25.10, per column)", () => {
    expect([...CABLE_DEFAULT_SHOWN.wire]).toEqual(["cable", "color", "series", "Тип провода", "Стандарт", "Производители", "awg", "section",
      "strands", "core", "Материал жилы", "Материал оболочки", "outer", "voltage", "temp", "Масса, г/м", "Поставщик", "Артикул поставщика",
      "Цена поставщика, ₽/м", "Ссылка", "Ссылка на datasheet"]);
    expect([...CABLE_DEFAULT_SHOWN.multi]).toEqual(["cable", "Тип провода", "Стандарт", "Производители", "Число жил", "Сечение, мм2", "AWG",
      "Жила: проволок × диаметр, мм", "Материал жилы", "Материал изоляции жилы", "Экран", "Материал экрана", "Материал оболочки", "Наружный диаметр, мм", "Напряжение, В", "Масса, г/м",
      "Темп. от, °C", "Темп. до, °C", "Поставщик", "Артикул поставщика", "Цена поставщика, ₽/м", "Ссылка", "Ссылка на datasheet"]);
    expect(CABLE_DEFAULT_SHOWN.coax.has("Частота до, ГГц") && CABLE_DEFAULT_SHOWN.coax.has("D3") && CABLE_DEFAULT_SHOWN.coax.has("Стандарт")).toBe(true);
    for (const gone of ["__op", "__fc", "__span", "__range", "__card", "__kind", "__reason", "Напряжение, В", "Цвета", "Дата цены"]) {
      expect(CABLE_DEFAULT_SHOWN.coax.has(gone)).toBe(false);
    }
    expect(CABLE_DEFAULT_SHOWN.multi.has("Цвета")).toBe(false);
    // the core construction of a multicore cable stays (owner 25.10: 100 % / 97 % filled)
    expect(CABLE_DEFAULT_SHOWN.multi.has("Материал жилы") && CABLE_DEFAULT_SHOWN.multi.has("Жила: проволок × диаметр, мм")).toBe(true);
  });
});
