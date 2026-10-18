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
  designToScene(doc, "drawing").filter((o) => o.kind === "connector").forEach((o) => {
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
      text({ x: tip.x + 14, y: tip.y - 32 }, `Своб. концы от ${label}`, 12);
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

  // Выноски оболочек: одинаковые на нескольких ветвях — одна выноска «Трубка ТТК 8/2, 3 места» с линиями к каждому месту (СБ).
  // Текст — на лучшем месте по цене: наложение на занятое и на линии — дорого, выноска поперёк линий — дорого, длинные
  // выноски и сторона против «наружу от ствола» — немного.
  const groups = new Map<string, Array<(typeof callouts)[number]>>();
  callouts.forEach((callout) => groups.set(callout.text, [...(groups.get(callout.text) ?? []), callout]));
  groups.forEach((members) => {
    const many = members.length > 1;
    const text = many ? `${members[0]!.label}, ${members.length} ${members.length < 5 ? "места" : "мест"}` : members[0]!.text;
    const width = text.length * CHAR + 4;
    const geometry = members.map((m) => {
      const [a, b] = ends(m.segmentId);
      const length = Math.hypot(b.x - a.x, b.y - a.y) || 1, dir = { x: (b.x - a.x) / length, y: (b.y - a.y) / length };
      const at = (t: number) => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
      const away = Math.abs(at(m.at).y - trunkY) > 8 ? Math.sign(at(m.at).y - trunkY) : -1;
      let normal = { x: -dir.y, y: dir.x };
      if (Math.abs(normal.y) < 0.2 ? normal.x < 0 : normal.y * away < 0) normal = { x: -normal.x, y: -normal.y };
      return { at, normal, t0: m.at };
    });
    const nearOf = (box: Box, from: Point) => ({ x: Math.max(box.x, Math.min(from.x, box.x + box.width)), y: from.y < box.y ? box.y : from.y > box.y + box.height ? box.y + box.height : from.y });
    const legCost = (box: Box, from: Point) => {
      const near = nearOf(box, from);
      let crossings = 0;
      const steps = Math.max(1, Math.ceil(Math.hypot(near.x - from.x, near.y - from.y) / 5));
      for (let i = 3; i <= steps; i++) {
        const p = { x: from.x + ((near.x - from.x) * i) / steps, y: from.y + ((near.y - from.y) * i) / steps };
        if (lines.some((l) => Math.hypot(l.x - p.x, l.y - p.y) < 5 && Math.hypot(l.x - from.x, l.y - from.y) > 12)) crossings++;
      }
      return crossings * 25 + Math.hypot(near.x - from.x, near.y - from.y) * 0.15;
    };
    let best: { box: Box; cost: number } | null = null;
    geometry.forEach((g, primary) => {
      for (const flip of [1, -1]) {
        for (const d of [20, 34, 50, 68, 90, 116]) {
          for (const shift of [0, 0.1, -0.1, 0.2, -0.2, 0.3, -0.3, 0.4, -0.4]) {
            const t = Math.min(0.97, Math.max(0.03, g.t0 + shift));
            const from = g.at(t), nx = g.normal.x * flip, ny = g.normal.y * flip, q = { x: from.x + nx * d, y: from.y + ny * d };
            const box: Box = { x: nx < -0.35 ? q.x - width : nx < 0.35 ? q.x - width / 2 : q.x, y: ny > 0.35 ? q.y : ny < -0.35 ? q.y - LINE : q.y - LINE / 2, width, height: LINE };
            let cost = taken.filter((other) => overlaps(box, other)).length * 100 +
              lines.filter((p) => p.x > box.x - 3 && p.x < box.x + box.width + 3 && p.y > box.y - 3 && p.y < box.y + box.height + 3).length * 3 +
              Math.abs(shift) * 30 + (flip < 0 ? 12 : 0) + (primary > 0 ? 6 : 0);
            geometry.forEach((other, i) => { cost += legCost(box, other.at(i === primary ? t : other.t0)); });
            if (!best || cost < best.cost) best = { box, cost };
          }
        }
      }
    });
    if (!best) return;
    const { box } = best as { box: Box };
    taken.push(box);
    geometry.forEach((g) => {
      const from = g.at(g.t0), near = nearOf(box, from);
      graphics.push({ id: `gen-g${graphics.length + 1}`, view: "drawing", kind: "line", points: [{ x: Math.round(from.x), y: Math.round(from.y) }, { x: Math.round(near.x), y: Math.round(near.y) }],
        color: LEADER, width: 1 });
    });
    graphics.push({ id: `gen-g${graphics.length + 1}`, view: "drawing", kind: "text", points: [{ x: Math.round(box.x + 2), y: Math.round(box.y + LINE - 3) }], text, fontSize: 12, color: NOTE });
  });

  return { showDimensions: true, dimensions, graphics, hideObjectIndices: true };
}
