import {clearDecorationSpans} from "./e4-decoration-spans";
type Point = { readonly x: number; readonly y: number };
type Orientation = "horizontal" | "vertical";
interface Segment { readonly index:number; readonly start:Point; readonly end:Point; readonly orientation:Orientation }
export interface ScreenCrossSection {
  readonly orientation:Orientation;
  readonly start:number;
  readonly end:number;
  readonly crossMinimum:number;
  readonly crossMaximum:number;
  readonly firstWireDirection:1|-1;
  readonly routeIndex:number;
  readonly segmentByWireId:Readonly<Record<string,Segment>>;
  readonly crossings:readonly Segment[];
}

/** Exact transverse extent at the requested position, including oblique paths. */
export function screenSectionAt(span:ScreenCrossSection,position:number):{crossMinimum:number;crossMaximum:number} {
  const along=(p:Point)=>span.orientation==="horizontal"?p.x:p.y;
  const cross=(p:Point)=>span.orientation==="horizontal"?p.y:p.x;
  const values=span.crossings.map(s=>{
    const t=Math.max(0,Math.min(1,(position-along(s.start))/(along(s.end)-along(s.start))));
    return cross(s.start)+(cross(s.end)-cross(s.start))*t;
  });
  return {crossMinimum:Math.min(...values),crossMaximum:Math.max(...values)};
}

/** Body orientation is independent of the axis used to slide along the wires. */
export function uprightScreenBody(span:ScreenCrossSection,position:number,minimumHeight:number,narrowWidth:number) {
  const section=screenSectionAt(span,position),cross=(section.crossMinimum+section.crossMaximum)/2;
  const width=span.orientation==="horizontal"?narrowWidth:Math.max(narrowWidth,section.crossMaximum-section.crossMinimum+18);
  const height=Math.max(32,minimumHeight,span.orientation==="horizontal"?section.crossMaximum-section.crossMinimum+18:width+14);
  return {center:span.orientation==="horizontal"?{x:position,y:cross}:{x:cross,y:position},orientation:"horizontal" as const,alongSize:width,crossSize:height};
}

export function clearScreenSections(spans:readonly ScreenCrossSection[],tables:readonly {x:number;y:number;width:number;height:number}[],minimumHeight:number,narrowWidth:number):ScreenCrossSection[] {
  return spans.flatMap(span=>{
    const width=span.orientation==="horizontal"?narrowWidth:Math.max(narrowWidth,span.crossMaximum-span.crossMinimum+18);
    const height=Math.max(32,minimumHeight,span.orientation==="horizontal"?span.crossMaximum-span.crossMinimum+18:width+14);
    return clearDecorationSpans([span],tables,(span.orientation==="horizontal"?width:height)/2,()=>span.orientation==="horizontal"?height:width);
  });
}

/** Sweep every route at the same X, independently of its bend indices.
 * A pure vertical bundle uses Y for movement; the body remains upright.
 * Neither case modifies a route.
 */
export function screenCrossSections(paths:readonly {id:string;points:readonly Point[]}[]):ScreenCrossSection[] {
  if(!paths.length)return [];
  for(const orientation of ["horizontal","vertical"] as const){
    const along=(p:Point)=>orientation==="horizontal"?p.x:p.y;
    const cross=(p:Point)=>orientation==="horizontal"?p.y:p.x;
    const lists=paths.map(path=>path.points.slice(1).flatMap((end,index)=>{
      const start=path.points[index]!;
      return along(start)!==along(end)?[{index,start,end,orientation}]:[];
    }));
    if(lists.some(list=>!list.length))continue;
    const breaks=[...new Set(lists.flatMap(list=>list.flatMap(s=>[along(s.start),along(s.end)])))].sort((a,b)=>a-b);
    const direction=along(lists[0]![0]!.end)>along(lists[0]![0]!.start)?1:-1;
    const spans:ScreenCrossSection[]=[];
    for(let i=1;i<breaks.length;i++){
      const start=breaks[i-1]!,end=breaks[i]!,middle=(start+end)/2;
      const selected=lists.map(list=>list.filter(s=>Math.min(along(s.start),along(s.end))<middle&&Math.max(along(s.start),along(s.end))>middle));
      if(selected.some(list=>!list.length))continue;
      const crosses=selected.flatMap(list=>list.flatMap(s=>[start,end].map(p=>cross(s.start)+(cross(s.end)-cross(s.start))*(p-along(s.start))/(along(s.end)-along(s.start)))));
      spans.push({orientation,start,end,crossMinimum:Math.min(...crosses),crossMaximum:Math.max(...crosses),firstWireDirection:direction,routeIndex:0,
        crossings:selected.flat(),
        segmentByWireId:Object.fromEntries(paths.map((path,index)=>[path.id,selected[index]![0]!]))});
    }
    if(spans.length)return spans.sort((a,b)=>direction*(a.start-b.start));
  }
  return [];
}
