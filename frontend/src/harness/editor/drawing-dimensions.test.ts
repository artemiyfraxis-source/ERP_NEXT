// @vitest-environment node
import {buildHarnessSelectionIndex,resolveHarnessSelection} from "./harness-selection";
import { describe,it,expect } from "vitest";
import { physicalFixture } from "./physical-topology-fixture";
import { dimensionGeometry,dimensionRouteKey,dimensionWirePoints,segmentDimensionKey,pipeMeasuredWireLength,validateDrawingDimensions,setPipeIntervalLength,toggleDrawingDimensions,drawingDimensionScene } from "./drawing-dimensions";
import { applyEditorCommand } from "./commands";
import { createEditorHistory,executeEditorCommand,undoEditorCommand } from "./history";
import { calculateWireCutLength,parseHarnessDesignDocument } from "./model";
import { buildDrawingBom, emptyDrawingDocuments } from "./drawing-documents";
const fixture=()=>{const d=physicalFixture();return {...d,physicalTopology:undefined,wires:d.wires.map(w=>({...w,drawingRoute:[{x:300,y:100},{x:300,y:300}]}))};};
describe("bound dimensions",()=>{
 it("keeps aligned offsets parallel and vertical/horizontal projections axis aligned",()=>{
  const a={x:10,y:20},b={x:40,y:60},points=dimensionGeometry(a,b,"aligned",12);
  expect(points[2]!.x-points[1]!.x).toBeCloseTo(30);expect(points[2]!.y-points[1]!.y).toBeCloseTo(40);
  expect(dimensionGeometry(a,b,"horizontal",10).slice(1,3)).toEqual([{x:10,y:70},{x:40,y:70}]);
  expect(dimensionGeometry(a,b,"vertical",10).slice(1,3)).toEqual([{x:50,y:20},{x:50,y:60}]);
 });
 it("updates cutting length atomically, round trips, invalidates removed bends and undoes",()=>{
  const d=fixture(),wire=d.wires[0]!,base={wireId:wire.id,pointCount:4,routeKey:dimensionRouteKey(d,wire),mode:"aligned" as const,offset:40};
  const dimensions=[{...base,id:"dim1",from:0,to:1,lengthMm:100.125},{...base,id:"dim2",from:1,to:3,lengthMm:200.125}];
  const history=executeEditorCommand(createEditorHistory(d),{type:"set-drawing-documents",documents:{...emptyDrawingDocuments(),dimensions}});
  expect(history.present.wires[0]!.lengthMm).toBe(300.25);expect(calculateWireCutLength(history.present.wires[0]!).cutLengthMm).toBe(301);
  expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(history.present))).drawingDocuments?.dimensions).toEqual(dimensions);
  expect(undoEditorCommand(history).present).toBe(d);
  const moved=applyEditorCommand(history.present,{type:"set-wire-route",wireId:wire.id,route:[{x:320,y:80},{x:340,y:330}]});
  expect(moved.drawingDocuments?.dimensions).toEqual(dimensions);expect(moved.wires[0]!.lengthMm).toBe(300.25);
  const removed=applyEditorCommand(moved,{type:"set-wire-route",wireId:wire.id,route:[{x:320,y:80}]});
  expect(removed.drawingDocuments?.dimensions).toEqual([]);expect(removed.wires[0]!.lengthMm).toBeNull();
  expect(()=>applyEditorCommand(moved,{type:"update-wire",wireId:wire.id,lengthMm:4})).toThrow(/размерами/);
  expect(applyEditorCommand(moved,{type:"set-drawing-documents",documents:{...moved.drawingDocuments!,dimensions:[dimensions[0]!]}}).wires[0]!.lengthMm).toBeNull();
 });
 it("follows a physical path and invalidates replacement of its branch identity",()=>{
  const d=physicalFixture(),wire=d.wires[0]!,points=dimensionWirePoints(d,wire);
  const dimension={id:"physical-dim",wireId:wire.id,from:0,to:points.length-1,pointCount:points.length,routeKey:dimensionRouteKey(d,wire),mode:"horizontal" as const,offset:20,lengthMm:432.1};
  const measured=applyEditorCommand(d,{type:"set-drawing-documents",documents:{...emptyDrawingDocuments(),dimensions:[dimension]}});
  expect(measured.wires[0]!.lengthMm).toBe(432.1);
  const topology={...d.physicalTopology!,segments:d.physicalTopology!.segments.map(s=>s.id==="S0"?{...s,id:"replacement"}:s),routes:d.physicalTopology!.routes.map(r=>({...r,steps:r.steps.map(s=>s.segmentId==="S0"?{...s,segmentId:"replacement"}:s)}))};
  expect(applyEditorCommand(measured,{type:"set-physical-topology",topology}).wires[0]!.lengthMm).toBeNull();
  expect(()=>validateDrawingDimensions([{...dimension,from:"bad"}],d)).toThrow();

 });
 it("shares measured pipes, sums per route, preserves individual corrections and invalidates missing lengths",()=>{
  const d=physicalFixture();
  const dimensions=d.physicalTopology!.segments.map((segment,i)=>({id:`P${i}`,segmentId:segment.id,from:0,to:segment.path.points.length+1,pointCount:segment.path.points.length+2,routeKey:segmentDimensionKey(d,segment.id),mode:"aligned" as const,offset:30,lengthMm:[125.5,200,50][i]!}));
  const measured=applyEditorCommand(d,{type:"set-drawing-documents",documents:{...emptyDrawingDocuments(),dimensions}});
  expect(measured.wires.map(w=>w.lengthMm)).toEqual([325.5,175.5,250]);
  expect(resolveHarnessSelection(buildHarnessSelectionIndex(measured),["P0"]).wireIds).toEqual(["W1","W2"]);
  expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(measured))).drawingDocuments?.dimensions).toEqual(dimensions);
  const corrected=applyEditorCommand(measured,{type:"update-wire",wireId:"W1",endCorrectionFromMm:10,endCorrectionToMm:2.5});
  expect(calculateWireCutLength(corrected.wires[0]!).cutLengthMm).toBe(338);
  expect(corrected.wires[1]!.lengthMm).toBe(175.5);
  const partial=applyEditorCommand(corrected,{type:"set-drawing-documents",documents:{...corrected.drawingDocuments!,dimensions:dimensions.slice(0,2)}});
  expect(partial.wires.map(w=>w.lengthMm)).toEqual([325.5,null,null]);
  const moved=applyEditorCommand(measured,{type:"set-physical-topology",topology:{...measured.physicalTopology!,snap:false,segments:measured.physicalTopology!.segments.map(s=>({...s,path: { kind: "routed" as const, points: s.path.points.map(p=>({x:p.x+20,y:p.y+10})) }}))}});
  expect(moved.wires.map(w=>w.lengthMm)).toEqual([325.5,175.5,250]);
  const changed=applyEditorCommand(moved,{type:"set-physical-topology",topology:{...moved.physicalTopology!,segments:moved.physicalTopology!.segments.map(s=>s.id==="S0"?{...s,path: { kind: "routed" as const, points: [] }}:s)}});
  expect(changed.wires.map(w=>w.lengthMm)).toEqual([325.5,175.5,250]);
  expect(changed.drawingDocuments!.dimensions![0]).toMatchObject({from:0,to:1,pointCount:2,lengthMm:125.5});
  expect(()=>applyEditorCommand(measured,{type:"update-wire",wireId:"W1",lengthMm:1})).toThrow(/размерами/);
  expect(pipeMeasuredWireLength(measured,"W1").managed).toBe(true);
 });
});

