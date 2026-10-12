// @vitest-environment node
import { expect, it } from "vitest";
import { physicalSegmentPoints } from "./physical-geometry";
import { standardCoveringOver, projectOntoPolyline } from "./physical-coverings";
import { drawingRouteSamples } from "./drawing-route-path";
import { physicalFixture } from "./physical-topology-fixture";
import { pipeBundleDisplaySamples, pipeBundleTransitionHandles, projectPipeBundleControls, projectPipeBundlePoint, unprojectPipeBundleEdit } from "./pipe-bundle-projection";
import { physicalWireDisplayPaths, physicalWirePoints } from "./physical-wire-geometry";
import { createEmptyHarnessDesign, type HarnessDesignDocument } from "./model";
import { applyEditorCommand, createConnector, createWire } from "./commands";
import { createEditorHistory,executeEditorCommand,undoEditorCommand } from "./history";
import { coveringScene, moveCovering } from "./covering-layout";
import { splitPhysicalSegment } from "./physical-topology";
import { pipeBundleSections } from "./pipe-bundle-section";
import { physicalTopologyScene } from "./physical-scene";
import { bendSnapAnchors, snapBendPoint, editPhysicalBend, physicalEditablePoints } from "./physical-editing";

function parallel(): HarnessDesignDocument {
  const connectors=["A","B","C","D"].map((id,i)=>createConnector(id,id,1,{x:i%2*600,y:Math.floor(i/2)*100}));
  return {...createEmptyHarnessDesign(),connectors,
    wires:[createWire("W1",{connectorId:"A",contactId:"A:contact:1"},{connectorId:"B",contactId:"B:contact:1"}),
      createWire("W2",{connectorId:"C",contactId:"C:contact:1"},{connectorId:"D",contactId:"D:contact:1"})].map(w=>({...w,lengthMm:400})),
    drawingDocuments:{tables:[],leaders:[],bomOrder:[],bendRadius:0},
    physicalTopology:{snap:false,
      nodes:[0,1,2].flatMap(i=>[{id:`a${i}`,position:{x:0,y:i*100}},{id:`b${i}`,position:{x:600,y:i*100}}]),
      segments:[0,1,2].map(i=>({id:`s${i}`,from:`a${i}`,to:`b${i}`,path:{kind:"polyline",points:[]},width:10})),
      routes:[{wireId:"W1",steps:[{segmentId:"s0",reverse:false}]},{wireId:"W2",steps:[{segmentId:"s1",reverse:false}]}],
      coverings:[{id:"group",name:"Оболочка",width:0,color:"#334455",lengthMm:240,lengthMode:"manual",
        spans:[{segmentId:"s0",from:.3,to:.7}],bundle:{mode:"flat",members:[{kind:"segment",id:"s0"},{kind:"segment",id:"s1"}]}}]}};
}

it("shares immutable pipe samples across wire lanes and invalidates on geometry, radius, width and undo",()=>{
 const base=parallel(),doc={...base,drawingDocuments:{...base.drawingDocuments!,bendRadius:18}};
 const before=pipeBundleDisplaySamples(doc,"s1")!;
 expect(pipeBundleDisplaySamples(doc,"s1")).toBe(before);
 const copy=JSON.parse(JSON.stringify(before));
 for(const wire of doc.wires)physicalWireDisplayPaths(doc,wire.id,{x:0,y:0},{x:600,y:0});
 expect(before).toEqual(copy);
 const moved=applyEditorCommand(doc,{type:"move-physical-node",nodeId:"b1",position:{x:700,y:130},mode:"carry"});
 const variants=[moved,
   {...doc,drawingDocuments:{...doc.drawingDocuments,bendRadius:35}},
   {...doc,physicalTopology:{...doc.physicalTopology!,coverings:doc.physicalTopology!.coverings!.map(c=>({...c,width:80}))}},
 ];
 for(const next of variants){
   const samples=pipeBundleDisplaySamples(next,"s1")!;
   expect(samples).not.toBe(before);
   expect(samples).toEqual(pipeBundleDisplaySamples(JSON.parse(JSON.stringify(next)),"s1"));
   expect(pipeBundleDisplaySamples(next,"s1")).toBe(samples);
 }
 expect(pipeBundleDisplaySamples(doc,"s1")).toBe(before);
 expect(pipeBundleDisplaySamples(moved,"s1")).not.toEqual(before);
 expect(before).toEqual(copy);
 expect(moved.wires).toBe(doc.wires);
});

