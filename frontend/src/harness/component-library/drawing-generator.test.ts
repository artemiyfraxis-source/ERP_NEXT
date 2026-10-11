import { buildComponentTemplateViewInstances } from "../editor/HarnessDesignEditor";

import { createEmptyHarnessDesign } from "../editor/model";
import { createWire } from "../editor/commands";
import {contactShapeLabel} from "./contact-shape";
import {deleteDrawingSelection} from "./drawing-selection";
import {assignGeneratorRole} from "./drawing-generator";
import { setDrawingArray } from "./drawing-array-commands";
import { describe, expect, it } from "vitest";
import { newTemplateContentV3, addBasicNodeV3, addContactPointV3, addArticleVariantsV3, addContactTypeGroupV3, setArticleVariantContactGroupV3, moveNodeV3, deleteNodeV3 } from "./template-commands-v3";
import { createE4ConnectorSeriesTableFromV3 } from "./e4-connector-series-table";
import { createTemplateContentV5FromEditor, validateTemplateContentV5, projectTemplateContentV5ToV3 } from "./template-model-v5";
import { newDrawingGenerator, generatorLayout, materializeGenerator, guardGeneratorArticleEdit, validateDrawingGenerators, reconcileDrawingGenerators, generatorFromLegacyArray } from "./drawing-generator";
import { projectComponentTemplateView, projectE4DrawingCompanions } from "../editor/component-template-view-renderer";
import { createConnectorInstanceFromComponentTemplateV3 } from "../editor/component-template-placement";

