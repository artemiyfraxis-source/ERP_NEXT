import type { HarnessDesignDocument, Point } from "./model";
import type { JoiningPipeMember, PhysicalJoiningPipe } from "./physical-topology-model";
import { drawingPhysicalScale, drawingPipeWidth } from "./drawing-thickness";
import { drawingBendRadius, drawingRouteSamples } from "./drawing-route-path";
import { pathLength } from "./physical-coverings";
import { physicalSegmentPoints } from "./physical-geometry";
import { joiningPipePoints, joiningMemberPoints, joiningPipeExitVector } from "./physical-joining-pipes";
import { packPipeBundle } from "./pipe-bundle-packing";

export interface JoiningPipeSample { readonly fraction:number; readonly point:Point }
export interface JoiningPipeMemberControl {
 readonly fraction:number;
 readonly point:Point;
 readonly controlled:boolean;
 readonly transition?:{readonly memberIndex:number;readonly side:"enter"|"exit"};
 readonly connection?:boolean;
 readonly boundary?:"outerEnter"|"axisEnter"|"axisExit"|"outerExit";
 readonly memberIndex?:number;
 readonly lead?:"enter"|"exit";
}
const mix=(a:Point,b:Point,t:number):Point=>({x:a.x+(b.x-a.x)*t,y:a.y+(b.y-a.y)*t});
function at(samples:readonly JoiningPipeSample[],fraction:number):Point {
  if(fraction<=samples[0]!.fraction)return samples[0]!.point;
  const i=samples.findIndex(s=>s.fraction>=fraction);if(i<0)return samples.at(-1)!.point;
  const a=samples[i-1]!,b=samples[i]!;return mix(a.point,b.point,(fraction-a.fraction)/(b.fraction-a.fraction||1));
}
function sample(points:readonly Point[],radius=0):JoiningPipeSample[] {
  const length=pathLength(points);let distance=0;
  if(radius>0)return drawingRouteSamples(points,radius).map(route=>({fraction:length?route.distance/length:0,point:route.point}));
  return points.map((point,index)=>{if(index)distance+=Math.hypot(point.x-points[index-1]!.x,point.y-points[index-1]!.y);return {fraction:length?distance/length:0,point};});
}
export function joiningPipePacking(document:HarnessDesignDocument,pipe:PhysicalJoiningPipe) {
  const scale=drawingPhysicalScale(document),t=document.physicalTopology!;
  const axis=joiningPipePoints(pipe),axisStart=axis[0]!,axisEnd=axis.at(-1)!;
  const axisStartNext=axis.find(point=>Math.hypot(point.x-axisStart.x,point.y-axisStart.y)>1e-7)??axisEnd;
  const axisEndPrevious=[...axis].reverse().find(point=>Math.hypot(point.x-axisEnd.x,point.y-axisEnd.y)>1e-7)??axisStart;
  const startLength=Math.hypot(axisStartNext.x-axisStart.x,axisStartNext.y-axisStart.y)||1;
  const endLength=Math.hypot(axisEnd.x-axisEndPrevious.x,axisEnd.y-axisEndPrevious.y)||1;
  const startNormal={x:-(axisStartNext.y-axisStart.y)/startLength,y:(axisStartNext.x-axisStart.x)/startLength};
  const endNormal={x:-(axisEnd.y-axisEndPrevious.y)/endLength,y:(axisEnd.x-axisEndPrevious.x)/endLength};
  // Keep a member's cross-sectional lane tied to its physical position. The
  // draft order is an editing detail and must not decide which connector exits
  // above or below the other members.
  const candidates=pipe.members.map((member,index)=>{
    const route=joiningMemberPoints(document,member.segmentIds);
    const enter=member.reverse?route.at(-1)!:route[0]!;
    const exit=member.reverse?route[0]!:route.at(-1)!;
    const startOffset=(enter.x-axisStart.x)*startNormal.x+(enter.y-axisStart.y)*startNormal.y;
    const endOffset=(exit.x-axisEnd.x)*endNormal.x+(exit.y-axisEnd.y)*endNormal.y;
    return {member,index,startOffset,endOffset,key:(startOffset+endOffset)/2};
  });
  // A lane is a longitudinal slot.  Sorting by the average of both exits is
  // attractive for a symmetric pair, but it can put one member between two
  // others when the connector order differs at the two ends.  That makes the
  // transition shoulders cross (and leaves no simple contour for an OP shell).
  // Choose the stable endpoint order with the fewest inversions; ties retain
  // the old nearest-average ordering so ordinary drawings do not move.
  const inversions=(items:readonly typeof candidates[number][],key:"startOffset"|"endOffset")=>{
    let count=0;for(let i=0;i<items.length;i++)for(let j=i+1;j<items.length;j++)if(items[i]![key]>items[j]![key]+1e-7)count++;return count;
  };
  const byAverage=[...candidates].sort((a,b)=>a.key-b.key||a.member.segmentIds.join("\u0000").localeCompare(b.member.segmentIds.join("\u0000")));
  const byStart=[...candidates].sort((a,b)=>a.startOffset-b.startOffset||a.endOffset-b.endOffset||a.index-b.index);
  const byEnd=[...candidates].sort((a,b)=>a.endOffset-b.endOffset||a.startOffset-b.startOffset||a.index-b.index);
  const score=(items:readonly typeof candidates[number][])=>inversions(items,"startOffset")+inversions(items,"endOffset");
  const ordered=[byAverage,byStart,byEnd].sort((a,b)=>score(a)-score(b)||Number(a!==byAverage)-Number(b!==byAverage))[0]!;
  // The OP lane is an independent physical object. A separately selected
  // covering on a member P must not resize the OP when its authored width is
  // edited; lower covering surfaces are handled by coveringScene itself.
  const packed=packPipeBundle(ordered.map(({index,member})=>({id:String(index),diameter:Math.max(.5*scale,...member.segmentIds.map(id=>
    drawingPipeWidth(document,t.segments.find(s=>s.id===id)!)))})),pipe.mode);
  const byId=new Map(packed.members.map(member=>[member.id,member]));
  return {...packed,members:pipe.members.map((_,index)=>byId.get(String(index))!)};
}
export function joiningPipeWidth(document:HarnessDesignDocument,pipe:PhysicalJoiningPipe):number {
  const scale=drawingPhysicalScale(document),packing=joiningPipePacking(document,pipe),axis=sample(joiningPipePoints(pipe));
  // The centreline lanes are offset around OP corners. Include the largest
  // offset envelope in the shell width so a bent member stays inside it.
  const envelope=packing.members.reduce((maximum,member)=>{
    const lane=offsetSamples(axis,member.offset);
    return Math.max(maximum,...lane.map((sample,index)=>Math.hypot(sample.point.x-axis[index]!.point.x,sample.point.y-axis[index]!.point.y)+member.diameter/2));
  },0);
  return Math.max((pipe.width??0)*scale,packing.width+scale,envelope*2+scale);
}
// Parallel offsets use the same bend stations, including sharp corners. Capped
// miters keep a tight turn finite; the OP remains the authoritative centreline.
function offsetSamples(axis:readonly JoiningPipeSample[],offset:number):JoiningPipeSample[] {
  return axis.map((s,i)=>{
    const p=s.point,a=axis[Math.max(0,i-1)]!.point,b=axis[Math.min(axis.length-1,i+1)]!.point;
    const la=Math.hypot(p.x-a.x,p.y-a.y),lb=Math.hypot(b.x-p.x,b.y-p.y);
    const u=la?{x:-(p.y-a.y)/la,y:(p.x-a.x)/la}:null,v=lb?{x:-(b.y-p.y)/lb,y:(b.x-p.x)/lb}:null;
    const n=u&&v?{x:u.x+v.x,y:u.y+v.y}:u??v??{x:0,y:1},l=Math.hypot(n.x,n.y)||1;
    // Keep the same capped miter as the covering surface.  A wider miter on
    // a member lane would let the pipe leave the common shell at an OP bend.
    const normal={x:n.x/l,y:n.y/l},factor=u&&v?Math.min(1.5,1/Math.max(.25,normal.x*v.x+normal.y*v.y)):1;
    return {fraction:s.fraction,point:{x:p.x+normal.x*offset*factor,y:p.y+normal.y*offset*factor}};
  });
}
interface Placement {
  readonly source:readonly JoiningPipeSample[];
  readonly axis:readonly JoiningPipeSample[];
  readonly axisControls:readonly JoiningPipeSample[];
  readonly enter:readonly JoiningPipeSample[];
  readonly exit:readonly JoiningPipeSample[];
  readonly member:JoiningPipeMember;
  readonly before:number; readonly length:number; readonly total:number;
  readonly sourceBefore:number; readonly sourceLength:number;
  readonly low:number; readonly high:number;
  readonly memberIndex:number;
  readonly enterBend:Point|null; readonly exitBend:Point|null;
  readonly enterLeadPoint:Point; readonly exitLeadPoint:Point;
}
const cache=new WeakMap<HarnessDesignDocument,ReadonlyMap<string,Placement>>();
function placements(document:HarnessDesignDocument):ReadonlyMap<string,Placement> {
  const known=cache.get(document);if(known)return known;
  const result=new Map<string,Placement>(),t=document.physicalTopology;
  for(const pipe of t?.joiningPipes??[]){
    const axis=sample(joiningPipePoints(pipe),drawingBendRadius(document)),packed=joiningPipePacking(document,pipe);
    for(const [i,member] of pipe.members.entries()){
      const source=sample(joiningMemberPoints(document,member.segmentIds));
      // A reversed member enters the OP from the opposite connector. Keep the
      // authored source for projection deltas, but use an OP-oriented copy for
      // the two transition shoulders.
      const orientedSource=member.reverse
        ? [...source].reverse().map(sample=>({fraction:1-sample.fraction,point:sample.point}))
        : source;
      const rawAxis=sample(joiningPipePoints(pipe));
      const lane=offsetSamples(axis,packed.members[i]!.offset);
      const oriented=member.reverse?[...lane].reverse().map(s=>({fraction:1-s.fraction,point:s.point})):lane;
      // The painted OP uses the rounded axis. Project its authored stations
      // onto that same lane so a member handle never floats away from the
      // visible contour when the OP has a corner.
      const axisControlsBase=rawAxis.map(s=>({fraction:s.fraction,point:at(lane,s.fraction)}));
      const axisControls=member.reverse?[...axisControlsBase].reverse().map(s=>({fraction:1-s.fraction,point:s.point})):axisControlsBase;
      const lengths=member.segmentIds.map(id=>pathLength(physicalSegmentPoints(document,t!.segments.find(s=>s.id===id)!))),total=lengths.reduce((a,b)=>a+b,0);
      const low=member.from/2,high=(1+member.to)/2;
      const a=oriented[0]!.point,b=oriented.at(-1)!.point,outerA=member.enterOuter??at(orientedSource,low),outerB=member.exitOuter??at(orientedSource,high);
      const enterLead=member.reverse?pipe.exitLength:pipe.enterLength;
      const exitLead=member.reverse?pipe.enterLength:pipe.exitLength;
      const enterVector=joiningPipeExitVector(pipe,member.reverse?"to":"from"),exitVector=joiningPipeExitVector(pipe,member.reverse?"from":"to");
      const enterLeadPoint={x:a.x+enterVector.x*(enterLead??20),y:a.y+enterVector.y*(enterLead??20)};
      const exitLeadPoint={x:b.x+exitVector.x*(exitLead??20),y:b.y+exitVector.y*(exitLead??20)};
      const enterBend=member.enterBend??null,exitBend=member.exitBend??null;
      const enter=sample(enterBend===null?[outerA,enterLeadPoint,a]:[outerA,enterBend,enterLeadPoint,a]);
      const exit=sample(exitBend===null?[b,exitLeadPoint,outerB]:[b,exitLeadPoint,exitBend,outerB]);
      let before=0;
      member.segmentIds.forEach((id,j)=>{result.set(id,{source,axis:oriented,axisControls,enter,exit,member,before,length:lengths[j]!,sourceBefore:before,sourceLength:lengths[j]!,total,low,high,memberIndex:i,enterBend,exitBend,enterLeadPoint,exitLeadPoint});before+=lengths[j]!;});
      if(member.authoredBendRegions?.some(entry=>entry.displayPoint)&&member.segmentIds.length>1){
        const anchor=result.get(member.segmentIds[0]!)!;
        const stations=orderedAuthoredStations(anchor);
        const boundaries=[0,...member.segmentIds.slice(0,-1).map((id,j)=>{
          const raw=(result.get(id)!.sourceBefore+result.get(id)!.sourceLength)/(total||1);
          const left=Math.max(0,...stations.filter(s=>member.segmentIds.indexOf(s.segmentId)<=j).map(s=>s.chain));
          const right=Math.min(1,...stations.filter(s=>member.segmentIds.indexOf(s.segmentId)>j).map(s=>s.chain));
          return Math.max(left+1e-6,Math.min(right-1e-6,raw));
        }),1];
        if(boundaries.every((value,j)=>j===0||value>boundaries[j-1]!))member.segmentIds.forEach((id,j)=>{
          result.set(id,{...result.get(id)!,before:boundaries[j]!*total,length:(boundaries[j+1]!-boundaries[j]!)*total});
        });
      }
    }
  }
  cache.set(document,result);return result;
}
function chainFraction(p:Placement,fraction:number) {return (p.before+fraction*p.length)/(p.total||1);}
function localFraction(p:Placement,fraction:number) {return (fraction*p.total-p.before)/(p.length||1);}
function sourceChainFraction(p:Placement,fraction:number) {return (p.sourceBefore+fraction*p.sourceLength)/(p.total||1);}
function transitionControlCandidates(p:Placement):readonly JoiningPipeMemberControl[] {
 const m=p.member;
 const enterBendFraction=p.low+(m.from-p.low)*(p.enter[1]?.fraction??.5);
 const exitBendFraction=m.to+(p.high-m.to)*(p.exit.at(-2)?.fraction??.5);
 const candidates:JoiningPipeMemberControl[]=[
   // The outer station is the first real transition corner on the member
   // route. Keep it visible and numbered; OP axis stations remain controlled
   // by the common pipe itself.
   {fraction:p.low,point:p.enter[0]!.point,controlled:false,connection:true,boundary:"outerEnter",memberIndex:p.memberIndex},
   ...(p.enterBend===null?[]:[{fraction:enterBendFraction,point:p.enterBend,controlled:false,transition:{memberIndex:p.memberIndex,side:"enter" as const}}]),
   {fraction:p.low+(m.from-p.low)*p.enter.at(-2)!.fraction,point:p.enterLeadPoint,controlled:true,lead:"enter",memberIndex:p.memberIndex},
   {fraction:m.from,point:p.axisControls[0]!.point,controlled:true,connection:true,boundary:"axisEnter",memberIndex:p.memberIndex},
   ...p.axisControls.slice(1,-1).map(sample=>({fraction:m.from+sample.fraction*(m.to-m.from),point:sample.point,controlled:true})),
   {fraction:m.to,point:p.axisControls.at(-1)!.point,controlled:true,connection:true,boundary:"axisExit",memberIndex:p.memberIndex},
   {fraction:m.to+(p.high-m.to)*p.exit[1]!.fraction,point:p.exitLeadPoint,controlled:true,lead:"exit",memberIndex:p.memberIndex},
   ...(p.exitBend===null?[]:[{fraction:exitBendFraction,point:p.exitBend,controlled:false,transition:{memberIndex:p.memberIndex,side:"exit" as const}}]),
   {fraction:p.high,point:p.exit.at(-1)!.point,controlled:false,connection:true,boundary:"outerExit",memberIndex:p.memberIndex},
 ];
 return candidates;
}
/** Connection grips at the OP axis move its ends; outer grips and transition
 * bends edit the member, while interior OP stations remain controlled. */
