import type { HarnessDesignDocument, Point } from "./model";
import type { PhysicalSegment } from "./physical-topology-model";
import { physicalSegmentPoints, physicalSegmentControls } from "./physical-geometry";
import { physicalNodePoint } from "./physical-ports";
import { measuredWireLength } from "./drawing-dimensions";
import { resolvedCoveringSpan } from "./physical-coverings";
import { joiningMemberPoints } from "./physical-joining-pipes";
import type { JoiningPipeMember } from "./physical-topology-model";
import { projectPipeBundleControls } from "./pipe-bundle-projection";

type BendRegion=NonNullable<JoiningPipeMember["authoredBendRegions"]>[number]["region"];
function regionAt(fraction:number,from:number,to:number):BendRegion {
  return fraction<from/2?"before-enter":fraction<from?"enter":fraction<=to?"axis":fraction<(1+to)/2?"exit":"after-exit";
}
function memberBendRegions(document:HarnessDesignDocument,member:JoiningPipeMember){
  if(member.authoredBendRegions)return member.authoredBendRegions.map(entry=>{
    if(entry.displayPoint)return entry;
    const segment=document.physicalTopology!.segments.find(item=>item.id===entry.segmentId)!;
    return {...entry,displayPoint:projectPipeBundleControls(document,entry.segmentId,[segment.path.points[entry.bendIndex]!])[0]!};
  });
  const route=joiningMemberPoints(document,member.segmentIds),length=route.slice(1).reduce((sum,p,i)=>sum+Math.hypot(p.x-route[i]!.x,p.y-route[i]!.y),0);
  let travelled=0;
  return member.segmentIds.flatMap(id=>{
    const segment=document.physicalTopology!.segments.find(item=>item.id===id)!,points=physicalSegmentPoints(document,segment);
    return points.slice(1).flatMap((p,index)=>{
      travelled+=Math.hypot(p.x-points[index]!.x,p.y-points[index]!.y);
      const authored=index<segment.path.points.length;
      return authored?[{segmentId:id,bendIndex:index,region:regionAt(length?travelled/length:0,member.from,member.to),
        displayPoint:projectPipeBundleControls(document,id,[p])[0]!}]:[];
    });
  });
}

export type PhysicalDragMode = "carry" | "adjacent";
const near = (a:Point,b:Point) => Math.hypot(a.x-b.x,a.y-b.y)<1e-6;
const shifted = (p:Point,d:Point):Point => ({x:p.x+d.x,y:p.y+d.y});
const TAU=Math.PI*2;
const angleDistance=(a:number,b:number)=>{
  const d=Math.abs((a-b)%TAU);
  return Math.min(d,TAU-d);
};

/** Direction state kept for one pointer gesture. It prevents a bend from
 * switching to a distant intersection while the pointer is moving along the
 * currently selected shoulder. */
export interface BendSnapState {
  previous?:Point;
  lastPointer?:Point;
  active?:readonly {anchor:Point;direction:Point}[];
}

function snappedDirectionIndex(angle:number,angleStep:number):number {
  const count=Math.max(1,Math.round(TAU/angleStep));
  const normalized=((angle%TAU)+TAU)%TAU;
  const axes=[0,Math.PI/2,Math.PI,3*Math.PI/2];
  // The cardinal sectors have priority over the regular midpoint rule.
  const axis=axes.find(a=>angleDistance(normalized,a)<=Math.PI/9+1e-9);
  const chosen=axis===undefined?Math.round(normalized/angleStep)*angleStep:axis;
  return ((Math.round(chosen/angleStep)%count)+count)%count;
}

function directionFor(a:Point,p:Point,directions:readonly Point[],angleStep:number):Point {
  const dx=p.x-a.x,dy=p.y-a.y;
  if(Math.hypot(dx,dy)<1e-8)return directions[0]!;
  return directions[snappedDirectionIndex(Math.atan2(dy,dx),angleStep)]!;
}

/**
 * Automatic corners become author-owned only when an edit is committed.  This
 * includes a member P which is currently projected through an OP: the source
 * route may still be automatic even though its visible projection contains
 * several corners.
 */
