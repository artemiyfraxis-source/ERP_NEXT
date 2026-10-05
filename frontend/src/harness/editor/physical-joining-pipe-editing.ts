import type {HarnessDesignDocument,Point} from "./model";
import type {PhysicalJoiningPipe,PhysicalTopology} from "./physical-topology-model";
import type {PhysicalDragMode} from "./physical-editing";
import {joiningPipePoints} from "./physical-joining-pipes";

const rotate=(point:Point,oldOrigin:Point,newOrigin:Point,from:Point,to:Point):Point=>{
  const a=Math.atan2(from.y,from.x),b=Math.atan2(to.y,to.x),angle=b-a;
  const dx=point.x-oldOrigin.x,dy=point.y-oldOrigin.y,c=Math.cos(angle),s=Math.sin(angle);
  return {x:newOrigin.x+dx*c-dy*s,y:newOrigin.y+dx*s+dy*c};
};
const tangent=(points:readonly Point[],side:"enter"|"exit")=>{
  const a=side==="enter"?points[0]!:points.at(-2)!,b=side==="enter"?points[1]!:points.at(-1)!;
  const length=Math.hypot(b.x-a.x,b.y-a.y)||1;return {x:(b.x-a.x)/length,y:(b.y-a.y)/length};
};
/** An OP bend changes the direction of its member lanes. Explicit member
 * transition bends are authored in that lane's local frame, so rotate them
 * with the corresponding OP endpoint instead of leaving a stale, invisible
 * control point behind the new contour. */
export function remapJoiningPipeMemberBends(before:PhysicalJoiningPipe,after:PhysicalJoiningPipe) {
  const oldAxis=joiningPipePoints(before),newAxis=joiningPipePoints(after);
  return before.members.map(member=>({
    ...member,
    ...(member.enterBend&&{enterBend:rotate(member.enterBend,oldAxis[0]!,newAxis[0]!,tangent(oldAxis,"enter"),tangent(newAxis,"enter"))}),
    ...(member.exitBend&&{exitBend:rotate(member.exitBend,oldAxis.at(-1)!,newAxis.at(-1)!,tangent(oldAxis,"exit"),tangent(newAxis,"exit"))}),
    ...(member.enterOuter&&{enterOuter:rotate(member.enterOuter,oldAxis[0]!,newAxis[0]!,tangent(oldAxis,"enter"),tangent(newAxis,"enter"))}),
    ...(member.exitOuter&&{exitOuter:rotate(member.exitOuter,oldAxis.at(-1)!,newAxis.at(-1)!,tangent(oldAxis,"exit"),tangent(newAxis,"exit"))}),
    authoredBendRegions:member.authoredBendRegions?.map(entry=>!entry.displayPoint?entry:{...entry,displayPoint:entry.region==="before-enter"||entry.region==="enter"
      ?rotate(entry.displayPoint,oldAxis[0]!,newAxis[0]!,tangent(oldAxis,"enter"),tangent(newAxis,"enter"))
      :entry.region==="exit"||entry.region==="after-exit"
        ?rotate(entry.displayPoint,oldAxis.at(-1)!,newAxis.at(-1)!,tangent(oldAxis,"exit"),tangent(newAxis,"exit"))
        :entry.displayPoint}),
  }));
}

/** Move the complete OP geometry while preserving its authored shape and
 * member transition handles. The electrical graph and member references stay
 * unchanged; only the common pipe's presentation axis moves. */
export function moveJoiningPipe(t:PhysicalTopology,id:string,delta:Point):PhysicalTopology {
  if(!Number.isFinite(delta.x)||!Number.isFinite(delta.y)||(!delta.x&&!delta.y))return t;
  return {...t,joiningPipes:t.joiningPipes?.map(pipe=>pipe.id!==id?pipe:{
    ...pipe,
    start:{x:pipe.start.x+delta.x,y:pipe.start.y+delta.y},
    end:{x:pipe.end.x+delta.x,y:pipe.end.y+delta.y},
    path:{...pipe.path,points:pipe.path.points.map(point=>({x:point.x+delta.x,y:point.y+delta.y}))},
    members:pipe.members.map(member=>({...member,
      ...(member.enterBend?{enterBend:{x:member.enterBend.x+delta.x,y:member.enterBend.y+delta.y}}:{}),
      ...(member.exitBend?{exitBend:{x:member.exitBend.x+delta.x,y:member.exitBend.y+delta.y}}:{}),
      ...(member.enterOuter?{enterOuter:{x:member.enterOuter.x+delta.x,y:member.enterOuter.y+delta.y}}:{}),
      ...(member.exitOuter?{exitOuter:{x:member.exitOuter.x+delta.x,y:member.exitOuter.y+delta.y}}:{}),
      authoredBendRegions:member.authoredBendRegions?.map(entry=>entry.displayPoint?{...entry,displayPoint:{x:entry.displayPoint.x+delta.x,y:entry.displayPoint.y+delta.y}}:entry),
    })),
  })};
}

/** Authored points only; derived member bends never become editable OP vertices. */
export function editJoiningPipeBend(document:HarnessDesignDocument,id:string,index:number,point:Point,mode:PhysicalDragMode,insert=false,remove=false):PhysicalTopology {
  const t=document.physicalTopology!,pipe=t.joiningPipes?.find(p=>p.id===id);if(!pipe)return t;
  const all=joiningPipePoints(pipe),points=[...pipe.path.points];
  if(index<0||index>points.length||!insert&&index===points.length)return t;
  if(remove)points.splice(index,1);
  else {
    const original=insert?{x:(all[index]!.x+all[index+1]!.x)/2,y:(all[index]!.y+all[index+1]!.y)/2}:points[index]!;
    const delta={x:point.x-original.x,y:point.y-original.y};
    if(mode==="carry")for(const i of insert?[index-1,index]:[index-1,index+1])if(points[i])points[i]={x:points[i]!.x+delta.x,y:points[i]!.y+delta.y};
    if(insert)points.splice(index,0,point);else points[index]=point;
  }
  const coverings=t.coverings?.map(c=>({...c,spans:c.spans.map(s=>{
    if(s.segmentId!==id)return s;
    const anchor=(a:number|undefined)=>a===undefined?undefined:remove?(a===index+1?undefined:a>index+1?a-1:a):insert&&a>index?a+1:a;
    return {...s,fromAnchor:anchor(s.fromAnchor),toAnchor:anchor(s.toAnchor)};
  })}));
  const joiningPipes=t.joiningPipes?.map(p=>{
    if(p.id!==id)return p;
    const next={...p,path:{kind:"polyline" as const,points}};
    return {...next,members:remapJoiningPipeMemberBends(p,next)};
  });
  return {...t,coverings,joiningPipes};
}
export function removeJoiningPipe(t:PhysicalTopology,id:string):PhysicalTopology {
  return {...t,joiningPipes:t.joiningPipes?.filter(p=>p.id!==id),coverings:t.coverings?.flatMap(c=>{
    const spans=c.spans.filter(s=>s.segmentId!==id);return spans.length?[{...c,spans}]:[];
  })};
}
