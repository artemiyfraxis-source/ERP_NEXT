import { defaultE4WireLead, wireEndpointE4Anchor, type HarnessDesignDocument, type E4RouteAnchor, type Point } from "./model";
import { segmentPointDistance } from "./segment-geometry";
import type { PhysicalDragMode } from "./physical-editing";
import { straightLeadEnd } from "./route-lead";
/** Edit authored points before the electrical route validates its leads and obstacles. */
export function editedE4Points(points:readonly Point[],index:number,point:Point,mode:PhysicalDragMode,insert=false,simplify=true):Point[] {
  if(!Number.isInteger(index)||index<0||index>=points.length-(insert?1:2))throw new Error("Точка маршрута Э4 не найдена.");
  if(!Number.isFinite(point.x)||!Number.isFinite(point.y))throw new Error("Координаты перегиба заданы неверно.");
  const result=points.map(p=>({...p})),at=index+1;
  if(insert){
    const a=result[index]!,b=result[at]!,delta={x:point.x-(a.x+b.x)/2,y:point.y-(a.y+b.y)/2};
    if(mode==="carry")for(const i of [index,at])if(i>0&&i<result.length-1)
      result[i]={x:result[i]!.x+delta.x,y:result[i]!.y+delta.y};
    result.splice(at,0,{...point});
  }
  else {
    const delta={x:point.x-result[at]!.x,y:point.y-result[at]!.y};
    result[at]={...point};
    if(mode==="carry")for(const i of [at-1,at+1])if(i>0&&i<result.length-1)
      result[i]={x:result[i]!.x+delta.x,y:result[i]!.y+delta.y};
  }
  if(simplify&&!insert&&mode==="carry")for(let i=result.length-2;i>0;i--){
    const a=result[i-1]!,b=result[i]!,c=result[i+1]!;
    if(Math.abs((b.x-a.x)*(c.y-b.y)-(b.y-a.y)*(c.x-b.x))<1e-7&&
      (b.x-a.x)*(c.x-b.x)+(b.y-a.y)*(c.y-b.y)>=0)result.splice(i,1);
  }
  return result;
}

/** Junction identity follows its original segment, never the nearest unrelated line. */
export function movedE4Junctions(document:HarnessDesignDocument,wireId:string,before:readonly Point[],after:readonly Point[]):Map<string,Point> {
  const result=new Map<string,Point>();
  if(before.length!==after.length)throw new Error("Не совпадают опоры переноса узла.");
  for(const j of document.junctions.filter(j=>j.wireIds.includes(wireId))){
    for(let i=0;i<before.length-1;i++){
      const a=before[i]!,b=before[i+1]!;
      if(segmentPointDistance(j.position,{start:a,end:b})>1e-6)continue;
      const dx=b.x-a.x,dy=b.y-a.y,length=dx*dx+dy*dy,t=length?Math.max(0,Math.min(1,((j.position.x-a.x)*dx+(j.position.y-a.y)*dy)/length)):0;
      const p=after[i]!,q=after[i+1]!;
      const next={x:p.x+(q.x-p.x)*t,y:p.y+(q.y-p.y)*t};
      if(Math.hypot(next.x-j.position.x,next.y-j.position.y)>1e-7)result.set(j.id,next);
      break;
    }
  }
  return result;
}

/** A T node follows its carrier, never a branch that merely ends there. If
 * several carriers move differently, use the junction's stored carrier order;
 * the remaining routes must join that one position in the same transaction. */
export function resolveE4JunctionMoves(document:HarnessDesignDocument,proposals:ReadonlyMap<string,ReadonlyMap<string,Point>>):Map<string,Point> {
  const result=new Map<string,Point>();
  for(const junction of document.junctions){
    for(const id of junction.wireIds){
      const wire=document.wires.find(w=>w.id===id);
      if(!wire||[wire.from,wire.to].some(e=>e.junctionId===junction.id))continue;
      const point=proposals.get(id)?.get(junction.id);
      if(point){result.set(junction.id,point);break;}
    }
  }
  return result;
}

