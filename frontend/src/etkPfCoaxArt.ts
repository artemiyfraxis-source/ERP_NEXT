// Картинки ПФ коаксиальной сборки на размер карточки схемы ПФ (владелец 19.10: «визуал полуфабрикатов прям такой себе» —
// КБ1 «SMA-Male to SMA-Female RG316 275mm»). Рисуются в масштабе 1:1 карточки (320 px шириной), подписи не мельче 10 px:
//  - ПФ «Кабель с центральным контактом» (пайка) — только конец кабеля крупно: оболочка, оплётка, диэлектрик, жила и
//    припаянный центральный контакт (штырь у вилки, гнездо у розетки);
//  - ПФ «Сторона … собрана» — тот же конец с ТУТ, втулкой экрана и корпусом разъёма плоским боковым видом (накидная гайка
//    у вилки SMA, резьбовой корпус у розетки);
//  - изделие — вся сборка: разъём А, кабель с размером длины, разъём Б, ТУТ на обоих концах, названия обоих разъёмов.
// Данные — из конфигурации сборки `build_config.coax` (разъёмы концов, размеры разделки, ТУТ, кабель) и `gen_state`
// (длина по чертежу): выданные ЭТК перевыпускать не нужно.
import { coaxEndNames, coaxKindTitle } from "./coaxNames";
import { tubeShort } from "./etkPfScheme";
import type { EtkPfGroup } from "./etkRoute";

type Row = Record<string, unknown>;
type Side = "A" | "B";

export const INK = "#17272b", MUTED = "#5f7470", METAL = "#d4d9dd", EDGE = "#8d969c", BRASS = "#d9b25f", COPPER = "#e08a2c";
export const JACKET = "#222", TUBE = "#3b4147", SOLDER = "#c9ced3", DIEL = "#fbfbf8";
const W = 320;

