// @vitest-environment node
import { describe, expect, it } from "vitest";
import { createEmptyHarnessDesign, type HarnessDesignDocument } from "./model";
import { createJoiningPipe, joiningPipeExitId, joiningPipeExitPoint } from "./physical-joining-pipes";
import { physicalTopologyScene } from "./physical-scene";
import { applyEditorCommand } from "./commands";
import { joiningPipeDisplaySamples, joiningPipeMemberControls } from "./physical-joining-pipe-projection";
import { coveringHit } from "./covering-renderer";
import { coveringScene } from "./covering-layout";
import { physicalFixture } from "./physical-topology-fixture";
import { physicalWireDisplay } from "./physical-wire-geometry";
import { drawingRouteHitPoints } from "./drawing-route-path";
import { drawingWireWidth } from "./drawing-thickness";
import { parseHarnessDesignDocument } from "./model";
import { createEditorHistory, executeEditorCommand, undoEditorCommand } from "./history";

function fixture() {
  const d = createEmptyHarnessDesign();
  const topology = { snap: true, nodes: [
    { id: "a", position: { x: 0, y: 0 } }, { id: "b", position: { x: 600, y: 0 } },
    { id: "c", position: { x: 0, y: 100 } }, { id: "e", position: { x: 600, y: 100 } },
  ], segments: [
    { id: "p0", from: "a", to: "b", path: { kind: "polyline" as const, points: [] } },
    { id: "p1", from: "c", to: "e", path: { kind: "polyline" as const, points: [] } },
  ], routes: [] as never[] };
  const op = createJoiningPipe({ ...d, physicalTopology: topology }, [["p0"], ["p1"]], "op");
  return { ...d, physicalTopology: { ...topology, joiningPipes: [op] } };
}

