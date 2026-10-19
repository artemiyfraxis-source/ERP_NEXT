// Эскизы операций коакса в ЭТК на тех же рисунках и цветах, что картинки ПФ (etkPfCoaxArt): «Надеть ТУТ / втулку экрана» — оба
// разделанных конца крупно, каждый со своим разъёмом, ТУТ или втулка надвинута на оболочку, середина кабеля — знак разрыва;
// «Монтаж корпуса» — настоящий боковой вид SMA в разрезе (владелец 19.10: «эскизы операций тоже слабые»). Холст 640 px, подписи
// не мельче 12. Чистые функции от данных операции (`fields.coaxParts.ends`, `fields.coaxEnd`).
import { coaxKindTitle } from "./coaxNames";
import { tubeShort } from "./etkPfScheme";
import { BRASS, DIEL, EDGE, endGraphics, INK, isFemale, JACKET, label, line, METAL, MUTED, rect, SOLDER, stepsOf, TUBE } from "./etkPfCoaxArt";

type Row = Record<string, unknown>;
const str = (value: unknown): string => (value == null ? "" : String(value));
const f = (value: number) => value.toFixed(1);
const arrowHead = (x: number, y: number, dir: 1 | -1) => `<path d="M${f(x)},${f(y)} l${9 * dir},-3.5 v7 z" fill="${INK}"/>`;

/** Название конца в заголовке: «Сторона А — SMA-Male (вилка)»; типа разъёма нет — по артикулу. */
function endCaption(end: Row): string {
  const side = ["В", "B"].includes(str(end.side).trim()) ? "В" : "А";
  return `Сторона ${side} — ${coaxKindTitle(end) || str(end.connector || end.article).trim() || "разъём"}`;
}

/** «Надеть: ТУТ / втулка экрана»: оба разделанных конца крупно, каждый со своим разъёмом; ТУТ или втулка надвинута на оболочку
 *  на расстоянии от торца, стрелка — со свободного конца; середина кабеля обрезана знаком разрыва. Ряд — 140 px.
 *  `offsetMm` — на сколько отодвинуть от торца оболочки (нет в данных — подпись без числа). */
export function coaxPartsEtk(ends: Row[], kind: "tube" | "sleeve", name: string, offsetMm: string): { height: number; body: string } {
  const x0 = 70, brk = [428, 458], arrowFrom = 592;
  let body = "";
  ends.slice(0, 2).forEach((end, index) => {
    const top = 30 + index * 140, cy = top + 72, steps = stepsOf(end, x0, 150), { x1, x2, x3 } = steps;
    body += label(8, top + 14, endCaption(end), 14, 700, INK, "start");
    body += endGraphics(end, x0, cy, steps, brk[0], "url(#etkBraid)") + rect(brk[1], 600, cy - 16, 32, JACKET, "#000", 4);
    // Знак разрыва: кабель в середине не рисуется во всю длину.
    body += `<g data-part="break">${line(brk[0] + 8, cy - 22, brk[0] - 8, cy + 22, INK, 1.6)}${line(brk[1] + 8, cy - 22, brk[1] - 8, cy + 22, INK, 1.6)}</g>`;
    const from = x3 + 36, width = kind === "tube" ? 96 : 46, to = from + width;
    if (kind === "tube") {
      body += rect(from, to, cy - 21, 42, TUBE, "#000", 7, 'fill-opacity=".92" data-part="tube"') + label((from + to) / 2, cy + 4.5, tubeShort(name) || "ТУТ", 13, 700, "#fff");
    } else {
      body += rect(from, to, cy - 19, 38, METAL, EDGE, 3, 'data-part="sleeve"');
      for (let x = from + 7; x < to - 3; x += 6) body += line(x, cy - 19, x, cy + 19, EDGE, .8);
      body += label(to + 8, cy + 30, "втулка экрана", 12, 700, INK, "start") + line(to - 6, cy + 19, to + 10, cy + 22, MUTED, .9, "2 2");
    }
    // Стрелка: надвигать со свободного конца к торцу; размер «отодвинуть …» — от торца оболочки до кромки.
    body += line(arrowFrom, cy - 31, to + 10, cy - 31, INK, 1.2) + arrowHead(to + 10, cy - 31, -1) +
      label(arrowFrom, cy - 37, "надвинуть со свободного конца", 12, 600, MUTED, "end");
    const yd = cy + 36;
    body += line(x3, cy + 17, x3, yd + 6, MUTED, .9, "3 3") + line(from, cy + 22, from, yd + 6, MUTED, .9, "3 3") +
      line(x3, yd, from, yd, INK, 1) + arrowHead(x3, yd, 1) + arrowHead(from, yd, -1) +
      label(x3, yd + 20, offsetMm ? `отодвинуть на ${offsetMm} мм от торца оболочки` : "отодвинуть от торца оболочки", 12, 600, INK, "start");
    body += label(10, cy - 44, "контакт, припой", 12, 600, INK, "start") + line(x0 - 14, cy - 40, x0 - 14, cy - 8, MUTED, .9, "2 2");
    body += label((x2 + x3) / 2 + 8, cy - 44, "оплётка", 12, 600, INK) + line((x2 + x3) / 2 + 8, cy - 40, (x2 + x3) / 2 + 8, cy - 13, MUTED, .9, "2 2");
    body += label((x1 + x2) / 2 - 18, cy + 34, "диэлектрик", 12, 600, INK) + line((x1 + x2) / 2, cy + 10, (x1 + x2) / 2 - 12, cy + 22, MUTED, .9, "2 2");
    body += label(528, cy + 4.5, str(end.cable) || "кабель", 12, 600, "#fff");
  });
  return { height: 30 + Math.min(2, ends.length) * 140 + 4, body };
}

