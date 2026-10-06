// @vitest-environment node
import { describe, expect, it } from "vitest";
import { physicalFixture } from "./physical-topology-fixture";
import { catalogOuterDiameter, drawingReferenceDiameter, drawingWireWidth, drawingPipeWidth, segmentWireLanes } from "./drawing-thickness";
import { coveringScene, moveCovering, offsetPolyline, wireExitPath } from "./covering-layout";
import { coveringHit, coveringGrips, coveringSurfaces } from "./covering-renderer";
import { coveringMeasuredLength, coveringRoute, standardCoveringOver, type PhysicalCovering } from "./physical-coverings";
import { drawingDimensionScene, dimensionTargetPoints, moveDrawingDimension, setPipeIntervalLength, toggleDrawingDimensions } from "./drawing-dimensions";
import { applyEditorCommand } from "./commands";
import { parseHarnessDesignDocument, type HarnessDesignDocument } from "./model";
import { hitTestEditorScene, drawEditorSceneObject } from "./CanvasViewport";
import { vi } from "vitest";
import { drawingObjectPerimeter } from "./drawing-object-perimeter";

function straight():HarnessDesignDocument {
 const d=physicalFixture();return {...d,physicalTopology:{...d.physicalTopology!,snap:false,nodes:d.physicalTopology!.nodes.map(n=>n.id==="NA"?{...n,position:{x:180,y:60}}:n.id==="J"?{...n,position:{x:480,y:60}}:n),segments:d.physicalTopology!.segments.map(s=>({...s,path: { kind: "routed" as const, points: [] }}))}};
}
const sleeve=(patch:Partial<PhysicalCovering>={}):PhysicalCovering=>({id:"cover",name:"Термоусадка",kind:"heat-shrink",width:20,color:"#556677",lengthMm:null,lengthMode:"auto",spans:[{segmentId:"S0",from:.1,to:.8}],...patch});
const covered=(c:PhysicalCovering[]= [sleeve()])=>{const d=straight();return {...d,physicalTopology:{...d.physicalTopology!,coverings:c}};};

describe("covering offsets",()=>{
 it("caps sharp transition miters instead of producing long spikes",()=>{
  const points=offsetPolyline([{x:0,y:0},{x:10,y:10},{x:20,y:0}],[20,20,20]);
  expect(Math.hypot(points[1]!.x-10,points[1]!.y-10)).toBeLessThanOrEqual(30);
 });
 it("keeps ordinary covering caps square after duplicate route points",()=>{
  const left=offsetPolyline([{x:0,y:0},{x:0,y:0},{x:0,y:100}],[10,10,10]);
  const right=offsetPolyline([{x:0,y:0},{x:0,y:0},{x:0,y:100}],[-10,-10,-10]);
  // A vertical route has horizontal cap edges. Duplicate authored points
  // must not make the endpoint fall back to a world-axis normal.
  expect(left[0]).toEqual({x:-10,y:0});
  expect(right[0]).toEqual({x:10,y:0});
 });
});

describe("relative drawing scale",()=>{
 it("reads Russian diameter columns with outer diameter precedence and decimal commas",()=>{
  expect(catalogOuterDiameter({"Диаметр изоляции":1.2,"Внешний диаметр, мм":"2,4 мм"})).toBe(2.4);
  expect(catalogOuterDiameter({"Диаметр изоляции":1.2,"Внешний диаметр (мм)":"2,4 мм"})).toBe(2.4);
  expect(catalogOuterDiameter({"Внешний диаметр":"—","Диаметр изоляции":"1,7"})).toBe(1.7);
  expect(catalogOuterDiameter({"Внешний диаметр":-3})).toBeUndefined();
 });
 it("scales all widths against the smallest known diameter while preserving ratios",()=>{
  const d=straight();const doc={...d,connectors:d.connectors.map((c,i)=>({...c,contacts:c.contacts.map((p,j)=>({...p,wireDiameterMm:i===0?j===0?1.2:3:undefined}))}))};
  expect(drawingReferenceDiameter(doc)).toBe(1.2);
  expect(drawingWireWidth(doc,doc.wires[1]!)/drawingWireWidth(doc,doc.wires[0]!)).toBeCloseTo(2.5);
  const scaled={...doc,drawingDocuments:{tables:[],leaders:[],bomOrder:[],physicalScale:2}};
  expect(drawingWireWidth(scaled,doc.wires[0]!)).toBe(2*drawingWireWidth(doc,doc.wires[0]!));
  expect(drawingPipeWidth(scaled,doc.physicalTopology!.segments[0]!)).toBe(2*drawingPipeWidth(doc,doc.physicalTopology!.segments[0]!));
  expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(scaled))).drawingDocuments?.physicalScale).toBe(2);
 });
});

