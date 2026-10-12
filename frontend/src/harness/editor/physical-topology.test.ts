// @vitest-environment node
import { physicalFixture } from "./physical-topology-fixture";
import { describe, expect, it } from "vitest";
import { applyEditorCommand } from "./commands";
import { connectorContactPosition, createEmptyHarnessDesign, createOrthogonalE4Route, wireEndpointE4Anchor, parseHarnessDesignDocument } from "./model";
import { automaticPipeRoute, constrainedPolyline, physicalNodePoint, physicalNodeDirection, physicalNodeFacingDirection, physicalNodeContactDirection, physicalWireDisplayPaths, physicalSegmentPoints, physicalWirePoints, splitPhysicalSegment, removePhysicalSegment, connectPhysicalNodeToSegment, branchPhysicalSegment, type PhysicalTopology } from "./physical-topology";
import { createJoiningPipe } from "./physical-joining-pipes";
import { physicalTopologyScene } from "./physical-scene";
import { resolvePhysicalRoutePointCommand } from "./physical-route-point-command";
import { buildHarnessSelectionIndex, resolveHarnessSelection } from "./harness-selection";
import { createEditorHistory, executeEditorCommand, undoEditorCommand } from "./history";



describe("physical topology", () => {
  it("prefers a 45 degree middle leg while keeping directed straight exits", () => {
    const points = automaticPipeRoute({ x: 0, y: 0 }, { x: 200, y: 100 }, { x: 1, y: 0 }, { x: -1, y: 0 });
    expect(points[1]!.y).toBeCloseTo(0);
    expect(points.at(-2)!.y).toBeCloseTo(100);
    expect(points).toHaveLength(4);
    expect(points.some((point, index) => index > 0 && Math.abs(Math.abs(point.x - points[index - 1]!.x) - Math.abs(point.y - points[index - 1]!.y)) < 1e-6)).toBe(true);
  });
  it("uses a single straight leg for aligned exits and keeps every chosen outward direction",()=>{
    expect(automaticPipeRoute({x:0,y:0},{x:200,y:0},{x:1,y:0},{x:-1,y:0})).toEqual([{x:0,y:0},{x:200,y:0}]);
    const directions=[{x:1,y:0},{x:-1,y:0},{x:0,y:1},{x:0,y:-1}];
    for(const a of directions)for(const b of directions)for(const end of [{x:200,y:100},{x:-120,y:75},{x:0,y:200},{x:3,y:-2}]){
      const route=automaticPipeRoute({x:0,y:0},end,a,b),first=route[1]!,last=route.at(-2)!;
      expect(first.x*a.y-first.y*a.x).toBeCloseTo(0,6);
      expect(first.x*a.x+first.y*a.y).toBeGreaterThan(0);
      expect((last.x-end.x)*b.y-(last.y-end.y)*b.x).toBeCloseTo(0,6);
      expect((last.x-end.x)*b.x+(last.y-end.y)*b.y).toBeGreaterThan(0);
      expect(route.length).toBeLessThanOrEqual(5);
      expect(route.flatMap(p=>[p.x,p.y]).every(Number.isFinite)).toBe(true);
    }
  });
  it("applies contact directions at both wire ends, including reverse routes and shrink-covered exits",()=>{
    const base=physicalFixture();
    const topology:PhysicalTopology={...base.physicalTopology!,nodes:base.physicalTopology!.nodes.map(n=>n.id==="NA"?{...n,direction:"right",contactDirections:{"A:contact:1":"up"}}:n.id==="NB"?{...n,direction:"left",contactDirections:{"B:contact:1":"down"}}:n)};
    const check=(t:PhysicalTopology)=>{
      const d={...base,physicalTopology:t},start={x:118,y:28},end={x:768,y:528};
      const rendered=physicalWireDisplayPaths(d,"W1",start,end)!,paths=[rendered[0]!,rendered.at(-1)!,physicalWirePoints(d,"W1",start,end)!];
      for(const p of [paths[0]!,paths[2]!]){expect(p[0]).toEqual(start);expect(p[1]!.x).toBeCloseTo(start.x);expect(p[1]!.y).toBeLessThan(start.y);}
      for(const p of [paths[1]!,paths[2]!]){expect(p.at(-1)).toEqual(end);expect(p.at(-2)!.x).toBeCloseTo(end.x);expect(p.at(-2)!.y).toBeGreaterThan(end.y);}
    };
    check(topology);
    const reversed:PhysicalTopology={...topology,segments:topology.segments.map(s=>({...s,from:s.to,to:s.from,path: { kind: "routed" as const, points: [...s.path.points].reverse() }})),routes:topology.routes.map(r=>({...r,steps:r.steps.map(s=>({...s,reverse:!s.reverse}))}))};
    check(reversed);
    check({...topology,coverings:[{id:"shrink",name:"Термоусадка",kind:"heat-shrink",width:20,color:"#123456",lengthMm:null,spans:[{segmentId:"S0",from:-.01,to:.2},{segmentId:"S1",from:.8,to:1.01}]}]});
  });
  it("persists directions, rotates vectors with the connector and rejects invalid direction records",()=>{
    const base=physicalFixture(),node={...base.physicalTopology!.nodes[0]!,direction:"right" as const,contactDirections:{"A:contact:1":"up" as const}};
    const topology={...base.physicalTopology!,nodes:[node,...base.physicalTopology!.nodes.slice(1)]};
    const h=executeEditorCommand(createEditorHistory(base),{type:"set-physical-topology",topology});
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(h.present))).physicalTopology).toEqual(topology);
    expect(undoEditorCommand(h).present).toEqual(base);
    const d={...h.present,connectors:base.connectors.map(c=>c.id==="A"?{...c,drawingPlacements:[{drawingId:"view:drawing",offset:{x:0,y:0},visible:true,scale:2,rotationDegrees:90}]}:c)};
    expect(physicalNodeDirection(d,node)!.x).toBeCloseTo(0);expect(physicalNodeDirection(d,node)!.y).toBeCloseTo(1);
    expect(physicalNodeContactDirection(d,node,"A:contact:1")!.x).toBeCloseTo(1);expect(physicalNodeContactDirection(d,node,"A:contact:1")!.y).toBeCloseTo(0);
    const points=physicalSegmentPoints(d,{...topology.segments[0]!,path: { kind: "routed" as const, points: [] }}),start=physicalNodePoint(d,node);
    expect(points[1]!.x).toBeCloseTo(start.x);expect(points[1]!.y).toBeGreaterThan(start.y);
    for(const patch of [{direction:"diagonal"},{contactDirections:null},{contactDirections:[]},{contactDirections:{missing:"up"}}])
      expect(()=>parseHarnessDesignDocument({...base,physicalTopology:{...topology,nodes:[{...node,...patch},...topology.nodes.slice(1)]}})).toThrow();
  });
  it("resolves explicit branch membership independently of electrical connectivity", () => {
    const d = physicalFixture(), index = buildHarnessSelectionIndex(d);
    expect(resolveHarnessSelection(index, ["S1"]).wireIds.sort()).toEqual(["W1", "W3"]);
    expect(resolveHarnessSelection(index, ["A"]).wireIds.sort()).toEqual(["W1", "W2"]);
    expect(resolveHarnessSelection(index, ["W1"], true).wireIds).toEqual(["W1"]);
    const crossed = { ...d, physicalTopology: { ...d.physicalTopology!, segments: [...d.physicalTopology!.segments, { id: "cross", from: "NA", to: "NC", path: { kind: "routed" as const, points: [] }}] } };
    expect(resolveHarnessSelection(buildHarnessSelectionIndex(crossed), ["cross"]).wireIds).toEqual([]);
  });
  it("splits at existing bends, preserves reverse routes, serializes and undoes", () => {
    const d = physicalFixture();
    const topology = splitPhysicalSegment(d, "S1", 1, "split", "new");
    const h = executeEditorCommand(createEditorHistory(d), { type: "set-physical-topology", topology });
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(h.present))).physicalTopology).toEqual(topology);
    expect(topology.routes.find(r => r.wireId === "W3")!.steps.slice(0, 2)).toEqual([{ segmentId: "new", reverse: true }, { segmentId: "S1", reverse: true }]);
    expect(undoEditorCommand(h).present).toBe(d);
    expect(h.present.wires).toBe(d.wires);
  });
  it("keeps every segment on the 15 degree grid after arbitrary anchor moves", () => {
    for (let i = 1; i < 60; i++) {
      const points = constrainedPolyline([{ x: -10, y: 70 }, { x: i * 13.7 - 400, y: i * 2.31 - 20 }, { x: 220, y: -111 }], true);
      for (let j = 1; j < points.length; j++) {
        const angle = Math.atan2(points[j]!.y - points[j - 1]!.y, points[j]!.x - points[j - 1]!.x) / (Math.PI / 12);
        expect(Math.abs(angle - Math.round(angle))).toBeLessThan(1e-6);
      }
    }
    const input = [{ x: 0, y: 0 }, { x: 123, y: 31 }];
    expect(constrainedPolyline(input, false)).toEqual(input);
  });
  it("moves common exits with connectors without changing lengths, electrical ends or shared topology", () => {
    const d = physicalFixture();
    const moved = applyEditorCommand(d, { type: "move-connector", connectorId: "A", view: "drawing", position: { x: 33, y: 66 } });
    expect(physicalSegmentPoints(moved, moved.physicalTopology!.segments[0]!)[0]).toEqual({ x: 183, y: 106 });
    expect(moved.wires).toBe(d.wires);
    expect(physicalWirePoints(moved, "W1", { x: 0, y: 0 }, { x: 1, y: 1 })!.length).toBeGreaterThan(3);
  });
  it("rejects broken references, discontinuous routes and wrong connector exits", () => {
    const d = physicalFixture();
    for (const steps of [[{ segmentId: "missing", reverse: false }], [{ segmentId: "S0", reverse: false }, { segmentId: "S1", reverse: true }], [{ segmentId: "S2", reverse: false }]]) {
      expect(() => parseHarnessDesignDocument({ ...d, physicalTopology: { ...d.physicalTopology!, routes: [{ wireId: "W1", steps }] } })).toThrow();
    }
    expect(parseHarnessDesignDocument(createEmptyHarnessDesign()).physicalTopology).toBeUndefined();
  });
  it("removes route references when a wire is deleted", () => {
    const d = applyEditorCommand(physicalFixture(), { type: "remove-wire", wireId: "W1" });
    expect(d.physicalTopology!.routes.map(r => r.wireId)).toEqual(["W2", "W3"]);
    expect(() => parseHarnessDesignDocument(d)).not.toThrow();
  });
  it("faces an exit toward its connected contacts on both sides and rotates with the connector",()=>{
    const base=physicalFixture(),node=base.physicalTopology!.nodes[0]!;
    const atLeft={...base,physicalTopology:{...base.physicalTopology!,nodes:base.physicalTopology!.nodes.map(n=>n.id===node.id?{...n,position:{x:-30,y:40},direction:"left" as const}:n)}};
    expect(physicalNodeDirection(atLeft,atLeft.physicalTopology.nodes[0]!)!.x).toBeCloseTo(-1);
    expect(physicalNodeFacingDirection(atLeft,atLeft.physicalTopology.nodes[0]!)!.x).toBeGreaterThan(0);
    const atRight={...base,physicalTopology:{...base.physicalTopology!,nodes:base.physicalTopology!.nodes.map(n=>n.id===node.id?{...n,position:{x:170,y:40},direction:"right" as const}:n)}};
    expect(physicalNodeFacingDirection(atRight,atRight.physicalTopology.nodes[0]!)!.x).toBeLessThan(0);
    const rotated={...atLeft,connectors:atLeft.connectors.map(c=>c.id===node.connectorId?{...c,drawingPlacements:[{drawingId:"view:drawing",offset:{x:0,y:0},visible:true,scale:1,rotationDegrees:90}]}:c)};
    const facing=physicalNodeFacingDirection(rotated,rotated.physicalTopology.nodes[0]!)!;
    const exitPoint=physicalNodePoint(rotated,rotated.physicalTopology.nodes[0]!);
    const contactPoints=rotated.connectors[0]!.contacts.map(contact=>connectorContactPosition(rotated.connectors[0]!,contact.id,"drawing")!);
    const center=contactPoints.reduce((sum,point)=>({x:sum.x+point.x/contactPoints.length,y:sum.y+point.y/contactPoints.length}),{x:0,y:0});
    const expected={x:center.x-exitPoint.x,y:center.y-exitPoint.y};
    expect(facing.x).toBeCloseTo(expected.x/Math.hypot(expected.x,expected.y));
    expect(facing.y).toBeCloseTo(expected.y/Math.hypot(expected.x,expected.y));
    expect(physicalNodeFacingDirection(base,base.physicalTopology!.nodes.find(n=>n.id==="J")!)).toBeNull();
  });
  it("removes a pipe, its authored bends, protection spans and orphan junction", () => {
    const d = physicalFixture();
    const topology = removePhysicalSegment(d, "S0");
    expect(topology.segments.some(segment => segment.id === "S0")).toBe(false);
    expect(topology.routes.some(route => route.steps.some(step => step.segmentId === "S0"))).toBe(false);
    expect(topology.coverings ?? []).toEqual([]);
    expect(topology.nodes.some(node => node.id === "J")).toBe(true);
    const withOnlyPipe = { ...d, physicalTopology: { ...d.physicalTopology!, segments: [d.physicalTopology!.segments[0]!], routes: d.physicalTopology!.routes.filter(route => route.steps.some(step => step.segmentId === "S0")) } };
    const removed = removePhysicalSegment(withOnlyPipe, "S0");
    expect(removed.nodes.some(node => node.id === "J")).toBe(false);
    expect(parseHarnessDesignDocument({ ...withOnlyPipe, physicalTopology: removed }).physicalTopology).toEqual(removed);
  });
  it("connects a node to an interior point of another pipe and preserves the pipe style", () => {
    const d = physicalFixture();
    const segment = d.physicalTopology!.segments[0]!;
    const points = physicalSegmentPoints(d, segment);
    const point = { x: (points[0]!.x + points[1]!.x) / 2, y: (points[0]!.y + points[1]!.y) / 2 };
    const topology = connectPhysicalNodeToSegment(d, "NC", "S0", point, { junction: "join", segment: "branch", continuation: "tail" });
    expect(topology.nodes.some(node => node.id === "join")).toBe(true);
    expect(topology.segments.find(item => item.id === "branch")).toMatchObject({ from: "NC", to: "join" });
    expect(topology.segments.find(item => item.id === "tail")).toMatchObject({ from: "join", to: "J" });
    expect(topology.segments.filter(item => item.from === "join" || item.to === "join")).toHaveLength(3);
    expect(parseHarnessDesignDocument({ ...d, physicalTopology: topology }).physicalTopology).toEqual(topology);
  });
  it("deletes only dependent geometry and restores it with undo", () => {
    const base = physicalFixture();
    const doc = {...base, physicalTopology: {...base.physicalTopology!,
      nodes: [...base.physicalTopology!.nodes, {id: "loose", position: {x: 10, y: 20}}],
      coverings: [{id: "wrap", name: "Tape", width: 12, color: "#333333", lengthMm: null,
        spans: [{segmentId: "S0", from: 0, to: 1}, {segmentId: "S1", from: 0, to: 1}]}]}};
    const h = executeEditorCommand(createEditorHistory(doc), {type: "remove-physical-segment", segmentId: "S0"});
    expect(h.present.physicalTopology!.nodes.some(node => node.id === "loose")).toBe(true);
    expect(h.present.physicalTopology!.coverings![0]!.spans).toEqual([{segmentId: "S1", from: 0, to: 1}]);
    expect(h.present.physicalTopology!.routes.map(route => route.wireId)).toEqual(["W3"]);
    expect(h.present.wires).toBe(doc.wires);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(h.present))).physicalTopology).toEqual(h.present.physicalTopology);
    expect(undoEditorCommand(h).present).toBe(doc);
  });
});

