import { coveringWidthProfile, encloseWidthProfiles, profileHalfWidth, type WidthSupport } from "./covering-width-profile";
import type { EditorSceneObject } from "./editor-types";
import type { HarnessDesignDocument, Point } from "./model";
import { coveringControlFractions, coveringKind, coveringRoute, resolvedCoveringSpan, trimPolyline, type PhysicalCovering } from "./physical-coverings";
import { coveringDiameterRatio, drawingPhysicalScale, drawingPipeWidth, segmentWireLanes } from "./drawing-thickness";
import { drawingBendRadius, drawingRouteSamples, drawingRouteSection } from "./drawing-route-path";
import { pipeBundleSections } from "./pipe-bundle-section";
import { pipeBundleAxisPath, pipeBundleCoatingKey } from "./pipe-bundle-model";
import { pipeBundleProjectionStops, projectPipeBundlePoint, pipeBundleTransitionHandles } from "./pipe-bundle-projection";
import { moveBundleCovering, bundleSpanEdgeVisible } from "./covering-motion";
import {hasJoiningPipeProjection,joiningPipeWidth} from "./physical-joining-pipe-projection";
import { conformalCoveringContour, squareConformalContourEnds } from "./covering-contour";
import { coveringRidges, type CoveringRidge } from "./covering-relief";

export interface CoveringHandle { readonly objectId:string; readonly spanIndex:number; readonly part:"from"|"to"|"transition-from"|"transition-to"; readonly point:Point; readonly normal:Point; readonly halfWidth:number; readonly rightHalfWidth?:number; readonly pointMarker?:boolean; readonly bound:boolean }
export interface CoveringSurface { readonly polygon:readonly Point[]; readonly path:readonly Point[]; readonly spanIndex?:number; readonly openStart?:boolean; readonly openEnd?:boolean; readonly conformal?:boolean; readonly ridges?:readonly CoveringRidge[] }
export type CoveringDragPart="from"|"to"|"body"|"transition-from"|"transition-to";
type TailSample = { readonly at:number; readonly side:"from"|"to"|"core"; readonly members:readonly {readonly point:Point;readonly radius:number}[] };
type JoiningOutset = {readonly from:number;readonly to:number;readonly width:number};

