// @vitest-environment node
import {expect,it,vi} from "vitest";
import {physicalFixture} from "./physical-topology-fixture";
import {drawingPipeWidth,segmentWireProjection} from "./drawing-thickness";
import {physicalWireDisplay} from "./physical-wire-geometry";
import {hitTestEditorScene,redrawCanvas} from "./CanvasViewport";
import {parseHarnessDesignDocument} from "./model";
import {createEditorHistory,executeEditorCommand,undoEditorCommand} from "./history";
import type {EditorLayer,EditorSceneObject} from "./editor-types";

const section=(count:number,mode:"flat"|"round")=>{
 const base=physicalFixture(),wire=base.wires[0]!;
 return {...base,wires:Array.from({length:count},(_,i)=>({...wire,id:`W${i+1}`,color:i%2?"#aa0000":"#0000aa"})),physicalTopology:{...base.physicalTopology!,segments:base.physicalTopology!.segments.map(s=>s.id==="S0"?{...s,mode}:s),routes:Array.from({length:count},(_,i)=>({wireId:`W${i+1}`,steps:[{segmentId:"S0",reverse:false}]}))}};
};

it("packs twenty insulated conductors as tangent circles and projects first-hit strips",()=>{
 const flat=section(20,"flat"),round=section(20,"round"),projection=segmentWireProjection(round,"S0");
 expect(projection.mode).toBe("round");
 expect(drawingPipeWidth(round,round.physicalTopology.segments[0]!)).toBeLessThan(drawingPipeWidth(flat,flat.physicalTopology.segments[0]!)/2);
 for(let i=0;i<projection.lanes.length;i++)for(let j=i+1;j<projection.lanes.length;j++){
  const a=projection.lanes[i]!,b=projection.lanes[j]!;
  expect(Math.hypot(a.offset-b.offset,(a.depth??0)-(b.depth??0))).toBeGreaterThanOrEqual((a.width+b.width)/2-1e-6);
 }
 for(const strip of projection.strips){
  const hits=projection.lanes.flatMap(lane=>{
   const delta=strip.offset-lane.offset,r=lane.width/2;
   return Math.abs(delta)<=r?[{id:lane.id,surface:(lane.depth??0)-Math.sqrt(r*r-delta*delta)}]:[];
  }).sort((a,b)=>a.surface-b.surface);
  expect(hits[0]?.id).toBe(strip.id);
 }
 expect(projection.strips.length).toBeGreaterThan(2);
});

it("round mode survives JSON storage and undo keeps the previous layout",()=>{
 const original=physicalFixture();
 const changed=executeEditorCommand(createEditorHistory(original),{type:"set-physical-topology",topology:{...original.physicalTopology!,segments:original.physicalTopology!.segments.map(s=>s.id==="S0"?{...s,mode:"round" as const}:s)}});
 expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(changed.present))).physicalTopology?.segments[0]?.mode).toBe("round");
 expect(undoEditorCommand(changed).present).toEqual(original);
});

it("retains a selection route through a pipe whose conductors are normally hidden",()=>{
 const document=section(3,"round");
 const hidden={...document,physicalTopology:{...document.physicalTopology,segments:document.physicalTopology.segments.map(s=>s.id==="S0"?{...s,showWires:false}:s)}};
 const display=physicalWireDisplay(hidden,"W1",{x:0,y:0},{x:600,y:600})!;
 expect(display.visibleStrokes.every(stroke=>stroke.points.length<display.selectionPaths[0]!.length)).toBe(true);
 expect(display.selectionPaths[0]!.length).toBeGreaterThan(display.paths.flat().length);
});

it("keeps the complete route for selection while clicking only a visible conductor",()=>{
 const document=section(3,"round"),projection=segmentWireProjection(document,"S0");
 const strip=projection.strips.find(s=>s.width>.1&&s.width<projection.lanes.find(l=>l.id===s.id)!.width)!;
 const display=physicalWireDisplay(document,strip.id,{x:0,y:0},{x:600,y:600})!;
 expect(display.paths.length).toBeGreaterThan(0);
 expect(display.visibleStrokes.some(s=>s.width<projection.lanes.find(l=>l.id===strip.id)!.width)).toBe(true);
 const stroke=display.visibleStrokes.find(s=>s.width===strip.width)!;
 const point=stroke.points[Math.floor(stroke.points.length/2)]!;
 const layers:EditorLayer[]=[{id:"wires",label:"Wires",visible:true,locked:false}];
 const wire:EditorSceneObject={id:strip.id,kind:"wire",layerId:"wires",label:"",x:0,y:0,width:0,height:0,color:"#aa0000",points:display.paths[0],paths:display.paths,visibleWireStrokes:display.visibleStrokes,metadata:{physicalRoute:"true",drawingWidth:"2.5"}};
 const pipe:EditorSceneObject={id:"S0",kind:"physical-segment",layerId:"wires",label:"",x:0,y:0,width:20,height:0,color:"#888888",points:stroke.points,paths:[stroke.points],pipe:{role:"pipe",controls:[],handles:[],wireIds:[strip.id]}};
 expect(hitTestEditorScene([wire,pipe],layers,point,10,"drawing")).toBe("S0");
});

it("draws a selected hidden route above the pipe and sheath until selection clears",()=>{
 vi.stubGlobal("window",{devicePixelRatio:1});
 const strokes:string[]=[];const state:Record<string,unknown>={};
 const context=new Proxy(state,{get(target,key:string){if(key in target)return target[key];return (..._args:unknown[])=>{if(key==="stroke")strokes.push(String(target.strokeStyle));};}}) as unknown as CanvasRenderingContext2D;
 const canvas={width:300,height:200,clientWidth:300,clientHeight:200,getContext:()=>context} as unknown as HTMLCanvasElement;
 const layers:EditorLayer[]=[{id:"wires",label:"Wires",visible:true,locked:false}];
 const points=[{x:20,y:50},{x:200,y:50}];
 const wire:EditorSceneObject={id:"W",kind:"wire",layerId:"wires",label:"",x:0,y:0,width:0,height:0,color:"#aa0000",points,paths:[points],visibleWireStrokes:[],metadata:{drawingWidth:"2.5",physicalRoute:"true"}};
 const pipe:EditorSceneObject={id:"P",kind:"physical-segment",layerId:"wires",label:"",x:0,y:0,width:12,height:0,color:"#999999",points,pipe:{role:"pipe",controls:[],handles:[],wireIds:["W"]}};
 redrawCanvas(canvas,"drawing",{offsetX:0,offsetY:0,zoom:1},[wire,pipe],layers,new Set(),[],undefined,undefined,[],undefined,undefined,[],null,["W"]);
 expect(strokes.lastIndexOf("#aa0000")).toBeGreaterThan(strokes.lastIndexOf("#999999"));
 strokes.length=0;
 redrawCanvas(canvas,"drawing",{offsetX:0,offsetY:0,zoom:1},[wire,pipe],layers,new Set());
 expect(strokes).not.toContain("#aa0000");
 vi.unstubAllGlobals();
});
