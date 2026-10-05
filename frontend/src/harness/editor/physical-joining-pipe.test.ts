// @vitest-environment node
import {describe,expect,it} from "vitest";
import {createEmptyHarnessDesign,type HarnessDesignDocument} from "./model";
import {createJoiningPipe,joiningPipePoints} from "./physical-joining-pipes";
import {joiningPipeDisplaySamples,joiningPipeMemberControls,joiningPipePacking,joiningPipeTransitionHandles,joiningPipeWidth,projectJoiningPipePoint} from "./physical-joining-pipe-projection";
import {editJoiningPipeBend,moveJoiningPipe} from "./physical-joining-pipe-editing";
import {parsePhysicalTopology} from "./physical-topology-validation";
import {coveringScene,moveCovering} from "./covering-layout";
import {removePhysicalSegment} from "./physical-topology";
import {splitPhysicalSegment} from "./physical-topology";
import {physicalTopologyScene} from "./physical-scene";
import {applyEditorCommand} from "./commands";
import {parseHarnessDesignDocument} from "./model";
import {migrateJoiningPipes} from "./physical-joining-pipes";
import {beginJoiningPipe,toggleJoiningPipeMember,joiningPipeDraftTopology} from "./JoiningPipeEditor";
import {createEditorHistory,executeEditorCommand,undoEditorCommand} from "./history";
import {hitTestEditorScene,hitTestWireRoutePoint,numberedPipeBendHandles,pipeMidpoints} from "./CanvasViewport";
import {coveringHit,coveringSurfaces} from "./covering-renderer";
import {coveringGrips} from "./covering-renderer";
import {coveringRoute,standardCovering} from "./physical-coverings";
import {drawingRouteCommands,drawingRouteHitPoints} from "./drawing-route-path";
import {bendSnapAnchors,pipeBendSnapAnchors,physicalObjectRouteAnchors,joiningPipeEndpointSnapAnchors,snapBendPoint,snapPhysicalPoint} from "./physical-editing";
import {unprojectPipeBundleEdit} from "./pipe-bundle-projection";

function fixture():HarnessDesignDocument {
 const d=createEmptyHarnessDesign();
 return {...d,physicalTopology:{snap:true,nodes:[
   {id:"a",position:{x:0,y:0}},{id:"b",position:{x:600,y:0}},{id:"c",position:{x:0,y:100}},{id:"d",position:{x:600,y:100}}],
   segments:[
    {id:"p0",from:"a",to:"b",path:{kind:"polyline",points:[]}},
    {id:"p1",from:"c",to:"d",path:{kind:"polyline",points:[]}}],routes:[]}};
}

it("keeps an outer OP covering outside the inner covering along both member tails",()=>{
 const source=fixture(),op=createJoiningPipe(source,[["p0"],["p1"]],"op");
 const inner={id:"inner",name:"Inner",kind:"heat-shrink" as const,width:0,color:"#8899aa",lengthMm:null,
  spans:[{segmentId:"op",from:-.3,to:1.3}]};
 const outer={...inner,id:"outer",name:"Outer"};
 const document={...source,physicalTopology:{...source.physicalTopology!,joiningPipes:[op],coverings:[inner,outer]}};
 const [inside,outside]=coveringScene(document),innerPolygon=coveringSurfaces(inside!)[0]!.polygon;
 expect(innerPolygon.length).toBeGreaterThan(4);
 for(const point of innerPolygon)expect(coveringHit(outside!,point,0)).toBe(0);
});

describe("joining pipe hierarchy",()=>{
 it.each(["from","to"] as const)("snaps the %s OP endpoint to a horizontal axis from below and above",side=>{
  const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op");
  const doc={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op]}};
  const scene=physicalTopologyScene(doc),end=scene.find(o=>o.id===`op:${side}`)!;
  const anchors=joiningPipeEndpointSnapAnchors(scene,end);
  expect(anchors).toHaveLength(1);
  for(const direction of [1,-1]){
   const pointerYs=direction===1?[45,30,20,10,0,-10]:[-45,-30,-20,-10,0,10];
   const snapped=pointerYs.map(delta=>snapPhysicalPoint({x:end.x+100,y:anchors[0]!.y+delta},anchors,true,7,Math.PI/6).point);
   expect(snapped[4]!.y).toBeCloseTo(anchors[0]!.y);
  }
  expect(snapPhysicalPoint({x:end.x+35,y:end.y+9},anchors,false,7,Math.PI/6).point).toEqual({x:end.x+35,y:end.y+9});
  expect(joiningPipeEndpointSnapAnchors(scene,{...end,metadata:{joiningPipe:"other"}})).toEqual([]);
 });
 it("retains local Ctrl and Shift route anchors for a member endpoint",()=>{
  const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op");
  const doc={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op]}};
  const scene=physicalTopologyScene(doc),node=scene.find(object=>object.id==="c")!;
  expect(node.metadata?.bundleMember).toBe("true");
  for(const mode of ["carry","adjacent"] as const){
   const anchors=physicalObjectRouteAnchors(scene,node,mode);
   expect(anchors.length).toBeGreaterThan(0);
   expect(snapBendPoint({x:node.x+31,y:node.y+22},anchors,true,7,node,Math.PI/6).point)
    .not.toEqual({x:node.x+31,y:node.y+22});
  }
 });
 it("keeps Ctrl anchors on member transitions and maps Shift anchors to authored bends",()=>{
  const d=fixture(),segments=d.physicalTopology!.segments.map(segment=>segment.id==="p0"
    ?{...segment,path:{kind:"polyline" as const,points:[{x:80,y:30},{x:500,y:30}]}}:segment);
  const source={...d,physicalTopology:{...d.physicalTopology!,segments}};
  const op=createJoiningPipe(source,[["p0"],["p1"]],"op");
  const doc={...source,physicalTopology:{...source.physicalTopology,joiningPipes:[op]}};
  const member=physicalTopologyScene(doc).find(object=>object.id==="p0")!;
  const pipe=member.pipe!,display=[member.points![0]!,...pipe.handles,member.points!.at(-1)!];
  expect(pipe.joiningTransitionHandles).toHaveLength(0);
  const authoredIndex=pipe.authoredHandleIndices!.findIndex(value=>value===1);
  expect(authoredIndex).toBeGreaterThanOrEqual(0);
  const authoredPoint=pipe.handles[authoredIndex]!;
 });
