interface Span {
  readonly orientation: "horizontal" | "vertical";
  readonly start: number;
  readonly end: number;
  readonly crossMinimum: number;
  readonly crossMaximum: number;
}

/** Reserve the complete decoration footprint, not just the conductor centre. */
export function clearDecorationSpans<T extends Span>(
  spans: readonly T[],
  tables: readonly { x: number; y: number; width: number; height: number }[],
  alongRadius: number,
  crossSize: (span: T) => number,
): T[] {
  return spans.flatMap(span => {
    const cross = (span.crossMinimum + span.crossMaximum) / 2;
    const radius = crossSize(span) / 2 + 4;
    let intervals = [{ start: span.start, end: span.end }];
    for (const table of tables) {
      const horizontal = span.orientation === "horizontal";
      const crossStart = horizontal ? table.y : table.x;
      const crossEnd = crossStart + (horizontal ? table.height : table.width);
      if (cross + radius < crossStart || cross - radius > crossEnd) continue;
      const start = (horizontal ? table.x : table.y) - alongRadius - 4;
      const end = (horizontal ? table.x + table.width : table.y + table.height) + alongRadius + 4;
      intervals = intervals.flatMap(interval => {
        if (end <= interval.start || start >= interval.end) return [interval];
        return [
          ...(start > interval.start ? [{ start: interval.start, end: start }] : []),
          ...(end < interval.end ? [{ start: end, end: interval.end }] : []),
        ];
      });
    }
    return intervals.filter(interval => interval.end > interval.start).map(interval => ({ ...span, ...interval }));
  });
}
