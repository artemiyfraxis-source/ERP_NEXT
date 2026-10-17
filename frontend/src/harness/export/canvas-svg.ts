// Записывающий контекст рисования: те же вызовы, что поле чертежа делает на canvas (линии, дуги, заливки, текст,
// обрезка, пунктир, узоры и картинки), превращаются в векторный SVG. Так лист для печати — векторный и точно
// такой, как на экране, без второй отрисовки чертежа.
//
// Всё в SVG самодостаточно: узоры и картинки лежат как data:-адреса, внешних ссылок нет. Растровые заливки
// (текстуры оболочек из covering-relief) попадают как плитки-картинки.

type Matrix = readonly [number, number, number, number, number, number];
const IDENTITY: Matrix = [1, 0, 0, 1, 0, 0];

const multiply = (m: Matrix, n: Matrix): Matrix => [
  m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1],
  m[0] * n[2] + m[2] * n[3], m[1] * n[2] + m[3] * n[3],
  m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5],
];
const apply = (m: Matrix, x: number, y: number): [number, number] => [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]];
const num = (value: number) => String(Math.round(value * 1000) / 1000);
const matrixAttr = (m: Matrix) => `matrix(${m.map(num).join(" ")})`;
const isIdentity = (m: Matrix) => m.every((value, index) => value === IDENTITY[index]);
/** Средний линейный масштаб матрицы: ширина пера и пунктир считаются в локальных единицах. */
const meanScale = (m: Matrix) => Math.sqrt(Math.abs(m[0] * m[3] - m[1] * m[2]));

export const escapeXml = (value: string) => value
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");

interface MatrixLike { readonly a: number; readonly b: number; readonly c: number; readonly d: number; readonly e: number; readonly f: number }
const fromLike = (m: MatrixLike): Matrix => [m.a, m.b, m.c, m.d, m.e, m.f];

class RecordedGradient {
  readonly stops: { offset: number; color: string }[] = [];
  constructor(readonly x0: number, readonly y0: number, readonly x1: number, readonly y1: number) {}
  addColorStop(offset: number, color: string): void { this.stops.push({ offset, color }); }
}
class RecordedPattern {
  matrix: Matrix = IDENTITY;
  constructor(readonly tile: unknown) {}
  setTransform(matrix?: MatrixLike): void { this.matrix = matrix ? fromLike(matrix) : IDENTITY; }
}

type CanvasImage = { width?: number; height?: number; naturalWidth?: number; naturalHeight?: number; src?: string; toDataURL?: (type?: string) => string };

/** PNG-адрес картинки или её части; null — источник нельзя прочитать (чужой домен, ещё не загружена). */
export function imageDataUrl(source: unknown, crop?: { sx: number; sy: number; sw: number; sh: number }): string | null {
  const image = source as CanvasImage;
  try {
    if (!crop) {
      if (typeof image.toDataURL === "function") return image.toDataURL("image/png");
      if (typeof image.src === "string" && image.src.startsWith("data:")) return image.src;
    }
    if (typeof document === "undefined") return null;
    const scratch = document.createElement("canvas");
    const width = crop?.sw ?? image.naturalWidth ?? image.width ?? 0, height = crop?.sh ?? image.naturalHeight ?? image.height ?? 0;
    if (!(width > 0) || !(height > 0)) return null;
    scratch.width = Math.ceil(width); scratch.height = Math.ceil(height);
    const brush = scratch.getContext("2d");
    if (!brush) return null;
    if (crop) brush.drawImage(source as CanvasImageSource, crop.sx, crop.sy, crop.sw, crop.sh, 0, 0, scratch.width, scratch.height);
    else brush.drawImage(source as CanvasImageSource, 0, 0);
    return scratch.toDataURL("image/png");
  } catch { return null; }
}

interface PenState {
  fillStyle: unknown; strokeStyle: unknown; lineWidth: number; lineCap: string; lineJoin: string; miterLimit: number;
  globalAlpha: number; font: string; textAlign: string; textBaseline: string; lineDash: number[]; lineDashOffset: number;
  ctm: Matrix; groups: number;
}

/**
 * Шрифт canvas («bold 12px "Segoe UI", sans-serif») как атрибуты SVG. Не `style="font:…"`: страница ERP
 * запрещает встроенные стили (CSP `style-src 'self'`), а окно печати наследует это правило.
 */