it("keeps explicit member transition points separate from shared exits",()=>{
  const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op"),base={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op]}};
  expect(joiningPipeTransitionHandles(base,"p0")).toHaveLength(0);
  const moved=applyEditorCommand(base,{type:"update-joining-pipe-member-bend",pipeId:"op",memberIndex:0,side:"enter",position:{x:150,y:-40}});
  expect(joiningPipeTransitionHandles(moved,"p0")).toHaveLength(1);
  expect(joiningPipeDisplaySamples(moved,"p0")!.some(sample=>Math.hypot(sample.point.x-150,sample.point.y+40)<1e-6)).toBe(true);
  const changed=applyEditorCommand(moved,{type:"update-joining-pipe-exit",pipeId:"op",side:"from",position:{x:op.start.x-80,y:op.start.y+50}});
  expect(changed.physicalTopology!.joiningPipes![0]!.members[0]!.enterBend).toEqual({x:150,y:-40});
  expect(joiningPipeDisplaySamples(changed,"p0")!.some(sample=>Math.hypot(sample.point.x-150,sample.point.y+40)<1e-6)).toBe(true);
  expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(changed))).physicalTopology!.joiningPipes![0]!.members[0]!.enterBend).toEqual({x:150,y:-40});
 });
 it("moves a member's authored outside bend through the displayed handle",()=>{
  const d=fixture(),segments=d.physicalTopology!.segments.map(segment=>segment.id==="p0"?{...segment,path:{kind:"polyline" as const,points:[{x:80,y:30},{x:500,y:30}]}}:segment);
  const source={...d,physicalTopology:{...d.physicalTopology!,segments}},op=createJoiningPipe(source,[["p0"],["p1"]],"op");
  const doc={...source,physicalTopology:{...source.physicalTopology,joiningPipes:[op]}},before=physicalTopologyScene(doc).find(o=>o.id==="p0")!;
  const index=before.pipe!.authoredHandleIndices!.findIndex(i=>i===1),visible=before.pipe!.handles[index]!;
  expect(index).toBeGreaterThanOrEqual(0);
  const target={x:visible.x+25,y:visible.y-35};
  const position=unprojectPipeBundleEdit(doc,"p0",{x:80,y:30},target);
  const preview=applyEditorCommand(doc,{type:"edit-physical-bend",segmentId:"p0",index:0,position,mode:"adjacent"});
  expect(preview.physicalTopology!.segments[0]!.path.points[0]).toEqual(position);
  const after=physicalTopologyScene(preview).find(o=>o.id==="p0")!;
  expect(after.pipe!.handles[after.pipe!.authoredHandleIndices!.findIndex(i=>i===1)]).not.toEqual(visible);
  expect(preview.physicalTopology!.segments[0]!.path.points).toHaveLength(2);
 });
 it("keeps Ctrl anchors on a saved transition",()=>{
  const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op"),explicit={...op,members:op.members.map((entry,index)=>index===0?{...entry,enterBend:{x:140,y:-25}}:entry)};
  const doc={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[explicit]}};
  const member=physicalTopologyScene(doc).find(object=>object.id==="p0")!,pipe=member.pipe!,display=[member.points![0]!,...pipe.handles,member.points!.at(-1)!];
  const transition=pipe.joiningTransitionHandles!.find(handle=>handle.side==="enter")!;
  const transitionPoint=pipe.handles[transition.index]!;
  const transitionAnchors=pipeBendSnapAnchors(pipe,display,transition.index,false,"adjacent",transitionPoint);
  expect(transitionAnchors).toEqual(bendSnapAnchors(display,transition.index,false,"adjacent",transitionPoint));
  expect(transitionAnchors).toHaveLength(2);
  const snapped=snapBendPoint({x:transitionPoint.x+27,y:transitionPoint.y+41},transitionAnchors,true,7,undefined,Math.PI/6).point;
  expect(snapped).not.toEqual({x:transitionPoint.x+27,y:transitionPoint.y+41});
  const memberIndex=transition.memberIndex;
  const memberWithOuter={...op,members:op.members.map((entry,index)=>index===memberIndex
   ?{...entry,enterBend:transitionPoint,enterOuter:display[transition.index]!}:entry)};
  const start={...doc,physicalTopology:{...doc.physicalTopology,joiningPipes:[memberWithOuter]}};
  const destination={x:transitionPoint.x+24,y:transitionPoint.y-12};
  const adjacent=applyEditorCommand(start,{type:"update-joining-pipe-member-bend",pipeId:"op",memberIndex,side:"enter",position:destination,mode:"adjacent"});
  const carried=applyEditorCommand(start,{type:"update-joining-pipe-member-bend",pipeId:"op",memberIndex,side:"enter",position:destination,mode:"carry"});
  expect(adjacent.physicalTopology!.joiningPipes![0]!.members[memberIndex]!.enterOuter).toEqual(display[transition.index]);
  expect(carried.physicalTopology!.joiningPipes![0]!.members[memberIndex]!.enterOuter!.x).toBeCloseTo(display[transition.index]!.x+24);
  expect(carried.physicalTopology!.joiningPipes![0]!.members[memberIndex]!.enterOuter!.y).toBeCloseTo(display[transition.index]!.y-12);
  expect(carried.physicalTopology!.routes).toEqual(start.physicalTopology.routes);
 });
 it("owns an independent centreline and keeps members parallel inside it",()=>{
  const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op");
  const next={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op]}};
  expect(op.start).toEqual({x:180,y:0});expect(op.end).toEqual({x:420,y:0});
  const inside0=projectJoiningPipePoint(next,"p0",.5,{x:300,y:0}),inside1=projectJoiningPipePoint(next,"p1",.5,{x:300,y:100});
  expect(inside0.x).toBe(300);expect(inside1.x).toBe(300);expect(inside0.y).toBeLessThan(inside1.y);
  expect(joiningPipeWidth(next,op)).toBeGreaterThan(0);
  expect(joiningPipePoints(op)).toEqual([{x:180,y:0},{x:420,y:0}]);
 });
