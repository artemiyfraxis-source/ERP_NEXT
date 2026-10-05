// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  builtInWireColors,
  builtInWireReferences,
  createCustomWireColor,
  filterWireSuggestions,
  resolveWireColorHex,
  secondaryWireColorChoices,
  wireColorChoices,
} from "./wire-reference-catalog";

describe("wire reference catalog", () => {
  it("contains the required UL1061 and НВ-4 sizes", () => {
    expect(builtInWireReferences.map((wire) => wire.designation)).toEqual([
      "UL1061 30AWG", "UL1061 28AWG", "UL1061 26AWG", "UL1061 24AWG",
      "НВ-4 0,05 мм²", "НВ-4 0,08 мм²", "НВ-4 0,12 мм²", "НВ-4 0,2 мм²", "НВ-4 0,35 мм²",
    ]);
    expect(builtInWireReferences.map((wire) => wire.size)).toEqual([
      { system: "awg", value: 30 }, { system: "awg", value: 28 },
      { system: "awg", value: 26 }, { system: "awg", value: 24 },
      { system: "mm2", value: 0.05 }, { system: "mm2", value: 0.08 },
      { system: "mm2", value: 0.12 }, { system: "mm2", value: 0.2 },
      { system: "mm2", value: 0.35 },
    ]);
  });

  it("filters wire hints by series and localized size while preserving catalog order", () => {
    expect(filterWireSuggestions("ul 28").map((wire) => wire.designation)).toEqual(["UL1061 28AWG"]);
    expect(filterWireSuggestions("нв 0.12").map((wire) => wire.designation)).toEqual(["НВ-4 0,12 мм²"]);
    expect(filterWireSuggestions("0,0", builtInWireReferences, 2).map((wire) => wire.designation)).toEqual([
      "НВ-4 0,05 мм²", "НВ-4 0,08 мм²",
    ]);
    expect(filterWireSuggestions("несуществующий")).toEqual([]);
    expect(() => filterWireSuggestions("", builtInWireReferences, -1)).toThrow(/Лимит/);
  });

  it("provides classic colors and accepts a user palette color", () => {
    expect(builtInWireColors.map((color) => color.name)).toEqual([
      "красный", "черный", "белый", "оранжевый", "зеленый", "фиолетовый", "желтый",
      "голубой", "синий", "коричневый", "серый", "розовый", "бирюзовый",
    ]);
    const custom = createCustomWireColor(" #12abef ", "Фирменный голубой");
    expect(custom).toEqual({
      id: "custom:12abef", name: "Фирменный голубой", hex: "#12ABEF", kind: "custom",
    });
    expect(wireColorChoices([custom]).at(-1)).toEqual(custom);
    expect(resolveWireColorHex("черный")).toBe("#202124");
    expect(resolveWireColorHex(" #12abef ")).toBe("#12ABEF");
    expect(secondaryWireColorChoices([custom])).toEqual([null, ...builtInWireColors, custom]);
    expect(() => createCustomWireColor("blue")).toThrow(/#RRGGBB/);
  });
});
