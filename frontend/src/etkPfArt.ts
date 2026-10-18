// Эскиз полуфабриката в схеме ПФ ЭТК (владелец 17.10: «нормальные эскизы полуфабрикатов» вместо списка ПФ слева).
// Рисунок ПФ — состояние изделия после его последней операции, собранное из готовых рисунков: эскизы операций etkDraw
// (провода с концами и цветами, нарезанные трубки, торец разъёма с проводами по гнёздам, свивка), кабель в оболочке —
// рисунком схемы ПФ генератора (RouteCanvas.CableArt), изделие — схема жгута (etkDrawHarness). Чего в etkDraw нет —
// сросток под трубкой, ветвь (владелец 17.10: «визуал полуфабриката оставляет желать лучшего» — разъём с проводами,
// свитая пара, оплётка с длиной, ТТК у разъёма, метка с надписью) и слои на пучке — рисуется здесь, тем же стилем.
// Считается при показе: выданные ЭТК перевыпускать не нужно.
import { connectorFace, connectorFaceUnits, topFeature, wireColorKnown, type TopFeature } from "./connectorArt";
import { connectorTopView } from "./connectorTopView";
import { twistedPairSvg } from "./twistArt";
import { etkCutEnds, etkDrawHarness, etkFigure, etkWireColor, type CutEndKind, type EtkHousing, type EtkTopologyData } from "./etkDraw";
import { formatMinutes, type EtkPfGroup } from "./etkRoute";
import type { WireBlankEnd } from "./harness/WireBlankCatalog";
import { coreColor } from "./pinoutCheck";
import { coresWord, type WireThumb } from "./routeCards";
import { harnessModel, harnessOverview, harnessTubeDrawing, spliceDrawing, spliceSketches, spliceSketchesPlain, tubeShort, type Drawing, type TubeBranch } from "./etkPfScheme";
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

function drawn(drawing: Drawing): string {
  return svg(drawing.width, drawing.height, drawing.body);
}

