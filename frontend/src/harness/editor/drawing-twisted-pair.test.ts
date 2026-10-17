// @vitest-environment node
import { expect, it } from "vitest";
import { designToScene } from "./HarnessDesignEditor";
import { freeTwistedPairPaths, physicalTwistedPairPath } from "./drawing-twisted-pair";
import { physicalFixture } from "./physical-topology-fixture";
import { physicalWireDisplay } from "./physical-wire-geometry";
import { drawEditorSceneObject } from "./CanvasViewport";
import type { EditorSceneObject } from "./editor-types";

const group = { id: "pair", wireIds: ["W1", "W2"] as const, step: 25, amplitude: 4, variant: 1 as const };

it("projects a physical E4 pair as alternating colored paths without changing stored routes", () => {
  const base = physicalFixture();
  const document = { ...base, diffPairs: [group] };
  const before = JSON.stringify(document);
  const center = [{ x: 0, y: 0 }, { x: 120, y: 0 }];
  const first = physicalTwistedPairPath(document, "S0", "W1", center)!;
  const second = physicalTwistedPairPath(document, "S0", "W2", center)!;
  expect(first).not.toBeNull();
  expect(second).not.toBeNull();
  expect(first.path[0]!.y).toBeCloseTo(first.path.at(-1)!.y);
  expect(second.path[0]!.y).toBeCloseTo(second.path.at(-1)!.y);
  expect(first.path.find(point => point.x === 12)!.y).toBeCloseTo(second.path[0]!.y);
  expect(second.path.find(point => point.x === 12)!.y).toBeCloseTo(first.path[0]!.y);
  expect(first.strokes.length).toBeGreaterThan(1);
  expect(second.strokes.length).toBeGreaterThan(1);
  const projected = designToScene(document, "drawing").filter(object => object.kind === "wire" && group.wireIds.includes(object.id as "W1" | "W2"));
  expect(projected).toHaveLength(2);
  expect(projected.every(wire => wire.visibleWireStrokes!.length > 2)).toBe(true);
  expect(projected.every(wire => wire.routeRadius === 24)).toBe(true);
  expect(projected.every(wire => wire.visibleWireStrokes!.some(stroke => stroke.radius === 0))).toBe(true);
  const ordinary = designToScene({ ...document, diffPairs: [] }, "drawing").find(object => object.id === "W1")!;
  expect(ordinary.visibleWireStrokes!.length).toBeLessThan(projected[0]!.visibleWireStrokes!.length);
  expect(JSON.stringify(document)).toBe(before);
});

it("scales the visible pitch with wire thickness while preserving the stored pair step", () => {
  const base = physicalFixture();
  const center = [{ x: 0, y: 0 }, { x: 300, y: 0 }];
  const thin = physicalTwistedPairPath({ ...base, diffPairs: [group] }, "S0", "W1", center)!;
  const thick = physicalTwistedPairPath({ ...base, diffPairs: [group], drawingDocuments: {
    ...base.drawingDocuments!, physicalScale: 5,
  } }, "S0", "W1", center)!;
  expect(thick.strokes.length).toBeLessThan(thin.strokes.length);
  expect(group.step).toBe(25);
});

it("rounds the lead after a twist without rounding away the twist itself", () => {
  const calls: string[] = [];
  const context = new Proxy({}, { get: (_target, key: string) => (..._args: unknown[]) => { calls.push(key); } }) as CanvasRenderingContext2D;
  const object: EditorSceneObject = {
    id: "W1", kind: "wire", layerId: "wires", label: "", x: 0, y: 0, width: 0, height: 0,
    color: "#1166aa", routeRadius: 24, paths: [[{ x: 0, y: 0 }, { x: 40, y: 0 }, { x: 40, y: 40 }]],
    visibleWireStrokes: [
      { points: [{ x: 0, y: 0 }, { x: 40, y: 0 }, { x: 40, y: 40 }], width: 3 },
      { points: [{ x: 40, y: 40 }, { x: 45, y: 42 }, { x: 50, y: 40 }], width: 3, radius: 0 },
    ],
  };
  drawEditorSceneObject(context, object, false, "drawing");
  expect(calls.filter(call => call === "arcTo")).toHaveLength(1);
});

