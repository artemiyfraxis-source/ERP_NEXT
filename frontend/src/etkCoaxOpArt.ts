// Эскизы операций коакса в ЭТК на тех же деталях и цветах, что картинки ПФ (etkPfCoaxArt): «Надеть ТУТ / гильзу» — оба
// разделанных конца крупно, деталь надвинута на оболочку, середина — знак разрыва; «Монтаж корпуса» — разъём СВОЕГО семейства
// в разрезе (SMA — боковой вид; FAKRA, HFM, N, MMCX, угловые — схема с пометкой «без масштаба», не подставной SMA), контакт —
// гнездо у розетки и штырь у вилки, детали — те, что стоят на этой стадии. Владелец 23.11: «эскиз слабый, очень много
// нарушений» — подписи короткими названиями на полках, выноски сплошные тонкие и заканчиваются точкой на самой детали,
// выносные линии сплошные (ГОСТ 2.303), выносной элемент «А» без выдуманного масштаба (ГОСТ 2.305 п. 8.2), число заглубления —
// на размерной линии (ГОСТ 2.307). Холст 640 px, подписи не мельче 12.
import { coaxKindTitle } from "./coaxNames";
import { tubeShort } from "./etkPfScheme";
import {
  BRASS, callouts, coaxFamily, coaxFamilyTitle, coaxGeometryKnown, connectorParts, DIEL, dimension, EDGE, EMPTY_STATE, endGraphics, extensionLine,
  INK, isFemale, JACKET, label, line, METAL, MUTED, partsExtent, partsSvg, rect, stepsOf, THIN, TUBE, type Callout, type CoaxEndState,
} from "./etkPfCoaxArt";

type Row = Record<string, unknown>;
const str = (value: unknown): string => (value == null ? "" : String(value));
const f = (value: number) => value.toFixed(1);
/** Стрелка направления действия (не размерная): остриё в (x, y). */
const actionHead = (x: number, y: number, dir: 1 | -1) => `<path d="M${f(x)},${f(y)} l${9 * dir},-3.5 v7 z" fill="${INK}"/>`;

/** Название конца в заголовке: «Сторона А — SMA-Male (вилка)»; типа разъёма нет — по артикулу. */
function endCaption(end: Row): string {
  const side = ["В", "B"].includes(str(end.side).trim()) ? "В" : "А";
  return `Сторона ${side} — ${coaxKindTitle(end) || str(end.connector || end.article).trim() || "разъём"}`;
}

/** «Надеть: ТУТ / гильза экрана»: оба разделанных конца крупно (контакта на этой стадии ещё нет — рисуется только разделка),
 *  деталь надвинута на оболочку на расстоянии от торца, стрелка — со свободного конца; середина кабеля — знак разрыва. */
