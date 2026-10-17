// @vitest-environment node
import {expect,it,vi} from "vitest";
import {tintTexturePixels} from "./texture-tint";
import {prepareGlobalCoverings} from "./global-covering-materials";
import {physicalFixture} from "./physical-topology-fixture";
import {standardCovering} from "./physical-coverings";
import {createEditorHistory,executeEditorCommand,undoEditorCommand,redoEditorCommand} from "./history";
import {parseHarnessDesignDocument} from "./model";
import {validCoveringStyle} from "./covering-style";
import type {GlobalMaterial} from "./covering-assets";
const entry={sha256:"a".repeat(64),name:"Материал"};
const material:GlobalMaterial={materialId:"id",name:"Образец",lineColor:"#123456",lineWidth:2.5,textureAngle:45,textureScale:2,tint:"#16a34a",coveringKind:"heat-shrink",updatedUtc:"",revision:1};
it.each(["#000000","#ffffff","#16a34a","#dc2626"])("recolours %s without flattening tones or changing transparency",tint=>{
 const data=new Uint8ClampedArray([0,0,0,0,96,96,96,71,255,255,255,255]);tintTexturePixels(data,tint);
 expect([data[3],data[7],data[11]]).toEqual([0,71,255]);
 for(let c=0;c<3;c++){expect(data[c+4]!).toBeGreaterThan(data[c]!);expect(data[c+8]!).toBeGreaterThan(data[c+4]!);}
});
it("pins settings and bytes only for new coverings; undo is atomic and old sleeves never follow library updates",async()=>{
 const doc=physicalFixture(),old=standardCovering(doc,"S0",{x:200,y:60},"Термоусадка","old"),fresh={...old,id:"new"};
 const before={...doc,physicalTopology:{...doc.physicalTopology!,coverings:[old]}};
 const command={type:"set-physical-topology" as const,topology:{...before.physicalTopology,coverings:[old,fresh]}};
 const pin=vi.fn(async()=>entry);
 const prepared=await prepareGlobalCoverings(before,command,async()=>[material],pin);
 expect(pin).toHaveBeenCalledTimes(1);
 const h=executeEditorCommand(createEditorHistory(before),prepared),c=h.present.physicalTopology!.coverings![1]!;
 expect(c.style).toMatchObject({texture:`asset:${entry.sha256}`,lineWidth:2.5,textureTint:"#16a34a",textureRotation:45,textureScale:2});
 expect(h.present.physicalTopology!.coverings![0]).toEqual(old);
 expect(h.present.drawingDocuments!.coveringLibrary!.textures).toEqual([entry]);
 const restored=parseHarnessDesignDocument(JSON.parse(JSON.stringify(h.present)));
 expect(restored.physicalTopology).toEqual(h.present.physicalTopology);expect(restored.drawingDocuments).toEqual(h.present.drawingDocuments);
 expect(undoEditorCommand(h).present).toEqual(before);expect(redoEditorCommand(undoEditorCommand(h)).present).toEqual(h.present);
 const list=vi.fn(async()=>[]);await prepareGlobalCoverings(h.present,{type:"set-physical-topology",topology:h.present.physicalTopology!},list,pin);expect(list).not.toHaveBeenCalled();
});
it("an explicit local preference wins and failed asset pinning cannot return a half-applied document",async()=>{
 const doc=physicalFixture(),cover=standardCovering(doc,"S0",{x:200,y:60},"Термоусадка","new");
 const command={type:"set-physical-topology" as const,topology:{...doc.physicalTopology!,coverings:[cover]}};
 const list=vi.fn(async()=>[material]),pin=vi.fn(async()=>{throw new Error("upload failed")});
 const local={...doc,drawingDocuments:{tables:[],leaders:[],bomOrder:[],coveringLibrary:{textures:[],defaults:{"heat-shrink":{texture:"none" as const}}}}};
 expect(await prepareGlobalCoverings(local,command,list,pin)).toBe(command);expect(list).not.toHaveBeenCalled();
 await expect(prepareGlobalCoverings(doc,command,list,pin)).rejects.toThrow("upload failed");expect(doc.physicalTopology?.coverings??[]).toHaveLength(0);
});
it.each([{lineWidth:0},{lineWidth:NaN},{lineWidth:21},{textureTint:"red"}])("rejects invalid extended style %o",style=>expect(validCoveringStyle(style)).toBe(false));

