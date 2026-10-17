// Эскиз полуфабриката в схеме ПФ ЭТК (владелец 17.10: «нормальные эскизы полуфабрикатов» вместо списка ПФ слева).
// Рисунок ПФ — состояние изделия после его последней операции, собранное из готовых рисунков: эскизы операций etkDraw
// (провода с концами и цветами, нарезанные трубки, торец разъёма с проводами по гнёздам, свивка), кабель в оболочке —
// рисунком схемы ПФ генератора (RouteCanvas.CableArt), изделие — схема жгута (etkDrawHarness). Чего в etkDraw нет —
// сросток под трубкой и слои на пучке (оплётка, лента, термоусадка, маркировка с надписью) — рисуется здесь, тем же
// стилем. Считается при показе: выданные ЭТК перевыпускать не нужно.
import { wireColorKnown } from "./connectorArt";
import { etkCutEnds, etkDrawHarness, etkFigure, etkWireColor, type CutEndKind, type EtkTopologyData } from "./etkDraw";
import { formatMinutes, type EtkPfGroup } from "./etkRoute";
import type { WireBlankEnd } from "./harness/WireBlankCatalog";
import { coreColor } from "./pinoutCheck";
import { coresWord, type WireThumb } from "./routeCards";
import { setCustomColors } from "./wireColors";

type Row = Record<string, unknown>;

/** Главный рисунок ПФ: SVG-строка, кабель в оболочке (React-рисунок генератора) или картинка технолога. */
export type PfMain = { svg: string } | { cable: WireThumb } | { image: string };
/** Эскиз ПФ: главный рисунок и полоса слоёв на пучке под ним (оплётка, лента, трубки, маркировка). */
export type PfArt = { main: PfMain | null; layers: string };

const INK = "#17272b";
const MUTED = "#5f7470";

