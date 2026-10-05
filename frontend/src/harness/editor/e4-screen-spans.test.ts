// @vitest-environment node
import {expect,it} from "vitest";
import {screenCrossSections,screenSectionAt} from "./e4-screen-spans";
import {getE4ScreenLayout} from "./CanvasViewport";
import type {EditorSceneObject} from "./editor-types";
import {createEmptyHarnessDesign,createJunctionEndpoint,wireScreenConnectionGeometry,wireEndpointE4Anchor} from "./model";
import {createWire} from "./commands";

it("sweeps sloping wires at the actual X without inflating the screen to their complete bounding box",()=>{
 const paths=[{id:"a",points:[{x:0,y:0},{x:200,y:100}]},{id:"b",points:[{x:0,y:40},{x:200,y:140}]}];
 const spans=screenCrossSections(paths);
 expect(spans).toHaveLength(1);
 expect(screenSectionAt(spans[0]!,50)).toEqual({crossMinimum:25,crossMaximum:65});
 const objects:EditorSceneObject[]=paths.map(p=>({...p,kind:"wire",layerId:"wires",label:p.id,x:0,y:0,width:0,height:0,color:"red"}));
 for(const position of [0,.25,.5,.75,1]){
  const layout=getE4ScreenLayout({id:"s",wireIds:["a","b"],position,width:20,label:"S"},objects)!;
  expect(layout.center).toEqual({x:200*position,y:100*position+20});
  expect(layout.crossSize).toBe(58);
  expect(layout.orientation).toBe("horizontal");
 }
});

it("includes all crossed legs and retains a perpendicular fallback for a pure vertical bundle",()=>{
 const paths=[{id:"a",points:[{x:0,y:0},{x:100,y:100},{x:0,y:200}]}];
 expect(screenSectionAt(screenCrossSections(paths)[0]!,50)).toEqual({crossMinimum:50,crossMaximum:150});
 const vertical=screenCrossSections([{id:"a",points:[{x:10,y:0},{x:10,y:200}]}]);
 expect(vertical[0]!.orientation).toBe("vertical");
 expect(screenSectionAt(vertical[0]!,80)).toEqual({crossMinimum:10,crossMaximum:10});
});

it("keeps a vertical body and top/bottom ports while moving along a vertical bundle",()=>{
 const objects:EditorSceneObject[]=[100,140].map((x,i)=>({id:String(i),kind:"wire",layerId:"wires",label:String(i),x:0,y:0,width:0,height:0,color:"red",points:[{x,y:0},{x,y:400}]}));
 for(const position of [.1,.5,.9]){
   const layout=getE4ScreenLayout({id:"s",wireIds:["0","1"],position,width:32,label:"S",terminalSide:"both"},objects)!;
   expect(layout.center).toEqual({x:120,y:400*position});
   expect(layout.span.orientation).toBe("vertical");
   expect(layout.orientation).toBe("horizontal"); // renderer's long Y axis
   expect(layout.alongSize).toBe(58);
   expect(layout.crossSize).toBeGreaterThan(layout.alongSize);
   expect(layout.terminals[0]!.connectionPoint).toEqual({x:120,y:400*position-layout.crossSize/2});
   expect(layout.terminals[1]!.connectionPoint.y).toBeGreaterThan(layout.center.y);
 }
});

it("keeps model and canvas ports equal for a vertical junction-to-junction route",()=>{
 const from=createJunctionEndpoint("a"),to=createJunctionEndpoint("b");
 const d={...createEmptyHarnessDesign(),junctions:[{id:"a",position:{x:100,y:0},wireIds:["w1","w2"]},{id:"b",position:{x:100,y:400},wireIds:["w1","w2"]}],wires:[createWire("w1",from,to),createWire("w2",from,to)],screens:[{id:"s",wireIds:["w1","w2"],label:"SH",position:.5,width:32,terminalSide:"both" as const}]};
 const geometry=wireScreenConnectionGeometry(d,"s")!;
 const objects:EditorSceneObject[]=d.wires.map(w=>({id:w.id,kind:"wire",layerId:"wires",label:w.id,x:0,y:0,width:0,height:0,color:"red",points:[{x:100,y:0},{x:100,y:400}]}));
 const layout=getE4ScreenLayout(d.screens[0]!,objects)!;
 expect(layout.center).toEqual(geometry.center);
 expect(layout.terminals).toEqual(geometry.terminals);
 expect(wireEndpointE4Anchor(d,{connectorId:"",contactId:"",screenId:"s",screenTerminalSide:"above"})!.leadDirection).toBe("up");
});

it("reserves the full vertical body's height when avoiding a table",()=>{
 const objects:EditorSceneObject[]=[100,140].map((x,i)=>({id:String(i),kind:"wire",layerId:"wires",label:String(i),x:0,y:0,width:0,height:0,color:"red",points:[{x,y:0},{x,y:400}]}));
 objects.push({id:"table",kind:"connector",layerId:"connectors",label:"X",x:80,y:160,width:80,height:80,color:"black"});
 for(const position of [.1,.49,.5,.51,.9]){
  const layout=getE4ScreenLayout({id:"s",wireIds:["0","1"],position,width:32,label:"S"},objects)!;
  expect(layout.center.y+layout.crossSize/2<160||layout.center.y-layout.crossSize/2>240).toBe(true);
 }
});
