import type { CableRow } from "./api";

/* «Коаксиальные кабели» read like a catalogue (owner 14.10.2026: «сортировку улучшить, сейчас хлам»): groups in a fixed
   order — RG, LMR, D-FB, thin RF, semi-rigid and flexible, Russian marks, feeders, the rest — and inside a group by size,
   the base mark before its variants (A/U, C/U, Cu, LSZH, UF…). Spellings are normalised only for this order
   (RG58 = RG-58); the marks themselves stay as written. A column header sort still overrides it. */

export const NO_FAMILY = "Без марки";

const GROUPS = ["RG", "LMR", "D-FB", "Тонкие RF", "Полужёсткие и гибкие", "Отечественные", "Фидеры", "Прочие", NO_FAMILY] as const;
export type CoaxGroup = (typeof GROUPS)[number];

/** RG by number, but the everyday RG-58 / RG-59 / RG-6 first, as a catalogue lists them. */
const RG_FIRST = [58, 59, 6];
// Semi-rigid and flexible series, in the order the shop reads them.
const SEMI = [/^SR[-\s]?(\d+)/i, /^SUCOFORM\s*(\d+)?/i, /^UT[-\s]?(\d+)/i, /^MULTIFLEX\s*(\d+)?/i, /^SS[-\s]?(\d+)/i];

type Place = { group: number; series: number; size: number; size2: number; variant: string };

const decimal = (text: string) => Number(text.replace(",", "."));
const variantOf = (rest: string) => rest.replace(/^[-\s/]+/, "").trim();

/** Where a mark stands in the catalogue: group, series inside it, size, and the variant suffix ("" — the base mark). */
export function coaxPlace(mark: string): Place {
  const name = String(mark ?? "").trim().replace(/\s+/g, " ");
  const place = (group: CoaxGroup, size = 0, rest = "", series = 0, size2 = 0): Place =>
    ({ group: GROUPS.indexOf(group), series, size, size2, variant: variantOf(rest) });
  let match: RegExpMatchArray | null;
  if (!name) return place(NO_FAMILY);
  // «RG1.37» is a thin micro-coax like RF 1.37, not RG-1.
  if ((match = name.match(/^RG[-\s]?(\d+[.,]\d+)(.*)$/i))) return place("Тонкие RF", decimal(match[1]), match[2]);
  if ((match = name.match(/^RG[-\s]?(\d+)(.*)$/i))) {
    const number = Number(match[1]);
    const first = RG_FIRST.indexOf(number);
    return place("RG", first >= 0 ? first : RG_FIRST.length + number, match[2]);
  }
  if ((match = name.match(/^LMR[-\s]?(\d+)(.*)$/i))) return place("LMR", Number(match[1]), match[2]);
  if ((match = name.match(/^(\d+)\s*D[-\s]?FB(.*)$/i))) return place("D-FB", Number(match[1]), match[2]);
  if ((match = name.match(/^RF[-\s]?(\d+[.,]\d+)(.*)$/i))) return place("Тонкие RF", decimal(match[1]), match[2]);
  for (const [series, pattern] of SEMI.entries()) {
    if ((match = name.match(pattern))) return place("Полужёсткие и гибкие", Number(match[1] ?? 0), name.slice(match[0].length), series);
  }
  // РК50-1,5-21: by the size (the second number), then the impedance; other Russian marks (КВСФ-75) after them.
  if ((match = name.match(/^РК\s*-?\s*(\d+)\s*-\s*(\d+(?:[.,]\d+)?)(.*)$/i))) return place("Отечественные", decimal(match[2]), match[3], 0, Number(match[1]));
  if (/^[А-ЯЁ]/i.test(name)) return place("Отечественные", 0, "", 1);
  if ((match = name.match(/^(\d+)\/(\d+)\s*(?:"|″|'')?(.*)$/))) return place("Фидеры", Number(match[1]) / Number(match[2]), match[3]);
  return place("Прочие");
}

/** The group a mark is shown under. */
export const coaxGroupName = (mark: string): CoaxGroup => GROUPS[coaxPlace(mark).group];

const text = (a: string, b: string) => a.localeCompare(b, "ru", { numeric: true, sensitivity: "base" });

/** Catalogue order of two marks: group, series, size, base before variants, variants A–Я, then the spelling. */
export function compareCoaxMarks(a: string, b: string): number {
  const left = coaxPlace(a);
  const right = coaxPlace(b);
  return left.group - right.group || left.series - right.series || left.size - right.size || left.size2 - right.size2
    || Number(left.variant !== "") - Number(right.variant !== "") || text(left.variant, right.variant) || text(a, b);
}

/** Group label per row (the rows are not needed any more: kept for the table's `groupBy` wiring). */
export function coaxGroupOf(_rows?: CableRow[]): (row: CableRow) => string {
  return (row) => coaxGroupName(row.cable);
}

/** Rows in catalogue order, without touching the input. */
export function sortCoax(rows: CableRow[]): CableRow[] {
  return [...rows].sort((a, b) => compareCoaxMarks(a.cable, b.cable));
}

type Naming = { full_name?: string; aliases?: Array<{ alias: string }> };
/** Full name and aliases the card carries (the API sends them; the shared `CoaxCard` type does not list them). */
export const coaxNaming = (row: CableRow): { fullName: string; aliases: string[] } => {
  const card = (row.card ?? {}) as Naming;
  return { fullName: card.full_name?.trim() ?? "", aliases: (card.aliases ?? []).map((item) => item.alias).filter(Boolean) };
};
