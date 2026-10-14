/*
 * Раскладка чертежа «Построить чертёж» (generatorToHarness.ts) для жгута с несколькими разъёмами стороны А, сростками или
 * хвостами — как на чертеже владельца (13.10): разъёмы А колонкой слева, разъёмы В колонкой справа, жгуты от разъёмов А
 * сходятся в один ствол, сростки стоят на стволе друг за другом, хвосты уходят от своего сростка поперёк ствола, ветки к
 * разъёмам В расходятся веером от конца ствола. Подписи проводов и разъёмов разнесены так, чтобы не налезать друг на
 * друга и на подписи хвостов.
 *
 * Только раскладка: провода, сростки, хвосты и длины — те, что собрал генератор; здесь меняются места разъёмов, узлов и
 * подписей на чертеже и участки трассы между ними.
 */
import { designToScene } from "./harness/editor/HarnessDesignEditor";
import type { EditorPoint, EditorSceneObject } from "./harness/editor/editor-types";
import { isFreeEndpoint, isJunctionEndpoint, type HarnessDesignDocument, type Point, type WireEndpoint } from "./harness/editor/model";
import { physicalNodePoint } from "./harness/editor/physical-ports";
import type { PhysicalNode, PhysicalRoute, PhysicalSegment, PhysicalStep } from "./harness/editor/physical-topology-model";
import type { Side } from "./tcGenLogic";

// ── Порядок колонки В ────────────────────────────────────────────────────────────────────────────────────────────

/** Разъёмы стороны В в порядке их партнёров на стороне А (среднее место партнёров в колонке А): провода идут без
 *  перекрёстков, где это возможно. Без партнёров на А — на своём месте в конце. `links` — пары [разъём А, разъём В]. */
export function orderByPartners<T>(aKeys: readonly string[], bItems: readonly T[], keyOf: (item: T) => string,
  links: readonly (readonly [string, string])[]): T[] {
  const rank = (item: T) => {
    const places = links.filter(([, b]) => b === keyOf(item)).map(([a]) => aKeys.indexOf(a)).filter((index) => index >= 0);
    return places.length ? places.reduce((sum, index) => sum + index, 0) / places.length : Infinity;
  };
  return bItems.map((item, index) => ({ item, index, rank: rank(item) }))
    .sort((x, y) => x.rank - y.rank || x.index - y.index).map((x) => x.item);
}

// ── Ствол ───────────────────────────────────────────────────────────────────────────────────────────────────────────

/** Веер жгутов к стволу: не уже FAN_MIN, наклон крайнего не круче ~50°. */
const FAN_MIN = 120, FAN_SLOPE = 0.8;
/** Шаг сростков по стволу (между ними — место под хвост и подпись «60 мм»); ствол не короче TRUNK_MIN. */
const SPLICE_GAP = 120, TRUNK_MIN = 220;
/** Хвост на чертеже: от сростка или выхода разъёма до свободного конца; соседний хвост того же узла — веером. */
const TAIL = 80, TAIL_FAN = 28;

/** Ширина веера под разброс выходов по вертикали относительно ствола. */
export function fanWidth(offsets: readonly number[]): number {
  return Math.round(Math.max(FAN_MIN, ...offsets.map((dy) => Math.abs(dy) * FAN_SLOPE)));
}

export interface TrunkGeometry {
  /** Линия ствола по вертикали. */
  readonly trunkY: number;
  /** Начало ствола: узел схода жгутов А (несколько разъёмов А) или выход единственного разъёма А. */
  readonly startX: number;
  /** Конец ствола: узел ветвления к разъёмам В (несколько) или выход единственного разъёма В. */
  readonly endX: number;
}

/** Ствол по выходам разъёмов А и числу сростков на нём. */
export function trunkGeometry(aExits: readonly Point[], splices: number): TrunkGeometry {
  const ys = aExits.map((p) => p.y);
  const trunkY = Math.round(ys.length ? (Math.min(...ys) + Math.max(...ys)) / 2 : 0);
  const exitX = Math.max(0, ...aExits.map((p) => p.x));
  const startX = aExits.length > 1 ? exitX + fanWidth(ys.map((y) => y - trunkY)) : exitX;
  return { trunkY, startX, endX: startX + Math.max(TRUNK_MIN, (splices + 1) * SPLICE_GAP) };
}