it("keeps a coating over a bundle on the same axis when its convergence handle moves",()=>{
 const base=parallel(),inner=base.physicalTopology!.coverings![0]!;
 const overlay=standardCoveringOver(base,inner,"Оплётка","overlay");
 const doc={...base,drawingDocuments:{...base.drawingDocuments!,bendRadius:18},physicalTopology:{...base.physicalTopology!,coverings:[inner,overlay]}};
 const moved=moveCovering(doc,"group",0,"transition-from",{x:132,y:100},{x:102,y:100},0)!;
 const next={...doc,physicalTopology:{...doc.physicalTopology,coverings:[moved,overlay]}};
 const single={...next,physicalTopology:{...next.physicalTopology,coverings:[moved]}};
 expect(pipeBundleDisplaySamples(next,"s1")).toEqual(pipeBundleDisplaySamples(single,"s1"));
 const scene=coveringScene(next);
 expect(scene.find(c=>c.id==="overlay")!.paths).toEqual(scene.find(c=>c.id==="group")!.paths);
 expect(JSON.parse(scene.find(c=>c.id==="overlay")!.metadata!.coveringHandles!).filter((h:{part:string})=>h.part.startsWith("transition-"))).toHaveLength(0);
});

it("does not create a second convergence when a short coating is placed over a bundle",()=>{
 const base=parallel(),inner=base.physicalTopology!.coverings![0]!;
 const overlay=standardCoveringOver(base,inner,"Оплётка","overlay",{x:300,y:0});
 expect(overlay.spans[0]!.to-overlay.spans[0]!.from).toBeLessThan(inner.spans[0]!.to-inner.spans[0]!.from);
 const layered={...base,physicalTopology:{...base.physicalTopology!,coverings:[inner,overlay]}};
 expect(pipeBundleDisplaySamples(layered,"s1")).toEqual(pipeBundleDisplaySamples(base,"s1"));
 const coating=coveringScene(layered).find(item=>item.id==="overlay")!;
 expect(coating.paths?.[0]?.length).toBeGreaterThan(1);
 expect(coating.paths?.[0]?.every(point=>Number.isFinite(point.x)&&Number.isFinite(point.y))).toBe(true);
});

it("uses the configured minimum when a short coating is placed over a bundle",()=>{
 const base=parallel(),inner=base.physicalTopology!.coverings![0]!;
 const document={...base,drawingDocuments:{...base.drawingDocuments!,minimumCoveringOverlapPx:20}};
 const overlay=standardCoveringOver(document,inner,"Оплётка","overlay",{x:300,y:0});
 expect((overlay.spans[0]!.to-overlay.spans[0]!.from)*600).toBeCloseTo(20);
});

it("moves one shared exit control vertically for every member",()=>{
 const base=parallel(),cover=base.physicalTopology!.coverings![0]!;
 const handle=pipeBundleTransitionHandles(base,"group").find(item=>item.part==="transition-from")!;
 const moved=moveCovering(base,"group",handle.spanIndex,"transition-from",handle.point,
   {x:handle.point.x,y:handle.point.y+30})!;
 expect(moved.bundle?.transitionBendStart?.y).toBeCloseTo(30);
 const document={...base,physicalTopology:{...base.physicalTopology!,coverings:[moved]}};
 for(const id of ["s0","s1"]){
  const before=pipeBundleDisplaySamples(base,id)!,after=pipeBundleDisplaySamples(document,id)!;
  const middle=before.findIndex(point=>Math.abs(point.fraction-.26)<.005);
  expect(middle).toBeGreaterThan(0);
  expect(after[middle]!.point.y-before[middle]!.point.y).toBeGreaterThan(0);
 }
});