it("keeps conductors hidden when their physical pipe hides wires", () => {
  const base = physicalFixture();
  const document = { ...base, diffPairs: [group], physicalTopology: {
    ...base.physicalTopology!, segments: base.physicalTopology!.segments.map(segment =>
      segment.id === "S0" ? { ...segment, showWires: false } : segment),
  } };
  const display = physicalWireDisplay(document, "W1", { x: 0, y: 0 }, { x: 600, y: 600 })!;
  expect(display.twisted).toBe(false);
  expect(display.selectionPaths.length).toBeGreaterThan(0);
});

it("twists free drawing routes on their common straight section and leaves the ends anchored", () => {
  const wires = [
    { id: "W1", points: [{ x: 0, y: 0 }, { x: 120, y: 0 }], width: 2.5 },
    { id: "W2", points: [{ x: 0, y: 10 }, { x: 120, y: 10 }], width: 2.5 },
  ];
  const result = freeTwistedPairPaths(group, wires);
  expect(result.size).toBe(2);
  expect(result.get("W1")!.path[0]).toEqual(wires[0]!.points[0]);
  expect(result.get("W2")!.path.at(-1)).toEqual(wires[1]!.points[1]);
  expect(result.get("W1")!.strokes.length).toBeGreaterThan(1);
  expect(freeTwistedPairPaths(group, [{ ...wires[0]!, points: [{ x: 0, y: 0 }, { x: 0, y: 120 }] }, wires[1]!]).size).toBe(0);
});

it("keeps both leads visible when the common straight section covers only part of a route", () => {
  const wires = [
    { id: "W1", points: [{ x: 0, y: 0 }, { x: 200, y: 0 }], width: 2.5 },
    { id: "W2", points: [{ x: 40, y: 10 }, { x: 160, y: 10 }], width: 2.5 },
  ];
  const first = freeTwistedPairPaths(group, wires).get("W1")!;
  expect(first.strokes[0]!.points[0]).toEqual(wires[0]!.points[0]);
  expect(first.strokes.at(-1)!.points.at(-1)).toEqual(wires[0]!.points.at(-1));
  expect(first.strokes[0]!.radius).toBeUndefined();
  expect(first.strokes.at(-1)!.radius).toBeUndefined();
});

it("keeps a visible twist after one polyline bend moves and preserves both end coordinates", () => {
  const wires = [
    { id: "W1", points: [{ x: 0, y: 0 }, { x: 60, y: 45 }, { x: 120, y: 0 }], width: 2.5 },
    { id: "W2", points: [{ x: 0, y: 14 }, { x: 60, y: 14 }, { x: 120, y: 14 }], width: 2.5 },
  ];
  const before = structuredClone(wires), result = freeTwistedPairPaths(group, wires, 1, true);
  expect(result.size).toBe(2);
  for (const wire of wires) {
    const display = result.get(wire.id)!;
    expect(display.path[0]!.x).toBeCloseTo(wire.points[0]!.x);
    expect(display.path[0]!.y).toBeCloseTo(wire.points[0]!.y);
    expect(display.path.at(-1)!.x).toBeCloseTo(wire.points.at(-1)!.x);
    expect(display.path.at(-1)!.y).toBeCloseTo(wire.points.at(-1)!.y);
    expect(display.strokes.length).toBeGreaterThan(2);
    expect(display.strokes.every(stroke => stroke.radius === 0)).toBe(true);
  }
  expect(wires).toEqual(before);
  const reversed = freeTwistedPairPaths(group, [wires[0]!, { ...wires[1]!, points: [...wires[1]!.points].reverse() }], 1, true);
  expect(reversed.get("W2")!.path[0]!.x).toBeCloseTo(120);
  expect(reversed.get("W2")!.path.at(-1)!.x).toBeCloseTo(0);
});
