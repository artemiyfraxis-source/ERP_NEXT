import { projectComponentTemplateView, type ComponentTemplateViewInstance, type ProjectedComponentTemplateCommand } from "./component-template-view-renderer";
import { drawingLocalPoint, drawingPointToLocal } from "./drawing-scale";
import { findWireEndpoint, type HarnessDesignDocument, type Point } from "./model";
import { physicalWirePoints } from "./physical-wire-geometry";
import { physicalNodePoint } from "./physical-ports";
import { physicalSegmentPoints } from "./physical-geometry";
import { drawingPipeWidth } from "./drawing-thickness";
import { coveringScene } from "./covering-layout";
import { coveringSurfaces } from "./covering-renderer";
import { drawingBendRadius, drawingRouteHitPoints } from "./drawing-route-path";

/** Local contours from the pinned drawing; independent of camera, translation and placement scale. */
export type DrawingPerimeters = ReadonlyMap<string, readonly (readonly Point[])[]>;
const rectangle = (x:number,y:number,w:number,h:number):Point[] => [{x,y},{x:x+w,y},{x:x+w,y:y+h},{x,y:y+h},{x,y}];

function commandPath(c:ProjectedComponentTemplateCommand):Point[] {
  let points:Point[]=[];
  if(c.opacity<=0 || c.kind==="text")return [];
  if(c.kind==="polyline")points=[...c.points,...(c.closed?[c.points[0]!]:[])];
  if(c.kind==="rectangle") {
    const r=c.radii.map(r=>Math.max(0,Math.min(r,c.width/2,c.height/2)));
    const corners=[{x:c.x+c.width-r[1]!,y:c.y+r[1]!,r:r[1]!,a:-Math.PI/2},{x:c.x+c.width-r[2]!,y:c.y+c.height-r[2]!,r:r[2]!,a:0},{x:c.x+r[3]!,y:c.y+c.height-r[3]!,r:r[3]!,a:Math.PI/2},{x:c.x+r[0]!,y:c.y+r[0]!,r:r[0]!,a:Math.PI}];
    points=corners.flatMap(p=>Array.from({length:13},(_,i)=>({x:p.x+p.r*Math.cos(p.a+i*Math.PI/24),y:p.y+p.r*Math.sin(p.a+i*Math.PI/24)})));
    points.push(points[0]!);
  }
  if(c.kind==="image")points=rectangle(c.x,c.y,c.width,c.height);
  if(c.kind==="ellipse")points=Array.from({length:129},(_,i)=>({x:c.centerX+c.radiusX*Math.cos(i*Math.PI/64),y:c.centerY+c.radiusY*Math.sin(i*Math.PI/64)}));
  if(c.kind==="bezier") {
    points=[c.points[0]!];
    for(let i=1;i<c.points.length;i+=3)for(let n=1;n<=32;n++) {
      const t=n/32,s=1-t,[a,b,d,e]=c.points.slice(i-1,i+3) as [Point,Point,Point,Point];
      points.push({x:s*s*s*a.x+3*s*s*t*b.x+3*s*t*t*d.x+t*t*t*e.x,y:s*s*s*a.y+3*s*s*t*b.y+3*s*t*t*d.y+t*t*t*e.y});
    }
    if(c.closed)points.push(points[0]!);
  }
  const {a,b,c:cc,d,e,f}=c.transform;
  return points.map(p=>({x:a*p.x+cc*p.y+e,y:b*p.x+d*p.y+f}));
}

export function buildDrawingPerimeters(instances:readonly ComponentTemplateViewInstance[]):DrawingPerimeters {
  return new Map(instances.flatMap(instance=>{
    const projection=projectComponentTemplateView({...instance,drawingPlacements:undefined},"drawing",{x:0,y:0});
    if(!projection)return [];
    const foreground=projection.commands.filter(c=>c.kind!=="image"||!c.underlay).map(commandPath).filter(p=>p.length>1);
    return [[instance.objectId,foreground.length?foreground:projection.commands.map(commandPath).filter(p=>p.length>1)] as const];
  }));
}

function nearestOnPaths(paths:readonly (readonly Point[])[],target:Point):Point|null {
  let best:Point|null=null,distance=Infinity;
  for(const points of paths)for(let i=1;i<points.length;i++){
    const a=points[i-1]!,b=points[i]!,dx=b.x-a.x,dy=b.y-a.y;
    const t=Math.max(0,Math.min(1,((target.x-a.x)*dx+(target.y-a.y)*dy)/(dx*dx+dy*dy||1)));
    const p={x:a.x+t*dx,y:a.y+t*dy},d=Math.hypot(p.x-target.x,p.y-target.y);
    if(d<distance){best=p;distance=d;}
  }
  return best;
}

