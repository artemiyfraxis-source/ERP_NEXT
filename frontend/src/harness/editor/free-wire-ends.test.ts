// @vitest-environment node
import { describe, expect, it } from "vitest";
import { applyEditorCommand, createConnector, createWire } from "./commands";
import { createEditorHistory, executeEditorCommand, undoEditorCommand } from "./history";
import { createEmptyHarnessDesign, parseHarnessDesignDocument, type HarnessDesignDocument } from "./model";

function base(): { document: HarnessDesignDocument; connectorId: string; wireId: string } {
  const connector = createConnector("X", "X1", 1, { x: 0, y: 0 });
  const wire = createWire("W", { connectorId: connector.id, contactId: connector.contacts[0]!.id }, { connectorId: "isolated:W:to", contactId: "free" });
  const document = { ...createEmptyHarnessDesign(), connectors: [connector], wires: [{ ...wire, drawingEndpoints: { from: { x: 0, y: 0 }, to: { x: 100, y: 25 } }, drawingEndStyles: { from: "cut" as const, to: "cut" as const } }] };
  return { document, connectorId: connector.id, wireId: wire.id };
}

describe("free drawing wire ends", () => {
  it("rejects style and endpoint edits on attached ends", () => {
    const { document, wireId } = base();
    expect(() => applyEditorCommand(document, { type: "set-wire-drawing-end-style", wireId, end: "from", style: "tin" })).toThrow(/свободный конец/);
    expect(() => applyEditorCommand(document, { type: "set-wire-drawing-endpoint", wireId, end: "from", position: { x: 20, y: 10 } })).toThrow(/свободный конец/);
  });

  it("rejects a locked bulk style update atomically", () => {
    const { document, wireId } = base();
    const locked = { ...document, views: { ...document.views, drawing: { ...document.views.drawing, layers: document.views.drawing.layers.map(layer => layer.id === "wires" ? { ...layer, locked: true } : layer) } } };
    expect(() => applyEditorCommand(locked, { type: "set-wire-drawing-end-styles", wireIds: [wireId], end: "to", style: "tin" })).toThrow(/заблокирован/);
    expect(locked.wires[0]!.drawingEndStyles?.to).toBe("cut");
  });

  it("bulk-skips attached ends in a mixed selection", () => {
    const { document, wireId } = base();
    const changed = applyEditorCommand(document, { type: "set-wire-drawing-end-styles", wireIds: [wireId], end: "from", style: "tin" });
    expect(changed.wires[0]!.drawingEndStyles?.from).toBe("cut");
    const freeChanged = applyEditorCommand(changed, { type: "set-wire-drawing-end-styles", wireIds: [wireId], end: "to", style: "sealed" });
    expect(freeChanged.wires[0]!.drawingEndStyles?.to).toBe("sealed");
  });

  it("applies group styles and boundary as one undo step each", () => {
    const { document } = base();
    const original = { ...document, wires: [document.wires[0]!, { ...document.wires[0]!, id: "W2", drawingEndpoints: { from: {x:0,y:40}, to:{x:100,y:40} } }] };
    const history = executeEditorCommand(createEditorHistory(original), { type: "set-wire-drawing-end-styles", wireIds: ["W", "W2"], end: "to", style: "sealed" });
    expect(history.past).toHaveLength(1);
    expect(history.present.wires.map(w => w.drawingEndStyles?.to)).toEqual(["sealed", "sealed"]);
    expect(undoEditorCommand(history).present).toBe(original);
    const boundary = executeEditorCommand(history, { type: "set-wire-drawing-endpoints-x", wireIds: ["W", "W2"], end: "to", x: 320 });
    expect(boundary.present.wires.map(w => w.drawingEndpoints!.to)).toEqual([{x:320,y:25},{x:320,y:40}]);
    expect(undoEditorCommand(boundary).present).toBe(history.present);
  });

  it("moves only an attached saved drawing endpoint with its connector", () => {
    const { document, connectorId } = base();
    const moved = applyEditorCommand(document, { type: "move-connector", connectorId, view: "drawing", position: { x: 30, y: 40 } });
    expect(moved.wires[0]!.drawingEndpoints).toEqual({ from: { x: 30, y: 40 }, to: { x: 100, y: 25 } });
  });

});
