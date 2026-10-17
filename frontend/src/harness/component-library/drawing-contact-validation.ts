import type { TemplateContentV3 } from "./template-model-v3";
import { materializeE4ConnectorArticle, type E4ConnectorSeriesTable } from "./e4-connector-series-table";
import { drawingArrayContactRows } from "./drawing-array-contacts";
import type { ArticleDrawing, DrawingContactBinding } from "./drawing-bindings";

/** Empty selections mean a deliberately removed drawing. Electrical rows remain authoritative. */
export function validateArticleDrawingContacts(content:TemplateContentV3,table:E4ConnectorSeriesTable,bindings:readonly DrawingContactBinding[],drawing:ArticleDrawing):void {
  if(!drawing.nodeIds.length&&!drawing.contactPointIds.length&&!drawing.bundlePortIds?.length)return;
  const view=content.views.find(v=>drawing.viewId?v.id===drawing.viewId:v.kind==="drawing");
  if(!view)throw new Error("Вид рисунка отсутствует.");
  const ports=drawing.bundlePortIds??[];
  if(ports.length){
    if(drawing.target!=="drawing"||ports.length!==1||drawing.contactPointIds.length||!view.bundlePorts.some(p=>p.id===ports[0]))throw new Error("Общий контакт допускается только в чертеже: одна точка для всех проводов, без отдельных контактов.");
    return;
  }
  // Illustrations may have no electrical anchors, even when the article has
  // contacts. Completeness is required only once contact mapping is present.
  if (!drawing.contactPointIds.length
      && !view.contactPoints.some(point => point.shape && drawing.nodeIds.includes(point.shape.nodeId))
      && !view.repeatPlacements.some(repeat => repeat.contactPointIds.length && drawing.nodeIds.includes(repeat.prototypeGroupId))) return;
  const rows=materializeE4ConnectorArticle(table,drawing.articleVariantId).rows;
  const arrays=drawingArrayContactRows(content,table,bindings,drawing.articleVariantId).filter(item=>item.viewId===view.id&&drawing.contactPointIds.includes(item.point.prototypeContactPointId));
  const repeated=new Set(arrays.map(item=>item.point.prototypeContactPointId));
  const selected=view.contactPoints.filter(p=>drawing.contactPointIds.includes(p.id)&&!repeated.has(p.id));
  const represented=[...selected.map(p=>bindings.find(b=>b.logicalContactId===p.logicalContactId)?.seriesRowId ?? rows.find(r=>r.seriesRowId===p.logicalContactId)?.seriesRowId),...arrays.filter(item=>item.row).map(item=>item.row!.seriesRowId)];
  if(represented.length!==rows.length||represented.some(id=>!id||!rows.some(r=>r.seriesRowId===id))||new Set(represented).size!==rows.length){
    const article=content.articleVariants.find(a=>a.id===drawing.articleVariantId)?.articleKey??drawing.articleVariantId;
    throw new Error(`${article}: рисунок должен содержать по одной точке для каждого из ${rows.length} контактов артикула. Связано ${new Set(represented.filter(Boolean)).size}. Проверьте привязку к колонке № или используйте общий контакт в чертеже.`);
  }
}
