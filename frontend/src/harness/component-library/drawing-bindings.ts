import type { TemplateContentV3 } from "./template-model-v3";
import type { TemplateViewV2 } from "./template-model-v2";
import { materializeE4ConnectorArticle, type E4ConnectorSeriesTable } from "./e4-connector-series-table";

export type DrawingTarget = "e4" | "drawing" | "route";
export interface ArticleDrawing { articleVariantId: string; nodeIds: string[]; contactPointIds: string[]; bundlePortIds?:string[]; target?: DrawingTarget; viewId?: string }
export function findArticleDrawing(drawings:readonly ArticleDrawing[]|undefined,articleId:string|null,target:DrawingTarget) {
  return drawings?.find(d=>d.articleVariantId===articleId && d.target===target)
    ?? drawings?.find(d=>d.articleVariantId===articleId && !d.target && target!=="route");
}

/** Split the old shared drawing once, so editing E4 never changes the drawing sheet. */
export function separateLegacyDrawings(content:TemplateContentV3,drawings:readonly ArticleDrawing[]|undefined):{content:TemplateContentV3;drawings:ArticleDrawing[]} {
  const legacy=drawings?.filter(d=>!d.target)??[],source=content.views.find(v=>v.kind==="drawing");
  if(!legacy.length||!source)return {content,drawings:[...(drawings??[])]};
  const clone=structuredClone(source),ids=new Map<string,string>();
  for(const id of [clone.id,...clone.layers.flatMap(l=>[l.id,...l.nodes.map(n=>n.id)]),...clone.contactPoints.map(p=>p.id),...clone.bundlePorts.map(p=>p.id)])ids.set(id,crypto.randomUUID());
  clone.id=ids.get(clone.id)!;clone.name="Рисунки · Схема Э4";clone.kind="additional";
  for(const layer of clone.layers){layer.id=ids.get(layer.id)!;for(const node of layer.nodes){node.id=ids.get(node.id)!;node.layerId=layer.id;if(node.kind==="group")node.geometry.childIds=node.geometry.childIds.map(id=>ids.get(id)!);}}
  for(const point of clone.contactPoints){point.id=ids.get(point.id)!;if(point.shape)point.shape.nodeId=ids.get(point.shape.nodeId)!;}
  for(const point of clone.bundlePorts)point.id=ids.get(point.id)!;
  for(const repeat of clone.repeatPlacements){repeat.prototypeGroupId=ids.get(repeat.prototypeGroupId)!;repeat.contactPointIds=repeat.contactPointIds.map(id=>ids.get(id)!);}
  return {content:{...content,views:[...content.views,clone]},drawings:[...(drawings??[]).filter(d=>d.target),...legacy.flatMap(d=>[
    {...d,target:"drawing" as const,viewId:source.id},
    {...d,target:"e4" as const,viewId:clone.id,nodeIds:d.nodeIds.map(id=>ids.get(id)!),contactPointIds:d.contactPointIds.map(id=>ids.get(id)!),bundlePortIds:[]},
  ]).filter((d,index,all)=>!(drawings??[]).some(old=>old.articleVariantId===d.articleVariantId&&old.target===d.target))]};
}
export interface DrawingContactBinding { logicalContactId: string; seriesRowId: string }

/** A group is selected as a whole, including its descendants. */
export function drawingSelection(view: TemplateViewV2, selectedIds: readonly string[], articleVariantId: string): ArticleDrawing {
  const selected = new Set(selectedIds);
  const nodes = view.layers.flatMap(layer => layer.nodes);
  let changed = true;
  while (changed) {
    changed = false;
    for (const node of nodes) if (node.kind === "group" && (selected.has(node.id) || node.geometry.childIds.some(id => selected.has(id)))) {
      for (const id of [node.id, ...node.geometry.childIds]) if (!selected.has(id)) { selected.add(id); changed = true; }
    }
  }
  for(const point of view.contactPoints) if(point.shape && selected.has(point.shape.nodeId)) selected.add(point.id);
  for (const placement of view.repeatPlacements) if (selected.has(placement.prototypeGroupId)) placement.contactPointIds.forEach(id => selected.add(id));
  return { articleVariantId, nodeIds: nodes.filter(node => selected.has(node.id)).map(node => node.id),
    contactPointIds: view.contactPoints.filter(point => selected.has(point.id)).map(point => point.id),
    bundlePortIds:view.bundlePorts.filter(point=>selected.has(point.id)).map(point=>point.id) };
}

export function articleDrawingView(view: TemplateViewV2, drawings: readonly ArticleDrawing[] | undefined, articleId: string | null, target:DrawingTarget="drawing"): TemplateViewV2 {
  const drawing = findArticleDrawing(drawings,articleId,target);
  if (!drawing) return drawings?.some(d=>d.articleVariantId===articleId && d.target) ? {...view,layers:view.layers.map(l=>({...l,nodes:[]})),contactPoints:[],bundlePorts:[],repeatPlacements:[]} : view;
  if(drawing.viewId && drawing.viewId!==view.id) return {...view,layers:view.layers.map(l=>({...l,nodes:[]})),contactPoints:[],bundlePorts:[],repeatPlacements:[]};
  const nodes = new Set(drawing.nodeIds), points = new Set(drawing.contactPointIds);
  return { ...view, layers: view.layers.map(layer => ({ ...layer, nodes: layer.nodes.map(node => ({ ...node, visible: node.visible && nodes.has(node.id) })) })),
    bundlePorts:view.bundlePorts.filter(point=>drawing.bundlePortIds?.includes(point.id)),
    contactPoints: view.contactPoints.filter(point => points.has(point.id)),
    repeatPlacements: view.repeatPlacements.filter(placement => nodes.has(placement.prototypeGroupId)) };
}

/** Electrical data is read from the article table, never copied back from the drawing. */
export function drawingContactContent(content: TemplateContentV3, table: E4ConnectorSeriesTable,
  bindings: readonly DrawingContactBinding[], articleId: string | null): TemplateContentV3 {
  const rows = articleId ? materializeE4ConnectorArticle(table, articleId).rows
    : table.seriesDefaults.map(row => ({ ...row.values, seriesRowId: row.rowId }));
  const rowsById = new Map(rows.map(row => [row.seriesRowId, row]));
  const byContact = new Map(bindings.map(binding => [binding.logicalContactId, binding.seriesRowId]));
  return { ...content, logicalContacts: content.logicalContacts.map(contact => {
    const row = rowsById.get(byContact.get(contact.id) ?? "");
    return row ? { ...contact, number: row.number, name: row.name, circuitText: row.circuitText, contactTypeGroupId: row.contactTypeGroupId } : contact;
  }) };
}
