import { expect, it } from "vitest";
import { COLOR_GROUPS, PALETTE, canonicalColor, colorBackground, normalizeColors, parseColors, setCustomColors } from "./wireColors";
import { coreColor } from "./pinoutCheck";

/* Owner 15.10: «думаешь, мне хватит этих цветов?» — the palette grew to the colours wire and cable makers list (RU catalogues,
   IEC 60757 codes, DIN 47100 base); compound single colours stay ONE colour, a true pair still splits into main + stripe. */

it("every palette colour is in a popover row and keeps a unique name and stem", () => {
  expect(new Set(PALETTE.map((c) => c.name)).size).toBe(PALETTE.length);
  expect(new Set(PALETTE.map((c) => c.stem)).size).toBe(PALETTE.length);
  const groups = new Set(COLOR_GROUPS.map((g) => g.id));
  expect(PALETTE.every((c) => groups.has(c.group) && /^#[0-9a-f]{6}$/.test(c.hex))).toBe(true);
  expect(PALETTE.length).toBeGreaterThanOrEqual(27);
});

it.each([
  ["тёмно-синий", "тёмно-синий"], ["темно-синий", "тёмно-синий"], ["тёмно-зелёный", "тёмно-зелёный"], ["светло-зеленый", "светло-зелёный"],
  ["салатовый", "светло-зелёный"], ["лиловый", "сиреневый"], ["бордо", "бордовый"], ["оливковый", "оливковый"], ["малиновый", "малиновый"],
  ["бежевый", "бежевый"], ["светло-серый", "светло-серый"], ["тёмно-серый", "тёмно-серый"], ["золотистый", "золотистый"], ["серебристый", "серебристый"],
  ["BK", "чёрный"], ["BN", "коричневый"], ["RD", "красный"], ["OG", "оранжевый"], ["YE", "жёлтый"], ["GN", "зелёный"], ["BU", "синий"],
  ["VT", "фиолетовый"], ["GY", "серый"], ["WH", "белый"], ["PK", "розовый"], ["TQ", "бирюзовый"], ["GD", "золотистый"], ["SR", "серебристый"],
])("%s is the palette colour %s (one colour, not a pair)", (typed, canonical) => {
  expect(canonicalColor(typed)).toBe(canonical);
  expect(colorBackground(typed)).toBe(PALETTE.find((c) => c.name === canonical)!.hex);
});

it("a true two-colour name still splits into main + stripe, with compound halves too", () => {
  expect(canonicalColor("бело-синий")).toBe("бело-синий");
  expect(colorBackground("бело-синий")).toBe("linear-gradient(135deg, #ffffff 50%, #2b5fd9 50%)");
  expect(coreColor("бело-синий")).toMatchObject({ baseName: "белый", hex: "#ffffff", stripe: "#2b5fd9" });
  expect(canonicalColor("жёлто-зелёный")).toBe("жёлто-зелёный");
  expect(canonicalColor("тёмно-сине-белый")).toBe("тёмно-сине-белый");
  expect(coreColor("тёмно-сине-белый")).toMatchObject({ baseName: "тёмно-синий", hex: "#1b3a8a", stripe: "#ffffff" });
  expect(canonicalColor("белый-тёмно-зелёный")).toBe("бело-тёмно-зелёный");
  expect(coreColor("бело-тёмно-зелёный")).toMatchObject({ baseName: "белый", stripe: "#1f6b3a" });
  expect(canonicalColor("светло-синий")).toBeNull(); // not a palette colour: stays text
});

it("existing aliases and БД.ПРОВ cells parse as before", () => {
  expect(parseColors("чёрный; желто-зеленый, КР, белый, коричнево-серый, красно-черный")).toEqual(["чёрный", "жёлто-зелёный", "красный", "белый", "коричнево-серый", "красно-чёрный"]);
  expect(normalizeColors("тёмно-синий, светло-зелёный, бело-синий, салатовый")).toBe("тёмно-синий, светло-зелёный, бело-синий");
  expect(canonicalColor("к")).toBeNull();
});

it("a custom colour is painted by name once its hex is known; without a hex it stays text", () => {
  expect(colorBackground("болотный")).toBeNull();
  expect(coreColor("болотный")).toBeNull();
  setCustomColors({ болотный: "#6b7a3a" });
  expect(colorBackground("Болотный")).toBe("#6b7a3a");
  expect(coreColor("болотный")).toMatchObject({ name: "болотный", hex: "#6b7a3a", stripe: null });
  setCustomColors({ плохой: "red" });
  expect(colorBackground("плохой")).toBeNull();
  setCustomColors(null);
});