it("deduplicates transition handles and keeps a bent shared axis finite",()=>{
 const base=parallel(),topology=base.physicalTopology!;
 const segments=topology.segments.map(s=>s.id==="s0"
   ?{...s,path:{kind:"polyline" as const,points:[{x:160,y:0},{x:210,y:80},{x:390,y:80},{x:440,y:0}]}}
   :s.id==="s1"
     ?{...s,path:{kind:"polyline" as const,points:[{x:160,y:100},{x:210,y:180},{x:390,y:180},{x:440,y:100}]}}
     :s);
 const doc={...base,drawingDocuments:{...base.drawingDocuments!,bendRadius:18},physicalTopology:{...topology,segments}};
 const samples=pipeBundleDisplaySamples(doc,"s0")!,handles=pipeBundleTransitionHandles(doc,"group");
 expect(handles).toHaveLength(2);
 expect(samples.every(s=>Number.isFinite(s.point.x)&&Number.isFinite(s.point.y))).toBe(true);
 expect(samples[0]!.point).toEqual({x:0,y:0});
 expect(samples.at(-1)!.point).toEqual({x:600,y:0});
 const source=drawingRouteSamples(physicalSegmentPoints(doc,segments[0]!),18);
 const length=source.at(-1)!.distance;
 const point=(t:number)=>{const index=source.findIndex(p=>p.distance>=t*length),b=source[index]!,a=source[Math.max(0,index-1)]!,u=(t*length-a.distance)/(b.distance-a.distance||1);
   return projectPipeBundlePoint(doc,"s0",t,{x:a.point.x+(b.point.x-a.point.x)*u,y:a.point.y+(b.point.y-a.point.y)*u});};
 for(const t of [.22,.3,.7,.78]){
   const a=point(t-1e-7),b=point(t+1e-7);
   expect(Math.hypot(a.x-b.x,a.y-b.y)).toBeLessThan(.01);
 }
 const reversed={...doc,physicalTopology:{...doc.physicalTopology,coverings:doc.physicalTopology.coverings!.map(c=>({...c,spans:[...c.spans].reverse()}))}};
 expect(pipeBundleDisplaySamples(reversed,"s0")).toEqual(samples);
});

it("shares a moved transition when an overlay stores several supports in reverse order",()=>{
 const base=parallel(),inner={...base.physicalTopology!.coverings![0]!,spans:[{segmentId:"s0",from:.3,to:.7},{segmentId:"s1",from:.3,to:.7}]};
 const overlay=standardCoveringOver(base,inner,"Оплётка","overlay");
 const doc={...base,drawingDocuments:{...base.drawingDocuments!,bendRadius:18},physicalTopology:{...base.physicalTopology!,coverings:[inner,{...overlay,spans:[...overlay.spans].reverse()}]}};
 const moved=moveCovering(doc,"group",0,"transition-from",{x:132,y:100},{x:102,y:100},0)!;
 const next={...doc,physicalTopology:{...doc.physicalTopology,coverings:[moved,doc.physicalTopology.coverings[1]!]}};
 const single={...next,physicalTopology:{...next.physicalTopology,coverings:[moved]}};
 expect(pipeBundleDisplaySamples(next,"s1")).toEqual(pipeBundleDisplaySamples(single,"s1"));
 expect(pipeBundleTransitionHandles(next,"overlay")).toHaveLength(0);
 const reordered={...next,physicalTopology:{...next.physicalTopology,coverings:[...next.physicalTopology.coverings].reverse()}};
 expect(pipeBundleDisplaySamples(reordered,"s1")).toEqual(pipeBundleDisplaySamples(next,"s1"));
 expect(pipeBundleTransitionHandles(reordered,"group")).toEqual(pipeBundleTransitionHandles(next,"group"));
});

it("keeps one handle per action where bundle participants coincide",()=>{
 const base=parallel(),doc={...base,physicalTopology:{...base.physicalTopology!,nodes:base.physicalTopology!.nodes.map(n=>({...n,position:{...n.position,y:0}}))}};
 const handles=pipeBundleTransitionHandles(doc,"group");
 expect(handles).toHaveLength(2);
 expect(new Set(handles.map(h=>h.part)).size).toBe(2);
});

