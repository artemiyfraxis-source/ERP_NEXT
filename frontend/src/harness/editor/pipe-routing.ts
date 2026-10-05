import type { Point } from "./model";

const dot = (a: Point, b: Point) => a.x * b.x + a.y * b.y;
const cross = (a: Point, b: Point) => a.x * b.y - a.y * b.x;
const opposite = (p: Point): Point => ({ x: -p.x, y: -p.y });
export const directionTowards = (a: Point, b: Point): Point => Math.abs(b.x-a.x) >= Math.abs(b.y-a.y)
  ? { x: b.x >= a.x ? 1 : -1, y: 0 } : { x: 0, y: b.y >= a.y ? 1 : -1 };

/** Explicit input distinguishes user-owned geometry from an automatic route. */
export type PipeRouteInput =
  | { readonly kind: "authored"; readonly points: readonly Point[] }
  | { readonly kind: "automatic"; readonly start: Point; readonly end: Point;
      readonly from: Point | null; readonly to: Point | null };

export function computePipeRoute(input: PipeRouteInput): Point[] {
  return input.kind === "authored" ? [...input.points] : automaticPipeRoute(input.start, input.end, input.from, input.to);
}

export function constrainedPolyline(points: readonly Point[], snap: boolean): Point[] {
  const result: Point[] = [];
  for (const end of points) {
    const start = result.at(-1);
    if (start && Math.hypot(end.x - start.x, end.y - start.y) < 1e-7) continue;
    if (start && snap) {
      const dx = end.x - start.x, dy = end.y - start.y;
      const angle = Math.round(Math.atan2(dy, dx) / (Math.PI / 12)) * Math.PI / 12;
      const cx = Math.cos(angle), sy = Math.sin(angle);
      const distance = Math.min(Math.abs(cx) < 1e-8 ? Infinity : Math.abs(dx / cx), Math.abs(sy) < 1e-8 ? Infinity : Math.abs(dy / sy));
      const bend = { x: start.x + distance * cx, y: start.y + distance * sy };
      if (Number.isFinite(distance) && distance > 1e-7 && Math.hypot(bend.x - end.x, bend.y - end.y) > 1e-7) result.push(bend);
    }
    result.push(end);
  }
  return result;
}

/** Minimum-corner route, with fixed outward leads and 45° preferred over a square elbow.
 * Searches at most four legs; no intermediate routing vertices are persisted as edits.
 */
export function automaticPipeRoute(start: Point, end: Point, from: Point | null = null, to: Point | null = null): Point[] {
  const delta = { x: end.x-start.x, y: end.y-start.y }, distance = Math.hypot(delta.x, delta.y);
  if (distance < 1e-7) return [start, end];
  const a = from ?? directionTowards(start, end), b = opposite(to ?? directionTowards(end, start));
  if (Math.abs(cross(a, delta)) < 1e-7 && dot(a, delta) > 0 && dot(a,b) > 1-1e-7) return [start,end];
  const compass = Array.from({length:8}, (_,i) => ({x:Math.cos(i*Math.PI/4),y:Math.sin(i*Math.PI/4)}));
  const lead = Math.min(24, distance/4);
  for (let count=2; count<=4; count++) {
    let best: {points:Point[]; score:number} | undefined;
    const consider = (vectors: Point[]) => {
      if (vectors.slice(1).some((v,i)=>Math.abs(dot(v,vectors[i]!)) > 1-1e-7)) return;
      const minimum = vectors.map((_,i)=>i===0||i===vectors.length-1?lead:1e-5);
      const candidates: number[][] = [];
      // The least-norm solution balances the two straight leads in the usual S route.
      const xx=vectors.reduce((s,v)=>s+v.x*v.x,0), yy=vectors.reduce((s,v)=>s+v.y*v.y,0), xy=vectors.reduce((s,v)=>s+v.x*v.y,0), determinant=xx*yy-xy*xy;
      if (Math.abs(determinant)>1e-8) {
        const x=(yy*delta.x-xy*delta.y)/determinant, y=(xx*delta.y-xy*delta.x)/determinant;
        candidates.push(vectors.map(v=>v.x*x+v.y*y));
      }
      // Boundary solutions ensure feasibility even when a lead must stay at its minimum.
      for(let i=0;i<vectors.length;i++) for(let j=i+1;j<vectors.length;j++) {
        const u=vectors[i]!,v=vectors[j]!,det=cross(u,v); if(Math.abs(det)<1e-8)continue;
        const lengths=[...minimum],remaining={...delta};
        vectors.forEach((w,k)=>{if(k!==i&&k!==j){remaining.x-=w.x*lengths[k]!;remaining.y-=w.y*lengths[k]!;}});
        lengths[i]=cross(remaining,v)/det; lengths[j]=cross(u,remaining)/det; candidates.push(lengths);
      }
      for(const lengths of candidates) {
        if(lengths.some((n,i)=>n<minimum[i]!-1e-7))continue;
        const points=[start]; vectors.forEach((v,i)=>{const p=points.at(-1)!;points.push({x:p.x+v.x*lengths[i]!,y:p.y+v.y*lengths[i]!});}); points[points.length-1]=end;
        const diagonal=vectors.slice(1,-1).some(v=>Math.abs(Math.abs(v.x)-Math.abs(v.y))<1e-7);
        const length=lengths.reduce((s,n)=>s+n,0);
        const score=(count===3&&!diagonal?1e9:0)+length+Math.abs(lengths[0]!-lengths.at(-1)!)*1e-5;
        if(!best||score<best.score-1e-7)best={points,score};
      }
    };
    if(count===2)consider([a,b]);
    if(count===3)for(const v of compass)consider([a,v,b]);
    if(count===4)for(const u of compass)for(const v of compass)consider([a,u,v,b]);
    if(best)return best.points;
  }
  // Rotated drawings can have non-octilinear leads: keep both directions exact.
  return [start,{x:start.x+a.x*lead,y:start.y+a.y*lead},{x:end.x-b.x*lead,y:end.y-b.y*lead},end];
}