it("keeps connector exits and drag coordinates consistent with a rotated scaled drawing",async()=>{
 const {physicalNodeLocalPoint,physicalNodePoint}=await import("./physical-topology");
 const {createConnector}=await import("./commands");
 const {createEmptyHarnessDesign}=await import("./model");
 const c={...createConnector("c","X1",1,{x:0,y:0}),drawingPlacements:[{drawingId:"view:drawing",offset:{x:0,y:0},visible:true,scale:2,rotationDegrees:90}]};
 const doc={...createEmptyHarnessDesign(),connectors:[c]},node={id:"exit",connectorId:c.id,position:{x:30,y:20}};
 const point=physicalNodePoint(doc,node);
 expect(point.x).toBeCloseTo(c.positions.drawing.x-40);expect(point.y).toBeCloseTo(c.positions.drawing.y+60);
 const local=physicalNodeLocalPoint(doc,node,point);expect(local.x).toBeCloseTo(30);expect(local.y).toBeCloseTo(20);
});

it("displays E4 conductors as separate channel lanes and hides them without changing the route",async()=>{
 const {physicalWireDisplayPaths}=await import("./physical-topology");
 const d=physicalFixture(),start={x:0,y:0},end={x:1000,y:500};
 const first=physicalWireDisplayPaths(d,"W1",start,end)!,second=physicalWireDisplayPaths(d,"W2",start,end)!;
 expect(first[0]).not.toEqual(second[0]);
 const t={...d.physicalTopology!,segments:d.physicalTopology!.segments.map(s=>({...s,showWires:false,width:24,color:"#112233"}))};
    const h=executeEditorCommand(createEditorHistory(d),{type:"set-physical-topology",topology:t});
    const hiddenPaths=physicalWireDisplayPaths(h.present,"W1",start,end)!;
    expect(hiddenPaths).toHaveLength(2);
    expect(hiddenPaths[0]!.length).toBeLessThan(physicalWireDisplayPaths(d,"W1",start,end)![0]!.length);
 expect(h.present.wires).toEqual(d.wires);expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(h.present))).physicalTopology).toEqual(t);
 expect(undoEditorCommand(h).present).toEqual(d);
});

