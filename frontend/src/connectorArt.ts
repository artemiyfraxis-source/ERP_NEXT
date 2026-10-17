// Рисунки разъёмов по сериям (владелец, 30.09: «все разъёмы одной общей иконкой — ничего не понятно»).
// Геометрия — из раскладки семейства (connector_housings.py: шаг, ряды, сетка контактов, защёлка, цвет) в миллиметрах,
// поэтому SH 1,0 мм и Mini-Fit 4,2 мм выглядят разными, а не одной иконкой. Два вида:
//  • connectorFace — торец со стороны проводов крупно, с номерами контактов и цветами проводов (эскиз монтажа);
//  • connectorPicture — объёмный вид корпуса (схема жгута, «База данных → Разъёмы»).
// Всё — SVG-строки: одни и те же рисунки идут на экран и в печать (etkPrint.ts).

export type ConnectorHousing = {
  art?: string; family?: string; title?: string; pitch?: number | null; rows: number; cols: number; n?: number;
  kind?: string; color: string; latch?: string; cavity?: string; labels?: string[] | null; male?: boolean | null;
  grid: number[][]; round?: Array<{ pin: number; x: number; y: number }> | null; conf?: string; note?: string;
  body?: HousingBody | null; src?: string; model?: string; roof?: Roof | null; mate?: Mate | null;
};

/** Признак на крыше корпуса, прочитанный с листа производителя (connector_housings.py `roof_of`): «latch» — замок
 *  (по центру — «center», плечи на торцах — «ends»), «slots» — окна по контакту («contacts») или кнопки поляризации
 *  («keys», место ориентировочное); `src` — лист, по которому это записано. Нет признака — нет и подтверждения: крыша
 *  рисуется голой, без выдуманного. */
export type Roof = { kind: "latch" | "slots"; at: "center" | "ends" | "pair" | "contacts" | "keys"; src: string };

/** Сторона пары «вилка — розетка» по листу производителя (connector_housings.py `MATING`, владелец 17.10: «почему
 *  розетка и вилка одинаково выглядят»): `gender` — как корпус называет производитель (receptacle — розетка, plug —
 *  вилка), `contacts` — штыри (pin) или гнёзда (socket), `shell` — outer: кожух принимает ответный корпус, inner: корпус
 *  входит в ответный; `lock` — arm: гибкое плечо/рычаг замка, catch: жёсткий упор или зацеп под плечо, window: окно, в
 *  которое входит замок ответной части. Нет поля — лист этого не показывает, и рисунок его не выдумывает. */
export type Mate = {
  gender?: "plug" | "receptacle"; contacts: "pin" | "socket"; shell?: "outer" | "inner"; lock?: "arm" | "catch" | "window"; src: string;
};

/** Корпус по чертежу производителя (connector_housings.py `body`), мм: ширина B = (колонок−1)·шаг + extra (+ выступы
 *  ears с каждой стороны), толщина h у торца проводов без защёлки и rear_h у стыковочной части (утоплена с грани метки),
 *  длина len, окна контактов win = [ширина, высота]; защёлка на верхней грани — высотой lock_h, шириной lock_w или
 *  B − 2·lock_inset. Есть у JST SH, GH, XH, PH, ZH и Molex Micro-Fit 43025. */
export type HousingBody = {
  extra: number; h: number; rear_h?: number; len: number; front_len?: number; ears?: number; ear_len?: number;
  win?: [number, number] | number[]; lock_w?: number; lock_inset?: number; lock_h?: number;
};

export type Pt = [number, number];
export type Box = { w: number; h: number; body: string };
/** Гнездо на торце: центр и радиус зоны щелчка в тех же координатах, что и рисунок (для экрана «Соединения по распиновке»). */
export type FaceHit = { pin: number; x: number; y: number; r: number };

export const EDGE = "#17272b";
const WIRE_COLORS: Array<[RegExp, string]> = [
  [/ж[её]лт/i, "#f2c200"], [/зел[её]н/i, "#2e9e4f"], [/ч[её]рн/i, "#222"], [/красн/i, "#d7322d"], [/(?<![а-яё])бел/i, "#f4f4f4"],
  [/син/i, "#2463c7"], [/голуб/i, "#5bb4e5"], [/оранж/i, "#f08a1c"], [/коричн/i, "#7a4a24"], [/(?<![а-яё])сер/i, "#8d9297"], [/фиолет/i, "#7b4bb3"],
  [/розов/i, "#ee82b4"],
];
const DARK_WIRE = /#(222|2463c7|7a4a24|7b4bb3|d7322d|2e9e4f|6b7c86)/;

/** Цвет жилы по слову в наименовании, null — цвета в наименовании нет (схема соединений подписывает такой провод). */
export function wireColorKnown(name: unknown): string | null {
  const text = name == null ? "" : String(name);
  for (const [pattern, color] of WIRE_COLORS) if (pattern.test(text)) return color;
  return null;
}

/** Цвет жилы по слову в наименовании (как SUPPLYMAP etkWireColor_), иначе серо-синий. */
export function wireColor(name: unknown): string {
  return wireColorKnown(name) ?? "#6b7c86";
}

function esc(value: unknown): string {
  return String(value == null ? "" : value)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

export const f = (n: number) => String(Math.round(n * 100) / 100);

function rgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h.padEnd(6, "0");
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) || 0) as [number, number, number];
}

/** Светлее (k > 0) или темнее (k < 0) — грани объёмного корпуса. */
export function shade(hex: string, k: number): string {
  const [r, g, b] = rgb(hex);
  const mix = (c: number) => Math.round(k >= 0 ? c + (255 - c) * k : c * (1 + k));
  return `#${[r, g, b].map((c) => Math.max(0, Math.min(255, mix(c))).toString(16).padStart(2, "0")).join("")}`;
}

export function isDark(hex: string): boolean {
  const [r, g, b] = rgb(hex);
  return r * 0.3 + g * 0.59 + b * 0.11 < 110;
}

export function poly(points: Pt[], fill: string, stroke = EDGE, width = 0.8, extra = ""): string {
  return `<polygon points="${points.map(([x, y]) => `${f(x)},${f(y)}`).join(" ")}" fill="${fill}" stroke="${stroke}" stroke-width="${width}" stroke-linejoin="round"${extra}/>`;
}

/** Вид корпуса серии: форма, глубина корпуса вдоль провода, защёлка. Размеры — ориентировочные, по каталогам. */
type Look = { shape: "rect" | "xt" | "dsub" | "round" | "rj45" | "barrel" | "lug" | "banana"; depth: number; wall: number; latch: string; orig?: string };

