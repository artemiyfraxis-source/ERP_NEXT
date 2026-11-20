import { describe, expect, it } from "vitest";
import { equipmentPool, poolInventory, poolToolLabel } from "./equipmentPool";

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
  it("обычный станок не меняется", () => {
    expect(poolToolLabel("WL-2.0ВТ", "ОБ.027.4")).toBeNull();
    expect(equipmentPool("Пинцет", "")).toBeNull();
  });
  it("пустые значения", () => {
    expect(poolToolLabel(null, undefined)).toBeNull();
    expect(poolToolLabel("", "")).toBeNull();
    expect(poolToolLabel("(R)", "")).toBeNull();
    expect(poolInventory(" ОБ.027.X ")).toBe("ОБ.027.*");
    expect(poolInventory("ОБ.027.1")).toBeNull();
  });
});