it("changing an existing type replaces its pinned texture atomically and preserves shape and lengths",async()=>{
 const doc=physicalFixture(),cover={...standardCovering(doc,"S0",{x:200,y:60},"Оплётка","old"),width:17,lengthMm:45,lengthMode:"manual" as const,style:{texture:"Metal049A" as const,hatch:"cross" as const}};
 const before={...doc,physicalTopology:{...doc.physicalTopology!,coverings:[cover]}};
 const prepared=await prepareGlobalCoverings(before,{type:"set-physical-topology",topology:{...before.physicalTopology,coverings:[{...cover,kind:"heat-shrink"}]}},async()=>[material],async()=>entry);
 const history=executeEditorCommand(createEditorHistory(before),prepared),result=history.present.physicalTopology!.coverings![0]!;
 expect(result).toMatchObject({id:cover.id,width:17,lengthMm:45,lengthMode:"manual",spans:cover.spans,kind:"heat-shrink",style:{texture:`asset:${entry.sha256}`,hatch:"cross"}});
 expect(history.present.wires).toBe(before.wires);
 expect(undoEditorCommand(history).present).toEqual(before);
 expect(redoEditorCommand(undoEditorCommand(history)).present).toEqual(history.present);
});

it.each([false,true])("type change uses local preference or builtin fallback without keeping the old image (local %s)",async local=>{
 const doc=physicalFixture(),cover={...standardCovering(doc,"S0",{x:200,y:60},"Оплётка","old"),style:{texture:"Metal049A" as const,textureRotation:15}};
 const before={...doc,drawingDocuments:{tables:[],leaders:[],bomOrder:[],coveringLibrary:{textures:[],defaults:local?{"heat-shrink":{texture:"none" as const}}:{}}},physicalTopology:{...doc.physicalTopology!,coverings:[cover]}};
 const list=vi.fn(async()=>[]),pin=vi.fn(async()=>entry);
 const prepared=await prepareGlobalCoverings(before,{type:"set-physical-topology",topology:{...before.physicalTopology,coverings:[{...cover,kind:"heat-shrink"}]}},list,pin);
 expect(prepared.topology.coverings![0]!.style).toMatchObject({texture:local?"none":"auto",textureRotation:15});
 expect(list).toHaveBeenCalledTimes(local?0:1);expect(pin).not.toHaveBeenCalled();
});

it("does not return a partially changed type when material download fails",async()=>{
 const doc=physicalFixture(),cover=standardCovering(doc,"S0",{x:200,y:60},"Оплётка","old");
 const before={...doc,physicalTopology:{...doc.physicalTopology!,coverings:[cover]}};
 await expect(prepareGlobalCoverings(before,{type:"set-physical-topology",topology:{...before.physicalTopology,coverings:[{...cover,kind:"heat-shrink"}]}},async()=>[material],async()=>{throw new Error("offline");})).rejects.toThrow("offline");
 expect(before.physicalTopology.coverings[0]).toBe(cover);
});

it("pins hatching, background and independent line width without a raster dependency",async()=>{
 const doc=physicalFixture(),cover=standardCovering(doc,"S0",{x:200,y:60},"Термоусадка","hatch");
 const pin=vi.fn(async()=>entry);
 const prepared=await prepareGlobalCoverings(doc,{type:"set-physical-topology",topology:{...doc.physicalTopology!,coverings:[cover]}},async()=>[{...material,hatchCode:"H48",hatchLineWidth:3,backgroundColor:"#aabbcc",textureScale:.1}],pin);
 expect(pin).not.toHaveBeenCalled();
 const result=executeEditorCommand(createEditorHistory(doc),prepared).present;
 expect(result.physicalTopology!.coverings![0]).toMatchObject({color:"#aabbcc",style:{texture:"none",hatchCode:"H48",hatchLineWidth:3,hatchSpacing:1,hatchColor:material.tint}});
 expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(result))).physicalTopology).toEqual(result.physicalTopology);
});
