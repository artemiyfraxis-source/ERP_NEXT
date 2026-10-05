export const hatchKinds = ["parallel", "cross", "double", "dots", "brick"] as const;
export interface DrawingHatch { kind: typeof hatchKinds[number]; spacing: number; angle: number; backgroundColor?: string | null }
export const hatchLabels = { parallel: "Наклонные линии", cross: "Перекрёстная", double: "Парные линии", dots: "Точки", brick: "Кладка" };
/** Generic drafting presets; material meaning is selected by the author. */
export function hatchTile(hatch: DrawingHatch) {
  const s = hatch.spacing;
  const lines: number[][] = hatch.kind === "dots" ? [] : [[0, 0, s, 0]];
  if (hatch.kind === "cross") lines.push([0, 0, 0, s]);
  if (hatch.kind === "double") lines.push([0, s / 3, s, s / 3]);
  if (hatch.kind === "brick") lines.push([0, s / 2, s, s / 2], [0, 0, 0, s / 2], [s / 2, s / 2, s / 2, s]);
  return { lines, dots: hatch.kind === "dots" ? [[s / 2, s / 2, Math.max(0.5, s / 12)]] : [] };
}
