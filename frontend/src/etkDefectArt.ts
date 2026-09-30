// Библиотека рисунков типовых дефектов ЭТК: на каждый дефект пара «Норма | Брак» в стиле эскиза операции
// (провод, жила, терминал, стакан, трубка — плоские технические рисунки, место брака обведено красным).
// Одна на все ТК: рисунок выбирается при показе по названию дефекта и коду операции, поэтому выданные ТК
// перевыпускать не нужно. Названия дефектов берутся из шаблонов SUPPLYMAP (БД.ТК «ТИПОВЫЕ ДЕФЕКТЫ»).

const E = "#17272b";
const INS = "#7f919b", INS_LO = "#5d6e78";
const CU = "#c87533", CU_HI = "#eba66e", CU_LO = "#8a4a1c";
const TIN = "#d4d9dd", TIN_HI = "#f4f6f7", TIN_LO = "#98a2a9";
const SOLDER = "#c9ced3";
const PLAST = "#efe6cf", PLAST_E = "#8a7d5c";
const TUBE = "#3b4147";
const RED = "#d93025", GREEN = "#188038", MUTED = "#5f7470";

type Ctx = "cut" | "tube" | "strip" | "coax" | "crimp" | "lug" | "solder" | "shrink" | "mark" | "assembly" | "mold" | "weld" | "other";

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
  let strands = "";
  const r = mode === "over" ? 2.2 : 2.5;
  const pts: Array<[number, number]> = mode === "loose"
    ? [[-12, 3], [-6, 7], [6, 7], [12, 2], [0, 9], [-13, -3], [9, -2]]
    : mode === "over" ? [[-18, 3], [-13, 5], [-8, 5], [-3, 5], [2, 5], [7, 5], [12, 5], [17, 3], [-15, 0], [-10, 1], [-5, 1], [5, 1], [10, 1], [15, 0]]
      : [[-13, 1], [-8, 0], [-11, 5], [-6, 5], [-1, 6], [4, 6], [9, 5], [14, 1], [8, 0], [11, 5], [-3, 2], [3, 2], [-9, 9], [-4, 10], [1, 10], [6, 10]];
  for (const [dx, dy] of pts) strands += circle(cx + dx, cy + dy, r, CU, CU_LO, 0.5);
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

