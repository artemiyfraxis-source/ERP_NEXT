import type { HarnessDesignDocument, Point } from "./model";
import type { JoiningPipeMember, PhysicalJoiningPipe, PhysicalTopology } from "./physical-topology-model";
import { physicalSegmentPoints } from "./physical-geometry";
import { pathLength, projectOntoPolyline, trimPolyline, type PhysicalCovering } from "./physical-coverings";
import { pipeBundlePaths } from "./pipe-bundle-model";

export const joiningPipePoints = (pipe:PhysicalJoiningPipe):Point[] => [pipe.start,...pipe.path.points,pipe.end];
export const joiningPipeEndpointId = (id:string,side:"from"|"to") => `${id}:${side}`;
export const joiningPipeExitId = (id:string,side:"from"|"to") => `${id}:exit:${side}`;
export function joiningPipeExitVector(pipe:PhysicalJoiningPipe,side:"from"|"to"):Point {
  const axis=joiningPipePoints(pipe),edge=side==="from"?axis[0]!:axis.at(-1)!;
  const inside=(side==="from"?axis.slice(1):axis.slice(0,-1).reverse()).find(point=>Math.hypot(point.x-edge.x,point.y-edge.y)>1e-7)??edge;
  const dx=edge.x-inside.x,dy=edge.y-inside.y,norm=Math.hypot(dx,dy)||1;
  return {x:dx/norm,y:dy/norm};
}
export function joiningPipeExit(t:PhysicalTopology|undefined,id:string) {
  for(const pipe of t?.joiningPipes??[])for(const side of ["from","to"] as const)
    if(joiningPipeExitId(pipe.id,side)===id)return {pipe,side};
  return undefined;
}
export function joiningPipeExitPoint(pipe:PhysicalJoiningPipe,side:"from"|"to"):Point {
  const axis=joiningPipePoints(pipe),edge=side==="from"?axis[0]!:axis.at(-1)!;
  const length=side==="from"?pipe.enterLength??20:pipe.exitLength??20;
  const vector=joiningPipeExitVector(pipe,side);
  return {x:edge.x+vector.x*length,y:edge.y+vector.y*length};
}
export function joiningPipeEndpoint(t:PhysicalTopology|undefined,id:string) {
  for(const pipe of t?.joiningPipes??[])for(const side of ["from","to"] as const)
    if(joiningPipeEndpointId(pipe.id,side)===id)return {pipe,side};
  return undefined;
}
export function joiningMemberPoints(document:HarnessDesignDocument,ids:readonly string[]):Point[] {
  return ids.flatMap((id,i)=>{
    const segment=document.physicalTopology?.segments.find(s=>s.id===id);
    const points=segment?physicalSegmentPoints(document,segment):[];
    return i?points.slice(1):points;
  });
}

/** Copy the initial support once. Future member edits never redefine the OP axis. */
export function createJoiningPipe(document:HarnessDesignDocument,paths:readonly (readonly string[])[],id:string,source?:PhysicalCovering):PhysicalJoiningPipe {
  if(paths.length<2)throw new Error("Добавьте минимум два пайпа в ОП.");
  const first=joiningMemberPoints(document,paths[0]!);
  if(pathLength(first)<1e-6)throw new Error("Для ОП нужен пайп ненулевой длины.");
  let from=.3,to=.7;
  if(source){
    const lengths=paths[0]!.map(id=>pathLength(joiningMemberPoints(document,[id]))),total=lengths.reduce((a,b)=>a+b,0);
    const bounds=source.spans.filter(s=>paths[0]!.includes(s.segmentId)).map(s=>{
      const i=paths[0]!.indexOf(s.segmentId),before=lengths.slice(0,i).reduce((a,b)=>a+b,0);
      return {from:(before+s.from*lengths[i]!)/total,to:(before+s.to*lengths[i]!)/total};
    });
    if(bounds.length){from=Math.max(.001,Math.min(...bounds.map(s=>s.from)));to=Math.min(.999,Math.max(...bounds.map(s=>s.to)));}
  }
  const axis=trimPolyline(first,from,to);
  if(axis.length<2)throw new Error("Не удалось определить границы ОП.");
  const offset=source?.bundle?.bodyOffset??{x:0,y:0};
  const shifted=axis.map(p=>({x:p.x+offset.x,y:p.y+offset.y}));
  const axisLead=(edge:Point,outer:Point)=>Math.max(20,Math.min(100,Math.hypot(edge.x-outer.x,edge.y-outer.y)/3));
  const enterLength=axisLead(shifted[0]!,first[0]!);
  const exitLength=axisLead(shifted.at(-1)!,first.at(-1)!);
  const members=paths.map((segmentIds,i):JoiningPipeMember=>{
    const route=joiningMemberPoints(document,segmentIds);
    if(route.length<2||pathLength(route)<1e-6)throw new Error("В составе ОП найден пустой пайп.");
    const a=projectOntoPolyline(route,axis[0]!).fraction,b=projectOntoPolyline(route,axis.at(-1)!).fraction;
    const direct=Math.hypot(route[0]!.x-first[0]!.x,route[0]!.y-first[0]!.y)+Math.hypot(route.at(-1)!.x-first.at(-1)!.x,route.at(-1)!.y-first.at(-1)!.y);
    const reversed=Math.hypot(route.at(-1)!.x-first[0]!.x,route.at(-1)!.y-first[0]!.y)+Math.hypot(route[0]!.x-first.at(-1)!.x,route[0]!.y-first.at(-1)!.y);
    const low=Math.max(.001,Math.min(a,b)),high=Math.min(.999,Math.max(a,b));
    return {segmentIds:[...segmentIds],from:i===0?from:high-low>.001?low:.3,to:i===0?to:high-low>.001?high:.7,reverse:i!==0&&(Math.abs(a-b)>.001?a>b:reversed<direct)};
  });
  return {id,start:shifted[0]!,end:shifted.at(-1)!,path:{kind:"polyline",points:shifted.slice(1,-1)},members,enterLength,exitLength,mode:source?.bundle?.mode??"flat"};
}

