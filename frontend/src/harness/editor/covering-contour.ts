import type { Point } from "./model";

const cross = (a: Point, b: Point) => a.x * b.y - a.y * b.x;
const minus = (a: Point, b: Point): Point => ({ x: a.x - b.x, y: a.y - b.y });
const at = (a: Point, b: Point, t: number): Point => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
const key = (p: Point) => `${Math.round(p.x * 1e6)}:${Math.round(p.y * 1e6)}`;

interface Cell { points: Point[]; minX: number; maxX: number; minY: number; maxY: number }
function cell(points: Point[]): Cell {
 return { points, minX: Math.min(...points.map(p => p.x)), maxX: Math.max(...points.map(p => p.x)),
  minY: Math.min(...points.map(p => p.y)), maxY: Math.max(...points.map(p => p.y)) };
}
function convexHull(points: Point[]): Point[] {
 const sorted = [...points].sort((a, b) => a.x - b.x || a.y - b.y);
 const lower: Point[] = [], upper: Point[] = [];
 for (const p of sorted) {
  while (lower.length > 1 && cross(minus(lower.at(-1)!, lower.at(-2)!), minus(p, lower.at(-1)!)) <= 1e-10) lower.pop();
  lower.push(p);
 }
 for (const p of sorted.reverse()) {
  while (upper.length > 1 && cross(minus(upper.at(-1)!, upper.at(-2)!), minus(p, upper.at(-1)!)) <= 1e-10) upper.pop();
  upper.push(p);
 }
 return [...lower.slice(0, -1), ...upper.slice(0, -1)];
}
function simplifySamples(points: readonly Point[], left: readonly number[], right: readonly number[]) {
 const tolerance=.08, keep=new Set([0,points.length-1]),stack:[number,number][]=[[0,points.length-1]];
 while(stack.length){
  const [start,end]=stack.pop()!,a=points[start]!,b=points[end]!;
  const dx=b.x-a.x,dy=b.y-a.y,den=dx*dx+dy*dy;
  let maximum=tolerance,index=-1;
  for(let i=start+1;i<end;i++){
   const p=points[i]!,t=den?Math.max(0,Math.min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/den)):0;
   const error=Math.max(Math.hypot(p.x-a.x-t*dx,p.y-a.y-t*dy),
    Math.abs(left[i]!-left[start]!-(left[end]!-left[start]!)*t),
    Math.abs(right[i]!-right[start]!-(right[end]!-right[start]!)*t));
   if(error>maximum){maximum=error;index=i;}
  }
  if(index>=0){keep.add(index);stack.push([start,index],[index,end]);}
 }
 const indices=[...keep].sort((a,b)=>a-b);
 return {path:indices.map(i=>points[i]!),left:indices.map(i=>left[i]!),right:indices.map(i=>right[i]!)};
}
function inside(poly: readonly Point[], p: Point): boolean {
 let result = false;
 for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
  const a = poly[j]!, b = poly[i]!;
  if ((a.y > p.y) !== (b.y > p.y) && p.x < a.x + (p.y - a.y) * (b.x - a.x) / (b.y - a.y)) result = !result;
 }
 return result;
}

/** Trim only a bounded OP span to transverse end caps. Overhanging spans keep
 * their continuation cells so the visible P remains fully enclosed. */
