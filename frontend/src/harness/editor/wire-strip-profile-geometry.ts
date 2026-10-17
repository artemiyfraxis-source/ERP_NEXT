import {
  calculateWireStripSteps,
  type Point,
  type WireStripProfileBinding,
  type WireStripStep,
} from "./model";

export type WireStripProfileEnd = "from" | "to";

export interface WireStripProfilePrimitive {
  readonly layerIndex: number;
  readonly diameterMm: number;
  readonly cumulativeLengthMm: number;
  readonly stepLengthMm: number;
  /** Centre-line interval occupied by this layer, directed into the wire. */
  readonly centerline: readonly [Point, Point];
  /** Four corners in stable positive-normal/negative-normal winding order. */
  readonly polygon: readonly [Point, Point, Point, Point];
}

/**
 * Screen-independent geometry of one stepped end treatment. The origin is the
 * selected polyline endpoint and `direction` always points into the wire.
 * Rendering concerns such as zoom, stroke width and colours are deliberately
 * absent from this representation.
 */
export interface WireStripProfileGeometry {
  readonly end: WireStripProfileEnd;
  readonly origin: Point;
  readonly direction: Point;
  readonly normal: Point;
  readonly totalLength: number;
  readonly primitives: readonly WireStripProfilePrimitive[];
}

const geometryEpsilon = 1e-9;
/** Below this length the stepped symbol cannot remain legible. */
export const wireStripProfileMinimumSegmentLength = 8;
export const wireStripProfileMaximumLength = 72;
/** Keeps two end profiles on one straight segment from overlapping. */
export const wireStripProfileSegmentFraction = 0.4;
export const wireStripProfileMaximumDiameter = 14;

export function buildWireStripProfileGeometry(
  points: readonly Point[],
  end: WireStripProfileEnd,
  profile: WireStripProfileBinding,
): WireStripProfileGeometry | null {
  try {
    return buildWireStripStepGeometry(points, end, calculateWireStripSteps(profile.layers));
  } catch {
    return null;
  }
}

/**
 * Low-level counterpart useful when the caller has already calculated strip
 * steps. Returns `null` for a missing/degenerate endpoint segment or when the
 * complete profile does not fit on that straight segment.
 */
export function buildWireStripStepGeometry(
  points: readonly Point[],
  end: WireStripProfileEnd,
  steps: readonly WireStripStep[],
): WireStripProfileGeometry | null {
  if (points.length < 2 || steps.length < 1 || !points.every(isFinitePoint)) return null;

  const oriented = endpointDirection(points, end);
  if (oriented === null || oriented.availableLength < wireStripProfileMinimumSegmentLength) return null;

  const maximumPhysicalLength = steps.at(-1)!.cumulativeLengthMm;
  const maximumPhysicalDiameter = Math.max(...steps.map((step) => step.diameterMm));
  if (!Number.isFinite(maximumPhysicalLength) || maximumPhysicalLength <= 0 ||
      !Number.isFinite(maximumPhysicalDiameter) || maximumPhysicalDiameter <= 0) return null;

  const totalLength = cleanCoordinate(Math.min(
    wireStripProfileMaximumLength,
    oriented.availableLength * wireStripProfileSegmentFraction,
  ));
  const axialScale = totalLength / maximumPhysicalLength;
  const crossScale = wireStripProfileMaximumDiameter / maximumPhysicalDiameter;

  const normal = Object.freeze({ x: -oriented.direction.y, y: oriented.direction.x });
  let axialStart = 0;
  const primitives: WireStripProfilePrimitive[] = [];
  for (const step of steps) {
    const axialEnd = step.cumulativeLengthMm * axialScale;
    const halfDiameter = step.diameterMm * crossScale / 2;
    if (!validStep(step, axialStart, axialEnd, halfDiameter)) return null;

    const centerStart = offset(oriented.origin, oriented.direction, axialStart);
    const centerEnd = offset(oriented.origin, oriented.direction, axialEnd);
    primitives.push(Object.freeze({
      layerIndex: step.index,
      diameterMm: step.diameterMm,
      cumulativeLengthMm: step.cumulativeLengthMm,
      stepLengthMm: step.stepLengthMm,
      centerline: Object.freeze([centerStart, centerEnd] as const),
      polygon: Object.freeze([
        offset(centerStart, normal, halfDiameter),
        offset(centerEnd, normal, halfDiameter),
        offset(centerEnd, normal, -halfDiameter),
        offset(centerStart, normal, -halfDiameter),
      ] as const),
    }));
    axialStart = axialEnd;
  }

  return Object.freeze({
    end,
    origin: oriented.origin,
    direction: oriented.direction,
    normal,
    totalLength,
    primitives: Object.freeze(primitives),
  });
}