export function physicalEditablePoints(document:HarnessDesignDocument,segment:PhysicalSegment,materializeAutomatic=false):readonly Point[] {
  if(document.physicalTopology?.joiningPipes?.some(pipe=>pipe.members.some(member=>member.segmentIds.includes(segment.id)))&&!materializeAutomatic)
    return physicalSegmentControls(document,segment);
  return segment.path.kind==="routed"&&!segment.path.points.length&&document.physicalTopology?.snap
    ? physicalSegmentPoints(document,segment) : physicalSegmentControls(document,segment);
}

/** A dimension owns its visible corners; freeze them in the same undo transaction. */
export function materializePhysicalPath(document:HarnessDesignDocument,id:string):HarnessDesignDocument {
  const segment=document.physicalTopology?.segments.find(s=>s.id===id);
  if(!segment||segment.path.kind==="polyline")return document;
  const points=physicalEditablePoints(document,segment,true);
  return replacePath(document,segment,points,anchorMap(document,segment,points));
}

/** Snap the dragged point to the requested angular directions. */
export function snapPhysicalPoint(point:Point,anchors:readonly Point[],enabled:boolean,tolerance:number,angleStep=Math.PI/12) {
  if(!enabled||!anchors.length)return {point,guide:undefined as readonly Point[]|undefined};
  let best:{point:Point;guide:readonly Point[];distance:number}|undefined;
  for(const a of anchors) {
    const index=snappedDirectionIndex(Math.atan2(point.y-a.y,point.x-a.x),angleStep);
    const angle=index*angleStep,u={x:Math.cos(angle),y:Math.sin(angle)},length=(point.x-a.x)*u.x+(point.y-a.y)*u.y;
    const p={x:a.x+u.x*length,y:a.y+u.y*length},distance=Math.hypot(p.x-point.x,p.y-point.y);
    if(distance<=tolerance&&(!best||distance<best.distance))best={point:p,guide:[a,p],distance};
  }
  if(best)return best;
  const a=anchors.reduce((closest,current)=>Math.hypot(current.x-point.x,current.y-point.y)<Math.hypot(closest.x-point.x,closest.y-point.y)?current:closest,anchors[0]!);
  const index=snappedDirectionIndex(Math.atan2(point.y-a.y,point.x-a.x),angleStep),angle=index*angleStep;
  const length=Math.hypot(point.x-a.x,point.y-a.y);
  return {point:{x:a.x+Math.cos(angle)*length,y:a.y+Math.sin(angle)*length},guide:undefined};
}

/** The moved shoulder group is rigid in carry mode. Each boundary constrains
 * the pointer relative to a virtual anchor translated by that shoulder offset. */
export function bendSnapAnchors(points:readonly Point[],index:number,insert:boolean,mode:PhysicalDragMode,displayOrigin?:Point):Point[] {
  const copy=[...points],at=index+1;
  if(insert){const a=copy[index]!,b=copy[at]!;copy.splice(at,0,{x:(a.x+b.x)/2,y:(a.y+b.y)/2});}
  const origin=copy[at];if(!origin)return [];
  const first=mode==="carry"?Math.max(1,at-1):at,last=mode==="carry"?Math.min(copy.length-2,at+1):at;
  return [[first,first-1],[last,last+1]].flatMap(([moving,fixed])=>{
    const a=copy[moving!]!,b=copy[fixed!];
    // Pointer deltas are later applied to authored vertices. Translate the
    // constraints into the displayed handle frame, without snapping the
    // generated convergence geometry of a bundled pipe.
    return b?[{x:b.x-a.x+(displayOrigin??origin).x,y:b.y-a.y+(displayOrigin??origin).y}]:[];
  });
}

/** Resolve a displayed member grip to its authored station before choosing
 * shoulders. OP-generated grips have no authored station and use only their
 * neighbouring visible shoulders. */
