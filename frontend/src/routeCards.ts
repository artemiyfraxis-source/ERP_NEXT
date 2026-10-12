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

/** Связь от низа карточки-входа к верху карточки, что её берёт. Цель ниже — ортогональная линия со скруглёнными
    углами: вниз, вдоль зазора над целью, вниз; строго под входом — прямая. Цель выше или рядом (карточку утащили) —
    плавная S-кривая. `mid` — где стоят число штук и кнопки «+» / «×». */
export function linkGeometry(from: Rect, to: Rect): { d: string; mid: Point } {
  const sx = from.x + from.width / 2, sy = from.y + from.height, tx = to.x + to.width / 2, ty = to.y;
  if (ty - sy >= 24) {
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

/** Полуфабрикат генератора: нарезанные провода, нарезанные трубки, собранные стороны А и В, готовое изделие. */
export type GenStage = "wires" | "tubes" | "A" | "B" | "final";
const STAGES: GenStage[] = ["wires", "tubes", "A", "B", "final"];
const WIRE_CUTS = new Set(["cutWire", "cutWireStrip", "cutWireCrimp", "coaxCut"]);
const TUBE_CUTS = new Set(["cutTut", "coaxCutTut"]);

/** Полуфабрикат каждой операции (по номеру в ЭТК): резка проводов и кабеля — «Нарезанные провода», резка ТУТ и
    термоусадки — «Нарезанные трубки», операция стороны — её сторона; операция без стороны (надеть ТУТ, свивка,
    маркировка, обмотка) готовит ближайшую следующую сторону, а после последней стороны — готовое изделие. */
export function opStages(ops: readonly RouteOp[]): Map<number, GenStage> {
  const result = new Map<number, GenStage>();
  let waiting: number[] = [];
  for (const op of [...ops].sort((a, b) => a.position - b.position)) {
    const code = op.code.toUpperCase();
    const stage: GenStage | null = TUBE_CUTS.has(op.type) || code.startsWith("CUT_TUT") ? "tubes"
      : WIRE_CUTS.has(op.type) || code.startsWith("CUT_") ? "wires" : op.side ?? null;
    if (stage === "A" || stage === "B") { waiting.forEach((position) => result.set(position, stage)); waiting = []; }
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
  if (stage === "wires") return ops.length && ops.every((op) => op.type === "coaxCut") ? "Нарезанный кабель" : "Нарезанные провода";
  if (stage === "tubes") return "Нарезанные трубки";
  if (final) return "Готовый кабель";
  return stage === "A" ? "Сторона А собрана" : stage === "B" ? "Сторона В собрана" : "Отделка";
}

/** Карточки схемы генератора поверх её блоков (схема ЭТК не меняется): блок — в карточке полуфабриката своей последней
    операции; карточка с итоговым блоком — «Готовый кабель» (ФИНАЛ). Карточки — по ходу сборки (первая операция в
    ЭТК), финал последним. Связь карточек — стрелки блоков между ними; стрелка назад по ходу сборки (коаксиал:
    разделка А, разделка В, пайка А…) карточек не связывает. Число штук на
    связи — когда под ней одна стрелка (или все с одним числом). */
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
      id: stage, kind: final ? "final" : stage === "wires" || stage === "tubes" ? "semiFinished" : "assembly",
      title: genTitle(stage, final, cardOps), blocks: blocks.map((block) => block.key), ops: cardOps, final,
    };
  });
  // Ход сборки — по первой операции карточки в ЭТК (технолог мог поставить сторону В раньше А); финал — последний.
  const first = new Map(cards.map((card) => [card.id, card.final ? Infinity : Math.min(...card.ops.map((op) => op.position), Infinity)]));
  cards.sort((a, b) => first.get(a.id)! - first.get(b.id)! || STAGES.indexOf(a.id) - STAGES.indexOf(b.id));
  const rank = (stage: GenStage) => first.get(stage) ?? Infinity;
  const grouped = new Map<string, GenLink>();
  for (const edge of route.edges) {
    const from = cardOf.get(edge.from), to = cardOf.get(edge.to);
    if (!from || !to || from === to || rank(from) > rank(to)) continue;
    const id = `${from}>${to}`;
    const link = grouped.get(id) ?? { id, from, to, qty: null, edges: [] };
    link.edges.push(edge);
    grouped.set(id, link);
  }
  const links = [...grouped.values()].map((link) => ({ ...link, qty: link.edges.every((edge) => edge.per_unit === link.edges[0].per_unit) ? link.edges[0].per_unit : null }));
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
export type WireThumb = { key: string; label: string; title: string; color: string; left: WireBlankEnd; right: WireBlankEnd };
/** Торец разъёма стороны: корпус из базы разъёмов (нет — подпись), цвета проводов по контактам. */
export type FaceThumb = { key: string; label: string; housing: ConnectorHousing | null; byPin: Record<number, string> | null };
export type TubeThumb = { key: string; label: string; count: number };
export type GenArt = { wires: WireThumb[]; tubes: TubeThumb[]; faces: { A: FaceThumb[]; B: FaceThumb[] } };
export const EMPTY_ART: GenArt = { wires: [], tubes: [], faces: { A: [], B: [] } };

