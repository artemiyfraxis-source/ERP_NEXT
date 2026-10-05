// @vitest-environment node
import { expect,it,vi } from "vitest";
import { physicalFixture } from "./physical-topology-fixture";
import { emptyDrawingDocuments } from "./drawing-documents";
import { parseHarnessDesignDocument, type HarnessDesignDocument } from "./model";
import { drawingBendRadius,drawingRouteSection } from "./drawing-route-path";
import { createEditorHistory,executeEditorCommand,undoEditorCommand } from "./history";
import { designToScene } from "./HarnessDesignEditor";
import { hitTestEditorScene,drawEditorSceneObject } from "./CanvasViewport";
import { coveringScene,moveCovering } from "./covering-layout";
import { standardCovering } from "./physical-coverings";
import { coveringSurfaces,coveringGrips,coveringHit } from "./covering-renderer";
import { drawingObjectPerimeter } from "./drawing-object-perimeter";
import { createJoiningPipe } from "./physical-joining-pipes";

function fixture():HarnessDesignDocument {
  const d=physicalFixture();return {...d,physicalTopology:{snap:false,nodes:[{id:"from",position:{x:0,y:0}},{id:"to",position:{x:100,y:100}}],segments:[{id:"pipe",from:"from",to:"to",path:{kind:"polyline",points:[{x:100,y:0}]},width:2}],routes:[],coverings:[{id:"cover",name:"Sleeve",width:3,color:"#334455",lengthMm:200,spans:[{segmentId:"pipe",from:.4,to:.6}]}]}};
}