export function connectorLook(hs: ConnectorHousing): Look {
  const family = hs.family || "", pitch = hs.pitch || 2.5;
  // Замок на крыше рисуется только с подтверждением листа (hs.roof): рычаг/рампа/плечи «ends»; без него рычаг из базы не
  // рисуем. Метка ▽, ребро трения и окна остаются как есть — это не замок (orig).
  const orig = hs.latch || "none", roof = hs.roof;
  const latch = roof?.kind === "latch" ? (roof.at === "ends" || roof.at === "pair" ? roof.at : orig === "lever" ? "lever" : "ramp")
    : orig === "lever" || orig === "ramp" ? "none" : orig;
  if (hs.kind === "lug") return { shape: "lug", depth: 0, wall: 0, latch: "none" };
  if (hs.kind === "banana") return { shape: "banana", depth: 0, wall: 0, latch: "none" };
  if (hs.kind === "barrel") return { shape: "barrel", depth: 14, wall: 0, latch: "none" };
  if (hs.kind === "round") return { shape: "round", depth: 16, wall: 0, latch: "none" };
  if (hs.kind === "dsub") return { shape: "dsub", depth: 12, wall: 1.6, latch: "none" };
  if (family === "amass-xt" || family === "amass-qs8") return { shape: "xt", depth: 16, wall: 1.6, latch: "none" };
  if (family === "rj45") return { shape: "rj45", depth: 21, wall: 1.2, latch: roof?.kind === "latch" ? "lever" : "none" };
  const depth = /deutsch|superseal/.test(family) ? 26 : /minifit|microfit|jst-vh|jst-sm|multilock/.test(family) ? 12
    : family === "connfly-ds1071" ? 14 : Math.max(4.5, pitch * 3.2);
  const wall = /deutsch|superseal/.test(family) ? 2.2 : Math.max(0.55, Math.min(1.6, pitch * 0.38));
  return { shape: "rect", depth, wall, latch, orig };
}

/** Признак ориентации на крыше корпуса (владелец 17.10: «сверху замок или прорези — по детали»): «latch» — замок,
 *  «slots» — прорези/окна/кнопки поляризации. Берётся из `hs.roof` (лист производителя); null — подтверждённого признака
 *  нет (или у корпуса нет крыши: круглые, лепестки, D-Sub). Один выбор на все рисунки серии: 3D-грань каталога, вид сверху,
 *  торец эскиза. */
export type TopFeature = "latch" | "slots";

export function topFeature(hs: ConnectorHousing): TopFeature | null {
  const look = connectorLook(hs);
  if (look.shape !== "rect" && look.shape !== "xt" && look.shape !== "rj45") return null;
  return hs.roof?.kind ?? null;
}

/** Прорези на крыше в долях ширины корпуса: окно по каждому контакту («contacts», ширина — по окну чертежа или 0,56 шага)
 *  или пара рёбер-кнопок поляризации («keys»). */
export function slotSpans(hs: ConnectorHousing): Array<[number, number]> {
  if (hs.roof?.at === "keys") return [[0.3, 0.37], [0.63, 0.7]];
  const b = hs.body, pitch = hs.pitch || 1, cols = Math.max(1, hs.cols);
  if (b) {
    const core = (cols - 1) * pitch + b.extra, half = ((b.win && b.win.length === 2 ? b.win[0] : pitch * 0.6) * 0.4) / core;
    return Array.from({ length: cols }, (_, c) => { const m = (b.extra / 2 + c * pitch) / core; return [m - half, m + half] as [number, number]; });
  }
  return Array.from({ length: cols }, (_, c) => { const m = (c + 0.5) / cols; return [m - 0.28 / cols, m + 0.28 / cols] as [number, number]; });
}

/** Замок на крыше: [начало, ширина] в мм — один посередине («center»), два плеча на торцах («ends») или пара
 *  посередине («pair», PicoBlade 51047: от 4 контактов два окна). */
function latchSpans(look: Look, lw: number, W: number): Array<[number, number]> {
  if (look.latch === "ends") { const e = Math.max(W * 0.1, Math.min(W * 0.16, 2)); return [[0, e], [W - e, e]]; }
  if (look.latch === "pair") { const w = Math.min(lw, W * 0.22); return [[W * 0.3 - w / 2, w], [W * 0.7 - w / 2, w]]; }
  return [[(W - lw) / 2, lw]];
}

/** Метка признака ориентации в SVG: `data-top` — по ней тесты и сверка видят, что на крыше есть замок или прорези. */
export const roofMark = (kind: TopFeature, svg: string) => `<g data-top="${kind}">${svg}</g>`;

/** Как рисуется замок на крыше. Сторона пары с листа (hs.mate.lock): плечо — рычаг посередине («lever») или плечи с
 *  зацепами на торцах / парой («hook»), упор под плечо — низкий клин («catch»), окно — проём в крыше («window»). Без
 *  стороны пары — как было: рычаг из базы или клин. */
type LatchStyle = "lever" | "hook" | "ramp" | "catch" | "window";

function latchStyle(hs: ConnectorHousing, look: Look): LatchStyle {
  const lock = hs.mate?.lock;
  if (lock === "arm") return look.latch === "ends" || look.latch === "pair" ? "hook" : "lever";
  if (lock === "catch" || lock === "window") return lock;
  return look.latch === "lever" ? "lever" : "ramp";
}

const hasLatch = (look: Look) => look.latch === "lever" || look.latch === "ramp" || look.latch === "ends" || look.latch === "pair";

/** Замок стороны пары в SVG: `data-lock` — плечо, упор или окно (по нему тесты видят, что вилка и розетка разные). */
const lockMark = (hs: ConnectorHousing, svg: string) => hs.mate?.lock ? `<g data-lock="${hs.mate.lock}">${svg}</g>` : svg;

/** Насколько кожух (shell «outer») шире торца проводов с каждой стороны, мм: в него входит ответный корпус. */
const shroudGrow = (look: Look) => Math.max(look.wall * 0.7, 0.35);

const PIN = "#d8b454", PIN_EDGE = "#8a6d1f";

/** Контакт в гнезде по стороне пары (hs.mate.contacts): штырь — сплошной металл с бликом торца, гнездо — тёмное окно с
 *  кольцом гнезда; без стороны пары — окно заливкой `hole`, как было. `hw`/`hh` — полуширина и полувысота окна, `sy` —
 *  сжатие круга в ракурсе «top». `data-contact` — по нему тесты видят штыри и гнёзда. */
function contactGlyph(kind: Mate["contacts"] | undefined, x: number, y: number, hw: number, hh: number, round: boolean,
  hole: string, sy = 1, rx = 0): string {
  if (!kind) return round ? dot(x, y, hw, sy, hole) : windowMark(x, y, hw * 2, hh * 2, hole, rx);
  const ring = (r: number) => sy === 1
    ? `<circle cx="${f(x)}" cy="${f(y)}" r="${f(r)}" fill="none" stroke="${PIN}" stroke-width="${f(Math.max(0.8, r * 0.28))}"/>`
    : `<ellipse cx="${f(x)}" cy="${f(y)}" rx="${f(r)}" ry="${f(r * sy)}" fill="none" stroke="${PIN}" stroke-width="${f(Math.max(0.6, r * 0.22))}"/>`;
  const inner = kind === "pin"
    ? round ? dot(x, y, hw, sy, PIN, PIN_EDGE) + dot(x, y, hw * 0.42, sy, shade(PIN, 0.4))
      : windowMark(x, y, hw * 2, hh * 2, PIN, rx) + windowMark(x, y, hw * 0.84, hh * 0.84, shade(PIN, 0.4))
    : (round ? dot(x, y, hw, sy, hole) : windowMark(x, y, hw * 2, hh * 2, hole, rx)) + ring(Math.min(hw, hh / (sy || 1)) * 0.56);
  return `<g data-contact="${kind}">${inner}</g>`;
}

/** Раскладка контактов в мм от левого верхнего угла торца: центр каждого контакта сетки. */
function cavities(hs: ConnectorHousing, pitch: number, wall: number, dsub = false): Array<{ pin: number; x: number; y: number }> {
  const out: Array<{ pin: number; x: number; y: number }> = [];
  const dx = dsub ? pitch / 2 : pitch;
  hs.grid.forEach((row, r) => row.forEach((pin, c) => {
    if (pin) out.push({ pin, x: wall + (dsub ? pitch / 2 : 0) + c * dx + (dsub ? 0 : pitch / 2), y: wall + r * pitch + pitch / 2 });
  }));
  return out;
}

