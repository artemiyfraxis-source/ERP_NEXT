import type { CableRow } from "./api";

/* «Коаксиальные кабели» grouped by series/family (owner 13.10.2026): RG-174, RG-316 … one group per RG number, LMR as one
   family, every other mark by its first word. The family comes from the card when the row has one, else from the mark. */

export const NO_FAMILY = "Без семейства";
const RG = /^rg[-\s]?(\d+)(?![.\d])/i; // «RG1.37» is a micro-coax of its own, not RG-1

/** «RG316», «RG-316 A/U», «rg 316» → «RG-316»; «LMR-195», «LMR400» → «LMR»; else the mark up to the first space/digit group. */
export function coaxFamilyName(text: string): string {
  const name = String(text ?? "").trim();
  if (!name) return NO_FAMILY;
  const rg = name.match(RG);
  if (rg) return `RG-${Number(rg[1])}`;
  if (/^lmr/i.test(name)) return "LMR";
  const word = name.split(/[\s/]+/)[0].replace(/[-_]?\d.*$/, "");
  return (word || name.split(/\s+/)[0]).toUpperCase();
}

// The card's own `family` is a descriptive class («Гибкий тонкий»), not a series: the family comes from the mark.
export const coaxFamily = (row: CableRow): string => coaxFamilyName(row.cable);

/** Order of groups: RG by number, LMR, other families A–Я, «Прочие семейства», the unnamed last. */
export function compareFamilies(a: string, b: string): number {
  const rank = (name: string) => name === NO_FAMILY ? 4 : name === OTHER_FAMILIES ? 3 : RG.test(name) ? 0 : name === "LMR" ? 1 : 2;
  const number = (name: string) => Number(name.match(RG)?.[1] ?? 0);
  return rank(a) - rank(b) || number(a) - number(b) || a.localeCompare(b, "ru", { numeric: true });
}

/** A family of one cable would be a header as tall as its row (RG-141, RG-142, RG-178 …): those gather in one group. */
export const OTHER_FAMILIES = "Прочие семейства";

/** Group label per row: the family, or «Прочие семейства» when the family has a single cable (the unnamed stay apart). */
export function coaxGroupOf(rows: CableRow[]): (row: CableRow) => string {
  const sizes = new Map<string, number>();
  for (const row of rows) sizes.set(coaxFamily(row), (sizes.get(coaxFamily(row)) ?? 0) + 1);
  return (row) => {
    const family = coaxFamily(row);
    return family !== NO_FAMILY && sizes.get(family) === 1 ? OTHER_FAMILIES : family;
  };
}

/** Rows by group (single-cable families gathered into «Прочие семейства» before the unnamed), then family, then mark. */
export function sortCoax(rows: CableRow[]): CableRow[] {
  const group = coaxGroupOf(rows);
  return [...rows].sort((a, b) => compareFamilies(group(a), group(b)) || compareFamilies(coaxFamily(a), coaxFamily(b))
    || a.cable.localeCompare(b.cable, "ru", { numeric: true, sensitivity: "base" }));
}

type Naming = { full_name?: string; aliases?: Array<{ alias: string }> };
/** Full name and aliases the card carries (the API sends them; the shared `CoaxCard` type does not list them). */
export const coaxNaming = (row: CableRow): { fullName: string; aliases: string[] } => {
  const card = (row.card ?? {}) as Naming;
  return { fullName: card.full_name?.trim() ?? "", aliases: (card.aliases ?? []).map((item) => item.alias).filter(Boolean) };
};
