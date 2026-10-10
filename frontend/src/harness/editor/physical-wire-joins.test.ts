// @vitest-environment node
import {expect,it} from 'vitest';
import {createConnector,createWire} from './commands';
import {createEmptyHarnessDesign,type HarnessDesignDocument} from './model';
import {physicalWireDisplayPaths,physicalWirePoints} from './physical-wire-geometry';
import {drawingRouteCommands} from './drawing-route-path';
import {physicalFixture} from './physical-topology-fixture';

function fixture():HarnessDesignDocument {
  return {...createEmptyHarnessDesign(),connectors:[createConnector('A','X1',1,{x:0,y:0}),createConnector('B','X2',1,{x:400,y:200})],
    wires:[createWire('W',{connectorId:'A',contactId:'A:contact:1'},{connectorId:'B',contactId:'B:contact:1'})],
    physicalTopology:{snap:false,nodes:[{id:'a',connectorId:'A',position:{x:0,y:0}},
      {id:'j',position:{x:200,y:0}},{id:'k',position:{x:200,y:200}},{id:'b',connectorId:'B',position:{x:0,y:0}}],
      segments:[{id:'s1',from:'a',to:'j',path:{kind:'polyline',points:[]}},
        {id:'s2',from:'j',to:'k',path:{kind:'polyline',points:[]}},
        {id:'s3',from:'k',to:'b',path:{kind:'polyline',points:[]}}],
      routes:[{wireId:'W',steps:['s1','s2','s3'].map(segmentId=>({segmentId,reverse:false}))}]}};
}
const start={x:0,y:0},end={x:400,y:200};

it('traces connected legs once and rounds the actual common corner',()=>{
  const doc=fixture(),before=JSON.stringify(doc),paths=physicalWireDisplayPaths(doc,'W',start,end)!;
  expect(paths).toEqual([[start,{x:200,y:0},{x:200,y:200},end]]);
  const sharp=drawingRouteCommands(paths[0]!,0),rounded=drawingRouteCommands(paths[0]!,20);
  expect(sharp.filter(c=>c.kind==='line')).toHaveLength(3);
  expect(rounded.filter(c=>c.kind==='arc')).toHaveLength(2);
  expect(rounded.filter(c=>c.kind==='arc').map(c=>[c.cornerX,c.cornerY])).toEqual([[200,0],[200,200]]);
  expect(physicalWirePoints(doc,'W',start,end)).toEqual(paths[0]);
  expect(JSON.stringify(doc)).toBe(before);
});

it.each([0,1,2])('does not bridge hidden leg %s, including reversed topology',hidden=>{
  const doc=fixture(),t=doc.physicalTopology!;
  for(const reverse of [false,true]){
    const next={...doc,physicalTopology:{...t,segments:t.segments.map((s,i)=>({...s,showWires:i!==hidden,
      ...(reverse?{from:s.to,to:s.from}:{} )})),routes:t.routes.map(r=>({...r,steps:r.steps.map(s=>({...s,reverse}))}))}};
    const paths=physicalWireDisplayPaths(next,'W',start,end)!;
    const forbidden=[[start,{x:200,y:0}],[{x:200,y:0},{x:200,y:200}],[{x:200,y:200},end]][hidden]!;
    expect(paths).toHaveLength(2);
    expect(paths.flatMap(path=>path.slice(1).map((p,i)=>[path[i],p]))).not.toContainEqual(forbidden);
    if(hidden===1)expect(paths).toEqual([[start,{x:200,y:0}],[{x:200,y:200},end]]);
  }
});

it('draws conductor exits through an extended nylon sleeve without changing its route',()=>{
 const base=physicalFixture(),topology=base.physicalTopology!;
 const covered={...base,physicalTopology:{...topology,coverings:[{
   id:'nylon',name:'Нейлонка',kind:'nylon' as const,width:0,color:'#b19c77',lengthMm:null,
   spans:[{segmentId:'S0',from:-10,to:.4}],
 }]}};
 const wire=covered.wires.find(item=>item.id==='W1')!;
 const start=covered.connectors.find(connector=>connector.id===wire.from.connectorId)!.positions.drawing;
 const end=covered.connectors.find(connector=>connector.id===wire.to.connectorId)!.positions.drawing;
 const bare=physicalWireDisplayPaths(base,wire.id,start,end)!;
 const sleeved=physicalWireDisplayPaths(covered,wire.id,start,end)!;
 expect(sleeved[0]![0]).toEqual(start);
 expect(sleeved[0]!.length).toBeGreaterThan(bare[0]!.length);
 expect(sleeved.flat().some(point=>point.x>100&&point.x<190&&point.y<100)).toBe(true);
 expect(covered.physicalTopology!.coverings).toHaveLength(1);
 expect(covered.physicalTopology!.routes).toBe(topology.routes);
});
