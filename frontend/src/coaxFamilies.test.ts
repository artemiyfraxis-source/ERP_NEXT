import { describe, expect, it } from "vitest";
import type { CableRow } from "./api";
import { coaxGroupOf, coaxNaming, coaxPlace, sortCoax } from "./coaxFamilies";

const row = (cable: string, extra: Partial<CableRow> = {}): CableRow => ({
  id: cable, kind: "coax", auto_kind: "coax", reason: "", manual: false, cable, record: null, dia: null, dia_hint: null, ...extra,
});

// A slice of the shop's coax base (16.10.2026), in the order the owner wants it: groups by the head of the mark.
const MARKS = [
  "D-FB:5D-FB", "D-FB:5D-FB Cu", "D-FB:8D-FB", "D-FB:10D-FB",
  "LMR:LMR-100", "LMR:LMR-195", "LMR:LMR-400", "LMR:LMR-400 UF",
  "RF:RF 0.81", "RF:RF 1.13", "RF:RF 1.37",
  "RG:RG1.37", "RG:RG-6", "RG:RG-6/U", "RG:RG-11", "RG:RG58", "RG:RG-58 A/U", "RG:RG-58 C/U", "RG:RG-59", "RG:RG-142",
  "RG:RG-174", "RG:RG-179B/U", "RG:RG-316", "RG:RG-316D",
  "SAT:SAT-703", "SAT:SAT-703B",
  "SF:SF-047", "SF:SF-047 SPC FEP", "SF:SF-141", "SF:SF-141 100 Ом", "SF:SF-141 FEP",
  "РК:РК50-2-22", "РК:РК50-9-32-С", "РК:РК50-12-331-С",
  "Фидеры:1/2″", "Фидеры:1/2 Baltic Signal BSF-50-12 LSZH", "Фидеры:7/8″",
  "Прочие:GNS-360B", "Прочие:TZC500-97",
];
const CATALOGUE = MARKS.map((item) => item.split(":")[1]);
const GROUPS = MARKS.map((item) => item.split(":")[0]);

describe("coax catalogue order (owner 16.10: groups by the head of the mark — RG, SF, SAT…)", () => {
  it("groups by the head and orders inside by the mark's numbers, base before variants, whatever order rows come in", () => {
    const rows = [...CATALOGUE].sort().reverse().map((cable) => row(cable));
    expect(sortCoax(rows).map((item) => item.cable)).toEqual(CATALOGUE);
    expect(CATALOGUE.map((cable) => coaxGroupOf(rows)(row(cable)))).toEqual(GROUPS);
  });

  it("a head only one mark has goes to «Прочие»; RG-6 before RG-58 by number", () => {
    const rows = [row("RG-58"), row("RG-6"), row("LDF4-50A"), row("RG-142")];
    expect(rows.map(coaxGroupOf(rows))).toEqual(["RG", "RG", "Прочие", "RG"]);
    expect(sortCoax(rows).map((item) => item.cable)).toEqual(["RG-6", "RG-58", "RG-142", "LDF4-50A"]);
    expect(coaxPlace("")).toEqual({ head: "Без марки", numbers: [], variant: "" });
    expect(coaxPlace("РК50-12-331-С")).toEqual({ head: "РК", numbers: [50, 12, 331], variant: "С" });
  });

  it("sorts without touching the input", () => {
    const input = [row("LMR-195"), row("RG-6")];
    sortCoax(input);
    expect(input[0].cable).toBe("LMR-195");
  });

  it("reads full name and aliases off the card", () => {
    const card = { full_name: "Кабель RG-316", aliases: [{ alias: "M17/113" }, { alias: "" }] } as unknown as CableRow["card"];
    expect(coaxNaming(row("RG316", { card }))).toEqual({ fullName: "Кабель RG-316", aliases: ["M17/113"] });
    expect(coaxNaming(row("RG316"))).toEqual({ fullName: "", aliases: [] });
  });
});
