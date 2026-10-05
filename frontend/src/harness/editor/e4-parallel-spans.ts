import type {Point} from "./model";

export interface ParallelSegment {readonly index:number;readonly start:Point;readonly end:Point;readonly orientation:"horizontal"|"vertical"}
export interface ParallelSpan {
  readonly orientation:"horizontal"|"vertical";
  /** Oblique spans use an orthonormal frame (along, cross). Axis spans keep world coordinates. */
  readonly direction?:Point;
  readonly start:number;readonly end:number;
  readonly crossMinimum:number;readonly crossMaximum:number;
  readonly firstWireDirection:1|-1;
  readonly segmentByWireId:Readonly<Record<string,ParallelSegment>>;
}
export function parallelSpanWorld(span:ParallelSpan,along:number,cross:number):Point {
  const u=span.direction;
  return u?{x:u.x*along-u.y*cross,y:u.y*along+u.x*cross}
    :span.orientation==="horizontal"?{x:along,y:cross}:{x:cross,y:along};
}
export function parallelSpanLocal(span:ParallelSpan,p:Point):Point {
  const u=span.direction;
  return u?{x:p.x*u.x+p.y*u.y,y:-p.x*u.y+p.y*u.x}
    :span.orientation==="horizontal"?p:{x:p.y,y:p.x};
}

/** Longest common parallel interval, shared by rendering and model validation. */
export function commonParallelSpan(paths:readonly {id:string;points:readonly Point[]}[]):ParallelSpan|null {
  if(!paths.length)return null;
  const lists=paths.map(path=>path.points.slice(1).flatMap((end,index)=>{
    const start=path.points[index]!,dx=end.x-start.x,dy=end.y-start.y,length=Math.hypot(dx,dy);
    if(length<1e-7)return [];
    const sign=dx<0||Math.abs(dx)<1e-8&&dy<0?-1:1;
    return [{index,start,end,orientation:Math.abs(dy)<1e-8?"horizontal" as const:"vertical" as const,u:{x:dx/length*sign,y:dy/length*sign}}];
  }));
  const directions:Point[]=[{x:1,y:0},{x:0,y:1}];
  for(const s of lists[0]??[])if(!directions.some(u=>Math.abs(u.x*s.u.y-u.y*s.u.x)<1e-7))directions.push(s.u);
  let best:ParallelSpan|null=null;
  for(const u of directions){
    const vertical=Math.abs(u.x)<1e-8,diagonal=Math.abs(u.x)>1e-8&&Math.abs(u.y)>1e-8;
    const along=(p:Point)=>p.x*u.x+p.y*u.y;
    const cross=(p:Point)=>diagonal?-p.x*u.y+p.y*u.x:vertical?p.x:p.y;
    const groups=lists.map(list=>list.filter(s=>Math.abs(u.x*s.u.y-u.y*s.u.x)<1e-7).map(s=>({s,start:Math.min(along(s.start),along(s.end)),end:Math.max(along(s.start),along(s.end))})));
    if(groups.some(g=>!g.length))continue;
    const starts=[...new Set(groups.flatMap(g=>g.map(s=>s.start)))].sort((a,b)=>a-b);
    for(const start of starts){
      const chosen=groups.map(g=>g.filter(s=>s.start<=start+1e-7&&s.end>start+1e-7).sort((a,b)=>b.end-a.end)[0]);
      if(chosen.some(s=>!s))continue;
      const end=Math.min(...chosen.map(s=>s!.end));
      if(best&&best.end-best.start>=end-start-1e-7)continue;
      const crosses=chosen.map(s=>cross(s!.s.start));
      best={orientation:vertical?"vertical":"horizontal",...(diagonal?{direction:u}:{}),start,end,
        crossMinimum:Math.min(...crosses),crossMaximum:Math.max(...crosses),firstWireDirection:along(chosen[0]!.s.end)>along(chosen[0]!.s.start)?1:-1,
        segmentByWireId:Object.fromEntries(paths.map((p,i)=>{const {u:_,...segment}=chosen[i]!.s;return [p.id,segment];}))};
    }
  }
  return best;
}

/** The twisted E4 symbol is laid out only on a horizontal run shared by both
 * real conductors. Vertical and oblique spans remain valid for other E4
 * features, such as screens, but never become the differential pair body. */
export function commonHorizontalPairSpan(paths:readonly {id:string;points:readonly Point[]}[]):ParallelSpan|null {
  if(paths.length!==2)return null;
  const horizontal=paths.map(path=>path.points.slice(1).flatMap((end,index)=>{
    const start=path.points[index]!;
    if(Math.abs(end.y-start.y)>1e-7||Math.abs(end.x-start.x)<1e-7)return [];
    return [{index,start,end,minimum:Math.min(start.x,end.x),maximum:Math.max(start.x,end.x)}];
  }));
  let best:ParallelSpan|null=null;
  for(const first of horizontal[0]??[])for(const second of horizontal[1]??[]){
    const start=Math.max(first.minimum,second.minimum),end=Math.min(first.maximum,second.maximum);
    if(end-start<1e-7||best&&best.end-best.start>=end-start-1e-7)continue;
    best={orientation:"horizontal",start,end,crossMinimum:Math.min(first.start.y,second.start.y),
      crossMaximum:Math.max(first.start.y,second.start.y),firstWireDirection:first.end.x>=first.start.x?1:-1,
      segmentByWireId:{[paths[0]!.id]:{index:first.index,start:first.start,end:first.end,orientation:"horizontal"},
        [paths[1]!.id]:{index:second.index,start:second.start,end:second.end,orientation:"horizontal"}}};
  }
  return best;
}