export function coaxPartsEtk(ends: Row[], kind: "tube" | "sleeve", name: string, offsetMm: string): { height: number; body: string } {
  const x0 = 70, brk = [428, 458], arrowFrom = 592;
  let body = "";
  ends.slice(0, 2).forEach((end, index) => {
    const top = 30 + index * 150, cy = top + 72, steps = stepsOf(end, x0, 150), { x1, x2, x3 } = steps;
    body += label(8, top + 14, endCaption(end), 14, 700, INK, "start");
    body += endGraphics(end, x0, cy, steps, brk[0], "url(#etkBraid)", false) + rect(brk[1], 600, cy - 16, 32, JACKET, "#000", 4);
    body += `<g data-part="break">${line(brk[0] + 8, cy - 22, brk[0] - 8, cy + 22, INK, 1.6)}${line(brk[1] + 8, cy - 22, brk[1] - 8, cy + 22, INK, 1.6)}</g>`;
    // Прижимной экран (владелец 19.10): вместо гильзы — гайка, шайба, уплотнитель и зажимное кольцо (конус) на оболочке.
    const clamp = kind === "sleeve" && /прижим|зажим/i.test(str(end.shield));
    const from = x3 + 36, width = kind === "tube" ? 96 : clamp ? 104 : 46, to = from + width;
    const marks: Callout[] = [
      { text: "жила", anchor: [(x0 + x1) / 2, cy], target: "conductor" },
      { text: "диэлектрик", anchor: [(x1 + x2) / 2, cy - 6], target: "dielectric" },
      { text: "оплётка", anchor: [(x2 + x3) / 2, cy - 8], target: "braid" },
    ];
    let below: Callout | null = null;
    if (kind === "tube") {
      body += rect(from, to, cy - 21, 42, TUBE, "#000", 7, 'fill-opacity=".92" data-part="tube"') + label((from + to) / 2, cy + 4.5, tubeShort(name) || "ТУТ", 13, 700, "#fff");
    } else if (clamp) {
      body += `<g data-part="clamp-set">` + rect(from, from + 36, cy - 22, 44, METAL, EDGE, 2);
      for (let x = from + 7; x < from + 33; x += 7) body += line(x, cy - 22, x, cy + 22, EDGE, .8);
      body += rect(from + 40, from + 46, cy - 20, 40, "#aab2b7", EDGE, 1) + rect(from + 50, from + 66, cy - 16, 32, "#4b5257", EDGE, 3) +
        `<polygon points="${f(from + 72)},${f(cy - 8)} ${f(to)},${f(cy - 18)} ${f(to)},${f(cy + 18)} ${f(from + 72)},${f(cy + 8)}" fill="${METAL}" stroke="${EDGE}" stroke-width=".9"/></g>`;
      below = { text: "гайка, шайба, уплотнитель, кольцо", anchor: [from + 18, cy + 14], target: "clamp-set" };
    } else {
      body += rect(from, to, cy - 19, 38, METAL, EDGE, 3, 'data-part="sleeve"');
      for (let x = from + 7; x < to - 3; x += 6) body += line(x, cy - 19, x, cy + 19, EDGE, .8);
      below = { text: "гильза экрана", anchor: [(from + to) / 2, cy + 14], target: "sleeve" };
    }
    body += callouts(marks, cy - 44, 8, from - 12, 12);
    // Стрелка действия: надвигать со свободного конца к торцу.
    body += `<g data-role="action">${line(arrowFrom, cy - 31, to + 10, cy - 31, INK, 1.2)}${actionHead(to + 10, cy - 31, -1)}</g>` +
      label(arrowFrom, cy - 37, "надвинуть со свободного конца", 12, 600, MUTED, "end");
    // Размер: от торца оболочки до кромки детали; число — на размерной линии, выносные — сплошные тонкие.
    const yd = cy + 38;
    body += extensionLine(x3, cy + 18, x3, yd + 4) + extensionLine(from, cy + 23, from, yd + 4) + dimension(x3, from, yd, offsetMm, 12, 700) +
      label(from + 8, yd + 4, offsetMm ? "мм от торца оболочки" : "отодвинуть от торца оболочки", 12, 600, INK, "start");
    if (below) body += callouts([below], cy + 62, from - 40, 630, 12);
    body += label(528, cy + 4.5, str(end.cable) || "кабель", 12, 600, "#fff");
  });
  return { height: 30 + Math.min(2, ends.length) * 150 + 4, body };
}

const TOP_PARTS = ["housing", "body", "insulator", "cap", "seal", "window", "disc"];
const BOTTOM_PARTS = ["contact", "braid", "ferrule", "support", "solder", "clamp", "tube"];

/** Монтаж корпуса: разъём семейства конца в разрезе в состоянии после операции (`state`). Подписи — короткие названия
 *  деталей на полках сверху и снизу, выноски — к самим деталям. Под видом — пометка о геометрии; заглубление контакта —
 *  выносным элементом «А». */
