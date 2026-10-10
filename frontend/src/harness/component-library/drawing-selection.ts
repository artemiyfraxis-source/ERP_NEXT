import type { FillV2, StrokeV2, TemplateNodeV2, TemplateViewV2, NumericExpressionV2 } from "./template-model-v2";
import { validateTemplateContentV3Structure, type TemplateContentV3 } from "./template-model-v3";
import { drawingLayerOutlines } from "./drawing-geometry";
import { constantExpressionV3 as c } from "./template-commands-v3";

export interface SelectionBox { left: number; top: number; right: number; bottom: number }
type Evaluate = (value: NumericExpressionV2) => number | null;
type RepeatOffsets = ReadonlyMap<string,readonly {offset:{x:number;y:number}}[]>;
function expandedOutlines(nodes:readonly TemplateNodeV2[],evaluate:Evaluate,repeats?:RepeatOffsets) {
  return drawingLayerOutlines(nodes,evaluate).flatMap(outline=>{
    const occurrences=repeats?.get(outline.id);
    return occurrences ? occurrences.map(({offset})=>({...outline,points:outline.points.map(p=>({x:p.x+offset.x,y:p.y+offset.y}))})) : [outline];
  });
}
export function selectionBounds(view: TemplateViewV2, ids: readonly string[], evaluate: Evaluate,repeats?:RepeatOffsets): SelectionBox | null {
  const points = view.layers.filter(layer => layer.visible).flatMap(layer => expandedOutlines(layer.nodes,evaluate,repeats)).filter(outline => ids.includes(outline.id)).flatMap(outline => outline.points);
  for (const point of [...view.contactPoints, ...view.bundlePorts].filter(point => ids.includes(point.id))) {
    const x=evaluate(point.x), y=evaluate(point.y);
    if(x!==null && y!==null) points.push({x:x-5,y:y-5},{x:x+5,y:y+5});
  }
  if (!points.length) return null;
  return {left:Math.min(...points.map(p=>p.x)),top:Math.min(...points.map(p=>p.y)),right:Math.max(...points.map(p=>p.x)),bottom:Math.max(...points.map(p=>p.y))};
}

/** Window selection: every visible contour of a root must fit, in either drag direction. */
export function nodesInsideSelectionBox(view: TemplateViewV2, box: SelectionBox, evaluate: Evaluate,repeats?:RepeatOffsets): string[] {
  const nodes = view.layers.filter(layer => layer.visible && !layer.locked).flatMap(layer => {
    const outlines=expandedOutlines(layer.nodes,evaluate,repeats);
    const ids=[...new Set(outlines.map(outline=>outline.id))];
    return ids.filter(id => !layer.nodes.find(node=>node.id===id)?.locked && outlines.filter(outline=>outline.id===id).every(outline=>outline.points.length>0 && outline.points.every(p=>p.x>=box.left && p.x<=box.right && p.y>=box.top && p.y<=box.bottom)));
  });
  return [...nodes,...[...view.contactPoints,...view.bundlePorts].filter(point=>{
    const x=evaluate(point.x),y=evaluate(point.y);
    return x!==null && y!==null && x-5>=box.left && x+5<=box.right && y-5>=box.top && y+5<=box.bottom;
  }).map(point=>point.id)];
}