/** Разъём в разрезе, лицо в x = 0, кабель уходит вправо, середина по высоте — y = 0 (размеры 1:1 с видом connectorSide на ПФ):
 *  стенки корпуса, изолятор, центральный контакт (штырь у вилки, гнездо у розетки), входящий в изолятор и в чашку на жиле,
 *  диэлектрик кабеля в хвостовике, оплётка, расправленная по хвостовику, и оболочка. */
function connectorSection(female: boolean): string {
  let art = "";
  if (female) {
    // Резьбовой стержень (стенки с резьбой) и шестигранный корпус.
    art += rect(0, 44, -11, 4, METAL, EDGE) + rect(0, 44, 7, 4, METAL, EDGE);
    for (let x = 5; x < 44; x += 5) art += line(x, -11, x - 2, -7, EDGE, .8) + line(x, 7, x - 2, 11, EDGE, .8);
    art += rect(44, 92, -16, 6, METAL, EDGE) + rect(44, 92, 10, 6, METAL, EDGE) + line(60, -16, 60, -10) + line(76, -16, 76, -10) + line(60, 10, 60, 16) + line(76, 10, 76, 16);
    art += rect(44, 92, -10, 20, DIEL, EDGE) + rect(6, 62, -4.5, 9, BRASS, EDGE) + rect(6, 14, -2.2, 4.4, TUBE, TUBE);
  } else {
    // Накидная гайка с рифлением (стенки и резьба внутри), под ней корпус.
    art += rect(0, 56, -16, 5.5, METAL, EDGE, 1) + rect(0, 56, 10.5, 5.5, METAL, EDGE, 1) + rect(0, 7, -16, 32, "none", EDGE, 1);
    [18, 30, 42].forEach((x) => { art += line(x, -16, x, -10.5) + line(x, 10.5, x, 16); });
    for (let x = 9; x < 56; x += 4) art += line(x, -10.5, x - 2, -7.5, EDGE, .7) + line(x, 10.5, x - 2, 7.5, EDGE, .7);
    art += rect(56, 96, -10, 5, METAL, EDGE) + rect(56, 96, 5, 5, METAL, EDGE) + rect(40, 96, -5, 10, DIEL, EDGE) + rect(10, 82, -2.2, 4.4, BRASS, EDGE);
  }
  // Хвостовик под обжим (стенки), чашка контакта с припоем на жиле, диэлектрик кабеля, оплётка по хвостовику, оболочка.
  art += rect(96, 112, -8, 2.5, METAL, EDGE) + rect(96, 112, 5.5, 2.5, METAL, EDGE) +
    rect(84, 104, -4, 8, BRASS, EDGE) + `<ellipse cx="94" cy="0" rx="6" ry="3.4" fill="${SOLDER}" stroke="${EDGE}" stroke-width=".6"/>` +
    rect(104, 132, -4.5, 9, DIEL, INK) +
    `<polygon points="98,-8 120,-8 136,-11 136,11 120,8 98,8" fill="url(#etkBraid)" stroke="${EDGE}" stroke-width=".6"/>` +
    rect(136, 190, -10, 20, JACKET, "#000", 2);
  return art;
}

/** Монтаж корпуса: настоящий боковой вид SMA в разрезе крупно (гайка у вилки, резьба и шестигранник у розетки), контакт входит в
 *  изолятор, оплётка расправлена по хвостовику. Подписи с выносками; высота 178. */