/**
 * Same geometry at the drawing's own length scale: `axial` is the new total length along the wire, `cross` the new
 * widest diameter (both in drawing units). Layer proportions stay; the origin and direction do not move.
 */
export function scaleWireStripProfileGeometry(
  geometry: WireStripProfileGeometry,
  axial: number,
  cross: number,
  /** Сдвиг вдоль провода от конца (например, за точку узла, что закрывает конец). */
  shift = 0,
): WireStripProfileGeometry {
  const kx = axial / geometry.totalLength;
  const maxHalf = Math.max(...geometry.primitives.map((primitive) => Math.hypot(
    primitive.polygon[0].x - primitive.centerline[0].x, primitive.polygon[0].y - primitive.centerline[0].y)));
  const ky = maxHalf > 0 ? cross / 2 / maxHalf : 1;
  const map = (point: Point): Point => {
    const dx = point.x - geometry.origin.x, dy = point.y - geometry.origin.y;
    const along = (dx * geometry.direction.x + dy * geometry.direction.y) * kx + shift;
    const side = (dx * geometry.normal.x + dy * geometry.normal.y) * ky;
    return Object.freeze({
      x: cleanCoordinate(geometry.origin.x + geometry.direction.x * along + geometry.normal.x * side),
      y: cleanCoordinate(geometry.origin.y + geometry.direction.y * along + geometry.normal.y * side),
    });
  };
  return Object.freeze({
    ...geometry,
    totalLength: cleanCoordinate(axial),
    primitives: Object.freeze(geometry.primitives.map((primitive) => Object.freeze({
      ...primitive,
      centerline: Object.freeze([map(primitive.centerline[0]), map(primitive.centerline[1])] as const),
      polygon: Object.freeze(primitive.polygon.map(map) as unknown as [Point, Point, Point, Point]),
    }))),
  });
}

function endpointDirection(
  points: readonly Point[],
  end: WireStripProfileEnd,
): { readonly origin: Point; readonly direction: Point; readonly availableLength: number } | null {
  const originIndex = end === "from" ? 0 : points.length - 1;
  const increment = end === "from" ? 1 : -1;
  const origin = points[originIndex]!;
  for (let index = originIndex + increment; index >= 0 && index < points.length; index += increment) {
    const candidate = points[index]!;
    const dx = candidate.x - origin.x;
    const dy = candidate.y - origin.y;
    const availableLength = Math.hypot(dx, dy);
    if (availableLength <= geometryEpsilon) continue;
    return Object.freeze({
      origin: Object.freeze({ ...origin }),
      direction: Object.freeze({ x: dx / availableLength, y: dy / availableLength }),
      availableLength,
    });
  }
  return null;
}

function offset(point: Point, direction: Point, distance: number): Point {
  return Object.freeze({
    x: cleanCoordinate(point.x + direction.x * distance),
    y: cleanCoordinate(point.y + direction.y * distance),
  });
}

function cleanCoordinate(value: number): number {
  const rounded = Math.round(value * 1e9) / 1e9;
  return Math.abs(rounded) <= geometryEpsilon ? 0 : rounded;
}

function isFinitePoint(point: Point): boolean {
  return Number.isFinite(point.x) && Number.isFinite(point.y);
}

function validStep(step: WireStripStep, axialStart: number, axialEnd: number, halfDiameter: number): boolean {
  return Number.isSafeInteger(step.index) && step.index > 0 &&
    Number.isFinite(step.stepLengthMm) && step.stepLengthMm > 0 &&
    Number.isFinite(step.cumulativeLengthMm) && step.cumulativeLengthMm > 0 &&
    Number.isFinite(step.diameterMm) && step.diameterMm > 0 &&
    Number.isFinite(axialEnd) && axialEnd > axialStart &&
    Number.isFinite(halfDiameter) && halfDiameter > 0;
}