describe("pipe dimensions",()=>{
 it("sets a pipe interval once, retains its identity and hides without losing lengths",()=>{
  let d=straight();d=applyEditorCommand(d,{type:"set-drawing-documents",documents:setPipeIntervalLength(d,"S0",0,1,123.456)});
  const id=d.drawingDocuments!.dimensions![0]!.id;
  expect(drawingDimensionScene(d)).toHaveLength(0);
  d=applyEditorCommand(d,{type:"set-drawing-documents",documents:toggleDrawingDimensions(d)});
  expect(drawingDimensionScene(d).find(o=>o.id===id)?.label).toBe("123.456 мм");
  d=applyEditorCommand(d,{type:"set-drawing-documents",documents:toggleDrawingDimensions(d)});
  expect(drawingDimensionScene(d)).toHaveLength(0);
  expect(d.drawingDocuments!.dimensions!.find(x=>x.id===id)?.lengthMm).toBe(123.456);
  expect(setPipeIntervalLength(d,"S0",0,1,200).dimensions!.find(x=>x.segmentId==="S0")?.id).toBe(id);
 });
 it("attaches connector witnesses to a perimeter corner",()=>{
  const d=straight(),docs=setPipeIntervalLength(d,"S0",0,1,100),dimension=docs.dimensions![0]!;
  const p=dimensionTargetPoints(d,dimension,new Map([["A",[[{x:0,y:0},{x:100,y:0},{x:100,y:80},{x:0,y:80},{x:0,y:0}]]]]));
  expect(p[0]).toEqual({x:100,y:80});expect(p[1]).toEqual({x:480,y:60});
 });
 it("snaps collinear dimensions and replaces adjacent arrows with dots",()=>{
  let d=straight();d={...d,physicalTopology:{...d.physicalTopology!,nodes:d.physicalTopology!.nodes.map(n=>n.id==="NA"?{...n,connectorId:undefined}:n),segments:d.physicalTopology!.segments.map(s=>s.id==="S0"?{...s,path: { kind: "routed" as const, points: [{x:330,y:60}] }}:s)}};
  d={...d,drawingDocuments:setPipeIntervalLength(d,"S0",0,1,100)};
  d={...d,drawingDocuments:setPipeIntervalLength(d,"S0",1,2,200)};
  d={...d,drawingDocuments:{...d.drawingDocuments!,showDimensions:true}};
  const second=d.drawingDocuments!.dimensions![1]!;
  const changed=moveDrawingDimension(d,second.id,{x:405,y:104})!;
  expect(changed.dimensions![1]!.offset).toBe(40);
  const scene=drawingDimensionScene({...d,drawingDocuments:changed});
  expect(scene[0]!.metadata?.dotEnd).toBe("true");expect(scene[1]!.metadata?.dotStart).toBe("true");
 });
 it("draws a path dimension through all bends",()=>{
  const d=physicalFixture(),docs=setPipeIntervalLength(d,"S0",0,2,250);
  const scene=drawingDimensionScene({...d,drawingDocuments:{...docs,showDimensions:true,dimensions:docs.dimensions!.map(x=>({...x,mode:"path"}))}});
  expect(scene[0]!.points!.length).toBeGreaterThan(4);
  const context=new Proxy({},{get:()=>vi.fn(),set:()=>true}) as CanvasRenderingContext2D;
  expect(()=>drawEditorSceneObject(context,scene[0]!,false,"drawing")).not.toThrow();
 });
});

