// @vitest-environment node
import { describe, expect, it } from "vitest";
import { snapE4ConnectorPosition, type E4ConnectorSnapTarget } from "./e4-connector-snap";

const leftTable: E4ConnectorSnapTarget = {
  id: "left",
  x: 100,
  y: 80,
  width: 180,
  height: 124,
  contactSide: "left",
  firstContactY: 144,
};

describe("E4 connector alignment", () => {
  it("soft-snaps the first contact row without changing the other axis", () => {
    const moving: E4ConnectorSnapTarget = {
      id: "moving", x: 400, y: 200, width: 220, height: 148,
      contactSide: "right", firstContactY: 264,
    };
    const result = snapE4ConnectorPosition(moving, [leftTable], { x: 337, y: 86 }, 1);

    expect(result.position).toEqual({ x: 337, y: 80 });
    expect(result.guides.vertical).toBeUndefined();
    expect(result.guides.horizontal).toEqual({ y: 144, fromX: 100, toX: 557 });
  });

  it("aligns the moving first row with a nearest non-first target row", () => {
    const moving: E4ConnectorSnapTarget = {
      id: "moving", x: 400, y: 200, width: 220, height: 148,
      contactSide: "right", contactRowsY: [224, 248, 272],
    };
    const target: E4ConnectorSnapTarget = {
      ...leftTable, contactRowsY: [104, 128, 152, 176],
    };
    const result = snapE4ConnectorPosition(moving, [target], { x: 400, y: 130 }, 1);

    expect(result.position).toEqual({ x: 400, y: 128 });
    expect(result.guides.horizontal).toEqual({ y: 152, fromX: 100, toX: 620 });
  });

  it("does not pull the table towards a match on a lower moving row", () => {
    const moving = { ...leftTable, id: "moving", x: 400, y: 200,
      firstContactY: 224, contactRowsY: [224, 248, 272] };
    const target = { ...leftTable, contactRowsY: [144] };
    const result = snapE4ConnectorPosition(moving, [target], { x: 400, y: 98 }, 1);
    expect(result.position.y).toBe(98);
    expect(result.guides.horizontal).toBeUndefined();
  });

  it("aligns the actual contact edges for left and right orientations", () => {
    const moving: E4ConnectorSnapTarget = {
      id: "moving", x: 400, y: 200, width: 220, height: 148,
      contactSide: "right", firstContactY: 264,
    };
    const result = snapE4ConnectorPosition(moving, [leftTable], { x: -113, y: 260 }, 1);

    // The moving right edge (-120 + 220) lands on the target's left contact edge (100).
    expect(result.position).toEqual({ x: -120, y: 260 });
    expect(result.guides.horizontal).toBeUndefined();
    expect(result.guides.vertical).toEqual({ x: 100, fromY: 80, toY: 408 });
  });

  it("keeps a free position outside the magnetic zone", () => {
    const moving: E4ConnectorSnapTarget = {
      id: "moving", x: 400, y: 200, width: 220, height: 148,
      contactSide: "left", firstContactY: 264,
    };
    const position = { x: 115, y: 100 };
    expect(snapE4ConnectorPosition(moving, [leftTable], position, 1)).toEqual({
      position,
      guides: {},
    });
  });

  it("uses a constant screen-pixel snap distance at different zoom levels", () => {
    const moving: E4ConnectorSnapTarget = {
      id: "moving", x: 400, y: 200, width: 100, height: 100,
      contactSide: "left", firstContactY: 264,
    };
    expect(snapE4ConnectorPosition(moving, [leftTable], { x: 114, y: 80 }, 0.5).position.x).toBe(100);
    expect(snapE4ConnectorPosition(moving, [leftTable], { x: 105, y: 80 }, 2).position.x).toBe(105);
  });

  it("does not create a row guide for a table without contacts", () => {
    const moving = { ...leftTable, id: "empty", x: 400, firstContactY: undefined };
    const result = snapE4ConnectorPosition(moving, [leftTable], { x: 300, y: 82 }, 1);
    expect(result.position.y).toBe(82);
    expect(result.guides.horizontal).toBeUndefined();
  });
});