it("moves an OP bend without changing members or electrical routes",()=>{
  const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op"),base={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op]}};
  const moved={...base,physicalTopology:editJoiningPipeBend(base,"op",0,{x:300,y:80},"adjacent",true)};
  expect(moved.physicalTopology!.joiningPipes![0]!.path.points).toEqual([{x:300,y:80}]);
  expect(moved.physicalTopology!.segments).toEqual(base.physicalTopology!.segments);
  expect(moved.physicalTopology!.routes).toBe(base.physicalTopology!.routes);
  expect(projectJoiningPipePoint(moved,"p1",.5,{x:300,y:100}).y).toBeGreaterThan(75);
 });
 it("allows a covering to target OP and survives JSON validation",()=>{
  const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op"),next={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op],coverings:[{id:"cover",name:"Термоусадка",width:0,color:"#334455",lengthMm:null,spans:[{segmentId:"op",from:0,to:1}]}]}};
  const path=coveringScene(next).find(c=>c.id==="cover")!.paths![0]!;
  expect(path[0]!.x).toBeCloseTo(op.start.x);expect(path.at(-1)!.x).toBeCloseTo(op.end.x);
  const loaded=JSON.parse(JSON.stringify(next));expect(parsePhysicalTopology(loaded.physicalTopology,loaded)!.joiningPipes![0]!.id).toBe("op");
 });
 it("slides an OP covering past its end and encloses outgoing member pipes",()=>{
  const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op");
  const cover={id:"sleeve",name:"Термоусадка",width:0,color:"#334455",lengthMm:null,spans:[{segmentId:"op",from:.1,to:.9}]};
  const base={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op],coverings:[cover]}};
  const route=coveringRoute(base,"op")!;
  expect(route.min).toBeLessThan(0);expect(route.max).toBeGreaterThan(1);
  expect(route.points[0]!.x).toBeLessThan(op.start.x);
  const grip=coveringGrips(coveringScene(base).find(object=>object.id==="sleeve")!).find(handle=>handle.part==="from")!;
  const moved=moveCovering(base,"sleeve",0,"from",grip.point,{x:75,y:50},0)!;
  expect(moved.spans[0]!.from).toBeLessThan(0);
  const endGrip=coveringGrips(coveringScene(base).find(object=>object.id==="sleeve")!).find(handle=>handle.part==="to")!;
  expect(moveCovering(base,"sleeve",0,"to",endGrip.point,{x:525,y:50},0)!.spans[0]!.to).toBeGreaterThan(1);
  const slid=moveCovering(base,"sleeve",0,"body",{x:300,y:0},{x:90,y:50},0)!;
  expect(slid.spans[0]!.from).toBeLessThan(0);
  const next={...base,physicalTopology:{...base.physicalTopology,coverings:[moved]}},shell=coveringScene(next).find(object=>object.id==="sleeve")!;
  expect(shell.paths![0]![0]!.x).toBeLessThan(op.start.x);
  expect(coveringHit(shell,projectJoiningPipePoint(base,"p0",110/600,{x:110,y:0}),1)).not.toBeNull();
  expect(coveringHit(shell,projectJoiningPipePoint(base,"p1",110/600,{x:110,y:100}),1)).not.toBeNull();
  expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(next))).physicalTopology!.coverings![0]!.spans).toEqual(moved.spans);
  const bent=applyEditorCommand(next,{type:"edit-joining-pipe-bend",pipeId:"op",index:0,position:{x:300,y:90},mode:"adjacent",insert:true});
  expect(coveringScene(bent).find(object=>object.id==="sleeve")!.paths).not.toEqual(shell.paths);
  expect(bent.physicalTopology!.routes).toEqual(base.physicalTopology!.routes);
 });
 it("removes an OP when fewer than two member pipes remain",()=>{
  const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op"),next={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op]}};
  const removed=removePhysicalSegment(next,"p1");expect(removed.joiningPipes).toEqual([]);
 });
});