export function pipeBendSnapAnchors(pipe:{
  authoredPoints?:readonly Point[];handles:readonly Point[];
  joiningMember?:boolean;
  authoredHandleIndices?:readonly number[];
  joiningTransitionHandles?:readonly {index:number;side:"enter"|"exit"}[];
  joiningBoundaryHandles?:readonly {index:number}[];
  joiningTransitionMidpoints?:readonly {index:number}[];
  authoredHandleRegions?:readonly (BendRegion|undefined)[];
  midpointRegions?:readonly (BendRegion|undefined)[];
},displayPoints:readonly Point[],index:number,insert:boolean,mode:PhysicalDragMode,displayOrigin:Point):Point[] {
  const transition=!insert?pipe.joiningTransitionHandles?.find(handle=>handle.index===index):undefined;
  if(transition&&mode==="carry"){
    const at=index+1,step=transition.side==="enter"?-1:1;
    const outer=displayPoints[at+step],fixed=displayPoints[at+2*step],axis=displayPoints[at-step];
    if(outer&&fixed&&axis)return [
      {x:displayOrigin.x+fixed.x-outer.x,y:displayOrigin.y+fixed.y-outer.y},axis,
    ];
  }
  const generated=!!transition
    ||pipe.joiningBoundaryHandles?.some(handle=>handle.index===index)
    ||insert&&pipe.joiningTransitionMidpoints?.some(handle=>handle.index===index);
  if(generated)return bendSnapAnchors(displayPoints,index,insert,"adjacent",displayOrigin);
  if(insert&&pipe.joiningMember){
    const region=pipe.midpointRegions?.[index];
    return [-1,1].flatMap(direction=>{
      const neighbour=index+(direction<0?0:1),point=displayPoints[neighbour];
      if(!point)return [];
      const handleIndex=neighbour-1;
      const carried=mode==="carry"&&region!==undefined&&
        pipe.authoredHandleRegions?.[handleIndex]===region&&
        (pipe.authoredHandleIndices?.[handleIndex]??-1)>=1;
      if(!carried)return [point];
      const fixed=displayPoints[neighbour+direction];
      return fixed?[{x:displayOrigin.x+fixed.x-point.x,y:displayOrigin.y+fixed.y-point.y}]:[];
    });
  }
  const authoredIndex=pipe.authoredHandleIndices?.[index];
  if(!insert&&authoredIndex!==undefined&&authoredIndex>=1&&pipe.joiningMember){
    const at=index+1,region=pipe.authoredHandleRegions?.[index];
    return [-1,1].flatMap(direction=>{
      const neighbour=at+direction,point=displayPoints[neighbour];
      if(!point)return [];
      const handleIndex=neighbour-1;
      const carried=mode==="carry"&&region!==undefined&&
        pipe.authoredHandleRegions?.[handleIndex]===region&&
        (pipe.authoredHandleIndices?.[handleIndex]??-1)>=1;
      if(!carried)return [point];
      const fixed=displayPoints[neighbour+direction];
      return fixed?[{x:displayOrigin.x+fixed.x-point.x,y:displayOrigin.y+fixed.y-point.y}]:[];
    });
  }
  if(!insert&&authoredIndex!==undefined&&authoredIndex>=1&&pipe.authoredPoints)
    return bendSnapAnchors(pipe.authoredPoints,authoredIndex-1,false,mode,displayOrigin);
  return bendSnapAnchors(displayPoints,index,insert,mode,displayOrigin);
}

/** Intersect angular direction families and validate every changing shoulder.
 * Collinear supports retain continuous motion along the line. */
