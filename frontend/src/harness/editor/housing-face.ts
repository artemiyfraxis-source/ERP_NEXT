// ERP: разъём из базы разъёмов на чертеже жгута (владелец 18.10: «разъёмы — вид сверху и правильно развёрнутые», ПФ 2 —
// образец). Вид сверху — тот же рисунок серии (connectorTopView: объёмная модель корпуса, развёрнутая видом сверху, с крышей,
// защёлкой и торцем со стороны проводов), что в базе разъёмов, на карточке серии и в схеме ПФ (etkPfArt.ts pfBranchSvg).
// Разворот как в ПФ: торец проводов — к жгуту; сторона А слева, провода вправо (как pfBranchSvg: оси рисунка меняются местами,
// защёлка слева), сторона В зеркально — справа, провода влево (поворот на 90° по часовой, защёлка справа). Контакт 1 — сверху
// на обеих сторонах (на виде сверху он слева у торца проводов). Провода встают на краю торца, на высоте своего контакта.
// Серия без вида сверху — торец серии (connectorFace), как у pfBranchSvg, без поворота. Рисунок плоский, без 3D на схеме
// (connector-3d-only-on-card); геометрию серий рисует connectorArt.ts — здесь только вызов и разворот.
import { bodySpan, connectorFace, type ConnectorHousing, type FaceHit } from "../../connectorArt";
import { connectorTopView, hasTopView } from "../../connectorTopView";
import type { Point } from "./model";

/** Размеры вида сверху (ширина поперёк контактов × длина вдоль проводов, потолок), мм рисунка; шаг торца серии без вида сверху. */
const TOP_W = 110, TOP_L = 170, FACE_STEP = 24, PAD = 6;
/** Единый масштаб схемы жгута, px на мм корпуса (владелец 18.10: «X5/X6 в разы больше X1–X4»): корпус каждого разъёма — в
 *  своих размерах по чертежу серии (`body`: ширина поперёк контактов, длина вдоль проводов), все в одном масштабе. */
export const HOUSING_PX_PER_MM = 8;
/** Размер корпуса из базы разъёмов, мм: ширина поперёк контактов (с кожухом и плечами) и длина вдоль проводов; нет чертежа серии — null. */
export function housingSizeMm(housing: ConnectorHousing): { readonly width: number; readonly length: number } | null {
  if (!housing.body) return null;
  return { width: bodySpan(housing, housing.body, housing.pitch || 2.5).W, length: housing.body.len };
}

export interface HousingFace {
  /** SVG-рисунок целиком (viewBox 0 0 width height). */
  readonly svg: string;
  readonly width: number;
  readonly height: number;
  /** Вид сверху (иначе торец серии). */
  readonly top: boolean;
  /** px на мм корпуса, когда размеры серии известны (единый для всех разъёмов схемы); иначе null — рисунок вписан в рамку. */
  readonly pxPerMm: number | null;
  /** Точка выхода провода каждого контакта — на краю торца со стороны жгута, относительно левого верхнего угла рисунка. */
  readonly contacts: ReadonlyMap<number, Point>;
}

const cache = new WeakMap<ConnectorHousing, Map<string, HousingFace | null>>();
const r2 = (value: number) => Math.round(value * 100) / 100;

/** Разъём с корпусом из базы; `contactsLeft` — сторона В (контакты слева, провода уходят влево). null — у серии нет ни вида
 *  сверху, ни гнёзд торца (лепестки, «бананы»): остаётся обычная рамка. */
export function housingFace(housing: ConnectorHousing, byPin: Record<number, string> | null = null, contactsLeft = false): HousingFace | null {
  const key = `${contactsLeft ? "B" : "A"}${JSON.stringify(byPin ?? {})}`;
  const known = cache.get(housing)?.get(key);
  if (known !== undefined) return known;
  let face: HousingFace | null = null;
  const size = housingSizeMm(housing);
  // Известны размеры серии — вид сверху в единых мм → px (естественный масштаб рисунка сводится к HOUSING_PX_PER_MM); нет — вписан в рамку.
  const natural = hasTopView(housing) && size ? connectorTopView(housing, 4000, 4000, byPin) : null;
  const factor = natural && size ? HOUSING_PX_PER_MM / (natural.w / size.width) : 1;
  const fitted = natural ?? (hasTopView(housing) ? connectorTopView(housing, TOP_W, TOP_L, byPin) : null);
  const top = fitted && { w: fitted.w * (natural ? factor : 1), h: fitted.h * (natural ? factor : 1), body: fitted.body };
  const scale = natural ? ` scale(${r2(factor)})` : "";
  if (top) {
    // Вид сверху: ширина top.w поперёк контактов, длина top.h вдоль проводов (торец проводов — снизу). Контакты — по колонкам
    // сетки корпуса вдоль ширины; два ряда сетки в одной колонке — рядом, чуть разведены.
    const cols = Math.max(1, housing.cols), rows = Math.max(1, housing.grid.length);
    const pins = new Map<number, Point>();
    housing.grid.forEach((row, r) => row.forEach((pin, c) => {
      if (pin <= 0) return;
      const along = top.w * (0.14 + (0.72 * (c + 0.5)) / cols) + (r - (rows - 1) / 2) * (rows > 1 ? 4 : 0);
      pins.set(pin, { x: contactsLeft ? 0 : r2(top.h), y: r2(along) });
    }));
    // A: оси меняются местами (x' = y, y' = x) — провода вправо; В: поворот на 90° по часовой — провода влево.
    const matrix = contactsLeft ? `matrix(0,1,-1,0,${r2(top.h)},0)` : "matrix(0,1,1,0,0,0)";
    face = { svg: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${r2(top.h)} ${r2(top.w)}" width="${r2(top.h)}" height="${r2(top.w)}">` +
      `<g data-part="connector" data-view="top" transform="${matrix}${scale}">${top.body}</g></svg>`, width: r2(top.h), height: r2(top.w), top: true, pxPerMm: natural ? HOUSING_PX_PER_MM : null, contacts: pins };
  } else {
    const hits: FaceHit[] = [];
    const box = connectorFace(housing, PAD, PAD, FACE_STEP, byPin, hits);
    if (hits.length) {
      const w = box.w + PAD * 2, h = box.h + PAD * 2;
      face = { svg: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${r2(w)} ${r2(h)}" width="${r2(w)}" height="${r2(h)}">${box.body}</svg>`, width: r2(w), height: r2(h), top: false, pxPerMm: null,
        contacts: new Map(hits.map((hit) => [hit.pin, { x: contactsLeft ? 0 : r2(w), y: r2(hit.y) }])) };
    }
  }
  if (!cache.has(housing)) cache.set(housing, new Map());
  cache.get(housing)!.set(key, face);
  return face;
}

/** Где провод встаёт на разъём: край со стороны жгута на высоте контакта. */
export function housingFaceEntry(face: HousingFace, pin: number): Point | undefined {
  return face.contacts.get(pin);
}