function obj(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

function rows(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") as Row[] : [];
}

function str(value: unknown): string {
  return value == null ? "" : String(value);
}

function num(value: unknown): number {
  const parsed = Number(str(value).replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
}

function esc(value: unknown): string {
  return str(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function cut(text: string, size: number): string {
  return text.length > size ? `${text.slice(0, size - 1)}…` : text;
}

function svg(width: number, height: number, body: string): string {
  return `<svg class="etk-svg etk-pf-svg" viewBox="0 0 ${width} ${height}" width="100%" preserveAspectRatio="xMinYMin meet" xmlns="http://www.w3.org/2000/svg">` +
    `<defs><pattern id="pfBraid" width="7" height="7" patternUnits="userSpaceOnUse"><rect width="7" height="7" fill="#9aa1a6"/>` +
    `<path d="M0,0 L7,7 M7,0 L0,7" stroke="#4b5257" stroke-width="1.1"/></pattern>` +
    `<pattern id="pfWrap" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(35)"><rect width="9" height="9" fill="#26292c"/>` +
    `<line x1="0" y1="0" x2="0" y2="9" stroke="#5b6166" stroke-width="2"/></pattern></defs>` + body + "</svg>";
}

const END_ART: Record<CutEndKind, WireBlankEnd> = { none: "cut", strip: "copper", tin: "tin", crimp: "terminal" };

/** Кабель в оболочке (жил > 1) в операции резки — рисунком схемы ПФ генератора (RouteCanvas.CableArt): оболочка
 *  посередине, жилы веером в своих цветах, концы — как отдаёт резка. Жилы и их цвета — у провода в конфигурации сборки
 *  (`build_config.wires`: `cores`, `coreColors`), поэтому выданные ЭТК перевыпускать не нужно. Одиночные провода — null
 *  (эскиз резки как был). */
export function etkCutCable(operation: Row, config: Row): WireThumb | null {
  if (!/^cutWire/.test(str(operation.type))) return null;
  const own = rows(config.wires);
  const same = (a: Row, b: Row) => str(a.art) === str(b.art) && str(a.name) === str(b.name);
  for (const wire of rows(obj(operation.fields).wires)) {
    const source = own.find((item) => same(item, wire)) ?? {};
    const cores = Math.round(Number(wire.cores ?? source.cores) || 0);
    if (cores <= 1) continue;
    setCustomColors(config.colorHex as Record<string, string> | undefined);
    const names = (Array.isArray(wire.coreColors) ? wire.coreColors : Array.isArray(source.coreColors) ? source.coreColors : []) as unknown[];
    const label = str(wire.art || wire.name) || "кабель";
    const base = wireColorKnown(wire.name || wire.art) ?? "#6b7c86";
    const colors = Array.from({ length: cores }, (_, k) => coreColor(str(names[k]))?.hex ?? base);
    const ends = etkCutEnds(operation);
    return {
      key: label, label, title: `${label} · ${cores} ${coresWord(cores)}`,
      color: base, left: END_ART[ends.a.kind], right: END_ART[ends.b.kind],
      cable: { cores: colors.map((color, k) => ({ color, label: str(names[k]) })), sub: "" },
    };
  }
  return null;
}

type Kind = "splice" | "braid" | "wrap" | "tube" | "mark" | "twist";

/** Вид операции для рисунка ПФ: сросток, слой на пучке или ничего (у остальных свой эскиз в etkDraw). */
function kindOf(operation: Row): Kind | null {
  const type = str(operation.type), code = str(operation.code).toUpperCase();
  if (type === "splice" || /WELD|SPLICE/.test(code)) return "splice";
  if (/BRAID/.test(code)) return "braid";
  if (type === "insMark" || /MARK/.test(code)) return "mark";
  if (type === "twist" || /^TWIST/.test(code)) return "twist";
  if (/WRAP/.test(code)) return "wrap";
  if (type === "heatTut" || type === "insTut" || /TUT|TTK/.test(code)) return "tube";
  return null;
}

/** Длина на изделие: «0,45 м», «445 мм»; штуки — «×20». Норма в документе — на партию. */
function perUnit(row: Row, batch: number): string {
  const value = num(row.norm) / (batch > 0 ? batch : 1);
  if (!(value > 0)) return "";
  const unit = str(row.unit).toLowerCase().replace(/\.$/, "");
  if (unit === "м") return value < 1 ? `${Math.round(value * 1000)} мм` : `${formatMinutes(Math.round(value * 100) / 100)} м`;
  if (unit === "мм") return `${formatMinutes(Math.round(value))} мм`;
  return `×${formatMinutes(Math.round(value * 100) / 100)}`;
}

/** Короткое название материала: до первой запятой («Изолента Temflex 1300, черная» → «Изолента Temflex 1300»). */
function material(row: Row): string {
  return (str(row.art) || str(row.name)).split(/,\s+/)[0].trim();
}

type Layer = { kind: Exclude<Kind, "splice">; label: string; note: string; text: string };

/** Слои операции на пучке: материал и его длина на изделие; маркировка — метка на строку КД (надпись — из меток). */
function layersOf(operation: Row, kind: Exclude<Kind, "splice">, batch: number): Layer[] {
  const parts = rows(operation.components);
  if (kind === "mark") {
    const marks = rows(obj(operation.fields).marks);
    if (marks.length) {
      return marks.slice(0, 3).map((mark) => ({ kind, label: str(mark.target) || str(mark.material) || "Метка", note: str(mark.qty) ? `×${str(mark.qty)}` : "", text: str(mark.text) }));
    }
    return (parts.length ? parts : [{}]).slice(0, 3).map((row) => ({ kind, label: material(row) || "Маркировка", note: perUnit(row, batch), text: "" }));
  }
  if (kind === "twist") {
    const pitch = str(obj(obj(operation.fields).twist).pitch);
    return [{ kind, label: "Свивка", note: pitch ? `шаг ${pitch} мм` : "", text: "" }];
  }
  const row = parts[0] ?? {};
  const fallback = kind === "braid" ? "Оплётка" : kind === "wrap" ? "Лента" : "Термоусадка";
  return [{ kind, label: material(row) || fallback, note: perUnit(row, batch), text: "" }];
}

/** Полоса слоёв на пучке: пучок проводов, на нём по участку на слой — оплётка сеткой, лента полосами, термоусадка
 *  тёмной трубкой, метка — белой трубкой с надписью; под участком — материал и длина на изделие. */
export function pfLayersSvg(layers: Layer[]): string {
  if (!layers.length) return "";
  const shown = layers.slice(0, 5), W = 640, x0 = 16, x1 = W - 16, cy = 34, gap = 14;
  const slot = (x1 - x0 - gap * (shown.length - 1)) / shown.length;
  let body = `<rect x="${x0 - 8}" y="${cy - 6}" width="${x1 - x0 + 16}" height="12" rx="5" fill="#6b7c86" stroke="${INK}" stroke-width=".8"/>`;
  shown.forEach((layer, index) => {
    const x = x0 + index * (slot + gap), mid = x + slot / 2, chars = Math.max(8, Math.floor(slot / 7.4));
    if (layer.kind === "braid") body += `<rect x="${x}" y="${cy - 12}" width="${slot}" height="24" rx="6" fill="url(#pfBraid)" stroke="${INK}" stroke-width=".9"/>`;
    if (layer.kind === "wrap") body += `<rect x="${x}" y="${cy - 11}" width="${slot}" height="22" rx="3" fill="url(#pfWrap)" stroke="${INK}" stroke-width=".9"/>`;
    if (layer.kind === "tube") body += `<rect x="${x + slot * 0.2}" y="${cy - 11}" width="${slot * 0.6}" height="22" rx="6" fill="#2f3b40" stroke="${INK}" stroke-width=".9"/>`;
    if (layer.kind === "mark") {
      body += `<rect x="${x + slot * 0.12}" y="${cy - 13}" width="${slot * 0.76}" height="26" rx="5" fill="#fffef6" stroke="${INK}" stroke-width="1"/>`;
      if (layer.text) body += `<text x="${mid}" y="${cy + 5}" text-anchor="middle" font-size="14" font-weight="700" fill="${INK}">${esc(cut(layer.text, Math.floor(slot * 0.76 / 8.4)))}</text>`;
    }
    if (layer.kind === "twist") {
      [0, 1].forEach((phase) => {
        let d = "";
        for (let at = 0; at <= slot; at += 4) d += `${at ? "L" : "M"}${(x + at).toFixed(1)},${(cy + 8 * Math.sin((at / slot) * 4 * Math.PI + phase * Math.PI)).toFixed(1)}`;
        body += `<path d="${d}" fill="none" stroke="${INK}" stroke-width="6.5" stroke-linecap="round"/><path d="${d}" fill="none" stroke="${phase ? "#d7322d" : "#2b2b2b"}" stroke-width="4.6" stroke-linecap="round"/>`;
      });
    }
    body += `<text x="${mid}" y="${cy + 34}" text-anchor="middle" font-size="13" font-weight="600" fill="${INK}">${esc(cut(layer.label, chars))}</text>`;
    if (layer.note) body += `<text x="${mid}" y="${cy + 51}" text-anchor="middle" font-size="13" fill="${MUTED}">${esc(layer.note)}</text>`;
  });
  return svg(W, cy + 60, body);
}

/** Провода из названия входа: «Провода нарезаны: ПВАМ 0,5 черный — 1 шт по 1000 мм; …» → цвета по проводу. */
function wireColors(operations: Row[]): string[] {
  const names = operations.flatMap((operation) => rows(operation.semis).map((row) => str(row.name)))
    .flatMap((name) => name.slice(name.indexOf(": ") + 1).split(/;\s*/).map((part) => part.split(" — ")[0].trim()))
    .filter(Boolean);
  const colors = [...new Set(names.map((name) => etkWireColor(name)))];
  return colors.length ? colors : ["#2b2b2b", "#d7322d"];
}

/** Сросток: провода сходятся в спай под термоусадочной трубкой и выходят с другой стороны. */
export function pfSpliceSvg(colors: string[], count: string, tube: string): string {
  const cy = 62, left = colors.slice(0, 3), right = colors.length > 3 ? colors.slice(3, 5) : colors.slice(0, 2);
  const fan = (list: string[], from: number, to: number) => list.map((color, index) => {
    const y = cy + (index - (list.length - 1) / 2) * 26, y2 = cy + (index - (list.length - 1) / 2) * 4;
    const d = `M${from},${y} C${(from + to) / 2},${y} ${(from + to) / 2},${y2} ${to},${y2}`;
    return `<path d="${d}" fill="none" stroke="${INK}" stroke-width="9" stroke-linecap="round"/><path d="${d}" fill="none" stroke="${color}" stroke-width="7" stroke-linecap="round"/>`;
  }).join("");
  const body = fan(left, 24, 250) + fan(right.map((color) => color), 616, 390) +
    `<rect x="244" y="${cy - 7}" width="152" height="14" rx="3" fill="#c88954" stroke="${INK}" stroke-width=".8"/>` +
    `<rect x="228" y="${cy - 17}" width="184" height="34" rx="9" fill="#2f3b40" opacity=".9" stroke="${INK}" stroke-width="1"/>` +
    `<text x="320" y="${cy + 5}" text-anchor="middle" font-size="14" font-weight="700" fill="#fff">${esc(`сросток${count ? ` ${count}` : ""}`)}</text>` +
    (tube ? `<text x="320" y="${cy + 42}" text-anchor="middle" font-size="13" fill="${MUTED}">${esc(`под ${tube}`)}</text>` : "");
  return svg(640, cy + 50, body);
}

/** Трубка сростка: из входа ПФ («ТТК 4,8/1,6 ×3») или из комплектующих его операций. */
function spliceTube(inputs: string[], operations: Row[]): string {
  const from = [...inputs, ...operations.flatMap((operation) => rows(operation.components).map((row) => material(row)))]
    .find((text) => /ТТК|ТУТ|трубк|термоус/i.test(text));
  return from ? from.replace(/\s*×\s*\d+(?:[.,]\d+)?$/, "").trim() : "";
}

/** «×3» сростков из схемы жгута («Сростки ×3»). */
function spliceCount(topology: EtkTopologyData | null | undefined): string {
  const node = (topology?.nodes ?? []).find((item) => item.kind === "splice");
  return node?.conn.match(/×\s*\d+/)?.[0].replace(/\s+/g, "") ?? "";
}

/** Эскиз ПФ. `operations` — операции ПФ в порядке работы; `inputs` — что приходит из других ПФ (текст стрелок);
 *  `pins` — распиновка по узлу схемы (для изделия); `image` — своя картинка технолога у операции. */
export function etkPfArt({ group, operations, inputs = [], document, pins = {}, batch = 1, image = () => "" }: {
  group: EtkPfGroup; operations: Row[]; inputs?: string[]; document: Row | null | undefined;
  pins?: Record<string, Record<number, string>>; batch?: number; image?: (operation: Row) => string;
}): PfArt | null {
  const config = obj(document?.build_config);
  const topology = obj(document?.topology) as EtkTopologyData;
  const kinds = operations.map(kindOf);
  const figures = operations.map((operation) => etkFigure(operation));
  // Изделие — схема жгута целиком.
  let main: PfMain | null = null;
  if (group.final) {
    const harness = etkDrawHarness(topology, pins);
    if (harness) main = { svg: harness };
  }
  if (!main && kinds.includes("splice")) main = { svg: pfSpliceSvg(wireColors(operations), spliceCount(topology), spliceTube(inputs, operations)) };
  // Иначе — рисунок последней операции, у которой он есть: состояние ПФ на выходе.
  for (let index = operations.length - 1; !main && index >= 0; index -= 1) {
    const cable = etkCutCable(operations[index], config);
    if (cable) main = { cable };
    else if (figures[index]) main = { svg: figures[index] };
  }
  // Слои на пучке — от операций без своего эскиза; у сростка его трубки уже на рисунке.
  const layers = group.final ? [] : operations.flatMap((operation, index) => {
    const kind = kinds[index];
    if (!kind || kind === "splice" || figures[index] || (kind === "tube" && kinds.includes("splice"))) return [];
    return layersOf(operation, kind, batch);
  });
  for (let index = operations.length - 1; !main && !layers.length && index >= 0; index -= 1) {
    const own = image(operations[index]);
    if (own) main = { image: own };
  }
  const strip = pfLayersSvg(layers);
  return main || strip ? { main, layers: strip } : null;
}