export function snapBendPoint(point:Point,anchors:readonly Point[],enabled:boolean,tolerance:number,fallback?:Point,angleStep=Math.PI/12,state?:BendSnapState,strategy:"all"|"nearest-compatible"="all") {
  const unique=anchors.filter((a,i)=>anchors.findIndex(b=>near(a,b))===i);
  if(enabled&&strategy==="nearest-compatible"&&unique.length){
    const candidates=unique.map(anchor=>{
      const direction=snappedDirectionIndex(Math.atan2(point.y-anchor.y,point.x-anchor.x),angleStep)*angleStep;
      const unit={x:Math.cos(direction),y:Math.sin(direction)};
      const along=(point.x-anchor.x)*unit.x+(point.y-anchor.y)*unit.y;
      const projected={x:anchor.x+along*unit.x,y:anchor.y+along*unit.y};
      return {anchor,projected,distance:Math.hypot(projected.x-point.x,projected.y-point.y)};
    }).sort((a,b)=>a.distance-b.distance||Math.hypot(point.x-a.anchor.x,point.y-a.anchor.y)-Math.hypot(point.x-b.anchor.x,point.y-b.anchor.y));
    const chosen=candidates[0]!;
    if(state){state.active=undefined;state.previous=chosen.projected;state.lastPointer=point;}
    return {point:chosen.projected,guide:[chosen.anchor,chosen.projected]};
  }
  if(!enabled||unique.length<2){
    if(state){state.active=undefined;state.previous=undefined;state.lastPointer=point;}
    return snapPhysicalPoint(point,unique,enabled,tolerance,angleStep);
  }
  const a=unique[0]!;
  const directions=Array.from({length:Math.round((Math.PI*2)/angleStep)},(_,i)=>({x:Math.cos(i*angleStep),y:Math.sin(i*angleStep)}));
  let best:{point:Point;guide:readonly Point[];distance:number;active:readonly {anchor:Point;direction:Point}[]}|undefined;
  const add=(p:Point)=>{
    if(!Number.isFinite(p.x)||!Number.isFinite(p.y))return;
    if(unique.some(anchor=>{
      const dx=p.x-anchor.x,dy=p.y-anchor.y,length=Math.hypot(dx,dy);
      return length<1e-6||!directions.some(u=>Math.abs(dx*u.y-dy*u.x)<=1e-7*length);
    }))return;
    const distance=Math.hypot(p.x-point.x,p.y-point.y);
    const active=unique.map(anchor=>({anchor,direction:directionFor(anchor,p,directions,angleStep)}));
    if(!best||distance<best.distance-1e-7)best={point:p,guide:unique.flatMap((anchor,i)=>i?[p,anchor]:[anchor]),distance,active};
  };
  for(const b of unique.slice(1))for(const u of directions)for(const v of directions){
    const denominator=u.x*v.y-u.y*v.x,dx=b.x-a.x,dy=b.y-a.y;
    if(Math.abs(denominator)<1e-8){
      if(Math.abs(dx*u.y-dy*u.x)<1e-7){const t=(point.x-a.x)*u.x+(point.y-a.y)*u.y;add({x:a.x+t*u.x,y:a.y+t*u.y});}
    }else{const t=(dx*v.y-dy*v.x)/denominator;add({x:a.x+t*u.x,y:a.y+t*u.y});}
  }
  // Several fixed shoulders may admit no common angular point. Keep the
  // original position instead of silently violating one connected route.
  if(!state)return best??{point:fallback??point,guide:undefined};
  const previous=state.previous;
  const pointerDelta=state.lastPointer?{x:point.x-state.lastPointer.x,y:point.y-state.lastPointer.y}:undefined;
  state.lastPointer=point;
  if(!previous||!state.active){
    const result=best??{point:fallback??point,guide:undefined,active:undefined};
    if(result.active)state.active=result.active;
    state.previous=result.point;
    return {point:result.point,guide:result.guide};
  }
  const movement=Math.hypot(pointerDelta?.x??0,pointerDelta?.y??0);
  const candidate=best;
  const jump=candidate?Math.hypot(candidate.point.x-previous.x,candidate.point.y-previous.y):Infinity;
  const forward=candidate&&pointerDelta&&movement>1e-6
    ?(candidate.point.x-previous.x)*pointerDelta.x+(candidate.point.y-previous.y)*pointerDelta.y>=-1e-6
    :true;
  // A direction change is accepted only when it is local to the current
  // branch. Otherwise project onto the active shoulder; this preserves a
  // continuous drag and waits for the next valid intersection.
  const maxJump=Math.max(32,tolerance*8);
  const nearPointer=!!candidate&&candidate.distance<=maxJump;
  if(unique.length===2&&Math.abs(angleStep-Math.PI/6)<1e-9&&Math.abs(unique[0]!.y-unique[1]!.y)>1e-7){
    const [start,end]=unique,dx=end!.x-start!.x;
    // A horizontal shoulder from either anchor intersects a 30° shoulder
    // from the other. Offer that exact station while the pointer enters its
    // horizontal attraction band, including when approached from below.
    for(const anchor of [start!,end!]){
      const other=anchor===start?end!:start!;
      for(const slope of [Math.tan(angleStep),-Math.tan(angleStep)]){
        const x=other.x+(anchor.y-other.y)/slope;
        if((x-start!.x)*dx<0||(x-end!.x)*dx>0)continue;
        const horizontal={x,y:anchor.y};
        if(Math.abs(point.y-horizontal.y)>Math.max(tolerance*3,20))continue;
        const horizontalDistance=Math.hypot(point.x-x,point.y-anchor.y);
        if(horizontalDistance>maxJump*3)continue;
        if(candidate&&horizontalDistance>candidate.distance+Math.max(tolerance*3,20))continue;
        state.active=unique.map(a=>({anchor:a,direction:directionFor(a,horizontal,directions,angleStep)}));
        state.previous=horizontal;
        return {point:horizontal,guide:[anchor,horizontal]};
      }
    }
  }
  if(candidate&&forward&&(jump<=maxJump||nearPointer)){
    state.active=candidate.active;
    state.previous=candidate.point;
    return {point:candidate.point,guide:candidate.guide};
  }
  const projected=state.active.map(({anchor,direction})=>{
    const length=(point.x-anchor.x)*direction.x+(point.y-anchor.y)*direction.y;
    const p={x:anchor.x+direction.x*length,y:anchor.y+direction.y*length};
    const advance=pointerDelta?(p.x-previous.x)*pointerDelta.x+(p.y-previous.y)*pointerDelta.y:0;
    return {p,anchor,distance:Math.hypot(p.x-point.x,p.y-point.y),advance};
  }).filter(item=>!pointerDelta||movement<1e-6||item.advance>=-1e-6)
    .sort((left,right)=>left.distance-right.distance)[0];
  const result=projected?.p??candidate?.point??fallback??point;
  state.previous=result;
  return {point:result,guide:projected?[projected.anchor,projected.p]:undefined};
}