describe("covering surfaces and editing",()=>{
 it("makes an overlay a short local sleeve at the picked point",()=>{
  const d=covered([sleeve({id:"base",spans:[{segmentId:"S0",from:0,to:1}]})]);
  const next=standardCoveringOver(d,d.physicalTopology!.coverings![0]! ,"Оплётка","overlay",{x:300,y:60});
  const span=next.spans[0]!;
  expect(span.from).toBeGreaterThan(0);expect(span.to).toBeLessThan(1);
  const route=coveringRoute(d,"S0")!;
  expect((span.to-span.from)*route.length).toBeGreaterThanOrEqual(99.99);
  expect((span.to-span.from)*route.length).toBeLessThanOrEqual(Math.max(route.length*.05,100)+1e-6);
 });
 it("creates a second covering from the existing span when the pipe is already covered",()=>{
  const d=covered([sleeve({id:"base",spans:[{segmentId:"S0",from:.2,to:.7,fromAnchor:0,toAnchor:1}]})]);
  const next=standardCoveringOver(d,d.physicalTopology!.coverings![0]! ,"Оплётка","overlay");
  expect(next.spans).toEqual([{segmentId:"S0",from:.2,to:.7,fromAnchor:0,toAnchor:1}]);
  expect(coveringScene({...d,physicalTopology:{...d.physicalTopology!,coverings:[...d.physicalTopology!.coverings!,next]}}).map(o=>o.id)).toEqual(["base","overlay"]);
 });
 it("is opaque, preserves layer order, and can be hit above the pipe",()=>{
  const d=covered([sleeve({id:"nylon",kind:"nylon"}),sleeve()]),scene=coveringScene(d);
  expect(scene.map(s=>s.id)).toEqual(["nylon","cover"]);
  const surface=coveringSurfaces(scene[1]!)[0]!,p=surface.path[0]!;
  expect(coveringHit(scene[1]!,{x:p.x+20,y:p.y},0)).toBe(0);
  expect(coveringSurfaces(scene[1]!)[0]!.polygon).not.toEqual(coveringSurfaces(scene[0]!)[0]!.polygon);
  expect(hitTestEditorScene(scene,[{id:"wires",label:"wires",visible:true,locked:false}],{x:p.x+20,y:p.y},1,"drawing")).toBe("cover");
  expect(coveringGrips(scene[1]!)).toHaveLength(2);
 });
 it("slides a sleeve without changing manual length and stretches either end with snapping",()=>{
  const d=covered([sleeve({lengthMode:"manual",lengthMm:70})]);
  const moved=moveCovering(d,"cover",0,"body",{x:240,y:60},{x:270,y:60})!;
  expect(moved.spans[0]!.from).toBeCloseTo(.2);expect(moved.spans[0]!.to).toBeCloseTo(.9);expect(moved.lengthMm).toBe(70);
  const anchored=moveCovering(d,"cover",0,"from",{x:210,y:60},{x:182,y:60})!;
  expect(anchored.spans[0]!.fromAnchor).toBe(0);expect(anchored.spans[0]!.from).toBe(0);
  const stretched=moveCovering(d,"cover",0,"to",{x:420,y:60},{x:478,y:60})!;
  expect(stretched.spans[0]!.toAnchor).toBe(1);
 });
 it("uses measured lengths only for fully bound automatic spans",()=>{
  let d=covered();d={...d,drawingDocuments:setPipeIntervalLength(d,"S0",0,1,150.125)};
  const bound=sleeve({spans:[{segmentId:"S0",from:0,to:1,fromAnchor:0,toAnchor:1}]});
  expect(coveringMeasuredLength(d,bound)).toBe(150.125);
  expect(coveringMeasuredLength(d,{...bound,lengthMode:"manual",lengthMm:80})).toBe(80);
  expect(coveringMeasuredLength(d,sleeve())).toBeNull();
 });
 it("shrinks around parallel wires on connector tails while nylon keeps its pipe width",()=>{
  const c=sleeve({width:0,spans:[{segmentId:"S0",from:-.1,to:.3}]}),d=covered([c]);
  const route=coveringRoute(d,"S0")!;expect(route.min).toBeLessThan(0);
  const exit=wireExitPath(d,"S0","from","W1",{x:118,y:60})!;expect(exit.length).toBeGreaterThanOrEqual(3);
  const heat=coveringGrips(coveringScene(d)[0]!);
  const nylon=coveringGrips(coveringScene(covered([{...c,kind:"nylon"}]))[0]!);
  expect(heat[0]!.halfWidth).toBeLessThan(heat[1]!.halfWidth);
  expect(nylon[0]!.halfWidth).toBeCloseTo(nylon[1]!.halfWidth);
  expect(wireExitPath(covered([{...c,kind:"nylon"}]),"S0","from","W1",{x:118,y:60})).toBeNull();
 });
});

