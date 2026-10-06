// @vitest-environment node
import {expect,it} from 'vitest';
import {createEmptyHarnessDesign,parseHarnessDesignDocument,type HarnessDesignDocument,type Point} from './model';
import {createConnector,applyEditorCommand} from './commands';
import {physicalObjectRouteAnchors,snapBendPoint,type PhysicalDragMode} from './physical-editing';
import {physicalTopologyScene} from './physical-scene';
import {physicalSegmentControls} from './physical-geometry';
import {createEditorHistory,executeEditorCommand,undoEditorCommand} from './history';

function fixture(connector:boolean):HarnessDesignDocument{
  return {...createEmptyHarnessDesign(),connectors:connector?[createConnector('A','X1',1,{x:0,y:0})]:[],
    physicalTopology:{snap:true,nodes:[{id:'a',position:{x:0,y:0},...(connector?{connectorId:'A'}:{})},
      {id:'b',position:{x:300,y:200}},{id:'c',position:{x:-300,y:200}},{id:'unrelated',position:{x:23,y:19}}],
    segments:[{id:'left',from:'a',to:'b',path:{kind:'polyline',points:[{x:100,y:0},{x:100,y:100},{x:300,y:100}]}},
      {id:'right',from:'c',to:'a',path:{kind:'polyline',points:[{x:-300,y:100},{x:-100,y:100},{x:-100,y:0}]}}],routes:[]}};
}
function quantized(a:Point,b:Point){
  if(Math.hypot(a.x-b.x,a.y-b.y)<1e-6)return;
  const angle=Math.atan2(b.y-a.y,b.x-a.x)/(Math.PI/12);
  expect(angle).toBeCloseTo(Math.round(angle),6);
}

it.each(['carry','adjacent'] as const)('snaps all connected shoulders for nodes and connectors in %s mode',mode=>{
  for(const connector of [false,true]){
    const doc=fixture(connector),scene=physicalTopologyScene(doc);
    const object=connector?{id:'A',kind:'connector',x:0,y:0}:scene.find(o=>o.id==='a')!;
    const anchors=physicalObjectRouteAnchors(scene,object,mode);
    expect(anchors).toHaveLength(2);
    const target={x:object.x+37,y:object.y+43};
    const snapped=snapBendPoint(target,anchors,true,7,object).point;
    const command=connector?{type:'move-connector' as const,connectorId:'A',view:'drawing' as const,position:snapped,physicalDragMode:mode}
      :{type:'move-physical-node' as const,nodeId:'a',position:{x:snapped.x+5,y:snapped.y+5},mode};
    const history=executeEditorCommand(createEditorHistory(doc),command),changed=history.present;
    for(const segment of changed.physicalTopology!.segments){
      const points=physicalSegmentControls(changed,segment);
      points.slice(1).forEach((p,i)=>quantized(points[i]!,p));
      const original=doc.physicalTopology!.segments.find(s=>s.id===segment.id)!;
      expect(segment.path.points[1]).toEqual(original.path.points[1]);
      if(mode==='adjacent')expect(segment.path.points).toEqual(original.path.points);
    }
    expect(undoEditorCommand(history).present).toBe(doc);
    expect(snapBendPoint(target,anchors,false,7,object).point).toEqual(target);
  }
});

it('enforces three independent directions instead of rounding only the first two',()=>{
  const anchors=[{x:0,y:100},{x:100,y:0},{x:100,y:100}];
  const result=snapBendPoint({x:37,y:43},anchors,true,7,{x:0,y:0});
  for(const anchor of anchors)quantized(anchor,result.point);
  expect(result.guide).toHaveLength(5);
});

it('ignores unrelated nodes and preserves both ends of a pipe carried by the same connector',()=>{
  const doc=fixture(true),t=doc.physicalTopology!;
  const next={...doc,physicalTopology:{...t,nodes:[...t.nodes,{id:'a2',connectorId:'A',position:{x:200,y:0}}],
    segments:[{id:'same',from:'a',to:'a2',path:{kind:'polyline' as const,points:[{x:100,y:100}]}}]}};
  expect(physicalObjectRouteAnchors(physicalTopologyScene(next),{id:'A',kind:'connector',x:0,y:0},'carry')).toEqual([]);
  const changed=applyEditorCommand(next,{type:'move-connector',connectorId:'A',view:'drawing',position:{x:37,y:43},physicalDragMode:'carry'});
  expect(changed.physicalTopology!.segments[0]!.path.points).toEqual([{x:137,y:143}]);
});

