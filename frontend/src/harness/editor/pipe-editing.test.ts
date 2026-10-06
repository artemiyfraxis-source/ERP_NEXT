// @vitest-environment node
import { describe, it, expect } from 'vitest';
import { physicalFixture } from './physical-topology-fixture';
import { ensureConnectorExits, insertPhysicalBend, physicalSegmentControls, physicalSegmentPoints, physicalWirePoints, physicalSegmentHandles, movePhysicalHandle, removePhysicalHandle } from './physical-topology';
import { hitTestEditorScene, hitTestWireRoutePoint } from './CanvasViewport';
import { designToScene } from './HarnessDesignEditor';
import { applyEditorCommand } from './commands';
import { parseHarnessDesignDocument } from './model';
import {createEditorHistory,executeEditorCommand,undoEditorCommand} from './history';

const layers=[{id:'wires',label:'Провода',visible:true,locked:false},{id:'connectors',label:'Компоненты',visible:true,locked:false}];
describe('pipe editing regressions M4-70',()=>{
 it('adds one exit per connector without replacing existing exits/routes or duplicating on reopen',()=>{
  const d=physicalFixture();expect(ensureConnectorExits(d)).toBe(d.physicalTopology);
  const old={...d,physicalTopology:undefined};const t=ensureConnectorExits(old);
  expect(t.nodes.map(n=>n.connectorId)).toEqual(['A','B','C']);
  expect(ensureConnectorExits({...old,physicalTopology:t})).toBe(t);
  expect(()=>parseHarnessDesignDocument({...old,physicalTopology:t})).not.toThrow();
 });
 it('inserts a bend on the nearest leg instead of appending it after all bends',()=>{
  const d=physicalFixture();const t={...d.physicalTopology!,snap:false,nodes:[{id:'a',position:{x:0,y:0}},{id:'b',position:{x:300,y:100}}],segments:[],routes:[]};
  const doc={...d,physicalTopology:t},s={id:'pipe',from:'a',to:'b',path: { kind: "routed" as const, points: [{x:100,y:0},{x:100,y:100}] }};
  const updated=insertPhysicalBend(doc,s,{x:40,y:0});expect(updated.path.points).toEqual([{x:40,y:0},...s.path.points]);
  expect(insertPhysicalBend(doc,s,{x:100,y:0})).toBe(s);
 });
 it('exposes automatic corners as editing handles without persisting them',()=>{
  const d=physicalFixture(),s=d.physicalTopology!.segments[0]!;
  expect(physicalSegmentPoints(d,s)).toEqual(physicalSegmentControls(d,s));
  const scene=designToScene(d,'drawing');const pipe=scene.find(o=>o.id==='S0')!;
  expect(hitTestWireRoutePoint(pipe,s.path.points[0]!,100)).toBe(physicalSegmentHandles(d,s).findIndex(h=>h.bendIndex===0));
  const automatic = {...s,path: { kind: "routed" as const, points: [] }};
  const autoScene = designToScene({...d,physicalTopology:{...d.physicalTopology!,segments:[automatic,...d.physicalTopology!.segments.slice(1)]}},'drawing').find(o=>o.id===s.id)!;
  for(const [i,corner] of autoScene.points!.slice(1,-1).entries())expect(hitTestWireRoutePoint(autoScene,corner,100)).toBe(i);
  const next=applyEditorCommand(d,{type:'set-physical-topology',topology:{...d.physicalTopology!,segments:d.physicalTopology!.segments.map(x=>x.id===s.id?{...x,path: { kind: "routed" as const, points: [{x:250,y:90}] }}:x)}});
  expect(next.wires).toEqual(d.wires);expect(next.physicalTopology!.routes).toEqual(d.physicalTopology!.routes);
  expect(physicalWirePoints(next,'W1',{x:0,y:0},{x:1,y:1})).not.toEqual(physicalWirePoints(d,'W1',{x:0,y:0},{x:1,y:1}));
 });
 it('selects the pipe across its channel instead of a wire underneath',()=>{
  const d=physicalFixture(),scene=designToScene(d,'drawing'),pipe=scene.find(o=>o.id==='S0')!;
  const points=pipe.points!,p={x:(points[0]!.x+points[1]!.x)/2,y:(points[0]!.y+points[1]!.y)/2};
  expect(hitTestEditorScene(scene,layers,p,10,'drawing')).toBe('S0');
  const dx=points[1]!.x-points[0]!.x,dy=points[1]!.y-points[0]!.y,length=Math.hypot(dx,dy);
  const edge={x:p.x-dy/length*(pipe.width/2+.6),y:p.y+dx/length*(pipe.width/2+.6)};
  expect(hitTestEditorScene(scene,layers,edge,10,'drawing')).toBe('S0');
  expect(pipe.pipe!.wireIds).toEqual(['W1','W2']);
  const wire={...scene.find(o=>o.id==='W1')!,metadata:{},points:pipe.points,visibleWireStrokes:undefined};
  expect(hitTestEditorScene([pipe,wire],layers,p,10,'drawing')).toBe('S0');
 });
});

