/* Холст маршрута (владелец 12.10: «наше, конечно, далековато от идеала» рядом с «Маршрутом v3» Василия): карточки
   физических полуфабрикатов вместо цепочки операций. Здесь — чистая логика: раскладка рядами по глубине (алгоритм
   Василия из generateRouteV3, TECHMAP-GRAPHER), геометрия связей и карточки «Схемы полуфабрикатов» генератора,
   выведенные из её операций. Рисует RouteCanvas.tsx; маршрут чертежа позиции — harnessRouteLogic.ts. */

import type { ConnectorHousing } from "./connectorArt";
import type { WireBlankEnd } from "./harness/WireBlankCatalog";
import type { FreeSide, PinoutEnd, PinUnit } from "./pinoutCheck";
import type { Route, RouteEdge, RouteOp } from "./routeLogic";
import type { OpSpec } from "./tcGenLogic";

export type CardKind = "semiFinished" | "assembly" | "final";
/** Маленький ярлык вида над названием карточки. */
export const CARD_KIND_LABEL: Record<CardKind, string> = { semiFinished: "ПОЛУФАБРИКАТ", assembly: "СБОРКА", final: "ФИНАЛ" };
/** Вид карточки словами — в списке выбора панели настройки. */
export const CARD_KIND_NAME: Record<CardKind, string> = { semiFinished: "Полуфабрикат", assembly: "Сборка", final: "Финал — готовое изделие" };

export type Point = { x: number; y: number };
export type Size = { width: number; height: number };
export type Rect = Point & Size;
export type Spacing = { gapX: number; gapY: number; margin: number };
export type LayoutLink = { from: string; to: string };

/** Карточка и шаг Василия: 280 × 264, по горизонтали через 304, по вертикали через 328, поле 32. */
export const V3_CARD: Size = { width: 280, height: 264 };
export const V3_SPACING: Spacing = { gapX: 24, gapY: 64, margin: 32 };

/** Глубина карточки (1 + наибольшая глубина входов) и порядок обхода: топологический с сохранением порядка списка. */
export function depthOrder(ids: readonly string[], links: readonly LayoutLink[]): { depth: Map<string, number>; order: string[] } {
  const known = new Set(ids);
  const parents = new Map(ids.map((id) => [id, [...new Set(links.filter((link) => link.to === id && link.from !== id && known.has(link.from)).map((link) => link.from))]]));
  const depth = new Map<string, number>();
  let rest = [...ids];
  const order: string[] = [];
  while (rest.length) {
    const next = rest.find((id) => parents.get(id)!.every((parent) => depth.has(parent))) ?? rest[0];
    const placed = parents.get(next)!.filter((parent) => depth.has(parent));
    depth.set(next, placed.length ? 1 + Math.max(...placed.map((parent) => depth.get(parent)!)) : 0);
    order.push(next);
    rest = rest.filter((id) => id !== next);
  }
  return { depth, order };
}

/** Сколько карточек в самом широком ряду. */
export function widestRow(ids: readonly string[], links: readonly LayoutLink[]): number {
  const counts = new Map<number, number>();
  for (const value of depthOrder(ids, links).depth.values()) counts.set(value, (counts.get(value) ?? 0) + 1);
  return Math.max(1, ...counts.values());
}

/** Раскладка Василия (generateRouteV3 → addNode): глубина карточки — 1 + наибольшая глубина её входов, в ряду
    глубины карточки встают слева направо в порядке списка; x = поле + колонка · (ширина + зазор), y — ряды сверху вниз.
    С карточками 280 × 264 это ровно его 32 + колонка · 304 и 32 + глубина · 328. Высота ряда — самая высокая карточка
    ряда (у генератора карточки разной высоты). Порядок — топологический с сохранением порядка списка; цикл не
    роняет раскладку — карточка цикла встаёт по порядку списка. */
export function layoutByDepth(ids: readonly string[], links: readonly LayoutLink[], size: (id: string) => Size = () => V3_CARD,
  spacing: Spacing = V3_SPACING): Map<string, Point> {
  const { depth, order } = depthOrder(ids, links);
  const rows = Math.max(0, ...[...depth.values()].map((value) => value + 1));
  const rowHeight = Array.from({ length: rows }, () => 0);
  for (const id of order) rowHeight[depth.get(id)!] = Math.max(rowHeight[depth.get(id)!], size(id).height);
  const rowTop: number[] = [];
  rowHeight.reduce((top, height, row) => { rowTop[row] = top; return top + height + spacing.gapY; }, spacing.margin);
  const rowX = Array.from({ length: rows }, () => spacing.margin);
  const result = new Map<string, Point>();
  for (const id of order) {
    const row = depth.get(id)!;
    result.set(id, { x: rowX[row], y: rowTop[row] });
    rowX[row] += size(id).width + spacing.gapX;
  }
  return result;
}