export function generatorFixture(counts=[1,2,10]) {
  let core=newTemplateContentV3(); const view=core.views[1]!,layer=view.layers[0]!;
  let group:string; [core,group]=addContactTypeGroupV3(core,"Signal");
  core=addArticleVariantsV3(core,counts.map(n=>({sourceId:"test",entityType:"connector",articleKey:`PHR-${n}`})));
  core.articleVariants.forEach((a,i)=>{core=setArticleVariantContactGroupV3(core,a.id,group,counts[i]!,[]);});
  const table=createE4ConnectorSeriesTableFromV3(core,true);
  const g=newDrawingGenerator(view.id,"drawing");
  for(const role of ["start","period","end","static"] as const){let id:string;[core,id]=addBasicNodeV3(core,view.id,layer.id,"rectangle");g.roles[role]=[id];}
  let point:string;[core,point]=addContactPointV3(core,view.id,{number:"1",name:"Pin",contactTypeGroupId:group});g.periodPointIds=[point];
  const bindings=[{logicalContactId:core.views[1]!.contactPoints[0]!.logicalContactId,seriesRowId:table.seriesDefaults[0]!.rowId}];
  g.articles=core.articleVariants.map(a=>({articleId:a.id,nodeIds:[]}));
  return {core,table,g,bindings,viewId:view.id,layerId:layer.id};
}
describe("series drawing generator",()=>{
  it.each([1,2,10])("materializes %s contacts, fixed logo and a single moving end",count=>{
    const {core,table,g,bindings}=generatorFixture();const article=core.articleVariants.find(a=>a.articleKey===`PHR-${count}`)!;
    const result=materializeGenerator(core,table,bindings,g,article.id);
    expect(result.layout.repeats).toBe(count);expect(result.view.layers[0]!.nodes).toHaveLength(count+3);
    expect(result.points.map(p=>p.row.number)).toEqual(Array.from({length:count},(_,i)=>String(i+1)));
    const end=result.view.layers[0]!.nodes.find(n=>n.id.includes(":end:"))!;expect(end.transform.translateX).toEqual({kind:"constant",value:(count-1)*40});
    const persisted=createTemplateContentV5FromEditor(core,table,[],[],undefined,[],bindings,[g]).content;
    expect(validateTemplateContentV5(JSON.parse(JSON.stringify(persisted))).valid).toBe(true);
    const projection=projectComponentTemplateView({content:persisted,articleVariantId:article.id,objectId:"x",snapshotId:"s"},"drawing",{x:0,y:0});
    expect(projection?.commands).toHaveLength(count+3);
    const placed=createConnectorInstanceFromComponentTemplateV3({templateId:"t",version:2,versionSha256:"a".repeat(64),code:"PHR",name:"Test",assets:[],articleBindings:[],content:persisted},{id:"x",designation:"X1",articleVariantId:article.id,e4Position:{x:0,y:0}});
    expect(placed.contacts).toHaveLength(count);
    if(placed.libraryBinding?.mode!=="template")throw new Error("binding");
    expect(placed.libraryBinding.snapshot.contacts.map(c=>c.representations.length)).toEqual(Array(count).fill(1));
  });
  it.each([2,3,4])("keeps %s-row end position independent of numbering and orientation",rows=>{
    const f=generatorFixture([rows*3]);f.g.rows=rows;const id=f.core.articleVariants[0]!.id;
    const plain=materializeGenerator(f.core,f.table,f.bindings,f.g,id);
    const snake=materializeGenerator(f.core,f.table,f.bindings,{...f.g,numbering:"snake"},id);
    expect(plain.layout.endOffset).toBe(80);expect(snake.view.layers).toEqual(plain.view.layers);
    expect(snake.points.map(p=>p.point.x)).not.toEqual(plain.points.map(p=>p.point.x));
    const vertical=materializeGenerator(f.core,f.table,f.bindings,{...f.g,axis:"vertical",traversal:"across",reverse:true},id);
    expect(vertical.layout.endOffset).toBe(80);
    expect(vertical.view.layers[0]!.nodes.find(n=>n.id.includes(":end:"))!.transform.translateY).toEqual({kind:"constant",value:80});
  });
  it("rejects partial periods, missing sources and cross-article additions",()=>{
    const f=generatorFixture([3]);expect(()=>generatorLayout({...f.g,rows:2},3)).toThrow(/целые/);
    expect(()=>validateDrawingGenerators(f.core,f.table,[{...f.g,roles:{...f.g.roles,end:["missing"]}}])).toThrow();
    expect(()=>validateDrawingGenerators(f.core,f.table,[{...f.g,articles:[{articleId:f.core.articleVariants[0]!.id,nodeIds:f.g.roles.start}]}])).toThrow();
  });
  it("keeps additions local and rejects source deletion, movement and mixed edits",()=>{
    const f=generatorFixture([2,10]);const id=f.core.articleVariants[0]!.id;
    let added:string;let next;[next,added]=addBasicNodeV3(f.core,f.viewId,f.layerId,"ellipse");
    const g=guardGeneratorArticleEdit(f.core,next,f.g,id);expect(g.articles[0]!.nodeIds).toEqual([added]);
    const a=materializeGenerator(next,f.table,f.bindings,g,id),b=materializeGenerator(next,f.table,f.bindings,g,f.core.articleVariants[1]!.id);
    expect(a.view.layers[0]!.nodes.some(n=>n.id===added)).toBe(true);expect(b.view.layers[0]!.nodes.some(n=>n.id===added)).toBe(false);
    const moved=moveNodeV3(next,f.viewId,f.layerId,added,5,6);expect(()=>guardGeneratorArticleEdit(next,moved,g,id)).not.toThrow();
    expect(()=>guardGeneratorArticleEdit(next,moved,g,id,[added,a.view.layers[0]!.nodes[0]!.id])).toThrow(/защищена/);
    expect(()=>guardGeneratorArticleEdit(next,deleteNodeV3(next,f.viewId,f.layerId,g.roles.start[0]!),g,id)).toThrow(/защищена/);
    expect(()=>guardGeneratorArticleEdit(next,moveNodeV3(moved,f.viewId,f.layerId,g.roles.start[0]!,5,6),g,id)).toThrow(/защищена/);
    expect(materializeGenerator(moveNodeV3(next,f.viewId,f.layerId,g.roles.start[0]!,5,6),f.table,f.bindings,g,id).view.layers[0]!.nodes.some(n=>n.id===added)).toBe(true);
  });
  it.each(["e4","route"] as const)("projects %s without losing whole drawing identity",target=>{
    const f=generatorFixture([2]);f.g.target=target;
    const content=createTemplateContentV5FromEditor(f.core,f.table,[],[],undefined,[],f.bindings,[f.g]).content;
    expect(projectTemplateContentV5ToV3(content)).not.toHaveProperty("drawingGenerators");
    const instance={content,articleVariantId:f.core.articleVariants[0]!.id,objectId:"x",snapshotId:"s"};
    if(target==="e4"){const companions=projectE4DrawingCompanions(instance,{x:0,y:0},300);expect(companions).toHaveLength(1);expect(companions[0]!.commands).toHaveLength(5);}
    else expect(projectComponentTemplateView(instance,"drawing",{x:0,y:0},undefined,"route")?.commands).toHaveLength(5);
  });
  it("allows additions after a persisted V5 round trip with reordered object keys",()=>{
    const f=generatorFixture([2]);
    const persisted=createTemplateContentV5FromEditor(f.core,f.table,[],[],undefined,[],f.bindings,[f.g]).content;
    const reopened=projectTemplateContentV5ToV3(JSON.parse(JSON.stringify(persisted)));
    const [next,id]=addBasicNodeV3(reopened,f.viewId,f.layerId,"ellipse");
    expect(guardGeneratorArticleEdit(reopened,next,f.g,f.core.articleVariants[0]!.id).articles[0]!.nodeIds).toEqual([id]);
  });
  it("prunes deleted source and article references without losing other roles",()=>{
    const f=generatorFixture([2,10]);
    const next=deleteNodeV3(f.core,f.viewId,f.layerId,f.g.roles.end[0]!);
    next.articleVariants=next.articleVariants.slice(0,1);
    const [g]=reconcileDrawingGenerators(f.core,next,[f.g]);
    expect(g!.roles.end).toEqual([]);expect(g!.roles.period).toEqual(f.g.roles.period);
    expect(g!.articles).toHaveLength(1);expect(()=>validateDrawingGenerators(next,f.table,[g])).not.toThrow();
  });
  it("copies a legacy array without altering its view and assignments",()=>{
    const f=generatorFixture([2,10]);
    const old=setDrawingArray(f.core,f.viewId,f.layerId,[...f.g.roles.period,...f.g.periodPointIds],{rows:1,direction:"long-side",numbering:"snake",countSource:"article",count:2,pitchX:40,pitchY:40});
    const snapshot=structuredClone(old);
    const converted=generatorFromLegacyArray(old,f.viewId,"drawing");
    expect(old).toEqual(snapshot);expect(converted.content.views.slice(0,old.views.length)).toEqual(old.views);
    const g={...converted.generator,articles:f.g.articles};
    expect(()=>validateDrawingGenerators(converted.content,f.table,[g])).not.toThrow();
    expect(materializeGenerator(converted.content,f.table,f.bindings,g,f.core.articleVariants[1]!.id).points).toHaveLength(10);
  });
  it("keeps corner numbering independent of geometry and supports preview N",()=>{
    const f=generatorFixture([2]);const id=f.core.articleVariants[0]!.id;
    const plain=materializeGenerator(f.core,f.table,f.bindings,f.g,id);
    const reverse=materializeGenerator(f.core,f.table,f.bindings,{...f.g,corner:"bottom-right"},id);
    expect(reverse.view.layers).toEqual(plain.view.layers);
    expect(reverse.points[0]!.point.x).toEqual(plain.points[1]!.point.x);
    expect(materializeGenerator(f.core,f.table,f.bindings,f.g,id,1).points).toHaveLength(1);
    expect(f.core.articleVariants).toHaveLength(1);
  });

  it("binds fixed end contacts once and maps remaining rows to periods",()=>{
    const f=generatorFixture([3]);let fixed:string;
    const [core,id]=addContactPointV3(f.core,f.viewId,{number:"2",name:"End"});fixed=id;
    const point=core.views.find(v=>v.id===f.viewId)!.contactPoints.find(p=>p.id===fixed)!;
    const g={...f.g,fixedPointIds:[fixed],endPointIds:[fixed]};
    const bindings=[...f.bindings,{logicalContactId:point.logicalContactId,seriesRowId:f.table.seriesDefaults[2]!.rowId}];
    const result=materializeGenerator(core,f.table,bindings,g,core.articleVariants[0]!.id);
    expect(result.layout.repeats).toBe(2);expect(result.points.map(p=>p.row.number)).toEqual(["3","1","2"]);
    expect(result.points[0]!.point.x).toEqual({kind:"constant",value:300});
    expect(()=>materializeGenerator(core,f.table,[],g,core.articleVariants[0]!.id)).toThrow(/не связан/);
    expect(createTemplateContentV5FromEditor(core,f.table,[],[],undefined,[],bindings,[g]).content.drawingGenerators).toHaveLength(1);
  });

});

