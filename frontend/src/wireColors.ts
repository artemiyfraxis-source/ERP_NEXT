/* The wire colour palette — ONE module for the table dots and the «Цвета» picker (owner 12.10.2026).
   Names are the ones snabcable.ru lists on the hookup-wire pages the datasheet links point to (checked 12.10.2026):
   https://snabcable.ru/car/pvam-pvame-pvamde/pvam/  (all single colours and the two-colour pairs «бело-голубой»…)
   https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/  ·  https://snabcable.ru/mounting/nv-nve/nv/  (+ «жёлто-зелёный»)
   https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/
   «Бирюзовый», «натуральный» and «прозрачный» are not on those pages: they come from the shop's own data (hookup-wires.json /
   БД.ПРОВ) and the usual cable set. Hex values are display swatches, not a standard. The stored cell stays a comma-separated
   list of canonical names. */

/** Single colours in picker order. `stem` is the first half of a two-colour name («красно» + «-чёрный»). `group` is the
 *  popover row. Aliases: RU abbreviations and the IEC 60757 two-letter codes (BK BN RD OG YE GN BU VT GY WH PK TQ GD SR). */
export type ColorGroup = "main" | "dark" | "light" | "other";
export interface WireColor { name: string; hex: string; stem: string; aliases: string[]; group: ColorGroup }
export const PALETTE: WireColor[] = [
  { name: "белый", hex: "#ffffff", stem: "бело", aliases: ["бел", "бе", "w", "wh", "white"], group: "main" },
  { name: "чёрный", hex: "#222222", stem: "чёрно", aliases: ["черный", "чёрн", "черн", "чр", "bk", "black"], group: "main" },
  { name: "красный", hex: "#d93a32", stem: "красно", aliases: ["красн", "кр", "rd", "red"], group: "main" },
  { name: "синий", hex: "#2b5fd9", stem: "сине", aliases: ["син", "сн", "bu", "blue"], group: "main" },
  { name: "голубой", hex: "#4aa8e0", stem: "голубо", aliases: ["голуб", "гол", "light blue"], group: "main" },
  { name: "зелёный", hex: "#2e9e4f", stem: "зелёно", aliases: ["зеленый", "зелен", "зел", "gn", "green"], group: "main" },
  { name: "жёлтый", hex: "#f2c200", stem: "жёлто", aliases: ["желтый", "желт", "жёлт", "жел", "ye", "yellow"], group: "main" },
  { name: "оранжевый", hex: "#f08a1c", stem: "оранжево", aliases: ["оранж", "орнж", "орж", "og", "orange"], group: "main" },
  { name: "коричневый", hex: "#7a4a21", stem: "коричнево", aliases: ["коричн", "корич", "кор", "bn", "brown"], group: "main" },
  { name: "серый", hex: "#8a8f98", stem: "серо", aliases: ["сер", "gy", "gray", "grey"], group: "main" },
  { name: "фиолетовый", hex: "#7b3fa0", stem: "фиолетово", aliases: ["фиолет", "фиол", "vt", "purple", "violet"], group: "main" },
  { name: "розовый", hex: "#f08fb0", stem: "розово", aliases: ["розов", "роз", "pk", "pink"], group: "main" },
  { name: "бирюзовый", hex: "#27b5a6", stem: "бирюзово", aliases: ["бирюз", "бир", "tq", "turquoise"], group: "main" },
  { name: "тёмно-синий", hex: "#1b3a8a", stem: "тёмно-сине", aliases: ["темно-синий", "т-син", "т син", "тсин", "dark blue", "navy"], group: "dark" },
  { name: "тёмно-зелёный", hex: "#1f6b3a", stem: "тёмно-зелёно", aliases: ["темно-зеленый", "т-зел", "т зел", "тзел", "dark green"], group: "dark" },
  { name: "тёмно-серый", hex: "#55595f", stem: "тёмно-серо", aliases: ["темно-серый", "т-сер", "т сер", "dark gray", "dark grey"], group: "dark" },
  { name: "бордовый", hex: "#7a1f2e", stem: "бордово", aliases: ["бордо", "бордов", "burgundy"], group: "dark" },
  { name: "оливковый", hex: "#6b7a2a", stem: "оливково", aliases: ["оливк", "olive"], group: "dark" },
  { name: "светло-зелёный", hex: "#8fd16a", stem: "светло-зелёно", aliases: ["светло-зеленый", "салатовый", "салатный", "св-зел", "св зел", "light green"], group: "light" },
  { name: "светло-серый", hex: "#c3c7cc", stem: "светло-серо", aliases: ["св-сер", "св сер", "light gray", "light grey"], group: "light" },
  { name: "малиновый", hex: "#c2185b", stem: "малиново", aliases: ["малин", "raspberry"], group: "light" },
  { name: "сиреневый", hex: "#b89ad6", stem: "сиренево", aliases: ["лиловый", "сирен", "лилов", "lilac"], group: "light" },
  { name: "бежевый", hex: "#d8c3a0", stem: "бежево", aliases: ["беж", "beige"], group: "light" },
  { name: "золотистый", hex: "#c9a227", stem: "золотисто", aliases: ["золот", "золото", "gd", "gold"], group: "other" },
  { name: "серебристый", hex: "#c0c4c9", stem: "серебристо", aliases: ["серебр", "серебро", "sr", "silver"], group: "other" },
  { name: "натуральный", hex: "#e6d9b8", stem: "натурально", aliases: ["натур", "бесцветный", "natural"], group: "other" },
  { name: "прозрачный", hex: "#e8f1f5", stem: "прозрачно", aliases: ["прозр", "transparent", "clear"], group: "other" },
];