/** Old sleeves remain materials. Their former convergence becomes an independent OP. */
export function migrateJoiningPipes(document:HarnessDesignDocument):HarnessDesignDocument {
  const t=document.physicalTopology;
  if(!t?.coverings?.some(c=>c.bundle))return document;
  const pipes=[...t.joiningPipes??[]],byMembership=new Map<string,PhysicalJoiningPipe>();
  const old=[...t.coverings].filter(c=>c.bundle).sort((a,b)=>pipeBundlePaths(b,t.coverings!).flat().length-pipeBundlePaths(a,t.coverings!).flat().length||b.spans.reduce((n,s)=>n+s.to-s.from,0)-a.spans.reduce((n,s)=>n+s.to-s.from,0));
  const converted=new Map<string,PhysicalCovering>();
  for(const covering of old){
    const paths=pipeBundlePaths(covering,t.coverings),key=JSON.stringify(paths);
    let pipe=byMembership.get(key);
    if(!pipe){
      const leaves=new Set(paths.flat());
      const owned=pipes.filter(p=>p.members.some(m=>m.segmentIds.some(id=>leaves.has(id))));
      if(owned.length)continue;
      const occupied=new Set([...t.nodes,...t.segments,...t.coverings,...pipes].map(o=>o.id));
      let id=`op-${covering.id}`;while(occupied.has(id)||id.length>120)id=crypto.randomUUID();
      pipe=createJoiningPipe(document,paths,id,covering);pipes.push(pipe);byMembership.set(key,pipe);
    }
    const first=pipe.members[0]!,lengths=first.segmentIds.map(id=>pathLength(joiningMemberPoints(document,[id]))),total=lengths.reduce((a,b)=>a+b,0);
    const bounds=covering.spans.filter(s=>first.segmentIds.includes(s.segmentId)).map(s=>{
      const i=first.segmentIds.indexOf(s.segmentId),before=lengths.slice(0,i).reduce((a,b)=>a+b,0);
      return {from:(before+s.from*lengths[i]!)/total,to:(before+s.to*lengths[i]!)/total};
    });
    const from=bounds.length?Math.max(0,(Math.min(...bounds.map(s=>s.from))-first.from)/(first.to-first.from)):0;
    const to=bounds.length?Math.min(1,(Math.max(...bounds.map(s=>s.to))-first.from)/(first.to-first.from)):1;
    converted.set(covering.id,{...covering,bundle:undefined,spans:[{segmentId:pipe.id,from:from<to?from:0,to:from<to?to:1}]});
  }
  return {...document,physicalTopology:{...t,joiningPipes:pipes,coverings:t.coverings.map(c=>converted.get(c.id)??(c.bundle?{...c,bundle:undefined}:c))}};
}

export function pruneJoiningPipes(t:PhysicalTopology):PhysicalTopology {
  if(!t.joiningPipes?.length)return t;
  const ids=new Set(t.segments.map(s=>s.id));
  const joiningPipes=t.joiningPipes.flatMap(p=>{
    const members=p.members.filter(m=>m.segmentIds.every(id=>ids.has(id)));
    return members.length>=2?[members.length===p.members.length?p:{...p,members}]:[];
  });
  const allowed=new Set([...ids,...joiningPipes.map(p=>p.id)]);
  return {...t,joiningPipes,coverings:t.coverings?.flatMap(c=>{const spans=c.spans.filter(s=>allowed.has(s.segmentId));return spans.length?[{...c,spans}]:[];})};
}