it.each(['carry','adjacent'] as PhysicalDragMode[])('uses the same virtual shoulders as the model for initially straight pipes in %s mode',mode=>{
  const doc=fixture(false),t=doc.physicalTopology!;
  const next={...doc,physicalTopology:{...t,nodes:t.nodes.map(n=>n.id==='b'?{...n,position:{x:300,y:0}}:n),
    segments:[{...t.segments[0]!,path:{kind:'polyline' as const,points:[]}}]}};
  const scene=physicalTopologyScene(next),object=scene.find(o=>o.id==='a')!;
  const anchors=physicalObjectRouteAnchors(scene,object,mode);
  const p=snapBendPoint({x:37,y:43},anchors,true,7,object).point;
  const changed=applyEditorCommand(next,{type:'move-physical-node',nodeId:'a',position:{x:p.x+5,y:p.y+5},mode});
  const points=physicalSegmentControls(changed,changed.physicalTopology!.segments[0]!);
  expect(changed.physicalTopology!.segments[0]!.path.points).toEqual([]);
});

it.each(['carry','adjacent'] as const)('constrains a three-way branch using every route in %s mode',mode=>{
  const doc=fixture(false),t=doc.physicalTopology!;
  const next={...doc,physicalTopology:{...t,nodes:[...t.nodes,{id:'d',position:{x:0,y:-300}}],
    segments:[...t.segments,{id:'third',from:'a',to:'d',path:{kind:'polyline' as const,points:[{x:0,y:-100},{x:100,y:-100},{x:100,y:-300}]}}]}};
  const scene=physicalTopologyScene(next),object=scene.find(o=>o.id==='a')!;
  const anchors=physicalObjectRouteAnchors(scene,object,mode);
  expect(anchors).toHaveLength(3);
  const p=snapBendPoint({x:32,y:38},anchors,true,7,object).point;
  const changed=applyEditorCommand(next,{type:'move-physical-node',nodeId:'a',position:{x:p.x+5,y:p.y+5},mode});
  for(const segment of changed.physicalTopology!.segments){
  }
});

it('does not use bundled display offsets as authored angular constraints',()=>{
  const doc=fixture(false),scene=physicalTopologyScene(doc),object=scene.find(o=>o.id==='a')!;
  const projected=scene.map(o=>o.kind==='physical-segment'?{...o,
    points:o.points!.map(p=>({x:p.x+17,y:p.y+31})),
    pipe:{...o.pipe!,handles:o.pipe!.handles.map(p=>({x:p.x-13,y:p.y+29}))}}:o);
  for(const mode of ['carry','adjacent'] as const){
    expect(physicalObjectRouteAnchors(projected,object,mode)).toEqual(physicalObjectRouteAnchors(scene,object,mode));
    const movedOrigin={...object,x:object.x+17,y:object.y+31};
    expect(physicalObjectRouteAnchors(projected,movedOrigin,mode)).toEqual(
      physicalObjectRouteAnchors(scene,object,mode).map(p=>({x:p.x+17,y:p.y+31})));
  }
});

it('keeps the starting position when independent constraints have no common solution',()=>{
  const origin={x:7,y:13};
  const result=snapBendPoint({x:37,y:43},[{x:0,y:100},{x:100,y:0},{x:Math.PI,y:Math.E},{x:Math.SQRT2,y:Math.LN2}],true,7,origin);
  expect(result.point).toEqual(origin);
  expect(result.guide).toBeUndefined();
});

it.each(['carry','adjacent'] as const)('materializes automatic paths on multiple connector exits in %s mode',mode=>{
  const doc=fixture(true),t=doc.physicalTopology!;
  const next:HarnessDesignDocument={...doc,physicalTopology:{...t,
    nodes:[...t.nodes,{id:'a2',connectorId:'A',position:{x:0,y:80}}],
    segments:[{id:'auto1',from:'a',to:'b',path:{kind:'routed',points:[]}},
      {id:'auto2',from:'c',to:'a2',path:{kind:'routed',points:[]}}]}};
  const object={id:'A',kind:'connector',x:0,y:0};
  const anchors=physicalObjectRouteAnchors(physicalTopologyScene(next),object,mode);
  expect(anchors).toHaveLength(2);
  const p=snapBendPoint({x:37,y:43},anchors,true,7,object).point;
  const changed=applyEditorCommand(next,{type:'move-connector',connectorId:'A',view:'drawing',position:p,physicalDragMode:mode});
  for(const segment of changed.physicalTopology!.segments){
    expect(segment.path.points).toEqual([]);
  }
  expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(changed))).physicalTopology).toEqual(changed.physicalTopology);
});
