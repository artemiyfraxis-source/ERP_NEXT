// @vitest-environment node
import {expect,it} from 'vitest';
import {pipeBundleSections} from './pipe-bundle-section';
import {physicalFixture} from './physical-topology-fixture';
import {splitPhysicalSegment} from './physical-topology';
import {coveringScene} from './covering-layout';
import type {HarnessDesignDocument} from './model';
function fixture():HarnessDesignDocument {
 const d=physicalFixture();return {...d,physicalTopology:{...d.physicalTopology!,snap:false,routes:[],segments:d.physicalTopology!.segments.map(s=>({...s,width:10})),coverings:[
 {id:'group',name:'Group',width:0,color:'#123456',lengthMm:null,spans:[{segmentId:'S0',from:.2,to:.8}],bundle:{mode:'flat',members:['S0','S1','S2'].map(id=>({kind:'segment',id}))}}
 ]}};
}
it('computes flat and round projections without changing document or wire routes',()=>{
 const d=fixture(),before=JSON.stringify(d),flat=pipeBundleSections(d).get('group')!;
 expect(flat.width).toBe(30.5);expect([...flat.leafOffsets.values()].map(p=>p.offset)).toEqual([-10,0,10]);
 const round={...d,physicalTopology:{...d.physicalTopology!,coverings:d.physicalTopology!.coverings!.map(c=>({...c,bundle:{...c.bundle!,mode:'round' as const}}))}};
 expect(pipeBundleSections(round).get('group')!.width).toBeLessThan(flat.width);
 expect(coveringScene(d)[0]!.width).toBe(flat.width);
 expect(JSON.stringify(d)).toBe(before);
});
it('counts consecutive fragments once, including after a split',()=>{
 const d=fixture(),t=splitPhysicalSegment(d,'S0',1,'cut','tail');
 const section=pipeBundleSections({...d,physicalTopology:t}).get('group')!;
 expect(section.width).toBe(pipeBundleSections(d).get('group')!.width);
 expect(section.members).toHaveLength(3);
 expect(section.leafOffsets.get('tail')).toEqual(section.leafOffsets.get('S0'));
});
it('packs nested groups as complete shells independently of paint order and scales proportionally',()=>{
 const d=fixture(),t=d.physicalTopology!,group=t.coverings![0]!;
 const inner={...group,id:'inner',bundle:{...group.bundle!,members:group.bundle!.members.slice(0,2)}};
 const outer={...group,bundle:{mode:'round' as const,members:[{kind:'covering' as const,id:'inner'},group.bundle!.members[2]!]}};
 const doc={...d,physicalTopology:{...t,coverings:[outer,inner]}};
 const result=pipeBundleSections(doc);
 expect(result.get('group')!.members[0]!.diameter).toBe(result.get('inner')!.diameter);
 expect(result.get('group')!.leafOffsets.size).toBe(3);
 expect([...pipeBundleSections({...doc,physicalTopology:{...doc.physicalTopology,coverings:[inner,outer]}})]).toEqual([...result]);
 const scaled=pipeBundleSections({...doc,drawingDocuments:{tables:[],leaders:[],bomOrder:[],physicalScale:2}});
 expect(scaled.get('group')!.width).toBeCloseTo(result.get('group')!.width*2);
});
