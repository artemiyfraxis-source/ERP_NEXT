import { describe, expect, it } from "vitest";
import { addArticleVariantsV3, addBasicNodeV3, addContactPointV3, addContactTypeGroupV3, newTemplateContentV3, setArticleVariantContactGroupV3 } from "./template-commands-v3";
import { clearArticleDrawings, detachIncompleteGenerators, reconcileArticleDrawings } from "./drawing-reset";
import { newDrawingGenerator, reconcileDrawingGenerators, materializeGenerator } from "./drawing-generator";
import { createE4ConnectorSeriesTableFromV3 } from "./e4-connector-series-table";
import { createTemplateContentV5FromEditor, validateTemplateContentV5 } from "./template-model-v5";
import { deleteDrawingSelection } from "./drawing-selection";
import { validateArticleDrawingContacts } from "./drawing-contact-validation";
import { articleDrawingView, type DrawingTarget } from "./drawing-bindings";
import { projectComponentTemplateView } from "../editor/component-template-view-renderer";

function fixture(target: DrawingTarget = "drawing") {
  let content = newTemplateContentV3();
  let group: string; [content, group] = addContactTypeGroupV3(content, "Signal");
  content = addArticleVariantsV3(content, ["A", "B"].map(articleKey => ({sourceId:"test",entityType:"connector",articleKey})));
  for (const article of content.articleVariants) content = setArticleVariantContactGroupV3(content, article.id, group, 2, []);
  const view = content.views[1]!;
  let node: string, point: string;
  [content, node] = addBasicNodeV3(content, view.id, view.layers[0]!.id, "rectangle");
  [content, point] = addContactPointV3(content, view.id, {number:"1",name:"Pin",contactTypeGroupId:group});
  const generator = newDrawingGenerator(view.id, target);
  generator.roles.period = [node]; generator.periodPointIds = [point];
  generator.articles = content.articleVariants.map(article => ({articleId:article.id,nodeIds:[]}));
  return { content, drawingGenerators:[generator], articleDrawings:[], viewId:view.id, table:createE4ConnectorSeriesTableFromV3(content,true), node, point };
}

