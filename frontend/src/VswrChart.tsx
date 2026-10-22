import type { VswrTracePoint } from "./api";
import { formatNumber } from "./format";

/* График замера КСВН: КСВН по частоте, норма — красная горизонталь, границы полосы — штрих и светлая заливка,
   максимум — отмеченная точка. Чёрное на белом: читается и на экране, и на чёрно-белой печати. Только атрибуты
   представления (без style и var()), поэтому та же разметка печатается в iframe под CSP (coaxPassportPrint.ts). */

export interface VswrChartProps {
  trace: readonly VswrTracePoint[];
  /** Норма КСВН (верхняя граница); null — нормы нет, линия не рисуется. */
  normMax: number | null;
  /** Полоса замера, МГц: [от, до]; пустые границы не рисуются. */
  band?: readonly [number | null, number | null] | null;
  maxVswr?: number | null;
  maxMhz?: number | null;
  width?: number;
  height?: number;
}

const FONT = 'font-family="Arial, Helvetica, sans-serif"';
const RED = "#c00000";

const esc = (value: number) => String(Math.round(value * 100) / 100);
const text = (value: number, digits = 2) => formatNumber(value, { digits });

/** «Круглый» шаг делений: 1, 2, 2,5, 5 × 10^n так, чтобы делений было около `count`. */
function niceStep(span: number, count: number): number {
  const raw = span / Math.max(count, 1);
  const power = 10 ** Math.floor(Math.log10(raw));
  const unit = raw / power;
  return (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 5 ? 5 : 10) * power;
}

function ticks(from: number, to: number, count: number): number[] {
  const step = niceStep(to - from, count);
  const result: number[] = [];
  for (let value = Math.ceil(from / step) * step; value <= to + step * 1e-9; value += step) result.push(Math.round(value / step) * step);
  return result;
}