/** Размер поля под карточками: правый и нижний край самой дальней карточки плюс поле. */
export function contentSize(rects: readonly Rect[], margin = V3_SPACING.margin, minimum: Size = { width: 320, height: 200 }): Size {
  return {
    width: Math.max(minimum.width, ...rects.map((rect) => rect.x + rect.width + margin)),
    height: Math.max(minimum.height, ...rects.map((rect) => rect.y + rect.height + margin)),
  };
}

const r2 = (value: number) => Math.round(value * 10) / 10;

const LANE_GAP = 14;
const hitsRect = (x1: number, y1: number, x2: number, y2: number, rect: Rect) =>
  Math.max(x1, x2) > rect.x - 2 && Math.min(x1, x2) < rect.x + rect.width + 2
  && Math.max(y1, y2) > rect.y - 2 && Math.min(y1, y2) < rect.y + rect.height + 2;

/** Связь, что шла бы прямо под чужой карточкой, обходит её сбоку («Обжатые провода → Готовый кабель» под «Сторона А
    собрана» был не виден): вниз из входа, в сторону до полосы у края закрывающих карточек, вниз вдоль неё, к цели. */
function detourGeometry(sx: number, sy: number, tx: number, ty: number, obstacles: readonly Rect[]): { d: string; mid: Point } | null {
  const my = ty - Math.min(32, (ty - sy) / 2);
  const crossed = (rect: Rect) => hitsRect(sx, sy, sx, my, rect) || hitsRect(sx, my, tx, my, rect) || hitsRect(tx, my, tx, ty, rect);
  const inSpan = obstacles.filter((rect) => rect.y < ty && rect.y + rect.height > sy);
  let blockers = inSpan.filter(crossed);
  if (blockers.length === 0) return null;
  let lane = sx;
  for (let pass = 0; pass < 6; pass += 1) {
    const left = Math.min(...blockers.map((rect) => rect.x)) - LANE_GAP;
    const right = Math.max(...blockers.map((rect) => rect.x + rect.width)) + LANE_GAP;
    lane = left < LANE_GAP || Math.abs(sx - right) + Math.abs(tx - right) <= Math.abs(sx - left) + Math.abs(tx - left) ? right : left;
    const more = inSpan.filter((rect) => !blockers.includes(rect) && hitsRect(lane, sy, lane, ty, rect));
    if (more.length === 0) break;
    blockers = [...blockers, ...more];
  }
  const y1 = sy + 12, y2 = ty - 12;
  const points: Point[] = [{ x: sx, y: sy }, { x: sx, y: y1 }, { x: lane, y: y1 }, { x: lane, y: y2 }, { x: tx, y: y2 }, { x: tx, y: ty }];
  let d = `M${r2(sx)},${r2(sy)}`;
  for (let i = 1; i < points.length - 1; i += 1) {
    const a = points[i - 1], b = points[i], c = points[i + 1];
    const radius = Math.min(6, (Math.abs(b.x - a.x) + Math.abs(b.y - a.y)) / 2, (Math.abs(c.x - b.x) + Math.abs(c.y - b.y)) / 2);
    if (radius <= 0) continue;
    const unit = (p: Point, q: Point) => ({ x: Math.sign(q.x - p.x), y: Math.sign(q.y - p.y) });
    const u = unit(b, a), v = unit(b, c);
    d += ` L${r2(b.x + u.x * radius)},${r2(b.y + u.y * radius)} Q${r2(b.x)},${r2(b.y)} ${r2(b.x + v.x * radius)},${r2(b.y + v.y * radius)}`;
  }
  d += ` L${r2(tx)},${r2(ty)}`;
  return { d, mid: { x: lane, y: (sy + ty) / 2 } };
}

/** Связь от низа карточки-входа к верху карточки, что её берёт. Цель ниже — ортогональная линия со скруглёнными
    углами: вниз, вдоль зазора над целью, вниз; строго под входом — прямая. Цель выше или рядом (карточку утащили) —
    плавная S-кривая. `mid` — где стоят число штук и кнопки «+» / «×». */
