// @vitest-environment node
import { describe,it,expect } from "vitest";
import { drawingRouteCommands,drawingRouteHitPoints,drawingRouteSamples,drawingRouteSection } from "./drawing-route-path";
describe("drawing circular bends",()=>{
 it("supports zero without losing the path and clamps a large radius to both shoulders",()=>{
  const points=[{x:0,y:0},{x:100,y:0},{x:100,y:100}];
  expect(drawingRouteCommands(points,0)).toEqual([{kind:"move",x:0,y:0},{kind:"line",x:100,y:0},{kind:"line",x:100,y:100}]);
  expect(drawingRouteHitPoints(points,0)).toEqual(points);
  expect(drawingRouteCommands(points,200).find(c=>c.kind==="arc")!.radius).toBeCloseTo(50);
  expect(drawingRouteCommands(points,12).find(c=>c.kind==="arc")).toMatchObject({radius:12});
 });
 it.each([0,12,40,200])("retains authored distance and trims a covering inside the bend at radius %s",radius=>{
  const points=[{x:0,y:0},{x:100,y:0},{x:100,y:100}];
  const samples=drawingRouteSamples(points,radius),section=drawingRouteSection(points,radius,80,120,[100]);
  expect(samples.at(-1)!.distance).toBeCloseTo(200);
  expect(section[0]!.distance).toBe(80);expect(section.at(-1)!.distance).toBe(120);
  expect(section.some(s=>s.distance===100)).toBe(true);
  const reverse=drawingRouteSection([...points].reverse(),radius,80,120,[100]).reverse();
  expect(reverse.length).toBe(section.length);
  section.forEach((s,i)=>{expect(s.point.x).toBeCloseTo(reverse[i]!.point.x);expect(s.point.y).toBeCloseTo(reverse[i]!.point.y);});
  if(radius>0)expect(section.some(s=>Math.hypot(s.point.x-100,s.point.y)<1)).toBe(false);
 });
 it("keeps straight tangents, endpoints and the original movable corner",()=>{
  const points=[{x:0,y:0},{x:100,y:0},{x:100,y:100}],before=JSON.stringify(points);
  const commands=drawingRouteCommands(points);
  expect(commands).toEqual([{kind:"move",x:0,y:0},{kind:"line",x:76,y:0},{kind:"arc",cornerX:100,cornerY:0,x:100,y:23.999999999999996,radius:24,sweep:1},{kind:"line",x:100,y:100}]);
  expect(JSON.stringify(points)).toBe(before);
  const hit=drawingRouteHitPoints(points);expect(hit[0]).toEqual(points[0]);expect(hit.at(-1)).toEqual(points[2]);
  expect(hit.some(p=>Math.hypot(p.x-100,p.y)<5)).toBe(false);
 });
 it("handles reversed paths, short legs and repeated points without overshoot",()=>{
  for(const points of [[{x:0,y:0},{x:5,y:0},{x:5,y:5}],[{x:5,y:5},{x:5,y:0},{x:0,y:0}],[{x:0,y:0},{x:0,y:0},{x:5,y:0}]]){
   const hit=drawingRouteHitPoints(points);expect(hit[0]).toEqual(points[0]);expect(hit.at(-1)).toEqual(points.at(-1));
   expect(hit.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)&&p.x>=-1e-6&&p.x<=5.000001&&p.y>=-1e-6&&p.y<=5.000001)).toBe(true);
  }
 });
});