/** Move shared junctions and bend only attached portions of their other wires. */
export function followE4Junctions(before:HarnessDesignDocument,after:HarnessDesignDocument,positions:ReadonlyMap<string,Point>,owners:ReadonlySet<string>,mode:PhysicalDragMode):HarnessDesignDocument {
  const changed={...after,junctions:after.junctions.map(j=>positions.has(j.id)?{...j,position:positions.get(j.id)!}:j)};
  return {...changed,wires:changed.wires.map(w=>{
    if(owners.has(w.id))return w;
    const moved=before.junctions.filter(j=>j.wireIds.includes(w.id)&&positions.has(j.id));if(!moved.length)return w;
    // A shield-to-junction branch must be rebuilt as a straight lead by the command.
    if([w.from,w.to].some(e=>e.screenId)&&[w.from,w.to].some(e=>e.junctionId))return w;
    const a=wireEndpointE4Anchor(before,w.from),b=wireEndpointE4Anchor(before,w.to),start=wireEndpointE4Anchor(changed,w.from),end=wireEndpointE4Anchor(changed,w.to);
    if(!a||!b||!start||!end)return w;
    const original=[a.position,...w.e4Route,b.position];
    let points=original.flatMap((p,i)=>{
      const q=original[i+1];if(!q)return [p];
      const inner=moved.filter(j=>segmentPointDistance(j.position,{start:p,end:q})<1e-6&&Math.hypot(j.position.x-p.x,j.position.y-p.y)>1e-6&&Math.hypot(j.position.x-q.x,j.position.y-q.y)>1e-6)
        .sort((j,k)=>Math.hypot(j.position.x-p.x,j.position.y-p.y)-Math.hypot(k.position.x-p.x,k.position.y-p.y));
      return [p,...inner.map(j=>j.position)];
    });
    const anchors=points.map(p=>moved.find(j=>Math.hypot(j.position.x-p.x,j.position.y-p.y)<1e-6)?.id);
    points=moveE4Ends(points,start.position,end.position,mode);
    if(points.length===anchors.length)points=points.map((p,i)=>i===0||i===points.length-1?p:positions.get(anchors[i]??"")??p);
    return {...w,e4Route:preserveE4Leads(points,start,end).slice(1,-1),e4RouteMode:"manual" as const};
  })};
}

/** A freely edited shoulder starts after the directed contact's straight lead. */
export function preserveE4Leads(points:readonly Point[],start:E4RouteAnchor,end:E4RouteAnchor):Point[] {
  let result=[...points];
  for(const [anchor,reverse] of [[start,false],[end,true]] as const){
    if(reverse)result.reverse();
    const direction=anchor.leadDirection;
    if(direction){
      const u=direction==="left"?{x:-1,y:0}:direction==="right"?{x:1,y:0}:direction==="up"?{x:0,y:-1}:{x:0,y:1};
      const a=result[0]!,b=straightLeadEnd(result),d={x:b.x-a.x,y:b.y-a.y},length=Math.min(defaultE4WireLead,anchor.leadLength??defaultE4WireLead);
      if(Math.abs(d.x*u.y-d.y*u.x)>1e-7||d.x*u.x+d.y*u.y<length)
        result.splice(1,0,{x:a.x+u.x*length,y:a.y+u.y*length});
    }
    if(reverse)result.reverse();
  }
  return result;
}

export function moveE4Ends(points:readonly Point[],from:Point,to:Point,mode:PhysicalDragMode):Point[] {
  let result=points.map(p=>({...p}));
  if(result.length===2&&mode==="carry"){
    const [a,b]=result as [Point,Point];result=[a,{x:a.x+(b.x-a.x)/3,y:a.y+(b.y-a.y)/3},{x:a.x+2*(b.x-a.x)/3,y:a.y+2*(b.y-a.y)/3},b];
  }
  const original=[...result],shifts=new Map<number,Point[]>();
  for(const [at,p] of [[0,from],[result.length-1,to]] as const){
    const a=original[at]!,delta={x:p.x-a.x,y:p.y-a.y},shoulder=at===0?1:at-1;
    result[at]=p;
    if(mode==="carry"&&result.length>2&&(delta.x||delta.y))shifts.set(shoulder,[...shifts.get(shoulder)??[],delta]);
  }
  for(const [i,deltas] of shifts)result[i]={x:original[i]!.x+deltas.reduce((n,d)=>n+d.x,0)/deltas.length,y:original[i]!.y+deltas.reduce((n,d)=>n+d.y,0)/deltas.length};
  return result;
}
