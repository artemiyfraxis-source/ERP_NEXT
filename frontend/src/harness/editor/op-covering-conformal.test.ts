// @vitest-environment node
import { describe, expect, it } from "vitest";
import { coveringScene, moveCovering } from "./covering-layout";
import { coveringHit, coveringSurfaces } from "./covering-renderer";
import { applyEditorCommand, createConnector, createWire } from "./commands";
import { createEmptyHarnessDesign, createOrthogonalE4Route, parseHarnessDesignDocument,
  wireEndpointE4Anchor, type HarnessDesignDocument, type Point } from "./model";
import { createJoiningPipe } from "./physical-joining-pipes";
import { joiningPipeDisplaySamples } from "./physical-joining-pipe-projection";
import { drawingPipeWidth } from "./drawing-thickness";
import { drawingRouteHitPoints } from "./drawing-route-path";
import { coveringRoute } from "./physical-coverings";
import { conformalCoveringContour, squareConformalContourEnds } from "./covering-contour";

function fixture(reverse = false, side: "from" | "to" = "to", continuous = false): HarnessDesignDocument {
  const blank = createEmptyHarnessDesign();
  const positions = [{ x: 0, y: 0 }, { x: 640, y: 0 },
    { x: 0, y: continuous && side === "from" ? 240 : 80 },
    { x: 640, y: continuous && side === "to" ? 240 : 80 }];
  const connectors = ["A", "B", "C", "D"].map((id, index) =>
    createConnector(id, id, 1, positions[index]!));
  const endpoint = (id: string) => ({ connectorId: id, contactId: `${id}:contact:1` });
  const wires = [
    { ...createWire("upper-wire", endpoint("A"), endpoint("B")), lengthMm: 720 },
    { ...createWire("lower-wire", endpoint("C"), endpoint("D")), lengthMm: 760 },
  ];
  const nodes = [
    { id: "a", connectorId: "A", position: { x: 0, y: 0 } },
    { id: "b", connectorId: "B", position: { x: 0, y: 0 } },
    { id: "c", connectorId: "C", position: { x: 0, y: 0 } },
    { id: "d", connectorId: "D", position: { x: 0, y: 0 } },
  ];
  const segments = [
    { id: "straight", from: "a", to: "b", path: { kind: "polyline" as const, points: [] }, width: 10 },
    { id: "bent", from: reverse ? "d" : "c", to: reverse ? "c" : "d",
      path: { kind: "polyline" as const, points: [] }, width: 10 },
  ];
  const base = { ...blank, connectors, wires };
  const source = { ...base, wires: wires.map(wire => ({ ...wire,
    e4Route: createOrthogonalE4Route(wireEndpointE4Anchor(base, wire.from)!, wireEndpointE4Anchor(base, wire.to)!) })),
    drawingDocuments: { tables: [], leaders: [], bomOrder: [], bendRadius: 16 },
    physicalTopology: { snap: false, nodes, segments, routes: [
      { wireId: "upper-wire", steps: [{ segmentId: "straight", reverse: false }] },
      { wireId: "lower-wire", steps: [{ segmentId: "bent", reverse }] },
    ] } };
  const created = createJoiningPipe(source, [["straight"], ["bent"]], "op");
  const pipe = { ...created, start: { x: 180, y: 12 }, end: { x: 460, y: 12 },
    members: created.members.map((member, index) => index === 1
      ? { ...member, ...(side === "to" ? { exitBend: { x: 550, y: 175 } } : { enterBend: { x: 90, y: 175 } }) }
      : member) };
  return { ...source, physicalTopology: { ...source.physicalTopology, joiningPipes: [pipe],
    coverings: [{ id: "shell", name: "Shell", kind: "heat-shrink" as const,
      color: "#627c85", width: 0, lengthMm: 420,
      spans: [{ segmentId: "op", from: -2, to: 3 }] }] } };
}

const distance = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y);

function pointInside(polygon: readonly Point[], point: Point): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[j]!, b = polygon[i]!;
    if ((a.y > point.y) !== (b.y > point.y) &&
      point.x < (b.x - a.x) * (point.y - a.y) / (b.y - a.y) + a.x) inside = !inside;
  }
  return inside;
}

