import type { Point } from "./model";

/** Collinear authoring handles do not shorten a physical straight contact lead. */
export function straightLeadEnd(points:readonly Point[]):Point {
  const a=points[0]!,b=points[1]!;let end=b;
  const dx=b.x-a.x,dy=b.y-a.y;
  for(const p of points.slice(2)){
    if(Math.abs((p.x-a.x)*dy-(p.y-a.y)*dx)>1e-7||(p.x-end.x)*dx+(p.y-end.y)*dy<=0)break;
    end=p;
  }
  return end;
}
