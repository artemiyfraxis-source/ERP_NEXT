import type { CableRow } from "./api";

/* «Коаксиальные кабели» grouped by the mark family. 16.10.2026 the owner asked for a sort by the head of the mark («взять
   за головную например RG, SF, SAT итд»); 19.10.2026, seeing every supplier article with its own materials as its own
   row, he said: «почему все RG в одном, почему все D-FB в одном». So a group is now the family — the head letters plus
   the numbers of the mark as `coaxPlace` reads them: RG-58 (RG-58, RG-58 A/U, RG-58 CU PE AL PVC …), RG-6, RG-174, 5D-FB,
   8D-FB, 10D-FB (each its own), LMR-195, RF 1.37, РК50-2-22 … Spellings fold together (RG58 = RG-58, 5D FB = 5D-FB). The
   label is the family's base mark as written in the list (the row without a variant, else the shortest mark). A family
   of one row is no group: such rows of one head gather in «<head> — разные», before that head's families; a head with
   only one such row overall goes to «Прочие». Marks that start with an inch size (1/2″, 7/8 Baltic Signal …) stay one
   group «Фидеры». Order: by head (Latin, then Cyrillic), then by the family's numbers (RG-6, RG-11, RG-58, RG-142 …;
   5D-FB, 8D-FB, 10D-FB); «Фидеры» and «Прочие» last; inside a group the base mark before its variants (A/U, C/U, FEP,
   UF…). Spellings are normalised only for grouping and order; the marks stay as written. A column header sort inside a
   group still overrides it. */

export const NO_FAMILY = "Без марки";
export const FEEDERS = "Фидеры";
export const OTHERS = "Прочие";

type Place = { head: string; numbers: number[]; variant: string };

const decimal = (text: string) => Number(text.replace(",", "."));
const text = (a: string, b: string) => a.localeCompare(b, "ru", { numeric: true, sensitivity: "base" });

