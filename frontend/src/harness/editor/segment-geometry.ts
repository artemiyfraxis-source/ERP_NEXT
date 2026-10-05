import type { Point } from "./model";

export interface LineSegment {readonly start:Point;readonly end:Point}
const epsilon=1e-8;
const cross=(a:Point,b:Point)=>a.x*b.y-a.y*b.x;
const sub=(a:Point,b:Point):Point=>({x:a.x-b.x,y:a.y-b.y});
const dot=(a:Point,b:Point)=>a.x*b.x+a.y*b.y;

export function segmentPointDistance(p:Point,s:LineSegment):number {
  const v=sub(s.end,s.start),length=dot(v,v),t=length?Math.max(0,Math.min(1,dot(sub(p,s.start),v)/length)):0;
  return Math.hypot(p.x-s.start.x-v.x*t,p.y-s.start.y-v.y*t);
}
export function segmentsParallel(a:LineSegment,b:LineSegment):boolean {
  const u=sub(a.end,a.start),v=sub(b.end,b.start);
  return Math.abs(cross(u,v))<=epsilon*Math.max(1,Math.hypot(u.x,u.y)*Math.hypot(v.x,v.y));
}
export function intersectSegments(a:LineSegment,b:LineSegment):{kind:"none"}|{kind:"overlap"}|{kind:"point";point:Point} {
  const u=sub(a.end,a.start),v=sub(b.end,b.start),w=sub(b.start,a.start),den=cross(u,v),length=dot(u,u);
  if(length<epsilon*epsilon)return segmentPointDistance(a.start,b)<=epsilon?{kind:"point",point:a.start}:{kind:"none"};
  if(segmentsParallel(a,b)){
    if(Math.abs(cross(w,u))>epsilon*Math.max(1,Math.hypot(u.x,u.y)))return {kind:"none"};
    const p=dot(w,u)/length,q=dot(sub(b.end,a.start),u)/length;
    const lo=Math.max(0,Math.min(p,q)),hi=Math.min(1,Math.max(p,q));
    if(hi<lo-epsilon)return {kind:"none"};
    return hi-lo>epsilon?{kind:"overlap"}:{kind:"point",point:{x:a.start.x+u.x*lo,y:a.start.y+u.y*lo}};
  }
  const t=cross(w,v)/den,r=cross(w,u)/den;
  return t>=-epsilon&&t<=1+epsilon&&r>=-epsilon&&r<=1+epsilon
    ?{kind:"point",point:{x:a.start.x+t*u.x,y:a.start.y+t*u.y}}:{kind:"none"};
}
export function parallelSegmentGap(a:LineSegment,b:LineSegment):number|null {
  if(!segmentsParallel(a,b))return null;
  const v=sub(a.end,a.start),length=Math.hypot(v.x,v.y);if(!length)return null;
  const p=dot(sub(b.start,a.start),v)/length,q=dot(sub(b.end,a.start),v)/length;
  if(Math.min(length,Math.max(p,q))-Math.max(0,Math.min(p,q))<=epsilon)return null;
  return Math.abs(cross(v,sub(b.start,a.start)))/length;
}
export function segmentEntersRect(s:LineSegment,r:{left:number;right:number;top:number;bottom:number}):boolean {
  let lo=0,hi=1;
  for(const [a,b,min,max] of [[s.start.x,s.end.x,r.left+epsilon,r.right-epsilon],[s.start.y,s.end.y,r.top+epsilon,r.bottom-epsilon]]){
    if(min!>=max!)return false;
    const d=b!-a!;
    if(Math.abs(d)<epsilon){if(a!<=min!||a!>=max!)return false;continue;}
    const x=(min!-a!)/d,y=(max!-a!)/d;
    lo=Math.max(lo,Math.min(x,y));hi=Math.min(hi,Math.max(x,y));
    if(hi<=lo)return false;
  }
  return hi>lo;
}
