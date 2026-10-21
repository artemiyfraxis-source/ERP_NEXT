import { describe, expect, it } from "vitest";
import type { CableRow } from "./api";
import { coaxGroupOf, coaxOtherNames, coaxPlace, sortCoax } from "./coaxFamilies";

const row = (cable: string, extra: Partial<CableRow> = {}): CableRow => ({
  id: cable, kind: "coax", auto_kind: "coax", reason: "", manual: false, cable, record: null, dia: null, dia_hint: null, ...extra,
});

// A slice of the shop's coax base (19.10.2026), in the order the owner wants it: a group per mark family.
const MARKS = [
  "D-FB — разные:3D-FB", "D-FB — разные:4D-FB","5D-FB:5D-FB", "5D-FB:5D-FB CCA PE AL PE", "5D-FB:5D FB Cu ГОСТ", "8D-FB:8D-FB", "8D-FB:8D-FB CU LSZH",
  "10D-FB:10D-FB", "10D-FB:10D-FB Titan",
  "LMR-195:LMR-195", "LMR-195:LMR-195 UF", "LMR-400:LMR-400", "LMR-400:LMR-400 UF",
  "RF — разные:RF 0.81", "RF — разные:RF 1.13", "RF 1.37:RF 1.37", "RF 1.37:RF 1.37 FEP",
  "RG — разные:RG-8", "RG — разные:RG-141","RG — разные:RG-213",
  "RG-6:RG-6", "RG-6:RG-6 CCS PE TC PE", "RG-6:RG-6/U", "RG-11:RG-11", "RG-11:RG-11 A/U",
  "RG-58:RG-58", "RG-58:RG58", "RG-58:RG-58 A/U", "RG-58:RG-58 CU PE TC PVC", "RG-142:RG-142", "RG-142:RG-142 B/U",
  "RG-174:RG-174", "RG-174:RG-174 A/U", "RG-316:RG-316", "RG-316:RG-316D",
  "SF-141:SF-141", "SF-141:SF-141 FEP",
  "РК50-2-22:РК50-2-22", "РК50-2-22:РК50-2-22 ГОСТ",
  "Фидеры:1/2″", "Фидеры:1/2 Baltic Signal BSF-50-12 LSZH", "Фидеры:7/8″",
  "Прочие:GNS-360B", "Прочие:TZC500-97",
];
const CATALOGUE = MARKS.map((item) => item.split(":")[1]);
const GROUPS = MARKS.map((item) => item.split(":")[0]);

describe("coax catalogue order (owner 19.10: a group per mark family, not per head letters)", () => {
  it("groups by the mark family, singles by head, whatever order rows come in", () => {
    const rows = [...CATALOGUE].sort().reverse().map((cable) => row(cable));
    const sorted = sortCoax(rows);
    expect(sorted.map((item) => item.cable)).toEqual(CATALOGUE);
    expect(sorted.map(coaxGroupOf(rows))).toEqual(GROUPS);
  });

  it("folds variants and spellings of one mark into one group, labelled by the base mark", () => {
    const rows = [row("RG58"), row("RG-58 C/U TC PE TC PVC"), row("RG-58"), row("RG-58 A/U")];
    expect(new Set(rows.map(coaxGroupOf(rows)))).toEqual(new Set(["RG-58"]));
    const noBase = [row("RG-58 CU PE AL PVC"), row("RG-58 A/U")];
    expect(noBase.map(coaxGroupOf(noBase))).toEqual(["RG-58 A/U", "RG-58 A/U"]); // no base row: the shortest mark
    const dfb = [row("5D FB"), row("5D-FB CCA PE AL PE"), row("8D-FB"), row("8D-FB CU LSZH"), row("10D-FB"), row("10D-FB Titan")];
    expect(dfb.map(coaxGroupOf(dfb))).toEqual(["5D FB", "5D FB", "8D-FB", "8D-FB", "10D-FB", "10D-FB"]);
  });

  it("families of one row gather in «<head> — разные» before the head's families; a lone one goes to «Прочие»", () => {
    const rows = [row("RG-58"), row("RG-58 A/U"), row("RG-8"), row("RG-213"), row("LDF4-50A"), row("RG-6"), row("RG-6/U")];
    expect(rows.map(coaxGroupOf(rows))).toEqual(["RG-58", "RG-58", "RG — разные", "RG — разные", "Прочие", "RG-6", "RG-6"]);
    expect(sortCoax(rows).map((item) => item.cable)).toEqual(["RG-8", "RG-213", "RG-6", "RG-6/U", "RG-58", "RG-58 A/U", "LDF4-50A"]);
  });

  it("orders families by number: RG-6 < RG-11 < RG-58", () => {
    const rows = [row("RG-58"), row("RG-58 A/U"), row("RG-11"), row("RG-11 A/U"), row("RG-6"), row("RG-6/U")];
    const labels = sortCoax(rows).map(coaxGroupOf(rows));
    expect(labels.filter((label, index) => labels.indexOf(label) === index)).toEqual(["RG-6", "RG-11", "RG-58"]);
    expect(coaxPlace("")).toEqual({ head: "Без марки", numbers: [], variant: "" });
    expect(coaxPlace("РК50-12-331-С")).toEqual({ head: "РК", numbers: [50, 12, 331], variant: "С" });
  });

  it("sorts without touching the input", () => {
    const input = [row("LMR-195"), row("RG-6")];
    sortCoax(input);
    expect(input[0].cable).toBe("LMR-195");
  });

  it("reads the other names of the very cable off its card, never repeating the mark or a family's names", () => {
    const card = { name: "RG-316", full_name: "Кабель RG-316 50 Ом", aliases: [
      { alias: "RG 316", kind: "mark" }, { alias: "M17/113", kind: "mark" }, { alias: "" },
      { alias: "Кабель RG-316 50 Ом", kind: "full_name" }, { alias: "1951", kind: "supplier_article", supplier: "ГСМ Репитеры" },
    ] } as unknown as CableRow["card"];
    expect(coaxOtherNames(row("RG316", { card, card_exact: true }))).toEqual(["M17/113", "Кабель RG-316 50 Ом", "арт. 1951 (ГСМ Репитеры)"]);
    expect(coaxOtherNames(row("RG-316 B/U", { card, card_exact: false }))).toEqual([]); // the family's names are another cable's
    expect(coaxOtherNames(row("RG316"))).toEqual([]);
  });
});
