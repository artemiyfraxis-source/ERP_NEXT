import type { CableInstance } from "./model";
import type { EditorPoint, EditorSceneObject } from "./editor-types";

export type CableSheathEdge = readonly [EditorPoint, EditorPoint];

export interface CableSheathMemberSpan {
  readonly wireId: string;
  readonly routeIndex: number;
  /** The complete source segment, preserving its original route direction. */
  readonly source: CableSheathEdge;
  /** The part of the member centreline covered by the common sheath. */
  readonly covered: CableSheathEdge;
  readonly sourceDirection: 1 | -1;
}

export interface CableSheathGeometry {
  readonly cableId: string;
  readonly memberWireIds: readonly string[];
  readonly routeIndex: number;
  /** Canonical unit vector along the selected common segment. */
  readonly direction: EditorPoint;
  /** Canonical left-hand normal of `direction`. */
  readonly normal: EditorPoint;
  readonly length: number;
  readonly crossMinimum: number;
  readonly crossMaximum: number;
  readonly outerCrossMinimum: number;
  readonly outerCrossMaximum: number;
  readonly centerline: CableSheathEdge;
  /** Stable winding: start/min, end/min, end/max, start/max. */
  readonly polygon: readonly [EditorPoint, EditorPoint, EditorPoint, EditorPoint];
  readonly edges: readonly [CableSheathEdge, CableSheathEdge, CableSheathEdge, CableSheathEdge];
  readonly bounds: Readonly<{ x: number; y: number; width: number; height: number }>;
  readonly memberSpans: Readonly<Record<string, CableSheathMemberSpan>>;
}

const geometryEpsilon = 1e-9;
const parallelTolerance = 1e-6;

/** A shorter visual trunk cannot communicate that several conductors share one cable. */
export const cableSheathMinimumSpanLength = 12;
export const cableSheathCrossPadding = 6;
export const cableSheathMinimumCrossSize = 14;

interface Segment {
  readonly routeIndex: number;
  readonly start: EditorPoint;
  readonly end: EditorPoint;
  readonly direction: EditorPoint;
}

interface Candidate {
  readonly routeIndex: number;
  readonly direction: EditorPoint;
  readonly normal: EditorPoint;
  readonly alongStart: number;
  readonly alongEnd: number;
  readonly crosses: readonly number[];
  readonly segments: readonly Segment[];
  readonly sourceDirections: readonly (1 | -1)[];
}

/**
 * Derives one conservative common sheath trunk from the scene polylines of a
 * physical multicore cable. Segments are compared only at the same route
 * index. This deliberately avoids guessing how independently routed branches
 * should be joined.
 */