it('moves an authored bend in place without creating another bend',()=>{
 const d=physicalFixture(),segment=d.physicalTopology!.segments[0]!;
 const handles=physicalSegmentHandles(d,segment),index=handles.findIndex(h=>h.bendIndex===0);
 expect(index).toBe(0);
 const point={x:handles[index]!.point.x+85,y:handles[index]!.point.y-40};
 const updated=movePhysicalHandle(d,segment,index,point);
 expect(updated.path.points).toHaveLength(segment.path.points.length);
 const history=executeEditorCommand(createEditorHistory(d),{type:'set-physical-topology',topology:{...d.physicalTopology!,segments:d.physicalTopology!.segments.map(s=>s.id===segment.id?updated:s)}});
 const moved=applyEditorCommand(history.present,{type:'move-connector',connectorId:'A',view:'drawing',position:{x:3,y:2}});
 const reopened=parseHarnessDesignDocument(JSON.parse(JSON.stringify(moved)));
 const saved=reopened.physicalTopology!.segments.find(s=>s.id===segment.id)!;
 expect(saved.path.points).toEqual([point]);
 expect(physicalSegmentPoints(reopened,saved)).toContainEqual(point);
 expect(reopened.wires).toEqual(parseHarnessDesignDocument(JSON.parse(JSON.stringify(d))).wires);expect(reopened.physicalTopology!.routes).toEqual(d.physicalTopology!.routes);
 expect(undoEditorCommand(history).present).toEqual(d);
});

it('keeps both visible and saved corner counts stable through repeated drags (C1)',()=>{
 const d=physicalFixture(),original=d.physicalTopology!.segments[0]!;
 let segment=insertPhysicalBend(d,original,{x:170,y:65});
 const otherBend=segment.path.points[1]!;
 for(let i=0;i<60;i++){
  const point={x:80+i*7.3,y:220-i*4.8};
  segment=movePhysicalHandle(d,segment,0,point);
  expect(segment.path.points).toEqual([point,otherBend]);
  expect(physicalSegmentPoints(d,segment)).toEqual([physicalSegmentControls(d,segment)[0],point,otherBend,physicalSegmentControls(d,segment).at(-1)]);
  expect(physicalSegmentHandles(d,segment)).toHaveLength(2);
 }
 expect(d.physicalTopology!.segments[0]).toBe(original);
 segment=removePhysicalHandle(d,segment,1);
 segment=removePhysicalHandle(d,segment,0);
 expect(segment.path.points).toEqual([]);
 expect(physicalSegmentHandles(d,segment)).toEqual([]);
 expect(physicalSegmentPoints(d,segment)).toEqual(physicalSegmentPoints(d,{...original,path: { kind: "routed" as const, points: [] }}));
});