const value = (expression:NumericExpressionV2) => { if(expression.kind!=="constant") throw new Error("Преобразование задано параметром."); return expression.value; };
function validated(content:TemplateContentV3) { const check=validateTemplateContentV3Structure(content); if(!check.valid) throw new Error(check.diagnostics[0]?.message); return content; }
function selection(content:TemplateContentV3,viewId:string,ids:readonly string[], editing=true) {
  const view=content.views.find(view=>view.id===viewId); if(!view) throw new Error("Вид не найден.");
  const nodes=view.layers.flatMap(layer=>layer.nodes);
  const owned=new Set(nodes.flatMap(node=>node.kind==="group" ? node.geometry.childIds : []));
  const roots=nodes.filter(node=>ids.includes(node.id) && !owned.has(node.id));
  const points=[...view.contactPoints,...view.bundlePorts].filter(point=>ids.includes(point.id));
  if(!roots.length && !points.length) throw new Error("Выберите фигуры или контакты.");
  const selected=new Set<string>();
  const visit=(node:TemplateNodeV2) => { if(selected.has(node.id)) return; selected.add(node.id); if(node.kind==="group") node.geometry.childIds.forEach(id=>{ const child=nodes.find(n=>n.id===id); if(child) visit(child); }); };
  roots.forEach(visit);
  if(editing && view.layers.some(layer=>layer.nodes.some(node=>selected.has(node.id) && (layer.locked || node.locked)))) throw new Error("Выделение содержит заблокированные фигуры.");
  return {view,roots,points,nodes:nodes.filter(node=>selected.has(node.id)),ids:selected};
}

export interface DrawingClipboard { nodes:TemplateNodeV2[]; roots:string[]; source:TemplateContentV3; viewId:string }
/** A successful native copy replaces any old image. Later external copies are honored. */
export function useInternalDrawingClipboard(text:string, token:string|null, synchronized:boolean):boolean {
  return token!==null && (!synchronized || text===token);
}
export function copyDrawingSelection(content:TemplateContentV3,viewId:string,ids:readonly string[]):DrawingClipboard {
  const selected=selection(content,viewId,ids,false);
  return structuredClone({nodes:selected.nodes,roots:selected.roots.map(node=>node.id),source:content,viewId});
}

export function pasteDrawingSelection(content:TemplateContentV3,viewId:string,layerId:string,clipboard:DrawingClipboard,offset=-12):[TemplateContentV3,string[]] {
  const next=structuredClone(content),view=next.views.find(view=>view.id===viewId),layer=view?.layers.find(layer=>layer.id===layerId);
  if(!view || !layer || layer.locked) throw new Error("Выберите разблокированный слой.");
  if(clipboard.nodes.some(node=>node.kind==="image" && !content.assets.some(asset=>asset.assetId===node.geometry.assetId))) throw new Error("Изображение отсутствует в этой серии. Копируйте рисунок внутри исходной серии.");
  const ids=new Map<string,string>(clipboard.nodes.map(node=>[node.id,crypto.randomUUID()]));
  const nodes=structuredClone(clipboard.nodes).map(node=>{
    node.id=ids.get(node.id)!; node.layerId=layer.id;
    if(node.kind==="group") node.geometry.childIds=node.geometry.childIds.map(id=>ids.get(id)!);
    return node;
  });
  const roots=clipboard.roots.map(id=>ids.get(id)!);
  for(const node of nodes) if(roots.includes(node.id)) {
    node.transform.translateX=c(value(node.transform.translateX)+offset); node.transform.translateY=c(value(node.transform.translateY)+offset);
  }
  // Graphical repeat copies get their own domain. Electrical contacts are not duplicated.
  for(const placement of clipboard.source.views.find(v=>v.id===clipboard.viewId)!.repeatPlacements.filter(p=>ids.has(p.prototypeGroupId))) {
    const domain=clipboard.source.repeaters.find(d=>d.id===placement.repeatDomainId)!;
    if(domain.logicalContactIds.length) throw new Error("Копируйте фигуры вне электрического массива: контакт нельзя дублировать в одном виде.");
    const originalParameter=clipboard.source.parameters.find(p=>p.id===domain.countParameterId)!;
    const parameterId=crypto.randomUUID(),domainId=crypto.randomUUID();
    next.parameters.push({...structuredClone(originalParameter),id:parameterId});
    next.repeaters.push({...structuredClone(domain),id:domainId,countParameterId:parameterId});
    view.repeatPlacements.push({...structuredClone(placement),repeatDomainId:domainId,prototypeGroupId:ids.get(placement.prototypeGroupId)!,contactPointIds:[]});
    next.articleVariants.forEach(article=>{ const override=clipboard.source.articleVariants.find(a=>a.id===article.id)?.parameterValues.find(v=>v.parameterId===originalParameter.id); if(override) article.parameterValues.push({...override,parameterId}); });
  }
  layer.nodes.push(...nodes);
  return [validated(next),roots];
}