function joiningSupportCells(samples:readonly TailSample[],from:number,to:number,outsets:readonly JoiningOutset[]):Point[][] {
 const result:Point[][]=[];
 const between=(a:Point,b:Point,t:number):Point=>({x:a.x+(b.x-a.x)*t,y:a.y+(b.y-a.y)*t});
 const disk=(point:Point,radius:number):Point[]=>Array.from({length:12},(_,i)=>({
  x:point.x+Math.cos(i*Math.PI/6)*radius,y:point.y+Math.sin(i*Math.PI/6)*radius}));
 // At each OP junction the sorted envelope contains equal-distance samples
 // from both sides. Group them first so those duplicates cannot hide the
 // first tail section or the whole core when looking for neighbouring cells.
 const grouped=(["from","core","to"] as const).flatMap(side=>samples.filter(sample=>sample.side===side));
 for(let i=1;i<grouped.length;i++){
  const a=grouped[i-1]!,b=grouped[i]!;
  if(a.side!==b.side||a.at>=to||b.at<=from||b.at-a.at<1e-7)continue;
  const first=Math.max(from,a.at),last=Math.min(to,b.at);
  if(last-first<1e-7)continue;
  const cuts=[first,...outsets.flatMap(item=>[item.from,item.to]).filter(at=>at>first&&at<last),last].sort((x,y)=>x-y);
  for(let stop=1;stop<cuts.length;stop++){
   const lo=cuts[stop-1]!,hi=cuts[stop]!,start=(lo-a.at)/(b.at-a.at),end=(hi-a.at)/(b.at-a.at);
   const width=Math.max(...outsets.filter(item=>(lo+hi)/2>item.from&&(lo+hi)/2<item.to).map(item=>item.width),0);
   const section:Point[]=[];
   const capFrom=Math.abs(lo-from)<1e-7;
   const capTo=Math.abs(hi-to)<1e-7;
   const startMembers=a.members.map((member,index)=>({
    point:between(member.point,b.members[index]!.point,start),radius:member.radius+(b.members[index]!.radius-member.radius)*start}));
   const endMembers=a.members.map((member,index)=>({
    point:between(member.point,b.members[index]!.point,end),radius:member.radius+(b.members[index]!.radius-member.radius)*end}));
   const cap=(members:readonly {readonly point:Point;readonly radius:number}[],inside:readonly {readonly point:Point;readonly radius:number}[])=>{
    if(!members.length||!inside.length)return;
    const center=(value:readonly {readonly point:Point}[]):Point=>({
     x:value.reduce((sum,member)=>sum+member.point.x,0)/value.length,
     y:value.reduce((sum,member)=>sum+member.point.y,0)/value.length});
    const origin=center(members),next=center(inside),dx=origin.x-next.x,dy=origin.y-next.y,length=Math.hypot(dx,dy)||1;
    const tangent={x:dx/length,y:dy/length},normal={x:-tangent.y,y:tangent.x};
    const memberTangents=members.map((member,index)=>{
     const other=inside[index]!,x=member.point.x-other.point.x,y=member.point.y-other.point.y,size=Math.hypot(x,y);
     return size>1e-7?{x:x/size,y:y/size}:tangent;
    });
    const aligned=memberTangents.every(direction=>direction.x*tangent.x+direction.y*tangent.y>.98);
    if(!aligned){
     members.forEach((member,index)=>{
      const local=memberTangents[index]!,side={x:-local.y,y:local.x},reach=member.radius+width;
      const front={x:member.point.x+local.x*reach,y:member.point.y+local.y*reach};
      const back={x:member.point.x-local.x*reach,y:member.point.y-local.y*reach};
      section.push({x:back.x-side.x*reach,y:back.y-side.y*reach},
       {x:back.x+side.x*reach,y:back.y+side.y*reach},
       {x:front.x-side.x*reach,y:front.y-side.y*reach},
       {x:front.x+side.x*reach,y:front.y+side.y*reach});
     });
     return;
    }
    const front=Math.max(0,...members.map(member=>(member.point.x-origin.x)*tangent.x+
      (member.point.y-origin.y)*tangent.y+member.radius+width));
    const back=Math.min(0,...members.map(member=>(member.point.x-origin.x)*tangent.x+
      (member.point.y-origin.y)*tangent.y-member.radius-width));
    const transverse=members.flatMap(member=>{
      const offset=(member.point.x-origin.x)*normal.x+(member.point.y-origin.y)*normal.y;
      return [offset-member.radius-width,offset+member.radius+width];
    });
    const minimum=Math.min(0,...transverse),maximum=Math.max(0,...transverse);
    const frontCenter={x:origin.x+tangent.x*front,y:origin.y+tangent.y*front};
    const backCenter={x:origin.x+tangent.x*back,y:origin.y+tangent.y*back};
    section.push({x:backCenter.x+normal.x*minimum,y:backCenter.y+normal.y*minimum},
      {x:backCenter.x+normal.x*maximum,y:backCenter.y+normal.y*maximum},
      {x:frontCenter.x+normal.x*minimum,y:frontCenter.y+normal.y*minimum},
      {x:frontCenter.x+normal.x*maximum,y:frontCenter.y+normal.y*maximum});
   };
   if(capFrom)cap(startMembers,endMembers);
   if(capTo)cap(endMembers,startMembers);
   for(let member=0;member<a.members.length;member++){
    const one=a.members[member]!,two=b.members[member]!;
    if(!two)continue;
    const p=between(one.point,two.point,start),q=between(one.point,two.point,end);
    const pr=one.radius+width,qr=two.radius+width;
    section.push(...disk(p,pr),...disk(q,qr));
    // The two end disks alone leave a gap when a narrow member turns sharply
    // between envelope stations. Add the local capsule sides so the OP shell
    // follows the member continuously through the stepped transition.
    const dx=q.x-p.x,dy=q.y-p.y,length=Math.hypot(dx,dy)||1,n={x:-dy/length,y:dx/length};
    section.push({x:p.x+n.x*pr,y:p.y+n.y*pr},{x:p.x-n.x*pr,y:p.y-n.y*pr},
      {x:q.x+n.x*qr,y:q.y+n.y*qr},{x:q.x-n.x*qr,y:q.y-n.y*qr});
   }
   if(section.length)result.push(section);
  }
 }
 return result;
}
function endpointGripPoint(polygon:readonly Point[],point:Point,tangent:Point,part:"from"|"to"):Point {
 const facing=polygon.filter(p=>((p.x-point.x)*tangent.x+(p.y-point.y)*tangent.y)*(part==="to"?1:-1)>1e-6);
 const candidates=facing.length?facing:polygon;
 return candidates.reduce((nearest,p)=>Math.hypot(p.x-point.x,p.y-point.y)<Math.hypot(nearest.x-point.x,nearest.y-point.y)?p:nearest,candidates[0]??point);
}
/** Round generated OP→P transitions after projection. The source route is
 * rounded before projection, but the shoulder points are created afterwards;
 * sampling this visible control polygon applies the same radius regulator to
 * those shoulders as to authored pipe bends. */