export function linkGeometry(from: Rect, to: Rect, obstacles: readonly Rect[] = []): { d: string; mid: Point } {
  const sx = from.x + from.width / 2, sy = from.y + from.height, tx = to.x + to.width / 2, ty = to.y;
  if (ty - sy >= 24) {
    const around = detourGeometry(sx, sy, tx, ty, obstacles);
    if (around) return around;
    if (Math.abs(sx - tx) < 1) return { d: `M${r2(sx)},${r2(sy)} V${r2(ty)}`, mid: { x: sx, y: (sy + ty) / 2 } };
    const my = ty - Math.min(32, (ty - sy) / 2);
    const dir = tx > sx ? 1 : -1;
    const radius = Math.min(8, Math.abs(tx - sx) / 2, my - sy, ty - my);
    const d = `M${r2(sx)},${r2(sy)} V${r2(my - radius)} Q${r2(sx)},${r2(my)} ${r2(sx + dir * radius)},${r2(my)}`
      + ` H${r2(tx - dir * radius)} Q${r2(tx)},${r2(my)} ${r2(tx)},${r2(my + radius)} V${r2(ty)}`;
    return { d, mid: { x: (sx + tx) / 2, y: my } };
  }
  const bend = Math.max(48, (sy - ty) / 2 + 48);
  return { d: `M${r2(sx)},${r2(sy)} C${r2(sx)},${r2(sy + bend)} ${r2(tx)},${r2(ty - bend)} ${r2(tx)},${r2(ty)}`, mid: { x: (sx + tx) / 2, y: (sy + ty) / 2 } };
}

/* ── «Схема полуфабрикатов» генератора: карточки из операций ─────────────────────────────────────────────────── */

/** Полуфабрикат генератора: нарезанные провода, нарезанные трубки, обжатые провода (до первой стороны), обжим
    стороны после сборки другой (`crimpedA` / `crimpedB`: резка, обжим А, сторона А, обжим В, сторона В — владелец
    14.10), собранные стороны А и В, готовое изделие. */
export type GenStage = "wires" | "tubes" | "crimped" | "crimpedA" | "A" | "crimpedB" | "B" | "final";
const STAGES: GenStage[] = ["wires", "tubes", "crimped", "crimpedA", "A", "crimpedB", "B", "final"];
/** Карточки обжатых проводов. */
export const CRIMP_STAGES: ReadonlySet<GenStage> = new Set<GenStage>(["crimped", "crimpedA", "crimpedB"]);
const WIRE_CUTS = new Set(["cutWire", "cutWireStrip", "cutWireCrimp", "coaxCut"]);
const TUBE_CUTS = new Set(["cutTut", "coaxCutTut"]);
/** Разделка коаксиала отдельной операцией готовит нарезанный кабель (владелец 14.10: «зачистка идёт до всего остального»),
    а не сторону: резка, разделка А, разделка В — один полуфабрикат с резкой. */
const COAX_STRIPS = new Set(["coaxStrip"]);
/** Резка коаксиала с разделкой на роторном станке (CUT_COAX+1strip / +2strip). */
const COAX_CUT_STRIP = /^CUT_COAX\+\d*STRIP/;
/** Обжим концов руками: терминалы и наконечники (и двойной обжим — та же операция), сростки. */
const END_CRIMPS = new Set(["prsTerm", "splice"]);
/** Пайка центрального контакта коаксиала — двойник обжима терминала: перед монтажом корпуса своей стороны это подготовка
    конца (796К.2 КБ1, 15.10: пайка А, пайка В, потом корпуса — пайка В в «Стороне В собрана» ломала порядок работы).
    Угловой контакт, паянный внутри корпуса (после него у стороны ничего), — сборка самой стороны. */
const CONTACT_SOLDERS = new Set(["coaxPin"]);

/** Операция готовит конец провода (владелец 13.10: «а почему тут нет полуфабриката обжатых проводов»): зачистка и
    разделка кабеля под разъём, складывание жилы, лужение, обжим терминала / наконечника, сростки с их трубкой и
    усадкой, ТУТ на провод перед пайкой. Скрутка экрана, монтаж в разъём, пайка разъёма и всё после — у стороны. */
function wireEndPrep(op: RouteOp): boolean {
  if (END_CRIMPS.has(op.type) || op.type === "tin" || op.type === "foldCore") return true;
  if (op.type === "stripCable") return !op.key.startsWith("shield");
  return op.key === "insTut" || op.key === "insSpl" || op.key === "heatSpl";
}