export function coaxMountEtk(end: Row, title: string, hint: string, state: CoaxEndState = { ...EMPTY_STATE, contact: true, insert: true, shield: "loose" }): { height: number; body: string } {
  const family = coaxFamily(end), known = coaxGeometryKnown(end), [top, bottom] = partsExtent(end, state);
  const s = Math.min(2.6, 120 / (bottom - top)), artTop = 104, cy = artTop - top * s;
  const X0 = 64, gx = (x: number) => X0 + s * x, gy = (y: number) => cy + s * y;
  const parts = connectorParts(end, state, 540 / s);
  let body = label(8, 20, title, 14, 700, INK, "start") + label(8, 40, hint, 12, 400, MUTED, "start");
  const article = str(end.connector || end.article).trim();
  body += label(8, 58, known ? `Разрез ${coaxFamilyTitle(end)}, без масштаба`
    : `Схема: ${coaxFamilyTitle(end)}, без масштаба · форма деталей — по чертежу изготовителя${article ? ` ${article}` : ""}`, 12, 400, MUTED, "start");
  body += `<g data-geometry="${known ? "section" : "schematic"}" data-family="${family}" transform="translate(${X0},${f(cy)}) scale(${f(s)})">${partsSvg(parts)}</g>`;
  const pick = (ids: string[]) => parts.filter((item) => ids.includes(item.id))
    .map((item): Callout => ({ text: item.name, anchor: [gx(item.anchor[0]), gy(item.anchor[1])], target: item.id }));
  body += callouts(pick(TOP_PARTS), artTop - 16, 8, 632, 12);
  const rowBottom = gy(bottom) + 30;
  body += callouts(pick(BOTTOM_PARTS), rowBottom, 8, 632, 12);
  const jacket = parts.find((item) => item.id === "jacket");
  if (jacket) body += label(X0 + 540 - 44, cy + 4.5, str(end.cable) || "кабель", 12, 600, "#fff");
  const depth = depthText(end.contactDepth);
  let height = rowBottom + 14;
  if (!depth) return { height, body };
  if (family === "fakra-ra" || family === "sma-ra") {
    // Угловой: лицо стыковки внизу — выносной элемент по оси кабеля был бы неверен; только величина.
    return { height: height + 20, body: body + label(8, height + 10, `Заглубление контакта ${depth} — от торца корпуса, по чертежу изготовителя`, 12, 600, INK, "start") };
  }
  const detail = depthCallout(depth, gx(4), cy, height + 22, isFemale(end));
  return { height: detail.bottom + 8, body: body + detail.body };
}

/** «0,38…1,14» / «0,38-1,14» → «0,38…1,14 мм»; «не менее 2,50» → «не менее 2,50 мм»; «9,14 (номинал)» → «9,14 мм (номинал)»;
 *  без числа — пусто (ничего не рисуется). */
export function depthText(value: unknown): string {
  const raw = str(value).trim();
  const bound = /^(не\s+менее|не\s+более)\s+(\d+(?:[.,]\d+)?)$/i.exec(raw);
  if (bound) return `${bound[1].toLowerCase().replace(/\s+/g, " ")} ${bound[2].replace(/\./g, ",")} мм`;
  const nominal = /^(\d+(?:[.,]\d+)?)\s*\(номинал\)$/i.exec(raw);
  if (nominal) return `${nominal[1].replace(/\./g, ",")} мм (номинал)`;
  const text = raw.replace(/\s*(?:\.{2,3}|…|–|—|-)\s*/g, "…");
  return /^\d+(?:[.,]\d+)?(?:…\d+(?:[.,]\d+)?)?$/.test(text) ? `${text.replace(/\./g, ",")} мм` : "";
}

/** Выносной элемент «А» (ГОСТ 2.305 п. 8.2): на виде — сплошная тонкая замкнутая линия вокруг лица разъёма и обозначение «А»
 *  на полке линии-выноски; ниже — вид А в круге, заголовок «А» без масштаба (масштаб не соблюдается — так и написано рядом),
 *  торец корпуса и контакт в глубине; размер заглубления — числом на размерной линии между выносными. */
