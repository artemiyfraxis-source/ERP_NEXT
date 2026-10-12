// @vitest-environment node
import { designToScene } from "./HarnessDesignEditor";
import { describe, expect, it } from "vitest";
import { physicalFixture } from "./physical-topology-fixture";
import { coveringPaths, pathLength, trimPolyline, type PhysicalCovering } from "./physical-coverings";
import { parseHarnessDesignDocument } from "./model";
import { physicalSegmentPoints, splitPhysicalSegment } from "./physical-topology";
import { applyEditorCommand } from "./commands";
import { buildHarnessSelectionIndex, resolveHarnessSelection } from "./harness-selection";

describe("branch coverings", () => {
  const covering: PhysicalCovering = { id: "COVER", name: "Оплётка", width: 20, color: "#789abc", lengthMm: 200,
    spans: [{ segmentId: "S0", from: .1, to: .9 }] };
  const fixture = () => { const d = physicalFixture(); return { ...d, physicalTopology: { ...d.physicalTopology!, coverings: [covering] } }; };
  it("trims along bends and not a bounding box", () => {
    expect(trimPolyline([{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 100 }], .25, .75)).toEqual([{ x: 50, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 50 }]);
  });
  it("tracks moved exits without changing material length or covering neighbouring branches", () => {
    const d = fixture();
    const moved = applyEditorCommand(d, { type: "move-connector", connectorId: "A", view: "drawing", position: { x: -100, y: 50 } });
    expect(coveringPaths(d, covering)).not.toEqual(coveringPaths(moved, covering));
    expect(moved.physicalTopology!.coverings).toEqual([covering]);
    expect(resolveHarnessSelection(buildHarnessSelectionIndex(moved), ["COVER"]).wireIds).toEqual(["W1", "W2"]);
    expect(moved.cables).toEqual([]);
  });
  it("preserves the covered interval when splitting a branch and round trips", () => {
    const d = fixture();
    const before = coveringPaths(d, covering).reduce((sum, points) => sum + pathLength(points), 0);
    const topology = splitPhysicalSegment(d, "S0", 1, "N", "NEW");
    const after = { ...d, physicalTopology: topology };
    expect(coveringPaths(after, topology.coverings![0]!).reduce((sum, points) => sum + pathLength(points), 0)).toBeCloseTo(before, 8);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(after))).physicalTopology!.coverings).toEqual(topology.coverings);
  });
  it("represents disconnected covered branches as one selectable object without a joining line", () => {
    const d=fixture();
    const c={...covering,spans:[...covering.spans,{segmentId:"S2",from:.2,to:.8}]};
    const document={...d,physicalTopology:{...d.physicalTopology!,coverings:[c]}};
    const objects=designToScene(document,"drawing").filter(o=>o.id==="COVER");
    expect(objects).toHaveLength(1);
    expect(objects[0]!.paths).toHaveLength(2);
  });
  it("rejects missing branch, reversed boundaries and invalid pinned identity", () => {
    const d = fixture();
    for (const change of [{ spans: [{ segmentId: "absent", from: 0, to: 1 }] }, { spans: [{ segmentId: "S0", from: .8, to: .3 }] }, { material: {} }, { material: null }, { lengthMm: -1 }])
      expect(() => parseHarnessDesignDocument({ ...d, physicalTopology: { ...d.physicalTopology!, coverings: [{ ...covering, ...change }] } })).toThrow();
    expect(physicalSegmentPoints(d, d.physicalTopology!.segments[0]!).length).toBeGreaterThan(2);
  });
});

it("adds standard protection at the clicked span and lists it before material assignment",async()=>{
 const {standardCovering,standardCoveringKinds}=await import("./physical-coverings"),{buildDrawingBom}=await import("./drawing-documents");
 const doc=physicalFixture(),cover=standardCovering(doc,"S0",{x:180,y:100},standardCoveringKinds[0],"heat");
 expect(cover.spans[0]!.to-cover.spans[0]!.from).toBeCloseTo(.2);
 const d={...doc,physicalTopology:{...doc.physicalTopology!,coverings:[cover]}};
 expect(buildDrawingBom(d).find(r=>r.objectIds.includes("heat"))).toMatchObject({name:"Термоусадка",amount:null});
});
