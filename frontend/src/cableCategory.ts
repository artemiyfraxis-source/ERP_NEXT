/* «Кабели» (multi-core and other cables) grouped for the shop (owner 12.10.2026): a category from a deterministic
   classifier over the name and type, a manual override in the record's cell «Категория», and inside a category a
   sub-split by «Экран». Rules are read top to bottom, the first match wins. */

export const CATEGORIES = [
  "Витая пара / Ethernet", "Шлейфы плоские", "Коммуникационные многожильные", "Контрольные / управления",
  "Силовые", "USB / интерфейсные", "Коаксиальные / комбинированные", "Прочие",
] as const;
export type CableCategory = (typeof CATEGORIES)[number];
export const CATEGORY_CELL = "Категория";
export const OTHER_CATEGORY: CableCategory = "Прочие";

const RULES: Array<[CableCategory, RegExp]> = [
  ["USB / интерфейсные", /usb|rs-?\s?(485|232|422)|\bcan\b|hdmi|profibus|интерфейс/i],
  ["Витая пара / Ethernet", /cat\.?\s?[5-7]|utp|ftp|\bstp\b|\blan\b|ethernet|парлан|parlan|витая/i],
  ["Шлейфы плоские", /шлейф|ribbon|\bffc\b|ds\s?1057|шаг\s?1[.,]27|flat\s?cable/i],
  ["Коаксиальные / комбинированные", /\bsdi\b|\brgb\b|коакс|\brg-?\s?\d|\blmr-?\s?\d/i],
  ["Коммуникационные многожильные", /ul\s?2464|\bcccu?\b/i],
  ["Контрольные / управления", /liycy|liy\b|unitronic|кдвв|квв|кввг|paar|flexicore|контрол|управлен/i],
  ["Силовые", /ввг|h0[57]\s?(rn|vv)|\bnym\b|nyy|силов|шввп|\bпвс\b|нг\s?\(a\)/i],
];

export const isCategory = (value: string): value is CableCategory => (CATEGORIES as readonly string[]).includes(value);

/** The category of a cable: the manual cell «Категория» when it names one, else the first rule that matches the mark/type. */
export function cableCategory(fields: Record<string, string>): { category: CableCategory; manual: boolean } {
  const manual = String(fields[CATEGORY_CELL] ?? "").trim();
  if (isCategory(manual)) return { category: manual, manual: true };
  const text = `${fields["Марка"] ?? ""} ${fields["Тип провода"] ?? ""}`;
  for (const [category, pattern] of RULES) if (pattern.test(text)) return { category, manual: false };
  return { category: OTHER_CATEGORY, manual: false };
}

export const SCREEN_SPLIT = ["экранированный", "без экрана", "экран не указан"] as const;
export type ScreenSplit = (typeof SCREEN_SPLIT)[number];
const NO_SCREEN = new Set(["", "нет", "без экрана", "no", "none"]);
const UNCLEAR = new Set(["—", "-", "?", "н/д", "не указан", "не указано", "не найдено", "нет данных", "⚠ нет данных"]);

/** From «Экран»: empty / «нет» / «без экрана» — no screen; unclear marks — not stated; anything else — screened. */
export function screenSplit(screen: string): ScreenSplit {
  const key = String(screen ?? "").trim().toLowerCase();
  if (NO_SCREEN.has(key)) return "без экрана";
  if (UNCLEAR.has(key)) return "экран не указан";
  return "экранированный";
}

/** «LiYCY 4x0,5» → «LiYCY»: the name up to the first word that starts with a digit (the size). */
export function cableSeries(mark: string): string {
  const words = String(mark ?? "").trim().split(/\s+/);
  const cut = words.findIndex((word, index) => index > 0 && /^\d/.test(word));
  return (cut > 0 ? words.slice(0, cut) : words).join(" ");
}

const numberOf = (text: string): number => {
  const match = String(text ?? "").replace(",", ".").match(/\d+(\.\d+)?/);
  return match ? Number(match[0]) : Number.POSITIVE_INFINITY;
};

export interface CableGroupKey { category: CableCategory; split: ScreenSplit; label: string }

export function cableGroup(fields: Record<string, string>): CableGroupKey {
  const { category } = cableCategory(fields);
  const split = screenSplit(fields["Экран"] ?? "");
  return { category, split, label: `${category} · ${split}` };
}

/** Natural order: category, screen split, series, cores as a number, section as a number, then the mark. */
export function compareCables(a: Record<string, string>, b: Record<string, string>): number {
  const [ga, gb] = [cableGroup(a), cableGroup(b)];
  return CATEGORIES.indexOf(ga.category) - CATEGORIES.indexOf(gb.category)
    || SCREEN_SPLIT.indexOf(ga.split) - SCREEN_SPLIT.indexOf(gb.split)
    || cableSeries(a["Марка"] ?? "").localeCompare(cableSeries(b["Марка"] ?? ""), "ru", { numeric: true, sensitivity: "base" })
    || numberOf(a["Число жил"] ?? "") - numberOf(b["Число жил"] ?? "")
    || numberOf(a["Сечение, мм2"] ?? "") - numberOf(b["Сечение, мм2"] ?? "")
    || (a["Марка"] ?? "").localeCompare(b["Марка"] ?? "", "ru", { numeric: true });
}
