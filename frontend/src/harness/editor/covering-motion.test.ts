// @vitest-environment node
import { expect, it } from "vitest";
import { createEmptyHarnessDesign, parseHarnessDesignDocument, type HarnessDesignDocument } from "./model";
import { moveBundleCovering, bundleSpanEdgeVisible } from "./covering-motion";
import { coveringScene, moveCovering } from "./covering-layout";
import { pipeBundleDisplaySamples, unprojectPipeBundlePoint } from "./pipe-bundle-projection";
import { branchPhysicalSegment, connectPhysicalNodeToSegment } from "./physical-topology";
import { coveringGrips, coveringSurfaces } from "./covering-renderer";
import { createEditorHistory, executeEditorCommand, undoEditorCommand } from "./history";

function fixture():HarnessDesignDocument {
  return {...createEmptyHarnessDesign(),drawingDocuments:{tables:[],leaders:[],bomOrder:[],bendRadius:0},physicalTopology:{snap:false,
    nodes:[{id:'a',position:{x:0,y:0}},{id:'j',position:{x:300,y:0}},{id:'b',position:{x:600,y:0}},
      {id:'p',position:{x:0,y:100}},{id:'q',position:{x:600,y:100}},{id:'tip',position:{x:450,y:200}}],
    segments:[{id:'s0',from:'a',to:'j',path:{kind:'polyline',points:[]},width:10},
      {id:'s1',from:'j',to:'b',path:{kind:'polyline',points:[]},width:10},
      {id:'s2',from:'p',to:'q',path:{kind:'polyline',points:[]},width:10}],routes:[],
    coverings:[{id:'group',name:'Общая',width:0,color:'#334455',lengthMm:200,lengthMode:'manual',
      spans:[{segmentId:'s0',from:.2,to:1},{segmentId:'s1',from:0,to:.6}],
      bundle:{mode:'flat',members:[{kind:'segment',id:'s0',continuationIds:['s1']},{kind:'segment',id:'s2'}]}}]}};
}

it('moves the whole sleeve freely from either fragment and undoes once',()=>{
  const doc=fixture(),c=doc.physicalTopology!.coverings![0]!,before=JSON.stringify(doc);
  const moved=moveCovering(doc,c.id,0,'body',{x:240,y:0},{x:300,y:0})!;
  expect(moved.spans).toEqual(c.spans);
  expect(moved.bundle?.bodyOffset).toEqual({x:60,y:0});
  expect(moveCovering(doc,c.id,1,'body',{x:360,y:0},{x:420,y:0})).toEqual(moved);
  expect(moved.lengthMm).toBe(200);
  const history=executeEditorCommand(createEditorHistory(doc),{type:'set-physical-topology',topology:{...doc.physicalTopology!,coverings:[moved]}});
  expect(undoEditorCommand(history).present).toBe(doc);
  expect(JSON.stringify(doc)).toBe(before);
});

it('moves a grouped sleeve laterally without forcing it back onto the supporting pipe',()=>{
  const doc=fixture(),c=doc.physicalTopology!.coverings![0]!;
  const moved=moveCovering(doc,c.id,0,'body',{x:240,y:0},{x:240,y:45})!;
  expect(moved.spans).toEqual(c.spans);
  expect(moved.bundle?.bodyOffset).toEqual({x:0,y:45});
  const next={...doc,physicalTopology:{...doc.physicalTopology!,coverings:[moved]}};
  const sleeve=coveringScene(next).find(item=>item.id===c.id)!;
  expect(sleeve.paths?.[0]?.some(point=>point.y>40)).toBe(true);
  const wires=pipeBundleDisplaySamples(next,'s2')!;
  expect(wires[0]?.point.y).toBeCloseTo(100);
  expect(wires.some(sample=>sample.point.y<90)).toBe(true);
  expect(wires.some(sample=>sample.point.y>40&&sample.point.y<60)).toBe(true);
  const saved=parseHarnessDesignDocument(JSON.parse(JSON.stringify(next)));
  expect(saved.physicalTopology?.coverings?.[0]?.bundle?.bodyOffset).toEqual({x:0,y:45});
  expect(pipeBundleDisplaySamples(saved,'s2')).toEqual(wires);
});