export function coaxMountEtk(end: Row, title: string, hint: string): { height: number; body: string } {
  const female = isFemale(end), s = 2.6, cy = 120, gx = (x: number) => 40 + s * x;
  let body = label(8, 20, title, 14, 700, INK, "start") + label(8, 40, hint, 12, 400, MUTED, "start");
  body += `<g transform="translate(40,${cy}) scale(${s})" data-part="${female ? "female-body" : "male-body"}">${connectorSection(female)}</g>`;
  const nutX = gx(female ? 22 : 28);
  body += label(nutX, cy - 62, female ? "резьбовой корпус" : "накидная гайка", 12, 600, INK) + line(nutX, cy - 57, nutX, cy - 16 * s - 1, MUTED, .9, "2 2");
  body += label(gx(76), cy - 62, "изолятор", 12, 600, INK) + line(gx(76), cy - 57, gx(76), cy - 8, MUTED, .9, "2 2");
  body += label(gx(40), cy + 80, "контакт входит в изолятор", 12, 600, INK) + line(gx(40), cy + 66, gx(40), cy + 8, MUTED, .9, "2 2");
  body += label(gx(122) - 30, cy + 80, "оплётка расправлена по хвостовику,", 12, 600, INK, "start") + label(gx(122) - 30, cy + 96, "внутрь не завёрнута", 12, 600, INK, "start") +
    line(gx(122), cy + 66, gx(122), cy + 11 * s, MUTED, .9, "2 2");
  body += label(gx(163), cy + 4.5, str(end.cable) || "кабель", 12, 600, "#fff");
  const depth = depthText(end.contactDepth);
  if (!depth) return { height: 228, body };
  return { height: 228 + DEPTH_ROW, body: body + depthCallout(depth, cy) };
}

/** «0,38…1,14» / «0,38-1,14» → «0,38…1,14»; без числа — пусто (ничего не рисуется). */
function depthText(value: unknown): string {
  const text = str(value).trim().replace(/\s*(?:\.{2,3}|…|–|—|-)\s*/g, "…");
  return /^\d+(?:[.,]\d+)?(?:…\d+(?:[.,]\d+)?)?$/.test(text) ? text.replace(/\./g, ",") : "";
}

const DEPTH_ROW = 120;

/** Заглубление контакта от торца корпуса (БД.КОАКС «Заглубление контакта, мм»): величина в десятые доли миллиметра не читается на
 *  разрезе, поэтому на нём — кружок «А» у торца с выноской, а под ним увеличенный вид А (10:1, не в масштабе): торец корпуса,
 *  контакт в глубине и размерная линия между ними с подписью. */
function depthCallout(depth: string, cy: number): string {
  const faceX = 40, ring = 15, cxD = 104, cyD = cy + 108 + DEPTH_ROW / 2 - 10, r = 50;
  let art = `<circle cx="${f(faceX + 12)}" cy="${f(cy)}" r="${ring}" fill="none" stroke="${INK}" stroke-width="1.2" stroke-dasharray="4 2"/>` +
    label(faceX + 12 - ring - 6, cy + 5, "А", 13, 700, INK, "end") +
    line(faceX + 12 - 10, cy + ring - 4, cxD - 30, cyD - r + 4, INK, .9, "3 3");
  const id = "etkDepthZoom";
  // Увеличенный вид: слева торец корпуса (штриховая стенка по краям), справа — контакт; расстояние — между торцом и кончиком контакта.
  const faceZ = cxD - 28, tipZ = cxD + 26;
  art += `<defs><clipPath id="${id}"><circle cx="${f(cxD)}" cy="${f(cyD)}" r="${r}"/></clipPath></defs><circle cx="${f(cxD)}" cy="${f(cyD)}" r="${r}" fill="#fff" stroke="${INK}" stroke-width="1.4"/>` +
    `<g clip-path="url(#${id})">` +
    rect(faceZ, cxD + r, cyD - r, 26, METAL, EDGE) + rect(faceZ, cxD + r, cyD + r - 26, 26, METAL, EDGE) +
    rect(faceZ, cxD + r, cyD - 24, 48, DIEL, EDGE) + rect(tipZ, cxD + r, cyD - 8, 16, BRASS, EDGE, 2) +
    `</g>` + line(faceZ, cyD - r + 6, faceZ, cyD + r - 6, INK, 1.3, "5 3") + line(tipZ, cyD - 30, tipZ, cyD + 30, INK, 1.1, "5 3") +
    line(faceZ, cyD + 36, tipZ, cyD + 36, INK, 1.2) + arrowHead(faceZ, cyD + 36, 1) + arrowHead(tipZ, cyD + 36, -1) +
    label(cxD, cyD - r - 6, "А (10:1)", 13, 700, INK);
  const tx = cxD + r + 22;
  art += label(tx, cyD - 14, `заглубление контакта ${depth} мм`, 14, 700, INK, "start") +
    label(tx, cyD + 6, "от торца корпуса до контакта", 12, 400, MUTED, "start") +
    label(tx, cyD + 24, "вид А увеличен, не в масштабе", 12, 400, MUTED, "start");
  return art;
}