export function squareConformalContourEnds(contour: readonly Point[], path: readonly Point[]): Point[] {
 if (contour.length < 3 || path.length < 2) return [...contour];
 const first=path[0]!,last=path.at(-1)!;
 const second=path.find(point=>Math.hypot(point.x-first.x,point.y-first.y)>1e-9);
 const previous=[...path].reverse().find(point=>Math.hypot(point.x-last.x,point.y-last.y)>1e-9);
 if(!second||!previous)return [...contour];
 const clip=(input:readonly Point[],endpoint:Point,tangent:Point,keep:1|-1):Point[]=>{
  const output:Point[]=[];
  const signed=(point:Point)=>keep*((point.x-endpoint.x)*tangent.x+(point.y-endpoint.y)*tangent.y);
  for(let index=0;index<input.length;index++){
   const a=input[index]!,b=input[(index+1)%input.length]!,da=signed(a),db=signed(b),ai=da>=-1e-8,bi=db>=-1e-8;
   if(ai)output.push(a);
   if(ai!==bi){const t=da/(da-db||1);output.push({x:a.x+(b.x-a.x)*t,y:a.y+(b.y-a.y)*t});}
  }
  return output;
 };
 const start={x:second.x-first.x,y:second.y-first.y},end={x:last.x-previous.x,y:last.y-previous.y};
 return clip(clip(contour,first,start,1),last,end,-1);
}

/** Union neighbouring sleeve sections and corner joins. Only local sections
 * are merged, so a distant arm of a U cannot fill its open interior. */
