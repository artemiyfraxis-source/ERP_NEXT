// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  getE4WireLabelLayout,
  normalizeE4WireLabelPosition,
  projectPointToE4WireLabelPosition,
} from "./e4-wire-label";

const route = [
  { x: 0, y: 0 },
  { x: 100, y: 0 },
  { x: 100, y: 100 },
];

describe("E4 wire label geometry", () => {
  it("places a horizontal label by normalized route length", () => {
    expect(getE4WireLabelLayout(route, "W1", 0.25)).toMatchObject({
      anchor: { x: 50, y: 0 }, orientation: "horizontal", y: -23, height: 18,
    });
  });

  it("moves around a bend and projects a pointer back to the route", () => {
    expect(getE4WireLabelLayout(route, "CAN-H", 0.75)).toMatchObject({
      anchor: { x: 100, y: 50 }, orientation: "vertical", x: 105,
    });
    expect(projectPointToE4WireLabelPosition(route, { x: 108, y: 60 })).toBeCloseTo(0.8);
    expect(projectPointToE4WireLabelPosition(route, { x: 25, y: -12 })).toBeCloseTo(0.125);
  });

  it("rejects an invalid persisted position", () => {
    expect(() => normalizeE4WireLabelPosition(-0.1)).toThrow(/от 0 до 1/);
    expect(() => normalizeE4WireLabelPosition(1.1)).toThrow(/от 0 до 1/);
    expect(projectPointToE4WireLabelPosition([{ x: 0, y: 0 }], { x: 0, y: 0 })).toBeNull();
  });
});

