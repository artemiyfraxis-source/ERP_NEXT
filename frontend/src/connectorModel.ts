// Модели корпусов для «База данных → Разъёмы» по шаблону серии (владелец, 10.10: «с визуалом всё ещё сильно мимо»).
// Шаблон повторяет то, по чему серию узнают на чертеже производителя, в размерах серии (connector_housings.py `body`):
//  • slots — JST PH, ZH, EH, SH: фланец у торца проводов и прорези фиксаторов по контакту на одной грани корпуса
//    (у PH/ZH/EH эта грань снизу — модель показана снизу, у SH сверху, с выступами по бокам и меткой ▽);
//  • plate — JST GH, NSH: плоская защёлка-пластина на верхней грани с зацепом у торца проводов (у GH почти во всю
//    ширину между боковыми стойками, у NSH — узкая по центру);
//  • frame — JST XH (и аналог Connfly DS1069): защёлка-рамка «П» — перемычка у торца проводов и два плеча по краям
//    с зацепами на концах.
// Серия без шаблона (или без размеров с чертежа) модели не получает — галерея пишет «модель не сверена» и показывает
// только вид со стороны проводов: лучше честный торец, чем похожий на другую серию «кубик».
import {
  slotSpans, isPairBody, pairModel,
  EDGE, bodyColumns, bodyWidth, f, gradId, gradient, isDark, lockWidth, poly, prism, roofMark, shade, topFeature,
  type Box, type ConnectorHousing, type PictureView, type Pt,
} from "./connectorArt";

//  • hood / arms — пара JST SM (eSM): вилка SMR (штыри) — кожух шире корпуса и рычаг замка с рифлением по центру крыши;
//    розетка SMP (гнёзда) — фланец, «нос» с упором замка и плечи крепления в панель по торцам ряда (connectorArt `pairModel`).
export const MODEL_TEMPLATES = ["slots", "plate", "frame", "hood", "arms"] as const;

/** Есть ли у корпуса модель, сверенная с чертежом серии. */
export function hasModel(hs: ConnectorHousing): boolean {
  return Boolean(hs.body && hs.rows === 1 && (MODEL_TEMPLATES as readonly string[]).includes(hs.model || ""));
}

/** Объёмная модель корпуса по шаблону серии, вписанная в maxW × maxH; null — шаблона нет. `view: "top"` — та же модель,
 *  развёрнутая видом сверху: крыша с защёлкой, торец проводов снизу. У корпуса с прорезями фиксаторов снизу (JST PH, ZH,
 *  EH) смотрим со стороны прорезей (владелец 16.10: «нет прорезей как в реальном PHR»): модель повёрнута вокруг оси
 *  контактов, порядок контактов слева направо тот же, торец проводов — снизу, вверх ногами. */