/** The same initially straight path must produce the same carried shoulders
 * in the interaction constraints and in the committed model. */
export function carriedExitPoints(points:readonly Point[],mode:PhysicalDragMode):readonly Point[]{
  if(mode!=='carry'||points.length!==2)return points;
  const [a,b]=points as readonly [Point,Point];
  return [a,{x:a.x+(b.x-a.x)/3,y:a.y+(b.y-a.y)/3},
    {x:a.x+2*(b.x-a.x)/3,y:a.y+2*(b.y-a.y)/3},b];
}

/** Only boundaries between translated and fixed vertices constrain a move.
 * References use topology IDs, never coincident coordinates or nearby nodes. */
export function physicalObjectRouteAnchors(objects:readonly {
  id:string;kind:string;x:number;y:number;points?:readonly Point[];
  port?:{connectorId?:string};pipe?:{fromNodeId?:string;toNodeId?:string;authoredPoints?:readonly Point[];handles:readonly Point[]};
}[],object:{id:string;kind:string;x:number;y:number},mode:PhysicalDragMode):Point[]{
  const ids=new Set(object.kind==='physical-node'?[object.id]:objects.filter(o=>o.kind==='physical-node'&&o.port?.connectorId===object.id).map(o=>o.id));
  return objects.flatMap(o=>{
    if(o.kind!=='physical-segment'||!o.pipe||!o.points?.length)return [];
    const from=!!o.pipe.fromNodeId&&ids.has(o.pipe.fromNodeId),to=!!o.pipe.toNodeId&&ids.has(o.pipe.toNodeId);
    if(!from&&!to)return [];
    // Bundle projection is presentation only. The committed move translates
    // authored vertices by the pointer delta, including a projected node origin.
    const points=carriedExitPoints(o.pipe.authoredPoints??[o.points[0]!,...o.pipe.handles,o.points.at(-1)!],mode);
    const moving=new Set<number>();
    if(from){moving.add(0);if(mode==='carry'&&points.length>2)moving.add(1);}
    if(to){moving.add(points.length-1);if(mode==='carry'&&points.length>2)moving.add(points.length-2);}
    return points.slice(1).flatMap((p,i)=>{
      if(moving.has(i)===moving.has(i+1))return [];
      const mobile=moving.has(i)?points[i]!:p,fixed=moving.has(i)?p:points[i]!;
      return [{x:object.x+fixed.x-mobile.x,y:object.y+fixed.y-mobile.y}];
    });
  });
}

