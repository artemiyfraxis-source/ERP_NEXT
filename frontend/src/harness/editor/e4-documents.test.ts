// @vitest-environment node
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { DrawingDocumentsPanel } from "./DrawingDocumentsPanel";
import { DrawingTableWindows } from "./DrawingTableWindows";
import { HarnessRelationsPanel } from "./HarnessRelationsPanel";
import { createEmptyHarnessDesign } from "./model";
import { buildHarnessSelectionIndex, resolveHarnessSelection } from "./harness-selection";
import { createWire } from "./commands";

const document = { ...createEmptyHarnessDesign(), drawingDocuments: {
  tables: [
    { id: "cut", kind: "cut" as const, position: { x: 0, y: 0 } },
    { id: "bom", kind: "bom" as const, position: { x: 0, y: 0 } },
    { id: "connections", kind: "connections" as const, position: { x: 0, y: 0 } },
  ], leaders: [], bomOrder: [],
} };

describe("documents by harness view", () => {
  it("shows only connections on the E4 field and retains all tables in drawing", () => {
    const props = { document, camera: { offsetX: 0, offsetY: 0, zoom: 1 }, quantity: 1, revision: 1, unsaved: false, selectedIds: [], onChange: vi.fn(), onCommand: vi.fn(), onReveal: vi.fn() };
    const e4 = renderToStaticMarkup(createElement(DrawingTableWindows, { ...props, view: "e4" }));
    expect(e4).toContain('aria-label="Таблица соединений на поле"');
    expect(e4).not.toContain('aria-label="Резка и разделка на поле"');
    expect(e4).not.toContain('aria-label="Спецификация на поле"');
    const drawing = renderToStaticMarkup(createElement(DrawingTableWindows, { ...props, view: "drawing" }));
    expect(drawing).toContain('aria-label="Резка и разделка на поле"');
    expect(drawing).toContain('aria-label="Спецификация на поле"');
  });

  it("does not offer cut controls or cut list in E4", () => {
    const controls = renderToStaticMarkup(createElement(DrawingDocumentsPanel, { document, quantity: 1, selectedId: null, selectedIds: [], availableKinds: ["connections"], onChange: vi.fn(), onCommand: vi.fn(), onReveal: vi.fn() }));
    expect(controls).toContain("Соединения +");
    expect(controls).not.toContain("Резка");
    const props = { document, projectId: "project", harnessId: "harness", quantity: 1, related: resolveHarnessSelection(buildHarnessSelectionIndex(document), []), wholeNet: false, onWholeNet: vi.fn(), onReveal: vi.fn(), onClear: vi.fn(), unsaved: false, hiddenCount: 0, revision: 1, onCommand: vi.fn() };
    const e4 = renderToStaticMarkup(createElement(HarnessRelationsPanel, props));
    expect(e4).not.toContain("Карта резки");
    expect(e4).not.toContain("Схема резки");
    const drawing = renderToStaticMarkup(createElement(HarnessRelationsPanel, { ...props, showCut: true }));
    expect(drawing).toContain("Карта резки");
  });

  it("renders the same connection values in the read-only route table", () => {
    const wire = createWire("wire1", { connectorId: "x", contactId: "c1" }, { connectorId: "y", contactId: "c2" }, 123, "NET");
    const markup = renderToStaticMarkup(createElement(DrawingDocumentsPanel, { document: { ...document, wires: [wire] }, mode: "connections", readOnly: true, quantity: 1, selectedId: null, selectedIds: [], onChange: vi.fn(), onCommand: vi.fn(), onReveal: vi.fn() }));
    expect(markup).toContain('aria-label="Таблица соединений"');
    expect(markup).toContain('readOnly="" value="NET"');
    expect(markup).toContain('value="123"');
    expect(markup).not.toContain("Резка +");
  });
});