export function connectorModel(hs: ConnectorHousing, maxW: number, maxH: number, view: PictureView = "iso"): Box | null {
  if (!hasModel(hs) || !hs.body) return null;
  if (hs.model === "hood" || hs.model === "arms") return isPairBody(hs.body) ? pairModel(hs, hs.body, maxW, maxH, view) : null;
  const b = hs.body, pitch = hs.pitch || 1, color = hs.color || "#efe6cf", dark = isDark(color);
  const W = bodyWidth(hs, b, pitch), H = b.h, L = b.len, ears = b.ears || 0;
  const below = hs.model === "slots" && hs.latch === "windows"; // грань с прорезями снизу — смотрим снизу
  const lw = lockWidth(b, W), lockH = hs.model === "slots" ? 0 : (lw ? b.lock_h || 0 : 0);
  const flat = view === "top";
  const DX = flat ? 0 : 0.62, DY = flat ? -0.87 : below ? 0.4 : -0.4, SY = flat ? 0.5 : 1;
  const k = Math.min(maxW / (W + L * DX), maxH / ((H + lockH) * SY + L * Math.abs(DY)), 18);
  const top = (DY > 0 ? 0 : L * -DY * k) + lockH * k * SY + 1;
  const flip = flat && below; // грань с прорезями — к зрителю
  const P = (x: number, y: number, d: number): Pt => [x * k + 1 + DX * k * d, (flip ? H - y : y) * k * SY + top + DY * k * d];
  const quad = (x0: number, x1: number, y: number, d0: number, d1: number): Pt[] => [P(x0, y, d0), P(x1, y, d0), P(x1, y, d1), P(x0, y, d1)];
  const box = (x0: number, y0: number, x1: number, y1: number, d0: number, d1: number, fill: string, width = 0.8) => {
    const front = [P(x0, y0, d0), P(x1, y0, d0), P(x1, y1, d0), P(x0, y1, d0)];
    return prism(front, DX * k * (d1 - d0), DY * k * (d1 - d0), fill, width) + poly(front, fill, EDGE, width);
  };
  const hole = dark ? "#5d666b" : "#2f3336", groove = shade(color, -0.42);
  const columns = bodyColumns(hs, b, pitch), row = hs.grid[0] || [];
  const [winW, winH] = b.win && b.win.length === 2 ? b.win : [pitch * 0.6, H * 0.45];
  let body = gradient(gradId(color), color);

  if (hs.model === "slots") {
    // Фланец у торца проводов во всю толщину; за ним корпус тоньше с грани прорезей (SH: rear_h с чертежа).
    const fl = Math.min(b.front_len || Math.max(0.6, Math.min(1.2, L * 0.15)), L * 0.5);
    const rearH = Math.min(b.rear_h || H - Math.min(0.4, H * 0.1), H);
    const face = below ? H : 0, rearFace = below ? rearH : H - rearH;
    body += box(ears, below ? 0 : H - rearH, W - ears, below ? rearH : H, fl, L, shade(color, -0.03));
    // Прорези фиксаторов: по одной на контакт, от фланца почти до стыковочного торца.
    body += roofMark("slots", columns.map((cx) => poly(quad(cx - winW * 0.4, cx + winW * 0.4, rearFace, fl + 0.2, L - 0.45), groove, groove, 0.3)).join(""));
    body += box(ears, 0, W - ears, H, 0, fl, color);
    if (ears) {
      const el = Math.min(b.ear_len || fl, fl);
      body += box(0, 0, ears, H, 0, el, shade(color, -0.06)) + box(W - ears, 0, W, H, 0, el, shade(color, -0.06));
    }
    // Гребёнка фланца над прорезями (зубцы у каждого контакта, как на чертеже PH/ZH/EH).
    columns.forEach((cx) => { body += poly(quad(cx - winW * 0.32, cx + winW * 0.32, face, 0.08, fl - 0.08), shade(color, below ? -0.2 : 0.12), EDGE, 0.3); });
    const one = columns[row.indexOf(1)];
    if (hs.latch === "mark" && one !== undefined) {
      const t = pitch * 0.42; // SH: ▽ отлита на фланце над контактом 1
      body += poly([P(one - t, 0, 0.15), P(one + t, 0, 0.15), P(one, 0, Math.min(fl - 0.1, 0.15 + t * 1.4))], groove, groove, 0.3);
    }
  } else {
    body += box(ears, 0, W - ears, H, 0, L, color);
    if (topFeature(hs) === "slots") {
      // Замка у модели нет — прорези ключа на крыше у стороны контакта 1.
      body += roofMark("slots", slotSpans(hs).map(([a, c]) => poly(quad(a * (W - 2 * ears) + ears, c * (W - 2 * ears) + ears, 0, 0.8, L - 0.6), groove, groove, 0.3)).join(""));
    }
    if (hs.model === "plate" && lw) {
      // Защёлка-пластина: прорези по её краям в верхней грани, сама пластина и зацеп у торца проводов.
      const x0 = (W - lw) / 2, x1 = (W + lw) / 2, gap = Math.max(0.12, pitch * 0.1);
      body += roofMark("latch", poly(quad(x0 - gap, x0, 0, 0, L * 0.85), groove, groove, 0.3) + poly(quad(x1, x1 + gap, 0, 0, L * 0.85), groove, groove, 0.3) +
        box(x0, -lockH * 0.55, x1, 0, 0.5, L * 0.85, shade(color, 0.05)) + box(x0, -lockH, x1, 0, 0, 0.5, shade(color, 0.1)) +
        poly(quad(x0 + lw * 0.42, x1 - lw * 0.42, -lockH * 0.55, 1.2, 1.8), groove, groove, 0.3));
    }
    if (hs.model === "frame" && lw) {
      // Рамка «П»: перемычка у торца проводов, плечи по краям, зацепы на концах плеч; между плечами — окно.
      const x0 = (W - lw) / 2, x1 = (W + lw) / 2, arm = Math.min(1.0, lw * 0.18), end = L * 0.82;
      body += roofMark("latch", poly(quad(x0 + arm, x1 - arm, 0, 1.3, end), shade(color, -0.25), EDGE, 0.4) +
        box(x0, -lockH * 0.7, x0 + arm, 0, 0, end, shade(color, 0.04)) + box(x1 - arm, -lockH * 0.7, x1, 0, 0, end, shade(color, 0.04)) +
        box(x0, -lockH, x0 + arm, -lockH * 0.7, end - 1.0, end, shade(color, 0.1)) + box(x1 - arm, -lockH, x1, -lockH * 0.7, end - 1.0, end, shade(color, 0.1)) +
        box(x0, -lockH, x1, 0, 0, 1.3, shade(color, 0.1)));
    }
  }
  // Торец проводов: окна контактов, контакт 1 — красная метка, как на виде со стороны проводов.
  body += poly([P(ears, 0, 0), P(W - ears, 0, 0), P(W - ears, H, 0), P(ears, H, 0)], `url(#${gradId(color)})`, EDGE, 0.9);
  columns.forEach((cx, c) => {
    const [px, py] = P(cx, H / 2, 0);
    body += `<rect x="${f(px - winW * k / 2)}" y="${f(py - winH * k * SY / 2)}" width="${f(winW * k)}" height="${f(winH * k * SY)}" fill="${hole}"/>`;
    if (row[c] === 1) {
      const t = Math.max(2.5, Math.min(winW, winH) * k * 0.45), [ex] = P(c > (columns.length - 1) / 2 ? W : ears, 0, 0);
      const right = c > (columns.length - 1) / 2, x = right ? ex + 2 : ex - 2, d = right ? 1 : -1;
      body += `<path d="M${f(x + d * t * 1.5)},${f(py - t)} L${f(x + d * t * 1.5)},${f(py + t)} L${f(x)},${f(py)} z" fill="#d7322d"/>`;
    }
  });
  const width = W * k + DX * k * L + 2, height = top + H * k * SY + (DY > 0 ? DY * k * L : 0) + 2;
  // Метка контакта 1 может выйти за левый край — сдвигаем всё вправо на её ширину.
  const pad = Math.max(6, Math.min(winW, winH) * k * 0.7);
  return { w: width + pad * 2, h: height, body: `<g transform="translate(${f(pad)},0)">${body}</g>` };
}