function roundProjectedCenterline(points:readonly Point[],distances:readonly number[],radius:number):readonly {point:Point;distance:number}[] {
 if(points.length<3||radius<=0)return points.map((point,index)=>({point,distance:distances[index]!}));
 const controlDistances:number[]=[];let control=0;
 points.forEach((point,index)=>{if(index)control+=Math.hypot(point.x-points[index-1]!.x,point.y-points[index-1]!.y);controlDistances.push(control);});
 const sourceAt=(value:number)=>{
  const index=controlDistances.findIndex(distance=>distance>=value);if(index<0)return distances.at(-1)!;
  if(index===0)return distances[0]!;
  const a=controlDistances[index-1]!,b=controlDistances[index]!,t=(value-a)/(b-a||1);
  return distances[index-1]!+(distances[index]!-distances[index-1]!)*t;
 };
 const rounded=drawingRouteSamples(points,radius).map(sample=>({point:sample.point,distance:sourceAt(sample.distance)}));
 rounded[0]={point:points[0]!,distance:distances[0]!};
 rounded[rounded.length-1]={point:points.at(-1)!,distance:distances.at(-1)!};
 return rounded;
}

/** Offset edges are shared by the filled surface and endpoint grips. A sharp
 * transition can otherwise create a long miter spike when the covering is
 * wide; the cap turns that corner into a short bevel while preserving the
 * authored centreline and all endpoint positions. */
export function offsetPolyline(points:readonly Point[],offsets:readonly number[]):Point[] {
 // End caps use the first/last non-zero tangent explicitly. This keeps the
 // ordinary covering end square even when a route contains duplicate
 // control points or a very short first segment. Interior joins retain the
 // bounded miter used for local bends and transition ramps.
 const normalBetween=(a:Point,b:Point):Point|null=>{
  const length=Math.hypot(b.x-a.x,b.y-a.y);
  return length>1e-9?{x:-(b.y-a.y)/length,y:(b.x-a.x)/length}:null;
 };
 const normalAt=(index:number):{normal:Point;factor:number}=>{
  let before=index-1;while(before>=0&&!normalBetween(points[before]!,points[index]!))before--;
  let after=index+1;while(after<points.length&&!normalBetween(points[index]!,points[after]!))after++;
  const u=before>=0?normalBetween(points[before]!,points[index]!):null;
  const v=after<points.length?normalBetween(points[index]!,points[after]!):null;
  if(!u&&!v)return {normal:{x:0,y:1},factor:1};
  if(!u)return {normal:v!,factor:1};
  if(!v)return {normal:u,factor:1};
  const n={x:u.x+v.x,y:u.y+v.y},len=Math.hypot(n.x,n.y)||1;
  const normal={x:n.x/len,y:n.y/len};
  return {normal,factor:Math.min(1.5,1/Math.max(.25,normal.x*v.x+normal.y*v.y))};
 };
 return points.map((p,i)=>{
  const {normal,factor}=normalAt(i),amount=offsets[i]??0;
  return {x:p.x+normal.x*amount*factor,y:p.y+normal.y*amount*factor};
 });
}

