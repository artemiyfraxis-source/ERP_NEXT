// Ported from TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission (THIRD_PARTY_NOTICES.md).
// Original: src/Techmap.Client/src/manufacturing/route-drawing-copy.test.ts @ 0a456d2f7bd23288111e871058ebf55bf14426cd (branch codex/integration-m5-22).
import { expect, it } from "vitest";
import { physicalFixture } from "../editor/physical-topology-fixture";
// ERP changes: an isolated copy is made by createIsolatedRouteDrawingCopy (a source copy holds hidden IDs only); the
// legacy localStorage migration test is not ported; the source-copy and resolve cases are ERP's own.
import { createIndependentIsolatedDocument, createIsolatedRouteDrawingCopy, createRouteDrawingCopy, parseRouteDrawingCopy, resolveRouteDrawingCopy } from "./route-drawing-copy";
import { designToScene } from "../editor/HarnessDesignEditor";
import { dimensionRouteKey } from "../editor/drawing-dimensions";
import { applyEditorCommand } from "../editor/commands";

it("ERP: a source copy keeps only hidden IDs and is shown on the position's drawing", () => {
  const source = physicalFixture();
  const copy = createRouteDrawingCopy(["W2", "S0"]);
  expect(copy).toEqual({ hiddenObjectIds: ["W2", "S0"] });
  expect(JSON.stringify(copy)).not.toContain("connectors");
  expect(resolveRouteDrawingCopy(copy, source).wires).toEqual(source.wires);
  expect(parseRouteDrawingCopy(JSON.parse(JSON.stringify(copy)))).toEqual(copy);
  expect(() => parseRouteDrawingCopy({ hiddenObjectIds: ["W2", "W2"] })).toThrow();
  expect(() => parseRouteDrawingCopy({ hiddenObjectIds: [], extra: 1 })).toThrow();
  expect(() => parseRouteDrawingCopy({ document: {} })).toThrow();
  const isolated = createIsolatedRouteDrawingCopy(createIndependentIsolatedDocument(source, designToScene(source, "drawing"), ["W1"])!);
  expect(resolveRouteDrawingCopy(isolated, source).wires.map(wire => wire.id)).toEqual(["W1"]);
});

it("keeps complete pairs and rebinds dimensions through bends, free end movement and reopen", () => {
  const base = physicalFixture();
  const source = { ...base, diffPairs: [{ id: "pair", wireIds: ["W1", "W2"] as const, step: 40, amplitude: 8, variant: 2 as const }],
    wires: base.wires.map(w => ({ ...w, lengthMm: 385 })) };
  const before = structuredClone(source);
  const isolated = createIndependentIsolatedDocument(source, designToScene(source, "drawing"), ["W1", "W2"])!;
  expect(isolated.diffPairs).toEqual(source.diffPairs);
  expect(designToScene(isolated, "drawing").filter(o => o.kind === "dimension")).toHaveLength(2);
  expect(designToScene(isolated, "drawing").find(o => o.id === "W1")!.visibleWireStrokes!.length).toBeGreaterThan(2);
  const bent = applyEditorCommand(isolated, { type: "edit-detached-wire-bend", wireId: "W1", index: 0, position: { x: 300, y: 190 }, insert: true });
  const moved = applyEditorCommand(bent, { type: "set-wire-drawing-endpoint", wireId: "W1", end: "to", position: { x: 800, y: 40 } });
  const reopened = parseRouteDrawingCopy(JSON.parse(JSON.stringify(createIsolatedRouteDrawingCopy(moved)))).document!;
  expect(reopened.diffPairs).toEqual(source.diffPairs);
  expect(reopened.drawingDocuments!.dimensions![0]).toMatchObject({ pointCount: 3, to: 2, routeKey: dimensionRouteKey(reopened, reopened.wires[0]!), lengthMm: 385 });
  expect(designToScene(reopened, "drawing").filter(o => o.kind === "dimension")).toHaveLength(2);
  expect(reopened.wires[0]!.drawingEndpoints!.to).toEqual({ x: 800, y: 40 });
  expect(reopened.wires.map(w => w.lengthMm)).toEqual([385, 385]);
  expect(createIndependentIsolatedDocument(source, designToScene(source, "drawing"), ["W1"])!.diffPairs).toEqual([]);
  expect(source).toEqual(before);
});

it("creates a detached drawing model from selected material and drops physical routing", () => {
  const source = physicalFixture();
  const before = structuredClone(source);
  const scene = designToScene(source, "drawing");
  const isolated = createIndependentIsolatedDocument(source, scene, ["W1"]);
  expect(isolated).not.toBeNull();
  expect(isolated!.wires.map(wire => wire.id)).toEqual(["W1"]);
  expect(isolated!.connectors).toEqual([]);
  expect(isolated!.physicalTopology).toBeUndefined();
  expect(isolated!.junctions).toEqual([]);
  expect(isolated!.screens).toEqual([]);
  expect(isolated!.diffPairs).toEqual([]);
  expect(isolated!.wires[0]!.drawingRoute).toEqual([]);
  expect(isolated!.wires[0]!.from.connectorId).toBe("isolated:W1:from");
  expect(isolated!.wires[0]!.to.connectorId).toBe("isolated:W1:to");
  expect(isolated!.wires[0]!.drawingEndpoints).toBeDefined();
  expect(isolated!.wires[0]!.drawingEndStyles).toEqual({ from: "cut", to: "cut" });
  expect(isolated!.wires[0]!.stripProfiles).toEqual(source.wires[0]!.stripProfiles);
  expect(source).toEqual(before);
});