export function buildCableSheathGeometry(
  cable: CableInstance,
  objects: readonly EditorSceneObject[],
): CableSheathGeometry | null {
  if (cable.memberWireIds.length < 2 || new Set(cable.memberWireIds).size !== cable.memberWireIds.length) return null;

  const memberObjects: EditorSceneObject[] = [];
  const segmentsByWire = cable.memberWireIds.map((wireId) => {
    const matches = objects.filter((object) => object.id === wireId && object.kind === "wire");
    if (matches.length !== 1 || !validPolyline(matches[0]!.points)) return null;
    memberObjects.push(matches[0]!);
    return polylineSegments(matches[0]!.points!);
  });
  if (segmentsByWire.some((segments) => segments === null)) return null;
  if (new Set(memberObjects.map((object) => object.layerId)).size !== 1) return null;
  const segmentGroups = segmentsByWire as readonly (readonly Segment[])[];
  const segmentCount = segmentGroups[0]!.length;
  if (segmentCount === 0 || segmentGroups.some((segments) => segments.length !== segmentCount)) return null;

  // Physical lengths are independent of canvas scale. The first member defines
  // the cable's from/to direction; technological allowances do not move its installed ends.
  const strip = cable.sheathStrip;
  if (strip && (strip.fromMm === null || strip.toMm === null || cable.lengthMm === null || cable.lengthMm <= 0)) return null;
  const reference = segmentGroups[0]!;
  const referenceLengths = reference.map(segment => Math.hypot(segment.end.x - segment.start.x, segment.end.y - segment.start.y));
  const totalVisualLength = referenceLengths.reduce((sum, value) => sum + value, 0);
  const fromDistance = strip ? totalVisualLength * strip.fromMm! / cable.lengthMm! : 0;
  const toDistance = strip ? totalVisualLength * (1 - strip.toMm! / cable.lengthMm!) : totalVisualLength;
  let distanceBefore = 0;

  const candidates: Candidate[] = [];
  for (let routeIndex = 0; routeIndex < segmentCount; routeIndex += 1) {
    const segments = segmentGroups.map((items) => items[routeIndex]!);
    const direction = canonicalDirection(segments[0]!.direction);
    if (segments.some((segment) => Math.abs(crossProduct(direction, segment.direction)) > parallelTolerance)) return null;

    const normal = freezePoint(-direction.y, direction.x);
    const starts = segments.map((segment) => Math.min(dot(segment.start, direction), dot(segment.end, direction)));
    const ends = segments.map((segment) => Math.max(dot(segment.start, direction), dot(segment.end, direction)));
    const referenceSegment = reference[routeIndex]!;
    const referenceLength = referenceLengths[routeIndex]!;
    const clipStart = Math.max(0, fromDistance - distanceBefore);
    const clipEnd = Math.min(referenceLength, toDistance - distanceBefore);
    distanceBefore += referenceLength;
    if (strip && clipEnd <= clipStart) continue;
    const sign = dot(referenceSegment.direction, direction) >= 0 ? 1 : -1;
    const origin = dot(referenceSegment.start, direction);
    const clipCoordinates = [origin + sign * clipStart, origin + sign * clipEnd];
    const alongStart = Math.max(...starts, ...(strip ? [Math.min(...clipCoordinates)] : []));
    const alongEnd = Math.min(...ends, ...(strip ? [Math.max(...clipCoordinates)] : []));
    if (alongEnd - alongStart + geometryEpsilon < cableSheathMinimumSpanLength) continue;

    candidates.push(Object.freeze({
      routeIndex,
      direction,
      normal,
      alongStart,
      alongEnd,
      crosses: Object.freeze(segments.map((segment) => cleanCoordinate(dot(segment.start, normal)))),
      segments: Object.freeze(segments),
      sourceDirections: Object.freeze(segments.map((segment) => dot(segment.direction, direction) >= 0 ? 1 : -1)),
    }));
  }
  if (candidates.length === 0) return null;

  candidates.sort((left, right) =>
    (right.alongEnd - right.alongStart) - (left.alongEnd - left.alongStart) ||
    left.routeIndex - right.routeIndex ||
    left.alongStart - right.alongStart ||
    left.alongEnd - right.alongEnd);
  return geometryFromCandidate(cable, candidates[0]!);
}

