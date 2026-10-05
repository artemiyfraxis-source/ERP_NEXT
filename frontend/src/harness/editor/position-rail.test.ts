// @vitest-environment node
import { describe, expect, it } from "vitest";
import { createPositionRail, movePositionRail, snapRailEnd } from "./position-rail";
import { emptyDrawingDocuments } from "./drawing-documents";

describe("position rails", () => {
  it("allows diagonal rails and moving both axes", () => {
    expect(snapRailEnd({ x: 10, y: 20 }, { x: 90, y: 80 })).toEqual({ x: 90, y: 80 });
    const docs = createPositionRail({ ...emptyDrawingDocuments(), leaders: [
      { id: "l", objectId: "o", rowKey: "r", anchorOffset: { x: 0, y: 0 }, circle: { x: 30, y: 40 } },
    ] }, { id: "rail", start: { x: 0, y: 0 }, end: { x: 100, y: 100 }, leaderIds: ["l"] });
    const moved = movePositionRail(docs, "rail", { x: 30, y: 40 })!;
    expect(moved.rails?.[0]).toMatchObject({ start: { x: 30, y: 40 }, end: { x: 130, y: 140 } });
  });
});
