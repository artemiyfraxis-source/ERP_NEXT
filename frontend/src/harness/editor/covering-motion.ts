import type { HarnessDesignDocument, Point } from "./model";
import { pipeBundlePaths } from "./pipe-bundle-model";
import { coveringRoute, resolvedCoveringSpan, type PhysicalCovering, type CoveringSpan } from "./physical-coverings";
import { drawingBendRadius, drawingRouteSection } from "./drawing-route-path";
import { pipeBundleProjectionStops, projectPipeBundlePoint } from "./pipe-bundle-projection";

interface Station { point: Point; distance: number }
interface Part { id: string; before: number; length: number; min: number; max: number }
interface Interval { from: number; to: number }
const pointDistance=(a:Point,b:Point)=>Math.hypot(a.x-b.x,a.y-b.y);

function closestDistance(stations: readonly Station[], point: Point): number {
  let nearest=Infinity,result=0;
  for(let i=1;i<stations.length;i++){
    const a=stations[i-1]!,b=stations[i]!,dx=b.point.x-a.point.x,dy=b.point.y-a.point.y;
    const t=Math.max(0,Math.min(1,((point.x-a.point.x)*dx+(point.y-a.point.y)*dy)/(dx*dx+dy*dy||1)));
    const distance=Math.hypot(point.x-a.point.x-t*dx,point.y-a.point.y-t*dy);
    if(distance<nearest){nearest=distance;result=a.distance+(b.distance-a.distance)*t;}
  }
  return result;
}

function coveringAxis(document:HarnessDesignDocument,covering:PhysicalCovering,ids:readonly string[]){
  const parts:Part[]=[],stations:Station[]=[];
  let length=0;
  for(const [i,id] of ids.entries()){
    const route=coveringRoute(document,id);if(!route)return null;
    const before=length,min=i===0?route.min:0,max=i===ids.length-1?route.max:1;
    parts.push({id,before,length:route.length,min,max});
    const display=drawingRouteSection(route.points,drawingBendRadius(document),route.before+min*route.length,route.before+max*route.length,
      pipeBundleProjectionStops(document,id,covering.id).map(f=>route.before+f*route.length));
    stations.push(...display.map(p=>({distance:before+p.distance-route.before,
      point:projectPipeBundlePoint(document,id,(p.distance-route.before)/route.length,p.point,covering.id)})));
    length+=route.length;
  }
  const intervals:Interval[]=[];
  const ranges=covering.spans.flatMap(s=>{
    const part=parts.find(p=>p.id===s.segmentId);if(!part)return [];
    const resolved=resolvedCoveringSpan(document,s);
    return [{from:part.before+resolved.from*part.length,to:part.before+resolved.to*part.length}];
  }).sort((a,b)=>a.from-b.from);
  for(const range of ranges){const last=intervals.at(-1);if(last&&range.from<=last.to+1e-7)last.to=Math.max(last.to,range.to);else intervals.push({...range});}
  return {parts,stations,intervals,length,min:parts[0]!.min*parts[0]!.length,max:length+(parts.at(-1)!.max-1)*parts.at(-1)!.length};
}

/** Re-split intervals while keeping unchanged edge bindings. Resizing one end
 * must not detach an opposite end from its authored bend. Body motion detaches
 * bindings intentionally, as ordinary covering motion does. */
function spansForRanges(document:HarnessDesignDocument,covering:PhysicalCovering,
  axis:NonNullable<ReturnType<typeof coveringAxis>>,ranges:readonly Interval[],keepBindings:boolean):CoveringSpan[]{
  return axis.parts.flatMap(p=>ranges.flatMap(r=>{
    const from=Math.max(p.before+p.min*p.length,r.from),to=Math.min(p.before+p.max*p.length,r.to);
    if(to-from<=1e-7)return [];
    const span:CoveringSpan={segmentId:p.id,from:(from-p.before)/p.length,to:(to-p.before)/p.length};
    if(!keepBindings)return [span];
    const originals=covering.spans.filter(s=>s.segmentId===p.id).map(s=>resolvedCoveringSpan(document,s));
    const fromAnchor=originals.find(s=>Math.abs(s.from-span.from)<1e-8)?.fromAnchor;
    const toAnchor=originals.find(s=>Math.abs(s.to-span.to)<1e-8)?.toAnchor;
    return [{...span,...(fromAnchor!==undefined?{fromAnchor}:{}),...(toAnchor!==undefined?{toAnchor}:{})}];
  }));
}

/** Move a common sleeve freely while keeping its support intervals as stable
 * membership anchors. Only exposed end grips edit those intervals. */