it("endpoints move freely in both axes and preserve connectors, routes and member paths",()=>{
 const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op"),base={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op]}};
 const moved=applyEditorCommand(base,{type:"update-joining-pipe",pipeId:"op",start:{x:250,y:170},end:{x:500,y:230}});
 expect(moved.physicalTopology!.joiningPipes![0]!.start).toEqual({x:250,y:170});
 expect(moved.physicalTopology!.segments).toEqual(base.physicalTopology!.segments);expect(moved.connectors).toBe(base.connectors);expect(moved.wires).toBe(base.wires);
 const a=projectJoiningPipePoint(moved,"p0",.4,{x:240,y:0}),b=projectJoiningPipePoint(moved,"p0",.6,{x:360,y:0});
 expect((b.y-a.y)/(b.x-a.x)).toBeCloseTo(60/250,6);
 for(const id of ["p0","p1"]){const path=joiningPipeDisplaySamples(moved,id)!;expect(path[0]!.point).toEqual({x:0,y:id==="p0"?0:100});expect(path.at(-1)!.point).toEqual({x:600,y:id==="p0"?0:100});}
});
it("moves the complete OP axis and authored transition bends together",()=>{
 const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op"),withBend={...op,members:op.members.map((member,index)=>index===1?{...member,enterBend:{x:140,y:20},exitBend:{x:460,y:20}}:member),path:{kind:"polyline" as const,points:[{x:300,y:40}]}};
 const base={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[withBend]}},moved=moveJoiningPipe(base.physicalTopology!,"op",{x:25,y:-12}),next=moved.joiningPipes![0]!;
 expect(next.start).toEqual({x:205,y:-12});expect(next.end).toEqual({x:445,y:-12});expect(next.path.points).toEqual([{x:325,y:28}]);
 expect(next.members[1]!.enterBend).toEqual({x:165,y:8});expect(next.members[1]!.exitBend).toEqual({x:485,y:8});
 expect(moved.segments).toBe(base.physicalTopology!.segments);
});
it("rotates authored member transition bends with an OP corner",()=>{
 const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op"),withBend={...op,members:op.members.map((member,index)=>index===1?{...member,enterBend:{x:140,y:20},exitBend:{x:460,y:20}}:member)},base={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[withBend]}};
 const before=base.physicalTopology!.joiningPipes![0]!.members[1]!;
 const bent=applyEditorCommand(base,{type:"edit-joining-pipe-bend",pipeId:"op",index:0,position:{x:300,y:120},mode:"adjacent",insert:true});
 const after=bent.physicalTopology!.joiningPipes![0]!.members[1]!;
 expect(after.enterBend).not.toEqual(before.enterBend);expect(after.exitBend).not.toEqual(before.exitBend);
 const scene=physicalTopologyScene(bent).find(object=>object.id==="p1")!;
 expect(scene.pipe!.joiningTransitionHandles).toHaveLength(2);
 for(const handle of scene.pipe!.joiningTransitionHandles!)expect(hitTestWireRoutePoint(scene,scene.pipe!.handles[handle.index]!,1)).toBe(handle.index);
});
it("keeps projected members inside a covering when the OP bends",()=>{
 const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op"),base={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op],coverings:[{id:"cover",name:"Термоусадка",width:0,color:"#334455",lengthMm:null,spans:[{segmentId:"op",from:0,to:1}]}]}};
 const bent=applyEditorCommand(base,{type:"edit-joining-pipe-bend",pipeId:"op",index:0,position:{x:300,y:220},mode:"adjacent",insert:true});
 const shell=coveringScene(bent).find(object=>object.id==="cover")!,path=joiningPipeDisplaySamples(bent,"p0")!;
 expect(path.filter(sample=>sample.fraction>=.3&&sample.fraction<=.7).every(sample=>coveringHit(shell,sample.point,1e-6)!==null)).toBe(true);
});

it("retains the rounded OP axis after an exterior member bend is saved",()=>{
 const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op");
 const bent={...op,path:{kind:"polyline" as const,points:[{x:300,y:120}]}};
 const base={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[bent]}};
 const before=physicalTopologyScene(base).find(object=>object.id==="p1")!;
 const member=bent.members[1]!;
 const saved={...base,physicalTopology:{...base.physicalTopology,joiningPipes:[{...bent,members:bent.members.map((entry,index)=>
   index===1?{...entry,authoredBendRegions:[{segmentId:"p1",bendIndex:0,region:"before-enter" as const,
     displayPoint:{x:90,y:100}}]}:entry)}]}};
 const after=physicalTopologyScene(saved).find(object=>object.id==="p1")!;
 const centre=bent.path.points[0]!;
 const axisBefore=drawingRouteHitPoints(before.points!,before.routeRadius).filter(point=>Math.hypot(point.x-centre.x,point.y-centre.y)<55);
 const axisAfter=drawingRouteHitPoints(after.points!,after.routeRadius);
 expect(member.from).toBeLessThan(member.to);
 expect(axisBefore.length).toBeGreaterThan(0);
 for(const point of axisBefore)expect(Math.min(...axisAfter.map(other=>Math.hypot(point.x-other.x,point.y-other.y)))).toBeLessThan(5);
});

