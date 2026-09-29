// Эскизы электронной ТК строкой SVG — порт SUPPLYMAP PortalApp.html etkDraw* / etkHousing_ / etkChips (renderEtk_).
// Одни и те же строки идут и на экран (блок «Эскиз»), и в печать (etkPrint.ts): печатное окно — отдельный
// документ без React, поэтому рисунок — готовая разметка. Геометрия и подписи — как в SUPPLYMAP, без доработок.

type Row = Record<string, unknown>;

export type EtkHousing = {
  art?: string; title?: string; pitch?: number | null; rows: number; cols: number; n?: number;
  kind?: string; color: string; latch?: string; cavity?: string; labels?: string[] | null;
  grid: number[][]; round?: Array<{ pin: number; x: number; y: number }> | null; conf?: string; note?: string;
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

const WIRE_COLORS: Array<[RegExp, string]> = [
  [/желт/i, "#f2c200"], [/зел[её]н/i, "#2e9e4f"], [/черн/i, "#222"], [/красн/i, "#d7322d"], [/бел/i, "#f4f4f4"],
  [/син/i, "#2463c7"], [/голуб/i, "#5bb4e5"], [/оранж/i, "#f08a1c"], [/коричн/i, "#7a4a24"], [/сер/i, "#8d9297"], [/фиолет/i, "#7b4bb3"],
];

/** SUPPLYMAP etkWireColor_: цвет жилы по слову в наименовании, иначе серо-синий. */
export function etkWireColor(name: unknown): string {
  const text = str(name);
  for (const [pattern, color] of WIRE_COLORS) if (pattern.test(text)) return color;
  return "#6b7c86";
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

/** Провода резки: полоса на провод, зачищенные концы медью, длина с допуском размером (etkDrawWires_). */
export function etkDrawWires(operation: Row): string {
  const f = obj(operation.fields), ws = list(f.wires).slice(0, 8);
  if (!ws.length || !/^cutWire/.test(str(operation.type))) return "";
  const st = obj(f.strip), W = 640, x0 = 40, x1 = W - 40, rowH = 26, top = 34;
  let body = "";
  ws.forEach((w, i) => {
    const y = top + i * rowH, c = etkWireColor(w.name || w.art);
    const a = st.a ? 26 : 0, b = st.b ? 26 : 0;
    if (a) body += `<rect x="${x0}" y="${y + 7}" width="${a}" height="6" fill="#c87533"/>`;
    if (b) body += `<rect x="${x1 - b}" y="${y + 7}" width="${b}" height="6" fill="#c87533"/>`;
    body += `<rect x="${x0 + a}" y="${y + 4}" width="${x1 - x0 - a - b}" height="12" rx="3" fill="${c}" stroke="#17272b" stroke-width=".8"/>` +
      `<text x="${(x0 + x1) / 2}" y="${y + 13.5}" text-anchor="middle" font-size="10.5" fill="${LIGHT_TEXT.test(c) ? "#fff" : "#17272b"}">` +
      `${esc(`${str(w.art || w.name)} × ${str(w.qty)}`)}</text>`;
  });
  const yd = top + ws.length * rowH + 16, L = ws[0].length ? `${str(ws[0].length)} мм` : "по КД";
  body += dim(x0, x1, yd, `L = ${L}${f.cutTolerance ? `  ${str(f.cutTolerance)}` : ""}`);
  if (st.a) body += dim(x0, x0 + 26, top - 10, `A ${mm(st.lenA) || "?"}`);
  if (st.b) body += dim(x1 - 26, x1, top - 10, `B ${mm(st.lenB) || "?"}`);
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
    `<text x="300" y="${cy + 62}" font-size="12" fill="${edge}">H обжима по жиле: ${esc(f.crimpHeight ? `${str(f.crimpHeight)} мм` : "⚠ нет в БД.ТЕР")}</text>` +
    `<text x="24" y="${cy + 62}" font-size="12" fill="${edge}">Отрыв: ${esc(f.pullForce ? `${str(f.pullForce)} Н` : "—")}</text>`;
  return svg(560, cy + 72, body);
}

/** Корпус разъёма по раскладке семейства: вид со стороны проводов (etkHousing_). Отдаёт {w, h, body} от (x, y). */
export function etkHousingSvg(hs: EtkHousing, x: number, y: number, s: number, byPin: Record<number, string> | null) {
  const dark = /^#(1|2|3)/.test(hs.color), hole = dark ? "#6d767b" : "#2f3336", pad = Math.max(3, s * 0.28);
  const latchH = (hs.latch !== "ramp" && hs.latch !== "lever") ? 0 : Math.max(4, s * 0.42);
  const W = hs.cols * s + pad * 2, H = hs.rows * s + pad * 2, top = y + latchH;
  const edge = dark ? "#000" : "#8a7d5c", fs = Math.max(6.5, s * 0.34);
  let body = "";
  if (hs.kind === "lug") {
    const w0 = byPin && byPin[1];
    body += `<rect x="${x}" y="${y + s * 0.3}" width="${s * 1.3}" height="${s * 0.5}" rx="${s * 0.1}" fill="#d9d9d9" stroke="#17272b" stroke-width=".8"/>` +
      `<circle cx="${x + s * 1.9}" cy="${y + s * 0.55}" r="${s * 0.55}" fill="#e8c35a" stroke="#17272b" stroke-width=".8"/>` +
      `<circle cx="${x + s * 1.9}" cy="${y + s * 0.55}" r="${s * 0.22}" fill="#fff" stroke="#17272b" stroke-width=".6"/>` +
      (w0 ? `<rect x="${x - s * 0.9}" y="${y + s * 0.4}" width="${s * 0.9}" height="${s * 0.3}" fill="${etkWireColor(w0)}" stroke="#17272b" stroke-width=".5"/>` : "");
    return { w: s * 2.5, h: s * 1.2, body };
  }
  if (hs.kind === "banana") {
    const wb = byPin && byPin[1];
    body += (wb ? `<rect x="${x - s * 0.9}" y="${y + s * 0.4}" width="${s * 0.9}" height="${s * 0.3}" fill="${etkWireColor(wb)}" stroke="#17272b" stroke-width=".5"/>` : "") +
      `<rect x="${x}" y="${y + s * 0.15}" width="${s * 1.5}" height="${s * 0.8}" rx="${s * 0.15}" fill="${hs.color}" stroke="#17272b" stroke-width=".8"/>` +
      `<rect x="${x + s * 1.5}" y="${y + s * 0.4}" width="${s * 1.1}" height="${s * 0.3}" rx="${s * 0.12}" fill="#d9d9d9" stroke="#17272b" stroke-width=".7"/>` +
      `<ellipse cx="${x + s * 2.05}" cy="${y + s * 0.55}" rx="${s * 0.3}" ry="${s * 0.21}" fill="#d9d9d9" stroke="#17272b" stroke-width=".7"/>`;
    return { w: s * 2.7, h: s * 1.2, body };
  }
  if (hs.kind === "round" && hs.round) {
    const R = s * 2.2, ccx = x + R + 2, ccy = y + R + 2, pr = Math.max(2.5, s * 0.36);
    body += `<circle cx="${ccx}" cy="${ccy}" r="${R}" fill="${hs.color}" stroke="#000" stroke-width="1.2"/>` +
      `<rect x="${ccx - s * 0.3}" y="${ccy - R - 1}" width="${s * 0.6}" height="${s * 0.45}" fill="#fff" stroke="#000" stroke-width="1"/>`;
    hs.round.forEach((c) => {
      const px = ccx + c.x * R, py = ccy + c.y * R, wn = byPin && byPin[c.pin], wc = wn ? etkWireColor(wn) : "#c8a24a";
      body += `<circle cx="${px}" cy="${py}" r="${pr}" fill="${wc}" stroke="#e9eef0" stroke-width="1"/>`;
      if (s >= 12) body += `<text x="${px}" y="${py - pr - 2}" text-anchor="middle" font-size="${fs}" fill="#e9eef0">${c.pin}</text>`;
    });
    return { w: R * 2 + 4, h: R * 2 + 4 + 6, body };
  }
  if (latchH) {
    const lw = hs.latch === "lever" ? W * 0.62 : Math.min(W * 0.5, s * 1.6), lx = x + (W - lw) / 2;
    body += hs.latch === "lever"
      ? `<path d="M${lx},${top + 1} L${lx},${y + 1} L${lx + lw},${y + 1} L${lx + lw},${top + 1}" fill="${hs.color}" stroke="${edge}" stroke-width="1"/>` +
        `<line x1="${lx + lw * 0.2}" y1="${y + latchH * 0.55}" x2="${lx + lw * 0.8}" y2="${y + latchH * 0.55}" stroke="${edge}" stroke-width=".7"/>`
      : `<path d="M${lx},${top + 1} L${lx + lw * 0.2},${y} L${lx + lw * 0.8},${y} L${lx + lw},${top + 1} z" fill="${hs.color}" stroke="${edge}" stroke-width="1"/>`;
  }
  body += `<rect x="${x}" y="${top}" width="${W}" height="${H}" rx="${Math.max(2, s * 0.18)}" fill="${hs.color}" stroke="${edge}" stroke-width="1.2"/>`;
  hs.grid.forEach((row, r) => {
    row.forEach((pin, c) => {
      if (!pin) return;
      const cx = x + pad + c * s + s / 2, cy = top + pad + r * s + s / 2, hw = s * 0.4, wn = byPin && byPin[pin];
      body += hs.cavity === "round"
        ? `<circle cx="${cx}" cy="${cy}" r="${hw}" fill="${hole}"/>`
        : `<rect x="${cx - hw}" y="${cy - hw}" width="${hw * 2}" height="${hw * 2}" rx="${s * 0.08}" fill="${hole}"/>`;
      const label = hs.labels ? (hs.labels[pin - 1] == null ? pin : hs.labels[pin - 1]) : pin;
      if (wn) {
        const wc = etkWireColor(wn);
        body += `<circle cx="${cx}" cy="${cy}" r="${hw * 0.86}" fill="${wc}" stroke="#e9eef0" stroke-width="1"/>` +
          `<text x="${cx}" y="${cy + fs * 0.36}" text-anchor="middle" font-size="${fs}" font-weight="700" fill="` +
            `${/#(222|2463c7|7a4a24|7b4bb3|d7322d|2e9e4f|6b7c86)/.test(wc) ? "#fff" : "#17272b"}">${esc(label)}</text>`;
      } else if (s >= 14) {
        body += `<text x="${cx}" y="${cy + fs * 0.36}" text-anchor="middle" font-size="${fs}" fill="#e9eef0">${esc(label)}</text>`;
      }
      if (pin === 1 && !hs.labels) {
        const tw = Math.max(3, s * 0.2), last = c === hs.cols - 1;
        if (last || c === 0) {
          const tx = last ? x + W + 1 : x - 1, d = last ? 1 : -1;
          body += `<path d="M${tx + d * tw * 1.5},${cy - tw} L${tx + d * tw * 1.5},${cy + tw} L${tx},${cy} z" fill="#d7322d"/>`;
        } else {
          const ty = r === 0 ? top - 1 : top + H + 1, dir = r === 0 ? 1 : -1;
          body += `<path d="M${cx - tw},${ty - dir * tw * 1.4} L${cx + tw},${ty - dir * tw * 1.4} L${cx},${ty} z" fill="#d7322d"/>`;
        }
      }
    });
  });
  return { w: W, h: H + latchH + 6, body };
}

function housingCaption(hs: EtkHousing): string {
  return esc(`${str(hs.art)} · ${str(hs.title)}${hs.pitch ? `, шаг ${String(hs.pitch).replace(".", ",")} мм` : ""}` +
    (hs.kind === "lug" || hs.kind === "banana" ? "" : ` · вид со стороны проводов${hs.latch === "ramp" || hs.latch === "lever" ? ", защёлка сверху" : hs.latch === "mark" ? ", метка ▽ сверху" : ""}`));
}

function housingWarn(hs: EtkHousing): string {
  return hs.conf === "high" ? "" : `⚠ нумерация не сверена с чертежом производителя${hs.note ? ` — ${hs.note}` : ""}`;
}

/** Монтаж в разъём: корпус крупно + легенда «конт. — провод» (etkDrawHousingOp_). */
export function etkDrawHousingOp(operation: Row): string {
  const hs = operation.housing as EtkHousing, byPin: Record<number, string> = {}, ws = list(obj(operation.fields).wires);
  ws.forEach((w) => (Array.isArray(w.pins) ? w.pins as number[] : []).forEach((p) => { byPin[p] = str(w.art || w.name); }));
  const warn = housingWarn(hs), W = 640, s = Math.max(16, Math.min(44, 520 / Math.max(hs.cols, 1))), top = warn ? 46 : 30;
  const g = etkHousingSvg(hs, 0, 0, s, byPin), x0 = Math.max(20, (W - g.w) / 2);
  let body = `<text x="20" y="16" font-size="11" fill="#5f7470">${housingCaption(hs)}</text>` +
    (warn ? `<text x="20" y="32" font-size="10.5" fill="#b3261e">${esc(warn.length > 110 ? `${warn.slice(0, 109)}…` : warn)}</text>` : "") +
    `<g transform="translate(${x0},${top})">${g.body}</g>`;
  let ly = top + g.h + 22;
  const pins = Object.keys(byPin).map(Number).sort((a, b) => a - b);
  if (pins.length) {
    pins.forEach((pin, i) => {
      const cx = 40 + (i % 2) * 300, cy = ly + Math.floor(i / 2) * 17;
      body += `<rect x="${cx}" y="${cy - 9}" width="12" height="12" fill="${etkWireColor(byPin[pin])}" stroke="#17272b" stroke-width=".6"/>` +
        `<text x="${cx + 18}" y="${cy + 1}" font-size="11" fill="#17272b">конт. ${pin} — ${esc(byPin[pin])}</text>`;
    });
    ly += Math.ceil(pins.length / 2) * 17;
  } else {
    body += `<text x="40" y="${ly}" font-size="11" fill="#5f7470">Распиновка в генераторе не задана — контакт каждого провода по КД:</text>`;
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
  const ly = y0 + cw + 20, colW = 300;
  used.forEach((pin, i) => {
    const cx = 40 + (i % 2) * colW, cy = ly + Math.floor(i / 2) * 17, w = byPin[pin];
    body += `<rect x="${cx}" y="${cy - 9}" width="12" height="12" fill="${etkWireColor(w.art || w.name)}" stroke="#17272b" stroke-width=".6"/>` +
      `<text x="${cx + 18}" y="${cy + 1}" font-size="11" fill="#17272b">конт. ${pin} — ${esc(w.art || w.name)}</text>`;
  });
  return svg(W, ly + Math.ceil(used.length / 2) * 17 + 6, body);
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

/** Схема жгута: разъёмы по слоям слева направо, связи — пучок жил, число проводов и длина (etkDrawHarness_). */
export function etkDrawHarness(topo: EtkTopologyData | null | undefined): string {
  const nodes = topo?.nodes ?? [];
  if (nodes.length < 2) return "";
  const cols: Record<number, typeof nodes> = {};
  nodes.forEach((n) => { (cols[n.depth || 0] = cols[n.depth || 0] || []).push(n); });
  const depths = Object.keys(cols).map(Number).sort((a, b) => a - b);
  const maxRows = Math.max(...depths.map((d) => cols[d].length));
  const withHs = nodes.some((n) => n.housing);
  const bw = 160, bh = withHs ? 84 : 38, gapY = 26, colW = depths.length > 1 ? Math.min(300, 620 / (depths.length - 1)) : 0;
  const span = (depths.length - 1) * colW + bw, W = Math.max(640, span + 80), x0 = (W - span) / 2, H = 30 + maxRows * (bh + gapY);
  const pos: Record<string, { x: number; y: number }> = {};
  depths.forEach((d, ci) => {
    const col = cols[d], top = (H - col.length * (bh + gapY) + gapY) / 2;
    col.forEach((n, i) => { pos[n.id] = { x: x0 + ci * colW, y: top + i * (bh + gapY) }; });
  });
  let body = "";
  (topo?.edges ?? []).forEach((e) => {
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) return;
    const x1 = a.x + bw, y1 = a.y + bh / 2, x2 = b.x, y2 = b.y + bh / 2, mx = (x1 + x2) / 2;
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
    let ty = p.y + bh - 22;
    body += `<rect x="${p.x}" y="${p.y}" width="${bw}" height="${bh}" rx="7" fill="#e0efed" stroke="#00706a" stroke-width="1.2"/>`;
    if (n.housing) {
      const hs = n.housing, s = Math.max(4, Math.min(12, 136 / Math.max(hs.cols, 1), 40 / Math.max(hs.rows, 1)));
      const g = etkHousingSvg(hs, 0, 0, s, null);
      body += `<g transform="translate(${p.x + (bw - g.w) / 2},${p.y + Math.max(4, (bh - 26 - g.h) / 2 + 2)})">${g.body}</g>`;
    } else if (n.kind === "splice") {
      const cx = p.x + bw / 2, cy = p.y + (bh - 26) / 2 + 4;
      body += `<line x1="${cx - 44}" y1="${cy}" x2="${cx + 44}" y2="${cy}" stroke="#6b7c86" stroke-width="4" stroke-linecap="round"/>` +
        `<rect x="${cx - 20}" y="${cy - 8}" width="40" height="16" rx="4" fill="#d9d9d9" stroke="#17272b" stroke-width="1"/>` +
        `<line x1="${cx - 8}" y1="${cy - 8}" x2="${cx - 8}" y2="${cy + 8}" stroke="#17272b"/>` +
        `<line x1="${cx + 8}" y1="${cy - 8}" x2="${cx + 8}" y2="${cy + 8}" stroke="#17272b"/>`;
      if (!withHs) ty = p.y + bh - 8;
    } else ty = p.y + 16;
    body += `<text x="${p.x + bw / 2}" y="${ty}" text-anchor="middle" font-size="11.5" font-weight="700" fill="#00554f">` +
        `${esc(name.length > 24 ? `${name.slice(0, 23)}…` : name)}</text>` +
      `<text x="${p.x + bw / 2}" y="${ty + 13}" text-anchor="middle" font-size="10" fill="#5f7470">` +
        `${esc(cut(`${n.housing ? `${str(n.housing.title)} · ` : ""}${n.side ? `сторона ${n.side === "B" ? "В" : "А"}` : ""}`, 30))}</text>`;
  });
  return svg(W, H, body);
}

/** Чипы полей операции над ходом операции (renderEtk_ chips): аппликатор, H обжима, отрыв, длина, допуск,
 * зачистка, свивка и время из БД.ОП (на штуку — с, подготовка — мин). */
export function etkChips(operation: Row): Array<[string, string]> {
  const f = obj(operation.fields), out: Array<[string, string]> = [];
  if (f.applicator) out.push(["Аппликатор", str(f.applicator)]);
  if ("crimpHeight" in f) out.push(["H обжима", f.crimpHeight ? `${str(f.crimpHeight)} мм` : "⚠ нет данных"]);
  if (f.pullForce) out.push(["Отрыв", `${str(f.pullForce)} Н`]);
  if (f.lengthKd) out.push(["Длина по КД", str(f.lengthKd)]);
  if (f.cutTolerance) out.push(["Допуск реза", str(f.cutTolerance)]);
  const strip = obj(f.strip);
  if (strip.a || strip.b) {
    out.push(["Зачистка", [strip.a ? `A ${mm(strip.lenA) || "?"}` : "", strip.b ? `B ${mm(strip.lenB) || "?"}` : ""].filter(Boolean).join(" · ")]);
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
