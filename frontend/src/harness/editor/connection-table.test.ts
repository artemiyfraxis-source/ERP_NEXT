// @vitest-environment node
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { DrawingDocumentsPanel } from "./DrawingDocumentsPanel";
import { connectionTableColumnIds, getConnectionTableSettings, updateConnectionTableSettings } from "./connection-table-settings";
import { connectionTableEndLabel, connectionWireColor } from "./drawing-documents";
import { physicalFixture } from "./physical-topology-fixture";

describe("connection table", () => {
  it("uses Wn indexes and appends connector article to both endpoints", () => {
    const base = physicalFixture();
    const document = {
      ...base,
      connectors: base.connectors.map(connector => connector.id === "A" || connector.id === "B"
        ? { ...connector, partNumber: `ARTICLE-${connector.id}` }
        : connector),
    };
    expect(connectionTableEndLabel(document, document.wires[0]!.from)).toBe("A (ARTICLE-A):1");
    expect(connectionTableEndLabel(document, document.wires[0]!.to)).toBe("B (ARTICLE-B):1");
    const markup = renderToStaticMarkup(createElement(DrawingDocumentsPanel, {
      document, mode: "connections", quantity: 1, selectedId: null, selectedIds: [],
      onChange: vi.fn(), onCommand: vi.fn(), onReveal: vi.fn(),
    }));
    expect(markup).toContain("Индекс (Wn)");
    expect(markup).toContain(">W1    </td>");
    expect(markup).toContain("ARTICLE-A");
    expect(markup).toContain("Маркировка");
    expect(markup).not.toContain("Материал");
  });

  it("renders the stored wire color as a named, colored cell", () => {
    const base = physicalFixture();
    const document = {
      ...base,
      connectors: base.connectors.map(connector => connector.id === "A"
        ? { ...connector, contacts: connector.contacts.map(contact => contact.id === "A:contact:1" ? { ...contact, color: "желтый" } : contact) }
        : connector),
    };
    expect(connectionWireColor(document, document.wires[0]!)).toEqual({ label: "желтый", hex: "#FBC02D" });
    const markup = renderToStaticMarkup(createElement(DrawingDocumentsPanel, {
      document, mode: "connections", quantity: 1, selectedId: null, selectedIds: [],
      onChange: vi.fn(), onCommand: vi.fn(), onReveal: vi.fn(),
    }));
    expect(markup).toContain("background-color:#FBC02D");
    expect(markup).toContain("желтый </td>");
  });

  it("marks a scheme color that is absent from the wire catalog", () => {
    const base = physicalFixture();
    const document = {
      ...base,
      connectors: base.connectors.map(connector => connector.id === "A"
        ? { ...connector, contacts: connector.contacts.map(contact => contact.id === "A:contact:1" ? { ...contact, color: "мятный" } : contact) }
        : connector),
    };
    const markup = renderToStaticMarkup(createElement(DrawingDocumentsPanel, {
      document, mode: "connections", quantity: 1, selectedId: null, selectedIds: [],
      onChange: vi.fn(), onCommand: vi.fn(), onReveal: vi.fn(), wireOptions: [],
    }));
    expect(markup).toContain("мятный (в базе не найден)");
  });

  it("keeps column visibility and order as a shared preference", () => {
    const original = getConnectionTableSettings();
    try {
      updateConnectionTableSettings({ columns: [
        { id: "route", visible: true }, { id: "index", visible: false },
      ] });
      const next = getConnectionTableSettings();
      expect(next.columns.map(column => column.id)).toEqual(["route", "index", ...connectionTableColumnIds.filter(id => id !== "route" && id !== "index")]);
      expect(next.columns.find(column => column.id === "index")?.visible).toBe(false);
    } finally {
      updateConnectionTableSettings(original);
    }
  });
});
