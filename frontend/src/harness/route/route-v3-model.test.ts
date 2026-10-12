// Ported from TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission (THIRD_PARTY_NOTICES.md).
// Original: src/Techmap.Client/src/manufacturing-v3/route-v3-model.test.ts @ 0a456d2f7bd23288111e871058ebf55bf14426cd (branch codex/integration-m5-22).
import { describe, expect, it } from "vitest";
import { appendRouteV3Fragment, captureRouteV3Selection, createInitialRouteV3, generateRouteV3, parseRouteV3, revealedRouteV3Ids, removeRouteV3Fragment, type RouteV3Fragment } from "./route-v3-model";
// ERP changes: imports; a snapshot holds hidden IDs instead of a drawing copy (the last case is ERP's own).
import { createIndependentIsolatedDocument, createIsolatedRouteDrawingCopy, createRouteDrawingCopy } from "./route-drawing-copy";
import { physicalFixture } from "../editor/physical-topology-fixture";
import { designToScene } from "../editor/HarnessDesignEditor";
const source = physicalFixture();
const scene = designToScene(source, "drawing");
function fragment(id: string, objectIds: string[]): RouteV3Fragment {
  return { ...captureRouteV3Selection(source, scene, objectIds), id, title: id, mode: "source", backgroundOpacity: .2, createdAt: "2026-10-06" };
}
describe("Route v3 snapshot workflow", () => {
  it("preserves edited cards across snapshot saves/removal until explicit regeneration", () => {
    const initial = createInitialRouteV3(source);
    const generated = generateRouteV3(appendRouteV3Fragment(initial, fragment("one", ["W1"])));
    const edited = { ...generated, graphEdited: true, nodes: generated.nodes.map(n => ({ ...n, width: 510 })) };
    const saved = appendRouteV3Fragment(edited, fragment("two", ["W2"]));
    expect(saved.nodes).toEqual(edited.nodes);
    expect(saved.edges).toEqual(edited.edges);
    expect(saved.generated).toBe(false);
    const removed = removeRouteV3Fragment(saved, "one");
    expect(removed.nodes).toHaveLength(edited.nodes.length);
    expect(parseRouteV3(JSON.parse(JSON.stringify(removed)))).not.toBeNull();
    expect(generateRouteV3(removed).graphEdited).toBe(false);
    expect(generateRouteV3(removed).nodes.every(n => n.width === undefined)).toBe(true);
    expect(parseRouteV3({ ...removed, graphEdited: "true" })).toBeNull();
  });
  it("starts with wire blanks and dimmed opacity", () => {
    const initial = createInitialRouteV3(source);
    expect(initial.backgroundOpacity).toBe(.2);
    expect(initial.preparedRefs).toHaveLength(3);
    expect(initial.fragments).toEqual([]);
  });
  it("captures only selected IDs and their index labels without mutating source", () => {
    const before = JSON.stringify(source);
    const copy = fragment("one", ["W1"]);
    expect(copy.refs).toEqual([{kind:"wire",id:"W1"}]);
    expect(copy.objectIds).toContain("W1");
    expect(copy.drawingCopy.hiddenObjectIds).toContain("W2");
    expect(JSON.stringify(source)).toBe(before);
    expect(copy.drawingCopy.document).toBeUndefined();
  });
  it("resolves physical pipe selections into wires and includes no neighbouring material", () => {
    const copy = fragment("pipe", ["S0"]);
    expect(copy.refs.map(ref => ref.id).sort()).toEqual(["W1", "W2"]);
    expect(copy.objectIds).not.toContain("W3");
  });
  it("saves independent snapshots, preserves edit order, and reveals the union", () => {
    let route = appendRouteV3Fragment(createInitialRouteV3(source), fragment("a", ["W1"]));
    route = appendRouteV3Fragment(route, fragment("b", ["W2"]));
    route = appendRouteV3Fragment(route, { ...route.fragments[0]!, title: "renamed" });
    expect(route.fragments.map(f=>f.id)).toEqual(["a","b"]);
    expect(revealedRouteV3Ids(route)).toEqual(expect.arrayContaining(["W1","W2"]));
    expect(revealedRouteV3Ids(removeRouteV3Fragment(route,"a"))).not.toContain("W1");
  });
  it("merges both prior PFs and keeps equal composition states in chronological order", () => {
    let route = createInitialRouteV3(source);
    for (const f of [fragment("a",["W1"]),fragment("b",["W2"]),fragment("merge",["W1","W2"]),fragment("next",["W1","W2"])]) route = appendRouteV3Fragment(route,f);
    const generated=generateRouteV3(route,source);
    expect(generated.edges.filter(e=>e.to==="fragment:merge").map(e=>e.from).sort()).toEqual(["fragment:a","fragment:b"]);
    expect(generated.edges.filter(e=>e.to==="fragment:next").map(e=>e.from)).toEqual(["fragment:merge"]);
    expect(generated.nodes.some(node=>node.kind==="final")).toBe(false);
  });
  it("rejects partial reuse of an indivisible PF",()=>{
    let route=appendRouteV3Fragment(createInitialRouteV3(source),fragment("pair",["W1","W2"]));
    route=appendRouteV3Fragment(route,fragment("part",["W1"]));
    expect(()=>generateRouteV3(route,source)).toThrow("часть полуфабриката");
  });
  it("creates final only when all source materials are captured",()=>{
    const route=appendRouteV3Fragment(createInitialRouteV3(source),fragment("all",scene.map(o=>o.id)));
    const generated=generateRouteV3(route,source);
    expect(generated.nodes.at(-1)?.kind).toBe("final");
    expect(generateRouteV3(route, source, ["derived-dimension-not-a-material"]).nodes.at(-1)?.kind).toBe("final");
    expect(parseRouteV3(JSON.parse(JSON.stringify(generated)))).toEqual(generated);
  });
  it("rejects damaged drawing, invalid IDs, opacity and cyclic graphs without discarding data",()=>{
    const route=appendRouteV3Fragment(createInitialRouteV3(source),fragment("a",["W1"]));
    expect(parseRouteV3({...route,backgroundOpacity:2})).toBeNull();
    expect(parseRouteV3({...route,fragments:[{...route.fragments[0],drawingCopy:{}}]})).toBeNull();
    expect(parseRouteV3({...route,fragments:[{...route.fragments[0],refs:[{kind:"bad",id:"x"}]}]})).toBeNull();
    const g=generateRouteV3(route,source);
    expect(parseRouteV3({...g,edges:[...g.edges,{id:"cycle",from:"fragment:a",to:"prepared:wire"}]})).toBeNull();
  });
  it("supports graphic-only snapshot and bounds its independent copy",()=>{
    const extra={...scene[0]!, id:"graphic", kind:"graphic-text" as const};
    const capture=captureRouteV3Selection(source,[...scene,extra],["graphic"]);
    expect(capture.objectIds).toEqual(["graphic"]);
    expect(capture.refs).toEqual([]);
    expect(()=>createIsolatedRouteDrawingCopy(source)).not.toThrow();
  });
  it("ERP: refuses a source snapshot carrying a full drawing and an isolated one without its geometry",()=>{
    const route=appendRouteV3Fragment(createInitialRouteV3(source),fragment("a",["W1"]));
    const f=route.fragments[0]!;
    expect(JSON.stringify(f.drawingCopy)).not.toContain("connectors");
    expect(parseRouteV3({...route,fragments:[{...f,drawingCopy:createIsolatedRouteDrawingCopy(source)}]})).toBeNull();
    const isolated=createIsolatedRouteDrawingCopy(createIndependentIsolatedDocument(source,scene,["W1"])!);
    expect(parseRouteV3({...route,fragments:[{...f,mode:"isolated",isolatedDrawingCopy:isolated}]})).not.toBeNull();
    expect(parseRouteV3({...route,fragments:[{...f,mode:"isolated",isolatedDrawingCopy:createRouteDrawingCopy([])}]})).toBeNull();
  });
});
