import { describe, expect, it } from "vitest";
import { equipmentPool, poolToolLabel } from "./equipmentPool";

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
