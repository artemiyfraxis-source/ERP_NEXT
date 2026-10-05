// @vitest-environment node
import { describe, expect, it } from "vitest";
import { createEmptyHarnessDesign, parseHarnessDesignDocument, type HarnessDesignDocument, type Point } from "./model";
import { createJoiningPipe } from "./physical-joining-pipes";
import { physicalTopologyScene } from "./physical-scene";
import { drawingRouteHitPoints } from "./drawing-route-path";
import { pipeMidpoints } from "./CanvasViewport";
import { applyEditorCommand } from "./commands";
import { createEditorHistory, executeEditorCommand, undoEditorCommand } from "./history";
import { resolvePhysicalRoutePointCommand } from "./physical-route-point-command";
import { pipeBendSnapAnchors, snapBendPoint } from "./physical-editing";
import { joiningPipeMemberControls, projectJoiningPipePoint } from "./physical-joining-pipe-projection";
import { physicalSegmentPoints } from "./physical-geometry";
import { splitPhysicalSegment } from "./physical-topology";

function videoFixture(reverse = false): HarnessDesignDocument {
  const blank = createEmptyHarnessDesign();
  const topology = {
    snap: true,
    nodes: [
      { id: "a", position: { x: 0, y: 0 } },
      { id: "b", position: { x: 640, y: 0 } },
      { id: "c", position: { x: 0, y: 140 } },
      { id: "d", position: { x: 640, y: 300 } },
    ],
    segments: [
      { id: "p0", from: "a", to: "b", path: { kind: "polyline" as const, points: [] } },
      { id: "p1", from: reverse ? "d" : "c", to: reverse ? "c" : "d", path: { kind: "polyline" as const, points: [] } },
    ],
    routes: [],
  };
  const source = { ...blank, physicalTopology: topology };
  const created = createJoiningPipe(source, [["p0"], ["p1"]], "op");
  const pipe = {
    ...created,
    start: { x: 180, y: 12 },
    end: { x: 460, y: 72 },
    path: { kind: "polyline" as const, points: [{ x: 310, y: 88 }] },
    enterLength: 58,
    exitLength: 54,
    members: created.members.map((member, index) => index === 1
      ? { ...member, from: .28, to: .72 }
      : member),
  };
  return {
    ...source,
    drawingDocuments: { ...(source.drawingDocuments ?? { tables: [], leaders: [], bomOrder: [] }), bendRadius: 24 },
    physicalTopology: {
      ...topology,
      joiningPipes: [pipe],
      coverings: [{ id: "cover", name: "Shell", color: "#667788", width: 12, lengthMm: 520,
        spans: [{ segmentId: "op", from: 0, to: 1 }] }],
    },
  };
}

function distanceToRoute(point: Point, route: readonly Point[]): number {
  return Math.min(...route.slice(1).map((end, index) => {
    const start = route[index]!;
    const dx = end.x - start.x, dy = end.y - start.y;
    const fraction = Math.max(0, Math.min(1,
      ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy || 1)));
    return Math.hypot(point.x - start.x - fraction * dx, point.y - start.y - fraction * dy);
  }));
}

function lineAngle(a: Point, b: Point): number {
  return Math.atan2(b.y - a.y, b.x - a.x);
}

function angleGap(a: number, b: number): number {
  const difference = Math.abs(a - b) % (Math.PI * 2);
  return Math.min(difference, Math.PI * 2 - difference);
}

function memberScene(document: HarnessDesignDocument) {
  return physicalTopologyScene(document).find(object => object.id === "p1")!;
}

function withoutEnterTransition(): HarnessDesignDocument {
  const document = videoFixture();
  const topology = document.physicalTopology!;
  const op = topology.joiningPipes![0]!;
  return { ...document, physicalTopology: { ...topology, joiningPipes: [{ ...op,
    members: op.members.map((member, index) => index === 1 ? { ...member, enterBend: null } : member) }] } };
}