function cutStage(op: RouteOp): GenStage | null {
  const code = op.code.toUpperCase();
  if (TUBE_CUTS.has(op.type) || code.startsWith("CUT_TUT")) return "tubes";
  return WIRE_CUTS.has(op.type) || COAX_STRIPS.has(op.type) || code.startsWith("CUT_") ? "wires" : null;
}

/** Полуфабрикат каждой операции (по номеру в ЭТК): резка проводов и кабеля и разделка коаксиала — «Нарезанные провода»
    («Нарезанный и разделанный кабель»), резка ТУТ и
    термоусадки — «Нарезанные трубки»; подготовка концов стороны до последней операции сторон — обжим этой стороны
    (`crimpedA` / `crimpedB`, прямо перед стороной: резка, обжим А, сторона А, обжим В, сторона В — владелец 14.10),
    если среди неё есть обжим руками (обжато всё на станке резки — отдельной карточки нет, зачистка и лужение остаются
    у стороны); подготовка без стороны до первой стороны — общие «Провода со сростками» (`crimped`), если в ней есть
    сростки, иначе она готовит следующий обжим или сторону; операция стороны — её сторона; операция без стороны
    (свивка, маркировка, обмотка) готовит ближайший следующий обжим или сторону, а после последней стороны — готовое
    изделие (лужение свободных концов в конце — тоже). Зеркало backend `techcards/domain/pf_split.py` `op_stages`. */
export function opStages(ops: readonly RouteOp[]): Map<number, GenStage> {
  const sorted = [...ops].sort((a, b) => a.position - b.position);
  const assembly = (op: RouteOp) => !!op.side && !cutStage(op) && !wireEndPrep(op) && !CONTACT_SOLDERS.has(op.type);
  const pins = new Set(sorted.filter((op) => CONTACT_SOLDERS.has(op.type) && op.side
    && sorted.some((other) => other.position > op.position && other.side === op.side && assembly(other))).map((op) => op.position));
  const endPrep = (op: RouteOp) => wireEndPrep(op) || pins.has(op.position);
  const sided = sorted.filter((op) => op.side && !cutStage(op) && !endPrep(op)).map((op) => op.position);
  const firstSide = Math.min(Infinity, ...sided), lastSide = Math.max(-Infinity, ...sided);
  const prep = sorted.filter((op) => !cutStage(op) && endPrep(op) && op.position < lastSide);
  const crimped = new Map<number, GenStage>();
  const group = (own: RouteOp[], stage: GenStage) => {
    if (own.some((op) => END_CRIMPS.has(op.type) || pins.has(op.position))) own.forEach((op) => crimped.set(op.position, stage));
  };
  group(prep.filter((op) => op.side === "A"), "crimpedA");
  group(prep.filter((op) => op.side === "B"), "crimpedB");
  group(prep.filter((op) => !op.side && op.position < firstSide), "crimped");
  const result = new Map<number, GenStage>();
  let waiting: number[] = [];
  for (const op of sorted) {
    const stage: GenStage | null = cutStage(op) ?? crimped.get(op.position) ?? op.side ?? null;
    if (stage && stage !== "wires" && stage !== "tubes") { waiting.forEach((position) => result.set(position, stage)); waiting = []; }
    if (stage) result.set(op.position, stage); else waiting.push(op.position);
  }
  waiting.forEach((position) => result.set(position, "final"));
  return result;
}

export type GenCard = {
  id: GenStage; kind: CardKind; title: string;
  /** Блоки схемы (ЭТК), из которых собрана карточка, — по порядку. */
  blocks: string[];
  /** Операции карточки по порядку в ЭТК. */
  ops: RouteOp[];
  final: boolean;
};
export type GenLink = { id: string; from: GenStage; to: GenStage; qty: number | null; edges: RouteEdge[] };

