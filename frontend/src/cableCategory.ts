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

/* «Многожильные кабели» by mark family (owner 15.10.2026: «нормальная сортировка по маркам — ПВВС, КДЭВГ, CAT5e, CAT6 и
   т.д.»): the family comes from the mark (the type and makers only name a twisted pair's category), the record's cell
   «Семейство» wins when filled. Inside a family — cores, then section as numbers. The screen stays a column. */
export const FAMILY_CELL = "Семейство";
const TWISTED_PAIR = "Витая пара";
// Russian power cords and control cables first, then the imported control cables, the twisted pair, the rest; the
// order of this list is the order on the screen.
const FAMILY_RULES: Array<[string, RegExp]> = [
  ["ПВС", /^пвс(?![а-яё])/i], ["ПВВС", /^пввс/i], ["ШВВП", /^шввп/i], ["ШВПМ", /^швпм/i], ["H05RN-F", /^h0[57]\s?rn-?f/i],
  // Каждая марка — своя группа (владелец 15.10: «ты смешал два в один, так нельзя»): КДВВГ ≠ КДВЭВГ, КСПВ ≠ КСПВГ.
  ["КДВВГ", /^кдввг/i], ["КДВЭВГ", /^кдвэвг/i], ["КДЭВГ", /^кдэвг/i],
  ["КСПВ", /^кспв(?![а-яё])/i], ["КСПВГ", /^кспвг(?![а-яё])/i], ["КСПЭВ", /^кспэв(?![а-яё])/i], ["КСПЭВГ", /^кспэвг(?![а-яё])/i],
  ["КСВВ", /^ксвв/i], ["МКШ", /^мкш/i], ["РВВП", /^(рввп|rvvp)/i], ["ШТЛЭн", /^штлэн/i], ["ШТСПЭн", /^штспэн/i],
  ["LiYCY", /liycy/i], ["FLEXICORE", /flexicore/i], ["PAAR-TRONIC-CY", /paar-?tronic/i],
  ["UL2464 CCC", /^ccc-/i], ["UL2464 CCCU", /^cccu-/i], ["UL2464", /ul\s?2464/i], ["UL2725", /ul\s?2725/i],
  [TWISTED_PAIR, /^$/], // the slot of the twisted pair families, computed below
  ["Шлейфы плоские", /шлейф|ribbon|ds\s?1057/i], ["PROFIBUS", /profibus|6xv1830/i], ["USB", /\busb\b|tsk1160/i],
  ["Мультикор SDI / RGB", /мультикор|multicore|\bsdi\b/i],
];
const FAMILY_RANK = new Map(FAMILY_RULES.map(([name], index) => [name, index]));
export const PAIR_CATEGORIES = ["Cat5", "Cat5e", "Cat6", "Cat6A", "Cat7", "Cat7A", "Cat8"] as const;
export const PAIR_SCREENS = ["U/UTP", "F/UTP", "S/UTP", "SF/UTP", "U/FTP", "S/FTP"] as const;
const IS_PAIR = /cat\.?\s?\d|(?:^|[\s/-])[usf]{0,2}\/?[uf]tp\b|\bsstp\b|витая\s+пара|parlan|\blan\b/i;

function pairCategory(text: string): string {
  const match = text.match(/cat(?:egory)?\.?\s?(5e|5|6a|6|7a|7|8)(?![0-9])/i) ?? text.match(/категори[яи]\s?(5e|5|6a|6|7a|7)/i);
  return match ? `Cat${match[1].toUpperCase().replace("E", "e")}` : "";
}

function pairScreen(mark: string, screen: string): string {
  if (/s\/ftp|\bsstp|pimf/i.test(mark)) return "S/FTP";
  if (/sf\/utp|\bsftp/i.test(mark)) return "SF/UTP";
  if (/u\/ftp/i.test(mark)) return "U/FTP";
  if (/f\/utp|\bftp|-f\/u-/i.test(mark)) return "F/UTP";
  if (/u\/utp|\butp/i.test(mark)) return "U/UTP";
  const text = screen.toLowerCase();
  const foil = /фольг|f\/utp|ftp/.test(text);
  const braid = /оплёт|оплет|braid/.test(text);
  if (foil && braid) return "SF/UTP";
  if (braid) return "S/UTP";
  if (foil) return "F/UTP";
  return screenSplit(screen) === "экранированный" ? "экранированная" : "U/UTP";
}

export interface CableFamily { family: string; manual: boolean; rank: number }

/** The mark family of a multi-core cable: the cell «Семейство», else the first rule over the mark, a twisted pair as
 *  «<экран> <категория>» («F/UTP Cat5e»), else the mark without its size («МКШ 2x0,75» → «МКШ»). */
export function cableFamily(fields: Record<string, string>): CableFamily {
  const manual = String(fields[FAMILY_CELL] ?? "").trim();
  if (manual) return { family: manual, manual: true, rank: FAMILY_RANK.get(manual) ?? FAMILY_RULES.length };
  const mark = String(fields["Марка"] ?? "").trim();
  const type = String(fields["Тип провода"] ?? "");
  for (const [name, pattern] of FAMILY_RULES) {
    if (name !== TWISTED_PAIR && pattern.test(name === "USB" ? `${mark} ${type}` : mark)) {
      return { family: name, manual: false, rank: FAMILY_RANK.get(name) ?? 0 };
    }
  }
  const more = `${type} ${fields["Производители"] ?? ""}`;
  if (IS_PAIR.test(mark) || /витая\s+пара/i.test(type)) {
    const category = pairCategory(mark) || pairCategory(more);
    const screen = pairScreen(mark, fields["Экран"] ?? "");
    const catRank = category ? (PAIR_CATEGORIES as readonly string[]).indexOf(category) : PAIR_CATEGORIES.length;
    const screenRank = Math.max(0, (PAIR_SCREENS as readonly string[]).indexOf(screen));
    return {
      family: `${screen} ${category || "(категория не указана)"}`, manual: false,
      rank: (FAMILY_RANK.get(TWISTED_PAIR) ?? 0) + (catRank * 10 + screenRank) / 1000,
    };
  }
  return { family: cableSeries(mark).replace(/-\d[\w-]*$/, "") || mark, manual: false, rank: FAMILY_RULES.length };
}

/** Only the group order of two cables (family by its rank, an unknown one by name): rows inside a group are ordered by the caller. */
export function compareFamilyGroups(a: Record<string, string>, b: Record<string, string>): number {
  const [fa, fb] = [cableFamily(a), cableFamily(b)];
  return fa.rank - fb.rank || fa.family.localeCompare(fb.family, "ru", { numeric: true, sensitivity: "base" });
}

/** «Многожильные»: family by its rank (an unknown family — after the known ones, by name), then cores, then section. */
export function compareByFamily(a: Record<string, string>, b: Record<string, string>): number {
  const [fa, fb] = [cableFamily(a), cableFamily(b)];
  return fa.rank - fb.rank
    || fa.family.localeCompare(fb.family, "ru", { numeric: true, sensitivity: "base" })
    || numberOf(a["Число жил"] ?? "") - numberOf(b["Число жил"] ?? "")
    || numberOf(a["Сечение, мм2"] ?? "") - numberOf(b["Сечение, мм2"] ?? "")
    || (a["Марка"] ?? "").localeCompare(b["Марка"] ?? "", "ru", { numeric: true });
}