function depthCallout(depth: string, faceX: number, cy: number, top: number, female: boolean): { body: string; bottom: number } {
  const ring = 18, ox = faceX + 6;
  let art = `<circle data-role="detail-origin" cx="${f(ox)}" cy="${f(cy)}" r="${ring}" fill="none" stroke="${INK}" stroke-width=".8"/>`;
  const lx = ox - ring * .7, ly = cy - ring * .7, shelfY = cy - ring - 16;
  art += `<g data-role="leader" data-target="detail-origin"><line x1="${f(lx)}" y1="${f(ly)}" x2="${f(ox - 36)}" y2="${f(shelfY)}" stroke="${THIN}" stroke-width=".7"/></g>` +
    `<line data-role="shelf" x1="${f(ox - 36)}" y1="${f(shelfY)}" x2="${f(ox - 52)}" y2="${f(shelfY)}" stroke="${THIN}" stroke-width=".7"/>` +
    `<text data-detail-id="origin" x="${f(ox - 44)}" y="${f(shelfY - 3)}" text-anchor="middle" font-size="14" font-weight="700" fill="${INK}">А</text>`;
  const r = 50, cxD = 104, cyD = top + 22 + r, faceZ = cxD - 28, tipZ = cxD + 26, axis = cyD - 11, id = "etkDepthZoom";
  // На размерной линии — только число: «0,38…1,14», «≥ 2,50», «9,14»; полная запись — в подписи рядом.
  const value = depth.replace(/\s*мм/, "").replace(/^не менее\s+/, "≥ ").replace(/^не более\s+/, "≤ ").replace(/\s*\(номинал\)$/, "");
  art += `<text data-detail-id="view" x="${f(cxD)}" y="${f(cyD - r - 8)}" text-anchor="middle" font-size="14" font-weight="700" fill="${INK}">А</text>` +
    `<defs><clipPath id="${id}"><circle cx="${f(cxD)}" cy="${f(cyD)}" r="${r}"/></clipPath></defs>` +
    `<circle cx="${f(cxD)}" cy="${f(cyD)}" r="${r}" fill="#fff" stroke="${INK}" stroke-width=".8"/>` +
    `<g clip-path="url(#${id})">` + rect(faceZ, cxD + r, cyD - r, 22, METAL, EDGE) + rect(faceZ, cxD + r, cyD - 28, 30, DIEL, EDGE) +
    (female
      ? rect(tipZ, cxD + r, axis - 8, 16, BRASS, EDGE) + `<rect data-part="socket-bore" x="${f(tipZ)}" y="${f(axis - 3)}" width="14" height="6" fill="${TUBE}"/>`
      : rect(tipZ + 6, cxD + r, axis - 4, 8, BRASS, EDGE) + `<polygon data-part="pin-tip" points="${f(tipZ)},${f(axis)} ${f(tipZ + 6)},${f(axis - 4)} ${f(tipZ + 6)},${f(axis + 4)}" fill="${BRASS}" stroke="${EDGE}" stroke-width=".8"/>`) +
    line(faceZ, cyD - r, faceZ, cyD + 2, INK, 1.2) + `</g>`;
  const yd = cyD + 22;
  art += extensionLine(faceZ, cyD + 4, faceZ, yd + 4) + extensionLine(tipZ, axis + 9, tipZ, yd + 4) + dimension(faceZ, tipZ, yd, value, 13, 700);
  const tx = cxD + r + 22;
  art += label(tx, cyD - 14, `Заглубление контакта ${depth}`, 14, 700, INK, "start") +
    label(tx, cyD + 6, "от торца корпуса до контакта", 12, 400, MUTED, "start") +
    label(tx, cyD + 24, "Вид А — без масштаба", 12, 400, MUTED, "start");
  return { body: art, bottom: cyD + r + 4 };
}