it.each([0,40,200])("uses document radius %s for rendering, selection and sleeve boundary without changing physical geometry",bendRadius=>{
  const d=fixture(),documents={...emptyDrawingDocuments(),bendRadius};
  const history=executeEditorCommand(createEditorHistory(d),{type:"set-drawing-documents",documents});
  const doc=history.present,scene=designToScene(doc,"drawing"),pipe=scene.find(o=>o.id==="pipe")!;
  expect(pipe.routeRadius).toBe(bendRadius);
  expect(scene.filter(o=>o.kind==="wire").every(o=>o.routeRadius===bendRadius)).toBe(true);
  expect(designToScene(doc,"e4").filter(o=>o.kind==="wire").every(o=>o.routeRadius===undefined)).toBe(true);
  expect(doc.wires).toEqual(d.wires);expect(doc.physicalTopology).toEqual(d.physicalTopology);
  expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(doc))).drawingDocuments).toEqual(documents);
  expect(undoEditorCommand(history).present).toBe(d);
  const arcTo=vi.fn(),ctx=new Proxy({arcTo},{get:(o,p)=>Reflect.get(o,p)??vi.fn()}) as unknown as CanvasRenderingContext2D;
  drawEditorSceneObject(ctx,pipe,false,"drawing");
  expect(arcTo.mock.calls.length).toBe(bendRadius?1:0);expect(ctx.lineJoin).toBe(bendRadius?"round":"miter");
  const layers=[{id:"wires",label:"Wires",visible:true,locked:false}];
  expect(hitTestEditorScene([pipe],layers,{x:100,y:0},10,"drawing")).toBe(bendRadius?null:"pipe");
  const sleeve=coveringScene(doc)[0]!,surface=coveringSurfaces(sleeve)[0]!,grips=coveringGrips(sleeve);
  expect(sleeve.routeRadius).toBe(0); // Its surface is already sampled, never rounded twice.
  expect(grips[0]!.point).toEqual(surface.path[0]);expect(grips[1]!.point).toEqual(surface.path.at(-1));
  const middle=surface.path[Math.floor(surface.path.length/2)]!;
  expect(coveringHit(sleeve,middle,.1)).toBe(0);
  if(bendRadius)expect(surface.path.every(p=>Math.hypot(p.x-100,p.y)>1)).toBe(true);
  else expect(surface.path).toContainEqual({x:100,y:0});
  const anchor=drawingObjectPerimeter(doc,"pipe",{x:100,y:0})!;
  expect(Math.hypot(anchor.x-100,anchor.y)).toBeGreaterThan(bendRadius?5:0);
});
it("defaults legacy documents to 24",()=>expect(drawingBendRadius(fixture())).toBe(24));
it("keeps the global radius on a wire projected through an OP and its connector tails",()=>{
  const base=fixture(),t=base.physicalTopology!;
  const topology={...t,nodes:[...t.nodes,{id:"otherFrom",position:{x:0,y:100}},{id:"otherTo",position:{x:100,y:200}}],
    segments:[...t.segments,{id:"other",from:"otherFrom",to:"otherTo",path:{kind:"polyline" as const,points:[{x:100,y:100}]}}],
    routes:[{wireId:base.wires[0]!.id,steps:[{segmentId:"pipe",reverse:false}]}]};
  const source={...base,physicalTopology:topology},op=createJoiningPipe(source,[["pipe"],["other"]],"op");
  const doc={...source,physicalTopology:{...topology,joiningPipes:[op]},drawingDocuments:{...emptyDrawingDocuments(),bendRadius:48}};
  const wire=designToScene(doc,"drawing").find(o=>o.id===base.wires[0]!.id)!;
  expect(wire.routeRadius).toBe(48);
  expect(wire.paths?.some(path=>path.length>3)).toBe(true);
  const arcTo=vi.fn(),ctx=new Proxy({arcTo},{get:(o,p)=>Reflect.get(o,p)??vi.fn()}) as unknown as CanvasRenderingContext2D;
  drawEditorSceneObject(ctx,wire,false,"drawing");
  expect(arcTo).toHaveBeenCalled();
});
it("rounds editable bends of an isolated wire without changing its stored polyline",()=>{
  const base=fixture(),original=base.wires[0]!;
  const document={...base,drawingDocuments:{...emptyDrawingDocuments(),bendRadius:36},wires:base.wires.map(wire=>wire.id!==original.id?wire:{
    ...wire,
    drawingEndpoints:{from:{x:0,y:0},to:{x:120,y:100}},
    drawingRoute:[{x:120,y:0}],
  })};
  const before=structuredClone(document.wires),wire=designToScene(document,"drawing").find(item=>item.id===original.id)!;
  expect(wire.routeRadius).toBe(36);
  const arcTo=vi.fn(),ctx=new Proxy({arcTo},{get:(o,p)=>Reflect.get(o,p)??vi.fn()}) as unknown as CanvasRenderingContext2D;
  drawEditorSceneObject(ctx,wire,false,"drawing");
  expect(arcTo).toHaveBeenCalledTimes(1);
  expect(document.wires).toEqual(before);
});
it("creates and drags a sleeve on the visible arc using authored parameters",()=>{
  const d=fixture(),doc={...d,drawingDocuments:{...emptyDrawingDocuments(),bendRadius:40}};
  const points=[{x:0,y:0},{x:100,y:0},{x:100,y:100}];
  const start=drawingRouteSection(points,40,100,110)[0]!.point;
  const end=drawingRouteSection(points,40,110,120)[0]!.point;
  const cover=standardCovering(doc,"pipe",start,"Термоусадка","new");
  expect(cover.spans[0]!.from).toBeCloseTo(.4);expect(cover.spans[0]!.to).toBeCloseTo(.6);
  const moved=moveCovering(doc,"cover",0,"body",start,end,0)!;
  expect(moved.spans[0]!.from).toBeCloseTo(.45);expect(moved.spans[0]!.to).toBeCloseTo(.65);
  const unchanged=moveCovering(doc,"cover",0,"body",start,start,0)!;
  expect(unchanged.spans[0]!.from).toBe(.4);expect(unchanged.spans[0]!.to).toBe(.6);
});
it.each([-1,200.01,NaN,Infinity,"24",null])("rejects invalid radius %s",bendRadius=>{
  expect(()=>parseHarnessDesignDocument({...fixture(),drawingDocuments:{...emptyDrawingDocuments(),bendRadius}})).toThrow();
});