export function coveringScene(document:HarnessDesignDocument):EditorSceneObject[] {
 const topology=document.physicalTopology;if(!topology)return [];
 const scale=drawingPhysicalScale(document),sourceCoverings=topology.coverings??[];
 const diameterRatio=coveringDiameterRatio(document),coveringClearance=.5*scale;
 // Physical stacking of ordinary coatings is authored by array order. Bundle
 // shells are painted after their contained groups regardless of save order.
 const coverings:PhysicalCovering[]=sourceCoverings.filter(c=>!c.bundle),seen=new Set(coverings.map(c=>c.id));
 const addBundle=(covering:PhysicalCovering)=>{if(seen.has(covering.id))return;seen.add(covering.id);
   for(const member of covering.bundle?.members??[])if(member.kind==='covering'){const inner=sourceCoverings.find(c=>c.id===member.id);if(inner){
     for(const layer of sourceCoverings)if(layer.bundle&&pipeBundleCoatingKey(layer)===pipeBundleCoatingKey(inner))addBundle(layer);
   }}
   coverings.push(covering);};
 for(const covering of sourceCoverings)if(covering.bundle)addBundle(covering);
 const bundleSections=pipeBundleSections(document);
 const supportsBySegment=new Map<string,WidthSupport[]>();
 const joiningOutsetsBySegment=new Map<string,JoiningOutset[]>();
 return coverings.map((covering,order)=>{
  const handles:CoveringHandle[]=[],surfaces:CoveringSurface[]=[],paths:Point[][]=[];
  const ownSupports:{segmentId:string;support:WidthSupport}[]=[];
  const ownJoiningOutsets:{segmentId:string;outset:JoiningOutset}[]=[];
  let maximumWidth=0;
  const bundleAxis=covering.bundle?pipeBundleAxisPath(covering,sourceCoverings):undefined;
  for(const [spanIndex,original] of covering.spans.entries()){
   if(bundleAxis&&!bundleAxis.includes(original.segmentId))continue;
   const s=resolvedCoveringSpan(document,original),route=coveringRoute(document,s.segmentId),segment=topology.segments.find(p=>p.id===s.segmentId),joining=topology.joiningPipes?.find(p=>p.id===s.segmentId);if(!route||!segment&&!joining)continue;
   const from=Math.max(route.min,s.from),to=Math.min(route.max,s.to);if(from>=to)continue;
   const groupedWidth=bundleSections.get(covering.id)?.width;
   const pipeWidth=groupedWidth??(segment?drawingPipeWidth(document,segment):joiningPipeWidth(document,joining!)),lanes=segment?segmentWireLanes(document,segment.id):[];
   const bundle=lanes.length?2*Math.max(...lanes.map(l=>Math.abs(l.offset)+l.width/2)):pipeWidth;
   const automaticBandage=covering.width===0&&coveringKind(covering)==="band";
   const lowerCoverings=joining?[]:coverings.slice(0,order);
   // OP shells are independent authored objects. An overlapping predecessor
   // marks this layer as installed over an OP shell, but must not become a
   // geometry support: changing one inspector width must leave every other
   // OP shell unchanged.
   const overOpCovering=!!joining&&coverings.slice(0,order).some(lower=>lower.spans.some(ls=>{
      const r=resolvedCoveringSpan(document,ls);return ls.segmentId===s.segmentId&&r.from<to&&r.to>from;
    }));
   // Thread bandage already has its own automatic 2% allowance. Do not
   // apply a second allowance when it is installed over an OP shell.
   const widthMultiplier=(automaticBandage||(overOpCovering&&coveringKind(covering)!=="band"))?1.02:1;
   const halfAt=(fraction:number):number=>{
    if(groupedWidth!==undefined)return groupedWidth/2;
    let width=pipeWidth;
    if(coveringKind(covering)==="heat-shrink"&&(fraction<0||fraction>1)) width=joining?Math.max(width,bundle):bundle;
    // Array order encloses ordinary P coatings. OP coatings intentionally
    // do not inherit one another's contours; their width fields are local.
    for(const lower of lowerCoverings) {
     if(lower.spans.some(ls=>{const r=resolvedCoveringSpan(document,ls);return ls.segmentId===s.segmentId&&fraction>=r.from&&fraction<=r.to;})) {
      width=Math.max(lower.width*scale,width+coveringClearance);
     }
    }
    return (width+.5*scale)/2*widthMultiplier;
   };
   const boundaries=[0,1,...route.envelope.map(sample=>(sample.at-route.before)/route.length),...lowerCoverings.flatMap(lower=>lower.spans.filter(ls=>ls.segmentId===s.segmentId).flatMap(ls=>{const r=resolvedCoveringSpan(document,ls);return [r.from,r.to];}))];
   const fitted=encloseWidthProfiles(
     coveringWidthProfile(route.min*route.length,route.max*route.length,boundaries.map(f=>f*route.length),distance=>halfAt(distance/route.length)),
     joining?[]:supportsBySegment.get(s.segmentId)??[],coveringClearance/2);
   // The width field controls the largest diameter. Every smaller diameter
   // grows with it, at 1/ratio of the increment per adjacent support level.
   const levels=[...new Set(fitted.map(p=>p.halfWidth))].sort((a,b)=>b-a);
   const growth=Math.max(0,covering.width*scale*widthMultiplier/2-(levels[0]??0));
   const profile=fitted.map(p=>({...p,halfWidth:p.halfWidth+growth/Math.pow(diameterRatio,levels.indexOf(p.halfWidth))}));
   if(!joining)ownSupports.push({segmentId:s.segmentId,support:{from:from*route.length,to:to*route.length,profile}});
   const stops=[...profile.map(p=>route.before+p.at),...[...coveringControlFractions(document,s.segmentId),...pipeBundleProjectionStops(document,s.segmentId,covering.id)].map(f=>route.before+f*route.length)];
   const display=drawingRouteSection(route.points,hasJoiningPipeProjection(document,s.segmentId)?0:drawingBendRadius(document),route.before+from*route.length,route.before+to*route.length,stops);
   const projected=display.map(p=>projectPipeBundlePoint(document,s.segmentId,(p.distance-route.before)/route.length,p.point,covering.id));
   const visible=hasJoiningPipeProjection(document,s.segmentId)
     ? roundProjectedCenterline(projected,display.map(p=>p.distance),drawingBendRadius(document))
     : projected.map((point,index)=>({point,distance:display[index]!.distance}));
   const centerline=visible.map(sample=>sample.point);if(centerline.length<2)continue;
   if(joining&&Math.abs(from)<1e-9)centerline[0]=joining.start;
   if(joining&&Math.abs(to-1)<1e-9)centerline[centerline.length-1]=joining.end;
   const widths=visible.map(sample=>profileHalfWidth(profile,sample.distance-route.before));
   for(const width of widths)maximumWidth=Math.max(maximumWidth,2*width);
   const left=offsetPolyline(centerline,widths),right=offsetPolyline(centerline,widths.map(w=>-w));
   const polygon=[...left,...right.reverse()];
   const segmentFrom=route.before+from*route.length,segmentTo=route.before+to*route.length;
   const lowerOutsets=joiningOutsetsBySegment.get(s.segmentId)??[];
   const limits=[segmentFrom,...lowerOutsets.flatMap(item=>[item.from,item.to]).filter(at=>at>segmentFrom&&at<segmentTo),segmentTo].sort((a,b)=>a-b);
   const outsets:JoiningOutset[]=[];
   if(joining)for(let i=1;i<limits.length;i++){
    const start=limits[i-1]!,end=limits[i]!,mid=(start+end)/2;
    const lower=Math.max(0,...lowerOutsets.filter(item=>mid>item.from&&mid<item.to).map(item=>item.width));
    // Keep the OP transition shoulder deliberately small. The global control
    // lets the drawing owner tune the extra edge allowance without changing
    // authored widths or the pipe geometry.
    const edgeAllowance=document.drawingDocuments?.opCoveringEdgePx??2;
    const outset={from:start,to:end,width:Math.max(edgeAllowance+growth,lower+edgeAllowance)};
    outsets.push(outset);ownJoiningOutsets.push({segmentId:s.segmentId,outset});
   }
   const supports=joining?joiningSupportCells(route.envelope,segmentFrom,segmentTo,outsets):[];
   const contour=joining
     ? (from>=-1e-9&&to<=1+1e-9
       ? squareConformalContourEnds(conformalCoveringContour(centerline,widths,widths,supports),centerline)
       : conformalCoveringContour(centerline,widths,widths,supports))
     : polygon;
   surfaces.push({polygon:contour,path:centerline,spanIndex,
     ...(joining?{conformal:true,ridges:coveringRidges(route.envelope,segmentFrom,segmentTo,coveringClearance+growth,centerline[0]!)}:{}),
     ...(!bundleSpanEdgeVisible(document,covering,spanIndex,'from')?{openStart:true}:{}),
     ...(!bundleSpanEdgeVisible(document,covering,spanIndex,'to')?{openEnd:true}:{})});paths.push(centerline);
   for(const part of ["from","to"] as const){if(!bundleSpanEdgeVisible(document,covering,spanIndex,part))continue;const i=part==="from"?0:centerline.length-1,p=centerline[i]!,q=centerline[part==="from"?1:i-1]!,dx=part==="from"?q.x-p.x:p.x-q.x,dy=part==="from"?q.y-p.y:p.y-q.y,len=Math.hypot(dx,dy)||1;
    const at=visible[i]!.distance,near=route.envelope.findIndex(sample=>sample.at>=at),a=route.envelope[Math.max(0,near<0?route.envelope.length-1:near-1)],b=route.envelope[near<0?route.envelope.length-1:near];
    const t=a&&b?Math.max(0,Math.min(1,(at-a.at)/(b.at-a.at||1))):0;
    const leftSpread=a&&b?a.leftSpread+(b.leftSpread-a.leftSpread)*t:0,rightSpread=a&&b?a.rightSpread+(b.rightSpread-a.rightSpread)*t:0;
    const gripPoint=joining?endpointGripPoint(contour,p,{x:dx/len,y:dy/len},part):p;
    handles.push({objectId:covering.id,spanIndex,part,point:gripPoint,normal:{x:-dy/len,y:dx/len},halfWidth:Math.max(widths[i]!,leftSpread+coveringClearance),...(joining?{rightHalfWidth:Math.max(widths[i]!,rightSpread+coveringClearance),pointMarker:true}:{}),bound:original[part==="from"?"fromAnchor":"toAnchor"]!==undefined});}
  }
  if(covering.bundle)handles.push(...pipeBundleTransitionHandles(document,covering.id));
  for(const {segmentId,support} of ownSupports) supportsBySegment.set(segmentId,[...(supportsBySegment.get(segmentId)??[]),support]);
  for(const {segmentId,outset} of ownJoiningOutsets)joiningOutsetsBySegment.set(segmentId,[...(joiningOutsetsBySegment.get(segmentId)??[]),outset]);
  return {id:covering.id,kind:"physical-covering",layerId:"wires",x:0,y:0,width:maximumWidth,height:0,color:covering.color,label:covering.name,paths,points:paths.flat(),routeRadius:0,metadata:{coveringKind:coveringKind(covering),supportSegmentIds:JSON.stringify([...new Set(covering.spans.map(span=>span.segmentId))]),...(document.drawingDocuments?.volumeShading === false ? {volumeShading:"false"} : {}),coveringStyle:JSON.stringify({...covering.style,texture:!covering.style?.texture||covering.style.texture==="auto"?document.drawingDocuments?.coveringLibrary?.defaults[coveringKind(covering)]?.texture??"auto":covering.style.texture}),surfaces:JSON.stringify(surfaces),coveringHandles:JSON.stringify(handles)}};
 });
}