describe("OP shared exit drag regression", () => {
  it("exposes exactly two shared exit grips and keeps each on its axis", () => {
    const d = fixture(), scene = physicalTopologyScene(d);
    const exits = scene.filter(o => o.metadata?.joiningPipeExit === "op");
    expect(exits.map(o => o.id).sort()).toEqual([joiningPipeExitId("op", "from"), joiningPipeExitId("op", "to")].sort());
    expect(exits).toHaveLength(2);
    const op = d.physicalTopology!.joiningPipes![0]!;
    for (const side of ["from", "to"] as const) {
      const p = joiningPipeExitPoint(op, side), edge = side === "from" ? op.start : op.end;
      expect(p.y).toBe(edge.y);
    }
  });

  it("moves the common exit without materializing member bends", () => {
    const d = fixture(), op = d.physicalTopology!.joiningPipes![0]!;
    const moved = applyEditorCommand(d, { type: "update-joining-pipe-exit", pipeId: "op", side: "from", position: { x: op.start.x - 100, y: op.start.y + 80 } });
    const next = moved.physicalTopology!.joiningPipes![0]!;
    expect(next.enterLength).toBeGreaterThan(op.enterLength!);
    expect(next.path.points).toEqual(op.path.points);
    expect(next.members.every(m => m.enterBend === undefined)).toBe(true);
    const before = joiningPipeMemberControls(d, "p0")!.length;
    let current = moved;
    for (let i = 0; i < 4; i++) current = applyEditorCommand(current, { type: "update-joining-pipe-exit", pipeId: "op", side: "from", position: { x: op.start.x - 100 - i * 10, y: op.start.y + i * 20 } });
    expect(joiningPipeMemberControls(current, "p0")!.length).toBe(before);
  });

  it("keeps an OP covering over every projected member sample", () => {
    const d = fixture();
    const withCover = { ...d, physicalTopology: { ...d.physicalTopology!, coverings: [{ id: "cover", name: "С", width: 0, color: "#334455", lengthMm: null, spans: [{ segmentId: "op", from: 0, to: 1 }] }] } };
    const moved = applyEditorCommand(withCover, { type: "edit-joining-pipe-bend", pipeId: "op", index: 0, position: { x: 300, y: 180 }, mode: "adjacent", insert: true });
    const shell = coveringScene(moved).find(o => o.id === "cover")!;
    for (const id of ["p0", "p1"]) for (const sample of joiningPipeDisplaySamples(moved, id)!.filter(s => s.fraction >= .3 && s.fraction <= .7)) expect(coveringHit(shell, sample.point, 1e-5)).not.toBeNull();
  });

  it("round-trips shared lengths and undo without changing member orientation", () => {
    const d = fixture(), op = d.physicalTopology!.joiningPipes![0]!;
    const reversed = { ...op, members: op.members.map((m, i) => i ? { ...m, reverse: !m.reverse } : m) };
    const source = { ...d, physicalTopology: { ...d.physicalTopology!, joiningPipes: [reversed] } };
    const history = executeEditorCommand(createEditorHistory(source), { type: "update-joining-pipe-exit", pipeId: "op", side: "to", position: { x: op.end.x + 80, y: op.end.y } });
    const roundTrip = parseHarnessDesignDocument(JSON.parse(JSON.stringify(history.present)));
    expect(roundTrip.physicalTopology!.joiningPipes![0]!.members[1]!.reverse).toBe(!op.members[1]!.reverse);
    expect(roundTrip.physicalTopology!.joiningPipes![0]!.exitLength).toBe(history.present.physicalTopology!.joiningPipes![0]!.exitLength);
    expect(undoEditorCommand(history).present).toEqual(source);
  });

  it("repeated connector drags keep routed member paths empty", () => {
    const source = physicalFixture();
    const op = createJoiningPipe(source, [["S1"], ["S2"]], "op");
    let current: HarnessDesignDocument = { ...source, physicalTopology: { ...source.physicalTopology!, joiningPipes: [op] } };
    const baseline = physicalTopologyScene(current).find(o => o.id === "S1")!.pipe!.handles.length;
    for (let index = 0; index < 5; index++) {
      current = applyEditorCommand(current, { type: "move-connector", connectorId: "B", view: "drawing", position: { x: 690 + 17 * index, y: 540 + 13 * index }, physicalDragMode: index % 2 ? "adjacent" : "carry" });
      expect(current.physicalTopology!.segments.find(s => s.id === "S1")!.path.points).toEqual([]);
      expect(physicalTopologyScene(current).find(o => o.id === "S1")!.pipe!.handles.length).toBe(baseline);
    }
  });

  it("keeps wire display finite and near the projected member through OP drags", () => {
    const source = physicalFixture();
    const op = createJoiningPipe(source, [["S1"], ["S2"]], "op");
    const base = { ...source, physicalTopology: { ...source.physicalTopology!, joiningPipes: [op] } };
    for (const position of [{ x: 300, y: 140 }, { x: 360, y: 250 }, { x: 320, y: 80 }]) {
      const frame = applyEditorCommand(base, { type: "update-joining-pipe", pipeId: "op", start: position });
      const segment = physicalTopologyScene(frame).find(o => o.id === "S1")!;
      const wire = physicalWireDisplay(frame, "W1", { x: 150, y: 40 }, { x: 800, y: 540 })!;
      expect(wire.selectionPaths.flat().every(p => Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(true);
      const projected = joiningPipeDisplaySamples(frame, "S1")!;
      expect(segment.points).toEqual(projected.map(s => s.point));
      const curve = drawingRouteHitPoints(segment.points!, segment.routeRadius);
      const distance = (p: {x:number;y:number}) => Math.min(...curve.slice(1).map((b,i) => {
        const a = curve[i]!, dx = b.x-a.x, dy = b.y-a.y;
        const t = Math.max(0, Math.min(1, ((p.x-a.x)*dx+(p.y-a.y)*dy)/(dx*dx+dy*dy||1)));
        return Math.hypot(p.x-a.x-t*dx,p.y-a.y-t*dy);
      }));
      const halfClearance = Math.max(0, segment.width/2-drawingWireWidth(frame, frame.wires.find(w=>w.id==="W1")!)/2);
      const paintedWire = wire.visibleStrokes.flatMap(stroke=>drawingRouteHitPoints(stroke.points, 24));
      const inside = paintedWire.filter(p=>p.x>Math.min(...segment.points!.map(q=>q.x))+40&&p.x<Math.max(...segment.points!.map(q=>q.x))-40);
      expect(Math.max(...inside.map(distance))).toBeLessThanOrEqual(halfClearance+1);
    }
  });
});