export interface TrunkInput {
  readonly doc: HarnessDesignDocument;
  /** Узлы выходов разъёмов (и отрезков ТУТ) — дополняются узлами ствола, сростков и хвостов. */
  readonly nodes: PhysicalNode[];
  /** Отрезки ТУТ — дополняются участками ствола, вееров и хвостов. */
  readonly segments: PhysicalSegment[];
  /** Узлы выходов разъёмов А и В в порядке колонок. */
  readonly aExits: readonly string[];
  readonly bExits: readonly string[];
  readonly geometry: TrunkGeometry;
  /** Сторона разъёма чертежа (id разъёма редактора). */
  readonly sideOf: (connectorId: string) => Side | null;
  /** Узлы двойного обжима (второй провод в контакте): не сросток на стволе, а выход своего разъёма — узел трассы. */
  readonly atConnector?: ReadonlyMap<string, string>;
}

/** Сторона сростка: 0 — провода только со стороны А, 1 — с обеих, 2 — только с В (по стволу от А к В). */
function spliceRank(doc: HarnessDesignDocument, junctionId: string, sideOf: TrunkInput["sideOf"]): number {
  const junction = doc.junctions.find((j) => j.id === junctionId)!;
  const ends = doc.wires.filter((w) => junction.wireIds.includes(w.id)).flatMap((w) => [w.from, w.to])
    .filter((end) => !isJunctionEndpoint(end) && !isFreeEndpoint(end));
  const sides = new Set(ends.map((end) => sideOf(end.connectorId)).filter(Boolean));
  return sides.has("A") && sides.has("B") ? 1 : sides.has("B") ? 2 : 0;
}

/** Ствол, веера к разъёмам, сростки на стволе и хвосты; трассы проводов сростков и хвостов (провода с разъёма на разъём
 *  трассирует редактор). Меняет `nodes` и `segments`. */