it.each([false,true])("joins an outer transition continuously to an already grouped pipe (reverse %s)",reverse=>{
 const base=parallel(),inner={...base.physicalTopology!.coverings![0]!,spans:[{segmentId:"s0",from:.1,to:.9}]};
 const outer={...inner,id:"outer",spans:[{segmentId:"s0",from:.4,to:.6}],bundle:{mode:"flat" as const,members:[{kind:"covering" as const,id:"group"},{kind:"segment" as const,id:"s2"}]}};
 const doc={...base,drawingDocuments:{...base.drawingDocuments!,bendRadius:18},physicalTopology:{...base.physicalTopology!,segments:base.physicalTopology!.segments.map(s=>reverse&&s.id==="s1"?{...s,from:s.to,to:s.from}:s),coverings:[inner,outer]}};
 for(const station of [.4-(.6-.4)/3,.4,.6,.6+(.6-.4)/3]){
   const point=(t:number)=>projectPipeBundlePoint(doc,"s1",reverse?1-t:t,{x:t*600,y:100});
   const before=point(station-1e-7),after=point(station+1e-7);
   expect(Math.hypot(before.x-after.x,before.y-after.y),`station ${station} before ${JSON.stringify(before)} after ${JSON.stringify(after)}`).toBeLessThan(.01);
 }
 expect(doc.wires).toBe(base.wires);
 expect(doc.physicalTopology.routes).toBe(base.physicalTopology!.routes);
});

it("keeps a coating of an inner bundle centred inside the outer sleeve",()=>{
 const base=parallel(),inner=base.physicalTopology!.coverings![0]!;
 const outer={...inner,id:"outer",bundle:{mode:"flat" as const,members:[{kind:"covering" as const,id:"group"},{kind:"segment" as const,id:"s2"}]}};
 const overlay=standardCoveringOver(base,inner,"Оплётка","overlay");
 const doc={...base,physicalTopology:{...base.physicalTopology!,coverings:[inner,overlay,outer]}};
 const scene=coveringScene(doc);
 expect(scene.find(c=>c.id==="overlay")!.paths).toEqual(scene.find(c=>c.id==="group")!.paths);
 expect(scene.at(-1)!.id).toBe("outer");
 const reordered={...doc,physicalTopology:{...doc.physicalTopology,coverings:[outer,inner,overlay]}};
 expect(coveringScene(reordered).at(-1)!.id).toBe("outer");
 expect(pipeBundleDisplaySamples(reordered,"s1")).toEqual(pipeBundleDisplaySamples(doc,"s1"));
});

it.each(['carry','adjacent'] as const)('snaps authored corners and inserted midpoints through bundle projection in %s mode',mode=>{
  const base=parallel(),topology=base.physicalTopology!;
  const doc={...base,physicalTopology:{...topology,snap:true,segments:topology.segments.map(s=>s.id==='s1'?{...s,path:{kind:'polyline' as const,points:[{x:200,y:100},{x:200,y:300},{x:400,y:300},{x:400,y:100}]}}:s)}};
  const segment=doc.physicalTopology.segments.find(s=>s.id==='s1')!;
  const original=physicalEditablePoints(doc,segment),scene=physicalTopologyScene(doc).find(o=>o.id===segment.id)!;
  for(const insert of [false,true]){
    const index=1,origin=insert?{x:(original[index]!.x+original[index+1]!.x)/2,y:(original[index]!.y+original[index+1]!.y)/2}:original[index+1]!;
    const shown=insert?scene.pipe!.midpoints![index]!:scene.pipe!.handles[index]!;
    expect(Math.hypot(shown.x-origin.x,shown.y-origin.y)).toBeGreaterThan(1);
    const anchors=bendSnapAnchors(original,index,insert,mode,shown);
    const target={x:shown.x+37,y:shown.y+43};
    const snapped=snapBendPoint(target,anchors,true,7).point;
    const authored=unprojectPipeBundleEdit(doc,segment.id,origin,snapped);
    const edited=editPhysicalBend(doc,segment.id,index,authored,mode,insert);
    const points=physicalEditablePoints(edited,edited.physicalTopology!.segments.find(s=>s.id===segment.id)!);
    points.slice(1).forEach((p,i)=>{
      const a=points[i]!,length=Math.hypot(p.x-a.x,p.y-a.y);
      if(length<1e-6)return;
      const angle=Math.atan2(p.y-a.y,p.x-a.x)/(Math.PI/12);
      expect(angle).toBeCloseTo(Math.round(angle),6);
    });
    const free=unprojectPipeBundleEdit(doc,segment.id,origin,snapBendPoint(target,anchors,false,7).point);
    expect(free.x-origin.x).toBeCloseTo(37);
    expect(free.y-origin.y).toBeCloseTo(43);
    expect(edited.physicalTopology!.coverings).toEqual(doc.physicalTopology.coverings);
    expect(edited.wires).toBe(doc.wires);
  }
});

