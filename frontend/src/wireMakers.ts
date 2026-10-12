/* БД.ПРОВ: «Стандарт» (one value) and «Производители» (a list) are two cells of a record (owner 12.10.2026). The list is
   stored as names joined by «, ». The split of the old «Стандарт / производитель» cell is done on the server
   (backend/app/modules/references/wire_makers.py); this file only reads and writes the list. */

export const STANDARD = "Стандарт";
export const MAKERS = "Производители";
export const LEGACY_STANDARD = "Стандарт / производитель";

/** Parts split on «,» / «;» outside brackets. */
export function parseMakers(text: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = "";
  for (const char of String(text ?? "")) {
    if ("([«".includes(char)) depth += 1;
    else if (")]»".includes(char)) depth = Math.max(0, depth - 1);
    if (depth === 0 && (char === "," || char === ";")) {
      parts.push(current);
      current = "";
    } else current += char;
  }
  parts.push(current);
  const seen = new Set<string>();
  return parts.map((part) => part.trim()).filter((part) => {
    const key = part.toLowerCase();
    if (!part || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export const joinMakers = (names: string[]): string => parseMakers(names.join(";")).join(", ");

/** Names already used in the table, most frequent first: the suggestions of the field. */
export function makerSuggestions(values: Iterable<string>): string[] {
  const count = new Map<string, number>();
  for (const value of values) for (const name of parseMakers(value)) count.set(name, (count.get(name) ?? 0) + 1);
  return [...count.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "ru")).map(([name]) => name);
}
