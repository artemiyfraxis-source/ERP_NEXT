/** A view-local array; numbering follows the chosen traversal from contact 1. */
export interface DrawingArrayLayout {
  rows: number;
  direction: "long-side" | "short-side";
  numbering: "new-row" | "snake";
  countSource: "article" | "parameter";
}
export function arrayPositions(count: number, layout: DrawingArrayLayout, pitchX: number, pitchY: number) {
  if (!Number.isSafeInteger(count) || count < 1 || count > 1000 ||
      !Number.isSafeInteger(layout.rows) || layout.rows < 1 || layout.rows > 4 ||
      ![pitchX, pitchY].every(value => Number.isFinite(value) && value > 0 && value <= 10000))
    throw new RangeError("Массив: 1–1000 элементов, 1–4 ряда, положительный шаг до 10000.");
  const columns = Math.ceil(count / layout.rows);
  const cells: { x: number; y: number }[] = [];
  const across = layout.direction === "short-side";
  for (let line = 0; line < (across ? columns : layout.rows); line++) {
    const length = across ? layout.rows : columns;
    for (let position = 0; position < length; position++) {
      const offset = layout.numbering === "snake" && line % 2 ? length - position - 1 : position;
      const row = across ? offset : line, column = across ? line : offset;
      if (row * columns + column < count) cells.push({ x: column * pitchX, y: row * pitchY });
    }
  }
  return cells;
}