it('moves beyond a fragment boundary without changing the covered supports',()=>{
  const doc=fixture(),t=doc.physicalTopology!,c={...t.coverings![0]!,spans:[{segmentId:'s0',from:.5,to:.9}]};
  const initial={...doc,physicalTopology:{...t,coverings:[c]}};
  const moved=moveCovering(initial,c.id,0,'body',{x:180,y:0},{x:480,y:0})!;
  expect(moved.spans).toEqual(c.spans);
  expect(moved.bundle?.bodyOffset).toEqual({x:300,y:0});
  const next={...doc,physicalTopology:{...t,coverings:[moved]}};
  expect(moveCovering(next,c.id,0,'body',{x:480,y:0},{x:180,y:0})!.bundle?.bodyOffset).toEqual({x:0,y:0});
  const distant=moveCovering(initial,c.id,0,'body',{x:180,y:0},{x:900,y:0})!;
  expect(distant.spans).toEqual(c.spans);
  expect(distant.bundle?.bodyOffset).toEqual({x:720,y:0});
});

it('resizes the outer edge across a split and exposes no internal grip',()=>{
  const doc=fixture(),c=doc.physicalTopology!.coverings![0]!;
  expect(bundleSpanEdgeVisible(doc,c,0,'to')).toBe(false);
  expect(bundleSpanEdgeVisible(doc,c,1,'from')).toBe(false);
  expect(coveringGrips(coveringScene(doc)[0]!).filter(g=>!g.part.startsWith("transition-")).map(g=>[g.spanIndex,g.part])).toEqual([[0,'from'],[1,'to']]);
  expect(coveringSurfaces(coveringScene(doc)[0]!).map(s=>[s.openStart,s.openEnd])).toEqual([[undefined,true],[true,undefined]]);
  const smaller=moveBundleCovering(doc,c,1,'to',{x:480,y:0},{x:240,y:0},0)!;
  expect(smaller.spans).toEqual([{segmentId:'s0',from:.2,to:.8}]);
  const next={...doc,physicalTopology:{...doc.physicalTopology!,coverings:[smaller]}};
  expect(moveBundleCovering(next,smaller,0,'to',{x:240,y:0},{x:480,y:0},0)!.spans).toEqual(c.spans);
  expect(moveBundleCovering(doc,c,0,'body',{x:240,y:0},{x:240,y:0},0)).toBe(c);
});

it('maps a context hit to the original station and keeps a new T branch on the displayed pipe',()=>{
  const doc=fixture(),screen={x:450,y:5},original=unprojectPipeBundlePoint(doc,'s2',screen);
  expect(original).toEqual({x:450,y:100});
  const t=branchPhysicalSegment(doc,'s2',original,{junction:'cut',continuation:'tail',tip:'end',branch:'branch'});
  const changed={...doc,physicalTopology:t};
  const tail=pipeBundleDisplaySamples(changed,'tail')![0]!.point;
  expect(tail).toEqual(screen);
  expect(pipeBundleDisplaySamples(changed,'branch')![0]!.point).toEqual(screen);
  expect(t.nodes.find(n=>n.id==='cut')!.position).toEqual(original);
});

it('connects an existing node at the projected station without mutating the saved member axis',()=>{
  const doc=fixture(),screen={x:420,y:5};
  const topology=connectPhysicalNodeToSegment(doc,'tip','s2',unprojectPipeBundlePoint(doc,'s2',screen),
    {junction:'cut',segment:'new',continuation:'tail'});
  const changed={...doc,physicalTopology:topology};
  expect(topology.nodes.find(n=>n.id==='cut')!.position).toEqual({x:420,y:100});
  expect(pipeBundleDisplaySamples(changed,'new')!.at(-1)!.point).toEqual(screen);
  expect(doc.physicalTopology!.segments.find(s=>s.id==='s2')!.path.points).toEqual([]);
});