export function fontAttributes(font: string): string {
  const match = /^\s*((?:(?:normal|italic|oblique|small-caps|bold|bolder|lighter|\d{3})\s+)*)(\d+(?:\.\d+)?)(px|pt)(?:\/\S+)?\s+(.+?)\s*$/.exec(font);
  if (!match) return "";
  const [, modifiers = "", size, unit, family] = match;
  const words = modifiers.split(/\s+/);
  const weight = words.find((word) => /^(bold|bolder|lighter|\d{3})$/.test(word));
  const italic = words.some((word) => word === "italic" || word === "oblique");
  return `font-family="${escapeXml(family!)}" font-size="${num(Number(size) * (unit === "pt" ? 4 / 3 : 1))}"` +
    `${weight ? ` font-weight="${weight}"` : ""}${italic ? ' font-style="italic"' : ""}`;
}

/** Подмножество CanvasRenderingContext2D, которым пользуется отрисовка чертежа, с выводом в SVG. */
export class SvgRecordingContext {
  fillStyle: unknown = "#000000";
  strokeStyle: unknown = "#000000";
  lineWidth = 1;
  lineCap = "butt";
  lineJoin = "miter";
  miterLimit = 10;
  globalAlpha = 1;
  font = "10px sans-serif";
  textAlign = "start";
  textBaseline = "alphabetic";
  lineDashOffset = 0;
  imageSmoothingEnabled = true;
  imageSmoothingQuality = "low";
  readonly canvas: unknown = null;

  private ctm: Matrix = IDENTITY;
  private dash: number[] = [];
  private groups = 0;
  private stack: PenState[] = [];
  private path: string[] = [];
  private current: [number, number] | null = null;
  private subpathStart: [number, number] | null = null;
  private readonly body: string[] = [];
  private readonly defs: string[] = [];
  private nextId = 0;
  private readonly tileUrls = new Map<unknown, string | null>();
  private readonly patternDefs = new Map<string, string>();
  private scratch: CanvasRenderingContext2D | null | undefined;

  // --- состояние ---------------------------------------------------------------------------------------
  save(): void {
    this.stack.push({
      fillStyle: this.fillStyle, strokeStyle: this.strokeStyle, lineWidth: this.lineWidth, lineCap: this.lineCap, lineJoin: this.lineJoin,
      miterLimit: this.miterLimit, globalAlpha: this.globalAlpha, font: this.font, textAlign: this.textAlign, textBaseline: this.textBaseline,
      lineDash: this.dash, lineDashOffset: this.lineDashOffset, ctm: this.ctm, groups: this.groups,
    });
    this.groups = 0;
  }
  restore(): void {
    const saved = this.stack.pop();
    if (!saved) return;
    for (let index = 0; index < this.groups; index++) this.body.push("</g>");
    Object.assign(this, {
      fillStyle: saved.fillStyle, strokeStyle: saved.strokeStyle, lineWidth: saved.lineWidth, lineCap: saved.lineCap, lineJoin: saved.lineJoin,
      miterLimit: saved.miterLimit, globalAlpha: saved.globalAlpha, font: saved.font, textAlign: saved.textAlign, textBaseline: saved.textBaseline,
      dash: saved.lineDash, lineDashOffset: saved.lineDashOffset, ctm: saved.ctm, groups: saved.groups,
    });
  }
  setTransform(a: number | MatrixLike, b?: number, c?: number, d?: number, e?: number, f?: number): void {
    this.ctm = typeof a === "number" ? [a, b!, c!, d!, e!, f!] : fromLike(a);
  }
  resetTransform(): void { this.ctm = IDENTITY; }
  transform(a: number, b: number, c: number, d: number, e: number, f: number): void { this.ctm = multiply(this.ctm, [a, b, c, d, e, f]); }
  translate(x: number, y: number): void { this.transform(1, 0, 0, 1, x, y); }
  scale(x: number, y: number): void { this.transform(x, 0, 0, y, 0, 0); }
  rotate(angle: number): void { const cos = Math.cos(angle), sin = Math.sin(angle); this.transform(cos, sin, -sin, cos, 0, 0); }
  getTransform(): MatrixLike {
    const [a, b, c, d, e, f] = this.ctm;
    return { a, b, c, d, e, f };
  }
  setLineDash(segments: number[]): void { this.dash = segments.length % 2 ? [...segments, ...segments] : [...segments]; }
  getLineDash(): number[] { return [...this.dash]; }

