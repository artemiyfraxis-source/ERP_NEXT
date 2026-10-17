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
}

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
      const inward = (end === "from" ? points.slice(1) : points.slice(0, -1).reverse()).find((p) => Math.hypot(p.x - tip.x, p.y - tip.y) > 1e-6);
      const length = inward ? Math.hypot(inward.x - tip.x, inward.y - tip.y) : 1;
      const direction = inward ? { x: (inward.x - tip.x) / length, y: (inward.y - tip.y) / length } : { x: 1, y: 0 };
      const tinned = object.metadata?.[`strip${key}Tin`] === "true";
      result.push({ key: id, kind, lengthMm, tinned, text: `зач. ${mmText(lengthMm)}${tinned && kind !== "splice" ? ", луж." : ""}`, tip: { x: tip.x, y: tip.y }, direction });
    }
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
    let x = c.tip.x + 9, y = c.tip.y - 9;
    context.textAlign = "left"; context.textBaseline = "bottom";
    if (c.kind !== "splice") {
      const away = { x: -c.direction.x, y: -c.direction.y };
      if (Math.abs(away.x) >= Math.abs(away.y)) {
        x = c.tip.x + away.x * 8;
        context.textAlign = away.x >= 0 ? "left" : "right";
      } else {
        // Вертикальный хвост: длина провода — на его оси за концом, подпись зачистки — правее, на уровне конца.
        x = c.tip.x + 12; y = c.tip.y;
        context.textBaseline = "middle";
      }
    }
    context.strokeStyle = "rgba(255,255,255,.95)";
    context.strokeText(c.text, x, y);
    context.fillStyle = CALLOUT_COLOR;
    context.fillText(c.text, x, y);
  }
  context.restore();
}

/** Строка требований под чертежом: слева, ниже нижнего провода. */
export function drawStripNotes(context: CanvasRenderingContext2D, objects: readonly EditorSceneObject[], line: string): void {
  if (!line) return;
  const points = objects.filter((o) => o.kind === "wire").flatMap((o) => o.points ?? []);
  if (!points.length) return;
  context.save();
  context.font = "600 12px Inter, Arial, sans-serif";
  context.fillStyle = CALLOUT_COLOR;
  context.textAlign = "left"; context.textBaseline = "top";
  context.fillText(line, Math.min(...points.map((p) => p.x)), Math.max(...points.map((p) => p.y)) + 40);
  context.restore();
}
