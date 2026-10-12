// @vitest-environment node
import {expect,it} from "vitest";
import {initialLinearLeader,pointAlongDrawingPaths} from "./drawing-leader-placement";
import {physicalFixture} from "./physical-topology-fixture";
import {addDrawingPositions,drawingDocumentScene,moveDrawingAnnotation} from "./drawing-documents";
import {applyEditorCommand} from "./commands";
import {parseHarnessDesignDocument} from "./model";
import {physicalSegmentPoints} from "./physical-geometry";

it("uses total arc length and never counts the gap between disjoint covering spans",()=>{
 expect(pointAlongDrawingPaths([[{x:0,y:0},{x:30,y:0},{x:30,y:170}]],.2)?.point).toEqual({x:30,y:10});
 expect(pointAlongDrawingPaths([[{x:0,y:0},{x:10,y:0}],[{x:1000,y:0},{x:1090,y:0}]],.2)?.point).toEqual({x:1010,y:0});
 expect(pointAlongDrawingPaths([[{x:0,y:0},{x:0,y:0}]],.2)).toBeNull();
});

function straight(){
 const d=physicalFixture();
 return {...d,physicalTopology:{...d.physicalTopology!,snap:false,nodes:[{id:"left",position:{x:0,y:0},connectorId:"A"},{id:"right",position:{x:350,y:-500},connectorId:"B"}],segments:[{id:"pipe",from:"left",to:"right",path:{kind:"polyline" as const,points:[]}}],routes:[]}};
}
it("chooses the left connector independently of the pipe's stored direction",()=>{
 const d=straight(),s=d.physicalTopology.segments[0]!,path=physicalSegmentPoints(d,s);
 expect(path).toEqual([{x:0,y:0},{x:1000,y:0}]);
 expect(initialLinearLeader(d,"pipe")?.point).toEqual({x:200,y:0});
 const reversed={...d,physicalTopology:{...d.physicalTopology,segments:[{...s,from:s.to,to:s.from}]}};
 expect(initialLinearLeader(reversed,"pipe")?.point).toEqual({x:200,y:0});
});
it("prioritizes the only connector even when it is at the right end",()=>{
 const d=straight(),onlyRight={...d,physicalTopology:{...d.physicalTopology,nodes:d.physicalTopology.nodes.map(n=>n.id==="left"?{id:n.id,position:n.position}:n)}};
 expect(initialLinearLeader(onlyRight,"pipe")?.point).toEqual({x:800,y:0});
});
it("positions a partial covering at one fifth of its own length",()=>{
 const d=straight(),covered={...d,physicalTopology:{...d.physicalTopology,coverings:[{id:"cover",name:"Термоусадка",width:12,color:"black",lengthMm:null,spans:[{segmentId:"pipe",from:.3,to:.8}]}]}};
 expect(initialLinearLeader(covered,"cover")?.point).toEqual({x:400,y:0});
});
it("uses the same left-side anchor for a wire and cable when electrical endpoints reverse",()=>{
 const d=physicalFixture(),wire=d.wires[0]!;
 const normal=initialLinearLeader(d,wire.id)!;
 const reversed={...d,wires:d.wires.map(w=>w.id===wire.id?{...w,from:w.to,to:w.from,drawingRoute:[...w.drawingRoute].reverse()}:w),physicalTopology:{...d.physicalTopology!,routes:d.physicalTopology!.routes.map(r=>r.wireId===wire.id?{...r,steps:[...r.steps].reverse().map(s=>({...s,reverse:!s.reverse}))}:r)}};
 expect(initialLinearLeader(reversed,wire.id)?.point.x).toBeCloseTo(normal.point.x);
 expect(initialLinearLeader(reversed,wire.id)?.point.y).toBeCloseTo(normal.point.y);
 const cable={id:"cable",memberWireIds:[wire.id],lengthMm:null,endCorrectionFromMm:0,endCorrectionToMm:0,cutRoundingStepMm:1};
 expect(initialLinearLeader({...d,cables:[cable]},"cable")).toEqual(normal);
});
it("creates a leader at the chosen material cross section and retains manual edits and serialization",()=>{
 const base=straight(),d={...base,physicalTopology:{...base.physicalTopology,coverings:[{id:"cover",name:"Термоусадка",width:12,color:"#333333",lengthMm:null,spans:[{segmentId:"pipe",from:0,to:1}]}]}},documents=addDrawingPositions(d),leader=documents.leaders.find(l=>l.objectId==="cover")!;
 expect(documents.leaders.some(l=>l.objectId==="pipe")).toBe(false);
 const saved=applyEditorCommand(d,{type:"set-drawing-documents",documents});
 expect(drawingDocumentScene(saved).find(o=>o.id===leader.id)!.points![0]!.x).toBeCloseTo(200);
 const changed=moveDrawingAnnotation(saved,leader.id+":anchor",{x:696,y:-4})!;
 const manual={...saved,drawingDocuments:changed};
 expect(addDrawingPositions(manual).leaders.find(l=>l.id===leader.id)).toEqual(changed.leaders.find(l=>l.id===leader.id));
 expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(manual))).drawingDocuments).toEqual(changed);
});