function geometryFromCandidate(cable: CableInstance, candidate: Candidate): CableSheathGeometry {
  const crossMinimum = Math.min(...candidate.crosses);
  const crossMaximum = Math.max(...candidate.crosses);
  const crossCenter = (crossMinimum + crossMaximum) / 2;
  const halfCrossSize = Math.max(
    cableSheathMinimumCrossSize / 2,
    (crossMaximum - crossMinimum) / 2 + cableSheathCrossPadding,
  );
  const outerCrossMinimum = cleanCoordinate(crossCenter - halfCrossSize);
  const outerCrossMaximum = cleanCoordinate(crossCenter + halfCrossSize);
  const polygon = Object.freeze([
    projectedPoint(candidate, candidate.alongStart, outerCrossMinimum),
    projectedPoint(candidate, candidate.alongEnd, outerCrossMinimum),
    projectedPoint(candidate, candidate.alongEnd, outerCrossMaximum),
    projectedPoint(candidate, candidate.alongStart, outerCrossMaximum),
  ] as const);
  const centerline = Object.freeze([
    projectedPoint(candidate, candidate.alongStart, crossCenter),
    projectedPoint(candidate, candidate.alongEnd, crossCenter),
  ] as const);
  const edges = Object.freeze([
    Object.freeze([polygon[0], polygon[1]] as const),
    Object.freeze([polygon[1], polygon[2]] as const),
    Object.freeze([polygon[2], polygon[3]] as const),
    Object.freeze([polygon[3], polygon[0]] as const),
  ] as const);
  const xs = polygon.map((point) => point.x);
  const ys = polygon.map((point) => point.y);
  const memberSpans = Object.freeze(Object.fromEntries(cable.memberWireIds.map((wireId, index) => {
    const segment = candidate.segments[index]!;
    const cross = candidate.crosses[index]!;
    return [wireId, Object.freeze({
      wireId,
      routeIndex: candidate.routeIndex,
      source: Object.freeze([freezePoint(segment.start.x, segment.start.y), freezePoint(segment.end.x, segment.end.y)] as const),
      covered: Object.freeze([
        projectedPoint(candidate, candidate.alongStart, cross),
        projectedPoint(candidate, candidate.alongEnd, cross),
      ] as const),
      sourceDirection: candidate.sourceDirections[index]!,
    })];
  })));

  return Object.freeze({
    cableId: cable.id,
    memberWireIds: Object.freeze([...cable.memberWireIds]),
    routeIndex: candidate.routeIndex,
    direction: candidate.direction,
    normal: candidate.normal,
    length: cleanCoordinate(candidate.alongEnd - candidate.alongStart),
    crossMinimum,
    crossMaximum,
    outerCrossMinimum,
    outerCrossMaximum,
    centerline,
    polygon,
    edges,
    bounds: Object.freeze({
      x: Math.min(...xs),
      y: Math.min(...ys),
      width: cleanCoordinate(Math.max(...xs) - Math.min(...xs)),
      height: cleanCoordinate(Math.max(...ys) - Math.min(...ys)),
    }),
    memberSpans,
  });
}

function validPolyline(points: readonly EditorPoint[] | undefined): points is readonly EditorPoint[] {
  return points !== undefined && points.length >= 2 && points.every((point) =>
    Number.isFinite(point.x) && Number.isFinite(point.y));
}

function polylineSegments(points: readonly EditorPoint[]): readonly Segment[] {
  const segments: Segment[] = [];
  for (let pointIndex = 1; pointIndex < points.length; pointIndex += 1) {
    const start = points[pointIndex - 1]!;
    const end = points[pointIndex]!;
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const length = Math.hypot(dx, dy);
    if (length <= geometryEpsilon) continue;
    segments.push(Object.freeze({
      routeIndex: segments.length,
      start,
      end,
      direction: freezePoint(dx / length, dy / length),
    }));
  }
  return Object.freeze(segments);
}

function canonicalDirection(direction: EditorPoint): EditorPoint {
  const reverse = Math.abs(direction.x) >= Math.abs(direction.y) ? direction.x < 0 : direction.y < 0;
  return reverse ? freezePoint(-direction.x, -direction.y) : freezePoint(direction.x, direction.y);
}

function projectedPoint(candidate: Candidate, along: number, cross: number): EditorPoint {
  return freezePoint(
    candidate.direction.x * along + candidate.normal.x * cross,
    candidate.direction.y * along + candidate.normal.y * cross,
  );
}

function dot(left: EditorPoint, right: EditorPoint): number {
  return left.x * right.x + left.y * right.y;
}

function crossProduct(left: EditorPoint, right: EditorPoint): number {
  return left.x * right.y - left.y * right.x;
}

function freezePoint(x: number, y: number): EditorPoint {
  return Object.freeze({ x: cleanCoordinate(x), y: cleanCoordinate(y) });
}

function cleanCoordinate(value: number): number {
  const rounded = Math.round(value * 1e9) / 1e9;
  return Math.abs(rounded) <= geometryEpsilon ? 0 : rounded;
}