it("converges straight parallel members onto one sleeve axis with transitions outside its edges",()=>{
  const doc=parallel(),before=JSON.stringify(doc);
  for(const [id,y] of [["s0",-5],["s1",5]] as const){
    const points=pipeBundleDisplaySamples(doc,id)!;
    expect(points.find(p=>p.fraction===.3)!.point).toEqual({x:180,y});
    expect(points.find(p=>p.fraction===.7)!.point).toEqual({x:420,y});
    expect(points[0]!.point).toEqual({x:0,y:id==="s0"?0:100});
    expect(points.at(-1)!.point).toEqual({x:600,y:id==="s0"?0:100});
  }
  const cover=coveringScene(doc)[0]!;
  expect(cover.paths).toHaveLength(1);
  expect(cover.paths![0]!.every(p=>p.y===0)).toBe(true);
  expect(cover.width).toBe(20.5);
  expect(JSON.stringify(doc)).toBe(before);
});

it("routes each wire inside its projected pipe while preserving measured and electrical data",()=>{
  const doc=parallel(),before=JSON.stringify(doc);
  const pipe=pipeBundleDisplaySamples(doc,"s1")!.map(p=>p.point);
  expect(physicalWireDisplayPaths(doc,"W2",{x:0,y:100},{x:600,y:100})![0]).toEqual(pipe);
  expect(physicalWirePoints(doc,"W2",{x:0,y:100},{x:600,y:100})).toEqual([{x:0,y:100},{x:600,y:100}]);
  expect(doc.wires.map(w=>w.lengthMm)).toEqual([400,400]);
  expect(JSON.stringify(doc)).toBe(before);
});

it("uses one common sleeve axis for independent supports without duplicating shells",()=>{
  const doc=parallel(),topology=doc.physicalTopology!;
  const covering={...topology.coverings![0]!,spans:[{segmentId:"s0",from:.3,to:.7},{segmentId:"s1",from:.3,to:.7}]};
  const independent={...doc,physicalTopology:{...topology,coverings:[covering]}};
  expect(projectPipeBundlePoint(independent,"s0",.5,{x:300,y:0})).toEqual({x:300,y:-5});
  expect(projectPipeBundlePoint(independent,"s1",.5,{x:300,y:100})).toEqual({x:300,y:5});
  const shell=coveringScene(independent)[0]!;
  expect(shell.paths).toHaveLength(1);
  expect(shell.paths![0]!.every(p=>p.y===0)).toBe(true);
  const reversed={...independent,physicalTopology:{...independent.physicalTopology,coverings:[{...covering,spans:[...covering.spans].reverse()}]}};
  expect(coveringScene(reversed)[0]!.paths).toEqual(shell.paths);
  expect(pipeBundleDisplaySamples(reversed,"s1")).toEqual(pipeBundleDisplaySamples(independent,"s1"));
});