it('moves all independent supports with the sleeve without changing their intervals',()=>{
  const base=fixture(),t=base.physicalTopology!,c={...t.coverings![0]!,spans:[...t.coverings![0]!.spans,{segmentId:'s2',from:.4,to:.9}]};
  const doc={...base,physicalTopology:{...t,coverings:[c]}};
  const moved=moveCovering(doc,c.id,0,'body',{x:180,y:0},{x:300,y:0})!;
  expect(moved.spans).toEqual(c.spans);
  expect(moved.bundle?.bodyOffset).toEqual({x:120,y:0});
  expect(moved.lengthMm).toBe(c.lengthMm);
});

it('keeps a reversed support anchored while moving the sleeve',()=>{
  const base=fixture(),t=base.physicalTopology!,c={...t.coverings![0]!,spans:[...t.coverings![0]!.spans,{segmentId:'s2',from:.2,to:.8}]};
  const doc={...base,physicalTopology:{...t,segments:t.segments.map(s=>s.id==='s2'?{...s,from:s.to,to:s.from}:s),coverings:[c]}};
  const moved=moveCovering(doc,c.id,0,'body',{x:180,y:0},{x:240,y:0})!;
  expect(moved.spans).toEqual(c.spans);
  expect(moved.bundle?.bodyOffset).toEqual({x:60,y:0});
});

function unequalSupports():HarnessDesignDocument {
  const base=fixture(),t=base.physicalTopology!;
  return {...base,physicalTopology:{...t,
    nodes:t.nodes.map(n=>n.id==='q'?{...n,position:{x:1200,y:100}}:n),
    segments:t.segments.map(s=>s.id==='s2'?{...s,from:s.to,to:s.from}:s),
    coverings:[{...t.coverings![0]!,spans:[...t.coverings![0]!.spans,{segmentId:'s2',from:.2,to:.9}]}]}};
}

it('resizes corresponding ends of unequal reversed supports across a split and undoes once',()=>{
  const doc=unequalSupports(),t=doc.physicalTopology!,c=t.coverings![0]!,before=JSON.stringify(doc);
  const resized=moveCovering(doc,c.id,1,'to',{x:480,y:0},{x:240,y:0},0)!;
  expect(resized.spans).toHaveLength(2);
  expect(resized.spans[0]).toEqual({segmentId:'s0',from:.2,to:.8});
  expect(resized.spans[1]!.from).toBeCloseTo(.6);
  expect(resized.spans[1]!.to).toBeCloseTo(.9);
  expect(resized.lengthMm).toBe(200);
  const history=executeEditorCommand(createEditorHistory(doc),{type:'set-physical-topology',topology:{...t,coverings:[resized]}});
  expect(undoEditorCommand(history).present).toBe(doc);
  const restored=moveCovering(history.present,c.id,0,'to',{x:240,y:0},{x:480,y:0},0)!;
  expect(restored.spans[0]).toEqual(c.spans[0]);
  expect(restored.spans[1]!.to).toBeCloseTo(.6);
  expect(restored.spans[2]!.from).toBeCloseTo(.2);
  expect(JSON.stringify(doc)).toBe(before);
});

it('clamps a shared edge at the first support boundary and preserves the opposite ends',()=>{
  const doc=unequalSupports(),t=doc.physicalTopology!,original=t.coverings![0]!;
  const c={...original,spans:[...original.spans.slice(0,2),{segmentId:'s2',from:.05,to:.9}]};
  const next={...doc,physicalTopology:{...t,coverings:[c]}};
  const resized=moveCovering(next,c.id,1,'to',{x:480,y:0},{x:600,y:0},0)!;
  expect(resized.spans[1]!.to).toBeCloseTo(.7);
  expect(resized.spans[2]!.from).toBeCloseTo(0);
  expect(resized.spans[2]!.to).toBe(.9);
  expect(resized.spans[0]!.from).toBe(.2);
});