function genTitle(stage: GenStage, final: boolean, ops: RouteOp[]): string {
  if (stage === "wires") {
    if (ops.length && ops.every((op) => op.type === "coaxCut" || COAX_STRIPS.has(op.type))) {
      const stripped = ops.some((op) => COAX_STRIPS.has(op.type) || COAX_CUT_STRIP.test(op.code.toUpperCase()));
      return stripped ? "Нарезанный и разделанный кабель" : "Нарезанный кабель";
    }
    return ops.some((op) => op.type === "cutWireCrimp") ? "Нарезанные и обжатые провода" : "Нарезанные провода";
  }
  if (stage === "tubes") return "Нарезанные трубки";
  if (CRIMP_STAGES.has(stage) && !final) {
    if (ops.length && ops.every((op) => CONTACT_SOLDERS.has(op.type))) {
      return `Кабель с центральным контактом${stage === "crimpedA" ? ", сторона А" : stage === "crimpedB" ? ", сторона В" : ""}`;
    }
    if (!ops.some((op) => op.type === "prsTerm")) return "Провода со сростками";
    return stage === "crimpedA" ? "Обжатые провода, сторона А" : stage === "crimpedB" ? "Обжатые провода, сторона В" : "Обжатые провода";
  }
  if (final) return "Готовый кабель";
  return stage === "A" ? "Сторона А собрана" : stage === "B" ? "Сторона В собрана" : "Отделка";
}

/** Карточки схемы генератора поверх её блоков (схема ЭТК не меняется): блок — в карточке полуфабриката своей последней
    операции; карточка с итоговым блоком — «Готовый кабель» (ФИНАЛ). Карточки — по ходу сборки (первая операция в
    ЭТК), финал последним. Связь карточек — стрелки блоков между ними; стрелка назад по ходу сборки (коаксиал:
    разделка А, разделка В, пайка А…) карточек не связывает; сторона А идёт в обжим стороны В после неё (провода
    проходят через сторону), «Обжатые провода» до первой стороны всегда идут из нарезанных, обжим стороны без
    входа — тоже. Число штук на связи — когда под ней одна стрелка (или все с одним числом). */
export function genCards(route: Route, ops: readonly RouteOp[]): { cards: GenCard[]; links: GenLink[]; cardOf: Map<string, GenStage> } {
  const stages = opStages(ops);
  const byPosition = new Map(ops.map((op) => [op.position, op]));
  const cardOf = new Map<string, GenStage>();
  for (const block of route.blocks) {
    const last = block.ops.length ? Math.max(...block.ops) : null;
    cardOf.set(block.key, last === null ? "final" : stages.get(last) ?? "final");
  }
  const present = STAGES.filter((stage) => [...cardOf.values()].includes(stage));
  const finalBlock = route.blocks.find((block) => block.final);
  const finalStage = finalBlock ? cardOf.get(finalBlock.key)! : present[present.length - 1];
  const cards = present.map((stage): GenCard => {
    const blocks = route.blocks.filter((block) => cardOf.get(block.key) === stage);
    const cardOps = blocks.flatMap((block) => block.ops).sort((a, b) => a - b)
      .map((position) => byPosition.get(position)).filter((op): op is RouteOp => !!op);
    const final = stage === finalStage;
    return {
      id: stage, kind: final ? "final" : stage === "wires" || stage === "tubes" || CRIMP_STAGES.has(stage) ? "semiFinished" : "assembly",
      title: genTitle(stage, final, cardOps), blocks: blocks.map((block) => block.key), ops: cardOps, final,
    };
  });
  // Ход сборки — по первой операции карточки в ЭТК (технолог мог поставить сторону В раньше А); финал — последний.
  const first = new Map(cards.map((card) => [card.id, card.final ? Infinity : Math.min(...card.ops.map((op) => op.position), Infinity)]));
  // Обжим стороны — прямо перед ней, даже если операция стороны (скрутка экрана) стоит в ЭТК раньше обжима.
  for (const [crimp, side] of [["crimpedA", "A"], ["crimpedB", "B"]] as const) {
    if (first.has(crimp) && first.has(side) && first.get(crimp)! > first.get(side)!) first.set(crimp, first.get(side)! - 0.5);
  }
  cards.sort((a, b) => first.get(a.id)! - first.get(b.id)! || STAGES.indexOf(a.id) - STAGES.indexOf(b.id));
  const rank = (stage: GenStage) => first.get(stage) ?? Infinity;
  const grouped = new Map<string, GenLink>();
  const add = (from: GenStage, to: GenStage, edge: RouteEdge) => {
    const id = `${from}>${to}`;
    const link = grouped.get(id) ?? { id, from, to, qty: null, edges: [] };
    if (!link.edges.includes(edge)) link.edges.push(edge);
    grouped.set(id, link);
  };
  for (const edge of route.edges) {
    const from = cardOf.get(edge.from), to = cardOf.get(edge.to);
    if (from && to && from !== to && rank(from) <= rank(to)) add(from, to, edge);
  }
  // Обжатые провода — это нарезанные: до первой стороны без стрелки из нарезанных или трубок, обжим стороны вовсе без
  // входа — связь из нарезанных без стрелки ЭТК, по одной штуке.
  const implied: GenLink[] = present.includes("wires") ? present.filter((stage) => CRIMP_STAGES.has(stage) && stage !== finalStage
    && rank("wires") <= rank(stage)
    && !(stage === "crimped" ? [...grouped.values()].some((link) => link.to === stage && (link.from === "wires" || link.from === "tubes"))
      : [...grouped.values()].some((link) => link.to === stage)))
    .map((stage) => ({ id: `wires>${stage}`, from: "wires" as const, to: stage, qty: 1, edges: [] })) : [];
  const links = [...implied, ...[...grouped.values()].map((link) => ({ ...link, qty: link.edges.every((edge) => edge.per_unit === link.edges[0].per_unit) ? link.edges[0].per_unit : null }))];
  return { cards, links, cardOf };
}

