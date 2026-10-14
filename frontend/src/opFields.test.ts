import { expect, it } from "vitest";
import { changedFields, groupOpFields, normBasis, timeUnit, typeForBasis } from "./opFields";

const row = {
  opNumber: "CUT_WIRE_auto", opName: "Резка", tOp: "3,50", "Номер": "CUT_WIRE_auto", "Название": "Резка", "Машина": "EW-05F",
  "Инструмент": "", "Апликаторы / модули": "S1", "Время Операции": "3,50", "Время подготовки, сек": "180", "Время машины, сек/оп; сек/м": "3,50",
  "Тип операции": "Погонный", "Программа": "7", "Провод": "", "Разъем": "", "Время доп.операции": "", "Инструкция": "", "Отказы": "word",
  "Полуфабрикат": "x", "Время Чел, сек/оп; сек/м": "0,00", "Уд.Цена ЧЛ, сек": "0,42", "Уд.Цена ЧЛ_МАГ, сек": "0,42", "Уд.Цена МШ, сек": "0,1",
  "Расход на настройку м; шт;": "2", "Время ручных работ": "1", "Скорость проката, сек/м": "3", "Скорость работы инструмента": "0,5",
  "Кол-во операций инструмента": "1", "Цвет": "красный",
};

it("maps columns to groups with Russian names; dead columns and schema duplicates are hidden", () => {
  const groups = groupOpFields(row);
  expect(groups.map((group) => group.id)).toEqual(["ident", "equip", "norms", "other", "raw"]);
  const byId = Object.fromEntries(groups.map((group) => [group.id, group.fields.map((field) => field.label)]));
  expect(byId.ident).toEqual(["Код", "Название"]);
  expect(byId.equip).toEqual(["Машина", "Инструмент", "Апликаторы / модули"]);
  expect(groupOpFields({ "Апликаторы / модули": "S1", "Машина": "M" }).flatMap((group) => group.fields.map((field) => field.label))).toEqual(["Машина", "Апликаторы / модули"]);
  expect(byId.norms).toEqual(["Время операции", "Время подготовки", "Время машины", "Норма на"]);
  expect(byId.raw).toHaveLength(9);
  const keys = groups.flatMap((group) => group.fields.map((field) => field.key));
  for (const hidden of ["Программа", "Провод", "Разъем", "Время доп.операции", "Инструкция", "Отказы", "Полуфабрикат", "opNumber", "tOp"]) expect(keys).not.toContain(hidden);
});

it("an unknown column lands in «Прочее» and is never dropped; the code is read-only", () => {
  const groups = groupOpFields(row);
  expect(groups.find((group) => group.id === "other")?.fields.map((field) => field.key)).toEqual(["Цвет"]);
  expect(groups[0].fields[0].readOnly).toBe(true);
});

it("units of the times follow the norm basis", () => {
  expect(normBasis("Погонный")).toBe("meter");
  expect(normBasis("Статичный")).toBe("operation");
  expect(normBasis("Переменный")).toBe("operation");
  expect(normBasis("")).toBe("");
  expect(timeUnit("Погонный")).toBe("с на метр");
  expect(groupOpFields(row).find((group) => group.id === "norms")?.fields.map((field) => field.unit)).toEqual(["с на метр", "с", "с на метр", undefined]);
  expect(groupOpFields({ ...row, "Тип операции": "Переменный" }).find((group) => group.id === "norms")?.fields[0].unit).toBe("с на операцию");
});

it("choosing the basis keeps a fitting stored word", () => {
  expect(typeForBasis("Переменный", "operation")).toBe("Переменный");
  expect(typeForBasis("Погонный", "operation")).toBe("Статичный");
  expect(typeForBasis("Статичный", "meter")).toBe("Погонный");
});

it("changedFields sends only what differs", () => {
  expect(changedFields({ a: "1", b: "2" }, { a: "1", b: "3", c: "" })).toEqual([{ field: "b", old: "2", value: "3" }]);
});