export function conformalCoveringContour(path: readonly Point[], left: readonly number[], right: readonly number[], supports: readonly (readonly Point[])[] = []): Point[] {
 if (path.length < 2) return [];
 const simplified=simplifySamples(path,left,right);
 path=simplified.path;left=simplified.left;right=simplified.right;
 const cells: Cell[] = [], normals: Point[] = [];
 for (let i = 1; i < path.length; i++) {
  const a = path[i - 1]!, b = path[i]!, length = Math.hypot(b.x - a.x, b.y - a.y);
  const n = length ? { x: -(b.y - a.y) / length, y: (b.x - a.x) / length } : normals.at(-1) ?? { x: 0, y: 1 };
  normals.push(n);
  if (!length) continue;
  const offset = (p: Point, width: number): Point => ({ x: p.x + n.x * width, y: p.y + n.y * width });
  cells.push(cell([offset(a, left[i - 1]!), offset(b, left[i]!), offset(b, -right[i]!), offset(a, -right[i - 1]!) ]));
 }
 for (let i = 1; i < path.length - 1; i++) {
  const p = path[i]!, before = normals[i - 1]!, after = normals[i]!;
  if(Math.abs(cross(before,after))<1e-6&&before.x*after.x+before.y*after.y>0)continue;
  const corners = [
   { x: p.x + before.x * left[i]!, y: p.y + before.y * left[i]! },
   { x: p.x + after.x * left[i]!, y: p.y + after.y * left[i]! },
   { x: p.x - before.x * right[i]!, y: p.y - before.y * right[i]! },
   { x: p.x - after.x * right[i]!, y: p.y - after.y * right[i]! },
  ];
  const hull = convexHull(corners);
  if (hull.length >= 3) cells.push(cell(hull));
 }
 for (const support of supports) if (support.length >= 3) cells.push(cell(convexHull([...support])));
 interface Edge { a: Point; b: Point; cuts: number[]; minX: number; maxX: number; minY: number; maxY: number }
 const edges: Edge[] = cells.flatMap(c => c.points.map((a, i) => {
  const b = c.points[(i + 1) % c.points.length]!;
  return { a, b, cuts: [0, 1], minX: Math.min(a.x, b.x), maxX: Math.max(a.x, b.x),
   minY: Math.min(a.y, b.y), maxY: Math.max(a.y, b.y) };
 }));
 for (let i = 0; i < edges.length; i++) {
  const first = edges[i]!, r = minus(first.b, first.a);
  for (let j = i + 1; j < edges.length; j++) {
   const second = edges[j]!;
   if (first.maxX < second.minX - 1e-8 || second.maxX < first.minX - 1e-8 ||
       first.maxY < second.minY - 1e-8 || second.maxY < first.minY - 1e-8) continue;
   const s = minus(second.b, second.a), divisor = cross(r, s);
   if (Math.abs(divisor) < 1e-10) {
    if (Math.abs(cross(minus(second.a, first.a), r)) > 1e-8) continue;
    const rr = r.x * r.x + r.y * r.y, ss = s.x * s.x + s.y * s.y;
    if (rr < 1e-12 || ss < 1e-12) continue;
    for (const point of [second.a, second.b]) {
     const t = (point.x - first.a.x) * r.x / rr + (point.y - first.a.y) * r.y / rr;
     if (t > 1e-8 && t < 1 - 1e-8) first.cuts.push(t);
    }
    for (const point of [first.a, first.b]) {
     const u = (point.x - second.a.x) * s.x / ss + (point.y - second.a.y) * s.y / ss;
     if (u > 1e-8 && u < 1 - 1e-8) second.cuts.push(u);
    }
    continue;
   }
   const delta = minus(second.a, first.a), t = cross(delta, s) / divisor, u = cross(delta, r) / divisor;
   if (t > 1e-8 && t < 1 - 1e-8 && u > -1e-8 && u < 1 + 1e-8) first.cuts.push(t);
   if (u > 1e-8 && u < 1 - 1e-8 && t > -1e-8 && t < 1 + 1e-8) second.cuts.push(u);
  }
 }
 const boundary: { from: Point; to: Point; start: string; end: string }[] = [], seen = new Set<string>();
 const gridSize=24, buckets=new Map<string,number[]>();
 const bucket=(x:number,y:number)=>`${x}:${y}`;
 cells.forEach((c,index)=>{
  for(let x=Math.floor(c.minX/gridSize);x<=Math.floor(c.maxX/gridSize);x++)
   for(let y=Math.floor(c.minY/gridSize);y<=Math.floor(c.maxY/gridSize);y++){
    const id=bucket(x,y),items=buckets.get(id);
    if(items)items.push(index);else buckets.set(id,[index]);
   }
 });
 const occupied=(p:Point)=>{
  const x=Math.floor(p.x/gridSize),y=Math.floor(p.y/gridSize);
  for(const index of buckets.get(bucket(x,y))??[]){
   const c=cells[index]!;
   if(p.x>=c.minX-1e-9&&p.x<=c.maxX+1e-9&&p.y>=c.minY-1e-9&&p.y<=c.maxY+1e-9&&inside(c.points,p))return true;
  }
  return false;
 };
 for (const edge of edges) {
  const stops = [...new Set(edge.cuts)].sort((a, b) => a - b);
  for (let i = 1; i < stops.length; i++) {
   const a = at(edge.a, edge.b, stops[i - 1]!), b = at(edge.a, edge.b, stops[i]!);
   const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy);
   if (length < 1e-8) continue;
   const middle = at(a, b, .5), epsilon = Math.min(1e-5, length * 1e-4);
   const one = occupied({ x: middle.x - dy / length * epsilon, y: middle.y + dx / length * epsilon });
   const other = occupied({ x: middle.x + dy / length * epsilon, y: middle.y - dx / length * epsilon });
   if (one === other) continue;
   const from = one ? a : b, to = one ? b : a, start = key(from), end = key(to);
   if (seen.has(`${start}>${end}`)) continue;
   seen.add(`${start}>${end}`); boundary.push({ from, to, start, end });
  }
 }
 const outgoing = new Map<string, number[]>();
 boundary.forEach((edge, i) => outgoing.set(edge.start, [...outgoing.get(edge.start) ?? [], i]));
 const visited = new Set<number>(), loops: Point[][] = [];
 for (let start = 0; start < boundary.length; start++) {
  if (visited.has(start)) continue;
  const origin = boundary[start]!.start, points: Point[] = [];
  let current: number | undefined = start;
  while (current !== undefined && !visited.has(current)) {
   const edge: (typeof boundary)[number] = boundary[current]!;
   visited.add(current); points.push(edge.from);
   if (edge.end === origin) { if (points.length >= 3) loops.push(points); break; }
   current = outgoing.get(edge.end)?.find(next => !visited.has(next));
  }
 }
 const area = (points: readonly Point[]) => Math.abs(points.reduce((sum, p, i) => sum + cross(p, points[(i + 1) % points.length]!), 0));
 return loops.sort((a, b) => area(b) - area(a))[0] ?? [];
}
