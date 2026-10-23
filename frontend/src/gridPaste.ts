/* Generic pieces of the paste grids (procurement request grid, warehouse receipt grid): split a block copied from
   Excel/Sheets, match its header row to the grid's fields, fill the grid. No React; the callers keep their own
   field lists, defaults and wording. */

export type PasteField<K extends string = string> = { key: K; label: string; aliases: string[] };

export type TsvParse<K extends string = string> =
  | { rows: Array<Partial<Record<K, string>>>; recognized: string[]; unknown: string[] }
  | { error: string };

export function splitLines(text: string): string[] {
  const lines = text.replace(/\r/g, "").split("\n");
  while (lines.length && lines[lines.length - 1].trim() === "") lines.pop();
  return lines;
}

/** Header cell as compared: no trailing «*» (required marker), one space, lower case. */
export function normalizeHeader(value: string): string {
  return value.replace(/\s*\*+\s*$/, "").replace(/\s+/g, " ").trim().toLowerCase();
}

/** «31.12.2026» from Excel → «2026-12-31»; anything else that is not ISO → empty. */
export function dateToIso(value: string): string {
  const text = value.trim();
  const match = text.match(/^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$/);
  if (match) return `${match[3]}-${match[2].padStart(2, "0")}-${match[1].padStart(2, "0")}`;
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : "";
}

export function headerMap<K extends string>(line: string, fields: ReadonlyArray<PasteField<K>>) {
  const byNorm = new Map<string, PasteField<K>>();
  for (const field of fields) {
    for (const name of [field.label, ...field.aliases]) byNorm.set(normalizeHeader(name), field);
  }
  const heads = line.split("\t");
  return { heads, colField: heads.map((head) => byNorm.get(normalizeHeader(head)) ?? null) };
}

/** A pasted block whose first line names at least one known column is read by headers. */
export function hasHeaderRow<K extends string>(text: string, fields: ReadonlyArray<PasteField<K>>): boolean {
  const first = splitLines(text)[0];
  if (first === undefined) return false;
  return headerMap(first, fields).colField.some(Boolean);
}

export function parseTsv<K extends string>(
  text: string,
  fields: ReadonlyArray<PasteField<K>>,
  dateKeys: ReadonlySet<K>,
  messages: { short: string; noHeaders: string },
): TsvParse<K> {
  const lines = splitLines(text);
  if (lines.length < 2) return { error: messages.short };
  const { heads, colField } = headerMap(lines[0], fields);
  const recognized: string[] = [];
  const unknown: string[] = [];
  heads.forEach((head, index) => {
    const field = colField[index];
    if (field) recognized.push(field.label);
    else if (head.trim()) unknown.push(head.trim());
  });
  if (!recognized.length) return { error: messages.noHeaders };
  const rows: Array<Partial<Record<K, string>>> = [];
  for (let r = 1; r < lines.length; r += 1) {
    if (lines[r].trim() === "") continue;
    const cells = lines[r].split("\t");
    const row: Partial<Record<K, string>> = {};
    colField.forEach((field, c) => {
      if (!field) return;
      const value = (cells[c] ?? "").trim();
      row[field.key] = dateKeys.has(field.key) ? dateToIso(value) : value;
    });
    rows.push(row);
  }
  return { rows, recognized, unknown };
}

export type PasteResult<R> = {
  positions: R[];
  /** rows written or added by this paste */
  filled: number;
  /** rows that did not fit under the limit and were dropped */
  dropped: number;
  mode: "headers" | "cells";
  unknown: string[];
};

export type PasteSetup<K extends string, R extends Record<string, unknown>> = {
  fields: ReadonlyArray<PasteField<K>>;
  dateKeys: ReadonlySet<K>;
  /** grid columns in the order a headerless block fills them */
  columns: ReadonlyArray<K>;
  limit: number;
  seed: R;
  isBlank: (row: R) => boolean;
  /** a pasted value in the shape the cell keeps («1,5» → «1.5», a date → ISO) */
  cellValue: (key: K, raw: string) => string;
  /** rule applied to every written row */
  finalize?: (row: R) => R;
  /** keys of the cell mode that take an empty pasted value too */
  overwriteEmpty?: ReadonlyArray<K>;
  messages: { short: string; noHeaders: string };
};

/** Rows of a header block, ready to follow the filled rows: blank tail rows are reused, the limit is respected. */
export function appendParsedRows<K extends string, R extends Record<string, unknown>>(
  positions: R[],
  parsed: Array<Partial<Record<K, string>>>,
  setup: Pick<PasteSetup<K, R>, "limit" | "seed" | "isBlank" | "finalize">,
): { positions: R[]; filled: number; dropped: number } {
  const kept = [...positions];
  while (kept.length && setup.isBlank(kept[kept.length - 1])) kept.pop();
  const incoming = parsed.map((row) => {
    const filled = Object.fromEntries(Object.entries(row).filter(([, value]) => value !== ""));
    const merged = { ...setup.seed, ...filled } as R;
    return setup.finalize ? setup.finalize(merged) : merged;
  });
  const room = Math.max(setup.limit - kept.length, 0);
  const taken = incoming.slice(0, room);
  return { positions: [...kept, ...taken], filled: taken.length, dropped: incoming.length - taken.length };
}

/** Paste of a block copied from Excel/Sheets into a grid.
 *  With a header row columns are matched by name and the rows go after the filled ones (blank rows are reused).
 *  Without headers the block lands from the focused cell down and to the right in the order of the columns. */
export function applyGridPaste<K extends string, R extends Record<string, unknown>>(
  positions: R[],
  rowIndex: number,
  colKey: K,
  text: string,
  setup: PasteSetup<K, R>,
): PasteResult<R> | { error: string } {
  if (hasHeaderRow(text, setup.fields)) {
    const parsed = parseTsv(text, setup.fields, setup.dateKeys, setup.messages);
    if ("error" in parsed) return parsed;
    const result = appendParsedRows(positions, parsed.rows, setup);
    return { ...result, mode: "headers", unknown: parsed.unknown };
  }
  const lines = splitLines(text);
  const startCol = Math.max(setup.columns.indexOf(colKey), 0);
  const next = positions.map((item) => ({ ...item }));
  let dropped = 0;
  let filled = 0;
  lines.forEach((line, offset) => {
    const target = rowIndex + offset;
    if (target >= setup.limit) {
      dropped += 1;
      return;
    }
    while (next.length <= target) next.push({ ...setup.seed });
    line.split("\t").forEach((raw, c) => {
      const key = setup.columns[startCol + c];
      if (!key) return;
      const value = setup.cellValue(key, raw);
      // an empty pasted cell keeps what is there; a date that does not read keeps it too
      if (value === "" && !setup.overwriteEmpty?.includes(key)) return;
      next[target] = { ...next[target], [key]: value };
    });
    if (setup.finalize) next[target] = setup.finalize(next[target]);
    filled += 1;
  });
  return { positions: next, filled, dropped, mode: "cells", unknown: [] };
}