/** Минуты карточки на одну штуку изделия: нормы её операций на партию / партию; ни у одной нормы нет — null. */
export function opsMinutes(ops: readonly RouteOp[], quantity: number): number | null {
  const timed = ops.filter((op) => op.minutes != null);
  if (!timed.length) return null;
  return timed.reduce((sum, op) => sum + (op.minutes ?? 0), 0) / (quantity > 0 ? quantity : 1);
}

/* ── Рисунки карточек генератора ──────────────────────────────────────────────────────────────────────────────── */

/** Нарезанный провод: «А1:1 → В1:1», цвет жилы и концы после резки (медь, наконечник, лужение). */
export type WireThumb = { key: string; label: string; title: string; color: string; left: WireBlankEnd; right: WireBlankEnd;
  /** Кабель в оболочке (владелец 15.10: «визуал полуфабриката неправильный» — четыре провода вместо кабеля): один кабель,
   *  жилы веером из оболочки с концами `left`/`right`; `label` — марка, `sub` — «4 жилы · А1 → В1». */
  cable?: { cores: Array<{ color: string; label: string }>; sub: string } };
/** Торец разъёма стороны: корпус из базы разъёмов (нет — подпись), цвета проводов по контактам. */
export type FaceThumb = { key: string; label: string; housing: ConnectorHousing | null; byPin: Record<number, string> | null };
export type TubeThumb = { key: string; label: string; count: number };
/** `crimped` — провода с концами после ручного обжима (наконечник у обжатого конца) — у «Обжатых проводов»;
    `crimpedBy` — то же по стороне обжима (провода, обжатые руками на стороне, наконечник только у её конца) — у
    карточки обжима одной стороны. */
export type GenArt = {
  wires: WireThumb[]; tubes: TubeThumb[]; crimped: WireThumb[]; crimpedBy?: { A: WireThumb[]; B: WireThumb[] };
  faces: { A: FaceThumb[]; B: FaceThumb[] };
};
export const EMPTY_ART: GenArt = { wires: [], tubes: [], crimped: [], crimpedBy: { A: [], B: [] }, faces: { A: [], B: [] } };

/** Провода на рисунке карточки обжатых проводов: обжим стороны — провода этой стороны, иначе все обжатые. */
export function crimpWires(stage: GenStage, art: GenArt): WireThumb[] {
  const side = stage === "crimpedA" ? "A" : stage === "crimpedB" ? "B" : null;
  return side ? art.crimpedBy?.[side] ?? art.crimped : art.crimped;
}

/** Сколько строк рисунка видно в карточке: остальное — «и ещё N». */
export const ART_ROWS = 4;
export const GEN_CARD = { width: 248, head: 64, opRow: 22, pad: 20, artGap: 8 } as const;
export const GEN_SPACING: Spacing = { gapX: 16, gapY: 40, margin: 4 };

/** Высота рисунка карточки генератора, px: провода и трубки — по строке (не больше ART_ROWS и строка «и ещё»),
    торцы разъёмов — полоса 72 px; у готового изделия — оба торца с кабелем между ними. */