describe("E4 contact shape recovery",()=>{
  it("recovers after deleting and replacing the period contact",()=>{
    const f=generatorFixture([2]);
    const deleted=deleteDrawingSelection(f.core,f.viewId,f.g.periodPointIds);
    expect(()=>materializeGenerator(deleted,f.table,f.bindings,f.g,f.core.articleVariants[0]!.id)).toThrow(/Контакт исходника удалён/);
    const [g]=reconcileDrawingGenerators(f.core,deleted,[f.g]);
    expect(g!.periodPointIds).toEqual([]);
    expect(()=>validateDrawingGenerators(deleted,f.table,[g],true)).not.toThrow();
    expect(()=>validateDrawingGenerators(deleted,f.table,[g])).toThrow(/Назначьте/);
    const [replaced,id]=addContactPointV3(deleted,f.viewId,{name:"replacement"});
    const repaired=assignGeneratorRole(replaced,g!,"period",[id]);
    expect(materializeGenerator(replaced,f.table,f.bindings,repaired,replaced.articleVariants[0]!.id).points).toHaveLength(2);
    expect(materializeGenerator(f.core,f.table,f.bindings,f.g,f.core.articleVariants[0]!.id).points).toHaveLength(2);
  });
  it("repeats shape contacts with stable row colours and centred fitted Arial labels",()=>{
    const f=generatorFixture([10]);f.g.target="e4";f.g.numbering="snake";f.g.rows=2;
    const point=f.core.views[1]!.contactPoints[0]!;
    point.shape={nodeId:f.g.roles.period[0]!,fillFromWire:true};
    const assigned=assignGeneratorRole(f.core,{...f.g,periodPointIds:[]},"period",f.g.roles.period);
    expect(assigned.periodPointIds).toEqual([point.id]);
    const content=createTemplateContentV5FromEditor(f.core,f.table,[],[],undefined,[],f.bindings,[assigned]).content;
    expect(validateTemplateContentV5(JSON.parse(JSON.stringify(content))).valid).toBe(true);
    const second=f.table.seriesDefaults[1]!.rowId;
    const instance={content,articleVariantId:f.core.articleVariants[0]!.id,objectId:"x",snapshotId:"s",contactWireColors:{[second]:"#ff0000"}};
    const template={templateId:crypto.randomUUID(),version:1,versionSha256:"a".repeat(64),code:"T",name:"Test",articleBindings:content.articleVariants.map(a=>({sourceId:a.sourceId.toLowerCase(),entityType:a.entityType,articleKey:a.articleKey})),assets:[],content};
    const connector=createConnectorInstanceFromComponentTemplateV3(template,{id:crypto.randomUUID(),designation:"X1",articleVariantId:instance.articleVariantId,e4Position:{x:0,y:0}});
    const endpoint={connectorId:connector.id,contactId:connector.contacts[1]!.id};
    const wire={...createWire("W1",endpoint,endpoint,null),color:"#ff0000"};
    const snapshot={snapshotId:"s",projectId:"p",sourceTemplateId:template.templateId,sourceVersion:1,sourceVersionSha256:template.versionSha256,code:template.code,name:template.name,articleBindings:template.articleBindings,assets:[],schemaVersion:5,content,createdUtc:"",updatedUtc:""};
    const actual=buildComponentTemplateViewInstances({...createEmptyHarnessDesign(),connectors:[connector],wires:[wire]},new Map([[connector.id,snapshot]]))[0]!;
    expect(actual).toBeDefined();
    expect(projectE4DrawingCompanions(actual,{x:0,y:0},300)[0]!.commands.filter(c=>c.fill==="#ff0000").map(c=>c.contactLabel?.text)).toEqual(["2"]);
    const drawings=projectE4DrawingCompanions(instance,{x:0,y:0},300);
    expect(drawings).toHaveLength(1);
    const shapes=drawings[0]!.commands.filter(c=>c.contactLabel);
    expect(shapes).toHaveLength(10);
    expect(shapes.map(c=>c.contactLabel!.text).sort()).toEqual(["1","10","2","3","4","5","6","7","8","9"]);
    expect(shapes.filter(c=>c.fill==="#ff0000").map(c=>c.contactLabel!.text)).toEqual(["2"]);
    expect(projectE4DrawingCompanions({...instance,contactWireColors:{}},{x:0,y:0},300)[0]!.commands.some(c=>c.fill==="#ff0000")).toBe(false);
    const deleted=deleteDrawingSelection(f.core,f.viewId,f.g.roles.period);
    expect(deleted.views[1]!.contactPoints).toEqual([]);
    expect(reconcileDrawingGenerators(f.core,deleted,[f.g])[0]!.periodPointIds).toEqual([]);
  });
  it.each([1,5,40,400])("keeps an inset at size %s and for multiple digits",size=>{
    for(const ellipse of [false,true]){
      const label=contactShapeLabel("100",0,0,size,size,size*.05,ellipse);
      expect(label.fontSize).toBeGreaterThan(0);
      expect(label.fontSize*3).toBeLessThan(size*.7);
      expect(label.fontSize).toBeLessThan(size*.7);
    }
  });
});
