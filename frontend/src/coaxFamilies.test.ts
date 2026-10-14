import { describe, expect, it } from "vitest";
import type { CableRow } from "./api";
import { coaxGroupName, coaxGroupOf, coaxNaming, compareCoaxMarks, sortCoax } from "./coaxFamilies";

const row = (cable: string, extra: Partial<CableRow> = {}): CableRow => ({
  id: cable, kind: "coax", auto_kind: "coax", reason: "", manual: false, cable, record: null, dia: null, dia_hint: null, ...extra,
});

// The 51 coax marks of the shop's base (14.10.2026), in the order the owner reads a catalogue.
const CATALOGUE = [
  "RG58", "RG-58 A/U", "RG-58 C/U", "RG59", "RG59 B/U", "RG-6", "RG6/U", "RG-141", "RG142", "RG174", "RG174 A/U", "RG178",
  "RG-213", "RG213 C/U", "RG-223", "RG316", "RG316-D", "RG-400", "RG-401", "RG402", "RG405",
  "LMR-100", "LMR-195", "LMR-200", "LMR240", "LMR-400", "LMR-400 LSZH", "LMR400 UF",
  "5D-FB", "5D-FB Cu", "8D-FB", "8D-FB Cu", "10D-FB", "10D-FB PE",
  "RF 0.81", "RF 1.13", "RF 1.37", "RG1.37", "RF 1.47",
  "SR-086-50", "SR-141-50", "Sucoform 141 Cu FEP", "UT-047", "MULTIFLEX 86", "SS405",
  "РК50-1,5-21", "РК50-2-11", "РК50-4-11", "КВСФ-75",
  "1/2 Baltic Signal BSF-50-12 LSZH",
  "MFC-240 UF",
];

describe("coax catalogue order", () => {
  it("orders the shop's 51 marks like a catalogue, whatever order they come in", () => {
    expect(CATALOGUE).toHaveLength(51);
    const shuffled = [...CATALOGUE].sort().reverse();
    expect(shuffled.sort(compareCoaxMarks)).toEqual(CATALOGUE);
  });

  it("groups by series in a fixed order", () => {
    const groups = [...new Set(CATALOGUE.map(coaxGroupName))];
    expect(groups).toEqual(["RG", "LMR", "D-FB", "Тонкие RF", "Полужёсткие и гибкие", "Отечественные", "Фидеры", "Прочие"]);
    expect(coaxGroupName("RG1.37")).toBe("Тонкие RF"); // a micro-coax, not RG-1
    expect(coaxGroupName("")).toBe("Без марки");
  });

  it("reads one mark however it is spelled, base mark before its variants", () => {
    expect(compareCoaxMarks("RG-58", "RG58")).not.toBe(0); // the spelling breaks the tie, the place is the same
    expect(["RG-174 A/U", "rg 174", "RG-58 C/U"].sort(compareCoaxMarks)).toEqual(["RG-58 C/U", "rg 174", "RG-174 A/U"]);
    expect(["LMR400 UF", "LMR-400 LSZH", "LMR-400", "LMR-195"].sort(compareCoaxMarks)).toEqual(["LMR-195", "LMR-400", "LMR-400 LSZH", "LMR400 UF"]);
  });

  it("sorts rows without touching the input and labels each row with its group", () => {
    const input = [row("LMR-195"), row("RG316"), row("RG-6"), row("RG-58 A/U"), row("RG58")];
    expect(sortCoax(input).map((item) => item.cable)).toEqual(["RG58", "RG-58 A/U", "RG-6", "RG316", "LMR-195"]);
    expect(input[0].cable).toBe("LMR-195");
    expect(input.map(coaxGroupOf(input))).toEqual(["LMR", "RG", "RG", "RG", "RG"]);
  });

  it("reads full name and aliases off the card", () => {
    const card = { full_name: "Кабель RG-316", aliases: [{ alias: "M17/113" }, { alias: "" }] } as unknown as CableRow["card"];
    expect(coaxNaming(row("RG316", { card }))).toEqual({ fullName: "Кабель RG-316", aliases: ["M17/113"] });
    expect(coaxNaming(row("RG316"))).toEqual({ fullName: "", aliases: [] });
  });
});
