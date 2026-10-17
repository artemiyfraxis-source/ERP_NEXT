// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { physicalFixture } from "./physical-topology-fixture";
import { buildDrawingBom, emptyDrawingDocuments } from "./drawing-documents";
import { alignDrawingConnectorIndexes, drawingObjectIndexScene, moveDrawingIndex } from "./drawing-indices";
import type { EditorSceneObject } from "./editor-types";
import type { ComponentTemplateViewInstance } from "./component-template-view-renderer";
import { parseHarnessDesignDocument } from "./model";
import { executeEditorCommand, createEditorHistory, redoEditorCommand, undoEditorCommand } from "./history";
import { hitTestEditorScene, redrawCanvas } from "./CanvasViewport";
import { hiddenIdsForIsolatedObjects } from "./HarnessEditorWorkspace";

vi.mock("./component-template-view-renderer", async importOriginal => ({
  ...await importOriginal<typeof import("./component-template-view-renderer")>(),
  // The visible library picture is deliberately displaced from the connector's
  // legacy scene box, reproducing a rotated/offset library drawing.
  projectComponentTemplateView: vi.fn((_instance: unknown, _view: unknown, origin: {x:number;y:number}) => ({
    objectId: "A", snapshotId: "snapshot", viewId: "drawing", viewKind: "drawing" as const, commands: [],
    bounds: { minX: origin.x + 120, minY: origin.y + 25, maxX: origin.x + 190, maxY: origin.y + 55 },
  })),
}));

const sceneObject = (id: string, kind: EditorSceneObject["kind"], points?: readonly {x:number;y:number}[]): EditorSceneObject => ({
  id, kind, layerId: "wires", label: id, x: 0, y: 0, width: 80, height: 30, color: "#333", points,
});

