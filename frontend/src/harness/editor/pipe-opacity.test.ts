// @vitest-environment node
import {expect,it} from "vitest";
import {createEmptyHarnessDesign} from "./model";
import {createJoiningPipe} from "./physical-joining-pipes";
import {physicalTopologyScene} from "./physical-scene";
import {parseHarnessDesignDocument} from "./model";
import {createEditorHistory,executeEditorCommand,undoEditorCommand} from "./history";
import {drawEditorSceneObject} from "./CanvasViewport";

it("saves and renders one drawing transparency for P and OP without changing geometry",()=>{
 const empty=createEmptyHarnessDesign();
 const topology={snap:false,nodes:[
  {id:"a",position:{x:0,y:0}},{id:"b",position:{x:600,y:0}},
  {id:"c",position:{x:0,y:100}},{id:"d",position:{x:600,y:100}},
 ],segments:[
  {id:"p0",from:"a",to:"b",path:{kind:"polyline" as const,points:[]}},
  {id:"p1",from:"c",to:"d",path:{kind:"polyline" as const,points:[]}},
 ],routes:[]};
 const base={...empty,physicalTopology:topology};
 const joining=createJoiningPipe(base,[["p0"],["p1"]],"op");
 const document={...base,physicalTopology:{...topology,joiningPipes:[joining]}};
 const opChanged=executeEditorCommand(createEditorHistory(document),{type:"set-drawing-documents",documents:{tables:[],leaders:[],bomOrder:[],pipeOpacity:.4}});
 const restored=parseHarnessDesignDocument(JSON.parse(JSON.stringify(opChanged.present)));
 expect(restored.drawingDocuments?.pipeOpacity).toBe(.4);
 expect(restored.physicalTopology).toEqual(document.physicalTopology);
 expect(restored.physicalTopology?.segments.map(s=>s.path)).toEqual(document.physicalTopology.segments.map(s=>s.path));
 const scene=physicalTopologyScene(restored),p=scene.find(o=>o.id==="p0")!,op=scene.find(o=>o.id==="op")!;
 expect(p.metadata?.opacity).toBe("0.4");expect(op.metadata?.opacity).toBe("0.4");
 expect(p.metadata?.volumeShading).toBe("true");
 for(const [object,expected] of [[p,.4],[op,.4]] as const){
  const state:Record<string,unknown>={globalAlpha:1};
  const context=new Proxy(state,{get(target,key:string){return key in target?target[key]:()=>{};}}) as unknown as CanvasRenderingContext2D;
  drawEditorSceneObject(context,{...object,metadata:{...object.metadata,volumeShading:"false"}},false,"drawing");
  expect(state.globalAlpha).toBe(expected);
 }
 expect(undoEditorCommand(opChanged).present.drawingDocuments?.pipeOpacity).toBeUndefined();

});