function faceSize(hs: ConnectorHousing, look: Look, pitch: number): [number, number] {
  if (look.shape === "dsub") return [((hs.grid[0]?.length || 9) + 1) * pitch / 2 + look.wall * 2 + pitch * 0.4, 2 * pitch + look.wall * 2 + 0.6];
  if (look.shape === "xt") return [pitch * 2 + look.wall * 2, pitch + look.wall * 2];
  return [hs.cols * pitch + look.wall * 2, hs.rows * pitch + look.wall * 2];
}

function facePitch(hs: ConnectorHousing, look: Look): number {
  if (look.shape === "xt") return hs.family === "amass-qs8" ? 12 : /XT90/i.test(hs.art || "") ? 10.9 : /XT30/i.test(hs.art || "") ? 5 : 7.2;
  return hs.pitch || (look.shape === "dsub" ? 2.77 : /superseal/.test(hs.family || "") ? 3.5 : 2.5);
}

/** Контур торца в мм (для объёма и для плоского вида). */
function outline(look: Look, w: number, h: number): Pt[] {
  const c = Math.min(w, h) * 0.16;
  if (look.shape === "xt") {
    const k = h * 0.32; // скосы с одной стороны — ключ XT/QS
    return [[0, 0], [w - k, 0], [w, k], [w, h - k], [w - k, h], [0, h]];
  }
  if (look.shape === "dsub") {
    const s = h * 0.18; // трапеция «D»
    return [[0, 0], [w, 0], [w - s, h], [s, h]];
  }
  return [[c * 0.3, 0], [w - c * 0.3, 0], [w, c * 0.3], [w, h - c * 0.3], [w - c * 0.3, h], [c * 0.3, h], [0, h - c * 0.3], [0, c * 0.3]];
}

function circlePts(cx: number, cy: number, r: number, n = 36): Pt[] {
  return Array.from({ length: n }, (_, i) => [cx + r * Math.cos((i / n) * 2 * Math.PI), cy + r * Math.sin((i / n) * 2 * Math.PI)] as Pt);
}

/** Призма: торец `front` вытянут назад на (ox, oy); видимые боковые грани светлее сверху и темнее справа. */
export function prism(front: Pt[], ox: number, oy: number, color: string, width = 0.8): string {
  const cx = front.reduce((s, p) => s + p[0], 0) / front.length, cy = front.reduce((s, p) => s + p[1], 0) / front.length;
  const faces: Array<{ pts: Pt[]; light: number; z: number }> = [];
  front.forEach((a, i) => {
    const b = front[(i + 1) % front.length];
    const mx = (a[0] + b[0]) / 2 - cx, my = (a[1] + b[1]) / 2 - cy;
    let nx = b[1] - a[1], ny = -(b[0] - a[0]);
    if (nx * mx + ny * my < 0) { nx = -nx; ny = -ny; }
    if (nx * ox + ny * oy <= 0) return;
    const len = Math.hypot(nx, ny) || 1;
    faces.push({ pts: [a, b, [b[0] + ox, b[1] + oy], [a[0] + ox, a[1] + oy]], light: -ny / len * 0.35 - nx / len * 0.28, z: -(my + mx) });
  });
  return faces.sort((p, q) => p.z - q.z).map((face) => poly(face.pts, shade(color, face.light), EDGE, width)).join("");
}

export function gradient(id: string, color: string): string {
  return `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${shade(color, 0.22)}"/>` +
    `<stop offset="1" stop-color="${shade(color, -0.12)}"/></linearGradient></defs>`;
}

export function gradId(color: string): string {
  return `cgr${color.replace(/[^0-9a-f]/gi, "")}`;
}

/** Ракурс объёмного вида: «iso» — сбоку-сверху; «top» — та же модель, развёрнутая видом сверху (владелец, 16.10: «просто
 *  развернуть»): крыша корпуса с защёлкой, торец проводов с контактами — снизу, в сжатой перспективе. */
export type PictureView = "iso" | "top";
const VIEWS: Record<PictureView, { dx: number; dz: number; sy: number }> = {
  iso: { dx: 0.62, dz: 0.4, sy: 1 },
  top: { dx: 0, dz: 0.87, sy: 0.5 },
};

/** Окно контакта на торце: прямоугольник, в ракурсе «top» сжатый по высоте. */
function windowMark(x: number, y: number, w: number, h: number, fill: string, rx = 0): string {
  return `<rect x="${f(x - w / 2)}" y="${f(y - h / 2)}" width="${f(w)}" height="${f(h)}"${rx ? ` rx="${f(rx)}"` : ""} fill="${fill}"/>`;
}

function dot(x: number, y: number, r: number, sy: number, fill: string, stroke = ""): string {
  const edge = stroke ? ` stroke="${stroke}" stroke-width=".5"` : "";
  return sy === 1 ? `<circle cx="${f(x)}" cy="${f(y)}" r="${f(r)}" fill="${fill}"${edge}/>`
    : `<ellipse cx="${f(x)}" cy="${f(y)}" rx="${f(r)}" ry="${f(r * sy)}" fill="${fill}"${edge}/>`;
}

/** Объёмный вид корпуса разъёма (схема жгута, «База данных → Разъёмы»). Вписывается в maxW × maxH. Круглые разъёмы,
 *  лепестки и «бананы» ракурса «top» не имеют — рисуются как обычно. */