it("follows a moved sleeve and recreates the same display after JSON without storing display vertices",()=>{
  const doc=parallel(),t=doc.physicalTopology!;
  const moved=moveCovering(doc,"group",0,"body",{x:200,y:0},{x:260,y:0})!;
  const next={...doc,physicalTopology:{...t,coverings:[moved]}};
  expect(moved.spans).toEqual(t.coverings![0]!.spans);
  expect(moved.bundle?.bodyOffset).toEqual({x:60,y:0});
  expect(projectPipeBundlePoint(next,"s1",.4,{x:240,y:100})).toEqual({x:300,y:5});
  expect(projectPipeBundlePoint(next,"s1",.2,{x:120,y:100})).toEqual({x:120,y:100});
  expect(pipeBundleDisplaySamples(JSON.parse(JSON.stringify(next)),"s1")).toEqual(pipeBundleDisplaySamples(next,"s1"));
  expect(next.physicalTopology.segments).toBe(t.segments);
  expect(next.physicalTopology.routes).toBe(t.routes);
  expect(moved.lengthMm).toBe(240);
});

it("uses the same lane for split continuations without repeating the full sleeve on each fragment",()=>{
  const base=parallel(),doc={...base,physicalTopology:{...base.physicalTopology!,segments:base.physicalTopology!.segments.map(s=>s.id==='s0'?{...s,path:{kind:'polyline' as const,points:[{x:300,y:0}]}}:s)}};
  const split={...doc,physicalTopology:splitPhysicalSegment(doc,"s0",1,"cut","tail")};
  expect(pipeBundleDisplaySamples(split,"s0")!.at(-1)!.point).toEqual({x:300,y:-5});
  expect(pipeBundleDisplaySamples(split,"tail")![0]!.point).toEqual({x:300,y:-5});
  expect(pipeBundleSections(split).get("group")!.width).toBe(pipeBundleSections(doc).get("group")!.width);
  expect(projectPipeBundlePoint(split,"s1",.5,{x:300,y:100})).toEqual({x:300,y:5});
  const node=physicalTopologyScene(split).find(s=>s.id==='cut')!;
  expect({x:node.x+5,y:node.y+5}).toEqual({x:300,y:-5});
  const branched={...split,physicalTopology:{...split.physicalTopology,nodes:[...split.physicalTopology.nodes,{id:'tip',position:{x:300,y:-200}}],
    segments:[...split.physicalTopology.segments,{id:'branch',from:'cut',to:'tip',path:{kind:'polyline' as const,points:[]}}]}};
  expect(pipeBundleDisplaySamples(branched,'branch')![0]!.point).toEqual({x:300,y:-5});
  expect(pipeBundleDisplaySamples(branched,'branch')!.at(-1)!.point).toEqual({x:300,y:-200});
});

it("projects nested shells by their own centre, independent of covering array order",()=>{
  const base=parallel(),inner=base.physicalTopology!.coverings![0]!;
  const outer={...inner,id:"outer",bundle:{mode:"flat" as const,members:[{kind:"covering" as const,id:"group"},{kind:"segment" as const,id:"s2"}]}};
  const doc={...base,physicalTopology:{...base.physicalTopology!,coverings:[outer,inner]}};
  const other={...doc,physicalTopology:{...doc.physicalTopology,coverings:[inner,outer]}};
  expect(pipeBundleDisplaySamples(doc,"s1")).toEqual(pipeBundleDisplaySamples(other,"s1"));
  const scene=coveringScene(doc),nested=scene.find(c=>c.id==='group')!;
  expect(nested.paths![0]!.every(p=>p.y===-5)).toBe(true);
  expect(scene.find(c=>c.id==='outer')!.paths![0]!.every(p=>p.y===0)).toBe(true);
  expect(scene.map(c=>c.id)).toEqual(['group','outer']);
});

