// @vitest-environment node
import { describe, expect, it } from "vitest";
import { applyEditorCommand, createWire } from "./commands";
import { createEmptyHarnessDesign, parseHarnessDesignDocument, type HarnessDesignDocument } from "./model";
import { detachedWireBendInsertionIndex } from "./detached-wire-editing";
import { createEditorHistory, executeEditorCommand, undoEditorCommand, redoEditorCommand } from "./history";
import { dimensionWirePoints, drawingDimensionScene, toggleDrawingDimensions } from "./drawing-dimensions";

function fixture(): HarnessDesignDocument {
  return { ...createEmptyHarnessDesign(), wires: [0, 1].map(index => ({
    ...createWire(`w${index}`, { connectorId: `free-${index}-a`, contactId: "free" }, { connectorId: `free-${index}-b`, contactId: "free" }, 385),
    drawingEndpoints: { from: { x: 0, y: index * 14 }, to: { x: 300, y: index * 14 } },
    drawingRoute: [{ x: 100, y: index * 14 }, { x: 200, y: index * 14 + 70 }],
  })) };
}

describe("independent wire polyline editing", () => {
  it("inserts in the clicked segment, moves one bend and removes it in single undoable steps", () => {
    const source = fixture(), before = structuredClone(source);
    const wire = source.wires[0]!, point = { x: 40, y: 12 };
    expect(detachedWireBendInsertionIndex(wire, point)).toBe(0);
    expect(detachedWireBendInsertionIndex(wire, { x: 150, y: 35 })).toBe(1);
    expect(detachedWireBendInsertionIndex(wire, { x: 270, y: 21 })).toBe(2);
    const inserted = executeEditorCommand(createEditorHistory(source), { type: "edit-detached-wire-bend", wireId: wire.id, index: 0, position: point, insert: true });
    expect(inserted.present.wires[0]!.drawingRoute).toEqual([point, ...wire.drawingRoute]);
    const moved = executeEditorCommand(inserted, { type: "edit-detached-wire-bend", wireId: wire.id, index: 0, position: { x: 50, y: 40 } });
    expect(moved.present.wires[0]!.drawingEndpoints).toEqual(wire.drawingEndpoints);
    expect(moved.present.wires[0]!.drawingRoute.slice(1)).toEqual(wire.drawingRoute);
    const removed = executeEditorCommand(moved, { type: "remove-detached-wire-bend", wireId: wire.id, index: 0 });
    expect(removed.present.wires[0]!.drawingRoute).toEqual(wire.drawingRoute);
    expect(undoEditorCommand(removed).present).toEqual(moved.present);
    expect(redoEditorCommand(undoEditorCommand(removed)).present).toEqual(removed.present);
    expect(source).toEqual(before);
    expect(removed.present.wires[0]!.lengthMm).toBe(385);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(removed.present))).wires[0]!.drawingRoute).toEqual(wire.drawingRoute);
  });

  it("rejects bad bend controls and locked layers without changing the source", () => {
    const source = fixture();
    expect(() => applyEditorCommand(source, { type: "edit-detached-wire-bend", wireId: "w0", index: 3, position: { x: 1, y: 2 } })).toThrow();
    expect(() => applyEditorCommand(source, { type: "edit-detached-wire-bend", wireId: "w0", index: 0, position: { x: NaN, y: 2 } })).toThrow();
    const locked = { ...source, views: { ...source.views, drawing: { ...source.views.drawing, layers: source.views.drawing.layers.map(layer => ({ ...layer, locked: true })) } } };
    expect(() => applyEditorCommand(locked, { type: "edit-detached-wire-bend", wireId: "w0", index: 0, position: { x: 1, y: 2 } })).toThrow(/заблокирован/);
  });
});