export function vswrChartSvg(props: VswrChartProps): string {
  const width = props.width ?? 720;
  const height = props.height ?? 300;
  const left = 52, right = 16, top = 16, bottom = 40;
  const finite = props.trace.filter((point) => Number.isFinite(point.mhz) && Number.isFinite(point.vswr));
  const band = props.band ?? [null, null];
  const bandFrom = band[0] ?? null;
  const bandTo = band[1] ?? null;
  // Выгрузка ВАЦ часто шире полосы (1…3000 МГц при полосе 1700…1900): показываем полосу с полем в полполосы по бокам.
  const pad = bandFrom !== null && bandTo !== null && bandTo > bandFrom ? (bandTo - bandFrom) / 2 : null;
  const trace = pad === null ? finite : finite.filter((point) => point.mhz >= (bandFrom as number) - pad && point.mhz <= (bandTo as number) + pad);
  const norm = props.normMax;
  const label = `График КСВН по частоте${norm !== null ? `, норма не более ${text(norm)}` : ""}`;
  if (!trace.length) {
    return `<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="${label}" viewBox="0 0 ${width} ${height}" width="100%" ${FONT}>` +
      `<text x="${width / 2}" y="${height / 2}" text-anchor="middle" font-size="13" fill="#000">Нет точек замера</text></svg>`;
  }
  const xs = [...trace.map((point) => point.mhz), ...(bandFrom !== null ? [bandFrom] : []), ...(bandTo !== null ? [bandTo] : [])];
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs) === xMin ? xMin + 1 : Math.max(...xs);
  const yTop = Math.max(...trace.map((point) => point.vswr), norm ?? 0, props.maxVswr ?? 0);
  const yMin = 1;
  const yMax = Math.max(yMin + 0.5, Math.ceil(yTop * 1.12 * 10) / 10);
  const plotW = width - left - right;
  const plotH = height - top - bottom;
  const px = (mhz: number) => left + ((mhz - xMin) / (xMax - xMin)) * plotW;
  const py = (vswr: number) => top + (1 - (Math.min(vswr, yMax) - yMin) / (yMax - yMin)) * plotH;
  const parts: string[] = [];
  if (bandFrom !== null && bandTo !== null) {
    parts.push(`<rect x="${esc(px(bandFrom))}" y="${top}" width="${esc(px(bandTo) - px(bandFrom))}" height="${plotH}" fill="#eeeeee"/>`);
  }
  for (const value of ticks(yMin, yMax, 6)) {
    parts.push(`<line x1="${left}" x2="${left + plotW}" y1="${esc(py(value))}" y2="${esc(py(value))}" stroke="#d0d0d0" stroke-width="1"/>`,
      `<text x="${left - 6}" y="${esc(py(value) + 4)}" text-anchor="end" font-size="11" fill="#000">${text(value, 2)}</text>`);
  }
  for (const value of ticks(xMin, xMax, 8)) {
    parts.push(`<line x1="${esc(px(value))}" x2="${esc(px(value))}" y1="${top + plotH}" y2="${top + plotH + 4}" stroke="#000" stroke-width="1"/>`,
      `<text x="${esc(px(value))}" y="${top + plotH + 16}" text-anchor="middle" font-size="11" fill="#000">${text(value, 0)}</text>`);
  }
  parts.push(`<rect x="${left}" y="${top}" width="${plotW}" height="${plotH}" fill="none" stroke="#000" stroke-width="1"/>`,
    `<text x="${left + plotW / 2}" y="${height - 6}" text-anchor="middle" font-size="12" fill="#000">Частота, МГц</text>`,
    `<text x="14" y="${top + plotH / 2}" text-anchor="middle" font-size="12" fill="#000" transform="rotate(-90 14 ${top + plotH / 2})">КСВН</text>`);
  for (const edge of [bandFrom, bandTo]) {
    if (edge !== null) parts.push(`<line x1="${esc(px(edge))}" x2="${esc(px(edge))}" y1="${top}" y2="${top + plotH}" stroke="#555" stroke-width="1" stroke-dasharray="5 3"/>`);
  }
  parts.push(`<polyline fill="none" stroke="#000" stroke-width="1.5" stroke-linejoin="round" points="${trace.map((point) => `${esc(px(point.mhz))},${esc(py(point.vswr))}`).join(" ")}"/>`);
  if (norm !== null) {
    const y = py(norm);
    parts.push(`<line x1="${left}" x2="${left + plotW}" y1="${esc(y)}" y2="${esc(y)}" stroke="${RED}" stroke-width="2"/>`,
      `<text x="${left + plotW - 4}" y="${esc(y - 5)}" text-anchor="end" font-size="12" font-weight="bold" fill="${RED}">Норма ≤ ${text(norm)}</text>`);
  }
  if (props.maxVswr != null && props.maxMhz != null) {
    const x = px(props.maxMhz);
    const y = py(props.maxVswr);
    const anchor = x > left + plotW * 0.7 ? "end" : "start";
    const dx = anchor === "end" ? -8 : 8;
    parts.push(`<circle cx="${esc(x)}" cy="${esc(y)}" r="5" fill="#fff" stroke="#000" stroke-width="2"/>`,
      `<text x="${esc(x + dx)}" y="${esc(Math.max(y - 8, top + 12))}" text-anchor="${anchor}" font-size="12" font-weight="bold" fill="#000" stroke="#fff" stroke-width="4" paint-order="stroke">Максимум ${text(props.maxVswr)} на ${text(props.maxMhz, 1)} МГц</text>`);
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="${label}" viewBox="0 0 ${width} ${height}" width="100%" ${FONT}>${parts.join("")}</svg>`;
}

export function VswrChart(props: VswrChartProps) {
  // Разметка собрана из чисел и постоянных подписей — пользовательского текста в ней нет.
  return <div className="vswr-chart" dangerouslySetInnerHTML={{ __html: vswrChartSvg(props) }} />;
}