/** The head of a mark, its numbers in order and the variant suffix after them ("" — the base mark). */
export function coaxPlace(mark: string): Place {
  const name = String(mark ?? "").trim().replace(/\s+/g, " ");
  if (!name) return { head: NO_FAMILY, numbers: [], variant: "" };
  let match: RegExpMatchArray | null;
  // «1/2″», «7/8 Baltic Signal …»: an inch size first.
  if ((match = name.match(/^(\d+)\/(\d+)\s*(?:"|″|'')?(.*)$/))) {
    return { head: FEEDERS, numbers: [Number(match[1]) / Number(match[2])], variant: match[3].trim() };
  }
  // «5D-FB Cu»: the size before the letters.
  if ((match = name.match(/^(\d+)\s*D[-\s]?FB(.*)$/i))) return { head: "D-FB", numbers: [Number(match[1])], variant: variantOf(match[2]) };
  match = name.match(/^([A-Za-zА-Яа-яЁё]+)(.*)$/);
  if (!match) return { head: OTHERS, numbers: [], variant: name };
  const head = match[1].toUpperCase();
  const rest = match[2];
  // The numbers of the mark: «RG-58 C/U» → 58; «RF 1.37» → 1.37; «РК50-2-22» → 50, 2, 22; «SF-141 100 Ом» → 141.
  const body = rest.match(/^[-\s]*(\d+(?:[.,]\d+)?(?:-\d+(?:[.,]\d+)?)*)(.*)$/);
  if (!body) return { head, numbers: [], variant: variantOf(rest) };
  return { head, numbers: body[1].split("-").map(decimal), variant: variantOf(body[2]) };
}

const variantOf = (rest: string) => rest.replace(/^[-\s/]+/, "").trim();

/** Order of two marks inside one group: by their numbers, the base mark before its variants, then the spelling. */
function compareInGroup(a: string, b: string): number {
  const left = coaxPlace(a);
  const right = coaxPlace(b);
  for (let index = 0; index < Math.max(left.numbers.length, right.numbers.length); index += 1) {
    const difference = (left.numbers[index] ?? -1) - (right.numbers[index] ?? -1);
    if (difference) return difference;
  }
  return Number(left.variant !== "") - Number(right.variant !== "") || text(left.variant, right.variant) || text(a, b);
}

const LAST = [FEEDERS, OTHERS, NO_FAMILY];
const latin = (name: string) => /^[A-Z]/.test(name);

type Slot = { label: string; head: string; rank: number; numbers: number[]; own?: string };
const familyKey = (place: Place) => `${place.head}|${place.numbers.join("-")}`;
const SINGLES = " — разные";

/** Where each row of the list goes: its family's group, or «<head> — разные», or «Прочие». Keyed by the mark as written. */
function layout(rows: CableRow[]): (cable: string) => Slot {
  const families = new Map<string, string[]>();
  for (const item of rows) {
    const place = coaxPlace(item.cable);
    if (LAST.includes(place.head)) continue;
    const key = familyKey(place);
    const marks = families.get(key) ?? [];
    marks.push(item.cable);
    families.set(key, marks);
  }
  const singlesPerHead = new Map<string, number>();
  for (const [key, marks] of families) {
    if (marks.length === 1) singlesPerHead.set(key.split("|")[0], (singlesPerHead.get(key.split("|")[0]) ?? 0) + 1);
  }
  const labels = new Map<string, string>();
  for (const [key, marks] of families) {
    const pool = marks.filter((mark) => coaxPlace(mark).variant === "");
    labels.set(key, [...(pool.length ? pool : marks)].sort((a, b) => Number(b.includes("-")) - Number(a.includes("-")) || a.length - b.length || text(a, b))[0]);
  }
  return (cable) => {
    const place = coaxPlace(cable);
    if (LAST.includes(place.head)) return { label: place.head, head: place.head, rank: 1, numbers: place.numbers };
    const key = familyKey(place);
    if ((families.get(key)?.length ?? 0) > 1) return { label: labels.get(key) ?? cable, head: place.head, rank: 1, numbers: place.numbers };
    if ((singlesPerHead.get(place.head) ?? 0) > 1) return { label: place.head + SINGLES, head: place.head, rank: 0, numbers: [] };
    return { label: OTHERS, head: OTHERS, rank: 1, numbers: [], own: place.head };
  };
}

function compareSlots(a: Slot, b: Slot): number {
  const last = LAST.indexOf(a.head) - LAST.indexOf(b.head);
  if (last) return last;
  if (LAST.includes(a.head)) { // inside «Прочие» the marks go by their own head letters
    const [left, right] = [a.own ?? "", b.own ?? ""];
    return Number(!latin(left)) - Number(!latin(right)) || text(left, right);
  }
  const byHead = Number(!latin(a.head)) - Number(!latin(b.head)) || text(a.head, b.head);
  if (byHead || a.rank !== b.rank) return byHead || a.rank - b.rank;
  for (let index = 0; index < Math.max(a.numbers.length, b.numbers.length); index += 1) {
    const difference = (a.numbers[index] ?? -1) - (b.numbers[index] ?? -1);
    if (difference) return difference;
  }
  return text(a.label, b.label);
}

/** Group label per row: its mark family, «<head> — разные» for single-row families, «Прочие» for a lone one. */
export function coaxGroupOf(rows: CableRow[]): (row: CableRow) => string {
  const slot = layout(rows);
  return (row) => slot(row.cable).label;
}

/** Catalogue order of two rows within a list: group, then the mark's numbers and variant. */
export function compareCoaxRows(rows: CableRow[]): (a: CableRow, b: CableRow) => number {
  const slot = layout(rows);
  return (a, b) => compareSlots(slot(a.cable), slot(b.cable)) || compareInGroup(a.cable, b.cable);
}

/** Only the group order of two rows (the mark family): the rows inside a group are ordered by the caller. */
export function compareCoaxGroups(rows: CableRow[]): (a: CableRow, b: CableRow) => number {
  const slot = layout(rows);
  return (a, b) => compareSlots(slot(a.cable), slot(b.cable));
}

/** Rows in catalogue order, without touching the input. */
export function sortCoax(rows: CableRow[]): CableRow[] {
  return [...rows].sort(compareCoaxRows(rows));
}

type Naming = { name?: string; aliases?: Array<{ alias: string; kind?: string; supplier?: string }> };
const nameKey = (value: string) => String(value || "").toLowerCase().replace(/[\s-]/g, "");
/** Other names of the very cable of the row (the API sends the card's aliases; the shared `CoaxCard` type does not list
    them): a spelling of the mark, a supplier's name without materials in it, an article with its supplier. A family
    card's names belong to another cable; a name that only repeats the mark or the card is not shown (owner 19.10.2026:
    «разные артикулы кабелей записал в одно» — offers of other materials are no aliases since). */
export const coaxOtherNames = (row: CableRow): string[] => {
  if (!row.card || !row.card_exact) return [];
  const card = row.card as Naming;
  const own = new Set([nameKey(row.cable), nameKey(card.name ?? "")]);
  return (card.aliases ?? []).filter((item) => item.alias && !own.has(nameKey(item.alias)))
    .map((item) => item.kind === "supplier_article" ? `арт. ${item.alias}${item.supplier ? ` (${item.supplier})` : ""}` : item.alias);
};