it("exposes joining transitions as authored member handles",()=>{
 const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op"),base={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op]}};
 const transitions=joiningPipeTransitionHandles(base,"p0");
 expect(transitions).toHaveLength(0);
 const member=physicalTopologyScene(base).find(object=>object.id==="p0")!;
 expect(member.pipe?.joiningTransitionHandles).toHaveLength(0);
 for(const transition of transitions)expect(member.pipe?.handles).toContainEqual(transition.point);
 expect(member.routeRadius).toBeGreaterThan(0);
 expect(drawingRouteCommands(member.points!,member.routeRadius).some(command=>command.kind==="arc")).toBe(true);
 const moved=applyEditorCommand(base,{type:"update-joining-pipe-member-bend",pipeId:"op",memberIndex:0,side:"enter",position:{x:150,y:-40}});
 expect(moved.physicalTopology!.joiningPipes![0]!.members[0]!.enterBend).toEqual({x:150,y:-40});
 expect(joiningPipeDisplaySamples(moved,"p0")!.some(sample=>Math.hypot(sample.point.x-150,sample.point.y+40)<1e-6)).toBe(true);
 const restored=parseHarnessDesignDocument(JSON.parse(JSON.stringify(moved)));
 expect(restored.physicalTopology!.joiningPipes![0]!.members[0]!.enterBend).toEqual({x:150,y:-40});
});

it("numbers, deletes, restores and recreates a member transition like a normal bend",()=>{
 const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op"),base={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op]}};
 const original=physicalTopologyScene(base).find(object=>object.id==="p1")!;
 expect(original.pipe!.joiningTransitionHandles).toHaveLength(0);
 const added=applyEditorCommand(base,{type:"update-joining-pipe-member-bend",pipeId:"op",memberIndex:1,side:"enter",position:{x:130,y:75}});
 const authored=physicalTopologyScene(added).find(object=>object.id==="p1")!;
 const enter=authored.pipe!.joiningTransitionHandles!.find(handle=>handle.side==="enter")!;
 const transitionPoint=authored.pipe!.handles[enter.index]!;
 expect(hitTestWireRoutePoint(authored,transitionPoint,1)).toBe(enter.index);
 expect(numberedPipeBendHandles(authored)).toContain(enter.index);
 const history=executeEditorCommand(createEditorHistory(added),{type:"update-joining-pipe-member-bend",pipeId:"op",memberIndex:enter.memberIndex,side:"enter",clear:true});
 const removed=history.present,member=physicalTopologyScene(removed).find(object=>object.id==="p1")!;
 expect(removed.physicalTopology!.joiningPipes![0]!.members[enter.memberIndex]!.enterBend).toBeNull();
 expect(member.pipe!.joiningTransitionHandles!.some(handle=>handle.side==="enter")).toBe(false);
 expect(member.pipe!.joiningTransitionHandles!.some(handle=>handle.side==="exit")).toBe(false);
 expect(joiningPipeDisplaySamples(removed,"p1")!.length).toBeLessThan(joiningPipeDisplaySamples(added,"p1")!.length);
 const loaded=parseHarnessDesignDocument(JSON.parse(JSON.stringify(removed)));
 expect(loaded.physicalTopology!.joiningPipes![0]!.members[enter.memberIndex]!.enterBend).toBeNull();
 expect(undoEditorCommand(history).present).toBe(added);
 const midpoint=member.pipe!.joiningTransitionMidpoints!.find(handle=>handle.side==="enter")!;
 expect(pipeMidpoints(member).some(handle=>handle.index===midpoint.index)).toBe(true);
 const recreated=applyEditorCommand(removed,{type:"update-joining-pipe-member-bend",pipeId:"op",memberIndex:enter.memberIndex,side:"enter",position:{x:150,y:75}});
 expect(physicalTopologyScene(recreated).find(object=>object.id==="p1")!.pipe!.joiningTransitionHandles!.some(handle=>handle.side==="enter")).toBe(true);
 expect(recreated.physicalTopology!.segments).toEqual(base.physicalTopology!.segments);
 expect(recreated.physicalTopology!.routes).toEqual(base.physicalTopology!.routes);
});

it("builds each member transition as connection to bend to connection with usable midpoints",()=>{
 const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op"),base={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op]}};
 const controls=joiningPipeMemberControls(base,"p1")!;
 expect(controls).toHaveLength(6);
 expect(controls.filter(control=>control.connection)).toHaveLength(4);
 expect(controls.filter(control=>control.lead)).toHaveLength(2);
 expect(controls.filter(control=>control.transition)).toHaveLength(0);
 expect(controls.map(control=>control.transition?.side)).toEqual(Array(6).fill(undefined));
 expect(controls.every((control,index)=>index===0||control.fraction>controls[index-1]!.fraction)).toBe(true);
 const member=physicalTopologyScene(base).find(object=>object.id==="p1")!;
 expect(member.pipe?.joiningTransitionMidpoints).toHaveLength(2);
 expect(pipeMidpoints(member)).toEqual(expect.arrayContaining(member.pipe!.joiningTransitionMidpoints!.map(handle=>expect.objectContaining({index:handle.index}))));
});