/** Update ordinal anchors explicitly; never silently attach a measurement to another corner. */
function replacePath(document:HarnessDesignDocument,segment:PhysicalSegment,points:readonly Point[],oldToNew:ReadonlyMap<number,number>):HarnessDesignDocument {
  const t=document.physicalTopology!;
  const changed={...segment,path:{kind:"polyline" as const,points:points.slice(1,-1)}};
  const coverings=t.coverings?.map(c=>({...c,spans:c.spans.map(s=>s.segmentId!==segment.id?s:{...resolvedCoveringSpan(document,s),
    fromAnchor:s.fromAnchor===undefined?undefined:oldToNew.get(s.fromAnchor),
    toAnchor:s.toAnchor===undefined?undefined:oldToNew.get(s.toAnchor)})}));
  const stored=document.drawingDocuments?.dimensions;
  const affected=stored?.filter(d=>d.segmentId===segment.id&&!d.auxiliary)??[];
  const lost=affected.some(d=>!oldToNew.has(d.from)||!oldToNew.has(d.to));
  const source=lost&&affected.length?[...stored!.filter(d=>!affected.includes(d)),{...affected[0]!,from:0,to:affected[0]!.pointCount-1,lengthMm:measuredWireLength(affected)}]:stored;
  const dimensions=source?.flatMap(d=>{
    if(d.segmentId!==segment.id)return [d];
    const from=oldToNew.get(d.from),to=oldToNew.get(d.to);
    return from===undefined||to===undefined||from>=to?[]:[{...d,from,to,pointCount:points.length,
      routeKey:JSON.stringify([segment.id,segment.from,segment.to,points.length-2])}];
  });
  return {...document,physicalTopology:{...t,segments:t.segments.map(s=>s.id===segment.id?changed:s),coverings},
    ...(document.drawingDocuments?{drawingDocuments:{...document.drawingDocuments,dimensions}}:{})};
}

function anchorMap(document:HarnessDesignDocument,segment:PhysicalSegment,editable:readonly Point[]) {
  return new Map(physicalSegmentControls(document,segment).map((p,i)=>[i,
    i===0?0:i===segment.path.points.length+1?editable.length-1:segment.path.points.length?i:editable.findIndex(q=>near(p,q))]));
}

function mergeStraight(points:Point[],map:Map<number,number>) {
  for(let i=points.length-2;i>0;i--){
    const a=points[i-1]!,b=points[i]!,c=points[i+1]!;
    const cross=(b.x-a.x)*(c.y-b.y)-(b.y-a.y)*(c.x-b.x);
    const dot=(b.x-a.x)*(c.x-b.x)+(b.y-a.y)*(c.y-b.y);
    if(Math.abs(cross)<=1e-7*Math.max(1,Math.hypot(c.x-a.x,c.y-a.y))&&dot>=0){
      points.splice(i,1);
      for(const [old,value] of map){if(value===i)map.delete(old);else if(value>i)map.set(old,value-1);}
    }
  }
}

export function physicalObjectSnapAnchors(objects:readonly {id:string;kind:string;x:number;y:number;port?:{connectorId?:string}}[],object:{id:string;kind:string;x:number;y:number}):Point[] {
  if(object.kind!=="connector"&&object.kind!=="physical-node")return [];
  const own=object.kind==="physical-node"?[object]:objects.filter(o=>o.kind==="physical-node"&&o.port?.connectorId===object.id);
  return own.flatMap(exit=>objects.filter(o=>o.kind==="physical-node"&&o.id!==exit.id&&o.port?.connectorId!==object.id)
    .map(o=>({x:o.x-(exit.x-object.x),y:o.y-(exit.y-object.y)})));
}

/** OP endpoint drags use the opposite endpoint of the same OP as their local
 * angular reference. Presentation overlap with member pipes is irrelevant. */
export function joiningPipeEndpointSnapAnchors(objects:readonly {
  id:string;kind:string;x:number;y:number;metadata?:Readonly<Record<string,string>>;
}[],object:{id:string;kind:string;metadata?:Readonly<Record<string,string>>}):Point[] {
  const pipeId=object.metadata?.joiningPipe;
  if(object.kind!=="physical-node"||!pipeId)return [];
  const opposite=objects.find(candidate=>candidate.kind==="physical-node"
    &&candidate.metadata?.joiningPipe===pipeId&&candidate.id!==object.id);
  return opposite?[{x:opposite.x,y:opposite.y}]:[];
}

