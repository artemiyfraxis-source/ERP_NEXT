import { constantExpressionV3 as c, projectTemplateContentV3CoreToV2 } from "./template-commands-v3";
import { validateTemplateContentV3Structure, type TemplateContentV3 } from "./template-model-v3";
import { expandTemplateRepeatsV2 } from "./template-repeat-v2";
import type { DrawingArrayLayout } from "./array-layout";

export interface DrawingArrayInput extends DrawingArrayLayout { count: number; pitchX: number; pitchY: number; }

/** Independent drawing counts: the electrical table remains the authority for pin totals. */
export function withDrawingArticleCounts(content: TemplateContentV3): TemplateContentV3 {
  const domains = new Set(content.views.flatMap(view => view.repeatPlacements.filter(p => p.arrayLayout?.countSource === "article").map(p => p.repeatDomainId)));
  const arrayDomains = content.repeaters.filter(domain => domains.has(domain.id));
  const parameters = arrayDomains.map(domain => domain.countParameterId);
  if (!parameters.length) return content;
  return { ...content, articleVariants: content.articleVariants.map(article => {
    const count = article.contactGroups?.reduce((sum, group) => sum + group.contactCount, 0) ?? 0;
    if (!count) return article;
    return { ...article, parameterValues: [...article.parameterValues.filter(value => !parameters.includes(value.parameterId)), ...arrayDomains.map(domain => ({ parameterId:domain.countParameterId, value:Math.ceil(count/Math.max(1,content.views.flatMap(v=>v.repeatPlacements).find(p=>p.repeatDomainId===domain.id)?.contactPointIds.length??0)) }))] };
  }) };
}

/** Repeats geometry and point representations; the E4 table owns electrical identities. */
export function setDrawingArray(content: TemplateContentV3, viewId: string, layerId: string, selectedIds: readonly string[], input: DrawingArrayInput, existingDomainId?: string): TemplateContentV3 {
  const next = structuredClone(content);
  const view = next.views.find(v => v.id === viewId);
  const layer = view?.layers.find(l => l.id === layerId);
  if (!view || view.kind === "e4" || !layer || layer.locked) throw new Error("Выберите разблокированный слой рисунка.");
  let placement = view.repeatPlacements.find(p => p.repeatDomainId === existingDomainId);
  if (!placement) {
    const owned = new Set(layer.nodes.flatMap(n => n.kind === "group" ? n.geometry.childIds : []));
    const nodes = layer.nodes.filter(n => selectedIds.includes(n.id) && !owned.has(n.id));
    const points = view.contactPoints.filter(p=>selectedIds.includes(p.id));
    if(points.some(p=>view.repeatPlacements.some(r=>r.contactPointIds.includes(p.id)))) throw new Error("Контакт уже включён в другой массив.");
    if (!nodes.length || nodes.some(n => n.locked)) throw new Error("Выберите объекты одного элемента массива.");
    const repeated = new Set(view.repeatPlacements.map(p => p.prototypeGroupId));
    if (nodes.some(n => owned.has(n.id) || repeated.has(n.id))) throw new Error("Выберите свободные объекты или группу вне другого массива.");
    const groupId = crypto.randomUUID(), parameterId = crypto.randomUUID(), domainId = crypto.randomUUID();
    layer.nodes.push({ id:groupId, kind:"group", layerId, visible:true, locked:false, opacity:1,
      transform:{translateX:c(0),translateY:c(0),rotationDegrees:c(0),scaleX:c(1),scaleY:c(1)},
      stroke:{color:"#000000",width:c(0)},fill:{color:null},geometry:{childIds:nodes.map(n=>n.id)} });
    next.parameters.push({id:parameterId,name:"Элементов рисунка",type:"integer",unit:"шт",defaultValue:input.count,minimum:1,maximum:1000,formula:null});
    next.repeaters.push({id:domainId,countParameterId:parameterId,logicalContactIds:[]});
    placement = {repeatDomainId:domainId,prototypeGroupId:groupId,step:{x:c(input.pitchX),y:c(input.pitchY)},contactPointIds:points.map(p=>p.id)};
    view.repeatPlacements.push(placement);
  }
  placement.arrayLayout = {rows:input.rows,direction:input.direction,numbering:input.numbering,countSource:input.countSource};
  placement.step = {x:c(input.pitchX),y:c(input.pitchY)};
  const domain = next.repeaters.find(d=>d.id===placement!.repeatDomainId)!;
  next.parameters.find(p=>p.id===domain.countParameterId)!.defaultValue=input.count;
  const result = withDrawingArticleCounts(next);
  const validation = validateTemplateContentV3Structure(result);
  if (!validation.valid) throw new Error(validation.diagnostics[0]!.message);
  const core = projectTemplateContentV3CoreToV2(result);
  expandTemplateRepeatsV2(core);
  for (const article of result.articleVariants) expandTemplateRepeatsV2(core, {articlePreset:article.id});
  return result;
}
