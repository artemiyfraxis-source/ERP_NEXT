import type { TemplateContentV3 } from "./template-model-v3";
import { materializeE4ConnectorArticle, type E4ConnectorSeriesTable } from "./e4-connector-series-table";
import type { DrawingContactBinding } from "./drawing-bindings";
import { projectTemplateContentV3CoreToV2 } from "./template-commands-v3";
import { expandTemplateViewRepeatsV2 } from "./template-repeat-v2";
import { materializeArticleVariantV3 } from "./template-article-materialization-v3";
import { withDrawingArticleCounts } from "./drawing-array-commands";

/** Runtime-only projection onto existing table rows: no electrical contacts are created. */
export function drawingArrayContactRows(content:TemplateContentV3,table:E4ConnectorSeriesTable,bindings:readonly DrawingContactBinding[],articleId:string|null) {
  if(!articleId)return [];
  const rows=materializeE4ConnectorArticle(table,articleId).rows;
  const graphic=withDrawingArticleCounts(content);
  const parameters=materializeArticleVariantV3({...graphic,articleVariants:graphic.articleVariants.map(a=>({...a,contactGroups:null}))},articleId).overrides;
  const core=projectTemplateContentV3CoreToV2(graphic);
  return core.views.flatMap(view=>expandTemplateViewRepeatsV2(core,view.id,{overrides:parameters}).flatMap(expansion=>{
    const placement=view.repeatPlacements.find(p=>p.prototypeGroupId===expansion.prototypeGroupId)!;
    if(!placement.arrayLayout)return [];
    return expansion.occurrences.flatMap(occurrence=>occurrence.contactPoints.map(point=>{
      const rowId=bindings.find(b=>b.logicalContactId===point.prototypeLogicalContactId)?.seriesRowId;
      const start=rows.findIndex(r=>r.seriesRowId===rowId);
      const row=start<0?undefined:rows[start+occurrence.index*placement.contactPointIds.length];
      return {viewId:view.id,viewName:view.name,viewKind:view.kind,point,row};
    }));
  }));
}