const obj = (value: unknown): Row => (value && typeof value === "object" && !Array.isArray(value) ? value as Row : {});
const str = (value: unknown): string => (value == null ? "" : String(value));
const num = (value: unknown): number => {
  const parsed = Number(str(value).replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
};
const esc = (value: unknown): string => str(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const f = (value: number) => value.toFixed(1);

/** Прямоугольник по краям x1..x2 (в любом порядке). */
export function rect(x1: number, x2: number, y: number, h: number, fill: string, stroke = EDGE, rx = 0, extra = ""): string {
  return `<rect x="${f(Math.min(x1, x2))}" y="${f(y)}" width="${f(Math.abs(x2 - x1))}" height="${f(h)}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width=".9"${extra ? ` ${extra}` : ""}/>`;
}
export const line = (x1: number, y1: number, x2: number, y2: number, stroke = EDGE, width = .9, dash = "") =>
  `<line x1="${f(x1)}" y1="${f(y1)}" x2="${f(x2)}" y2="${f(y2)}" stroke="${stroke}" stroke-width="${width}"${dash ? ` stroke-dasharray="${dash}"` : ""}/>`;
export const label = (x: number, y: number, value: string, size = 11, weight = 600, fill = INK, anchor = "middle") =>
  `<text x="${f(x)}" y="${f(y)}" text-anchor="${anchor}" font-size="${size}" font-weight="${weight}" fill="${fill}">${esc(value)}</text>`;

function svg(height: number, body: string): string {
  return `<svg class="etk-svg etk-pf-svg" viewBox="0 0 ${W} ${height}" width="100%" preserveAspectRatio="xMinYMin meet" xmlns="http://www.w3.org/2000/svg">` +
    `<defs><pattern id="pfBraid" width="7" height="7" patternUnits="userSpaceOnUse"><rect width="7" height="7" fill="#9aa1a6"/>` +
    `<path d="M0,0 L7,7 M7,0 L0,7" stroke="#4b5257" stroke-width="1.1"/></pattern></defs>${body}</svg>`;
}

/** Разъём конца: вилка (штыри) или розетка (гнёзда); типа нет — вилкой по умолчанию. */
export const isFemale = (end: Row) => coaxEndNames(end).word === "розетка";

// ── Разделанный конец с припаянным центральным контактом ──

/** Геометрия ступеней разделки конца: L1–L3 в масштабе на `span` px от кончика жилы `x0`; нет размеров — типовые 2,5 / 3,5 / 7,5. */
export function stepsOf(end: Row, x0: number, span: number): { x1: number; x2: number; x3: number } {
  const [l1, l2, l3] = [num(end.l1), num(end.l2), num(end.l3)];
  const [a, b, c] = l1 > 0 && l2 > l1 && l3 > l2 ? [l1, l2, l3] : [2.5, 3.5, 7.5];
  const k = span / c;
  return { x1: x0 + a * k, x2: x0 + b * k, x3: x0 + c * k };
}

/** Рисунок (без подписей) конца кабеля, сторона А — конец слева: оболочка до `jacketTo`, оплётка, диэлектрик, жила и припаянный
 *  центральный контакт — штырь у вилки, гнездо у розетки. `braid` — заливка оплётки (у каждого холста свой узор). */
export function endGraphics(end: Row, x0: number, cy: number, steps: { x1: number; x2: number; x3: number }, jacketTo: number, braid: string): string {
  const { x1, x2, x3 } = steps, female = isFemale(end);
  let art = rect(x3, jacketTo, cy - 16, 32, JACKET, "#000", 4) +
    rect(x2, x3, cy - 12, 24, braid) + rect(x1, x2, cy - 9, 18, DIEL, INK) + rect(x0, x1, cy - 2.5, 5, COPPER, INK);
  // Центральный контакт: хвостовик-чашка на жиле с припоем и рабочая часть — штырь (вилка) или гнездо (розетка).
  art += rect(x0 - 4, x1 - 2, cy - 5.5, 11, BRASS) +
    `<ellipse cx="${f((x0 + x1) / 2)}" cy="${f(cy)}" rx="${f((x1 - x0) * 0.36)}" ry="4.6" fill="${SOLDER}" stroke="${EDGE}" stroke-width=".8"/>` +
    rect(x0 - 8, x0 - 4, cy - 8, 16, BRASS);
  art += female
    ? rect(x0 - 32, x0 - 8, cy - 5.5, 11, BRASS) + rect(x0 - 32, x0 - 24, cy - 2.6, 5.2, TUBE, TUBE)
    : rect(x0 - 32, x0 - 8, cy - 2.6, 5.2, BRASS) + `<polygon points="${f(x0 - 38)},${f(cy)} ${f(x0 - 32)},${f(cy - 2.6)} ${f(x0 - 32)},${f(cy + 2.6)}" fill="${BRASS}" stroke="${EDGE}" stroke-width=".8"/>`;
  return art;
}

// ── ПФ «Кабель с центральным контактом»: конец кабеля крупно ──

/** Разделанный конец кабеля с припаянным центральным контактом. Сторона А — конец слева, кабель уходит вправо; сторона Б —
 *  зеркально. Ступени L1–L3 — в масштабе по размерам разделки конца. */
export function coaxContactDrawing(side: Side, end: Row): string {
  const mirror = side === "B", cy = 64, x0 = 46;
  const mx = (x: number) => (mirror ? W - x : x);
  const { x1, x2, x3 } = stepsOf(end, x0, 150);
  const art = endGraphics(end, x0, cy, { x1, x2, x3 }, 312, "url(#pfBraid)");
  const flip = mirror ? `<g transform="translate(${W},0) scale(-1,1)">${art}</g>` : art;
  const near = mirror ? "end" : "start", edge = mirror ? W - 4 : 4;
  const wordKind = coaxEndNames(end).kind;
  let body = label(4, 14, `сторона ${side === "A" ? "А" : "В"}${wordKind ? ` · под ${wordKind}` : ""}`, 12, 700, INK, "start") + flip;
  body += label(edge, 36, "контакт, припой", 11, 600, INK, near) + line(mx(x0 - 14), 40, mx(x0 - 14), cy - 8, MUTED, .9, "2 2");
  body += label(mx((x2 + x3) / 2), 36, "оплётка", 11, 600, INK) + line(mx((x2 + x3) / 2), 40, mx((x2 + x3) / 2), cy - 13, MUTED, .9, "2 2");
  body += label(mx((x1 + x2) / 2), 96, "диэлектрик", 11, 600, INK) + line(mx((x1 + x2) / 2), cy + 10, mx((x1 + x2) / 2), 85, MUTED, .9, "2 2");
  body += label(mx((x3 + 312) / 2), cy + 4, str(end.cable) || "кабель", 11, 600, "#fff");
  return svg(104, body);
}

// ── Корпус разъёма плоским боковым видом ──

/** Разъём с втулкой экрана и ТУТ, локальные координаты: лицо разъёма в x = 0, кабель уходит вправо, середина по высоте — y = 0.
 *  Вилка SMA — корпус с накидной гайкой (шестигранник); розетка — резьбовой стержень и шестигранный корпус. */
function connectorSide(female: boolean, jacketTo: number): string {
  let art = rect(100, jacketTo, -10, 20, JACKET, "#000", 2);
  if (female) {
    // Резьбовая часть спереди, затем шестигранник, шейка под обжим.
    art += rect(0, 44, -11, 22, METAL, EDGE, 1);
    for (let x = 5; x < 44; x += 5) art += line(x, -11, x - 2, 11, EDGE, .8);
    art += rect(44, 92, -16, 32, METAL, EDGE, 1) + line(60, -16, 60, 16) + line(76, -16, 76, 16) + rect(92, 108, -7, 14, METAL);
  } else {
    // Накидная гайка с рифлением, под ней корпус, шейка под обжим.
    art += rect(0, 56, -16, 32, METAL, EDGE, 2) + rect(0, 7, -16, 32, "#b9c1c7", EDGE, 2);
    [18, 30, 42].forEach((x) => { art += line(x, -16, x, 16); });
    art += rect(56, 96, -10, 20, METAL) + rect(96, 108, -7, 14, METAL);
  }
  // Втулка экрана, обжатая на оплётке, с рифлением, и ТУТ поверх стыка.
  art += rect(106, 142, -11, 22, METAL, EDGE, 2);
  for (let x = 112; x < 140; x += 6) art += line(x, -11, x, 11, EDGE, .8);
  art += rect(126, 216, -14, 28, TUBE, "#000", 6, 'fill-opacity=".92"');
  return art;
}

/** Конец кабеля со смонтированным разъёмом, ТУТ и втулкой экрана. */
export function coaxMountedDrawing(side: Side, end: Row, tube: string): string {
  const mirror = side === "B", cy = 58, mx = (x: number) => (mirror ? W - x : x);
  const female = isFemale(end), wordKind = coaxEndNames(end).kind;
  const art = `<g transform="translate(${mirror ? W - 8 : 8},${cy}) scale(${mirror ? -1 : 1},1)">${connectorSide(female, 304)}</g>`;
  let body = label(4, 14, `сторона ${side === "A" ? "А" : "В"}${wordKind ? ` · под ${wordKind}` : ""}`, 12, 700, INK, "start") + art;
  const near = mirror ? "end" : "start", edge = mirror ? W - 4 : 4;
  body += label(edge, 36, female ? "корпус с резьбой" : "накидная гайка", 11, 600, INK, near) + line(mx(8 + (female ? 22 : 28)), 40, mx(8 + (female ? 22 : 28)), cy - 17, MUTED, .9, "2 2");
  body += label(mx(8 + 124), 96, "втулка экрана", 11, 600, INK) + line(mx(8 + 124), cy + 12, mx(8 + 124), 85, MUTED, .9, "2 2");
  body += label(mx(8 + 171), cy + 4, tube || "ТУТ", 11, 700, "#fff");
  body += label(mx(8 + 262), cy + 4, str(end.cable) || "кабель", 11, 600, "#fff");
  return svg(104, body);
}

// ── Изделие: вся сборка ──

/** Вся сборка: разъём А, кабель с размером длины, разъём Б, ТУТ на обоих концах, названия обоих разъёмов. */
export function coaxAssemblyDrawing(a: Row, b: Row, lengthMm: string, tube: string, cable: string): string {
  const cy = 48, s = 0.6, cableA = 6 + 106 * s, cableB = W - 6 - 106 * s;
  const place = (end: Row, mirror: boolean) =>
    `<g transform="translate(${mirror ? W - 6 : 6},${cy}) scale(${mirror ? -s : s},${s})">${connectorSide(isFemale(end), 216)}</g>`;
  // Кабель между ТУТ: скрытая под гайкой и ТУТ часть рисуется самими разъёмами.
  let body = rect(cableA, cableB, cy - 6, 12, JACKET, "#000", 2) + place(a, false) + place(b, true);
  const tubeMid = (x: number) => x;
  body += label(tubeMid(6 + 171 * s), cy + 26, "ТУТ", 10, 700, MUTED) + label(tubeMid(W - 6 - 171 * s), cy + 26, "ТУТ", 10, 700, MUTED);
  body += label(W / 2, cy + 3.6, cable, 11, 600, "#fff");
  const nameA = coaxKindTitle(a), nameB = coaxKindTitle(b);
  if (nameA) body += label(6, 14, nameA, 12, 700, INK, "start");
  if (nameB) body += label(W - 6, 14, nameB, 12, 700, INK, "end");
  // Размер длины кабеля между лицами разъёмов.
  const yd = 84;
  body += line(6, cy + 20, 6, yd + 6, MUTED, .9, "3 3") + line(W - 6, cy + 20, W - 6, yd + 6, MUTED, .9, "3 3") +
    line(6, yd, W - 6, yd, INK, 1) + `<path d="M6,${yd} l9,-3.5 v7 z M${W - 6},${yd} l-9,-3.5 v7 z" fill="${INK}"/>` +
    `<rect x="${W / 2 - 46}" y="${yd - 10}" width="92" height="20" fill="#f6f8f8"/>` +
    label(W / 2, yd + 5, lengthMm ? `L = ${lengthMm} мм` : "L по КД", 13, 700, INK);
  if (tube) body += label(W / 2, 108, `${tube} на обоих концах · втулки экрана обжаты`, 10, 400, MUTED);
  return svg(114, body);
}

// ── Вход из etkPfArt ──

/** Длина изделия по чертежу: из формы генератора, иначе из названия («… 275mm»), иначе длина реза. */
function assemblyLength(config: Row, name: string): string {
  const state = obj(obj(config.gen_state).S);
  const own = str(state.drawingLength || state.sharedLengthText).trim();
  if (num(own) > 0) return str(num(own));
  const named = name.match(/(\d+(?:[.,]\d+)?)\s*(?:mm|мм)\b/i)?.[1];
  return named ? str(num(named)) : num(obj(config.coax).cableLength) > 0 ? str(num(obj(config.coax).cableLength)) : "";
}

/** Рисунок ПФ коаксиальной сборки; ПФ без своей картинки (резка кабеля, трубок) и не коакс — null. `operations` — операции ПФ. */
export function coaxPfArt(group: EtkPfGroup, operations: Row[], config: Row): string | null {
  const coax = obj(config.coax);
  if (str(config.mode) !== "coax" || !Object.keys(coax).length || !operations.length) return null;
  const end = (side: Side) => ({ ...obj(coax[side === "A" ? "sideA" : "sideB"]), cable: str(coax.cableArt || coax.cableName) });
  const tube = str(coax.tutArt || coax.tutName) ? tubeShort(str(coax.tutArt || coax.tutName)) : "";
  const types = operations.map((operation) => str(operation.type));
  if (group.final) return coaxAssemblyDrawing(end("A"), end("B"), assemblyLength(config, group.name), tube, str(coax.cableArt || coax.cableName) || "кабель");
  const last = operations[operations.length - 1], side: Side = str(last.side) === "B" ? "B" : "A";
  if (types.includes("coaxHousing")) return coaxMountedDrawing(side, end(side), tube);
  if (types.every((type) => type === "coaxPin")) return coaxContactDrawing(side, end(side));
  return null;
}
