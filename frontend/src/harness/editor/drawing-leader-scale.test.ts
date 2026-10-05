// @vitest-environment node
import { expect, it, vi } from "vitest";
import { physicalFixture } from "./physical-topology-fixture";
import { addDrawingPositions, drawingDocumentScene, emptyDrawingDocuments, moveDrawingAnnotation } from "./drawing-documents";
import { parseHarnessDesignDocument } from "./model";
import { createEditorHistory, executeEditorCommand, undoEditorCommand } from "./history";
import { drawEditorSceneObject, hitTestEditorScene } from "./CanvasViewport";

function fixture() {
  const d=physicalFixture();
  return {...d,drawingDocuments:addDrawingPositions(d)};
}

it.each([.25,1,2,4])("scales leader geometry and canvas at %s while preserving centers and hit bounds",leaderScale=>{
  const d=fixture(),doc={...d,drawingDocuments:{...d.drawingDocuments,leaderScale}};
  const leader=doc.drawingDocuments.leaders[0]!,scene=drawingDocumentScene(doc);
  const circle=scene.find(o=>o.id===leader.id)!,anchor=scene.find(o=>o.id===leader.id+":anchor")!;
  expect(circle).toMatchObject({x:leader.circle.x-12*leaderScale,y:leader.circle.y-12*leaderScale,width:24*leaderScale,height:24*leaderScale});
  expect(circle.points).toEqual(drawingDocumentScene(d).find(o=>o.id===leader.id)!.points);
  const arc=vi.fn(),fillText=vi.fn();
  const ctx=new Proxy({arc,fillText},{get:(t,p)=>Reflect.get(t,p)??vi.fn()}) as unknown as CanvasRenderingContext2D;
  drawEditorSceneObject(ctx,circle,false,"drawing");
  expect(arc).toHaveBeenLastCalledWith(leader.circle.x,leader.circle.y,12*leaderScale,0,Math.PI*2);
  expect(ctx.font).toBe(`${12*leaderScale}px Arial`);
  expect(fillText.mock.calls[0]!.slice(0,3)).toEqual([circle.label,leader.circle.x,leader.circle.y]);
  expect(fillText.mock.calls[0]![3]).toBeCloseTo(19.2*leaderScale);
  drawEditorSceneObject(ctx,anchor,false,"drawing");
  expect(arc).toHaveBeenLastCalledWith(circle.points![0]!.x,circle.points![0]!.y,4*leaderScale,0,Math.PI*2);
  const layers=[{id:"dimensions",label:"Positions",visible:true,locked:false}];
  expect(hitTestEditorScene([circle],layers,{x:leader.circle.x+11*leaderScale,y:leader.circle.y},10,"drawing")).toBe(leader.id);
  expect(hitTestEditorScene([circle],layers,{x:leader.circle.x+13*leaderScale+1,y:leader.circle.y},10,"drawing")).toBeNull();
});

it("moves scaled circles and anchors without jumping and preserves lengths, BOM and Undo",()=>{
  const d=fixture(),documents={...d.drawingDocuments,leaderScale:2};
  const history=executeEditorCommand(createEditorHistory(d),{type:"set-drawing-documents",documents});
  const doc=history.present,leader=documents.leaders[0]!,scene=drawingDocumentScene(doc);
  for(const id of [leader.id,leader.id+":anchor"]){
    const object=scene.find(o=>o.id===id)!;
    const moved=moveDrawingAnnotation(doc,id,{x:object.x,y:object.y})!;
    const after=drawingDocumentScene({...doc,drawingDocuments:moved}).find(o=>o.id===id)!;
    expect(after.x).toBeCloseTo(object.x);expect(after.y).toBeCloseTo(object.y);
    expect(moved.leaders[0]!.circle).toEqual(leader.circle);
  }
  expect(moveDrawingAnnotation(doc,leader.id,{x:100,y:200})!.leaders[0]!.circle).toEqual({x:124,y:224});
  expect(doc.wires).toEqual(d.wires);expect(doc.physicalTopology).toEqual(d.physicalTopology);
  expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(doc))).drawingDocuments).toEqual(documents);
  expect(undoEditorCommand(history).present).toBe(d);
});

it.each([0,.249,4.01,NaN,Infinity,"2",null])("rejects invalid leader scale %s",leaderScale=>{
  expect(()=>parseHarnessDesignDocument({...physicalFixture(),drawingDocuments:{...emptyDrawingDocuments(),leaderScale}})).toThrow();
});

it("keeps legacy default and spaces newly added leaders using the chosen scale",()=>{
  const d=physicalFixture(),base=addDrawingPositions(d),scaled=addDrawingPositions({...d,drawingDocuments:{...emptyDrawingDocuments(),leaderScale:2}});
  const oldScene=drawingDocumentScene({...d,drawingDocuments:base}),newScene=drawingDocumentScene({...d,drawingDocuments:scaled});
  const before=oldScene.find(o=>o.kind==="position-leader")!,after=newScene.find(o=>o.kind==="position-leader")!;
  expect(before.width).toBe(24);expect(after.width).toBe(48);
  expect(after.points![1]!.x-before.points![1]!.x).toBeCloseTo(48);
  expect(after.points![1]!.y-before.points![1]!.y).toBeCloseTo(-48);
  expect(addDrawingPositions({...d,drawingDocuments:{...base,leaderScale:2}}).leaders).toEqual(base.leaders);
});
