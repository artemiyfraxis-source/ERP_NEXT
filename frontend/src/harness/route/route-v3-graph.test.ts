// Ported from TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission (THIRD_PARTY_NOTICES.md).
// Original: src/Techmap.Client/src/manufacturing-v3/route-v3-graph.test.ts @ 0a456d2f7bd23288111e871058ebf55bf14426cd (branch codex/integration-m5-22).
import { describe, expect, it } from "vitest";
import { connectRouteV3, deleteRouteV3Edge, deleteRouteV3Node, editableRouteV3, insertRouteV3Node, refreshRouteV3 } from "./route-v3-graph";
import { parseRouteV3, routeV3ConnectionPort, routeV3ResizeNodeSize, type RouteV3Document, type RouteV3Node } from "./route-v3-model";
const w1 = { kind: "wire" as const, id: "W1" }, w2 = { kind: "wire" as const, id: "W2" };
const node = (id: string, refs = [w1]): RouteV3Node => ({ id, title: id, refs, fragmentIds: [], kind: "assembly", x: 30, y: 30 });
const graph = (): RouteV3Document => ({ version: 1, fragments: [], preparedRefs: [], generated: true, backgroundOpacity: .2, nodes: [node("a", [w1, w2]), node("b"), node("c")], edges: [{ id: "ab", from: "a", to: "b" }, { id: "bc", from: "b", to: "c" }] });
describe("Route v3 editable dependency graph", () => {
  it("migrates old cumulative refs without transferring unrelated cut blanks", () => {
    const original = graph();
    const d = refreshRouteV3(editableRouteV3({ ...original, nodes: original.nodes.map(n => n.id === "a" ? { ...n, id: "prepared:wire" } : n), edges: original.edges.map(e => e.from === "a" ? { ...e, from: "prepared:wire" } : e) }));
    expect(d.nodes[1]!.rawRefs).toEqual([]);
    expect(d.nodes[1]!.refs).toEqual([w1]);
    expect(parseRouteV3(JSON.parse(JSON.stringify(d)))).toEqual(d);
    expect(editableRouteV3(d)).toEqual(d);
  });
  it("transfers later material changes through generated assembly dependencies", () => {
    const original = graph();
    const d = editableRouteV3({ ...original, nodes: original.nodes.map(n => ({ ...n, refs: [w1] })) });
    const changed = refreshRouteV3({ ...d, nodes: d.nodes.map(n => n.id === "a" ? { ...n, rawRefs: [w1, w2] } : n) });
    expect(changed.nodes.find(n => n.id === "c")!.refs).toEqual([w1, w2]);
  });
  it("removes inherited materials transitively while retaining explicit raw material", () => {
    let d = editableRouteV3(graph());
    d = { ...d, nodes: d.nodes.map(n => n.id === "c" ? { ...n, rawRefs: [w2] } : n) };
    const cut = deleteRouteV3Edge(d, "ab");
    expect(cut.nodes[1]!.refs).toEqual([]);
    expect(cut.nodes[2]!.refs).toEqual([w2]);
    const restored = connectRouteV3(cut, "a", "b", "new-ab");
    expect(restored.nodes[1]!.refs).toEqual([w1, w2]);
    expect(restored.nodes[2]!.refs).toEqual([w2, w1]);
  });
  it("inserts one card before all immediate children, retaining other parents", () => {
    const d = editableRouteV3({ ...graph(), nodes: [node("a"), node("b"), node("c"), node("other", [w2])], edges: [{ id: "ab", from: "a", to: "b" }, { id: "ac", from: "a", to: "c" }, { id: "ob", from: "other", to: "b" }] });
    const next = insertRouteV3Node(d, "a", "inserted");
    expect(next.edges.map(e => `${e.from}>${e.to}`).sort()).toEqual(["a>inserted", "inserted>b", "inserted>c", "other>b"]);
    expect(next.nodes.find(n => n.id === "b")!.inputNodeIds).toEqual(["inserted", "other"]);
    expect(next.nodes.find(n => n.id === "inserted")!.refs).toEqual([w1]);
    expect(next.nodes.find(n => n.id === "b")!.y).toBeGreaterThan(next.nodes.at(-1)!.y);
  });
  it("adds at leaves and rejects cycles even for deselected inputs", () => {
    const d = editableRouteV3(graph());
    expect(insertRouteV3Node(d, "c", "new").nodes.at(-1)!.refs).toEqual([w1, w2]);
    expect(() => connectRouteV3(d, "c", "a", "cycle")).toThrow(/цикл/);
    expect(() => connectRouteV3(d, "a", "a", "self")).toThrow();
    expect(connectRouteV3(d, "a", "b", "duplicate")).toBe(d);
    expect(deleteRouteV3Node(d, "b").nodes.find(n => n.id === "c")!.refs).toEqual([]);
  });
  it("deduplicates diamond inheritance and updates ports after resize", () => {
    let d = editableRouteV3(graph());
    d = connectRouteV3(d, "a", "c", "ac");
    expect(d.nodes.find(n => n.id === "c")!.refs).toEqual([w1, w2]);
    const size = routeV3ResizeNodeSize(500, 570);
    expect(routeV3ConnectionPort({ ...node("n"), ...size }, "bottom")).toEqual({ x: 280, y: 600 });
    expect(routeV3ResizeNodeSize(-100, 9999)).toEqual({ width: 220, height: 720 });
  });
  it("persists operations and sizes and rejects invalid additions", () => {
    const d = editableRouteV3(graph());
    const saved = { ...d, nodes: d.nodes.map(n => ({ ...n, width: 500, height: 570, quantity: 2, operations: [{ id: "op", title: "Cut", kind: "static" as const, quantity: 3, minutesEach: 2, lengthMm: 0, speedMmPerMinute: 1, employees: 1 }] })) };
    expect(parseRouteV3(JSON.parse(JSON.stringify(saved)))).toEqual(saved);
    expect(parseRouteV3({ ...saved, nodes: [{ ...saved.nodes[0], width: -2 }] })).toBeNull();
  });
});