it("keeps boundary stations out of bend hit targets while preserving their geometry",()=>{
 const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op"),base={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op]}};
 const member=physicalTopologyScene(base).find(object=>object.id==="p1")!;
 const outer=member.pipe!.joiningBoundaryHandles!.find(handle=>handle.boundary==="outerEnter")!;
 const outerPoint=member.pipe!.handles[outer.index]!;
 expect(hitTestWireRoutePoint(member,outerPoint,1)).toBe(outer.index);
 const moved=applyEditorCommand(base,{type:"update-joining-pipe-member-boundary",pipeId:"op",memberIndex:outer.memberIndex,boundary:outer.boundary,origin:outerPoint,position:{x:outerPoint.x+20,y:outerPoint.y+30}});
 expect(moved.physicalTopology!.joiningPipes![0]!.members[1]!.enterOuter).toEqual({x:outerPoint.x+20,y:outerPoint.y+30});
 expect(joiningPipeMemberControls(moved,"p1")!.find(control=>control.boundary==="outerEnter")!.point).toEqual({x:outerPoint.x+20,y:outerPoint.y+30});
 const axis=member.pipe!.joiningBoundaryHandles!.find(handle=>handle.boundary==="axisEnter")!,axisPoint=member.pipe!.handles[axis.index]!;
 const shifted=applyEditorCommand(base,{type:"update-joining-pipe-member-boundary",pipeId:"op",memberIndex:axis.memberIndex,boundary:axis.boundary,origin:axisPoint,position:{x:axisPoint.x+15,y:axisPoint.y-10}});
 expect(shifted.physicalTopology!.joiningPipes![0]!.start).toEqual({x:op.start.x+15,y:op.start.y-10});
 expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(moved))).physicalTopology!.joiningPipes![0]!.members[1]!.enterOuter).toEqual({x:outerPoint.x+20,y:outerPoint.y+30});
});
it("keeps authored member bends attached when an OP endpoint moves",()=>{
 const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op"),withBend={...op,members:op.members.map((member,index)=>index===1?{...member,enterBend:{x:140,y:20},exitBend:{x:460,y:20}}:member)},base={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[withBend]}},scene=physicalTopologyScene(base).find(object=>object.id==="p1")!,axis=scene.pipe!.joiningBoundaryHandles!.find(handle=>handle.boundary==="axisEnter")!,origin=scene.pipe!.handles[axis.index]!;
 const moved=applyEditorCommand(base,{type:"update-joining-pipe-member-boundary",pipeId:"op",memberIndex:axis.memberIndex,boundary:axis.boundary,origin,position:{x:origin.x+30,y:origin.y+15}}),member=moved.physicalTopology!.joiningPipes![0]!.members[1]!;
 expect(member.enterBend).not.toEqual(withBend.members[1]!.enterBend);expect(member.exitBend).not.toEqual(withBend.members[1]!.exitBend);
});

it("reversed pipes follow the OP direction with fixed endpoints",()=>{
 const d=fixture(),reversed={...d,physicalTopology:{...d.physicalTopology!,segments:d.physicalTopology!.segments.map(s=>s.id==="p1"?{...s,from:s.to,to:s.from}:s)}};
 const op=createJoiningPipe(reversed,[["p0"],["p1"]],"op"),next={...reversed,physicalTopology:{...reversed.physicalTopology,joiningPipes:[op]}};
 expect(op.members[1]!.reverse).toBe(true);
 const a=projectJoiningPipePoint(next,"p1",.4,{x:360,y:100});expect(a.x).toBeCloseTo(360);expect(a.y).toBeCloseTo(.25);
 expect(joiningPipeDisplaySamples(next,"p1")![0]!.point).toEqual({x:600,y:100});
});
it("keeps an OP covering independent from a member P covering width",()=>{
 const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op");
 const memberCover={id:"member-cover",name:"Термоусадка",width:7,color:"#334455",lengthMm:null,spans:[{segmentId:"p0",from:0,to:1}]};
 const opCover={id:"op-cover",name:"Термоусадка",width:0,color:"#445566",lengthMm:null,spans:[{segmentId:"op",from:0,to:1}]};
 const base={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op],coverings:[memberCover,opCover]}};
 const before=coveringScene(base),beforeOp=before.find(object=>object.id==="op-cover")!,beforeMember=before.find(object=>object.id==="member-cover")!,beforePacking=joiningPipePacking(base,op);
 const changed={...base,physicalTopology:{...base.physicalTopology!,coverings:[{...memberCover,width:14},opCover]}};
 const after=coveringScene(changed),afterOp=after.find(object=>object.id==="op-cover")!,afterMember=after.find(object=>object.id==="member-cover")!,afterPacking=joiningPipePacking(changed,op);
 expect(afterMember.width).toBeGreaterThan(beforeMember.width);
 expect(afterOp.width).toBeCloseTo(beforeOp.width);
 expect(afterOp.paths).toEqual(beforeOp.paths);
 expect(afterPacking).toEqual(beforePacking);
 expect(changed.physicalTopology!.coverings![1]).toEqual(opCover);
});
it("keeps overlapping OP shell widths local and gives non-band overlays one 2% allowance",()=>{
  const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op");
  const shell=(id:string,width:number,kind:"heat-shrink"|"metal-braid"|"band")=>({id,name:id,kind,width,color:"#445566",lengthMm:null,spans:[{segmentId:"op",from:0,to:1}]});
  const inner=shell("inner",20,"heat-shrink"),outer=shell("outer",20,"metal-braid"),band=shell("band",0,"band");
  const base={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op],coverings:[inner,outer]}};
  const outerBefore=coveringScene(base).find(object=>object.id==="outer")!;
  expect(outerBefore.width).toBeCloseTo(20.4);
  const changed={...base,physicalTopology:{...base.physicalTopology!,coverings:[{...inner,width:36},outer]}};
  const outerAfter=coveringScene(changed).find(object=>object.id==="outer")!;
  expect(outerAfter.width).toBeCloseTo(outerBefore.width);
  expect(outerAfter.paths).toEqual(outerBefore.paths);
  const automatic={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op],coverings:[shell("base",0,"heat-shrink"),shell("plain",0,"metal-braid")]}};
  const automaticBand={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op],coverings:[shell("base",0,"heat-shrink"),band]}};
  expect(coveringScene(automatic).find(object=>object.id==="plain")!.width).toBeCloseTo(coveringScene(automaticBand).find(object=>object.id==="band")!.width);
});

