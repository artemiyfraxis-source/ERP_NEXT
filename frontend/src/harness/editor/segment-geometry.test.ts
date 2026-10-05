// @vitest-environment node
import {expect,it} from "vitest";
import {intersectSegments,segmentEntersRect,segmentPointDistance,parallelSegmentGap} from "./segment-geometry";
import {validateE4Route,routeE4Wire} from "./e4-router";
import {getE4WireCrossings,getE4BridgeGeometry,hitTestWireSegment} from "./CanvasViewport";
import type {EditorSceneObject} from "./editor-types";
const diagonal={start:{x:0,y:0},end:{x:100,y:100}};
it("handles oblique intersections, gaps, endpoints and overlaps",()=>{
 expect(intersectSegments(diagonal,{start:{x:0,y:100},end:{x:100,y:0}})).toEqual({kind:"point",point:{x:50,y:50}});
 expect(intersectSegments(diagonal,{start:{x:50,y:50},end:{x:150,y:150}})).toEqual({kind:"overlap"});
 expect(intersectSegments(diagonal,{start:{x:100,y:100},end:{x:150,y:150}})).toEqual({kind:"point",point:{x:100,y:100}});
 expect(segmentPointDistance({x:50,y:60},diagonal)).toBeCloseTo(Math.sqrt(50));
 expect(parallelSegmentGap(diagonal,{start:{x:0,y:10},end:{x:100,y:110}})).toBeCloseTo(Math.sqrt(50));
 expect(segmentEntersRect(diagonal,{left:20,right:40,top:20,bottom:40})).toBe(true);
 expect(segmentEntersRect(diagonal,{left:20,right:40,top:50,bottom:80})).toBe(false);
});
it("validates diagonal paths against rectangles, parallel clearance and self intersections",()=>{
 const request={start:{position:diagonal.start,leadDirection:null},end:{position:diagonal.end,leadDirection:null}};
 expect(()=>validateE4Route([diagonal.start,diagonal.end],request)).not.toThrow();
 expect(()=>validateE4Route([diagonal.start,diagonal.end],{...request,obstacles:[{x:30,y:20,width:20,height:40}]})).toThrow("препятствие");
 expect(()=>validateE4Route([diagonal.start,diagonal.end],{...request,occupiedRoutes:[{points:[{x:0,y:10},{x:100,y:110}]}]})).toThrow("зазор");
 expect(()=>validateE4Route([{x:0,y:0},{x:100,y:100},{x:0,y:100},{x:100,y:0}],{...request,end:{position:{x:100,y:0},leadDirection:null}})).toThrow("сам себя");
});
it("routes automatic wires while treating oblique manual wires as occupied geometry",()=>{
 const request={start:{position:{x:0,y:60},leadDirection:null},end:{position:{x:100,y:60},leadDirection:null},occupiedRoutes:[{points:[{x:20,y:20},{x:80,y:80}]}]};
 const result=routeE4Wire(request);
 expect(()=>validateE4Route(result.points,request)).not.toThrow();
 expect(result.points[0]).toEqual(request.start.position);
 expect(result.points.at(-1)).toEqual(request.end.position);
});
it("renders diagonal crossings in the same coordinates as collision geometry",()=>{
 const wire=(id:string,points:readonly {x:number;y:number}[]):EditorSceneObject=>({id,kind:"wire",label:id,layerId:"wires",points,x:0,y:0,width:0,height:0,color:"#333"});
 const scene=[wire("a",[diagonal.start,diagonal.end]),wire("b",[{x:0,y:100},{x:100,y:0}])];
 const layers=[{id:"wires",label:"Провода",visible:true,locked:false}];
 const crossings=getE4WireCrossings(scene,layers);
 expect(crossings).toHaveLength(1);expect(crossings[0]!.point).toEqual({x:50,y:50});
 expect(hitTestWireSegment(scene,layers,{x:29,y:31},1,"b")?.point).toEqual({x:30,y:30});
 const bridge=getE4BridgeGeometry(crossings[0]!);
 expect(segmentPointDistance(bridge.arcStart,{start:scene[1]!.points![0]!,end:scene[1]!.points![1]!})).toBeCloseTo(0);
 expect(getE4WireCrossings(scene,layers,[{id:"j",position:{x:50,y:50},wireIds:["a","b"]}])).toEqual([]);
});