function svg(width: number, height: number, body: string): string {
  return `<svg class="etk-svg etk-pf-svg" viewBox="0 0 ${width} ${height}" width="100%" preserveAspectRatio="xMinYMin meet" xmlns="http://www.w3.org/2000/svg">` +
    `<defs><pattern id="pfBraid" width="7" height="7" patternUnits="userSpaceOnUse"><rect width="7" height="7" fill="#9aa1a6"/>` +
    `<path d="M0,0 L7,7 M7,0 L0,7" stroke="#4b5257" stroke-width="1.1"/></pattern>` +
    // Сетка оплётки ветви — прозрачная: свитая пара под ней видна в своих цветах.
    `<pattern id="pfMesh" width="8" height="8" patternUnits="userSpaceOnUse"><rect width="8" height="8" fill="#9aa1a6" opacity=".22"/>` +
    `<path d="M0,0 L8,8 M8,0 L0,8" stroke="#4b5257" stroke-width="1" opacity=".6"/></pattern>` +
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

/** Строка генератора операции ЭТК (`build_config.ops` по позиции, когда списки совпадают — как etk_builder._route_ops):
 *  её label, трубка, оплётка, ветвь; списки не совпадают или конфигурации нет — пустая строка. */
export function etkConfigOp(document: Row | null | undefined): (operation: Row) => Row {
  const own = rows(obj(document?.build_config).ops), operations = rows(document?.operations);
  const aligned = own.length > 0 && own.length === operations.length;
  return (operation) => (aligned ? own[Math.round(num(operation.position)) - 1] ?? {} : {});
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

/** Ветвь: разъём со своими проводами и то, что на них надето. Материалы и надписи — из операций ПФ и их строк
 *  генератора (`build_config.ops`: tutArt, wrapArt/wrapLen, label «Надеть маркировку X4 «A6» — «A6»»). */
export type PfBranch = {
  name: string; housing: EtkHousing | null; byPin: Record<number, string>; wires: string[];
  twist: { pairs: string[][]; pitch: string } | null;
  braid: { label: string; len: string } | null; wrap: { label: string; len: string } | null;
  sleeve: { label: string; heat: boolean } | null; marks: Array<{ text: string; place: string }>;
};

/** Надписи меток из строки генератора: «Надеть маркировку X4 «A6» — «A6», «A7»» → ["A6", "A7"]. */
function labelMarks(label: string): string[] {
  const tail = label.includes(" — ") ? label.slice(label.lastIndexOf(" — ") + 3) : "";
  return [...tail.matchAll(/«([^»]+)»/g)].map((match) => match[1]);
}

/** Длина строкой: «1000» → «1000 мм», «1 м» как есть. */
function mmText(value: unknown): string {
  const text = str(value).trim();
  return !text ? "" : /^\d+(?:[.,]\d+)?$/.test(text) ? `${text.replace(".", ",")} мм` : text;
}

/** Ветвь из операций ПФ: есть монтаж в разъём (корпус или распиновка) и хоть что-то на проводах — оплётка, лента,
 *  трубка, метка или свивка; иначе null. `source` — строка генератора той же операции. */
export function pfBranch(operations: Row[], source: (operation: Row) => Row, config: Row = {}, batch = 1): PfBranch | null {
  const conn = [...operations].reverse().find((operation) => operation.housing && typeof operation.housing === "object")
    ?? operations.find((operation) => rows(obj(operation.fields).wires).some((wire) => Array.isArray(wire.pins) && wire.pins.length));
  if (!conn) return null;
  const byPin: Record<number, string> = {};
  const wires = rows(obj(conn.fields).wires).map((wire) => {
    const name = str(wire.art || wire.name);
    (Array.isArray(wire.pins) ? wire.pins as number[] : []).forEach((pin) => { byPin[pin] = name; });
    return name;
  });
  const branch: PfBranch = { name: "", housing: conn.housing ? conn.housing as EtkHousing : null, byPin, wires,
    twist: null, braid: null, wrap: null, sleeve: null, marks: [] };
  const markRows = rows(config.markRows);
  operations.forEach((operation) => {
    const own = source(operation), parts = rows(operation.components);
    const kind = own.markTube ? "mark" : kindOf(operation);
    if (!branch.name) branch.name = str(own.branch).trim();
    if (kind === "twist") {
      const twist = obj(obj(operation.fields).twist);
      const pairs = str(twist.pairs).split("\n").map((line) => line.replace(/^\d+\)\s*/, "").split(" + ")).filter((pair) => pair.length === 2);
      branch.twist = { pairs: pairs.length ? pairs : [["", ""]], pitch: str(twist.pitch) };
    }
    if (kind === "braid" || (kind === "wrap" && own.wrapKind === "braid")) {
      branch.braid ??= { label: material({ art: own.wrapArt }) || material(parts[0] ?? {}) || "Оплётка", len: mmText(own.wrapLen) || perUnit(parts[0] ?? {}, batch) };
    } else if (kind === "wrap") {
      branch.wrap ??= { label: material({ art: own.wrapArt }) || material(parts[0] ?? {}) || "Лента", len: mmText(own.wrapLen) || perUnit(parts[0] ?? {}, batch) };
    }
    if (kind === "tube") {
      const label = str(own.tutArt) || material(parts[0] ?? {}) || str(own.tutName);
      branch.sleeve = { label: label || branch.sleeve?.label || "Термоусадка", heat: (branch.sleeve?.heat ?? false) || operation.type === "heatTut" };
    }
    if (kind === "mark") {
      const marks = rows(obj(operation.fields).marks).filter((mark) => str(mark.text).trim());
      const texts = marks.length ? marks.map((mark) => str(mark.text).trim()) : labelMarks(str(own.label));
      texts.forEach((text, index) => {
        if (branch.marks.some((mark) => mark.text === text)) return;
        const row = markRows.find((item) => str(item.text).trim() === text) ?? {};
        const place = str(marks[index]?.place) || (str(row.place) === "dist" && str(row.mm) ? `в ${str(row.mm)} мм от конца` : "");
        branch.marks.push({ text, place: /^[a-z]+$/i.test(place) ? "" : place });
      });
    }
  });
  const connector = str(conn.connector).trim();
  if (!branch.name && connector && !/^\d+$/.test(connector)) branch.name = connector;
  const art = str(conn.connector_article) || str(branch.housing?.art);
  if (art && !branch.name.includes(art)) branch.name = [branch.name, art].filter(Boolean).join(" · ");
  // Ветвь ПФ из нескольких операций (обжим и монтаж в разъём) — рисунком ветви и без надетого; одна операция монтажа —
  // её эскиз с легендой контактов, как был.
  const dressed = branch.twist || branch.braid || branch.wrap || branch.sleeve || branch.marks.length;
  return dressed || (branch.housing && operations.length > 1) ? branch : null;
}

/** Длина в мм из подписи: «110 мм» → 110, «1,2 м» → 1200; нет числа — 0. */
function lengthMm(text: string): number {
  const match = text.match(/(\d+(?:[.,]\d+)?)\s*(мм|м)(?![а-яё])/i);
  return match ? num(match[1]) * (match[2].toLowerCase() === "м" ? 1000 : 1) : 0;
}

/** Рисунок ветви слева направо: разъём (вид сверху, провода справа), провода выходят из него веером и сходятся в
 *  пучок; оплётка — от тыла разъёма под ТТК, сеткой с размером длины; ТТК — тёмной трубкой на тыле разъёма; метка —
 *  белой трубкой с надписью поверх оплётки; свитая пара — двумя жилами в своих цветах винтом с шагом в масштабе
 *  длины оплётки (20 мм на 110 мм — 5,5 витка), видна сквозь сетку и выходит из неё на открытом конце. */
export function pfBranchSvg(branch: PfBranch): string {
  const W = 700, H = 218, cy = 100, faceW = 190, faceH = 112, x0 = 12;
  let body = "";
  // Разъём: вид сверху (как в базе — развёрнутая объёмная модель), провода уходят вправо; серии без вида сверху —
  // торец по раскладке серии, вписанный в рамку; без корпуса — тёмный корпус-прямоугольник.
  let faceRight = x0 + 70, faceTop = cy - 30, faceBottom = cy + 30, topView = false, topSide: TopFeature | null = null;
  const view = branch.housing ? connectorTopView(branch.housing, 84, faceW - 40, branch.byPin) : null;
  if (branch.housing && view) {
    const fx = x0 + (faceW - 40 - view.h) / 2, fy = cy - view.w / 2;
    body += `<g data-part="connector" data-view="top" transform="matrix(0,1,1,0,${fx.toFixed(1)},${fy.toFixed(1)})">${view.body}</g>`;
    faceRight = fx + view.h; faceTop = fy; faceBottom = fy + view.w; topView = true; topSide = branch.housing ? topFeature(branch.housing) : null;
  } else if (branch.housing) {
    const units = Math.max(1, connectorFaceUnits(branch.housing));
    let s = Math.min(30, faceW / units / 1.15);
    let face = connectorFace(branch.housing, 0, 0, s, branch.byPin);
    if (face.h > faceH) { s *= faceH / face.h; face = connectorFace(branch.housing, 0, 0, s, branch.byPin); }
    const fx = x0 + (faceW - face.w) / 2, fy = cy - face.h / 2;
    body += `<g data-part="connector" transform="translate(${fx.toFixed(1)},${fy.toFixed(1)})">${face.body}</g>`;
    faceRight = fx + face.w; faceTop = fy; faceBottom = fy + face.h;
  } else {
    body += `<rect data-part="connector" x="${x0}" y="${cy - 30}" width="70" height="60" rx="6" fill="#3a4448" stroke="${INK}" stroke-width="1.2"/>`;
  }
  const label = (x: number, y: number, text: string, size = 14, weight = 600, color = INK) =>
    `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle" font-size="${size}" font-weight="${weight}" fill="${color}">${esc(text)}</text>`;
  body += `<text x="${x0}" y="22" font-size="20" font-weight="700" fill="${INK}">${esc(cut(branch.name || "Разъём", 40))}</text>` +
    `<text x="${x0}" y="${H - 6}" font-size="14" fill="${MUTED}">${esc(`разъём — ${topView ? `вид сверху, провода справа, ${topSide === "latch" ? "защёлка сверху" : "прорези вверх"}` : "вид со стороны проводов"}${branch.housing?.title ? `, ${branch.housing.title}` : ""}`)}</text>`;
  // Провода рисуются под всем надетым, когда известно, где начинается свивка: собираются в `wires` и встают сразу
  // после разъёма.
  const head = body;
  body = "";
  // Жилы: свитые пары (до двух) и прямые провода — те, что не в паре.
  const names = (branch.wires.length ? branch.wires : ["", ""]).slice(0, 10);
  const colors = names.map((name) => etkWireColor(name));
  const twisted = new Set((branch.twist?.pairs ?? []).flat().filter(Boolean));
  const pairs: Array<[string, string]> = branch.twist ? branch.twist.pairs.slice(0, 2).map((pair, index) => pair[0]
    ? [etkWireColor(pair[0]), etkWireColor(pair[1])] : [colors[index * 2] ?? "#2b2b2b", colors[index * 2 + 1] ?? "#d7322d"]) : [];
  const straight = branch.twist
    ? colors.filter((_, index) => (twisted.size ? !twisted.has(names[index]) : index >= pairs.length * 2)) : colors;
  const fanFrom = faceRight + 4, fanTo = fanFrom + 60, bundleEnd = W - 14, k = 2.8;
  const strandY = (index: number, count: number) => cy + (index - (count - 1) / 2) * k;
  let wires = "";
  // Веер от гнёзд к пучку.
  colors.forEach((color, index) => {
    const ya = faceTop + 6 + ((faceBottom - faceTop - 12) * (index + 0.5)) / colors.length, yb = strandY(index, colors.length);
    const d = `M${(fanFrom - 6).toFixed(1)},${ya.toFixed(1)} C${(fanFrom + 30).toFixed(1)},${ya.toFixed(1)} ${(fanTo - 30).toFixed(1)},${yb.toFixed(1)} ${fanTo.toFixed(1)},${yb.toFixed(1)}`;
    wires += `<path d="${d}" fill="none" stroke="${INK}" stroke-width="4.4"/><path d="${d}" fill="none" stroke="${color}" stroke-width="3"/>`;
  });
  // Пучок прямых жил от `from` до `to`.
  const bundle = (from: number, to: number, list: string[]) => {
    if (!list.length || to - from < 1) return "";
    return `<rect x="${from.toFixed(1)}" y="${(strandY(0, list.length) - 3).toFixed(1)}" width="${(to - from).toFixed(1)}" height="${(list.length * k + 3).toFixed(1)}" rx="3" fill="${INK}"/>` +
      list.map((color, index) => `<line x1="${from.toFixed(1)}" y1="${strandY(index, list.length).toFixed(1)}" x2="${to.toFixed(1)}" y2="${strandY(index, list.length).toFixed(1)}" stroke="${color}" stroke-width="${k - 0.6}"/>`).join("");
  };
  // Свивка: пары винтом от `from` до открытого конца — спереди/сзади на каждом перекрёстке (twistArt); две пары — двумя
  // тонкими винтами. В атрибутах — шаг в px и витков на мерной длине (оплётки или участка).
  const helix = (from: number, pitchPx: number, turns: number) => {
    const amp = pairs.length > 1 ? 4.5 : 7, offsets = pairs.length > 1 ? [-6, 6] : [0], width = pairs.length > 1 ? 3.2 : 4.4;
    return `<g data-part="twist" data-pitch-px="${pitchPx.toFixed(1)}" data-turns="${turns.toFixed(2)}">` +
      pairs.map((pair, index) => twistedPairSvg(from, bundleEnd + 2, cy + offsets[index], pitchPx, amp, pair, width, INK).replaceAll(' data-part="twist"', "")).join("") +
      "</g>";
  };
  // Что надето. Оплётка начинается у тыла разъёма и идёт под ТТК; ТТК надета на тыл разъёма поверх начала оплётки;
  // метка — поверх оплётки чуть дальше ТТК; свивка — внутри оплётки (видна сквозь сетку) и выходит из неё на открытом
  // конце. Без оплётки — ТТК, метка вплотную, лента, и свивка отдельным участком на открытом конце.
  const twistPairs = branch.twist ? branch.twist.pairs.filter((item) => item[0]).length : 0;
  const pitchMm = num(branch.twist?.pitch);
  // Шаг в px: в масштабе длины оплётки; без неё — 3,5 px на мм; слишком частый — не мельче 22 px, чтобы не слиться.
  const pitchOf = (pxPerMm: number) => (pitchMm > 0 ? Math.min(240, Math.max(22, pitchMm * pxPerMm)) : 60);
  const sleeveX = faceRight - 12, sleeveW = 104, sleeveEnd = sleeveX + sleeveW;
  const braidX1 = bundleEnd - (branch.twist ? 64 : 36);
  const lenMm = lengthMm(branch.braid?.len ?? "");
  const drawSleeve = () => {
    // Подпись ТТК — под трубкой, чтобы не лезла на метку и оплётку.
    if (!branch.sleeve) return;
    const tx = faceRight + 6;
    body += `<rect data-part="sleeve" x="${sleeveX.toFixed(1)}" y="${cy - 20}" width="${sleeveW}" height="40" rx="10" fill="#2f3b40" opacity=".96" stroke="${INK}" stroke-width="1.2"/>` +
      `<text x="${tx.toFixed(1)}" y="${cy + 70}" font-size="17" font-weight="700" fill="${INK}">${esc(cut(branch.sleeve.label, 16))}</text>` +
      `<text x="${tx.toFixed(1)}" y="${cy + 88}" font-size="15" fill="${MUTED}">${branch.sleeve.heat ? "усажена на разъёме" : "у разъёма"}</text>`;
  };
  const drawMarks = (from: number): number => {
    let mx = from;
    branch.marks.slice(0, 2).forEach((mark) => {
      const w = Math.max(64, Math.min(130, mark.text.length * 14 + 28));
      body += `<rect data-part="mark" x="${mx.toFixed(1)}" y="${cy - 18}" width="${w}" height="36" rx="6" fill="#fffef6" stroke="${INK}" stroke-width="1.3"/>` +
        label(mx + w / 2, cy + 7, cut(mark.text, 9), 24, 800) +
        label(mx + w / 2, cy - 27, "маркировка", 15, 400, MUTED) +
        (mark.place ? label(mx + w / 2, cy - 45, cut(mark.place, 22), 15, 400, MUTED) : "");
      mx += w + 4;
    });
    return mx;
  };
  const dimension = (xa: number, xb: number, text: string) => {
    const yd = cy + 46, top = Math.max(cy + 22, faceBottom + 2);
    body += `<line x1="${xa.toFixed(1)}" y1="${top.toFixed(1)}" x2="${xa.toFixed(1)}" y2="${yd + 6}" stroke="#9aa7ad" stroke-dasharray="3 3"/>` +
      `<line x1="${xb.toFixed(1)}" y1="${cy + 16}" x2="${xb.toFixed(1)}" y2="${yd + 6}" stroke="#9aa7ad" stroke-dasharray="3 3"/>` +
      `<line x1="${xa.toFixed(1)}" y1="${yd}" x2="${xb.toFixed(1)}" y2="${yd}" stroke="${INK}" stroke-width="1"/>` +
      `<path d="M${xa.toFixed(1)},${yd} l10,-4 v8 z M${xb.toFixed(1)},${yd} l-10,-4 v8 z" fill="${INK}"/>` +
      `<rect x="${((xa + xb) / 2 - 58).toFixed(1)}" y="${yd - 11}" width="116" height="22" fill="#fff"/>` +
      label((xa + xb) / 2, yd + 5, text, 17, 700);
  };
  let x = fanTo + 10, inside = false;
  if (branch.braid) {
    const bx = faceRight, braid = branch.braid;
    // Свивка внутри оплётки: пара под прозрачной сеткой, шаг — в масштабе длины оплётки, если она известна.
    wires += `<rect x="${bx.toFixed(1)}" y="${cy - 15}" width="${(braidX1 - bx).toFixed(1)}" height="30" rx="8" fill="#e4e8ea"/>`;
    if (branch.twist) {
      const pitchPx = pitchOf(lenMm > 0 ? (braidX1 - bx) / lenMm : 3.5);
      wires += bundle(fanTo, bundleEnd, straight) + helix(fanTo, pitchPx, (braidX1 - bx) / pitchPx);
      inside = true;
    } else {
      wires += bundle(fanTo, bundleEnd, colors);
    }
    body += `<rect data-part="braid" x="${bx.toFixed(1)}" y="${cy - 15}" width="${(braidX1 - bx).toFixed(1)}" height="30" rx="8" fill="url(#pfMesh)" stroke="${INK}" stroke-width="1"/>`;
    drawSleeve();
    const markEnd = branch.marks.length ? drawMarks(branch.sleeve ? sleeveEnd + 12 : faceRight + 24) : (branch.sleeve ? sleeveEnd : faceRight);
    // Название оплётки — над свободной частью оплётки, правее метки; длина — от тыла разъёма до конца оплётки.
    const nameFrom = Math.max(markEnd + (branch.marks.length ? 50 : 0), faceRight + 20);
    body += label((nameFrom + braidX1) / 2, cy - 24, cut(braid.label, 30), 17, 700);
    if (braid.len) dimension(bx, braidX1, braid.len);
    if (branch.wrap) body += label((bx + braidX1) / 2, cy + 66, `под оплёткой ${cut(branch.wrap.label, 24)}`, 15, 400, MUTED);
    if (inside) body += `<text x="${bundleEnd.toFixed(1)}" y="${cy + 88}" text-anchor="end" font-size="15" fill="${MUTED}">${esc(`свивка${twistPairs > 1 ? ` ×${twistPairs}` : ""}, ${pitchMm > 0 ? `шаг ${branch.twist!.pitch} мм` : "шаг по КД"}`)}</text>`;
  } else {
    drawSleeve();
    if (branch.sleeve) x = sleeveEnd + 4;
    x = drawMarks(x);
    if (branch.wrap) {
      const x1 = bundleEnd - (branch.twist ? 150 : 36);
      if (x1 - x > 80) {
        body += `<rect data-part="wrap" x="${x.toFixed(1)}" y="${cy - 15}" width="${(x1 - x).toFixed(1)}" height="30" rx="8" fill="url(#pfWrap)" stroke="${INK}" stroke-width="1"/>` +
          label((x + x1) / 2, cy - 24, cut(branch.wrap.label, 30), 17, 700);
        if (branch.wrap.len) dimension(x, x1, branch.wrap.len);
        x = x1;
      }
    }
  }
  if (branch.twist && !inside) {
    // Свитая пара без оплётки — пучком до открытого конца, там отдельным участком винтом; прямые жилы — до конца.
    const from = x + 14, to = bundleEnd + 2, span = to - from, pitchPx = pitchOf(3.5);
    wires += bundle(fanTo, from, colors) + bundle(from, bundleEnd, straight) + helix(from, pitchPx, span / pitchPx);
    body += label(from + span / 2, cy - 22, `свивка${twistPairs > 1 ? ` ×${twistPairs}` : ""}`, 16, 700) +
      label(from + span / 2, cy + 38, pitchMm > 0 ? `шаг ${branch.twist.pitch} мм` : "шаг по КД", 15, 400, MUTED);
  } else if (!branch.braid) {
    wires += bundle(fanTo, bundleEnd, colors);
  }
  return svg(W, H, head + wires + body);
}

/** Сростки ПФ: по эскизу на каждый — провода в своих цветах, трубка поверх. Какие провода в каком сростке и куда идут их
 *  концы — по снимку формы генератора (`build_config.gen_state`); без него — по числу сростков и цветам входа. */
function spliceArt(operations: Row[], source: (operation: Row) => Row, config: Row, inputs: string[], topology: EtkTopologyData): string {
  const own = operations.map((operation) => source(operation)).find((item) => str(item.type) === "splice") ?? {};
  const names = [...str(own.branch).matchAll(/[СC]\s*(\d+)/g)].map((match) => `С${match[1]}`);
  const label = str(own.label), model = harnessModel(config);
  const tubeText = spliceTube(inputs, operations);
  const tube = tubeText ? tubeShort(tubeText) : model?.spliceTube ?? "";
  const len = model?.spliceTubeLen ?? "";
  const tubeLabel = [tube, len && `${len} мм`].filter(Boolean).join(" · ");
  const sketches = model && names.length ? spliceSketches(model, names) : [];
  if (sketches.length) return drawn(spliceDrawing(sketches, tubeLabel));
  const count = Math.round(num(own.units)) || Math.round(num(spliceCount(topology).replace("×", ""))) || names.length || 1;
  const size = Math.round(num(label.match(/по\s+(\d+)\s+провод/)?.[1])) || 2;
  const shown = names.length ? names : Array.from({ length: count }, (_, index) => `С${index + 1}`);
  return drawn(spliceDrawing(spliceSketchesPlain(shown, size, wireColors(operations)), tubeLabel));
}

/** ПФ «ТУТ на месте Б»: ветви (разъёмы стороны А с цветами проводов) сходятся под одну трубку на жгуте, справа выходят
 *  провода к разъёмам стороны Б. Разъёмы и провода — из операций монтажа в `build_config.ops`; нет ветвей — null. */
function harnessTubeArt(operations: Row[], source: (operation: Row) => Row, config: Row, batch: number): string | null {
  const branches = (side: string): TubeBranch[] => {
    const seen = new Set<string>();
    return rows(config.ops).flatMap((op) => {
      const ep = obj(op.ep), label = str(ep.label).trim();
      if (str(op.type) !== "insTerm" || str(op.side) !== side || !label || seen.has(label)) return [];
      seen.add(label);
      return [{ label, colors: rows(op.wires).map((wire) => etkWireColor(str(wire.art || wire.name))) }];
    });
  };
  const left = branches("A"), right = branches("B");
  if (!left.length || !right.length) return null;
  const parts = operations.flatMap((operation) => rows(operation.components)).find((row) => /ТТК|ТУТ|трубк|термоус/i.test(`${str(row.art)} ${str(row.name)}`));
  const heat = operations.map((operation) => str(source(operation).label)).join(" ");
  const model = harnessModel(config);
  const tube = parts ? tubeShort(material(parts)) : model?.harnessTube || (heat ? tubeShort(heat) : "");
  const len = model?.harnessTubeLen || (parts ? perUnit(parts, batch).replace(/\s*мм$/, "") : "");
  return drawn(harnessTubeDrawing(left, right, tube, /^\d/.test(len) ? len : ""));
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
    // Плоский обзор жгута по снимку генератора; нет снимка — схема жгута по топологии.
    const model = harnessModel(config);
    const harness = model ? drawn(harnessOverview(model)) : etkDrawHarness(topology, pins);
    if (harness) main = { svg: harness };
  }
  // Ветвь — разъём с проводами и всем, что на них надето, одним рисунком (слоёв под ним нет: они на рисунке).
  const source = etkConfigOp(document);
  const branch = group.final || kinds.includes("splice") ? null : pfBranch(operations, source, config, batch);
  if (branch) return { main: { svg: pfBranchSvg(branch) }, layers: "" };
  // ПФ «ТУТ на месте Б» — трубка на жгуте (операции с флагом `harness`): ветви сходятся под одну трубку. Проверяется после
  // ветви: у ветви (монтаж в разъём) ТТК тоже с флагом `harness` — её ПФ рисуется ветвью, как раньше (владелец 18.10).
  if (!group.final && !kinds.includes("splice") && operations.some((operation) => source(operation).harness)
    && !operations.some((operation) => str(source(operation).type) === "insTerm")) {
    const tubes = harnessTubeArt(operations, source, config, batch);
    if (tubes) return { main: { svg: tubes }, layers: "" };
  }
  if (!main && kinds.includes("splice")) main = { svg: spliceArt(operations, source, config, inputs, topology) };
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
