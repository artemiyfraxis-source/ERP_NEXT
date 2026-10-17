import { expect, it } from "vitest";
import { changedFields, groupOpFields, linearPiece, normBasis, opTypeHint, timeUnit } from "./opFields";

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
  expect(groups.map((group) => group.id)).toEqual(["ident", "equip", "norms", "nodes", "other", "raw"]);
  const byId = Object.fromEntries(groups.map((group) => [group.id, group.fields.map((field) => field.label)]));
  expect(byId.ident).toEqual(["Код", "Название"]);
  expect(byId.equip).toEqual(["Машина", "Инструмент", "Апликаторы / модули"]);
  expect(groupOpFields({ "Апликаторы / модули": "S1", "Машина": "M" }).flatMap((group) => group.fields.map((field) => field.label))).toEqual(["Машина", "Апликаторы / модули"]);
  expect(byId.norms).toEqual(["Тип операции", "Время операции", "Время подготовки", "Время машины"]);
  // Узлы станка и ручные работы — видимая группа; в свёрнутой остались только цены и «Время Чел».
  expect(byId.nodes).toEqual(["Скорость проката", "Скорость работы инструмента", "Кол-во операций инструмента", "Ручные работы", "Расход на настройку"]);
  expect(byId.raw).toEqual(["Время Чел, сек/оп; сек/м", "Уд.Цена ЧЛ, сек", "Уд.Цена ЧЛ_МАГ, сек", "Уд.Цена МШ, сек"]);
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
  expect(groupOpFields(row).find((group) => group.id === "norms")?.fields.map((field) => field.unit)).toEqual([undefined, "с на метр", "с", "с на метр"]);
  expect(groupOpFields({ ...row, "Тип операции": "Переменный" }).find((group) => group.id === "norms")?.fields[1].unit).toBe("с на операцию");
});

it("the operation type carries its hint; the roll speed is shown honestly as м/с", () => {
  expect(opTypeHint("Погонный")).toBe("по длине провода");
  expect(opTypeHint("Переменный")).toBe("по количеству, время зависит от изделия");
  expect(opTypeHint("Статичный")).toBe("фиксированное на штуку");
  const roll = groupOpFields(row).find((group) => group.id === "nodes")!.fields[0];
  expect([roll.unit, roll.keep]).toEqual(["м/с (подача)", true]);
  expect(groupOpFields({ ...row, "Тип операции": "Статичный" }).find((group) => group.id === "nodes")!.fields[0].keep).toBe(false);
});

it("a «Погонная» operation's piece time follows the node speeds and the wire length", () => {
  const crimp = { ...row, "Скорость проката, сек/м": "3", "Скорость работы инструмента": "0,2", "Кол-во операций инструмента": "5" };
  const piece = linearPiece(crimp, 100)!;
  expect(piece.feed).toBeCloseTo(0.0333, 4);
  expect(piece.tool).toBeCloseTo(1, 6);
  expect(piece.total).toBeCloseTo(1.0333, 4);
  expect(linearPiece(crimp, 1000)!.total).toBeCloseTo(1.3333, 4);
  expect(linearPiece({ ...crimp, "Скорость проката, сек/м": "0" }, 100)).toBeNull();
  expect(linearPiece({ ...crimp, "Тип операции": "Статичный" }, 100)).toBeNull();
});

it("changedFields sends only what differs", () => {
  expect(changedFields({ a: "1", b: "2" }, { a: "1", b: "3", c: "" })).toEqual([{ field: "b", old: "2", value: "3" }]);
});
