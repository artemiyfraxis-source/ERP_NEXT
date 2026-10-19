import type { CableRow } from "./api";

/* «Коаксиальные кабели» grouped by the head of the mark (owner 16.10.2026: «нормальную сортировку по марке, взять за
   головную например RG, SF, SAT итд»): the letters a mark starts with — RG, SF, SR, SAT, LMR, MFC, LCF, РК … — and
   «D-FB» for 5D-FB / 8D-FB / 10D-FB. Marks that start with an inch size (1/2″, 7/8 Baltic Signal …) are «Фидеры»; a head
   only one mark has goes to «Прочие», so the list is not cut into single rows. Groups in alphabet order (Latin, then
   Cyrillic), «Фидеры» and «Прочие» last; inside a group by the numbers of the mark (RG-6, RG-11, RG-58, RG-142 …), the
   base mark before its variants (A/U, C/U, FEP, UF…). Spellings are normalised only for this order (RG58 = RG-58);
   the marks themselves stay as written. A column header sort still overrides it. */

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
function compareGroups(a: string, b: string): number {
  return LAST.indexOf(a) - LAST.indexOf(b) || Number(!latin(a)) - Number(!latin(b)) || text(a, b);
}

/** Group label per row: the head of the mark, «Прочие» for a head only one mark of the list has. */
export function coaxGroupOf(rows: CableRow[]): (row: CableRow) => string {
  const counts = new Map<string, number>();
  for (const item of rows) {
    const head = coaxPlace(item.cable).head;
    counts.set(head, (counts.get(head) ?? 0) + 1);
  }
  return (row) => {
    const head = coaxPlace(row.cable).head;
    return LAST.includes(head) || (counts.get(head) ?? 0) > 1 ? head : OTHERS;
  };
}

/** Catalogue order of two marks within a list: group, then the mark's numbers and variant. */
export function compareCoaxRows(rows: CableRow[]): (a: CableRow, b: CableRow) => number {
  const group = coaxGroupOf(rows);
  return (a, b) => compareGroups(group(a), group(b)) || compareInGroup(a.cable, b.cable);
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
