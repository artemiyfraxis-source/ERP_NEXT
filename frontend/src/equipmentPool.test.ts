import { describe, expect, it } from "vitest";
import { equipmentPool, poolToolLabel, poolSetupLabel, setupUnitLabel } from "./equipmentPool";

describe("equipmentPool", () => {
  it("пул по инв. № .X и по артикулу (R)", () => {
    expect(poolToolLabel("EW-50A(R) | Полуавтомат", "ОБ.027.X")).toBe("EW-50A — любой свободный ОБ.027.* · Полуавтомат");
    expect(poolToolLabel("BZW-D2.0S(R)", "ОБ.100.X")).toBe("BZW-D2.0S — любой свободный ОБ.100.*");
  });
  it("только .X без (R): модель — имя как есть", () => {
    expect(poolToolLabel("Пресс BZW", "ОБ.099.x")).toBe("Пресс BZW — любой свободный ОБ.099.*");
  });
  it("только (R) без инв. №", () => {
    expect(poolToolLabel("EW-50A(R)", "")).toBe("EW-50A — любой свободный");
  });
  it("номер станка главнее (R)", () => {
    expect(poolToolLabel("EW-50A(R)", "ОБ.027.1")).toBeNull();
    expect(poolToolLabel("WL-2.0ВТ", "ОБ.027.4")).toBeNull();
    expect(poolToolLabel("EW-50A(R)", "ИН-17")).toBeNull();
  });
  it("голый номер модели: пул только с (R)", () => {
    expect(poolToolLabel("BZW-D2.0S(R)", "ОБ.100")).toBe("BZW-D2.0S — любой свободный ОБ.100.*");
    expect(poolToolLabel("Станок", "ОБ.094")).toBeNull();
  });
  it("латиница, кириллическая Х и пробелы в номере", () => {
    expect(poolToolLabel("A", "OB.100.X")).toBe("A — любой свободный ОБ.100.*");
    expect(poolToolLabel("A", " ОБ . 027 . Х ")).toBe("A — любой свободный ОБ.027.*");
  });
  it("(R) в любом месте имени и примечание « — »", () => {
    expect(poolToolLabel("BZW(R) — кабель до 5", "ОБ.100.X")).toBe("BZW — любой свободный ОБ.100.* · кабель до 5");
    expect(poolToolLabel("BZW-886 | Станок (R) — кабель до 5", "ОБ.100.X")).toBe("BZW-886 — любой свободный ОБ.100.* · Станок · кабель до 5");
  });
  it("пустые значения", () => {
    expect(poolToolLabel(null, undefined)).toBeNull();
    expect(poolToolLabel("", "")).toBeNull();
    expect(poolToolLabel("(R)", "")).toBeNull();
    expect(equipmentPool("Пинцет", "")).toBeNull();
  });
});

describe("setupUnitLabel", () => {
  const ready = new Date(2026, 10, 20, 14, 30).toISOString();
  const unit = { inventory_number: "ОБ.027.2", ready_at: ready };
  it("налаженный станок пула заменяет «любой свободный»", () => {
    expect(setupUnitLabel("EW-50A(R)", "ОБ.027.X", unit)).toBe("EW-50A — ОБ.027.2 · налажен 20.11 14:30");
  });
  it("описание и примечание остаются хвостом", () => {
    expect(setupUnitLabel("EW-50A(R) | Полуавтомат — до 5", "ОБ.027.X", unit)).toBe("EW-50A — ОБ.027.2 · налажен 20.11 14:30 · Полуавтомат · до 5");
  });
  it("станок другого пула игнорируется", () => {
    expect(setupUnitLabel("EW-50A(R)", "ОБ.027.X", { ...unit, inventory_number: "ОБ.100.1" })).toBe("EW-50A — любой свободный ОБ.027.*");
  });
  it("без налаженного станка — текст пула", () => {
    expect(setupUnitLabel("EW-50A(R)", "ОБ.027.X")).toBe("EW-50A — любой свободный ОБ.027.*");
    expect(setupUnitLabel("EW-50A(R)", "ОБ.027.X", null)).toBe("EW-50A — любой свободный ОБ.027.*");
  });
  it("обычный станок и пул-номер вместо станка не заменяются", () => {
    expect(setupUnitLabel("Пинцет", "", unit)).toBeNull();
    expect(setupUnitLabel("EW-50A(R)", "ОБ.027.X", { ...unit, inventory_number: "ОБ.027.X" })).toBe("EW-50A — любой свободный ОБ.027.*");
  });
  it("формат даты dd.MM HH:mm с нулями", () => {
    const early = new Date(2026, 0, 5, 7, 4).toISOString();
    expect(setupUnitLabel("EW-50A(R)", "ОБ.027.X", { ...unit, ready_at: early })).toBe("EW-50A — ОБ.027.2 · налажен 05.01 07:04");
  });
});

describe("poolSetupLabel", () => {
  const u = (inventory_number: string) => ({ inventory_number });
  it("перечисляет налаженные станки пула, хвост описания сохраняется", () => {
    expect(poolSetupLabel("EW-50A(R) | Полуавтомат", "ОБ.027.X", [u("ОБ.027.4"), u("ОБ.027.2"), u("ОБ.027.4")]))
      .toBe("EW-50A — любой свободный ОБ.027.* · налажены: ОБ.027.2, ОБ.027.4 · Полуавтомат");
  });
  it("один станок в разных написаниях номера — одна запись", () => {
    expect(poolSetupLabel("EW-50A(R)", "ОБ.027.X", [u("ОБ.27.2"), u("OB.027.02"), u("ОБ.027.2")]))
      .toBe("EW-50A — любой свободный ОБ.027.* · налажены: ОБ.027.2");
  });
  it("станки другого пула и неточные номера отфильтровываются", () => {
    expect(poolSetupLabel("EW-50A(R)", "ОБ.027.X", [u("ОБ.031.1"), u("ОБ.027.X")])).toBe("EW-50A — любой свободный ОБ.027.*");
  });
  it("без станков — текст пула; обычная строка — null", () => {
    expect(poolSetupLabel("EW-50A(R)", "ОБ.027.X", [])).toBe(poolToolLabel("EW-50A(R)", "ОБ.027.X"));
    expect(poolSetupLabel("Пинцет", "", [u("ОБ.027.2")])).toBeNull();
  });
});