/** Outermost intersection, so a leader cannot terminate at internal contact linework. */
export function perimeterPoint(paths:readonly (readonly Point[])[],target:Point):Point|null {
  const points=paths.flat();if(!points.length)return null;
  let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  for(const p of points){minX=Math.min(minX,p.x);minY=Math.min(minY,p.y);maxX=Math.max(maxX,p.x);maxY=Math.max(maxY,p.y);}
  const center={x:(minX+maxX)/2,y:(minY+maxY)/2};
  let dx=target.x-center.x,dy=target.y-center.y;
  if(Math.hypot(dx,dy)<1e-8){dx=0;dy=-1;}
  let best:Point|null=null,furthest=-Infinity;
  for(const path of paths)for(let i=1;i<path.length;i++){
    const a=path[i-1]!,b=path[i]!,ex=b.x-a.x,ey=b.y-a.y,den=dx*ey-dy*ex;
    if(Math.abs(den)<1e-10)continue;
    const ax=a.x-center.x,ay=a.y-center.y,t=(ax*ey-ay*ex)/den,u=(ax*dy-ay*dx)/den;
    if(t>=0&&u>=-1e-8&&u<=1+1e-8&&t>furthest){best={x:center.x+t*dx,y:center.y+t*dy};furthest=t;}
  }
  return best??nearestOnPaths(paths,target);
}

export function drawingObjectPerimeter(document:HarnessDesignDocument,id:string,target:Point,perimeters?:DrawingPerimeters):Point|null {
  const connector=document.connectors.find(c=>c.id===id);
  if(connector){
    const origin=connector.positions.drawing;
    const local=drawingPointToLocal({x:target.x-origin.x,y:target.y-origin.y},connector.drawingPlacements);
    const paths=perimeters?.get(id);
    // Empty drawings use the same fallback body as CanvasViewport.
    const point=paths?.length?perimeterPoint(paths,local):perimeterPoint([rectangle(0,0,118,Math.max(72,44+connector.contacts.length*16))],{x:target.x-origin.x,y:target.y-origin.y});
    if(!point)return null;
    const offset=paths?.length?drawingLocalPoint(point,connector.drawingPlacements):point;
    return {x:origin.x+offset.x,y:origin.y+offset.y};
  }
  const item=document.drawingDocuments?.specificationItems?.find(i=>i.id===id);
  if(item?.position)return perimeterPoint([rectangle(item.position.x,item.position.y,110,38)],target);
  const topology=document.physicalTopology,node=topology?.nodes.find(n=>n.id===id);
  if(node){const p=physicalNodePoint(document,node),angle=Math.atan2(target.y-p.y,target.x-p.x);return {x:p.x+5*Math.cos(angle),y:p.y+5*Math.sin(angle)};}
  const covering=topology?.coverings?.find(c=>c.id===id),segment=topology?.segments.find(s=>s.id===id);
  if(covering) {
    const scene=coveringScene(document).find(c=>c.id===id);
    const polygons=scene?coveringSurfaces(scene).map(s=>[...s.polygon,s.polygon[0]!]):[];
    return nearestOnPaths(polygons,target);
  }
  const cable=document.cables.find(c=>c.id===id),wire=document.wires.find(w=>w.id===(cable?.memberWireIds[0]??id));
  const start=wire&&findWireEndpoint(document,wire.from,"drawing"),end=wire&&findWireEndpoint(document,wire.to,"drawing");
  const paths=segment?[physicalSegmentPoints(document,segment)]:wire&&start&&end?[physicalWirePoints(document,wire.id,start,end)??[start,...wire.drawingRoute,end]]:[];
  const point=nearestOnPaths(paths.map(p=>drawingRouteHitPoints(p,drawingBendRadius(document))),target);if(!point)return null;
  const radius=segment?drawingPipeWidth(document,segment)/2:1.5;
  const angle=Math.atan2(target.y-point.y,target.x-point.x);
  return {x:point.x+radius*Math.cos(angle),y:point.y+radius*Math.sin(angle)};
}


/** Connector dimension witness starts at the nearest outer corner of its pinned picture. */
export function drawingConnectorCorner(document:HarnessDesignDocument,id:string,target:Point,perimeters?:DrawingPerimeters):Point|null {
 const c=document.connectors.find(c=>c.id===id);if(!c)return null;
 const points=(perimeters?.get(id)?.flat()??rectangle(0,0,118,Math.max(72,44+c.contacts.length*16))).map(p=>{const q=drawingLocalPoint(p,c.drawingPlacements);return {x:c.positions.drawing.x+q.x,y:c.positions.drawing.y+q.y};});
 const sorted=[...new Map(points.map(p=>[`${p.x},${p.y}`,p])).values()].sort((a,b)=>a.x-b.x||a.y-b.y);
 const cross=(a:Point,b:Point,c:Point)=>(b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x);
 const half=(list:Point[])=>{const result:Point[]=[];for(const p of list){while(result.length>=2&&cross(result.at(-2)!,result.at(-1)!,p)<=1e-7)result.pop();result.push(p);}return result;};
 const hull=[...half(sorted),...half([...sorted].reverse())];
 return hull.reduce<Point|null>((best,p)=>!best||Math.hypot(p.x-target.x,p.y-target.y)<Math.hypot(best.x-target.x,best.y-target.y)?p:best,null);
}