describe("OP midpoint gesture shown in video 14:43", () => {
  it.each([false, true])("places editable midpoints on the painted member route (reverse=%s)", reverse => {
    const scene = memberScene(videoFixture(reverse));
    const route = drawingRouteHitPoints(scene.points!, scene.routeRadius);
    expect(scene.routeRadius).toBe(24);
    expect(route.length).toBeGreaterThan(scene.points!.length);
    const grips = pipeMidpoints(scene);
    expect(grips.length).toBeGreaterThan(1);
    for (const grip of grips) {
      expect(distanceToRoute(grip.point, route)).toBeLessThan(1);
      expect(grip.index).toBeGreaterThanOrEqual(0);
      expect(grip.index).toBeLessThan(scene.pipe!.midpoints!.length);
    }
    for (const index of scene.pipe!.controlledMidpoints ?? []) {
      expect(grips.some(grip => grip.index === index)).toBe(false);
    }
  });

  it("inserts one transition bend from the midpoint and preserves the source graph", () => {
    const source = withoutEnterTransition();
    const scene = memberScene(source);
    const transition = scene.pipe!.joiningTransitionMidpoints!.find(item => item.side === "enter")!;
    const origin = scene.pipe!.midpoints![transition.index]!;
    const target = { x: origin.x - 40, y: origin.y + 35 };
    const before = source.physicalTopology!;
    const command = resolvePhysicalRoutePointCommand(source, "p1", transition.index, target, "carry", true)!;
    expect(command).toMatchObject({ type: "update-joining-pipe-member-bend", pipeId: "op",
      memberIndex: transition.memberIndex, side: transition.side });
    const previews = [target, { x: target.x - 20, y: target.y + 15 }, target]
      .map(position => applyEditorCommand(source,
        resolvePhysicalRoutePointCommand(source, "p1", transition.index, position, "carry", true)!));
    const history = executeEditorCommand(createEditorHistory(source), command);
    const committed = history.present;
    expect(committed).toEqual(previews.at(-1));
    expect(committed.physicalTopology!.joiningPipes![0]!.members[transition.memberIndex]!.enterBend).toEqual(target);
    expect(committed.physicalTopology!.segments).toEqual(before.segments);
    expect(committed.physicalTopology!.routes).toEqual(before.routes);
    expect(committed.physicalTopology!.coverings![0]!.lengthMm).toBe(520);
    expect(distanceToRoute(target, drawingRouteHitPoints(memberScene(committed).points!, 24))).toBeLessThan(24);
    expect(distanceToRoute(target, drawingRouteHitPoints(memberScene(source).points!, 24)))
      .toBeGreaterThan(distanceToRoute(target, drawingRouteHitPoints(memberScene(committed).points!, 24)) + 5);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(committed))).physicalTopology).toEqual(committed.physicalTopology);
    expect(undoEditorCommand(history).present).toEqual(source);
  });

  it.each(["adjacent", "carry"] as const)("inserts one authored point at the source interval with %s mode", mode => {
    const source = videoFixture();
    const scene = memberScene(source);
    const transitionIndices = new Set(scene.pipe!.joiningTransitionMidpoints!.map(item => item.index));
    const grip = pipeMidpoints(scene).find(item => !transitionIndices.has(item.index))!;
    expect(grip).toBeDefined();
    expect(scene.pipe!.midpointSources![grip.index]!.index).toBe(0);
    const target = { x: grip.point.x + 32, y: grip.point.y - 35 };
    const previewPositions = [
      { x: target.x + 10, y: target.y + 8 }, target,
      { x: target.x - 18, y: target.y - 12 }, target,
    ];
    const previews = previewPositions.map(position => {
      const command = resolvePhysicalRoutePointCommand(source, "p1", grip.index, position, mode, true)!;
      expect(command).toMatchObject({ type: "edit-physical-bend", segmentId: "p1", index: 0, insert: true });
      return applyEditorCommand(source, command);
    });
    expect(previews.every(preview => preview.physicalTopology!.segments[1]!.path.points.length === 1)).toBe(true);
    const command = resolvePhysicalRoutePointCommand(source, "p1", grip.index, target, mode, true)!;
    const history = executeEditorCommand(createEditorHistory(source), command);
    expect(history.present).toEqual(previews.at(-1));
    expect(history.present.physicalTopology!.segments[1]!.path.points).toHaveLength(1);
    expect(history.present.physicalTopology!.routes).toEqual(source.physicalTopology!.routes);
    expect(history.present.physicalTopology!.coverings![0]!.lengthMm).toBe(520);
    expect(memberScene(history.present).points).not.toEqual(scene.points);
    const committedScene = memberScene(history.present);
    const newHandle = committedScene.pipe!.authoredHandleIndices!.findIndex(value => value === 1);
    expect(newHandle).toBeGreaterThanOrEqual(0);
    expect(Math.hypot(committedScene.pipe!.handles[newHandle]!.x - target.x,
      committedScene.pipe!.handles[newHandle]!.y - target.y)).toBeLessThan(1);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(history.present))).physicalTopology)
      .toEqual(history.present.physicalTopology);
    expect(undoEditorCommand(history).present).toEqual(source);
  });

  it("places a Ctrl inserted authored handle at the snapped midpoint destination", () => {
    const source = videoFixture();
    const scene = memberScene(source);
    const transitionIndices = new Set(scene.pipe!.joiningTransitionMidpoints!.map(item => item.index));
    const grip = pipeMidpoints(scene).find(item => !transitionIndices.has(item.index))!;
    const display = [scene.points![0]!, ...scene.pipe!.handles, scene.points!.at(-1)!];
    const anchors = pipeBendSnapAnchors(scene.pipe!, display, grip.index, true,
      "adjacent", grip.point);
    const snapped = snapBendPoint({ x: grip.point.x + 43, y: grip.point.y + 24 }, anchors,
      true, 7, grip.point, Math.PI / 6);
    expect(snapped.guide).toBeDefined();
    const command = resolvePhysicalRoutePointCommand(source, "p1", grip.index,
      snapped.point, "adjacent", true)!;
    const moved = applyEditorCommand(source, command);
    const shown = memberScene(moved);
    const inserted = shown.pipe!.authoredHandleIndices!.findIndex(value => value === 1);
    expect(inserted).toBeGreaterThanOrEqual(0);
    expect(Math.hypot(shown.pipe!.handles[inserted]!.x - snapped.point.x,
      shown.pipe!.handles[inserted]!.y - snapped.point.y)).toBeLessThan(1);
    expect(distanceToRoute(snapped.point, drawingRouteHitPoints(shown.points!, 24)))
      .toBeLessThan(24);
  });

  it.each(["adjacent", "carry"] as const)("shows the snapped Ctrl guide on the moved route in %s mode", mode => {
    const source = withoutEnterTransition();
    const scene = memberScene(source);
    const transition = scene.pipe!.joiningTransitionMidpoints!.find(item => item.side === "enter")!;
    const origin = scene.pipe!.midpoints![transition.index]!;
    const display = [scene.points![0]!, ...scene.pipe!.handles, scene.points!.at(-1)!];
    const anchors = pipeBendSnapAnchors(scene.pipe!, display, transition.index, true, mode, origin);
    const snapped = snapBendPoint({ x: origin.x + 42, y: origin.y + 31 }, anchors, true, 7,
      origin, Math.PI / 6);
    expect(snapped.guide).toBeDefined();
    const command = resolvePhysicalRoutePointCommand(source, "p1", transition.index,
      snapped.point, mode, true)!;
    const moved = applyEditorCommand(source, command);
    const route = drawingRouteHitPoints(memberScene(moved).points!, 24);
    expect(distanceToRoute(snapped.point, route)).toBeLessThan(24);
    expect(memberScene(moved).points).not.toEqual(scene.points);
  });

  it("moves an existing bend in both directions while Ctrl changes during the gesture", () => {
    const base = videoFixture();
    const baseScene = memberScene(base);
    const transitionIndices = new Set(baseScene.pipe!.joiningTransitionMidpoints!.map(item => item.index));
    const grip = pipeMidpoints(baseScene).find(item => !transitionIndices.has(item.index))!;
    const created = applyEditorCommand(base, resolvePhysicalRoutePointCommand(base, "p1", grip.index,
      { x: grip.point.x + 36, y: grip.point.y - 38 }, "adjacent", true)!);
    const source = memberScene(created);
    const handleIndex = source.pipe!.authoredHandleIndices!.findIndex(value => value === 1);
    expect(handleIndex).toBeGreaterThanOrEqual(0);
    const origin = source.pipe!.handles[handleIndex]!;
    const display = [source.points![0]!, ...source.pipe!.handles, source.points!.at(-1)!];
    const previews = [
      { pointer: { x: origin.x + 30, y: origin.y + 25 }, ctrl: false },
      { pointer: { x: origin.x + 48, y: origin.y + 41 }, ctrl: true },
      { pointer: { x: origin.x - 36, y: origin.y - 24 }, ctrl: true },
      { pointer: { x: origin.x - 44, y: origin.y - 32 }, ctrl: false },
    ].map(({ pointer, ctrl }) => {
      const anchors = pipeBendSnapAnchors(source.pipe!, display, handleIndex, false,
        "adjacent", origin);
      const snap = snapBendPoint(pointer, anchors, ctrl, 7, origin, Math.PI / 6);
      const command = resolvePhysicalRoutePointCommand(created, "p1", handleIndex,
        snap.point, "adjacent", false)!;
      const document = applyEditorCommand(created, command);
      expect(document.physicalTopology!.segments[1]!.path.points).toHaveLength(1);
      expect(memberScene(document).points).not.toEqual(source.points);
      const shown = memberScene(document);
      const movedIndex = shown.pipe!.authoredHandleIndices!.findIndex(value => value === 1);
      expect(movedIndex).toBeGreaterThanOrEqual(0);
      expect(Math.hypot(shown.pipe!.handles[movedIndex]!.x - snap.point.x,
        shown.pipe!.handles[movedIndex]!.y - snap.point.y)).toBeLessThan(1);
      if (ctrl) {
        expect(snap.guide).toBeDefined();
        expect(distanceToRoute(snap.point, drawingRouteHitPoints(memberScene(document).points!, 24)))
          .toBeLessThan(24);
      }
      return { command, document };
    });
    expect(previews[0]!.document.physicalTopology!.segments[1]!.path.points[0])
      .not.toEqual(previews.at(-1)!.document.physicalTopology!.segments[1]!.path.points[0]);
    expect(memberScene(previews[0]!.document).points)
      .not.toEqual(memberScene(previews.at(-1)!.document).points);
    const history = executeEditorCommand(createEditorHistory(created), previews.at(-1)!.command);
    expect(history.present).toEqual(previews.at(-1)!.document);
    expect(history.present.physicalTopology!.segments[1]!.path.points).toHaveLength(1);
    expect(undoEditorCommand(history).present).toEqual(created);
  });

  it("keeps the Ctrl guide aligned with the actual generated shoulder after an authored move", () => {
    const base = videoFixture();
    const scene = memberScene(base);
    const transitionIndices = new Set(scene.pipe!.joiningTransitionMidpoints!.map(item => item.index));
    const grip = pipeMidpoints(scene).find(item => !transitionIndices.has(item.index))!;
    const created = applyEditorCommand(base, resolvePhysicalRoutePointCommand(base, "p1", grip.index,
      { x: grip.point.x + 36, y: grip.point.y - 38 }, "adjacent", true)!);
    const source = memberScene(created);
    const index = source.pipe!.authoredHandleIndices!.findIndex(value => value === 1);
    const origin = source.pipe!.handles[index]!;
    const controls = [source.points![0]!, ...source.pipe!.handles, source.points!.at(-1)!];
    const anchors = pipeBendSnapAnchors(source.pipe!, controls, index, false, "adjacent", origin);
    const snapped = snapBendPoint({ x: origin.x + 48, y: origin.y + 41 }, anchors,
      true, 7, origin, Math.PI / 6);
    expect(snapped.guide).toBeDefined();
    const moved = applyEditorCommand(created, resolvePhysicalRoutePointCommand(created, "p1", index,
      snapped.point, "adjacent", false)!);
    const shown = memberScene(moved);
    const movedIndex = shown.pipe!.authoredHandleIndices!.findIndex(value => value === 1);
    const nextControls = [shown.points![0]!, ...shown.pipe!.handles, shown.points!.at(-1)!];
    const nextPoint = nextControls[movedIndex + 1]!;
    const guideAnchors = snapped.guide!.filter(point => Math.hypot(point.x - snapped.point.x,
      point.y - snapped.point.y) > 1e-6);
    expect(guideAnchors.length).toBeGreaterThan(0);
    for (const guideAnchor of guideAnchors) {
      const sourceNeighbours = [index, index + 2];
      const sourceIndex = sourceNeighbours.find(i => Math.hypot(controls[i]!.x - guideAnchor.x,
        controls[i]!.y - guideAnchor.y) < 1)!;
      expect(sourceIndex).toBeDefined();
      const offset = sourceIndex - (index + 1);
      const actual = nextControls[movedIndex + 1 + offset]!;
      expect(angleGap(lineAngle(guideAnchor, snapped.point), lineAngle(actual, nextPoint)))
        .toBeLessThan(Math.PI / 60);
      const sampled = shown.points!;
      const nearest = (point: Point) => sampled.reduce((best, item, i) =>
        Math.hypot(item.x - point.x, item.y - point.y) <
        Math.hypot(sampled[best]!.x - point.x, sampled[best]!.y - point.y) ? i : best, 0);
      const from = nearest(actual), to = nearest(nextPoint);
      expect(Math.hypot(sampled[from]!.x - actual.x, sampled[from]!.y - actual.y)).toBeLessThan(1);
      expect(Math.hypot(sampled[to]!.x - nextPoint.x, sampled[to]!.y - nextPoint.y)).toBeLessThan(1);
      const shoulder = sampled.slice(Math.min(from,to), Math.max(from,to)+1);
      expect(shoulder.length).toBeGreaterThan(1);
      const dx = nextPoint.x - actual.x, dy = nextPoint.y - actual.y;
      const length = Math.hypot(dx,dy);
      for (const station of shoulder) {
        const cross = Math.abs((station.x - actual.x)*dy - (station.y - actual.y)*dx)/length;
        expect(cross).toBeLessThan(1);
      }
    }
  });

  it("keeps an existing member transition when its neighbouring midpoint is inserted", () => {
    const base = videoFixture();
    const original = { x: 135, y: 185 };
    const withTransition = applyEditorCommand(base, { type: "update-joining-pipe-member-bend",
      pipeId: "op", memberIndex: 1, side: "enter", position: original });
    const scene = memberScene(withTransition);
    const bend = scene.pipe!.joiningTransitionHandles!.find(item => item.side === "enter")!;
    const neighbour = pipeMidpoints(scene).find(item => Math.abs(item.index - bend.index) <= 1)!;
    expect(neighbour).toBeDefined();
    const target = { x: neighbour.point.x - 30, y: neighbour.point.y + 25 };
    const command = resolvePhysicalRoutePointCommand(withTransition, "p1", neighbour.index,
      target, "adjacent", true)!;
    expect(command).toMatchObject({ type: "edit-physical-bend", segmentId: "p1", insert: true });
    const result = applyEditorCommand(withTransition, command);
    expect(result.physicalTopology!.joiningPipes![0]!.members[1]!.enterBend).toEqual(original);
    expect(result.physicalTopology!.segments[1]!.path.points).toHaveLength(1);
    expect(result.physicalTopology!.routes).toEqual(withTransition.physicalTopology!.routes);
  });

  it.each([false, true])("keeps the shared axis, lead and saved transition fixed when member bends move (reverse=%s)", reverse => {
    const base = videoFixture(reverse);
    const original = { x: 125, y: 178 };
    const withTransition = applyEditorCommand(base, { type: "update-joining-pipe-member-bend",
      pipeId: "op", memberIndex: 1, side: "enter", position: original });
    const scene = memberScene(withTransition);
    const transitionIndices = new Set(scene.pipe!.joiningTransitionMidpoints!.map(item => item.index));
    const grip = pipeMidpoints(scene).find(item => !transitionIndices.has(item.index))!;
    const withAuthored = applyEditorCommand(withTransition,
      resolvePhysicalRoutePointCommand(withTransition, "p1", grip.index,
        { x: grip.point.x + 22, y: grip.point.y - 27 }, "adjacent", true)!);
    const beforeOp = withAuthored.physicalTopology!.joiningPipes![0]!;
    const beforeAxis = joiningPipeMemberControls(withAuthored, "p1")!
      .filter(control => control.controlled).map(control => control.point);
    const authoredScene = memberScene(withAuthored);
    const authoredIndex = authoredScene.pipe!.authoredHandleIndices!.findIndex(value => value === 1);
    expect(authoredIndex).toBeGreaterThanOrEqual(0);
    for (const delta of [{ x: 28, y: 23 }, { x: -31, y: -18 }]) {
      const origin = authoredScene.pipe!.handles[authoredIndex]!;
      const command = resolvePhysicalRoutePointCommand(withAuthored, "p1", authoredIndex,
        { x: origin.x + delta.x, y: origin.y + delta.y }, "adjacent", false)!;
      const moved = applyEditorCommand(withAuthored, command);
      const afterOp = moved.physicalTopology!.joiningPipes![0]!;
      expect(afterOp.start).toEqual(beforeOp.start);
      expect(afterOp.end).toEqual(beforeOp.end);
      expect(afterOp.path).toEqual(beforeOp.path);
      expect(afterOp.enterLength).toBe(beforeOp.enterLength);
      expect(afterOp.exitLength).toBe(beforeOp.exitLength);
      expect(moved.physicalTopology!.joiningPipes![0]!.members[1]!.enterBend).toEqual(original);
      expect(joiningPipeMemberControls(moved, "p1")!
        .filter(control => control.controlled).map(control => control.point)).toEqual(beforeAxis);
      expect(moved.physicalTopology!.segments[1]!.path.points).toHaveLength(1);
    }
  });

  it.each([false, true])("keeps projection continuous beside an authored exterior bend (reverse=%s)", reverse => {
    const source = videoFixture(reverse);
    const segment = source.physicalTopology!.segments[1]!;
    const point = reverse ? { x: 510, y: 278 } : { x: 130, y: 113.2 };
    const bent = { ...source, physicalTopology: { ...source.physicalTopology!, segments:
      source.physicalTopology!.segments.map(item => item.id === "p1"
        ? { ...segment, path: { kind: "polyline" as const, points: [point] } }
        : item) } };
    const route = physicalSegmentPoints(bent, bent.physicalTopology!.segments[1]!);
    const first = Math.hypot(route[1]!.x - route[0]!.x, route[1]!.y - route[0]!.y);
    const second = Math.hypot(route[2]!.x - route[1]!.x, route[2]!.y - route[1]!.y);
    const fraction = first / (first + second);
    const near = (offset: number) => {
      const local = offset < 0 ? route[0]! : route[2]!;
      const weight = Math.abs(offset) * (first + second) / (offset < 0 ? first : second);
      const raw = { x: point.x + (local.x - point.x) * weight,
        y: point.y + (local.y - point.y) * weight };
      return projectJoiningPipePoint(bent, "p1", fraction + offset, raw);
    };
    const middle = projectJoiningPipePoint(bent, "p1", fraction, point);
    for (const offset of [-1e-6, 1e-6]) {
      const adjacent = near(offset);
      expect(Math.hypot(adjacent.x - middle.x, adjacent.y - middle.y)).toBeLessThan(.01);
    }
  });

  it("keeps split member boundaries and untouched fragments fixed in adjacent mode", () => {
    const base = videoFixture();
    const topology = base.physicalTopology!;
    const prepared = { ...base, physicalTopology: { ...topology, segments: topology.segments.map(segment =>
      segment.id === "p1" ? { ...segment, path: { kind: "polyline" as const,
        points: [{ x: 100, y: 165 }, { x: 330, y: 230 }] } } : segment) } };
    const split = { ...prepared, physicalTopology: splitPhysicalSegment(prepared, "p1", 2, "joint", "tail") };
    expect(split.physicalTopology.joiningPipes![0]!.members[1]!.segmentIds).toEqual(["p1", "tail"]);
    const before = physicalTopologyScene(split).find(item => item.id === "p1")!;
    const bend = before.pipe!.authoredHandleIndices!.findIndex(value => value === 1);
    expect(bend).toBeGreaterThanOrEqual(0);
    const boundaries = (document: HarnessDesignDocument) => ["p1", "tail"].flatMap(id =>
      joiningPipeMemberControls(document, id)!
        .filter(control => control.boundary === "outerEnter" || control.boundary === "outerExit")
        .map(control => ({ id, boundary: control.boundary, point: control.point })));
    const baseline = boundaries(split);
    const tailBefore = split.physicalTopology.segments.find(segment => segment.id === "tail")!;
    const origin = before.pipe!.handles[bend]!;
    const command = resolvePhysicalRoutePointCommand(split, "p1", bend,
      { x: origin.x + 28, y: origin.y + 22 }, "adjacent", false)!;
    const moved = applyEditorCommand(split, command);
    expect(boundaries(moved)).toEqual(baseline);
    expect(moved.physicalTopology!.segments.find(segment => segment.id === "tail")).toEqual(tailBefore);
    expect(moved.physicalTopology!.segments.find(segment => segment.id === "p0"))
      .toEqual(split.physicalTopology.segments.find(segment => segment.id === "p0"));
    expect(moved.physicalTopology!.segments.find(segment => segment.id === "p1")!.path.points)
      .not.toEqual(split.physicalTopology.segments.find(segment => segment.id === "p1")!.path.points);
  });

  it("remaps durable bend ownership through insertion, split and deletion", () => {
    const base = videoFixture();
    const topology = base.physicalTopology!;
    const prepared = { ...base, physicalTopology: { ...topology, segments: topology.segments.map(segment =>
      segment.id === "p1" ? { ...segment, path: { kind: "polyline" as const,
        points: [{ x: 90, y: 160 }, { x: 270, y: 210 }, { x: 470, y: 265 }] } } : segment) } };
    const scene = memberScene(prepared);
    const metadata = scene.pipe!.authoredHandleIndices!.flatMap((value, index) => value > 0
      ? [{ bendIndex: value - 1, point: scene.pipe!.handles[index]! }] : []);
    expect(metadata).toHaveLength(3);
    const owned = { ...prepared, physicalTopology: { ...prepared.physicalTopology,
      joiningPipes: prepared.physicalTopology.joiningPipes!.map(pipe => ({ ...pipe,
        members: pipe.members.map((member, index) => index === 1 ? { ...member,
          authoredBendRegions: metadata.map((entry, bendIndex) => ({ segmentId: "p1", bendIndex,
            region: bendIndex === 2 ? "after-exit" as const : "enter" as const,
            displayPoint: entry.point })) } : member) })) } };
    const split = { ...owned, physicalTopology: splitPhysicalSegment(owned, "p1", 2, "joint2", "tail2") };
    const regions = split.physicalTopology.joiningPipes![0]!.members[1]!.authoredBendRegions!;
    expect(regions.map(entry => [entry.segmentId, entry.bendIndex]))
      .toEqual([["p1", 0], ["tail2", 0]]);
    expect(regions[0]!.displayPoint).toEqual(metadata[0]!.point);
    expect(regions[1]!.displayPoint).toEqual(metadata[2]!.point);
    const removed = applyEditorCommand(split, { type: "remove-physical-bend", segmentId: "p1", index: 0 });
    expect(removed.physicalTopology!.joiningPipes![0]!.members[1]!.authoredBendRegions)
      .toEqual([{ ...regions[1] }]);
  });

  it("keeps saved authored grips on the painted route without isolated projection spikes", () => {
    const source = videoFixture();
    const first = memberScene(source);
    const transitionIndices = new Set(first.pipe!.joiningTransitionMidpoints!.map(item => item.index));
    const midpoint = pipeMidpoints(first).find(item => !transitionIndices.has(item.index))!;
    const created = applyEditorCommand(source, resolvePhysicalRoutePointCommand(source, "p1", midpoint.index,
      { x: midpoint.point.x + 36, y: midpoint.point.y - 38 }, "adjacent", true)!);
    const firstBend = memberScene(created);
    const bendIndex = firstBend.pipe!.authoredHandleIndices!.findIndex(value => value === 1);
    const origin = firstBend.pipe!.handles[bendIndex]!;
    const moved = applyEditorCommand(created, resolvePhysicalRoutePointCommand(created, "p1", bendIndex,
      { x: origin.x + 48, y: origin.y + 41 }, "adjacent", false)!);
    const loaded = parseHarnessDesignDocument(JSON.parse(JSON.stringify(moved)));
    expect(loaded.physicalTopology).toEqual(moved.physicalTopology);
    for (const document of [moved, loaded]) {
      const scene = memberScene(document);
      const curve = drawingRouteHitPoints(scene.points!, scene.routeRadius);
      const authored = scene.pipe!.authoredHandleIndices!.findIndex(value => value === 1);
      expect(authored).toBeGreaterThanOrEqual(0);
      expect(distanceToRoute(scene.pipe!.handles[authored]!, curve)).toBeLessThan(24);
      const stops = scene.points!;
      for (let i = 1; i < stops.length - 1; i++) {
        const a = stops[i-1]!, b = stops[i]!, c = stops[i+1]!;
        const before = Math.hypot(b.x-a.x,b.y-a.y), after = Math.hypot(c.x-b.x,c.y-b.y);
        if (before < 1e-5 || after < 1e-5) continue;
        const cosine = ((b.x-a.x)*(c.x-b.x)+(b.y-a.y)*(c.y-b.y))/(before*after);
        if(cosine<=-.75) {
          const known=[stops[0]!,...scene.pipe!.handles,stops.at(-1)!];
          expect(Math.min(...known.map(point=>Math.hypot(point.x-b.x,point.y-b.y)))).toBeLessThan(1);
        }
      }
    }
  });

  it("carries only a neighbouring authored bend in the same exterior region", () => {
    const base = videoFixture();
    const topology = base.physicalTopology!;
    const source = { ...base, physicalTopology: { ...topology,
      segments: topology.segments.map(segment => segment.id === "p1" ? { ...segment,
        path: { kind: "polyline" as const, points: [{ x: 35, y: 155 }, { x: 80, y: 170 }] } } : segment),
      joiningPipes: topology.joiningPipes!.map(pipe => ({ ...pipe, members: pipe.members.map((member,index) =>
        index === 1 ? { ...member, authoredBendRegions: [
          { segmentId: "p1", bendIndex: 0, region: "before-enter" as const, displayPoint: { x: 35, y: 155 } },
          { segmentId: "p1", bendIndex: 1, region: "before-enter" as const, displayPoint: { x: 80, y: 170 } },
        ] } : member) })) } };
    const before = memberScene(source);
    const first = before.pipe!.authoredHandleIndices!.findIndex(value => value === 1);
    expect(first).toBeGreaterThanOrEqual(0);
    const target = { x: before.pipe!.handles[first]!.x + 18, y: before.pipe!.handles[first]!.y - 12 };
    const next = (mode: "carry" | "adjacent") => applyEditorCommand(source,
      resolvePhysicalRoutePointCommand(source, "p1", first, target, mode, false)!);
    const adjacent = next("adjacent"), carried = next("carry");
    const second = (document: HarnessDesignDocument) => document.physicalTopology!.segments[1]!.path.points[1]!;
    expect(second(adjacent)).toEqual(second(source));
    expect(second(carried)).not.toEqual(second(source));
    const visibleSecond = (document: HarnessDesignDocument) => document.physicalTopology!.joiningPipes![0]!
      .members[1]!.authoredBendRegions!.find(entry => entry.segmentId === "p1" && entry.bendIndex === 1)!.displayPoint;
    expect(visibleSecond(adjacent)).toEqual(visibleSecond(source));
    expect(visibleSecond(carried)).not.toEqual(visibleSecond(source));
    for(const document of [adjacent,carried]) {
      expect(document.physicalTopology!.joiningPipes![0]!.start).toEqual(topology.joiningPipes![0]!.start);
      expect(document.physicalTopology!.joiningPipes![0]!.end).toEqual(topology.joiningPipes![0]!.end);
      expect(joiningPipeMemberControls(document,"p1")!.filter(control => control.controlled).map(control => control.point))
        .toEqual(joiningPipeMemberControls(source,"p1")!.filter(control => control.controlled).map(control => control.point));
    }
  });

  it("uses the visible midpoint delta for a carried neighbouring bend on insertion", () => {
    const base = videoFixture();
    const topology = base.physicalTopology!;
    const source = { ...base, physicalTopology: { ...topology,
      segments: topology.segments.map(segment => segment.id === "p1" ? { ...segment,
        path: { kind: "polyline" as const, points: [{ x: 70, y: 168 }] } } : segment),
      joiningPipes: topology.joiningPipes!.map(pipe => ({ ...pipe, members: pipe.members.map((member,index) =>
        index === 1 ? { ...member, authoredBendRegions: [
          { segmentId: "p1", bendIndex: 0, region: "before-enter" as const,
            displayPoint: { x: 75, y: 178 } },
        ] } : member) })) } };
    const scene = memberScene(source);
    const authoredIndex = scene.pipe!.authoredHandleIndices!.findIndex(value => value === 1);
    const grip = pipeMidpoints(scene).find(item => Math.abs(item.index-authoredIndex)<=1 &&
      !scene.pipe!.joiningTransitionMidpoints!.some(transition=>transition.index===item.index))!;
    expect(grip).toBeDefined();
    const target = { x: grip.point.x + 24, y: grip.point.y - 19 };
    const command = resolvePhysicalRoutePointCommand(source, "p1", grip.index, target, "carry", true)!;
    expect(command).toMatchObject({ type: "edit-physical-bend", insert: true, displayOrigin: grip.point });
    const moved = applyEditorCommand(source, command);
    const previous=source.physicalTopology.joiningPipes![0]!.members[1]!.authoredBendRegions![0]!.displayPoint!;
    const carried=moved.physicalTopology!.joiningPipes![0]!.members[1]!.authoredBendRegions!
      .find(entry=>entry.segmentId==="p1"&&entry.bendIndex===1)!.displayPoint!;
    expect(carried.x-previous.x).toBeCloseTo(target.x-grip.point.x);
    expect(carried.y-previous.y).toBeCloseTo(target.y-grip.point.y);
    expect(moved.physicalTopology!.segments[1]!.path.points).toHaveLength(2);
  });

  it("anchors a carried midpoint to the next fixed OP shoulder", () => {
    const base=videoFixture(),topology=base.physicalTopology!;
    const source={...base,physicalTopology:{...topology,
      segments:topology.segments.map(segment=>segment.id==="p1"?{...segment,
        path:{kind:"polyline" as const,points:[{x:70,y:168}]}}:segment),
      joiningPipes:topology.joiningPipes!.map(pipe=>({...pipe,members:pipe.members.map((member,index)=>
        index===1?{...member,authoredBendRegions:[{segmentId:"p1",bendIndex:0,
          region:"before-enter" as const,displayPoint:{x:75,y:178}}]}:member)}))}};
    const scene=memberScene(source),controls=[scene.points![0]!,...scene.pipe!.handles,scene.points!.at(-1)!];
    const authored=scene.pipe!.authoredHandleIndices!.findIndex(value=>value===1);
    const index=[authored,authored+1].find(value=>scene.pipe!.midpointRegions?.[value]===
      scene.pipe!.authoredHandleRegions?.[authored]&&
      !scene.pipe!.joiningTransitionMidpoints!.some(transition=>transition.index===value))!;
    expect(index).toBeDefined();
    const origin=scene.pipe!.midpoints![index]!,neighbour=authored+1;
    const direction=index===authored?1:-1;
    const fixed=controls[neighbour+direction]!;
    const virtual={x:origin.x+fixed.x-controls[neighbour]!.x,
      y:origin.y+fixed.y-controls[neighbour]!.y};
    const anchors=pipeBendSnapAnchors(scene.pipe!,controls,index,true,"carry",origin);
    expect(anchors).toContainEqual(virtual);
    expect(anchors).not.toContainEqual(controls[neighbour]);
    const adjacent=pipeBendSnapAnchors(scene.pipe!,controls,index,true,"adjacent",origin);
    expect(adjacent).toContainEqual(controls[neighbour]);
    const target={x:virtual.x+18,y:virtual.y-31};
    const snap=snapBendPoint(target,anchors,true,7,origin,Math.PI/6,undefined,"nearest-compatible");
    expect(snap.guide![0]).toEqual(virtual);
    const moved=applyEditorCommand(source,resolvePhysicalRoutePointCommand(source,"p1",index,
      snap.point,"carry",true)!);
    const next=memberScene(moved);
    const carried=next.pipe!.authoredHandleIndices!.findIndex(value=>value===
      (index===authored?2:1));
    expect(carried).toBeGreaterThanOrEqual(0);
    const shoulder=next.pipe!.handles[carried]!;
    const guide=snap.guide![1]!;
    const cross=(shoulder.x-fixed.x)*(guide.y-virtual.y)-
      (shoulder.y-fixed.y)*(guide.x-virtual.x);
    expect(Math.abs(cross)).toBeLessThan(1e-5);
  });

  it("offers the nearer fixed shoulder on either side of an authored OP bend", () => {
    const base = videoFixture();
    const topology = base.physicalTopology!;
    const source = { ...base, physicalTopology: { ...topology,
      segments: topology.segments.map(segment => segment.id === "p1" ? { ...segment,
        path: { kind: "polyline" as const, points: [{ x: 70, y: 168 }] } } : segment),
      joiningPipes: topology.joiningPipes!.map(pipe => ({ ...pipe, members: pipe.members.map((member,index) =>
        index === 1 ? { ...member, authoredBendRegions: [
          { segmentId: "p1", bendIndex: 0, region: "before-enter" as const,
            displayPoint: { x: 75, y: 178 } },
        ] } : member) })) } };
    const scene = memberScene(source);
    const index = scene.pipe!.authoredHandleIndices!.findIndex(value => value === 1);
    const origin = scene.pipe!.handles[index]!;
    const controls = [scene.points![0]!, ...scene.pipe!.handles, scene.points!.at(-1)!];
    const anchors = pipeBendSnapAnchors(scene.pipe!,controls,index,false,"adjacent",origin);
    expect(anchors).toHaveLength(2);
    expect(anchors).toContainEqual(controls[index]);
    expect(anchors).toContainEqual(controls[index+2]);
    for (const anchor of anchors) {
      const pointer = { x: anchor.x + 29, y: anchor.y - 13 };
      const snapped = snapBendPoint(pointer,anchors,true,7,origin,Math.PI/6,undefined,"nearest-compatible");
      expect(snapped.guide![0]).toEqual(anchor);
    }
  });

  it("keeps the visible route joined when splitting at a persisted displayed bend", () => {
    const base = videoFixture();
    const topology = base.physicalTopology!;
    const displayPoint = { x: 115, y: 136 };
    const source = { ...base, physicalTopology: { ...topology,
      segments: topology.segments.map(segment => segment.id === "p1" ? { ...segment,
        path: { kind: "polyline" as const, points: [{ x: 100, y: 175 }] } } : segment),
      joiningPipes: topology.joiningPipes!.map(pipe => ({ ...pipe, members: pipe.members.map((member,index) =>
        index === 1 ? { ...member, authoredBendRegions: [
          { segmentId: "p1", bendIndex: 0, region: "before-enter" as const, displayPoint },
        ] } : member) })) } };
    const before = memberScene(source);
    const authored = before.pipe!.authoredHandleIndices!.findIndex(value => value === 1);
    expect(before.pipe!.handles[authored]).toEqual(displayPoint);
    const split = { ...source, physicalTopology: splitPhysicalSegment(source,"p1",1,"joint-display","tail-display") };
    for(const document of [split,parseHarnessDesignDocument(JSON.parse(JSON.stringify(split)))]) {
      const head=physicalTopologyScene(document).find(item=>item.id==="p1")!;
      const tail=physicalTopologyScene(document).find(item=>item.id==="tail-display")!;
      const end=head.points!.at(-1)!,start=tail.points![0]!;
      expect(Math.hypot(end.x-start.x,end.y-start.y)).toBeLessThan(1);
      expect(Math.hypot(end.x-displayPoint.x,end.y-displayPoint.y)).toBeLessThan(1);
    }
  });
});
