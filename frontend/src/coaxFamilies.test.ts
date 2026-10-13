import { describe, expect, it } from "vitest";
import type { CableRow } from "./api";
import { coaxFamily, coaxFamilyName, coaxNaming, compareFamilies, sortCoax } from "./coaxFamilies";

const row = (cable: string, extra: Partial<CableRow> = {}): CableRow => ({
  id: cable, kind: "coax", auto_kind: "coax", reason: "", manual: false, cable, record: null, dia: null, dia_hint: null, ...extra,
});

describe("coax families", () => {
  it("names the family: one group per RG number, LMR as one", () => {
    expect(coaxFamilyName("RG316")).toBe("RG-316");
    expect(coaxFamilyName("RG-316 A/U")).toBe("RG-316");
    expect(coaxFamilyName("rg 174")).toBe("RG-174");
    expect(coaxFamilyName("RG1.37")).toBe("RG");
    expect(coaxFamilyName("RG6/U")).toBe("RG-6");
    expect(coaxFamilyName("LMR-195")).toBe("LMR");
    expect(coaxFamilyName("LMR400")).toBe("LMR");
    expect(coaxFamilyName("Belden 9913")).toBe("BELDEN");
    expect(coaxFamilyName("")).toBe("Без семейства");
  });

  it("ignores the card's descriptive class: the family is the mark's", () => {
    expect(coaxFamily(row("RG316", { card: { family: "Гибкий тонкий" } as CableRow["card"] }))).toBe("RG-316");
  });

  it("orders groups RG by number, LMR, others, unnamed last", () => {
    const names = ["Без семейства", "SYV", "LMR", "RG-316", "RG-58", "RG-174"];
    expect(names.sort(compareFamilies)).toEqual(["RG-58", "RG-174", "RG-316", "LMR", "SYV", "Без семейства"]);
  });

  it("sorts rows by family then mark, without touching the input", () => {
    const input = [row("LMR-195"), row("RG316"), row("RG-174"), row("RG-58 A/U"), row("RG-58 C/U")];
    expect(sortCoax(input).map((item) => item.cable)).toEqual(["RG-58 A/U", "RG-58 C/U", "RG-174", "RG316", "LMR-195"]);
    expect(input[0].cable).toBe("LMR-195");
  });

  it("reads full name and aliases off the card", () => {
    const card = { full_name: "Кабель RG-316", aliases: [{ alias: "M17/113" }, { alias: "" }] } as unknown as CableRow["card"];
    expect(coaxNaming(row("RG316", { card }))).toEqual({ fullName: "Кабель RG-316", aliases: ["M17/113"] });
    expect(coaxNaming(row("RG316"))).toEqual({ fullName: "", aliases: [] });
  });
});
