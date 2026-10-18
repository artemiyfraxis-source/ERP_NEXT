import { expect, it } from "vitest";
import { changedFields, cycleTimes, groupOpFields, linearPiece, normBasis, opTypeHint, timeByLength, timeUnit, withCycleTimes } from "./opFields";

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

it("the time comes from the cyclogram as the sheet counts it: max of the hands (take + manual + put) and the machine (roll + stroke × strokes)", () => {
  // PRS_TERMINAL_auto из БД.ОП: руки 4 + 2 = 6, машина 3 × 1 = 3 → 6.
  const crimp = { "Номер": "PRS_TERMINAL_auto", "Время Операции": "1,00", "Время Чел, сек/оп; сек/м": "0", "Время машины, сек/оп; сек/м": "0",
    "Время ручных работ для взятия полуфабриката": "4", "Время ручных работ": "", "Время ручных работ для снятия полуфабриката": "2",
    "Скорость работы инструмента": "3,0", "Кол-во операций инструмента": "1", "Время подготовки, сек": "120" };
  expect(cycleTimes(crimp)).toEqual({ human: 6, machine: 3, operation: 6 });
  expect(withCycleTimes(crimp)).toMatchObject({ "Время Операции": "6,00", "Время Чел, сек/оп; сек/м": "6,00", "Время машины, сек/оп; сек/м": "3,00", "Время подготовки, сек": "120" });
  // Машина дольше рук — время по машине (PRS_TERMINAL_multi: 5 + 2 = 7 против 10 × 1).
  expect(cycleTimes({ ...crimp, "Время ручных работ для взятия полуфабриката": "5", "Скорость работы инструмента": "10" })!.operation).toBe(10);
  // Пустая циклограмма — время вводится руками и не трогается; «Время операции» тогда правится.
  const bare = { "Время Операции": "12", "Время подготовки, сек": "60" };
  expect(cycleTimes(bare)).toBeNull();
  expect(withCycleTimes(bare)).toBe(bare);
  expect(groupOpFields(bare).find((group) => group.id === "norms")!.fields.find((field) => field.label === "Время операции")!.readOnly).toBeFalsy();
  expect(groupOpFields(crimp).find((group) => group.id === "norms")!.fields.find((field) => field.label === "Время операции")!.readOnly).toBe(true);
});

it("the roll speed counts only for a «Погонная» operation: there the time follows the wire length, elsewhere it is ignored", () => {
  // PRINT_MARK: статичная с прокатом 2 — прокат не читается: руки 0 + 0 + 1, машина 2 × 1 → 2.
  const print = { "Тип операции": "Статичный", "Время Операции": "2,00", "Время ручных работ для взятия полуфабриката": "0", "Время ручных работ": "0",
    "Время ручных работ для снятия полуфабриката": "1", "Скорость проката, сек/м": "2", "Скорость работы инструмента": "2,0", "Кол-во операций инструмента": "1" };
  expect(timeByLength(print)).toBe(false);
  expect(cycleTimes(print)).toEqual({ human: 1, machine: 2, operation: 2 });
  // Погонная с прокатом: одной цифры нет — время по длине, как в ЭТК; руки дольше машины — берутся руки.
  const cut = { ...print, "Тип операции": "Погонный", "Время ручных работ для взятия полуфабриката": "4" };
  expect(timeByLength(cut)).toBe(true);
  expect(cycleTimes(cut)).toBeNull();
  expect(withCycleTimes(cut)).toBe(cut);
  expect(linearPiece(cut, 100)).toMatchObject({ hands: 5, total: 5 });
  expect(linearPiece(cut, 10000)!.total).toBeCloseTo(7, 6);
});