export function trunkTopology(input: TrunkInput): PhysicalRoute[] {
  const { doc, nodes, segments, aExits, bExits, geometry, sideOf, atConnector = new Map<string, string>() } = input;
  const { trunkY } = geometry;
  const nodeById = (id: string) => nodes.find((n) => n.id === id)!;
  const at = (id: string) => physicalNodePoint(doc, nodeById(id));
  const straight = (from: string, to: string) => {
    let n = segments.length + 1;
    while (segments.some((s) => s.id === `gen-s${n}`)) n++;
    const segment: PhysicalSegment = { id: `gen-s${n}`, from, to, path: { kind: "polyline", points: [] } };
    segments.push(segment);
    return segment;
  };

  // Сход жгутов А в начало ствола и веер к разъёмам В от его конца — прямыми, каждый под своим углом.
  let start: string | undefined = aExits[0];
  if (aExits.length > 1) {
    start = "gen-j0";
    nodes.push({ id: start, position: { x: geometry.startX, y: trunkY } });
    aExits.forEach((exit) => straight(exit, start!));
  }
  let end: string | undefined = bExits[0];
  if (bExits.length > 1) {
    end = "gen-j1";
    nodes.push({ id: end, position: { x: geometry.endX, y: trunkY } });
  }

  // Сростки на стволе: со стороны А — ближе к А, с обеих сторон — дальше, только со стороны В — у конца ствола.
  const splices = doc.junctions.filter((j) => !atConnector.has(j.id)).map((j, index) => ({ id: j.id, index, rank: spliceRank(doc, j.id, sideOf) }))
    .sort((x, y) => x.rank - y.rank || x.index - y.index);
  const spliceNode = new Map<string, string>(atConnector);
  const outward = new Map<string, Point>();
  const fromX = start ? at(start).x : geometry.startX, toX = end ? at(end).x : geometry.endX;
  splices.forEach((sp, k) => {
    const id = `gen-n-${sp.id}`;
    spliceNode.set(sp.id, id);
    nodes.push({ id, position: { x: Math.round(fromX + ((toX - fromX) * (k + 1)) / (splices.length + 1)), y: trunkY } });
    // Хвосты — поперёк ствола, у соседних сростков в разные стороны.
    outward.set(id, { x: 0, y: k % 2 ? 1 : -1 });
  });
  const chain = [start, ...splices.map((sp) => spliceNode.get(sp.id)!), end].filter((id): id is string => !!id);
  chain.slice(1).forEach((to, k) => straight(chain[k]!, to));
  if (bExits.length > 1) bExits.forEach((exit) => straight(end!, exit));

  // Хвосты: от сростка — поперёк ствола; от выхода разъёма — наружу от ствола под 45°; несколько у одного узла — веером.
  const tipNode = new Map<string, string>();
  const fan = new Map<string, number>();
  doc.wires.forEach((wire) => {
    const free = isFreeEndpoint(wire.from) ? "from" : isFreeEndpoint(wire.to) ? "to" : null;
    if (!free) return;
    const other = free === "from" ? wire.to : wire.from;
    const base = isJunctionEndpoint(other) ? spliceNode.get(other.junctionId)
      : nodes.find((n) => n.connectorId === other.connectorId && (!n.wireIds || n.wireIds.includes(wire.id)))?.id;
    if (!base) return;
    const k = fan.get(base) ?? 0;
    fan.set(base, k + 1);
    const origin = at(base);
    const away = origin.y > trunkY ? 1 : -1;
    const dir = outward.get(base) ?? (() => {
      const along = !isJunctionEndpoint(other) && sideOf(other.connectorId) === "B" ? -1 : 1;
      return { x: along * Math.SQRT1_2, y: away * Math.SQRT1_2 };
    })();
    const spread = (k % 2 ? -1 : 1) * Math.ceil(k / 2) * TAIL_FAN;
    const id = `gen-n-${wire.id}`;
    nodes.push({ id, position: { x: Math.round(origin.x + dir.x * TAIL - dir.y * spread), y: Math.round(origin.y + dir.y * TAIL + dir.x * spread) } });
    straight(base, id);
    tipNode.set(wire.id, id);
  });

  // Трассы проводов сростков и хвостов: кратчайший путь по участкам; через выход чужого разъёма провод не идёт.
  const endNodes = (wireId: string, wireEnd: WireEndpoint) => {
    if (isJunctionEndpoint(wireEnd)) return [spliceNode.get(wireEnd.junctionId)].filter((id): id is string => !!id);
    if (isFreeEndpoint(wireEnd)) return [tipNode.get(wireId)].filter((id): id is string => !!id);
    return nodes.filter((n) => n.connectorId === wireEnd.connectorId && (!n.wireIds || n.wireIds.includes(wireId))).map((n) => n.id);
  };
  const routes: PhysicalRoute[] = [];
  doc.wires.forEach((wire) => {
    if (![wire.from, wire.to].some((e) => isJunctionEndpoint(e) || isFreeEndpoint(e))) return;
    const sources = endNodes(wire.id, wire.from), targets = new Set(endNodes(wire.id, wire.to));
    const previous = new Map<string, { node: string; step: PhysicalStep }>();
    const queue = [...sources];
    const seen = new Set(sources);
    while (queue.length) {
      const current = queue.shift()!;
      if (targets.has(current)) {
        const steps: PhysicalStep[] = [];
        for (let node = current; previous.has(node); node = previous.get(node)!.node) steps.unshift(previous.get(node)!.step);
        if (steps.length) routes.push({ wireId: wire.id, steps });
        break;
      }
      if (nodeById(current).connectorId && !sources.includes(current)) continue;
      segments.forEach((s) => {
        const next = s.from === current ? s.to : s.to === current ? s.from : null;
        if (!next || seen.has(next)) return;
        seen.add(next);
        previous.set(next, { node: current, step: { segmentId: s.id, reverse: s.to === current } });
        queue.push(next);
      });
    }
  });
  return routes;
}

// ── Подписи ─────────────────────────────────────────────────────────────────────────────────────────────────────────

export interface LabelBox { readonly id: string; readonly x: number; readonly y: number; readonly width: number; readonly height: number }

const overlap = (a: Omit<LabelBox, "id">, b: Omit<LabelBox, "id">, gap = 0) =>
  Math.max(0, Math.min(a.x + a.width, b.x + b.width) + gap - Math.max(a.x, b.x)) *
  Math.max(0, Math.min(a.y + a.height, b.y + b.height) + gap - Math.max(a.y, b.y));

/** Подпись длины на свободном конце хвоста (open-wire-ends.ts): за концом по направлению провода, шрифт 11 px. */
export function openEndLabelBoxes(scene: readonly EditorSceneObject[]): LabelBox[] {
  return scene.flatMap((object) => {
    const points = object.points ?? [];
    if (object.kind !== "wire" || points.length < 2) return [];
    return (["From", "To"] as const).flatMap((end) => {
      const label = object.metadata?.[`open${end}`];
      if (label === undefined) return [];
      const tip = end === "From" ? points[0]! : points.at(-1)!;
      const before = (end === "From" ? points.slice(1) : points.slice(0, -1).reverse()).find((p) => Math.hypot(p.x - tip.x, p.y - tip.y) > 1e-6);
      const length = before ? Math.hypot(tip.x - before.x, tip.y - before.y) : 1;
      const dir = before ? { x: (tip.x - before.x) / length, y: (tip.y - before.y) / length } : { x: 1, y: 0 };
      const width = label.length * 7 + 2, height = 14;
      const x = tip.x + dir.x * 8, y = tip.y + dir.y * 8;
      const horizontal = Math.abs(dir.x) >= Math.abs(dir.y);
      return [{
        id: `open:${object.id}:${end}`,
        x: horizontal ? (dir.x >= 0 ? x : x - width) : x - width / 2,
        y: horizontal ? y - height / 2 : dir.y > 0 ? y : y - height,
        width, height,
      }];
    });
  });
}

