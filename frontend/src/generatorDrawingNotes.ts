/*
 * Размеры и выноски чертежа жгута со сростками (владелец 18.10: «чертёж генерируется прям плохой по сравнению с тем что на
 * самом деле» — на СБ ЮТНВ.713К.4КБ1 одна зона «С1–С4» под общей трубкой, размеры от центра ветвления, выноски на трубки,
 * оплётку и маркировку, без подписей W1…).
 *
 * Только чтение готового документа и плана генератора: размеры (вспомогательные — длины проводов они не пересчитывают),
 * подписи зоны и жгута свободных концов и выноски оболочек текстом. Все числа — из плана генератора (длины резки проводов
 * зоны, длина общей трубки, оплётки и трубок ветвей); размеров, которых генератор не знает, не рисуется.
 */
import { designToScene } from "./harness/editor/HarnessDesignEditor";
import { dimensionGeometry, segmentDimensionKey, type DrawingDimension } from "./harness/editor/drawing-dimensions";
import type { DrawingDocuments, DrawingGraphic } from "./harness/editor/drawing-documents";
import type { HarnessDesignDocument, Point } from "./harness/editor/model";
import { physicalNodePoint } from "./harness/editor/physical-ports";
import type { TrunkParts } from "./drawingLayout";
import type { GeneratorDrawingPlan, PlanConnector } from "./generatorToHarness";

export interface ZoneNotesInput {
  readonly doc: HarnessDesignDocument;
  readonly plan: GeneratorDrawingPlan;
  /** Зоны на чертеже: номер зоны плана и ключи её сростков (в порядке `parts.zones`). */
  readonly zones: ReadonlyArray<{ readonly index: number; readonly keys: readonly string[] }>;
  readonly parts: TrunkParts;
  readonly trunkY: number;
  /** Выноски оболочек: текст → участок и доля его длины. */
  readonly callouts: ReadonlyArray<{ readonly text: string; readonly label: string; readonly segmentId: string; readonly at: number }>;
  /** Глубина корпуса разъёма по базе разъёмов, мм (длина корпуса вдоль проводов); нет — null. */
  readonly depthMm?: (connector: PlanConnector) => number | null;
  /** Разъём плана по узлу выхода. */
  readonly connectorAt: (exitNode: string) => PlanConnector | undefined;
}

type Box = { x: number; y: number; width: number; height: number };
const CHAR = 6.6, LINE = 14, NOTE = "#17384b", LEADER = "#55798e";
const overlaps = (a: Box, b: Box, gap = 3) => a.x < b.x + b.width + gap && b.x < a.x + a.width + gap && a.y < b.y + b.height + gap && b.y < a.y + a.height + gap;
const round2 = (value: number) => Math.round(value * 100) / 100;