export function connectorPicture(hs: ConnectorHousing, maxW: number, maxH: number, byPin: Record<number, string> | null = null,
  view: PictureView = "iso"): Box {
  const look = connectorLook(hs);
  if (look.shape === "lug" || look.shape === "banana") {
    const s = Math.min(maxW / 2.7, maxH / 1.3);
    return { ...flatSmall(hs, look, s, byPin) };
  }
  const color = hs.color || "#efe6cf";
  if (look.shape === "round" || look.shape === "barrel") {
    const R = look.shape === "barrel" ? 5 : 9, D = look.depth;
    const k = Math.min(maxW / (R * 2 + D * 0.62 + 1), maxH / (R * 2 + D * 0.4 + 1), 7);
    const r = R * k, ox = D * k * 0.6, oy = -D * k * 0.38, cx = r + 1, cy = r - oy + 1;
    let body = prism(circlePts(cx, cy, r), ox, oy, color) + poly(circlePts(cx + ox, cy + oy, r), shade(color, -0.1));
    body += `<circle cx="${f(cx)}" cy="${f(cy)}" r="${f(r)}" fill="${shade(color, 0.08)}" stroke="${EDGE}" stroke-width=".9"/>`;
    if (look.shape === "barrel") {
      body += `<circle cx="${f(cx)}" cy="${f(cy)}" r="${f(r * 0.62)}" fill="#c9ccd0" stroke="${EDGE}" stroke-width=".7"/>` +
        `<circle cx="${f(cx)}" cy="${f(cy)}" r="${f(r * 0.22)}" fill="#e8c35a" stroke="${EDGE}" stroke-width=".6"/>`;
    } else {
      body += `<circle cx="${f(cx)}" cy="${f(cy)}" r="${f(r * 0.78)}" fill="#2b2b2b" stroke="${EDGE}" stroke-width=".6"/>` +
        `<rect x="${f(cx - r * 0.1)}" y="${f(cy - r * 0.82)}" width="${f(r * 0.2)}" height="${f(r * 0.18)}" fill="${shade(color, 0.08)}"/>`;
      (hs.round || []).forEach((p) => {
        const wn = byPin && byPin[p.pin];
        body += `<circle cx="${f(cx + p.x * r * 0.95)}" cy="${f(cy + p.y * r * 0.95)}" r="${f(Math.max(1.4, r * 0.1))}" fill="${wn ? wireColor(wn) : "#d8b454"}"/>`;
      });
    }
    return { w: 2 * r + ox + 2, h: 2 * r - oy + 2, body };
  }
  if (hs.body && hs.rows === 1) return bodyPicture(hs, hs.body, maxW, maxH, byPin, view);
  const { dx, dz, sy } = VIEWS[view];
  const pitch = facePitch(hs, look);
  const [W, H] = faceSize(hs, look, pitch);
  const style = latchStyle(hs, look), latched = hasLatch(look);
  const D = look.depth, latchH = !latched || style === "window" ? 0 : style === "lever" ? H * 0.28 + 0.6 : style === "catch" ? H * 0.12 + 0.2 : H * 0.2 + 0.3;
  // Сторона пары: кожух (outer) у стыковочного конца шире торца проводов — в него входит ответный корпус; входящий
  // корпус (inner) там уже — «нос». Граница — на `split` глубины.
  const shell = look.shape === "rect" ? hs.mate?.shell : undefined, g = shell ? shroudGrow(look) : 0, grow = shell === "outer" ? g : 0, split = 0.45;
  const k = Math.min(maxW / (W + 2 * grow + D * dx), maxH / ((H + 2 * grow + latchH) * sy + D * dz), 16);
  const ox = D * k * dx, oy = -D * k * dz, top = -oy + (latchH + grow) * k * sy + 1, left = 1 + grow * k;
  const front = outline(look, W, H).map(([x, y]) => [x * k + left, y * k * sy + top] as Pt);
  let body = "";
  if (shell) {
    const e = shell === "outer" ? g : -g;
    const mating = outline(look, W + 2 * e, H + 2 * e).map(([x, y]) => [(x - e) * k + left + ox * split, (y - e) * k * sy + top + oy * split] as Pt);
    body += `<g data-shell="${shell}">${prism(mating, ox * (1 - split), oy * (1 - split), shade(color, -0.03))}${poly(mating, shade(color, -0.1))}</g>`;
    body += prism(front, ox * split, oy * split, color);
  } else body += prism(front, ox, oy, color);
  // Замок на верхней грани: рычаг (Mini-Fit, DT, RJ45), выступ-клин (JST XH/PH/GH), плечи на торцах (SM), у пары — по
  // листу: плечо, упор или окно; на кожухе (outer) — на стыковочной части.
  if (latched) {
    const lw = style === "lever" ? Math.min(W * 0.42, 6) : Math.min(W * 0.34, 4), lh = latchH;
    const onShroud = shell === "outer", y0 = top - grow * k * sy, spanW = W + 2 * grow;
    const s0 = onShroud ? (style === "catch" ? 0.72 : split + 0.08) : style === "window" ? 0.3 : 0.18;
    // Упор — короткий выступ у края кожуха, плечо — длинное вдоль корпуса: на виде сверху их не спутать.
    const s1 = style === "window" ? Math.min(0.92, s0 + 0.3) : style === "catch" ? Math.min(0.92, s0 + 0.2)
      : onShroud ? 0.92 : style === "lever" ? 0.85 : look.latch === "ends" ? 0.7 : 0.5;
    const shift = (p: Pt, t: number): Pt => [p[0] + ox * t, p[1] + oy * t];
    body += roofMark("latch", lockMark(hs, latchSpans(look, lw, spanW).map(([lx, w]) => {
      const a = lx * k + 1, b = (lx + w) * k + 1;
      if (style === "window") return poly([shift([a, y0], s0), shift([b, y0], s0), shift([b, y0], s1), shift([a, y0], s1)], shade(color, -0.6), EDGE, 0.5);
      const base: Pt[] = [[a, y0], [b, y0], [b, y0 - lh * k * sy], [a, y0 - lh * k * sy]];
      const side = style === "catch" ? shade(color, -0.14) : shade(color, -0.04), face = style === "catch" ? shade(color, -0.08) : shade(color, 0.05);
      return prism(base.map((p) => shift(p, s0)), ox * (s1 - s0), oy * (s1 - s0), side, 0.6) + poly(base.map((p) => shift(p, s0)), face, EDGE, 0.6);
    }).join("")));
  }
  const dark = isDark(color);
  body += gradient(gradId(color), color) + poly(front, `url(#${gradId(color)})`, EDGE, 0.9);
  if (topFeature(hs) === "slots") {
    // Крыша без замка: прорези ключа вдоль корпуса (у стороны контакта 1).
    const groove = shade(color, -0.5);
    body += roofMark("slots", slotSpans(hs).map(([a, b]) => poly([
      [(a * W) * k + left + ox * 0.1, top + oy * 0.1], [(b * W) * k + left + ox * 0.1, top + oy * 0.1],
      [(b * W) * k + left + ox * 0.9, top + oy * 0.9], [(a * W) * k + left + ox * 0.9, top + oy * 0.9]], groove, groove, 0.3)).join(""));
  }
  if (look.shape === "dsub") {
    const inset = outline(look, W - look.wall * 2, H - look.wall * 2).map(([x, y]) => [(x + look.wall) * k + left, (y + look.wall) * k * sy + top] as Pt);
    body += poly(inset, "#2f3b44", EDGE, 0.6);
  }
  const holes = cavities(hs, pitch, look.wall + (look.shape === "dsub" ? 0.3 : 0), look.shape === "dsub");
  const hw = pitch * 0.34 * k, kind = hs.mate?.contacts;
  holes.forEach((c) => {
    const x = c.x * k + left, y = c.y * k * sy + top, wn = byPin && byPin[c.pin];
    const round = hs.cavity === "round" || look.shape === "xt" || look.shape === "dsub";
    body += contactGlyph(kind, x, y, hw, hw * sy, round, look.shape === "xt" ? "#d8b454" : dark ? "#5d666b" : "#2f3336", sy, round ? 0 : hw * 0.2);
    if (wn) body += dot(x, y, hw * 0.8, sy, wireColor(wn), "#e9eef0");
  });
  return { w: (W + 2 * grow) * k + ox + 2, h: top + (H + grow) * k * sy + 2, body };
}

