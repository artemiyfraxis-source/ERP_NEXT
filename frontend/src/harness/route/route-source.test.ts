// Ported from TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission (THIRD_PARTY_NOTICES.md).
// Original: src/Techmap.Client/src/manufacturing/route-source.test.ts @ 0a456d2f7bd23288111e871058ebf55bf14426cd (branch codex/integration-m5-22).
import { describe, expect, it } from "vitest";
import { createConnector, createWire } from "../editor/commands";
import { createEmptyHarnessDesign, normalizeCableInstance, type HarnessDesignDocument, type WireMaterialBinding, type WireStripProfileBinding } from "../editor/model";
import { buildRouteSourceItems, routeSourceDesignation } from "./route-source";

const material: WireMaterialBinding = { sourceId: "wires", snapshotId: "11111111-1111-4111-8111-111111111111", snapshotSha256: "a".repeat(64), recordId: "b".repeat(64), entityType: "wire", sourceKey: "MGT-0.35", displayName: "МГТФ 0,35" };
const stripping: WireStripProfileBinding = { ...material, entityType: "coax-termination", sourceKey: "strip-1", displayName: "Разделка 1", layers: [{ index: 1, diameterMm: 0.6, stripLengthMm: 5.125 }] };

function fixture(): HarnessDesignDocument {
  const a = createConnector("a", "X1", 2, { x: 0, y: 0 });
  const b = createConnector("b", "X2", 2, { x: 300, y: 0 });
  const connectors = [
    { ...a, partNumber: "BODY-A", contacts: a.contacts.map((c, i) => ({ ...c, number: i ? 20 : 10, wire: "МГТФ", wireSection: " 0,35 ", color: "#ff0000", terminalArticle: `A-${i}` })) },
    { ...b, partNumber: "BODY-B", contacts: b.contacts.map((c, i) => ({ ...c, number: i ? 40 : 30, wire: "", wireSection: "", color: "#0000ff", terminalArticle: `B-${i}` })) },
  ];
  const wires = [0, 1].map(i => ({ ...createWire(`wire-${i}`, { connectorId: a.id, contactId: a.contacts[i]!.id }, { connectorId: b.id, contactId: b.contacts[i]!.id }, 100.125, `Circuit ${i}`, "#123456", 0.125, -0.001, 0.001), materialBinding: material, stripProfiles: { from: stripping }, colorSource: { connectorId: b.id, contactId: b.contacts[i]!.id } }));
  return {
    ...createEmptyHarnessDesign(), connectors, wires,
    cables: [normalizeCableInstance({ id: "cable", memberWireIds: ["wire-1"], lengthMm: 200.001, endCorrectionFromMm: 0.001, endCorrectionToMm: 0.001, cutRoundingStepMm: 0.005, materialBinding: { ...material, entityType: "cable", displayName: "Кабель 2×0,35" } })],
    physicalTopology: { nodes: [{ id: "n1", position: { x: 0, y: 0 } }, { id: "n2", position: { x: 100, y: 0 } }], segments: [{ id: "s", from: "n1", to: "n2", path: { kind: "polyline", points: [] } }], routes: [], snap: true, coverings: [
      { id: "cover", name: "Термоусадка", color: "#444444", width: 10, lengthMm: null, lengthMode: "auto", spans: [{ segmentId: "s", from: 0, to: 1, fromAnchor: 0, toAnchor: 1 }], material: { ...material, entityType: "protective-covering", displayName: "Трубка 3/1" } },
      { id: "manual", name: "Оплётка", color: "#888888", width: 8, lengthMm: 35.125, lengthMode: "manual", spans: [{ segmentId: "s", from: 0.2, to: 0.5 }] },
      { id: "unknown", name: "Лента", color: "#ffffff", width: 20, lengthMm: null, spans: [{ segmentId: "s", from: 0, to: 1 }] },
    ] },
    drawingDocuments: { tables: [], leaders: [], bomOrder: [], physicalScale: 1, dimensions: [{ id: "d", segmentId: "s", from: 0, to: 1, pointCount: 2, routeKey: "s", mode: "path", offset: 20, lengthMm: 150.125 }] },
  };
}

