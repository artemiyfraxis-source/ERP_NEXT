// @vitest-environment node
import { describe, expect, it } from "vitest";
import { formatWireSection, wireDatabaseOption, filterWireOptions, wireMarkChoices, wireSectionChoices, isSelectableWirePayload } from "./wire-database";
import { createBuiltInConnectorInstance } from "./connector-series-demo";
import { createEmptyHarnessDesign, parseHarnessDesignDocument, connectorContactPosition, createOrthogonalE4Route, wireEndpointE4Anchor } from "./model";
import { createWire } from "./commands";
import { createEditorHistory, executeEditorCommand, undoEditorCommand } from "./history";
import { designToScene } from "./HarnessDesignEditor";
import { getE4ConnectorLayout } from "./CanvasViewport";

describe("wire database", () => {
  it.each(["1C", "1С", " 1 с "])("shows only the core section for %s", Core => {
    expect(formatWireSection({ Core, "Сечение C": "0,35" })).toBe("0,35");
  });
  it("combines core and pair sections without adding assumed units", () => {
    expect(formatWireSection({ Core: "3C", "Сечение C": 0.5, Pair: "2P", "Сечение P": "0,22" })).toBe("3Cx0,5 | 2Px0,22");
    expect(formatWireSection({ Core: "1C", "Сечение C": "30", Pair: "1E", "Сечение P": "28" })).toBe("1Cx30 | 1Ex28");
    expect(formatWireSection({ Core: "2С", "Сечение C": "24AWG" })).toBe("2Сx24AWG");
    expect(formatWireSection({ Core: "0C", Pair: "2P", "Сечение P": 0.12 })).toBe("2Px0,12");
  expect(formatWireSection({ Core: "3C", "Сечение C": "0,5", Pair: "0P", "Сечение P": "0" })).toBe("3Cx0,5");
  expect(isSelectableWirePayload({ Марка: "Монтажный провод" })).toBe(false);
  expect(isSelectableWirePayload({ Марка: "UL1007", Core: "1C", "Сечение C": 30 })).toBe(true);
  });
  it("keeps variants searchable by all imported characteristics", () => {
    const red = wireDatabaseOption({ recordId: "red", sourceLocation: null, entityType: "wire", sourceKey: "key1", payload: {
      Марка: "TEST", Core: "1C", "Сечение C": 0.35, Цвет: "красный", Артикул: "0007",
    } });
    const blue = wireDatabaseOption({ ...{ recordId: "blue", sourceLocation: null, entityType: "wire", sourceKey: "key2" }, payload: {
      Марка: "TEST", Core: "3C", "Сечение C": 0.5, Цвет: "синий",
    } });
    expect(red.mark).toBe("TEST");
    expect(red.section).toBe("0,35");
    expect(red.detail).toContain("Артикул: 0007");
    expect(red.detail).toContain("монтажный многожильный провод");
    expect(red.color).toBe("красный");
    expect(filterWireOptions([red, blue], "TEST 0.35 красный")).toEqual([red]);
  });
  it("filters marks and complex sections in both directions using the full catalog", () => {
    const options = [
      { id: "a", mark: "МГТФ", section: "0,35", label: "МГТФ · 0,35", detail: "" },
      { id: "b", mark: "МГТФ", section: "0,5", label: "МГТФ · 0,5", detail: "" },
      { id: "c", mark: "НВ-4", section: "0,35", label: "НВ-4 · 0,35", detail: "" },
      { id: "d", mark: "UL2725", section: "3Cx0,5 | 2Px0,22", label: "UL2725", detail: "" },
      { id: "e", mark: "UL2725", section: "3Cx0,5 | 1Px0,22", label: "UL2725", detail: "" },
    ];
    expect(filterWireOptions(options, "", "0.35").map(option => option.mark)).toEqual(["МГТФ", "НВ-4"]);
    expect(wireSectionChoices(options, "МГТФ")).toEqual(["0,35", "0,5"]);
    expect(wireMarkChoices(options, "")).toEqual(["МГТФ", "НВ-4", "UL2725"]);
    expect(wireMarkChoices(options, "", "0,35")).toEqual(["МГТФ", "НВ-4"]);
    expect(wireSectionChoices(options, "UL2725")).toEqual(["3Cx0,5 | 2Px0,22", "3Cx0,5 | 1Px0,22"]);
    expect(filterWireOptions(options, "", "3Cx0.5 | 2Px0.22").map(option => option.mark)).toEqual(["UL2725"]);
    expect(wireSectionChoices(options, "МГТФ", "устаревшее")).toContain("устаревшее");
  });
  it("pins a selected E4 variant to its published record and carries it to a new wire", () => {
    const option = wireDatabaseOption({ recordId: "b".repeat(64), sourceLocation: null, entityType: "wire", sourceKey: "МГТФ #1",
      payload: { Марка: "МГТФ", "Сечение C": "0,35", Core: "1C" } },
    { snapshotId: "11111111-1111-4111-8111-111111111111", snapshotSha256: "a".repeat(64) });
    const first = createBuiltInConnectorInstance("catalog-connector-free", {
      id: "x1", designation: "X1", e4Position: { x: 0, y: 0 }, freeContactCount: 1,
    });
    const second = createBuiltInConnectorInstance("catalog-connector-free", {
      id: "x2", designation: "X2", e4Position: { x: 500, y: 0 }, freeContactCount: 1,
    });
    const base = { ...createEmptyHarnessDesign(), connectors: [first, second] };
    const selected = executeEditorCommand(createEditorHistory(base), { type: "update-contact", connectorId: first.id,
      contactId: first.contacts[0]!.id, wire: option.mark, wireSection: option.section, materialBinding: option.materialBinding });
    const wire = createWire("w1", { connectorId: first.id, contactId: first.contacts[0]!.id },
      { connectorId: second.id, contactId: second.contacts[0]!.id });
    const connected = executeEditorCommand(selected, { type: "add-wire", wire });
    expect(connected.present.wires[0]!.materialBinding).toEqual(option.materialBinding);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(connected.present))).wires[0]!.materialBinding).toEqual(option.materialBinding);
    const changed = executeEditorCommand(connected, { type: "update-contact", connectorId: first.id,
      contactId: first.contacts[0]!.id, wireSection: "0,5", materialBinding: null });
    expect(changed.present.wires[0]!.materialBinding).toBeUndefined();
    expect(undoEditorCommand(changed).present.wires[0]!.materialBinding).toEqual(option.materialBinding);
  });
  it("roundtrips both fields, renders them on canvas and undoes the selection together", () => {
    const connector = createBuiltInConnectorInstance("catalog-connector-free", {
      id: "wire-test", designation: "X1", e4Position: { x: 0, y: 0 }, freeContactCount: 1,
    });
    const legacy = { ...createEmptyHarnessDesign(), connectors: [{ ...connector,
      contacts: connector.contacts.map(contact => ({ ...contact, wire: "legacy full text" })),
      schematic: { ...connector.schematic, baseColumns: connector.schematic.baseColumns.filter(c => c.key !== "wireSection") },
    }] };
    const parsed = parseHarnessDesignDocument(JSON.parse(JSON.stringify(legacy)));
    expect(parsed.connectors[0]!.contacts[0]!.wire).toBe("legacy full text");
    expect(parsed.connectors[0]!.schematic.baseColumns.find(c => c.key === "wireSection")?.visible).toBe(true);
    const history = executeEditorCommand(createEditorHistory(parsed), { type: "update-contact", connectorId: connector.id,
      contactId: connector.contacts[0]!.id, wire: "TEST", wireSection: "3C x 0,5 / 2P x 0,22" });
    const restored = parseHarnessDesignDocument(JSON.parse(JSON.stringify(history.present)));
    expect(restored.connectors[0]!.contacts[0]).toMatchObject({ wire: "TEST", wireSection: "3C x 0,5 / 2P x 0,22" });
    const scene = designToScene(restored, "e4");
    expect(JSON.stringify(scene)).toContain("wireSection");
    expect(getE4ConnectorLayout(scene.find(o => o.id === connector.id)!)?.columns.map(c => c.label)).toEqual(expect.arrayContaining(["Марка", "Сечение"]));
    expect(undoEditorCommand(history).present.connectors[0]!.contacts[0]!.wire).toBe("legacy full text");
  });
  it("opens legacy manual routes with the same contact coordinates and does not shift again", () => {
    const connectors = [0, 1200].map((x, i) => {
      const connector = createBuiltInConnectorInstance("catalog-connector-free", {
        id: `x${i}`, designation: `X${i}`, e4Position: { x, y: 0 }, freeContactCount: 1,
      });
      return { ...connector, schematic: { ...connector.schematic, baseColumns: connector.schematic.baseColumns.filter(c => c.key !== "wireSection") } };
    });
    let legacy = { ...createEmptyHarnessDesign(), connectors };
    const wire = createWire("w1", { connectorId: "x0", contactId: connectors[0]!.contacts[0]!.id }, { connectorId: "x1", contactId: connectors[1]!.contacts[0]!.id });
    legacy = { ...legacy, wires: [{ ...wire, e4Route: createOrthogonalE4Route(wireEndpointE4Anchor(legacy, wire.from)!, wireEndpointE4Anchor(legacy, wire.to)!), e4RouteMode: "manual" }] };
    const parsed = parseHarnessDesignDocument(JSON.parse(JSON.stringify(legacy)));
    parsed.connectors.forEach((connector, i) => expect(connectorContactPosition(connector, connector.contacts[0]!.id, "e4"))
      .toEqual(connectorContactPosition(connectors[i]!, connector.contacts[0]!.id, "e4")));
    expect(parsed.wires[0]!.e4Route).toEqual(legacy.wires[0]!.e4Route);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(parsed)))).toEqual(parsed);
  });
});
