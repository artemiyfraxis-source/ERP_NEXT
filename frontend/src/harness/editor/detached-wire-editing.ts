import type { Point, WireInstance } from "./model";
import { segmentPointDistance } from "./segment-geometry";

/** Insert into the clicked leg of the authored polyline, never at its tail. */
export function detachedWireBendInsertionIndex(wire: WireInstance, point: Point): number {
  if (!wire.drawingEndpoints) throw new Error("Провод не является независимым фрагментом.");
  const points = [wire.drawingEndpoints.from, ...wire.drawingRoute, wire.drawingEndpoints.to];
  let nearest = 0, distance = Infinity;
  for (let index = 0; index < points.length - 1; index++) {
    const next = segmentPointDistance(point, { start: points[index]!, end: points[index + 1]! });
    if (next < distance) { nearest = index; distance = next; }
  }
  return nearest;
}

/** Classic polyline controls: each bend is one explicit, independent point. */
export function editedDetachedWireBends(wire: WireInstance, index: number, position: Point, insert = false): readonly Point[] {
  if (!wire.drawingEndpoints) throw new Error("Провод не является независимым фрагментом.");
  if (!Number.isInteger(index) || index < 0 || index >= wire.drawingRoute.length + (insert ? 1 : 0)) throw new Error("Изгиб провода не найден.");
  if (!Number.isFinite(position.x) || !Number.isFinite(position.y) || Math.abs(position.x) > 1e7 || Math.abs(position.y) > 1e7) throw new Error("Координаты изгиба заданы неверно.");
  const route = wire.drawingRoute.map(point => ({ ...point }));
  route.splice(index, insert ? 0 : 1, { ...position });
  return route;
}
