// @vitest-environment node
import { describe, expect, it } from "vitest";
import type { WireStripProfileBinding, WireStripStep } from "./model";
import {
  buildWireStripProfileGeometry,
  buildWireStripStepGeometry,
  wireStripProfileMaximumDiameter,
  wireStripProfileMaximumLength,
  wireStripProfileMinimumSegmentLength,
} from "./wire-strip-profile-geometry";

const profile: WireStripProfileBinding = {
  sourceId: "technology-coax-terminations",
  snapshotId: "38d9aa91-b8d4-45d8-8e39-da14ee4effad",
  snapshotSha256: "a".repeat(64),
  recordId: "b".repeat(64),
  entityType: "coax-termination",
  sourceKey: "BNC|RG58|2|3|5",
  displayName: "BNC / RG58",
  layers: [
    { index: 1, diameterMm: 1, stripLengthMm: 2 },
    { index: 2, diameterMm: 3, stripLengthMm: 3 },
    { index: 3, diameterMm: 5, stripLengthMm: 5 },
  ],
};

describe("wire strip profile geometry", () => {
  it("normalizes horizontal L and D independently from the from end", () => {
    const points = [{ x: 10, y: 20 }, { x: 100, y: 20 }];
    const geometry = buildWireStripProfileGeometry(points, "from", profile);

    expect(geometry).toMatchObject({
      end: "from",
      origin: { x: 10, y: 20 },
      direction: { x: 1, y: 0 },
      normal: { x: -0, y: 1 },
    });
    expect(geometry?.totalLength).toBeCloseTo(36);
    expect(geometry?.primitives.map((primitive) => primitive.centerline)).toEqual([
      [{ x: 10, y: 20 }, { x: 24.4, y: 20 }],
      [{ x: 24.4, y: 20 }, { x: 31.6, y: 20 }],
      [{ x: 31.6, y: 20 }, { x: 46, y: 20 }],
    ]);
    expect(geometry?.primitives[1]?.polygon).toEqual([
      { x: 24.4, y: 24.2 }, { x: 31.6, y: 24.2 }, { x: 31.6, y: 15.8 }, { x: 24.4, y: 15.8 },
    ]);
    expect(geometry?.primitives[2]?.polygon[0].y! - geometry?.primitives[2]?.polygon[3].y!).toBe(wireStripProfileMaximumDiameter);
    expect(points).toEqual([{ x: 10, y: 20 }, { x: 100, y: 20 }]);
  });

  it("uses the first non-zero segment and supports a vertical from end", () => {
    const geometry = buildWireStripProfileGeometry(
      [{ x: 30, y: 90 }, { x: 30, y: 90 }, { x: 30, y: 20 }, { x: 80, y: 20 }],
      "from",
      profile,
    );

    expect(geometry).toMatchObject({ direction: { x: 0, y: -1 }, normal: { x: 1, y: 0 } });
    expect(geometry?.primitives[0]?.polygon).toEqual([
      { x: 31.4, y: 90 }, { x: 31.4, y: 78.8 }, { x: 28.6, y: 78.8 }, { x: 28.6, y: 90 },
    ]);
  });

  it("reverses orientation at the to end without reversing layer order", () => {
    const geometry = buildWireStripProfileGeometry(
      [{ x: 0, y: 10 }, { x: 100, y: 10 }],
      "to",
      profile,
    );

    expect(geometry).toMatchObject({
      end: "to",
      origin: { x: 100, y: 10 },
      direction: { x: -1, y: 0 },
      normal: { x: -0, y: -1 },
    });
    expect(geometry?.primitives.map((primitive) => primitive.centerline)).toEqual([
      [{ x: 100, y: 10 }, { x: 84, y: 10 }],
      [{ x: 84, y: 10 }, { x: 76, y: 10 }],
      [{ x: 76, y: 10 }, { x: 60, y: 10 }],
    ]);
  });

  it("keeps profiles at both ends of one segment from overlapping", () => {
    const points = [{ x: 0, y: 0 }, { x: 100, y: 0 }];
    const from = buildWireStripProfileGeometry(points, "from", profile)!;
    const to = buildWireStripProfileGeometry(points, "to", profile)!;
    const fromInnerEdge = from.primitives.at(-1)!.centerline[1].x;
    const toInnerEdge = to.primitives.at(-1)!.centerline[1].x;

    expect(from.totalLength + to.totalLength).toBeLessThanOrEqual(100 * 0.8);
    expect(fromInnerEdge).toBeLessThan(toInnerEdge);
    expect([fromInnerEdge, toInnerEdge]).toEqual([40, 60]);
  });

  it("uses only the endpoint segment of a bent route", () => {
    const geometry = buildWireStripProfileGeometry(
      [{ x: 0, y: 0 }, { x: 80, y: 0 }, { x: 80, y: 100 }],
      "from",
      profile,
    );

    expect(geometry?.direction).toEqual({ x: 1, y: 0 });
    expect(geometry?.primitives.at(-1)?.centerline[1]).toEqual({ x: 32, y: 0 });
  });

  it("preserves gapped layer indices while deriving adjacent axial steps", () => {
    const gapped = { ...profile, layers: [
      { index: 1, diameterMm: 1, stripLengthMm: 2 },
      { index: 3, diameterMm: 4, stripLengthMm: 6 },
      { index: 7, diameterMm: 8, stripLengthMm: 9 },
    ] };
    const geometry = buildWireStripProfileGeometry(
      [{ x: 0, y: 0 }, { x: 20, y: 0 }],
      "from",
      gapped,
    );

    expect(geometry?.primitives.map((primitive) => ({
      index: primitive.layerIndex,
      step: primitive.stepLengthMm,
      line: primitive.centerline,
    }))).toEqual([
      { index: 1, step: 2, line: [{ x: 0, y: 0 }, { x: 1.777777778, y: 0 }] },
      { index: 3, step: 4, line: [{ x: 1.777777778, y: 0 }, { x: 5.333333333, y: 0 }] },
      { index: 7, step: 3, line: [{ x: 5.333333333, y: 0 }, { x: 8, y: 0 }] },
    ]);
  });

  it.each([
    ["empty route", []],
    ["one point", [{ x: 0, y: 0 }]],
    ["coincident route", [{ x: 0, y: 0 }, { x: 0, y: 0 }, { x: 0, y: 0 }]],
    ["non-finite route", [{ x: 0, y: 0 }, { x: Number.NaN, y: 10 }]],
  ])("returns null for %s", (_name, points) => {
    expect(buildWireStripProfileGeometry(points, "from", profile)).toBeNull();
  });

  it("fits a physically long profile into its endpoint segment", () => {
    const longProfile = { ...profile, layers: profile.layers.map((layer) => ({
      ...layer, stripLengthMm: layer.stripLengthMm * 1_000,
    })) };
    const geometry = buildWireStripProfileGeometry([{ x: 0, y: 0 }, { x: 40, y: 0 }], "from", longProfile);

    expect(geometry?.totalLength).toBe(16);
    expect(geometry?.primitives.at(-1)?.cumulativeLengthMm).toBe(5_000);
    expect(geometry?.primitives.at(-1)?.centerline[1]).toEqual({ x: 16, y: 0 });
  });

  it("caps a long endpoint segment to a readable visual length", () => {
    const geometry = buildWireStripProfileGeometry([{ x: 0, y: 0 }, { x: 1_000, y: 0 }], "from", profile);
    expect(geometry?.totalLength).toBe(wireStripProfileMaximumLength);
  });

  it("returns null below the explicit minimum usable endpoint segment length", () => {
    expect(buildWireStripProfileGeometry(
      [{ x: 0, y: 0 }, { x: wireStripProfileMinimumSegmentLength - 0.001, y: 0 }], "from", profile,
    )).toBeNull();
    expect(buildWireStripProfileGeometry(
      [{ x: 0, y: 0 }, { x: wireStripProfileMinimumSegmentLength, y: 0 }], "from", profile,
    )).not.toBeNull();
  });

  it("returns null instead of throwing for an invalid profile", () => {
    expect(buildWireStripProfileGeometry([{ x: 0, y: 0 }, { x: 100, y: 0 }], "from", {
      ...profile, layers: [{ index: 1, diameterMm: 0, stripLengthMm: 2 }],
    })).toBeNull();
  });

  it("accepts precomputed steps and rejects malformed step sequences gracefully", () => {
    const route = [{ x: 0, y: 0 }, { x: 100, y: 0 }];
    const steps: readonly WireStripStep[] = [
      { index: 1, diameterMm: 1, cumulativeLengthMm: 2, stepLengthMm: 2 },
      { index: 3, diameterMm: 3, cumulativeLengthMm: 5, stepLengthMm: 3 },
    ];
    expect(buildWireStripStepGeometry(route, "from", steps)?.primitives).toHaveLength(2);
    expect(buildWireStripStepGeometry(route, "from", [])).toBeNull();
    expect(buildWireStripStepGeometry(route, "from", [
      steps[0]!, { ...steps[1]!, cumulativeLengthMm: 1 },
    ])).toBeNull();
  });

  it("does not mutate or alias its inputs", () => {
    const points = [{ x: 0, y: 0 }, { x: 100, y: 0 }];
    const original = structuredClone(points);
    const geometry = buildWireStripProfileGeometry(points, "from", profile);

    expect(points).toEqual(original);
    expect(geometry?.origin).not.toBe(points[0]);
    expect(Object.isFrozen(geometry)).toBe(true);
    expect(Object.isFrozen(geometry?.primitives)).toBe(true);
    expect(Object.isFrozen(geometry?.primitives[0]?.polygon)).toBe(true);
  });
});