it("moves a partially overlapping outer sleeve without moving the inner bounds or authored routes",()=>{
  const base=parallel(),t=base.physicalTopology!,inner={...t.coverings![0]!,spans:[{segmentId:'s0',from:.2,to:.6}]};
  const outer={...inner,id:'outer',spans:[{segmentId:'s0',from:.4,to:.8}],
    bundle:{mode:'flat' as const,members:[{kind:'covering' as const,id:'group'},{kind:'segment' as const,id:'s2'}]}};
  const doc={...base,physicalTopology:{...t,coverings:[outer,inner]}},before=JSON.stringify(doc);
  const moved=moveCovering(doc,'outer',0,'body',{x:300,y:0},{x:360,y:0},0)!;
  const next={...doc,physicalTopology:{...doc.physicalTopology,coverings:[moved,inner]}};
  // The inner sleeve is packed inside the outer where they overlap, and
  // returns to its own centre outside the outer's transition zone.
  const shell=coveringScene(next).find(c=>c.id==='group')!.paths![0]!;
  expect(shell[0]).toEqual({x:120,y:0});
  expect(shell.at(-1)).toEqual({x:420,y:-5});
  expect(projectPipeBundlePoint(next,'s1',.3,{x:180,y:100})).toEqual({x:180,y:5});
  expect(projectPipeBundlePoint(next,'s1',.55,{x:330,y:100})).toEqual({x:390,y:0});
  const reordered={...next,physicalTopology:{...next.physicalTopology,coverings:[inner,moved]}};
  expect(pipeBundleDisplaySamples(reordered,'s1')).toEqual(pipeBundleDisplaySamples(next,'s1'));
  expect(next.physicalTopology.coverings[1]).toBe(inner);
  expect(next.physicalTopology.segments).toBe(t.segments);
  expect(JSON.stringify(doc)).toBe(before);
});

it("maps a reversed pipe onto the shared axis without reversing its authored endpoints",()=>{
  const doc=parallel();
  const reversed={...doc,physicalTopology:{...doc.physicalTopology!,segments:doc.physicalTopology!.segments.map(s=>s.id==='s1'?{...s,from:s.to,to:s.from}:s)}};
  expect(projectPipeBundlePoint(reversed,'s1',.3,{x:420,y:100})).toEqual({x:420,y:5});
  expect(pipeBundleDisplaySamples(reversed,'s1')![0]!.point).toEqual({x:600,y:100});
});

it("keeps generated stations out of bend ordinals and translates pointer deltas without a jump",()=>{
  const doc=parallel(),pipe=physicalTopologyScene(doc).find(s=>s.id==='s1')!;
  expect(pipe.pipe!.handles).toHaveLength(0);
  expect(pipe.pipe!.midpoints).toEqual([{x:300,y:5}]);
  expect(unprojectPipeBundleEdit(doc,'s1',{x:300,y:100},{x:300,y:5})).toEqual({x:300,y:100});
  expect(unprojectPipeBundleEdit(doc,'s1',{x:300,y:100},{x:305,y:15})).toEqual({x:305,y:110});
});

it("projects bundled pipe presentation inside its sleeve and rejoins the authored route", () => {
  const base = physicalFixture();
  const document = {
    ...base,
    physicalTopology: {
      ...base.physicalTopology!,
      segments: base.physicalTopology!.segments.map(s => ({ ...s, width: 10 })),
      coverings: [{
        id: "bundle", name: "Общая оболочка", width: 0, color: "#334455", lengthMm: null,
        spans: [{ segmentId: "S0", from: .2, to: .8 }],
        bundle: { mode: "flat" as const, members: [{ kind: "segment" as const, id: "S0" }, { kind: "segment" as const, id: "S1" }] },
      }],
    },
  };
  const source = physicalSegmentPoints(document, document.physicalTopology.segments[0]!);
  const projected = pipeBundleDisplaySamples(document, "S0")!.map(s => s.point);
  expect(projected[0]).toEqual(source[0]);
  expect(projected.at(-1)).toEqual(source.at(-1));
  expect(projected.some(point => !source.some(base => Math.hypot(point.x - base.x, point.y - base.y) < 0.01))).toBe(true);
  const controls=projectPipeBundleControls(document, "S0", source);
  expect(controls).toHaveLength(source.length);
  expect(controls[0]).toEqual(source[0]);
  expect(controls.at(-1)).toEqual(source.at(-1));
  const wirePath = physicalWireDisplayPaths(document, "W1", { x: 0, y: 0 }, { x: 650, y: 500 })!.flat();
  expect(wirePath.some(point => !source.some(base => Math.hypot(point.x - base.x, point.y - base.y) < 0.01))).toBe(true);
});

