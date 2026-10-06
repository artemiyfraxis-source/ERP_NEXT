// @vitest-environment node
import { expect, it } from "vitest";
import { wireDrawingEndArtworkPlacement } from "./CanvasViewport";

it("anchors a free-end terminal at its inner jacket edge and renders it outside the wire", () => {
  const placement = wireDrawingEndArtworkPlacement({ x: 100, y: 40 }, { x: 20, y: 40 }, 5)!;
  // The local SVG x=222 is exactly the stored wire endpoint. Values below it,
  // which contain the terminal, are to the outside (right) of this free end.
  const edge = placement.x + Math.cos(placement.rotation) * 222 * placement.scale;
  const terminal = placement.x + Math.cos(placement.rotation) * 78 * placement.scale;
  expect(edge).toBeCloseTo(100);
  expect(terminal).toBeGreaterThan(100);
  expect(placement.y).toBeCloseTo(40);
});

it("keeps the artwork outside a vertical free end", () => {
  const placement = wireDrawingEndArtworkPlacement({ x: 60, y: 100 }, { x: 60, y: 20 }, 5)!;
  const edge = placement.y + Math.sin(placement.rotation) * 222 * placement.scale;
  const terminal = placement.y + Math.sin(placement.rotation) * 78 * placement.scale;
  expect(edge).toBeCloseTo(100);
  expect(terminal).toBeGreaterThan(100);
});