it("makes a T at an interior click without losing reverse routes, protection spans or channel style",async()=>{
 const {branchPhysicalSegment}=await import("./physical-topology");
 const d=physicalFixture(),segment={...d.physicalTopology!.segments[0]!,width:28,color:"#112233",showWires:false};
 const doc={...d,physicalTopology:{...d.physicalTopology!,snap:false,segments:[segment,...d.physicalTopology!.segments.slice(1)],coverings:[{id:"cover",name:"Tape",width:32,color:"#222222",lengthMm:50,spans:[{segmentId:"S0",from:0,to:1}]}]}};
 const points=physicalSegmentPoints(doc,segment),point={x:(points[0]!.x+points[1]!.x)/2,y:(points[0]!.y+points[1]!.y)/2};
 const t=branchPhysicalSegment(doc,"S0",point,{junction:"T",continuation:"tail",tip:"tip",branch:"branch"});
 expect(t.segments.filter(s=>s.from==="T"||s.to==="T")).toHaveLength(3);
 expect(t.segments.find(s=>s.id==="tail")).toMatchObject({width:28,color:"#112233",showWires:false});
 expect(t.coverings![0]!.spans.map(s=>s.segmentId)).toEqual(["S0","tail"]);
 expect(t.routes[0]!.steps.map(s=>s.segmentId)).toEqual(["S0","tail","S1"]);
 expect(parseHarnessDesignDocument({...doc,physicalTopology:t}).physicalTopology).toEqual(t);
 expect(doc.wires).toBe(d.wires);
});
it("keeps a split OP member bend editable after creating a T branch",()=>{
 const source=physicalFixture(),op=createJoiningPipe(source,[["S1"],["S2"]],"op"),withOp={...source,physicalTopology:{...source.physicalTopology!,joiningPipes:[op]}};
 const segment=withOp.physicalTopology!.segments.find(item=>item.id==="S1")!,points=physicalSegmentPoints(withOp,segment);
 const point={x:(points[0]!.x+points[1]!.x)/2,y:(points[0]!.y+points[1]!.y)/2};
 const topology=branchPhysicalSegment(withOp,"S1",point,{junction:"T",continuation:"tail",tip:"tip",branch:"branch"}),branched={...withOp,physicalTopology:topology};
 expect(topology.segments.filter(item=>item.from==="T"||item.to==="T")).toHaveLength(3);
 const scene=physicalTopologyScene(branched),candidate=["S1","tail"].map(id=>scene.find(object=>object.id===id)!).find(object=>object.pipe!.authoredHandleIndices!.some(index=>index>=1))!;
 const handle=candidate.pipe!.authoredHandleIndices!.findIndex(index=>index>=1),origin=candidate.pipe!.handles[handle]!;
 const command=resolvePhysicalRoutePointCommand(branched,candidate.id,handle,{x:origin.x+25,y:origin.y-20},"adjacent");
 expect(command).toMatchObject({type:"edit-physical-bend",segmentId:candidate.id});
 const history=executeEditorCommand(createEditorHistory(branched),command!);
 expect(history.present.physicalTopology!.segments.find(item=>item.id===candidate.id)!.path.points).not.toEqual(branched.physicalTopology!.segments.find(item=>item.id===candidate.id)!.path.points);
 expect(history.present.physicalTopology!.routes).toEqual(branched.physicalTopology!.routes);
 expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(history.present))).physicalTopology).toEqual(history.present.physicalTopology);
 expect(undoEditorCommand(history).present).toBe(branched);
});
it("places an ordinary bend grip on the rounded painted route and preserves its drag delta",()=>{
 const source=physicalFixture(),segment=source.physicalTopology!.segments.find(item=>item.id==="S0")!;
 const document={...source,drawingDocuments:{...source.drawingDocuments!,bendRadius:24},physicalTopology:{...source.physicalTopology!,
   segments:source.physicalTopology!.segments.map(item=>item.id===segment.id?{...item,path:{kind:"polyline" as const,points:[{x:220,y:80}]}}:item)}};
 const scene=physicalTopologyScene(document).find(item=>item.id===segment.id)!;
 const grip=scene.pipe!.handles[0]!;
 const painted=scene.points!;
 const nearest=Math.min(...painted.map(point=>Math.hypot(point.x-grip.x,point.y-grip.y)));
 expect(nearest).toBeLessThan(1e-6);
 const command=resolvePhysicalRoutePointCommand(document,segment.id,0,{x:grip.x+15,y:grip.y-8},"carry");
 expect(command).toMatchObject({type:"edit-physical-bend",segmentId:segment.id,index:0});
 expect((command as any).position).toEqual({x:235,y:72});
});
it("distributes wires to shortest channels, preserves pinned routes and supports separate exits for double crimp",async()=>{
 const {routePhysicalWires}=await import("./physical-topology");const base=physicalFixture();const d={...base,wires:base.wires.map(w=>w.id==="W2"?{...w,from:base.wires[0]!.from,e4Route:createOrthogonalE4Route(wireEndpointE4Anchor(base,base.wires[0]!.from)!,wireEndpointE4Anchor(base,w.to)!)}:w)};
 let t={...d.physicalTopology!,nodes:[...d.physicalTopology!.nodes,{id:"NA2",connectorId:"A",position:{x:160,y:70},wireIds:["W2"]}],segments:[...d.physicalTopology!.segments,{id:"direct",from:"NA2",to:"NC",path: { kind: "routed" as const, points: [] }}],routes:[]};
 t={...t,nodes:t.nodes.map(n=>n.id==="NA"?{...n,wireIds:["W1"]}:n)};
 const routed=routePhysicalWires(d,t);
 expect(routed.routes.find(r=>r.wireId==="W2")!.steps).toEqual([{segmentId:"direct",reverse:false}]);
 expect(routed.routes.find(r=>r.wireId==="W1")!.steps.map(s=>s.segmentId)).toEqual(["S0","S1"]);
 expect(parseHarnessDesignDocument({...d,physicalTopology:routed}).physicalTopology).toEqual(routed);
 expect(routePhysicalWires(d,d.physicalTopology!).routes).toEqual(d.physicalTopology!.routes);
 expect(d.wires[0]!.from.contactId).toEqual(d.wires[1]!.from.contactId);
 const noExit={...t,nodes:t.nodes.map(n=>n.connectorId==="A"?{...n,wireIds:[]}:n)};
 expect(routePhysicalWires(d,noExit).routes.some(r=>r.wireId==="W1"||r.wireId==="W2")).toBe(false);
});

it("routes a wire through a physically connected intermediate connector",async()=>{
 const {routePhysicalWires}=await import("./physical-topology");
 const base=physicalFixture(),t={...base.physicalTopology!,nodes:[
   {id:"from",connectorId:"A",position:{x:150,y:40},wireIds:["W1"]},
   {id:"x4",connectorId:"C",position:{x:150,y:40},transitWireIds:["W1"]},
   {id:"to",connectorId:"B",position:{x:150,y:40},wireIds:["W1"]},
 ],segments:[
   {id:"A-X4",from:"from",to:"x4",path:{kind:"polyline" as const,points:[]}},
   {id:"X4-B",from:"x4",to:"to",path:{kind:"polyline" as const,points:[]}},
 ],routes:[]};
 const routed=routePhysicalWires(base,t);
 expect(routed.routes.find(route=>route.wireId==="W1")?.steps).toEqual([
   {segmentId:"A-X4",reverse:false},{segmentId:"X4-B",reverse:false},
 ]);
 expect(routed.routes.some(route=>route.wireId==="W2"||route.wireId==="W3")).toBe(false);
});
