// Картинки ПФ коаксиальной сборки на размер карточки схемы ПФ (владелец 19.10: «визуал полуфабрикатов прям такой себе»;
// 23.11: «эскиз слабый, очень много нарушений», «тут визуал доработать тоже»). Холст 320 по ширине — масштаб 1:1 карточки,
// подписи не мельче 10. Рисунок ПФ — компактное СОСТОЯНИЕ полуфабриката, без инструкций и цепочек размеров: полный эскиз
// остаётся у операции (etkCoaxOpArt / etkDraw).
//  - ПФ резки и разделки — кабель с разделанными концами и длиной реза;
//  - ПФ «Кабель с метками и деталями» — что надето на кабель (трубки, гильзы, этикетки M1…);
//  - ПФ «Кабель с центральным контактом» — конец кабеля с контактом: штырь у вилки, гнездо у розетки;
//  - ПФ «Сторона … собрана» — разъём своего семейства (SMA, FAKRA, HFM, N, MMCX…) в том состоянии, которого достиг маршрут:
//    гильза надета или обжата, пластмассовый корпус, крышка, трубка надвинута или усажена;
//  - изделие — вся сборка: концы «А» и «В» короткими метками, этикетки M1…M3 и прозрачные ТУТ поверх них, цвет разъёмов,
//    размер L готового изделия по КД (не длина реза) и табличка расшифровки под рисунком.
// Геометрия семейства, для которого нет проверенного чертежа, рисуется СХЕМОЙ с пометкой «схема, без масштаба», а не
// подставным SMA. Линии — по ролям ГОСТ 2.303 (табл. 1): выносные, размерные и выноски — сплошные тонкие, штриховая —
// только невидимый контур. Данные — `build_config.coax`, операции документа и маршрут: выданные ЭТК перевыпускать не нужно.
import { coaxEndNames, coaxKindTitle } from "./coaxNames";
import { tubeShort } from "./etkPfScheme";
import type { EtkPfGroup } from "./etkRoute";

type Row = Record<string, unknown>;
type Side = "A" | "B";

export const INK = "#17272b", MUTED = "#5f7470", METAL = "#d4d9dd", EDGE = "#8d969c", BRASS = "#d9b25f", COPPER = "#e08a2c";
export const JACKET = "#222", TUBE = "#3b4147", SOLDER = "#c9ced3", DIEL = "#fbfbf8", PLASTIC = "#8e9aa2", RUBBER = "#4b5257";
/** Тонкие линии (выносные, размерные, выноски): ГОСТ 2.303 — сплошная тонкая. */
export const THIN = "#55666b";
const W = 320;

