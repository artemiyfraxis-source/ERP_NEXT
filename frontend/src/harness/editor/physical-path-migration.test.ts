// @vitest-environment node
import { expect, it } from "vitest";
import { physicalFixture } from "./physical-topology-fixture";
import { parseHarnessDesignDocument } from "./model";
import { physicalSegmentPoints } from "./physical-geometry";
import { removePhysicalHandle, splitPhysicalSegment } from "./physical-topology";
import { segmentDimensionKey } from "./drawing-dimensions";

it("migrates old paths once without changing geometry, routes, covers or dimension anchors", () => {
  const base = physicalFixture();
  for (const routing of [undefined, "auto", "fixed"]) {
    const old = { ...base, physicalTopology: { ...base.physicalTopology!,
      segments: base.physicalTopology!.segments.map(({ path, ...segment }) => ({ ...segment, bends: path.points, routing })),
      coverings: [{ id: "cover", name: "Cover", width: 20, color: "#123456", lengthMm: null,
        spans: [{ segmentId: "S0", from: 0, to: 1, fromAnchor: 0, toAnchor: 2 }] }] } };
    const before = JSON.stringify(old);
    const migrated = parseHarnessDesignDocument(JSON.parse(before));
    expect(JSON.stringify(old)).toBe(before);
    for (const segment of migrated.physicalTopology!.segments) {
      expect(segment.path.kind).toBe(routing === "fixed" ? "polyline" : "routed");
      expect(segment).not.toHaveProperty("bends");
      expect(segment).not.toHaveProperty("routing");
      expect(segmentDimensionKey(migrated, segment.id)).toBe(segmentDimensionKey(base, segment.id));
      const original = base.physicalTopology!.segments.find(s => s.id === segment.id)!;
      const expected = routing === "fixed" ? { ...original, path: { ...original.path, kind: "polyline" as const } } : original;
      expect(physicalSegmentPoints(migrated, segment)).toEqual(physicalSegmentPoints(base, expected));
    }
    expect(migrated.physicalTopology!.routes).toEqual(old.physicalTopology.routes);
    expect(migrated.physicalTopology!.coverings).toEqual(old.physicalTopology.coverings);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(migrated)))).toEqual(migrated);
  }
});

it("keeps split polylines explicit through deletion of their last internal vertex and reopen", () => {
  const base = physicalFixture();
  const topology = splitPhysicalSegment(base, "S1", 1, "split", "next");
  const split = { ...base, physicalTopology: topology };
  const fragment = topology.segments.find(s => s.id === "next")!;
  let edited = fragment;
  while (edited.path.points.length) edited = removePhysicalHandle(split, edited, 0);
  expect(edited.path).toEqual({ kind: "polyline", points: [] });
  const saved = parseHarnessDesignDocument(JSON.parse(JSON.stringify({ ...split,
    physicalTopology: { ...topology, segments: topology.segments.map(s => s.id === edited.id ? edited : s) } })));
  expect(physicalSegmentPoints(saved, saved.physicalTopology!.segments.find(s => s.id === edited.id)!)).toHaveLength(2);
});

it.each([
  { path: { kind: "unknown", points: [] } },
  { path: { kind: "polyline", points: [{ x: 0, y: null }] } },
  { path: { kind: "routed", points: [] }, bends: [] },
  { path: { kind: "routed", points: [] }, routing: "fixed" },
  { bends: [], routing: "unknown" },
])("rejects malformed or ambiguous path %#", patch => {
  const base = physicalFixture();
  const { path, ...segment } = base.physicalTopology!.segments[0]!;
  expect(() => parseHarnessDesignDocument({ ...base, physicalTopology: { ...base.physicalTopology!,
    segments: [{ ...segment, ...patch }, ...base.physicalTopology!.segments.slice(1)] } })).toThrow();
});
