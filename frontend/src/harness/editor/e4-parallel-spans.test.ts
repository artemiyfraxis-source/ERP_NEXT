// @vitest-environment node
import {expect,it,vi} from "vitest";
import {commonParallelSpan,commonHorizontalPairSpan,parallelSpanLocal,parallelSpanWorld} from "./e4-parallel-spans";
import {drawE4DifferentialPairs,getE4DifferentialPairLayout,hitTestE4DifferentialPair} from "./CanvasViewport";
import type {EditorSceneObject} from "./editor-types";
import {createConnector,createWire,applyEditorCommand} from "./commands";
import {createEmptyHarnessDesign,parseHarnessDesignDocument} from "./model";

const paths=[{id:"a",points:[{x:100,y:100},{x:500,y:300}]},{id:"b",points:[{x:100,y:140},{x:500,y:340}]}];
const objects:EditorSceneObject[]=paths.map(p=>({...p,kind:"wire",layerId:"wires",label:p.id,x:0,y:0,width:0,height:0,color:p.id==="a"?"#f00":"#00f"}));
const pair={id:"p",wireIds:["a","b"] as const,step:20,amplitude:4,variant:2 as const};

it("finds the shared oblique interval, rejects nonparallel paths and round-trips coordinates",()=>{
 const span=commonParallelSpan(paths)!;
 expect(span.direction).toEqual({x:2/Math.sqrt(5),y:1/Math.sqrt(5)});
 expect(span.crossMaximum-span.crossMinimum).toBeCloseTo(80/Math.sqrt(5));
 const point={x:250,y:360},local=parallelSpanLocal(span,point),back=parallelSpanWorld(span,local.x,local.y);
 expect(back.x).toBeCloseTo(point.x);expect(back.y).toBeCloseTo(point.y);
 expect(commonParallelSpan([paths[0]!,{id:"b",points:[{x:0,y:0},{x:50,y:100}]}])).toBeNull();
 expect(commonParallelSpan(paths.map(p=>({...p,points:[...p.points].reverse()})))!.firstWireDirection).toBe(-1);
});

it("C7 chooses the longest horizontal overlap even when vertical and oblique spans are longer",()=>{
 const routes=[
  {id:"a",points:[{x:0,y:0},{x:0,y:400},{x:100,y:400},{x:500,y:400}]},
  {id:"b",points:[{x:0,y:24},{x:0,y:424},{x:150,y:424},{x:500,y:424}]},
 ];
 const span=commonHorizontalPairSpan(routes)!;
 expect(span.orientation).toBe("horizontal");
 expect(span.start).toBe(150);
 expect(span.end).toBe(500);
 expect(span.crossMaximum-span.crossMinimum).toBe(24);
});

it("does not paint or select oblique differential motifs",()=>{
 expect(getE4DifferentialPairLayout(pair,objects)).toBeNull();
 const center={x:250,y:200};
 expect(hitTestE4DifferentialPair([pair],objects,center,1,[{id:"wires",label:"W",visible:true,locked:false}])).toBeNull();
 const context=new Proxy({},{get:(o:Record<string,unknown>,key:string)=>o[key]??(o[key]=vi.fn()),set:(o:Record<string,unknown>,key:string,value)=>{o[key]=value;return true;}}) as unknown as CanvasRenderingContext2D;
 drawE4DifferentialPairs(context,[pair],objects);
 expect(context.stroke).not.toHaveBeenCalled();
});

it("clips horizontal decoration away from connector bounds",()=>{
 const horizontalObjects:EditorSceneObject[]=objects.map((object,index)=>({...object,points:[{x:100,y:100+index*24},{x:500,y:100+index*24}]}));
 const original=getE4DifferentialPairLayout(pair,horizontalObjects)!;
 const table:EditorSceneObject={id:"x",kind:"connector",layerId:"connectors",label:"X",x:250,y:90,width:100,height:100,color:"black"};
 const clipped=getE4DifferentialPairLayout(pair,[...horizontalObjects,table])!;
 expect(clipped.span.end-clipped.span.start).toBeLessThan(original.span.end-original.span.start);
 for(const motif of clipped.motifs)for(const along of [motif.from,motif.to])for(const cross of [clipped.crossMinimum,clipped.crossMaximum]){
   const p=parallelSpanWorld(clipped.span,along,cross);
   expect(p.x>=table.x&&p.x<=table.x+table.width&&p.y>=table.y&&p.y<=table.y+table.height).toBe(false);
 }
});

it("keeps motif lanes on the real conductor coordinates even when they are wide",()=>{
 const wideObjects:EditorSceneObject[]= [
  {id:"top",kind:"wire",layerId:"wires",label:"TOP",x:0,y:0,width:0,height:0,color:"#f00",points:[{x:0,y:20},{x:500,y:20}]},
  {id:"bottom",kind:"wire",layerId:"wires",label:"BOTTOM",x:0,y:0,width:0,height:0,color:"#00f",points:[{x:0,y:180},{x:500,y:180}]},
 ];
 const layout=getE4DifferentialPairLayout({id:"compact",wireIds:["top","bottom"],step:25,amplitude:7,variant:2},wideObjects)!;
 expect(layout.crossMaximum-layout.crossMinimum).toBe(160);
 expect((layout.crossMinimum+layout.crossMaximum)/2).toBe(100);
 expect(layout.motifs.length).toBeGreaterThan(1);
});

it("creates and serializes a pair with a diagonal main section and directed contact leads",()=>{
 const a=createConnector("a","A",2,{x:0,y:0}),b=createConnector("b","B",2,{x:1000,y:200});
 const wires=[1,2].map(n=>({...createWire("w"+n,{connectorId:"a",contactId:"a:contact:"+n},{connectorId:"b",contactId:"b:contact:"+n}),e4Route:[{x:700,y:40+n*24},{x:900,y:240+n*24}],e4RouteMode:"manual" as const}));

 const d={...createEmptyHarnessDesign(),connectors:[a,{...b,schematic:{...b.schematic,orientation:"contacts-left" as const}}],wires};
 const changed=applyEditorCommand(d,{type:"create-diff-pair",group:{...pair,wireIds:["w1","w2"]}});
 expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(changed))).diffPairs).toEqual(changed.diffPairs);
});
