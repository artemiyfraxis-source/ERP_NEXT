import { drawingConnectorCorner, type DrawingPerimeters } from "./drawing-object-perimeter";
import { offsetPolyline } from "./covering-layout";
import type { DrawingDocuments } from "./drawing-documents";
import { findWireEndpoint, calculateWireCutLength, type HarnessDesignDocument, type Point, type WireInstance } from "./model";
import { physicalWirePoints } from "./physical-wire-geometry";
import { physicalSegmentControls } from "./physical-geometry";
import type { EditorSceneObject } from "./editor-types";

export type DimensionMode="horizontal"|"vertical"|"aligned"|"path";
export interface DrawingDimension {
  readonly id:string; readonly wireId?:string; readonly segmentId?:string; readonly from:number; readonly to:number;
  readonly pointCount:number; readonly routeKey:string; readonly mode:DimensionMode;
  readonly offset:number; readonly lengthMm:number|null; readonly auxiliary?:boolean; readonly modeOverride?:boolean;
}
/** Coordinate changes preserve anchors; a changed endpoint/topology never silently retargets an index. */
export function dimensionRouteKey(document:HarnessDesignDocument,wire:WireInstance):string {
  const endpoint=(e:WireInstance["from"])=>e.junctionId?`j:${e.junctionId}`:e.screenId?`s:${e.screenId}`:`c:${e.connectorId}:${e.contactId}`;
  const t=document.physicalTopology,route=t?.routes.find(r=>r.wireId===wire.id);
  return JSON.stringify([endpoint(wire.from),endpoint(wire.to),route?.steps.length?route.steps.map(step=>{
    const s=t!.segments.find(s=>s.id===step.segmentId)!;return [s.id,step.reverse,s.from,s.to,s.path.points.length];
  }):wire.drawingRoute.length]);
}
export function dimensionWirePoints(document:HarnessDesignDocument,wire:WireInstance):readonly Point[] {
  if(wire.drawingEndpoints)return [wire.drawingEndpoints.from,...wire.drawingRoute,wire.drawingEndpoints.to];
  const a=findWireEndpoint(document,wire.from,"drawing"),b=findWireEndpoint(document,wire.to,"drawing");
  return a&&b?physicalWirePoints(document,wire.id,a,b)??[a,...wire.drawingRoute,b]:[];
}
export function measuredWireLength(dimensions:readonly DrawingDimension[]):number|null {
  dimensions=dimensions.filter(d=>d.auxiliary!==true);if(!dimensions.length)return null;
  const end=dimensions[0]!.pointCount-1;
  const totals=dimensions.filter(d=>d.from===0&&d.to===end);
  if(totals.length>1)throw new Error("Общая длина провода уже задана размером.");
  if(totals.length)return totals[0]!.lengthMm;
  const sorted=[...dimensions].sort((a,b)=>a.from-b.from);
  let next=0,micros=0,complete=true;
  for(const d of sorted){
    if(d.from<next)throw new Error("Размерные участки перекрываются. Задайте общую длину либо непересекающиеся участки.");
    if(d.from!==next||d.lengthMm===null)complete=false;
    micros+=Math.round((d.lengthMm??0)*1000);next=d.to;
  }
  return complete&&next===end?micros/1000:null;
}
export function segmentDimensionKey(document:HarnessDesignDocument,segmentId:string):string {
  const s=document.physicalTopology?.segments.find(s=>s.id===segmentId);
  return JSON.stringify(s?[s.id,s.from,s.to,s.path.points.length]:null);
}
export function dimensionTargetKey(document:HarnessDesignDocument,d:DrawingDimension):string|null {
  if(d.segmentId)return document.physicalTopology?.segments.some(s=>s.id===d.segmentId)?segmentDimensionKey(document,d.segmentId):null;
  const wire=document.wires.find(w=>w.id===d.wireId);return wire?dimensionRouteKey(document,wire):null;
}
export function pipeMeasuredWireLength(document:HarnessDesignDocument,wireId:string):{managed:boolean;lengthMm:number|null} {
  const route=document.physicalTopology?.routes.find(r=>r.wireId===wireId);
  const dims=(document.drawingDocuments?.dimensions??[]).filter(d=>!d.auxiliary);
  const managed=!!route?.steps.some(s=>dims.some(d=>d.segmentId===s.segmentId));
  if(!managed||!route?.steps.length)return {managed:false,lengthMm:null};
  let micros=0;
  for(const step of route.steps){const length=measuredWireLength(dims.filter(d=>d.segmentId===step.segmentId));if(length===null)return {managed:true,lengthMm:null};micros+=Math.round(length*1000);}
  return {managed:true,lengthMm:micros/1000};
}
export function validateDrawingDimensions(value:unknown,document:HarnessDesignDocument):readonly DrawingDimension[]|undefined {
  if(value===undefined)return undefined;
  const fail=():never=>{throw new Error("Некорректные размеры чертежа.");};
  if(!Array.isArray(value)||value.length>10000)return fail();
  const ids=new Set<string>();
  for(const d of value as DrawingDimension[]){
    if(!d)return fail();
    const pipe=d.segmentId!==undefined;
    if(!d||typeof d.id!=="string"||!d.id.trim()||d.id.length>128||ids.has(d.id)||pipe&&(typeof d.segmentId!=="string"||d.wireId!==undefined)||!pipe&&typeof d.wireId!=="string"||typeof d.routeKey!=="string"||d.routeKey.length>65536||!["horizontal","vertical","aligned","path"].includes(d.mode)||!Number.isFinite(d.offset)||Math.abs(d.offset)>1e7||!Number.isInteger(d.pointCount)||d.pointCount<2||d.pointCount>50000||!Number.isInteger(d.from)||!Number.isInteger(d.to)||d.from<0||d.to<=d.from||d.to>=d.pointCount||d.lengthMm!==null&&(!Number.isFinite(d.lengthMm)||d.lengthMm<0||d.lengthMm>1e7||Math.abs(d.lengthMm*1000-Math.round(d.lengthMm*1000))>1e-5)||d.auxiliary!==undefined&&typeof d.auxiliary!=="boolean")return fail();
    if(d.modeOverride!==undefined&&typeof d.modeOverride!=="boolean")return fail();
    ids.add(d.id);
    if(d.routeKey!==dimensionTargetKey(document,d))return fail();
    if(pipe&&d.pointCount!==document.physicalTopology!.segments.find(s=>s.id===d.segmentId)!.path.points.length+2)return fail();
  }
  for(const target of new Set((value as DrawingDimension[]).map(d=>d.segmentId??d.wireId))){const group=(value as DrawingDimension[]).filter(d=>(d.segmentId??d.wireId)===target);if(group.some(d=>d.pointCount!==group[0]!.pointCount))return fail();measuredWireLength(group);}
  return value as DrawingDimension[];
}
/** Pipe measurements own shared route lengths; legacy wire dimensions remain individual overrides. */
export function reconcileDrawingDimensions(before:HarnessDesignDocument,after:HarnessDesignDocument):HarnessDesignDocument {
  const old=before.drawingDocuments?.dimensions??[],current=after.drawingDocuments?.dimensions??[];
  if(!old.length&&!current.length)return after;
  const rebound=current.map(d=>{
    if(d.from!==0||d.to!==d.pointCount-1)return d;
    const oldKey=dimensionTargetKey(before,d),nextKey=dimensionTargetKey(after,d);
    if(!oldKey||!nextKey||oldKey!==d.routeKey)return d;
    const stable=(key:string):string=>{
      const value=JSON.parse(key);
      return JSON.stringify(d.segmentId?value.slice(0,3):[value[0],value[1],Array.isArray(value[2])?value[2].map((step:unknown[])=>step.slice(0,4)):null]);
    };
    if(stable(oldKey)!==stable(nextKey))return d;
    const segment=after.physicalTopology?.segments.find(s=>s.id===d.segmentId),wire=after.wires.find(w=>w.id===d.wireId);
    const pointCount=segment?segment.path.points.length+2:wire?dimensionWirePoints(after,wire).length:d.pointCount;
    return {...d,pointCount,to:pointCount-1,routeKey:nextKey};
  });
  const dimensions=rebound.filter(d=>{
    if(dimensionTargetKey(after,d)!==d.routeKey)return false;
    if(d.segmentId)return after.physicalTopology!.segments.find(s=>s.id===d.segmentId)!.path.points.length+2===d.pointCount;
    const wire=after.wires.find(w=>w.id===d.wireId),previous=before.wires.find(w=>w.id===d.wireId);
    return wire&&(!previous||d.from===0&&d.to===d.pointCount-1||dimensionWirePoints(before,previous).length===dimensionWirePoints(after,wire).length);
  });
  const measured={...after,drawingDocuments:{...after.drawingDocuments!,dimensions}};
  return {...measured,wires:after.wires.map(w=>{
    // A route fragment's dimensions describe its illustration; its source
    // manufacturing length remains the pinned production value.
    if(w.drawingEndpoints)return w;
    const own=dimensions.filter(d=>d.wireId===w.id&&!d.auxiliary),shared=pipeMeasuredWireLength(measured,w.id);
    const affected=own.length||shared.managed||old.some(d=>d.wireId===w.id&&!d.auxiliary)||pipeMeasuredWireLength(before,w.id).managed;
    if(!affected)return w;
    const lengthMm=own.length?measuredWireLength(own):shared.lengthMm;
    calculateWireCutLength({...w,lengthMm});return {...w,lengthMm};
  })};
}
export function dimensionGeometry(a:Point,b:Point,mode:DimensionMode,offset:number):readonly Point[] {
  if(mode==="horizontal")return [a,{x:a.x,y:Math.max(a.y,b.y)+offset},{x:b.x,y:Math.max(a.y,b.y)+offset},b];
  if(mode==="vertical")return [a,{x:Math.max(a.x,b.x)+offset,y:a.y},{x:Math.max(a.x,b.x)+offset,y:b.y},b];
  const length=Math.hypot(b.x-a.x,b.y-a.y)||1,dx=-(b.y-a.y)/length*offset,dy=(b.x-a.x)/length*offset;
  return [a,{x:a.x+dx,y:a.y+dy},{x:b.x+dx,y:b.y+dy},b];
}
export function dimensionTargetPoints(document:HarnessDesignDocument,d:DrawingDimension,perimeters?:DrawingPerimeters):Point[] {
 const segment=document.physicalTopology?.segments.find(s=>s.id===d.segmentId),wire=document.wires.find(w=>w.id===d.wireId);
 const controls=segment?physicalSegmentControls(document,segment):wire?dimensionWirePoints(document,wire):[];
 const points=controls.slice(d.from,d.to+1).map(p=>({...p}));if(points.length<2)return [];
 const ends=segment?[document.physicalTopology?.nodes.find(n=>n.id===segment.from)?.connectorId,document.physicalTopology?.nodes.find(n=>n.id===segment.to)?.connectorId]:wire?.drawingEndpoints?[]:[wire?.from.connectorId,wire?.to.connectorId];
 if(d.from===0&&ends[0])points[0]=drawingConnectorCorner(document,ends[0],points[0]!,perimeters)??points[0]!;
 if(d.to===controls.length-1&&ends[1])points[points.length-1]=drawingConnectorCorner(document,ends[1],points.at(-1)!,perimeters)??points.at(-1)!;
 return points;
}
export const resolvedDimensionMode=(document:HarnessDesignDocument,d:DrawingDimension):DimensionMode=>d.modeOverride===false?document.drawingDocuments?.dimensionMode??d.mode:d.mode;
export function dimensionDisplayPoints(document:HarnessDesignDocument,d:DrawingDimension,perimeters?:DrawingPerimeters):Point[] {
 const points=dimensionTargetPoints(document,d,perimeters);if(points.length<2)return [];
 const mode=resolvedDimensionMode(document,d);
 return mode==="path"?[points[0]!,...offsetPolyline(points,points.map(()=>d.offset)),points.at(-1)!]:[...dimensionGeometry(points[0]!,points.at(-1)!,mode,d.offset)];
}
export function drawingDimensionScene(document:HarnessDesignDocument,_wires:readonly EditorSceneObject[]=[],perimeters?:DrawingPerimeters):EditorSceneObject[] {
 if(document.drawingDocuments?.showDimensions===false)return [];
 const explicit=document.drawingDocuments?.dimensions??[];
 const legacy=document.wires.filter(w=>document.drawingDocuments===undefined&&w.lengthMm!==null&&!explicit.some(d=>d.wireId===w.id)).flatMap(w=>{
  const points=dimensionWirePoints(document,w);if(points.length<2)return [];
  const geometry=dimensionGeometry(points[0]!,points.at(-1)!,"aligned",40);
  return [{id:`dimension:${w.id}`,kind:"dimension" as const,layerId:"dimensions",label:`${w.lengthMm} мм`,x:(geometry[1]!.x+geometry[2]!.x)/2,y:(geometry[1]!.y+geometry[2]!.y)/2,width:0,height:0,color:"#55798e",points:geometry,metadata:{boundDimension:"true",legacyDimension:"true",dimensionMode:"aligned"}}];
 });
  const scene:EditorSceneObject[]=[...legacy,...explicit.flatMap(d=>{
  const points=dimensionDisplayPoints(document,d,perimeters);if(points.length<4)return [];
  const p=points[1]!,q=points.at(-2)!;
  return [{id:d.id,kind:"dimension" as const,layerId:"dimensions",label:d.lengthMm===null?"— мм":`${d.lengthMm} мм`,x:(p.x+q.x)/2,y:(p.y+q.y)/2,width:0,height:0,color:"#55798e",points,metadata:{boundDimension:"true",dimensionMode:resolvedDimensionMode(document,d)}}];
 })];
  return scene.map(item=>{
  const ends=[item.points![1]!,item.points!.at(-2)!],line={x:ends[1]!.x-ends[0]!.x,y:ends[1]!.y-ends[0]!.y};
  const dots=ends.map(p=>scene.some(other=>{
   if(other.id===item.id)return false;const a=other.points![1]!,b=other.points!.at(-2)!,dx=b.x-a.x,dy=b.y-a.y;
   return Math.abs(dx*line.y-dy*line.x)<1e-5*Math.max(1,Math.hypot(dx,dy)*Math.hypot(line.x,line.y))&&[a,b].some(q=>Math.hypot(p.x-q.x,p.y-q.y)<.01);
  }));
  const source=explicit.find(d=>d.id===item.id);
  return {...item,metadata:{...item.metadata,dimensionScale:String(document.drawingDocuments?.dimensionScale??1),dotStart:String(dots[0]),dotEnd:String(dots[1]),...(source?.auxiliary?{auxiliary:"true"}:{})}};
 });
}
export function moveDrawingDimension(document:HarnessDesignDocument,id:string,point:Point,perimeters?:DrawingPerimeters):DrawingDocuments|null {
 const docs=document.drawingDocuments,stored=docs?.dimensions?.find(d=>d.id===id);if(!docs||!stored)return null;
 const d={...stored,mode:resolvedDimensionMode(document,stored)};
 const points=dimensionDisplayPoints(document,d,perimeters);if(points.length<4)return null;
 const a=points[0]!,b=points.at(-1)!,p=points[1]!,q=points.at(-2)!,length=Math.hypot(b.x-a.x,b.y-a.y)||1;
 const n=d.mode==="horizontal"?{x:0,y:1}:d.mode==="vertical"?{x:1,y:0}:{x:-(b.y-a.y)/length,y:(b.x-a.x)/length};
 let offset=d.offset+(point.x-(p.x+q.x)/2)*n.x+(point.y-(p.y+q.y)/2)*n.y;
 const next=()=>dimensionDisplayPoints(document,{...d,offset},perimeters)[1]!;
 for(const other of docs.dimensions??[]){if(other.id===id||resolvedDimensionMode(document,other)!==d.mode||d.mode==="path")continue;
  const op=dimensionDisplayPoints(document,other,perimeters);if(op.length<4)continue;const oa=op[0]!,ob=op.at(-1)!;
  if(d.mode==="aligned"&&Math.abs((ob.x-oa.x)*n.x+(ob.y-oa.y)*n.y)>1e-5)continue;
  const target=op[1]!,current=next(),delta=(target.x-current.x)*n.x+(target.y-current.y)*n.y;
  if(Math.abs(delta)<=10){offset+=delta;break;}
 }
 return {...docs,dimensions:docs.dimensions!.map(item=>item.id===id?{...item,offset}:item)};
}
export function setPipeIntervalLength(document:HarnessDesignDocument,segmentId:string,from:number,to:number,lengthMm:number|null):DrawingDocuments {
 const s=document.physicalTopology!.segments.find(s=>s.id===segmentId)!,docs=document.drawingDocuments??{tables:[],leaders:[],bomOrder:[]};
 const dimensions=docs.dimensions??[],existing=dimensions.find(d=>d.segmentId===segmentId&&!d.auxiliary&&d.from===from&&d.to===to);
 const value:DrawingDimension={id:existing?.id??crypto.randomUUID(),segmentId,from,to,pointCount:s.path.points.length+2,routeKey:segmentDimensionKey(document,segmentId),mode:existing?.mode??docs.dimensionMode??"aligned",modeOverride:existing?.modeOverride??!!existing,offset:existing?.offset??40,lengthMm, ...(existing?.auxiliary?{auxiliary:true}:{})};
 return {...docs,showDimensions:docs.showDimensions??false,dimensions:[...dimensions.filter(d=>d.auxiliary||d.segmentId!==segmentId||d.to<=from||d.from>=to),value]};
}
export function toggleDrawingDimensions(document:HarnessDesignDocument):DrawingDocuments {
 let docs=document.drawingDocuments??{tables:[],leaders:[],bomOrder:[]};
 const show=!(docs.showDimensions??!!docs.dimensions?.length);
 if(show)for(const s of document.physicalTopology?.segments??[]){if(docs.dimensions?.some(d=>d.segmentId===s.id&&!d.auxiliary))continue;
  docs=setPipeIntervalLength({...document,drawingDocuments:docs},s.id,0,s.path.points.length+1,null);
 }
 if(show)for(const wire of document.wires){
  if(docs.dimensions?.some(d=>d.wireId===wire.id&&!d.auxiliary)||document.physicalTopology?.routes.some(r=>r.wireId===wire.id))continue;
  const points=dimensionWirePoints(document,wire);if(points.length<2)continue;
  docs={...docs,dimensions:[...docs.dimensions??[],{id:crypto.randomUUID(),wireId:wire.id,from:0,to:points.length-1,pointCount:points.length,routeKey:dimensionRouteKey(document,wire),mode:docs.dimensionMode??"aligned",modeOverride:false,offset:40,lengthMm:wire.lengthMm}]};
 }
 return {...docs,showDimensions:show};
}