/** Рамки подписей позиций на чертеже (object-index): так их рисует поле (13 px, поля по 4 px). */
export function indexLabelBoxes(scene: readonly EditorSceneObject[]): LabelBox[] {
  return scene.filter((object) => object.kind === "object-index").map((object) => ({
    id: object.id, x: object.x, y: object.y, width: Math.max(object.width, object.label.length * 8.5 + 8), height: object.height,
  }));
}

const longestPath = (object: EditorSceneObject): readonly EditorPoint[] =>
  object.paths?.reduce<readonly EditorPoint[] | undefined>((longest, points) => (!longest || points.length > longest.length ? points : longest), undefined)
    ?? object.points ?? [];

/** Ближайшая к точке точка ломаной и нормаль её звена — так поле привязывает выноску подписи (drawing-indices.ts). */
function nearestOnPath(path: readonly EditorPoint[], point: EditorPoint) {
  let best: { anchor: EditorPoint; dx: number; dy: number; distance: number } | null = null;
  for (let i = 1; i < path.length; i++) {
    const a = path[i - 1]!, b = path[i]!, dx = b.x - a.x, dy = b.y - a.y, squared = dx * dx + dy * dy;
    if (!squared) continue;
    const t = Math.max(0, Math.min(1, ((point.x - a.x) * dx + (point.y - a.y) * dy) / squared));
    const anchor = { x: a.x + dx * t, y: a.y + dy * t };
    const distance = Math.hypot(point.x - anchor.x, point.y - anchor.y);
    if (!best || distance < best.distance) best = { anchor, dx, dy, distance };
  }
  return best;
}

/** Точки ломаной на долях её длины с направлением звена. */
function pathStations(path: readonly EditorPoint[], fractions: readonly number[]) {
  const lengths = path.slice(1).map((p, i) => Math.hypot(p.x - path[i]!.x, p.y - path[i]!.y));
  const total = lengths.reduce((sum, n) => sum + n, 0);
  if (!total) return [];
  return fractions.map((fraction) => {
    let remaining = total * fraction;
    for (let i = 0; i < lengths.length; i++) {
      const length = lengths[i]!;
      if (length && remaining <= length) {
        const a = path[i]!, b = path[i + 1]!;
        return { point: { x: a.x + ((b.x - a.x) * remaining) / length, y: a.y + ((b.y - a.y) * remaining) / length }, dir: { x: (b.x - a.x) / length, y: (b.y - a.y) / length } };
      }
      remaining -= length;
    }
    return null;
  }).filter((station): station is NonNullable<typeof station> => !!station);
}

/** Доли пути от середины к краям: подпись сдвигается вдоль провода, пока не найдёт свободное место. */
const FRACTIONS = [0.5, ...Array.from({ length: 9 }, (_, i) => [0.5 - (i + 1) * 0.05, 0.5 + (i + 1) * 0.05]).flat()];
/** Отступ подписи от провода и от соседних подписей. */
const LABEL_CLEAR = 6, LABEL_GAP = 3, CONNECTOR_LABEL_CLEAR = 16, NODE_ROOM = 22;
/** Наибольший угол между выноской и нормалью провода, который поле оставляет как есть (10°, drawing-indices.ts). */
const LEADER_ANGLE = Math.cos((9 * Math.PI) / 180);

/** Подписи проводов и разъёмов на чертеже без наложений: подпись провода сдвигается вдоль его трассы (по обе стороны
 *  от неё), подпись разъёма А встаёт слева от его корпуса; занятые места — подписи хвостов, узлы и корпуса разъёмов.
 *  Сдвиги пишутся в `drawingDocuments.indexOffsets` — их же редактор хранит, когда подпись двигают руками. */