  // --- контур ------------------------------------------------------------------------------------------
  beginPath(): void { this.path = []; this.current = null; this.subpathStart = null; }
  private point(x: number, y: number): string { const [px, py] = apply(this.ctm, x, y); return `${num(px)} ${num(py)}`; }
  moveTo(x: number, y: number): void {
    this.path.push(`M${this.point(x, y)}`);
    this.current = [x, y]; this.subpathStart = [x, y];
  }
  lineTo(x: number, y: number): void {
    if (!this.current) { this.moveTo(x, y); return; }
    this.path.push(`L${this.point(x, y)}`);
    this.current = [x, y];
  }
  closePath(): void {
    if (!this.current) return;
    this.path.push("Z");
    this.current = this.subpathStart;
  }
  quadraticCurveTo(cx: number, cy: number, x: number, y: number): void {
    if (!this.current) this.moveTo(cx, cy);
    this.path.push(`Q${this.point(cx, cy)} ${this.point(x, y)}`);
    this.current = [x, y];
  }
  bezierCurveTo(c1x: number, c1y: number, c2x: number, c2y: number, x: number, y: number): void {
    if (!this.current) this.moveTo(c1x, c1y);
    this.path.push(`C${this.point(c1x, c1y)} ${this.point(c2x, c2y)} ${this.point(x, y)}`);
    this.current = [x, y];
  }
  rect(x: number, y: number, width: number, height: number): void {
    this.moveTo(x, y); this.lineTo(x + width, y); this.lineTo(x + width, y + height); this.lineTo(x, y + height); this.closePath();
    this.current = [x, y];
  }
  /** Эллиптическая дуга кубическими кривыми (не больше четверти оборота на кривую). */
  ellipse(cx: number, cy: number, rx: number, ry: number, rotation: number, start: number, end: number, counterclockwise = false): void {
    let sweep = end - start;
    const full = Math.PI * 2;
    if (!counterclockwise) { if (sweep >= full) sweep = full; else while (sweep < 0) sweep += full; }
    else { if (sweep <= -full) sweep = -full; else while (sweep > 0) sweep -= full; }
    const cosR = Math.cos(rotation), sinR = Math.sin(rotation);
    const at = (t: number): [number, number] => {
      const x = rx * Math.cos(t), y = ry * Math.sin(t);
      return [cx + x * cosR - y * sinR, cy + x * sinR + y * cosR];
    };
    const derivative = (t: number): [number, number] => {
      const x = -rx * Math.sin(t), y = ry * Math.cos(t);
      return [x * cosR - y * sinR, x * sinR + y * cosR];
    };
    const pieces = Math.max(1, Math.ceil(Math.abs(sweep) / (Math.PI / 2) - 1e-9));
    const step = sweep / pieces, k = (4 / 3) * Math.tan(step / 4);
    const [sx, sy] = at(start);
    if (this.current) this.lineTo(sx, sy); else this.moveTo(sx, sy);
    for (let index = 0; index < pieces; index++) {
      const t0 = start + index * step, t1 = t0 + step;
      const [x0, y0] = at(t0), [x1, y1] = at(t1), [dx0, dy0] = derivative(t0), [dx1, dy1] = derivative(t1);
      this.bezierCurveTo(x0 + k * dx0, y0 + k * dy0, x1 - k * dx1, y1 - k * dy1, x1, y1);
    }
  }
  arc(x: number, y: number, radius: number, start: number, end: number, counterclockwise = false): void {
    this.ellipse(x, y, radius, radius, 0, start, end, counterclockwise);
  }
  arcTo(x1: number, y1: number, x2: number, y2: number, radius: number): void {
    if (!this.current) { this.moveTo(x1, y1); return; }
    const [x0, y0] = this.current;
    const ux = x0 - x1, uy = y0 - y1, vx = x2 - x1, vy = y2 - y1;
    const lu = Math.hypot(ux, uy), lv = Math.hypot(vx, vy);
    const cross = ux * vy - uy * vx;
    if (radius <= 0 || lu === 0 || lv === 0 || Math.abs(cross) < 1e-9) { this.lineTo(x1, y1); return; }
    const cos = (ux * vx + uy * vy) / (lu * lv), angle = Math.acos(Math.max(-1, Math.min(1, cos)));
    const tangent = radius / Math.tan(angle / 2);
    const t1x = x1 + (ux / lu) * tangent, t1y = y1 + (uy / lu) * tangent;
    const t2x = x1 + (vx / lv) * tangent, t2y = y1 + (vy / lv) * tangent;
    // Центр лежит на биссектрисе угла на расстоянии radius / sin(angle / 2).
    const bx = ux / lu + vx / lv, by = uy / lu + vy / lv, lb = Math.hypot(bx, by);
    const centre = radius / Math.sin(angle / 2);
    const cx = x1 + (bx / lb) * centre, cy = y1 + (by / lb) * centre;
    const a0 = Math.atan2(t1y - cy, t1x - cx), a1 = Math.atan2(t2y - cy, t2x - cx);
    this.lineTo(t1x, t1y);
    this.arc(cx, cy, radius, a0, a1, cross > 0);
  }