it("keeps bend handles on the painted rounded route",()=>{
  const base=physicalFixture();
  const document={...base,drawingDocuments:{...base.drawingDocuments!,bendRadius:18},physicalTopology:{...base.physicalTopology!,coverings:[{
    id:"bundle",name:"Общая оболочка",width:0,color:"#334455",lengthMm:null,spans:[{segmentId:"S0",from:0,to:1}],
    bundle:{mode:"flat" as const,members:[{kind:"segment" as const,id:"S0"},{kind:"segment" as const,id:"S1"}]},
  }],segments:base.physicalTopology!.segments.map(item=>item.id==="S0"?{...item,path:{kind:"routed" as const,points:[{x:250,y:130} ]}}:item)}};
  const segment=document.physicalTopology!.segments.find(item=>item.id==="S0")!,scene=physicalTopologyScene(document).find(item=>item.id===segment.id)!;
  const source=physicalSegmentPoints(document,segment),legacy=source.slice(1,-1).map(point=>projectPipeBundlePoint(document,segment.id,projectOntoPolyline(source,point).fraction,point));
  expect(legacy.some(point=>projectOntoPolyline(scene.points!,point).distance>1e-6)).toBe(true);
  for(const [index,authored] of (scene.pipe!.authoredHandleIndices??[]).entries())if(authored>=1){
    expect(projectOntoPolyline(scene.points!,scene.pipe!.handles[index]!).distance).toBeLessThan(1e-6);
  }
});

it("leaves an unbundled pipe and authored geometry unchanged", () => {
  const document = physicalFixture();
  const source = physicalSegmentPoints(document, document.physicalTopology!.segments[0]!);
  expect(projectPipeBundleControls(document, "S0", source)).toEqual(source);
  expect(JSON.stringify(document)).toBe(JSON.stringify(physicalFixture()));
});

it("rounds member convergence outside sleeve edges and exposes movable transition controls",()=>{
 const base=parallel(),doc={...base,drawingDocuments:{...base.drawingDocuments!,bendRadius:18}};
 for(const id of ["s0","s1"]){const points=pipeBundleDisplaySamples(doc,id)!;expect(points.every(p=>Number.isFinite(p.point.x)&&Number.isFinite(p.point.y))).toBe(true);
 const inside=points.filter(p=>p.fraction>=.3&&p.fraction<=.7);expect(inside.every(p=>Math.abs(p.point.y-(id==="s0"?-5:5))<1e-6)).toBe(true);
 const edge=points.findIndex(p=>Math.abs(p.fraction-.3)<1e-8);const a=points[edge-1]!.point,b=points[edge]!.point;
 expect(Math.abs((b.y-a.y)/(b.x-a.x))).toBeLessThan(.06);
 }
 const changed=moveCovering(doc,"group",0,"transition-from",{x:132,y:100},{x:102,y:100},0)!;
 expect(changed.bundle!.transitionStart).toBeCloseTo(.13);expect(changed.spans).toEqual(doc.physicalTopology!.coverings![0]!.spans);
 const next={...doc,physicalTopology:{...doc.physicalTopology!,coverings:[changed]}};
 const start=pipeBundleDisplaySamples(next,"s1")!.find(p=>Math.abs(p.fraction-.17)<1e-7)!.point;expect(start.x).toBeCloseTo(102);expect(start.y).toBe(100);
 expect(next.wires).toBe(doc.wires);
 const history=executeEditorCommand(createEditorHistory(doc),{type:"set-physical-topology",topology:next.physicalTopology});
 expect(history.present.physicalTopology!.coverings![0]!.bundle!.transitionStart).toBeCloseTo(.13);
 expect(undoEditorCommand(history).present).toBe(doc);
 expect(()=>applyEditorCommand(doc,{type:"set-physical-topology",topology:{...doc.physicalTopology!,coverings:[{...changed,bundle:{...changed.bundle!,transitionStart:-1}}]}})).toThrow();
});