describe("independent drawing pair", () => {
  it("preserves original twist parameters, straightens and restores in one undo step", () => {
    const source = fixture();
    const original = { id: "original-pair", wireIds: ["w0", "w1"] as const, step: 73, amplitude: 9, variant: 2 as const };
    const paired = { ...source, diffPairs: [original] };
    const updated = executeEditorCommand(createEditorHistory(paired), { type: "set-detached-wire-pair", wireIds: ["w1", "w0"], action: "twist" });
    expect(updated.present.diffPairs).toEqual([original]);
    const straight = executeEditorCommand(updated, { type: "set-detached-wire-pair", wireIds: ["w0", "w1"], action: "straighten" });
    expect(straight.present.diffPairs).toEqual([]);
    expect(straight.present.wires).toEqual(source.wires);
    expect(undoEditorCommand(straight).present.diffPairs).toEqual([original]);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(updated.present))).diffPairs).toEqual([original]);
  });

  it("creates exactly one detached pair and rejects invalid or occupied selection", () => {
    const source = fixture();
    const paired = applyEditorCommand(source, { type: "set-detached-wire-pair", wireIds: ["w0", "w1"], action: "twist", groupId: "new", step: 60 });
    expect(paired.diffPairs).toEqual([{ id: "new", wireIds: ["w0", "w1"], step: 60, amplitude: 6, variant: 1 }]);
    expect(paired.wires.map(wire => wire.lengthMm)).toEqual([385, 385]);
    expect(() => applyEditorCommand(source, { type: "set-detached-wire-pair", wireIds: ["w0"], action: "twist" })).toThrow(/два/);
    const withThird = { ...paired, wires: [...paired.wires, { ...paired.wires[0]!, id: "third" }] };
    expect(() => applyEditorCommand(withThird, { type: "set-detached-wire-pair", wireIds: ["w0", "third"], action: "twist" })).toThrow(/другую/);
    const crossing = { ...source, wires: source.wires.map((wire, index) => index === 0 ? { ...wire, drawingRoute: [], drawingEndpoints: { from: { x: 0, y: 0 }, to: { x: 0, y: 300 } } } : wire) };
    expect(() => applyEditorCommand(crossing, { type: "set-detached-wire-pair", wireIds: ["w0", "w1"], action: "twist" })).toThrow(/направленных вдоль/);
    const short = { ...source, wires: source.wires.map((wire, index) => ({ ...wire, drawingRoute: [], drawingEndpoints: { from: { x: 0, y: index * 10 }, to: { x: 10, y: index * 10 } } })) };
    expect(() => applyEditorCommand(short, { type: "set-detached-wire-pair", wireIds: ["w0", "w1"], action: "twist" })).toThrow(/достаточной длины/);
  });
});

it("shows detached wire dimensions, rebinds total after insertion and keeps production length", () => {
  const source = fixture(), wire = source.wires[0]!;
  expect(dimensionWirePoints(source, wire)).toEqual([wire.drawingEndpoints!.from, ...wire.drawingRoute, wire.drawingEndpoints!.to]);
  const measured = applyEditorCommand(source, { type: "set-drawing-documents", documents: toggleDrawingDimensions(source) });
  expect(drawingDimensionScene(measured)).toHaveLength(2);
  const dimension = measured.drawingDocuments!.dimensions![0]!;
  const inserted = applyEditorCommand(measured, { type: "edit-detached-wire-bend", wireId: wire.id, index: 0, position: { x: 40, y: 15 }, insert: true });
  expect(inserted.drawingDocuments!.dimensions!.find(item => item.id === dimension.id)).toMatchObject({ pointCount: 5, to: 4, lengthMm: 385 });
  expect(inserted.wires[0]!.lengthMm).toBe(385);
  const edited = applyEditorCommand(inserted, { type: "set-drawing-documents", documents: { ...inserted.drawingDocuments!, dimensions: inserted.drawingDocuments!.dimensions!.map(item => ({ ...item, lengthMm: 999 })) } });
  expect(edited.wires[0]!.lengthMm).toBe(385);
  expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(edited))).wires[0]!.lengthMm).toBe(385);
});