  // --- краски ------------------------------------------------------------------------------------------
  createLinearGradient(x0: number, y0: number, x1: number, y1: number): RecordedGradient { return new RecordedGradient(x0, y0, x1, y1); }
  createPattern(tile: unknown): RecordedPattern { return new RecordedPattern(tile); }

  private tileUrl(tile: unknown): string | null {
    if (!this.tileUrls.has(tile)) this.tileUrls.set(tile, imageDataUrl(tile));
    return this.tileUrls.get(tile)!;
  }
  /** Значение fill/stroke для SVG; `space` — матрица пространства, в котором рисует элемент. */
  private paint(style: unknown, space: Matrix): string {
    if (typeof style === "string") return style;
    if (style instanceof RecordedGradient) {
      const id = `g${this.nextId++}`;
      const stops = style.stops.map((stop) => `<stop offset="${num(stop.offset)}" stop-color="${escapeXml(stop.color)}"/>`).join("");
      this.defs.push(`<linearGradient id="${id}" gradientUnits="userSpaceOnUse" x1="${num(style.x0)}" y1="${num(style.y0)}" x2="${num(style.x1)}" y2="${num(style.y1)}"` +
        `${isIdentity(space) ? "" : ` gradientTransform="${matrixAttr(space)}"`}>${stops}</linearGradient>`);
      return `url(#${id})`;
    }
    if (style instanceof RecordedPattern) {
      const url = this.tileUrl(style.tile);
      const tile = style.tile as CanvasImage;
      const width = tile.width ?? tile.naturalWidth ?? 0, height = tile.height ?? tile.naturalHeight ?? 0;
      if (!url || !(width > 0) || !(height > 0)) return "none";
      const transform = matrixAttr(multiply(space, style.matrix));
      const key = `${url.length}:${url.slice(-48)}|${transform}|${width}x${height}`;
      let id = this.patternDefs.get(key);
      if (!id) {
        id = `p${this.nextId++}`;
        this.patternDefs.set(key, id);
        this.defs.push(`<pattern id="${id}" patternUnits="userSpaceOnUse" width="${num(width)}" height="${num(height)}" patternTransform="${transform}">` +
          `<image href="${url}" width="${num(width)}" height="${num(height)}" preserveAspectRatio="none"/></pattern>`);
      }
      return `url(#${id})`;
    }
    return "none";
  }
  private opacity(): string { return this.globalAlpha < 1 ? ` opacity="${num(Math.max(0, this.globalAlpha))}"` : ""; }

  // --- вывод -------------------------------------------------------------------------------------------
  fill(rule?: string | unknown, maybeRule?: string): void {
    const fillRule = typeof rule === "string" ? rule : maybeRule;
    if (!this.path.length) return;
    this.body.push(`<path d="${this.path.join("")}" fill="${escapeXml(this.paint(this.fillStyle, this.ctm))}"` +
      `${fillRule === "evenodd" ? ' fill-rule="evenodd"' : ""}${this.opacity()}/>`);
  }
  stroke(): void {
    if (!this.path.length) return;
    this.body.push(`<path d="${this.path.join("")}" fill="none" ${this.strokeAttributes(this.ctm)}/>`);
  }
  private strokeAttributes(space: Matrix, scaleBy = meanScale(this.ctm)): string {
    const dash = this.dash.some((value) => value > 0)
      ? ` stroke-dasharray="${this.dash.map((value) => num(value * scaleBy)).join(" ")}"${this.lineDashOffset ? ` stroke-dashoffset="${num(this.lineDashOffset * scaleBy)}"` : ""}` : "";
    return `stroke="${escapeXml(this.paint(this.strokeStyle, space))}" stroke-width="${num(this.lineWidth * scaleBy)}"` +
      `${this.lineCap !== "butt" ? ` stroke-linecap="${this.lineCap}"` : ""}${this.lineJoin !== "miter" ? ` stroke-linejoin="${this.lineJoin}"` : ""}` +
      `${this.miterLimit !== 10 ? ` stroke-miterlimit="${num(this.miterLimit)}"` : ""}${dash}${this.opacity()}`;
  }
  private rectPath(x: number, y: number, width: number, height: number): string {
    const corners = [[x, y], [x + width, y], [x + width, y + height], [x, y + height]] as const;
    return corners.map(([cx, cy], index) => `${index ? "L" : "M"}${this.point(cx, cy)}`).join("") + "Z";
  }
  fillRect(x: number, y: number, width: number, height: number): void {
    this.body.push(`<path d="${this.rectPath(x, y, width, height)}" fill="${escapeXml(this.paint(this.fillStyle, this.ctm))}"${this.opacity()}/>`);
  }
  strokeRect(x: number, y: number, width: number, height: number): void {
    this.body.push(`<path d="${this.rectPath(x, y, width, height)}" fill="none" ${this.strokeAttributes(this.ctm)}/>`);
  }
  clearRect(): void { /* белый фон рисует сама выгрузка */ }
  clip(rule?: string | unknown, maybeRule?: string): void {
    const clipRule = typeof rule === "string" ? rule : maybeRule;
    const id = `c${this.nextId++}`;
    this.defs.push(`<clipPath id="${id}"><path d="${this.path.join("") || "M0 0"}"${clipRule === "evenodd" ? ' clip-rule="evenodd"' : ""}/></clipPath>`);
    this.body.push(`<g clip-path="url(#${id})">`);
    this.groups++;
  }

