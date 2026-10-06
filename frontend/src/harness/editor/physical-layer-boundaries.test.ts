// @vitest-environment node
import { expect, it } from "vitest";
import { physicalFixture } from "./physical-topology-fixture";
import { routePhysicalWires } from "./physical-wire-routing";
import { physicalWirePoints, physicalWireDisplayPaths } from "./physical-wire-geometry";
import { parsePhysicalTopology } from "./physical-topology-validation";

it("keeps pinned routes, covers and source data when a shorter automatic path exists", () => {
  const document = physicalFixture();
  const original = document.physicalTopology!;
  const topology = { ...original, segments: [...original.segments,
    { id: "shortcut", from: "NA", to: "NB", path: { kind: "polyline" as const, points: [] } }],
    coverings: [{ id: "cover", name: "Cover", width: 20, color: "#123456", lengthMm: 100,
      spans: [{ segmentId: "S0", from: 0, to: 1 }] }] };
  const before = JSON.stringify({ document, topology });
  const routed = routePhysicalWires(document, topology);
  expect(routed.routes[0]).toBe(topology.routes[0]);
  expect(routed.coverings).toBe(topology.coverings);
  expect(parsePhysicalTopology(routed, document)).toBe(routed);
  const automatic = routePhysicalWires(document, { ...topology, routes: [] });
  expect(automatic.routes.find(route => route.wireId === "W1")!.steps)
    .toEqual([{ segmentId: "shortcut", reverse: false }]);
  expect(JSON.stringify({ document, topology })).toBe(before);
});

it("does not use an unrelated connector as a through junction", () => {
  const document = physicalFixture();
  const topology = { ...document.physicalTopology!, routes: [], segments: [
    { id: "AC", from: "NA", to: "NC", path: { kind: "routed" as const, points: [] }},
    { id: "CB", from: "NC", to: "NB", path: { kind: "routed" as const, points: [] }},
  ] };
  const routes = routePhysicalWires(document, topology).routes;
  expect(routes.some(route => route.wireId === "W1")).toBe(false);
  expect(routes.find(route => route.wireId === "W2")!.steps)
    .toEqual([{ segmentId: "AC", reverse: false }]);
  expect(routes.find(route => route.wireId === "W3")!.steps)
    .toEqual([{ segmentId: "CB", reverse: true }]);
});

it("keeps measured wire geometry independent of lane visibility and width", () => {
  const document = physicalFixture(), start = { x: 120, y: 20 }, end = { x: 770, y: 520 };
  const before = JSON.stringify(document);
  const styled = { ...document, physicalTopology: { ...document.physicalTopology!,
    segments: document.physicalTopology!.segments.map(segment => ({ ...segment, width: 100, showWires: false })) } };
  expect(physicalWirePoints(styled, "W1", start, end)).toEqual(physicalWirePoints(document, "W1", start, end));
  expect(physicalWireDisplayPaths(styled, "W1", start, end)).toHaveLength(2);
  expect(physicalWireDisplayPaths(document, "W1", start, end)).toHaveLength(1);
  expect(styled.wires).toBe(document.wires);
  expect(styled.physicalTopology.routes).toBe(document.physicalTopology!.routes);
  expect(JSON.stringify(document)).toBe(before);
});
