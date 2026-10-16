// Вид сверху корпуса разъёма — та же объёмная модель корпуса (connectorModel, без неё — connectorPicture), развёрнутая видом сверху (владелец,
// 16.10.2026: «просто корпуса развернул»): крыша с защёлкой, внизу торец со стороны проводов с окнами контактов.
// Один и тот же вид в «База данных → Разъёмы», в каталоге и на поле чертежа жгута и в ЭТК «Монтаж в разъём».
// Круглые, разъёмы-«бочки», D-sub и лепестки вида сверху не получают — null (там остаётся торец).
import { connectorLook, connectorPicture, type Box, type ConnectorHousing } from "./connectorArt";
import { connectorModel } from "./connectorModel";

/** Есть ли у серии вид сверху: прямоугольные корпуса (в том числе XT и RJ45). */
export function hasTopView(hs: ConnectorHousing): boolean {
  const shape = connectorLook(hs).shape;
  return (shape === "rect" || shape === "xt" || shape === "rj45") && hs.cols >= 1 && hs.grid.length > 0;
}

/** Вид сверху, вписанный в maxW × maxH; `byPin` — цвета проводов на контактах торца. */
export function connectorTopView(hs: ConnectorHousing, maxW: number, maxH: number,
  byPin: Record<number, string> | null = null): Box | null {
  if (!hasTopView(hs)) return null;
  // Модель по чертежу серии точнее картинки (прорези, защёлка); цвета проводов видны на торце и в легенде рядом.
  return connectorModel(hs, maxW, maxH, "top") || connectorPicture(hs, maxW, maxH, byPin, "top");
}
