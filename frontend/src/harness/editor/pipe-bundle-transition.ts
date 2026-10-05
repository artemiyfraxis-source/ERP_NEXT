import type { Point } from "./model";
import { drawingRouteSamples } from "./drawing-route-path";

const mix = (a: Point, b: Point, t: number): Point => ({
  x: a.x + (b.x - a.x) * t,
  y: a.y + (b.y - a.y) * t,
});

/** The sleeve-side shoulder stays on its axis and meets the incoming route
 * at one ordinary, independently controlled bend. */
export function bundleTransitionPoint(
  a: Point,
  b: Point,
  tangent: Point,
  t: number,
  radius: number,
  bend: Point = { x: 0, y: 0 },
): Point {
  if (t <= 0) return a;
  if (t >= 1) return b;
  const norm = Math.hypot(tangent.x, tangent.y) || 1;
  const axis = { x: tangent.x / norm, y: tangent.y / norm };
  const length = (b.x - a.x) * axis.x + (b.y - a.y) * axis.y;
  if (length <= 1e-6) return mix(a, b, t);

  // The bend handle's normal motion changes where the turn begins, while its
  // along-axis motion changes the transition length in covering-layout.
  const normalMotion = -bend.x * axis.y + bend.y * axis.x;
  const shoulder = Math.max(length / 4, Math.min(length * 3 / 4, length / 2 - normalMotion));
  const corner = { x: b.x - axis.x * shoulder, y: b.y - axis.y * shoulder };
  const effectiveRadius = Math.min(
    Math.max(0, radius), length / 4,
    Math.hypot(corner.x - a.x, corner.y - a.y) / 3,
    Math.hypot(b.x - corner.x, b.y - corner.y) / 3,
  );
  const samples = drawingRouteSamples([a, corner, b], effectiveRadius);
  const target = t * (samples.at(-1)?.distance || length);
  for (let i = 1; i < samples.length; i++) {
    const first = samples[i - 1]!, second = samples[i]!;
    if (target <= second.distance + 1e-8) {
      const u = (target - first.distance) / (second.distance - first.distance || 1);
      return mix(first.point, second.point, Math.max(0, Math.min(1, u)));
    }
  }
  return samples.at(-1)?.point ?? b;
}
