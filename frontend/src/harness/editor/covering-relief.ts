import type { Point } from "./model";

/** Derived drawing data; never persisted in the electrical/production model. */
export interface CoveringRidge { readonly points: readonly Point[]; readonly radii: readonly number[] }
interface EnvelopeStation {
 readonly at: number;
 readonly side: "from" | "to" | "core";
 readonly members: readonly { readonly point: Point; readonly radius: number }[];
}

export function coveringRidges(samples: readonly EnvelopeStation[], from: number, to: number, clearance: number, origin: Point = { x: 0, y: 0 }): CoveringRidge[] {
 const ridges: CoveringRidge[] = [];
 for (const side of ["from", "core", "to"] as const) {
  const stations = samples.filter(sample => sample.side === side);
  for (let member = 0; member < (stations[0]?.members.length ?? 0); member++) {
   const points: Point[] = [], radii: number[] = [];
   for (let i = 1; i < stations.length; i++) {
    const a = stations[i - 1]!, b = stations[i]!;
    const one = a.members[member], two = b.members[member];
    if (!one || !two) continue;
    // Keep the neighbouring stations as well: square fronts can extend past
    // the mean route station while still enclosing a member's shoulder.
    const margin = 3 * (Math.max(one.radius, two.radius) + clearance);
    if (b.at < from - margin || a.at > to + margin) continue;
    if (!points.length) { points.push(one.point); radii.push(one.radius + clearance); }
    points.push(two.point); radii.push(two.radius + clearance);
   }
   if (points.length > 1) ridges.push({ points: points.map(p => ({ x: p.x - origin.x, y: p.y - origin.y })), radii });
  }
 }
 return ridges;
}

/** A soft cylindrical crest decays into an occluded valley between members.
 * Taking the brightest support avoids doubled highlights at overlapping
 * stations and keeps the result independent of member order. */
export function coveringReliefLight(distance: number, radius: number): number {
 return -.25 + .68 * Math.exp(-2 * (distance / Math.max(.01, radius)) ** 2);
}

interface ReliefMask { readonly canvas: HTMLCanvasElement; readonly x: number; readonly y: number; readonly width: number; readonly height: number }
const masks = new Map<string, ReliefMask>();

export function drawCoveringRelief(context: CanvasRenderingContext2D, polygon: readonly Point[], ridges: readonly CoveringRidge[], origin: Point = { x: 0, y: 0 }): boolean {
 if (typeof document === "undefined" || !ridges.length || polygon.length < 3) return false;
 const key = JSON.stringify([polygon, ridges, origin]);
 let mask = masks.get(key);
 if (!mask) {
  const x = Math.min(...polygon.map(p => p.x)), y = Math.min(...polygon.map(p => p.y));
  const width = Math.max(...polygon.map(p => p.x)) - x, height = Math.max(...polygon.map(p => p.y)) - y;
  if (!(width > 0 && height > 0)) return false;
  // Cache at drawing resolution; no readback of the material texture. Limit
  // large drawings to a bounded mask without tying texture scale to its size.
  const step = Math.max(1, width / 768, height / 768);
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.ceil(width / step)); canvas.height = Math.max(1, Math.ceil(height / step));
  const target = canvas.getContext("2d");
  if (!target) return false;
  const dx = width / canvas.width, dy = height / canvas.height;
  const field = new Float32Array(canvas.width * canvas.height);
  field.fill(-.25);
  for (const ridge of ridges) for (let i = 1; i < ridge.points.length; i++) {
   const start = ridge.points[i - 1]!, end = ridge.points[i]!;
   const a = { x: start.x + origin.x, y: start.y + origin.y }, b = { x: end.x + origin.x, y: end.y + origin.y };
   const ra = ridge.radii[i - 1]!, rb = ridge.radii[i]!;
   const reach = Math.max(ra, rb) * 2.5, vx = b.x - a.x, vy = b.y - a.y, length2 = vx * vx + vy * vy;
   const minX = Math.max(0, Math.floor((Math.min(a.x, b.x) - reach - x) / dx));
   const maxX = Math.min(canvas.width - 1, Math.ceil((Math.max(a.x, b.x) + reach - x) / dx));
   const minY = Math.max(0, Math.floor((Math.min(a.y, b.y) - reach - y) / dy));
   const maxY = Math.min(canvas.height - 1, Math.ceil((Math.max(a.y, b.y) + reach - y) / dy));
   for (let row = minY; row <= maxY; row++) for (let col = minX; col <= maxX; col++) {
    const px = x + (col + .5) * dx - a.x, py = y + (row + .5) * dy - a.y;
    const t = length2 ? Math.max(0, Math.min(1, (px * vx + py * vy) / length2)) : 0;
    const light = coveringReliefLight(Math.hypot(px - vx * t, py - vy * t), ra + (rb - ra) * t);
    const index = row * canvas.width + col;
    field[index] = Math.max(field[index]!, light);
   }
  }
  const pixels = target.createImageData(canvas.width, canvas.height);
  for (let i = 0; i < field.length; i++) {
   const light = field[i]!, color = light > 0 ? 255 : 0;
   pixels.data[i * 4] = pixels.data[i * 4 + 1] = pixels.data[i * 4 + 2] = color;
   pixels.data[i * 4 + 3] = Math.round(Math.abs(light) * 255);
  }
  target.putImageData(pixels, 0, 0);
  mask = { canvas, x, y, width, height };
  if (masks.size >= 24) masks.delete(masks.keys().next().value!);
  masks.set(key, mask);
 }
 context.drawImage(mask.canvas, mask.x, mask.y, mask.width, mask.height);
 return true;
}