export function moveDrawingSelection(content:TemplateContentV3,viewId:string,ids:readonly string[],dx:number,dy:number):TemplateContentV3 {
  const chosen=selection(content,viewId,ids),next=structuredClone(content);
  const roots=new Set(chosen.roots.map(node=>node.id));
  for(const layer of next.views.find(view=>view.id===viewId)!.layers) for(const node of layer.nodes) if(roots.has(node.id)) {
    node.transform.translateX=c(value(node.transform.translateX)+dx); node.transform.translateY=c(value(node.transform.translateY)+dy);
  }
  const pointIds=new Set(chosen.points.map(p=>p.id));
  const view=next.views.find(v=>v.id===viewId)!;
  for(const point of [...view.contactPoints,...view.bundlePorts]) if(pointIds.has(point.id)) {point.x=c(value(point.x)+dx);point.y=c(value(point.y)+dy);}
  return validated(next);
}

/** Uniform world-space scaling preserves rotated shapes without introducing shear. */
export function stretchDrawingSelection(content:TemplateContentV3,viewId:string,ids:readonly string[],factor:number,anchor:{x:number;y:number}):TemplateContentV3 {
  if(!Number.isFinite(factor)||factor<=0) throw new Error("Размер выделения должен быть положительным.");
  const chosen=selection(content,viewId,ids),next=structuredClone(content),view=next.views.find(v=>v.id===viewId)!;
  if(chosen.view.repeatPlacements.some(p=>chosen.ids.has(p.prototypeGroupId))) throw new Error("Измените размеры массива в его настройках.");
  const roots=new Set(chosen.roots.map(n=>n.id)),points=new Set(chosen.points.map(p=>p.id));
  for(const layer of view.layers) for(const node of layer.nodes) if(roots.has(node.id)) {
    node.transform.translateX=c(anchor.x+(value(node.transform.translateX)-anchor.x)*factor);
    node.transform.translateY=c(anchor.y+(value(node.transform.translateY)-anchor.y)*factor);
    node.transform.scaleX=c(value(node.transform.scaleX)*factor);
    node.transform.scaleY=c(value(node.transform.scaleY)*factor);
  }
  for(const point of [...view.contactPoints,...view.bundlePorts]) if(points.has(point.id)) {
    point.x=c(anchor.x+(value(point.x)-anchor.x)*factor);point.y=c(anchor.y+(value(point.y)-anchor.y)*factor);
  }
  return validated(next);
}

export function rotateDrawingSelection(content:TemplateContentV3,viewId:string,ids:readonly string[],angle:number,center:{x:number;y:number}):TemplateContentV3 {
  const chosen=selection(content,viewId,ids),next=structuredClone(content),radians=angle*Math.PI/180;
  if(chosen.view.repeatPlacements.some(p=>chosen.ids.has(p.prototypeGroupId))) throw new Error("Сначала выделите фигуры вне массива для общего поворота.");
  const roots=new Set(chosen.roots.map(node=>node.id));
  for(const layer of next.views.find(view=>view.id===viewId)!.layers) for(const node of layer.nodes) if(roots.has(node.id)) {
    const x=value(node.transform.translateX)-center.x,y=value(node.transform.translateY)-center.y;
    node.transform.translateX=c(center.x+x*Math.cos(radians)-y*Math.sin(radians));
    node.transform.translateY=c(center.y+x*Math.sin(radians)+y*Math.cos(radians));
    node.transform.rotationDegrees=c(value(node.transform.rotationDegrees)+angle);
  }
  const pointIds=new Set(chosen.points.map(p=>p.id)),view=next.views.find(v=>v.id===viewId)!;
  for(const point of [...view.contactPoints,...view.bundlePorts]) if(pointIds.has(point.id)) {
    const x=value(point.x)-center.x,y=value(point.y)-center.y;
    point.x=c(center.x+x*Math.cos(radians)-y*Math.sin(radians));point.y=c(center.y+x*Math.sin(radians)+y*Math.cos(radians));
  }
  return validated(next);
}