it("keeps each OP lane nearest to its connector when members are added above the axis",()=>{
 const d=fixture(),nodes=d.physicalTopology!.nodes.map(node=>node.id==="c"||node.id==="d"?{...node,position:{...node.position,y:-100}}:node);
 const source={...d,physicalTopology:{...d.physicalTopology!,nodes}},op=createJoiningPipe(source,[["p0"],["p1"]],"op");
 const document={...source,physicalTopology:{...source.physicalTopology,joiningPipes:[op]}};
 const atMid=(id:string)=>joiningPipeDisplaySamples(document,id)!.reduce((best,current)=>
   Math.abs(current.fraction-.5)<Math.abs(best.fraction-.5)?current:best);
 expect(atMid("p1").point.y).toBeLessThan(atMid("p0").point.y);
});

it("keeps three shuffled member lanes ordered at both OP transitions and accepts an OP shell after reload",()=>{
 const d=fixture();
 const nodes=[
  {id:"a0",position:{x:0,y:0}},{id:"b0",position:{x:600,y:0}},
  {id:"a1",position:{x:0,y:-70}},{id:"b1",position:{x:600,y:-70}},
  {id:"a2",position:{x:0,y:70}},{id:"b2",position:{x:600,y:70}},
 ];
 const source={...d,physicalTopology:{...d.physicalTopology!,nodes,segments:[
  {id:"p0",from:"a0",to:"b0",path:{kind:"polyline" as const,points:[]}},
  {id:"p1",from:"a1",to:"b1",path:{kind:"polyline" as const,points:[]}},
  {id:"p2",from:"a2",to:"b2",path:{kind:"polyline" as const,points:[]}},
 ],routes:[]}};
 // Deliberately shuffled selection order must not become the visual lane order.
 const op=createJoiningPipe(source,[["p2"],["p0"],["p1"]],"op");
 const document={...source,physicalTopology:{...source.physicalTopology!,joiningPipes:[op]}};
 const packing=joiningPipePacking(document,op);
 const offsets=new Map(packing.members.map((member,index)=>[op.members[index]!.segmentIds[0]!,member.offset]));
 expect(offsets.get("p1")!).toBeLessThan(offsets.get("p0")!);
 expect(offsets.get("p0")!).toBeLessThan(offsets.get("p2")!);
 const shell=standardCovering(document,"op",op.start,"Термоусадка","shell");
 const saved=parseHarnessDesignDocument(JSON.parse(JSON.stringify({...document,physicalTopology:{...document.physicalTopology!,coverings:[shell]}})));
 expect(saved.physicalTopology!.coverings![0]!.spans[0]!.segmentId).toBe("op");
});

it("keeps reversed OP members attached to the nearest connector at both ends",()=>{
 const d=fixture(),nodes=d.physicalTopology!.nodes.map(node=>node.id==="c"||node.id==="d"?{...node,position:{...node.position,y:-100}}:node);
 const segment=d.physicalTopology!.segments.find(item=>item.id==="p1")!;
 const source={...d,physicalTopology:{...d.physicalTopology!,nodes,segments:d.physicalTopology!.segments.map(item=>
   item.id==="p1"?{...item,from:segment.to,to:segment.from}:item)}};
 const op=createJoiningPipe(source,[["p1"],["p0"]],"op"),document={...source,physicalTopology:{...source.physicalTopology,joiningPipes:[op]}};
 expect(op.members[1]!.reverse).toBe(true);
 const controls=joiningPipeMemberControls(document,"p0")!;
 expect(controls.find(control=>control.boundary==="outerEnter")!.point.x).toBeGreaterThan(controls.find(control=>control.boundary==="outerExit")!.point.x);
});

it("split fragments share a continuous lane and keep their node on the display route",()=>{
 const d=fixture(),t={...d.physicalTopology!,segments:d.physicalTopology!.segments.map(s=>s.id==="p0"?{...s,path:{kind:"polyline" as const,points:[{x:300,y:0}]}}:s)};
 const start={...d,physicalTopology:t},op=createJoiningPipe(start,[["p0"],["p1"]],"op"),base={...start,physicalTopology:{...t,joiningPipes:[op]}};
 const split={...base,physicalTopology:splitPhysicalSegment(base,"p0",1,"split","tail")};
 expect(split.physicalTopology.joiningPipes![0]!.members[0]!.segmentIds).toEqual(["p0","tail"]);
 const a=joiningPipeDisplaySamples(split,"p0")!.at(-1)!.point,b=joiningPipeDisplaySamples(split,"tail")![0]!.point;expect(a).toEqual(b);
 const node=physicalTopologyScene(split).find(o=>o.id==="split")!;expect(node.y+5).toBeCloseTo(a.y);
 expect(()=>parsePhysicalTopology(split.physicalTopology,split)).not.toThrow();
});