export function moveCovering(document:HarnessDesignDocument,id:string,spanIndex:number,part:CoveringDragPart,start:Point,point:Point,tolerance=10):PhysicalCovering|null {
 const c=document.physicalTopology?.coverings?.find(c=>c.id===id),original=c?.spans[spanIndex];if(!c||!original)return null;
 if(part==="transition-from"||part==="transition-to"){
  if(!c.bundle)return null;
  const grip=pipeBundleTransitionHandles(document,c.id).find(h=>h.part===part&&h.spanIndex===spanIndex);if(!grip)return null;
  const key=part==="transition-from"?"transitionBendStart":"transitionBendEnd";
  const previous=c.bundle[key]??{x:0,y:0};
  const dx=point.x-start.x,dy=point.y-start.y,along=dx*grip.tangent.x+dy*grip.tangent.y;
  const normal=dx*grip.normal.x+dy*grip.normal.y;
  const lengthKey=part==="transition-from"?"transitionStart":"transitionEnd";
  const length=Math.max(.001,Math.min(.5,grip.fraction+(part==="transition-from"?-along:along)/grip.axisLength));
  return {...c,bundle:{...c.bundle,[lengthKey]:length,
    [key]:{x:previous.x+normal*grip.normal.x,y:previous.y+normal*grip.normal.y}}};
 }
 const bundleMove=moveBundleCovering(document,c,spanIndex,part,start,point,tolerance);if(bundleMove)return bundleMove;
 const route=coveringRoute(document,original.segmentId);if(!route)return null;
 const s=resolvedCoveringSpan(document,original);
 const display=drawingRouteSection(route.points,drawingBendRadius(document),0,route.total,
   pipeBundleProjectionStops(document,original.segmentId,c.id).map(f=>route.before+f*route.length))
   .map(p=>({distance:p.distance,point:projectPipeBundlePoint(document,original.segmentId,(p.distance-route.before)/route.length,p.point,c.id)}));
 const project=(point:Point)=>{
   let nearest=Infinity,at=0;
   for(let i=1;i<display.length;i++){const a=display[i-1]!,b=display[i]!,dx=b.point.x-a.point.x,dy=b.point.y-a.point.y;
     const t=Math.max(0,Math.min(1,((point.x-a.point.x)*dx+(point.y-a.point.y)*dy)/(dx*dx+dy*dy||1)));
     const distance=Math.hypot(point.x-a.point.x-t*dx,point.y-a.point.y-t*dy);
     if(distance<nearest){nearest=distance;at=a.distance+(b.distance-a.distance)*t;}
   }
   return (at-route.before)/route.length;
 };
 const delta=project(point)-project(start),minimum=Math.min(.001,(s.to-s.from)/4);
 let from=s.from,to=s.to,fromAnchor=original.fromAnchor,toAnchor=original.toAnchor;
 if(part==="body"){
  const shift=Math.max(route.min-from,Math.min(route.max-to,delta));from+=shift;to+=shift;fromAnchor=toAnchor=undefined;
 }else {
  const target=Math.max(part==="from"?route.min:from+minimum,Math.min(part==="to"?route.max:to-minimum,(part==="from"?from:to)+delta));
  const fractions=coveringControlFractions(document,s.segmentId);
  const anchor=fractions.findIndex(f=>Math.abs(f-target)*route.length<=tolerance&&(part==="from"?f<to:f>from));
  if(part==="from"){from=anchor<0?target:fractions[anchor]!;fromAnchor=anchor<0?undefined:anchor;}else {to=anchor<0?target:fractions[anchor]!;toAnchor=anchor<0?undefined:anchor;}
 }
 return {...c,spans:c.spans.map((span,i)=>i===spanIndex?{...span,from,to,fromAnchor,toAnchor}:span)};
}