const obj = (value: unknown): Row => (value && typeof value === "object" && !Array.isArray(value) ? value as Row : {});
const rows = (value: unknown): Row[] => (Array.isArray(value) ? value.filter((item) => item && typeof item === "object") as Row[] : []);
const str = (value: unknown): string => (value == null ? "" : String(value));
const num = (value: unknown): number => {
  const parsed = Number(str(value).replace(",", ".").replace(/\s*мм\s*$/i, "").trim());
  return Number.isFinite(parsed) ? parsed : 0;
};
const esc = (value: unknown): string => str(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const f = (value: number) => value.toFixed(1);

/** Прямоугольник по краям x1..x2 (в любом порядке). */
export function rect(x1: number, x2: number, y: number, h: number, fill: string, stroke = EDGE, rx = 0, extra = ""): string {
  return `<rect x="${f(Math.min(x1, x2))}" y="${f(y)}" width="${f(Math.abs(x2 - x1))}" height="${f(h)}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width=".9"${extra ? ` ${extra}` : ""}/>`;
}
/** Линия контура детали (видимый контур, рифление, резьба). Выносные, размерные и выноски — `extensionLine`, `dimension`, `leader`. */
export const line = (x1: number, y1: number, x2: number, y2: number, stroke = EDGE, width = .9, dash = "") =>
  `<line x1="${f(x1)}" y1="${f(y1)}" x2="${f(x2)}" y2="${f(y2)}" stroke="${stroke}" stroke-width="${width}"${dash ? ` stroke-dasharray="${dash}"` : ""}/>`;
export const label = (x: number, y: number, value: string, size = 11, weight = 600, fill = INK, anchor = "middle") =>
  `<text x="${f(x)}" y="${f(y)}" text-anchor="${anchor}" font-size="${size}" font-weight="${weight}" fill="${fill}">${esc(value)}</text>`;

// ── Линии по ролям (ГОСТ 2.303, табл. 1) ──

/** Выносная линия размера: сплошная тонкая. */
export const extensionLine = (x1: number, y1: number, x2: number, y2: number) =>
  `<line x1="${f(x1)}" y1="${f(y1)}" x2="${f(x2)}" y2="${f(y2)}" stroke="${THIN}" stroke-width=".7" data-role="extension"/>`;
/** Невидимый контур: штриховая. */
export const hiddenLine = (x1: number, y1: number, x2: number, y2: number) =>
  `<line x1="${f(x1)}" y1="${f(y1)}" x2="${f(x2)}" y2="${f(y2)}" stroke="${EDGE}" stroke-width=".8" stroke-dasharray="4 2" data-role="hidden"/>`;
/** Стрелка размерной линии с остриём в (x, y), `dir` — куда смотрит остриё. */
export const arrowHead = (x: number, y: number, dir: 1 | -1, size = 8) =>
  `<path d="M${f(x)},${f(y)} l${f(-dir * size)},-${f(size * .36)} v${f(size * .72)} z" fill="${INK}"/>`;
/** Горизонтальная размерная линия с двумя стрелками и числом над ней. Текст — по ГОСТ 2.307 над линией, в середине. */
export function dimension(x1: number, x2: number, y: number, text: string, size = 12, weight = 600): string {
  const [a, b] = [Math.min(x1, x2), Math.max(x1, x2)];
  return `<g data-role="dimension"><line x1="${f(a)}" y1="${f(y)}" x2="${f(b)}" y2="${f(y)}" stroke="${INK}" stroke-width=".8"/>` +
    arrowHead(a, y, -1) + arrowHead(b, y, 1) + (text ? label((a + b) / 2, y - 4, text, size, weight, INK) : "") + "</g>";
}
/** Выноска от полки к детали: сплошная тонкая, на детали — точка (линия-выноска заканчивается на поверхности). */
export function leader(x1: number, y1: number, x2: number, y2: number, target: string): string {
  return `<g data-role="leader" data-target="${esc(target)}"><line x1="${f(x1)}" y1="${f(y1)}" x2="${f(x2)}" y2="${f(y2)}" stroke="${THIN}" stroke-width=".7"/>` +
    `<circle cx="${f(x2)}" cy="${f(y2)}" r="1.8" fill="${INK}"/></g>`;
}

/** Ширина строки в единицах холста для раскладки подписей: Open Sans / Segoe UI с запасом (жирный шире). */
export function textWidth(text: string, size: number, weight = 600): number {
  let units = 0;
  for (const char of Array.from(text)) {
    if (char === " ") units += .28;
    else if (/[.,:;·!|'’()[\]{}/\\-]/.test(char)) units += .34;
    else if (/[0-9]/.test(char)) units += .58;
    else if (/[ШЩЖМЮшщжмюWMmw…]/.test(char)) units += .86;
    else if (/[A-ZА-ЯЁ]/.test(char)) units += .68;
    else if (/[a-zа-яё]/.test(char)) units += .56;
    else units += 1;
  }
  return units * size * (weight >= 600 ? 1.06 : 1);
}

export type Callout = { text: string; anchor: [number, number]; target: string };
/** Подписи на полках в один ряд (`y` — базовая линия) между `from` и `to`: каждая — над своей деталью, без наложений;
 *  выноска идёт от ближнего к детали места полки до точки на детали. */
export function callouts(items: Callout[], y: number, from: number, to: number, size = 12): string {
  const placed = [...items].sort((a, b) => a.anchor[0] - b.anchor[0]).map((item) => {
    // Запас на шрифт браузера: не уже 0,62 em на знак (жирные подписи Open Sans / Segoe UI).
    const width = Math.max(textWidth(item.text, size), Array.from(item.text).length * size * .62);
    return { ...item, width, x: Math.min(Math.max(item.anchor[0] - width / 2, from), to - width) };
  });
  for (let i = 1; i < placed.length; i += 1) placed[i].x = Math.max(placed[i].x, placed[i - 1].x + placed[i - 1].width + 12);
  for (let i = placed.length - 1; i >= 0; i -= 1) {
    const limit = i === placed.length - 1 ? to : placed[i + 1].x - 12;
    placed[i].x = Math.min(placed[i].x, limit - placed[i].width);
  }
  return placed.map((item) => {
    const shelfY = y + 3, near = Math.min(Math.max(item.anchor[0], item.x), item.x + item.width);
    return `<g data-callout="${esc(item.target)}">` + label(item.x, y, item.text, size, 600, INK, "start") +
      `<line x1="${f(item.x - 2)}" y1="${f(shelfY)}" x2="${f(item.x + item.width + 2)}" y2="${f(shelfY)}" stroke="${THIN}" stroke-width=".7" data-role="shelf"/>` +
      leader(near, shelfY, item.anchor[0], item.anchor[1], item.target) + "</g>";
  }).join("");
}

/** Wrap SVG captions without dropping text, including article numbers with no spaces and non-BMP characters. */
export function wrapSvgText(value: string, columns: number): string[] {
  const lines: string[] = [];
  for (const paragraph of value.split(/\r?\n/)) {
    let line = "";
    for (const word of paragraph.trim().split(/\s+/).filter(Boolean)) {
      if (line && Array.from(`${line} ${word}`).length <= columns) { line += ` ${word}`; continue; }
      if (line) { lines.push(line); line = ""; }
      const letters = Array.from(word);
      let offset = 0;
      while (letters.length - offset > columns) { lines.push(letters.slice(offset, offset + columns).join("")); offset += columns; }
      line = letters.slice(offset).join("");
    }
    if (line) lines.push(line);
  }
  return lines;
}

/** Компактный рисунок ПФ: холст 320, класс `etk-pf-compact` — карточка не ужимает его по высоте (EtkRouteDiagram.css). */
/** Короткий детерминированный хеш: id узора уникален для каждого рисунка — несколько SVG на одной странице не делят id. */
export function svgId(prefix: string, body: string): string {
  let hash = 2166136261;
  for (let i = 0; i < body.length; i += 1) hash = Math.imul(hash ^ body.charCodeAt(i), 16777619);
  return `${prefix}-${(hash >>> 0).toString(36)}`;
}
function svg(height: number, body: string): string {
  const braid = svgId("pfBraid", body);
  return `<svg class="etk-svg etk-pf-svg etk-pf-compact" viewBox="0 0 ${W} ${f(height)}" width="100%" preserveAspectRatio="xMinYMin meet" xmlns="http://www.w3.org/2000/svg">` +
    `<defs><pattern id="${braid}" width="7" height="7" patternUnits="userSpaceOnUse"><rect width="7" height="7" fill="#9aa1a6"/>` +
    `<path d="M0,0 L7,7 M7,0 L0,7" stroke="#4b5257" stroke-width="1.1"/></pattern></defs>` +
    `${body.replaceAll("url(#pfBraid)", `url(#${braid})`).replaceAll("url(#etkBraid)", `url(#${braid})`)}</svg>`;
}

/** Табличка под рисунком: метка (А, В, M1…) жирным и расшифровка с переносом; строка — 15 единиц, шрифт 11. */
function legendRows(entries: Array<[string, string]>, top: number): { body: string; height: number } {
  let body = "", y = top;
  const column = 6 + Math.max(20, ...entries.map(([tag]) => textWidth(tag, 11, 800))) + 8, chars = Math.floor((W - column - 4) / 6.4);
  for (const [tag, text] of entries) {
    const lines = wrapSvgText(text, chars);
    body += `<g data-legend="${esc(tag)}">` + label(6, y + 11, tag, 11, 800, INK, "start") +
      lines.map((part, index) => label(column, y + 11 + index * 14, part, 11, 400, INK, "start")).join("") + "</g>";
    y += Math.max(1, lines.length) * 14 + 3;
  }
  return { body, height: y - top };
}

// ── Семейство разъёма, пол и стадия ──

export type CoaxFamily = "sma" | "rp-sma" | "sma-ra" | "fakra" | "fakra-ra" | "fakra-bh" | "hfm" | "n" | "tnc" | "mmcx" | "angled" | "other";
const FAMILY_TITLE: Record<CoaxFamily, string> = {
  sma: "SMA", "rp-sma": "RP-SMA", "sma-ra": "SMA угловой", angled: "угловой", fakra: "FAKRA прямой", "fakra-ra": "FAKRA угловой", "fakra-bh": "FAKRA проходной",
  hfm: "HFM", n: "N", tnc: "TNC", mmcx: "MMCX", other: "разъём",
};

/** Семейство по типу разъёма конца (`connectorType` / `type`), иначе по артикулу. */
export function coaxFamily(end: Row): CoaxFamily {
  const kind = coaxEndNames(end).kind, text = `${kind} ${str(end.type)} ${str(end.connector || end.article)}`;
  const angled = /R\s*\/\s*A|угл/i.test(text);
  if (/HFM/i.test(text)) return "hfm";
  if (/FAKRA/i.test(text)) return angled ? "fakra-ra" : /bulkhead|проходн/i.test(text) ? "fakra-bh" : "fakra";
  if (/MMCX/i.test(text)) return angled ? "angled" : "mmcx";
  if (/(^|[^A-Z])(RP-)?SMA(?![A-Z])/i.test(text)) return angled ? "sma-ra" : /RP-SMA/i.test(text) ? "rp-sma" : "sma";
  // Угловые N, TNC, MMCX и прочие: проверенного вида нет — общий угловой вариант схемой, а не прямой разъём.
  if (angled) return "angled";
  if (/TNC/i.test(text)) return "tnc";
  if (/(^|[\s(])N-(Male|Female)/i.test(kind) || /^N-/i.test(str(end.type))) return "n";
  return "other";
}
export function coaxFamilyTitle(end: Row): string {
  const family = coaxFamily(end);
  if (family !== "angled") return FAMILY_TITLE[family];
  const base = `${coaxEndNames(end).kind} ${str(end.type)}`.match(/RP-TNC|TNC|MMCX|MCX|BNC|N(?=-)/i)?.[0];
  return base ? `${base.toUpperCase()} угловой` : "угловой разъём";
}
/** Есть ли у семейства проверенный боковой вид. Нет — рисуется схема с пометкой «без масштаба». */
export const coaxGeometryKnown = (end: Row) => coaxFamily(end) === "sma" && contactKind(end) !== "unknown";

/** Разъём конца: вилка (штыри) или розетка (гнёзда); типа нет — вилкой по умолчанию. */
export const isFemale = (end: Row) => coaxEndNames(end).word === "розетка";
/** Контакт конца: гнездо у розетки, штырь у вилки; пол не задан или обратная полярность (RP-) — нейтральный контакт без
 *  острия и без отверстия: пол не угадывается. */
export type ContactKind = "socket" | "pin" | "unknown";
export function contactKind(end: Row): ContactKind {
  const { kind, word } = coaxEndNames(end);
  if (/(^|[^A-Z])RP-/i.test(`${kind} ${str(end.type)}`)) return "unknown";
  return word === "розетка" ? "socket" : word === "вилка" ? "pin" : "unknown";
}
const CONTACT_NAME: Record<ContactKind, string> = { socket: "гнездо", pin: "штырь", unknown: "контакт" };
export const contactName = (end: Row) => CONTACT_NAME[contactKind(end)];

/** Что на конце уже есть. `shield`: гильза экрана не надета / надета (не обжата) / обжата / зажим / экран припаян.
 *  `tube`: трубка на конце (не прозрачная поверх этикеток) — нет / надвинута / усажена. */
export type CoaxEndState = {
  contact: boolean; insert: boolean; support: boolean; housing: boolean; cap: boolean;
  shield: "none" | "loose" | "crimped" | "clamp" | "soldered";
  tube: "none" | "loose" | "shrunk"; tubeName: string;
};
export const EMPTY_STATE: CoaxEndState = { contact: false, insert: false, support: false, housing: false, cap: false, shield: "none", tube: "none", tubeName: "" };

const clampShield = (end: Row) => /прижим|зажим/i.test(str(end.shield));
const crimpShield = (end: Row) => !clampShield(end) && !/внутри/i.test(str(end.pinType)) && coaxFamily(end) !== "sma-ra";
const ANGLED: CoaxFamily[] = ["fakra-ra", "sma-ra", "angled"];

/** Состояние конца после ОДНОЙ операции монтажа — по её виду и коду (стадии маршрутов 708К: контакт во вставке → гильза
 *  обжата → пластмассовый корпус → уплотнитель и крышка; угловой FAKRA: контакт → гильза → металлическая крышка → корпус). */
export function coaxOpState(type: string, code: string, end: Row): CoaxEndState {
  const state: CoaxEndState = { ...EMPTY_STATE, contact: true, insert: true, shield: clampShield(end) ? "clamp" : crimpShield(end) ? "loose" : "none" };
  const c = code.toUpperCase();
  if (type === "coaxSolderShield") state.shield = "soldered";
  if (/_HOUSING$/.test(c)) { state.housing = true; if (state.shield === "loose") state.shield = "crimped"; if (/_RA_/.test(c)) state.cap = true; }
  if (/_SEAL$/.test(c) || (type === "coaxCapScrew" && /FAKRA/.test(c))) { state.housing = true; state.cap = true; if (state.shield === "loose") state.shield = "crimped"; }
  if (type === "coaxCapCrimp") { state.cap = true; if (state.shield === "loose") state.shield = "crimped"; }
  if (type === "coaxCapScrew" && !/FAKRA/.test(c)) { state.cap = true; if (coaxFamily(end) === "sma-ra") state.shield = "soldered"; }
  if (coaxFamily(end) === "hfm") state.support = true;
  return state;
}

// ── Детали разъёма в разрезе: локальные координаты — лицо в x = 0, кабель уходит вправо, ось y = 0 ──

export type Part = { id: string; name: string; svg: string; anchor: [number, number] };
const part = (id: string, name: string, anchor: [number, number], body: string, state = ""): Part =>
  ({ id, name, anchor, svg: `<g data-part="${id}"${state ? ` data-state="${state}"` : ""}>${body}</g>` });

/** Хвостовик контакта на жиле (x2 − 20…x2): чашка с припоем или гильза обжима с рифлением. */
function barrelArt(x2: number, solder: boolean): string {
  const barrel = x2 - 20;
  return rect(barrel, x2, -4, 8, BRASS, EDGE) + (solder ? `<ellipse cx="${f(barrel + 10)}" cy="0" rx="6" ry="3.4" fill="${SOLDER}" stroke="${EDGE}" stroke-width=".6"/>`
    : [4, 8, 12, 16].map((dx) => line(barrel + dx, -4, barrel + dx, 4, EDGE, .6)).join(""));
}

/** Центральный контакт: гнездо у розетки (втулка с открытым отверстием со стороны лица, без острия), штырь у вилки
 *  (стержень с фаской). Хвостовик — чашка с припоем или гильза обжима на жиле. */
export function contactPart(kind: ContactKind | boolean, x1: number, x2: number, solder: boolean): Part {
  const barrel = x2 - 20, gender: ContactKind = kind === true ? "socket" : kind === false ? "pin" : kind;
  let art = barrelArt(x2, solder);
  if (gender === "unknown") {
    art += rect(x1 + 4, barrel, -3.4, 6.8, BRASS, EDGE, 1);
    return part("contact", "контакт", [x1 + 24, 0], art, "unknown");
  }
  if (gender === "socket") {
    art += rect(x1, barrel, -4.5, 9, BRASS, EDGE) + `<rect data-part="socket-bore" x="${f(x1)}" y="-2" width="16" height="4" fill="${TUBE}"/>` +
      line(x1 + 16, 0, x1 + 26, 0, TUBE, .8);
    return part("contact", "гнездо", [x1 + 32, 0], art, "socket");
  }
  art += rect(barrel - 4, barrel, -4.5, 9, BRASS, EDGE) + rect(x1 + 6, barrel - 4, -2.2, 4.4, BRASS, EDGE) +
    `<polygon data-part="pin-tip" points="${f(x1)},0 ${f(x1 + 6)},-2.2 ${f(x1 + 6)},2.2" fill="${BRASS}" stroke="${EDGE}" stroke-width=".8"/>`;
  return part("contact", "штырь", [x1 + 24, 0], art, "pin");
}

/** Стенки тела вращения в разрезе: верхняя и нижняя полосы от радиуса `rIn` до `rOut`. */
const walls = (x1: number, x2: number, rIn: number, rOut: number, fill = METAL, stroke = EDGE) =>
  rect(x1, x2, -rOut, rOut - rIn, fill, stroke) + rect(x1, x2, rIn, rOut - rIn, fill, stroke);
const knurl = (x1: number, x2: number, rIn: number, rOut: number, step = 5) => {
  let art = "";
  for (let x = x1 + step; x < x2 - 1; x += step) art += line(x, -rOut, x - 2, -rIn, EDGE, .6) + line(x, rIn, x - 2, rOut, EDGE, .6);
  return art;
};

/** Хвост разъёма: диэлектрик кабеля, опорная гильза (HFM), оплётка по хвостовику, гильза экрана (обжата — на хвостовике,
 *  надета — сдвинута на оболочку), зажим, трубка и оболочка до `jacketTo`. */
function tailParts(end: Row, state: CoaxEndState, jacketTo: number, from = 96): Part[] {
  const parts: Part[] = [];
  parts.push(part("cable-dielectric", "диэлектрик", [from + 22, 0], rect(from + 8, from + 36, -4.5, 9, DIEL, INK)));
  if (state.support) parts.push(part("support", "опорная гильза под оплёткой", [from + 16, -5.8], rect(from + 2, from + 32, -6.8, 13.6, METAL, EDGE, 1)));
  if (state.shield !== "clamp") {
    parts.push(part("braid", "оплётка", [from + 32, -9], `<polygon points="${from + 2},-8 ${from + 24},-8 ${from + 40},-11 ${from + 40},11 ${from + 24},8 ${from + 2},8" fill="url(#etkBraid)" stroke="${EDGE}" stroke-width=".6"/>`));
  }
  if (state.shield === "crimped") {
    let art = rect(from + 4, from + 40, -13, 26, METAL, EDGE, 1);
    for (let x = from + 10; x < from + 38; x += 6) art += line(x, -13, x, 13, EDGE, .7);
    parts.push(part("ferrule", "гильза обжата", [from + 22, -11.5], art, "crimped"));
  }
  if (state.shield === "soldered") parts.push(part("solder", "экран припаян", [from + 30, -10], rect(from + 22, from + 40, -12, 24, SOLDER, EDGE, 6)));
  if (state.shield === "clamp") {
    parts.push(part("clamp", "кольцо и гайка", [from + 20, -12], rect(from, from + 40, -14, 28, METAL, EDGE, 2) +
      [8, 16, 24, 32].map((dx) => line(from + dx, -14, from + dx, 14, EDGE, .7)).join("")));
  }
  parts.push(part("jacket", str(end.cable) || "кабель", [Math.min(jacketTo - 8, from + 70), 0], rect(from + 40, jacketTo, -10, 20, JACKET, "#000", 2)));
  if (state.shield === "loose") {
    parts.push(part("ferrule", "гильза (надета)", [from + 64, -11], rect(from + 52, from + 76, -12.5, 25, METAL, EDGE, 1, 'fill-opacity=".85"'), "loose"));
  }
  if (state.tube === "shrunk") parts.push(part("tube", tubeShort(state.tubeName) || "трубка", [from + 46, -10], rect(from + 26, from + 66, -13.5, 27, TUBE, "#000", 5, 'fill-opacity=".92"'), "shrunk"));
  if (state.tube === "loose") parts.push(part("tube", tubeShort(state.tubeName) || "трубка", [from + 96, -12], rect(from + 82, from + 112, -14, 28, TUBE, "#000", 5, 'fill-opacity=".55"'), "loose"));
  return parts;
}

/** Разъём конца в разрезе по семейству и состоянию. SMA — проверенный боковой вид (гайка у вилки, резьба и шестигранник
 *  у розетки); остальные семейства — схема из их деталей: пластмассовый корпус FAKRA/HFM, четыре канала HFM, угловой корпус,
 *  крышка, диск проходного FAKRA. Контакт — гнездо или штырь по полу конца. */
export function connectorParts(end: Row, state: CoaxEndState, jacketTo = 190): Part[] {
  const family = coaxFamily(end), gender = contactKind(end), female = gender === "socket", solder = /пайк/i.test(str(end.pinType));
  const parts: Part[] = [];
  const insulator = (x1: number, x2: number, r: number, anchorX = (x1 + x2) / 2) =>
    part("insulator", "изолятор", [anchorX, -(r + 4.5) / 2], rect(x1, x2, -r, r * 2, DIEL, EDGE));
  const tail = () => tailParts(end, state, jacketTo);
  if (ANGLED.includes(family)) {
    // Угловой: ось стыковки вертикальна (лицо внизу), кабель входит справа; контакт — колено с гнездом/штырём вниз.
    if (state.housing) parts.push(part("housing", "пластмассовый корпус", [-4, 20], rect(-8, 54, -24, 80, PLASTIC, INK, 3)));
    let body = rect(0, 44, -14, 28, METAL, EDGE) + walls(44, 96, 5.5, 9) + rect(6, 11, 14, 40, METAL, EDGE) + rect(33, 38, 14, 40, METAL, EDGE);
    if (family === "sma-ra") for (let y = 18; y < 52; y += 5) body += line(33, y, 38, y - 2, EDGE, .6) + line(6, y, 11, y - 2, EDGE, .6);
    parts.push(part("body", family === "sma-ra" ? "угловой корпус" : "металлический корпус", [40, -10], body));
    parts.push(part("insulator", "изолятор", [30, 30], rect(11, 33, 14, 38, DIEL, EDGE) + rect(8, 44, -7, 14, DIEL, EDGE)));
    // Контакт-колено: на жиле — хвостовик, вниз к лицу — гнездо (отверстие внизу) или штырь с фаской.
    const tip = gender === "socket"
      ? rect(17.5, 26.5, 8, 42, BRASS, EDGE) + `<rect data-part="socket-bore" x="20" y="34" width="4" height="16" fill="${TUBE}"/>`
      : gender === "pin" ? rect(20, 24, 8, 36, BRASS, EDGE) + `<polygon data-part="pin-tip" points="22,50 20,44 24,44" fill="${BRASS}" stroke="${EDGE}" stroke-width=".8"/>`
        : rect(19, 25, 8, 40, BRASS, EDGE, 1);
    parts.push(part("contact", CONTACT_NAME[gender], [22, 24], rect(18, 84, -3, 6, BRASS, EDGE) + tip + barrelArt(104, solder), gender));
    if (state.cap) parts.push(part("cap", "крышка", [22, -16], rect(0, 44, -18, 4, METAL, INK, 1)));
    else parts.push(part("window", "окно", [22, -12.5], rect(14, 30, -14, 3, "#fff", EDGE)));
    return [...parts, ...tail()];
  }
  if (family === "hfm") {
    // QUAD: четыре канала в пластмассовом корпусе; в разрезе — свой канал, соседний — невидимым контуром.
    if (state.housing) {
      parts.push(part("housing", "корпус QUAD, 4 канала", [50, -24], rect(-6, 100, -36, 52, PLASTIC, INK, 3) +
        `<g data-part="channel-2">${hiddenLine(0, -31, 64, -31)}${hiddenLine(0, -19, 64, -19)}${hiddenLine(64, -31, 64, -19)}</g>`));
    }
    parts.push(part("body", "металлический корпус", [28, -7.8], walls(0, 56, 6.5, 9) + walls(56, 90, 6.5, 11) + knurl(56, 90, 6.5, 11) + walls(90, 112, 5, 7.5)));
    parts.push(insulator(2, 90, 6.5, 70));
    parts.push(contactPart(gender, 6, 104, solder));
    return [...parts, ...tail()];
  }
  if (family === "fakra" || family === "fakra-bh") {
    if (state.housing && family === "fakra") {
      parts.push(part("housing", "пластмассовый корпус", [56, -20], walls(-8, 98, 14, 26, PLASTIC, INK) +
        `<polygon points="14,-26 40,-26 36,-32 18,-32" fill="${PLASTIC}" stroke="${INK}" stroke-width=".9"/>` + rect(-8, 30, -14, 3, PLASTIC, INK)));
    }
    if (state.cap && family === "fakra") {
      parts.push(part("seal", "уплотнитель", [101, -11], rect(98, 104, -14, 28, RUBBER, "#000")));
      parts.push(part("cap", "крышка", [126, -14.5], walls(104, 150, 12, 17, PLASTIC, INK) + rect(146, 152, -17, 7, PLASTIC, INK) + rect(146, 152, 10, 7, PLASTIC, INK)));
    }
    let body = walls(0, 60, 8, 11) + walls(60, 96, 8, 14) + knurl(60, 96, 8, 14) + walls(96, 112, 5.5, 8);
    if (family === "fakra-bh") for (let x = 22; x < 58; x += 4) body += line(x, -11, x - 3, -14, EDGE, .7) + line(x, 11, x - 3, 14, EDGE, .7);
    if (family === "fakra-bh") body += rect(18, 58, -14, 3, METAL, EDGE) + rect(18, 58, 11, 3, METAL, EDGE);
    parts.push(part("body", "металлический корпус", [30, -9.5], body));
    parts.push(insulator(2, family === "fakra-bh" ? 54 : 92, 8, 46));
    if (family === "fakra-bh") parts.push(part("disc", "диск", [58, -6], rect(54, 62, -8, 16, "#e9e2cf", INK)));
    parts.push(contactPart(gender, female ? 8 : 4, 104, solder));
    return [...parts, ...tail()];
  }
  if (family === "sma" && gender !== "unknown") {
    if (female) {
      parts.push(part("body", "корпус с резьбой", [22, -9], walls(0, 44, 7, 11) + knurl(0, 44, 7, 11) +
        walls(44, 92, 10, 16) + line(60, -16, 60, -10) + line(76, -16, 76, -10) + line(60, 10, 60, 16) + line(76, 10, 76, 16) + walls(92, 112, 5.5, 8)));
      parts.push(insulator(44, 92, 10, 80));
      parts.push(contactPart(true, 6, 104, solder));
    } else {
      let nut = walls(0, 56, 10.5, 16) + rect(0, 7, -16, 32, "none", EDGE, 1) + knurl(9, 56, 7.5, 10.5, 4);
      [18, 30, 42].forEach((x) => { nut += line(x, -16, x, -10.5) + line(x, 10.5, x, 16); });
      parts.push(part("body", "накидная гайка", [28, -13.2], nut + walls(56, 96, 5, 10) + walls(96, 112, 5.5, 8)));
      parts.push(insulator(40, 96, 5, 70));
      parts.push(contactPart(false, 10, 104, solder));
    }
    return [...parts, ...tail()];
  }
  // N, TNC, RP-SMA, MMCX и прочие: схема — корпус (гайка с резьбой у N/TNC/RP-SMA и SMA без пола, защёлка MMCX), изолятор, контакт.
  let body = walls(60, 96, 8, 14) + walls(96, 112, 5.5, 8);
  const threaded = family === "n" || family === "tnc" || family === "rp-sma" || family === "sma";
  if (threaded) body += walls(0, 60, 16, 22) + knurl(0, 60, 16, 22, 4) + walls(20, 60, 8, 16);
  else if (family === "mmcx") body += walls(0, 60, 6, 9) + rect(0, 5, -11, 4, METAL, EDGE) + rect(0, 5, 7, 4, METAL, EDGE);
  else body += walls(0, 60, 8, 12);
  parts.push(part("body", threaded ? "гайка с резьбой" : "корпус", threaded ? [30, -19] : [30, -7.5], body));
  parts.push(insulator(family === "mmcx" ? 2 : 22, 92, family === "mmcx" ? 6 : 8, 76));
  parts.push(contactPart(gender, 6, 104, solder));
  return [...parts, ...tail()];
}

/** Детали одной строкой SVG (в локальных координатах). */
export const partsSvg = (parts: Part[]) => parts.map((item) => item.svg).join("");
/** Вертикальный размах деталей разъёма: для подбора масштаба. */
export function partsExtent(end: Row, state: CoaxEndState): [number, number] {
  const family = coaxFamily(end);
  if (ANGLED.includes(family)) return [state.housing ? -24 : -18, state.housing ? 56 : 54];
  if (family === "hfm") return [state.housing ? -36 : -14, state.housing ? 16 : 14];
  if (family === "fakra" && (state.housing || state.cap)) return [state.housing ? -32 : -17, state.housing ? 26 : 17];
  if (family === "n" || family === "tnc" || family === "rp-sma" || (family === "sma" && contactKind(end) === "unknown")) return [-22, 22];
  return [-17, 17];
}

// ── Разделанный конец с центральным контактом ──

/** Геометрия ступеней разделки конца: L1–L3 в масштабе на `span` px от кончика жилы `x0`; нет размеров — типовые 2,5 / 3,5 / 7,5. */
export function stepsOf(end: Row, x0: number, span: number): { x1: number; x2: number; x3: number } {
  const [l1, l2, l3] = [num(end.l1), num(end.l2), num(end.l3)];
  const [a, b, c] = l1 > 0 && l2 > l1 && l3 > l2 ? [l1, l2, l3] : [2.5, 3.5, 7.5];
  const k = span / c;
  return { x1: x0 + a * k, x2: x0 + b * k, x3: x0 + c * k };
}

/** Рисунок (без подписей) разделанного конца, сторона А — конец слева: оболочка до `jacketTo`, оплётка, диэлектрик, жила
 *  и, если `contact`, центральный контакт на жиле — штырь у вилки, гнездо у розетки, на припое или обжатый. */
export function endGraphics(end: Row, x0: number, cy: number, steps: { x1: number; x2: number; x3: number }, jacketTo: number, braid: string, contact = true): string {
  const { x1, x2, x3 } = steps, gender = contactKind(end);
  let art = rect(x3, jacketTo, cy - 16, 32, JACKET, "#000", 4) +
    rect(x2, x3, cy - 12, 24, braid) + rect(x1, x2, cy - 9, 18, DIEL, INK) + rect(x0, x1, cy - 2.5, 5, COPPER, INK);
  if (!contact) return art;
  const solder = !/обжим/i.test(str(end.pinType));
  art += `<g data-part="contact" data-state="${gender}">` + rect(x0 - 4, x1 - 2, cy - 5.5, 11, BRASS) +
    (solder ? `<ellipse cx="${f((x0 + x1) / 2)}" cy="${f(cy)}" rx="${f((x1 - x0) * 0.36)}" ry="4.6" fill="${SOLDER}" stroke="${EDGE}" stroke-width=".8"/>`
      : [0.25, 0.5, 0.75].map((k) => line(x0 + (x1 - x0) * k, cy - 5.5, x0 + (x1 - x0) * k, cy + 5.5, EDGE, .7)).join("")) +
    rect(x0 - 8, x0 - 4, cy - 8, 16, BRASS);
  art += gender === "unknown" ? rect(x0 - 30, x0 - 8, cy - 4, 8, BRASS, EDGE, 1) : gender === "socket"
    ? rect(x0 - 32, x0 - 8, cy - 5.5, 11, BRASS) + `<rect data-part="socket-bore" x="${f(x0 - 32)}" y="${f(cy - 2.6)}" width="10" height="5.2" fill="${TUBE}"/>`
    : rect(x0 - 32, x0 - 8, cy - 2.6, 5.2, BRASS) + `<polygon data-part="pin-tip" points="${f(x0 - 38)},${f(cy)} ${f(x0 - 32)},${f(cy - 2.6)} ${f(x0 - 32)},${f(cy + 2.6)}" fill="${BRASS}" stroke="${EDGE}" stroke-width=".8"/>`;
  return art + "</g>";
}

/** Строка заголовка компактного рисунка: «сторона А · FAKRA-Female (тип Z)»; без проверенной геометрии — «схема». */
function sideTitle(side: Side, end: Row, schematic: boolean): string {
  const kind = coaxEndNames(end).kind;
  return `сторона ${side === "A" ? "А" : "В"}${kind ? ` · ${kind}` : ""}${schematic ? " · схема" : ""}`;
}

/** Компактный рисунок одного конца: ПФ «Кабель с центральным контактом». Сторона В — зеркально. */
export function coaxContactDrawing(side: Side, end: Row): string {
  const mirror = side === "B", cy = 46, x0 = 46, steps = stepsOf(end, x0, 140);
  const art = endGraphics(end, x0, cy, steps, 312, "url(#pfBraid)");
  const crimp = /обжим/i.test(str(end.pinType));
  let body = label(4, 14, sideTitle(side, end, false), 12, 700, INK, "start") +
    (mirror ? `<g transform="translate(${W},0) scale(-1,1)">${art}</g>` : art);
  const gender = contactKind(end), neuter = gender === "socket", done = crimp ? (neuter ? "обжато" : "обжат") : (neuter ? "припаяно" : "припаян");
  body += label(mirror ? W - 4 : 4, cy + 40, `${contactName(end)} ${done} на жиле`, 11, 600, INK, mirror ? "end" : "start");
  return svg(cy + 48, body);
}

/** Компактный рисунок собранного конца в достигнутом состоянии: ПФ «Сторона … собрана». */
export function coaxMountedDrawing(side: Side, end: Row, state: CoaxEndState): string {
  const mirror = side === "B", [top, bottom] = partsExtent(end, state), s = Math.min(1, 46 / (bottom - top));
  const cy = 26 - top * s, schematic = !coaxGeometryKnown(end);
  const parts = connectorParts(end, state, (W - 16) / s);
  const art = `<g data-geometry="${schematic ? "schematic" : "section"}" transform="translate(${mirror ? W - 8 : 8},${f(cy)}) scale(${f(mirror ? -s : s)},${f(s)})">${partsSvg(parts)}</g>`;
  let body = label(4, 14, sideTitle(side, end, schematic), 12, 700, INK, "start") + art;
  const notes = [
    state.housing ? "корпус надет" : "", state.cap ? "крышка" : "",
    state.shield === "crimped" ? "гильза обжата" : state.shield === "loose" ? "гильза надета, не обжата" : state.shield === "clamp" ? "зажим: кольцо и гайка" : state.shield === "soldered" ? "экран припаян" : "",
    state.tube === "shrunk" ? `${tubeShort(state.tubeName) || "трубка"} усажена` : state.tube === "loose" ? `${tubeShort(state.tubeName) || "трубка"} надвинута, не усажена` : "",
  ].filter(Boolean);
  const lines = wrapSvgText(notes.join(" · "), 46), y = cy + bottom * s + 16;
  lines.forEach((text, index) => { body += label(mirror ? W - 4 : 4, y + index * 14, text, 11, 600, INK, mirror ? "end" : "start"); });
  return svg(y + Math.max(0, lines.length - 1) * 14 + 6, body);
}

// ── Изделие: вся сборка ──

const COLOR_HEX: Record<string, string> = { WH: "#ffffff", BU: "#2463c7", GN: "#2e9e4f", VT: "#7b4bb3", YE: "#f2c200", RD: "#d7322d", BK: "#222222", OG: "#f08a24", PK: "#e87ab0" };

export type CoaxAssemblyState = {
  marks?: Array<{ text: string; place: string }>; protect?: string; color?: { color: string; code: string } | null;
  ends?: [CoaxEndState, CoaxEndState]; lengthNote?: string; lengthSource?: LengthSource;
};

/** Готовый конец изделия (что стоит на нём после всех операций семейства): для старых ЭТК, у которых маршрут не
 *  содержит операций монтажа, — иначе изделие рисовалось бы голым кабелем. */
export function coaxFinalState(end: Row, tube: string): CoaxEndState {
  const family = coaxFamily(end);
  return { contact: true, insert: true, support: family === "hfm", housing: /fakra|hfm/.test(family) && family !== "fakra-bh",
    cap: ["fakra", "fakra-ra", "sma-ra"].includes(family), shield: clampShield(end) ? "clamp" : crimpShield(end) ? "crimped" : "soldered",
    tube: tube ? "shrunk" : "none", tubeName: tube };
}

/** Вся сборка: разъёмы А и В своего семейства, кабель, этикетки M1… на своих местах (без выдуманных расстояний) и
 *  прозрачные ТУТ поверх них, цвет разъёмов, размер L готового изделия между лицами разъёмов. Под рисунком — табличка:
 *  «А», «В», M1… с полными названиями — длинные названия разъёмов не налезают друг на друга. */
export function coaxAssemblyDrawing(a: Row, b: Row, lengthMm: string, tube: string, cable: string, extra: CoaxAssemblyState = {}): string {
  const s = 0.42, top = 22;
  const states = extra.ends ?? [coaxFinalState(a, tube), coaxFinalState(b, tube)];
  const extents = [partsExtent(a, states[0]), partsExtent(b, states[1])];
  const cy = top + 6 - Math.min(extents[0][0], extents[1][0]) * s;
  const reach = 160, place = (end: Row, state: CoaxEndState, mirror: boolean) =>
    `<g data-end="${mirror ? "B" : "A"}" data-geometry="${coaxGeometryKnown(end) ? "section" : "schematic"}" transform="translate(${mirror ? W - 6 : 6},${f(cy)}) scale(${f(mirror ? -s : s)},${f(s)})">${partsSvg(connectorParts(end, state, reach))}</g>`;
  const cableA = 6 + reach * s, cableB = W - 6 - reach * s;
  let body = rect(cableA - 2, cableB + 2, cy - 4.2, 8.4, JACKET, "#000", 2, 'data-part="cable"') + place(a, states[0], false) + place(b, states[1], true);
  // Цвет разъёмов — полоса на корпусе (только если задан в данных).
  const color = extra.color && extra.color.code ? COLOR_HEX[extra.color.code] ?? "" : "";
  if (color) [6 + 20 * s, W - 6 - 40 * s].forEach((x) => { body += `<rect data-part="conn-color" x="${f(x)}" y="${f(cy - 9)}" width="${f(20 * s)}" height="18" fill="${color}" stroke="${INK}" stroke-width=".8"/>`; });
  // Концевые метки «А» и «В» над разъёмами.
  body += label(6 + 40 * s, top - 6, "А", 13, 800, INK) + label(W - 6 - 40 * s, top - 6, "В", 13, 800, INK);
  // Этикетки M1…: середина — по центру, у А / у В — рядом с разъёмом; расстояния не выдумываются.
  const marks = (extra.marks ?? []).slice(0, 6), tags: Array<[string, string]> = [];
  const slot = (placeName: string, index: number, total: number) => {
    if (/^(A|А)$/i.test(placeName)) return cableA + 22 + index * 34;
    if (/^(B|В)$/i.test(placeName)) return cableB - 22 - index * 34;
    return W / 2 + (index - (total - 1) / 2) * 40;
  };
  const groups: Record<string, number> = {};
  marks.forEach((mark) => { groups[mark.place] = (groups[mark.place] ?? 0) + 1; });
  const seen: Record<string, number> = {};
  marks.forEach((mark, index) => {
    const tag = `M${index + 1}`, at = seen[mark.place] = (seen[mark.place] ?? -1) + 1, x = slot(mark.place, at, groups[mark.place]);
    body += `<g data-part="mark" data-tag="${tag}">` + rect(x - 13, x + 13, cy - 7, 14, "#fffef6", INK, 2) +
      (extra.protect ? rect(x - 16, x + 16, cy - 9, 18, "#cfe6ef", "#5f8796", 4, 'fill-opacity=".35" data-part="cover"') : "") +
      label(x, cy + 3.6, tag, 10, 800, INK) + "</g>";
    const where = /^(A|А)$/i.test(mark.place) ? "у конца А" : /^(B|В)$/i.test(mark.place) ? "у конца В" : "середина";
    tags.push([tag, `${mark.text} · ${where}`]);
  });
  // Размер L — между лицами разъёмов, выносные линии сплошные тонкие.
  const yd = cy + Math.max(extents[0][1], extents[1][1]) * s + 18;
  body += extensionLine(6, cy + extents[0][1] * s + 2, 6, yd + 4) + extensionLine(W - 6, cy + extents[1][1] * s + 2, W - 6, yd + 4) +
    dimension(6, W - 6, yd, lengthMm ? `L = ${lengthMm} мм` : "L — нет длины готового изделия", 12, 700);
  const entries: Array<[string, string]> = [
    ["А", [coaxKindTitle(a), str(a.connector || a.article || a.connName)].filter(Boolean).join(" · ") || "разъём"],
    ["В", [coaxKindTitle(b), str(b.connector || b.article || b.connName)].filter(Boolean).join(" · ") || "разъём"],
    ...tags,
  ];
  if (extra.protect && marks.length) entries.push(["ТУТ", `${extra.protect} поверх этикеток, усажена`]);
  // Трубки на концах — из того же состояния, что нарисовано: текст не расходится с рисунком.
  const tubeText = (state: CoaxEndState) => state.tube === "none" ? "" : `${tubeShort(state.tubeName) || state.tubeName || "трубка"} — ${state.tube === "shrunk" ? "усажена" : "надвинута, не усажена"}`;
  const [tubeA, tubeB] = states.map(tubeText);
  if (tubeA && tubeA === tubeB) entries.push(["ТУТ", tubeA.replace(" — ", " на концах — ")]);
  else { if (tubeA) entries.push(["ТУТ", `конец А: ${tubeA}`]); if (tubeB) entries.push(["ТУТ", `конец В: ${tubeB}`]); }
  if (color) entries.push(["Цвет", `разъёмов: ${extra.color!.color} (${extra.color!.code})`]);
  const source = extra.lengthSource === "name" ? "из названия изделия" : extra.lengthSource === "drawing" ? "из формы генератора (длина по чертежу)" : "по КД";
  entries.push(["L", lengthMm ? `длина готового изделия ${source}${extra.lengthNote ? ` · ${extra.lengthNote}` : ""}` : "длины готового изделия в данных нет — по КД"]);
  if (cable) entries.push(["Кабель", cable]);
  const legend = legendRows(entries, yd + 10);
  return svg(yd + 10 + legend.height + 4, body + legend.body);
}

// ── Кабель с надетыми деталями, разделанный кабель ──

/** Кабель с разделанными концами (без контактов) и тем, что на него надето: трубки, гильзы, этикетки M1… Середина —
 *  знаком разрыва. `rowsText` — расшифровка под рисунком. */
function coaxCableDrawing(a: Row, b: Row, items: Array<{ kind: "tube" | "cover" | "sleeve" | "mark"; place: string; tag?: string }>, legend: Array<[string, string]>): string {
  const cy = 26;
  const stA = stepsOf(a, 10, 36), stB = stepsOf(b, 10, 36);
  let body = endGraphics(a, 10, cy, stA, 190, "url(#pfBraid)", false) +
    `<g transform="translate(${W},0) scale(-1,1)">${endGraphics(b, 10, cy, stB, 114, "url(#pfBraid)", false)}</g>`;
  body += `<g data-part="break"><rect x="188" y="${cy - 18}" width="20" height="36" fill="#fff"/>${line(194, cy - 18, 186, cy + 18, INK, 1.2)}${line(210, cy - 18, 202, cy + 18, INK, 1.2)}</g>`;
  const counts: Record<string, number> = {};
  items.forEach((item) => {
    // Гильзы — у концов; трубки, прозрачные ТУТ и этикетки под ними — дальше от концов; середина — по центру.
    const key = `${item.kind}:${item.place}`, at = counts[key] = (counts[key] ?? -1) + 1, near = item.kind === "sleeve" ? 62 : 100;
    const x = item.place === "A" ? near + at * 30 : item.place === "B" ? W - near - at * 30 : 160 - at * 30;
    if (item.kind === "mark") body += `<g data-part="mark" data-tag="${esc(item.tag)}">${rect(x - 12, x + 12, cy - 10, 20, "#fffef6", INK, 2)}${label(x, cy + 4, item.tag ?? "", 10, 800)}</g>`;
    if (item.kind === "tube") body += rect(x - 13, x + 13, cy - 19, 38, TUBE, "#000", 5, 'fill-opacity=".6" data-part="tube" data-state="loose"');
    if (item.kind === "cover") body += rect(x - 13, x + 13, cy - 19, 38, "#cfe6ef", "#5f8796", 5, 'fill-opacity=".45" data-part="cover" data-state="loose"');
    if (item.kind === "sleeve") body += rect(x - 9, x + 9, cy - 18, 36, METAL, EDGE, 1, 'data-part="ferrule" data-state="loose"');
  });
  const table = legendRows(legend, cy + 26);
  return svg(cy + 26 + table.height + 4, body + table.body);
}

/** Нарезанные трубки коакса: на вид строка «ТУТ 4/2мм, прозрачная — 35 мм ×8, концевые» и отрезок в масштабе длины. */
function coaxTubeCutDrawing(operations: Row[]): string | null {
  const pieces: Array<{ name: string; len: number; text: string }> = [];
  operations.forEach((operation) => {
    const fields = obj(operation.fields), part = rows(operation.components)[0] ?? {};
    const name = str(obj(rows(fields.shrinkMaterials)[0]).article || part.art || "ТУТ");
    const protective = rows(fields.coaxProtectiveTubes);
    if (protective.length) {
      protective.forEach((row) => pieces.push({ name, len: num(row.length),
        text: `${name} — ${str(row.length)} мм${str(row.units) ? ` ×${str(row.units)}` : ""}, ${str(row.place) === "middle" ? "центральные" : "концевые"}` }));
    } else {
      const len = num(str(fields.lengthKd).replace(/мм.*$/i, ""));
      if (len > 0 || str(fields.lengthKd)) pieces.push({ name, len, text: `${name} — ${len > 0 ? `${String(len).replace(".", ",")} мм` : str(fields.lengthKd)}` });
    }
  });
  if (!pieces.length) return null;
  const longest = Math.max(1, ...pieces.map((piece) => piece.len));
  let body = "", y = 4;
  pieces.slice(0, 6).forEach((piece) => {
    const w = piece.len > 0 ? 40 + 120 * piece.len / longest : 100, clear = /прозрачн/i.test(piece.name);
    body += label(8, y + 12, piece.text, 11, 600, INK, "start") +
      rect(8, 8 + w, y + 17, 14, clear ? "#cfe6ef" : TUBE, clear ? "#5f8796" : "#000", 4, 'data-part="tube"');
    y += 38;
  });
  return svg(y + 2, body);
}

// ── Вход из etkPfArt ──

/** Длина готового изделия по КД: сохранённая `finishedLength` (основа длины — «готовое изделие»), иначе длина по чертежу
 *  из формы генератора, иначе из названия («… 275mm»). Длина реза в размер «лицо — лицо» НЕ подставляется. `note` —
 *  расхождение с названием. */
export type LengthSource = "finished" | "drawing" | "name";
export function assemblyLength(config: Row, name: string): { value: string; note: string; source?: LengthSource } {
  const coax = obj(config.coax), state = obj(obj(config.gen_state).S);
  const named = num(name.match(/(\d+(?:[.,]\d+)?)\s*(?:mm|мм)\b/i)?.[1]);
  const text = (value: number) => String(value).replace(".", ",");
  let value = 0, source: LengthSource = "name";
  // Основа «резка»: длины готового изделия в данных нет — только из названия; длина по чертежу формы — не «по КД».
  if (str(coax.lengthBasis) === "cut") value = named;
  else if (str(coax.lengthBasis) === "finished" || num(coax.finishedLength) > 0) { value = num(coax.finishedLength); source = "finished"; }
  else if (num(state.drawingLength) || num(state.sharedLengthText)) { value = num(state.drawingLength) || num(state.sharedLengthText); source = "drawing"; }
  else value = named;
  if (!(value > 0)) return { value: "", note: "" };
  return { value: text(value), source, note: source !== "name" && named > 0 && named !== value ? `в названии ${text(named)} мм — сверить с КД` : "" };
}

const sideOf = (value: unknown): Side | "" => (/^(A|А)$/i.test(str(value).trim()) ? "A" : /^(B|В)$/i.test(str(value).trim()) ? "B" : "");

/** Состояние конца `side` после операций документа до `upto` включительно: что смонтировано, гильза, трубка на конце.
 *  Операции без стороны относятся к сторонам своего ПФ (маршрут), ПФ без стороны — к обоим концам. */
export function coaxEndStateAt(document: Row, side: Side, upto: number, end: Row): CoaxEndState {
  const operations = rows(document.operations), blocks = rows(obj(document.route).blocks);
  const coax = obj(obj(document.build_config).coax);
  const covers = rows(coax.marks).length > 0 && !!coax.markProtect;
  const sidesOfBlock = (position: number): Side[] => {
    const block = blocks.find((item) => Array.isArray(item.ops) && (item.ops as unknown[]).map(Number).includes(position));
    const own = new Set((block ? (block.ops as unknown[]).map(Number) : [position])
      .map((at) => sideOf(obj(obj(operations[at - 1]).fields).coaxEnd ? obj(obj(obj(operations[at - 1]).fields).coaxEnd).side : ""))
      .filter(Boolean) as Side[]);
    return own.size ? [...own] : ["A", "B"];
  };
  let state: CoaxEndState = { ...EMPTY_STATE, shield: "none" };
  const endTube = covers ? "" : str(coax.tutArt || coax.tutName);
  operations.forEach((operation, index) => {
    const position = Math.round(num(operation.position)) || index + 1;
    if (position > upto) return;
    const fields = obj(operation.fields), type = str(operation.type), code = str(operation.code);
    const own = obj(fields.coaxEnd);
    const sides = Object.keys(own).length ? [sideOf(own.side) || "A"] : sidesOfBlock(position);
    if (!sides.includes(side)) return;
    if (type === "coaxPin" || type === "coaxPinCrimp") state.contact = true;
    if (type === "coaxSupportCrimp") state.support = true;
    if (type === "coaxInsSleeve" && state.shield === "none") state.shield = clampShield(end) ? "clamp" : "loose";
    if (type === "coaxShield") state.shield = "crimped";
    if (["coaxHousing", "coaxSolderShield", "coaxCapScrew", "coaxCapCrimp"].includes(type)) {
      const next = coaxOpState(type, code, end);
      state = { ...state, contact: true, insert: true, housing: state.housing || next.housing, cap: state.cap || next.cap,
        shield: next.shield === "crimped" || next.shield === "soldered" || next.shield === "clamp" ? (state.shield === "crimped" ? "crimped" : next.shield)
          : state.shield === "none" ? next.shield : state.shield };
    }
    const material = str(obj(rows(fields.shrinkMaterials)[0]).article);
    if (type === "coaxInsTut" && !/MARK/i.test(code) && endTube) state = { ...state, tube: state.tube === "shrunk" ? "shrunk" : "loose", tubeName: endTube };
    if (type === "coaxHeat" && !fields.coaxProtectiveHeat) {
      if (/BH_TTK/i.test(code) && material) state = { ...state, tube: "shrunk", tubeName: material };
      else if (endTube) state = { ...state, tube: "shrunk", tubeName: endTube };
    }
  });
  return state;
}

/** Рисунок ПФ коаксиальной сборки; ПФ без своей картинки и не коакс — null. `operations` — операции ПФ, `document` —
 *  весь документ ЭТК (состояние концов считается по маршруту до последней операции ПФ). */
export function coaxPfArt(group: EtkPfGroup, operations: Row[], config: Row, document: Row = {}): string | null {
  const coax = obj(config.coax);
  if (str(config.mode) !== "coax" || !Object.keys(coax).length || !operations.length) return null;
  const cableName = str(coax.cableArt || coax.cableName);
  const end = (side: Side) => ({ ...obj(coax[side === "A" ? "sideA" : "sideB"]), cable: cableName });
  const doc = Object.keys(document).length ? document : { operations, build_config: config };
  const marks = rows(coax.marks).map((mark) => ({ text: str(mark.text), place: str(mark.place) })).filter((mark) => mark.text);
  const covers = marks.length > 0 && !!coax.markProtect;
  const tubeName = str(coax.tutArt || coax.tutName), tube = tubeName ? tubeShort(tubeName) : "";
  const types = operations.map((operation) => str(operation.type));
  const upto = Math.max(...operations.map((operation, index) => Math.round(num(operation.position)) || index + 1));
  if (group.final) {
    const length = assemblyLength(config, group.name);
    const color = obj(coax.connColor), states: [CoaxEndState, CoaxEndState] = [coaxEndStateAt(doc, "A", upto, end("A")), coaxEndStateAt(doc, "B", upto, end("B"))];
    const known = Object.keys(document).length > 0;
    return coaxAssemblyDrawing(end("A"), end("B"), length.value, covers ? "" : tube, cableName || "кабель", {
      marks, protect: covers ? tube || "прозрачная ТУТ" : "", lengthNote: length.note, lengthSource: length.source,
      color: str(color.method) && str(color.method) !== "none" && str(color.code) ? { color: str(color.color), code: str(color.code) } : null,
      // Маршрут без операций монтажа конца (старые ЭТК) — готовый конец, а не голый кабель.
      ends: known ? states.map((state, index) => (state.insert || state.shield === "crimped" ? state
        : coaxFinalState(end(index ? "B" : "A"), covers ? "" : tube))) as [CoaxEndState, CoaxEndState] : undefined,
    });
  }
  const sides = [...new Set(operations.map((operation) => sideOf(obj(obj(operation.fields).coaxEnd).side)).filter(Boolean))] as Side[];
  const side: Side = sides[0] ?? (str(operations[operations.length - 1].side) === "B" ? "B" : "A");
  if (types.some((type) => ["coaxHousing", "coaxSolderShield", "coaxCapScrew", "coaxCapCrimp"].includes(type))) {
    const own = end(side), state = coaxEndStateAt(doc, side, upto, own);
    return coaxMountedDrawing(side, own, { ...state, contact: true, insert: true });
  }
  if (types.some((type) => type === "coaxPin" || type === "coaxPinCrimp")) return coaxContactDrawing(side, end(side));
  if (types.some((type) => type === "coaxInsTut" || type === "coaxInsSleeve" || type === "coaxSupportCrimp")) {
    const items: Array<{ kind: "tube" | "cover" | "sleeve" | "mark"; place: string; tag?: string }> = [];
    const legend: Array<[string, string]> = [];
    operations.forEach((operation) => {
      const parts = obj(obj(operation.fields).coaxParts), code = str(operation.code);
      if (rows(obj(operation.fields).coaxMarks).length || /MARK_LABEL/i.test(code)) {
        marks.forEach((mark, index) => {
          const place = sideOf(mark.place) || "middle";
          items.push({ kind: "mark", place, tag: `M${index + 1}` });
          legend.push([`M${index + 1}`, `${mark.text} · ${place === "A" ? "у конца А" : place === "B" ? "у конца В" : "середина"}`]);
        });
      } else if (str(operation.type) === "coaxInsTut") {
        const name = str(parts.name) || tubeName || "ТУТ", count = Math.max(1, Math.round(num(parts.count)) || 1);
        if (covers) {
          ["A", "middle", "B"].forEach((place) => items.push({ kind: "cover", place }));
          legend.push(["ТУТ", `${tubeShort(name) || name} ×${count} — сдвинуты к местам этикеток, не усажены`]);
        } else {
          ["A", "B"].forEach((place) => items.push({ kind: "tube", place }));
          legend.push(["ТУТ", `${tubeShort(name) || name} ×${count} — надвинуты, не усажены`]);
        }
      } else if (str(operation.type) === "coaxInsSleeve") {
        const sideHint = /BH_PREP/i.test(code) ? ["A"] : /ST_PREP/i.test(code) ? ["B"] : ["A", "B"];
        const which = sideHint.filter((place) => {
          const own = end(place as Side);
          return /BH_PREP/i.test(code) ? coaxFamily(own) === "fakra-bh" : /ST_PREP/i.test(code) ? coaxFamily(own) === "fakra" : true;
        });
        const at = which.length ? which : sideHint;
        at.forEach((place) => items.push({ kind: "sleeve", place }));
        const what = /BH_PREP/i.test(code) ? "гильза и диск" : /ST_PREP/i.test(code) ? "крышка, уплотнитель, гильза" : "гильза экрана";
        legend.push(["Гильза", `${what} — надеты (${at.map((place) => (place === "A" ? "А" : "В")).join(", ")}), не обжаты`]);
      } else if (str(operation.type) === "coaxSupportCrimp") {
        legend.push(["HFM", "опорная гильза обжата"]);
      }
    });
    return coaxCableDrawing(end("A"), end("B"), items, legend);
  }
  if (types.every((type) => type === "coaxCutTut")) return coaxTubeCutDrawing(operations);
  if (types.includes("coaxCut") || types.includes("coaxStrip")) {
    const cut = operations.find((operation) => str(operation.type) === "coaxCut");
    const length = str(obj(cut?.fields).lengthKd).replace(/\s*мм\s*$/i, "").trim();
    return coaxCableDrawing(end("A"), end("B"), [], [
      ["Рез", length ? `длина реза ${length} мм` : "длина реза — по КД"],
      ["Концы", "разделаны (размеры — на эскизе операции)"],
    ]);
  }
  return null;
}