// ── Прижимной экран (зажим): гайка и шайба, оплётка по зажимному кольцу, сборка корпуса (владелец 19.10) ──

const BRAID = "url(#etkBraid)";
const polygon = (points: Array<[number, number]>, fill: string, stroke = EDGE) =>
  `<polygon points="${points.map(([x, y]) => `${f(x)},${f(y)}`).join(" ")}" fill="${fill}" stroke="${stroke}" stroke-width=".9"/>`;

/** Гайка: шестигранник с рифлением (вид сбоку), центр по оси кабеля `cy`. */
function clampNut(x1: number, x2: number, cy: number, half: number): string {
  let art = rect(x1, x2, cy - half, half * 2, METAL, EDGE, 2);
  for (let x = x1 + 8; x < x2 - 4; x += 8) art += line(x, cy - half, x, cy + half, EDGE, .8);
  return art;
}

/** Монтаж корпуса с прижимным экраном: корпус в разрезе, контакт в изоляторе, оплётка зажата кольцом между конусом и корпусом,
 *  гайка затянута. Схема без масштаба; ни гильзы экрана, ни обжима здесь нет. */
export function coaxClampEtk(end: Row, title: string, hint: string): { height: number; body: string } {
  const cy = 150, cable = str(end.cable) || "кабель";
  let body = label(8, 20, title, 14, 700, INK, "start") + label(8, 40, hint, 12, 400, MUTED, "start") +
    label(8, 58, `Схема: ${coaxFamilyTitle(end)}, прижимной экран, без масштаба`, 12, 400, MUTED, "start");
  body += `<g data-part="body">${rect(92, 290, cy - 36, 8, METAL, EDGE)}${rect(92, 290, cy + 28, 8, METAL, EDGE)}</g>` +
    `<g data-part="insulator">${rect(92, 290, cy - 28, 56, DIEL, EDGE)}</g>` +
    `<g data-part="contact" data-state="${isFemale(end) ? "socket" : "pin"}">${rect(60, 250, cy - 5, 10, BRASS, EDGE, 2)}</g>` +
    `<g data-part="clamp-ring">${rect(260, 330, cy - 14, 28, METAL, EDGE, 2)}</g>`;
  body += `<g data-part="braid">${polygon([[250, cy - 12], [300, cy - 22], [330, cy - 14], [330, cy - 8], [250, cy - 8]], BRAID)}${polygon([[250, cy + 12], [300, cy + 22], [330, cy + 14], [330, cy + 8], [250, cy + 8]], BRAID)}</g>`;
  body += `<g data-part="nut">${clampNut(290, 360, cy, 38)}</g><g data-part="washer">${rect(360, 368, cy - 34, 68, "#aab2b7", EDGE, 1)}${rect(368, 392, cy - 28, 56, "#4b5257", EDGE, 3)}</g>` +
    rect(392, 610, cy - 16, 32, JACKET, "#000", 3, 'data-part="jacket"');
  body += callouts([
    { text: "корпус", anchor: [150, cy - 32], target: "body" },
    { text: "оплётка зажата кольцом", anchor: [282, cy - 12], target: "braid" },
    { text: "шайба и уплотнитель", anchor: [364, cy - 22], target: "washer" },
  ], cy - 56, 8, 632, 12);
  body += callouts([
    { text: "контакт в изоляторе", anchor: [190, cy], target: "contact" },
    { text: "гайка затянута", anchor: [325, cy + 30], target: "nut" },
  ], cy + 66, 8, 632, 12);
  body += label(500, cy + 4.5, cable, 12, 600, "#fff");
  body += `<g data-role="action"><path d="M300,${cy + 84} q40,18 80,0" fill="none" stroke="${INK}" stroke-width="1.4"/>${actionHead(380, cy + 84, 1)}</g>` +
    label(340, cy + 116, "момент затяжки — по документации производителя", 12, 600, INK);
  return { height: cy + 128, body };
}