describe("drawing object indices", () => {
  it("keeps the connector index when a terminal row is ordered before its body", () => {
    const base=physicalFixture();
    const document={...base,connectors:base.connectors.map(c=>({...c,contacts:c.contacts.map(p=>({...p,terminalArticle:"T-1"}))}))};
    const rows=buildDrawingBom(document),terminal=rows.find(row=>JSON.parse(row.key)[0]==="terminal-unpinned")!;
    const ordered={...document,drawingDocuments:{...emptyDrawingDocuments(),bomOrder:[terminal.key]}};
    expect(drawingObjectIndexScene(ordered,[sceneObject("A","connector")])[0]!.label).toBe("A");
  });
  afterEach(() => vi.unstubAllGlobals());
  it("creates index labels for connectors, wires, and coverings using BOM text", () => {
    const base = physicalFixture();
    const document = { ...base, drawingDocuments: { ...emptyDrawingDocuments(), indexScale: 2 } };
    const connector = sceneObject("connector", "connector");
    const wire = sceneObject("wire", "wire", [{x:0,y:0},{x:40,y:0}]);
    const covering = sceneObject("cover", "physical-covering", [{x:0,y:20},{x:40,y:20}]);
    const labels = drawingObjectIndexScene(document, [connector, wire, covering]);
    expect(labels.map(label => label.kind)).toEqual(["object-index", "object-index", "object-index"]);
    expect(labels.map(label => label.metadata?.indexObjectId)).toEqual(["connector", "wire", "cover"]);
    expect(labels.every(label => label.metadata?.indexScale === "2")).toBe(true);
  });

  it("persists object-relative offsets and applies them to the next scene", () => {
    const base = physicalFixture(), wire = sceneObject("wire", "wire", [{x:0,y:0},{x:40,y:0}]);
    const document = { ...base, drawingDocuments: emptyDrawingDocuments() };
    const initial = drawingObjectIndexScene(document, [wire])[0]!;
    const moved = moveDrawingIndex(document, initial.id, {x:100,y:50}, [wire, initial]);
    const next = drawingObjectIndexScene({ ...document, drawingDocuments: moved! }, [{...wire,points:[{x:10,y:10},{x:50,y:10}]}])[0]!;
    expect(moved?.indexOffsets?.wire).toEqual({x:next.x-Number(next.metadata?.indexBaseX),y:next.y-Number(next.metadata?.indexBaseY)});
    const [anchor, label] = next.points!;
    expect(anchor && label).toBeTruthy();
    const angle = Math.acos(Math.abs((label!.y-anchor!.y) / Math.hypot(label!.x-anchor!.x,label!.y-anchor!.y)));
    expect(angle).toBeLessThanOrEqual(Math.PI / 18 + 1e-8);
  });

  it("anchors a library connector index to its painted bounds, including after a drag", () => {
    const document = { ...physicalFixture(), drawingDocuments: emptyDrawingDocuments() };
    const connector = { ...sceneObject("A", "connector"), x: 10, y: 20, width: 80, height: 30 };
    const instance = { objectId: "A", snapshotId: "snapshot", articleVariantId: "article", content: {} } as ComponentTemplateViewInstance;
    const initial = drawingObjectIndexScene(document, [connector]);
    const aligned = alignDrawingConnectorIndexes(document, [...initial, connector], [instance]);
    const label = aligned.find(object => object.id === "object-index:A")!;
    expect(label.points?.[0]).toEqual({ x: 200, y: 60 });
    expect(label.metadata).toMatchObject({ indexOwnerMinX: "130", indexOwnerMaxX: "200" });

    const movedDocs = moveDrawingIndex(document, label.id, { x: 250, y: 65 }, aligned)!;
    const moved = alignDrawingConnectorIndexes({ ...document, drawingDocuments: movedDocs }, [...initial, connector], [instance])
      .find(object => object.id === label.id)!;
    expect(moved.points?.[0]).toEqual({ x: 200, y: 60 });
    expect(moved.x).toBeGreaterThan(200);
  });

  it("uses real BOM indices and preserves drag offsets through save, parent movement, and history", () => {
    const base = physicalFixture();
    const document = parseHarnessDesignDocument({
      ...base,
      drawingDocuments: { ...emptyDrawingDocuments(), indexScale: 1.5 },
      physicalTopology: { ...base.physicalTopology!, coverings: [{ id: "cover", name: "Sleeve", kind: "heat-shrink", width: 0, color: "#333333", lengthMm: 200, spans: [{ segmentId: "S0", from: 0, to: 1 }] }] },
    });
    const objects = [sceneObject("A", "connector"), sceneObject("W1", "wire", [{x:0,y:0},{x:40,y:0}]), sceneObject("cover", "physical-covering", [{x:0,y:20},{x:40,y:20}])];
    const labels = drawingObjectIndexScene(document, objects);
    expect(labels.map(label => label.label)).toEqual(["A", "W1", "ТУ1"]);
    const label = labels.find(item => item.metadata?.indexObjectId === "W1")!;
    const documents = moveDrawingIndex(document, label.id, {x:100,y:50}, [...objects, ...labels])!;
    const history = executeEditorCommand(createEditorHistory(document), { type: "set-drawing-documents", documents });
    const saved = parseHarnessDesignDocument(JSON.parse(JSON.stringify(history.present)));
    const movedWire = { ...objects[1]!, points: [{x:10,y:10},{x:50,y:10}] };
    const afterParentMove = drawingObjectIndexScene(saved, [objects[0]!, movedWire, objects[2]!]).find(item => item.metadata?.indexObjectId === "W1")!;
    const beforeParentMove = drawingObjectIndexScene(saved, objects).find(item => item.metadata?.indexObjectId === "W1")!;
    expect(afterParentMove.x).toBeCloseTo(beforeParentMove.x+10);
    expect(afterParentMove.y).toBeCloseTo(beforeParentMove.y+10);
    const [anchor, labelPosition] = afterParentMove.points!;
    expect(anchor && labelPosition).toBeTruthy();
    const angle = Math.acos(Math.abs((labelPosition!.y-anchor!.y) / Math.hypot(labelPosition!.x-anchor!.x,labelPosition!.y-anchor!.y)));
    expect(angle).toBeLessThanOrEqual(Math.PI / 18 + 1e-8);
    expect(undoEditorCommand(history).present).toBe(document);
    expect(redoEditorCommand(undoEditorCommand(history)).present.drawingDocuments?.indexOffsets?.W1).toEqual(documents.indexOffsets?.W1);
  });

  it("keeps grouped wire indices aligned when a BOM row also contains assigned physical segments", () => {
    const base = physicalFixture();
    const material = { sourceId:"source",snapshotId:"00000000-0000-4000-8000-000000000001",snapshotSha256:"a".repeat(64),recordId:"b".repeat(64),entityType:"wire" as const,sourceKey:"MATERIAL",displayName:"Wire" };
    const document = { ...base, wires: base.wires.map(wire => wire.id === "W1" || wire.id === "W2" ? { ...wire, materialBinding: material } : wire),
      physicalTopology: { ...base.physicalTopology!, segments: base.physicalTopology!.segments.map(segment => segment.id === "S0" ? { ...segment, specificationItemId:"W1" } : segment) } };
    const objects = [sceneObject("W1", "wire", [{x:0,y:0},{x:20,y:0}]),sceneObject("W2", "wire", [{x:0,y:10},{x:20,y:10}])];
    const labels = drawingObjectIndexScene(document, objects);
    expect(labels.find(item => item.metadata?.indexObjectId === "W1")?.label).toBe("W1");
    expect(labels.find(item => item.metadata?.indexObjectId === "W2")?.label).toBe("W2");
  });

  it("rejects index scales and offsets outside the persisted drawing limits", () => {
    const document = physicalFixture();
    expect(() => parseHarnessDesignDocument({ ...document, drawingDocuments: { ...emptyDrawingDocuments(), indexScale: 4.01 } })).toThrow();
    expect(() => parseHarnessDesignDocument({ ...document, drawingDocuments: { ...emptyDrawingDocuments(), indexOffsets: { W1: {x: 1e7 + 1,y: 0} } } })).toThrow();
  });

  it("paints the label above its connector, selects the label for dragging, and isolates it with its owner", () => {
    const connector = sceneObject("X1", "connector");
    const index: EditorSceneObject = { id: "object-index:X1", kind: "object-index", layerId: "wires", label: "X1", x: 10, y: 10, width: 30, height: 20, color: "#17384b", metadata: { indexObjectId: "X1", indexBaseX: "10", indexBaseY: "10", indexScale: "1" } };
    const drawingLayers = [{id:"wires",label:"Wires",visible:true,locked:false}];
    expect(hitTestEditorScene([connector,index], drawingLayers, {x:15,y:15}, 1, "drawing")).toBe(index.id);
    expect(hiddenIdsForIsolatedObjects([connector,index], [connector.id])).toEqual([]);
    expect(hiddenIdsForIsolatedObjects([connector,index], [index.id])).toEqual([]);
    expect(hiddenIdsForIsolatedObjects([connector,index], [])).toEqual([connector.id,index.id]);
    const calls: { method:string; args:unknown[] }[] = [];
    vi.stubGlobal("window", {devicePixelRatio:1});
    const context = new Proxy({measureText:()=>({width:16})},{get(target,key:string){if(key in target)return target[key as keyof typeof target];return (...args:unknown[])=>calls.push({method:key,args});}}) as unknown as CanvasRenderingContext2D;
    const canvas = { width:100,height:100,clientWidth:100,clientHeight:100,getContext:()=>context } as unknown as HTMLCanvasElement;
    redrawCanvas(canvas,"drawing",{offsetX:0,offsetY:0,zoom:1},[connector,index],drawingLayers,new Set());
    expect(calls.filter(call=>call.method==="fillText"&&call.args[0]==="X1")).toHaveLength(1);
  });
});