it("keeps endpoint length and undo when a bend is deleted and auxiliary dimensions never affect cutting",()=>{
 const d=physicalFixture(),docs=setPipeIntervalLength(d,"S0",0,2,125.5);
 const total=docs.dimensions![0]!;
 const before=applyEditorCommand(d,{type:"set-drawing-documents",documents:{...docs,dimensionMode:"vertical",showDimensions:true,dimensions:[total,{...total,id:"aux",auxiliary:true,lengthMm:999}]}});
 expect(pipeMeasuredWireLength(before,"W1").managed).toBe(true);
 const h=executeEditorCommand(createEditorHistory(before),{type:"remove-physical-bend",segmentId:"S0",index:0});
 expect(h.present.drawingDocuments!.dimensions).toHaveLength(2);
 expect(h.present.drawingDocuments!.dimensions![0]).toMatchObject({to:1,pointCount:2,lengthMm:125.5});
 expect(undoEditorCommand(h).present).toBe(before);
 expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(h.present))).drawingDocuments).toEqual(h.present.drawingDocuments);
 const auxiliaryOnly=applyEditorCommand(d,{type:"set-drawing-documents",documents:{...docs,dimensions:[{...total,auxiliary:true,lengthMm:999}]}});
 expect(auxiliaryOnly.wires).toEqual(d.wires);expect(pipeMeasuredWireLength(auxiliaryOnly,"W1").managed).toBe(false);
 const scene=drawingDimensionScene(before);expect(scene[0]!.metadata!.dimensionMode).toBe("vertical");
 const overridden={...before,drawingDocuments:{...before.drawingDocuments!,dimensions:before.drawingDocuments!.dimensions!.map(x=>({...x,mode:"horizontal" as const,modeOverride:true}))}};
 expect(drawingDimensionScene(overridden)[0]!.metadata!.dimensionMode).toBe("horizontal");
});
it("creates one endpoint measurement per pipe independently of bend count",()=>{
 const d=physicalFixture(),docs=toggleDrawingDimensions(d);
 expect(docs.dimensions!.filter(x=>x.segmentId)).toHaveLength(d.physicalTopology!.segments.length);
 expect(docs.dimensions!.every(x=>x.from===0&&x.to===x.pointCount-1)).toBe(true);
});

