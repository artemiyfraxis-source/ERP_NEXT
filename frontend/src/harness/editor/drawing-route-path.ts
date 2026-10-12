import { roundedPolylineCommandsV2 } from "../component-library/rounded-polyline-v2";
import type { EditorPoint } from "./editor-types";
import type { HarnessDesignDocument } from "./model";

export const drawingBendRadius=(document:HarnessDesignDocument)=>document.drawingDocuments?.bendRadius??24;

/** Circular tangent joins decorate the stored control polygon; lengths and IDs stay unchanged. */
export function drawingRouteCommands(points: readonly EditorPoint[],radius=24) {
  const clean = points.filter((p,i)=>i===0 || Math.hypot(p.x-points[i-1]!.x,p.y-points[i-1]!.y)>1e-7);
  return roundedPolylineCommandsV2(clean.map(p=>[p.x,p.y] as const),radius) ?? clean.map((p,i)=>({kind:i?"line" as const:"move" as const,...p}));
}
export function traceDrawingRoute(context:CanvasRenderingContext2D,points:readonly EditorPoint[],radius=24) {
  context.lineJoin=radius===0?"miter":"round";
  context.beginPath();
  for(const c of drawingRouteCommands(points,radius)) {
    if(c.kind==="move")context.moveTo(c.x,c.y);
    else if(c.kind==="line")context.lineTo(c.x,c.y);
    else context.arcTo(c.cornerX,c.cornerY,c.x,c.y,c.radius);
  }
}
/** Sample the same circular joins for pointer picking, instead of the hidden sharp corner. */
export function drawingRouteHitPoints(points:readonly EditorPoint[],radius=24):EditorPoint[] {
  return drawingRouteSamples(points,radius).map(s=>s.point);
}

/** Keep the authored distance alongside each visible point. A sleeve boundary
 * stays on its original route parameter when a corner is rounded or sharpened. */
export function drawingRouteSamples(points:readonly EditorPoint[],radius=24) {
  const result:{point:EditorPoint;distance:number}[]=[];
  let distance=0;
  for(const c of drawingRouteCommands(points,radius)) {
    const start=result.at(-1)?.point;
    if(c.kind!=="arc"){
      if(start)distance+=Math.hypot(c.x-start.x,c.y-start.y);
      result.push({point:{x:c.x,y:c.y},distance});continue;
    }
    if(!start)continue;
    const dx=c.cornerX-start.x,dy=c.cornerY-start.y,length=Math.hypot(dx,dy);
    if(length<1e-9){result.push({point:{x:c.x,y:c.y},distance});continue;}
    const sign=c.sweep?1:-1,center={x:start.x-dy/length*c.radius*sign,y:start.y+dx/length*c.radius*sign};
    const a=Math.atan2(start.y-center.y,start.x-center.x),b=Math.atan2(c.y-center.y,c.x-center.x);
    let angle=b-a;if(sign>0&&angle<0)angle+=2*Math.PI;if(sign<0&&angle>0)angle-=2*Math.PI;
    const steps=Math.max(2,Math.ceil(Math.abs(angle)*c.radius/2));
    const authoredLength=length+Math.hypot(c.x-c.cornerX,c.y-c.cornerY);
    for(let i=1;i<=steps;i++)result.push({point:i===steps?{x:c.x,y:c.y}:{x:center.x+Math.cos(a+angle*i/steps)*c.radius,y:center.y+Math.sin(a+angle*i/steps)*c.radius},distance:distance+authoredLength*i/steps});
    distance+=authoredLength;
  }
  return result;
}

/** Cut the visible curve by authored distance, not by its shorter arc length. */
export function drawingRouteSection(points:readonly EditorPoint[],radius:number,from:number,to:number,stops:readonly number[]=[]) {
  const samples=drawingRouteSamples(points,radius),length=samples.at(-1)?.distance??0;
  const at=(distance:number)=>{
    const index=samples.findIndex(s=>s.distance>=distance),b=samples[Math.max(0,index)]!,a=samples[Math.max(0,index-1)]!;
    const t=b.distance===a.distance?0:(distance-a.distance)/(b.distance-a.distance);
    return {distance,point:{x:a.point.x+(b.point.x-a.point.x)*t,y:a.point.y+(b.point.y-a.point.y)*t}};
  };
  if(!samples.length||from>=to||to<0||from>length)return [];
  const lo=Math.max(0,from),hi=Math.min(length,to);
  return [at(lo),...samples.filter(s=>s.distance>lo&&s.distance<hi),...stops.filter(d=>d>lo&&d<hi).map(at),at(hi)]
    .sort((a,b)=>a.distance-b.distance).filter((s,i,all)=>!i||s.distance-all[i-1]!.distance>1e-7);
}

/** Inverse of the display mapping, used when moving a sleeve on a visible arc. */
export function projectOntoDrawingRoute(points:readonly EditorPoint[],radius:number,point:EditorPoint):number {
  const samples=drawingRouteSamples(points,radius);
  let nearest=Infinity,distance=0;
  for(let i=1;i<samples.length;i++){
    const a=samples[i-1]!,b=samples[i]!,dx=b.point.x-a.point.x,dy=b.point.y-a.point.y;
    const t=Math.max(0,Math.min(1,((point.x-a.point.x)*dx+(point.y-a.point.y)*dy)/(dx*dx+dy*dy||1)));
    const delta=Math.hypot(point.x-a.point.x-t*dx,point.y-a.point.y-t*dy);
    if(delta<nearest){nearest=delta;distance=a.distance+(b.distance-a.distance)*t;}
  }
  return distance;
}
