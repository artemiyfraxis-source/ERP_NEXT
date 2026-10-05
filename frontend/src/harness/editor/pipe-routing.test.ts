// @vitest-environment node
import { describe, expect, it } from "vitest";
import { computePipeRoute } from "./pipe-routing";
import { physicalSegmentRouteInput, physicalSegmentPoints } from "./physical-geometry";
import { physicalFixture } from "./physical-topology-fixture";
import { physicalNodePoint, physicalNodeDirection, physicalNodeContactDirection } from "./physical-ports";
import { drawingRouteCommands } from "./drawing-route-path";
import { parseHarnessDesignDocument } from "./model";

describe("pipe routing boundary", () => {
  it.each([15,37,90,180,270])("rotates every exit vector with the drawing by %s degrees and keeps tangent leads", rotationDegrees=>{
    const base=physicalFixture(),node={...base.physicalTopology!.nodes[0]!,direction:"right" as const,
      contactDirections:{[base.connectors[0]!.contacts[0]!.id]:"up" as const}};
    const doc={...base,connectors:base.connectors.map(c=>c.id===node.connectorId?{...c,drawingPlacements:[{drawingId:"view:drawing",offset:{x:0,y:0},visible:true,scale:2,rotationDegrees}]}:c)};
    const a=rotationDegrees*Math.PI/180,from=physicalNodeDirection(doc,node)!;
    expect(from.x).toBeCloseTo(Math.cos(a));expect(from.y).toBeCloseTo(Math.sin(a));
    const contact=physicalNodeContactDirection(doc,node,base.connectors[0]!.contacts[0]!.id)!;
    expect(contact.x).toBeCloseTo(Math.sin(a));expect(contact.y).toBeCloseTo(-Math.cos(a));
    const start=physicalNodePoint(doc,node),points=computePipeRoute({kind:"automatic",start,end:{x:700,y:350},from,to:{x:-1,y:0}});
    const commands=drawingRouteCommands(points),lead=commands[1]!;
    expect((lead.x-start.x)*from.y-(lead.y-start.y)*from.x).toBeCloseTo(0);
    expect((lead.x-start.x)*from.x+(lead.y-start.y)*from.y).toBeGreaterThan(0);
    expect(commands.some(c=>c.kind==="arc")).toBe(true);
  });
  it("does not route, round or mutate authored points, even with coincident corners", () => {
    const points = Object.freeze([{x:0,y:0},{x:13,y:7},{x:13,y:7},{x:40,y:21}].map(p=>Object.freeze(p)));
    const result = computePipeRoute({kind:"authored",points});
    expect(result).toEqual(points);expect(result).not.toBe(points);
  });
  it("preserves the persisted policy for old, manual, unsnapped and fixed straight fragments", () => {
    const doc=physicalFixture(),segment={...doc.physicalTopology!.segments[0]!,path: { kind: "routed" as const, points: [] }};
    expect(physicalSegmentRouteInput(doc,segment).kind).toBe("automatic");
    for(const s of [{...segment,path:{kind:"polyline" as const,points:[]}},{...segment,path: { kind: "routed" as const, points: [{x:13,y:27}] }}]) {
      expect(physicalSegmentRouteInput(doc,s).kind).toBe("authored");
      const persisted={...doc,physicalTopology:{...doc.physicalTopology!,segments:doc.physicalTopology!.segments.map(p=>p.id===s.id?s:p)}};
      const reopened=parseHarnessDesignDocument(JSON.parse(JSON.stringify(persisted)));
      expect(physicalSegmentPoints(reopened,reopened.physicalTopology!.segments[0]!)).toEqual(physicalSegmentPoints(doc,s));
    }
    expect(physicalSegmentRouteInput({...doc,physicalTopology:{...doc.physicalTopology!,snap:false}},segment).kind).toBe("authored");
  });
  it("computes display vertices without writing corners or directions into persisted topology", () => {
    const doc=physicalFixture(),s={...doc.physicalTopology!.segments[1]!,path: { kind: "routed" as const, points: [] }},before=JSON.stringify(doc);
    const input=physicalSegmentRouteInput(doc,s),route=computePipeRoute(input);
    expect(input.kind).toBe("automatic");expect(route.length).toBeGreaterThan(2);
    expect(s.path.points).toEqual([]);expect(JSON.stringify(doc)).toBe(before);
  });
  it("keeps port direction independent of connector translation", () => {
    const base=physicalFixture(),node={...base.physicalTopology!.nodes[0]!,direction:"up" as const};
    const moved={...base,connectors:base.connectors.map(c=>({...c,positions:{...c.positions,drawing:{x:c.positions.drawing.x+123,y:c.positions.drawing.y-91}}}))};
    expect(physicalNodeDirection(moved,node)).toEqual(physicalNodeDirection(base,node));
    const a=physicalNodePoint(base,node),b=physicalNodePoint(moved,node);
    expect(b).toEqual({x:a.x+123,y:a.y-91});
  });
  it.each([-135,0,37,90,180,271])("routes a new pipe away from a left-side connector exit at %s degrees, from either segment end", rotationDegrees=>{
    const base=physicalFixture(),connector=base.connectors[0]!,node={...base.physicalTopology!.nodes[0]!,position:{x:0,y:36}};
    const rotated={...base,connectors:base.connectors.map(c=>c.id===connector.id?{...c,drawingPlacements:[{drawingId:"view:drawing",offset:{x:0,y:0},visible:true,scale:1.7,rotationDegrees}]}:c)};
    const start=physicalNodePoint(rotated,node),angle=rotationDegrees*Math.PI/180;
    const outward={x:-Math.cos(angle),y:-Math.sin(angle)};
    const far={id:"far",position:{x:start.x+outward.x*240-outward.y*60,y:start.y+outward.y*240+outward.x*60}};
    const doc={...rotated,physicalTopology:{...rotated.physicalTopology!,snap:true,nodes:[node,far],segments:[],routes:[]}};
    const direction=physicalNodeDirection(doc,node)!;
    expect(direction.x).toBeCloseTo(outward.x,6);expect(direction.y).toBeCloseTo(outward.y,6);
    const forward=physicalSegmentPoints(doc,{id:"new",from:node.id,to:far.id,path:{kind:"routed",points:[]}});
    const reverse=physicalSegmentPoints(doc,{id:"new",from:far.id,to:node.id,path:{kind:"routed",points:[]}});
    for(const lead of [{x:forward[1]!.x-start.x,y:forward[1]!.y-start.y},{x:reverse.at(-2)!.x-start.x,y:reverse.at(-2)!.y-start.y}]){
      expect(lead.x*outward.y-lead.y*outward.x).toBeCloseTo(0,6);
      expect(lead.x*outward.x+lead.y*outward.y).toBeGreaterThan(0);
    }
  });
  it("normalizes an inward manual pipe direction away from the connector",()=>{
    const base=physicalFixture(),node={...base.physicalTopology!.nodes[0]!,position:{x:0,y:36},direction:"right" as const};
    expect(physicalNodeDirection(base,node)!.x).toBeCloseTo(-1);
    expect(physicalNodeDirection(base,node)!.y).toBeCloseTo(0);
  });
});