describe("clearing drawings without restoring deleted generators", () => {
  it.each(["drawing","e4","route"] as const)("clears one %s article, preserves sibling and electrical rows, and survives reopening", target => {
    const original = fixture(target), snapshot = structuredClone(original);
    const [a,b] = original.content.articleVariants.map(article=>article.id) as [string,string];
    const next = clearArticleDrawings(original, target, [a], original.viewId);
    expect(next.viewId).not.toBe(original.viewId);
    expect(next.content.views.find(view=>view.id===original.viewId)).toEqual(original.content.views[1]);
    expect(next.drawingGenerators[0]!.articles.map(article=>article.articleId)).toEqual([b]);
    expect(materializeGenerator(next.content,original.table,[],next.drawingGenerators[0]!,b).view.layers[0]!.nodes).toHaveLength(2);
    const persisted = createTemplateContentV5FromEditor(next.content,original.table,[],[],undefined,next.articleDrawings,[],next.drawingGenerators).content;
    expect(validateTemplateContentV5(JSON.parse(JSON.stringify(persisted))).valid).toBe(true);
    const empty = articleDrawingView(next.content.views.find(view=>view.id===next.viewId)!,next.articleDrawings,a,target);
    expect(empty.layers.flatMap(layer=>layer.nodes)).toEqual([]);
    const recleared = clearArticleDrawings(next,target,[a],next.viewId);
    expect(recleared.viewId).toBe(next.viewId);
    expect(recleared.content.views).toHaveLength(next.content.views.length);
    expect(original).toEqual(snapshot); // the same snapshot is safe for Ctrl+Z
  });
  it("clears all articles, then saves a new ordinary drawing with no contact requirement", () => {
    const original = fixture();
    const next = clearArticleDrawings(original,"drawing",original.content.articleVariants.map(a=>a.id),original.viewId);
    expect(next.drawingGenerators).toEqual([]);
    expect(next.articleDrawings).toHaveLength(2);
    const view = next.content.views.find(v=>v.id===next.viewId)!;
    const [added,id] = addBasicNodeV3(next.content,view.id,view.layers[0]!.id,"ellipse");
    const drawings = reconcileArticleDrawings(next.content,added,next.articleDrawings,view.id);
    expect(drawings.every(d=>d.nodeIds.includes(id))).toBe(true);
    for (const drawing of drawings) expect(()=>validateArticleDrawingContacts(added,original.table,[],drawing)).not.toThrow();
    const persisted = createTemplateContentV5FromEditor(added,original.table,[],[],undefined,drawings,[],[]).content;
    expect(projectComponentTemplateView({content:persisted,articleVariantId:drawings[0]!.articleVariantId,objectId:"x",snapshotId:"s"},"drawing",{x:0,y:0})!.commands.map(command=>command.nodeId)).toEqual([id]);
    expect(original.table.articles.every(article=>article.rows.length===2)).toBe(true);
  });
  it("Delete of all source objects removes the recipe and persists explicit empty drawings", () => {
    const original = fixture();
    const content = deleteDrawingSelection(original.content,original.viewId,[original.node,original.point]);
    const next = detachIncompleteGenerators(content,reconcileDrawingGenerators(original.content,content,original.drawingGenerators),[]);
    expect(next.drawingGenerators).toEqual([]);
    expect(next.articleDrawings).toHaveLength(2);
    expect(()=>createTemplateContentV5FromEditor(content,original.table,[],[],undefined,next.articleDrawings,[],next.drawingGenerators)).not.toThrow();
    const view=content.views[1]!;
    const [added,id]=addBasicNodeV3(content,view.id,view.layers[0]!.id,"rectangle");
    expect(reconcileArticleDrawings(content,added,next.articleDrawings!,view.id).every(d=>d.nodeIds.includes(id))).toBe(true);
  });
  it("allows saving an intermediate source after deleting its period contact", () => {
    const original=fixture();
    const content=deleteDrawingSelection(original.content,original.viewId,[original.point]);
    const next=detachIncompleteGenerators(content,reconcileDrawingGenerators(original.content,content,original.drawingGenerators),[]);
    expect(next.drawingGenerators![0]!.articles).toEqual([]);
    expect(()=>createTemplateContentV5FromEditor(content,original.table,[],[],undefined,next.articleDrawings,[],next.drawingGenerators)).not.toThrow();
  });
  it("keeps other documentation sections and legacy drawings while clearing one target", () => {
    const original=fixture();
    const id=original.content.articleVariants[0]!.id;
    const state={...original,drawingGenerators:[],articleDrawings:[{articleVariantId:id,nodeIds:[original.node],contactPointIds:[original.point]}]};
    const next=clearArticleDrawings(state,"e4",[id],original.viewId);
    expect(next.articleDrawings.find(d=>d.target==="drawing")!.nodeIds).toEqual([original.node]);
    expect(next.articleDrawings.find(d=>d.target==="e4")!.nodeIds).toEqual([]);
    expect(next.content.views.find(v=>v.id===original.viewId)).toEqual(original.content.views[1]);
  });
  it("does not add new figures to a different article subset in the same view", () => {
    const original=fixture(), view=original.content.views[1]!;
    const [a,b]=original.content.articleVariants.map(article=>article.id) as [string,string];
    const drawings=[{articleVariantId:a,target:"drawing" as const,viewId:view.id,nodeIds:[original.node],contactPointIds:[]},
      {articleVariantId:b,target:"drawing" as const,viewId:view.id,nodeIds:[],contactPointIds:[]}];
    const [content,id]=addBasicNodeV3(original.content,view.id,view.layers[0]!.id,"ellipse");
    const next=reconcileArticleDrawings(original.content,content,drawings,view.id,a);
    expect(next[0]!.nodeIds).toContain(id);
    expect(next[1]!.nodeIds).toEqual([]);
  });
});
