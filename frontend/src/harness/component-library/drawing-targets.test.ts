// @vitest-environment node
import {describe,it,expect} from "vitest";
import {addAdditionalViewV3,addArticleVariantsV3,addBasicNodeV3,newTemplateContentV3} from "./template-commands-v3";
import {createE4ConnectorSeriesTableFromV3} from "./e4-connector-series-table";
import {createTemplateContentV5FromEditor,validateTemplateContentV5} from "./template-model-v5";
import {articleDrawingView,separateLegacyDrawings,type ArticleDrawing} from "./drawing-bindings";
import {projectComponentTemplateView,projectE4DrawingCompanions} from "../editor/component-template-view-renderer";

describe("drawings assigned to documentation sections",()=>{
  it("roundtrips three separate drawings per article and renders only the target",()=>{
    let core=addArticleVariantsV3(newTemplateContentV3(),[{sourceId:"s",entityType:"connector",articleKey:"A"}]);
    const articleId=core.articleVariants[0]!.id,drawings:ArticleDrawing[]=[];
    for(const target of ["drawing","e4","route"] as const){
      let viewId=core.views[1]!.id;if(target!=="drawing")[core,viewId]=addAdditionalViewV3(core,target);
      const view=core.views.find(v=>v.id===viewId)!;let id:string;[core,id]=addBasicNodeV3(core,viewId,view.layers[0]!.id,"rectangle");
      drawings.push({target,viewId,articleVariantId:articleId,nodeIds:[id],contactPointIds:[]});
    }
    const content=JSON.parse(JSON.stringify(createTemplateContentV5FromEditor(core,createE4ConnectorSeriesTableFromV3(core),[],[],undefined,drawings).content));
    expect(validateTemplateContentV5(content).valid).toBe(true);
    const instance={objectId:"x",snapshotId:"s",articleVariantId:articleId,content};
    expect(projectComponentTemplateView(instance,"drawing",{x:0,y:0})!.commands.map(c=>c.nodeId)).toEqual(drawings[0]!.nodeIds);
    expect(projectE4DrawingCompanions(instance,{x:0,y:0},200).map(c=>c.drawingId)).toEqual(drawings[1]!.nodeIds);
    expect(projectComponentTemplateView(instance,"drawing",{x:0,y:0},undefined,"route")!.commands.map(c=>c.nodeId)).toEqual(drawings[2]!.nodeIds);
    content.articleDrawings[2].nodeIds=drawings[0]!.nodeIds;
    expect(validateTemplateContentV5(content).valid).toBe(false);
  });
  it("keeps legacy shared drawings and an explicit empty target hides every object",()=>{
    let core=newTemplateContentV3();const viewId=core.views[1]!.id;let id:string;[core,id]=addBasicNodeV3(core,viewId,core.views[1]!.layers[0]!.id,"rectangle");
    const legacy={articleVariantId:"a",nodeIds:[id],contactPointIds:[]};
    const separated=separateLegacyDrawings(core,[legacy]);
    expect(separated.drawings).toHaveLength(2);
    expect(separated.drawings[0]!.nodeIds).not.toEqual(separated.drawings[1]!.nodeIds);
    expect(separated.content.views).toHaveLength(core.views.length+1);
    expect(articleDrawingView(core.views[1]!,[legacy],"a","e4").layers[0]!.nodes[0]!.visible).toBe(true);
    expect(articleDrawingView(core.views[1]!,[{...legacy,target:"e4",viewId,nodeIds:[]}],"a","drawing").layers[0]!.nodes).toEqual([]);
  });
});