/** Preserve adjacent inner shoulders in carry mode; Shift edits only the selected vertex. */
export function editPhysicalBend(document:HarnessDesignDocument,id:string,index:number,point:Point,mode:PhysicalDragMode,insert=false,region?:BendRegion,displayPosition?:Point,displayOrigin?:Point):HarnessDesignDocument {
  const segment=document.physicalTopology?.segments.find(s=>s.id===id);if(!segment)return document;
  const frozen=(document.physicalTopology?.joiningPipes??[]).flatMap(pipe=>pipe.members.flatMap((member,memberIndex)=>{
    if(!member.segmentIds.includes(id))return [];
    const route=joiningMemberPoints(document,member.segmentIds),length=route.slice(1).reduce((sum,p,i)=>sum+Math.hypot(p.x-route[i]!.x,p.y-route[i]!.y),0);
    const at=(fraction:number)=>{let remaining=length*fraction;for(let i=1;i<route.length;i++){
      const a=route[i-1]!,b=route[i]!,span=Math.hypot(b.x-a.x,b.y-a.y);
      if(remaining<=span||i===route.length-1){const t=span?remaining/span:0;return {x:a.x+(b.x-a.x)*t,y:a.y+(b.y-a.y)*t};}remaining-=span;
    }return route[0]!;};
    return [{pipeId:pipe.id,memberIndex,enterOuter:member.enterOuter??at(member.from/2),exitOuter:member.exitOuter??at((1+member.to)/2),
      authoredBendRegions:memberBendRegions(document,member)}];
  }));
  const original=physicalEditablePoints(document,segment,true),map=anchorMap(document,segment,original);
  const points=[...original];
  const owned=frozen[0]?.authoredBendRegions??[];
  const selectedRegion=insert?region:owned.find(entry=>entry.segmentId===id&&entry.bendIndex===index)?.region;
  const sameRegion=(controlIndex:number)=>controlIndex>0&&controlIndex<original.length-1&&
    (!frozen.length||owned.some(entry=>entry.segmentId===id&&entry.bendIndex===controlIndex-1&&entry.region===selectedRegion));
  if(insert){
    if(index<0||index>=points.length-1)return document;
    const a=points[index]!,b=points[index+1]!;
    const delta={x:point.x-(a.x+b.x)/2,y:point.y-(a.y+b.y)/2};
    if(mode==="carry")for(const i of [index,index+1])if(sameRegion(i))points[i]=shifted(points[i]!,delta);
    points.splice(index+1,0,point);
    for(const [old,value] of map)if(value>index)map.set(old,value+1);
  }else {
    const at=index+1;if(at<=0||at>=points.length-1)return document;
    const delta={x:point.x-points[at]!.x,y:point.y-points[at]!.y};
    points[at]=point;
    if(mode==="carry")for(const neighbor of [at-1,at+1])if(sameRegion(neighbor))points[neighbor]=shifted(points[neighbor]!,delta);
    if(mode==="carry")mergeStraight(points,map);
  }
  const changed=replacePath(document,segment,points,map);
  if(!frozen.length||!changed.physicalTopology)return changed;
  const joiningPipes=changed.physicalTopology.joiningPipes?.map(pipe=>({...pipe,members:pipe.members.map((member,memberIndex)=>{
    const match=frozen.find(item=>item.pipeId===pipe.id&&item.memberIndex===memberIndex);
    if(!match)return member;
    const remapped=match.authoredBendRegions.flatMap(entry=>{
      if(entry.segmentId!==id)return [entry];
      const nextIndex=map.get(entry.bendIndex+1);
      if(nextIndex===undefined||nextIndex<=0||nextIndex>=points.length-1)return [];
      const previous=original[entry.bendIndex+1]!,next=points[nextIndex]!;
      const moved=Math.hypot(next.x-previous.x,next.y-previous.y)>1e-7;
      const visibleDelta=moved&&insert&&displayPosition&&displayOrigin
        ?{x:displayPosition.x-displayOrigin.x,y:displayPosition.y-displayOrigin.y}
        :{x:next.x-previous.x,y:next.y-previous.y};
      const shiftedDisplay=entry.displayPoint?shifted(entry.displayPoint,visibleDelta):next;
      return [{...entry,bendIndex:nextIndex-1,displayPoint:!insert&&entry.bendIndex===index
        ?displayPosition??shiftedDisplay:shiftedDisplay}];
    });
    if(insert)remapped.push({segmentId:id,bendIndex:index,region:region??regionAt(member.from/2,member.from,member.to),
      displayPoint:displayPosition??point});
    return {...member,enterOuter:match.enterOuter,exitOuter:match.exitOuter,authoredBendRegions:remapped};
  })}));
  return {...changed,physicalTopology:{...changed.physicalTopology,joiningPipes}};
}