function flatSmall(hs: ConnectorHousing, look: Look, s: number, byPin: Record<number, string> | null): Box {
  const w0 = byPin && byPin[1];
  if (look.shape === "lug") {
    const body = `<rect x="${f(s * 0.9)}" y="${f(s * 0.3)}" width="${f(s * 1.1)}" height="${f(s * 0.5)}" rx="${f(s * 0.1)}" fill="#d64541" stroke="${EDGE}" stroke-width=".8"/>` +
      `<path d="M${f(s * 2)},${f(s * 0.35)} L${f(s * 2.1)},${f(s * 0.35)} A${f(s * 0.5)},${f(s * 0.5)} 0 1 1 ${f(s * 2.1)},${f(s * 0.75)} L${f(s * 2)},${f(s * 0.75)} z" fill="#e8c35a" stroke="${EDGE}" stroke-width=".8"/>` +
      `<circle cx="${f(s * 2.4)}" cy="${f(s * 0.55)}" r="${f(s * 0.2)}" fill="#fff" stroke="${EDGE}" stroke-width=".6"/>` +
      `<rect x="0" y="${f(s * 0.4)}" width="${f(s * 0.9)}" height="${f(s * 0.3)}" fill="${w0 ? wireColor(w0) : "#6b7c86"}" stroke="${EDGE}" stroke-width=".5"/>`;
    return { w: s * 3, h: s * 1.2, body };
  }
  const body = `<rect x="0" y="${f(s * 0.4)}" width="${f(s * 0.9)}" height="${f(s * 0.3)}" fill="${w0 ? wireColor(w0) : "#6b7c86"}" stroke="${EDGE}" stroke-width=".5"/>` +
    `<rect x="${f(s * 0.9)}" y="${f(s * 0.15)}" width="${f(s * 1.5)}" height="${f(s * 0.8)}" rx="${f(s * 0.15)}" fill="${hs.color}" stroke="${EDGE}" stroke-width=".8"/>` +
    `<rect x="${f(s * 2.4)}" y="${f(s * 0.4)}" width="${f(s * 1.1)}" height="${f(s * 0.3)}" rx="${f(s * 0.12)}" fill="#d9d9d9" stroke="${EDGE}" stroke-width=".7"/>`;
  return { w: s * 3.6, h: s * 1.2, body };
}

/** Торец разъёма со стороны проводов: корпус в масштабе серии, гнёзда, номера, цвета проводов, контакт 1 — красная
 *  метка. `s` — пикселей на шаг контактов. Отдаёт {w, h, body} от (x, y). */
export function connectorFace(hs: ConnectorHousing, x: number, y: number, s: number, byPin: Record<number, string> | null, hits?: FaceHit[]): Box {
  const look = connectorLook(hs);
  if (look.shape === "lug" || look.shape === "banana") {
    const g = flatSmall(hs, look, s, byPin);
    return { w: g.w, h: g.h, body: `<g transform="translate(${f(x)},${f(y)})">${g.body}</g>` };
  }
  const color = hs.color || "#efe6cf", dark = isDark(color), fs = Math.max(6.5, s * 0.34);
  if (look.shape === "round" || look.shape === "barrel") return roundFace(hs, look, x, y, s, byPin, hits);
  if (hs.body) return bodyFace(hs, hs.body, x, y, s, byPin, hits);
  const pitch = facePitch(hs, look), k = s / pitch;
  const [W, H] = faceSize(hs, look, pitch);
  const style = latchStyle(hs, look), latched = hasLatch(look);
  // Кожух стороны пары (outer) виден со стороны проводов ободом вокруг торца — он шире корпуса за ним.
  const grow = look.shape === "rect" && hs.mate?.shell === "outer" ? shroudGrow(look) * k : 0;
  const latchH = latched && style !== "window" ? Math.max(4, s * (style === "catch" ? 0.26 : 0.42)) : 0;
  const rimTop = y + latchH, top = rimTop + grow, x0 = x;
  x += grow;
  let body = "";
  if (latchH) {
    const lw = style === "lever" ? Math.min(W * k * 0.55, s * 2.2) : Math.min(W * k * 0.45, s * 1.5);
    body += roofMark("latch", lockMark(hs, latchSpans(look, lw / k, W + 2 * grow / k).map(([sx, sw]) => {
      const lx = x0 + sx * k, w = sw * k, base = rimTop + 1;
      if (style === "lever") {
        return `<path d="M${f(lx)},${f(base)} L${f(lx)},${f(y + 1)} L${f(lx + w)},${f(y + 1)} L${f(lx + w)},${f(base)}" fill="${shade(color, 0.1)}" stroke="${EDGE}" stroke-width="1"/>` +
          `<line x1="${f(lx + w * 0.2)}" y1="${f(y + latchH * 0.55)}" x2="${f(lx + w * 0.8)}" y2="${f(y + latchH * 0.55)}" stroke="${EDGE}" stroke-width=".7"/>`;
      }
      if (style === "hook") {
        // Плечо с зацепом: стойка от крыши и крючок наружу на её конце.
        const out = lx + w / 2 < x0 + (W * k + 2 * grow) / 2 ? -1 : 1, bar = w * 0.55, bx = out < 0 ? lx + w - bar : lx;
        const tip = out < 0 ? bx - w * 0.45 : bx + bar + w * 0.45, edge = out < 0 ? bx : bx + bar;
        return `<path d="M${f(bx)},${f(base)} L${f(bx)},${f(y + 1)} L${f(bx + bar)},${f(y + 1)} L${f(bx + bar)},${f(base)} z" fill="${shade(color, 0.1)}" stroke="${EDGE}" stroke-width="1"/>` +
          `<path d="M${f(edge)},${f(y + 1)} L${f(tip)},${f(y + 1 + latchH * 0.45)} L${f(edge)},${f(y + 1 + latchH * 0.6)} z" fill="${shade(color, 0.1)}" stroke="${EDGE}" stroke-width="1"/>`;
      }
      const fill = style === "catch" ? shade(color, -0.12) : shade(color, 0.1);
      return `<path d="M${f(lx)},${f(base)} L${f(lx + w * 0.2)},${f(y)} L${f(lx + w * 0.8)},${f(y)} L${f(lx + w)},${f(base)} z" fill="${fill}" stroke="${EDGE}" stroke-width="1"/>`;
    }).join("")));
  }
  if (grow) {
    const rim = outline(look, W + 2 * grow / k, H + 2 * grow / k).map(([px, py]) => [x0 + px * k, rimTop + py * k] as Pt);
    body += `<g data-shell="outer">${poly(rim, shade(color, -0.22), EDGE, 1)}</g>`;
  }
  const front = outline(look, W, H).map(([px, py]) => [x + px * k, top + py * k] as Pt);
  body += gradient(gradId(color), color) + poly(front, `url(#${gradId(color)})`, EDGE, 1.2);
  if (latched && style === "window") {
    // Окно замка в стенке кожуха — проём у верхней кромки.
    const lw = Math.min(W * k * 0.45, s * 1.5), h = Math.max(3, Math.min(6, s * 0.2));
    body += roofMark("latch", lockMark(hs, latchSpans(look, lw / k, W + 2 * grow / k).map(([sx, sw]) =>
      `<rect x="${f(x0 + sx * k)}" y="${f(rimTop)}" width="${f(sw * k)}" height="${f(h + grow)}" fill="${shade(color, -0.6)}" stroke="${EDGE}" stroke-width=".6"/>`).join("")));
  }
  if (topFeature(hs) === "slots") body += faceNotches(hs, x, top, W * k, s, shade(color, -0.5));
  if (look.orig === "friction") {
    // PicoBlade / Milli-Grid: ключ-рёбрышко сбоку корпуса.
    body += `<rect x="${f(x - Math.max(2, s * 0.14))}" y="${f(top + H * k * 0.25)}" width="${f(Math.max(2, s * 0.14))}" height="${f(H * k * 0.5)}" fill="${shade(color, -0.08)}" stroke="${EDGE}" stroke-width=".7"/>`;
  }
  if (look.orig === "mark") {
    body += `<path d="M${f(x + W * k / 2 - 4)},${f(top + 2)} L${f(x + W * k / 2 + 4)},${f(top + 2)} L${f(x + W * k / 2)},${f(top + 7)} z" fill="${shade(color, -0.35)}"/>`;
  }
  if (look.shape === "dsub") {
    const inset = outline(look, W - look.wall * 2, H - look.wall * 2).map(([px, py]) => [x + (px + look.wall) * k, top + (py + look.wall) * k] as Pt);
    body += poly(inset, "#2f3b44", EDGE, 0.8);
  }
  const holes = cavities(hs, pitch, look.wall + (look.shape === "dsub" ? 0.3 : 0), look.shape === "dsub");
  const hw = s * (look.shape === "xt" ? 0.36 : 0.4) * (look.shape === "dsub" ? 0.62 : 1);
  const round = hs.cavity === "round" || look.shape === "xt" || look.shape === "dsub", kind = hs.mate?.contacts;
  // Цифра на металле штыря (и на золотых гнёздах XT / D-Sub без стороны пары) — тёмная, в тёмном окне — светлая.
  const lightCavity = kind ? kind === "pin" : look.shape === "xt" || look.shape === "dsub";
  holes.forEach((c) => {
    const cx = x + c.x * k, cy = top + c.y * k, wn = byPin && byPin[c.pin];
    hits?.push({ pin: c.pin, x: cx, y: cy, r: look.shape === "dsub" ? s * 0.24 : s * 0.5 });
    const hole = round && !kind && (look.shape === "xt" || look.shape === "dsub") ? "#d8b454" : dark ? "#5d666b" : "#2f3336";
    body += contactGlyph(kind, cx, cy, hw, hw, round, hole, 1, round ? 0 : s * 0.08);
    const label = hs.labels ? (hs.labels[c.pin - 1] == null ? c.pin : hs.labels[c.pin - 1]) : c.pin;
    const lfs = look.shape === "dsub" ? fs * 0.8 : fs;
    if (wn) {
      const wc = wireColor(wn);
      body += `<circle cx="${f(cx)}" cy="${f(cy)}" r="${f(hw * 0.86)}" fill="${wc}" stroke="#e9eef0" stroke-width="1"/>` +
        (label !== "" ? `<text x="${f(cx)}" y="${f(cy + lfs * 0.36)}" text-anchor="middle" font-size="${f(lfs)}" font-weight="700" fill="${DARK_WIRE.test(wc) ? "#fff" : EDGE}">${esc(label)}</text>` : "");
    } else if (s >= 14 && label !== "") {
      body += `<text x="${f(cx)}" y="${f(cy + lfs * 0.36)}" text-anchor="middle" font-size="${f(lfs)}" fill="${lightCavity ? EDGE : "#e9eef0"}">${esc(label)}</text>`;
    }
    if (c.pin === 1 && !hs.labels) body += pinOneMark(hs, look, c, x, top, W * k, H * k, k, s);
  });
  return { w: W * k + 2 * grow, h: H * k + latchH + 2 * grow + 6, body };
}