function crossings(polygon: readonly Point[]) {
  const result: { i: number; j: number; a: Point; b: Point; c: Point; d: Point }[] = [];
  const orient = (a: Point, b: Point, c: Point) =>
    (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
  for (let i = 0; i < polygon.length; i++) {
    const a = polygon[i]!, b = polygon[(i + 1) % polygon.length]!;
    for (let j = i + 2; j < polygon.length; j++) {
      if ((j + 1) % polygon.length === i) continue;
      const c = polygon[j]!, d = polygon[(j + 1) % polygon.length]!;
      const ab1 = orient(a, b, c), ab2 = orient(a, b, d);
      const cd1 = orient(c, d, a), cd2 = orient(c, d, b);
      if (ab1 * ab2 < -1e-8 && cd1 * cd2 < -1e-8) result.push({ i, j, a, b, c, d });
    }
  }
  return result;
}

function longestTerminalEdge(polygon: readonly Point[], document: HarnessDesignDocument, side: "from" | "to") {
  const route = coveringRoute(document, "op")!;
  const span = document.physicalTopology!.coverings![0]!.spans[0]!;
  const at = route.before + Math.max(route.min, Math.min(route.max, span[side])) * route.length;
  const samples = (["from", "core", "to"] as const).flatMap(side => route.envelope.filter(p => p.side === side));
  const sections = samples.flatMap((b, index) => {
    const a = samples[index - 1];
    if (!a || a.side !== b.side || b.at - a.at < 1e-7 || at < a.at - 1e-7 || at > b.at + 1e-7) return [];
    const t = (at - a.at) / (b.at - a.at), sign = side === "from" ? -1 : 1;
    const centers = a.members.map((member, i) => ({ x: member.point.x + (b.members[i]!.point.x - member.point.x) * t,
      y: member.point.y + (b.members[i]!.point.y - member.point.y) * t }));
    const directions = a.members.map((member, i) => ({ x: sign * (b.members[i]!.point.x - member.point.x),
      y: sign * (b.members[i]!.point.y - member.point.y) }));
    const mean = { x: directions.reduce((sum, p) => sum + p.x, 0), y: directions.reduce((sum, p) => sum + p.y, 0) };
    return [{ centers, directions: [...directions, mean] }];
  });
  return polygon.reduce((maximum, point, index) => {
    const next = polygon[(index + 1) % polygon.length]!, edgeLength = distance(point, next);
    if (edgeLength < 1e-7) return maximum;
    const middle = { x: (point.x + next.x) / 2, y: (point.y + next.y) / 2 };
    const cap = sections.some(({ centers, directions }) => centers.some(p => distance(p, middle) < 80) &&
      directions.some(d => {
        const length = Math.hypot(d.x, d.y);
        if (length < 1e-7) return false;
        const parallel = Math.abs((next.x - point.x) * d.x + (next.y - point.y) * d.y) / length;
        const outside = Math.max(...centers.map(p => ((middle.x - p.x) * d.x + (middle.y - p.y) * d.y) / length));
        return parallel < .01 && outside > -.01;
      }));
    return cap ? Math.max(maximum, edgeLength) : maximum;
  }, 0);
}
function terminalFrontProjection(polygon: readonly Point[], path: readonly Point[], side: "from" | "to") {
  const endpoint = side === "from" ? path[0]! : path.at(-1)!;
  const adjacent = side === "from" ? path.find(point => distance(point, endpoint) > 1e-7)!
    : [...path].reverse().find(point => distance(point, endpoint) > 1e-7)!;
  const length = distance(endpoint, adjacent) || 1;
  const direction = { x: (endpoint.x - adjacent.x) / length, y: (endpoint.y - adjacent.y) / length };
  return Math.max(...polygon.map(point => point.x * direction.x + point.y * direction.y));
}

function spanFixture(source: HarnessDesignDocument, from: number, to: number): HarnessDesignDocument {
  const shell = source.physicalTopology!.coverings![0]!;
  return { ...source, physicalTopology: { ...source.physicalTopology!, coverings: [
    { ...shell, spans: [{ segmentId: "op", from, to }] },
  ] } };
}

function assertConformal(document: HarnessDesignDocument, side: "from" | "to") {
  const object = coveringScene(document).find(item => item.id === "shell")!;
  const surface = coveringSurfaces(object)[0]!;
  expect(surface.path.length).toBeGreaterThan(2);
  expect(surface.polygon.every(p => Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(true);
  expect(crossings(surface.polygon)).toEqual([]);
  const segment = document.physicalTopology!.segments.find(item => item.id === "bent")!;
  const radius = drawingPipeWidth(document, segment) / 2;
  const samples = drawingRouteHitPoints(joiningPipeDisplaySamples(document, "bent")!.map(sample => sample.point), 16);
  const outside = samples.filter(point => side === "to" ? point.x > 475 : point.x < 165);
  expect(outside.length).toBeGreaterThan(1);
  for (const [index, point] of outside.entries()) {
    expect(pointInside(surface.polygon, point), `uncovered member centre ${JSON.stringify(point)}`).toBe(true);
    expect(coveringHit(object, point, 0)).toBe(0);
    // Both edges of the painted P must remain inside the shell, not just its centreline.
    const previous = outside[Math.max(0, index - 1)]!;
    const next = outside[Math.min(outside.length - 1, index + 1)]!;
    const length = distance(previous, next) || 1;
    const normal = { x: -(next.y - previous.y) / length, y: (next.x - previous.x) / length };
    for (const sign of [-1, 1]) {
      const edge = { x: point.x + sign * normal.x * radius, y: point.y + sign * normal.y * radius };
      if (distance(point, surface.path[0]!) > radius && distance(point, surface.path.at(-1)!) > radius)
        expect(pointInside(surface.polygon, edge), `uncovered member edge ${JSON.stringify(edge)}`).toBe(true);
    }
  }
  return surface;
}

describe("REQ-039: OP shell follows member pipes beyond both ends", () => {
  it.each([false, true])("encloses the C1-like right bend, reverse=%s", reverse => {
    assertConformal(fixture(reverse), "to");
  });

  it.each([false, true])("encloses the left bend, reverse=%s", reverse => {
    assertConformal(fixture(reverse, "from"), "from");
  });

  it.each(["from", "to"] as const)("follows a C1-like P continuing down beyond the %s end", side => {
    assertConformal(fixture(false, side, true), side);
  });

  it("keeps a short C1-like cuff outside the right OP end around the downward P", () => {
    const source = fixture();
    const shell = source.physicalTopology!.coverings![0]!;
    const document = { ...source, physicalTopology: { ...source.physicalTopology!,
      coverings: [{ ...shell, spans: [{ segmentId: "op", from: .9, to: 1.22 }] }] } };
    const object = coveringScene(document)[0]!;
    const surface = coveringSurfaces(object)[0]!;
    expect(crossings(surface.polygon)).toEqual([]);
    const downward = drawingRouteHitPoints(joiningPipeDisplaySamples(document, "bent")!
      .map(sample => sample.point), 16).filter(point => point.x > 475 && point.x < 515);
    expect(downward.length).toBeGreaterThan(1);
    expect(downward.every(point => pointInside(surface.polygon, point))).toBe(true);
    const lowerEdge = surface.polygon.filter(point => point.x > 475 && point.x < 515);
    expect(lowerEdge.length).toBeGreaterThan(0);
    expect(Math.max(...lowerEdge.map(point => point.y)) - Math.max(...downward.map(point => point.y)))
      .toBeLessThan(20);
  });

  it("moves its boundaries and body along the continuation without changing the physical graph", () => {
    const original = fixture();
    const source = { ...original, physicalTopology: { ...original.physicalTopology!,
      coverings: [{ ...original.physicalTopology!.coverings![0]!,
        spans: [{ segmentId: "op", from: -.4, to: 1.4 }] }] } };
    const cover = source.physicalTopology!.coverings![0]!;
    const route = coveringSurfaces(coveringScene(source)[0]!)[0]!;
    const from = route.path[0]!, to = route.path.at(-1)!;
    const shortened = moveCovering(source, cover.id, 0, "to", to, { x: to.x - 36, y: to.y - 15 })!;
    expect(shortened.spans[0]!.to).toBeLessThan(cover.spans[0]!.to);
    const extended = moveCovering(source, cover.id, 0, "from", from, { x: from.x - 24, y: from.y })!;
    expect(extended.spans[0]!.from).not.toBe(cover.spans[0]!.from);
    const moved = moveCovering(source, cover.id, 0, "body", from, { x: from.x + 24, y: from.y })!;
    expect(moved.spans[0]!.from).not.toBe(cover.spans[0]!.from);
    const edited = { ...source, physicalTopology: { ...source.physicalTopology!, coverings: [shortened] } };
    expect(coveringSurfaces(coveringScene(edited)[0]!)[0]!.polygon).not.toEqual(route.polygon);
    const shifted = { ...source, physicalTopology: { ...source.physicalTopology!, coverings: [moved] } };
    expect(coveringSurfaces(coveringScene(shifted)[0]!)[0]!.polygon).not.toEqual(route.polygon);
    const stretched = { ...source, physicalTopology: { ...source.physicalTopology!, coverings: [extended] } };
    expect(coveringSurfaces(coveringScene(stretched)[0]!)[0]!.polygon).not.toEqual(route.polygon);
    expect(edited.physicalTopology.segments).toEqual(source.physicalTopology!.segments);
    expect(edited.physicalTopology.routes).toEqual(source.physicalTopology!.routes);
    expect(edited.wires).toEqual(source.wires);
    expect(shortened.lengthMm).toBe(420);
  });

  it("keeps IDs, links, manufacturing length and shell after an OP exit edit and reload", () => {
    const source = fixture();
    const before = assertConformal(source, "to").polygon;
    const edited = applyEditorCommand(source, { type: "update-joining-pipe-member-bend", pipeId: "op",
      memberIndex: 1, side: "exit", position: { x: 535, y: 205 } });
    const after = assertConformal(edited, "to").polygon;
    expect(after).not.toEqual(before);
    expect(edited.wires).toEqual(source.wires);
    expect(edited.wires.map(wire => wire.lengthMm)).toEqual([720, 760]);
    expect(edited.physicalTopology!.routes).toEqual(source.physicalTopology!.routes);
    expect(edited.physicalTopology!.segments).toEqual(source.physicalTopology!.segments);
    expect(edited.physicalTopology!.coverings![0]).toEqual(source.physicalTopology!.coverings![0]);
    const reopened = parseHarnessDesignDocument(JSON.parse(JSON.stringify(edited)));
    expect(coveringSurfaces(coveringScene(reopened)[0]!)[0]!.polygon).toEqual(after);
  });
});

describe("OP envelope contour", () => {
  it("keeps a bounded OP span square at both ends", () => {
    const path = [{ x: 0, y: 0 }, { x: 100, y: 0 }];
    const contour = squareConformalContourEnds(conformalCoveringContour(path, [8, 8], [8, 8], [[
      { x: -24, y: -12 }, { x: 124, y: -12 }, { x: 124, y: 12 }, { x: -24, y: 12 },
    ]]), path);
    expect(Math.min(...contour.map(point => point.x))).toBeCloseTo(0, 6);
    expect(Math.max(...contour.map(point => point.x))).toBeCloseTo(100, 6);
    expect(crossings(contour)).toEqual([]);
  });
  it("keeps a straight asymmetric sleeve rectangular", () => {
    const polygon = conformalCoveringContour([{ x: 0, y: 0 }, { x: 100, y: 0 }], [8, 8], [14, 14]);
    expect(crossings(polygon)).toEqual([]);
    expect(pointInside(polygon, { x: 50, y: 7 })).toBe(true);
    expect(pointInside(polygon, { x: 50, y: -13 })).toBe(true);
    expect(pointInside(polygon, { x: 50, y: 12 })).toBe(false);
  });

  it("contains both shoulders of a sharp turn with changing width", () => {
    const path = [{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 108, y: 90 }];
    const polygon = conformalCoveringContour(path, [8, 24, 12], [12, 30, 8]);
    expect(polygon.every(p => Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(true);
    expect(crossings(polygon)).toEqual([]);
    expect(pointInside(polygon, { x: 100, y: 0 })).toBe(true);
    expect(pointInside(polygon, { x: 97, y: 20 })).toBe(true);
  });

  it("leaves the open interior of a U-shaped path empty", () => {
    const path = [{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 100 }, { x: 0, y: 100 }];
    const polygon = conformalCoveringContour(path, [8, 8, 8, 8], [8, 8, 8, 8]);
    expect(crossings(polygon)).toEqual([]);
    expect(pointInside(polygon, { x: 50, y: 0 })).toBe(true);
    expect(pointInside(polygon, { x: 50, y: 100 })).toBe(true);
    expect(pointInside(polygon, { x: 50, y: 50 })).toBe(false);
  });
});

describe("OP continuation transverse fronts", () => {
  it("uses the document OP edge allowance for support shoulders", () => {
    const source = spanFixture(fixture(), 0, 1);
    const narrow = coveringSurfaces(coveringScene({ ...source, drawingDocuments: { ...source.drawingDocuments!, opCoveringEdgePx: 0 } })[0]!)[0]!;
    const wide = coveringSurfaces(coveringScene({ ...source, drawingDocuments: { ...source.drawingDocuments!, opCoveringEdgePx: 12 } })[0]!)[0]!;
    const extent = (polygon: readonly Point[]) => Math.max(...polygon.map(point => point.y)) - Math.min(...polygon.map(point => point.y));
    expect(extent(wide.polygon)).toBeGreaterThan(extent(narrow.polygon));
  });

  it.each([["from", -.55, 1], ["to", 0, 1.7]] as const)("uses a straight common front at the mid-station %s boundary", (side, from, to) => {
    for (const reverse of [false, true]) {
      const document = spanFixture(fixture(reverse, side), from, to);
      const object = coveringScene(document)[0]!;
      const surface = coveringSurfaces(object)[0]!;
      expect(longestTerminalEdge(surface.polygon, document, side), `reverse=${reverse}`).toBeGreaterThan(10);
      expect(crossings(surface.polygon)).toEqual([]);
    }
  });

  it.each([["from", -.55, -.5501, 1], ["to", 1.7, 1.7001, 0]] as const)(
    "moves the %s front continuously across a sample boundary", (side, boundary, moved, fixed) => {
      const source = fixture(false, side);
      const before = coveringSurfaces(coveringScene(spanFixture(source,
        side === "from" ? boundary : fixed, side === "to" ? boundary : 1))[0]!)[0]!;
      const after = coveringSurfaces(coveringScene(spanFixture(source,
        side === "from" ? moved : fixed, side === "to" ? moved : 1))[0]!)[0]!;
      const delta = Math.abs(terminalFrontProjection(after.polygon, after.path, side) -
        terminalFrontProjection(before.polygon, before.path, side));
      expect(delta).toBeLessThan(.5);
      expect(delta).toBeGreaterThan(0);
    });

  it.each([[.9, 1.22], [1.05, 1.2], [-.6, -.4]] as const)(
    "squares both span ends at arbitrary from/to tail positions (%s, %s)", (from, to) => {
      const document = spanFixture(fixture(), from, to);
      const surface = coveringSurfaces(coveringScene(document)[0]!)[0]!;
      expect(longestTerminalEdge(surface.polygon, document, "from")).toBeGreaterThan(10);
      expect(longestTerminalEdge(surface.polygon, document, "to")).toBeGreaterThan(10);
      expect(crossings(surface.polygon)).toEqual([]);
    });

  it("keeps straight transverse fronts for nested shells on both extended ends", () => {
    const source = fixture();
    const shell = source.physicalTopology!.coverings![0]!;
    const document = { ...source, physicalTopology: { ...source.physicalTopology!, coverings: [
      { ...shell, id: "inner", width: 0, spans: [{ segmentId: "op", from: -.55, to: 1.7 }] },
      { ...shell, id: "outer", width: 8, spans: [{ segmentId: "op", from: -.55, to: 1.7 }] },
    ] } };
    const objects = coveringScene(document);
    for (const side of ["from", "to"] as const) {
      for (const object of objects) {
        const surface = coveringSurfaces(object)[0]!;
        expect(longestTerminalEdge(surface.polygon, document, side)).toBeGreaterThan(24);
        expect(crossings(surface.polygon)).toEqual([]);
      }
    }
  });
});
