// Эскизы электронной ТК строкой SVG — порт SUPPLYMAP PortalApp.html etkDraw* / etkHousing_ / etkChips (renderEtk_).
// Одни и те же строки идут и на экран (блок «Эскиз»), и в печать (etkPrint.ts): печатное окно — отдельный
// документ без React, поэтому рисунок — готовая разметка. Геометрия и подписи — как в SUPPLYMAP; разъёмы — рисунками
// серий из connectorArt.ts (владелец 30.09: одна общая иконка на все разъёмы непонятна).
import { connectorFace, connectorFaceUnits, connectorPicture, connectorSummary, wireColor, type HousingBody } from "./connectorArt";
import { wireEndArt } from "./etkDefectArt";

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
export type CutEnd = { kind: CutEndKind; len: string; terminal: string };

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
    return { kind: k, len: str(key === "a" ? st.lenA : st.lenB).trim(),
      terminal: k === "crimp" ? terminals[key === "b" && terminals.length > 1 ? 1 : 0] || "" : "" };
  };
  return { a: end("a"), b: end("b") };
}

/** Зачистка конца: «A 1,2 мм», длины нет — «?»; с пометкой — лужёный «B 3 мм (лужение)» (в эскизе это подпись под проводом). */
function endStrip(key: "A" | "B", end: CutEnd, note = false): string {
  return `${key} ${mm(end.len) || "?"}${note && end.kind === "tin" ? " (лужение)" : ""}`;
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
      return { ins: at(18), tip: at(52), zone: [at(20), at(52)], attach, dir };
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

/** Разделка коаксиала: ступени от торца, L1–L3 из базы коакса (etkDrawCoax_). */
export function etkDrawCoax(operation: Row): string {
  const c = obj(obj(operation.fields).coax);
  if (!Object.keys(c).length) return "";
  const L = [num(c.l1), num(c.l2), num(c.l3)];
  if (!(L[0] > 0 && L[1] > L[0] && L[2] > L[1])) {
    return `<div class="tr-none">Размеров разделки нет в базе коакса для ${esc(c.connector || c.cable)}.</div>`;
  }
  const W = 640, x0 = 40, span = 400, k = span / L[2], cy = 104;
  const xs = [x0, x0 + L[0] * k, x0 + L[1] * k, x0 + L[2] * k];
  const h = [8, 22, 28, 36];
  let body = `<rect x="${xs[3]}" y="${cy - h[3] / 2}" width="${W - 30 - xs[3]}" height="${h[3]}" rx="4" fill="#222"/>` +
    `<text x="${(xs[3] + W - 30) / 2}" y="${cy + 4}" text-anchor="middle" font-size="12" fill="#fff">${esc(c.cable || "кабель")}</text>` +
    `<rect x="${xs[2]}" y="${cy - h[2] / 2}" width="${xs[3] - xs[2]}" height="${h[2]}" fill="url(#etkBraid)" stroke="#8d9297"/>` +
    `<rect x="${xs[1]}" y="${cy - h[1] / 2}" width="${xs[2] - xs[1]}" height="${h[1]}" fill="#fbfbf8" stroke="#17272b" stroke-width=".8"/>` +
    `<rect x="${xs[0]}" y="${cy - h[0] / 2}" width="${xs[1] - xs[0]}" height="${h[0]}" fill="#e08a2c" stroke="#17272b" stroke-width=".8"/>`;
  [1, 2, 3].forEach((i) => {
    const y = cy - 30 - (i - 1) * 24;
    body += `<line x1="${xs[i]}" y1="${y - 4}" x2="${xs[i]}" y2="${cy - h[i] / 2}" stroke="#9aa7ad" stroke-dasharray="3 3"/>` +
      dim(x0, xs[i], y, `L${i} = ${mm(c[`l${i}`])}`);
  });
  body += `<line x1="${x0}" y1="${cy - 84}" x2="${x0}" y2="${cy + 20}" stroke="#9aa7ad" stroke-dasharray="3 3"/>`;
  const ds = ["d1", "d2", "d3"].filter((key) => c[key]).map((key) => `${key.toUpperCase()} = ${mm(c[key])}`);
  if (ds.length) body += `<text x="${x0}" y="${cy + 42}" font-size="12" fill="#5f7470">Ø: ${esc(ds.join(" · "))}</text>`;
  return svg(W, cy + 52, body);
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

/** Монтаж в разъём: торец со стороны проводов крупно, рядом вид корпуса, ниже легенда «конт. — провод»
 *  (etkDrawHousingOp_). Без распиновки в генераторе — список проводов «контакт по КД». */
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
  let ly = top + Math.max(g.h + 6, pic.h + 18) + 22;
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
  return etkDrawWires(operation) || etkDrawCoax(operation) || (operation.type !== "prsTerm" ? etkDrawPinout(operation) : "") ||
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

/** Чипы полей операции над ходом операции (renderEtk_ chips): аппликатор, высота обжима, отрыв, длина, допуск,
 * зачистка, свивка и время из БД.ОП (на штуку — с, подготовка — мин). */
export function etkChips(operation: Row): Array<[string, string]> {
  const f = obj(operation.fields), out: Array<[string, string]> = [];
  if (f.applicator) out.push(["Аппликатор", str(f.applicator)]);
  if ("crimpHeight" in f) out.push(["Высота обжима", f.crimpHeight ? `${str(f.crimpHeight)} мм` : "⚠ нет данных"]);
  if (f.pullForce) out.push(["Отрыв", `${str(f.pullForce)} Н`]);
  if (f.lengthKd) out.push(["Длина по КД", str(f.lengthKd)]);
  if (f.cutTolerance) out.push(["Допуск реза", etkTolerance(f.cutTolerance)]);
  // Ручная сборка: сколько раз операция на одно изделие, параметры и примечание технолога.
  if (f.perItem) out.push(["На изделие", `${str(f.perItem)} шт.`]);
  if (f.params) out.push(["Параметры", str(f.params)]);
  if (f.note) out.push(["Примечание", str(f.note)]);
  const strip = obj(f.strip);
  // У резки концы — по операции (лужёный конец зачищен всегда); у прочих — как в поле зачистки.
  const ends = /^cutWire/.test(str(operation.type)) ? etkCutEnds(operation) : null;
  const shown = (key: "a" | "b") => ends ? ends[key].kind !== "none" : !!strip[key];
  if (shown("a") || shown("b")) {
    const end = (key: "a" | "b"): CutEnd => ends ? ends[key] : { kind: "strip", len: str(key === "a" ? strip.lenA : strip.lenB).trim(), terminal: "" };
    out.push(["Зачистка", [shown("a") ? endStrip("A", end("a"), true) : "", shown("b") ? endStrip("B", end("b"), true) : ""].filter(Boolean).join(" · ")]);
  }
  if (f.twist) {
    const twist = obj(f.twist);
    out.push(["Шаг свивки", twist.pitch ? `${str(twist.pitch)} мм` : "по КД"], ["Пар", str(twist.count)]);
  }
  const times = obj(operation.times);
  const sec = (v: unknown) => Number(str(v).replace(",", ".")) || 0;
  const fmt = (x: number) => String(Math.round(x * 100) / 100).replace(".", ",");
  const op = sec(times.operation_seconds), prep = sec(times.setup_seconds);
  out.push(["Время", `${op ? `${fmt(op)} с на шт.` : "—"} · подгот. ${prep ? `${fmt(prep / 60)} мин` : "—"}`]);
  return out;
}