export interface SelectionStyle { stroke?:Partial<StrokeV2>; fill?:Partial<FillV2>; opacity?:number; clearHatch?:boolean }
export function drawingStyleLeaves(nodes:readonly TemplateNodeV2[],all:readonly TemplateNodeV2[]):TemplateNodeV2[] {
  const visited=new Set<string>();
  const visit=(node:TemplateNodeV2):TemplateNodeV2[]=>{
    if(visited.has(node.id)) return []; visited.add(node.id);
    return node.kind!=="group" ? [node] : node.geometry.childIds.flatMap(id=>{const child=all.find(n=>n.id===id);return child ? visit(child) : [];});
  };
  return nodes.flatMap(visit);
}
export function styleDrawingSelection(content:TemplateContentV3,viewId:string,ids:readonly string[],style:SelectionStyle):TemplateContentV3 {
  const chosen=selection(content,viewId,ids),next=structuredClone(content),roots=new Set(chosen.roots.map(node=>node.id));
  for(const layer of next.views.find(view=>view.id===viewId)!.layers) for(const node of layer.nodes) if(chosen.ids.has(node.id)) {
    if(style.opacity!==undefined && roots.has(node.id)) node.opacity=style.opacity;
    if(node.kind==="group") continue;
    if(style.stroke) node.stroke={...node.stroke,...style.stroke};
    if(style.fill && (node.kind==="text" || node.kind==="rectangle" || node.kind==="ellipse" || node.kind==="closedContour" || node.kind==="bezier" && node.geometry.closed)) node.fill={...node.fill,...style.fill};
    if(style.clearHatch) delete node.fill.hatch;
  }
  return validated(next);
}

export function deleteDrawingSelection(content:TemplateContentV3,viewId:string,ids:readonly string[]):TemplateContentV3 {
  const chosen=selection(content,viewId,ids),next=structuredClone(content),view=next.views.find(view=>view.id===viewId)!;
  const removedPlacements=view.repeatPlacements.filter(p=>chosen.ids.has(p.prototypeGroupId));
  const pointIds=new Set(chosen.points.map(p=>p.id));
  view.contactPoints=view.contactPoints.filter(p=>!pointIds.has(p.id) && !(p.shape && chosen.ids.has(p.shape.nodeId)));
  view.bundlePorts=view.bundlePorts.filter(p=>!pointIds.has(p.id));
  view.layers.forEach(layer=>{layer.nodes=layer.nodes.filter(node=>!chosen.ids.has(node.id));});
  view.repeatPlacements=view.repeatPlacements.filter(p=>!chosen.ids.has(p.prototypeGroupId));
  const removedDomains=new Set(removedPlacements.filter(p=>!next.views.some(v=>v.repeatPlacements.some(other=>other.repeatDomainId===p.repeatDomainId))).map(p=>p.repeatDomainId));
  next.repeaters=next.repeaters.filter(domain=>!removedDomains.has(domain.id));
  return validated(next);
}

export function drawingKeyboardAction(event:Pick<KeyboardEvent,"key"|"ctrlKey"|"metaKey"|"altKey"|"shiftKey"> & {target?:EventTarget|null}):"copy"|"paste"|"delete"|null {
  const target=event.target as {tagName?:string;isContentEditable?:boolean}|null;
  if(target?.isContentEditable || ["input","textarea","select"].includes(target?.tagName?.toLowerCase() ?? "") || event.altKey || event.shiftKey) return null;
  const modifier=event.ctrlKey || event.metaKey;
  return modifier && event.key.toLowerCase()==="c" ? "copy" : modifier && event.key.toLowerCase()==="v" ? "paste" : !modifier && event.key==="Delete" ? "delete" : null;
}