/** Торец, крыша с прорезями: на верхней кромке две выемки ключа у стороны контакта 1. */
function faceNotches(hs: ConnectorHousing, x: number, top: number, width: number, s: number, fill: string): string {
  const h = Math.max(2, Math.min(5, s * 0.14));
  return roofMark("slots", slotSpans(hs).map(([a, b]) =>
    `<rect x="${f(x + a * width)}" y="${f(top)}" width="${f((b - a) * width)}" height="${f(h)}" fill="${fill}" stroke="${EDGE}" stroke-width=".5"/>`).join(""));
}

function pinOneMark(hs: ConnectorHousing, look: Look, c: { x: number; y: number }, x: number, top: number, W: number, H: number, k: number, s: number): string {
  const tw = Math.max(3, s * 0.2), cx = x + c.x * k, cy = top + c.y * k;
  const edgeCol = look.shape === "dsub" ? false : hs.grid.some((row) => row[0] === 1) || hs.grid.some((row) => row[row.length - 1] === 1);
  if (edgeCol) {
    const last = hs.grid.some((row) => row[row.length - 1] === 1) && !hs.grid.some((row) => row[0] === 1);
    const tx = last ? x + W + 1 : x - 1, d = last ? 1 : -1;
    return `<path d="M${f(tx + d * tw * 1.5)},${f(cy - tw)} L${f(tx + d * tw * 1.5)},${f(cy + tw)} L${f(tx)},${f(cy)} z" fill="#d7322d"/>`;
  }
  const ty = cy < top + H / 2 ? top - 1 : top + H + 1, dir = cy < top + H / 2 ? 1 : -1;
  return `<path d="M${f(cx - tw)},${f(ty - dir * tw * 1.4)} L${f(cx + tw)},${f(ty - dir * tw * 1.4)} L${f(cx)},${f(ty)} z" fill="#d7322d"/>`;
}

function roundFace(hs: ConnectorHousing, look: Look, x: number, y: number, s: number, byPin: Record<number, string> | null, hits?: FaceHit[]): Box {
  const R = s * 2.2, cx = x + R + 2, cy = y + R + 2, pr = Math.max(2.5, s * 0.36), fs = Math.max(6.5, s * 0.34);
  if (look.shape === "barrel") {
    const lab = hs.labels || ["центр", "корпус"];
    const w1 = byPin && byPin[1], w2 = byPin && byPin[2];
    const body = `<circle cx="${f(cx)}" cy="${f(cy)}" r="${f(R)}" fill="${hs.color}" stroke="#000" stroke-width="1.2"/>` +
      `<circle cx="${f(cx)}" cy="${f(cy)}" r="${f(R * 0.62)}" fill="${w2 ? wireColor(w2) : "#c9ccd0"}" stroke="${EDGE}" stroke-width="1"/>` +
      `<circle cx="${f(cx)}" cy="${f(cy)}" r="${f(R * 0.24)}" fill="${w1 ? wireColor(w1) : "#e8c35a"}" stroke="${EDGE}" stroke-width=".8"/>` +
      `<text x="${f(cx + R + 6)}" y="${f(cy - 4)}" font-size="${f(fs)}" fill="${EDGE}">1 — ${esc(lab[0])}</text>` +
      `<text x="${f(cx + R + 6)}" y="${f(cy + fs + 2)}" font-size="${f(fs)}" fill="${EDGE}">2 — ${esc(lab[1])}</text>`;
    return { w: R * 2 + 4 + fs * 6, h: R * 2 + 10, body };
  }
  let body = `<circle cx="${f(cx)}" cy="${f(cy)}" r="${f(R)}" fill="${hs.color}" stroke="#000" stroke-width="1.2"/>` +
    `<circle cx="${f(cx)}" cy="${f(cy)}" r="${f(R * 0.86)}" fill="${isDark(hs.color) ? "#3a4147" : "#2b2b2b"}" stroke="#000" stroke-width=".6"/>` +
    `<rect x="${f(cx - s * 0.3)}" y="${f(cy - R - 1)}" width="${f(s * 0.6)}" height="${f(s * 0.45)}" fill="#fff" stroke="#000" stroke-width="1"/>`;
  (hs.round || []).forEach((c) => {
    const px = cx + c.x * R, py = cy + c.y * R, wn = byPin && byPin[c.pin], wc = wn ? wireColor(wn) : "#c8a24a";
    hits?.push({ pin: c.pin, x: px, y: py, r: pr * 1.1 });
    body += `<circle cx="${f(px)}" cy="${f(py)}" r="${f(pr)}" fill="${wc}" stroke="#e9eef0" stroke-width="1"/>`;
    if (s >= 12) body += `<text x="${f(px)}" y="${f(py - pr - 2)}" text-anchor="middle" font-size="${f(fs)}" fill="#e9eef0">${c.pin}</text>`;
  });
  return { w: R * 2 + 4, h: R * 2 + 4 + 6, body };
}