describe("manufacturing route source", () => {
  it("keeps each wire, cable, covering and connector addressable and marks cable members", () => {
    const items = buildRouteSourceItems(fixture());
    expect(items.map(item => item.ref)).toEqual([
      { kind: "wire", id: "wire-0" }, { kind: "wire", id: "wire-1" }, { kind: "cable", id: "cable" },
      { kind: "covering", id: "cover" }, { kind: "covering", id: "manual" }, { kind: "covering", id: "unknown" },
      { kind: "connector", id: "a" }, { kind: "connector", id: "b" },
    ]);
    expect(items[0]).not.toHaveProperty("cableId");
    expect(items[1]).toMatchObject({ cableId: "cable", lengthMm: 100.249 });
    expect(items[2]).toMatchObject({ lengthMm: 200.005, color: null, material: "Кабель 2×0,35" });
    expect(items.filter(item => !item.cableId && ["wire", "cable"].includes(item.ref.kind))).toHaveLength(2);
    expect(items[6]).toMatchObject({ title: "X1", material: "BODY-A", lengthMm: null });
  });

  it("resolves contact attributes by stable endpoint IDs, preserving original numbers and stripping", () => {
    const source = fixture();
    const reordered = { ...source, connectors: source.connectors.map(c => ({ ...c, contacts: [...c.contacts].reverse() })) };
    const wire = buildRouteSourceItems(reordered)[0]!;
    expect(wire).toMatchObject({ title: "X1:10 → X2:30 · Circuit 0", material: "МГТФ 0,35", color: "#0000ff", section: "0,35", terminalFrom: "A-0", terminalTo: "B-0", stripProfiles: { from: stripping } });
  });

  it("uses only exact measured or manual millimetres and stays independent of drawing scale/geometry", () => {
    const source = fixture(), before = JSON.stringify(source), expected = buildRouteSourceItems(source);
    expect(expected.slice(3, 6).map(item => item.lengthMm)).toEqual([150.125, 35.125, null]);
    const scaled = { ...source, drawingDocuments: { ...source.drawingDocuments!, physicalScale: 8 }, connectors: source.connectors.map(c => ({ ...c, positions: { e4: { x: -1000, y: 7000 }, drawing: { x: 90000, y: 150000 } } })), wires: source.wires.map(w => ({ ...w, drawingRoute: [{ x: -100000, y: -100000 }, { x: 100000, y: 100000 }] })), physicalTopology: { ...source.physicalTopology!, nodes: source.physicalTopology!.nodes.map((node, index) => ({ ...node, position: { x: index * 500000, y: 900000 } })) } };
    expect(buildRouteSourceItems(scaled)).toEqual(expected);
    expect(JSON.stringify(source)).toBe(before);
  });

  it("carries the measured length plus both terminal corrections into the connection scheme", () => {
    const source = fixture();
    const wire = { ...source.wires[0]!, lengthMm: 100.1, endCorrectionFromMm: 1.25, endCorrectionToMm: -0.35, cutRoundingStepMm: 0.001 };
    expect(buildRouteSourceItems({ ...source, wires: [wire], cables: [] })[0]).toMatchObject({ lengthMm: 101 });
  });

  it("keeps unknown physical lengths unknown and handles junction ends without borrowing terminals", () => {
    const source = fixture(), wire = source.wires[0]!;
    const junctionWire = { ...wire, from: { junctionId: "j", connectorId: "" as const, contactId: "" as const }, colorSource: null, materialBinding: undefined, lengthMm: null };
    const item = buildRouteSourceItems({ ...source, wires: [junctionWire] })[0]!;
    expect(item).toMatchObject({ title: "Узел → X2:30 · Circuit 0", lengthMm: null, terminalFrom: "", terminalTo: "B-0", material: "", section: "", color: "#123456" });
    expect(buildRouteSourceItems(createEmptyHarnessDesign())).toEqual([]);
  });

  it("formats catalogue articles and complex wire sections without inventing units", () => {
    const items = buildRouteSourceItems(fixture());
    expect(routeSourceDesignation(items[0]!)).toContain("МГТФ 0,35 · арт. MGT-0.35");
    expect(routeSourceDesignation({ ...items[0]!, material: "UL1061 28AWG", materialArticle: "UL1061", section: "28AWG" })).toContain("арт. UL1061");
    expect(routeSourceDesignation({ ...items[0]!, material: "Кабель 3Cx0,5 | 2Px0,22", materialArticle: "CAB-1", section: "3Cx0,5 | 2Px0,22" })).not.toContain("мм²");
  });
});
