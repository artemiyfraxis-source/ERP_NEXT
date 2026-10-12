export type WireSize =
  | { readonly system: "awg"; readonly value: 30 | 28 | 26 | 24 }
  | { readonly system: "mm2"; readonly value: 0.05 | 0.08 | 0.12 | 0.2 | 0.35 };

export interface BuiltInWireReference {
  readonly id: string;
  readonly series: "UL1061" | "НВ-4";
  readonly designation: string;
  readonly size: WireSize;
}

export interface WireColorReference {
  readonly id: string;
  readonly name: string;
  readonly hex: string;
  readonly kind: "built-in" | "custom";
}

const ul1061AwgSizes = [30, 28, 26, 24] as const;
const nv4SectionSizes = [0.05, 0.08, 0.12, 0.2, 0.35] as const;

export const builtInWireReferences: readonly BuiltInWireReference[] = [
  ...ul1061AwgSizes.map((value): BuiltInWireReference => ({
    id: `wire:ul1061:${value}-awg`,
    series: "UL1061",
    designation: `UL1061 ${value}AWG`,
    size: { system: "awg", value },
  })),
  ...nv4SectionSizes.map((value): BuiltInWireReference => ({
    id: `wire:nv-4:${String(value).replace(".", "-")}-mm2`,
    series: "НВ-4",
    designation: `НВ-4 ${formatRussianDecimal(value)} мм²`,
    size: { system: "mm2", value },
  })),
];

export const builtInWireColors: readonly WireColorReference[] = [
  builtInColor("red", "красный", "#D32F2F"),
  builtInColor("black", "черный", "#202124"),
  builtInColor("white", "белый", "#FFFFFF"),
  builtInColor("orange", "оранжевый", "#F57C00"),
  builtInColor("green", "зеленый", "#388E3C"),
  builtInColor("violet", "фиолетовый", "#7B1FA2"),
  builtInColor("yellow", "желтый", "#FBC02D"),
  builtInColor("light-blue", "голубой", "#29B6F6"),
  builtInColor("blue", "синий", "#1976D2"),
  builtInColor("brown", "коричневый", "#795548"),
  builtInColor("gray", "серый", "#757575"),
  builtInColor("pink", "розовый", "#EC407A"),
  builtInColor("turquoise", "бирюзовый", "#00A6A6"),
];

/** Returns local wire hints ordered as in the catalog. Empty input exposes all items. */
export function filterWireSuggestions(
  query: string,
  catalog: readonly BuiltInWireReference[] = builtInWireReferences,
  limit = catalog.length,
): readonly BuiltInWireReference[] {
  if (!Number.isSafeInteger(limit) || limit < 0) throw new Error("Лимит подсказок должен быть целым неотрицательным числом.");
  const tokens = searchTokens(query);
  return catalog
    .filter((wire) => tokens.every((token) => searchableWireText(wire).includes(token)))
    .slice(0, limit);
}

/** Color choices for both primary and optional secondary color selectors. */
export function wireColorChoices(
  customColors: readonly WireColorReference[] = [],
): readonly WireColorReference[] {
  const result = [...builtInWireColors, ...customColors.map(normalizeCustomColor)];
  const ids = new Set<string>();
  for (const color of result) {
    if (ids.has(color.id)) throw new Error(`Цвет с ID ${color.id} уже есть в справочнике.`);
    ids.add(color.id);
  }
  return result;
}

/** The leading null maps to an empty value: no secondary insulation color. */
export function secondaryWireColorChoices(
  customColors: readonly WireColorReference[] = [],
): readonly (WireColorReference | null)[] {
  return [null, ...wireColorChoices(customColors)];
}

/** Resolves a stored reference name or a custom #RRGGBB value for canvas rendering. */
export function resolveWireColorHex(
  value: string,
  choices: readonly WireColorReference[] = builtInWireColors,
  fallback = "#334155",
): string {
  const normalized = value.trim();
  if (/^#[0-9a-f]{6}$/i.test(normalized)) return normalized.toUpperCase();
  return choices.find((color) => normalizeSearchText(color.name) === normalizeSearchText(normalized))?.hex ?? fallback;
}

/** Creates a palette color that can be supplied to wireColorChoices. */
export function createCustomWireColor(hex: string, name: string = hex): WireColorReference {
  const normalizedHex = normalizeHex(hex);
  const normalizedName = name.trim();
  if (!normalizedName || normalizedName.length > 80) throw new Error("Название пользовательского цвета задано неверно.");
  return { id: `custom:${normalizedHex.slice(1).toLowerCase()}`, name: normalizedName, hex: normalizedHex, kind: "custom" };
}

function builtInColor(id: string, name: string, hex: string): WireColorReference {
  return { id: `color:${id}`, name, hex, kind: "built-in" };
}

function normalizeCustomColor(color: WireColorReference): WireColorReference {
  if (color.kind !== "custom") throw new Error("Дополнительный цвет должен быть пользовательским.");
  const normalized = createCustomWireColor(color.hex, color.name);
  return { ...normalized, id: color.id.trim() || normalized.id };
}

function searchableWireText(wire: BuiltInWireReference): string {
  const sizeAliases = wire.size.system === "awg"
    ? [`${wire.size.value}awg`, `${wire.size.value} awg`]
    : [String(wire.size.value), formatRussianDecimal(wire.size.value), `${formatRussianDecimal(wire.size.value)}мм2`];
  return normalizeSearchText([wire.series, wire.designation, ...sizeAliases].join(" "));
}

function searchTokens(query: string): readonly string[] {
  return normalizeSearchText(query).split(" ").filter(Boolean);
}

function normalizeSearchText(value: string): string {
  return value.toLocaleLowerCase("ru-RU")
    .replace(/ё/g, "е")
    .replace(/,/g, ".")
    .replace(/мм[²2]/g, "мм2")
    .replace(/[^a-zа-я0-9.]+/giu, " ")
    .trim();
}

function formatRussianDecimal(value: number): string {
  return String(value).replace(".", ",");
}

function normalizeHex(value: string): string {
  const normalized = value.trim().toUpperCase();
  if (!/^#[0-9A-F]{6}$/.test(normalized)) throw new Error("Цвет палитры должен быть задан в формате #RRGGBB.");
  return normalized;
}
