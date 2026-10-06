import type { Point } from "./model";
import type { DrawingDocuments, PositionRail } from "./drawing-documents";

export const railCatchDistance = 18;
export const railReleaseDistance = 30;

export function snapRailEnd(start: Point, cursor: Point): Point {
  void start;
  return cursor;
}

export function railDistance(rail: Pick<PositionRail, "start" | "end">, point: Point): number {
  const dx = rail.end.x - rail.start.x, dy = rail.end.y - rail.start.y;
  const length2 = dx * dx + dy * dy;
  if (!length2) return Math.hypot(point.x - rail.start.x, point.y - rail.start.y);
  const t = Math.max(0, Math.min(1, ((point.x - rail.start.x) * dx + (point.y - rail.start.y) * dy) / length2));
  return Math.hypot(point.x - rail.start.x - t * dx, point.y - rail.start.y - t * dy);
}

export function railParameter(rail: PositionRail, point: Point): number {
  const dx = rail.end.x - rail.start.x, dy = rail.end.y - rail.start.y;
  const length2 = dx * dx + dy * dy;
  return length2 ? Math.max(0, Math.min(1, ((point.x - rail.start.x) * dx + (point.y - rail.start.y) * dy) / length2)) : 0;
}

export function distributeRail(d: DrawingDocuments, rail: PositionRail): DrawingDocuments {
  const count = rail.leaderIds.length;
  const slots = new Map(rail.leaderIds.map((id, index) => [id, (index + 1) / (count + 1)]));
  return { ...d, rails: (d.rails ?? []).map(r => r.id === rail.id ? rail : r), leaders: d.leaders.map(l => {
    const t = slots.get(l.id);
    return t === undefined ? l : { ...l, circle: { x: rail.start.x + (rail.end.x - rail.start.x) * t, y: rail.start.y + (rail.end.y - rail.start.y) * t } };
  }) };
}

export function createPositionRail(d: DrawingDocuments, rail: PositionRail): DrawingDocuments {
  const claimed = new Set((d.rails ?? []).flatMap(r => r.leaderIds));
  const visible = new Set(d.leaders.filter(l => !l.hidden && !claimed.has(l.id)).map(l => l.id));
  const ids = [...new Set(rail.leaderIds)].filter(id => visible.has(id));
  ids.sort((a, b) => railParameter(rail, d.leaders.find(l => l.id === a)!.circle) - railParameter(rail, d.leaders.find(l => l.id === b)!.circle));
  const next = { ...rail, leaderIds: ids };
  return distributeRail({ ...d, rails: [...d.rails ?? [], next] }, next);
}

export function movePositionRail(d: DrawingDocuments, id: string, point: Point): DrawingDocuments | null {
  const suffix = id.endsWith(":start") ? "start" : id.endsWith(":end") ? "end" : "body";
  const rail = (d.rails ?? []).find(r => r.id === id || `${r.id}:${suffix}` === id);
  if (!rail) return null;
  let next: PositionRail;
  if (suffix === "body") {
    const dx = point.x - Math.min(rail.start.x, rail.end.x), dy = point.y - Math.min(rail.start.y, rail.end.y);
    next = { ...rail, start: { x: rail.start.x + dx, y: rail.start.y + dy }, end: { x: rail.end.x + dx, y: rail.end.y + dy } };
  } else {
    const center = { x: point.x + 5, y: point.y + 5 };
    const other = suffix === "start" ? rail.end : rail.start;
    const snapped = center;
    if (Math.hypot(snapped.x - other.x, snapped.y - other.y) < 24) return d;
    next = { ...rail, [suffix]: snapped };
  }
  return distributeRail(d, next);
}

/** A perpendicular pull releases a leader; moving along the rail changes its order. */
export function movePositionLeaderOnRails(d: DrawingDocuments, id: string, circle: Point): DrawingDocuments {
  const old = (d.rails ?? []).find(r => r.leaderIds.includes(id));
  const scale = d.leaderScale ?? 1;
  const nearest = (d.rails ?? []).map(r => ({ rail: r, distance: railDistance(r, circle) })).sort((a, b) => a.distance - b.distance)[0];
  const target = nearest && nearest.distance <= (old?.id === nearest.rail.id ? railReleaseDistance : railCatchDistance) * scale ? nearest.rail : null;
  const stripped = (d.rails ?? []).map(r => ({ ...r, leaderIds: r.leaderIds.filter(member => member !== id) }));
  let result: DrawingDocuments = { ...d, rails: stripped, leaders: d.leaders.map(l => l.id === id ? { ...l, circle } : l) };
  if (old) result = distributeRail(result, stripped.find(r => r.id === old.id)!);
  if (!target) return result;
  const rail = (result.rails ?? []).find(r => r.id === target.id)!;
  const t = railParameter(rail, circle);
  const ids = [...rail.leaderIds];
  const index = ids.findIndex((_, i) => t < (i + 1) / (ids.length + 1));
  ids.splice(index < 0 ? ids.length : index, 0, id);
  return distributeRail(result, { ...rail, leaderIds: ids });
}

export function removePositionLeaderFromRails(d: DrawingDocuments, id: string): DrawingDocuments {
  let result = d;
  for (const rail of d.rails ?? []) if (rail.leaderIds.includes(id)) {
    const next = { ...rail, leaderIds: rail.leaderIds.filter(member => member !== id) };
    result = distributeRail(result, next);
  }
  return result;
}