/** Wires run parallel inside shrink extensions, then fan out beyond the sleeve edge. */
export function wireExitPath(document:HarnessDesignDocument,segmentId:string,nodeSide:"from"|"to",wireId:string,contact:Point):Point[]|null {
 const route=coveringRoute(document,segmentId);if(!route)return null;
 const fractions=(document.physicalTopology?.coverings??[]).filter(c=>coveringKind(c)==="heat-shrink").flatMap(c=>c.spans.filter(s=>s.segmentId===segmentId).map(s=>resolvedCoveringSpan(document,s)));
 const end=nodeSide==="from"?Math.max(route.min,Math.min(0,...fractions.filter(s=>s.from<0&&s.to>=0).map(s=>s.from))):Math.min(route.max,Math.max(1,...fractions.filter(s=>s.to>1&&s.from<=1).map(s=>s.to)));
 if(nodeSide==="from"?end===0:end===1)return null;
 const a=Math.min(nodeSide==="from"?0:1,end),b=Math.max(nodeSide==="from"?0:1,end);
 const path=trimPolyline(route.points,(route.before+a*route.length)/route.total,(route.before+b*route.length)/route.total);
 const lane=segmentWireLanes(document,segmentId).find(l=>l.id===wireId)?.offset??0;
 const points=offsetPolyline(path,path.map(()=>lane));
 return nodeSide==="from"?[contact,...points]:[...points,contact];
}
