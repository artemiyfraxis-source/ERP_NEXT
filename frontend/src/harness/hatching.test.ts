// @vitest-environment node
import { expect, it, vi } from "vitest";
import { drawCatalogHatch, hatchingCatalog, hatchSegments, knownHatchCode, validHatchCode } from "./hatching";
import { validCoveringStyle } from "./editor/covering-style";

const bounds = { x: 0, y: 0, width: 160, height: 64 };

it.each(hatchingCatalog)("renders $code ($title) within the requested bounds", (hatch) => {
  const paths = hatchSegments(hatch.code, 1, 0, bounds);
  expect(paths.length).toBeGreaterThan(0);
  for (const pair of paths) for (const point of pair) {
    expect(point.x).toBeGreaterThanOrEqual(-1e-6); expect(point.x).toBeLessThanOrEqual(160 + 1e-6);
    expect(point.y).toBeGreaterThanOrEqual(-1e-6); expect(point.y).toBeLessThanOrEqual(64 + 1e-6);
  }
});

it("has unique codes and Russian titles", () => {
  expect(new Set(hatchingCatalog.map((h) => h.code)).size).toBe(hatchingCatalog.length);
  for (const h of hatchingCatalog) expect(h.title).toMatch(/[А-Яа-я]/);
});

it("keeps phase across neighbouring clipped regions instead of repeating a cropped image", () => {
  const right = hatchSegments("Ш1", 1, 0, { x: 160, y: 0, width: 160, height: 64 });
  expect(right.map(([a, b]) => [a.x - 160, a.y, b.x - 160, b.y])).not.toEqual(hatchSegments("Ш1", 1, 0, bounds).map(([a, b]) => [a.x, a.y, b.x, b.y]));
});

it("changes spacing and stroke width independently", () => {
  const widths: number[] = [];
  const ctx = new Proxy({ stroke() { widths.push(this.lineWidth); }, lineWidth: 0 }, { get: (o, k) => k in o ? Reflect.get(o, k) : vi.fn() }) as unknown as CanvasRenderingContext2D;
  drawCatalogHatch(ctx, "Ш1", 1, 0, "#123456", 2, bounds);
  drawCatalogHatch(ctx, "Ш1", 2, 0, "#123456", 4, bounds);
  expect(widths).toEqual([2, 4]);
  expect(hatchSegments("Ш1", 2, 0, bounds).length).toBeLessThan(hatchSegments("Ш1", 1, 0, bounds).length);
});

it("draws dots and rotates horizontal families", () => {
  expect(hatchSegments("Ш7", 1, 0, bounds).some(([a, b]) => a.x === b.x && a.y === b.y)).toBe(true);
  expect(hatchSegments("Ш4", 1, 90, bounds).every(([a, b]) => Math.abs(a.x - b.x) < 1e-6)).toBe(true);
});

it("keeps a foreign hatch code from a TECHMAP-GRAPHER document valid but draws nothing for it", () => {
  expect(validHatchCode("H07")).toBe(true);
  expect(knownHatchCode("H07")).toBe(false);
  expect(hatchSegments("H07", 1, 0, bounds)).toEqual([]);
  expect(validCoveringStyle({ hatchCode: "H07" })).toBe(true);
  expect(validHatchCode("<script>")).toBe(false);
});