/** Строка кабеля в оболочке на рисунке карточки, px (жилы веером — выше строки провода). */
export const CABLE_ROW = 38;
export function genArtHeight(stage: GenStage, art: GenArt, final: boolean): number {
  const rows = (count: number, row: number) => (count ? Math.min(count, ART_ROWS) * row + (count > ART_ROWS ? 16 : 0) + 8 : 0);
  const wireRows = (list: WireThumb[]) => (list.length
    ? list.slice(0, ART_ROWS).reduce((h, w) => h + (w.cable ? CABLE_ROW : 22), 0) + (list.length > ART_ROWS ? 16 : 0) + 8 : 0);
  if (stage === "wires") return wireRows(art.wires);
  if (CRIMP_STAGES.has(stage) && !final) return wireRows(crimpWires(stage, art));
  if (stage === "tubes") return rows(art.tubes.length, 22);
  if (final) return art.faces.A.length || art.faces.B.length ? 72 : 0;
  return art.faces[stage === "B" ? "B" : "A"].length ? 72 : 0;
}

/** Высота карточки генератора: шапка, рисунок, строка на операцию (на планшете строка под палец — 40 px). */
export function genCardHeight(ops: number, artHeight: number, opRow: number = GEN_CARD.opRow): number {
  return GEN_CARD.head + (artHeight ? artHeight + GEN_CARD.artGap : 0) + ops * opRow + GEN_CARD.pad;
}

/** Концы нарезанного провода по операции резки: обжим стороны — наконечник, разделка — медь, лужение свободного конца
    тем же станком — олово; без операции резки — «после резки». */
export function cutEnds(cut: { type: string; side?: "A" | "B"; both?: boolean; strip?: { a: boolean; b: boolean }; tin?: boolean } | null): { left: WireBlankEnd; right: WireBlankEnd } {
  if (!cut) return { left: "cut", right: "cut" };
  const stripped = (on: boolean | undefined): WireBlankEnd => (on ? "copper" : "cut");
  if (cut.type === "cutWireCrimp") {
    const a: WireBlankEnd = cut.both || cut.side !== "B" ? "terminal" : cut.tin ? "tin" : stripped(cut.strip?.a);
    const b: WireBlankEnd = cut.both || cut.side === "B" ? "terminal" : cut.tin ? "tin" : stripped(cut.strip?.b);
    return { left: a, right: b };
  }
  if (cut.type === "cutWireStrip") return { left: stripped(cut.strip?.a ?? true), right: stripped(cut.strip?.b ?? true) };
  return { left: "cut", right: "cut" };
}

const CUT_TYPES = new Set(["cutWire", "cutWireStrip", "cutWireCrimp"]);

/** Кабель в оболочке на рисунке: строка «Кабели» (`idx`), марка и жил в одном кабеле. */
export type ArtCable = { idx: number; mark: string; cores: number };

export const coresWord = (n: number) => (n % 10 === 1 && n % 100 !== 11 ? "жила" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? "жилы" : "жил");

/** Рисунки карточек генератора из формы: провода — по распиновке (контакты разъёмов сторон, цвет жилы, длина) с
    концами по их операции резки; жилы кабеля в оболочке (`cables`, жил > 1) — одним кабелем на штуку; обжатые — те же провода с наконечниками после ручной опрессовки; трубки — операции
    резки ТУТ; торцы — разъёмы сторон из базы разъёмов с цветами
    проводов по контактам. Сторона без разъёма — «своб. конец». */