it("auxiliary wire dimensions allow manual manufacturing lengths",()=>{
 const d=fixture(),wire=d.wires[0]!,dimension={id:"aux",wireId:wire.id,from:0,to:3,pointCount:4,routeKey:dimensionRouteKey(d,wire),mode:"aligned" as const,offset:40,lengthMm:999,auxiliary:true};
 const measured=applyEditorCommand(d,{type:"set-drawing-documents",documents:{...emptyDrawingDocuments(),dimensions:[dimension]}});
 expect(applyEditorCommand(measured,{type:"update-wire",wireId:wire.id,lengthMm:42}).wires[0]!.lengthMm).toBe(42);
});
it("uses a changed drawing dimension as the material consumption length",()=>{
 const source=fixture(),wire=source.wires[0]!,binding={sourceId:"source",snapshotId:"00000000-0000-4000-8000-000000000001",snapshotSha256:"a".repeat(64),recordId:"b".repeat(64),entityType:"wire" as const,sourceKey:"MAT",displayName:"Провод"};
 const document={...source,wires:source.wires.map(item=>item.id===wire.id?{...item,materialBinding:binding}:item)};
 const dimension={id:"length",wireId:wire.id,from:0,to:3,pointCount:4,routeKey:dimensionRouteKey(document,wire),mode:"aligned" as const,offset:40,lengthMm:1234};
 const measured=applyEditorCommand(document,{type:"set-drawing-documents",documents:{...emptyDrawingDocuments(),dimensions:[dimension]}});
 expect(measured.wires[0]!.lengthMm).toBe(1234);
 const material=buildDrawingBom(measured).find(row=>row.objectIds.includes(wire.id))!;
 expect(material.amount).toBe(1.234);
 expect(material.unit).toBe("м");
});
it("combines legacy partial lengths when their common bend is removed and undo restores both",()=>{
 const d=physicalFixture();let docs=setPipeIntervalLength(d,"S0",0,1,100);docs=setPipeIntervalLength({...d,drawingDocuments:docs},"S0",1,2,200);
 const before=applyEditorCommand(d,{type:"set-drawing-documents",documents:docs});
 const h=executeEditorCommand(createEditorHistory(before),{type:"remove-physical-bend",segmentId:"S0",index:0});
 expect(h.present.drawingDocuments!.dimensions).toHaveLength(1);expect(h.present.drawingDocuments!.dimensions![0]).toMatchObject({from:0,to:1,lengthMm:300});
 expect(undoEditorCommand(h).present).toBe(before);
});
