// @vitest-environment node
import {expect,it} from "vitest";
import {threadBandStrokes} from "./thread-band-renderer";
const surface={path:[{x:0,y:0},{x:30,y:0}],polygon:[{x:0,y:-5},{x:30,y:-5},{x:30,y:5},{x:0,y:5}]};
it("lays close regular turns across the pipe rather than stripes along it",()=>{
 const strokes=threadBandStrokes(surface,1,0);
 expect(strokes).toHaveLength(10);
 expect(strokes.every(([a,b])=>a.x===b.x&&a.y<0&&b.y>0)).toBe(true);
 expect(strokes.map(([a])=>a.x)).toEqual(Array.from({length:10},(_,i)=>1.5+i*3));
 expect(threadBandStrokes(surface,2,0)).toHaveLength(5);
});
it("rotates winding direction with a vertical pipe and supports explicit texture angle",()=>{
 const rotated={path:surface.path.map(p=>({x:-p.y,y:p.x})),polygon:surface.polygon.map(p=>({x:-p.y,y:p.x}))};
 const strokes=threadBandStrokes(rotated,1,0);
 expect(strokes).toHaveLength(10);expect(strokes.every(([a,b])=>a.y===b.y&&a.x>0&&b.x<0)).toBe(true);
 expect(threadBandStrokes(surface,1,30).every(([a,b])=>a.x!==b.x&&a.y!==b.y)).toBe(true);
});