/** Ширина торца в шагах контактов — по ней эскиз и база подбирают масштаб, чтобы корпус влез целиком. */
export function connectorFaceUnits(hs: ConnectorHousing): number {
  const pitch = hs.pitch || 2.5;
  if (hs.body) return bodyWidth(hs, hs.body, pitch) / pitch;
  return Math.max(hs.cols + (hs.kind === "dsub" ? 1 : 0), 1) / (hs.kind === "dsub" ? 2 : 1);
}

export function bodyWidth(hs: ConnectorHousing, b: HousingBody, pitch: number): number {
  return (hs.cols - 1) * pitch + b.extra + 2 * (b.ears || 0);
}

/** Центры окон по x (мм от левого края корпуса) в порядке колонок сетки. */
export function bodyColumns(hs: ConnectorHousing, b: HousingBody, pitch: number): number[] {
  const x0 = (b.ears || 0) + b.extra / 2;
  return Array.from({ length: hs.cols }, (_, c) => x0 + c * pitch);
}

/** Ширина защёлки на верхней грани (мм): своя ширина или корпус без отступов lock_inset; 0 — защёлки нет. */
export function lockWidth(b: HousingBody, W: number): number {
  if (!b.lock_h) return 0;
  return b.lock_w ? Math.min(b.lock_w, W) : Math.max(1.5, W - 2 * (b.lock_inset || 0));
}

/** Торец корпуса по чертежу (JST SH, GH, XH, PH, ZH, Micro-Fit): прямоугольник B × h, выступы по бокам, защёлка на
 *  верхней грани, прямоугольные окна контактов по сетке, у PH/ZH — окна фиксаторов на нижней грани, у SH — метка ▽
 *  над контактом 1; красная стрелка у контакта 1. */
function bodyFace(hs: ConnectorHousing, b: HousingBody, x: number, y: number, s: number, byPin: Record<number, string> | null, hits?: FaceHit[]): Box {
  const pitch = hs.pitch || 1, k = s / pitch, color = hs.color || "#efe6cf", dark = isDark(color);
  const W = bodyWidth(hs, b, pitch), H = b.h, ears = b.ears || 0, fs = Math.max(6.5, Math.min(s * 0.34, 15));
  const [winW, winH] = b.win && b.win.length === 2 ? b.win : [pitch * 0.6, H * 0.45];
  const centered = hs.roof?.kind === "latch" && hs.roof.at === "center", ends = hs.roof?.kind === "latch" && hs.roof.at === "ends";
  const lw = centered ? lockWidth(b, W) : 0, lockH = lw ? b.lock_h || 0 : 0, endsH = ends ? Math.max(4, s * 0.3) : 0;
  const markH = lockH ? lockH * k + 1 : endsH ? endsH + 1 : hs.latch === "mark" ? Math.max(7, s * 0.2) : 2;
  const slotsBelow = hs.latch === "windows" && hs.model === "slots"; // PH/EH/ZH: фиксаторы на нижней грани — их и показывает рисунок
  const slotH = hs.latch === "windows" ? Math.max(3, Math.min(0.7, H * 0.2) * k) : 0;
  const top = y + markH, X = (mm: number) => x + mm * k, Y = (mm: number) => top + mm * k;
  let body = gradient(gradId(color), color);
  if (lockH) {
    // Защёлка по чертежу: рычаг/рамка на верхней грани, по центру корпуса.
    const lx = X((W - lw) / 2);
    body += roofMark("latch", lockMark(hs, `<rect x="${f(lx)}" y="${f(y + 1)}" width="${f(lw * k)}" height="${f(lockH * k + 1)}" rx="${f(Math.min(2, k * 0.15))}" fill="${shade(color, 0.08)}" stroke="${EDGE}" stroke-width="1"/>` +
      `<line x1="${f(lx + lw * k * 0.2)}" y1="${f(y + 1 + lockH * k * 0.5)}" x2="${f(lx + lw * k * 0.8)}" y2="${f(y + 1 + lockH * k * 0.5)}" stroke="${EDGE}" stroke-width=".7"/>`));
  }
  if (endsH) {
    // Замок плечами на торцах корпуса: два выступа над верхней гранью по краям.
    const ew = Math.max(3, Math.min(W * 0.14, 2) * k);
    body += roofMark("latch", lockMark(hs, [X(0), X(W) - ew].map((ex) =>
      `<rect x="${f(ex)}" y="${f(y + 1)}" width="${f(ew)}" height="${f(endsH + 1)}" rx="1" fill="${shade(color, 0.08)}" stroke="${EDGE}" stroke-width="1"/>`).join("")));
  }
  if (ears) {
    body += `<rect x="${f(X(0))}" y="${f(Y(0))}" width="${f(ears * k)}" height="${f(H * k)}" fill="${shade(color, -0.07)}" stroke="${EDGE}" stroke-width="1"/>` +
      `<rect x="${f(X(W - ears))}" y="${f(Y(0))}" width="${f(ears * k)}" height="${f(H * k)}" fill="${shade(color, -0.07)}" stroke="${EDGE}" stroke-width="1"/>`;
  }
  body += `<rect x="${f(X(ears))}" y="${f(Y(0))}" width="${f((W - 2 * ears) * k)}" height="${f(H * k)}" rx="${f(Math.min(2, k * 0.1))}" fill="url(#${gradId(color)})" stroke="${EDGE}" stroke-width="1.2"/>`;
  const columns = bodyColumns(hs, b, pitch), rows = Math.max(1, hs.grid.length);
  if (slotH) {
    // PH/ZH: грань с окнами фиксаторов контактов (на ZH там же отлиты номера) — снизу, по окну на контакт.
    const windows = columns.map((cxMm) =>
      `<rect x="${f(X(cxMm) - winW * k / 2)}" y="${f(Y(H) - slotH)}" width="${f(winW * k)}" height="${f(slotH)}" fill="${shade(color, -0.4)}"/>`).join("");
    body += topFeature(hs) === "slots" ? roofMark("slots", windows) : windows;
  }
  if (topFeature(hs) === "slots" && !slotH) body += faceNotches(hs, X(ears), Y(0), (W - 2 * ears) * k, s, shade(color, -0.5));
  hs.grid.forEach((row, r) => columns.forEach((cxMm, c) => {
    const pin = row[c];
    if (!pin) return;
    const cx = X(cxMm), cy = Y(H / 2 + (r - (rows - 1) / 2) * pitch), wn = byPin && byPin[pin];
    hits?.push({ pin, x: cx, y: cy, r: s * 0.5 });
    body += contactGlyph(hs.mate?.contacts, cx, cy, winW * k / 2, winH * k / 2, false, dark ? "#5d666b" : "#2f3336", 1, Math.min(2, k * 0.06));
    const rad = Math.min(winW, winH) * k * 0.46;
    if (wn) {
      const wc = wireColor(wn);
      body += `<circle cx="${f(cx)}" cy="${f(cy)}" r="${f(rad)}" fill="${wc}" stroke="#e9eef0" stroke-width="1"/>` +
        `<text x="${f(cx)}" y="${f(cy + fs * 0.36)}" text-anchor="middle" font-size="${f(fs)}" font-weight="700" fill="${DARK_WIRE.test(wc) ? "#fff" : EDGE}">${pin}</text>`;
    } else if (s >= 14) {
      body += `<text x="${f(cx)}" y="${f(cy + fs * 0.36)}" text-anchor="middle" font-size="${f(fs)}" fill="${hs.mate?.contacts === "pin" ? EDGE : "#e9eef0"}">${pin}</text>`;
    }
    if (pin === 1) {
      const t = Math.max(3.5, s * 0.14), tw = Math.max(3, s * 0.2);
      // SH: ▽ отлита на верхней грани над контактом 1 (как на чертеже). Красная стрелка — у крайнего окна.
      if (hs.latch === "mark") body += `<path d="M${f(cx - t)},${f(y + 1)} L${f(cx + t)},${f(y + 1)} L${f(cx)},${f(top - 1)} z" fill="${shade(color, -0.45)}"/>`;
      const right = c > (hs.cols - 1) / 2, ex = right ? X(W) + 2 : X(0) - 2, d = right ? 1 : -1;
      body += `<path d="M${f(ex + d * tw * 1.5)},${f(cy - tw)} L${f(ex + d * tw * 1.5)},${f(cy + tw)} L${f(ex)},${f(cy)} z" fill="#d7322d"/>`;
    }
  }));
  return { w: W * k, h: H * k + markH + 6, body };
}

