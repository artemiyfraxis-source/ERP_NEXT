import type { Point } from "./model";

export interface E4WireLabelLayout {
  readonly anchor: Point;
  readonly orientation: "horizontal" | "vertical";
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

const labelHeight = 18;
const labelGap = 5;
const labelHorizontalPadding = 5;
const estimatedCharacterWidth = 7.2;

export function normalizeE4WireLabelPosition(value: number): number {
  if (!Number.isFinite(value) || value < 0 || value > 1) {
    throw new Error("Положение обозначения провода должно быть от 0 до 1.");
  }
  return value;
}

export function getE4WireLabelLayout(
  points: readonly Point[],
  label: string,
  position: number,
): E4WireLabelLayout | null {
  const placement = pointAtPolylinePosition(points, normalizeE4WireLabelPosition(position));
  if (!placement) return null;
  const width = Math.max(18, Math.ceil(Array.from(label).length * estimatedCharacterWidth + 2 * labelHorizontalPadding));
  return placement.orientation === "horizontal"
    ? {
      ...placement,
      x: placement.anchor.x - width / 2,
      y: placement.anchor.y - labelGap - labelHeight,
      width,
      height: labelHeight,
    }
    : {
      ...placement,
      x: placement.anchor.x + labelGap,
      y: placement.anchor.y - labelHeight / 2,
      width,
      height: labelHeight,
    };
}

export function projectPointToE4WireLabelPosition(points: readonly Point[], point: Point): number | null {
  const segments = polylineSegments(points);
  const totalLength = segments.reduce((sum, segment) => sum + segment.length, 0);
  if (totalLength <= 0) return null;
  let elapsed = 0;
  let best: { distance: number; along: number } | null = null;
  for (const segment of segments) {
    const projected = segment.orientation === "horizontal"
      ? {
        x: clamp(point.x, Math.min(segment.start.x, segment.end.x), Math.max(segment.start.x, segment.end.x)),
        y: segment.start.y,
      }
      : {
        x: segment.start.x,
        y: clamp(point.y, Math.min(segment.start.y, segment.end.y), Math.max(segment.start.y, segment.end.y)),
      };
    const distance = Math.hypot(point.x - projected.x, point.y - projected.y);
    const local = Math.abs(projected.x - segment.start.x) + Math.abs(projected.y - segment.start.y);
    if (!best || distance < best.distance || distance === best.distance && elapsed + local < best.along) {
      best = { distance, along: elapsed + local };
    }
    elapsed += segment.length;
  }
  return best ? best.along / totalLength : null;
}

function pointAtPolylinePosition(
  points: readonly Point[],
  position: number,
): Pick<E4WireLabelLayout, "anchor" | "orientation"> | null {
  const segments = polylineSegments(points);
  const totalLength = segments.reduce((sum, segment) => sum + segment.length, 0);
  if (totalLength <= 0) return null;
  const target = position * totalLength;
  let elapsed = 0;
  for (const [index, segment] of segments.entries()) {
    if (target <= elapsed + segment.length || index === segments.length - 1) {
      const distance = clamp(target - elapsed, 0, segment.length);
      const ratio = segment.length === 0 ? 0 : distance / segment.length;
      return {
        anchor: {
          x: segment.start.x + (segment.end.x - segment.start.x) * ratio,
          y: segment.start.y + (segment.end.y - segment.start.y) * ratio,
        },
        orientation: segment.orientation,
      };
    }
    elapsed += segment.length;
  }
  return null;
}

function polylineSegments(points: readonly Point[]) {
  return points.flatMap((end, index) => {
    if (index === 0) return [];
    const start = points[index - 1]!;
    const horizontal = start.y === end.y && start.x !== end.x;
    const vertical = start.x === end.x && start.y !== end.y;
    if (!horizontal && !vertical) return [];
    return [{
      start,
      end,
      orientation: horizontal ? "horizontal" as const : "vertical" as const,
      length: Math.abs(end.x - start.x) + Math.abs(end.y - start.y),
    }];
  });
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

