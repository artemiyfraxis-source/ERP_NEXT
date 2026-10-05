// @vitest-environment node
import { expect, it } from "vitest";
import { orderPhysicalScene, physicalSceneStackOrder, physicalTopologyScene, pipeSceneControls, pipeSceneHandles, pipeSceneWireIds } from "./physical-scene";
import { physicalFixture } from "./physical-topology-fixture";
import { physicalSegmentPoints, physicalSegmentControls } from "./physical-geometry";
import { physicalNodeFacingDirection } from "./physical-ports";
import { hitTestWireRoutePoint, objectsInPaintOrder } from "./CanvasViewport";
import { defaultLayerIds, parseHarnessDesignDocument } from "./model";

it("carries author controls, rendered route and wire identity independently without metadata serialization",()=>{
 const doc=physicalFixture(),before=JSON.stringify(doc),scene=physicalTopologyScene(doc);
 for(const segment of doc.physicalTopology!.segments){
  const pipe=scene.find(o=>o.id===segment.id)!;
  expect(pipe.points).toEqual(physicalSegmentPoints(doc,segment));
  expect(pipeSceneControls(pipe)).toEqual(physicalSegmentControls(doc,segment));
  expect(pipeSceneHandles(pipe)).toEqual(segment.path.points.length?segment.path.points:pipe.points!.slice(1,-1));
  expect(pipe.metadata?.opacity).toBe("0.72");
  for(const [i,p] of segment.path.points.entries())expect(hitTestWireRoutePoint(pipe,p,100)).toBe(i);
  if(!segment.path.points.length)for(const [i,p] of pipe.points!.slice(1,-1).entries())expect(hitTestWireRoutePoint(pipe,p,100)).toBe(i);
 }
 expect(pipeSceneWireIds(scene.find(o=>o.id==="S0"))).toEqual(["W1","W2"]);
 expect(JSON.stringify(doc)).toBe(before);
});
it("keeps physical connection points on a dedicated top layer and distinguishes exits",()=>{
 const doc=physicalFixture(),scene=physicalTopologyScene(doc);
 const exit=scene.find(o=>o.id==="NA")!,junction=scene.find(o=>o.id==="J")!;
 expect(exit.layerId).toBe(defaultLayerIds.connectionPoints);
 expect(junction.layerId).toBe(defaultLayerIds.connectionPoints);
 expect(exit.color).toBe("#f59e0b");
 expect(junction.color).toBe("#1179ac");
 expect(exit.metadata?.nodeRole).toBe("connector-exit");
 expect(junction.metadata?.nodeRole).toBe("junction");
 const painted=objectsInPaintOrder(scene,doc.views.drawing.layers.map(layer=>({...layer,label:layer.name})));
 expect(painted.findIndex(o=>o.id==="NA")).toBeGreaterThan(painted.findIndex(o=>o.id==="S0"));
 const manuallyReordered=objectsInPaintOrder(scene,[...doc.views.drawing.layers].reverse().map(layer=>({...layer,label:layer.name})));
 expect(manuallyReordered.at(-1)?.id).toBe("J");
 expect(manuallyReordered.findIndex(o=>o.id==="NA")).toBeGreaterThan(manuallyReordered.findIndex(o=>o.id==="S0"));
});
it("migrates legacy view layers with a visible connection-point layer",()=>{
 const doc=physicalFixture();
 const legacy={...doc,views:{...doc.views,e4:{...doc.views.e4,layers:doc.views.e4.layers.filter(layer=>layer.id!==defaultLayerIds.connectionPoints)},drawing:{...doc.views.drawing,layers:doc.views.drawing.layers.filter(layer=>layer.id!==defaultLayerIds.connectionPoints)}}};
 const parsed=parseHarnessDesignDocument(legacy);
 expect(parsed.views.e4.layers.find(layer=>layer.id===defaultLayerIds.connectionPoints)).toMatchObject({visible:true,locked:false});
 expect(parsed.views.drawing.layers.find(layer=>layer.id===defaultLayerIds.connectionPoints)).toMatchObject({visible:true,locked:false});
 const highOrder={...legacy,views:{...legacy.views,drawing:{...legacy.views.drawing,layers:legacy.views.drawing.layers.map((layer,i)=>({...layer,order:10000-i}))}}};
 const migrated=parseHarnessDesignDocument(highOrder);
 expect(()=>parseHarnessDesignDocument(JSON.parse(JSON.stringify(migrated)))).not.toThrow();
});
it("resolves typed port directions at the same scene boundary",()=>{
 const base=physicalFixture(),doc={...base,physicalTopology:{...base.physicalTopology!,nodes:base.physicalTopology!.nodes.map(n=>({...n,direction:"up" as const}))}};
 const scene=physicalTopologyScene(doc);
 for(const node of doc.physicalTopology.nodes){
  const object=scene.find(o=>o.id===node.id)!;
  expect(object.port).toEqual({connectorId:node.connectorId,direction:physicalNodeFacingDirection(doc,node)});
  expect(object.metadata?.nodeRole).toBe(node.connectorId ? "connector-exit" : "junction");
 }
});

it("orders drawing objects from wires to pipes to common pipes and nested common pipes",()=>{
 const doc=physicalFixture(),inner={id:"op-inner",kind:"physical-segment" as const,layerId:"wires",label:"ОП1",x:0,y:0,width:0,height:0,color:"#000000",pipe:{role:"joining-pipe" as const,handles:[],controls:[],wireIds:[]}},outer={id:"op-outer",kind:"physical-segment" as const,layerId:"wires",label:"ОП2",x:0,y:0,width:0,height:0,color:"#000000",pipe:{role:"joining-pipe" as const,handles:[],controls:[],wireIds:[]}},pipe={id:"S0",kind:"physical-segment" as const,layerId:"wires",label:"П",x:0,y:0,width:0,height:0,color:"#000000",pipe:{handles:[],controls:[],wireIds:[]}},wire={id:"W1",kind:"wire" as const,layerId:"wires",label:"Провод",x:0,y:0,width:0,height:0,color:"#000000"},cover={id:"cover",kind:"physical-covering" as const,layerId:"wires",label:"Оболочка",x:0,y:0,width:0,height:0,color:"#000000"};
 const topology={...doc.physicalTopology!,joiningPipes:[{id:"op-inner",start:{x:0,y:0},end:{x:10,y:0},path:{kind:"polyline" as const,points:[]},members:[{segmentIds:["S0"],from:0,to:1,reverse:false}],mode:"flat" as const},{id:"op-outer",start:{x:0,y:0},end:{x:10,y:0},path:{kind:"polyline" as const,points:[]},members:[{segmentIds:["op-inner"],from:0,to:1,reverse:false}],mode:"flat" as const}],coverings:[{id:"cover",name:"Оболочка",width:10,color:"#000000",lengthMm:null,spans:[{segmentId:"S0",from:0,to:1}]}]};
 const layered={...doc,physicalTopology:topology},ordered=orderPhysicalScene(layered,[outer,pipe,wire,inner,cover]);
 expect(ordered.map(object=>object.id)).toEqual(["W1","S0","cover","op-inner","op-outer"]);
 expect(physicalSceneStackOrder(layered,outer)).toBeGreaterThan(physicalSceneStackOrder(layered,inner));
});