// ── сцены: каждая — рисунок «норма» и «брак» в поле 116×70 ─────────────────────────────────────────────
/** view — окно рисунка (viewBox) для крупного плана, по умолчанию всё поле 0 0 116 70. */
type Scene = { ok: string; ng: string; view?: string };
const CLOSE_UP = "6 14 80 50";
const W = { ins: (x2 = 58) => ins(4, x2), bare: (x1 = 58, x2 = 100) => cond(x1, x2) };

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
  crimpIns: () => ({
    ok: ins(2, 34) + cond(34, 78, 34, 6) + terminal(14),
    ng: ins(2, 52) + cond(52, 78, 34, 6) + terminal(14) + rect(42, 30.5, 10, 7, INS, ` opacity=".85"`) + mark(46, 34, 9, 10),
    view: CLOSE_UP,
  }),
  brush: () => ({
    ok: ins(2, 34) + cond(34, 66, 34, 6) + terminal(14) + rect(62, 31, 4, 6, CU) + dim(62, 66, 50, "", GREEN) + mark(64, 34, 5, 6, GREEN),
    ng: ins(2, 34) + cond(34, 78, 34, 6) + terminal(14) + rect(62, 31, 16, 6, CU) + mark(71, 34, 9, 9),
    view: CLOSE_UP,
  }),
  strayCrimp: () => ({
    ok: ins(2, 34) + cond(34, 66, 34, 6) + terminal(14),
    ng: ins(2, 34) + cond(34, 66, 34, 6) + terminal(14) + path("M40,28 C44,18 50,16 56,14 M42,28 C46,22 50,20 54,20", "none", CU, 1.2) + mark(48, 20, 10, 8),
    view: CLOSE_UP,
  }),
  crimpHeight: () => ({
    ok: crimpSection(50, 34, "ok") + vdim(86, 21, 47, "H по КД", GREEN),
    ng: crimpSection(50, 34, "loose") + vdim(86, 17, 47, "H > КД", RED),
  }),
  crimpFlash: () => ({
    ok: crimpSection(58, 34, "ok"),
    ng: crimpSection(58, 34, "over") + text(58, 60, "облой, «уши»", RED),
  }),
  bellmouth: () => ({
    ok: ins(2, 34) + cond(34, 66, 34, 6) + terminal(14) + path("M42,24 L40,21 M42,44 L40,47", "none", E, 0.9) + mark(41, 34, 5, 14, GREEN),
    ng: ins(2, 34) + cond(34, 66, 34, 6) + terminal(14) + line(42, 24, 42, 44, RED, 1.6) + mark(42, 34, 5, 14),
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
    ok: ins(0, 36) + cond(36, 72, 34, 6) + terminal(22) + arrow(18, 50, 2, 50, GREEN) + text(58, 62, "усилие отрыва по норме", GREEN),
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
    ng: ins(4, 50) + tinned(30, 66, 34, 6) + rect(30, 28, 20, 12, "#fff", ` opacity=".35"`) + path("M30,27 L50,26 L50,42 L30,41", "none", RED, 0.9, ' stroke-dasharray="2 2"') + cup(62) + mark(40, 34, 14, 10) + text(40, 58, "припой под изоляцией", RED),
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
  generic: () => ({ ok: "", ng: "" }),
};

// ── выбор рисунка: по названию дефекта (затем по пояснению) с учётом вида операции ───────────────────────
type Rule = [string, RegExp, Ctx[]?];

const RULES: Rule[] = [
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
  const c = String(code || "").toUpperCase();
  if (/^PRS_(НШВИ|HKИ|НКИ|ТМЛ|SLEEVE|CAP|COPPER)/.test(c) || /НШВИ|ТМЛ|НВИ/.test(c)) return "lug";
  if (/COAX/.test(c) && /^(STRIP|CUT)/.test(c)) return "coax";
  if (/^CUT_(TUT|ГОФРА|ПМЛ|NEYLON)/.test(c)) return "tube";
  if (/^CUT/.test(c)) return /\+\d?(STRIP|CRIMP)/.test(c) ? (/CRIMP/.test(c) ? "crimp" : "strip") : "cut";
  if (/^STRIP|^PREP/.test(c)) return "strip";
  if (/^PRS/.test(c)) return "crimp";
  if (/^SDR/.test(c)) return "solder";
  if (/^HEAT|^INS_TUT|_TUT/.test(c)) return "shrink";
  if (/^INS_MARK|MARK$/.test(c)) return "mark";
  if (/^(MOLD|PLASM)/.test(c)) return "mold";
  if (/^US_WELD/.test(c)) return "weld";
  if (/^(INS|SEAL|TWIST)/.test(c)) return "assembly";
  return "other";
}

/** Какой рисунок библиотеки подходит дефекту; «generic» — подходящего нет. */
export function defectSceneId(name: string, note = "", code = ""): string {
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

function pair(scene: Scene, id: string): string {
  const panel = (x: number, ok: boolean, body: string) =>
    rect(x + 0.5, 0.5, 115, 71, ok ? "#f4faf6" : "#fdf4f3", ` rx="6" stroke="${ok ? "#cfe7d7" : "#f3c7c3"}" stroke-width="1"`) +
    `<svg x="${x + 1}" y="1" width="114" height="70" style="width:114px;height:70px" viewBox="${scene.view || "0 0 116 70"}">${body}</svg>` +
    text(x + 58, 83, ok ? "✓ Норма" : "✗ Брак", ok ? GREEN : RED, 9.5, "middle", 700);
  return `<svg viewBox="0 0 240 86" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" data-scene="${id}">` +
    panel(0, true, scene.ok) + panel(124, false, scene.ng) + `</svg>`;
}
