// Вид сверху корпуса разъёма — чертёжный вид «как у производителя» (владелец, 16.10.2026: «вид сверху, чтобы можно было
// в чертежи тащить», образец — чертёж Molex: контур с закруглёнными углами, защёлка по центру, окна контактов вдоль
// ширины, размеры шаг / A / B, «контакт 1»; «вход в разъём снизу»). Стыковочная сторона — сверху, вход проводов — снизу.
// Всё считается из тех же данных корпуса (`ConnectorHousing`: шаг, колонки, сетка номеров, защёлка, размеры по чертежу
// `body`), без рисунков под артикул. Размеры в мм; ширина B ставится со знаком «≈», если корпус не сверен с чертежом
// (ширина = шаг × (колонок − 1) + стенки по правилу семейства). Это тонкая линейная графика без заливки: годится и на
// листе чертежа, и в карточке. Круглые, разъёмы-«бочки», D-sub и лепестки вида сверху не получают — null (там остаётся торец).
import {
  EDGE, bodyColumns, bodyWidth, connectorLook, f, faceSize, facePitch, lockWidth,
  type Box, type ConnectorHousing,
} from "./connectorArt";

export type TopView = Box & { widthMm: number; depthMm: number; estimated: boolean };

const comma = (n: number) => String(Math.round(n * 100) / 100).replace(".", ",");
const LINE = 0.8;
const THIN = 0.55;

/** Есть ли у серии вид сверху: прямоугольные корпуса (в том числе XT и RJ45). */
export function hasTopView(hs: ConnectorHousing): boolean {
  const shape = connectorLook(hs).shape;
  return (shape === "rect" || shape === "xt" || shape === "rj45") && hs.cols >= 1 && hs.grid.length > 0;
}

function line(x1: number, y1: number, x2: number, y2: number, width = THIN, extra = ""): string {
  return `<line x1="${f(x1)}" y1="${f(y1)}" x2="${f(x2)}" y2="${f(y2)}" stroke="${EDGE}" stroke-width="${width}"${extra}/>`;
}

function text(x: number, y: number, value: string, size = 11, anchor = "middle", extra = ""): string {
  return `<text x="${f(x)}" y="${f(y)}" font-size="${size}" font-family="Arial, sans-serif" text-anchor="${anchor}" fill="${EDGE}"${extra}>${value}</text>`;
}

/** Горизонтальный размер со стрелками и выносными линиями от y0 вверх до линии размера на y. */
function dimension(x1: number, x2: number, y: number, y0: number, label: string): string {
  const a = 3.2, b = 1.2;
  return line(x1, y0, x1, y - 2, 0.4) + line(x2, y0, x2, y - 2, 0.4) + line(x1, y, x2, y, 0.5) +
    `<path d="M${f(x1 + a)},${f(y - b)} L${f(x1)},${f(y)} L${f(x1 + a)},${f(y + b)} M${f(x2 - a)},${f(y - b)} L${f(x2)},${f(y)} L${f(x2 - a)},${f(y + b)}" fill="none" stroke="${EDGE}" stroke-width=".5"/>` +
    text((x1 + x2) / 2, y - 3, label);
}

