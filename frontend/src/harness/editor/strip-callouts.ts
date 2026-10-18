// ERP: длина зачистки концов проводов читается на чертеже текстом (владелец 17.10: «так же в чертеже надо обновить»).
// Сцена несёт у провода метаданные `strip{End}` (длина зачистки, мм), `strip{End}Kind` (`splice:<узел>` | `tail` | `end`)
// и `strip{End}Tin`; здесь из них собираются подписи: у сростка — одна на узел, у хвоста — своя, у прочего конца — своя.
// Любой профиль зачистки на конце провода получает читаемую длину — не только профили генератора ТК.
import type { EditorSceneObject } from "./editor-types";
import { isFreeEndpoint, isJunctionEndpoint, type WireInstance } from "./model";

export interface StripCallout {
  readonly key: string;
  readonly kind: "splice" | "tail" | "end";
  readonly lengthMm: number;
  readonly tinned: boolean;
  readonly text: string;
  /** Конец провода на чертеже и направление внутрь провода. */
  readonly tip: { readonly x: number; readonly y: number };
  readonly direction: { readonly x: number; readonly y: number };
  /** Где стоит текст (после обхода подписей проводов и разъёмов). */
  readonly x: number;
  readonly y: number;
  readonly align: "left" | "right";
  readonly baseline: "top" | "middle" | "bottom";
}

interface Box { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number }
const CHAR_PX = 6, TEXT_H = 13;

const textBox = (text: string, x: number, y: number, align: StripCallout["align"], baseline: StripCallout["baseline"]): Box => {
  const w = text.length * CHAR_PX;
  const x0 = align === "left" ? x : x - w;
  const y0 = baseline === "bottom" ? y - TEXT_H : baseline === "middle" ? y - TEXT_H / 2 : y;
  return { x0, y0, x1: x0 + w, y1: y0 + TEXT_H };
};
const overlaps = (a: Box, b: Box) => a.x0 < b.x1 + 2 && a.x1 + 2 > b.x0 && a.y0 < b.y1 + 2 && a.y1 + 2 > b.y0;

/** То, что подпись зачистки не должна закрывать: метки проводов и позиций, разъёмы, подписи длины хвостов. */
function obstacles(objects: readonly EditorSceneObject[]): Box[] {
  const result: Box[] = [];
  for (const o of objects) {
    if (o.kind === "object-index" || o.kind === "connector") result.push({ x0: o.x, y0: o.y, x1: o.x + o.width, y1: o.y + o.height });
    if (o.kind !== "wire") continue;
    const points = o.points ?? [];
    for (const [key, tip, next] of [["openFrom", points[0], points[1]], ["openTo", points.at(-1), points.at(-2)]] as const) {
      const label = o.metadata?.[key];
      if (label === undefined || !tip || !next) continue;
      const len = Math.hypot(tip.x - next.x, tip.y - next.y) || 1, dx = (tip.x - next.x) / len, dy = (tip.y - next.y) / len;
      const cx = tip.x + dx * 8, cy = tip.y + dy * 8, w = label.length * CHAR_PX;
      result.push(Math.abs(dx) >= Math.abs(dy)
        ? { x0: dx >= 0 ? cx : cx - w, y0: cy - 7, x1: dx >= 0 ? cx + w : cx, y1: cy + 7 }
        : { x0: cx - w / 2, y0: dy > 0 ? cy : cy - 13, x1: cx + w / 2, y1: dy > 0 ? cy + 13 : cy });
    }
  }
  return result;
}

type Slot = readonly [dx: number, dy: number, align: StripCallout["align"], baseline: StripCallout["baseline"]];
const SPLICE_SLOTS: readonly Slot[] = [[9, -9, "left", "bottom"], [9, 9, "left", "top"], [-9, -9, "right", "bottom"], [-9, 9, "right", "top"],
  [9, -24, "left", "bottom"], [9, 24, "left", "top"], [-9, -24, "right", "bottom"], [-9, 24, "right", "top"], [30, -9, "left", "bottom"], [-30, 9, "right", "top"]];

const slotsFor = (kind: StripCallout["kind"], direction: { x: number; y: number }): readonly Slot[] => {
  if (kind === "splice") return SPLICE_SLOTS;
  const away = { x: -direction.x, y: -direction.y };
  const horizontal = Math.abs(away.x) >= Math.abs(away.y);
  // Хвост: длина провода — на его оси за концом, подпись зачистки — сбоку от конца.
  const first: Slot = horizontal ? [away.x * 8, -9, away.x >= 0 ? "left" : "right", "bottom"] : [12, 0, "left", "middle"];
  return [first, ...(horizontal ? [[first[0], 9, first[2], "top"] as Slot] : [[-12, 0, "right", "middle"] as Slot]), ...SPLICE_SLOTS];
};

const mmText = (mm: number) => `${String(Math.round(mm * 100) / 100).replace(".", ",")} мм`;
const TIN_PATTERN = /луж|хвост/i;

/** Метаданные сцены: длина зачистки каждого конца с профилем и где этот конец сидит. */
export function stripEndMetadata(wire: Pick<WireInstance, "from" | "to" | "stripProfiles">): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [end, key] of [["from", "From"], ["to", "To"]] as const) {
    const profile = wire.stripProfiles?.[end];
    if (!profile || profile.layers.length === 0) continue;
    const at = wire[end];
    result[`strip${key}`] = String(Math.max(...profile.layers.map((layer) => layer.stripLengthMm)));
    result[`strip${key}Kind`] = isJunctionEndpoint(at) ? `splice:${at.junctionId}` : isFreeEndpoint(at) ? "tail" : "end";
    if (TIN_PATTERN.test(profile.displayName) || TIN_PATTERN.test(profile.sourceKey)) result[`strip${key}Tin`] = "true";
  }
  return result;
}

