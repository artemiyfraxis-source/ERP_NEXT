// Эскизы электронной ТК строкой SVG — порт SUPPLYMAP PortalApp.html etkDraw* / etkHousing_ / etkChips (renderEtk_).
// Одни и те же строки идут и на экран (блок «Эскиз»), и в печать (etkPrint.ts): печатное окно — отдельный
// документ без React, поэтому рисунок — готовая разметка. Геометрия и подписи — как в SUPPLYMAP; разъёмы — рисунками
// серий из connectorArt.ts (владелец 30.09: одна общая иконка на все разъёмы непонятна).
import { connectorFace, connectorFaceUnits, connectorPicture, connectorSummary, wireColor, type HousingBody } from "./connectorArt";
import { connectorTopView } from "./connectorTopView";
import { wireEndArt } from "./etkDefectArt";
import { solderMode } from "./etkOperation";

type Row = Record<string, unknown>;

export type EtkHousing = {
  art?: string; title?: string; pitch?: number | null; rows: number; cols: number; n?: number;
  kind?: string; color: string; latch?: string; cavity?: string; labels?: string[] | null;
  grid: number[][]; round?: Array<{ pin: number; x: number; y: number }> | null; conf?: string; note?: string;
  family?: string; male?: boolean | null; body?: HousingBody | null;
};

export type EtkTopologyData = {
  nodes?: Array<{ id: string; conn: string; side?: string; depth?: number; kind?: string; housing?: EtkHousing }>;
  edges?: Array<{ from: string; to: string; wires: string[]; count: number; length: number }>;
};