/** Вид сверху, вписанный в maxW × maxH; `dims: false` — без размеров и подписей (миниатюра в списке серий). */
export function connectorTopView(hs: ConnectorHousing, maxW: number, maxH: number, dims = true): TopView | null {
  if (!hasTopView(hs)) return null;
  const look = connectorLook(hs), b = hs.body || null;
  const pitch = look.shape === "xt" ? facePitch(hs, look) : hs.pitch || 2.5;
  const estimated = !b;
  const W = b ? bodyWidth(hs, b, pitch) : faceSize(hs, look, pitch)[0];
  const L = b ? b.len : look.depth;
  const ears = b?.ears || 0;
  const centers = b ? bodyColumns(hs, b, pitch)
    : Array.from({ length: hs.cols }, (_, c) => W / 2 + (c - (hs.cols - 1) / 2) * pitch);
  const m = dims ? 28 : 3, top = dims ? 6 + 15 * (hs.cols > 2 ? 3 : 2) + 6 : 3, bottom = dims ? 58 : 3;
  const k = Math.max(1, Math.min((maxW - 2 * m) / W, (maxH - top - bottom) / L, 40));
  const x0 = m + (dims ? Math.max(0, (150 - (W * k + 2 * m)) / 2) : 0), y0 = top, w = W * k, h = L * k, sx = (mm: number) => x0 + mm * k;
  let g = "";

  // Контур: корпус с закруглёнными углами, выступы (фланцы) по бокам у входа проводов.
  const r = Math.min(1.2 * k, 4, w / 6);
  if (ears) {
    const el = (b?.ear_len || L * 0.3) * k, ew = ears * k;
    g += `<path d="M${f(x0 + ew + r)},${f(y0)} H${f(x0 + w - ew - r)} q${f(r)},0 ${f(r)},${f(r)} V${f(y0 + h - el)} H${f(x0 + w)} V${f(y0 + h)} H${f(x0)} V${f(y0 + h - el)} H${f(x0 + ew)} V${f(y0 + r)} q0,${f(-r)} ${f(r)},${f(-r)} z" fill="#fff" stroke="${EDGE}" stroke-width="${LINE}" stroke-linejoin="round"/>`;
  } else {
    g += `<rect x="${f(x0)}" y="${f(y0)}" width="${f(w)}" height="${f(h)}" rx="${f(r)}" fill="#fff" stroke="${EDGE}" stroke-width="${LINE}"/>`;
  }
  // Окна контактов сверху: по колонкам, вдоль оси провода; нижний ряд 2-рядного корпуса скрыт под верхним.
  const [winW, winL] = b?.win && b.win.length === 2 ? [b.win[0], L * 0.76] : [Math.min(pitch * 0.62, 3), L * 0.76];
  const cw = Math.max(1.5, winW * k), cl = winL * k, cy = y0 + h * 0.12;
  centers.forEach((cx) => {
    g += `<rect x="${f(sx(cx) - cw / 2)}" y="${f(cy)}" width="${f(cw)}" height="${f(cl)}" rx="${f(Math.min(cw / 3, 1.5))}" fill="none" stroke="${EDGE}" stroke-width="${THIN}" stroke-dasharray="2.2 1.6"/>`;
  });
  // Защёлка по центру: язычок с зацепом у стыковочного торца.
  const latch = hs.latch || "none";
  if (latch === "lever" || latch === "ramp") {
    const lw = (b ? lockWidth(b, W) : 0) || Math.min(W * (latch === "lever" ? 0.42 : 0.34), latch === "lever" ? 6 : 4), lx = x0 + w / 2 - (lw * k) / 2;
    g += `<rect x="${f(lx)}" y="${f(y0 + h * 0.06)}" width="${f(lw * k)}" height="${f(h * 0.5)}" rx="1.2" fill="#fff" stroke="${EDGE}" stroke-width="${LINE}"/>` +
      line(lx, y0 + h * 0.2, lx + lw * k, y0 + h * 0.2, THIN) + line(lx + lw * k * 0.5, y0 + h * 0.06, lx + lw * k * 0.5, y0 + h * 0.2, THIN);
  } else if (latch === "mark") {
    const col = pinOne(hs)[1], mx = sx(centers[col] ?? W / 2), t = Math.max(2, Math.min(pitch * k * 0.3, 5));
    g += `<path d="M${f(mx - t)},${f(y0 + 1)} L${f(mx + t)},${f(y0 + 1)} L${f(mx)},${f(y0 + 1 + t * 1.2)} z" fill="none" stroke="${EDGE}" stroke-width="${THIN}"/>`;
  } else if (latch === "windows") {
    for (const side of [0, 1]) g += line(x0 + side * w, y0 + h * 0.62, x0 + side * w + (side ? -3 : 3), y0 + h * 0.62, LINE);
  } else if (latch === "friction") {
    for (const side of [0, 1]) g += `<circle cx="${f(x0 + side * w)}" cy="${f(y0 + h * 0.55)}" r="1.6" fill="none" stroke="${EDGE}" stroke-width="${THIN}"/>`;
  }

  if (dims) {
    const [row, col] = pinOne(hs), px = sx(centers[col] ?? W / 2), n = hs.cols;
    let dy = y0 - 13;
    if (n >= 2) { g += dimension(sx(centers[0]), sx(centers[1]), dy, cy, comma(pitch)); dy -= 15; }
    if (n >= 3) { g += dimension(sx(centers[0]), sx(centers[n - 1]), dy, cy, `A ${comma((n - 1) * pitch)}`); dy -= 15; }
    g += dimension(x0, x0 + w, dy, y0, `B ${estimated ? "≈ " : ""}${comma(W)}`);
    // Контакт 1 — выноска снизу к окну; вход проводов — стрелка вверх.
    const yb = y0 + h;
    g += line(px, yb + 11, px, cy + cl, THIN) +
      `<path d="M${f(px - 2)},${f(cy + cl + 3.5)} L${f(px)},${f(cy + cl)} L${f(px + 2)},${f(cy + cl + 3.5)}" fill="none" stroke="${EDGE}" stroke-width=".5"/>` +
      text(Math.min(Math.max(px, 64), 2 * x0 + w - 64), yb + 24, `Конт. 1${hs.rows > 1 && row > 0 ? " (нижний ряд)" : ""}`) +
      text(x0 + w / 2, yb + 38, "вход проводов ▲", 10) + (hs.rows > 1 ? text(x0 + w / 2, yb + 51, `${hs.rows} ряда, нижний скрыт`, 10) : "");
    if (b) { // длина корпуса — только когда она с чертежа
      const xr = x0 + w + 12;
      g += line(x0 + w, y0, xr + 2, y0, 0.4) + line(x0 + w, y0 + h, xr + 2, y0 + h, 0.4) + line(xr, y0, xr, y0 + h, 0.5) +
        text(xr + 4, y0 + h / 2, comma(L), 11, "middle", ` transform="rotate(90 ${f(xr + 4)} ${f(y0 + h / 2)})"`);
    }
  }
  const width = Math.max(2 * x0 + w, 30), height = y0 + h + bottom;
  return { w: width, h: height, body: g, widthMm: W, depthMm: L, estimated };
}

/** Строка/колонка контакта 1 в сетке корпуса; (0, 0), если сетка без единицы. */
function pinOne(hs: ConnectorHousing): [number, number] {
  for (let r = 0; r < hs.grid.length; r++) {
    const c = hs.grid[r].indexOf(1);
    if (c >= 0) return [r, c];
  }
  return [0, 0];
}
