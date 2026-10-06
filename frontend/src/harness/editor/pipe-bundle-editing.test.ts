// @vitest-environment node
import {expect,it} from "vitest";
import {physicalFixture} from "./physical-topology-fixture";
import {parseHarnessDesignDocument,type HarnessDesignDocument} from "./model";
import {splitPhysicalSegment,removePhysicalSegment,prunePhysicalTopology} from "./physical-topology";
import {pipeMemberSegments,resolvePipeBundles} from "./pipe-bundle-model";
import {prunePipeBundles} from "./pipe-bundle-editing";
import {createEditorHistory,executeEditorCommand,undoEditorCommand} from "./history";
import type {PhysicalCovering} from "./physical-coverings";
import {buildHarnessSelectionIndex,resolveHarnessSelection} from "./harness-selection";

const member=(id:string)=>({kind:"segment" as const,id});
const cover=(id:string,members:NonNullable<PhysicalCovering['bundle']>['members']):PhysicalCovering=>({id,name:id,width:0,color:'#778899',lengthMm:null,spans:[{segmentId:'S0',from:0,to:1}],bundle:{mode:'flat',members}});
function fixture():HarnessDesignDocument {
 const d=physicalFixture();
 return {...d,physicalTopology:{...d.physicalTopology!,snap:false,segments:d.physicalTopology!.segments.map(s=>s.id==='S0'?{...s,path:{kind:'polyline',points:[{x:240,y:120},{x:300,y:160}]}}:s),
 coverings:[cover('outer',[{kind:'covering',id:'inner'},member('S2')]),cover('inner',[member('S0'),member('S1')])]}};
}
it('splits a nested participant longitudinally and retains memberships through JSON and Undo',()=>{
 const d=fixture(),t=splitPhysicalSegment(d,'S0',1,'split','tail');
 expect(t.coverings![1]!.bundle!.members).toEqual([{kind:'segment',id:'S0',continuationIds:['tail']},member('S1')]);
 expect(t.coverings![0]!.bundle!.members).toEqual(d.physicalTopology!.coverings![0]!.bundle!.members);
 expect(t.coverings![0]!.spans.map(s=>s.segmentId)).toEqual(['S0','tail']);
 const h=executeEditorCommand(createEditorHistory(d),{type:'set-physical-topology',topology:t});
 expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(h.present))).physicalTopology).toEqual(t);
 expect(undoEditorCommand(h).present).toBe(d);
 expect(h.present.wires).toBe(d.wires);
 const again=splitPhysicalSegment(h.present,'tail',1,'split2','tail2');
 expect(again.coverings![1]!.bundle!.members[0]).toEqual({kind:'segment',id:'S0',continuationIds:['tail','tail2']});
 expect(()=>parseHarnessDesignDocument({...d,physicalTopology:again})).not.toThrow();
});
it('promotes a surviving continuation after deleting the head without adding a parallel member',()=>{
 const d=fixture(),split=splitPhysicalSegment(d,'S0',1,'split','tail');
 const t=removePhysicalSegment({...d,physicalTopology:split},'S0');
 expect(t.coverings![1]!.bundle!.members[0]).toEqual(member('tail'));
 expect(()=>parseHarnessDesignDocument({...d,physicalTopology:t})).not.toThrow();
});
it('dissolves a one-member nested group and keeps the parent and all surviving materials',()=>{
 const d=fixture(),t=removePhysicalSegment(d,'S1');
 expect(t.coverings!.map(c=>c.id)).toEqual(['outer','inner']);
 expect(t.coverings![1]!.bundle).toBeUndefined();
 expect(t.coverings![0]!.bundle!.members).toEqual([member('S0'),member('S2')]);
 expect(()=>parseHarnessDesignDocument({...d,physicalTopology:t})).not.toThrow();
});
it('removes dependent memberships when a child covering or connector is removed',()=>{
 const d=fixture();
 const covers=prunePipeBundles(d.physicalTopology!.coverings!.filter(c=>c.id!=='inner'),d.physicalTopology!.segments);
 expect(covers).toEqual([]); // parent support disappeared with its only supporting member
 const pruned=prunePhysicalTopology({...d,connectors:d.connectors.filter(c=>c.id!=='A'),wires:[]});
 expect(pruned.physicalTopology!.coverings).toEqual([]);
 expect(()=>parseHarnessDesignDocument(pruned)).not.toThrow();
});
it('rejects missing, repeated, disconnected and closed continuations',()=>{
 const d=fixture();
 for(const continuationIds of [[],['missing'],['S0'],['S2','S1'],['S1','S0']]) {
  const c=cover('bad',[{...member('S0'),continuationIds},member('S2')]);
  expect(()=>parseHarnessDesignDocument({...d,physicalTopology:{...d.physicalTopology!,coverings:[c]}})).toThrow();
 }
});
it('resolves all fragments for highlighting while preserving a single cross-section member',()=>{
 const d=fixture(),t=splitPhysicalSegment(d,'S0',1,'split','tail');
 const resolved=resolvePipeBundles(t.coverings!,new Set(t.segments.map(s=>s.id)));
 expect(resolved.get('outer')).toEqual(['S0','tail','S1','S2']);
 const first=t.coverings![1]!.bundle!.members[0]!;
 expect(first.kind==='segment'&&pipeMemberSegments(first)).toEqual(['S0','tail']);
 const selected=resolveHarnessSelection(buildHarnessSelectionIndex({...d,physicalTopology:t}),['outer']);
 expect(selected.wireIds.sort()).toEqual(['W1','W2','W3']);
});
it('drops a broken longitudinal member instead of treating disconnected remnants as parallel pipes',()=>{
 const d=fixture(),t=splitPhysicalSegment(d,'S0',1,'split','tail');
 const next=splitPhysicalSegment({...d,physicalTopology:t},'tail',1,'split2','tail2');
 const removed=removePhysicalSegment({...d,physicalTopology:next},'tail');
 expect(removed.coverings).toEqual([]);
 expect(removed.segments.map(s=>s.id)).toContain('S0');
 expect(removed.segments.map(s=>s.id)).toContain('tail2');
 expect(()=>parseHarnessDesignDocument({...d,physicalTopology:removed})).not.toThrow();
});
