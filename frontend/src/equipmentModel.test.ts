import { describe, expect, it } from "vitest";
import type { TechOperationRecord } from "./api";
import { applicatorTokens, groupModels, servedBy, servedUnits, inventoryOrder, inventoryRange, invOf, operationField, operationsOfModel, parseInventory, typeOf } from "./equipmentModel";

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

  it("reads and sorts applicator codes and measuring instruments without inventing numbers", () => {
    expect(parseInventory("M10")).toEqual({ prefix: "M", base: 10, baseText: "10", suffix: "" });
    expect(parseInventory("СИ.001.2")).toEqual({ prefix: "СИ.", base: 1, baseText: "001", suffix: "2" });
    expect(["M10", "M2", "A1", "S3"].sort(inventoryOrder)).toEqual(["A1", "M2", "M10", "S3"]);
    expect(parseInventory("Q1")).toBeNull();
    expect(invOf(unit("a", { Тип: "Аппликаторы", "Инв. номер": "ОБ.106", "Код в базе терминалов": " m10 " }))).toBe("M10");
    expect(invOf(unit("b", { Тип: "Аппликаторы", "Инв. номер": "ОБ.126" }))).toBe("");
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

describe("equipment identity", () => {
  it("keeps supporting classification and role without requiring or returning operation links", () => {
    const [model] = groupModels([{ ...unit("s", { Тип: "Обеспечивающее оборудование", "Инв. номер": "ОБ.200", "Для базы": "Компрессор" }),
      equipment_model_id: "m", equipment_role: "supporting" }]);
    expect(model.type).toBe("Обеспечивающее оборудование");
    expect(model.role).toBe("supporting");
    expect(invOf(model.items[0])).toBe("ОБ.200");
    expect(operationsOfModel(model, [])).toEqual([]);
    expect(operationsOfModel(model, [unit("o", { Машина: "Компрессор" })])).toEqual([]);
    expect(operationsOfModel(model, [{ ...unit("o", {}), equipment_links: [{ column: "Машина", position: 0, model_id: "m" }] }])).toEqual([]);
  });

  it("groups by equipment_model_id, not by name + article + maker; the role comes from the units", () => {
    const models = groupModels([
      { ...unit("a", { "Наменование": "Стрипер", "Инв. номер": "ОБ.100.1" }), equipment_model_id: "m1", equipment_role: "operational" },
      { ...unit("b", { "Наменование": "Стрипер (другое имя)", "Артикул": "X", "Инв. номер": "ОБ.100.2" }), equipment_model_id: "m1", equipment_role: "operational" },
      { ...unit("c", { "Наменование": "Стрипер", "Инв. номер": "ОБ.101" }), equipment_model_id: "m2", equipment_role: "supporting" },
    ]);
    expect(models).toHaveLength(2);
    expect(models.find((model) => model.id === "m1")!.items.map((item) => item.id)).toEqual(["a", "b"]);
    expect(models.find((model) => model.id === "m2")!.role).toBe("supporting");
  });

  it("finds the operations of a model through equipment_links, not by the «Машина» text", () => {
    const [model] = groupModels([{ ...unit("a", { "Наменование": "Станция", "Для базы": "T21/Станция", "Инв. номер": "ОБ.071.1" }), equipment_model_id: "m1" }]);
    const op = (id: string, fields: Record<string, string>, links: Array<{ column: string; position: number; model_id: string }>) =>
      ({ id, tab: "op", display_text: "", source_sheet: "", fields, equipment_links: links }) as TechOperationRecord;
    const ops = [op("o1", { "Машина": "другое написание" }, [{ column: "Машина", position: 0, model_id: "m1" }]), op("o2", { "Машина": "T21/Станция" }, [])];
    expect(operationsOfModel(model, ops).map((item) => item.id)).toEqual(["o1"]);
  });

  it("lists the machines a supporting unit serves and who serves a machine", () => {
    const pump = { ...unit("p", { "Инв. номер": "ОБ.200" }), serves_unit_ids: ["m"] } as TechOperationRecord;
    const machine = unit("m", { "Инв. номер": "ОБ.010" });
    expect(servedUnits(pump, [pump, machine]).map((item) => item.id)).toEqual(["m"]);
    expect(servedBy(machine, [pump, machine]).map((item) => item.id)).toEqual(["p"]);
  });
});
