// @vitest-environment node
import { describe, expect, it } from "vitest";
import { addBasicNodeV3, addContactTypeGroupV3, newTemplateContentV3, setArticleVariantContactGroupV3 } from "./template-commands-v3";
import { expandTemplateViewRepeatsV2 } from "./template-repeat-v2";
import { projectTemplateContentV3CoreToV2 } from "./template-commands-v3";
import { setDrawingArray } from "./drawing-array-commands";

describe("drawing array command", () => {
  it("uses one prototype for PHR-2 and PHR-10 article counts", () => {
    let content = newTemplateContentV3();
    const drawing = content.views.find(v => v.kind === "drawing")!;
    let groupId = "";
    [content, groupId] = addContactTypeGroupV3(content, "Signal");
    const articleId = crypto.randomUUID();
    content = { ...content, articleVariants: [{ id:articleId, sourceId:"test", entityType:"connector", articleKey:"PHR-2", parameterValues:[], contactGroups:null }] };
    content = setArticleVariantContactGroupV3(content, articleId, groupId, 2, []);
    const layer = content.views.find(v=>v.id===drawing.id)!.layers[0]!;
    let nodeId = "";
    [content,nodeId] = addBasicNodeV3(content, drawing.id, layer.id, "rectangle");
    const second = {...content.articleVariants[0]!, id:crypto.randomUUID(),articleKey:"PHR-10",contactGroups:[{contactTypeGroupId:groupId,contactCount:10,allowedTerminalArticleKeys:[]}]};
    content.articleVariants.push(second);
    const configured = setDrawingArray(content, drawing.id, layer.id, [nodeId], {rows:2,direction:"short-side",numbering:"snake",countSource:"article",count:2,pitchX:10,pitchY:20});
    const placement = configured.views.find(v=>v.id===drawing.id)!.repeatPlacements[0]!;
    expect(placement.arrayLayout).toMatchObject({rows:2,numbering:"snake"});
    const expanded = expandTemplateViewRepeatsV2(projectTemplateContentV3CoreToV2(configured), drawing.id, {articlePreset:articleId})[0]!.occurrences;
    expect(expanded).toHaveLength(2);
    expect(expanded[1]!.offset).toEqual({x:0,y:20});
    expect(configured.articleVariants[0]!.articleKey).toBe("PHR-2");
    expect(expandTemplateViewRepeatsV2(projectTemplateContentV3CoreToV2(configured), drawing.id, {articlePreset:second.id})[0]!.occurrences).toHaveLength(10);
    expect(configured.logicalContacts).toHaveLength(0);
  });
});
