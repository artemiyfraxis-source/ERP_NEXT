/* Column widths and row windows of a list shown in blocks (DataTable `blocks`, «База данных»).

   26.10.2026 («БД как-то глючит», «когда листаешь, фпс падает»): the blocks used to be laid out twice on every open —
   every table at its content width to measure, then again at the levelled widths — and every row of every block was in
   the page (1 235 rows × 20 columns on «Монтажные провода»). Now the widths come from the data (the longest texts of
   each column, measured on a canvas) and the tables use the fixed layout, one line per row; only the rows near the
   screen are in the page, the others are empty space of their height. */

/** Cell padding left + right (`.data-table th, td { padding: 6px 12px }`). */
export const CELL_PAD = 24;
/** The row-select column (`table.selectable … td:first-child { width: 36px }`). */
export const SELECT_WIDTH = 36;
/** The widest a text column is measured at (a longer text is cut with «…»), the narrowest it is squeezed to. */
export const LEVEL_MAX = 360;
export const LEVEL_MIN = 64;
/** Text columns of a list too wide for the screen are cut to one width, never below `CAP_FLOOR` (about 9 letters); a
    list that cannot fit even so cuts them at `CAP_READABLE` and scrolls sideways. */
export const CAP_FLOOR = 96;
export const CAP_READABLE = 150;
/** Room for a dot, a chip's padding or a link mark in a column the screen draws itself (`render`). */
export const RENDER_ROOM = 16;
/** The sort mark « ▲» after a caption. */
const SORT_MARK = 14;
/** Rows per window step: a block is put in the page by pieces of this many rows (an even number keeps the zebra). */
export const CHUNK_ROWS = 20;
/** A row's height until it is measured (`.data-table td`: 13 px × 1.35 + 2 × 6 px + the border). */
export const ROW_HEIGHT = 31;
/** A header row's height until it is measured (a caption on two lines). */
export const HEAD_HEIGHT = 52;

export type Measure = (text: string, kind: "head" | "cell" | "key") => number;

/** The `count` longest texts (by characters — close enough to pick the widest ones to measure). */
export function longestTexts(values: Iterable<string>, count = 6): string[] {
  const top: string[] = [];
  for (const raw of values) {
    const value = raw.trim();
    if (!value) continue;
    if (top.length < count) { top.push(value); top.sort((a, b) => b.length - a.length); continue; }
    if (value.length <= top[top.length - 1].length || top.includes(value)) continue;
    top[top.length - 1] = value;
    top.sort((a, b) => b.length - a.length);
  }
  return top;
}

/** A caption's width on two lines at most: the best split of its words (one word — the whole word). */
export function headWidth(title: string, measure: Measure): number {
  const words = title.trim().split(/\s+/).filter(Boolean);
  if (words.length <= 1) return measure(title.trim(), "head");
  let best = Infinity;
  for (let at = 1; at < words.length; at += 1) {
    best = Math.min(best, Math.max(measure(words.slice(0, at).join(" "), "head"), measure(words.slice(at).join(" "), "head")));
  }
  return best;
}

export interface LevelColumn {
  key: string;
  title: string;
  numeric: boolean;
  /** The column's texts (only the longest few are measured). */
  texts: string[];
  /** The first column (bold). */
  first: boolean;
  /** The widest the column is measured at (the column's own `maxWidth`, else `LEVEL_MAX`). */
  max?: number;
  /** The screen draws the cell itself (a dot, a chip, a link): a little more room than its text. */
  rendered?: boolean;
}

/** The natural width of each column (text + padding): its widest text up to `max`, never narrower than its caption on
    two lines. */
export function naturalWidths(columns: LevelColumn[], measure: Measure): Map<string, number> {
  const result = new Map<string, number>();
  for (const column of columns) {
    const kind = column.first ? "key" : "cell";
    const text = Math.max(0, ...longestTexts(column.texts).map((value) => measure(value, kind)));
    const head = headWidth(column.title, measure) + SORT_MARK;
    result.set(column.key, Math.ceil(Math.max(head, Math.min(text + (column.rendered ? RENDER_ROOM : 0), column.max ?? LEVEL_MAX)) + CELL_PAD));
  }
  return result;
}

export interface LevelTable {
  /** The keys of the table's columns that take a levelled width, in order. */
  keys: string[];
  /** The width already taken by its other columns (dragged, set by the screen, the row select). */
  taken: number;
}

export interface Levelled {
  widths: Map<string, number>;
  /** Per table: its width when it is wider than the list (it scrolls sideways), else null. */
  wide: Array<number | null>;
}

/** The blocks go level (one list, one set of widths): each column takes its natural width. When a table is wider than
    `room`, the widest text columns give way first — every text column is capped at one width, as high as lets the table
    fit (a short column keeps its whole text, a long one is cut with «…»); numbers keep their width. A table that does
    not fit even with the cap at `CAP_FLOOR` keeps the cap at `CAP_READABLE` and scrolls sideways. */
export function levelWidths(natural: Map<string, number>, numeric: Set<string>, tables: LevelTable[], room: number): Levelled {
  const texts = (table: LevelTable) => table.keys.filter((key) => !numeric.has(key)).map((key) => natural.get(key) ?? 0);
  const numbers = (table: LevelTable) => table.keys.reduce((sum, key) => sum + (numeric.has(key) ? natural.get(key) ?? 0 : 0), 0);
  const capped = (widths: number[], cap: number) => widths.reduce((sum, width) => sum + Math.min(width, cap), 0);
  let cap = Infinity;
  let tooWide = false;
  for (const table of tables) {
    const own = texts(table);
    const space = room - table.taken - numbers(table);
    if (capped(own, Infinity) <= space) continue;
    if (capped(own, CAP_FLOOR) > space) { tooWide = true; continue; }
    // the highest cap that fits: between the floor and the widest text, by halves
    let low = CAP_FLOOR;
    let high = Math.max(...own);
    while (high - low > 1) {
      const middle = (low + high) / 2;
      if (capped(own, middle) <= space) low = middle; else high = middle;
    }
    cap = Math.min(cap, Math.floor(low));
  }
  if (tooWide) cap = Math.min(cap, CAP_READABLE);
  const widths = new Map<string, number>();
  for (const [key, width] of natural) widths.set(key, numeric.has(key) ? width : Math.min(width, cap));
  const wide = tables.map((table) => {
    const width = table.taken + table.keys.reduce((sum, key) => sum + (widths.get(key) ?? 0), 0);
    return width > room + 1 ? Math.ceil(width) : null;
  });
  return { widths, wide };
}

/** Text widths on a canvas in the table's fonts, cached; without a canvas (tests) — by the number of characters. */
export function canvasMeasure(fonts: Record<"head" | "cell" | "key", string>): Measure {
  let context: CanvasRenderingContext2D | null = null;
  try {
    const jsdom = typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent);
    context = jsdom ? null : document.createElement("canvas").getContext("2d");
  } catch { context = null; }
  const cache = new Map<string, number>();
  return (text, kind) => {
    const id = `${kind}\u0001${text}`;
    let width = cache.get(id);
    if (width === undefined) {
      if (context) { context.font = fonts[kind]; width = context.measureText(text).width; } else width = text.length * 7;
      cache.set(id, width);
    }
    return width;
  };
}

/** The pieces of a block's rows the window puts in the page. */
export function chunksOf<T>(rows: T[], size = CHUNK_ROWS): T[][] {
  const result: T[][] = [];
  for (let at = 0; at < rows.length; at += size) result.push(rows.slice(at, at + size));
  return result;
}