export function schemeArt(specs: readonly OpSpec[], ends: readonly PinoutEnd[], units: readonly PinUnit[],
  housings: Record<string, ConnectorHousing | null>, free: FreeSide | null, cables: readonly ArtCable[] = []): GenArt {
  const keyOf = new Map(ends.map((end) => [end.id, end.key]));
  const sideOf = new Map(ends.map((end) => [end.id, end.side]));
  const cuts = specs.filter((spec) => CUT_TYPES.has(spec.type));
  const single = units.map((unit): WireThumb => {
    const at = Object.entries(unit.at).map(([id, pin]) => `${keyOf.get(id) ?? "?"}:${pin ?? "?"}`);
    const label = !at.length ? unit.name || "провод"
      : free ? (free.side === "B" ? `${at[0]} → своб. конец` : `своб. конец → ${at[0]}`) : at.join(" → ");
    const cut = cuts.find((spec) => spec.wireIdxs?.includes(unit.idx)) ?? null;
    return {
      key: unit.key, label, title: `${unit.num} · ${unit.name || "провод"}${unit.length ? ` · ${unit.length} мм` : ""}`,
      color: unit.color ?? "#6b7c86",
      ...cutEnds(cut ? { type: cut.type, side: cut.side, both: !!cut.crimpEpIdB, strip: cut.strip, tin: cut.tin } : null),
    };
  });
  // Жилы одного кабеля в оболочке — одна строка: штука кабеля k — жилы k·N … k·N+N−1 строки «Кабели».
  const cableOf = new Map(cables.filter((c) => c.cores > 1).map((c) => [c.idx, c]));
  const items: Array<{ thumb: WireThumb; units: PinUnit[] }> = [];
  const pieces = new Map<string, { thumb: WireThumb; units: PinUnit[] }>();
  units.forEach((unit, index) => {
    const cable = cableOf.get(unit.idx);
    if (!cable) { items.push({ thumb: single[index], units: [unit] }); return; }
    const piece = `${unit.idx}:${Math.floor(unit.unit / cable.cores)}`;
    const core = { color: unit.color ?? "#6b7c86", label: `${unit.num}${unit.colorName ? ` ${unit.colorName}` : ""} · ${single[index].label}` };
    const have = pieces.get(piece);
    if (have) { have.units.push(unit); have.thumb.cable!.cores.push(core); return; }
    const item = { thumb: { ...single[index], key: `cable-${piece}`, label: cable.mark || unit.name || "кабель", cable: { cores: [core], sub: "" } }, units: [unit] };
    pieces.set(piece, item);
    items.push(item);
  });
  pieces.forEach(({ thumb, units: own }) => {
    const n = own.length;
    const keysOn = (side: "A" | "B") => [...new Set(own.flatMap((u) => Object.keys(u.at).filter((id) => sideOf.get(id) === side).map((id) => keyOf.get(id) ?? "?")))].join(", ");
    const a = keysOn("A"), b = keysOn("B");
    const route = a && b ? `${a} → ${b}` : a ? (free ? `${a} → своб. концы` : a) : b ? (free ? `своб. концы → ${b}` : b) : "";
    thumb.cable!.sub = `${n} ${coresWord(n)}${route ? ` · ${route}` : ""}`;
    thumb.title = [`${thumb.label} · ${n} ${coresWord(n)}${own[0].length ? ` · ${own[0].length} мм` : ""}`, ...thumb.cable!.cores.map((c) => c.label)].join("\n");
  });
  const wires = items.map((item) => item.thumb);
  // Обжатые провода: конец, заведённый в разъём с ручной опрессовкой, — с наконечником; в карточке — провода, у которых
  // руками обжат хотя бы один конец (обжатые только станком резки идут к стороне прямо из нарезанных).
  const handCrimped = new Set(specs.filter((spec) => spec.type === "prsTerm" && spec.epId).map((spec) => spec.epId!));
  const crimpedOn = (sides: readonly ("A" | "B")[]) => items.flatMap(({ thumb: wire, units: own }): WireThumb[] => {
    const at = (side: "A" | "B") => sides.includes(side)
      && own.some((unit) => Object.keys(unit.at).some((id) => sideOf.get(id) === side && handCrimped.has(id)));
    const left: WireBlankEnd = at("A") ? "terminal" : wire.left, right: WireBlankEnd = at("B") ? "terminal" : wire.right;
    return left === wire.left && right === wire.right ? [] : [{ ...wire, left, right }];
  });
  const crimped = crimpedOn(["A", "B"]);
  const tubes = specs.filter((spec) => spec.type === "cutTut" || spec.type === "coaxCutTut")
    .map((spec): TubeThumb => ({ key: spec.opKey, label: spec.label.replace(/^Резка\s+/i, "") || "Трубка", count: 0 }));
  const face = (side: "A" | "B") => ends.filter((end) => end.side === side).map((end): FaceThumb => {
    const byPin: Record<number, string> = {};
    // Торец рисуется основным цветом выбранной жилы (как схема распиновки), иначе — цветом из наименования.
    units.forEach((unit) => { const pin = unit.at[end.id]; if (pin != null && !byPin[pin]) byPin[pin] = unit.baseName ?? unit.name; });
    return { key: end.key, label: `${end.key} · ${end.conn || "разъём не выбран"}`, housing: housings[end.conn] ?? null, byPin: Object.keys(byPin).length ? byPin : null };
  });
  return { wires, tubes, crimped, crimpedBy: { A: crimpedOn(["A"]), B: crimpedOn(["B"]) }, faces: { A: face("A"), B: face("B") } };
}

/** «#222» → «#222222»: рисунок провода принимает только полный цвет. */
export function fullHex(color: string): string {
  const short = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(color);
  return short ? `#${short[1]}${short[1]}${short[2]}${short[2]}${short[3]}${short[3]}` : color;
}
