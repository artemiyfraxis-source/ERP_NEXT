import {findWireEndpoint,type HarnessDesignDocument,type Point} from "./model";
import {physicalWirePoints} from "./physical-wire-geometry";
import {physicalSegmentPoints} from "./physical-geometry";
import {physicalNodePoint} from "./physical-ports";
import {coveringPaths} from "./physical-coverings";

const length=(points:readonly Point[])=>points.slice(1).reduce((n,p,i)=>n+Math.hypot(p.x-points[i]!.x,p.y-points[i]!.y),0);

/** Position by arc length across all legs; gaps between covering spans add no material. */
export function pointAlongDrawingPaths(paths:readonly (readonly Point[])[],fraction:number):{point:Point;normal:Point}|null {
  const total=paths.reduce((n,p)=>n+length(p),0);if(total<1e-8)return null;
  let remaining=total*Math.max(0,Math.min(1,fraction));
  for(const path of paths)for(let i=1;i<path.length;i++){
    const a=path[i-1]!,b=path[i]!,dx=b.x-a.x,dy=b.y-a.y,l=Math.hypot(dx,dy);if(l<1e-8)continue;
    if(remaining<=l+1e-7)return {point:{x:a.x+dx*Math.min(1,remaining/l),y:a.y+dy*Math.min(1,remaining/l)},normal:{x:dy/l,y:-dx/l}};
    remaining-=l;
  }
  return null;
}

/** Automatic initial position only. Existing/manual leaders remain untouched. */
export function initialLinearLeader(document:HarnessDesignDocument,id:string):{point:Point;normal:Point}|null {
  const topology=document.physicalTopology;
  const segment=topology?.segments.find(s=>s.id===id),covering=topology?.coverings?.find(c=>c.id===id);
  const cable=document.cables.find(c=>c.id===id),wire=document.wires.find(w=>w.id===(cable?.memberWireIds[0]??id));
  let paths:readonly (readonly Point[])[]=[];
  let from:Point|undefined,to:Point|undefined;
  if(segment){
    paths=[physicalSegmentPoints(document,segment)];
    const a=topology!.nodes.find(n=>n.id===segment.from),b=topology!.nodes.find(n=>n.id===segment.to);
    if(a?.connectorId)from=physicalNodePoint(document,a);if(b?.connectorId)to=physicalNodePoint(document,b);
  }else if(covering){
    paths=coveringPaths(document,covering);
    const first=covering.spans[0],last=covering.spans.at(-1);
    const a=topology!.segments.find(s=>s.id===first?.segmentId),b=topology!.segments.find(s=>s.id===last?.segmentId);
    const startNode=topology!.nodes.find(n=>n.id===a?.from),endNode=topology!.nodes.find(n=>n.id===b?.to);
    if(startNode?.connectorId)from=physicalNodePoint(document,startNode);
    if(endNode?.connectorId)to=physicalNodePoint(document,endNode);
  }else if(wire){
    const a=findWireEndpoint(document,wire.from,"drawing"),b=findWireEndpoint(document,wire.to,"drawing");
    if(!a||!b)return null;
    paths=[physicalWirePoints(document,wire.id,a,b)??[a,...wire.drawingRoute,b]];
    if(document.connectors.some(c=>c.id===wire.from.connectorId))from=a;
    if(document.connectors.some(c=>c.id===wire.to.connectorId))to=b;
  }else return null;
  const a=paths[0]?.[0],b=paths.at(-1)?.at(-1);if(!a||!b)return null;
  const reverse=to?(!from||to.x<from.x||to.x===from.x&&to.y<from.y):!from&&(b.x<a.x||b.x===a.x&&b.y<a.y);
  return pointAlongDrawingPaths(reverse?[...paths].reverse().map(p=>[...p].reverse()):paths,.2);
}