describe("compact pipe and covering widths",()=>{
 it("makes an automatic thread bandage 2% wider than the same automatic base",()=>{
  const lower=sleeve({id:"lower",width:14,spans:[{segmentId:"S0",from:0,to:1}]}),
    plain=sleeve({id:"top",name:"Оплётка",kind:"braid",width:0,spans:[{segmentId:"S0",from:0,to:1}]}),
    band=sleeve({id:"top",name:"Нитевый бандаж",kind:"band",width:0,spans:[{segmentId:"S0",from:0,to:1}]}),
    plainScene=coveringScene(covered([lower,plain])),bandScene=coveringScene(covered([lower,band]));
  expect(bandScene.find(item=>item.id==="top")!.width).toBeCloseTo(plainScene.find(item=>item.id==="top")!.width*1.02,8);
  expect(coveringScene(covered([lower,sleeve({id:"explicit",name:"Нитевый бандаж",kind:"band",width:33,spans:[{segmentId:"S0",from:0,to:1}]})])).find(item=>item.id==="explicit")!.width).toBe(33);
 });
 it("fits mixed wire diameters tightly and follows changes in routed membership",()=>{
  const base=straight(),d={...base,connectors:base.connectors.map(c=>({...c,contacts:c.contacts.map((p,i)=>({...p,wireDiameterMm:i===0?1:2}))}))},s=d.physicalTopology!.segments[0]!;
  const lanes=segmentWireLanes(d,s.id),bundle=lanes.reduce((sum,l)=>sum+l.width,0)+(lanes.length-1)*.25;
  expect(drawingPipeWidth(d,s)).toBeCloseTo(bundle+.5);
  expect(drawingPipeWidth(d,{...s,width:.1})).toBeCloseTo(bundle+.5);
  expect(drawingPipeWidth(d,{...s,width:250.25})).toBe(250.25);
  const empty={...d,physicalTopology:{...d.physicalTopology!,routes:[]}};
  expect(drawingPipeWidth(empty,s)).toBe(.5);
  expect(drawingPipeWidth(empty,{...s,width:0})).toBe(.5);
 });
 it("keeps leader anchors on the rendered pipe and covering after width changes",()=>{
  const d=covered([sleeve({width:35.25})]);
  expect(drawingObjectPerimeter(d,"cover",{x:300,y:160})).toEqual({x:300,y:60+35.25/2});
  expect(drawingObjectPerimeter(d,"S0",{x:300,y:160})).toEqual({x:300,y:60+drawingPipeWidth(d,d.physicalTopology!.segments[0]!)/2});
  const compact=covered([sleeve({width:0})]);
  expect(drawingObjectPerimeter(compact,"cover",{x:300,y:160})!.y).toBeCloseTo(60+coveringScene(compact)[0]!.width/2);
 });
 it("encloses every lower surface and honours width edits at the current scale",()=>{
  const d=covered([sleeve({id:"a",width:0}),sleeve({id:"b",width:0}),sleeve({id:"c",width:0})]);
  const width=drawingPipeWidth(d,d.physicalTopology!.segments[0]!);
  expect(coveringScene(d).map(s=>s.width)).toEqual([width+.5,width+1,width+1.5]);
  const edited=covered([sleeve({width:35.25})]);
  expect(coveringScene(edited)[0]!.width).toBe(35.25);
  expect(coveringScene({...edited,drawingDocuments:{tables:[],leaders:[],bomOrder:[],physicalScale:2}})[0]!.width).toBe(70.5);
 });
 it.each([0,.01,3.5,250.25,10000000])("persists width %s in both pipe and covering",width=>{
  const d=covered([sleeve({width})]);
  const doc={...d,physicalTopology:{...d.physicalTopology!,segments:d.physicalTopology!.segments.map(s=>({...s,width}))}};
  expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(doc))).physicalTopology).toEqual(doc.physicalTopology);
 });
 it.each([-1,10000001,NaN,Infinity])("rejects invalid width %s on either object",width=>{
  const d=covered();
  expect(()=>parseHarnessDesignDocument({...d,physicalTopology:{...d.physicalTopology!,segments:d.physicalTopology!.segments.map(s=>({...s,width}))}})).toThrow();
  expect(()=>parseHarnessDesignDocument(covered([sleeve({width})]))).toThrow();
 });
});
