// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  E4_BRIDGE_MINIMUM_SPACING,
  polylineLength,
  routeE4Wire,
  routeE4WireThroughWaypoints,
  validateE4Route,
  type E4RoutingRequest,
} from "./e4-router";

const horizontalRequest = (overrides: Partial<E4RoutingRequest> = {}): E4RoutingRequest => ({
  start: { position: { x: 0, y: 0 }, leadDirection: "right" },
  end: { position: { x: 100, y: 0 }, leadDirection: "left" },
  options: { leadLength: 10, wireClearance: 8 },
  ...overrides,
});

describe("E4 obstacle router", () => {
  it("uses a 30 degree lattice when angular routing is enabled", () => {
    const request: E4RoutingRequest = {
      start: { position: { x: 0, y: 0 }, leadDirection: null },
      end: { position: { x: 100, y: 100 / Math.sqrt(3) }, leadDirection: null },
      options: { leadLength: 0, angleStep: Math.PI / 6 },
    };
    const route = routeE4Wire(request);
    expect(route.points).toEqual([
      { x: 0, y: 0 },
      { x: 100, y: 100 / Math.sqrt(3) },
    ]);
    expect(() => validateE4Route(route.points, request)).not.toThrow();
  });

  it("minimizes the full path through a mandatory point, including bends after that point", () => {
    const request: E4RoutingRequest = {
      start: { position: { x: 0, y: 0 }, leadDirection: null },
      end: { position: { x: 100, y: 100 }, leadDirection: null },
      options: { leadLength: 0 },
    };
    const waypoint = { x: 50, y: 50 };
    const locallyChosenFirstLeg = routeE4Wire({ ...request, end: { position: waypoint, leadDirection: null } });
    const locallyChosenSecondLeg = routeE4Wire({ ...request, start: { position: waypoint, leadDirection: null } });
    const route = routeE4WireThroughWaypoints(request, [waypoint]);
    const localPoints = [...locallyChosenFirstLeg.points, ...locallyChosenSecondLeg.points.slice(1)];
    const localBends = localPoints.slice(2).filter((point, index) => {
      const first = localPoints[index]!;
      const second = localPoints[index + 1]!;
      return (first.x === second.x) !== (second.x === point.x);
    }).length;

    expect(route.length).toBe(200);
    expect(route.bends).toBe(2);
    expect(route.points.slice(1).some((point, index) => {
      const previous = route.points[index]!;
      return previous.x === point.x && previous.x === waypoint.x &&
          waypoint.y >= Math.min(previous.y, point.y) && waypoint.y <= Math.max(previous.y, point.y) ||
        previous.y === point.y && previous.y === waypoint.y &&
          waypoint.x >= Math.min(previous.x, point.x) && waypoint.x <= Math.max(previous.x, point.x);
    })).toBe(true);
    expect(localBends).toBe(3);
    expect(route.bends).toBeLessThan(localBends);
    expect(() => validateE4Route(route.points, request)).not.toThrow();
  });

  it("keeps mandatory contact leads and produces a minimal orthogonal route", () => {
    const request = horizontalRequest({
      end: { position: { x: 100, y: 40 }, leadDirection: "left" },
    });
    const route = routeE4Wire(request);

    expect(route.points[0]).toEqual({ x: 0, y: 0 });
    expect(route.points[1]).toEqual({ x: 10, y: 0 });
    expect(route.points.at(-1)).toEqual({ x: 100, y: 40 });
    expect(route.points.at(-2)?.y).toBe(40);
    expect(route.points.at(-2)?.x).toBeLessThanOrEqual(90);
    expect(route.length).toBe(140);
    expect(() => validateE4Route(route.points, request)).not.toThrow();
  });

  it("takes a shortest orthogonal detour around a connector table", () => {
    const request = horizontalRequest({
      obstacles: [{ id: "X3", x: 40, y: -10, width: 20, height: 20 }],
    });
    const route = routeE4Wire(request);

    expect(route.length).toBe(120);
    expect(route.points.some((point) => point.y === -10 || point.y === 10)).toBe(true);
    expect(() => validateE4Route(route.points, request)).not.toThrow();
  });

  it("honors obstacle padding while allowing a contact lead to leave its own table", () => {
    const request = horizontalRequest({
      start: { position: { x: 20, y: 0 }, leadDirection: "right", obstacleId: "X1" },
      end: { position: { x: 100, y: 0 }, leadDirection: "left" },
      obstacles: [
        { id: "X1", x: 0, y: -20, width: 20, height: 40 },
        { id: "X3", x: 45, y: -5, width: 10, height: 10 },
      ],
      options: { leadLength: 10, obstacleClearance: 4, wireClearance: 8 },
    });
    const route = routeE4Wire(request);

    expect(route.points[1]?.y).toBe(0);
    expect(route.points[1]?.x).toBeGreaterThanOrEqual(30);
    expect(route.points.some((point) => Math.abs(point.y) === 9)).toBe(true);
    expect(() => validateE4Route(route.points, request)).not.toThrow();
  });

  it("extends a lead through its own padding when padding exceeds the minimum lead", () => {
    const request = horizontalRequest({
      start: { position: { x: 20, y: 0 }, leadDirection: "right", obstacleId: "X1" },
      obstacles: [{ id: "X1", x: 0, y: -20, width: 20, height: 40 }],
      options: { leadLength: 4, obstacleClearance: 12, wireClearance: 8 },
    });
    const route = routeE4Wire(request);

    expect(route.points[1]?.x).toBeGreaterThanOrEqual(32);
    expect(route.points[1]?.y).toBe(0);
    expect(() => validateE4Route(route.points, request)).not.toThrow();
  });

  it("separates parallel portions by the complete bridge footprint including its halo", () => {
    const request = horizontalRequest({
      start: { position: { x: 0, y: 2 }, leadDirection: "right" },
      end: { position: { x: 100, y: 2 }, leadDirection: "left" },
      occupiedRoutes: [{ id: "W1", points: [{ x: 30, y: 0 }, { x: 70, y: 0 }] }],
      options: { leadLength: 10, wireClearance: 10 },
    });
    const route = routeE4Wire(request);

    expect(route.points.some((point) => point.y === E4_BRIDGE_MINIMUM_SPACING)).toBe(true);
    expect(E4_BRIDGE_MINIMUM_SPACING).toBeGreaterThanOrEqual(21);
    expect(() => validateE4Route(route.points, request)).not.toThrow();
  });

  it("permits perpendicular crossings of different wires", () => {
    const request = horizontalRequest({
      occupiedRoutes: [{ id: "W1", points: [{ x: 50, y: -1_000 }, { x: 50, y: 1_000 }] }],
    });
    const route = routeE4Wire(request);

    expect(route.points).toEqual([{ x: 0, y: 0 }, { x: 100, y: 0 }]);
    expect(route.length).toBe(100);
  });

  it("prefers a short parallel lane instead of creating an avoidable automatic crossing", () => {
    const request = horizontalRequest({
      occupiedRoutes: [{
        id: "W1",
        points: [{ x: 50, y: -20 }, { x: 50, y: 20 }],
      }],
      options: { leadLength: 10, wireClearance: 8 },
    });

    const route = routeE4Wire(request);

    expect(route.points).not.toEqual([{ x: 0, y: 0 }, { x: 100, y: 0 }]);
    expect(route.points.some((point) => Math.abs(point.y) > 20)).toBe(true);
    expect(route.length).toBeLessThan(200);
    expect(() => validateE4Route(route.points, request)).not.toThrow();
  });

  it("uses a crossing when avoiding it would require a materially longer detour", () => {
    const request = horizontalRequest({
      occupiedRoutes: [{
        id: "W1",
        points: [{ x: 50, y: -1_000 }, { x: 50, y: 1_000 }],
      }],
      options: { leadLength: 10, wireClearance: 8 },
    });

    const route = routeE4Wire(request);

    expect(route.points).toEqual([{ x: 0, y: 0 }, { x: 100, y: 0 }]);
  });

  it("never uses a collinear overlap, even when clearance is zero", () => {
    const request = horizontalRequest({
      occupiedRoutes: [{ id: "W1", points: [{ x: 30, y: 0 }, { x: 70, y: 0 }] }],
      options: { leadLength: 10, wireClearance: 0 },
    });
    const route = routeE4Wire(request);

    expect(route.length).toBeGreaterThan(100);
    expect(() => validateE4Route(route.points, request)).not.toThrow();
  });

  it("does not revisit an earlier point of a partially built route", () => {
    const request = horizontalRequest({
      start: { position: { x: 20, y: 20 }, leadDirection: null },
      end: { position: { x: 100, y: 0 }, leadDirection: null },
      occupiedRoutes: [{
        id: "partial",
        points: [{ x: 0, y: 0 }, { x: 0, y: 20 }, { x: 20, y: 20 }],
        allowCrossings: false,
        allowedTouchPoint: { x: 20, y: 20 },
      }],
      options: { leadLength: 0, wireClearance: 0 },
    });
    const route = routeE4Wire(request);

    expect(route.points).not.toContainEqual({ x: 0, y: 0 });
    expect(route.points).not.toContainEqual({ x: 0, y: 20 });
  });

  it("rejects self intersections and self overlaps", () => {
    const request: E4RoutingRequest = {
      start: { position: { x: 0, y: 0 }, leadDirection: null },
      end: { position: { x: 30, y: 10 }, leadDirection: null },
      options: { leadLength: 0 },
    };
    expect(() => validateE4Route([
      { x: 0, y: 0 }, { x: 30, y: 0 }, { x: 30, y: 20 },
      { x: 10, y: 20 }, { x: 10, y: -10 }, { x: 30, y: -10 }, { x: 30, y: 10 },
    ], request)).toThrow(/сам себя/);
    expect(() => validateE4Route([
      { x: 0, y: 0 }, { x: 30, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 30, y: 10 },
    ], request)).toThrow(/сам себя/);
  });

  it("fails when the compulsory lead itself is blocked", () => {
    const request = horizontalRequest({
      obstacles: [{ x: 5, y: -2, width: 2, height: 4 }],
    });
    expect(() => routeE4Wire(request)).toThrow(/препятствие/);
  });

  it("reports the Manhattan length of a route", () => {
    expect(polylineLength([{ x: 1, y: 2 }, { x: 5, y: 2 }, { x: 5, y: -3 }])).toBe(9);
  });

  it("coalesces near-identical grid coordinates instead of creating zero-length edges", () => {
    const result = routeE4WireThroughWaypoints({
      start: { position: { x: 0, y: 0 }, leadDirection: null },
      end: { position: { x: 100, y: 40 }, leadDirection: null },
      options: { leadLength: 0 },
    }, [{ x: 0.5e-9, y: 0 }]);

    expect(result.points.every((point, index) => index === 0 ||
      point.x !== result.points[index - 1]!.x || point.y !== result.points[index - 1]!.y)).toBe(true);
    expect(() => validateE4Route(result.points, {
      start: { position: { x: 0, y: 0 }, leadDirection: null },
      end: { position: { x: 100, y: 40 }, leadDirection: null },
      options: { leadLength: 0 },
    })).not.toThrow();
  });

  it("keeps a crossing away from the corner of another route", () => {
    const request: E4RoutingRequest = {
      start: { position: { x: 0, y: 35 }, leadDirection: null },
      end: { position: { x: 100, y: 35 }, leadDirection: null },
      occupiedRoutes: [{ id: "L", points: [{ x: 40, y: 0 }, { x: 40, y: 40 }, { x: 80, y: 40 }] }],
      options: { leadLength: 0, wireClearance: 8 },
    };
    expect(() => validateE4Route([
      { x: 0, y: 35 }, { x: 100, y: 35 },
    ], request)).toThrow(/зазор|углом/);
  });

  it("keeps a bend away from a foreign straight segment", () => {
    const request: E4RoutingRequest = {
      start: { position: { x: 0, y: 10 }, leadDirection: null },
      end: { position: { x: 100, y: 50 }, leadDirection: null },
      occupiedRoutes: [{ id: "W1", points: [{ x: 0, y: 0 }, { x: 100, y: 0 }] }],
      options: { leadLength: 0, wireClearance: 8 },
    };
    expect(() => validateE4Route([
      { x: 0, y: 10 }, { x: 20, y: 10 }, { x: 20, y: 50 }, { x: 100, y: 50 },
    ], request)).toThrow(/зазор/);
  });

  it("routes a two-contact crossover through separated dogleg corridors", () => {
    const first = [
      { x: 0, y: 0 }, { x: 60, y: 0 }, { x: 60, y: 24 }, { x: 120, y: 24 },
    ];
    const secondRequest: E4RoutingRequest = {
      start: { position: { x: 0, y: 24 }, leadDirection: "right" },
      end: { position: { x: 120, y: 0 }, leadDirection: "left" },
      occupiedRoutes: [{ id: "W1", points: first }],
      options: { leadLength: 24, wireClearance: 8 },
    };
    const second = routeE4Wire(secondRequest);

    expect(() => validateE4Route(second.points, secondRequest)).not.toThrow();
    expect(second.points.some((point) => point.y < 0 || point.y > 24)).toBe(true);
  });
});