/** Сколько строк рисунка видно в карточке: остальное — «и ещё N». */
export const ART_ROWS = 4;
export const GEN_CARD = { width: 248, head: 64, opRow: 22, pad: 20, artGap: 8 } as const;
export const GEN_SPACING: Spacing = { gapX: 16, gapY: 40, margin: 4 };

/** Высота рисунка карточки генератора, px: провода и трубки — по строке (не больше ART_ROWS и строка «и ещё»),
    торцы разъёмов — полоса 72 px; у готового изделия — оба торца с кабелем между ними. */
export function genArtHeight(stage: GenStage, art: GenArt, final: boolean): number {
  const rows = (count: number, row: number) => (count ? Math.min(count, ART_ROWS) * row + (count > ART_ROWS ? 16 : 0) + 8 : 0);
  if (stage === "wires") return rows(art.wires.length, 22);
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

/** Рисунки карточек генератора из формы: провода — по распиновке (контакты разъёмов сторон, цвет жилы, длина) с
    концами по их операции резки; трубки — операции резки ТУТ; торцы — разъёмы сторон из базы разъёмов с цветами
    проводов по контактам. Сторона без разъёма — «своб. конец». */
export function schemeArt(specs: readonly OpSpec[], ends: readonly PinoutEnd[], units: readonly PinUnit[],
  housings: Record<string, ConnectorHousing | null>, free: FreeSide | null): GenArt {
  const keyOf = new Map(ends.map((end) => [end.id, end.key]));
  const cuts = specs.filter((spec) => CUT_TYPES.has(spec.type));
  const wires = units.map((unit): WireThumb => {
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
  const tubes = specs.filter((spec) => spec.type === "cutTut" || spec.type === "coaxCutTut")
    .map((spec): TubeThumb => ({ key: spec.opKey, label: spec.label.replace(/^Резка\s+/i, "") || "Трубка", count: 0 }));
  const face = (side: "A" | "B") => ends.filter((end) => end.side === side).map((end): FaceThumb => {
    const byPin: Record<number, string> = {};
    units.forEach((unit) => { const pin = unit.at[end.id]; if (pin != null && !byPin[pin]) byPin[pin] = unit.name; });
    return { key: end.key, label: `${end.key} · ${end.conn || "разъём не выбран"}`, housing: housings[end.conn] ?? null, byPin: Object.keys(byPin).length ? byPin : null };
  });
  return { wires, tubes, faces: { A: face("A"), B: face("B") } };
}

/** «#222» → «#222222»: рисунок провода принимает только полный цвет. */
export function fullHex(color: string): string {
  const short = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(color);
  return short ? `#${short[1]}${short[1]}${short[2]}${short[2]}${short[3]}${short[3]}` : color;
}
