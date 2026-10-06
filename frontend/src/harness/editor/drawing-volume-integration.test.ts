// @vitest-environment node
import {expect,it} from "vitest";
import {drawCableSheaths,drawEditorSceneObject,getVisibleCableSheathScene} from "./CanvasViewport";
import type {EditorSceneObject} from "./editor-types";

function recorder() {
 const fills:{color:string;path:number[][]}[]=[],strokes:{color:string;path:number[][]}[]=[];
 let path:number[][]=[];
 const stack:{fillStyle:string;strokeStyle:string;lineWidth:number}[]=[];
 const state={fillStyle:"",strokeStyle:"",lineWidth:1,
  save(){stack.push({fillStyle:this.fillStyle,strokeStyle:this.strokeStyle,lineWidth:this.lineWidth});},
  restore(){Object.assign(this,stack.pop());},beginPath(){path=[];},
  moveTo(x:number,y:number){path.push([x,y]);},lineTo(x:number,y:number){path.push([x,y]);},
  closePath(){},clip(){},setLineDash(){},arc(){},fillText(){},
  fill(){fills.push({color:this.fillStyle,path:[...path]});},
  stroke(){strokes.push({color:this.strokeStyle,path:[...path]});},
 };
 return {context:state as unknown as CanvasRenderingContext2D,fills,strokes};
}
const profile={sourceId:"technology-coax-terminations",snapshotId:"38d9aa91-b8d4-45d8-8e39-da14ee4effad",
 snapshotSha256:"a".repeat(64),recordId:"b".repeat(64),entityType:"coax-termination" as const,
 sourceKey:"test",displayName:"test",layers:[{index:1,diameterMm:1,stripLengthMm:4},{index:2,diameterMm:4,stripLengthMm:8}]};
const wire:EditorSceneObject={id:"w",kind:"wire",layerId:"wires",label:"",x:0,y:0,width:0,height:0,
 color:"#8899aa",points:[{x:0,y:0},{x:200,y:0}],stripProfiles:{from:profile,to:profile}};

it.each([false,true])("shades strip layers on %s multi-path wire without changing outlines",multi=>{
 const object=multi?{...wire,paths:[wire.points!]}:wire;
 const flat=recorder(),lit=recorder();
 drawEditorSceneObject(flat.context,{...object,metadata:{volumeShading:"false"}},false,"drawing");
 drawEditorSceneObject(lit.context,{...object,metadata:{volumeShading:"true"}},false,"drawing");
 expect(flat.fills.map(f=>f.color)).toEqual(["#d6ad65",wire.color,"#d6ad65",wire.color]);
 expect(lit.fills.filter(f=>f.color==="rgba(0,0,0,.25)")).toHaveLength(4);
 expect(lit.strokes.filter(s=>s.color==="#344b59")).toEqual(flat.strokes.filter(s=>s.color==="#344b59"));
 const e4=recorder();drawEditorSceneObject(e4.context,{...object,metadata:{volumeShading:"true"}},false,"e4");
 expect(e4.fills).toEqual([]);
});

it.each([0,Math.PI/2,Math.PI/4])("shades cable width locally at angle %s and preserves the outer selected contour",angle=>{
 const rotate=(x:number,y:number)=>({x:x*Math.cos(angle)-y*Math.sin(angle),y:x*Math.sin(angle)+y*Math.cos(angle)});
 const members=[{...wire,id:"a",points:[rotate(0,0),rotate(200,0)]},
  {...wire,id:"b",points:[rotate(0,10),rotate(200,10)]}];
 const cable={id:"c",memberWireIds:["a","b"],lengthMm:200,endCorrectionFromMm:0,endCorrectionToMm:0,cutRoundingStepMm:1};
 const scene=getVisibleCableSheathScene([cable],members,[{id:"wires",label:"",visible:true,locked:false}]);
 const flat=recorder(),lit=recorder(),selected=new Set(["a","b"]);
 drawCableSheaths(flat.context,scene.geometries,selected,2);
 drawCableSheaths(lit.context,scene.geometries,selected,2,new Set(["a","b"]));
 expect(lit.fills).toHaveLength(9);expect(flat.fills).toHaveLength(1);
 expect(lit.strokes).toEqual(flat.strokes);
 const outer=lit.fills[1]!.path,inner=lit.fills.at(-1)!.path;
 const width=(p:number[][])=>Math.hypot(p[0]![0]!-p[3]![0]!,p[0]![1]!-p[3]![1]!);
 expect(width(inner)/width(outer)).toBeCloseTo(.18);
});
