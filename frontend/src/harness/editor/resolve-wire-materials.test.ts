// @vitest-environment node
import { describe, expect, it } from "vitest";
import { applyEditorCommand, createConnector, createWire } from "./commands";
import { createEmptyHarnessDesign, parseHarnessDesignDocument, type HarnessDesignDocument } from "./model";
import { resolveConnectedWireMaterials, uniqueWireMaterialOption } from "./resolve-wire-materials";
import type { WireDatabaseOption } from "./wire-database";
import { createEditorHistory, executeEditorCommand, undoEditorCommand } from "./history";

const binding = { sourceId: "technology-wires", snapshotId: "11111111-1111-4111-8111-111111111111",
  snapshotSha256: "a".repeat(64), recordId: "b".repeat(64), entityType: "wire" as const,
  sourceKey: "UL1001 #1", displayName: "UL1001 · 26AWG" };
const option: WireDatabaseOption = { id: binding.recordId, mark: "UL1001", section: "26AWG",
  label: "UL1001 · 26AWG", detail: "", color: "голубой", materialBinding: binding };

function connected(): HarnessDesignDocument {
  const x1 = createConnector("x1", "XS3", 1, { x: 0, y: 0 });
  const x2 = createConnector("x2", "XS2", 1, { x: 500, y: 0 });
  return { ...createEmptyHarnessDesign(), connectors: [
    { ...x1, contacts: [{ ...x1.contacts[0]!, wire: "UL1001", wireSection: "26AWG", color: "голубой" }] },
    x2,
  ], wires: [createWire("w4", { connectorId: "x1", contactId: x1.contacts[0]!.id },
    { connectorId: "x2", contactId: x2.contacts[0]!.id })] };
}

describe("connected wire material resolution", () => {
  it("binds a library preset to W4 and copies mark and section to XS2", () => {
    const resolved = resolveConnectedWireMaterials(connected(), [option]);
    expect(resolved.wires[0]?.materialBinding).toEqual(binding);
    expect(resolved.connectors[1]?.contacts[0]).toMatchObject({ wire: "UL1001", wireSection: "26AWG", materialBinding: binding });
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(resolved))).wires[0]?.materialBinding).toEqual(binding);
    expect(resolveConnectedWireMaterials(resolved, [option])).toBe(resolved);
  });

  it("requires an unambiguous published record, using color to separate variants", () => {
    const red = { ...option, id: "red", color: "красный", materialBinding: { ...binding, recordId: "c".repeat(64) } };
    expect(uniqueWireMaterialOption({ ...connected().connectors[0]!.contacts[0]!, color: "" }, [option, red])).toBeUndefined();
    expect(uniqueWireMaterialOption(connected().connectors[0]!.contacts[0]!, [option, red])).toEqual(option);
    expect(resolveConnectedWireMaterials(connected(), [{ ...option, id: "duplicate" }, option])).toEqual(connected());
  });

  it("uses mark and section even when the catalog contains several variants of the same mark", () => {
    const wider={...option,id:"wide",section:"30AWG",materialBinding:{...binding,recordId:"c".repeat(64)}};
    const resolved=resolveConnectedWireMaterials(connected(),[wider,option]);
    expect(resolved.wires[0]?.materialBinding).toEqual(binding);
    expect(resolved.connectors[1]?.contacts[0]).toMatchObject({wire:"UL1001",wireSection:"26AWG",materialBinding:binding});
  });

  it("resolves an AWG contact preset against numeric E3:AJ section values", () => {
    const imported={...option,section:"26"};
    const resolved=resolveConnectedWireMaterials(connected(),[imported]);
    expect(resolved.wires[0]?.materialBinding).toEqual(binding);
    expect(uniqueWireMaterialOption(connected().connectors[0]!.contacts[0]!,[{...imported,section:"24"}])).toBeUndefined();
  });

  it("changes both ends in one contact command and clears the old binding on manual edit", () => {
    const initial = connected();
    const selected = applyEditorCommand(initial, { type: "update-contact", connectorId: "x1",
      contactId: initial.connectors[0]!.contacts[0]!.id, materialBinding: binding,
      wire: "UL1001", wireSection: "26AWG" });
    expect(selected.wires[0]?.materialBinding).toEqual(binding);
    expect(selected.connectors[1]?.contacts[0]?.wireSection).toBe("26AWG");
    const edited = applyEditorCommand(selected, { type: "update-contact", connectorId: "x2",
      contactId: selected.connectors[1]!.contacts[0]!.id, wireSection: "24AWG", materialBinding: null });
    expect(edited.wires[0]?.materialBinding).toBeUndefined();
    expect(edited.connectors[0]?.contacts[0]).toMatchObject({ wire: "UL1001", wireSection: "24AWG" });
    expect(edited.connectors[0]?.contacts[0]?.materialBinding).toBeUndefined();
    const history = executeEditorCommand(createEditorHistory(initial), { type: "update-contact", connectorId: "x1",
      contactId: initial.connectors[0]!.contacts[0]!.id, wire: option.mark, wireSection: option.section, materialBinding: binding });
    expect(undoEditorCommand(history).present).toEqual(initial);
  });

  it("inherits preset fields when the material source is the second endpoint", () => {
    const initial = connected();
    const from = initial.wires[0]!.to;
    const to = initial.wires[0]!.from;
    const base = { ...initial, wires: [], connectors: initial.connectors.map((connector, index) => index === 0
      ? { ...connector, contacts: connector.contacts.map(contact => ({ ...contact, materialBinding: binding })) } : connector) };
    const added = applyEditorCommand(base, { type: "add-wire", wire: createWire("new", from, to) });
    expect(added.wires[0]?.materialBinding).toEqual(binding);
    expect(added.connectors[1]?.contacts[0]).toMatchObject({ wire: option.mark, wireSection: option.section, materialBinding: binding });
  });

  it("leaves unrelated contacts and conductors unchanged", () => {
    const initial = connected();
    const isolated = createConnector("isolated", "XS4", 1, { x: 0, y: 700 });
    const base = { ...initial, connectors: [...initial.connectors, isolated] };
    const resolved = resolveConnectedWireMaterials(base, [option]);
    expect(resolved.connectors[2]).toEqual(isolated);
  });

  it("repairs a connected wire after a component replacement removed the contact binding", () => {
    const initial = resolveConnectedWireMaterials(connected(), [option]);
    const replaced = { ...initial, connectors: initial.connectors.map(connector => ({
      ...connector, contacts: connector.contacts.map(contact => ({ ...contact, materialBinding: undefined })),
    })) };
    const repaired = resolveConnectedWireMaterials(replaced, [option]);
    expect(repaired.wires[0]?.materialBinding).toEqual(binding);
    expect(repaired.connectors.flatMap(connector => connector.contacts).filter(contact => contact.wire)
      .every(contact => contact.materialBinding?.recordId === binding.recordId)).toBe(true);
  });
});
