import { createConnector, createWire } from "./commands";
import { createOrthogonalE4Route, wireEndpointE4Anchor, createEmptyHarnessDesign, type HarnessDesignDocument } from "./model";

export function physicalFixture(): HarnessDesignDocument {
  const connectors = ["A", "B", "C"].map((id, i) => createConnector(id, id, 2, { x: i * 650, y: i * 500 }));
  const end = (id: string, number: number) => ({ connectorId: id, contactId: `${id}:contact:${number}` });
  const document: HarnessDesignDocument = { ...createEmptyHarnessDesign(), connectors,
    wires: [createWire("W1", end("A", 1), end("B", 1)), createWire("W2", end("A", 2), end("C", 1)), createWire("W3", end("B", 2), end("C", 2))],
    physicalTopology: { snap: true, nodes: [
      { id: "NA", connectorId: "A", position: { x: 150, y: 40 } },
      { id: "NB", connectorId: "B", position: { x: 150, y: 40 } },
      { id: "NC", connectorId: "C", position: { x: 150, y: 40 } },
      { id: "J", position: { x: 210, y: 200 } }],
      segments: [{ id: "S0", from: "NA", to: "J", path: { kind: "routed" as const, points: [{ x: 180, y: 100 }] }}, { id: "S1", from: "J", to: "NB", path: { kind: "routed" as const, points: [] }}, { id: "S2", from: "J", to: "NC", path: { kind: "routed" as const, points: [] }}],
      routes: [{ wireId: "W1", steps: [{ segmentId: "S0", reverse: false }, { segmentId: "S1", reverse: false }] }, { wireId: "W2", steps: [{ segmentId: "S0", reverse: false }, { segmentId: "S2", reverse: false }] }, { wireId: "W3", steps: [{ segmentId: "S1", reverse: true }, { segmentId: "S2", reverse: false }] }] } };
  return {...document,wires:document.wires.map(w => ({...w,e4Route:createOrthogonalE4Route(wireEndpointE4Anchor(document,w.from)!,wireEndpointE4Anchor(document,w.to)!)}))};
}