it("returns null when isolation receives only non-material scene objects", () => {
  const source = physicalFixture();
  const scene = designToScene(source, "drawing");
  expect(createIndependentIsolatedDocument(source, scene, ["S0"])).not.toBeNull();
  expect(createIndependentIsolatedDocument(source, scene, ["missing-object"])).toBeNull();
});

it("round-trips the isolated snapshot through route Save/reopen parsing", () => {
  const source = physicalFixture();
  const scene = designToScene(source, "drawing");
  const isolated = createIndependentIsolatedDocument(source, scene, ["W1"]);
  const saved = createIsolatedRouteDrawingCopy(isolated!, []);
  const reopened = parseRouteDrawingCopy(JSON.parse(JSON.stringify(saved)));
  expect(reopened.document!.physicalTopology).toBeUndefined();
  expect(reopened.document!.wires[0]!.drawingRoute).toEqual([]);
  expect(reopened.document!.wires[0]!.id).toBe("W1");
});

it("retains selected connector identity and assigns deterministic parallel lanes", () => {
  const source = physicalFixture();
  const scene = designToScene(source, "drawing");
  const connector = source.connectors[0]!;
  const wires = source.wires.slice(0, 2);
  const multi = { ...source, wires, physicalTopology: undefined };
  const multiScene = scene.filter(object => object.id === connector.id || (object.kind === "wire" && wires.some(w => w.id === object.id)));
  const isolated = createIndependentIsolatedDocument(multi, multiScene, [connector.id, ...wires.map(w => w.id)]);
  expect(isolated!.connectors[0]!.id).toBe(connector.id);
  expect(isolated!.wires.every(w => w.from.connectorId === connector.id)).toBe(true);
  expect(isolated!.connectors[0]).toMatchObject(connector);
  expect(isolated!.wires.every(w => w.drawingRoute.length === 0)).toBe(true);
  expect(isolated!.wires[0]!.drawingRoute).toEqual([]);
  const endpoints = isolated!.wires.map(w => w.drawingEndpoints!);
  expect(new Set(endpoints.map(e => e.to.x)).size).toBe(1);
  expect(new Set(endpoints.map(e => e.to.y)).size).toBe(2);
  expect(endpoints.every(e => e.from.y === e.to.y)).toBe(true);
  expect(designToScene(isolated!, "drawing").filter(o => o.kind === "wire").every(o => o.metadata?.routeMissing === "false")).toBe(true);
});


it("creates distinct horizontal lanes without connectors and inherits end treatments", () => {
  const source = physicalFixture(), before = structuredClone(source);
  const inherited = new Map([["W1", { from: "tin" as const, to: "sealed" as const }]]);
  const isolated = createIndependentIsolatedDocument(source, designToScene(source, "drawing"), ["W1", "W2"], inherited)!;
  const ends = isolated.wires.map(w => w.drawingEndpoints!);
  expect(new Set(ends.map(e => e.from.x)).size).toBe(1);
  expect(new Set(ends.map(e => e.to.x)).size).toBe(1);
  expect(new Set(ends.map(e => e.from.y)).size).toBe(2);
  expect(ends.every(e => e.from.y === e.to.y)).toBe(true);
  expect(isolated.wires[0]!.drawingEndStyles).toEqual(inherited.get("W1"));
  expect(source).toEqual(before);
  expect(parseRouteDrawingCopy(createIsolatedRouteDrawingCopy(isolated)).document!.wires).toEqual(isolated.wires);
});

it("preserves explicit end edits over inherited styles and both selected attachments", () => {
  const source = physicalFixture();
  const explicit = { ...source, wires: source.wires.map(w => ({ ...w, drawingEndStyles: { from: "copper" as const, to: "terminal" as const } })) };
  const scene = designToScene(explicit, "drawing");
  const isolated = createIndependentIsolatedDocument(explicit, scene, ["A", "B", "W1"], new Map([["W1", { from: "cut", to: "cut" }]]))!;
  expect(isolated.wires[0]!.from).toEqual(source.wires[0]!.from);
  expect(isolated.wires[0]!.to).toEqual(source.wires[0]!.to);
  expect(isolated.wires[0]!.drawingEndStyles).toEqual({ from: "copper", to: "terminal" });
  expect(isolated.wires[0]!.drawingEndpoints!.from).toEqual(scene.find(o => o.id === "W1")!.points![0]);
});
