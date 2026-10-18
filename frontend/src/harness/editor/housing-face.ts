// ERP: разъём из базы разъёмов на чертеже жгута — торец со стороны проводов (тот же рисунок серии, что в «Распиновке» и
// на карточке серии: connectorFace), повёрнутый так, чтобы ряд гнёзд шёл сверху вниз, а провода уходили вправо (сторона В —
// влево) из центров своих гнёзд (владелец 18.10: «вместо квадратов притягивать разъёмы»). Рисунок плоский, без объёма
// (connector-3d-only-on-card). Геометрию серии рисует connectorArt.ts — здесь только вызов и поворот.
import { connectorFace, type ConnectorHousing, type FaceHit } from "../../connectorArt";
import type { Point } from "./model";

/** Пикселей на шаг контактов и поля вокруг торца. */
const STEP = 24, PAD = 6;

export interface HousingFace {
  /** SVG-рисунок целиком (viewBox 0 0 width height). */
  readonly svg: string;
  readonly width: number;
  readonly height: number;
  /** Центры гнёзд по номеру контакта — относительно левого верхнего угла рисунка. */
  readonly contacts: ReadonlyMap<number, Point>;
}

const cache = new WeakMap<ConnectorHousing, Map<string, HousingFace | null>>();

/** Торец разъёма, повёрнутый на 90° по часовой стрелке (контакт 1 — сверху); цвета проводов на гнёздах — `byPin`.
 *  null — у серии нет гнёзд для щелчка (лепестки, «бананы»): остаётся обычная рамка. */
export function housingFace(housing: ConnectorHousing, byPin: Record<number, string> | null = null): HousingFace | null {
  const key = JSON.stringify(byPin ?? {});
  const known = cache.get(housing)?.get(key);
  if (known !== undefined) return known;
  const hits: FaceHit[] = [];
  const box = connectorFace(housing, PAD, PAD, STEP, byPin, hits);
  const w = box.w + PAD * 2, h = box.h + PAD * 2;
  // Поворот на 90° по часовой: (x, y) → (h − y, x); рисунок w × h становится h × w.
  const face: HousingFace | null = hits.length ? {
    svg: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${h} ${w}" width="${h}" height="${w}"><g transform="translate(${h},0) rotate(90)">${box.body}</g></svg>`,
    width: h, height: w,
    contacts: new Map(hits.map((hit) => [hit.pin, { x: Math.round((h - hit.y) * 100) / 100, y: Math.round(hit.x * 100) / 100 }])),
  } : null;
  if (!cache.has(housing)) cache.set(housing, new Map());
  cache.get(housing)!.set(key, face);
  return face;
}

/** Где провод встаёт на торец: у края рисунка со стороны, куда он уходит (контакты слева — левый край, иначе правый), на
 *  высоте своего гнезда — провод «входит» в корпус к гнезду, не лёжа поверх соседних. */
export function housingFaceEntry(face: HousingFace, pin: number, contactsLeft: boolean): Point | undefined {
  const at = face.contacts.get(pin);
  return at ? { x: contactsLeft ? 0 : face.width, y: at.y } : undefined;
}