it("bend add, move and delete use one undo step and retain covering anchors",()=>{
 const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op"),base={...d,physicalTopology:{...d.physicalTopology!,joiningPipes:[op],coverings:[{id:"cover",name:"Термоусадка",color:"#112233",width:0,lengthMm:200,spans:[{segmentId:"op",from:0,to:1,fromAnchor:0,toAnchor:1}]}]}};
 const h=executeEditorCommand(createEditorHistory(base),{type:"edit-joining-pipe-bend",pipeId:"op",index:0,position:{x:300,y:80},mode:"adjacent",insert:true});
 expect(h.present.physicalTopology!.coverings![0]!.spans[0]!.toAnchor).toBe(2);
  const covered=coveringScene(h.present)[0]!.paths![0]!;
  expect(Math.max(...covered.map(p=>p.y))).toBeGreaterThan(60);
  expect(covered[0]).toEqual(op.start);expect(covered.at(-1)).toEqual(op.end);
 expect(undoEditorCommand(h).present).toBe(base);
 const removed=applyEditorCommand(h.present,{type:"edit-joining-pipe-bend",pipeId:"op",index:0,position:{x:0,y:0},mode:"adjacent",remove:true});
 expect(removed.physicalTopology!.joiningPipes![0]!.path.points).toEqual([]);expect(removed.physicalTopology!.coverings![0]!.spans[0]!.toAnchor).toBe(1);
 expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(h.present))).physicalTopology!.joiningPipes).toEqual(h.present.physicalTopology!.joiningPipes);
});

it("migrates legacy groups and coatings without losing IDs or material lengths",()=>{
 const d=fixture(),cover={id:"cover",name:"Термоусадка",color:"#112233",width:0,lengthMm:200,spans:[{segmentId:"p0",from:.2,to:.8}],bundle:{mode:"flat" as const,members:[{kind:"segment" as const,id:"p0"},{kind:"segment" as const,id:"p1"}],bodyOffset:{x:40,y:50}}};
 const base={...d,physicalTopology:{...d.physicalTopology!,coverings:[cover,{...cover,id:"overlay",spans:[{segmentId:"p0",from:.4,to:.6}]}]}};
 const migrated=migrateJoiningPipes(base);expect(migrated.physicalTopology!.joiningPipes).toHaveLength(1);
 expect(migrated.physicalTopology!.joiningPipes![0]!.start).toEqual({x:160,y:50});
 expect(migrated.physicalTopology!.coverings!.map(c=>c.id)).toEqual(["cover","overlay"]);expect(migrated.physicalTopology!.coverings!.map(c=>c.lengthMm)).toEqual([200,200]);
 expect(migrated.physicalTopology!.coverings![1]!.spans[0]!.from).toBeCloseTo(1/3);
 expect(migrateJoiningPipes(migrated)).toBe(migrated);expect(()=>parseHarnessDesignDocument(JSON.parse(JSON.stringify(migrated)))).not.toThrow();
});

it.each(["duplicate","missing","range","path","collision","owner","collapsed","legacy","cycle"])("rejects invalid OP %s",mutation=>{
 const d=fixture(),op=createJoiningPipe(d,[["p0"],["p1"]],"op"),t=JSON.parse(JSON.stringify({...d.physicalTopology,joiningPipes:[op]}));
 if(mutation==="duplicate")t.joiningPipes[0].members[1].segmentIds=["p0"];
 if(mutation==="missing")t.joiningPipes[0].members[1].segmentIds=["absent"];
 if(mutation==="range")t.joiningPipes[0].members[0].from=1;
 if(mutation==="path")t.joiningPipes[0].path.points=[{x:NaN,y:0}];
 if(mutation==="collision")t.joiningPipes[0].id="a";
 if(mutation==="owner")t.joiningPipes.push({...op,id:"another"});
 if(mutation==="collapsed")t.joiningPipes[0].end={...op.start};
 if(mutation==="legacy")t.joiningPipes[0].bends=[];
 if(mutation==="cycle"){t.segments.push({...t.segments[0],id:"loop",from:"b",to:"a"});t.joiningPipes[0].members[0].segmentIds.push("loop");}
 expect(()=>parsePhysicalTopology(t,d)).toThrow();
});

it("draft creation, cancellation, selection and deletion keep the electrical graph intact",()=>{
 const d=fixture(),draft=beginJoiningPipe(d,"p0")!;
 expect(()=>joiningPipeDraftTopology(d,draft)).toThrow();
 const chosen=toggleJoiningPipeMember(d,draft,"p1"),t=joiningPipeDraftTopology(d,chosen),next={...d,physicalTopology:t};
 expect(d.physicalTopology!.joiningPipes).toBeUndefined();expect(t.routes).toBe(d.physicalTopology!.routes);
 const scene=physicalTopologyScene(next),pipe=scene.find(o=>o.id===chosen.id)!;expect(pipe.pipe!.midpoints).toHaveLength(1);
 expect(pipe.pipe!.role).toBe("joining-pipe");expect(scene.filter(o=>o.metadata?.joiningPipe===chosen.id)).toHaveLength(2);
 const layers=next.views.drawing.layers.map(l=>({id:l.id,label:l.name,visible:l.visible,locked:l.locked}));
 expect(hitTestEditorScene(scene,layers,{x:250,y:0},1,"drawing")).toBe(chosen.id);
 const memberMidpoints=pipeMidpoints(scene.find(o=>o.id==="p1")!);
 expect(memberMidpoints.length).toBeGreaterThan(0);
 expect(scene.find(o=>o.id==="p1")!.pipe?.joiningTransitionMidpoints).toHaveLength(2);
 const removed=applyEditorCommand(next,{type:"remove-physical-segment",segmentId:chosen.id});expect(removed.physicalTopology!.joiningPipes).toEqual([]);expect(removed.physicalTopology!.segments).toEqual(t.segments);
});