export function zoneAnnotations(input: ZoneNotesInput): Pick<DrawingDocuments, "showDimensions" | "dimensions" | "graphics" | "hideObjectIndices"> {
  const { doc, plan, zones, parts, trunkY, callouts, connectorAt, depthMm = () => null } = input;
  const topology = doc.physicalTopology!;
  const nodeAt = (id: string): Point => physicalNodePoint(doc, topology.nodes.find((n) => n.id === id)!);
  const ends = (segmentId: string): [Point, Point] => {
    const segment = topology.segments.find((s) => s.id === segmentId)!;
    return [nodeAt(segment.from), nodeAt(segment.to)];
  };

  const dimensions: DrawingDimension[] = [];
  const graphics: DrawingGraphic[] = [];
  const taken: Box[] = [];
  /** Точки занятых линий (участки, размеры) для проверки выносок. */
  const lines: Point[] = [];
  const sample = (a: Point, b: Point) => {
    const steps = Math.max(1, Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / 6));
    for (let i = 0; i <= steps; i++) lines.push({ x: a.x + ((b.x - a.x) * i) / steps, y: a.y + ((b.y - a.y) * i) / steps });
  };
  topology.segments.forEach((segment) => { const [a, b] = ends(segment.id); sample(a, b); });
  const boxes = new Map<string, Box>();
  const scene = designToScene(doc, "drawing");
  scene.filter((o) => o.kind === "connector").forEach((o) => {
    boxes.set(o.id, { x: o.x, y: o.y, width: o.width, height: o.height });
    taken.push({ x: o.x - 4, y: o.y - 4, width: o.width + 8, height: o.height + 8 });
  });

  const dim = (segmentId: string, mode: DrawingDimension["mode"], offset: number, lengthMm: number | null) => {
    const segment = topology.segments.find((item) => item.id === segmentId);
    if (!segment || lengthMm === null) return;
    dimensions.push({ id: `gen-d${dimensions.length + 1}`, segmentId, from: 0, to: segment.path.points.length + 1, pointCount: segment.path.points.length + 2,
      routeKey: segmentDimensionKey(doc, segmentId), mode, modeOverride: true, offset, lengthMm, auxiliary: true });
    const [a, b] = ends(segmentId);
    const geometry = dimensionGeometry(a, b, mode, offset);
    sample(geometry[1]!, geometry[2]!);
    sample(a, geometry[1]!);
    sample(b, geometry[2]!);
    const mid = { x: (geometry[1]!.x + geometry[2]!.x) / 2, y: (geometry[1]!.y + geometry[2]!.y) / 2 };
    const width = `${lengthMm} мм`.length * 7 + 6;
    taken.push({ x: mid.x - width / 2, y: mid.y - 22, width, height: 26 });
  };
  const text = (at: Point, value: string, size = 12, bold = false) => {
    const width = value.length * CHAR * (size / 12) + 4;
    taken.push({ x: at.x, y: at.y - size, width, height: size + 4 });
    graphics.push({ id: `gen-g${graphics.length + 1}`, view: "drawing", kind: "text", points: [{ x: Math.round(at.x), y: Math.round(at.y) }], text: value, fontSize: size,
      color: NOTE, ...(bold ? { fontFamily: "Arial" } : {}) });
  };
  const longest = (list: readonly (number | null)[]) => { const v = list.filter((x): x is number => x !== null); return v.length ? Math.max(...v) : null; };

  zones.forEach((zone, k) => {
    const part = parts.zones.find((z) => z.index === k);
    if (!part) return;
    const members = (plan.splices || []).filter((sp) => zone.keys.includes(sp.key));
    const tube = plan.coverings.find((c) => c.kind === "zone" && c.zone === zone.index);
    const zoneMm = tube?.lengthMm ?? null, half = zoneMm ? zoneMm / 2 : 0;
    // Провод сростка доходит до середины зоны: видимая длина ветви — длина резки без половины зоны.
    const visible = (value: number | null) => (value === null ? null : round2(Math.max(1, value - half)));
    const label = members.length > 2 ? `${members[0]!.label}–${members.at(-1)!.label}` : members.map((sp) => sp.label).join(", ");
    const [left, right] = [nodeAt(part.left), nodeAt(part.right)];
    const tailUp = part.tail ? nodeAt(part.tail.node).y < trunkY : false;
    const side = tailUp ? 1 : -1; // размеры и подпись зоны — с той стороны, где нет жгута хвостов

    dim(part.segmentId, "horizontal", side * 34, zoneMm);
    text({ x: (left.x + right.x) / 2 - (label.length * CHAR) / 2, y: trunkY + side * (zoneMm ? 64 : 30) + (side > 0 ? 12 : 0) }, label, 13, true);

    const aLength = (key: string) => longest(members.flatMap((sp) => sp.wires.filter((w) => w.side === "A" && w.end.connector === key).map((w) => w.lengthMm)));
    // Ветви — от центра ветвления до торца разъёма (СБ: «до переднего торца X1–X4, до заднего торца X5, X6»). «Ветвь по КД» формы
    // — размер как есть; нет её — длина самого длинного провода сростка на разъёме без половины зоны, для стороны В ещё и без
    // глубины корпуса (провод кончается у переднего торца, задний — на глубину корпуса ближе), если база разъёмов её знает.
    if (k === 0) {
      const own = (c: PlanConnector) => c.kdBranchMm ?? visible(aLength(c.key));
      const branch = [...parts.aBranches].reverse().find((b) => { const c = connectorAt(b.exit); return c && own(c) !== null; });
      if (branch) dim(branch.segmentId, "horizontal", 40, own(connectorAt(branch.exit)!));
    }
    parts.bBranches.forEach((branch) => {
      const c = connectorAt(branch.exit);
      if (!c) return;
      const mine = longest(members.flatMap((sp) => sp.wires.filter((w) => w.side === "B" && w.end.connector === c.key).map((w) => w.lengthMm)));
      const depth = depthMm(c);
      const computed = mine === null ? null : round2(Math.max(1, mine - half - (depth ?? 0)));
      // Размер по горизонтали от центра ветвления до торца разъёма (как на СБ), с внешней стороны ветви: над верхней, под нижней.
      const up = nodeAt(branch.exit).y < trunkY;
      const box = boxes.get(topology.nodes.find((n) => n.id === branch.exit)?.connectorId ?? "");
      dim(branch.segmentId, "horizontal", box ? (up ? box.y - 26 - trunkY : box.height + 26) : up ? -80 : 40, c.kdBranchMm ?? computed);
    });
    if (part.tail) {
      const tip = nodeAt(part.tail.node);
      // Жгут свободных концов — третья ветвь веера, размер от центра ветвления до концов проводов отвода, под нижним разъёмом В.
      dim(part.tail.segmentId, "horizontal", 34, visible(longest(members.flatMap((sp) => sp.tails.map((t) => t.lengthMm)))));
      // Подпись — справа от конца жгута, выше подписи зачистки хвоста (strip-callouts.ts): жгут подходит слева сверху.
      text({ x: tip.x + 14, y: tip.y - 40 }, `Своб. концы от ${label}`, 12);
    }
    // Точка «Б» — на нижней кромке зоны, у общей трубки («ТУТ на месте Б» по КД).
    if (tube) {
      const mid = { x: (left.x + right.x) / 2, y: trunkY + 14 };
      graphics.push({ id: `gen-g${graphics.length + 1}`, view: "drawing", kind: "ellipse", points: [{ x: Math.round(mid.x - 3), y: Math.round(mid.y - 3) }, { x: Math.round(mid.x + 3), y: Math.round(mid.y + 3) }],
        color: NOTE, width: 2 });
      taken.push({ x: mid.x - 6, y: mid.y - 6, width: 12, height: 12 });
      text({ x: mid.x + 8, y: mid.y + 14 }, "Б", 13, true);
    }
  });

  // Выноски оболочек. Одинаковые на нескольких ветвях — одна выноска «Трубка ТТК 8/2, 3 места» с короткими прямыми линиями к
  // каждому месту из общей точки на краю подписи (веер). Линии не пересекают ветви, провода, размеры, другие линии и подписи:
  // подпись ставится туда, откуда чистые линии идут к наибольшему числу мест (между ветвями — в просвете между разъёмами);
  // места, до которых из этой точки чисто не достать (ветвь закрыта соседней), берёт следующая подпись той же оболочки.
  const wirePaths = scene.filter((o) => o.kind === "wire" && (o.points?.length ?? 0) > 1).map((o) => o.points as readonly Point[]);
  const branchSegs: Array<[Point, Point]> = topology.segments.map((segment) => ends(segment.id));
  const wireSegs: Array<[Point, Point]> = wirePaths.flatMap((path) => path.slice(1).map((p, i): [Point, Point] => [path[i]!, p]));
  const placedLeaders: Array<[Point, Point]> = [];
  // Сетка точек занятых линий (участки, размеры): проверка «рядом ли линия» — по соседним ячейкам, а не по всем точкам.
  const CELL = 12;
  const grid = new Map<string, Point[]>();
  lines.forEach((l) => { const key = `${Math.floor(l.x / CELL)},${Math.floor(l.y / CELL)}`; grid.set(key, [...(grid.get(key) ?? []), l]); });
  const lineNear = (x0: number, y0: number, x1: number, y1: number, skip?: Point, skipR = 0) => {
    for (let cx = Math.floor(x0 / CELL); cx <= Math.floor(x1 / CELL); cx++) for (let cy = Math.floor(y0 / CELL); cy <= Math.floor(y1 / CELL); cy++) {
      const cell = grid.get(`${cx},${cy}`);
      if (cell?.some((l) => l.x >= x0 && l.x <= x1 && l.y >= y0 && l.y <= y1 && !(skip && Math.hypot(l.x - skip.x, l.y - skip.y) < skipR))) return true;
    }
    return false;
  };
  // Узлы (вход, выход зоны, концы жгута, сход ветвей) — с запасом под подписи зачистки, которые рисует поле (strip-callouts.ts).
  topology.nodes.filter((n) => !n.connectorId).forEach((n) => { const at = nodeAt(n.id); taken.push({ x: at.x + 4, y: at.y - 30, width: 104, height: 26 }); });
  const leaderThrough = (box: Box) => placedLeaders.some(([a, b]) => {
    const steps = Math.max(1, Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / 4));
    for (let i = 0; i <= steps; i++) {
      const x = a.x + ((b.x - a.x) * i) / steps, y = a.y + ((b.y - a.y) * i) / steps;
      if (x > box.x - 2 && x < box.x + box.width + 2 && y > box.y - 2 && y < box.y + box.height + 2) return true;
    }
    return false;
  });
  const cross = (a: Point, b: Point, c: Point, d: Point) => {
    const o = (p: Point, q: Point, r: Point) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
    const d1 = o(a, b, c), d2 = o(a, b, d), d3 = o(c, d, a), d4 = o(c, d, b);
    return d1 * d2 < -1e-9 && d3 * d4 < -1e-9;
  };
  const distToSeg = (p: Point, a: Point, b: Point) => {
    const dx = b.x - a.x, dy = b.y - a.y, len2 = dx * dx + dy * dy || 1, t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2));
    return Math.hypot(p.x - (a.x + dx * t), p.y - (a.y + dy * t));
  };
  const near = (a: Point, b: Point, within: number) => Math.hypot(a.x - b.x, a.y - b.y) < within;
  /** Линия K → P чистая: не пересекает ветви и провода (кроме как у своей точки P), размеры, чужие линии и занятые места. */
  const clean = (k: Point, p: Point, own: string) => {
    const cut = (from: Point, to: Point) => { const len = Math.hypot(to.x - from.x, to.y - from.y) || 1, t = Math.max(0, 1 - 2 / len); return { x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t }; };
    const end = cut(k, p);
    if (branchSegs.some(([a, b]) => cross(k, end, a, b))) return false;
    // Провода идут пучком по ветви: у самой точки P (в пределах пучка) линия подходит к ним сбоку — это не пересечение.
    if (wireSegs.some(([a, b]) => cross(k, end, a, b) && distToSeg(p, a, b) > 12)) return false;
    if (placedLeaders.some(([a, b]) => cross(k, end, a, b))) return false;
    const steps = Math.max(1, Math.ceil(Math.hypot(end.x - k.x, end.y - k.y) / 6));
    for (let i = 0; i <= steps; i++) {
      const q = { x: k.x + ((end.x - k.x) * i) / steps, y: k.y + ((end.y - k.y) * i) / steps };
      if (!near(q, p, 14) && taken.some((box) => q.x > box.x && q.x < box.x + box.width && q.y > box.y && q.y < box.y + box.height)) return false;
      if (i > 1 && lineNear(q.x - 3, q.y - 3, q.x + 3, q.y + 3, p, 16)) return false;
    }
    return true;
  };
  const edgePoint = (box: Box, from: Point): Point => ({ x: Math.max(box.x, Math.min(from.x, box.x + box.width)), y: Math.max(box.y, Math.min(from.y, box.y + box.height)) });
  const groups = new Map<string, Array<(typeof callouts)[number]>>();
  callouts.forEach((callout) => groups.set(callout.text, [...(groups.get(callout.text) ?? []), callout]));
  const REACH = 230;
  const T_STEPS = [0.5, 0.35, 0.65, 0.2, 0.8, 0.1, 0.9];
  groups.forEach((all) => {
    let rest = [...all];
    while (rest.length) {
      const memberSeg = rest.map((m) => ends(m.segmentId));
      const mids = rest.map((m, i) => ({ x: memberSeg[i]![0].x + (memberSeg[i]![1].x - memberSeg[i]![0].x) * m.at, y: memberSeg[i]![0].y + (memberSeg[i]![1].y - memberSeg[i]![0].y) * m.at }));
      const cx = mids.reduce((s, p) => s + p.x, 0) / mids.length, cy = mids.reduce((s, p) => s + p.y, 0) / mids.length;
      const spread = Math.max(120, ...mids.map((p) => Math.hypot(p.x - cx, p.y - cy)));
      let best: { box: Box; text: string; picked: Array<{ index: number; p: Point }>; k: Point; cost: number } | null = null;
      const textFor = (n: number, one: (typeof callouts)[number]) => (n > 1 ? `${one.label}, ${n} ${n < 5 ? "места" : "мест"}` : one.text);
      for (let gx = cx - spread - 120; gx <= cx + spread + 120; gx += 26) {
        for (let gy = cy - spread - 100; gy <= cy + spread + 100; gy += 16) {
          // Размер подписи зависит от числа мест; оцениваем по самому длинному варианту (все места).
          // Только рядом с местами: короткие линии (дальше REACH от ближайшего места подпись не ставится).
          if (!mids.some((m) => Math.hypot(m.x - gx, m.y - gy) < REACH)) continue;
          const width = textFor(rest.length, rest[0]!).length * CHAR + 4;
          const box: Box = { x: gx, y: gy, width, height: LINE };
          if (taken.some((other) => overlaps(box, other)) || leaderThrough(box)) continue;
          // Пучок проводов шире оси участка (до 10 px в сторону) — подпись держится от линий на этот запас.
          if (lineNear(box.x - 10, box.y - 12, box.x + box.width + 10, box.y + box.height + 12)) continue;
          // Места, до которых из общей точки k на краю подписи чистая линия: по одной точке на ветви (ближайшая чистая).
          const reach = (at: Box) => {
            const k = edgePoint(at, { x: cx, y: cy });
            const found: Array<{ index: number; p: Point; len: number }> = [];
            rest.forEach((m, index) => {
              const [a, b] = memberSeg[index]!;
              let bestAnchor: { p: Point; len: number } | null = null;
              for (const t of [m.at, ...T_STEPS]) {
                const p = { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
                const len = Math.hypot(p.x - k.x, p.y - k.y);
                if (len < 4 || (bestAnchor && len >= bestAnchor.len)) continue;
                if (clean(k, p, m.segmentId)) bestAnchor = { p, len };
              }
              if (bestAnchor) found.push({ index, ...bestAnchor });
            });
            return { k, found };
          };
          // Подпись по ширине своего числа мест: сначала оценка по самой длинной, потом проверка по точной ширине.
          const rough = reach(box);
          if (!rough.found.length) continue;
          const exact: Box = { ...box, width: textFor(rough.found.length, rest[rough.found[0]!.index]!).length * CHAR + 4 };
          if (taken.some((other) => overlaps(exact, other))) continue;
          const final = reach(exact);
          if (final.found.length !== rough.found.length) continue;
          const picked = final.found, k = final.k;
          if (!picked.length) continue;
          const cost = -picked.length * 1000 + picked.reduce((s, x) => s + x.len, 0);
          if (!best || cost < best.cost) best = { box: exact, text: "", picked, k, cost };
        }
      }
      if (!best) { rest = []; break; }
      const chosen = best.picked.map((x) => x.index);
      const label = textFor(chosen.length, rest[chosen[0]!]!);
      const box = best.box;
      taken.push(box);
      best.picked.forEach(({ p }) => {
        const from = best!.k;
        placedLeaders.push([from, p]);
        graphics.push({ id: `gen-g${graphics.length + 1}`, view: "drawing", kind: "line", points: [{ x: Math.round(from.x), y: Math.round(from.y) }, { x: Math.round(p.x), y: Math.round(p.y) }], color: LEADER, width: 1 });
      });
      graphics.push({ id: `gen-g${graphics.length + 1}`, view: "drawing", kind: "text", points: [{ x: Math.round(box.x + 2), y: Math.round(box.y + LINE - 3) }], text: label, fontSize: 12, color: NOTE });
      rest = rest.filter((_, index) => !chosen.includes(index));
    }
  });

  return { showDimensions: true, dimensions, graphics, hideObjectIndices: true };
}
