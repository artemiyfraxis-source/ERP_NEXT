// @vitest-environment node
import { describe, expect, it } from "vitest";
import { applyEditorCommand, createConnector, createWire } from "./commands";
import { createEmptyHarnessDesign, parseHarnessDesignDocument, wireEndpointE4Anchor, type HarnessDesignDocument } from "./model";

function contactsLeft(connector: ReturnType<typeof createConnector>) {
  return { ...connector, schematic: { ...connector.schematic, orientation: "contacts-left" as const } };
}

describe("connector contact wire colors", () => {
  it("stores an optional secondary color and clears it back to a one-color wire", () => {
    let document = applyEditorCommand(createEmptyHarnessDesign(), {
      type: "add-connector",
      connector: createConnector("x1", "X1", 1, { x: 0, y: 0 }),
    });
    document = applyEditorCommand(document, {
      type: "update-contact",
      connectorId: "x1",
      contactId: "x1:contact:1",
      wire: "UL1061 28 AWG",
      color: "красный",
      secondaryColor: "чёрный",
    });
    expect(document.connectors[0]?.contacts[0]).toMatchObject({
      wire: "UL1061 28 AWG", color: "красный", secondaryColor: "чёрный",
    });

    document = applyEditorCommand(document, {
      type: "update-contact", connectorId: "x1", contactId: "x1:contact:1", secondaryColor: "",
    });
    expect(document.connectors[0]?.contacts[0]?.secondaryColor).toBe("");

    document = applyEditorCommand(document, {
      type: "update-contact", connectorId: "x1", contactId: "x1:contact:1", color: "#12abef",
    });
    document = applyEditorCommand(document, {
      type: "update-contact", connectorId: "x1", contactId: "x1:contact:1", color: "красный",
    });
    expect(document.customWireColors).toEqual(["#12ABEF"]);
  });

  it("defaults a missing legacy secondary color to an empty value", () => {
    const connector = createConnector("x1", "X1", 1, { x: 0, y: 0 });
    const legacyContact = { ...connector.contacts[0] } as Record<string, unknown>;
    delete legacyContact.secondaryColor;
    const parsed = parseHarnessDesignDocument({
      ...createEmptyHarnessDesign(),
      connectors: [{ ...connector, contacts: [legacyContact] }],
    });
    expect(parsed.connectors[0]?.contacts[0]?.secondaryColor).toBe("");
    expect(parsed.customWireColors).toEqual([]);
  });

  it("restores custom colors from old contact values and validates the persisted list", () => {
    const connector = createConnector("x1", "X1", 1, { x: 0, y: 0 });
    const parsed = parseHarnessDesignDocument({
      ...createEmptyHarnessDesign(),
      customWireColors: undefined,
      connectors: [{ ...connector, contacts: [{ ...connector.contacts[0]!, color: "#12abef" }] }],
    });
    expect(parsed.customWireColors).toEqual(["#12ABEF"]);
    expect(() => parseHarnessDesignDocument({ ...createEmptyHarnessDesign(), customWireColors: [12] })).toThrow(/цвета/);
  });

  it("synchronizes a direct wire with the last edited endpoint color", () => {
    const x1 = createConnector("x1", "X1", 1, { x: 0, y: 0 });
    const x2 = contactsLeft(createConnector("x2", "X2", 1, { x: 600, y: 0 }));
    let document: HarnessDesignDocument = {
      ...createEmptyHarnessDesign(), connectors: [x1, x2],
      wires: [createWire("w1", { connectorId: "x1", contactId: x1.contacts[0]!.id },
        { connectorId: "x2", contactId: x2.contacts[0]!.id })],
    };
    document = applyEditorCommand(document, {
      type: "update-contact", connectorId: "x1", contactId: x1.contacts[0]!.id, color: "красный",
    });
    expect(document.wires[0]).toMatchObject({
      color: "#D32F2F", colorSource: { connectorId: "x1", contactId: x1.contacts[0]!.id },
    });
    expect(document.connectors[1]?.contacts[0]).toMatchObject({ color: "красный", colorMode: "auto" });
    document = applyEditorCommand(document, {
      type: "update-contact", connectorId: "x2", contactId: x2.contacts[0]!.id, color: "синий",
    });
    expect(document.wires[0]).toMatchObject({
      color: "#1976D2", colorSource: { connectorId: "x2", contactId: x2.contacts[0]!.id },
    });
    expect(document.connectors[0]?.contacts[0]).toMatchObject({ color: "красный", colorMode: "manual" });
    expect(document.connectors[1]?.contacts[0]).toMatchObject({ color: "синий", colorMode: "manual" });
  });

  it("preserves a manual opposite color and can reset that cell to the direct-wire automatic value", () => {
    const x1 = createConnector("x1", "X1", 1, { x: 0, y: 0 });
    const x2 = contactsLeft(createConnector("x2", "X2", 1, { x: 600, y: 0 }));
    let document: HarnessDesignDocument = {
      ...createEmptyHarnessDesign(), connectors: [x1, x2],
      wires: [createWire("w1", { connectorId: "x1", contactId: x1.contacts[0]!.id },
        { connectorId: "x2", contactId: x2.contacts[0]!.id })],
    };
    document = applyEditorCommand(document, {
      type: "update-contact", connectorId: "x2", contactId: x2.contacts[0]!.id, color: "синий",
    });
    document = applyEditorCommand(document, {
      type: "update-contact", connectorId: "x1", contactId: x1.contacts[0]!.id,
      color: "красный", secondaryColor: "белый",
    });
    expect(document.connectors[1]?.contacts[0]).toMatchObject({ color: "синий", colorMode: "manual" });
    expect(document.wires[0]).toMatchObject({ color: "#D32F2F" });

    document = applyEditorCommand(document, {
      type: "reset-contact-color-auto", connectorId: "x2", contactId: x2.contacts[0]!.id,
    });
    expect(document.connectors[1]?.contacts[0]).toMatchObject({
      color: "красный", secondaryColor: "белый", colorMode: "auto",
    });
    expect(document.wires[0]).toMatchObject({
      color: "#D32F2F", colorSource: { connectorId: "x1", contactId: x1.contacts[0]!.id },
    });

    document = applyEditorCommand(document, {
      type: "update-contact", connectorId: "x1", contactId: x1.contacts[0]!.id, color: "оранжевый",
    });
    expect(document.connectors[1]?.contacts[0]).toMatchObject({
      color: "оранжевый", secondaryColor: "белый", colorMode: "auto",
    });
  });

  it("starts a new direct wire from the chosen contact color and fills only an automatic opposite cell", () => {
    const x1 = createConnector("x1", "X1", 1, { x: 0, y: 0 });
    const x2 = contactsLeft(createConnector("x2", "X2", 1, { x: 600, y: 0 }));
    let document: HarnessDesignDocument = { ...createEmptyHarnessDesign(), connectors: [x1, x2] };
    document = applyEditorCommand(document, {
      type: "update-contact", connectorId: "x1", contactId: x1.contacts[0]!.id, color: "фиолетовый",
    });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w1", { connectorId: "x1", contactId: x1.contacts[0]!.id },
        { connectorId: "x2", contactId: x2.contacts[0]!.id }, 100, "", "#7B1FA2", 0, 0, 1,
        { connectorId: "x1", contactId: x1.contacts[0]!.id }),
    });
    expect(document.connectors[1]?.contacts[0]).toMatchObject({ color: "фиолетовый", colorMode: "auto" });
    expect(document.wires[0]?.color).toBe("#7B1FA2");
  });

  it("keeps branch and manually recolored direct wire colors independent", () => {
    const x1 = createConnector("x1", "X1", 2, { x: 0, y: 0 });
    const x2 = contactsLeft(createConnector("x2", "X2", 1, { x: 600, y: 0 }));
    const baseDocument: HarnessDesignDocument = { ...createEmptyHarnessDesign(), connectors: [x1, x2] };
    const branchStart = wireEndpointE4Anchor(baseDocument, { connectorId: "x1", contactId: x1.contacts[1]!.id })!;
    let document: HarnessDesignDocument = {
      ...baseDocument,
      wires: [
        createWire("direct", { connectorId: "x1", contactId: x1.contacts[0]!.id },
          { connectorId: "x2", contactId: x2.contacts[0]!.id }),
        { ...createWire("branch", { connectorId: "x1", contactId: x1.contacts[1]!.id },
          { connectorId: "", contactId: "", junctionId: "j1" }, 100, "", "#00FF00"),
          e4Route: [branchStart.position, { x: 180, y: branchStart.position.y }, { x: 180, y: 220 }, { x: 300, y: 220 }] },
      ],
      junctions: [{ id: "j1", position: { x: 300, y: 220 }, wireIds: ["branch"] }],
    };
    document = applyEditorCommand(document, { type: "update-wire", wireId: "direct", color: "#ABCDEF" });
    document = applyEditorCommand(document, {
      type: "update-contact", connectorId: "x1", contactId: x1.contacts[0]!.id, color: "оранжевый",
    });
    document = applyEditorCommand(document, {
      type: "update-contact", connectorId: "x1", contactId: x1.contacts[1]!.id, color: "синий",
    });
    expect(document.wires.find((wire) => wire.id === "direct")).toMatchObject({ color: "#ABCDEF", colorSource: null });
    expect(document.wires.find((wire) => wire.id === "branch")?.color).toBe("#00FF00");
    expect(document.connectors.find((connector) => connector.id === "x2")?.contacts[0]?.color).toBe("");
  });

  it("persists the direct wire color source and repairs a stale legacy source", () => {
    const x1 = createConnector("x1", "X1", 1, { x: 0, y: 0 });
    const x2 = contactsLeft(createConnector("x2", "X2", 1, { x: 600, y: 0 }));
    const wire = {
      ...createWire("w1", { connectorId: "x1", contactId: x1.contacts[0]!.id },
        { connectorId: "x2", contactId: x2.contacts[0]!.id }),
      colorSource: { connectorId: "x1", contactId: x1.contacts[0]!.id },
    };
    const { e4Route: _route, ...wireWithoutRoute } = wire;
    const parsed = parseHarnessDesignDocument({ ...createEmptyHarnessDesign(), connectors: [x1, x2], wires: [wireWithoutRoute] });
    expect(parsed.wires[0]?.colorSource).toEqual(wire.colorSource);
    const repaired = parseHarnessDesignDocument({
      ...createEmptyHarnessDesign(), connectors: [x1, x2],
      wires: [{ ...wireWithoutRoute, colorSource: { connectorId: "missing", contactId: "missing" } }],
    });
    expect(repaired.wires[0]?.colorSource).toEqual({ connectorId: "x1", contactId: x1.contacts[0]!.id });
  });
});
