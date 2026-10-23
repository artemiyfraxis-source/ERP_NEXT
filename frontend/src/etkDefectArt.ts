// Библиотека рисунков типовых дефектов ЭТК: на каждый дефект пара «Норма | Брак» в стиле эскиза операции
// (провод, жила, терминал, стакан, трубка — плоские технические рисунки, место брака обведено красным).
// Одна на все ТК: рисунок выбирается при показе по названию дефекта и коду операции, поэтому выданные ТК
// перевыпускать не нужно. Названия дефектов берутся из шаблонов SUPPLYMAP (БД.ТК «ТИПОВЫЕ ДЕФЕКТЫ»).

import { STO_SCENES } from "./etkStoScenes";

const E = "#17272b";
const INS = "#7f919b", INS_LO = "#5d6e78";
const CU = "#c87533", CU_HI = "#eba66e", CU_LO = "#8a4a1c";
const TIN = "#d4d9dd", TIN_HI = "#f4f6f7", TIN_LO = "#98a2a9";
const SOLDER = "#c9ced3";
const PLAST = "#efe6cf", PLAST_E = "#8a7d5c";
const TUBE = "#3b4147";
const RED = "#d93025", GREEN = "#188038", MUTED = "#5f7470";

type Ctx = "cut" | "tube" | "strip" | "coax" | "coaxSolder" | "coaxAsm" | "coaxCrimp" | "tin" | "crimp" | "lug" | "solder" | "shrink" | "mark" | "markAuto"
  | "assembly" | "mold" | "weld" | "other" | MarkCtx;
/** Маркировка по видам меток (etk_marking, владелец 12.10): своя группа дефектов на вид — свои рисунки. */
type MarkCtx = "markFlag" | "markHeat" | "markPvc" | "markTag" | "markRing" | "markPrint";
/** Заголовок группы дефектов маркировки (backend/data/etk-marking.json `title`) → вид. «Прочие метки» — общие рисунки. */
const MARK_GROUPS: Record<string, MarkCtx> = {
  "Хлястик": "markFlag", "Термоусадочная трубка": "markHeat", "ПВХ-трубка": "markPvc", "Бирка": "markTag",
  "Маркировочные кольца": "markRing", "Печать на изоляции": "markPrint",
};

// ── примитивы ────────────────────────────────────────────────────────────────────────────────────────────
const rect = (x: number, y: number, w: number, h: number, fill: string, extra = "") =>
  `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${fill}"${extra}/>`;
const path = (d: string, fill = "none", stroke = E, sw = 0.8, extra = "") =>
  `<path d="${d}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"${extra}/>`;
const line = (x1: number, y1: number, x2: number, y2: number, stroke = E, sw = 0.8, extra = "") =>
  `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${stroke}" stroke-width="${sw}"${extra}/>`;
const text = (x: number, y: number, value: string, fill = MUTED, size = 8, anchor = "middle", weight = 400) =>
  `<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" text-anchor="${anchor}" font-weight="${weight}" font-family="sans-serif">${value}</text>`;