export function deletePhysicalBend(document:HarnessDesignDocument,id:string,index:number):HarnessDesignDocument {
  const segment=document.physicalTopology?.segments.find(s=>s.id===id);if(!segment)return document;
  const original=physicalEditablePoints(document,segment,true),map=anchorMap(document,segment,original),at=index+1;
  if(at<=0||at>=original.length-1)return document;
  for(const [old,value] of map){if(value===at)map.delete(old);else if(value>at)map.set(old,value-1);}
  const changed=replacePath(document,segment,original.filter((_,i)=>i!==at),map);
  if(!changed.physicalTopology)return changed;
  const joiningPipes=changed.physicalTopology.joiningPipes?.map(pipe=>({...pipe,members:pipe.members.map(member=>
    member.segmentIds.includes(id)?{...member,authoredBendRegions:memberBendRegions(document,member)
      .filter(entry=>entry.segmentId!==id||entry.bendIndex!==index)
      .map(entry=>entry.segmentId===id&&entry.bendIndex>index?{...entry,bendIndex:entry.bendIndex-1}:entry)}:member)}));
  return {...changed,physicalTopology:{...changed.physicalTopology,joiningPipes}};
}

/** Freeze the visible route at move start and carry only shoulders next to moved exits. */
export function carryPhysicalExits(before:HarnessDesignDocument,after:HarnessDesignDocument,nodeIds:ReadonlySet<string>,mode:PhysicalDragMode):HarnessDesignDocument {
  if(!before.physicalTopology||!after.physicalTopology)return after;
  let result=after;
  for(const segment of before.physicalTopology.segments){
    if(!nodeIds.has(segment.from)&&!nodeIds.has(segment.to))continue;
    // Empty routed paths are generated from node geometry. Keep them empty
    // while dragging so a preview/commit cycle never materializes synthetic
    // shoulders as authored vertices.
    if(segment.path.points.length===0)continue;
    const original=carriedExitPoints(physicalEditablePoints(before,segment),mode);
    const points=[...original],map=anchorMap(before,segment,original),shifts=new Map<number,Point[]>();
    for(const side of ["from","to"] as const){
      if(!nodeIds.has(segment[side]))continue;
      const at=side==="from"?0:points.length-1;
      const node=after.physicalTopology.nodes.find(n=>n.id===segment[side]);if(!node)continue;
      const p=physicalNodePoint(after,node),delta={x:p.x-original[at]!.x,y:p.y-original[at]!.y};
      points[at]=p;
      const shoulder=side==="from"?1:points.length-2;
      if(mode==="carry"&&points.length>2&&(delta.x||delta.y))shifts.set(shoulder,[...shifts.get(shoulder)??[],delta]);
    }
    for(const [i,deltas] of shifts)points[i]=shifted(original[i]!,{x:deltas.reduce((n,d)=>n+d.x,0)/deltas.length,y:deltas.reduce((n,d)=>n+d.y,0)/deltas.length});
    if(mode==="carry")mergeStraight(points,map);
    result=replacePath(result,segment,points,map);
    const joiningPipes=result.physicalTopology!.joiningPipes?.map(pipe=>({...pipe,members:pipe.members.map(member=>{
      if(!member.segmentIds.includes(segment.id))return member;
      const originalMember=before.physicalTopology!.joiningPipes?.find(item=>item.id===pipe.id)?.members
        .find(item=>item.segmentIds.includes(segment.id));
      if(!originalMember)return member;
      const authoredBendRegions=memberBendRegions(before,originalMember).flatMap(entry=>{
        if(entry.segmentId!==segment.id)return [entry];
        const nextIndex=map.get(entry.bendIndex+1);
        if(nextIndex===undefined||nextIndex<=0||nextIndex>=points.length-1)return [];
        const previous=original[entry.bendIndex+1]!,next=points[nextIndex]!;
        const delta={x:next.x-previous.x,y:next.y-previous.y};
        return [{...entry,bendIndex:nextIndex-1,displayPoint:entry.displayPoint
          ?shifted(entry.displayPoint,delta):undefined}];
      });
      return {...member,authoredBendRegions};
    })}));
    if(joiningPipes)result={...result,physicalTopology:{...result.physicalTopology!,joiningPipes}};
  }
  return result;
}
