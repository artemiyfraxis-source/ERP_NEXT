// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { coveringRidges, drawCoveringRelief, type CoveringRidge } from "./covering-relief";
import { drawConformalVolumeSurface } from "./drawing-volume";

afterEach(() => vi.unstubAllGlobals());

function raster(ridges: readonly CoveringRidge[], shift = 0) {
 let pixels: { data: Uint8ClampedArray; width: number; height: number } | undefined;
 const target = {
  createImageData: (width: number, height: number) => ({ data: new Uint8ClampedArray(width * height * 4), width, height }),
  putImageData: (data: typeof pixels) => { pixels = data; },
 };
 vi.stubGlobal("document", { createElement: () => ({ width: 0, height: 0, getContext: () => target }) });
 const calls: string[] = [];
 const context = new Proxy({}, { get: (_, key) => () => { calls.push(String(key)); } }) as CanvasRenderingContext2D;
 const polygon = [{ x: shift, y: 0 }, { x: shift + 100, y: 0 }, { x: shift + 100, y: 80 }, { x: shift, y: 80 }];
 const localRidges = ridges.map(ridge => ({ ...ridge, points: ridge.points.map(p => ({ x: p.x - shift, y: p.y - 40 })) }));
 drawConformalVolumeSurface(context, polygon, [{ x: shift, y: 40 }, { x: shift + 100, y: 40 }], localRidges);
 expect(pixels).toBeDefined();
 const light = (x: number, y: number) => {
  const index = (y * pixels!.width + x) * 4;
  // Composite onto a black material; a clear crest must stay visible even
  // when the shell colour itself has no brightness to multiply.
  return pixels!.data[index]! * pixels!.data[index + 3]! / 255;
 };
 return { light, calls, pixels: pixels! };
}

it("shows two P crests and a darker valley through a black OP shell, clipped to its front", () => {
 const ridges = [20, 60].map(y => ({ points: [{ x: 0, y }, { x: 100, y }], radii: [12, 12] }));
 const result = raster(ridges);
 expect(result.light(50, 20)).toBeGreaterThan(90);
 expect(result.light(50, 60)).toBeGreaterThan(90);
 expect(result.light(50, 40)).toBeLessThan(10);
 expect(result.calls.indexOf("clip")).toBeLessThan(result.calls.indexOf("drawImage"));
 expect(result.calls.at(-1)).toBe("restore");
});

it("moves the lit crests and the valley with diverging P instead of the common axis", () => {
 const result = raster([
  { points: [{ x: 200, y: 30 }, { x: 300, y: 10 }], radii: [9, 9] },
  { points: [{ x: 200, y: 50 }, { x: 300, y: 70 }], radii: [9, 9] },
 ], 200);
 expect(result.light(75, 15)).toBeGreaterThan(90);
 expect(result.light(75, 65)).toBeGreaterThan(90);
 expect(result.light(75, 40)).toBe(0);
 expect(result.light(75, 30)).toBeLessThan(10);
});

it("keeps support geometry, radius and side ordering in derived relief data", () => {
 const members = (x: number) => [{ point: { x, y: 10 }, radius: 4 }, { point: { x, y: 30 }, radius: 8 }];
 const samples = [
  { at: 0, side: "from" as const, members: members(0) },
  { at: 30, side: "from" as const, members: members(30) },
  { at: 30, side: "core" as const, members: members(30) },
  { at: 80, side: "core" as const, members: members(80) },
 ];
 const original = JSON.stringify(samples);
 const ridges = coveringRidges(samples, 0, 80, 2);
 expect(ridges).toHaveLength(4);
 expect(ridges[0]).toEqual({ points: [{ x: 0, y: 10 }, { x: 30, y: 10 }], radii: [6, 6] });
 expect(ridges[3]!.radii).toEqual([10, 10]);
 expect(JSON.stringify(samples)).toBe(original);
 const translated = coveringRidges(samples, 0, 80, 2, { x: 30, y: 10 });
 expect(translated[0]!.points).toEqual([{ x: -30, y: 0 }, { x: 0, y: 0 }]);
});

it("preserves the light mask when a route drawing copy translates the shell", () => {
 const ridges = [{ points: [{ x: 400, y: 40 }, { x: 500, y: 40 }], radii: [16, 16] }];
 const first = raster(ridges, 400).pixels.data;
 const shifted = ridges.map(ridge => ({ ...ridge, points: ridge.points.map(p => ({ x: p.x + 200, y: p.y })) }));
 expect(raster(shifted, 600).pixels.data).toEqual(first);
});

it("does not allocate a mask for missing support data", () => {
 const createElement = vi.fn(); vi.stubGlobal("document", { createElement });
 expect(drawCoveringRelief({} as CanvasRenderingContext2D, [], [])).toBe(false);
 expect(createElement).not.toHaveBeenCalled();
});