const circle = (cx: number, cy: number, r: number, fill: string, stroke = E, sw = 0.6) =>
  `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;

/** Красная пунктирная обводка места брака. */
const mark = (cx: number, cy: number, rx: number, ry = rx, color = RED) =>
  `<ellipse cx="${cx}" cy="${cy}" rx="${rx}" ry="${ry}" fill="none" stroke="${color}" stroke-width="1.4" stroke-dasharray="3 2"/>`;
/** Размерная линия с засечками; подпись над ней. */
function dim(x1: number, x2: number, y: number, label: string, color = MUTED): string {
  return line(x1, y, x2, y, color, 0.8) + line(x1, y - 3, x1, y + 3, color, 0.8) + line(x2, y - 3, x2, y + 3, color, 0.8) +
    text((x1 + x2) / 2, y - 3, label, color, 7.5);
}
function vdim(x: number, y1: number, y2: number, label: string, color = MUTED): string {
  return line(x, y1, x, y2, color, 0.8) + line(x - 3, y1, x + 3, y1, color, 0.8) + line(x - 3, y2, x + 3, y2, color, 0.8) +
    text(x + 5, (y1 + y2) / 2 + 3, label, color, 7.5, "start");
}
const arrow = (x1: number, y1: number, x2: number, y2: number, color = RED) => {
  const a = Math.atan2(y2 - y1, x2 - x1), h = 4;
  const p1 = `${x2 - h * Math.cos(a - 0.5)},${y2 - h * Math.sin(a - 0.5)}`, p2 = `${x2 - h * Math.cos(a + 0.5)},${y2 - h * Math.sin(a + 0.5)}`;
  return line(x1, y1, x2, y2, color, 1.3) + `<path d="M${x2},${y2} L${p1} L${p2} z" fill="${color}"/>`;
};

/** Изоляция провода x1..x2, по центру y. */
function ins(x1: number, x2: number, y = 34, h = 12, fill = INS): string {
  return rect(x1, y - h / 2, x2 - x1, h, fill, ` rx="2.5" stroke="${E}" stroke-width=".7"`) +
    rect(x1 + 2, y - h / 2 + 1.6, Math.max(0, x2 - x1 - 4), h * 0.2, "#fff", ` rx="1" opacity=".3"`);
}
/** Скрученная медная жила x1..x2. */
function cond(x1: number, x2: number, y = 34, h = 6): string {
  let lay = "";
  for (let x = x1 + 1; x < x2 - 2; x += 3.2) lay += line(x, y + h / 2, x + 2.4, y - h / 2, CU_LO, 0.5);
  return rect(x1, y - h / 2, x2 - x1, h, CU, ` stroke="${CU_LO}" stroke-width=".5"`) + lay +
    line(x1 + 1, y - h / 2 + 1.2, x2 - 1, y - h / 2 + 1.2, CU_HI, 0.8);
}
/** Лужёная жила (серебристая). */
function tinned(x1: number, x2: number, y = 34, h = 6): string {
  return rect(x1, y - h / 2, x2 - x1, h, SOLDER, ` rx="1.5" stroke="${TIN_LO}" stroke-width=".6"`) +
    line(x1 + 1.5, y - h / 2 + 1.3, x2 - 1.5, y - h / 2 + 1.3, "#fff", 1);
}
/** Прямые отдельные проволоки (раскрученные). */
function strandsFlat(x1: number, x2: number, y = 34, h = 6, n = 4): string {
  let out = "";
  for (let i = 0; i < n; i += 1) out += line(x1, y - h / 2 + 0.8 + (i * (h - 1.6)) / (n - 1), x2, y - h / 2 + 0.8 + (i * (h - 1.6)) / (n - 1), CU, 1.3);
  return out;
}
/** Трубка (ТУТ/кембрик) x1..x2 высотой h. */
function tube(x1: number, x2: number, y = 34, h = 16, fill = TUBE): string {
  return rect(x1, y - h / 2, x2 - x1, h, fill, ` rx="3" stroke="${E}" stroke-width=".7"`) +
    rect(x1 + 2, y - h / 2 + 2, x2 - x1 - 4, 2.2, "#fff", ` rx="1" opacity=".22"`);
}
/** Открытый терминал сбоку: хвостовик по изоляции, окно, обжим по жиле, контактная часть (как эскиз опрессовки). */
function terminal(x: number, y = 34, opts: { wings?: "ok" | "open" | "bite"; box?: "ok" | "bent"; lance?: "ok" | "broken" } = {}): string {
  const wings = opts.wings || "ok";
  let out = rect(x - 2, y + 8, 88, 3, TIN, ` stroke="${E}" stroke-width=".5"`); // основание
  if (wings === "open") out += path(`M${x},${y + 8} L${x - 3},${y - 14} L${x + 3},${y - 14} L${x + 8},${y + 8} M${x + 12},${y + 8} L${x + 15},${y - 14} L${x + 20},${y - 14} L${x + 18},${y + 8}`, TIN, E, 0.7);
  else out += path(`M${x},${y + 9} L${x},${y - 7} Q${x + 10},${y - 11} ${x + 20},${y - 7} L${x + 20},${y + 9} z`, TIN, E, 0.7) +
    (wings === "bite" ? path(`M${x + 6},${y - 9} L${x + 9},${y - 1} L${x + 12},${y - 9}`, "none", RED, 1.3) : line(x + 10, y - 9, x + 10, y + 8, E, 0.5, ' stroke-dasharray="2 2" opacity=".5"'));
  out += path(`M${x + 28},${y + 9} L${x + 28},${y - 5} Q${x + 38},${y - 8} ${x + 48},${y - 5} L${x + 48},${y + 9} z`, TIN, E, 0.7) +
    line(x + 29, y - 1, x + 47, y - 1, TIN_LO, 0.5);
  if (opts.box === "bent") out += path(`M${x + 58},${y + 9} L${x + 64},${y + 9} L${x + 92},${y - 6} L${x + 88},${y - 14} L${x + 62},${y - 2} L${x + 58},${y - 2} z`, TIN, E, 0.8);
  else out += rect(x + 58, y - 8, 34, 17, TIN, ` rx="1" stroke="${E}" stroke-width=".8"`) + line(x + 58, y - 4, x + 92, y - 4, TIN_LO, 0.5);
  out += path(`M${x + 48},${y + 9} L${x + 52},${y + 9} L${x + 56},${y + 3} L${x + 58},${y + 3}`, "none", E, 0.6);
  if (opts.box !== "bent") {
    if (opts.lance === "broken") out += path(`M${x + 70},${y - 8} L${x + 66},${y - 9}`, "none", E, 0.8) + path(`M${x + 64},${y - 12} l-3,-3 m2,5 l-4,0`, "none", RED, 1);
    else out += path(`M${x + 74},${y - 8} L${x + 64},${y - 14} L${x + 62},${y - 12} L${x + 70},${y - 8} z`, TIN, E, 0.6);
  }
  return out;
}
/** Разрез обжима по жиле (B-обжим): плотный — жилы заполняют лунки, слабый — пустоты. */
function crimpSection(cx: number, cy: number, mode: "ok" | "loose" | "over"): string {
  const w = mode === "over" ? 26 : 20, top = mode === "loose" ? cy - 17 : mode === "over" ? cy - 9 : cy - 13;
  const bottom = mode === "over" ? cy + 9 : cy + 12;
  const shell = `M${cx - w},${top + 6} C${cx - w},${bottom + 2} ${cx + w},${bottom + 2} ${cx + w},${top + 6}` +
    ` M${cx - w},${top + 6} C${cx - w},${top - 2} ${cx - w / 2 - 3},${top - 3} ${cx - w / 2 + 1},${top + 4} C${cx - 3},${top + 10} ${cx - 1},${cy - 2} ${cx},${cy - 1}` +
    ` M${cx + w},${top + 6} C${cx + w},${top - 2} ${cx + w / 2 + 3},${top - 3} ${cx + w / 2 - 1},${top + 4} C${cx + 3},${top + 10} ${cx + 1},${cy - 2} ${cx},${cy - 1}`;
  // Проволоки укладываются сотами внутри контура оболочки (дно втулки и два загнутых крыла, сходящихся к центру) и не
  // задевают стенку — ни одна не выходит за контур. Слабый обжим — с пустотами.
  const r = mode === "over" ? 1.4 : 1.7, clear = r + 2;
  const bez = (a: [number, number], b: [number, number], c: [number, number], d: [number, number]) =>
    Array.from({ length: 24 }, (_, i) => {
      const s = (i + 1) / 24, u = 1 - s, k = [u ** 3, 3 * u * u * s, 3 * u * s * s, s ** 3];
      return [k[0] * a[0] + k[1] * b[0] + k[2] * c[0] + k[3] * d[0], k[0] * a[1] + k[1] * b[1] + k[2] * c[1] + k[3] * d[1]] as [number, number];
    });
  const wing = (sx: number) => [
    ...bez([cx + sx * w, top + 6], [cx + sx * w, top - 2], [cx + sx * (w / 2 + 3), top - 3], [cx + sx * (w / 2 - 1), top + 4]),
    ...bez([cx + sx * (w / 2 - 1), top + 4], [cx + sx * 3, top + 10], [cx + sx, cy - 2], [cx, cy - 1]),
  ];
  const contour: Array<[number, number]> = [[cx - w, top + 6],
    ...bez([cx - w, top + 6], [cx - w, bottom + 2], [cx + w, bottom + 2], [cx + w, top + 6]), ...wing(1), ...wing(-1).reverse()];
  const inside = (x: number, y: number) => {
    let hit = false;
    for (let i = 0, j = contour.length - 1; i < contour.length; j = i, i += 1) {
      const [xi, yi] = contour[i], [xj, yj] = contour[j];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
    }
    return hit;
  };
  const gap = (x: number, y: number) => Math.min(...contour.map(([ax, ay], i) => {
    const [bx, by] = contour[(i + 1) % contour.length], dx = bx - ax, dy = by - ay;
    const q = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy || 1)));
    return Math.hypot(x - ax - q * dx, y - ay - q * dy);
  }));
  const pts: Array<[number, number]> = [], step = 2 * r;
  for (let row = 0, y = bottom; y > top; row += 1, y -= step * 0.866) {
    for (let x = cx - w + (row % 2 ? step / 2 : 0); x <= cx + w; x += step) if (inside(x, y) && gap(x, y) >= clear) pts.push([x, y]);
  }
  let strands = "";
  pts.forEach(([x, y], i) => { if (mode !== "loose" || i % 3 !== 1) strands += circle(x, y, r, CU, CU_LO, 0.5); });
  let out = strands + path(shell, "none", TIN_LO, 3.6) + path(shell, "none", TIN, 2);
  if (mode === "over") out += path(`M${cx - w},${cy + 4} l-8,-1 l0,4 z M${cx + w},${cy + 4} l8,-1 l0,4 z`, TIN, RED, 1) + mark(cx - w - 4, cy + 5, 7, 5) + mark(cx + w + 4, cy + 5, 7, 5);
  if (mode === "loose") out += mark(cx - 3, cy + 1, 10, 8);
  return out;
}
/** Стакан под пайку (разрез сбоку), провод заходит слева. level: 0..1 заполнение. */
function cup(x: number, y = 36): string {
  return rect(x, y - 8, 38, 16, TIN_LO, ` rx="2" stroke="${E}" stroke-width=".7"`) + // задняя стенка
    rect(x + 38, y - 10, 12, 20, TIN, ` stroke="${E}" stroke-width=".7"`) + // тело контакта
    rect(x, y + 4, 38, 5, TIN, ` stroke="${E}" stroke-width=".7"`); // передняя (нижняя) стенка
}
/** Корпус разъёма сбоку. */
function housing(x: number, y: number, w: number, h: number, fill = PLAST): string {
  return rect(x, y, w, h, fill, ` rx="2" stroke="${PLAST_E}" stroke-width=".9"`) + line(x + 2, y + 2.5, x + w - 2, y + 2.5, "#fff", 1.2, ' opacity=".6"');
}
/** Кабель с оболочкой и оплёткой (разделка). */
function braid(x1: number, x2: number, y = 34, h = 12): string {
  let hatch = "";
  for (let x = x1; x < x2 - 1; x += 3) hatch += line(x, y - h / 2, Math.min(x + 3, x2), y + h / 2, "#8d9297", 0.7) + line(Math.min(x + 3, x2), y - h / 2, x, y + h / 2, "#8d9297", 0.7);
  return rect(x1, y - h / 2, x2 - x1, h, "#e2e5e8", ` stroke="${E}" stroke-width=".6"`) + hatch;
}

/** Пайка пина коаксиала сбоку: оболочка, оплётка, диэлектрик, центральная жила в хвостовике пина (хвостовик в разрезе,
 *  отверстие под пайку), дальше буртик и рабочая часть пина. */
type CoaxPin = { diel?: number; fill?: "ok" | "cold" | "lack" | "excess"; tilt?: boolean; melt?: boolean; bent?: boolean; strays?: boolean };
function coaxPinJoint(o: CoaxPin = {}): string {
  const d = o.diel ?? 50, fill = o.fill || "ok";
  let cable = ins(2, 16, 34, 18, "#3f474d") + braid(16, 26, 34, 14) + ins(26, d, 34, 10, "#f4f4f2");
  if (o.melt) cable += path(`M${d - 14},29 Q${d - 8},22 ${d - 1},27 Q${d + 2},34 ${d - 1},41 Q${d - 8},46 ${d - 14},39 z`, "#c9b184", "#8a6d3b", 0.7) +
    circle(d - 8, 31, 1.3, "#6b4f2a", "none", 0) + circle(d - 5, 37, 1.1, "#6b4f2a", "none", 0);
  cable += cond(d, 57, 34, 3.4);
  if (o.tilt) cable = `<g transform="rotate(7 56 34)">${cable}</g>`;
  const solderColor = fill === "cold" ? "#a4aaae" : SOLDER;
  // Отверстие в разрезе: припой заполняет его целиком, жила просвечивает; при недостатке видна пустота и жила.
  let bore = rect(56, 30.4, 20, 7.2, "#fff");
  if (fill === "lack") bore += rect(56, 35.6, 20, 2, SOLDER) + cond(56, 74, 34, 3.4);
  else bore += rect(56, 30.4, 20, 7.2, solderColor) + `<g opacity=".4">${cond(56, 74, 34, 3.4)}</g>`;
  if (fill === "cold") bore += [59, 63, 67, 71, 61, 65, 69].map((x, i) => circle(x, i < 4 ? 31.8 : 36.4, 0.8, "#7d8488", "none", 0)).join("");
  let fillet = "";
  if (fill === "ok" || fill === "excess") fillet = path("M56,30.4 Q56,32.3 51.5,32.3 L51.5,35.7 Q56,35.7 56,37.6 z", SOLDER, TIN_LO, 0.5);
  if (fill === "cold") fillet = path("M56,29.6 C51,27.5 48.5,31 50.5,34 C48.5,37 51,40.5 56,38.4 z", "#a4aaae", TIN_LO, 0.6);
  const walls = rect(56, 28, 20, 2.4, TIN, ` stroke="${E}" stroke-width=".6"`) + rect(56, 37.6, 20, 2.4, TIN, ` stroke="${E}" stroke-width=".6"`) +
    rect(76, 28, 6, 12, TIN, ` stroke="${E}" stroke-width=".6"`);
  const tipPart = rect(86, 31.5, 22, 5, TIN, ` stroke="${E}" stroke-width=".6"`) + path("M108,31.5 L113,34 L108,36.5 z", TIN, E, 0.6) + line(87, 32.6, 107, 32.6, "#fff", 0.8);
  const tip = (o.bent ? `<g transform="rotate(-16 86 34)">${tipPart}</g>` : tipPart) + rect(82, 29, 4, 10, TIN_HI, ` stroke="${E}" stroke-width=".6"`);
  let excess = "";
  if (fill === "excess") excess = path("M50,34 C50,24 58,21 66,22 C74,21 82,24 81,30 L81,38 C82,45 72,47 64,46 C56,46 50,42 50,34 z", SOLDER, TIN_LO, 0.7) +
    path("M66,22 L68,13 L70,22", SOLDER, TIN_LO, 0.6) + path("M56,25 C62,23 70,23 76,25", "none", "#fff", 1.1);
  let strays = "";
  if (o.strays) strays = path("M51,33 Q54,26 63,24", "none", CU, 1.2) + path("M52,35 Q56,43 66,45", "none", CU, 1.2) + path("M52,34 Q55,29 60,27.5", "none", CU, 1);
  return cable + bore + fillet + walls + tip + excess + strays;
}

/** Лужёный конец провода (SDR_tin): припой покрывает проволоки, повив виден сквозь него; dull — матовый, без блеска. */
function tinEnd(x1: number, x2: number, y = 34, h = 6, o: { dull?: boolean; lay?: boolean } = {}): string {
  let out = rect(x1, y - h / 2, x2 - x1, h, o.dull ? "#a4aaae" : SOLDER, ` rx="${h / 2}" stroke="${TIN_LO}" stroke-width=".6"`);
  if (o.lay !== false) for (let x = x1 + 2; x < x2 - 3; x += 3.2) out += line(x, y + h / 2 - 0.6, x + 2.4, y - h / 2 + 0.6, TIN_LO, 0.5);
  if (!o.dull) out += line(x1 + 2, y - h / 2 + 1.3, x2 - 3, y - h / 2 + 1.3, "#fff", 1);
  return out;
}
/** Провод с лужёным концом: изоляция, у её торца зазор без припоя (0,5–1,5 мм), дальше облуженная жила. */
const TN = { ins: () => ins(4, 50), gap: () => cond(50, 55), end: (o: { dull?: boolean; lay?: boolean } = {}) => tinEnd(55, 104, 34, 6, o) };
const tinOk = () => TN.ins() + TN.gap() + TN.end();

/** Концы провода для эскиза операции резки (etkDraw.etkDrawWires) — те же рисунки, что в «Норма | Брак». crimp — обжатый
 *  терминал, провод слева: крыло по изоляции x = 0..20, изоляция выходит из него и кончается в зазоре (до x = 26), жила
 *  до x = 52, торец контактной части x = 92. tin / copper — лужёная и голая жила x1..x2. */
export const wireEndArt = {
  crimp: (y: number) => cond(26, 52, y, 6) + terminal(0, y),
  tin: (x1: number, x2: number, y: number, h = 6) => tinEnd(x1, x2, y, h),
  copper: (x1: number, x2: number, y: number, h = 6) => cond(x1, x2, y, h),
};

// ── маркировка по видам: хлястик, трубка-маркер, бирка, кольца, надпись на изоляции ─────────────────────────────
const LABEL = "#fbfaf5", LABEL_E = "#8a8f94";
type Ink = { text?: string; ink?: string; blur?: boolean };
/** Надпись метки; blur — смазана: бледная, с мазком поверх. */
function inscription(x: number, y: number, o: Ink, size = 8.5): string {
  const value = o.text ?? "X1";
  if (!o.blur) return text(x, y, value, o.ink || E, size, "middle", 700);
  return text(x, y, value, "#b0b6ba", size, "middle", 700) + path(`M${x - 10},${y - 2} q5,-5 10,0 t10,0`, "none", "#9aa0a6", 2.2);
}
/** Хлястик на проводе (провод по y = 34): петля-шейка вокруг провода и флажок над ним с надписью. loose — петля
 *  шире провода и флажок завалился; tear — надрыв и залом флажка. */
function flagTag(x: number, o: Ink & { loose?: boolean; tear?: boolean } = {}): string {
  const neck = o.loose ? rect(x - 1, 22, 11, 24, "none", ` rx="3" stroke="${LABEL_E}" stroke-width="1.3"`)
    : rect(x, 27, 9, 14, LABEL, ` rx="1.5" stroke="${LABEL_E}" stroke-width=".7"`);
  let flag = rect(x + 1, 22, 7, 6, LABEL) + rect(x - 12, 5, 34, 18, LABEL, ` rx="2" stroke="${LABEL_E}" stroke-width=".7"`) +
    inscription(x + 5, 17.5, o);
  if (o.tear) flag += path(`M${x + 22},9 l-7,3 l6,3`, "#fdf3f2", RED, 1) + line(x - 12, 21, x + 2, 6, LABEL_E, 0.8, ' stroke-dasharray="2 2"');
  return neck + (o.loose ? `<g transform="rotate(26 ${x + 4.5} 24)">${flag}</g>` : flag);
}
/** Трубка-маркер с надписью на проводе x1..x2; h — диаметр (не усажена — шире провода); burn — прожог при усадке. */
function markTube(x1: number, x2: number, o: Ink & { h?: number; burn?: boolean } = {}): string {
  const h = o.h ?? 15;
  let out = rect(x1, 34 - h / 2, x2 - x1, h, LABEL, ` rx="3" stroke="${LABEL_E}" stroke-width=".8"`) +
    rect(x1 + 2, 34 - h / 2 + 1.6, x2 - x1 - 4, 1.8, "#fff", ` rx="1" opacity=".7"`) + inscription((x1 + x2) / 2, 37.2, o);
  if (o.burn) out += `<ellipse cx="${x1 + 9}" cy="31" rx="6" ry="3.6" fill="#3a2a1e"/>` + circle(x2 - 9, 37, 1.8, "#c9b184", "#8a6d3b", 0.5) +
    circle(x2 - 14, 30, 1.4, "#c9b184", "#8a6d3b", 0.5);
  return out;
}
/** Пучок проводов (y 12…26) со стяжкой и биркой на нити под ним; loose — стяжка не затянута, бирка висит косо. */
function bundleTag(x: number, o: Ink & { loose?: boolean } = {}): string {
  const bundle = [12, 19, 26].map((y) => line(4, y, 112, y, INS, 5.5)).join("");
  const tie = o.loose ? rect(x - 1, 4, 9, 30, "none", ` rx="3" stroke="#2a2e33" stroke-width="2"`) : rect(x, 7, 7, 24, "#2a2e33", ` rx="2"`);
  const tag = line(x + 3.5, 30, x + 3.5, 38, "#2a2e33", 1.2) + rect(x - 13.5, 38, 34, 19, LABEL, ` rx="2" stroke="${LABEL_E}" stroke-width=".7"`) +
    circle(x + 3.5, 41.5, 1.2, "#fff", LABEL_E, 0.5) + inscription(x + 3.5, 52, o);
  return bundle + tie + (o.loose ? `<g transform="rotate(24 ${x + 3.5} 30)">${tag}</g>` : tag);
}
/** Маркировочные кольца на проводе: по знаку на кольцо, «?» — кольца нет (пустое место пунктиром). */
function markRings(x: number, chars: string[], h = 15, step = 11): string {
  return chars.map((char, index) => {
    const left = x + index * step;
    return char === "?"
      ? rect(left, 34 - h / 2, 10, h, "none", ` rx="2" stroke="${RED}" stroke-dasharray="2 2" stroke-width="1.1"`) + text(left + 5, 37.5, "?", RED, 9, "middle", 700)
      : rect(left, 34 - h / 2, 10, h, LABEL, ` rx="2" stroke="${LABEL_E}" stroke-width=".7"`) + text(left + 5, 37.5, char, E, 8.5, "middle", 700);
  }).join("");
}
const flagWire = () => ins(4, 112);
const endWire = () => ins(4, 100) + cond(100, 112);

const MARK_SCENES: Record<string, () => Scene> = {
  flagText: () => ({
    ok: flagWire() + flagTag(52, { text: "КБ1" }) + text(58, 62, "надпись по КД", GREEN),
    ng: flagWire() + flagTag(52, { text: "КБ2", ink: RED }) + mark(57, 14, 20, 11) + text(58, 62, "по КД: КБ1", RED),
  }),
  flagBlur: () => ({
    ok: flagWire() + flagTag(52, { text: "КБ1" }),
    ng: flagWire() + flagTag(52, { text: "КБ1", blur: true }) + mark(57, 14, 20, 11) + text(58, 62, "смазано", RED),
  }),
  flagShift: () => ({
    ok: endWire() + flagTag(78) + dim(87, 100, 50, "", GREEN) + text(82, 64, "по КД", GREEN),
    ng: endWire() + flagTag(22) + arrow(40, 54, 76, 54) + mark(27, 22, 22, 18),
  }),
  flagLoose: () => ({
    ok: flagWire() + flagTag(52) + text(58, 62, "сидит плотно", GREEN),
    ng: flagWire() + flagTag(52, { loose: true }) + mark(58, 26, 24, 20) + text(58, 64, "болтается", RED),
  }),
  flagTear: () => ({
    ok: flagWire() + flagTag(52),
    ng: flagWire() + flagTag(52, { tear: true }) + mark(70, 12, 9, 8) + text(58, 62, "надрыв, залом", RED),
  }),
  tubeLen: () => ({
    ok: flagWire() + markTube(42, 76) + dim(42, 76, 54, "L по КД", GREEN),
    ng: flagWire() + markTube(42, 100) + dim(42, 100, 54, "L ≠ КД", RED),
  }),
  tubeText: () => ({
    ok: flagWire() + markTube(40, 80, { text: "X1" }) + text(60, 62, "надпись по КД", GREEN),
    ng: flagWire() + markTube(40, 80, { text: "X2", ink: RED }) + mark(60, 34, 24, 12) + text(60, 62, "по КД: X1", RED),
  }),
  tubeBlur: () => ({
    ok: flagWire() + markTube(40, 80),
    ng: flagWire() + markTube(40, 80, { blur: true }) + mark(60, 34, 24, 12) + text(60, 62, "смазано", RED),
  }),
  tubeLoose: () => ({
    ok: flagWire() + markTube(40, 80, { h: 14 }) + text(60, 62, "усажена плотно", GREEN),
    ng: flagWire() + markTube(36, 84, { h: 24 }) + mark(60, 21, 26, 5) + text(60, 62, "не усажена", RED),
  }),
  tubeSlide: () => ({
    ok: endWire() + markTube(58, 88) + text(58, 60, "на своём месте", GREEN),
    ng: endWire() + markTube(84, 112) + arrow(56, 54, 82, 54) + mark(98, 34, 16, 12) + text(36, 62, "сползла", RED),
  }),
  tubeHeat: () => ({
    ok: flagWire() + markTube(40, 80),
    ng: flagWire() + markTube(40, 80, { burn: true }) + mark(56, 33, 22, 12) + text(60, 62, "перегрев", RED),
  }),
  tubeShift: () => ({
    ok: endWire() + markTube(66, 92) + text(58, 62, "позиция по КД", GREEN),
    ng: endWire() + markTube(16, 42) + arrow(48, 54, 80, 54) + mark(29, 34, 18, 12),
  }),
  tagText: () => ({
    ok: bundleTag(52, { text: "W1" }),
    ng: bundleTag(52, { text: "W2", ink: RED }) + mark(56, 48, 20, 11),
  }),
  tagBlur: () => ({
    ok: bundleTag(52, { text: "W1" }),
    ng: bundleTag(52, { text: "W1", blur: true }) + mark(56, 48, 20, 11),
  }),
  tagShift: () => ({
    ok: bundleTag(84) + text(30, 62, "по КД", GREEN),
    ng: bundleTag(22) + arrow(48, 64, 82, 64) + mark(26, 46, 21, 13),
  }),
  tagLoose: () => ({
    ok: bundleTag(52),
    ng: bundleTag(52, { loose: true }) + mark(56, 32, 24, 26),
  }),
  ringOrder: () => ({
    ok: flagWire() + markRings(42, ["X", "1", "2"]) + text(58, 62, "порядок по КД", GREEN),
    ng: flagWire() + markRings(42, ["X", "2", "1"]) + mark(64, 34, 14, 12) + text(58, 62, "по КД: X12", RED),
  }),
  ringMissing: () => ({
    ok: flagWire() + markRings(42, ["X", "1", "2"]),
    ng: flagWire() + markRings(42, ["X", "?", "2"]) + text(58, 62, "нет кольца", RED),
  }),
  ringShift: () => ({
    ok: endWire() + markRings(64, ["X", "1", "2"]) + text(58, 62, "позиция по КД", GREEN),
    ng: endWire() + markRings(12, ["X", "1", "2"]) + arrow(50, 54, 80, 54) + mark(28, 34, 20, 12),
  }),
  ringSlide: () => ({
    ok: endWire() + markRings(52, ["X", "1", "2"], 14),
    ng: endWire() + markRings(78, ["X", "1", "2"], 22, 12) + arrow(48, 56, 74, 56) + mark(97, 34, 18, 14) + text(36, 64, "сползли", RED),
  }),
  printText: () => ({
    ok: ins(4, 112, 34, 14) + text(58, 37.5, "X1-3", "#fff", 9, "middle", 700),
    ng: ins(4, 112, 34, 14) + text(58, 37.5, "X1-8", RED, 9, "middle", 700) + mark(58, 34, 18, 10) + text(58, 62, "по КД: X1-3", RED),
  }),
  printBlur: () => ({
    ok: ins(4, 112, 34, 14) + text(58, 37.5, "X1-3", "#fff", 9, "middle", 700),
    ng: ins(4, 112, 34, 14) + text(58, 37.5, "X1-3", "#a9b4ba", 9, "middle", 700) + path("M44,36 q7,-6 14,0 t14,0", "none", "#c3cbd0", 2.4) +
      mark(58, 34, 18, 10) + text(58, 62, "стёрлась", RED),
  }),
};

// ── печать и резка маркировочной трубки (INS_MARK_TUBE_AUTO, маркиратор ZCUT-RT660; владелец 17.10): светлые отрезки с
// напечатанным текстом, не тёмная трубка резки ────────────────────────────────────────────────────────────────
/** Отрезок маркировочной трубки с текстом x1..x2 (y 34, высота 15) — уже отрезанный, без провода. */
const autoPiece = (x1: number, x2: number, o: Ink & { y?: number; h?: number } = {}) => {
  const y = o.y ?? 34, h = o.h ?? 15;
  return rect(x1, y - h / 2, x2 - x1, h, LABEL, ` rx="3" stroke="${LABEL_E}" stroke-width=".8"`) +
    rect(x1 + 2, y - h / 2 + 1.6, x2 - x1 - 4, 1.8, "#fff", ` rx="1" opacity=".7"`) + inscription((x1 + x2) / 2, y + 3.2, o);
};
const AUTO_TEXT = "A6";
const MARK_AUTO_SCENES: Record<string, () => Scene> = {
  autoText: () => ({
    ok: autoPiece(30, 86, { text: AUTO_TEXT }) + text(58, 60, "текст по КД", GREEN),
    ng: autoPiece(30, 86, { text: "A9", ink: RED }) + mark(58, 34, 24, 12) + text(58, 60, "по КД: A6", RED),
  }),
  autoBlur: () => ({
    ok: autoPiece(30, 86, { text: AUTO_TEXT }) + text(58, 60, "печать чёткая", GREEN),
    ng: autoPiece(30, 86, { text: AUTO_TEXT, blur: true }) + mark(58, 34, 24, 12) + text(58, 60, "смазано, бледно", RED),
  }),
  autoShift: () => ({
    ok: autoPiece(24, 92, { text: AUTO_TEXT }) + line(58, 20, 58, 48, GREEN, 0.7, ' stroke-dasharray="3 2"') + text(58, 60, "по центру отрезка", GREEN),
    ng: rect(24, 26.5, 68, 15, LABEL, ` rx="3" stroke="${LABEL_E}" stroke-width=".8"`) + text(82, 37.2, AUTO_TEXT, E, 8.5, "middle", 700) +
      line(92, 20, 92, 48, RED, 1, ' stroke-dasharray="2 2"') + mark(84, 34, 12, 12) + text(58, 60, "у самого реза", RED),
  }),
  autoLength: () => ({
    ok: autoPiece(20, 96, { text: AUTO_TEXT }) + dim(20, 96, 54, "L по КД", GREEN),
    ng: autoPiece(20, 70, { text: AUTO_TEXT }) + rect(70, 26.5, 26, 15, "none", ` rx="3" stroke="${RED}" stroke-dasharray="3 2"`) + dim(20, 70, 54, "L ≠ КД", RED),
  }),
  autoCut: () => ({
    ok: autoPiece(14, 92, { text: AUTO_TEXT }) + text(58, 58, "торец ровный, под 90°", GREEN),
    ng: path("M14,26.5 L96,26.5 L91,29.5 L93.5,32.5 L88,35.5 L89,38.5 L80,41.5 L14,41.5 z", LABEL, LABEL_E, 0.8, ' stroke-linejoin="round"') +
      text(50, 37.2, AUTO_TEXT, E, 8.5, "middle", 700) + mark(88, 34, 11, 12) + text(58, 58, "косой, рваный рез", RED),
  }),
  autoDirt: () => ({
    ok: autoPiece(30, 86, { text: AUTO_TEXT }) + text(58, 60, "чистая, ровная", GREEN),
    ng: autoPiece(30, 86, { text: AUTO_TEXT }) + `<ellipse cx="44" cy="31" rx="5" ry="2.4" fill="#5c6166" opacity=".55"/>` +
      `<ellipse cx="74" cy="38" rx="4" ry="2" fill="#5c6166" opacity=".55"/>` + path("M56,26.5 q4,5.5 8,0", "#e8e8e4", "#8a8f94", 1) +
      mark(58, 34, 28, 12) + text(58, 60, "следы ленты, вмятины", RED),
  }),
  autoSize: () => ({
    ok: ins(4, 112, 34, 9) + autoPiece(38, 82, { text: AUTO_TEXT }) + text(58, 60, "размер и цвет по КД", GREEN),
    ng: ins(4, 112, 34, 7) + rect(34, 21, 52, 26, "#f2c200", ` rx="4" stroke="${E}" stroke-width=".8"`) + text(60, 37.5, AUTO_TEXT, E, 9, "middle", 700) +
      mark(60, 34, 30, 17) + text(58, 62, "не тот размер и цвет", RED),
  }),
  autoMix: () => {
    const piece = (x: number, y: number, label: string, ink = E) =>
      rect(x, y, 24, 10, LABEL, ` rx="2" stroke="${LABEL_E}" stroke-width=".7"`) + text(x + 12, y + 7.2, label, ink, 7, "middle", 700);
    const tray = rect(10, 24, 96, 32, "#e3e6e8", ` rx="3" stroke="${E}" stroke-width=".8"`) + rect(10, 24, 96, 5, "#c9ced2", ` rx="2"`);
    return {
      ok: tray + piece(16, 32, AUTO_TEXT) + piece(44, 32, AUTO_TEXT) + piece(72, 32, AUTO_TEXT) + piece(30, 44, AUTO_TEXT) + piece(58, 44, AUTO_TEXT) +
        text(58, 68, "в таре один текст", GREEN),
      ng: tray + piece(16, 32, AUTO_TEXT) + piece(44, 32, "I3", RED) + piece(72, 32, AUTO_TEXT) + piece(30, 44, "C", RED) + piece(58, 44, AUTO_TEXT) +
        mark(58, 40, 46, 17) + text(58, 68, "тексты вперемешку", RED),
    };
  },
};

// ── коаксиальный кабель (резка, разделка, корпус, втулка экрана; владелец 15.10 о ТК КБ1 796К.2: «картинки говно» —
// резка и разделка коакса рисовались серым проводом с голой медью, втулка — открытым терминалом) ──────────────
const JACKET = "#2b3136";
/** Кабель в оболочке x1..x2 (сбоку). */
function jacket(x1: number, x2: number, y = 34, h = 18): string {
  return rect(x1, y - h / 2, x2 - x1, h, JACKET, ` rx="2.5" stroke="${E}" stroke-width=".7"`) +
    rect(x1 + 2, y - h / 2 + 2, Math.max(0, x2 - x1 - 4), 2.4, "#fff", ` rx="1" opacity=".18"`);
}
/** Торец коаксиала: оболочка, оплётка, диэлектрик, жила из 7 проволок; splay — проволоки разошлись. */
function coaxFace(cx: number, cy: number, r: number, o: { splay?: boolean; burr?: boolean; ragged?: boolean } = {}): string {
  const outer = o.ragged
    ? path(Array.from({ length: 18 }, (_, i) => {
      const a = (i / 18) * 2 * Math.PI, rr = r * (i % 2 ? 1.12 : 0.94) + (i % 5 === 0 ? 2.2 : 0);
      return `${i ? "L" : "M"}${(cx + rr * Math.cos(a)).toFixed(1)},${(cy + rr * Math.sin(a)).toFixed(1)}`;
    }).join(" ") + " z", JACKET, E, 0.6)
    : circle(cx, cy, r, JACKET, E, 0.6);
  const core = r * 0.2, strand = core / 3;
  const strands = [[0, 0], ...Array.from({ length: 6 }, (_, i) => [Math.cos((i * Math.PI) / 3), Math.sin((i * Math.PI) / 3)])]
    .map(([dx, dy], i) => {
      const spread = o.splay && i ? 2.6 + (i % 3) * 0.6 : 1;
      return circle(cx + dx * strand * 2 * spread, cy + dy * strand * 2 * spread, strand, CU, CU_LO, 0.4);
    }).join("");
  return outer + `<circle cx="${cx}" cy="${cy}" r="${r * 0.8}" fill="#b9c0c5" stroke="#8d9297" stroke-width=".6"/>` +
    circle(cx, cy, r * 0.62, "#f4f4f2", E, 0.5) + strands +
    (o.burr ? path(`M${cx + core},${cy - 1.5} L${cx + r * 0.55},${cy - 4} L${cx + core},${cy + 1.5} z`, CU, CU_LO, 0.5) : "");
}
/** Разделанный конец (кабель слева, торец справа): оболочка до a, оплётка до b, диэлектрик до c, жила до d. */
function coaxEnd(a = 40, b = 58, c = 80, d = 104): string {
  return jacket(4, a) + braid(a, b, 34, 14) + ins(b, c, 34, 9, "#f4f4f2") + cond(c, d, 34, 4);
}
/** Корпус коаксиального разъёма сбоку (металл) x1..x2 с хвостовиком до x3. */
function coaxBody(x1: number, x2: number, x3: number, y = 34): string {
  return path(`M${x1},${y - 14} L${x2},${y - 14} L${x2},${y - 8} L${x3},${y - 8} L${x3},${y + 8} L${x2},${y + 8} L${x2},${y + 14} L${x1},${y + 14} z`, TIN, TIN_LO, 0.8) +
    line(x1 + 2, y - 11, x2 - 2, y - 11, "#fff", 1.2, ' opacity=".7"') + line(x1 + (x2 - x1) * 0.45, y - 14, x1 + (x2 - x1) * 0.45, y + 14, TIN_LO, 0.6);
}
/** Шестигранная втулка экрана сбоку x1..x2. */
function hexSleeve(x1: number, x2: number, y = 34): string {
  return path(`M${x1 + 3},${y - 11} L${x2 - 3},${y - 11} L${x2},${y} L${x2 - 3},${y + 11} L${x1 + 3},${y + 11} L${x1},${y} z`, TIN, TIN_LO, 0.8) +
    line(x1 + 2, y - 4, x2 - 2, y - 4, TIN_LO, 0.5) + line(x1 + 2, y + 4, x2 - 2, y + 4, TIN_LO, 0.5);
}
/** Сечение обжатой втулки: шестигранник вокруг оплётки, диэлектрика и жилы. */
function hexSection(cx: number, cy: number, r: number, mode: "ok" | "loose" | "flash"): string {
  const hex = (rr: number) => Array.from({ length: 6 }, (_, i) => {
    const a = (i * Math.PI) / 3 + Math.PI / 6;
    return `${i ? "L" : "M"}${(cx + rr * Math.cos(a)).toFixed(1)},${(cy + rr * Math.sin(a)).toFixed(1)}`;
  }).join(" ") + " z";
  const inner = circle(cx, cy, r * 0.62, "#b9c0c5", "#8d9297", 0.5) + circle(cx, cy, r * 0.42, "#f4f4f2", E, 0.5) + circle(cx, cy, r * 0.1, CU, CU_LO, 0.4);
  if (mode === "loose") return circle(cx, cy, r * 0.98, TIN, TIN_LO, 0.8) + circle(cx, cy, r * 0.8, "#fff", "none", 0) + inner;
  let out = path(hex(r), TIN, TIN_LO, 0.9) + circle(cx, cy, r * 0.66, "#8d9297", "none", 0) + inner;
  if (mode === "flash") out += path(`M${cx + r * 0.86},${cy - 3} L${cx + r * 0.86 + 9},${cy - 1.5} L${cx + r * 0.86 + 9},${cy + 1.5} L${cx + r * 0.86},${cy + 3} z`, TIN, RED, 0.9) +
    path(`M${cx - r * 0.86},${cy - 3} L${cx - r * 0.86 - 9},${cy - 1.5} L${cx - r * 0.86 - 9},${cy + 1.5} L${cx - r * 0.86},${cy + 3} z`, TIN, RED, 0.9);
  return out;
}
/** Обрезанная трубка ТУТ сбоку x1..x2: торец справа ровный, рваный или косой. */
function tubeCut(x1: number, x2: number, end: "flat" | "ragged" | "skew" = "flat", y = 34, h = 18): string {
  if (end === "skew") return path(`M${x1},${y - h / 2} L${x2},${y - h / 2} L${x2 - 12},${y + h / 2} L${x1},${y + h / 2} z`, TUBE, E, 0.7);
  if (end === "ragged") return path(`M${x1},${y - h / 2} L${x2},${y - h / 2} L${x2 - 4},${y - 5} L${x2 + 3},${y - 2} L${x2 - 3},${y + 1} L${x2 + 2},${y + 4} L${x2 - 2},${y + h / 2} L${x1},${y + h / 2} z`, TUBE, E, 0.7);
  return tube(x1, x2, y, h);
}

/** Крупный план разделанного конца коаксиала (жила, диэлектрик, оплётка). */
const STRIP_VIEW = "36 12 76 47";

const COAX_SCENES: Record<string, () => Scene> = {
  // Резка коаксиального кабеля: сбоку кабель, рядом торец.
  cxJacketNick: () => ({
    ok: jacket(6, 110) + text(58, 58, "оболочка целая", GREEN),
    ng: jacket(6, 110) + path("M46,25 L54,36 L62,25 z", "#b9c0c5", RED, 1) + path("M72,28 l12,3 M74,32 l10,3", "none", "#fff", 0.9) + mark(56, 31, 12, 10) + text(58, 58, "до оплётки", RED),
  }),
  cxJacketCrush: () => ({
    ok: jacket(4, 66) + coaxFace(92, 34, 14) + text(58, 60, "сечение круглое", GREEN),
    ng: path("M4,25 L22,25 Q32,30 42,30 Q52,30 66,25 L66,43 Q52,38 42,38 Q32,38 22,43 L4,43 z", JACKET, E, 0.7) + mark(40, 34, 14, 9) +
      `<ellipse cx="92" cy="34" rx="18" ry="8" fill="${JACKET}"/><ellipse cx="92" cy="34" rx="14" ry="5.5" fill="#b9c0c5"/><ellipse cx="92" cy="34" rx="10" ry="3.6" fill="#f4f4f2"/>` +
      circle(92, 34, 1.6, CU, CU_LO, 0.4) + text(92, 60, "сплющено", RED),
  }),
  cxFaceRagged: () => ({
    ok: jacket(6, 64) + coaxFace(90, 34, 17) + text(58, 64, "торец ровный", GREEN),
    ng: jacket(6, 60) + path("M60,25 L66,27 L61,30 L67,33 L61,36 L66,40 L60,43", JACKET, E, 0.6) + coaxFace(90, 34, 17, { ragged: true }) + mark(90, 34, 21, 21) + text(40, 64, "рваный край", RED),
  }),
  cxSkew: () => ({
    ok: jacket(8, 90) + line(90, 25, 90, 43, "#b9c0c5", 2) + line(96, 18, 96, 50, GREEN, 0.8, ' stroke-dasharray="3 2"') + text(96, 60, "90°", GREEN),
    ng: path("M8,25 L96,25 L84,43 L8,43 z", JACKET, E, 0.7) + line(96, 25, 84, 43, "#b9c0c5", 2) + line(91, 31, 89, 34, CU, 2) +
      line(98, 18, 98, 50, MUTED, 0.6, ' stroke-dasharray="3 2"') + mark(90, 34, 9, 11) + text(96, 60, "≠ 90°", RED),
  }),
  cxPulled: () => ({
    ok: jacket(8, 94) + line(94, 32, 94, 36, CU, 2.4) + text(52, 60, "жила заподлицо", GREEN),
    ng: jacket(8, 94) + path("M94,32 L106,29 M94,34 L110,35 M94,36 L102,40", "none", CU, 1.3) + mark(102, 34, 10, 9) + text(52, 60, "проволоки торчат", RED),
  }),
  cxFaceSplay: () => ({
    ok: coaxFace(58, 32, 24) + text(58, 66, "повив жилы целый", GREEN),
    ng: coaxFace(58, 32, 24, { splay: true }) + mark(58, 32, 11, 11) + text(58, 66, "проволоки разошлись", RED),
  }),
  cxBurr: () => ({
    ok: coaxFace(58, 32, 24) + text(58, 66, "кромки гладкие", GREEN),
    ng: coaxFace(58, 32, 24, { burr: true }) + mark(65, 30, 9, 7) + text(58, 66, "острая кромка", RED),
  }),
  cxLength: () => ({
    ok: jacket(8, 106) + dim(8, 106, 54, "L по КД", GREEN),
    ng: jacket(8, 84) + rect(84, 25, 22, 18, "none", ` rx="2.5" stroke="${RED}" stroke-dasharray="3 2"`) + dim(8, 84, 54, "L ≠ КД", RED),
  }),
  // Разделка коаксиала: всё на разделанном конце — жила, диэлектрик, оплётка, оболочка.
  cxCoreNick: () => ({
    view: STRIP_VIEW,
    ok: coaxEnd(),
    ng: coaxEnd() + path("M88,31 L91,35 L94,31 z", "#fdf3f2", RED, 0.9) + path("M98,33 l7,-7", "none", CU, 1.2) + mark(93, 32, 10, 8),
  }),
  cxCoreSplay: () => ({
    view: STRIP_VIEW,
    ok: coaxEnd(),
    ng: coaxEnd(40, 58, 80, 80) + path("M80,33 C88,26 98,25 108,27 M80,34 C90,32 100,33 110,33 M80,35 C88,41 98,43 108,41", "none", CU, 1.2) + mark(95, 34, 15, 11),
  }),
  cxDielCut: () => ({
    view: STRIP_VIEW,
    ok: coaxEnd(),
    ng: coaxEnd() + path("M64,29.5 l3,5 M70,29.5 l2,4 M66,38.5 l3,-3", "none", RED, 1) + mark(68, 34, 9, 8),
  }),
  cxDielDeform: () => ({
    view: STRIP_VIEW,
    ok: coaxEnd() + dim(58, 80, 20, "Ø по КД", GREEN),
    ng: jacket(4, 40) + braid(40, 58, 34, 14) + path("M58,29.5 Q69,33 80,31 L80,37 Q69,35 58,38.5 z", "#f4f4f2", E, 0.7) + cond(80, 104, 34, 4) + mark(69, 34, 11, 7) + dim(58, 80, 20, "сужен", RED),
  }),
  cxBraidResidue: () => ({
    view: STRIP_VIEW,
    ok: coaxEnd(),
    ng: coaxEnd() + path("M58,30 L70,31 M58,38 L74,36.5 M60,31 L82,33.5", "none", "#8d9297", 1) + mark(70, 34, 13, 7),
  }),
  cxBraidCut: () => ({
    view: STRIP_VIEW,
    ok: coaxEnd(),
    ng: coaxEnd() + rect(44, 27, 3, 14, "#fff") + rect(50, 27, 2.5, 14, "#fff") + path("M47,27 l2,-5 M52,41 l2,5", "none", "#8d9297", 1) + mark(49, 34, 8, 11),
  }),
  // Резка ТУТ.
  tubeRagged: () => ({
    ok: tubeCut(10, 92) + text(58, 60, "торец ровный", GREEN),
    ng: tubeCut(10, 88, "ragged") + mark(89, 34, 8, 11) + text(52, 60, "рваный край", RED),
  }),
  tubeSlit: () => ({
    ok: tubeCut(10, 92),
    ng: tubeCut(10, 92) + path("M92,30 L76,31.5", "none", "#fff", 1.4) + path("M92,30 L76,31.5", "none", RED, 0.6) + mark(84, 31, 11, 6) + text(52, 60, "надрез вдоль трубки", RED),
  }),
  tubeSkew: () => ({
    ok: tubeCut(10, 92) + line(96, 20, 96, 48, GREEN, 0.8, ' stroke-dasharray="3 2"') + text(96, 60, "90°", GREEN),
    ng: tubeCut(10, 96, "skew") + line(98, 20, 98, 48, MUTED, 0.6, ' stroke-dasharray="3 2"') + mark(90, 34, 8, 11) + text(96, 60, "≠ 90°", RED),
  }),
  tubeLength: () => ({
    ok: tubeCut(20, 96) + dim(20, 96, 54, "L по КД", GREEN),
    ng: tubeCut(20, 78) + rect(78, 25, 18, 18, "none", ` rx="3" stroke="${RED}" stroke-dasharray="3 2"`) + dim(20, 78, 54, "L ≠ КД", RED),
  }),
  // Надеть / усадить ТУТ.
  tubeOrder: () => {
    const marked = (x: number, label: string) => tube(x, x + 24, 34, 18, label === "1" ? TUBE : "#c5221f") + text(x + 12, 37, label, "#fff", 8, "middle", 700);
    return {
      ok: jacket(4, 112, 34, 12) + marked(30, "1") + marked(64, "2") + text(58, 62, "порядок по КД", GREEN),
      ng: jacket(4, 112, 34, 12) + marked(30, "2") + marked(64, "1") + mark(58, 34, 34, 14) + text(58, 62, "по КД: 1, 2", RED),
    };
  },
  shrinkJacketMelt: () => ({
    ok: jacket(4, 112, 34, 12) + tube(36, 80, 34, 16) + text(58, 60, "оболочка целая", GREEN),
    ng: jacket(4, 112, 34, 12) + tube(36, 80, 34, 16) + path("M80,28 Q86,22 94,27 Q98,34 94,41 Q86,46 80,40 z", "#5a4a3c", E, 0.6) + circle(88, 31, 1.4, "#c9b184", "none", 0) + mark(88, 34, 12, 12) + text(58, 60, "оболочка оплавлена", RED),
  }),
  // Корпус коаксиального разъёма и втулка экрана.
  cxSeat: () => {
    const body = coaxBody(30, 84, 100) + rect(30, 27, 16, 14, "#f4f4f2", ` stroke="${E}" stroke-width=".5"`) + jacket(100, 114, 34, 12);
    return {
      ok: body + rect(20, 32.5, 22, 3, TIN, ` stroke="${TIN_LO}" stroke-width=".5"`) + line(20, 18, 20, 50, GREEN, 0.8, ' stroke-dasharray="3 2"') + text(58, 62, "контакт на месте", GREEN),
      ng: body + rect(30, 32.5, 12, 3, TIN, ` stroke="${TIN_LO}" stroke-width=".5"`) + line(20, 18, 20, 50, MUTED, 0.6, ' stroke-dasharray="3 2"') + dim(20, 30, 20, "", RED) + mark(26, 34, 9, 8) + text(58, 62, "контакт утоплен", RED),
    };
  },
  // Корпус с прижимом (INS_housing_COAX_clamp): оплётка по конусу, гайка, уплотнитель.
  cxBraidStrands: () => {
    const body = coaxBody(30, 84, 100) + rect(30, 27, 16, 14, "#f4f4f2", ` stroke="${E}" stroke-width=".5"`) + jacket(100, 114, 34, 12);
    return {
      ok: body + rect(20, 32.5, 22, 3, TIN, ` stroke="${TIN_LO}" stroke-width=".5"`) + text(58, 62, "изолятор и контакт чисты", GREEN),
      ng: body + rect(20, 32.5, 22, 3, TIN, ` stroke="${TIN_LO}" stroke-width=".5"`) + path("M46,30 l-14,-5 M48,36 l-18,4 M44,34 l-12,-1", "none", "#8d9297", 1) + mark(38, 33, 13, 10) + text(58, 62, "пряди — замыкание", RED),
    };
  },
  cxClampLoose: () => ({
    ok: coaxBody(30, 84, 100) + braid(84, 100, 34, 18) + jacket(100, 114, 34, 12) + text(58, 62, "оплётка зажата", GREEN),
    ng: coaxBody(30, 84, 100) + braid(92, 104, 34, 12) + jacket(108, 114, 34, 12) + path("M96,56 L112,56 M108,52 L112,56 L108,60", "none", RED, 1.2) + mark(100, 34, 12, 14) + text(58, 66, "выдёргивается", RED),
  }),
  cxNutOver: () => ({
    ok: coaxBody(30, 84, 100) + hexSleeve(66, 98) + jacket(98, 114, 34, 12) + rect(30, 27, 16, 14, "#f4f4f2", ` stroke="${E}" stroke-width=".5"`) + text(58, 62, "гайка по моменту", GREEN),
    ng: coaxBody(30, 84, 100) + hexSleeve(66, 98) + jacket(98, 114, 34, 12) + path("M30,27 Q36,34 30,41 L46,41 L46,27 z", "#f4f4f2", E, 0.6) + rect(34, 34, 12, 3, TIN, ` stroke="${TIN_LO}" stroke-width=".5"`) + mark(38, 34, 12, 10) + text(58, 62, "изолятор смят", RED),
  }),
  cxGasketMissing: () => ({
    ok: coaxBody(30, 84, 100) + rect(84, 20, 3, 28, TIN, ` stroke="${TIN_LO}" stroke-width=".5"`) + rect(87, 24, 9, 20, "#4b5257", ` rx="2" stroke="${E}" stroke-width=".5"`) + jacket(96, 114, 34, 12) + text(58, 62, "шайба и уплотнитель", GREEN),
    ng: coaxBody(30, 84, 100) + jacket(88, 114, 34, 12) + mark(90, 34, 10, 16) + text(58, 62, "нет шайбы, уплотнителя", RED),
  }),
  cxSleeveHairs: () => ({
    ok: coaxBody(4, 34, 48) + hexSleeve(36, 76) + jacket(76, 112, 34, 14),
    ng: coaxBody(4, 34, 48) + hexSleeve(36, 76) + jacket(76, 112, 34, 14) + path("M76,24 l6,-7 M78,25 l9,-4 M76,44 l7,6 M77,43 l9,3", "none", "#8d9297", 1) + mark(80, 34, 9, 15),
  }),
  cxHexLoose: () => ({
    ok: hexSection(42, 34, 20, "ok") + vdim(70, 17, 51, "HEX по КД", GREEN),
    ng: hexSection(42, 32, 20, "loose") + path("M66,16 A26,26 0 0 1 70,38", "none", RED, 1.2) + path("M70,38 l-4,-2 l5,-3 z", RED, RED, 0.5) + text(58, 64, "проворачивается", RED),
  }),
  cxHexFlash: () => ({
    ok: hexSection(58, 33, 20, "ok"),
    ng: hexSection(58, 33, 20, "flash") + mark(83, 33, 8, 6) + mark(33, 33, 8, 6) + text(58, 64, "облой, «уши»", RED),
  }),
  cxBodyDent: () => ({
    ok: coaxBody(10, 60, 76) + hexSleeve(62, 92) + jacket(92, 114, 34, 14),
    ng: coaxBody(10, 60, 76) + path("M26,20 Q32,27 38,20", "#8d969c", E, 0.7) + path("M24,48 Q31,41 38,48", "#8d969c", E, 0.7) + hexSleeve(62, 92) + jacket(92, 114, 34, 14) + mark(31, 34, 12, 16) + text(58, 64, "корпус смят", RED),
  }),
};

// ── сцены: каждая — рисунок «норма» и «брак» в поле 116×70 ─────────────────────────────────────────────
/** view — окно рисунка (viewBox) для крупного плана, по умолчанию всё поле 0 0 116 70. */
type Scene = { ok: string; ng: string; view?: string };
const CLOSE_UP = "6 14 80 50";
/** Пайка пина коаксиала: крупнее, без оболочки кабеля. */
const CX_VIEW = "24 12 92 44";
const W = { ins: (x2 = 58) => ins(4, x2), bare: (x1 = 58, x2 = 100) => cond(x1, x2) };

// ── рисунки под строки СТО ФРАК-001-2026, ред. 2, для которых в библиотеке не было подходящего (таблица — etkStoScenes.ts) ──
const BAND = "#c9ced3";
/** Сросток ленточным обжимом: два провода слева, жилы в ленте-обжиме справа. */
function spliceBase(o: { bandX?: number; bandW?: number; bandH?: number; insTo?: number; strandsTo?: number; tilt?: number } = {}): string {
  const bx = o.bandX ?? 52, bw = o.bandW ?? 26, bh = o.bandH ?? 24;
  let out = ins(4, o.insTo ?? 40, 28, 8) + ins(4, o.insTo ?? 40, 40, 8) + cond(o.insTo ?? 40, bx + bw - 4, 28, 4) + cond(o.insTo ?? 40, bx + bw - 4, 40, 4);
  const band = rect(bx, 34 - bh / 2, bw, bh, BAND, ` rx="3" stroke="${TIN_LO}" stroke-width=".8"`) + line(bx + 2, 34 - bh / 2 + 3, bx + bw - 2, 34 - bh / 2 + 3, "#fff", 1.2);
  out += o.tilt ? `<g transform="rotate(${o.tilt} ${bx + bw / 2} 34)">${band}</g>` : band;
  if (o.strandsTo) out += path(`M${bx + bw},30 L${o.strandsTo},27 M${bx + bw},34 L${o.strandsTo + 4},34 M${bx + bw},38 L${o.strandsTo},42`, "none", CU, 1.4);
  return out;
}
/** Терминал с жилой: изоляция, жила и обжим (для сцен «жила и барабан»). */
const termWire = (wire: string, opts: Parameters<typeof terminal>[2] = {}) => ins(2, 40) + wire + terminal(14, 34, opts);
/** Скрутка двух проводов со своим шагом на каждом участке. */
function twistRun(x0: number, pitches: number[]): string {
  let out = "", x = x0;
  for (const p of pitches) {
    out += path(`M${x},30 C${x + p * 0.3},30 ${x + p * 0.2},38 ${x + p / 2},38 C${x + p * 0.8},38 ${x + p * 0.7},30 ${x + p},30`, "none", "#1a73e8", 3) +
      path(`M${x},38 C${x + p * 0.3},38 ${x + p * 0.2},30 ${x + p / 2},30 C${x + p * 0.8},30 ${x + p * 0.7},38 ${x + p},38`, "none", "#e8f0fe", 3);
    x += p;
  }
  return out;
}

const STO_NEW_SCENES: Record<string, () => Scene> = {
  tubeShrunkEnd: () => ({
    ok: ins(60, 112, 34, 8) + tube(4, 84, 34, 18) + text(58, 60, "трубка круглая", GREEN),
    ng: ins(60, 112, 34, 8) + path("M4,25 L64,25 Q78,29 84,31 L84,37 Q78,39 64,43 L4,43 z", TUBE, E, 0.7) + mark(76, 34, 10, 12) + text(58, 60, "конец сжался при резке", RED),
  }),
  strandBreak: () => ({
    ok: W.ins() + W.bare(),
    ng: W.ins() + W.bare() + rect(70, 31, 4, 3, "#fff") + rect(82, 34, 5, 3, "#fff") + path("M70,31 L73,26 M82,37 L86,43 M92,31 L96,26", "none", CU, 1.3) + mark(82, 34, 20, 11) + text(58, 62, "оборванные проволоки", RED),
  }),
  crimpMatch: () => ({
    ok: termWire(cond(40, 66, 34, 6)),
    ng: termWire(cond(40, 66, 34, 2.4), { wings: "open" }) + mark(34, 28, 22, 15),
    view: CLOSE_UP,
  }),
  brushShort: () => ({
    ok: termWire(cond(40, 66, 34, 6)) + rect(62, 31, 4, 6, CU) + mark(64, 34, 5, 6, GREEN),
    ng: termWire(cond(40, 54, 34, 6)) + rect(55, 31, 10, 6, "#fff", ` stroke="${RED}" stroke-dasharray="2 2"`) + mark(60, 34, 8, 9),
    view: CLOSE_UP,
  }),
  carrierStrip: () => {
    const strip = (x: number) => rect(x, 26, 14, 16, TIN, ` stroke="${E}" stroke-width=".6"`) + circle(x + 7, 34, 2.6, "#fff", E, 0.5);
    return {
      ok: ins(0, 30) + cond(30, 46, 34, 6) + terminal(6),
      ng: ins(0, 30) + cond(30, 46, 34, 6) + terminal(6) + strip(98) + mark(104, 34, 11, 11) + text(58, 62, "остаток ленты", RED),
    };
  },
  ohms: () => {
    const meter = (value: string, color: string) => rect(22, 10, 72, 40, "#eef2f3", ` rx="4" stroke="${E}" stroke-width=".8"`) + rect(28, 16, 60, 18, "#dfe9e3", ` rx="2" stroke="${TIN_LO}" stroke-width=".5"`) +
      text(58, 30, value, color, 11, "middle", 700) + circle(40, 42, 3, TIN, E, 0.5) + circle(76, 42, 3, TIN, E, 0.5);
    return { ok: meter("0,4 мОм", GREEN) + text(58, 64, "в норме по КД", GREEN), ng: meter("9,8 мОм", RED) + mark(58, 25, 32, 11) + text(58, 64, "выше нормы", RED) };
  },
  crimpMarks: () => {
    const lug = (marks: number[]) => ins(2, 22) + cond(22, 38, 34, 6) + rect(30, 24, 70, 20, TIN, ` rx="2" stroke="${E}" stroke-width=".8"`) +
      marks.map((x) => rect(x, 22, 6, 24, TIN_LO, ` rx="1" opacity=".75"`)).join("");
    return {
      ok: lug([44, 74]) + dim(47, 77, 16, "по ДИ", GREEN),
      ng: lug([54, 58]) + dim(57, 61, 16, "", RED) + mark(56, 34, 10, 14) + text(58, 62, "обжатия слиты", RED),
    };
  },
  tinInCrimp: () => ({
    ok: termWire(cond(40, 66, 34, 6)),
    ng: termWire(tinned(40, 66, 34, 6)) + mark(52, 34, 14, 8),
    view: CLOSE_UP,
  }),
  spliceLoose: () => ({
    ok: spliceBase(),
    ng: spliceBase({ tilt: 9 }) + mark(66, 34, 18, 19) + text(58, 62, "обжим неплотный", RED),
  }),
  spliceStrands: () => ({
    ok: spliceBase(),
    ng: spliceBase({ strandsTo: 100 }) + mark(88, 34, 14, 12) + text(58, 62, "проволоки торчат", RED),
  }),
  spliceIns: () => ({
    ok: spliceBase(),
    ng: spliceBase({ insTo: 62 }) + rect(52, 22, 26, 24, BAND, ` rx="3" opacity=".55" stroke="${TIN_LO}" stroke-width=".8"`) + mark(58, 34, 12, 16) + text(58, 62, "изоляция под лентой", RED),
  }),
  spliceSize: () => ({
    ok: spliceBase() + dim(52, 78, 14, "по КД", GREEN),
    ng: spliceBase({ bandX: 46, bandW: 44, bandH: 30 }) + dim(46, 90, 14, "≠ КД", RED) + mark(68, 34, 24, 17),
  }),
  tnKink: () => ({
    ok: tinOk() + text(58, 58, "жила прямая", GREEN),
    ng: TN.ins() + TN.gap() + `<g transform="rotate(-16 60 34)">${tinEnd(60, 104)}</g>` + mark(62, 34, 10, 10) + text(58, 62, "излом на границе лужения", RED),
  }),
  flux: () => {
    const joint = () => ins(4, 30) + tinEnd(30, 96, 34, 7);
    return {
      ok: joint() + text(58, 58, "соединение чистое", GREEN),
      ng: joint() + [[44, 29], [58, 39], [72, 29], [84, 38], [52, 36]].map(([x, y]) => `<ellipse cx="${x}" cy="${y}" rx="4" ry="2.2" fill="#b9894a" opacity=".75"/>`).join("") + mark(64, 34, 28, 10) + text(58, 62, "остатки флюса", RED),
    };
  },
  solderNoHook: () => {
    const tab = rect(60, 14, 44, 40, TIN, ` rx="3" stroke="${E}" stroke-width=".8"`) + circle(82, 34, 5, "#fff", E, 0.6);
    return {
      ok: tab + ins(4, 44, 34, 8) + cond(44, 62, 34, 4) + path("M62,34 C66,18 98,18 98,34 C98,48 68,48 70,38", "none", CU, 2.2) + text(58, 64, "обхват жилой", GREEN),
      ng: tab + ins(4, 44, 34, 8) + cond(44, 74, 34, 4) + circle(76, 34, 6, SOLDER, TIN_LO, 0.7) + mark(70, 34, 12, 10) + text(58, 64, "жила просто приложена", RED),
    };
  },
  cupDepth: () => ({
    ok: ins(4, 42) + cup(56) + cond(42, 94, 34, 5) + dim(56, 94, 20, "до дна", GREEN),
    ng: ins(4, 42) + cup(56) + cond(42, 70, 34, 5) + dim(70, 94, 20, "пусто", RED) + mark(82, 34, 14, 8),
  }),
  compBurn: () => {
    const body = (burnt: boolean) => line(4, 34, 112, 34, TIN, 2) + rect(34, 24, 48, 20, burnt ? "#6b5a3e" : "#2b3238", ` rx="4" stroke="${E}" stroke-width=".8"`) +
      rect(40, 24, 5, 20, burnt ? "#a8967a" : "#d0d4d8") + (burnt ? path("M56,24 C54,16 66,16 64,24 M60,44 C58,52 70,52 68,44", "#8a7355", RED, 1) + path("M50,26 L56,34 L52,40", "none", RED, 1.2) : "");
    return { ok: body(false), ng: body(true) + mark(60, 34, 26, 16) + text(58, 66, "корпус вспучен", RED) };
  },
  shellSeam: () => {
    const base = ins(4, 30, 34, 18, "#3f474d") + braid(30, 52, 34, 14) + rect(52, 14, 50, 40, TIN, ` rx="3" stroke="${E}" stroke-width=".8"`);
    return {
      ok: base + rect(50, 16, 5, 36, SOLDER, ` stroke="${TIN_LO}" stroke-width=".5"`) + text(58, 64, "шов по окружности", GREEN),
      ng: base + rect(50, 16, 5, 13, SOLDER, ` stroke="${TIN_LO}" stroke-width=".5"`) + rect(50, 40, 5, 12, SOLDER, ` stroke="${TIN_LO}" stroke-width=".5"`) + mark(52, 34, 8, 9) + text(58, 64, "шов с разрывом", RED),
    };
  },
  shrinkWrinkle: () => ({
    ok: ins(4, 112) + tube(34, 86, 34, 15),
    ng: ins(4, 112) + tube(34, 86, 34, 15) + path("M46,26 l3,16 M58,26 l-2,16 M70,26 l3,16", "none", "#8e979d", 1) + circle(52, 30, 3, "#6b747a", "#aab3b9", 0.6) + circle(76, 38, 2.4, "#6b747a", "#aab3b9", 0.6) + mark(60, 34, 26, 11) + text(58, 60, "складки, пузыри", RED),
  }),
  cxNutUnder: () => ({
    ok: coaxBody(30, 76, 90) + hexSleeve(76, 104) + jacket(104, 114, 34, 10) + text(58, 62, "гайка по моменту", GREEN),
    ng: coaxBody(30, 76, 98) + [80, 84, 88, 92, 96].map((x) => line(x, 28, x, 40, TIN_LO, 1)).join("") + hexSleeve(98, 114) + mark(88, 34, 12, 12) + text(58, 62, "резьба видна", RED),
  }),
  vswr: () => {
    const frame = rect(10, 6, 96, 46, "#fff", ` rx="2" stroke="${TIN_LO}" stroke-width=".8"`) + line(10, 24, 106, 24, MUTED, 0.8, ' stroke-dasharray="4 2"') + text(14, 21, "предел", MUTED, 7, "start");
    return {
      ok: frame + path("M12,46 C30,42 40,36 52,38 S80,34 104,40", "none", GREEN, 1.8) + text(58, 64, "КСВН в пределе", GREEN),
      ng: frame + path("M12,46 C32,40 40,12 56,10 S84,40 104,36", "none", RED, 1.8) + mark(56, 14, 14, 9) + text(58, 64, "КСВН выше предела", RED),
    };
  },
  tieSpacing: () => {
    const bundle = [26, 32, 38, 44].map((y) => line(4, y, 112, y, INS, 5.5)).join("");
    const ties = (xs: number[]) => xs.map((x) => rect(x, 20, 6, 30, "#2a2e33", ` rx="2"`)).join("");
    return {
      ok: bundle + ties([22, 54, 86]) + dim(25, 57, 14, "шаг по КД", GREEN),
      ng: bundle + ties([16, 30, 90]) + dim(19, 33, 14, "", RED) + mark(26, 34, 16, 18) + text(58, 62, "шаг не по КД", RED),
    };
  },
  bendRadius: () => ({
    ok: path("M8,54 L40,54 Q72,54 72,22 L72,6", "none", INS, 8) + path("M8,54 L40,54 Q72,54 72,22 L72,6", "none", "#fff", 1, ' opacity=".3"') + text(36, 20, "R по КД", GREEN),
    ng: path("M8,54 L64,54 Q68,54 68,50 L68,6", "none", INS, 8) + mark(66, 52, 10, 9) + text(32, 30, "R < нормы", RED),
  }),
  corrOpen: () => {
    const corr = rect(4, 22, 108, 24, "#2f3337", ` rx="4"`) + [10, 22, 34, 46, 58, 70, 82, 94, 106].map((x) => line(x, 22, x, 46, "#555d63", 1.2)).join("");
    return {
      ok: corr + line(4, 34, 112, 34, "#14181b", 1.4) + text(58, 62, "разрез сомкнут", GREEN),
      ng: corr + rect(20, 29, 60, 10, "#e8eaed") + [31, 34, 37].map((y) => line(20, y, 84, y, INS, 2.6)).join("") + path("M80,30 l10,-8 M82,36 l10,6", "none", INS, 2.6) + mark(52, 34, 34, 11) + text(58, 62, "жгут выходит", RED),
    };
  },
  twistPitch: () => ({
    ok: ins(4, 20, 34, 18, "#3f474d") + twistRun(20, [14, 14, 14, 14, 14, 14]) + dim(20, 34, 18, "P", GREEN),
    ng: ins(4, 20, 34, 18, "#3f474d") + twistRun(20, [10, 34, 12, 38]) + dim(30, 64, 18, "P ≠", RED) + mark(48, 34, 20, 11),
  }),
  wetting: () => ({
    ok: rect(10, 44, 96, 8, INS, ` rx="2" stroke="${E}" stroke-width=".6"`) + path("M32,44 Q58,36 84,44 z", "#a7d3f2", "#5b9bd5", 0.8) + text(58, 28, "капля растекается", GREEN) + text(58, 66, "активация есть", GREEN),
    ng: rect(10, 44, 96, 8, INS, ` rx="2" stroke="${E}" stroke-width=".6"`) + path("M46,44 C42,26 74,26 70,44 z", "#a7d3f2", "#5b9bd5", 0.8) + mark(58, 36, 16, 12) + text(58, 20, "капля шариком", RED) + text(58, 66, "активации нет", RED),
  }),
  weldSize: () => {
    const node = (x: number, w: number, h: number) => ins(4, 34) + cond(34, x) + rect(x, 34 - h / 2, w, h, "#d9a06e", ` rx="2" stroke="${CU_LO}" stroke-width=".6"`) + cond(x + w, 84) + ins(84, 112);
    return {
      ok: node(50, 20, 12) + dim(50, 70, 18, "по ТИ", GREEN),
      ng: node(44, 34, 20) + dim(44, 78, 14, "вне ТИ", RED) + mark(61, 34, 20, 15),
    };
  },
  weldGap: () => ({
    ok: ins(4, 34) + cond(34, 50) + rect(50, 28, 20, 12, "#d9a06e", ` rx="2" stroke="${CU_LO}" stroke-width=".6"`) + cond(70, 84) + ins(84, 112) + dim(34, 50, 18, "зазор", GREEN),
    ng: ins(4, 58) + rect(50, 28, 20, 12, "#d9a06e", ` rx="2" opacity=".85" stroke="${CU_LO}" stroke-width=".6"`) + cond(70, 84) + ins(84, 112) + mark(56, 34, 12, 12) + text(58, 60, "изоляция в узле", RED),
  }),
  fold: () => ({
    ok: ins(4, 50) + rect(50, 28, 40, 12, CU, ` stroke="${CU_LO}" stroke-width=".5"`) + `<ellipse cx="90" cy="34" rx="3.5" ry="6" fill="${CU}" stroke="${CU_LO}" stroke-width=".5"/>` + line(50, 34, 90, 34, CU_LO, 0.6) + dim(50, 90, 18, "по КД", GREEN),
    ng: ins(4, 50) + cond(50, 98, 34, 6) + path("M80,31 L92,26 M82,37 L94,43", "none", CU, 1.3) + dim(50, 98, 18, "не сложена", RED) + mark(88, 34, 14, 12),
  }),
};

// ── рисунки «в точку» под строки СТО без стандартного рисунка: один рисунок — один критерий (владелец 23.10) ────────
const FIB = "#b8ad92", AMBER = "#b9894a", PCB = "#2f6b57";
const dash = (x1: number, y1: number, x2: number, y2: number, color: string) => line(x1, y1, x2, y2, color, 0.8, ' stroke-dasharray="3 2"');
/** Стрелка усилия (зелёная — норма). */
const force = (x1: number, y1: number, x2: number, y2: number, color = GREEN) => arrow(x1, y1, x2, y2, color);
const OWN_SCENES: Record<string, () => Scene> = {
  // 4.1.7 Коаксиал: жила вровень с диэлектриком
  coaxCoreOffset: () => {
    const base = jacket(4, 28, 34, 18) + braid(28, 42, 34, 14) + ins(42, 74, 34, 12, "#f4f4f2");
    return {
      ok: base + rect(72, 31, 2.5, 6, CU, ` stroke="${CU_LO}" stroke-width=".4"`) + dash(74, 14, 74, 54, GREEN) + text(58, 62, "жила вровень", GREEN),
      ng: base + cond(74, 94, 34, 4) + dash(74, 14, 74, 54, RED) + mark(86, 34, 12, 8) + text(58, 62, "жила выступает", RED),
    };
  },
  // 4.1.20 Рукав: оплавленный торец с открытым просветом / заплавленный
  sleeveMeltClosed: () => ({
    ok: circle(58, 34, 24, TUBE, E, 0.8) + circle(58, 34, 15, "#fff", E, 0.7) + circle(58, 34, 20, "none", "#6d757c", 1.4) + text(58, 67, "просвет открыт", GREEN),
    ng: path("M58,10 C74,8 84,20 82,34 C84,48 72,60 58,58 C42,61 32,48 34,34 C32,20 44,12 58,10 z", TUBE, E, 0.8) + circle(58, 34, 4, "#fff", E, 0.5) +
      mark(58, 34, 12, 12) + text(58, 67, "торец заплавлен", RED),
  }),
  // 4.2.9 Провод с волокнистым покровом: нити не торчат / торчат
  fiberLoose: () => {
    const hairs = [[72, 29, 86, 24], [74, 31, 90, 30], [73, 36, 89, 40], [71, 39, 84, 45], [75, 34, 92, 34]]
      .map(([a, b, c, d]) => path(`M${a},${b} Q${(a + c) / 2},${b - 2} ${c},${d}`, "none", FIB, 1)).join("");
    return {
      ok: ins(4, 74, 34, 14, FIB) + cond(74, 104, 34, 5) + text(58, 60, "покров закреплён", GREEN),
      ng: ins(4, 74, 34, 14, FIB) + cond(74, 104, 34, 5) + hairs + mark(82, 34, 14, 14) + dim(74, 90, 14, "> 1 мм", RED) + text(58, 62, "нити торчат", RED),
    };
  },
  // 4.2.12 Частичное стягивание: остаток изоляции сдвинут на L
  partialStrip: () => ({
    ok: ins(4, 38) + cond(38, 60, 34, 6) + ins(60, 76, 34, 12) + cond(76, 106, 34, 6) + dim(38, 60, 20, "L", GREEN) + text(58, 58, "сдвиг на L", GREEN),
    ng: ins(4, 38) + cond(38, 82, 34, 6) + ins(82, 96, 34, 12) + cond(96, 108, 34, 6) + dim(38, 82, 20, "≠ L", RED) + mark(88, 34, 10, 11) + text(58, 58, "сдвиг не на L", RED),
  }),
  // 4.2.18 Экран кабеля: срез оплётки ровный / проволоки за зоной разделки
  shieldWiresOut: () => {
    const base = jacket(4, 32, 34, 18) + braid(32, 54, 34, 14);
    return {
      ok: base + ins(54, 78, 34, 8, "#f4f4f2") + cond(78, 104, 34, 4) + text(58, 60, "срез ровный", GREEN),
      ng: base + ins(54, 78, 34, 8, "#f4f4f2") + cond(78, 104, 34, 4) + path("M52,28 L72,26 M53,30 L80,31 M52,40 L76,44 M54,38 L70,37", "none", "#8d9297", 1) +
        mark(66, 34, 16, 14) + text(58, 62, "проволоки за срезом", RED),
    };
  },
  // 4.2.19 Расстояние L от среза экрана до торца изоляции жилы
  shieldToCore: () => {
    const base = jacket(4, 28, 34, 18) + braid(28, 46, 34, 14);
    return {
      ok: base + ins(46, 74, 34, 9, "#f4f4f2") + cond(74, 104, 34, 4) + dim(46, 74, 20, "L", GREEN) + text(58, 60, "L в допуске", GREEN),
      ng: base + ins(46, 96, 34, 9, "#f4f4f2") + cond(96, 108, 34, 4) + dim(46, 96, 20, "L", RED) + mark(84, 34, 14, 9) + text(58, 60, "L больше нормы", RED),
    };
  },
  // 4.2.20 Внешняя волокнистая оплётка кабеля: под бандажом / распущена
  outerBraidFree: () => {
    const fibre = (x2: number) => rect(34, 25, x2 - 34, 18, FIB, ` stroke="${E}" stroke-width=".6"`) +
      Array.from({ length: Math.floor((x2 - 34) / 4) }, (_, i) => line(36 + i * 4, 25, 38 + i * 4, 43, "#8f8567", 0.6)).join("");
    return {
      ok: jacket(4, 34, 34, 16) + fibre(80) + rect(72, 23, 8, 22, "#2a2e33", ` rx="1.5"`) + cond(80, 106, 34, 5) + text(58, 60, "под бандажом", GREEN),
      ng: jacket(4, 34, 34, 16) + fibre(72) + path("M72,27 L90,20 M72,31 L96,28 M72,35 L94,38 M72,40 L88,48 M72,43 L82,52", "none", FIB, 1.4) + cond(76, 106, 34, 5) +
        mark(84, 34, 16, 17) + text(58, 62, "оплётка распущена", RED),
    };
  },
  // 4.2.25 Экран пары: фольга цела до среза / порвана
  pairFoilTorn: () => {
    const wires = rect(34, 29, 70, 5, "#1a73e8", ` rx="2"`) + rect(34, 36, 70, 5, "#f2a60d", ` rx="2"`);
    const foil = (x1: number, x2: number) => rect(x1, 24, x2 - x1, 20, "#e3e8ec", ` stroke="${TIN_LO}" stroke-width=".7"`) + line(x1 + 2, 27, x2 - 2, 27, "#fff", 1.2);
    return {
      ok: jacket(4, 34, 34, 20) + wires + foil(34, 72) + text(58, 60, "фольга цела", GREEN),
      ng: jacket(4, 34, 34, 20) + wires + foil(34, 46) + foil(62, 72) + path("M46,24 L50,30 L47,36 L51,44 M62,24 L58,31 L61,37 L57,44", "none", TIN_LO, 0.9) + mark(54, 34, 11, 14) + text(58, 60, "фольга порвана", RED),
    };
  },
  // 4.2.34 Коаксиал: срез оплётки ровный / ступенькой
  braidCutEven: () => {
    const head = jacket(4, 30, 34, 18);
    const strands = (ends: number[]) => ends.map((x, i) => line(30, 27.5 + i * 2.6, x, 27.5 + i * 2.6, "#8d9297", 2.2)).join("");
    return {
      ok: head + rect(30, 26, 22, 16, "#e2e5e8", ` stroke="${E}" stroke-width=".5"`) + ins(52, 80, 34, 9, "#f4f4f2") + cond(80, 106, 34, 4) + line(52, 25, 52, 43, GREEN, 0.9) + text(58, 60, "срез ровный", GREEN),
      ng: head + strands([52, 62, 46, 66, 54, 60]) + ins(60, 84, 34, 9, "#f4f4f2") + cond(84, 106, 34, 4) + mark(56, 34, 14, 12) + text(58, 60, "пряди разной длины", RED),
    };
  },
  // 4.3.1 Контакт, матрица и жила согласованы
  matchTrio: () => {
    const tile = (x: number, title: string, value: string, color: string) =>
      rect(x, 14, 32, 30, "#fff", ` rx="3" stroke="${color}" stroke-width="1.2"`) + text(x + 16, 24, title, MUTED, 6.5, "middle", 600) + text(x + 16, 38, value, color, 9, "middle", 700);
    return {
      ok: tile(4, "Контакт", "0,5", GREEN) + tile(42, "Матрица", "0,5", GREEN) + tile(80, "Жила", "0,5", GREEN) + text(58, 62, "комплект подобран", GREEN),
      ng: tile(4, "Контакт", "0,5", MUTED) + tile(42, "Матрица", "1,5", RED) + tile(80, "Жила", "0,5", MUTED) + line(44, 18, 70, 40, RED, 1.3) + line(70, 18, 44, 40, RED, 1.3) + text(58, 62, "матрица не та", RED),
    };
  },
  // 4.3.30 Обжим наконечника ТМЛ: размер по граням S в пределах ДИ
  tmlGeom: () => {
    const lug = (h: number) => ins(2, 22) + cond(22, 38, 34, 6) + rect(30, 34 - h / 2, 62, h, TIN, ` rx="2" stroke="${E}" stroke-width=".8"`) + rect(46, 34 - h / 2 - 1, 22, h + 2, TIN_LO, ` rx="1" opacity=".7"`);
    return {
      ok: lug(18) + vdim(100, 25, 43, "S", GREEN) + text(58, 62, "S по таблице матриц", GREEN),
      ng: lug(26) + vdim(100, 21, 47, "S", RED) + mark(57, 34, 14, 17) + text(58, 62, "S вне таблицы", RED),
    };
  },
  // 4.3.38 Гильза коаксиала: размер HEX по граням в пределах ДИ (штангенциркуль)
  hexFlats: () => {
    const view = (r: number, color: string, label: string) => {
      const half = r * 0.866;
      return hexSection(46, 32, r, "ok") + rect(46 - half - 5, 14, 3, 36, "#4a525a", ` rx="1"`) + rect(46 + half + 2, 14, 3, 36, "#4a525a", ` rx="1"`) +
        dim(46 - half - 3, 46 + half + 3, 11, label, color);
    };
    return { ok: view(18, GREEN, "HEX по ДИ"), ng: view(23, RED, "HEX > ДИ") };
  },
  // 4.3.45 Колпачок: жилы до упора, обжим по центру
  capStop: () => {
    const cap = (strandsTo: number, crimpX: number) => ins(2, 40, 34, 12) + rect(40, 23, 60, 22, "#eef1f3", ` rx="7" stroke="${TIN_LO}" stroke-width="1"`) +
      cond(36, strandsTo, 34, 11) + rect(crimpX, 21, 8, 26, TIN_LO, ` rx="1" opacity=".8"`);
    return {
      ok: cap(94, 62) + text(58, 62, "до упора, обжим по центру", GREEN),
      ng: cap(62, 44) + rect(63, 29, 32, 10, "#fff", ` stroke="${RED}" stroke-dasharray="2 2"`) + mark(76, 34, 20, 13) + text(58, 62, "не до упора, обжим у края", RED),
    };
  },
  // 4.3.50 Микроразъём: оплётка под обжимом / проволоки наружу
  mhfBraidOut: () => {
    const body = ins(4, 38, 34, 7, "#3f474d") + rect(38, 27, 16, 14, TIN, ` rx="1.5" stroke="${E}" stroke-width=".7"`) + line(40, 31, 52, 31, TIN_HI, 1) +
      rect(54, 25, 30, 18, TIN, ` rx="2" stroke="${E}" stroke-width=".8"`) + circle(75, 34, 5, "#f4f4f2", E, 0.6) + circle(75, 34, 1.6, CU, CU_LO, 0.3);
    return {
      ok: body + text(58, 60, "оплётка под обжимом", GREEN),
      ng: body + path("M38,28 L30,20 M40,27 L36,16 M38,41 L30,49 M41,42 L38,52", "none", "#8d9297", 1.2) + mark(36, 34, 12, 20) + text(58, 62, "проволоки наружу", RED),
    };
  },
  // 4.4.2.9 Зазор C между изоляцией и пайкой
  bareGapC: () => ({
    ok: cup(56) + ins(4, 46) + cond(46, 94, 34, 5) + dim(46, 58, 20, "C", GREEN) + text(58, 60, "C в пределах", GREEN),
    ng: cup(56) + ins(4, 26) + cond(26, 94, 34, 5) + dim(26, 56, 20, "C", RED) + mark(40, 34, 15, 8) + text(58, 60, "голая жила длинная", RED),
  }),
  // 4.4.2.11 Пайка необлужённой жилы
  untinnedSolder: () => ({
    ok: cup(56) + ins(4, 42) + tinned(42, 94, 34, 5) + path("M56,29 Q58,31 54,31 L54,37 Q58,37 56,39 z", SOLDER, TIN_LO, 0.5) + text(58, 60, "жила облужена", GREEN),
    ng: cup(56) + ins(4, 42) + cond(42, 94, 34, 5) + circle(60, 29, 2.6, SOLDER, TIN_LO, 0.5) + circle(66, 40, 2.2, SOLDER, TIN_LO, 0.5) + mark(64, 34, 18, 9) + text(58, 60, "медь, припой не взял", RED),
  }),
  // 4.4.2.12 Чашка: не более трёх проводов (вид с торца)
  cupCount: () => {
    const cupEnd = circle(58, 32, 20, TIN, E, 0.8) + circle(58, 32, 15, "#7e8a91", TIN_LO, 0.6);
    const w = (x: number, y: number) => circle(x, y, 4.2, CU, CU_LO, 0.5) + circle(x, y, 2.2, CU_HI, "none", 0);
    return {
      ok: cupEnd + w(52, 36) + w(64, 36) + w(58, 27) + text(58, 66, "три провода", GREEN),
      ng: cupEnd + w(50, 38) + w(61, 40) + w(66, 29) + w(55, 28) + w(46, 29) + w(58, 33) + mark(58, 32, 22) + text(58, 66, "больше трёх", RED),
    };
  },
  // 4.4.2.14 Галтель: гладкая / трещина от смещения провода при кристаллизации
  solderCrack: () => ({
    ok: cup(56) + ins(4, 42) + cond(42, 94, 34, 5) + path("M56,29.5 Q56,32 51,32 L51,36 Q56,36 56,38.5 z", SOLDER, TIN_LO, 0.5) + text(58, 60, "галтель гладкая", GREEN),
    ng: cup(56) + `<g transform="rotate(-7 56 34)">${ins(4, 42) + cond(42, 94, 34, 5)}</g>` + path("M56,29.5 Q56,32 51,32 L51,36 Q56,36 56,38.5 z", SOLDER, TIN_LO, 0.5) +
      path("M53,29 L51,33 L54,36 L52,40", "none", RED, 1.3) + mark(53, 34, 9, 10) + text(58, 60, "трещина в галтели", RED),
  }),
  // 4.4.2.17 Остатки флюса вокруг паяного соединения
  fluxJoint: () => {
    const joint = cup(56) + ins(4, 42) + cond(42, 94, 34, 5) + path("M56,29.5 Q56,32 51,32 L51,36 Q56,36 56,38.5 z", SOLDER, TIN_LO, 0.5);
    return {
      ok: joint + text(58, 60, "вокруг чисто", GREEN),
      ng: joint + [[50, 22], [66, 20], [84, 22], [48, 46], [70, 48], [88, 46], [58, 52]].map(([x, y]) => `<ellipse cx="${x}" cy="${y}" rx="5" ry="2.6" fill="${AMBER}" opacity=".75"/>`).join("") +
        mark(68, 34, 30, 22) + text(58, 66, "янтарные остатки флюса", RED),
    };
  },
  // 4.4.2.18 Контактная часть штыря: чистая / капля припоя и флюс
  pinSolderDrop: () => {
    const pin = rect(4, 24, 40, 20, TIN, ` rx="2" stroke="${E}" stroke-width=".8"`) + rect(44, 31, 56, 6, TIN_HI, ` stroke="${E}" stroke-width=".7"`) + path("M100,31 L108,34 L100,37 z", TIN_HI, E, 0.6);
    return {
      ok: pin + text(58, 58, "контактная часть чистая", GREEN),
      ng: pin + circle(70, 29, 5, SOLDER, TIN_LO, 0.7) + `<ellipse cx="86" cy="36" rx="7" ry="3" fill="${AMBER}" opacity=".8"/>` + mark(78, 32, 18, 10) + text(58, 58, "припой и флюс на штыре", RED),
    };
  },
  // 4.4.3.2 LEMO: повив чашки целый / одна проволочка к соседней чашке
  lemoStray: () => {
    const ring = (cx: number) => circle(cx, 32, 16, TIN, E, 0.8) + circle(cx, 32, 10, "#7e8a91", TIN_LO, 0.5) +
      Array.from({ length: 10 }, (_, i) => circle(cx + 7 * Math.cos((i / 10) * 2 * Math.PI), 32 + 7 * Math.sin((i / 10) * 2 * Math.PI), 1.7, CU, CU_LO, 0.4)).join("");
    return {
      ok: ring(30) + ring(86) + text(58, 66, "повив внутри чашки", GREEN),
      ng: ring(30) + ring(86) + path("M36,30 Q54,20 76,28", "none", CU, 1.4) + mark(56, 26, 20, 8) + text(58, 66, "проволочка к соседней", RED),
    };
  },
  // 4.4.3.8 Плата USB-C: резистор CC на площадках / пустые площадки
  usbcCc: () => {
    const board = rect(30, 6, 80, 46, PCB, ` rx="3" stroke="#1d4a3b" stroke-width=".8"`) + rect(4, 22, 30, 24, TIN, ` rx="9" stroke="${E}" stroke-width=".8"`) + rect(10, 30, 18, 8, "#2b3136", ` rx="3"`);
    const pads = rect(60, 28, 9, 12, TIN_HI, ` stroke="#1d4a3b" stroke-width=".4"`) + rect(78, 28, 9, 12, TIN_HI, ` stroke="#1d4a3b" stroke-width=".4"`);
    return {
      ok: board + pads + rect(64, 29, 18, 10, "#2b3136", ` rx="1.5"`) + rect(64, 29, 4, 10, SOLDER) + rect(78, 29, 4, 10, SOLDER) + text(76, 47, "CC", "#d8e8e0", 7, "middle", 700) + text(58, 64, "резистор на месте", GREEN),
      ng: board + pads + mark(73, 34, 18, 10) + text(76, 47, "CC", "#d8e8e0", 7, "middle", 700) + text(58, 64, "площадки пустые", RED),
    };
  },
  // 4.4.3.10 Чашка контакта XT: провод до конца / голая медь над чашкой
  xtCup: () => ({
    ok: cup(50) + ins(4, 48, 34, 12) + cond(48, 88, 34, 5) + text(58, 60, "изоляция у чашки", GREEN),
    ng: cup(50) + ins(4, 26, 34, 12) + cond(26, 88, 34, 5) + dim(26, 50, 20, "> 1,5 мм", RED) + mark(38, 34, 13, 8) + text(58, 60, "голая медь над чашкой", RED),
  }),
  // 4.4.3.14 Наконечник: припой только в хвостовике / поднялся по жиле
  lugWick: () => {
    const lug = rect(52, 28, 28, 12, TIN, ` stroke="${E}" stroke-width=".7"`) + circle(98, 34, 13, TIN, E, 0.8) + circle(98, 34, 5, "#fff", E, 0.6) + rect(80, 31, 6, 6, TIN, ` stroke="${E}" stroke-width=".5"`);
    return {
      ok: ins(4, 42, 34, 12) + cond(42, 54, 34, 6) + lug + text(58, 60, "жила гибкая", GREEN),
      ng: ins(4, 24, 34, 12) + tinEnd(24, 54, 34, 6) + lug + dim(24, 52, 20, "припой на жиле", RED) + mark(38, 34, 15, 8) + text(58, 60, "жила жёсткая", RED),
    };
  },
  // 4.4.3.17 «Крокодил»: губки свободны / залиты припоем
  crocClamp: () => {
    const jaws = ins(4, 40, 34, 10) + rect(38, 24, 16, 20, TIN_LO, ` rx="2" stroke="${E}" stroke-width=".7"`) +
      path("M54,24 L104,29 L104,33 L58,33 z", TIN, E, 0.7) + path("M54,44 L104,39 L104,35 L58,35 z", TIN, E, 0.7) +
      path("M70,30.5 l3,2 l3,-2 l3,2 l3,-2 l3,2 l3,-2 M70,37.5 l3,-2 l3,2 l3,-2 l3,2 l3,-2 l3,2", "none", TIN_LO, 0.6);
    return {
      ok: jaws + text(58, 60, "губки свободно смыкаются", GREEN),
      ng: jaws + path("M56,31 C66,22 94,24 102,31 L102,37 C94,46 66,46 56,37 z", SOLDER, TIN_LO, 0.8) + mark(80, 34, 26, 12) + text(58, 60, "губки залиты припоем", RED),
    };
  },
  // 4.4.4.1 Сросток: жилы скручены перед пайкой / встык
  spliceMech: () => ({
    ok: ins(4, 30) + ins(86, 112) + cond(30, 86, 34, 5) + path("M34,31 L40,37 L46,31 L52,37 L58,31 L64,37 L70,31 L76,37 L82,31", "none", CU_LO, 1) +
      rect(34, 30, 48, 8, SOLDER, ` rx="4" opacity=".65"`) + text(58, 58, "жилы скручены", GREEN),
    ng: ins(4, 30) + ins(86, 112) + cond(30, 52, 34, 5) + cond(64, 86, 34, 5) + path("M48,34 C48,24 68,24 68,34 C68,42 48,42 48,34 z", SOLDER, TIN_LO, 0.8) + mark(58, 34, 14, 12) + text(58, 58, "жилы встык, без скрутки", RED),
  }),
  // 4.4.4.3 Сросток: трубка надета до пайки / лежит отдельно
  tubeBefore: () => {
    const joint = ins(4, 56) + cond(56, 76, 34, 5) + ins(76, 112) + path("M60,34 C60,26 74,26 74,34 C74,41 60,41 62,34 z", SOLDER, TIN_LO, 0.8);
    return {
      ok: joint + tube(10, 40, 34, 15) + text(58, 60, "трубка уже на проводе", GREEN),
      ng: joint + tube(18, 56, 53, 12) + arrow(38, 46, 38, 41, RED) + dash(22, 42, 52, 42, RED) + mark(66, 34, 12, 10) + text(58, 68, "трубка лежит отдельно", RED),
    };
  },
  // 4.4.4.6 Бандаж экранов: число и ширина по КД
  shieldBand: () => {
    const tails = (n: number, spread: number) => Array.from({ length: n }, (_, i) => path(`M36,34 Q58,${34 + (i - (n - 1) / 2) * spread} 90,34`, "none", TIN_LO, 1.4)).join("");
    return {
      ok: jacket(4, 36, 34, 20) + tails(3, 4) + rect(62, 26, 7, 16, "#2a2e33", ` rx="1.5"`) + dim(62, 69, 18, "по КД", GREEN) + text(58, 60, "3 экрана, бандаж узкий", GREEN),
      ng: jacket(4, 36, 34, 20) + tails(6, 5) + rect(56, 19, 18, 30, "#2a2e33", ` rx="1.5"`) + dim(56, 74, 12, "шире КД", RED) + mark(65, 34, 13, 17) + text(58, 63, "6 экранов, бандаж шире", RED),
    };
  },
  // 4.4.4.7 Бандаж экранов: лужёная проволока / изолента
  shieldBandMat: () => {
    const tails = [-4, 0, 4].map((d) => path(`M36,34 Q58,${34 + d} 92,34`, "none", TIN_LO, 1.4)).join("");
    return {
      ok: jacket(4, 36, 34, 20) + tails + rect(58, 26, 14, 16, SOLDER, ` rx="2" stroke="${TIN_LO}" stroke-width=".7"`) +
        Array.from({ length: 6 }, (_, i) => line(60 + i * 2.2, 26, 60 + i * 2.2, 42, TIN_LO, 0.6)).join("") + text(58, 60, "лужёная проволока", GREEN),
      ng: jacket(4, 36, 34, 20) + tails + rect(58, 24, 14, 20, "#1b1f23", ` rx="3"`) + line(60, 28, 70, 28, "#6d757c", 1) + mark(65, 34, 12, 14) + text(58, 60, "изолента вместо проволоки", RED),
    };
  },
  // 4.4.5.8 Угловой контакт: жила стоит в чашке / торец жилы упёрся в стенку корпуса
  cxAngleWall: () => {
    const box = path("M32,6 L106,6 L106,54 L32,54", "#eef1f3", TIN_LO, 1.2) + circle(76, 34, 9, TIN, E, 0.7) + circle(76, 34, 4, "#7e8a91", E, 0.4);
    return {
      ok: box + ins(4, 34, 34, 10) + cond(32, 68, 34, 4) + dash(32, 34, 68, 34, GREEN) + text(70, 64, "жила в центре чашки", GREEN, 7.5),
      ng: box + ins(4, 34, 34, 10) + path("M32,34 L50,34 L70,10", "none", CU, 4) + circle(70, 9, 2.4, CU_HI, CU_LO, 0.4) + mark(70, 12, 9, 7) + text(70, 64, "торец жилы на стенке", RED, 7.5),
    };
  },
  // 4.5.6 Экран не заходит под изоляционную трубку
  shieldUnderTube: () => {
    const core = ins(46, 98, 34, 9, "#f4f4f2") + cond(98, 110, 34, 4);
    const under = `<g opacity=".85">${tube(48, 86, 34, 17)}</g>`;
    return {
      ok: jacket(4, 28, 34, 18) + braid(28, 42, 34, 14) + core + under + text(58, 60, "оплётка до трубки", GREEN),
      ng: jacket(4, 28, 34, 18) + core + under + braid(28, 62, 34, 14) + path("M48,28 L66,27 M48,40 L64,42", "none", "#c9ced2", 1.1) + mark(58, 34, 14, 13) + text(58, 60, "оплётка под трубкой", RED),
    };
  },
  // 4.5.10 Трубка надета до монтажа разъёма / пропущена
  tubeMissing: () => {
    const conn = ins(4, 72) + housing(70, 20, 38, 28) + cond(66, 72, 34, 4);
    return {
      ok: conn + tube(14, 48, 34, 14) + text(58, 60, "трубка надета до монтажа", GREEN),
      ng: conn + rect(14, 26, 34, 16, "none", ` rx="3" stroke="${RED}" stroke-dasharray="3 2" stroke-width="1.2"`) + text(31, 37.5, "?", RED, 11, "middle", 700) + text(58, 60, "разъём собран, трубки нет", RED),
    };
  },
  // 4.5.14 Коаксиал с прижимом: гильза, гайка, шайба надеты по порядку / гильзы нет
  coaxPartsMissing: () => {
    const nut = rect(52, 24, 12, 20, TIN, ` rx="1" stroke="${TIN_LO}" stroke-width=".8"`) + line(58, 24, 58, 44, TIN_LO, 0.6);
    const washer = rect(68, 26, 4, 16, TIN_HI, ` stroke="${TIN_LO}" stroke-width=".7"`) + rect(75, 28, 7, 12, TUBE, ` rx="1.5"`);
    const cable = jacket(4, 112, 34, 12);
    return {
      ok: cable + hexSleeve(30, 48) + nut + washer + text(58, 60, "детали по порядку", GREEN),
      ng: cable + rect(30, 23, 18, 22, "none", ` rx="3" stroke="${RED}" stroke-dasharray="3 2" stroke-width="1.2"`) + nut + washer + text(39, 38, "?", RED, 11, "middle", 700) + text(58, 60, "гильзы нет", RED),
    };
  },
  // 4.5.17 Лента без клеевого слоя: проклейка через 3–5 витков / витки расходятся
  tapeNoGlue: () => {
    const turns = (xs: number[]) => xs.map((x) => path(`M${x},24 L${x + 7},44 L${x + 13},44 L${x + 6},24 z`, "#e8e2c8", "#a69f84", 0.6)).join("");
    return {
      ok: ins(4, 112, 34, 14) + turns([30, 36, 42, 48, 54]) + rect(70, 24, 6, 20, "#d9a035", ` rx="1.5"`) + text(58, 60, "проклейка в конце намотки", GREEN),
      ng: ins(4, 112, 34, 14) + turns([30, 42, 54, 66]) + path("M80,26 C92,16 100,24 94,32", "none", "#e8e2c8", 2.4) + mark(60, 34, 34, 14) + text(58, 60, "витки без клея расходятся", RED),
    };
  },
  // 4.5.24 Колпачок: до упора / на половине, сползает
  capHalf: () => ({
    ok: ins(4, 50, 34, 12) + cond(50, 62, 34, 6) + rect(46, 24, 56, 20, "#d85a4a", ` rx="7" stroke="${E}" stroke-width=".7"`) + text(58, 60, "до упора на изоляцию", GREEN),
    ng: ins(4, 50, 34, 12) + cond(50, 82, 34, 6) + rect(70, 24, 38, 20, "#d85a4a", ` rx="7" stroke="${E}" stroke-width=".7"`) + dim(50, 70, 18, "голая жила", RED) + arrow(60, 52, 78, 52, RED) + mark(60, 34, 12, 9) + text(58, 66, "сидит на половине", RED),
  }),
  // 4.6.2 Маркер на проводе у разъёма: есть / пропущен
  markMissing: () => {
    const conn = flagWire() + housing(82, 20, 28, 28);
    return {
      ok: conn + markTube(34, 66, { text: "X1" }) + text(58, 62, "маркер на проводе", GREEN),
      ng: conn + rect(34, 26.5, 32, 15, "none", ` rx="3" stroke="${RED}" stroke-dasharray="3 2" stroke-width="1.2"`) + text(50, 38, "?", RED, 11, "middle", 700) + text(58, 62, "маркера нет", RED),
    };
  },
  // 4.6.6 Бирки в пучке: все читаются в одну сторону / одна перевёрнута
  tagsOrient: () => {
    const wire = [8, 14].map((y) => line(4, y, 112, y, INS, 5)).join("");
    const tag = (x: number, label: string, flip: boolean, ink = E) =>
      line(x + 14, 16, x + 14, 26, "#2a2e33", 1.2) + (flip ? `<g transform="rotate(180 ${x + 14} 38)">` : "") + rect(x, 26, 28, 24, LABEL, ` rx="2" stroke="${LABEL_E}" stroke-width=".7"`) +
      text(x + 14, 42, label, ink, 9, "middle", 700) + (flip ? "</g>" : "");
    return {
      ok: wire + tag(8, "W1", false) + tag(44, "W2", false) + tag(80, "W3", false) + text(58, 64, "все в одну сторону", GREEN),
      ng: wire + tag(8, "W1", false) + tag(44, "W2", true, RED) + tag(80, "W3", false) + mark(58, 38, 20, 16) + text(58, 64, "одна бирка перевёрнута", RED),
    };
  },
  // 4.7.1 Соединитель: расконсервирован / в плёнке и смазке
  connGrease: () => {
    const conn = housing(30, 16, 56, 32) + [42, 56, 70].map((x) => circle(x, 32, 4, "#2b3136", E, 0.5)).join("");
    return {
      ok: conn + text(58, 62, "чистый, без смазки", GREEN),
      ng: conn + rect(26, 12, 64, 40, "#cfe4f2", ` rx="5" opacity=".6" stroke="#8fb4cc" stroke-width=".8"`) + path("M34,20 l10,6 M60,16 l-6,12 M76,44 l-10,-8", "none", "#fff", 1.2) +
        `<ellipse cx="48" cy="38" rx="7" ry="3" fill="${AMBER}" opacity=".8"/>` + mark(58, 32, 34, 22) + text(58, 64, "в плёнке и смазке", RED),
    };
  },
  // 4.7.2 Детали кожуха надеты на кабель до заделки / лежат рядом
  shellPartsMissing: () => {
    const contact = rect(88, 30, 20, 8, TIN, ` rx="1" stroke="${E}" stroke-width=".6"`);
    const shell = rect(14, 22, 14, 24, "#9fa7ad", ` rx="2" stroke="${E}" stroke-width=".7"`) + rect(30, 24, 22, 20, "#b8c0c6", ` rx="2" stroke="${E}" stroke-width=".7"`);
    return {
      ok: ins(4, 84) + shell + contact + text(58, 62, "кожух надет до заделки", GREEN),
      ng: ins(4, 84) + contact + `<g transform="translate(34 24)">${rect(0, 0, 14, 20, "#9fa7ad", ` rx="2" stroke="${E}" stroke-width=".7"`) + rect(16, 2, 22, 16, "#b8c0c6", ` rx="2" stroke="${E}" stroke-width=".7"`)}</g>` +
        arrow(70, 52, 90, 40, RED) + mark(66, 34, 24, 8) + text(58, 14, "контакты заделаны", RED, 7.5),
    };
  },
  // 4.7.4 Проверка потягиванием: контакт держится / вышел из корпуса
  pullTest: () => ({
    ok: ins(2, 40, 34, 10) + rect(38, 31, 30, 6, TIN, ` stroke="${E}" stroke-width=".6"`) + housing(52, 18, 48, 32) + rect(52, 31, 36, 6, TIN, ` opacity=".85" stroke="${E}" stroke-width=".6"`) +
      force(30, 56, 6, 56) + text(58, 64, "5–10 Н, контакт держит", GREEN, 7.5),
    ng: ins(2, 20, 34, 10) + rect(18, 31, 30, 6, TIN, ` stroke="${E}" stroke-width=".6"`) + housing(52, 18, 48, 32) + force(30, 56, 6, 56, RED) +
      mark(50, 34, 12, 10) + text(58, 64, "контакт вышел из корпуса", RED, 7.5),
  }),
  // 4.7.21 Вилка RJ в разрезе: фиксатор на оболочке / на жилах
  rjClamp: () => {
    const plug = rect(40, 12, 66, 44, "#e9f1f5", ` rx="3" stroke="#8fa0a8" stroke-width=".9"`);
    const cores = (x: number) => ["#1a73e8", "#f2a60d", "#188038", "#8d6e63"].map((c, i) => line(x, 28 + i * 4, 102, 28 + i * 4, c, 2)).join("");
    return {
      ok: plug + jacket(4, 58, 34, 20) + cores(58) + rect(44, 12, 8, 44, "#2a2e33", ` rx="1"`) + text(58, 66, "фиксатор на оболочке", GREEN),
      ng: plug + jacket(4, 40, 34, 20) + cores(40) + rect(44, 12, 8, 44, "#2a2e33", ` rx="1"`) + mark(48, 34, 9, 20) + text(58, 66, "фиксатор на жилах", RED),
    };
  },
  // 4.7.22 Экранированная вилка: дренаж касается экрана вилки / не достаёт
  rjShieldDrain: () => {
    const shell = rect(54, 16, 50, 36, TIN, ` rx="2" stroke="${E}" stroke-width=".8"`) + line(58, 20, 100, 20, TIN_HI, 1.2);
    const cable = jacket(4, 38, 34, 22) + rect(38, 26, 10, 16, "#e3e8ec", ` stroke="${TIN_LO}" stroke-width=".6"`);
    return {
      ok: cable + shell + path("M48,28 Q52,22 62,24", "none", TIN_LO, 1.6) + circle(62, 24, 2.4, SOLDER, TIN_LO, 0.5) + text(58, 64, "дренаж на экране вилки", GREEN),
      ng: cable + shell + path("M48,28 Q50,22 52,23", "none", TIN_LO, 1.6) + mark(54, 24, 8, 8) + text(58, 64, "дренаж не доходит", RED),
    };
  },
  // 4.8.4 Корпус коаксиального разъёма в разрезе: проволока экрана на диэлектрике
  cxBraidInside: () => {
    const body = jacket(4, 28, 34, 18) + rect(28, 14, 76, 40, "#eef1f3", ` rx="2" stroke="${TIN_LO}" stroke-width="1"`) + rect(54, 24, 32, 20, "#f4f4f2", ` stroke="${E}" stroke-width=".6"`) + cond(30, 100, 34, 3.4);
    return {
      ok: body + path("M28,22 L50,22", "none", "#8d9297", 1.4) + text(58, 64, "диэлектрик чист", GREEN),
      ng: body + path("M28,24 Q48,20 62,28 Q70,33 74,34", "none", "#8d9297", 1.4) + mark(66, 30, 12, 8) + text(58, 64, "проволока на диэлектрике", RED),
    };
  },
  // 4.8.10 Угловой разъём: крышка садится / наплыв припоя поднимает крышку
  cxAngleCover: () => {
    const base = ins(4, 30, 34, 10) + rect(30, 26, 62, 26, TIN_LO, ` rx="2" stroke="${E}" stroke-width=".8"`) + circle(52, 34, 5, SOLDER, TIN_LO, 0.5);
    return {
      ok: base + rect(30, 18, 62, 8, TIN, ` rx="2" stroke="${E}" stroke-width=".8"`) + text(58, 64, "крышка села", GREEN),
      ng: base + circle(56, 27, 6, SOLDER, TIN_LO, 0.7) + `<g transform="rotate(-9 30 24)">${rect(30, 14, 62, 8, TIN, ` rx="2" stroke="${E}" stroke-width=".8"`)}</g>` + mark(60, 24, 12, 9) + text(58, 64, "наплыв, крышка не села", RED, 7.5),
    };
  },
  // 4.8.15 Прижимной разъём: оплётка под зажимным кольцом / выступает за кольцо
  braidBeyondRing: () => {
    const ring = rect(52, 20, 16, 28, TIN, ` rx="2" stroke="${E}" stroke-width=".8"`) + line(60, 20, 60, 48, TIN_LO, 0.6);
    const cable = jacket(4, 30, 34, 16) + ins(70, 100, 34, 9, "#f4f4f2") + cond(100, 112, 34, 4);
    return {
      ok: cable + braid(30, 56, 34, 14) + ring + text(58, 62, "оплётка под кольцом", GREEN),
      ng: cable + braid(30, 80, 34, 14) + ring + path("M70,28 L82,25 M70,40 L82,43", "none", "#8d9297", 1) + mark(76, 34, 12, 12) + text(58, 62, "оплётка за кольцом", RED),
    };
  },
  // 4.9.9 Торец гофры: гладкий / острые кромки
  corrSharp: () => {
    const corr = (x2: number) => rect(4, 22, x2 - 4, 24, "#2f3337", ` rx="4"`) + [10, 22, 34, 46, 58, 70, 82].filter((x) => x < x2 - 4).map((x) => line(x, 22, x, 46, "#555d63", 1.2)).join("");
    return {
      ok: corr(94) + path("M94,22 Q100,34 94,46", "none", "#555d63", 1) + text(58, 62, "торец гладкий", GREEN),
      ng: corr(90) + path("M90,22 L100,25 L92,28 L102,32 L92,35 L101,39 L92,42 L99,46 L90,46 z", "#2f3337", RED, 0.8) + mark(94, 34, 12, 16) + text(58, 62, "острые кромки на торце", RED),
    };
  },
  // 4.10.3 Шаг свивки больше нормы
  twistLoose: () => ({
    ok: ins(4, 20, 34, 18, "#3f474d") + twistRun(20, [14, 14, 14, 14, 14, 14]) + dim(20, 34, 18, "P", GREEN) + text(58, 60, "шаг по КД", GREEN),
    ng: ins(4, 20, 34, 18, "#3f474d") + twistRun(20, [38, 38]) + dim(20, 58, 18, "P больше нормы", RED) + mark(56, 34, 30, 11) + text(58, 60, "свивка рыхлая", RED),
  }),
  // 4.10.4 Провод закручен вокруг собственной оси: метка идёт прямо / по спирали
  axisTwist: () => ({
    ok: ins(4, 112, 34, 14) + line(6, 34, 110, 34, "#1a73e8", 2.4) + text(58, 60, "метка идёт прямо", GREEN),
    ng: ins(4, 112, 34, 14) + Array.from({ length: 8 }, (_, i) => line(10 + i * 13, 28, 18 + i * 13, 40, "#1a73e8", 2.4)).join("") + mark(58, 34, 40, 11) + text(58, 60, "метка идёт по спирали", RED),
  }),
  // 4.11.1 Лист сопровождения: отметки приёмки пайки и прозвонки до заливки / нет отметок
  sealBeforeAccept: () => {
    const check = (y: number, color: string) => path(`M42,${y} l3,3 l6,-7`, "none", color, 1.6);
    const row = (y: number, label: string, done: boolean, color: string) =>
      rect(40, y - 5, 10, 10, "#fff", ` stroke="${color}" stroke-width="1"`) + (done ? check(y, color) : "") + text(56, y + 3, label, E, 7.5, "start");
    const sheet = rect(26, 3, 68, 57, "#fff", ` rx="2" stroke="${LABEL_E}" stroke-width=".8"`);
    return {
      ok: sheet + row(16, "Пайка", true, GREEN) + row(30, "Прозвонка", true, GREEN) + row(44, "Заливка", false, MUTED) + text(58, 68, "приёмка до заливки", GREEN),
      ng: sheet + row(16, "Пайка", false, RED) + row(30, "Прозвонка", false, RED) + row(44, "Заливка", true, RED) + mark(58, 22, 24, 14) + text(58, 68, "заливка без приёмки", RED),
    };
  },
  // 4.11.15 Окно времени после плазмы: операция в окне / окно превышено
  plasmaWindow: () => {
    const cx = 58, cy = 32, r = 24;
    const pt = (deg: number, rr: number) => `${(cx + rr * Math.sin((deg * Math.PI) / 180)).toFixed(1)},${(cy - rr * Math.cos((deg * Math.PI) / 180)).toFixed(1)}`;
    const face = (hand: number, color: string) => circle(cx, cy, r, "#fff", E, 0.9) + path(`M${cx},${cy} L${pt(0, r - 2)} A${r - 2},${r - 2} 0 0 1 ${pt(120, r - 2)} z`, "#cfe7d7", "none", 0) +
      line(cx, cy, +pt(hand, r - 5).split(",")[0], +pt(hand, r - 5).split(",")[1], color, 1.8) + circle(cx, cy, 2, color, "none", 0);
    return {
      ok: face(70, GREEN) + text(100, 14, "окно", GREEN, 7.5) + text(58, 66, "в окне после плазмы", GREEN, 7.5),
      ng: face(200, RED) + mark(cx, cy, r + 3) + text(100, 14, "окно", MUTED, 7.5) + text(58, 66, "окно времени превышено", RED, 7.5),
    };
  },
  // 4.11.24 УЗ-сварка: жилы лежат параллельно / перекрещены перед сонотродом
  weldCross: () => {
    const colors = ["#1a73e8", "#d93025", "#188038", "#f2a60d"];
    const tool = rect(58, 8, 36, 10, "#8d9297", ` rx="2" stroke="${E}" stroke-width=".7"`) + rect(58, 52, 36, 8, "#8d9297", ` rx="2" stroke="${E}" stroke-width=".7"`);
    const wire = (i: number, d: string) => path(d, "none", colors[i], 3.2);
    return {
      ok: tool + colors.map((_, i) => wire(i, `M4,${24 + i * 5.5} L100,${24 + i * 5.5}`)).join("") + text(58, 68, "жилы параллельно", GREEN),
      ng: tool + wire(0, "M4,24 L100,24") + wire(1, "M4,29.5 C30,29.5 42,35.5 62,35.5 L100,35.5") + wire(2, "M4,35.5 C30,35.5 42,29.5 62,29.5 L100,29.5") + wire(3, "M4,41 L100,41") +
        mark(40, 32.5, 12, 9) + text(58, 68, "жилы перекрещены", RED),
    };
  },
  // 4.12.1 Жила в хвостовике: прямая / сложена вдвое без указания в КД
  foldUnauth: () => {
    const barrel = rect(52, 22, 34, 24, TIN, ` rx="2" stroke="${E}" stroke-width=".8"`);
    return {
      ok: ins(4, 34, 34, 12) + cond(34, 74, 34, 6) + barrel + text(58, 62, "жила не сложена", GREEN),
      ng: ins(4, 34, 34, 12) + path("M34,30 L76,30 Q84,34 76,38 L34,38", "none", CU, 2.4) + barrel.replace(TIN, "#d8dde0") + mark(70, 34, 18, 14) + text(58, 62, "сложена вдвое без КД", RED),
    };
  },
  // 4.12.6 Сложенная часть жилы: медь / облужена или спаяна
  foldTinned: () => {
    const fold = (fill: string, edge: string) => path("M34,30 L76,30 Q84,34 76,38 L34,38", "none", edge, 5.2) + path("M34,30 L76,30 Q84,34 76,38 L34,38", "none", fill, 3.2);
    return {
      ok: ins(4, 34, 34, 12) + fold(CU, CU_LO) + text(58, 62, "сложенная часть — медь", GREEN),
      ng: ins(4, 34, 34, 12) + fold(SOLDER, TIN_LO) + circle(78, 34, 4.5, SOLDER, TIN_LO, 0.6) + mark(60, 34, 24, 10) + text(58, 62, "сложенная часть облужена", RED),
    };
  },
};

const SCENES: Record<string, () => Scene> = {
  nick: () => ({
    ok: W.ins() + W.bare(),
    ng: W.ins() + W.bare() + path("M64,31 L68,36.5 L72,31 z", "#fdf3f2", RED, 0.9) + path("M70,31 l6,-8 M72,32 l8,-6", "none", CU, 1.2) + mark(70, 30, 10, 9),
  }),
  pulled: () => ({
    ok: ins(8, 96) + `<ellipse cx="96" cy="34" rx="2.5" ry="6" fill="${CU}" stroke="${CU_LO}" stroke-width=".5"/>` + text(58, 58, "срез ровный", GREEN),
    ng: ins(8, 96) + `<ellipse cx="96" cy="34" rx="2.5" ry="6" fill="${CU}" stroke="${CU_LO}" stroke-width=".5"/>` +
      path("M96,31 L108,29 M96,34 L112,35 M96,37 L104,40", "none", CU, 1.3) + mark(104, 34, 10, 9),
  }),
  birdcage: () => ({
    ok: W.ins() + W.bare(),
    ng: W.ins() + path("M58,32 C70,22 86,20 100,24 M58,33 C72,28 88,29 101,31 M58,35 C72,40 88,40 101,38 M58,36 C70,46 86,48 100,45 M58,34 L100,34", "none", CU, 1.3) + mark(82, 34, 22, 16),
  }),
  unlay: () => ({
    ok: W.ins() + W.bare(),
    ng: W.ins() + strandsFlat(58, 100, 34, 9, 5) + mark(80, 34, 22, 10),
  }),
  insDamage: () => ({
    ok: ins(6, 110),
    ng: ins(6, 110) + path("M44,28 L52,40 L60,28", "#fdf3f2", RED, 1) + rect(48.5, 33, 7, 3, CU) + path("M72,30 l12,4 M74,34 l10,3", "none", "#fff", 0.9) + mark(52, 34, 11, 10),
  }),
  insCrush: () => ({
    ok: ins(4, 72) + circle(94, 34, 11, INS) + circle(94, 34, 4.5, CU, CU_LO) + text(94, 60, "сечение"),
    ng: path("M4,28 L28,28 Q36,32 44,32 Q52,32 60,28 L72,28 L72,40 L60,40 Q52,36 44,36 Q36,36 28,40 L4,40 z", INS, E, 0.7) + mark(44, 34, 14, 8) +
      `<ellipse cx="94" cy="34" rx="15" ry="6" fill="${INS}" stroke="${E}" stroke-width=".6"/>` + `<ellipse cx="94" cy="34" rx="6" ry="2.4" fill="${CU}"/>` + text(94, 60, "сечение", RED),
  }),
  insRagged: () => ({
    ok: W.ins() + W.bare(),
    ng: ins(4, 54) + W.bare(54, 100) + path("M54,28 L58,30 L55,32 L61,34 L56,36 L60,38 L54,40", INS, E, 0.6) + path("M60,34 l5,2", "none", INS_LO, 1.4) + mark(58, 34, 9, 10),
  }),
  insResidue: () => ({
    ok: W.ins() + W.bare(),
    ng: W.ins() + W.bare() + rect(68, 30.5, 6, 7, INS, ` rx="1" stroke="${E}" stroke-width=".4"`) + rect(84, 31, 4, 6, INS, ` rx="1" stroke="${E}" stroke-width=".4"`) + mark(78, 34, 16, 9),
  }),
  skew: () => ({
    ok: ins(8, 92) + rect(92, 28, 2, 12, CU) + line(98, 22, 98, 46, GREEN, 0.8, ' stroke-dasharray="3 2"') + text(98, 58, "90°", GREEN),
    ng: path("M8,28 L96,28 L86,40 L8,40 z", INS, E, 0.7) + line(96, 28, 86, 40, CU, 2) + line(98, 22, 98, 46, MUTED, 0.6, ' stroke-dasharray="3 2"') + mark(91, 34, 8, 10) + text(96, 58, "≠ 90°", RED),
  }),
  length: () => ({
    ok: ins(8, 106) + dim(8, 106, 52, "L по КД", GREEN),
    ng: ins(8, 84) + rect(84, 28, 22, 12, "none", ` rx="2.5" stroke="${RED}" stroke-dasharray="3 2"`) + dim(8, 84, 52, "L ≠ КД", RED),
  }),
  stripLen: () => ({
    ok: ins(4, 62) + cond(62, 96) + dim(62, 96, 20, "по КД", GREEN),
    ng: ins(4, 62) + cond(62, 108) + dim(62, 108, 20, "длиннее КД", RED) + mark(102, 34, 7, 8),
  }),
  burrWire: () => ({
    ok: ins(8, 80) + rect(80, 30, 18, 8, CU, ` stroke="${CU_LO}" stroke-width=".5"`) + line(81, 31.5, 97, 31.5, CU_HI, 0.8) + text(58, 58, "торец гладкий", GREEN),
    ng: ins(8, 80) + rect(80, 30, 18, 8, CU, ` stroke="${CU_LO}" stroke-width=".5"`) + path("M98,30 L104,26 L99,32 L105,36 L98,38", CU, CU_LO, 0.6) + mark(100, 33, 8, 10) + text(58, 58, "острые кромки", RED),
  }),
  burr: () => ({
    ok: rect(8, 26, 90, 16, TIN, ` stroke="${E}" stroke-width=".7"`) + line(98, 26, 98, 42, E, 1),
    ng: rect(8, 26, 90, 16, TIN, ` stroke="${E}" stroke-width=".7"`) + path("M98,26 L104,23 L99,29 L106,32 L99,35 L105,40 L98,42", TIN, E, 0.7) + mark(101, 33, 8, 12),
  }),
  insMelt: () => ({
    ok: ins(4, 60) + tinned(60, 64) + cup(62) ,
    ng: ins(4, 52) + path("M40,28 Q48,22 56,27 Q60,34 56,41 Q48,46 40,40 z", "#4a3a2e", E, 0.6) + rect(44, 29, 6, 2, "#2a211b") + tinned(56, 64) + cup(62) + mark(49, 34, 12, 12),
  }),
  connMelt: () => ({
    ok: housing(10, 14, 96, 40) + [30, 50, 70, 90].map((x) => circle(x, 34, 5, TIN_HI, E, 0.7)).join("") + text(58, 66, "изолятор разъёма"),
    ng: housing(10, 14, 96, 40) + [30, 50, 70, 90].map((x) => circle(x, 34, 5, TIN_HI, E, 0.7)).join("") +
      path("M40,24 Q50,20 60,26 Q62,36 56,44 Q48,48 42,42 Q38,34 40,24 z", "#b8a57a", PLAST_E, 0.6) + circle(50, 34, 5, TIN_HI, E, 0.7) + path("M44,46 q2,6 0,9", "none", "#b8a57a", 2) + mark(50, 36, 14, 14),
  }),
  braidHairs: () => ({
    ok: ins(4, 44, 34, 16, "#3f474d") + braid(44, 64, 34, 12) + ins(64, 84, 34, 8, "#f0f0f0") + cond(84, 104, 34, 3),
    ng: ins(4, 44, 34, 16, "#3f474d") + braid(44, 64, 34, 12) + ins(64, 84, 34, 8, "#f0f0f0") + cond(84, 104, 34, 3) +
      path("M64,28 l6,-8 M66,29 l9,-5 M64,40 l7,8 M65,39 l9,4 M62,28 l2,-9", "none", "#8d9297", 1) + mark(68, 34, 10, 14),
  }),
  foil: () => ({
    ok: ins(4, 48, 34, 18, "#3f474d") + ins(48, 108, 29, 5, "#1a73e8") + ins(48, 108, 39, 5, "#f9ab00"),
    ng: ins(4, 48, 34, 18, "#3f474d") + ins(48, 108, 29, 5, "#1a73e8") + ins(48, 108, 39, 5, "#f9ab00") +
      path("M48,26 L62,25 L60,31 L68,33 L58,36 L64,42 L48,42 z", "#dfe3e6", "#8d9297", 0.7) + mark(58, 34, 12, 12),
  }),
  drain: () => ({
    ok: ins(4, 48, 34, 18, "#3f474d") + ins(48, 108, 29, 5, "#1a73e8") + ins(48, 108, 37, 5, "#f9ab00") + tinned(48, 108, 44, 2.5) + text(78, 58, "дренаж"),
    ng: ins(4, 48, 34, 18, "#3f474d") + ins(48, 108, 29, 5, "#1a73e8") + ins(48, 108, 37, 5, "#f9ab00") + tinned(48, 64, 44, 2.5) + tinned(74, 108, 44, 2.5) + mark(69, 44, 8, 6) + text(78, 60, "обрыв", RED),
  }),
  pairs: () => {
    const twist = (x1: number, x2: number, c1: string, c2: string) => {
      let out = "";
      for (let x = x1; x < x2; x += 10) out += path(`M${x},30 C${x + 3},30 ${x + 2},38 ${x + 5},38 C${x + 8},38 ${x + 7},30 ${x + 10},30`, "none", c1, 3) + path(`M${x},38 C${x + 3},38 ${x + 2},30 ${x + 5},30 C${x + 8},30 ${x + 7},38 ${x + 10},38`, "none", c2, 3);
      return out;
    };
    return {
      ok: ins(4, 34, 34, 18, "#3f474d") + twist(34, 94, "#1a73e8", "#e8f0fe") + line(94, 30, 106, 30, "#1a73e8", 3) + line(94, 38, 106, 38, "#c6d4ea", 3) + dim(94, 106, 20, "≤ КД", GREEN),
      ng: ins(4, 34, 34, 18, "#3f474d") + twist(34, 54, "#1a73e8", "#e8f0fe") + line(54, 28, 106, 26, "#1a73e8", 3) + line(54, 40, 106, 42, "#c6d4ea", 3) + dim(54, 106, 16, "раскрутка > КД", RED),
    };
  },
  coaxSteps: () => {
    const coax = (a: number, b: number, c: number, d: number) => ins(4, a, 34, 18, "#3f474d") + braid(a, b, 34, 14) + ins(b, c, 34, 9, "#f4f4f2") + tinned(c, d, 34, 3);
    return {
      ok: coax(40, 56, 80, 104) + dim(40, 56, 16, "A") + dim(56, 80, 16, "B") + dim(80, 104, 16, "C"),
      ng: coax(40, 70, 80, 110) + dim(40, 70, 16, "A ≠", RED) + dim(70, 80, 54, "B ≠", RED) + mark(74, 34, 10, 10),
    };
  },
  coaxPin: () => ({
    ok: circle(58, 34, 26, TIN, E, 0.8) + circle(58, 34, 17, "#f4f4f2", E, 0.6) + circle(58, 34, 4, CU_HI, E, 0.6) + line(40, 34, 76, 34, GREEN, 0.5, ' stroke-dasharray="2 2"') + line(58, 16, 58, 52, GREEN, 0.5, ' stroke-dasharray="2 2"'),
    ng: circle(58, 34, 26, TIN, E, 0.8) + circle(58, 34, 17, "#f4f4f2", E, 0.6) + circle(66, 28, 4, CU_HI, E, 0.6) + line(40, 34, 76, 34, MUTED, 0.5, ' stroke-dasharray="2 2"') + line(58, 16, 58, 52, MUTED, 0.5, ' stroke-dasharray="2 2"') + mark(66, 28, 8),
  }),
  // Пайка пина коаксиала (SDR_COAX_pin): все рисунки на одном виде соединения, дефекты по IPC/WHMA-A-620F разд. 4 и 13.
  // Лужение свободного конца погружением (SDR_tin, IPC/WHMA-A-620 разд. 4): рисунок — сам лужёный конец, без контакта.
  tnExcess: () => ({
    ok: tinOk() + text(58, 58, "контур жил виден", GREEN),
    ng: TN.ins() + TN.gap() + path("M55,29.5 L94,29.5 C103,28.5 108,33 107,38 C107,43 103,46 99,45 C98,50 97,54 95,55 C94,52 94,48 94,45 L94,38.5 L55,38.5 Q53,34 55,29.5 z", SOLDER, TIN_LO, 0.7) +
      line(58, 31.3, 92, 31.3, "#fff", 1.1) + mark(96, 40, 13, 13) + text(58, 62, "наплыв, жил не видно", RED),
  }),
  tnLack: () => ({
    ok: tinOk() + text(58, 58, "припой по всей длине", GREEN),
    ng: TN.ins() + cond(50, 104) + tinEnd(56, 66, 34, 6, { lay: false }) + tinEnd(76, 84, 34, 6, { lay: false }) +
      path("M103,32 L110,28 M103,36 L111,39", "none", CU, 1.2) + mark(88, 34, 20, 10) + text(58, 58, "видна медь", RED),
  }),
  tnCold: () => ({
    ok: tinOk() + text(58, 58, "гладкий, блестящий", GREEN),
    ng: TN.ins() + TN.gap() + TN.end({ dull: true, lay: false }) +
      [59, 65, 71, 77, 83, 89, 95, 62, 68, 74, 80, 86, 92, 99].map((x, i) => circle(x, i < 7 ? 32.4 : 35.8, 0.8, "#6f777c", "none", 0)).join("") +
      mark(80, 34, 25, 9) + text(58, 58, "матовый, зернистый", RED),
  }),
  tnMelt: () => ({
    ok: tinOk() + text(58, 58, "изоляция целая", GREEN),
    ng: ins(4, 42) + path("M40,28 Q47,23 52,27 Q56,34 52,41 Q47,45 40,40 z", "#4a3a2e", E, 0.6) + rect(43, 30, 5, 2, "#2a211b") +
      cond(51, 55) + TN.end() + mark(47, 34, 11, 12) + text(58, 60, "изоляция оплавлена", RED),
  }),
  tnWick: () => ({
    ok: tinOk() + dim(50, 55, 22, "зазор", GREEN) + text(30, 58, "жила гибкая", GREEN),
    ng: ins(4, 50) + rect(26, 31, 26, 6, SOLDER, ` rx="2" opacity=".85"`) + path("M26,30 L50,30 M26,38 L50,38", "none", RED, 0.9, ' stroke-dasharray="2 2"') +
      tinEnd(50, 104) + mark(38, 34, 14, 10) + text(58, 58, "припой под изоляцией", RED),
  }),
  tnSplay: () => ({
    ok: tinOk() + text(58, 58, "повив ровный", GREEN),
    ng: TN.ins() + TN.gap() + path("M55,32 C68,24 84,21 100,23 M55,33 C70,29 86,29 102,30 M55,35 C70,39 86,39 102,38 M55,36 C68,44 84,47 100,45", "none", TIN_LO, 2.6) +
      path("M55,32 C68,24 84,21 100,23 M55,33 C70,29 86,29 102,30 M55,35 C70,39 86,39 102,38 M55,36 C68,44 84,47 100,45", "none", SOLDER, 1.6) +
      mark(82, 34, 24, 15) + text(58, 62, "проволоки врозь, залиты", RED),
  }),
  cxCold: () => ({ view: CX_VIEW, ok: coaxPinJoint(), ng: coaxPinJoint({ fill: "cold" }) + mark(58, 34, 10, 9) }),
  cxLack: () => ({
    view: CX_VIEW,
    ok: coaxPinJoint() + dim(56, 76, 22, "100 %", GREEN),
    ng: coaxPinJoint({ fill: "lack" }) + dim(56, 76, 22, "< 75 %", RED) + mark(66, 33, 12, 6),
  }),
  cxExcess: () => ({
    view: CX_VIEW,
    ok: coaxPinJoint() + line(46, 28, 84, 28, GREEN, 0.6, ' stroke-dasharray="3 2"') + line(46, 40, 84, 40, GREEN, 0.6, ' stroke-dasharray="3 2"') + text(66, 54, "в Ø контакта", GREEN),
    ng: coaxPinJoint({ fill: "excess" }) + line(40, 28, 90, 28, RED, 0.6, ' stroke-dasharray="3 2"') + line(40, 40, 90, 40, RED, 0.6, ' stroke-dasharray="3 2"') + mark(66, 30, 20, 19),
  }),
  cxShift: () => ({
    view: CX_VIEW,
    ok: coaxPinJoint() + line(24, 34, 116, 34, GREEN, 0.5, ' stroke-dasharray="3 2"'),
    ng: coaxPinJoint({ tilt: true }) + line(24, 34, 116, 34, MUTED, 0.5, ' stroke-dasharray="3 2"') + path("M53,31 l2,2 l-2,2 l2,2", "none", RED, 1) + mark(54, 34, 7, 8),
  }),
  cxMelt: () => ({ view: CX_VIEW, ok: coaxPinJoint(), ng: coaxPinJoint({ melt: true }) + mark(43, 34, 11, 12) }),
  cxGap: () => ({
    view: CX_VIEW,
    ok: coaxPinJoint() + dim(50, 56, 22, "по КД", GREEN),
    ng: coaxPinJoint({ diel: 40 }) + dim(40, 56, 22, "голая жила", RED) + mark(48, 34, 10, 7),
  }),
  cxBent: () => ({
    view: CX_VIEW,
    ok: coaxPinJoint() + line(80, 34, 115, 34, GREEN, 0.5, ' stroke-dasharray="3 2"'),
    ng: coaxPinJoint({ bent: true }) + line(80, 34, 115, 34, MUTED, 0.5, ' stroke-dasharray="3 2"') + mark(100, 27, 13, 9),
  }),
  cxStrays: () => ({ view: CX_VIEW, ok: coaxPinJoint(), ng: coaxPinJoint({ strays: true }) + mark(57, 34, 11, 13) }),
  crimpIns: () => ({
    ok: ins(2, 40) + cond(40, 66, 34, 6) + terminal(14),
    ng: ins(2, 52) + cond(52, 66, 34, 6) + terminal(14) + rect(42, 30.5, 10, 7, INS, ` opacity=".85"`) + mark(46, 34, 9, 10),
    view: CLOSE_UP,
  }),
  brush: () => ({
    ok: ins(2, 40) + cond(40, 66, 34, 6) + terminal(14) + rect(62, 31, 4, 6, CU) + dim(62, 66, 50, "", GREEN) + mark(64, 34, 5, 6, GREEN),
    ng: ins(2, 40) + cond(40, 78, 34, 6) + terminal(14) + rect(62, 31, 16, 6, CU) + mark(71, 34, 9, 9),
    view: CLOSE_UP,
  }),
  strayCrimp: () => ({
    ok: ins(2, 40) + cond(40, 66, 34, 6) + terminal(14),
    ng: ins(2, 40) + cond(40, 66, 34, 6) + terminal(14) + path("M40,28 C44,18 50,16 56,14 M42,28 C46,22 50,20 54,20", "none", CU, 1.2) + mark(48, 20, 10, 8),
    view: CLOSE_UP,
  }),
  crimpHeight: () => ({
    ok: crimpSection(44, 34, "ok") + vdim(76, 21, 47, "H по КД", GREEN),
    ng: crimpSection(44, 34, "loose") + vdim(76, 17, 47, "H > КД", RED),
  }),
  crimpFlash: () => ({
    ok: crimpSection(58, 34, "ok"),
    ng: crimpSection(58, 34, "over") + text(58, 60, "облой, «уши»", RED),
  }),
  bellmouth: () => ({
    ok: ins(2, 40) + cond(40, 66, 34, 6) + terminal(14) + path("M42,24 L40,21 M42,44 L40,47", "none", E, 0.9) + mark(41, 34, 5, 14, GREEN),
    ng: ins(2, 40) + cond(40, 66, 34, 6) + terminal(14) + line(42, 24, 42, 44, RED, 1.6) + mark(42, 34, 5, 14),
    view: CLOSE_UP,
  }),
  insGrip: () => ({
    ok: ins(2, 40) + cond(40, 66, 34, 6) + terminal(14),
    ng: ins(2, 40) + cond(40, 66, 34, 6) + terminal(14, 34, { wings: "open" }) + mark(24, 26, 14, 12),
    view: CLOSE_UP,
  }),
  insBite: () => ({
    ok: ins(2, 40) + cond(40, 66, 34, 6) + terminal(14),
    ng: ins(2, 40) + cond(40, 66, 34, 6) + terminal(14, 34, { wings: "bite" }) + mark(24, 28, 10, 8),
    view: CLOSE_UP,
  }),
  termBent: () => ({
    ok: ins(2, 40) + cond(40, 66, 34, 6) + terminal(14) + line(72, 22, 108, 22, GREEN, 0.6, ' stroke-dasharray="3 2"'),
    ng: ins(2, 40) + cond(40, 66, 34, 6) + terminal(14, 34, { box: "bent" }) + line(72, 34, 110, 34, MUTED, 0.6, ' stroke-dasharray="3 2"') + mark(94, 26, 14, 10),
  }),
  banana: () => ({
    ok: ins(2, 40) + cond(40, 66, 34, 6) + terminal(14) + line(10, 48, 108, 48, GREEN, 0.6, ' stroke-dasharray="3 2"'),
    ng: `<g transform="translate(4 4) scale(.92) rotate(-7 58 34)">` + ins(2, 40) + cond(40, 66, 34, 6) + terminal(14) + `</g>` + line(10, 48, 108, 48, MUTED, 0.6, ' stroke-dasharray="3 2"') + arrow(104, 44, 104, 30) + text(58, 64, "изгиб вдоль оси", RED),
  }),
  lance: () => ({
    ok: ins(2, 40) + cond(40, 66, 34, 6) + terminal(14) + text(82, 60, "усик отогнут", GREEN),
    ng: ins(2, 40) + cond(40, 66, 34, 6) + terminal(14, 34, { lance: "broken" }) + mark(82, 22, 8, 6) + text(82, 60, "усик сломан", RED),
  }),
  seat: () => ({
    ok: housing(56, 14, 56, 40) + rect(60, 24, 52, 20, "#fff", ` stroke="${PLAST_E}" stroke-width=".5"`) + ins(4, 52, 34, 8) + rect(52, 29, 56, 10, TIN, ` stroke="${E}" stroke-width=".7"`) + text(84, 62, "до упора"),
    ng: housing(56, 14, 56, 40) + rect(60, 24, 52, 20, "#fff", ` stroke="${PLAST_E}" stroke-width=".5"`) + ins(4, 36, 34, 8) + rect(36, 29, 56, 10, TIN, ` stroke="${E}" stroke-width=".7"`) + dim(92, 110, 20, "", RED) + text(58, 62, "контакт не до упора", RED) + mark(101, 34, 10, 8),
  }),
  pull: () => ({
    ok: ins(0, 48) + cond(48, 72, 34, 6) + terminal(22) + arrow(18, 50, 2, 50, GREEN) + text(58, 62, "усилие отрыва по норме", GREEN),
    ng: ins(0, 14) + cond(14, 26, 34, 6) + terminal(22) + arrow(18, 50, 2, 50) + mark(58, 32, 12, 9) + text(58, 64, "жила вырвана из обжима", RED),
  }),
  latch: () => ({
    ok: housing(10, 22, 90, 26) + path("M40,22 L58,12 L76,12 L76,22", PLAST, PLAST_E, 0.9) + text(58, 62, "защёлка целая"),
    ng: housing(10, 22, 90, 26) + path("M40,22 L50,17 M58,12 L76,12 L76,22", "none", PLAST_E, 0.9) + path("M50,17 l4,-1 l-2,4 z", "none", RED, 1) + mark(54, 16, 11, 7),
  }),
  housingCrack: () => ({
    ok: housing(12, 12, 92, 44),
    ng: housing(12, 12, 92, 44) + path("M52,12 L48,24 L58,30 L50,42 L56,56", "none", RED, 1.6) + mark(53, 34, 10, 22),
  }),
  housingGap: () => ({
    ok: housing(8, 16, 50, 36) + housing(58, 16, 50, 36) + ins(108, 116, 34, 10),
    ng: housing(8, 16, 50, 36) + `<g transform="rotate(4 70 34)">` + housing(66, 16, 50, 36) + `</g>` + dim(58, 66, 62, "зазор", RED) + mark(62, 34, 6, 20),
  }),
  solderCold: () => ({
    ok: ins(4, 42) + tinned(42, 58) + cup(56) + path("M56,32 C66,30 84,30 94,30 L94,40 L56,40 z", SOLDER, TIN_LO, 0.6) + path("M60,31.5 C70,30.5 84,30.5 92,31", "none", "#fff", 1.3),
    ng: ins(4, 42) + tinned(42, 58) + cup(56) + path("M56,30 C62,24 72,27 78,25 C84,23 90,27 94,26 L94,40 L56,40 z", "#a4aaae", TIN_LO, 0.6) +
      [60, 66, 72, 78, 84, 90, 63, 75, 87, 69].map((x, i) => circle(x, i < 7 ? 32 : 36, 0.9, "#7d8488", "none", 0)).join("") + path("M72,27 l3,6", "none", RED, 1) + mark(75, 31, 20, 10),
  }),
  solderLack: () => ({
    ok: ins(4, 42) + tinned(42, 58) + cup(56) + path("M56,30 C66,29 84,29 94,29 L94,40 L56,40 z", SOLDER, TIN_LO, 0.6) + line(54, 29, 96, 29, GREEN, 0.6, ' stroke-dasharray="3 2"'),
    ng: ins(4, 42) + tinned(42, 58) + cup(56) + path("M56,36 C66,36 84,37 94,36 L94,40 L56,40 z", SOLDER, TIN_LO, 0.6) + cond(56, 92, 34, 5) + line(54, 29, 96, 29, RED, 0.8, ' stroke-dasharray="3 2"') + mark(75, 33, 20, 7),
  }),
  solderExcess: () => ({
    ok: ins(4, 42) + tinned(42, 58) + cup(56) + path("M56,30 C66,29 84,29 94,29 L94,40 L56,40 z", SOLDER, TIN_LO, 0.6),
    ng: ins(4, 42) + tinned(42, 58) + cup(56) + path("M54,40 C48,34 52,18 72,17 C90,16 100,24 98,36 C100,44 96,52 90,50 C88,48 70,48 54,40 z", SOLDER, TIN_LO, 0.7) + path("M64,22 C72,19 84,19 92,22", "none", "#fff", 1.2) + mark(76, 33, 26, 20),
  }),
  icicle: () => ({
    ok: ins(4, 42) + tinned(42, 58) + cup(56) + path("M56,30 C66,29 84,29 94,29 L94,40 L56,40 z", SOLDER, TIN_LO, 0.6),
    ng: ins(4, 42) + tinned(42, 58) + cup(56) + path("M56,30 C66,29 80,29 84,29 L90,16 L92,29 L100,29 L100,40 L56,40 z", SOLDER, TIN_LO, 0.6) + mark(86, 20, 7, 9),
  }),
  bridge: () => {
    const pins = (bridge: boolean) => [26, 58, 90].map((x) => rect(x - 8, 14, 16, 40, TIN, ` rx="2" stroke="${E}" stroke-width=".7"`) + `<ellipse cx="${x}" cy="34" rx="6" ry="8" fill="${SOLDER}" stroke="${TIN_LO}" stroke-width=".6"/>`).join("") +
      (bridge ? path("M32,30 Q42,26 52,30 L52,38 Q42,42 32,38 z", SOLDER, RED, 1) + mark(42, 34, 12, 9) : "");
    return { ok: pins(false), ng: pins(true) };
  },
  wicking: () => ({
    ok: ins(4, 50) + cond(50, 64) + tinned(64, 66) + cup(62) + dim(50, 64, 20, "жила гибкая", GREEN),
    ng: ins(4, 50) + tinned(30, 66, 34, 6) + rect(30, 28, 20, 12, "#fff", ` opacity=".35"`) + path("M30,27 L50,26 L50,42 L30,41", "none", RED, 0.9, ' stroke-dasharray="2 2"') + cup(62) + mark(40, 34, 14, 10) + text(58, 60, "припой под изоляцией", RED),
  }),
  gapIns: () => ({
    ok: ins(4, 50) + tinned(50, 58) + cup(56) + path("M56,30 C66,29 84,29 94,29 L94,40 L56,40 z", SOLDER, TIN_LO, 0.6) + dim(50, 56, 20, "≤ 2d", GREEN),
    ng: ins(4, 26) + cond(26, 58) + cup(56) + path("M56,30 C66,29 84,29 94,29 L94,40 L56,40 z", SOLDER, TIN_LO, 0.6) + dim(26, 56, 20, "голая жила", RED) + mark(41, 34, 16, 8),
  }),
  insInCup: () => ({
    ok: ins(4, 50) + tinned(50, 58) + cup(56) + path("M56,30 C66,29 84,29 94,29 L94,40 L56,40 z", SOLDER, TIN_LO, 0.6),
    ng: ins(4, 72) + cup(56) + path("M72,30 C80,29 84,29 94,29 L94,40 L72,40 z", SOLDER, TIN_LO, 0.6) + ins(56, 72, 34, 12) + mark(64, 34, 10, 10),
  }),
  pad: () => ({
    ok: rect(4, 38, 108, 10, "#2e7d4f", ` stroke="${E}" stroke-width=".6"`) + rect(40, 34, 36, 4, CU, ` stroke="${CU_LO}" stroke-width=".5"`) + path("M40,34 Q58,22 76,34 z", SOLDER, TIN_LO, 0.6),
    ng: rect(4, 38, 108, 10, "#2e7d4f", ` stroke="${E}" stroke-width=".6"`) + path("M40,34 L66,34 L80,26 L76,24 L64,31 L40,31 z", CU, CU_LO, 0.5) + path("M40,31 Q52,22 64,31 z", SOLDER, TIN_LO, 0.6) + mark(74, 30, 10, 7),
  }),
  shrinkLoose: () => ({
    ok: ins(4, 112) + tube(34, 86, 34, 15),
    ng: ins(4, 112) + tube(30, 90, 34, 24) + mark(60, 22, 26, 5) + text(60, 60, "трубка болтается", RED),
  }),
  shrinkBurn: () => ({
    ok: ins(4, 112) + tube(34, 86, 34, 15),
    ng: ins(4, 112) + tube(34, 86, 34, 15) + `<ellipse cx="54" cy="31" rx="7" ry="4" fill="#15181b"/>` + `<ellipse cx="54" cy="31" rx="3" ry="1.6" fill="#8a5a2b"/>` + path("M68,28 l6,8", "none", "#e8eaed", 1) + mark(60, 32, 16, 9),
  }),
  shrinkShift: () => ({
    ok: ins(4, 50) + tinned(50, 70) + ins(70, 112) + tube(38, 82, 34, 15) + dim(38, 50, 16, "", GREEN) + dim(70, 82, 16, "", GREEN),
    ng: ins(4, 50) + tinned(50, 70) + ins(70, 112) + tube(62, 106, 34, 15) + mark(56, 34, 8, 8) + text(56, 58, "не закрыто", RED),
  }),
  glue: () => ({
    ok: ins(4, 112) + `<ellipse cx="32" cy="34" rx="4" ry="9" fill="#e8b84a" opacity=".85"/>` + `<ellipse cx="88" cy="34" rx="4" ry="9" fill="#e8b84a" opacity=".85"/>` + tube(32, 88, 34, 15) + text(60, 58, "валик клея по торцам", GREEN),
    ng: ins(4, 112) + tube(32, 88, 34, 15) + mark(32, 34, 5, 11) + mark(88, 34, 5, 11),
  }),
  tubeFlat: () => ({
    ok: tube(4, 72, 34, 16) + circle(94, 34, 11, TUBE) + circle(94, 34, 8, "#fff", E, 0.4),
    ng: tube(4, 72, 34, 16) + `<ellipse cx="94" cy="34" rx="16" ry="4.5" fill="${TUBE}"/>` + `<ellipse cx="94" cy="34" rx="13" ry="1.2" fill="#fff"/>` + mark(94, 34, 19, 8),
  }),
  markText: () => ({
    ok: ins(4, 112) + rect(44, 25, 32, 18, "#fff", ` rx="2" stroke="${E}" stroke-width=".7"`) + text(60, 38, "X1:3", E, 10, "middle", 700),
    ng: ins(4, 112) + rect(44, 25, 32, 18, "#fff", ` rx="2" stroke="${E}" stroke-width=".7"`) + text(60, 38, "X1:3", "#b0b6ba", 10, "middle", 700) + path("M48,36 q6,-6 12,0 t12,0", "none", "#9aa0a6", 2.2) + mark(60, 34, 20, 12),
  }),
  markShift: () => ({
    ok: ins(4, 108) + cond(108, 116) + rect(78, 26, 18, 16, "#fff", ` rx="2" stroke="${E}" stroke-width=".7"`) + text(87, 37, "X1", E, 8) + dim(96, 108, 18, "по КД", GREEN),
    ng: ins(4, 108) + cond(108, 116) + rect(30, 26, 18, 16, "#fff", ` rx="2" stroke="${E}" stroke-width=".7"`) + text(39, 37, "X1", E, 8) + arrow(52, 50, 84, 50) + mark(39, 34, 13, 11),
  }),
  pinout: () => {
    const cols = ["#d93025", "#1a73e8", "#188038", "#f9ab00"];
    const wires = (order: number[]) => order.map((c, i) => line(4, 20 + i * 9, 76, 20 + i * 9, cols[c], 3.5)).join("");
    const pins = housing(76, 12, 34, 42) + [0, 1, 2, 3].map((i) => text(93, 23 + i * 9, String(i + 1), E, 7.5)).join("");
    return { ok: wires([0, 1, 2, 3]) + pins, ng: wires([0, 2, 1, 3]) + pins + mark(70, 33.5, 10, 10) + text(58, 66, "жилы 2 и 3 перепутаны", RED) };
  },
  missing: () => ({
    ok: ins(4, 112) + tube(40, 62, 34, 16) + housing(80, 18, 30, 32),
    ng: ins(4, 112) + rect(40, 26, 22, 16, "none", ` rx="3" stroke="${RED}" stroke-dasharray="3 2" stroke-width="1.2"`) + text(51, 38, "?", RED, 11, "middle", 700) + housing(80, 18, 30, 32) + text(51, 60, "деталь не установлена", RED),
  }),
  tieLoose: () => {
    const bundle = [26, 32, 38, 44].map((y) => line(4, y, 112, y, INS, 5.5)).join("");
    return {
      ok: bundle + rect(54, 20, 7, 30, "#2a2e33", ` rx="2"`),
      ng: bundle + rect(54, 12, 7, 44, "none", ` rx="3" stroke="#2a2e33" stroke-width="2.4"`) + mark(57.5, 34, 11, 24),
    };
  },
  tieTight: () => ({
    ok: [26, 32, 38, 44].map((y) => line(4, y, 112, y, INS, 5.5)).join("") + rect(54, 20, 7, 30, "#2a2e33", ` rx="2"`),
    ng: [26, 32, 38, 44].map((y, i) => path(`M4,${y} L44,${y} Q57,${35 + (y - 35) * 0.45} 70,${y} L112,${y}`, "none", INS, 5.5)).join("") + rect(54, 23, 7, 24, "#2a2e33", ` rx="2"`) + mark(57.5, 35, 16, 16),
  }),
  tieTail: () => ({
    ok: [26, 32, 38, 44].map((y) => line(4, y, 112, y, INS, 5.5)).join("") + rect(54, 20, 7, 30, "#2a2e33", ` rx="2"`) + rect(61, 20, 3, 5, "#2a2e33"),
    ng: [26, 32, 38, 44].map((y) => line(4, y, 112, y, INS, 5.5)).join("") + rect(54, 20, 7, 30, "#2a2e33", ` rx="2"`) + path("M61,20 L72,10 L73,14 L63,24 z", "#2a2e33", "#2a2e33", 0.5) + mark(69, 14, 8, 7),
  }),
  twist: () => ({
    ok: ins(4, 112, 34, 16) + line(4, 30, 112, 30, "#fff", 1.2, ' opacity=".35"'),
    ng: path("M4,26 L40,26 C52,26 52,42 64,42 L112,42 L112,26 L64,26 C52,26 52,42 40,42 L4,42 z", INS, E, 0.7) + mark(52, 34, 14, 12),
  }),
  cover: () => ({
    ok: [30, 36].map((y) => line(4, y, 112, y, INS, 5)).join("") + braid(20, 96, 33, 16) + dim(20, 96, 56, "по КД", GREEN),
    ng: [30, 36].map((y) => line(4, y, 112, y, INS, 5)).join("") + braid(20, 76, 33, 16) + dim(20, 96, 56, "по КД", RED) + mark(86, 33, 10, 10),
  }),
  fray: () => ({
    ok: braid(4, 96, 34, 16) + rect(92, 26, 5, 16, "#6d767b", ` rx="2"`) + text(58, 60, "торец оплавлен", GREEN),
    ng: braid(4, 92, 34, 16) + path("M92,27 l8,-5 M92,31 l12,-2 M92,35 l10,3 M92,39 l9,7 M92,42 l4,8", "none", "#8d9297", 1) + mark(98, 35, 9, 13),
  }),
  pinch: () => ({
    ok: housing(8, 10, 100, 20) + housing(8, 38, 100, 20) + ins(4, 112, 34, 6),
    ng: housing(8, 10, 100, 22) + housing(8, 36, 100, 22) + path("M4,34 L44,34 Q52,34 56,33 L64,33 Q70,34 112,34", "none", INS, 6) + path("M52,31 L60,31", "none", RED, 1.4) + mark(58, 33, 12, 6),
  }),
  clamp: () => ({
    ok: housing(40, 10, 70, 48) + ins(4, 72, 34, 16, "#3f474d") + rect(52, 22, 10, 24, TIN, ` stroke="${E}" stroke-width=".7"`) + text(57, 66, "оболочка под зажимом"),
    ng: housing(40, 10, 70, 48) + ins(4, 44, 34, 16, "#3f474d") + [29, 35, 41].map((y) => line(44, y, 100, y, INS, 4)).join("") + rect(52, 22, 10, 24, TIN, ` stroke="${E}" stroke-width=".7"`) + mark(50, 34, 10, 14),
  }),
  rjDepth: () => ({
    ok: housing(30, 14, 82, 40, "#e9f1f5") + [22, 28, 34, 40, 46].map((y, i) => line(4, y, 108, y, ["#f9ab00", "#188038", "#1a73e8", "#a142f4", "#d93025"][i], 2.4)).join(""),
    ng: housing(30, 14, 82, 40, "#e9f1f5") + [22, 28, 34, 40, 46].map((y, i) => line(4, y, i === 2 ? 88 : 108, y, ["#f9ab00", "#188038", "#1a73e8", "#a142f4", "#d93025"][i], 2.4)).join("") + mark(98, 34, 11, 5),
  }),
  idc: () => ({
    ok: ins(4, 112, 38, 10) + path("M52,14 L52,42 M64,14 L64,42", "none", TIN_LO, 3) + rect(55, 33, 6, 10, CU, ` opacity=".9"`),
    ng: ins(4, 112, 44, 10) + path("M52,14 L52,40 M64,14 L64,40", "none", TIN_LO, 3) + mark(58, 42, 12, 8) + text(58, 64, "жила вне ножа", RED),
  }),
  mold: () => ({
    ok: ins(4, 30, 34, 10) + path("M30,24 L90,22 L106,26 L106,42 L90,46 L30,44 z", "#2f3337", E, 0.8),
    ng: ins(4, 30, 34, 10) + path("M30,24 L90,22 L106,26 L106,42 L90,46 L30,44 z", "#2f3337", E, 0.8) + path("M50,22 L58,15 L60,22 z", "#2f3337", E, 0.6) +
      circle(70, 34, 3, "#6d7378", "none", 0) + circle(80, 38, 2, "#6d7378", "none", 0) + mark(56, 19, 7, 6) + mark(75, 36, 9, 6),
  }),
  seal: () => ({
    ok: housing(40, 14, 70, 40) + [26, 34, 42].map((y) => rect(24, y - 1.5, 16, 3, TIN, ` stroke="${E}" stroke-width=".4"`)).join("") + rect(96, 16, 14, 36, "#9fd3c7", ` opacity=".85"`),
    ng: housing(40, 14, 70, 40) + [26, 34, 42].map((y) => rect(24, y - 1.5, 16, 3, TIN, ` stroke="${E}" stroke-width=".4"`)).join("") + `<ellipse cx="30" cy="34" rx="8" ry="10" fill="#9fd3c7" opacity=".85"/>` + mark(30, 34, 12, 14),
  }),
  tape: () => {
    const wraps = (gap: boolean) => {
      let out = ins(4, 112, 34, 14, "#8d969c");
      for (let x = 20; x < 100; x += gap ? 16 : 9) out += path(`M${x},27 L${x + 10},27 L${x + 16},41 L${x + 6},41 z`, "#2f3337", E, 0.5);
      return out;
    };
    return { ok: wraps(false) + text(60, 58, "нахлёст по КД", GREEN), ng: wraps(true) + mark(58, 34, 16, 11) + text(60, 60, "просветы", RED) };
  },
  corrCut: () => {
    const corr = (end: number) => {
      let out = "";
      for (let x = 6; x < end; x += 8) out += rect(x, 22, 6, 24, "#2f3337", ` rx="2"`) + rect(x + 6, 25, 2, 18, "#2f3337");
      return out;
    };
    return {
      ok: corr(94) + line(94, 20, 94, 48, GREEN, 0.8, ' stroke-dasharray="3 2"') + text(58, 62, "рез по впадине", GREEN),
      ng: corr(90) + path("M90,22 L96,22 L94,28 L97,34 L94,40 L96,46 L90,46 z", "#2f3337", RED, 0.8) + mark(94, 34, 8, 14) + text(58, 62, "рез по гребню", RED),
    };
  },
  endCap: () => ({
    ok: ins(4, 96, 34, 12) + path("M92,26 L104,26 Q110,34 104,42 L92,42 z", "#f9ab00", E, 0.7),
    ng: ins(4, 96, 34, 12) + cond(96, 106, 34, 6) + mark(101, 34, 8, 8) + text(58, 58, "торец открыт", RED),
  }),
  test: () => {
    const conns = housing(4, 12, 20, 44) + housing(92, 12, 20, 44);
    return {
      ok: conns + [20, 30, 40, 50].map((y) => line(24, y - 2, 92, y - 2, "#5f6b73", 1.4)).join("") + text(58, 64, "1–1, 2–2, 3–3, 4–4", GREEN),
      ng: conns + line(24, 18, 50, 18, "#5f6b73", 1.4) + line(62, 18, 92, 18, "#5f6b73", 1.4) + line(24, 28, 92, 38, "#5f6b73", 1.4) + line(24, 38, 92, 28, "#5f6b73", 1.4) + line(24, 48, 92, 48, "#5f6b73", 1.4) + mark(56, 18, 8, 5) + mark(58, 33, 8, 7),
    };
  },
  time: () => {
    const clock = (color: string, hand: number) => circle(58, 32, 20, "#fff", color, 1.6) + line(58, 32, 58, 18, color, 1.6) + line(58, 32, 58 + 14 * Math.cos(hand), 32 + 14 * Math.sin(hand), color, 1.6);
    return { ok: clock(GREEN, 0) + text(58, 64, "выдержка по КД", GREEN), ng: clock(RED, 2.4) + text(58, 64, "раньше / позже срока", RED) };
  },
  shieldSpread: () => ({
    ok: ins(4, 44, 34, 16, "#3f474d") + path("M44,26 L64,22 L80,22 L80,46 L64,46 L44,42 z", "#dfe3e6", "#8d9297", 0.7) + braid(56, 80, 34, 26) + rect(80, 24, 30, 20, TIN, ` stroke="${E}" stroke-width=".7"`),
    ng: ins(4, 44, 34, 16, "#3f474d") + path("M44,26 L64,22 L80,22 L80,46 L64,46 L44,42 z", "#dfe3e6", "#8d9297", 0.7) + braid(56, 80, 28, 12) + rect(80, 24, 30, 20, TIN, ` stroke="${E}" stroke-width=".7"`) + path("M84,40 l10,-6 M84,42 l12,-2", "none", "#8d9297", 1) + mark(68, 42, 14, 6),
  }),
  ferrule: () => ({
    ok: ins(4, 44, 34, 12) + path("M44,26 L56,26 L56,42 L44,42 z", "#d93025", E, 0.6) + rect(56, 29, 44, 10, TIN, ` stroke="${E}" stroke-width=".7"`) + rect(98, 30.5, 2, 7, CU),
    ng: ins(4, 44, 34, 12) + cond(44, 60, 34, 6) + path("M60,26 L72,26 L72,42 L60,42 z", "#d93025", E, 0.6) + rect(72, 29, 40, 10, TIN, ` stroke="${E}" stroke-width=".7"`) + mark(52, 34, 9, 8) + text(58, 60, "наконечник сполз", RED),
  }),
  lugRing: () => ({
    ok: ins(4, 30, 34, 12) + rect(30, 29, 40, 10, TIN, ` stroke="${E}" stroke-width=".7"`) + circle(86, 34, 17, TIN, E, 0.8) + circle(86, 34, 7, "#fff", E, 0.6),
    ng: ins(4, 30, 34, 12) + rect(30, 29, 40, 10, TIN, ` stroke="${E}" stroke-width=".7"`) + `<ellipse cx="86" cy="34" rx="19" ry="14" fill="${TIN}" stroke="${E}" stroke-width=".8"/>` + `<ellipse cx="86" cy="34" rx="7" ry="3" fill="#fff" stroke="${E}" stroke-width=".6"/>` + mark(86, 34, 10, 7),
  }),
  weldLow: () => ({
    ok: ins(4, 34) + cond(34, 50) + rect(50, 28, 20, 12, "#d9a06e", ` rx="2" stroke="${CU_LO}" stroke-width=".6"`) + cond(70, 84) + ins(84, 112),
    ng: ins(4, 34) + cond(34, 50) + strandsFlat(50, 70, 34, 10, 5) + cond(70, 84) + ins(84, 112) + mark(60, 34, 12, 10) + text(60, 60, "жилы не сварены", RED),
  }),
  weldHigh: () => ({
    ok: ins(4, 34) + cond(34, 50) + rect(50, 28, 20, 12, "#d9a06e", ` rx="2" stroke="${CU_LO}" stroke-width=".6"`) + cond(70, 84) + ins(84, 112),
    ng: ins(4, 34) + cond(34, 50) + rect(50, 29, 20, 10, "#5a3a22", ` rx="2" stroke="${E}" stroke-width=".6"`) + path("M52,29 l3,-4 M60,29 l2,-5 M67,29 l3,-3", "none", "#5a3a22", 1.2) + cond(70, 84) + ins(84, 112) + mark(60, 32, 12, 10),
  }),
  size: () => ({
    ok: ins(4, 112, 34, 12) + tube(38, 82, 34, 15),
    ng: ins(4, 112, 34, 12) + tube(38, 82, 34, 28) + vdim(88, 20, 48, "", RED) + text(58, 62, "диаметр не по КД", RED),
  }),
  dirt: () => ({
    ok: ins(4, 112, 34, 16) + text(58, 60, "поверхность чистая", GREEN),
    ng: ins(4, 112, 34, 16) + `<ellipse cx="44" cy="32" rx="8" ry="4" fill="#6b5a3e" opacity=".55"/><ellipse cx="72" cy="37" rx="10" ry="3" fill="#6b5a3e" opacity=".45"/>` + mark(58, 34, 26, 11),
  }),
  loop: () => ({
    ok: ins(4, 112, 30, 7) + ins(4, 112, 38, 7),
    ng: ins(4, 112, 38, 7) + path("M4,30 L44,30 C52,30 50,12 60,12 C70,12 68,30 76,30 L112,30", "none", INS, 7) + mark(60, 20, 14, 12),
  }),
  lugDepth: () => ({
    ok: ins(4, 50, 34, 12) + path("M50,26 L60,26 L60,42 L50,42 z", "#d93025", E, 0.6) + rect(60, 29, 44, 10, TIN, ` stroke="${E}" stroke-width=".7"`) + rect(101, 30.5, 3, 7, CU) + text(82, 58, "жила до торца", GREEN),
    ng: ins(4, 50, 34, 12) + path("M50,26 L60,26 L60,42 L50,42 z", "#d93025", E, 0.6) + rect(60, 29, 44, 10, TIN, ` stroke="${E}" stroke-width=".7"`) + rect(66, 30.5, 22, 7, CU, ` opacity=".35"`) + rect(88, 30.5, 16, 7, "#fff", ` stroke="${RED}" stroke-dasharray="2 2"`) + dim(88, 104, 20, "пусто", RED),
  }),
  rjBlade: () => {
    const plug = housing(8, 26, 100, 28, "#e9f1f5") + [22, 34, 46, 58, 70, 82, 94].map((x) => line(x - 2, 44, x - 2, 54, "#b0b8be", 2)).join("");
    return {
      ok: plug + [22, 34, 46, 58, 70, 82, 94].map((x) => rect(x - 4, 22, 5, 10, "#d4a537", ` stroke="${E}" stroke-width=".5"`)).join("") + line(8, 26, 108, 26, GREEN, 0.6, ' stroke-dasharray="3 2"'),
      ng: plug + [22, 34, 46, 58, 70, 82, 94].map((x, i) => rect(x - 4, i === 3 || i === 4 ? 12 : 22, 5, 10, "#d4a537", ` stroke="${E}" stroke-width=".5"`)).join("") + mark(64, 16, 14, 7),
    };
  },
  ...MARK_SCENES,
  ...MARK_AUTO_SCENES,
  ...COAX_SCENES,
  ...STO_NEW_SCENES,
  ...OWN_SCENES,
  generic: () => ({ ok: "", ng: "" }),
};

// ── выбор рисунка: по названию дефекта (затем по пояснению) с учётом вида операции ───────────────────────
type Rule = [string, RegExp, Ctx[]?];

const MARK_TUBES: Ctx[] = ["markHeat", "markPvc"];
const MARK_AUTO: Ctx[] = ["markAuto"];
const RULES: Rule[] = [
  // Печать и резка маркировочной трубки (INS_MARK_TUBE_AUTO): свои рисунки на каждый дефект карты, «!» не остаётся.
  ["autoText", /^текст не соответств|не соответствует кд/i, MARK_AUTO],
  ["autoBlur", /нечитаем|смазан|бледн/i, MARK_AUTO],
  ["autoShift", /сдвинут|смещ/i, MARK_AUTO],
  ["autoLength", /длина вне допуск|длин/i, MARK_AUTO],
  ["autoCut", /\sрез|рваный|косой|заусен/i, MARK_AUTO],
  ["autoDirt", /загрязн|деформац|вмятин/i, MARK_AUTO],
  ["autoSize", /типоразмер|не тот.*цвет/i, MARK_AUTO],
  ["autoMix", /разных текстов|смешан|в одной таре/i, MARK_AUTO],
  ["flagText", /не соответств/i, ["markFlag"]],
  ["flagBlur", /нечитаем|смазан/i, ["markFlag"]],
  ["flagShift", /позици|смещ/i, ["markFlag"]],
  ["flagLoose", /закреп|болта/i, ["markFlag"]],
  ["flagTear", /надрыв|перегиб|залом/i, ["markFlag"]],
  ["tubeLen", /длин/i, MARK_TUBES],
  ["tubeText", /не соответств/i, MARK_TUBES],
  ["tubeBlur", /нечитаем|смазан/i, MARK_TUBES],
  ["tubeLoose", /не усажен|усажена не полностью|недоусадк/i, ["markHeat"]],
  ["tubeSlide", /сполза/i, MARK_TUBES],
  ["tubeHeat", /перегрев|прож[оё]г/i, ["markHeat"]],
  ["tubeShift", /позици|смещ/i, MARK_TUBES],
  ["tagText", /не соответств/i, ["markTag"]],
  ["tagBlur", /нечитаем|смазан/i, ["markTag"]],
  ["tagShift", /позици|смещ/i, ["markTag"]],
  ["tagLoose", /закреп|болта/i, ["markTag"]],
  ["ringOrder", /порядок|не соответств/i, ["markRing"]],
  ["ringMissing", /пропущ|нет кольц/i, ["markRing"]],
  ["ringShift", /позици|смещ/i, ["markRing"]],
  ["ringSlide", /сполза/i, ["markRing"]],
  ["printText", /не соответств/i, ["markPrint"]],
  ["printBlur", /нечитаем|стира/i, ["markPrint"]],
  ["insDamage", /поврежд/i, ["markPrint"]],
  // Коакс: резка и разделка — на коаксиальном кабеле, не на проводе.
  ["cxBraidCut", /пряд|проволок[а-яё]* экрана/i, ["coax"]],
  ["cxBraidResidue", /остатки экрана|волоск[а-яё]* оплет/i, ["coax"]],
  ["braidHairs", /(срез|распушение) оплет|бахром/i, ["coax"]],
  ["cxCoreNick", /обрыв|надрез[а-яё]* (жил|центр)/i, ["coax"]],
  ["cxCoreSplay", /скручивание или распушение|деформаци[а-яё]* повива/i, ["coax"]],
  ["cxDielCut", /порезы, надрывы, трещины|трещин[а-яё]* диэлектр/i, ["coax"]],
  ["cxDielDeform", /смятие или вытягивание|диаметр[а-яё]* диэлектр/i, ["coax"]],
  ["cxJacketCrush", /смят|сплющ|вмятин|прокус/i, ["coax"]],
  ["cxSkew", /косой/i, ["coax"]],
  ["cxFaceRagged", /задир|рван/i, ["coax"]],
  ["cxPulled", /вытягив/i, ["coax"]],
  ["cxFaceSplay", /деформация скрутки|раскрутк|распуш/i, ["coax"]],
  ["cxBurr", /заусен/i, ["coax"]],
  ["cxLength", /длина вне допуска|длин[а-яё]* отрезк/i, ["coax"]],
  ["cxJacketNick", /надрез|царап|потерт|порез/i, ["coax"]],
  // Резка ТУТ — на трубке.
  ["tubeSlit", /продольн|микротрещ/i, ["tube"]],
  ["tubeRagged", /рван|заусен|бахром/i, ["tube"]],
  ["tubeSkew", /косой/i, ["tube"]],
  ["tubeLength", /длин/i, ["tube"]],
  // Надеть / усадить ТУТ.
  ["tubeOrder", /порядок трубок|неверный порядок/i, ["shrink"]],
  ["shrinkJacketMelt", /оболочк[а-яё]*.*(расплав|оплав)|(расплав|оплав|термическ)[а-яё]*.*оболочк/i, ["shrink"]],
  // Корпус коаксиального разъёма и втулка экрана.
  ["cxBraidStrands", /пряди оплётки|оплётк[а-яё]* на (изолятор|контакт)/i, ["coaxAsm"]],
  ["cxClampLoose", /оплётка не зажата|выдёргив/i, ["coaxAsm"]],
  ["cxNutOver", /гайка перетянут|перетянут[а-яё]* гайк/i, ["coaxAsm"]],
  ["cxGasketMissing", /пропущен[а-яё]* (уплотнител|шайб)|нет уплотнител/i, ["coaxAsm"]],
  ["cxSeat", /недосад|не встал|неполн[а-яё]* посадк|не дошёл до упора|не дошел до упора/i, ["coaxAsm"]],
  ["cxBent", /изгиб пина|деформ[а-яё]* .*пин/i, ["coaxAsm"]],
  ["cxSleeveHairs", /волоск|торчащ/i, ["coaxCrimp"]],
  ["cxHexLoose", /недожим|проворач/i, ["coaxCrimp"]],
  ["cxHexFlash", /пережим|облой|«уш/i, ["coaxCrimp"]],
  ["cxBodyDent", /деформац[а-яё]* .*корпус/i, ["coaxCrimp"]],
  ["tnWick", /затекан|wicking|капилляр|зазор/i, ["tin"]],
  ["tnExcess", /избыт|наплыв|сосульк|капл/i, ["tin"]],
  ["tnLack", /недостат|непропай|открыт[а-яё]* мед|несмачив|раковин|пор[ыа]/i, ["tin"]],
  ["tnCold", /холодн|матов|зернист/i, ["tin"]],
  ["tnMelt", /оплав|обугл|перегрев|изоляци/i, ["tin"]],
  ["tnSplay", /распуш|ёрш|ерш|расплет|проволок/i, ["tin"]],
  ["cxCold", /холодн|непрогрев/i, ["coaxSolder"]],
  ["cxLack", /недостат|недолив|менее чем/i, ["coaxSolder"]],
  ["cxExcess", /избыт|наплыв|сосульк/i, ["coaxSolder"]],
  ["cxShift", /фиксац|смещени/i, ["coaxSolder"]],
  ["cxGap", /зазор/i, ["coaxSolder"]],
  ["cxMelt", /оплав|вспуч|перегрев/i, ["coaxSolder"]],
  ["cxBent", /деформ|погнут|сплющ/i, ["coaxSolder"]],
  ["cxStrays", /проволоч|торчат|вылет/i, ["coaxSolder"]],
  ["pull", /pull|отрыв|вырыв/i],
  ["test", /прозвон/i],
  ["time", /окна времени|до высыхани|время выдерж/i],
  ["dirt", /загрязн|обезжир/i],
  ["glue", /клеев[а-яё]* валик/i],
  ["seal", /герметик/i],
  ["missing", /пропуск (элемент|детал|операц|внешн|зоны|места)|отсутстви[а-яё]* .*(компонент|резистор)|отсутствие \/ повреждение компонент/i],
  ["markText", /нечитаем|маркировк[а-яё]* не соответ|неверн[а-яё]* (код )?маркир|пропуск\/неверн/i],
  ["markShift", /маркер/i],
  ["markShift", /смещ/i, ["mark"]],
  ["pinout", /распиновк|перепут|порядок (жил|трубок)|ориентац|перекрещиван|ключ\b/i],
  ["coaxPin", /центральн[а-яё]* контакт/i],
  ["mold", /недолив|раковин|пустот/i, ["mold"]],
  ["solderLack", /недолив|недостат[а-яё]* (заполн|колич)|непропай|раковин/i],
  ["bridge", /перемычк|замыкан|bridg/i],
  ["icicle", /сосульк|пик[иа]? припоя|острые выступ/i],
  ["solderExcess", /избыт[а-яё]* (припо|колич)|наплыв/i],
  ["solderCold", /холодн|матов|зернист|шевелен/i],
  ["wicking", /затекан|wicking|фитиль|стёкш|стекш/i],
  ["weldLow", /недовар/i],
  ["weldHigh", /перевар|пережог/i],
  ["pad", /площад/i],
  ["fray", /торцы оплётки|торцы оплетки|разлохмач|распускание|расплетание концов/i],
  ["braidHairs", /волоск|бахром|распушение (жил )?(экрана|оплет|оплёт)/i],
  ["shrinkBurn", /прож[оё]г|обуглив[а-яё]* клея|проколы трубки|трещины трубки|надрывы и проколы/i, ["shrink"]],
  ["shrinkBurn", /прож[оё]г (ТУТ|трубк)/i],
  ["shrinkLoose", /недоусадк|преждевременн[а-яё]* усадк/i],
  ["shrinkShift", /смещение (ТУТ|трубки)|открытая жила .* не закрыт/i],
  ["tubeFlat", /сплющивание \(слипание/i],
  ["size", /типоразмер|несоответствующего артикула/i],
  ["lugRing", /кольц[а-яё]* наконечник/i],
  ["crimpIns", /изоляци[а-яё]* (в зоне обж|провода в металл|зашла)/i],
  ["crimpFlash", /облой|«уши|ушей|перепрессов|разрушение втулк/i, ["crimp", "lug"]],
  ["lugDepth", /жила не до упора|утоплена|недозаход|неполный заход/i, ["lug"]],
  ["ferrule", /сполза|недопрессовк/i],
  ["strayCrimp", /выход жил из зоны обжим/i],
  ["bellmouth", /юбк|раструб/i],
  ["insGrip", /(отсутстви|слаб)[а-яё]* (зажим|обжим)[а-яё]* изоляци/i],
  ["insBite", /пережим[а-яё]* изоляции лапками|чрезмерн[а-яё]* пережим изоляци/i, ["crimp", "lug"]],
  ["rjBlade", /ножев/i],
  ["crimpHeight", /высот[а-яё]* опрессов|усили|недообжим|недожим|слаб[а-яё]* (обжим|зажим)|неплотн|неполн[а-яё]*[^а-яё].*обжим|число.* обжати/i],
  ["banana", /банан/i],
  ["lance", /усик/i],
  ["endCap", /колпач[а-яё]* не до упора|свободный конец|торец жилы открыт|не уложен в запас/i],
  ["rjDepth", /не дошли до торца|жила не до упора|утоплена|недозаход|неполный заход/i],
  ["housingGap", /недосад|крышк[а-яё]* не до упора|неполная посадка крышк|не завёрнут|не завернут|перекос|зазор по периметр|винты .*не затянут|гайка.* не затянут/i],
  ["seat", /недосыл|неполн[а-яё]* посадк|не на (проектной )?глубин|при неполной посадке|не на месте/i],
  ["latch", /замк|защ[её]л|фиксатор[а-яё]* не дожат|стопор|неполная фиксац/i],
  ["termBent", /деформ[а-яё]* (рабочей|контакт|центральн)|изгиб пина|ламел/i],
  ["brush", /вылет/i, ["crimp", "lug", "weld"]],
  ["gapIns", /вылет|зазор/i, ["solder"]],
  ["gapIns", /недопустимый зазор|вылет голой|оголенн/i],
  ["brush", /вылет жил/i],
  ["connMelt", /оплавлен[а-яё]* (корпус|изолятор|диэлектр|пластик|внутренн)|(корпус|изолятор)\S* оплавлен|сердечник|бакелит|тефлон|оплавл.*разъ[её]м/i],
  ["insMelt", /оплав|прожог|обугл|перегрев|термическ|побелен|вспучив/i],
  ["tieTight", /пережат[а-яё]* изоляции стяжк|в местах фиксац/i],
  ["tieLoose", /стяжк|редкая фиксац|нет\/редкая/i],
  ["tieTail", /острые кромки хвост/i],
  ["pinch", /защемлен|перелом проводов|кабель пережат|выходит из разреза|разрез расходится/i],
  ["clamp", /не заведена под фиксатор|кабельный зажим/i],
  ["insDamage", /порезы, надрывы/i],
  ["insRagged", /микротрещ/i],
  ["housingCrack", /трещин|растрескив|повреждение пластиков|крышка деформир|смятие (корпуса|миниатюрного)|деформация (корпуса|самого корпуса)|разрушение манжет|прорез[а-яё]* .*манжет|поломка|повреждение колпачка/i],
  ["idc", /не прорезан|вне паза/i],
  ["mold", /облой|литник|коробл|отливк|смещение арматуры|пузыр/i],
  ["corrCut", /гребн/i],
  ["tape", /просвет|нахл[её]ст|края ленты/i],
  ["cover", /не (закрывает|покрывает) участок|гофра смещена|короче КД/i],
  ["fray", /не зафиксирован|не закреплён|отходят/i, ["assembly", "tube", "other"]],
  ["twist", /перекрут|складк|пережим\/деформация рукава|неравномерная плотность/i],
  ["loop", /петля|из повива/i],
  ["shieldSpread", /заворачив|распределение экрана|экранные лепестки|экран не подключ/i],
  ["drain", /дренаж/i],
  ["foil", /фольг|остатки экрана|удаление\/формирование экрана|неполное удаление общего экрана/i],
  ["pairs", /раскрутк|расплетени|шаг[а-яё]* скрутк|D±|витых пар/i, ["strip", "assembly", "solder"]],
  ["pairs", /пар[аыу]?\b|витых|шаг[а-яё]* скрутк|D±/i],
  ["coaxSteps", /ступен|длин[а-яё]* (зачистки|снятия)|снятия/i, ["coax"]],
  ["stripLen", /длин[а-яё]* (зачистки|снятия)|длины открытых концов|сложена не по размеру/i],
  ["skew", /косой/i],
  ["length", /длин|шаг вне допуска|неверный шаг|размер/i],
  ["unlay", /раскрутк|деформация скрутки/i],
  ["birdcage", /распуш|ёрш|ерш|расплет|скручивание или распушение/i],
  ["unlay", /^вытягивание жил/i, ["strip", "crimp"]],
  ["pulled", /вытягив/i],
  ["nick", /надрез[а-яё]* (жил|пров)|подрез|перерез|обрыв[а-яё]* (жил|пров)|повреждени[а-яё]* прядей|царапины или обрыв|обрыв или надрез/i],
  ["insResidue", /остатки изоляц|неполное снятие изоляц/i],
  ["insRagged", /рван|задир|надрыв[а-яё]* на торце/i],
  ["burrWire", /заусен/i, ["cut"]],
  ["burr", /заусен/i],
  ["insCrush", /смят|сплющ|прокус|пережим|деформ/i],
  ["insDamage", /изоляц|оболочк|надрез|царап|потерт|порез|надрыв|повреждени|оплётк|оплетк/i],
];

/** Вид операции по коду карты (CUT_/STRIP_/PRS_/SDR_/HEAT_/INS_/MOLD_…). */
export function defectContext(code: string): Ctx {
  const group = /^MARK:(\w+)$/.exec(String(code || ""));
  if (group && (Object.values(MARK_GROUPS) as string[]).includes(group[1])) return group[1] as MarkCtx;
  const c = String(code || "").toUpperCase();
  if (/^PRS_SLEEVE/.test(c) && /COAX/.test(c)) return "coaxCrimp";
  if (/^PRS_(НШВИ|HKИ|НКИ|ТМЛ|SLEEVE|CAP|COPPER)/.test(c) || /НШВИ|ТМЛ|НВИ/.test(c)) return "lug";
  if (/COAX/.test(c) && /^(STRIP|CUT)/.test(c)) return "coax";
  if (/COAX/.test(c) && /^INS/.test(c)) return "coaxAsm";
  if (/^CUT_(TUT|ГОФРА|ПМЛ|NEYLON)/.test(c)) return "tube";
  if (/^CUT/.test(c)) return /\+\d?(STRIP|CRIMP)/.test(c) ? (/CRIMP/.test(c) ? "crimp" : "strip") : "cut";
  if (/^STRIP|^PREP/.test(c)) return "strip";
  if (/^PRS/.test(c)) return "crimp";
  if (/^SDR_COAX_PIN$/.test(c)) return "coaxSolder";
  if (/^SDR_TIN$/.test(c)) return "tin";
  if (/^SDR/.test(c)) return "solder";
  if (/^HEAT|^INS_TUT|_TUT/.test(c)) return "shrink";
  if (/^INS_MARK_TUBE_AUTO/.test(c)) return "markAuto";
  if (/^INS_MARK|MARK$/.test(c)) return "mark";
  if (/^(MOLD|PLASM)/.test(c)) return "mold";
  if (/^US_WELD/.test(c)) return "weld";
  if (/^(INS|SEAL|TWIST)/.test(c)) return "assembly";
  return "other";
}

/** Код карты для рисунков дефектов с учётом вида операции генератора: резка и разделка вкладки «Коакс» идут по общим
 *  картам провода (CUT_WIRE_auto), а режут коаксиальный кабель — рисунки коакса, не провода (владелец 15.10). */
export function defectOperationCode(code: string, type: unknown = ""): string {
  return /^coax(Cut|Strip)$/.test(String(type || "")) && !/COAX/i.test(code) ? `${code}+COAX` : code;
}

/** Код для рисунков группы дефектов комбинированной операции: группа «Лужение — …» из карты SDR_tin
 *  (CUT_WIRE+1crimp+tin) рисуется как лужение, остальные — по коду самой операции. */
export function defectGroupCode(code: string, groupTitle: string): string {
  if (/^Лужение\s+—/i.test(groupTitle || "")) return "SDR_tin";
  // Маркировка по КД: группа вида метки («Хлястик», «Термоусадочная трубка»…) рисуется своими рисунками.
  const kind = MARK_GROUPS[String(groupTitle || "").trim()];
  return kind && defectContext(code) === "mark" ? `MARK:${kind}` : code;
}

/** Какой рисунок библиотеки подходит дефекту; «generic» — подходящего нет. */
export function defectSceneId(name: string, note = "", code = ""): string {
  // Пункт СТО («4.3.14 Высота обжима…»): рисунок по коду строки СТО, а не по словам названия.
  const sto = STO_SCENES[/^(\d+\.\d+(?:\.\d+)+)\s/.exec(name)?.[1] ?? ""];
  if (sto && sto in SCENES) return sto;
  const ctx = defectContext(code);
  for (const source of [name, note]) {
    if (!source) continue;
    const found = RULES.find(([, pattern, only]) => (!only || only.includes(ctx)) && pattern.test(source));
    if (found) return found[0];
  }
  return "generic";
}

export const DEFECT_SCENES = Object.keys(SCENES).filter((id) => id !== "generic");

/** Пара «Норма | Брак» 240×86 для дефекта; без подходящего рисунка — знак внимания. */
export function defectArt(name: string, note = "", code = ""): string {
  const id = defectSceneId(name, note, code);
  if (id === "generic") {
    return `<svg viewBox="0 0 116 86" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" data-scene="generic">` +
      `<path d="M58 14 L92 70 L24 70 Z" fill="#fdecea" stroke="${RED}" stroke-width="2"/>` + text(58, 63, "!", RED, 28, "middle", 700) + `</svg>`;
  }
  return pair(SCENES[id](), id);
}

/** Рисунок библиотеки по его id (для проверки и витрины библиотеки). */
export function defectSceneArt(id: string): string {
  return id in SCENES && id !== "generic" ? pair(SCENES[id](), id) : defectArt("");
}

function pair(scene: Scene, id: string): string {
  const panel = (x: number, ok: boolean, body: string) =>
    rect(x + 0.5, 0.5, 115, 71, ok ? "#f4faf6" : "#fdf4f3", ` rx="6" stroke="${ok ? "#cfe7d7" : "#f3c7c3"}" stroke-width="1"`) +
    `<svg x="${x + 1}" y="1" width="114" height="70" style="width:114px;height:70px" viewBox="${scene.view || "0 0 116 70"}">${body}</svg>` +
    text(x + 58, 83, ok ? "✓ Норма" : "✗ Брак", ok ? GREEN : RED, 9.5, "middle", 700);
  return `<svg viewBox="0 0 240 86" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" data-scene="${id}">` +
    panel(0, true, scene.ok) + panel(124, false, scene.ng) + `</svg>`;
}