/** Popover rows (owner 15.10: «думаешь, мне хватит этих цветов?»). */
export const COLOR_GROUPS: Array<{ id: ColorGroup; title: string }> = [
  { id: "main", title: "Основные" }, { id: "dark", title: "Тёмные" }, { id: "light", title: "Светлые" }, { id: "other", title: "Металл, натуральный, прозрачный" },
];

/** The colours a technologist made up in the generator («болотный» #6b7a3a): name → hex, kept on the card (`GenState.colorHex`) and
 *  mirrored here so every painter (scheme row, drawing, ETK) finds the hex by the name. Not in the palette, never guessed from text. */
let custom: Record<string, string> = {};
const HEX = /^#[0-9a-f]{6}$/i;
export function setCustomColors(map: Record<string, string> | null | undefined): void {
  custom = {};
  for (const [name, hex] of Object.entries(map ?? {})) if (name.trim() && HEX.test(hex)) custom[name.trim().toLowerCase()] = hex.toLowerCase();
}
export const customHex = (name: string): string | null => custom[String(name ?? "").trim().toLowerCase()] ?? null;

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

/** «бело-синий», «тёмно-сине-белый», «белый-светло-зелёный» → [main, stripe]. A compound single colour («тёмно-синий») is tried
 *  first by the callers, so only a true pair gets here; the first half is a stem (or a full name), the rest a whole colour. */
export function splitPair(text: string): [WireColor, WireColor] | null {
  const key = fold(text);
  const heads = [...STEM.entries()].filter(([stem]) => stem.length > 1 && /[а-я]/.test(stem)).sort((a, b) => b[0].length - a[0].length);
  for (const [stem, first] of heads) {
    const rest = key.startsWith(`${stem}-`) ? key.slice(stem.length + 1) : key.startsWith(`${stem} `) ? key.slice(stem.length + 1) : null;
    const second = rest ? SINGLE.get(rest.trim()) : undefined;
    if (second && first !== second) return [first, second];
  }
  return null;
}

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
  const compound = splitPair(key);
  if (compound) return pair(compound[0], compound[1]);
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

/** CSS background of a colour name, or null when the name is neither in the palette nor a custom colour (shown as text). */
export function colorBackground(text: string): string | null {
  const name = canonicalColor(text);
  if (!name) return customHex(text);
  const single = colorOf(name);
  if (single) return single.hex;
  const both = splitPair(name);
  return both ? `linear-gradient(135deg, ${both[0].hex} 50%, ${both[1].hex} 50%)` : null;
}