export function joiningPipeMemberControls(document:HarnessDesignDocument,id:string):readonly JoiningPipeMemberControl[]|undefined {
 const p=placements(document).get(id);if(!p)return undefined;
 const start=p.before/(p.total||1),end=(p.before+p.length)/(p.total||1),epsilon=1e-7;
 const generated=transitionControlCandidates(p).filter(control=>control.fraction>=start-epsilon&&control.fraction<=end+epsilon)
   .map(control=>({...control,fraction:Math.max(0,Math.min(1,localFraction(p,control.fraction)))}));
 // A very short member or a split exactly at an OP boundary can put two
 // generated stations at the same fraction. Keep the OP-owned connection
 // station and drop the zero-length transition instead of drawing a kink.
 return generated.reduce<JoiningPipeMemberControl[]>((controls,control)=>{
   const previous=controls.at(-1);
   if(previous&&Math.abs(control.fraction-previous.fraction)<=epsilon){
     if(control.connection||!previous.connection)controls[controls.length-1]=control;
     return controls;
   }
   controls.push(control);return controls;
 },[]);
}
export function hasJoiningPipeProjection(document:HarnessDesignDocument,id:string) {return placements(document).has(id);}
export function joiningPipeControlsMemberStation(document:HarnessDesignDocument,id:string,fraction:number) {
  const p=placements(document).get(id);if(!p)return false;
  const t=chainFraction(p,fraction);return t>=p.member.from&&t<=p.member.to;
}
export function joiningPipeMidpointRegion(document:HarnessDesignDocument,id:string,fraction:number):"before-enter"|"enter"|"axis"|"exit"|"after-exit"|undefined {
  const p=placements(document).get(id);if(!p)return undefined;
  const t=chainFraction(p,fraction),enterLead=p.low+p.enter.at(-2)!.fraction*(p.member.from-p.low),exitLead=p.member.to+p.exit[1]!.fraction*(p.high-p.member.to);
  return t<p.low?"before-enter":t<enterLead?"enter":t<=exitLead?"axis":t<=p.high?"exit":"after-exit";
}
function rawProjectJoiningPipePoint(document:HarnessDesignDocument,id:string,fraction:number,point:Point):Point {
  const p=placements(document).get(id);if(!p)return point;
  const t=chainFraction(p,fraction),m=p.member;
  let projected:Point;
  if(t<p.low){const source=at(p.source,t),outer=at(p.source,p.low),target=p.enter[0]!.point;projected={x:source.x+(target.x-outer.x)*t/p.low,y:source.y+(target.y-outer.y)*t/p.low};}
  else if(t>p.high){const source=at(p.source,t),outer=at(p.source,p.high),target=p.exit.at(-1)!.point;projected={x:source.x+(target.x-outer.x)*(1-t)/(1-p.high),y:source.y+(target.y-outer.y)*(1-t)/(1-p.high)};}
  else if(t<m.from)projected=at(p.enter,(t-p.low)/(m.from-p.low));
  else if(t>m.to)projected=at(p.exit,(t-m.to)/(p.high-m.to));
  else projected=at(p.axis,(t-m.from)/(m.to-m.from));
  const original=at(p.source,t);
  return {x:point.x+projected.x-original.x,y:point.y+projected.y-original.y};
}
function orderedAuthoredStations(p:Placement){
  const member=p.member,leadEnter=p.low+p.enter.at(-2)!.fraction*(member.from-p.low),leadExit=member.to+p.exit[1]!.fraction*(p.high-member.to);
  const bands={"before-enter":[0,p.low],enter:[p.low,leadEnter],axis:[member.from,member.to],exit:[leadExit,p.high],"after-exit":[p.high,1]} as const;
  const entries=(member.authoredBendRegions?.filter(entry=>entry.displayPoint&&entry.region!=="axis")??[])
    .sort((a,b)=>member.segmentIds.indexOf(a.segmentId)-member.segmentIds.indexOf(b.segmentId)||a.bendIndex-b.bendIndex);
  return entries.map(entry=>{
    const [start,end]=bands[entry.region],same=entries.filter(item=>item.region===entry.region);
    const ordinal=same.findIndex(item=>item.segmentId===entry.segmentId&&item.bendIndex===entry.bendIndex),chain=start+(end-start)*(ordinal+1)/(same.length+1);
    return {chain,point:entry.displayPoint!,segmentId:entry.segmentId,bendIndex:entry.bendIndex};
  });
}
function authoredDisplayStations(document:HarnessDesignDocument,id:string){
  const p=placements(document).get(id);
  if(!p)return [];
  return orderedAuthoredStations(p).filter(station=>station.segmentId===id)
    .map(station=>({...station,fraction:localFraction(p,station.chain)}))
    .filter(station=>station.fraction>0&&station.fraction<1);
}
export function joiningPipeAuthoredHandle(document:HarnessDesignDocument,id:string,bendIndex:number):{point:Point;fraction:number;controlled:boolean}|undefined {
  const p=placements(document).get(id),entry=p?.member.authoredBendRegions?.find(item=>item.segmentId===id&&item.bendIndex===bendIndex);
  if(!p||!entry?.displayPoint)return undefined;
  const station=authoredDisplayStations(document,id).find(item=>item.bendIndex===bendIndex);
  if(!station)return undefined;
  return {point:entry.displayPoint,fraction:station.fraction,controlled:entry.region==="axis"};
}
const persistedControlsCache=new WeakMap<HarnessDesignDocument,Map<string,readonly JoiningPipeSample[]>>();
function persistedDisplayControls(document:HarnessDesignDocument,id:string):readonly JoiningPipeSample[]|undefined {
  const p=placements(document).get(id);
  if(!p||p.member.authoredBendRegions===undefined)return undefined;
  let map=persistedControlsCache.get(document);if(!map){map=new Map();persistedControlsCache.set(document,map);}
  const known=map.get(id);if(known)return known;
  const generated=joiningPipeMemberControls(document,id)??[];
  const stations=authoredDisplayStations(document,id);
  const sourceStart=at(p.source,chainFraction(p,0)),sourceEnd=at(p.source,chainFraction(p,1));
  const fragment=p.member.segmentIds.indexOf(id);
  const segment=document.physicalTopology!.segments.find(item=>item.id===id)!;
  const startNode=fragment>0?document.physicalTopology!.nodes.find(node=>node.id===segment.from):undefined;
  const endNode=fragment<p.member.segmentIds.length-1?document.physicalTopology!.nodes.find(node=>node.id===segment.to):undefined;
  const axisSamples=p.axis.map(sample=>({fraction:localFraction(p,p.member.from+sample.fraction*(p.member.to-p.member.from)),point:sample.point}))
    .filter(sample=>sample.fraction>0&&sample.fraction<1);
  const controls=[{fraction:0,point:startNode?.position??rawProjectJoiningPipePoint(document,id,0,sourceStart)},
    ...generated.map(control=>({fraction:control.fraction,point:control.point})),
    ...axisSamples,
    ...stations.map(station=>({fraction:station.fraction,point:station.point})),
    {fraction:1,point:endNode?.position??rawProjectJoiningPipePoint(document,id,1,sourceEnd)}]
    .sort((a,b)=>a.fraction-b.fraction)
    .reduce<JoiningPipeSample[]>((result,control)=>{
      const last=result.at(-1);
      if(last&&Math.abs(last.fraction-control.fraction)<1e-7){
        if(stations.some(station=>station.fraction===control.fraction&&station.point===control.point))result[result.length-1]=control;
      }else result.push(control);
      return result;
    },[]);
  map.set(id,controls);return controls;
}
export function projectJoiningPipePoint(document:HarnessDesignDocument,id:string,fraction:number,point:Point):Point {
  const persistent=persistedDisplayControls(document,id),placement=placements(document).get(id);
  if(persistent&&placement){
    const shown=at(persistent,fraction),source=at(placement.source,sourceChainFraction(placement,fraction));
    return {x:point.x+shown.x-source.x,y:point.y+shown.y-source.y};
  }
  return rawProjectJoiningPipePoint(document,id,fraction,point);
}
export function joiningPipeProjectionStops(document:HarnessDesignDocument,id:string):number[] {
  const p=placements(document).get(id);if(!p)return [];
  const persistent=persistedDisplayControls(document,id);
  if(persistent)return persistent.map(control=>control.fraction);
  const m=p.member;
  return [...p.source.map(s=>s.fraction),...authoredDisplayStations(document,id).map(s=>chainFraction(p,s.fraction)),...p.enter.map(s=>p.low+s.fraction*(m.from-p.low)),
    ...p.axis.map(s=>m.from+s.fraction*(m.to-m.from)),
    ...p.exit.map(s=>m.to+s.fraction*(p.high-m.to))]
    .map(t=>localFraction(p,t)).filter(t=>t>=0&&t<=1);
}
export function joiningPipeTransitionHandles(document:HarnessDesignDocument,id:string):readonly {fraction:number;point:Point;memberIndex:number;side:"enter"|"exit"}[] {
  const p=placements(document).get(id);if(!p)return [];
  const m=p.member;
  const handles: {fraction:number;point:Point;memberIndex:number;side:"enter"|"exit"}[]=[
    ...(p.enterBend===null?[]:[{fraction:localFraction(p,p.low+(m.from-p.low)*p.enter[1]!.fraction),point:p.enterBend,memberIndex:p.memberIndex,side:"enter" as const}]),
    ...(p.exitBend===null?[]:[{fraction:localFraction(p,m.to+(p.high-m.to)*p.exit.at(-2)!.fraction),point:p.exitBend,memberIndex:p.memberIndex,side:"exit" as const}]),
  ];
  return handles.filter(handle=>handle.fraction>0&&handle.fraction<1);
}
const displayCache=new WeakMap<HarnessDesignDocument,Map<string,readonly JoiningPipeSample[]>>();
export function joiningPipeDisplaySamples(document:HarnessDesignDocument,id:string):readonly JoiningPipeSample[]|undefined {
  const p=placements(document).get(id);if(!p)return undefined;
  let map=displayCache.get(document);if(!map){map=new Map();displayCache.set(document,map);}
  const known=map.get(id);if(known)return known;
  const fractions=[...new Set([0,1,...joiningPipeProjectionStops(document,id)])].sort((a,b)=>a-b);
  const result=fractions.map(fraction=>({fraction,point:projectJoiningPipePoint(document,id,fraction,at(p.source,sourceChainFraction(p,fraction)))}));
  map.set(id,result);return result;
}