/** Объёмный корпус по чертежу: торец проводов B × h (с выступами), за ним стыковочная часть потоньше (rear_h),
 *  утопленная с грани метки; на ней окна фиксаторов контактов; метка ▽ над контактом 1. */
function bodyPicture(hs: ConnectorHousing, b: HousingBody, maxW: number, maxH: number, byPin: Record<number, string> | null,
  view: PictureView): Box {
  const { dx, dz, sy } = VIEWS[view];
  const pitch = hs.pitch || 1, color = hs.color || "#efe6cf", dark = isDark(color);
  const W = bodyWidth(hs, b, pitch), H = b.h, L = b.len, ears = b.ears || 0;
  const rearH = Math.min(b.rear_h || H, H), fl = Math.min(b.front_len || L * 0.35, L), el = Math.min(b.ear_len || fl, fl);
  const lw = hs.roof?.kind === "latch" && hs.roof.at === "center" ? lockWidth(b, W) : 0, lockH = lw ? b.lock_h || 0 : 0;
  const k = Math.min(maxW / (W + L * dx), maxH / ((H + lockH) * sy + L * dz), 16), ox = dx * k, oy = -dz * k;
  const top = -oy * L + lockH * k * sy + 1;
  const P = (mx: number, my: number, d: number): Pt => [mx * k + 1 + ox * d, my * k * sy + top + oy * d];
  const rect = (x0: number, y0: number, x1: number, y1: number, d: number): Pt[] => [P(x0, y0, d), P(x1, y0, d), P(x1, y1, d), P(x0, y1, d)];
  const box = (x0: number, y0: number, x1: number, y1: number, d0: number, d1: number, fill: string) => {
    const front = rect(x0, y0, x1, y1, d0);
    return prism(front, ox * (d1 - d0), oy * (d1 - d0), fill) + poly(front, fill, EDGE, 0.8);
  };
  const core0 = ears, core1 = W - ears, columns = bodyColumns(hs, b, pitch), row = hs.grid[0] || [];
  // Стыковочная часть: тоньше, прижата к нижней грани; у SH сверху — окна фиксаторов по контактам.
  let body = box(core0, H - rearH, core1, H, fl, L, shade(color, -0.04));
  // Сторона пары: у этих корпусов стыковочная часть и есть «нос», входящий в ответный (PicoBlade 51021).
  if (hs.mate?.shell) body = `<g data-shell="${hs.mate.shell}">${body}</g>`;
  const [winW, winH] = b.win && b.win.length === 2 ? b.win : [pitch * 0.6, H * 0.45];
  if (hs.latch === "mark") columns.forEach((cx) => {
    body += poly([P(cx - winW / 2, H - rearH, fl + 0.5), P(cx + winW / 2, H - rearH, fl + 0.5), P(cx + winW / 2, H - rearH, L - 0.4), P(cx - winW / 2, H - rearH, L - 0.4)],
      dark ? "#5d666b" : shade(color, -0.3), EDGE, 0.4);
  });
  if (topFeature(hs) === "slots") {
    // Крыша без замка (PicoBlade): прорези ключа на длинной грани за фланцем.
    const groove = shade(color, -0.5), y = H - rearH, wd = core1 - core0;
    body += roofMark("slots", slotSpans(hs).map(([a, b]) => poly([P(core0 + a * wd, y, fl + 0.3), P(core0 + b * wd, y, fl + 0.3),
      P(core0 + b * wd, y, L - 0.4), P(core0 + a * wd, y, L - 0.4)], groove, groove, 0.3)).join(""));
  }
  body += box(core0, 0, core1, H, 0, fl, color);
  if (ears) body += box(0, 0, ears, H, 0, el, shade(color, -0.05)) + box(W - ears, 0, W, H, 0, el, shade(color, -0.05));
  // Защёлка на верхней грани (GH, XH): по центру, от торца проводов вдоль корпуса.
  if (lockH) body += roofMark("latch", lockMark(hs, box((W - lw) / 2, -lockH, (W + lw) / 2, 0, L * 0.1, L * 0.75, shade(color, 0.04))));
  body += gradient(gradId(color), color) + poly(rect(core0, 0, core1, H, 0), `url(#${gradId(color)})`, EDGE, 0.9);
  columns.forEach((cx, c) => {
    const pin = row[c], wn = pin && byPin && byPin[pin];
    const [px, py] = P(cx, H / 2, 0);
    body += contactGlyph(hs.mate?.contacts, px, py, winW * k / 2, winH * k * sy / 2, false, dark ? "#5d666b" : "#2f3336", sy);
    if (wn) body += dot(px, py, Math.min(winW, winH) * k * 0.42, sy, wireColor(wn), "#e9eef0");
    if (pin === 1 && hs.latch === "mark") {
      const t = pitch * 0.5;
      body += poly([P(cx - t / 2, 0, 0.3), P(cx + t / 2, 0, 0.3), P(cx, 0, 0.3 + t)], shade(color, -0.45), shade(color, -0.45), 0.3);
    }
  });
  return { w: W * k + ox * L + 2, h: top + H * k * sy + 2, body };
}

/** Подпись под рисунком: серия, шаг, число контактов — «JST GH · 1,25 мм · 4 конт.». */
export function connectorSummary(hs: ConnectorHousing): string {
  const parts = [String(hs.title || "")];
  if (hs.pitch && hs.kind !== "round") parts.push(`${String(hs.pitch).replace(".", ",")} мм`);
  if (hs.n && hs.kind !== "lug" && hs.kind !== "banana") parts.push(`${hs.n} конт.`);
  const contacts = hs.mate?.contacts ?? (hs.male != null && (hs.kind === "dsub" || hs.family === "deutsch-dt") ? (hs.male ? "pin" : "socket") : null);
  if (contacts) parts.push(contacts === "pin" ? "штыри" : "гнёзда");
  return parts.filter(Boolean).join(" · ");
}