/** Подписи зачистки по сцене чертежа: сросток — один раз на узел, хвост и прочий конец — у каждого. */
export function collectStripCallouts(objects: readonly EditorSceneObject[]): StripCallout[] {
  const result: StripCallout[] = [];
  const pending: Array<Omit<StripCallout, "x" | "y" | "align" | "baseline">> = [];
  const seen = new Set<string>();
  for (const object of objects) {
    const points = object.points ?? [];
    if (object.kind !== "wire" || points.length < 2) continue;
    for (const [end, key] of [["from", "From"], ["to", "To"]] as const) {
      const raw = object.metadata?.[`strip${key}`];
      const lengthMm = raw === undefined ? NaN : Number(raw);
      if (!Number.isFinite(lengthMm) || lengthMm <= 0) continue;
      const where = object.metadata?.[`strip${key}Kind`] ?? "end";
      const kind = where.startsWith("splice:") ? "splice" : where === "tail" ? "tail" : "end";
      const id = kind === "splice" ? where : `${object.id}:${end}`;
      if (seen.has(id)) continue;
      seen.add(id);
      const tip = end === "from" ? points[0]! : points.at(-1)!;
      // Концы в одной точке (жгут хвостов зоны сростков, сростки одной зоны у её входа и выхода) — одна подпись на точку.
      const twin = pending.some((c) => c.kind === kind && c.lengthMm === lengthMm && c.tinned === (object.metadata?.[`strip${key}Tin`] === "true")
        && Math.hypot(c.tip.x - tip.x, c.tip.y - tip.y) < 2);
      if (twin && kind !== "end") continue;
      const inward = (end === "from" ? points.slice(1) : points.slice(0, -1).reverse()).find((p) => Math.hypot(p.x - tip.x, p.y - tip.y) > 1e-6);
      const length = inward ? Math.hypot(inward.x - tip.x, inward.y - tip.y) : 1;
      const direction = inward ? { x: (inward.x - tip.x) / length, y: (inward.y - tip.y) / length } : { x: 1, y: 0 };
      const tinned = object.metadata?.[`strip${key}Tin`] === "true";
      const text = `зач. ${mmText(lengthMm)}${tinned && kind !== "splice" ? ", луж." : ""}`;
      pending.push({ key: id, kind, lengthMm, tinned, text, tip: { x: tip.x, y: tip.y }, direction });
    }
  }
  // Место подписи: первое свободное — не поверх меток W1, позиций, разъёмов, длин хвостов и других подписей зачистки.
  const taken = obstacles(objects);
  for (const c of pending) {
    const slots = slotsFor(c.kind, c.direction);
    const fits = (slot: Slot) => !taken.some((box) => overlaps(textBox(c.text, c.tip.x + slot[0], c.tip.y + slot[1], slot[2], slot[3]), box));
    const [dx, dy, align, baseline] = slots.find(fits) ?? slots[0]!;
    taken.push(textBox(c.text, c.tip.x + dx, c.tip.y + dy, align, baseline));
    result.push({ ...c, x: c.tip.x + dx, y: c.tip.y + dy, align, baseline });
  }
  return result;
}

/** Строка для технических требований чертежа из тех же профилей: «Зачистка концов под сросток — 5 мм; хвосты — 3 мм с лужением». */
export function stripNotesLine(callouts: readonly StripCallout[]): string {
  const lengths = (kind: StripCallout["kind"], tinned?: boolean) => [...new Set(callouts
    .filter((c) => c.kind === kind && (tinned === undefined || c.tinned === tinned)).map((c) => c.lengthMm))].sort((a, b) => a - b).map(mmText).join(", ");
  const parts = [
    lengths("splice") && `Зачистка концов под сросток — ${lengths("splice")}`,
    lengths("tail", true) && `хвосты — ${lengths("tail", true)} с лужением`,
    lengths("tail", false) && `хвосты — ${lengths("tail", false)}`,
    lengths("end") && `зачистка прочих концов — ${lengths("end")}`,
  ].filter(Boolean) as string[];
  if (!parts.length) return "";
  const [head, ...rest] = parts;
  return [head!.charAt(0).toUpperCase() + head!.slice(1), ...rest].join("; ");
}

const CALLOUT_COLOR = "#7a4318";

/** Подписи длины зачистки у концов проводов — с белой обводкой, чтобы читались поверх линий. */
export function drawStripCallouts(context: CanvasRenderingContext2D, callouts: readonly StripCallout[]): void {
  context.save();
  context.font = "600 11px Inter, Arial, sans-serif";
  context.lineJoin = "round";
  context.lineWidth = 3;
  for (const c of callouts) {
    context.textAlign = c.align; context.textBaseline = c.baseline;
    const { x, y } = c;
    context.strokeStyle = "rgba(255,255,255,.95)";
    context.strokeText(c.text, x, y);
    context.fillStyle = CALLOUT_COLOR;
    context.fillText(c.text, x, y);
  }
  context.restore();
}

/** Строка требований под чертежом: слева, ниже нижнего провода и нижней линии размера. */
export function drawStripNotes(context: CanvasRenderingContext2D, objects: readonly EditorSceneObject[], line: string): void {
  if (!line) return;
  const points = objects.filter((o) => o.kind === "wire" || o.kind === "dimension").flatMap((o) => o.points ?? []);
  if (!points.length) return;
  context.save();
  context.font = "600 12px Inter, Arial, sans-serif";
  context.fillStyle = CALLOUT_COLOR;
  context.textAlign = "left"; context.textBaseline = "top";
  context.fillText(line, Math.min(...points.map((p) => p.x)), Math.max(...points.map((p) => p.y)) + 40);
  context.restore();
}
