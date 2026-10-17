import { describe, expect, it } from "vitest";
import type { TechOperationRecord } from "./api";
import { applicatorTokens, groupModels, inventoryOrder, inventoryRange, operationField, operationsOfModel, parseInventory, typeOf } from "./equipmentModel";

const unit = (id: string, fields: Record<string, string>): TechOperationRecord =>
  ({ id, tab: "ob", display_text: "", source_sheet: "", fields } as TechOperationRecord);

describe("equipment model", () => {
  it("reads models, units and the legacy numbers", () => {
    expect(parseInventory("ОБ.070.8")).toMatchObject({ prefix: "ОБ.", base: 70, suffix: "8" });
    expect(parseInventory("ОБ.79.1")).toMatchObject({ base: 79 });
    expect(parseInventory("ОСН.001")).toMatchObject({ prefix: "ОСН.", suffix: "" });
    expect(parseInventory("станок")).toBeNull();
    expect(["ОБ.79.10", "ОБ.79.9", "ОБ.79.1"].sort(inventoryOrder)).toEqual(["ОБ.79.1", "ОБ.79.9", "ОБ.79.10"]);
  });

  it("shows a run of units as a range", () => {
    expect(inventoryRange(["ОБ.070.2", "ОБ.070.1", "ОБ.070.8"])).toBe("ОБ.070.1–8");
    expect(inventoryRange(["ОБ.044"])).toBe("ОБ.044");
    expect(inventoryRange(["ОБ.044", "ОБ.044.2"])).toBe("ОБ.044, ОБ.044.2");
    expect(inventoryRange(["ОБ.100.1", "ОБ.101"])).toBe("ОБ.100.1, ОБ.101");
  });

  it("groups units of one name + article + maker; a nameless unit stays alone; ПР counts as Оборудование", () => {
    const same = { "Наменование": "Стрипер", "Артикул": "BZW-D2.0S", "Производитель": "Bozwang", "Тип": "Оборудование (ПР)" };
    const models = groupModels([
      unit("a", { ...same, "Инв. номер": "ОБ.100.2" }), unit("b", { ...same, "Инв. номер": "ОБ.100.1" }),
      unit("c", { "Наменование": "Стрипер", "Артикул": "OTHER", "Инв. номер": "ОБ.101" }),
      unit("d", { "Инв. номер": "ОБ.020" }), unit("e", { "Инв. номер": "ОБ.021" }),
    ]);
    expect(models).toHaveLength(4);
    const strip = models.find((model) => model.article === "BZW-D2.0S")!;
    expect(strip.items.map((item) => item.id)).toEqual(["b", "a"]);
    expect(typeOf(strip.items[0])).toBe("Оборудование");
  });

  it("finds the operations of a model by its «Для базы» in the column that suits the type", () => {
    const [model] = groupModels([unit("a", { "Наменование": "Станция", "Артикул": "T21", "Для базы": "T21/Станция", "Инв. номер": "ОБ.071.1" })]);
    const ops = [
      { id: "o1", tab: "op", display_text: "", source_sheet: "", fields: { "Машина": "t21/станция" } },
      { id: "o2", tab: "op", display_text: "", source_sheet: "", fields: { "Машина": "T21(R)/Станция" } },
    ] as TechOperationRecord[];
    expect(operationsOfModel(model, ops).map((op) => op.id)).toEqual(["o1"]);
    expect(operationField("Матрицы")).toBe("Апликаторы / модули");
    expect(operationField("Обжимной инструмент")).toBe("Инструмент");
    expect(operationField("Оборудование")).toBe("Машина");
  });

  it("splits an «Аппликатор» cell into whole tokens and marks the doubtful ones", () => {
    expect(applicatorTokens("S10/M10/M19/M49/A8").map((token) => token.code)).toEqual(["S10", "M10", "M19", "M49", "A8"]);
    expect(applicatorTokens("возможно S12/M12")).toEqual([{ code: "S12", doubtful: true }, { code: "M12", doubtful: true }]);
    expect(applicatorTokens("S5 совмест. M2")).toEqual([{ code: "S5", doubtful: false }, { code: "M2", doubtful: true }]);
  });
});