export function placeDrawingLabels(document: HarnessDesignDocument, sideOf: (connectorId: string) => Side | null): HarnessDesignDocument {
  const scene = designToScene(document, "drawing");
  const owners = new Map(scene.filter((o) => o.kind !== "object-index").map((o) => [o.id, o]));
  const taken: Omit<LabelBox, "id">[] = [...openEndLabelBoxes(scene)];
  // Узел ствола или сросток — с запасом: подписи не толпятся у точки, где сходятся провода.
  scene.filter((o) => o.kind === "physical-node").forEach((o) => {
    const room = o.metadata?.nodeRole === "junction" ? NODE_ROOM : 2;
    taken.push({ x: o.x - room, y: o.y - room, width: o.width + room * 2, height: o.height + room * 2 });
  });
  scene.filter((o) => o.kind === "connector").forEach((o) => taken.push({ x: o.x, y: o.y, width: o.width, height: o.height }));
  const offsets: Record<string, Point> = { ...(document.drawingDocuments?.indexOffsets ?? {}) };
  const free = (box: Omit<LabelBox, "id">) => taken.reduce((sum, other) => sum + overlap(box, other, LABEL_GAP), 0);
  const labels = scene.filter((o) => o.kind === "object-index").map((label) => {
    const owner = owners.get(label.metadata?.indexObjectId ?? "");
    const path = owner?.kind === "wire" ? longestPath(owner) : [];
    const length = path.slice(1).reduce((sum, p, i) => sum + Math.hypot(p.x - path[i]!.x, p.y - path[i]!.y), 0);
    return { label, owner, path, length };
  });
  const connectorOf = (id: string) => document.connectors.find((c) => c.id === id);
  const movable = (x: (typeof labels)[number]) => x.owner?.kind === "wire" && x.path.length > 1 ||
    x.owner?.kind === "connector" && connectorOf(x.owner.id)?.libraryBinding?.mode !== "template" && sideOf(x.owner.id) === "A";
  // Неподвижные подписи (разъёмы В и из библиотеки, оболочки) — заняты первыми; затем разъёмы А, затем провода: короткие
  // раньше длинных (у короткого меньше мест).
  labels.filter((x) => !movable(x)).forEach(({ label }) => taken.push({ x: label.x, y: label.y, width: Math.max(label.width, label.label.length * 8.5 + 8), height: label.height }));
  const order = labels.filter(movable).sort((x, y) => Number(x.owner?.kind === "wire") - Number(y.owner?.kind === "wire") || x.length - y.length);
  for (const { label, owner, path } of order) {
    const width = Math.max(label.width, label.label.length * 8.5 + 8), height = label.height;
    const baseX = Number(label.metadata?.indexBaseX), baseY = Number(label.metadata?.indexBaseY);
    if (!owner || !Number.isFinite(baseX) || !Number.isFinite(baseY)) continue;
    const candidates: EditorPoint[] = [];
    if (owner.kind === "connector") {
      // Слева от корпуса, по середине его высоты (выноска — почти по нормали к левой стороне).
      const reach = CONNECTOR_LABEL_CLEAR + width;
      candidates.push({ x: owner.x - reach, y: owner.y + owner.height / 2 - Math.min(height / 2, reach * 0.15) });
    } else {
      for (const { point, dir } of pathStations(path, FRACTIONS)) {
        for (const sign of [1, -1]) {
          const normal = { x: -dir.y * sign, y: dir.x * sign };
          const reach = LABEL_CLEAR + (normal.y < 0 ? -normal.y * height : 0) + (normal.x < 0 ? -normal.x * width : 0);
          const position = { x: point.x + normal.x * reach, y: point.y + normal.y * reach };
          // Поле привязывает выноску к ближайшей точке трассы: место годится, только если она — эта же и по нормали.
          const near = nearestOnPath(path, position);
          if (!near) continue;
          const toLabel = { x: position.x - near.anchor.x, y: position.y - near.anchor.y }, n = Math.hypot(toLabel.x, toLabel.y) || 1;
          const segment = Math.hypot(near.dx, near.dy) || 1;
          if (Math.abs((toLabel.x * -near.dy + toLabel.y * near.dx) / (n * segment)) < LEADER_ANGLE) continue;
          candidates.push(position);
        }
      }
    }
    if (!candidates.length) continue;
    let best = candidates[0]!, cost = Infinity;
    for (const position of candidates) {
      const hit = free({ x: position.x, y: position.y, width, height });
      if (hit < cost - 1e-9) { best = position; cost = hit; }
      if (!hit) break;
    }
    taken.push({ x: best.x, y: best.y, width, height });
    offsets[owner.id] = { x: Math.round((best.x - baseX) * 100) / 100, y: Math.round((best.y - baseY) * 100) / 100 };
  }
  const docs = document.drawingDocuments ?? { tables: [], leaders: [], bomOrder: [] };
  return { ...document, drawingDocuments: { ...docs, indexOffsets: offsets } };
}
