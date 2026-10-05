// Штриховки оболочек — собственный небольшой набор ERP. Каталог TECHMAP-GRAPHER (hatching-catalog.json)
// взят из QCAD под GPL-3.0 и сюда сознательно не перенесён.
// Формат семейства линий: [угол°, x0, y0, сдвиг вдоль линии, шаг между линиями, ...штрихи (>0 линия, <0 пробел, 0 точка)].

export interface HatchPattern {
  readonly code: string;
  readonly title: string;
  /** Множитель геометрии при масштабе 1. */
  readonly scale_preview: number;
  readonly lines: readonly (readonly number[])[];
}

export const hatchingCatalog: readonly HatchPattern[] = [
  { code: "Ш1", title: "Наклонная 45°", scale_preview: 3, lines: [[45, 0, 0, 0, 3]] },
  { code: "Ш2", title: "Наклонная 135°", scale_preview: 3, lines: [[135, 0, 0, 0, 3]] },
  { code: "Ш3", title: "Сетка косая", scale_preview: 3, lines: [[45, 0, 0, 0, 3], [135, 0, 0, 0, 3]] },
  { code: "Ш4", title: "Горизонтальная", scale_preview: 3, lines: [[0, 0, 0, 0, 3]] },
  { code: "Ш5", title: "Сетка прямая", scale_preview: 3, lines: [[0, 0, 0, 0, 3], [90, 0, 0, 0, 3]] },
  { code: "Ш6", title: "Штриховая 45°", scale_preview: 3, lines: [[45, 0, 0, 1.5, 3, 2, -1]] },
  { code: "Ш7", title: "Точки", scale_preview: 3, lines: [[0, 0, 0, 3, 3, 0, -3]] },
  { code: "Ш8", title: "Двойная 45°", scale_preview: 3, lines: [[45, 0, 0, 0, 4], [45, 0, 0.8, 0, 4]] },
];

/**
 * Пусто — без штриховки. Неизвестный код (например, из документа TECHMAP-GRAPHER со штриховкой QCAD)
 * документ не ломает: он сохраняется как есть и просто не рисуется.
 */
export const validHatchCode = (code: unknown): code is string =>
  typeof code === "string" && (code === "" || /^[\p{L}\p{N}_-]{1,16}$/u.test(code));
export const knownHatchCode = (code: string): boolean => hatchingCatalog.some((h) => h.code === code);

type Point = { x: number; y: number };
type Segment = readonly [Point, Point];
const cache = new Map<string, readonly Segment[]>();

/** Отрезки штриховки внутри прямоугольника; фаза семейств сохраняется между соседними областями. */
export function hatchSegments(code: string, scale: number, angle: number, bounds: { x: number; y: number; width: number; height: number }): readonly Segment[] {
  const key = JSON.stringify([code, scale, angle, bounds]); const saved = cache.get(key); if (saved) return saved;
  const hatch = hatchingCatalog.find((h) => h.code === code); if (!hatch) return [];
  const result: Segment[] = []; const factor = scale * hatch.scale_preview;
  const rotation = angle * Math.PI / 180, cos = Math.cos(rotation), sin = Math.sin(rotation);
  const corners = [{ x: bounds.x, y: bounds.y }, { x: bounds.x + bounds.width, y: bounds.y }, { x: bounds.x, y: bounds.y + bounds.height }, { x: bounds.x + bounds.width, y: bounds.y + bounds.height }];
  for (const line of hatch.lines) {
    const [degrees = 0, x = 0, y = 0, sx = 0, sy = 1, ...dash] = line;
    const a = (degrees * Math.PI / 180) - rotation, ux = Math.cos(a), uy = -Math.sin(a), nx = -uy, ny = ux;
    const ox = factor * (x * cos + y * sin), oy = factor * (x * sin - y * cos), step = -sy * factor;
    if (Math.abs(step) < 1e-9) continue;
    const indices = corners.map((p) => ((p.x - ox) * nx + (p.y - oy) * ny) / step);
    const start = Math.floor(Math.min(...indices)) - 1, end = Math.ceil(Math.max(...indices)) + 1;
    const pattern = dash.map((d) => d * factor), period = pattern.reduce((n, d) => n + Math.abs(d), 0);
    for (let n = start; n <= end; n++) {
      const px = ox + n * (sx * factor * ux + step * nx), py = oy + n * (sx * factor * uy + step * ny);
      let lo = -Infinity, hi = Infinity;
      for (const [p, v, min, max] of [[px, ux, bounds.x, bounds.x + bounds.width], [py, uy, bounds.y, bounds.y + bounds.height]] as const) {
        if (Math.abs(v) < 1e-10) { if (p < min || p > max) { lo = 1; hi = 0; break; } }
        else { const t1 = (min - p) / v, t2 = (max - p) / v; lo = Math.max(lo, Math.min(t1, t2)); hi = Math.min(hi, Math.max(t1, t2)); }
      }
      if (lo > hi) continue;
      const add = (from: number, to: number) => result.push([{ x: px + from * ux, y: py + from * uy }, { x: px + to * ux, y: py + to * uy }]);
      if (!pattern.length) { add(lo, hi); continue; }
      if (!period) continue;
      for (let k = Math.floor(lo / period); k <= Math.ceil(hi / period); k++) {
        let pos = k * period;
        for (const d of pattern) { if (d >= 0 && pos <= hi && pos + d >= lo) add(Math.max(pos, lo), Math.min(pos + d, hi)); pos += Math.abs(d); }
      }
    }
  }
  if (cache.size >= 64) cache.delete(cache.keys().next().value!);
  cache.set(key, result);
  return result;
}

export function drawCatalogHatch(ctx: CanvasRenderingContext2D, code: string, scale: number, angle: number, color: string, width: number, bounds: { x: number; y: number; width: number; height: number }) {
  ctx.save(); ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = width; ctx.lineCap = "butt"; ctx.beginPath();
  const dots: Point[] = [];
  for (const [a, b] of hatchSegments(code, scale, angle, bounds)) {
    if (Math.hypot(a.x - b.x, a.y - b.y) < 1e-8) dots.push(a);
    else { ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); }
  }
  ctx.stroke(); ctx.beginPath();
  for (const p of dots) { ctx.moveTo(p.x + width / 2, p.y); ctx.arc(p.x, p.y, width / 2, 0, 2 * Math.PI); }
  ctx.fill(); ctx.restore();
}
