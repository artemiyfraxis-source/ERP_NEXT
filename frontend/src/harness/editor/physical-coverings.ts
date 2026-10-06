import type { EditorCatalogItem } from "./editor-types";
import { findWireEndpoint, type HarnessDesignDocument, type Point } from "./model";
import { physicalSegmentPoints, physicalSegmentControls, physicalNodePoint } from "./physical-geometry";
import { drawingBendRadius, drawingRouteSamples, projectOntoDrawingRoute } from "./drawing-route-path";
import { validCoveringStyle, type CoveringStyle } from "./covering-style";
import {applyCoveringPreference} from "./covering-library";
import {resolvePipeBundles,type PipeBundle} from "./pipe-bundle-model";
import {splitPipeBundleMembers} from "./pipe-bundle-editing";
import { joiningPipePoints } from "./physical-joining-pipes";
import { joiningPipeDisplaySamples } from "./physical-joining-pipe-projection";
import { drawingPipeWidth } from "./drawing-thickness";
import type { JoiningPipeMember, PhysicalJoiningPipe } from "./physical-topology-model";

export interface CoveringMaterial {
  readonly sourceId: string; readonly snapshotId: string; readonly snapshotSha256: string;
  readonly recordId: string; readonly entityType: "protective-covering";
  readonly sourceKey: string; readonly displayName: string;
}
export interface CoveringSpan { readonly segmentId: string; readonly from: number; readonly to: number; readonly fromAnchor?:number; readonly toAnchor?:number }
export function validateCoveringMaterial(value:unknown):void {
  const fail=():never=>{throw new Error("Некорректная привязка материала оболочки.");};
  if(!value||typeof value!=="object")return fail();
  const m=value as CoveringMaterial;
  if (m.entityType !== "protective-covering" || !/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(m.snapshotId) || m.snapshotId === "00000000-0000-0000-0000-000000000000" || !/^[\da-f]{64}$/i.test(m.snapshotSha256) || !/^[\da-f]{64}$/i.test(m.recordId)) return fail();
  for (const key of ["sourceId", "sourceKey", "displayName"] as const) if (typeof m[key] !== "string" || !m[key].trim() || m[key].length > 512) return fail();
}
export type CoveringKind="heat-shrink"|"nylon"|"braid"|"metal-braid"|"tape"|"band";
export interface PhysicalCovering {
  readonly bundle?: PipeBundle;
  readonly style?:CoveringStyle;
  readonly kind?:CoveringKind;
  readonly lengthMode?:"auto"|"manual";
  readonly id: string; readonly name: string; readonly spans: readonly CoveringSpan[];
  /** Drawing width; zero fits the underlying surfaces automatically. Independent of manufacturing length. */
  readonly width: number; readonly color: string; readonly lengthMm: number | null;
  readonly material?: CoveringMaterial;
}
export function coveringMaterial(item: EditorCatalogItem): CoveringMaterial {
  if (item.entityType !== "protective-covering" || !item.sourceId || !item.snapshotId || !item.snapshotSha256 || !item.recordId || !item.sourceKey) throw new Error("Выберите опубликованный материал защиты с закреплённой версией.");
  return { sourceId: item.sourceId, snapshotId: item.snapshotId, snapshotSha256: item.snapshotSha256, recordId: item.recordId, entityType: "protective-covering", sourceKey: item.sourceKey, displayName: item.referenceDisplayName || item.title };
}
export const pathLength = (points: readonly Point[]) => points.slice(1).reduce((sum, p, i) => sum + Math.hypot(p.x - points[i]!.x, p.y - points[i]!.y), 0);
export function trimPolyline(points: readonly Point[], from: number, to: number): Point[] {
  const total = pathLength(points), start = total * from, end = total * to;
  let distance = 0;
  const result: Point[] = [];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]!, b = points[i]!, length = Math.hypot(b.x - a.x, b.y - a.y);
    if (length > 0 && distance + length > start && distance < end) {
      const at = (d: number) => ({ x: a.x + (b.x - a.x) * d / length, y: a.y + (b.y - a.y) * d / length });
      if (!result.length) result.push(at(Math.max(0, start - distance)));
      result.push(at(Math.min(length, end - distance)));
    }
    distance += length;
  }
  return result;
}
export function coveringPaths(document: HarnessDesignDocument, covering: PhysicalCovering): Point[][] {
  return covering.spans.flatMap(span => {
    const route=coveringRoute(document,span.segmentId);if(!route)return [];
    const bounds=resolvedCoveringSpan(document,span);
    return [trimPolyline(route.points,(route.before+bounds.from*route.length)/route.total,(route.before+bounds.to*route.length)/route.total)];
  });
}
export function validateCoverings(value: unknown, segmentIds: ReadonlySet<string>, existingIds: Set<string>): readonly PhysicalCovering[] | undefined {
  if (value === undefined) return undefined;
  const fail = (): never => { throw new Error("Некорректная оболочка: проверьте участки, границы и материал."); };
  if (!Array.isArray(value) || value.length > 10000) return fail();
  for (const c of value as PhysicalCovering[]) {
    if (!c || typeof c.id !== "string" || !c.id.trim() || c.id.length > 128 || existingIds.has(c.id)) return fail();
    existingIds.add(c.id);
    if(c.style!==undefined&&!validCoveringStyle(c.style))return fail();
    if (typeof c.name !== "string" || !c.name.trim() || c.name.length > 256 || !/^#[0-9a-f]{6}$/i.test(c.color) || !Number.isFinite(c.width) || c.width < 0 || c.width > 1e7) return fail();
    if(c.kind!==undefined&&!["heat-shrink","nylon","braid","metal-braid","tape","band"].includes(c.kind)||c.lengthMode!==undefined&&!["auto","manual"].includes(c.lengthMode))return fail();
    if (c.lengthMm !== null && (!Number.isFinite(c.lengthMm) || c.lengthMm < 0 || c.lengthMm > 1e9 || Math.abs(c.lengthMm * 1000 - Math.round(c.lengthMm * 1000)) > 1e-4)) return fail();
    if (!Array.isArray(c.spans) || !c.spans.length || c.spans.length > 20000 || new Set(c.spans.map(s => s?.segmentId)).size !== c.spans.length) return fail();
    for (const s of c.spans) if (!s || !segmentIds.has(s.segmentId) || !Number.isFinite(s.from) || !Number.isFinite(s.to) || s.from < -10000 || s.to > 10001 || s.from >= s.to || [s.fromAnchor,s.toAnchor].some(i=>i!==undefined&&(!Number.isInteger(i)||i<0||i>1001))) return fail();
    if (c.material !== undefined) validateCoveringMaterial(c.material);
  }
  resolvePipeBundles(value,segmentIds);
  return value;
}

export function splitCoveringSpans(coverings: readonly PhysicalCovering[] | undefined, id: string, nextId: string, fraction: number): readonly PhysicalCovering[] | undefined {
  return coverings?.map(c => ({ ...splitPipeBundleMembers(c,id,nextId), spans: c.spans.flatMap(s => s.segmentId !== id ? [s] : [
    ...(s.from < fraction ? [{ segmentId: id, from: s.from / fraction, to: Math.min(s.to, fraction) / fraction }] : []),
    ...(s.to > fraction ? [{ segmentId: nextId, from: Math.max(0, (s.from - fraction) / (1 - fraction)), to: (s.to - fraction) / (1 - fraction) }] : []),
  ]) }));
}

export const standardCoveringKinds=["Термоусадка","Нейлонка","Оплётка","Нитевый бандаж","Обмотка","Металлическая плетёнка"] as const;
export type PhysicalContextAction=typeof standardCoveringKinds[number]|"branch"|"remove-pipe";
export type PhysicalContextTarget="segment"|"covering";
export function projectOntoPolyline(points:readonly Point[],point:Point){
 let best={point:points[0]??point,index:1,fraction:0,distance:Infinity},travelled=0;const total=pathLength(points);
 for(let i=1;i<points.length;i++){const a=points[i-1]!,b=points[i]!,dx=b.x-a.x,dy=b.y-a.y,length=Math.hypot(dx,dy),t=length?Math.max(0,Math.min(1,((point.x-a.x)*dx+(point.y-a.y)*dy)/(length*length))):0;
 const q={x:a.x+t*dx,y:a.y+t*dy},distance=Math.hypot(point.x-q.x,point.y-q.y);
 if(distance<best.distance)best={point:q,index:i,fraction:total?(travelled+t*length)/total:0,distance};travelled+=length;}
 return best;
}
export function standardCovering(document:HarnessDesignDocument,segmentId:string,point:Point,name:typeof standardCoveringKinds[number],id:string):PhysicalCovering {
 const points=coveringRoute(document,segmentId)?.core??[],length=pathLength(points);
 const at=length?projectOntoDrawingRoute(points,drawingBendRadius(document),point)/length:0;
 return applyCoveringPreference({id,name,kind:coveringKind({name}),lengthMode:"auto",width:0,color:name==="Металлическая плетёнка"?"#73838d":name==="Термоусадка"?"#424c53":"#b19c77",lengthMm:null,spans:[{segmentId,from:Math.max(0,at-.1),to:Math.min(1,at+.1)}]},document.drawingDocuments?.coveringLibrary);
}

/** Creates a second protective layer over the exact interval(s) of an existing
 * layer. Context actions intentionally keep the source spans and anchors, so
 * adding a sleeve on top does not require an uncovered pipe hit. */
export function standardCoveringOver(document:HarnessDesignDocument,source:PhysicalCovering,name:typeof standardCoveringKinds[number],id:string,point?:Point):PhysicalCovering {
 let spans:readonly CoveringSpan[]=source.spans.map(span=>({...span}));
 if(point){
  const candidates=source.spans.flatMap(span=>{
   const route=coveringRoute(document,span.segmentId);if(!route)return [];
   const resolved=resolvedCoveringSpan(document,span);
   const covered=trimPolyline(route.points,(route.before+resolved.from*route.length)/route.total,
     (route.before+resolved.to*route.length)/route.total);
   if(covered.length<2)return [];
   const projected=projectOntoPolyline(covered,point);
   return [{span,route,resolved,distance:projected.distance,at:resolved.from+(resolved.to-resolved.from)*projected.fraction}];
  });
  const selected=candidates.sort((a,b)=>a.distance-b.distance)[0];
  if(selected){
   const sourceLength=source.spans.reduce((sum,span)=>{
    const route=coveringRoute(document,span.segmentId),bounds=resolvedCoveringSpan(document,span);
    return sum+(route?Math.max(0,bounds.to-bounds.from)*route.length:0);
   },0);
   const minimum=document.drawingDocuments?.minimumCoveringOverlapPx??100;
   const interval=Math.min((selected.resolved.to-selected.resolved.from)*selected.route.length,
     Math.max(sourceLength*.05,minimum));
   const half=interval/selected.route.length/2;
   const from=Math.max(selected.resolved.from,Math.min(selected.at-half,selected.resolved.to-2*half));
   spans=[{segmentId:selected.span.segmentId,from,to:from+2*half}];
  }
 }
 return applyCoveringPreference({id,name,kind:coveringKind({name}),lengthMode:"auto",width:0,color:name==="Металлическая плетёнка"?"#73838d":name==="Термоусадка"?"#424c53":"#b19c77",lengthMm:null,spans,...(source.bundle?{bundle:{...source.bundle,members:source.bundle.members.map(m=>({...m}))}}:{})},document.drawingDocuments?.coveringLibrary);
}

export function coveringKind(c:{name:string;kind?:CoveringKind}):CoveringKind {
 return c.kind??(/термо/i.test(c.name)?"heat-shrink":/нейлон/i.test(c.name)?"nylon":/метал/i.test(c.name)?"metal-braid":/бандаж/i.test(c.name)?"band":/обмот|лент/i.test(c.name)?"tape":"braid");
}

/** The tails extend the same pipe parameter space towards the connector contacts. */
function joiningMemberCoveringSamples(document:HarnessDesignDocument,member:JoiningPipeMember):readonly {fraction:number;point:Point}[] {
 const segments=member.segmentIds.map(id=>document.physicalTopology?.segments.find(s=>s.id===id));
 const lengths=segments.map(segment=>segment?pathLength(physicalSegmentPoints(document,segment)):0),total=lengths.reduce((sum,length)=>sum+length,0);
 if(total<1e-7)return [];
 let before=0;
 const samples=member.segmentIds.flatMap((id,index)=>{
  const length=lengths[index]!,points=joiningPipeDisplaySamples(document,id)??[];
  const stations:number[]=[];let distance=0;
  points.forEach((sample,i)=>{if(i)distance+=Math.hypot(sample.point.x-points[i-1]!.point.x,sample.point.y-points[i-1]!.point.y);stations.push(distance);});
  const result=drawingRouteSamples(points.map(sample=>sample.point),drawingBendRadius(document)).map(sample=>{
   const next=stations.findIndex(at=>at>=sample.distance),i=next<0?stations.length-1:next;
   const a=Math.max(0,i-1),t=(sample.distance-stations[a]!)/(stations[i]!-stations[a]!||1);
   const fraction=points[a]!.fraction+(points[i]!.fraction-points[a]!.fraction)*Math.max(0,Math.min(1,t));
   return {fraction:(before+fraction*length)/total,point:sample.point};
  });
  before+=length;return index?result.slice(1):result;
 });
 return member.reverse?samples.reverse().map(sample=>({fraction:1-sample.fraction,point:sample.point})):samples;
}

function pointAtFraction(samples:readonly {fraction:number;point:Point}[],fraction:number):Point {
 if(fraction<=samples[0]!.fraction)return samples[0]!.point;
 for(let index=1;index<samples.length;index++){
  const next=samples[index]!;if(fraction>next.fraction)continue;
  const previous=samples[index-1]!,t=(fraction-previous.fraction)/(next.fraction-previous.fraction||1);
  return {x:previous.point.x+(next.point.x-previous.point.x)*t,y:previous.point.y+(next.point.y-previous.point.y)*t};
 }
 return samples.at(-1)!.point;
}

function joiningCoveringTail(document:HarnessDesignDocument,pipe:PhysicalJoiningPipe,side:"from"|"to") {
 const members=pipe.members.map(member=>({member,samples:joiningMemberCoveringSamples(document,member),start:member.reverse?1-member.to:member.from,end:member.reverse?1-member.from:member.to,
  radius:Math.max(...member.segmentIds.map(id=>{
   const segment=document.physicalTopology?.segments.find(item=>item.id===id);
   return segment?drawingPipeWidth(document,segment)/2:0;
  }))}));
 if(members.some(member=>member.samples.length<2))return [{point:side==="from"?pipe.start:pipe.end,spread:0,leftSpread:0,rightSpread:0,members:[]}];
 const positions=members.map(({samples,start,end,radius})=>{
  const boundary=side==="from"?start:end,range=side==="from"?start:1-end;
  return {samples,boundary,range,radius};
 });
 const stops=new Set<number>([0,1]);
 for(const {samples,boundary,range} of positions)if(range>1e-7)for(const sample of samples){
  const u=side==="from"?sample.fraction/range:(sample.fraction-boundary)/range;
  if(u>0&&u<1)stops.add(u);
 }
 for(let index=1;index<8;index++)stops.add(index/8);
 const stations=[...stops].sort((a,b)=>a-b).map(u=>{
  const memberPoints=positions.map(({samples,boundary,range})=>pointAtFraction(samples,side==="from"?u*range:boundary+u*range));
  const mean={x:memberPoints.reduce((sum,p)=>sum+p.x,0)/memberPoints.length,y:memberPoints.reduce((sum,p)=>sum+p.y,0)/memberPoints.length};
  const point=side==="from"&&u===1?pipe.start:side==="to"&&u===0?pipe.end:mean;
  return {point,memberPoints};
 });
 return stations.map((station,index)=>{
  const a=stations[Math.max(0,index-1)]!.point,b=stations[Math.min(stations.length-1,index+1)]!.point;
  const length=Math.hypot(b.x-a.x,b.y-a.y)||1,normal={x:-(b.y-a.y)/length,y:(b.x-a.x)/length};
  const offsets=station.memberPoints.map(p=>(p.x-station.point.x)*normal.x+(p.y-station.point.y)*normal.y);
  const leftSpread=Math.max(0,...offsets.map((offset,i)=>offset+positions[i]!.radius));
  const rightSpread=Math.max(0,...offsets.map((offset,i)=>-offset+positions[i]!.radius));
  return {point:station.point,leftSpread,rightSpread,spread:Math.max(leftSpread,rightSpread),
   members:station.memberPoints.map((point,i)=>({point,radius:positions[i]!.radius}))};
 });
}

function joiningCoveringCore(document:HarnessDesignDocument,pipe:PhysicalJoiningPipe) {
 const members=pipe.members.map(member=>({member,samples:joiningMemberCoveringSamples(document,member),
  radius:Math.max(...member.segmentIds.map(id=>{
   const segment=document.physicalTopology?.segments.find(item=>item.id===id);
   return segment?drawingPipeWidth(document,segment)/2:0;
  }))}));
 if(members.some(item=>item.samples.length<2))return [];
 const stops=new Set<number>([0,1]);
 for(const {member,samples} of members)for(const sample of samples){
  const start=member.reverse?1-member.to:member.from;
  const t=(sample.fraction-start)/(member.to-member.from);
  if(t>0&&t<1)stops.add(t);
 }
 return [...stops].sort((a,b)=>a-b).map(t=>({at:t,members:members.map(({member,samples,radius})=>({
  point:pointAtFraction(samples,(member.reverse?1-member.to:member.from)+t*(member.to-member.from)),radius}))}));
}

export function coveringRoute(document:HarnessDesignDocument,segmentId:string) {
 const t=document.physicalTopology,joining=t?.joiningPipes?.find(p=>p.id===segmentId);
 if(joining){
  const core=joiningPipePoints(joining),length=pathLength(core);if(length<1e-7)return null;
  const enter=joiningCoveringTail(document,joining,"from"),exit=joiningCoveringTail(document,joining,"to");
  const before=pathLength(enter.map(sample=>sample.point)),after=pathLength(exit.map(sample=>sample.point)),total=before+length+after;
  const points=[...enter.slice(0,-1).map(sample=>sample.point),...core,...exit.slice(1).map(sample=>sample.point)];
  const envelope:{at:number;spread:number;leftSpread:number;rightSpread:number;side:"from"|"to"|"core";members:readonly {point:Point;radius:number}[]}[]=[];
  let distance=0;enter.forEach((sample,index)=>{if(index)distance+=Math.hypot(sample.point.x-enter[index-1]!.point.x,sample.point.y-enter[index-1]!.point.y);envelope.push({at:distance,spread:sample.spread,leftSpread:sample.leftSpread,rightSpread:sample.rightSpread,side:"from",members:sample.members??[]});});
  distance=before+length;exit.forEach((sample,index)=>{if(index)distance+=Math.hypot(sample.point.x-exit[index-1]!.point.x,sample.point.y-exit[index-1]!.point.y);envelope.push({at:distance,spread:sample.spread,leftSpread:sample.leftSpread,rightSpread:sample.rightSpread,side:"to",members:sample.members??[]});});
  const coreMembers=joiningCoveringCore(document,joining);
  coreMembers.forEach(sample=>envelope.push({at:before+sample.at*length,spread:0,leftSpread:0,rightSpread:0,side:"core",members:sample.members}));
  envelope.sort((a,b)=>a.at-b.at);
  return {points,core,length,before,after,total,min:-before/length,max:1+after/length,envelope};
 }
 const s=t?.segments.find(s=>s.id===segmentId);if(!t||!s)return null;
 const core=physicalSegmentPoints(document,s),length=pathLength(core);if(length<1e-7)return null;
 const tail=(nodeId:string)=>{
  const node=t.nodes.find(n=>n.id===nodeId)!;if(!node.connectorId)return null;
  const ids=t.routes.filter(r=>r.steps.some(p=>p.segmentId===s.id)).map(r=>r.wireId);
  const points=document.wires.filter(w=>ids.includes(w.id)).flatMap(w=>[w.from,w.to].filter(e=>e.connectorId===node.connectorId).flatMap(e=>{const p=findWireEndpoint(document,e,"drawing");return p?[p]:[]}));
  return points.length?{x:points.reduce((n,p)=>n+p.x,0)/points.length,y:points.reduce((n,p)=>n+p.y,0)/points.length}:physicalNodePoint(document,node);
 };
 const a=tail(s.from),b=tail(s.to),before=a?Math.hypot(a.x-core[0]!.x,a.y-core[0]!.y):0,after=b?Math.hypot(b.x-core.at(-1)!.x,b.y-core.at(-1)!.y):0;
 return {points:[...(a?[a]:[]),...core,...(b?[b]:[])],core,length,before,after,total:before+length+after,min:-before/length,max:1+after/length,envelope:[]};
}

export function coveringControlFractions(document:HarnessDesignDocument,segmentId:string):number[] {
 const s=document.physicalTopology?.segments.find(s=>s.id===segmentId);
 if(s){const points=physicalSegmentPoints(document,s);return physicalSegmentControls(document,s).map(p=>projectOntoPolyline(points,p).fraction);}
 const joining=document.physicalTopology?.joiningPipes?.find(p=>p.id===segmentId);if(!joining)return [];
 const points=joiningPipePoints(joining);return points.map(p=>projectOntoPolyline(points,p).fraction);
}
export function resolvedCoveringSpan(document:HarnessDesignDocument,s:CoveringSpan):CoveringSpan {
 const fractions=coveringControlFractions(document,s.segmentId);
 return {...s,from:s.fromAnchor===undefined?s.from:fractions[s.fromAnchor]??s.from,to:s.toAnchor===undefined?s.to:fractions[s.toAnchor]??s.to};
}
/** Only explicitly bound endpoints use measured pipe intervals; pixels never become millimetres. */
export function coveringMeasuredLength(document:HarnessDesignDocument,c:PhysicalCovering):number|null {
 if(c.lengthMode==="manual"||c.lengthMode===undefined&&c.lengthMm!==null)return c.lengthMm;
 let sum=0;
 for(const s of c.spans){
  if(s.fromAnchor===undefined||s.toAnchor===undefined)return null;
  const dims=document.drawingDocuments?.dimensions?.filter(d=>d.segmentId===s.segmentId&&!d.auxiliary)??[];
  const exact=dims.find(d=>d.from===s.fromAnchor&&d.to===s.toAnchor);
  if(exact){if(exact.lengthMm===null)return null;sum+=Math.round(exact.lengthMm*1000);continue;}
  const parts=dims.filter(d=>d.from>=s.fromAnchor!&&d.to<=s.toAnchor!).sort((a,b)=>a.from-b.from);
  let next=s.fromAnchor;
  for(const d of parts){if(d.from!==next||d.lengthMm===null)return null;sum+=Math.round(d.lengthMm*1000);next=d.to;}
  if(next!==s.toAnchor)return null;
 }
 return sum/1000;
}
