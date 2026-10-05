// @vitest-environment node
import { describe, expect, it } from "vitest";
import { arrayPositions } from "./array-layout";
describe("drawing arrays", () => {
  it.each([1, 2, 3, 4])("creates %i rows and one unique cell per pin", rows => {
    for (const direction of ["short-side", "long-side"] as const) for (const numbering of ["new-row", "snake"] as const) {
      const cells = arrayPositions(12, { rows, direction, numbering, countSource: "article" }, 20, 30);
      expect(cells).toHaveLength(12);
      expect(new Set(cells.map(p => `${p.x}:${p.y}`)).size).toBe(12);
      expect(new Set(cells.map(p => p.y)).size).toBe(rows);
    }
  });
  it("numbers along or across rows and in a snake", () => {
    const base = { rows: 2, direction: "long-side", numbering: "new-row", countSource: "article" } as const;
    expect(arrayPositions(6, base, 10, 20)).toEqual([{x:0,y:0},{x:10,y:0},{x:20,y:0},{x:0,y:20},{x:10,y:20},{x:20,y:20}]);
    expect(arrayPositions(6, {...base, numbering:"snake"}, 10, 20).slice(3)).toEqual([{x:20,y:20},{x:10,y:20},{x:0,y:20}]);
    expect(arrayPositions(6, {...base, direction:"short-side"}, 10, 20).slice(0,4)).toEqual([{x:0,y:0},{x:0,y:20},{x:10,y:0},{x:10,y:20}]);
    expect(arrayPositions(5, {...base, numbering:"snake"}, 10, 20)).toHaveLength(5);
  });
  it("rejects oversized arrays and invalid pitch", () => {
    const base = { rows: 1, direction: "long-side", numbering: "new-row", countSource: "article" } as const;
    expect(() => arrayPositions(1001, base, 20, 20)).toThrow();
    expect(() => arrayPositions(10, {...base, rows:5}, 20, 20)).toThrow();
    expect(() => arrayPositions(10, base, 0, 20)).toThrow();
  });
});
