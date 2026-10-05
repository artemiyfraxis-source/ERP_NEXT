import type { PhysicalPath, PhysicalSegment } from "./physical-topology-model";

/** Only the load boundary understands pre-M4-91 bends/routing. Never mutates saved input. */
export function readPhysicalSegmentPath(value: unknown, fail: () => never): PhysicalSegment {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail();
  const record = value as Record<string, unknown>;
  if ("path" in record) {
    if ("bends" in record || "routing" in record) return fail();
    const path = record.path as Partial<PhysicalPath> | null;
    if (!path || (path.kind !== "routed" && path.kind !== "polyline") || !validPoints(path.points)) return fail();
    return value as PhysicalSegment;
  }
  if (!validPoints(record.bends) || record.routing !== undefined && record.routing !== "auto" && record.routing !== "fixed") return fail();
  const { bends, routing, ...rest } = record;
  return { ...rest, path: { kind: routing === "fixed" ? "polyline" : "routed", points: bends } } as PhysicalSegment;
}

function validPoints(value: unknown): boolean {
  return Array.isArray(value) && value.length <= 1000 && value.every(point => point &&
    Number.isFinite(point.x) && Number.isFinite(point.y) && Math.abs(point.x) <= 1e7 && Math.abs(point.y) <= 1e7);
}