export function moveBundleCovering(document:HarnessDesignDocument,covering:PhysicalCovering,spanIndex:number,
  part:"from"|"to"|"body",start:Point,point:Point,tolerance:number):PhysicalCovering|null {
  if(!covering.bundle)return null;
  const original=covering.spans[spanIndex];if(!original)return null;
  if(part==='body'){
    const dx=point.x-start.x,dy=point.y-start.y;
    if(Math.hypot(dx,dy)<1e-8)return covering;
    const offset=covering.bundle.bodyOffset??{x:0,y:0};
    const limit=(value:number)=>Math.max(-10000,Math.min(10000,value));
    return {...covering,bundle:{...covering.bundle,bodyOffset:{x:limit(offset.x+dx),y:limit(offset.y+dy)}}};
  }
  const paths=pipeBundlePaths(covering,document.physicalTopology!.coverings??[]);
  const path=paths.find(ids=>ids.includes(original.segmentId));if(!path)return null;
  const supported=paths.filter(ids=>covering.spans.some(s=>ids.includes(s.segmentId)));
  if(path.length<2&&supported.length<2)return null;
  const axis=coveringAxis(document,covering,path);if(!axis||!axis.intervals.length)return null;
  const delta=closestDistance(axis.stations,point)-closestDistance(axis.stations,start);
  if(Math.abs(delta)<1e-8)return covering;
  if(supported.length>1){
    const axes=supported.map(ids=>coveringAxis(document,covering,ids));
    if(axes.some(a=>!a||!a.length||!a.intervals.length))return covering;
    const a=axis.stations[0]!.point,b=axis.stations.at(-1)!.point;
    const oriented=axes.map(axis=>{const first=axis!.stations[0]!.point,last=axis!.stations.at(-1)!.point;
      const reverse=pointDistance(a,last)+pointDistance(b,first)<pointDistance(a,first)+pointDistance(b,last);
      return {axis:axis!,direction:reverse?-1:1};});
    const current=axis.parts.find(p=>p.id===original.segmentId)!;
    const span=resolvedCoveringSpan(document,original);
    const edge=current.before+(part==='from'?span.from:span.to)*current.length;
    const selected=axis.intervals.findIndex(r=>edge>=r.from-1e-7&&edge<=r.to+1e-7);
    const edits=oriented.map(({axis:a,direction})=>{
      const localPart=direction===1?part:part==='from'?'to':'from';
      // Disjoint intervals retain their order along the common axis, including
      // when an independent support was authored in the opposite direction.
      const index=direction===1?selected:a.intervals.length-1-selected;
      return {axis:a,direction,localPart,index,ranges:a.intervals.map(r=>({...r}))};
    });
    let lower=-Infinity,upper=Infinity;
    for(const {axis:a,direction,localPart,index,ranges} of edits){
      const range=ranges[index];if(!range)continue;
      const minimum=Math.min(a.length*.001,(range.to-range.from)/4);
      const start=localPart==='from'?(ranges[index-1]?.to??a.min):range.from+minimum;
      const end=localPart==='from'?range.to-minimum:(ranges[index+1]?.from??a.max);
      const lo=(start-range[localPart])/a.length,hi=(end-range[localPart])/a.length;
      lower=Math.max(lower,direction===1?lo:-hi);upper=Math.min(upper,direction===1?hi:-lo);
    }
    let shift=Math.max(lower,Math.min(upper,delta/axis.length));
    const target=edge+shift*axis.length;
    const anchor=axis.parts.flatMap(p=>[p.before,p.before+p.length]).find(d=>
      Math.abs(d-target)<=tolerance&&(d-edge)/axis.length>=lower&&(d-edge)/axis.length<=upper);
    if(anchor!==undefined)shift=(anchor-edge)/axis.length;
    if(Math.abs(shift)<1e-8)return covering;
    for(const edit of edits){
      const offset=shift*edit.axis.length*edit.direction;
      if(edit.ranges[edit.index])edit.ranges[edit.index]![edit.localPart]+=offset;
    }
    const spans=edits.flatMap(({axis,ranges})=>spansForRanges(document,covering,axis,ranges,true));
    return {...covering,spans};
  }
  const current=axis.parts.find(p=>p.id===original.segmentId)!,span=resolvedCoveringSpan(document,original);
  const ranges=axis.intervals.map(r=>({...r}));
  const edge=current.before+(part==='from'?span.from:span.to)*current.length;
  const range=ranges.find(r=>edge>=r.from-1e-7&&edge<=r.to+1e-7);if(!range)return null;
  const index=ranges.indexOf(range),minimum=Math.min(current.length*.001,(range.to-range.from)/4);
  const lo=part==='from'?(ranges[index-1]?.to??axis.min):range.from+minimum;
  const hi=part==='from'?range.to-minimum:(ranges[index+1]?.from??axis.max);
  let target=Math.max(lo,Math.min(hi,edge+delta));
  const closestAnchor=axis.parts.flatMap(p=>[p.before,p.before+p.length]).find(d=>d>=lo&&d<=hi&&Math.abs(d-target)<=tolerance);
  if(closestAnchor!==undefined)target=closestAnchor;
  if(part==='from')range.from=target;else range.to=target;
  const spans=spansForRanges(document,covering,axis,ranges,true);
  return {...covering,spans:[...covering.spans.filter(s=>!path.includes(s.segmentId)),...spans]};
}

/** Internal fragment boundaries belong to one sleeve; only its exposed ends
 * get resize grips. Body picking retains the span identity for the command. */
export function bundleSpanEdgeVisible(document:HarnessDesignDocument,c:PhysicalCovering,index:number,edge:"from"|"to"):boolean{
  if(!c.bundle)return true;
  const span=c.spans[index]!,resolved=resolvedCoveringSpan(document,span);
  if(edge==='from'?Math.abs(resolved.from)>1e-7:Math.abs(resolved.to-1)>1e-7)return true;
  const path=pipeBundlePaths(c,document.physicalTopology!.coverings??[]).find(p=>p.includes(span.segmentId));if(!path)return true;
  const at=path.indexOf(span.segmentId),neighbor=path[at+(edge==='from'?-1:1)];if(!neighbor)return true;
  return !c.spans.some(s=>{const r=resolvedCoveringSpan(document,s);return s.segmentId===neighbor&&(edge==='from'?Math.abs(r.to-1)<1e-7:Math.abs(r.from)<1e-7);});
}
