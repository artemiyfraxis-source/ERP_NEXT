// @vitest-environment node
import { expect, it } from "vitest";
import { bundleTransitionPoint } from "./pipe-bundle-transition";

it("keeps a straight sleeve-side shoulder when the shared bend control moves", () => {
  const outer = { x: 0, y: 100 };
  const edge = { x: 100, y: 0 };
  const axis = { x: 1, y: 0 };
  const bend = { x: 0, y: 30 };

  for (const radius of [0, 18]) {
    const left = bundleTransitionPoint(outer, edge, axis, .95, radius, bend);
    const right = bundleTransitionPoint(outer, edge, axis, .99, radius, bend);
    expect(left.y).toBeCloseTo(edge.y);
    expect(right.y).toBeCloseTo(edge.y);
    expect(left.x).toBeLessThan(right.x);

    const exit = bundleTransitionPoint(
      { x: 200, y: 100 }, { x: 100, y: 0 }, { x: -1, y: 0 }, .95, radius, bend,
    );
    expect(exit.y).toBeCloseTo(edge.y);
  }
});
