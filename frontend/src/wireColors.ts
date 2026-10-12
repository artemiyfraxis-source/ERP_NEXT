/* The wire colour palette — ONE module for the table dots and the «Цвета» picker (owner 12.10.2026).
   Names are the ones snabcable.ru lists on the hookup-wire pages the datasheet links point to (checked 12.10.2026):
   https://snabcable.ru/car/pvam-pvame-pvamde/pvam/  (all single colours and the two-colour pairs «бело-голубой»…)
   https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/  ·  https://snabcable.ru/mounting/nv-nve/nv/  (+ «жёлто-зелёный»)
   https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/
   «Бирюзовый», «натуральный» and «прозрачный» are not on those pages: they come from the shop's own data (hookup-wires.json /
   БД.ПРОВ) and the usual cable set. Hex values are display swatches, not a standard. The stored cell stays a comma-separated
   list of canonical names. */

export interface WireColor { name: string; hex: string; stem: string; aliases: string[] }

/** Single colours in picker order. `stem` is the first half of a two-colour name («красно» + «-чёрный»). */
export const PALETTE: WireColor[] = [
  { name: "белый", hex: "#ffffff", stem: "бело", aliases: ["бел", "бе", "w", "white"] },
  { name: "чёрный", hex: "#222222", stem: "чёрно", aliases: ["черный", "чёрн", "черн", "чр", "bk", "black"] },
  { name: "красный", hex: "#d93a32", stem: "красно", aliases: ["красн", "кр", "red"] },
  { name: "синий", hex: "#2b5fd9", stem: "сине", aliases: ["син", "сн", "blue"] },
  { name: "голубой", hex: "#4aa8e0", stem: "голубо", aliases: ["голуб", "гол", "light blue"] },
  { name: "зелёный", hex: "#2e9e4f", stem: "зелёно", aliases: ["зеленый", "зелен", "зел", "green"] },
  { name: "жёлтый", hex: "#f2c200", stem: "жёлто", aliases: ["желтый", "желт", "жёлт", "жел", "yellow"] },
  { name: "оранжевый", hex: "#f08a1c", stem: "оранжево", aliases: ["оранж", "орнж", "орж", "orange"] },
  { name: "коричневый", hex: "#7a4a21", stem: "коричнево", aliases: ["коричн", "корич", "кор", "brown"] },
  { name: "серый", hex: "#8a8f98", stem: "серо", aliases: ["сер", "gray", "grey"] },
  { name: "фиолетовый", hex: "#7b3fa0", stem: "фиолетово", aliases: ["фиолет", "фиол", "purple", "violet"] },
  { name: "розовый", hex: "#f08fb0", stem: "розово", aliases: ["розов", "роз", "pink"] },
  { name: "бирюзовый", hex: "#27b5a6", stem: "бирюзово", aliases: ["бирюз", "бир", "turquoise"] },
  { name: "натуральный", hex: "#e6d9b8", stem: "натурально", aliases: ["натур", "бесцветный", "natural"] },
  { name: "прозрачный", hex: "#e8f1f5", stem: "прозрачно", aliases: ["прозр", "transparent", "clear"] },
];

/** The two-colour name «жёлто-зелёный» is a palette item of its own: the PE earth wire. */
export const EARTH = "жёлто-зелёный";
export const SWATCHES: Array<{ name: string; hex: string; hex2?: string }> = [
  ...PALETTE.map(({ name, hex }) => ({ name, hex })),
  { name: EARTH, hex: "#f2c200", hex2: "#2e9e4f" },
];

const fold = (text: string): string => text.toLowerCase().replace(/ё/g, "е").replace(/[.\s]+/g, " ").trim();

const SINGLE = new Map<string, WireColor>();
const STEM = new Map<string, WireColor>();
for (const color of PALETTE) {
  for (const key of [color.name, ...color.aliases]) SINGLE.set(fold(key), color);
  STEM.set(fold(color.stem), color);
  for (const key of [color.name, ...color.aliases]) STEM.set(fold(key), color);
}
// One-letter forms exist only inside a pair («ж/з», «к-ч»): alone they are too ambiguous to guess.
const LETTER: Record<string, string> = { б: "белый", ч: "чёрный", к: "красный", с: "синий", г: "голубой", з: "зелёный", ж: "жёлтый", о: "оранжевый", р: "розовый", ф: "фиолетовый" };
for (const [letter, name] of Object.entries(LETTER)) STEM.set(letter, PALETTE.find((color) => color.name === name)!);

const pair = (first: WireColor, second: WireColor): string => `${first.stem}-${second.name}`;

const colorOf = (name: string): WireColor | undefined => PALETTE.find((color) => color.name === name);

/** The canonical Russian name of a typed colour («желто-зеленый», «ж/з», «чёрн.» → «жёлто-зелёный», «жёлто-зелёный», «чёрный»),
 *  or null when it is not a colour of the palette (a free-text colour stays as typed). */
export function canonicalColor(text: string): string | null {
  const key = fold(text);
  if (!key) return null;
  const single = SINGLE.get(key);
  if (single) return single.name;
  const parts = key.split(/\s*[-/]\s*|\s+/).filter(Boolean);
  if (parts.length === 2) {
    const [first, second] = [STEM.get(parts[0]), SINGLE.get(parts[1]) ?? STEM.get(parts[1])];
    if (first && second && first !== second) return pair(first, second);
  }
  return null;
}

/** «белый, желто-зеленый; кр» → ["белый", "жёлто-зелёный", "красный"]: names split on «,» / «;», aliases made canonical,
 *  a colour outside the palette kept as typed, repeats dropped. */
export function parseColors(text: string): string[] {
  const seen = new Set<string>();
  const names: string[] = [];
  for (const piece of String(text ?? "").split(/[,;]/)) {
    const raw = piece.trim();
    if (!raw) continue;
    const name = canonicalColor(raw) ?? raw;
    if (!seen.has(name.toLowerCase())) {
      seen.add(name.toLowerCase());
      names.push(name);
    }
  }
  return names;
}

/** The stored format: canonical names joined by «, » (what the cell held before). */
export const joinColors = (names: string[]): string => names.join(", ");
export const normalizeColors = (text: string): string => joinColors(parseColors(text));

/** CSS background of a colour name, or null when the name is not in the palette (shown as text). */
export function colorBackground(text: string): string | null {
  const name = canonicalColor(text);
  if (!name) return null;
  const single = colorOf(name);
  if (single) return single.hex;
  const [head, tail] = name.split("-");
  const first = PALETTE.find((color) => color.stem === head);
  const second = colorOf(tail);
  return first && second ? `linear-gradient(135deg, ${first.hex} 50%, ${second.hex} 50%)` : null;
}
