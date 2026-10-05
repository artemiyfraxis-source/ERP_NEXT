// @vitest-environment node
import { describe, expect, it } from "vitest";
import type { CableInstance } from "./model";
import type { EditorPoint, EditorSceneObject } from "./editor-types";
import {
  buildCableSheathGeometry,
  cableSheathMinimumSpanLength,
} from "./cable-sheath-geometry";

const cable: CableInstance = {
  id: "C1",
  memberWireIds: ["W1", "W2"],
  lengthMm: 1_000,
  endCorrectionFromMm: 0,
  endCorrectionToMm: 0,
  cutRoundingStepMm: 1,
};

function wire(id: string, points: readonly EditorPoint[]): EditorSceneObject {
  return { id, layerId: "drawing", kind: "wire", label: id, x: 0, y: 0, width: 0, height: 0, color: "#000", points };
}

describe("cable sheath geometry", () => {
  it("clips installed end stripping proportionally without using cut allowances as geometry", () => {
    const stripped = { ...cable, sheathStrip: { fromMm: 100, toMm: 200 }, endCorrectionFromMm: 50 };
    const objects = [wire("W1", [{ x: 0, y: 0 }, { x: 200, y: 0 }]), wire("W2", [{ x: 0, y: 10 }, { x: 200, y: 10 }])];
    expect(buildCableSheathGeometry(stripped, objects)?.centerline).toEqual([{ x: 20, y: 5 }, { x: 160, y: 5 }]);
    const reverse = objects.map(object => ({ ...object, points: [...object.points!].reverse() }));
    expect(buildCableSheathGeometry(stripped, reverse)?.centerline).toEqual([{ x: 40, y: 5 }, { x: 180, y: 5 }]);
    expect(buildCableSheathGeometry({ ...stripped, sheathStrip: { fromMm: 500, toMm: 500 } }, objects)).toBeNull();
    expect(buildCableSheathGeometry({ ...stripped, sheathStrip: { fromMm: null, toMm: 200 } }, objects)).toBeNull();
    expect(buildCableSheathGeometry({ ...stripped, lengthMm: null }, objects)).toBeNull();
  });

  it("measures stripping along bends before selecting the remaining common trunk", () => {
    const objects = [wire("W1", [{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 100 }]),
      wire("W2", [{ x: 0, y: 10 }, { x: 110, y: 10 }, { x: 110, y: 100 }])];
    expect(buildCableSheathGeometry({ ...cable, sheathStrip: { fromMm: 600, toMm: 100 } }, objects)?.centerline)
      .toEqual([{ x: 105, y: 20 }, { x: 105, y: 80 }]);
  });
  it("builds a horizontal envelope around the common member span", () => {
    const geometry = buildCableSheathGeometry(cable, [
      wire("W1", [{ x: 0, y: 10 }, { x: 100, y: 10 }]),
      wire("W2", [{ x: 20, y: 20 }, { x: 90, y: 20 }]),
    ]);

    expect(geometry).toMatchObject({
      cableId: "C1",
      memberWireIds: ["W1", "W2"],
      routeIndex: 0,
      direction: { x: 1, y: 0 },
      normal: { x: 0, y: 1 },
      length: 70,
      crossMinimum: 10,
      crossMaximum: 20,
      outerCrossMinimum: 4,
      outerCrossMaximum: 26,
      centerline: [{ x: 20, y: 15 }, { x: 90, y: 15 }],
      polygon: [
        { x: 20, y: 4 },
        { x: 90, y: 4 },
        { x: 90, y: 26 },
        { x: 20, y: 26 },
      ],
      bounds: { x: 20, y: 4, width: 70, height: 22 },
    });
    expect(geometry?.memberSpans.W1?.covered).toEqual([{ x: 20, y: 10 }, { x: 90, y: 10 }]);
    expect(geometry?.memberSpans.W2?.covered).toEqual([{ x: 20, y: 20 }, { x: 90, y: 20 }]);
  });

  it("builds the same deterministic vertical envelope from reverse member directions", () => {
    const forward = buildCableSheathGeometry(cable, [
      wire("W1", [{ x: 10, y: 0 }, { x: 10, y: 100 }]),
      wire("W2", [{ x: 20, y: 20 }, { x: 20, y: 80 }]),
    ]);
    const reversed = buildCableSheathGeometry(cable, [
      wire("W1", [{ x: 10, y: 100 }, { x: 10, y: 0 }]),
      wire("W2", [{ x: 20, y: 80 }, { x: 20, y: 20 }]),
    ]);

    expect(forward?.polygon).toEqual([
      { x: 26, y: 20 },
      { x: 26, y: 80 },
      { x: 4, y: 80 },
      { x: 4, y: 20 },
    ]);
    expect(forward?.bounds).toEqual({ x: 4, y: 20, width: 22, height: 60 });
    expect(reversed?.polygon).toEqual(forward?.polygon);
    expect(reversed?.memberSpans.W1?.sourceDirection).toBe(-1);
    expect(reversed?.memberSpans.W2?.sourceDirection).toBe(-1);
  });

  it("chooses the longest aligned span and resolves a tie by route index", () => {
    const geometry = buildCableSheathGeometry(cable, [
      wire("W1", [{ x: 0, y: 0 }, { x: 40, y: 0 }, { x: 40, y: 40 }]),
      wire("W2", [{ x: 10, y: 10 }, { x: 50, y: 10 }, { x: 50, y: 50 }]),
    ]);

    expect(geometry).toMatchObject({ routeIndex: 0, length: 30, direction: { x: 1, y: 0 } });
  });

  it("does not guess a trunk for missing, disjoint, or incompatible member routes", () => {
    expect(buildCableSheathGeometry(cable, [wire("W1", [{ x: 0, y: 0 }, { x: 100, y: 0 }])])).toBeNull();
    expect(buildCableSheathGeometry(cable, [
      wire("W1", [{ x: 0, y: 0 }, { x: 30, y: 0 }]),
      wire("W2", [{ x: 40, y: 10 }, { x: 80, y: 10 }]),
    ])).toBeNull();
    expect(buildCableSheathGeometry(cable, [
      wire("W1", [{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 40 }]),
      wire("W2", [{ x: 0, y: 10 }, { x: 100, y: 10 }]),
    ])).toBeNull();
    expect(buildCableSheathGeometry(cable, [
      wire("W1", [{ x: 0, y: 0 }, { x: 100, y: 0 }]),
      wire("W2", [{ x: 20, y: -20 }, { x: 20, y: 20 }]),
    ])).toBeNull();
  });

  it("rejects a too-short overlap and accepts the exact minimum", () => {
    expect(buildCableSheathGeometry(cable, [
      wire("W1", [{ x: 0, y: 0 }, { x: 100, y: 0 }]),
      wire("W2", [{ x: 100 - cableSheathMinimumSpanLength + 0.001, y: 10 }, { x: 100, y: 10 }]),
    ])).toBeNull();

    expect(buildCableSheathGeometry(cable, [
      wire("W1", [{ x: 0, y: 0 }, { x: 100, y: 0 }]),
      wire("W2", [{ x: 100 - cableSheathMinimumSpanLength, y: 10 }, { x: 100, y: 10 }]),
    ])?.length).toBe(cableSheathMinimumSpanLength);
  });

  it("does not invent a layer for members placed on different drawing layers", () => {
    const first = wire("W1", [{ x: 0, y: 0 }, { x: 100, y: 0 }]);
    const second = { ...wire("W2", [{ x: 0, y: 10 }, { x: 100, y: 10 }]), layerId: "other" };
    expect(buildCableSheathGeometry(cable, [first, second])).toBeNull();
  });
});