function esc(value: unknown): string {
  return String(value == null ? "" : value)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function obj(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

function list(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") as Row[] : [];
}

function str(value: unknown): string {
  return value == null ? "" : String(value);
}

/** SUPPLYMAP etkWireColor_: цвет жилы по слову в наименовании, иначе серо-синий. */
export function etkWireColor(name: unknown): string {
  return wireColor(name);
}

export function etkUnwrap(value: unknown): string {
  return str(value).replace(/\n(?=[a-zа-яё(])/g, " ");
}

function num(value: unknown): number {
  const n = parseFloat(str(value).replace(",", "."));
  return Number.isFinite(n) ? n : 0;
}

function mm(value: unknown): string {
  const s = str(value).trim();
  return s ? (/[a-zа-я]/i.test(s) ? s : `${s} мм`) : "";
}

function dim(x1: number, x2: number, y: number, label: string): string {
  return `<line x1="${x1}" y1="${y}" x2="${x2}" y2="${y}" stroke="#17272b" stroke-width="1" marker-start="url(#etkA)" marker-end="url(#etkA)"/>` +
    `<text x="${(x1 + x2) / 2}" y="${y - 6}" text-anchor="middle" font-size="12" fill="#17272b">${esc(label)}</text>`;
}

function svg(w: number, h: number, body: string): string {
  return `<svg class="etk-svg" viewBox="0 0 ${w} ${h}" width="100%" preserveAspectRatio="xMinYMin meet" xmlns="http://www.w3.org/2000/svg">` +
    `<defs><marker id="etkA" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">` +
    `<path d="M0,1 L9,5 L0,9 z" fill="#17272b"/></marker>` +
    `<pattern id="etkBraid" width="6" height="6" patternUnits="userSpaceOnUse"><path d="M0,0 L6,6 M6,0 L0,6" stroke="#8d9297" stroke-width="1"/></pattern></defs>` +
    body + "</svg>";
}

const LIGHT_TEXT = /#(222|2463c7|7a4a24|7b4bb3|d7322d|2e9e4f)/;

/** Допуск «(+/-)1мм», «±5» → «±1 мм», «±5 мм»; другой текст технолога — как есть. */
export function etkTolerance(value: unknown): string {
  const s = str(value).trim();
  const m = s.match(/^(?:\(\+\/-\)|±|\+\/-)\s*(\d+(?:[.,]\d+)?)\s*(?:мм)?$/i);
  return m ? `±${m[1].replace(".", ",")} мм` : s;
}

export type CutEndKind = "none" | "strip" | "tin" | "crimp";
/** `core` — зачистка жил кабеля в оболочке (ротор снимает оболочку `len` и зачищает жилы), "" — не кабель в оболочке. */
export type CutEnd = { kind: CutEndKind; len: string; terminal: string; core?: string };

/** Концы провода в операции резки: без зачистки, зачищен (медь), облужен или с обжатым терминалом. Считается при показе
 *  по типу, стороне и коду операции (CUT_WIRE+1crimp — обжим стороны операции, +2crimp — обеих, +tin — лудится свободный
 *  конец, +2tin — оба), поэтому выданные ЭТК перевыпускать не нужно. Терминал — строка «Шт.» комплектующих. */
export function etkCutEnds(operation: Row): { a: CutEnd; b: CutEnd } {
  const st = obj(obj(operation.fields).strip), code = str(operation.code);
  const crimpCode = code.match(/\+(\d)?crimp/i), tinCode = code.match(/\+(\d)?tin\b/i);
  const crimps = crimpCode ? Number(crimpCode[1] || 1) : operation.type === "cutWireCrimp" ? 1 : 0;
  const tins = tinCode ? Number(tinCode[1] || 1) : 0;
  const crimpSide = operation.side === "B" ? "b" : "a";
  const freeSide = crimps === 1 ? (crimpSide === "a" ? "b" : "a") : "b";
  const terminals = list(operation.components).filter((row) => /^шт/i.test(str(row.unit))).map((row) => str(row.art || row.name)).filter(Boolean);
  const kind = (key: "a" | "b"): CutEndKind => {
    if (crimps >= 2 || (crimps === 1 && key === crimpSide)) return "crimp";
    if (tins >= 2 || (tins === 1 && key === freeSide)) return "tin";
    return st[key] ? "strip" : "none";
  };
  const end = (key: "a" | "b"): CutEnd => {
    const k = kind(key);
    const coreKey = key === "a" ? "coreA" : "coreB";
    return { kind: k, len: str(key === "a" ? st.lenA : st.lenB).trim(),
      terminal: k === "crimp" ? terminals[key === "b" && terminals.length > 1 ? 1 : 0] || "" : "",
      ...(k === "strip" && coreKey in st ? { core: str(st[coreKey]).trim() } : {}) };
  };
  return { a: end("a"), b: end("b") };
}

/** Зачистка конца: «A 1,2 мм», длины нет — «?»; с пометкой — лужёный «B 3 мм (лужение)» (в эскизе это подпись под проводом). */
function endStrip(key: "A" | "B", end: CutEnd, note = false): string {
  const core = end.core !== undefined ? `, жилы ${mm(end.core) || "?"}` : "";
  return `${key} ${mm(end.len) || "?"}${core}${note && end.kind === "tin" ? " (лужение)" : ""}`;
}

/** Провода резки: полоса на провод, концы — как их отдаёт операция: зачищенная медь, лужёный конец, обжатый терминал
 *  (рисунки библиотеки дефектов), длина реза от торца до торца жилы с допуском (etkDrawWires_). */
export function etkDrawWires(operation: Row): string {
  const f = obj(operation.fields), ws = list(f.wires).slice(0, 8);
  if (!ws.length || !/^cutWire/.test(str(operation.type))) return "";
  const ends = etkCutEnds(operation);
  const crimped = ends.a.kind === "crimp" || ends.b.kind === "crimp";
  const W = 640, x0 = 40, x1 = W - 40, rowH = crimped ? 30 : 26, top = 34, S = 1, END = 26;
  // Геометрия конца: где кончается изоляция, торец жилы и зона зачистки (под размер «A …» / «B …»).
  const geo = (key: "a" | "b") => {
    const e = ends[key], dir = key === "a" ? -1 : 1, edge = key === "a" ? x0 : x1;
    if (e.kind === "crimp") {
      const attach = edge - dir * 92 * S, at = (local: number) => attach + dir * local * S;
      return { ins: at(26), tip: at(52), zone: [at(26), at(52)], attach, dir }; // изоляция выходит из крыльев в зазор
    }
    const inner = edge - dir * END;
    return e.kind === "none" ? { ins: edge, tip: edge, zone: null, attach: 0, dir } : { ins: inner, tip: edge, zone: [inner, edge], attach: 0, dir };
  };
  const ga = geo("a"), gb = geo("b");
  const endArt = (key: "a" | "b", cy: number) => {
    const e = ends[key], g = key === "a" ? ga : gb, edge = key === "a" ? x0 : x1;
    if (e.kind === "crimp") return `<g data-end="${key.toUpperCase()}:crimp" transform="translate(${g.attach.toFixed(1)},${cy}) scale(${g.dir * S},${S})">${wireEndArt.crimp(0)}</g>`;
    if (e.kind === "strip") return `<g data-end="${key.toUpperCase()}:strip">${wireEndArt.copper(Math.min(edge, g.ins), Math.max(edge, g.ins), cy)}</g>`;
    if (e.kind === "tin") {
      const gap = g.ins + g.dir * 4; // у торца изоляции жила без припоя, как в рисунке лужения
      return `<g data-end="${key.toUpperCase()}:tin">${wireEndArt.copper(Math.min(g.ins, gap), Math.max(g.ins, gap), cy)}` +
        `${wireEndArt.tin(Math.min(gap, edge), Math.max(gap, edge), cy)}</g>`;
    }
    return "";
  };
  let body = "";
  ws.forEach((w, i) => {
    const y = top + i * rowH, cy = y + rowH / 2 - 3, c = etkWireColor(w.name || w.art);
    body += `<rect x="${ga.ins.toFixed(1)}" y="${cy - 6}" width="${(gb.ins - ga.ins).toFixed(1)}" height="12" rx="3" fill="${c}" stroke="#17272b" stroke-width=".8"/>` +
      endArt("a", cy) + endArt("b", cy) +
      `<text x="${((ga.ins + gb.ins) / 2).toFixed(1)}" y="${cy + 3.5}" text-anchor="middle" font-size="10.5" fill="${LIGHT_TEXT.test(c) ? "#fff" : "#17272b"}">` +
      `${esc(`${str(w.art || w.name)} × ${str(w.qty)}`)}</text>`;
  });
  // Что с концом сделано — подписью под проводами: обжатый терминал и лужение.
  const caption = (end: CutEnd) => end.kind === "crimp" ? `обжат терминал${end.terminal ? ` ${end.terminal}` : ""}` : end.kind === "tin" ? "лужение" : "";
  const notes = [caption(ends.a), caption(ends.b)];
  let yd = top + ws.length * rowH + 16;
  if (notes.some(Boolean)) {
    const yc = top + ws.length * rowH + 6;
    if (notes[0]) body += `<text x="${x0}" y="${yc}" font-size="10.5" fill="#5f7470">${esc(notes[0])}</text>`;
    if (notes[1]) body += `<text x="${x1}" y="${yc}" text-anchor="end" font-size="10.5" fill="#5f7470">${esc(notes[1])}</text>`;
    yd = yc + 22;
  }
  const L = ws[0].length ? `${str(ws[0].length)} мм` : "по КД", tolerance = etkTolerance(f.cutTolerance);
  body += dim(ga.tip, gb.tip, yd, `L = ${L}${tolerance ? ` ${tolerance}` : ""}`);
  if (ga.zone) body += dim(Math.min(ga.zone[0], ga.zone[1]), Math.max(ga.zone[0], ga.zone[1]), top - 10, endStrip("A", ends.a));
  if (gb.zone) body += dim(Math.min(gb.zone[0], gb.zone[1]), Math.max(gb.zone[0], gb.zone[1]), top - 10, endStrip("B", ends.b));
  return svg(W, yd + 14, body);
}

/** Разделка коаксиала: ступени от торца, L1–L3 из базы коакса (etkDrawCoax_). Резка с разделкой на станке разделывает
    оба конца — эскиз каждой стороны со своей подписью (`fields.coaxB`). */
export function etkDrawCoax(operation: Row): string {
  const f = obj(operation.fields);
  const a = obj(f.coax), b = obj(f.coaxB);
  if (!Object.keys(a).length) return "";
  if (!Object.keys(b).length) return coaxSketch(a, "");
  return coaxSketch(a, "Сторона А") + coaxSketch(b, "Сторона В");
}

/** Ступени по тому, какие из L1–L3 заданы (пустая L — «такой ступени нет», не 0 мм): L1+L2+L3 — жила, диэлектрик, оплётка;
    L1+L2 (зажимной/F) — жила и оплётка, отогнутая на оболочку, до L2; L1+L3 (твист-он BNC, top-hat, semi-rigid) — жила и
    диэлектрик до L3, оплётка заподлицо; только L1 — одна жила; только L3 (7/8″ по RFS) — кабель режется заподлицо, снята
    одна оболочка. */
function coaxSketch(c: Row, caption: string): string {
  const L = [num(c.l1), num(c.l2), num(c.l3)];
  const has = L.map((v) => v > 0);
  const present = [0, 1, 2].filter((i) => has[i]);
  const ordered = present.every((i, n) => n === 0 || L[i] > L[present[n - 1]]);
  const flush = !has[0] && !has[1] && has[2];
  if ((!has[0] && !flush) || !ordered) {
    return `<div class="tr-none">Размеров разделки нет в базе коакса для ${esc(c.connector || c.cable)}.</div>`;
  }
  const end = L[present[present.length - 1]];
  const W = 640, x0 = 40, span = 400, k = span / end, cy = 104;
  const X = (v: number) => x0 + v * k;
  const xe = X(end);
  const band = (name: string, from: number, to: number, hh: number, fill: string, stroke: string, sw = ".8") =>
    `<rect data-band="${name}" x="${from}" y="${cy - hh / 2}" width="${to - from}" height="${hh}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
  let body = `<rect data-band="jacket" x="${xe}" y="${cy - 18}" width="${W - 30 - xe}" height="36" rx="4" fill="#222"/>` +
    `<text x="${(xe + W - 30) / 2}" y="${cy + 4}" text-anchor="middle" font-size="12" fill="#fff">${esc(c.cable || "кабель")}</text>`;
  if (flush) {
    body += band("braid", x0, xe, 28, "url(#etkBraid)", "#8d9297", "1") +
      `<text x="${x0}" y="${cy + 30}" font-size="12" fill="#5f7470">Жила, диэлектрик и внешний проводник — заподлицо с торцом</text>`;
  } else if (has[1] && has[2]) {
    body += band("braid", X(L[1]), xe, 28, "url(#etkBraid)", "#8d9297", "1") + band("dielectric", X(L[0]), X(L[1]), 22, "#fbfbf8", "#17272b");
  } else if (has[1]) {
    body += band("braid", X(L[0]), xe, 28, "url(#etkBraid)", "#8d9297", "1");
  } else if (has[2]) {
    body += band("dielectric", X(L[0]), xe, 22, "#fbfbf8", "#17272b");
  }
  if (!flush) body += band("conductor", x0, X(L[0]), 8, "#e08a2c", "#17272b");
  present.forEach((i, n) => {
    const y = cy - 30 - n * 24, x = X(L[i]), top = i === 0 ? 8 : i === 1 && !has[2] ? 28 : i === 1 ? 22 : has[1] ? 28 : 22;
    body += `<line x1="${x}" y1="${y - 4}" x2="${x}" y2="${cy - (i === 2 || present.length === 1 ? 18 : top / 2)}" stroke="#9aa7ad" stroke-dasharray="3 3"/>` +
      dim(x0, x, y, `L${i + 1} = ${mm(c[`l${i + 1}`])}`);
  });
  body += `<line x1="${x0}" y1="${cy - 30 - (present.length - 1) * 24 - 4}" x2="${x0}" y2="${cy + 20}" stroke="#9aa7ad" stroke-dasharray="3 3"/>`;
  const ds = ["d1", "d2", "d3"].filter((key) => c[key]).map((key) => `${key.toUpperCase()} = ${mm(c[key])}`);
  if (ds.length) body += `<text x="${x0}" y="${cy + 42}" font-size="12" fill="#5f7470">Ø: ${esc(ds.join(" · "))}</text>`;
  if (caption) body += `<text x="${W - 30}" y="${cy - 30}" text-anchor="end" font-size="12" font-weight="600" fill="#17272b">${esc(caption)}</text>`;
  return svg(W, cy + 52, body);
}

// ── Коакс по операциям (владелец 15.10, ТК КБ1 796К.2: «эскизов нет»): резка кабеля и ТУТ, пайка контакта, корпус,
// надеть ТУТ / втулки, обжать втулку экрана, усадить ТУТ. Рисуются из данных операции (длина реза, размеры разделки
// L1–L3 стороны, ТУТ и втулки), без картинок технолога; выданным ЭТК данные добавляет чтение (etk_builder.fill_generator_chain).
const CX = { jacket: "#222", diel: "#fbfbf8", cu: "#e08a2c", metal: "#d4d9dd", metalEdge: "#8d969c", tube: "#3b4147", ink: "#17272b", muted: "#5f7470" };

const caption = (x: number, y: number, value: string, anchor = "start", weight = 400, fill = CX.muted) =>
  `<text x="${x}" y="${y}" font-size="12" fill="${fill}" text-anchor="${anchor}" font-weight="${weight}">${esc(value)}</text>`;

/** Кабель в оболочке x1..x2 с подписью марки. */
function coaxJacket(x1: number, x2: number, cy: number, label = "", h = 30): string {
  return `<rect x="${x1}" y="${cy - h / 2}" width="${x2 - x1}" height="${h}" rx="3" fill="${CX.jacket}"/>` +
    `<rect x="${x1 + 3}" y="${cy - h / 2 + 3}" width="${Math.max(0, x2 - x1 - 6)}" height="4" rx="2" fill="#fff" opacity=".18"/>` +
    (label ? `<text x="${(x1 + x2) / 2}" y="${cy + 4}" text-anchor="middle" font-size="12" fill="#fff">${esc(label)}</text>` : "");
}

/** Торец коаксиала: оболочка, оплётка, диэлектрик, жила — вид с торца. */
function coaxFace(cx: number, cy: number, r = 26): string {
  return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${CX.jacket}"/>` +
    `<circle cx="${cx}" cy="${cy}" r="${r * 0.8}" fill="url(#etkBraid)" stroke="${CX.metalEdge}"/>` +
    `<circle cx="${cx}" cy="${cy}" r="${r * 0.64}" fill="${CX.diel}" stroke="${CX.ink}" stroke-width=".8"/>` +
    `<circle cx="${cx}" cy="${cy}" r="${r * 0.16}" fill="${CX.cu}" stroke="${CX.ink}" stroke-width=".6"/>`;
}

/** Трубка (ТУТ) или втулка x1..x2 поверх кабеля. */
function coaxSleeve(x1: number, x2: number, cy: number, h: number, kind: "tube" | "sleeve"): string {
  const fill = kind === "tube" ? CX.tube : CX.metal, edge = kind === "tube" ? CX.ink : CX.metalEdge;
  return `<rect x="${x1}" y="${cy - h / 2}" width="${x2 - x1}" height="${h}" rx="${kind === "tube" ? 5 : 2}" fill="${fill}" stroke="${edge}" stroke-width=".9"/>` +
    `<rect x="${x1 + 3}" y="${cy - h / 2 + 3}" width="${x2 - x1 - 6}" height="4" rx="2" fill="#fff" opacity="${kind === "tube" ? ".2" : ".7"}"/>`;
}

/** Корпус разъёма сбоку x1..x2 (металл), хвостовик справа до x3. */
function coaxBody(x1: number, x2: number, x3: number, cy: number): string {
  return `<path d="M${x1},${cy - 24} L${x2},${cy - 24} L${x2},${cy - 14} L${x3},${cy - 14} L${x3},${cy + 14} L${x2},${cy + 14} L${x2},${cy + 24} L${x1},${cy + 24} z" fill="${CX.metal}" stroke="${CX.metalEdge}" stroke-width="1"/>` +
    `<rect x="${x1 + 4}" y="${cy - 20}" width="${x2 - x1 - 8}" height="5" rx="2" fill="#fff" opacity=".7"/>` +
    [0.3, 0.55].map((f) => `<line x1="${x1 + (x2 - x1) * f}" y1="${cy - 24}" x2="${x1 + (x2 - x1) * f}" y2="${cy + 24}" stroke="${CX.metalEdge}" stroke-width=".8"/>`).join("");
}

function sideCaption(end: Row): string {
  return [str(end.side) ? `Сторона ${str(end.side)}` : "", str(end.connector)].filter(Boolean).join(" · ");
}

/** Резка коаксиального кабеля: мерный отрезок с длиной и допуском, торец — вид с торца (рез 90°, слои не смяты). */
function coaxCutSketch(operation: Row): string {
  const f = obj(operation.fields), cable = str(list(operation.components)[0]?.art);
  const tolerance = etkTolerance(f.cutTolerance), length = mm(str(f.lengthKd).replace(/мм$/, "")) || "по КД";
  const cy = 46;
  const body = coaxJacket(40, 470, cy, cable) +
    [40, 470].map((x) => `<line x1="${x}" y1="${cy - 26}" x2="${x}" y2="${cy + 52}" stroke="#9aa7ad" stroke-dasharray="3 3"/>`).join("") +
    dim(40, 470, cy + 46, `L = ${length}${tolerance ? ` ${tolerance}` : ""}`) +
    coaxFace(556, cy) + caption(556, cy + 46, "торец: рез 90°", "middle") + caption(556, cy + 62, "слои не смяты", "middle");
  return svg(640, cy + 74, body);
}

/** Резка ТУТ: отрезок трубки с длиной, сколько отрезков на партию. */
function coaxTutSketch(operation: Row): string {
  const f = obj(operation.fields), part = list(operation.components)[0] || {};
  const length = mm(str(f.lengthKd).replace(/мм$/, "")) || "по КД", cy = 44;
  const amount = str(part.norm) ? `на партию: ${str(part.norm)} ${str(part.unit).replace(/\.$/, "").toLowerCase()}` : "";
  const body = coaxSleeve(160, 440, cy, 30, "tube") +
    `<text x="300" y="${cy + 4}" text-anchor="middle" font-size="12" fill="#fff">${esc(str(part.art) || "ТУТ")}</text>` +
    [160, 440].map((x) => `<line x1="${x}" y1="${cy - 24}" x2="${x}" y2="${cy + 46}" stroke="#9aa7ad" stroke-dasharray="3 3"/>`).join("") +
    dim(160, 440, cy + 40, `L = ${length}`) +
    `<circle cx="540" cy="${cy}" r="22" fill="${CX.tube}"/><circle cx="540" cy="${cy}" r="16" fill="#fff" stroke="${CX.ink}" stroke-width=".6"/>` +
    caption(540, cy + 40, "сечение круглое", "middle") + (amount ? caption(300, cy + 66, amount, "middle") : "");
  return svg(640, cy + (amount ? 76 : 56), body);
}

/** Конец кабеля с разделкой L1–L3 (жила с x0, диэлектрик, оплётка, оболочка) — основа пайки контакта и корпуса. */
function coaxEndGeometry(c: Row, x0: number, span: number) {
  const L = [num(c.l1), num(c.l2), num(c.l3)];
  const ok = L[0] > 0 && L[1] > L[0] && L[2] > L[1];
  const [l1, l2, l3] = ok ? L : [1, 2.4, 4];
  const X = (v: number) => x0 + (v * span) / l3;
  return { ok, x1: X(l1), x2: X(l2), x3: X(l3) };
}

function coaxEndBase(c: Row, x0: number, span: number, cy: number, braidOver = false): string {
  const g = coaxEndGeometry(c, x0, span);
  return coaxJacket(g.x3, 610, cy, str(c.cable)) +
    (braidOver ? "" : `<rect x="${g.x2}" y="${cy - 14}" width="${g.x3 - g.x2}" height="28" fill="url(#etkBraid)" stroke="${CX.metalEdge}"/>`) +
    `<rect x="${g.x1}" y="${cy - 11}" width="${g.x2 - g.x1}" height="22" fill="${CX.diel}" stroke="${CX.ink}" stroke-width=".8"/>` +
    `<rect x="${x0}" y="${cy - 4}" width="${g.x1 - x0}" height="8" fill="${CX.cu}" stroke="${CX.ink}" stroke-width=".6"/>`;
}

/** Центральный контакт на жиле: хвостовик с отверстием под пайку (припой), буртик и рабочая часть влево. */
function coaxPin(x0: number, xEnd: number, cy: number, solder: boolean): string {
  return `<rect x="${x0 - 70}" y="${cy - 3}" width="44" height="6" fill="${CX.metal}" stroke="${CX.metalEdge}"/>` +
    `<path d="M${x0 - 70},${cy - 3} L${x0 - 80},${cy} L${x0 - 70},${cy + 3} z" fill="${CX.metal}" stroke="${CX.metalEdge}"/>` +
    `<rect x="${x0 - 26}" y="${cy - 8}" width="6" height="16" fill="${CX.metal}" stroke="${CX.metalEdge}"/>` +
    `<rect x="${x0 - 20}" y="${cy - 7}" width="${xEnd - x0 + 20}" height="14" rx="1" fill="${CX.metal}" stroke="${CX.metalEdge}"/>` +
    (solder ? `<rect x="${x0 - 16}" y="${cy - 3.5}" width="${xEnd - x0 + 12}" height="7" fill="#c9ced3"/><rect x="${x0 - 16}" y="${cy - 1.5}" width="${xEnd - x0 + 12}" height="3" fill="${CX.cu}" opacity=".45"/>` : "");
}

/** Операции конца коаксиала: пайка (обжим) центрального контакта, корпус разъёма, пайка экрана, крышка. */
function coaxEndSketch(operation: Row): string {
  const end = obj(obj(operation.fields).coaxEnd), type = str(operation.type), cy = 70, x0 = 150, span = 300;
  const g = coaxEndGeometry(end, x0, span), xPin = x0 + (g.x1 - x0) * 0.8;
  const crimp = /обжим/i.test(str(end.pinType));
  let body = caption(20, 18, sideCaption(end), "start", 600, CX.ink);
  if (type === "coaxPin") {
    body += coaxEndBase(end, x0, span, cy) + coaxPin(x0, xPin, cy, !crimp) +
      `<line x1="${xPin}" y1="${cy + 10}" x2="${xPin}" y2="${cy + 34}" stroke="#9aa7ad" stroke-dasharray="3 3"/>` +
      `<line x1="${g.x1}" y1="${cy + 12}" x2="${g.x1}" y2="${cy + 34}" stroke="#9aa7ad" stroke-dasharray="3 3"/>` +
      caption(x0 - 80, cy - 22, `Центральный контакт: ${crimp ? "обжать" : "припаять"} на жилу`) +
      caption(g.x1 + 6, cy + 32, "зазор до диэлектрика — по КД");
    if (g.ok) body += caption(x0, cy + 56, `L1 = ${mm(end.l1)} · L2 = ${mm(end.l2)} · L3 = ${mm(end.l3)}`);
    return svg(640, cy + (g.ok ? 66 : 46), body);
  }
  // Корпус: пин в изоляторе, оплётка расправлена по хвостовику; пайка экрана / крышка — тот же вид с подписью.
  const tail = g.x3 + 16;
  body += coaxEndBase(end, x0, span, cy, true) + coaxBody(x0 - 90, g.x2, tail, cy) +
    `<path d="M${g.x2},${cy - 14} L${g.x3 + 24},${cy - 17} L${g.x3 + 24},${cy + 17} L${g.x2},${cy + 14} z" fill="url(#etkBraid)" stroke="${CX.metalEdge}"/>`;
  const what: Record<string, string> = {
    coaxHousing: "Корпус надвинуть до упора: контакт зафиксирован в изоляторе",
    coaxSolderShield: "Экран припаять к корпусу по окружности",
    coaxCapCrimp: "Крышку надеть и обжать", coaxCapScrew: "Крышку завернуть до упора",
  };
  body += caption(x0 - 90, cy - 32, what[type] || "Монтаж разъёма") +
    caption(g.x2, cy + 38, "оплётка расправлена по хвостовику, внутрь не завёрнута");
  return svg(640, cy + 48, body);
}

/** Надеть ТУТ / втулки экрана, обжать втулку, усадить ТУТ. */
function coaxPartsSketch(operation: Row): string {
  const p = obj(obj(operation.fields).coaxParts), type = str(operation.type), cy = 62;
  const kind = str(p.kind) === "sleeve" ? "sleeve" : "tube", count = Math.max(1, num(p.count) || 1);
  const name = str(p.name) || (kind === "tube" ? "ТУТ" : "втулка экрана");
  const what = `${name}${count > 1 ? ` × ${count}` : ""}`;
  if (type === "coaxInsTut" || type === "coaxInsSleeve") {
    const h = kind === "tube" ? 38 : 36, w = kind === "tube" ? 70 : 34;
    let body = coaxJacket(40, 600, cy, str(p.cable));
    const xs = count > 1 ? [150, 600 - 110 - w] : [150];
    xs.forEach((x) => { body += coaxSleeve(x, x + w, cy, h, kind); });
    body += `<line x1="50" y1="${cy + 34}" x2="140" y2="${cy + 34}" stroke="${CX.ink}" stroke-width="1" marker-end="url(#etkA)"/>` +
      (count > 1 ? `<line x1="590" y1="${cy + 34}" x2="${600 - 100}" y2="${cy + 34}" stroke="${CX.ink}" stroke-width="1" marker-end="url(#etkA)"/>` : "") +
      caption(40, 18, `Надеть: ${what}`, "start", 600, CX.ink) +
      caption(320, cy + 52, "до монтажа корпусов, отодвинуть от концов", "middle");
    return svg(640, cy + 62, body);
  }
  // Обжать втулку / усадить ТУТ — у корпуса разъёма: корпус слева, хвостовик с оплёткой, втулка, кабель.
  const bx = 60, tail = 260;
  let body = coaxJacket(tail - 20, 600, cy, str(p.cable)) + coaxBody(bx, 200, tail, cy) +
    `<rect x="200" y="${cy - 16}" width="${tail - 200 + 8}" height="32" fill="url(#etkBraid)" stroke="${CX.metalEdge}"/>`;
  if (type === "coaxShield") {
    body += `<path d="M196,${cy - 19} L${tail + 14},${cy - 19} L${tail + 20},${cy} L${tail + 14},${cy + 19} L196,${cy + 19} L190,${cy} z" fill="${CX.metal}" stroke="${CX.metalEdge}" stroke-width="1"/>` +
      `<line x1="196" y1="${cy - 7}" x2="${tail + 16}" y2="${cy - 7}" stroke="${CX.metalEdge}"/><line x1="196" y1="${cy + 7}" x2="${tail + 16}" y2="${cy + 7}" stroke="${CX.metalEdge}"/>` +
      `<line x1="${(196 + tail) / 2}" y1="${cy - 44}" x2="${(196 + tail) / 2}" y2="${cy - 22}" stroke="${CX.ink}" marker-end="url(#etkA)"/>` +
      `<line x1="${(196 + tail) / 2}" y1="${cy + 44}" x2="${(196 + tail) / 2}" y2="${cy + 22}" stroke="${CX.ink}" marker-end="url(#etkA)"/>` +
      caption(20, 14, `Втулку экрана${count > 1 ? " (с каждой стороны)" : ""} надвинуть до упора в корпус и обжать шестигранником`, "start", 600, CX.ink) +
      caption(300, cy + 40, "волоски оплётки не выступают из-под втулки");
    return svg(640, cy + 52, body);
  }
  body += `<rect x="186" y="${cy - 21}" width="${tail + 120 - 186}" height="42" rx="6" fill="${CX.tube}" opacity=".92" stroke="${CX.ink}"/>` +
    `<text x="${(186 + tail + 120) / 2}" y="${cy + 4}" text-anchor="middle" font-size="12" fill="#fff">${esc(name)}</text>` +
    dim(tail, tail + 120, cy - 30, "на оболочку — по КД") +
    caption(20, 14, `Усадить: ${what}`, "start", 600, CX.ink) + caption(186, cy + 40, "заходит на корпус по КД; без складок и пузырей");
  return svg(640, cy + 50, body);
}

/** Эскиз операции коакса без разделки (её рисует etkDrawCoax): по виду операции и её данным. */
export function etkDrawCoaxOp(operation: Row): string {
  const type = str(operation.type), f = obj(operation.fields);
  if (type === "coaxCut" && !Object.keys(obj(f.coax)).length) return coaxCutSketch(operation);
  if (type === "coaxCutTut") return coaxTutSketch(operation);
  if (Object.keys(obj(f.coaxEnd)).length) return coaxEndSketch(operation);
  if (Object.keys(obj(f.coaxParts)).length) return coaxPartsSketch(operation);
  return "";
}

// ── Резка трубок (владелец 16.10, 713К.4 КБ1: «рисунка нет»): отрезки нарезанных трубок — тёмные скруглённые планки,
// на каждый вид подпись «ТТК 8/2 ×3 · 15 мм». Данные — `fields.tutParts` (если сервер их отдаёт) либо результат операции
// («Трубки нарезаны: ТТК 8/2мм, черная — 3 шт по 15 мм; …», «Трубка X нарезана: 2 шт по 120 мм; …»).
export type EtkTubePiece = { label: string; count: number; len: string };

const TUBE_ROWS = 7;

/** «ТТК 4,8/1,6мм прозрачная…, черная» → «ТТК 4,8/1,6мм»: слова до первого с размером (запятая в размере — не разделитель). */
function tubeLabel(value: string): string {
  const words = value.split(/,\s+/)[0].trim().split(/\s+/);
  const at = words.findIndex((word, index) => index > 0 && /\d/.test(word));
  return (at < 0 ? words : words.slice(0, at + 1)).join(" ");
}

/** Отрезки трубок операции резки (`cutTut`): вид, число на изделие и длина; не резка трубок — пусто. */
export function etkTubePieces(operation: Row): EtkTubePiece[] {
  if (str(operation.type) !== "cutTut") return [];
  const own = list(obj(operation.fields).tutParts).map((item) => ({ label: tubeLabel(str(item.art) || str(item.name)), count: num(item.count), len: str(item.len).trim() }))
    .filter((piece) => piece.label && piece.count > 0);
  if (own.length) return own;
  // Результат — строка на вид и длину: «ТТК 8/2мм, черная — 15 мм», количество в норме строки (отрезков на партию).
  const byRow = list(operation.result).flatMap((row) => {
    const found = str(row.name).trim().match(/^(.+?)\s+—\s+([\d.,]+)\s+мм$/);
    const count = Number(str(row.norm).replace(",", "."));
    return found && count > 0 ? [{ label: tubeLabel(found[1]), count, len: found[2] }] : [];
  });
  if (byRow.length && byRow.length === list(operation.result).length) return byRow;
  const text = str(list(operation.result)[0]?.name);
  const at = text.indexOf(": ");
  if (at < 0) return [];
  const head = text.slice(0, at);
  if (!/^(Трубк|Маркировк)/.test(head)) return [];
  const material = tubeLabel(head.match(/^Трубка\s+(.+?)\s+нарезана$/)?.[1] ?? (head.startsWith("Маркировк") ? "Маркировка" : "Трубка"));
  return text.slice(at + 2).split(/;\s*/).flatMap((chunk) => {
    const found = chunk.trim().match(/^(?:(.+?)\s+—\s+)?(\d+(?:[.,]\d+)?)\s+шт(?:\s+по\s+([\d.,]+)\s+мм)?$/);
    return found ? [{ label: found[1] ? tubeLabel(found[1]) : material, count: Number(found[2].replace(",", ".")), len: found[3] ?? "" }] : [];
  });
}

/** Нарезанные трубки: планка на вид, длина планки — по длине отрезка. */
export function etkDrawTubeCut(operation: Row): string {
  const pieces = etkTubePieces(operation);
  if (!pieces.length) return "";
  const shown = pieces.slice(0, TUBE_ROWS), rowH = 34, top = 14;
  const lens = shown.map((piece) => Number(piece.len.replace(",", ".")) || 0), longest = Math.max(1, ...lens);
  let body = "";
  shown.forEach((piece, at) => {
    const cy = top + at * rowH + 12, w = lens[at] ? 70 + (150 * lens[at]) / longest : 120;
    const count = Number.isInteger(piece.count) ? String(piece.count) : String(piece.count).replace(".", ",");
    body += coaxSleeve(20, 20 + w, cy, 22, "tube") +
      `<line x1="${20 + 12}" y1="${cy - 11}" x2="${20 + 12}" y2="${cy + 11}" stroke="#fff" stroke-width="1" opacity=".35"/>` +
      `<line x1="${20 + w - 12}" y1="${cy - 11}" x2="${20 + w - 12}" y2="${cy + 11}" stroke="#fff" stroke-width="1" opacity=".35"/>` +
      `<text x="${20 + w + 12}" y="${cy + 5}" font-size="14" fill="${CX.ink}" font-weight="600">${esc(piece.label)} ×${count}${piece.len ? ` · ${esc(mm(piece.len))}` : ""}</text>`;
  });
  const more = pieces.length - shown.length;
  if (more > 0) body += caption(20, top + shown.length * rowH + 12, `и ещё ${more}`);
  return svg(640, top + shown.length * rowH + (more > 0 ? 24 : 4), body);
}

/** Опрессовка: обжатый открытый контакт сбоку, высота обжима и отрыв из БД.ТЕР (etkDrawCrimp_). */
export function etkDrawCrimp(operation: Row): string {
  const f = obj(operation.fields);
  if (!("crimpHeight" in f) || operation.type !== "prsTerm") return "";
  const cy = 84, tin = "url(#etkTin)", edge = "#17272b";
  const ln = (x1: number, y1: number, x2: number, y2: number, extra = "") =>
    `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${edge}" stroke-width=".8"${extra}/>`;
  const P = (d: string, fill?: string | null, sw?: string) => `<path d="${d}" fill="${fill || "none"}" stroke="${edge}" stroke-width="${sw || ".8"}"/>`;
  const call = (x: number, ty: number, px: number, py: number, text: string) => {
    const y1 = ty < py ? ty + 4 : ty - 12;
    return `<line x1="${x}" y1="${y1}" x2="${px}" y2="${py}" stroke="#9aa7ad" stroke-width=".8"/>` +
      `<circle cx="${px}" cy="${py}" r="1.8" fill="#9aa7ad"/>` +
      `<text x="${x}" y="${ty}" text-anchor="middle" font-size="11" fill="#5f7470">${text}</text>`;
  };
  const grad = (id: string, stops: Array<[number, string]>) =>
    `<linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">${stops.map(([offset, color]) => `<stop offset="${offset}" stop-color="${color}"/>`).join("")}</linearGradient>`;
  const y = (d: number) => cy + d;
  const body = "<defs>" + grad("etkTin", [[0, "#f4f6f7"], [0.45, "#d2d7db"], [1, "#a9b1b7"]]) +
      grad("etkIns", [[0, "#8e9ca5"], [0.5, "#6b7c86"], [1, "#4d5b64"]]) +
      grad("etkCu", [[0, "#eba66e"], [0.5, "#c87533"], [1, "#94511e"]]) + "</defs>" +
    `<rect x="24" y="${y(-9)}" width="236" height="18" rx="5" fill="url(#etkIns)" stroke="${edge}" stroke-width=".8"/>` +
    `<rect x="258" y="${y(-5)}" width="96" height="10" fill="url(#etkCu)" stroke="${edge}" stroke-width=".6"/>` +
    [-2.5, 0, 2.5].map((d, i) => `<line x1="260" y1="${y(d)}" x2="${355 + (i % 2) * 3}" y2="${y(d)}" stroke="#7a3f14" stroke-width=".6"/>`).join("") +
    `<rect x="188" y="${y(11)}" width="200" height="4" fill="${tin}" stroke="${edge}" stroke-width=".6"/>` +
    `<rect x="182" y="${y(12)}" width="7" height="2.5" fill="${tin}" stroke="${edge}" stroke-width=".5"/>` +
    P(`M198,${y(-12)} Q220,${y(-17)} 242,${y(-12)} L244,${y(12)} L196,${y(12)} z`, tin) +
    P(`M204,${y(-13)} q2,-9 10,-7 q5,2 2,7`, tin) + P(`M236,${y(-13)} q-2,-9 -10,-7 q-5,2 -2,7`, tin) +
    ln(220, y(-15), 220, y(10), ' stroke-dasharray="2 2" stroke-opacity=".5"') +
    P(`M290,${y(-9)} Q314,${y(-12)} 338,${y(-9)} L338,${y(12)} L290,${y(12)} z`, tin) +
    P(`M290,${y(-9)} L283,${y(-13)} L283,${y(12)} L290,${y(12)}`, tin, ".7") +
    ln(292, y(-5), 336, y(-5), ' stroke-width=".6"') +
    P(`M338,${y(11)} L362,${y(11)} L378,${y(-6)} L387,${y(-6)} L387,${y(15)} L338,${y(15)} z`, tin, ".6") +
    `<rect x="386" y="${y(-14)}" width="132" height="30" rx="1.5" fill="${tin}" stroke="${edge}" stroke-width=".9"/>` +
    ln(386, y(-9), 518, y(-9), ' stroke-width=".5" stroke-opacity=".6"') +
    P(`M456,${y(-14)} L426,${y(-26)} L422,${y(-23)} L448,${y(-14)} z`, tin) +
    `<rect x="424" y="${y(-13)}" width="34" height="3" fill="#6d767b"/>` +
    `<rect x="468" y="${y(-3)}" width="36" height="10" rx="2" fill="#8d969c" stroke="${edge}" stroke-width=".5"/>` +
    P(`M470,${y(6)} Q486,${y(-2)} 502,${y(4)}`, null, ".7") +
    P(`M518,${y(-14)} L525,${y(-8)} L525,${y(10)} L518,${y(16)}`, tin) +
    call(80, 30, 80, cy - 9, "изоляция") +
    call(210, 30, 214, cy - 17, "обжим по изоляции") +
    call(312, 30, 304, cy - 9, "обжим по жиле") +
    call(468, 30, 468, cy - 14, "контактная часть · фиксатор") +
    call(262, cy + 38, 264, cy + 3, "окно") +
    call(356, cy + 38, 350, cy + 3, "выход жил") +
    `<line x1="326" y1="${cy - 20}" x2="326" y2="${cy + 20}" stroke="#9aa7ad" stroke-dasharray="3 3"/>` +
    `<line x1="326" y1="${cy - 9}" x2="326" y2="${cy + 12}" stroke="${edge}" marker-start="url(#etkA)" marker-end="url(#etkA)"/>` +
    `<text x="300" y="${cy + 62}" font-size="12" fill="${edge}">Высота обжима по жиле: ${esc(f.crimpHeight ? `${str(f.crimpHeight)} мм` : "⚠ нет в БД.ТЕР")}</text>` +
    `<text x="24" y="${cy + 62}" font-size="12" fill="${edge}">Отрыв: ${esc(f.pullForce ? `${str(f.pullForce)} Н` : "—")}</text>`;
  return svg(560, cy + 72, body);
}

/** Корпус разъёма по раскладке семейства: вид со стороны проводов (etkHousing_ → рисунок серии). {w, h, body} от (x, y). */
export function etkHousingSvg(hs: EtkHousing, x: number, y: number, s: number, byPin: Record<number, string> | null) {
  return connectorFace(hs, x, y, s, byPin);
}

function housingCaption(hs: EtkHousing): string {
  return esc(`${str(hs.art)} · ${str(hs.title)}${hs.pitch ? `, шаг ${String(hs.pitch).replace(".", ",")} мм` : ""}` +
    (hs.kind === "lug" || hs.kind === "banana" || hs.kind === "barrel" ? "" : ` · вид со стороны проводов${hs.latch === "ramp" || hs.latch === "lever" ? ", защёлка сверху" : hs.latch === "mark" ? ", метка ▽ сверху, над контактом 1" : hs.latch === "windows" ? ", окна фиксаторов снизу" : ""}`));
}

function housingWarn(hs: EtkHousing): string {
  return hs.conf === "high" ? "" : `⚠ нумерация не сверена с чертежом производителя${hs.note ? ` — ${hs.note}` : ""}`;
}

/** Монтаж в разъём: торец со стороны проводов крупно, рядом вид корпуса и под ним тот же корпус, развёрнутый видом сверху
 *  (только прямоугольные корпуса; владелец, 16.10: «просто развернуть»), ниже легенда «конт. — провод» (etkDrawHousingOp_).
 *  Без распиновки в генераторе — список проводов «контакт по КД». */
export function etkDrawHousingOp(operation: Row): string {
  const hs = operation.housing as EtkHousing, byPin: Record<number, string> = {}, ws = list(obj(operation.fields).wires);
  ws.forEach((w) => (Array.isArray(w.pins) ? w.pins as number[] : []).forEach((p) => { byPin[p] = str(w.art || w.name); }));
  const warn = housingWarn(hs), W = 640, top = warn ? 46 : 30;
  const pictureW = 150, faceArea = W - pictureW - 70;
  const round = hs.kind === "round" || hs.kind === "barrel";
  const s = round ? 22 : Math.max(16, Math.min(46, faceArea / connectorFaceUnits(hs) / 1.15));
  const g = etkHousingSvg(hs, 0, 0, s, byPin), pic = connectorPicture(hs, pictureW, 110, byPin);
  const x0 = Math.max(24, (faceArea - g.w) / 2 + 12);
  let body = `<text x="20" y="16" font-size="11" fill="#5f7470">${housingCaption(hs)}</text>` +
    (warn ? `<text x="20" y="32" font-size="10.5" fill="#b3261e">${esc(warn.length > 110 ? `${warn.slice(0, 109)}…` : warn)}</text>` : "") +
    `<g transform="translate(${x0},${top + 6})">${g.body}</g>` +
    `<g transform="translate(${W - pictureW - 20},${top})">${pic.body}</g>` +
    `<text x="${W - pictureW / 2 - 20}" y="${top + pic.h + 14}" text-anchor="middle" font-size="10" fill="#5f7470">${esc(connectorSummary(hs))}</text>`;
  let right = pic.h + 18;
  const view = connectorTopView(hs, pictureW, 110, byPin);
  if (view) {
    const vy = top + right + 14;
    body += `<g transform="translate(${W - pictureW / 2 - 20 - view.w / 2},${vy})">${view.body}</g>` +
      `<text x="${W - pictureW / 2 - 20}" y="${vy + view.h + 14}" text-anchor="middle" font-size="10" fill="#5f7470">вид сверху, провода снизу</text>`;
    right += view.h + 32;
  }
  let ly = top + Math.max(g.h + 6, right) + 22;
  const pins = Object.keys(byPin).map(Number).sort((a, b) => a - b);
  if (pins.length) {
    const legend = pinLegend(pins, (pin) => byPin[pin], pinPeers(obj(operation.fields)), ly);
    body += legend.body;
    ly += legend.h;
  } else {
    body += `<text x="40" y="${ly}" font-size="11" fill="#b3261e">Распиновка в генераторе не задана — контакт каждого провода по КД:</text>`;
    ly += 18;
    ws.slice(0, 12).forEach((w, i) => {
      const cx = 40 + (i % 2) * 300, cy = ly + Math.floor(i / 2) * 17;
      body += `<rect x="${cx}" y="${cy - 9}" width="12" height="12" fill="${etkWireColor(w.art || w.name)}" stroke="#17272b" stroke-width=".6"/>` +
        `<text x="${cx + 18}" y="${cy + 1}" font-size="11" fill="#17272b">${esc(`${str(w.art || w.name)} × ${str(w.qty || 1)}`)}</text>`;
    });
    ly += Math.ceil(Math.min(ws.length, 12) / 2) * 17;
  }
  return svg(W, ly + 4, body);
}

/** Распиновка: торец разъёма с пронумерованными контактами и легендой (etkDrawPinout_). */
export function etkDrawPinout(operation: Row): string {
  const f = obj(operation.fields);
  if (operation.housing && operation.type === "insTerm") return etkDrawHousingOp(operation);
  if (!f.pinout) return "";
  const byPin: Record<number, Row> = {};
  let max = 0;
  list(f.wires).forEach((w) => (Array.isArray(w.pins) ? w.pins as number[] : []).forEach((p) => { byPin[p] = w; if (p > max) max = p; }));
  if (!max) return "";
  const W = 640, cw = Math.min(40, 560 / max), x0 = (W - cw * max) / 2, y0 = 34;
  let body = `<text x="${x0}" y="16" font-size="11" fill="#5f7470">${esc(str(operation.connector) || "Разъём")} — вид со стороны контактов</text>`;
  for (let p = 1; p <= max; p++) {
    const x = x0 + (p - 1) * cw, w = byPin[p];
    body += `<rect x="${x}" y="${y0}" width="${cw}" height="${cw}" fill="#fff" stroke="#17272b" stroke-width="1.2"/>` +
      (w ? `<rect x="${x + cw * 0.22}" y="${y0 + cw * 0.22}" width="${cw * 0.56}" height="${cw * 0.56}" fill="${etkWireColor(w.art || w.name)}" stroke="#17272b" stroke-width=".8"/>` : "") +
      `<text x="${x + cw / 2}" y="${y0 - 5}" text-anchor="middle" font-size="${cw < 22 ? 9 : 11}" fill="#17272b">${p}</text>`;
  }
  const used = Object.keys(byPin).map(Number).sort((a, b) => a - b);
  const ly = y0 + cw + 20;
  const legend = pinLegend(used, (pin) => str(byPin[pin].art || byPin[pin].name), pinPeers(f), ly);
  return svg(W, ly + legend.h + 6, body + legend.body);
}

/** Другой конец провода из генератора (fields.links, etk_builder._links): контакт → «→ В1 конт. 3» / «← А1 конт. 1». */
function pinPeers(fields: Row): Record<number, string[]> {
  const out: Record<number, string[]> = {};
  list(fields.links).forEach((link) => {
    const peer = obj(link.peer), pin = num(link.pin);
    if (!pin || !peer.end) return;
    (out[pin] ||= []).push(`${link.dir === "from" ? "←" : "→"} ${str(peer.end)} конт. ${str(peer.pin)}`);
  });
  return out;
}

/** Легенда «конт. — провод» под торцом разъёма; с соединениями — в одну колонку, другой конец провода жирным. */
function pinLegend(pins: number[], wireOf: (pin: number) => string, peers: Record<number, string[]>, y: number): { body: string; h: number } {
  const cols = pins.some((pin) => peers[pin]) ? 1 : 2;
  const body = pins.map((pin, i) => {
    const cx = 40 + (i % cols) * 300, cy = y + Math.floor(i / cols) * 17, wire = wireOf(pin);
    return `<rect x="${cx}" y="${cy - 9}" width="12" height="12" fill="${etkWireColor(wire)}" stroke="#17272b" stroke-width=".6"/>` +
      `<text x="${cx + 18}" y="${cy + 1}" font-size="11" fill="#17272b">конт. ${pin} — ${esc(wire)}` +
      (peers[pin] ? ` <tspan font-weight="700" fill="#00554f">${esc(peers[pin].join(", "))}</tspan>` : "") + "</text>";
  }).join("");
  return { body, h: Math.ceil(pins.length / cols) * 17 };
}

export const ETK_CONNECTION_COLUMNS = ["№", "Провод", "Откуда", "Конт.", "Куда", "Конт."];

function connectionEnd(end: Row): string {
  return [[str(end.end), str(end.label)].filter(Boolean).join(" "), str(end.conn)].filter(Boolean).join(" · ");
}

/** Таблица соединений сборки (документ ЭТК `connections`, etk_builder._connections) строками — экран и печать. */
export function etkConnectionRows(value: unknown): string[][] {
  return list(value).map((item, i) => {
    const from = obj(item.from), to = obj(item.to);
    return [String(i + 1), str(item.wire), connectionEnd(from), str(from.pin), connectionEnd(to), str(to.pin)];
  });
}

/** Свивка: пары двумя синусоидами в их цветах, шаг — размером (etkDrawTwist_). */
export function etkDrawTwist(operation: Row): string {
  const t = obj(obj(operation.fields).twist);
  if (!Object.keys(t).length) return "";
  const pairs = str(t.pairs).split("\n").map((s) => s.replace(/^\d+\)\s*/, "").split(" + "))
    .filter((p) => p.length === 2).slice(0, 3);
  if (!pairs.length) return "";
  const W = 640, x0 = 40, x1 = 560, per = (x1 - x0) / 4, rowH = 54;
  let body = "";
  pairs.forEach((p, r) => {
    const cy = 30 + r * rowH;
    [0, 1].forEach((k) => {
      let d = "";
      for (let x = x0; x <= x1; x += 4) {
        const y = cy + 9 * Math.sin((x - x0) / per * 2 * Math.PI + k * Math.PI);
        d += `${x === x0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      }
      body += `<path d="${d}" fill="none" stroke="#17272b" stroke-width="6.5" stroke-linecap="round"/>` +
        `<path d="${d}" fill="none" stroke="${etkWireColor(p[k])}" stroke-width="5" stroke-linecap="round"/>`;
    });
    body += `<text x="${x1 + 12}" y="${cy - 2}" font-size="10.5" fill="#17272b">${esc(p[0])}</text>` +
      `<text x="${x1 + 12}" y="${cy + 11}" font-size="10.5" fill="#17272b">${esc(p[1])}</text>`;
  });
  const yd = 30 + pairs.length * rowH - 16;
  body += `<line x1="${x0}" y1="${yd - 18}" x2="${x0}" y2="${yd + 4}" stroke="#9aa7ad" stroke-dasharray="3 3"/>` +
    `<line x1="${x0 + per}" y1="${yd - 18}" x2="${x0 + per}" y2="${yd + 4}" stroke="#9aa7ad" stroke-dasharray="3 3"/>` +
    dim(x0, x0 + per, yd, `шаг ${t.pitch ? `${str(t.pitch)} мм` : "по КД"}`);
  return svg(W + 180, yd + 12, body);
}

/** Эскиз операции — первый подходящий, в порядке renderEtk_/etkPrintHtml_. */
export function etkFigure(operation: Row): string {
  return etkDrawWires(operation) || etkDrawTubeCut(operation) || etkDrawCoax(operation) || etkDrawCoaxOp(operation) || (operation.type !== "prsTerm" ? etkDrawPinout(operation) : "") ||
    etkDrawCrimp(operation) || etkDrawTwist(operation);
}

function cut(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

/** Схема жгута: разъёмы по слоям слева направо — рисунком своей серии (connectorArt), связи — пучок жил, число
 *  проводов и длина (etkDrawHarness_). `pins` — распиновка по узлу «разъём::сторона» из операций монтажа: провода
 *  видны цветом в своих гнёздах. */
export function etkDrawHarness(topo: EtkTopologyData | null | undefined, pins: Record<string, Record<number, string>> = {}): string {
  const nodes = topo?.nodes ?? [];
  if (nodes.length < 2) return "";
  const cols: Record<number, typeof nodes> = {};
  nodes.forEach((n) => { (cols[n.depth || 0] = cols[n.depth || 0] || []).push(n); });
  const depths = Object.keys(cols).map(Number).sort((a, b) => a - b);
  const maxRows = Math.max(...depths.map((d) => cols[d].length));
  const withHs = nodes.some((n) => n.housing);
  const picH = 78, bw = withHs ? 196 : 160, bh = withHs ? picH + 62 : 38, gapY = 26;
  const colW = depths.length > 1 ? Math.min(320, Math.max(bw + 120, 620 / (depths.length - 1))) : 0;
  const span = (depths.length - 1) * colW + bw, W = Math.max(640, span + 80), x0 = (W - span) / 2, H = 30 + maxRows * (bh + gapY);
  const pos: Record<string, { x: number; y: number; ay: number }> = {};
  depths.forEach((d, ci) => {
    const col = cols[d], top = (H - col.length * (bh + gapY) + gapY) / 2;
    col.forEach((n, i) => {
      const y = top + i * (bh + gapY);
      pos[n.id] = { x: x0 + ci * colW, y, ay: n.housing ? y + 8 + picH / 2 : y + bh / 2 };
    });
  });
  let body = "";
  (topo?.edges ?? []).forEach((e) => {
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) return;
    const x1 = a.x + bw, y1 = a.ay, x2 = b.x, y2 = b.ay, mx = (x1 + x2) / 2;
    const strands = (e.wires || []).slice(0, 8), k = 2.6;
    body += `<path d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" fill="none" stroke="#17272b" stroke-width="${strands.length * k + 1.6}" stroke-linecap="round"/>`;
    strands.forEach((wn, i) => {
      const d = (i - (strands.length - 1) / 2) * k;
      body += `<path d="M${x1},${y1 + d} C${mx},${y1 + d} ${mx},${y2 + d} ${x2},${y2 + d}" fill="none" stroke="${etkWireColor(wn)}" stroke-width="${k - 0.5}"/>`;
    });
    body += `<rect x="${mx - 46}" y="${(y1 + y2) / 2 - 9}" width="92" height="16" rx="8" fill="#fff" stroke="#dbe7e5"/>` +
      `<text x="${mx}" y="${(y1 + y2) / 2 + 3}" text-anchor="middle" font-size="10.5" fill="#17272b">` +
      `${esc(`${e.count || (e.wires || []).length} пров.${e.length ? ` · ${e.length} мм` : ""}`)}</text>`;
  });
  nodes.forEach((n) => {
    const p = pos[n.id], name = String(n.conn);
    let ty = p.y + bh - (n.housing ? 38 : 26);
    body += `<rect x="${p.x}" y="${p.y}" width="${bw}" height="${bh}" rx="7" fill="#f3f8f7" stroke="#00706a" stroke-width="1.2"/>`;
    if (n.housing) {
      const hs = n.housing, g = connectorPicture(hs, bw - 40, picH - 6, pins[`${name}::${n.side || ""}`] || null);
      body += `<g transform="translate(${p.x + (bw - g.w) / 2},${p.y + 6 + (picH - g.h) / 2})">${g.body}</g>`;
    } else if (n.kind === "splice") {
      const cx = p.x + bw / 2, cy = p.y + (withHs ? picH / 2 + 8 : (bh - 26) / 2 + 4);
      body += `<line x1="${cx - 44}" y1="${cy}" x2="${cx + 44}" y2="${cy}" stroke="#6b7c86" stroke-width="4" stroke-linecap="round"/>` +
        `<rect x="${cx - 20}" y="${cy - 8}" width="40" height="16" rx="4" fill="#d9d9d9" stroke="#17272b" stroke-width="1"/>` +
        `<line x1="${cx - 8}" y1="${cy - 8}" x2="${cx - 8}" y2="${cy + 8}" stroke="#17272b"/>` +
        `<line x1="${cx + 8}" y1="${cy - 8}" x2="${cx + 8}" y2="${cy + 8}" stroke="#17272b"/>`;
      if (!withHs) ty = p.y + bh - 8;
    } else ty = withHs ? p.y + picH / 2 + 4 : p.y + 16;
    const side = n.side ? `сторона ${n.side === "B" ? "В" : "А"}` : "";
    // Две строки под рисунком: серия · шаг · контакты, затем сторона — в одну не влезает.
    const hs = n.housing, pitch = hs?.pitch && hs.kind !== "round" ? `${String(hs.pitch).replace(".", ",")} мм` : "";
    const lines = hs
      ? [[hs.title, pitch].filter(Boolean).join(" · "), [connectorSummary(hs).split(" · ").filter((part) => part !== hs.title && part !== pitch).join(" · "), side].filter(Boolean).join(" · ")]
      : [side];
    body += `<text x="${p.x + bw / 2}" y="${ty + 4}" text-anchor="middle" font-size="12" font-weight="700" fill="#00554f">` +
        `${esc(name.length > 26 ? `${name.slice(0, 25)}…` : name)}</text>` +
      lines.filter(Boolean).map((line, i) => `<text x="${p.x + bw / 2}" y="${ty + 18 + i * 12}" text-anchor="middle" font-size="10" fill="#5f7470">` +
        `${esc(cut(line, 36))}</text>`).join("");
  });
  return svg(W, H, body);
}

/** Нет значения в БД.ТЕР — так и пишется (erp-screens: «не задано», не «?»); экран красит его тоном опасности. */
export const ETK_NOT_SET = "не задано";

/** Норма обжима для чтения: «0,60 - 0,75» → «0,60–0,75», точка → запятая; старый «?» и «⚠ нет данных» — «не задано». */
export function crimpText(value: unknown): string {
  return str(value).trim()
    .replace(/(\d)\.(\d)/g, "$1,$2")
    .replace(/(\d)\s*[-–—]\s*(?=\d)/g, "$1–")
    .replace(/⚠\s*нет данных/gi, ETK_NOT_SET)
    .replace(/(^|\s)\?(?=\s|$)/g, `$1${ETK_NOT_SET}`);
}

/** Обжим одного контакта на одном конце (или на обоих) и одном сечении — `fields.crimpEnds` новых ЭТК. */
export type EtkCrimpEnd = {
  ends: string[]; art: string; name?: string; wire?: string; section?: string;
  applicator?: string; crimpHeight?: string; pullForce?: string; strip?: string;
};

export function etkCrimpEnds(fields: Row): EtkCrimpEnd[] {
  return list(fields.crimpEnds).map((item) => ({
    ends: Array.isArray(item.ends) ? item.ends.map(str) : [], art: str(item.art), name: str(item.name), wire: str(item.wire),
    section: str(item.section), applicator: str(item.applicator), crimpHeight: str(item.crimpHeight),
    pullForce: str(item.pullForce), strip: str(item.strip),
  }));
}

const withUnit = (value: string | undefined, unit: string) => value?.trim() ? `${crimpText(value)} ${unit}` : ETK_NOT_SET;
/** Отрыв: одно число — нижняя граница «≥ 9,8 Н», диапазон — как есть «9,8–39,2 Н». */
const pullText = (value: string | undefined) => !value?.trim() ? ETK_NOT_SET
  : /[–;:]/.test(crimpText(value)) ? `${crimpText(value)} Н` : `≥ ${crimpText(value)} Н`;
const endsText = (ends: string[]) => ends.length > 1 ? `Концы ${ends.join(" и ")}` : `Конец ${ends[0] || "A"}`;

/** Серая строка контакта в «Комплектующих»: конец, провод и сечение. Аппликатор, обжим, отрыв, зачистка — в шапке операции. */
export function etkCrimpSpec(entry: EtkCrimpEnd): string {
  const wire = [entry.wire, entry.section && entry.section !== entry.wire ? `(${entry.section})` : ""].filter(Boolean).join(" ");
  return [endsText(entry.ends), wire].filter(Boolean).join(" · ");
}

/** Строки спецификации обжима для строки комплектующих по артикулу контакта; у выданных до 12.10 ЭТК — пусто. */
export function etkContactSpecs(fields: Row, art: unknown): string[] {
  const key = str(art).trim();
  return key ? etkCrimpEnds(fields).filter((entry) => entry.art.trim() === key).map(etkCrimpSpec) : [];
}

const DASH = "—";
/** Параметр шапки: подпись, значение одной строкой (печать) и — когда у сечений партии оно разное — строки «сечение →
 *  значение» (владелец 16.10: «0,12 мм²: …; 0,5 мм²: …» в одну строку плохо читается). */
export type EtkParam = [label: string, value: string, rows?: Array<[sub: string, value: string]>];
export type EtkSideParams = { end: "A" | "B"; title: string; items: EtkParam[] };
/** Параметры операции в шапке ЭТК (владелец 14.10): «Сторона А» | «Общие» | «Сторона В». Сторона В — как везде в ERP
 *  (В1, «Сторона В»). Колонка стороны есть, только если операция что-то делает с этим концом. */
export type EtkParams = { sides: EtkSideParams[]; common: EtkParam[] };

/** Параметр по записям обжима одного конца: одинаковое у всех сечений — одно значение, разное — строка на сечение. */
function sideParam(label: string, entries: EtkCrimpEnd[], pick: (entry: EtkCrimpEnd) => string): EtkParam {
  const values = [...new Set(entries.map(pick))];
  if (values.length < 2) return [label, values[0]];
  const rows = entries.map((entry): [string, string] => [entry.section || entry.wire || "", pick(entry)]);
  return [label, rows.map(([sub, value]) => `${sub}: ${value}`).join("; "), rows];
}

function firstText(source: Row[], keys: string[]): string {
  for (const item of source) for (const key of keys) if (str(item[key]).trim()) return str(item[key]).trim();
  return "";
}

function sideItems(operation: Row, end: "A" | "B"): EtkParam[] {
  const f = obj(operation.fields), key = end === "A" ? "a" : "b";
  const entries = etkCrimpEnds(f).filter((entry) => entry.ends.includes(end));
  const out: EtkParam[] = [];
  if (entries.length) {
    out.push(["Терминал", [...new Set(entries.map((entry) => entry.art).filter(Boolean))].join(", ") || ETK_NOT_SET],
      sideParam("Аппликатор", entries, (entry) => entry.applicator?.trim() || ETK_NOT_SET),
      sideParam("Высота обжима", entries, (entry) => withUnit(entry.crimpHeight, "мм")),
      sideParam("Отрыв", entries, (entry) => pullText(entry.pullForce)));
  }
  // Зачистка: резка считает концы по коду операции (лужёный конец зачищен всегда); прочие — по полю зачистки.
  const entryStrip = entries.map((entry) => entry.strip?.trim()).find(Boolean) || "";
  const strip = obj(f.strip);
  const coreKey = key === "a" ? "coreA" : "coreB";
  if (strip[key] && coreKey in strip) {
    // Кабель в оболочке (владелец 15.10): разделка оболочки и зачистка жил под обжим или пайку — двумя строками.
    out.push(["Разделка оболочки", withUnit(str(key === "a" ? strip.lenA : strip.lenB), "мм")],
      ["Зачистка жил", withUnit(str(strip[coreKey]), "мм")]);
  } else if (/^cutWire/.test(str(operation.type))) {
    const cut = etkCutEnds(operation)[key];
    if (cut.kind !== "none") out.push(["Зачистка", `${withUnit(entryStrip || cut.len, "мм")}${cut.kind === "tin" ? " (лужение)" : ""}`]);
  } else if (entryStrip || strip[key]) {
    out.push(["Зачистка", withUnit(entryStrip || str(key === "a" ? strip.lenA : strip.lenB), "мм")]);
  }
  // Пайка: жало и температура — на стороне операции (по умолчанию ERP — помечено, технологу уточнить).
  const solder = solderMode(operation);
  if (solder && (operation.side === "B" ? "B" : "A") === end) {
    out.push([solder.method === "bath" ? "Ванна" : "Жало", `${solder.tip}${solder.tipAssumed ? " (по умолчанию)" : ""}`],
      ["Температура", `${solder.temperature}${solder.tolerance ? ` (${solder.tolerance})` : ""}${solder.temperatureAssumed ? " (по умолчанию)" : ""}`]);
  }
  return out;
}

export function etkParams(operation: Row): EtkParams {
  const f = obj(operation.fields), common: EtkParam[] = [];
  const sides = (["A", "B"] as const).map((end) => ({ end, title: `Сторона ${end === "A" ? "А" : "В"}`, items: sideItems(operation, end) }))
    .filter((side) => side.items.length);
  // ЭТК, выданные до норм по концам (`crimpEnds`): аппликатор, высота обжима, отрыв — как были, в «Общих».
  if (!etkCrimpEnds(f).length) {
    if (f.applicator) common.push(["Аппликатор", crimpText(f.applicator)]);
    if ("crimpHeight" in f) common.push(["Высота обжима", f.crimpHeight ? `${crimpText(f.crimpHeight)} мм` : ETK_NOT_SET]);
    if (f.pullForce) common.push(["Отрыв", `${crimpText(f.pullForce)} Н`]);
  }
  // Партия резки — провода разной длины: все длины, а не первая.
  const lengths = [...new Set(list(f.wires).map((wire) => Number(wire.length)).filter((n) => n > 0))].sort((a, b) => a - b);
  if (lengths.length > 1) common.push(["Длина по КД", `${lengths.join(", ")} мм`]);
  else if (f.lengthKd) common.push(["Длина по КД", str(f.lengthKd)]);
  if (f.cutTolerance) common.push(["Допуск реза", etkTolerance(f.cutTolerance)]);
  // Диаметры провода приходят из БД.ПРОВ при сборке ЭТК (techcard_build.enrich_wire_diameters); нет в базе — «—», не выдумываем.
  const wires = list(f.wires), isWire = wires.length > 0 || /^cutWire/.test(str(operation.type)) || etkCrimpEnds(f).length > 0;
  if (isWire) {
    const outer = firstText(wires, ["outerDiameter", "outerDiameterMm", "outer"]) || str(f.outerDiameter).trim();
    const core = firstText(wires, ["coreDiameter", "conductorDiameter", "conductorDiameterMm"]) || str(f.coreDiameter).trim();
    const section = etkCrimpEnds(f).map((entry) => entry.section?.trim()).find(Boolean) || firstText(wires, ["section"]);
    // Провода партии разного сечения: диаметры — строкой на марку провода (сечение — из нормы обжима той же марки).
    const marks = new Map<string, Row>();
    wires.forEach((wire) => {
      const art = str(wire.art || wire.name).trim();
      const entry = etkCrimpEnds(f).find((item) => item.wire && art.startsWith(item.wire));
      const sub = entry?.section || entry?.wire || art;
      if (sub && !marks.has(sub)) marks.set(sub, wire);
    });
    const perMark = (keys: string[]) => [...marks].map(([sub, wire]): [string, string] => {
      const value = firstText([wire], keys);
      return [sub, value ? `${crimpText(value)} мм` : DASH];
    });
    const differs = (rows: Array<[string, string]>) => new Set(rows.map(([, value]) => value)).size > 1;
    const outerRows = perMark(["outerDiameter", "outerDiameterMm", "outer"]);
    const coreRows = perMark(["coreDiameter", "conductorDiameter", "conductorDiameterMm"]);
    const joined = (rows: Array<[string, string]>) => rows.map(([sub, value]) => `${sub}: ${value}`).join("; ");
    common.push(differs(outerRows) ? ["Внешний диаметр", joined(outerRows), outerRows] : ["Внешний диаметр", outer ? `${crimpText(outer)} мм` : DASH],
      differs(coreRows) ? ["Диаметр жилы", joined(coreRows), coreRows]
        : core || !section ? ["Диаметр жилы", core ? `${crimpText(core)} мм` : DASH] : ["Сечение жилы", section]);
  }
  // Ручная сборка: сколько раз операция на одно изделие, параметры и примечание технолога.
  if (f.perItem) common.push(["На изделие", `${str(f.perItem)} шт.`]);
  if (f.params) common.push(["Параметры", str(f.params)]);
  if (f.note) common.push(["Примечание", str(f.note)]);
  if (f.twist) {
    const twist = obj(f.twist);
    common.push(["Шаг свивки", twist.pitch ? `${str(twist.pitch)} мм` : "по КД"], ["Пар", str(twist.count)]);
  }
  const times = obj(operation.times);
  const sec = (v: unknown) => Number(str(v).replace(",", ".")) || 0;
  const fmt = (x: number) => String(Math.round(x * 100) / 100).replace(".", ",");
  const op = sec(times.operation_seconds), prep = sec(times.setup_seconds);
  common.push(["Время", `${op ? `${fmt(op)} с на шт.` : DASH} · подгот. ${prep ? `${fmt(prep / 60)} мин` : DASH}`]);
  return { sides, common };
}
