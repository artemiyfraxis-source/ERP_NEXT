const APPROXIMATE_GLYPH_WIDTH = 6.2;
const CELL_PADDING = 16;

/** Estimate compact canvas-table columns from their current visible content. */
export function drawingTableColumnWidths(
  headers: readonly string[],
  rows: readonly (readonly string[])[],
  { minimum = 42, maximum = 420 }: { minimum?: number; maximum?: number } = {},
): number[] {
  return headers.map((header, column) => {
    const longest = rows.reduce((length, row) => Math.max(length, Array.from(row[column] ?? "").length), Array.from(header).length);
    return Math.max(minimum, Math.min(maximum, Math.ceil(longest * APPROXIMATE_GLYPH_WIDTH + CELL_PADDING)));
  });
}