  // --- текст -------------------------------------------------------------------------------------------
  private measurer(): CanvasRenderingContext2D | null {
    if (this.scratch === undefined) {
      try { this.scratch = typeof document === "undefined" ? null : document.createElement("canvas").getContext("2d"); } catch { this.scratch = null; }
    }
    return this.scratch ?? null;
  }
  measureText(text: string): { width: number } {
    const brush = this.measurer();
    if (brush) { brush.font = this.font; return { width: brush.measureText(text).width }; }
    const size = Number(/(\d+(?:\.\d+)?)px/.exec(this.font)?.[1] ?? 10);
    return { width: text.length * size * 0.55 };
  }
  private text(value: string, x: number, y: number, mode: "fill" | "stroke"): void {
    const anchor = this.textAlign === "center" ? "middle" : this.textAlign === "right" || this.textAlign === "end" ? "end" : "start";
    const baselines: Record<string, string> = { top: "text-before-edge", hanging: "hanging", middle: "central", bottom: "text-after-edge", ideographic: "text-after-edge" };
    const baseline = baselines[this.textBaseline];
    const paint = mode === "fill"
      ? `fill="${escapeXml(this.paint(this.fillStyle, IDENTITY))}"`
      : `fill="none" ${this.strokeAttributes(IDENTITY, 1)}`;
    this.body.push(`<text x="${num(x)}" y="${num(y)}"${isIdentity(this.ctm) ? "" : ` transform="${matrixAttr(this.ctm)}"`} ` +
      `${fontAttributes(this.font)} ${paint}${anchor !== "start" ? ` text-anchor="${anchor}"` : ""}` +
      `${baseline ? ` dominant-baseline="${baseline}"` : ""}${this.opacity()} xml:space="preserve">${escapeXml(value)}</text>`);
  }
  fillText(value: string, x: number, y: number): void { this.text(value, x, y, "fill"); }
  strokeText(value: string, x: number, y: number): void { this.text(value, x, y, "stroke"); }

  // --- картинки ----------------------------------------------------------------------------------------
  drawImage(source: unknown, ...args: number[]): void {
    const image = source as CanvasImage;
    const naturalWidth = image.naturalWidth ?? image.width ?? 0, naturalHeight = image.naturalHeight ?? image.height ?? 0;
    let crop: { sx: number; sy: number; sw: number; sh: number } | undefined;
    let dx: number, dy: number, dw: number, dh: number;
    if (args.length >= 8) {
      const [sx, sy, sw, sh] = args as [number, number, number, number];
      [dx, dy, dw, dh] = args.slice(4) as [number, number, number, number];
      if (sx !== 0 || sy !== 0 || sw !== naturalWidth || sh !== naturalHeight) crop = { sx, sy, sw, sh };
    } else if (args.length >= 4) { [dx, dy, dw, dh] = args as [number, number, number, number]; }
    else { [dx, dy] = args as [number, number]; dw = naturalWidth; dh = naturalHeight; }
    const url = imageDataUrl(source, crop);
    if (!url || !(dw > 0) || !(dh > 0)) return;
    this.body.push(`<image href="${url}" x="${num(dx)}" y="${num(dy)}" width="${num(dw)}" height="${num(dh)}" preserveAspectRatio="none"` +
      `${isIdentity(this.ctm) ? "" : ` transform="${matrixAttr(this.ctm)}"`}${this.opacity()}/>`);
  }

  /** Содержимое `<svg>`: определения и записанные элементы. */
  markup(): string {
    const closing = "</g>".repeat(this.groups + this.stack.reduce((sum, state) => sum + state.groups, 0));
    return `${this.defs.length ? `<defs>${this.defs.join("")}</defs>` : ""}${this.body.join("")}${closing}`;
  }
}