it('moves different-length supports without changing their interval lengths',()=>{
  const doc=unequalSupports(),c=doc.physicalTopology!.coverings![0]!;
  const moved=moveCovering(doc,c.id,0,'body',{x:180,y:0},{x:240,y:0},0)!;
  expect(moved.spans).toEqual(c.spans);
  expect(moved.bundle?.bodyOffset).toEqual({x:60,y:0});
  expect((moved.spans[2]!.to-moved.spans[2]!.from)*1200).toBeCloseTo(840);
});

it('snaps the common start to a split while updating the opposite end of the reversed support',()=>{
  const doc=unequalSupports(),c=doc.physicalTopology!.coverings![0]!;
  const resized=moveCovering(doc,c.id,0,'from',{x:60,y:0},{x:296,y:0},10)!;
  expect(resized.spans).toHaveLength(2);
  expect(resized.spans[0]!.segmentId).toBe('s1');
  expect(resized.spans[0]!.from).toBe(0);
  expect(resized.spans[0]!.to).toBe(.6);
  expect(resized.spans[1]!.from).toBe(.2);
  expect(resized.spans[1]!.to).toBeCloseTo(.5);
  const scene=coveringScene({...doc,physicalTopology:{...doc.physicalTopology!,coverings:[resized]}})[0]!;
  expect(coveringGrips(scene).filter(g=>!g.part.startsWith("transition-")).map(g=>g.point.x)).toEqual([300,480]);
});

it('keeps disjoint intervals separate and limits shrinking by the shortest corresponding interval',()=>{
  const doc=unequalSupports(),t=doc.physicalTopology!,c={...t.coverings![0]!,spans:[
    {segmentId:'s0',from:.2,to:.6},{segmentId:'s1',from:.2,to:.8},
    {segmentId:'s2',from:.2,to:.4},{segmentId:'s2',from:.75,to:.8}]};
  const next={...doc,physicalTopology:{...t,coverings:[c]}};
  const resized=moveCovering(next,c.id,0,'from',{x:60,y:0},{x:179,y:0},0)!;
  const primary=resized.spans.find(s=>s.segmentId==='s0')!;
  const reverse=resized.spans.filter(s=>s.segmentId==='s2');
  expect(primary.from).toBeCloseTo(.298);
  expect(primary.to).toBe(.6);
  expect(reverse[0]).toEqual(c.spans[2]);
  expect(reverse[1]!.from).toBe(.75);
  expect(reverse[1]!.to).toBeCloseTo(.751);
  expect(resized.spans.find(s=>s.segmentId==='s1')).toEqual(c.spans[1]);
});

it('keeps opposite edge bindings during resize and follows the bound node afterwards',()=>{
  const doc=unequalSupports(),t=doc.physicalTopology!,original=t.coverings![0]!;
  const c={...original,spans:[{segmentId:'s0',from:0,to:1,fromAnchor:0},original.spans[1]!,
    {segmentId:'s2',from:.2,to:1,toAnchor:1}]};
  const next={...doc,physicalTopology:{...t,coverings:[c]}};
  const resized=moveCovering(next,c.id,1,'to',{x:480,y:0},{x:420,y:0},0)!;
  expect(resized.spans[0]!.fromAnchor).toBe(0);
  expect(resized.spans[2]!.toAnchor).toBe(1);
  const changed={...next,physicalTopology:{...next.physicalTopology,coverings:[resized],
    nodes:next.physicalTopology.nodes.map(n=>n.id==='a'?{...n,position:{x:-40,y:0}}:n)}};
  expect(coveringGrips(coveringScene(changed)[0]!)[0]!.point.x).toBe(-40);
});
