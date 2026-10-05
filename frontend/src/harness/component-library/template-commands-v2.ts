import {
  TEMPLATE_V2_LIMITS,
  validateTemplateContentV2,
  type BundlePortV2,
  type LayerV2,
  type ContactDirectionV2,
  type LogicalContactV2,
  type NumericExpressionV2,
  type ParameterValueV2,
  type PointExpressionV2,
  type TemplateContentV2,
  type TemplateNodeV2,
  type TemplateViewV2,
  type TransformV2,
  type ViewContactPointV2,
} from "./template-model-v2";
import { expandTemplateRepeatsV2, TemplateRepeatV2Error } from "./template-repeat-v2";

export type BasicNodeKindV2 = "line" | "polyline" | "rectangle" | "ellipse" | "bezier" | "closedContour" | "text";
export type NodeResizeHandleV2 = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w" | "start" | "end";
export type NodeEditV2 = Partial<Pick<TemplateNodeV2, "visible" | "locked" | "opacity" | "transform" | "stroke" | "fill">> & {
  geometry?: TemplateNodeV2["geometry"];
};
export type ExpressionValuesV2 = ReadonlyMap<string, ParameterValueV2> | Readonly<Record<string, ParameterValueV2>>;
export interface NewContactPointV2 {
  number?: string;
  name?: string;
  contactType?: string;
  x?: NumericExpressionV2;
  y?: NumericExpressionV2;
  direction?: ContactDirectionV2;
}
export interface LogicalContactEditV2 {
  number?: string;
  name?: string;
  contactType?: string;
}
export interface ContactPointEditV2 {
  x?: NumericExpressionV2;
  y?: NumericExpressionV2;
  direction?: ContactDirectionV2;
}
export interface NewBundlePortV2 {
  name?: string;
  x?: NumericExpressionV2;
  y?: NumericExpressionV2;
  direction?: ContactDirectionV2;
}
export interface BundlePortEditV2 {
  name?: string;
  x?: NumericExpressionV2;
  y?: NumericExpressionV2;
  direction?: ContactDirectionV2;
}
export interface CreateRepeatPrototypeV2Input {
  readonly viewId: string;
  readonly layerId: string;
  readonly prototypeNodeId: string;
  readonly prototypePointId: string;
  readonly count?: number;
  readonly step: PointExpressionV2;
}
export interface RepeatPrototypeIdsV2 {
  readonly groupId: string;
  readonly countParameterId: string;
  readonly repeatDomainId: string;
}
export interface AttachRepeatDomainV2Input {
  readonly viewId: string;
  readonly layerId: string;
  readonly prototypeNodeId: string;
  readonly repeatDomainId: string;
  readonly contactPointIds?: readonly string[];
  readonly step: PointExpressionV2;
}
export type ParameterizableNodeDimensionV2 = "width" | "height" | "radiusX" | "radiusY";
export interface ParameterizeNodeDimensionV2Input {
  readonly name: string;
  readonly unit?: string | null;
  readonly defaultValue: number;
  readonly minimum: number;
  readonly maximum: number;
}

export class TemplateCommandV2Error extends Error {
  constructor(readonly code: string, message: string) {
    super(message);
    this.name = "TemplateCommandV2Error";
  }
}

export function constantExpressionV2(value: number): NumericExpressionV2 {
  if (!Number.isFinite(value) || Math.abs(value) > TEMPLATE_V2_LIMITS.coordinate)
    throw new TemplateCommandV2Error("invalid_number", "Константа должна быть конечной и находиться в допустимом диапазоне.");
  return { kind: "constant", value };
}

export function constantExpressionValueV2(expression: NumericExpressionV2): number | null {
  return expression.kind === "constant" ? expression.value : null;
}

export function evaluateNumericExpressionV2(expression: NumericExpressionV2, values: ExpressionValuesV2 = {}): number {
  let nodes = 0;
  const readValue = (id: string) => typeof (values as ReadonlyMap<string, ParameterValueV2>).get === "function" ?
    (values as ReadonlyMap<string, ParameterValueV2>).get(id) : (values as Readonly<Record<string, ParameterValueV2>>)[id];
  const visit = (current: NumericExpressionV2, depth: number): number => {
    nodes++;
    if (depth > TEMPLATE_V2_LIMITS.expressionDepth || nodes > TEMPLATE_V2_LIMITS.expressionNodes)
      throw new TemplateCommandV2Error("expression_limit", "Выражение превышает допустимую глубину или размер.");
    let result: number;
    switch (current.kind) {
      case "constant": result = current.value; break;
      case "parameter": {
        const value = readValue(current.parameterId);
        if (typeof value !== "number" || !Number.isFinite(value))
          throw new TemplateCommandV2Error("parameter_value", `Нет числового значения параметра ${current.parameterId}.`);
        result = value;
        break;
      }
      case "negate": result = -visit(current.operand, depth + 1); break;
      case "binary": {
        const left = visit(current.left, depth + 1), right = visit(current.right, depth + 1);
        if (current.operator === "divide" && right === 0)
          throw new TemplateCommandV2Error("division_by_zero", "Деление на ноль запрещено.");
        result = current.operator === "add" ? left + right : current.operator === "subtract" ? left - right :
          current.operator === "multiply" ? left * right : left / right;
        break;
      }
    }
    if (!Number.isFinite(result) || Math.abs(result) > TEMPLATE_V2_LIMITS.coordinate)
      throw new TemplateCommandV2Error("expression_result", "Результат выражения выходит за допустимый диапазон.");
    return result;
  };
  return visit(expression, 1);
}

const identityTransform = (): TransformV2 => ({
  translateX: constantExpressionV2(0), translateY: constantExpressionV2(0),
  rotationDegrees: constantExpressionV2(0), scaleX: constantExpressionV2(1), scaleY: constantExpressionV2(1),
});

const newLayer = (): LayerV2 => ({ id: crypto.randomUUID(), name: "Основной", visible: true, locked: false, nodes: [] });
const newView = (kind: "e4" | "drawing", name: string): TemplateViewV2 => ({
  id: crypto.randomUUID(), name, kind, layers: [newLayer()], contactPoints: [], bundlePorts: [], repeatPlacements: [],
});

export function newTemplateContentV2(): TemplateContentV2 {
  const content: TemplateContentV2 = {
    schemaVersion: 2,
    views: [newView("e4", "Схема Э4"), newView("drawing", "Чертёж")],
    logicalContacts: [], parameters: [], repeaters: [], assets: [], articleParameterPresets: [],
  };
  if (!validateTemplateContentV2(content).valid)
    throw new TemplateCommandV2Error("invalid_initial_content", "Не удалось создать корректный шаблон v2.");
  return content;
}

export function addAdditionalViewV2(content: TemplateContentV2, name = ""): [TemplateContentV2, string] {
  if (content.views.length >= TEMPLATE_V2_LIMITS.views)
    throw new TemplateCommandV2Error("view_limit", "Достигнут лимит видов.");
  const view: TemplateViewV2 = {
    id: crypto.randomUUID(), name: normalizedName(name, `Дополнительный вид ${content.views.length - 1}`),
    kind: "additional", layers: [newLayer()], contactPoints: [], bundlePorts: [], repeatPlacements: [],
  };
  return [{ ...content, views: [...content.views, view] }, view.id];
}

export function renameViewV2(content: TemplateContentV2, viewId: string, name: string): TemplateContentV2 {
  const view = requireView(content, viewId);
  return replaceView(content, viewId, { ...view, name: normalizedName(name) });
}

export function deleteAdditionalViewV2(content: TemplateContentV2, viewId: string): TemplateContentV2 {
  const view = requireView(content, viewId);
  if (view.kind !== "additional")
    throw new TemplateCommandV2Error("mandatory_view", "Виды Э4 и Чертёж удалять нельзя.");
  return { ...content, views: content.views.filter(item => item.id !== viewId) };
}

export function addContactPointV2(
  content: TemplateContentV2,
  viewId: string,
  initial: NewContactPointV2 = {},
): [TemplateContentV2, string] {
  const view = requireView(content, viewId);
  if (content.logicalContacts.length >= TEMPLATE_V2_LIMITS.contacts)
    throw new TemplateCommandV2Error("contact_limit", "Достигнут лимит логических контактов.");
  const number = initial.number === undefined ? nextContactNumber(content) : normalizedContactNumber(initial.number);
  requireUniqueContactNumber(content, number);
  const logicalContact: LogicalContactV2 = {
    id: crypto.randomUUID(),
    number,
    name: initial.name === undefined ? `Контакт ${number}` : normalizedName(initial.name),
    contactType: normalizedContactType(initial.contactType ?? ""),
  };
  const point: ViewContactPointV2 = {
    id: crypto.randomUUID(),
    logicalContactId: logicalContact.id,
    x: initial.x ?? constantExpressionV2(260),
    y: initial.y ?? constantExpressionV2(200),
    direction: normalizedContactDirection(initial.direction ?? "right"),
  };
  const nextView = { ...view, contactPoints: [...view.contactPoints, point] };
  const next = { ...replaceView(content, viewId, nextView), logicalContacts: [...content.logicalContacts, logicalContact] };
  requireMaterializableRepeats(next);
  return [next, point.id];
}

/** Adds a view-specific point for an existing logical contact without copying the logical record. */
export function linkLogicalContactPointV2(
  content: TemplateContentV2,
  viewId: string,
  logicalContactId: string,
  initial: ContactPointEditV2 = {},
): [TemplateContentV2, string] {
  const view = requireView(content, viewId);
  requireLogicalContact(content, logicalContactId);
  if (view.contactPoints.some(point => point.logicalContactId === logicalContactId))
    throw new TemplateCommandV2Error("duplicate_contact_point", "В выбранном виде уже есть точка этого логического контакта.");
  const point: ViewContactPointV2 = {
    id: crypto.randomUUID(),
    logicalContactId,
    x: initial.x ?? constantExpressionV2(260),
    y: initial.y ?? constantExpressionV2(200),
    direction: normalizedContactDirection(initial.direction ?? "right"),
  };
  const next = replaceView(content, view.id, { ...view, contactPoints: [...view.contactPoints, point] });
  requireValidCommandResult(next, "invalid_contact_link");
  requireMaterializableRepeats(next);
  return [next, point.id];
}

export function editLogicalContactV2(
  content: TemplateContentV2,
  logicalContactId: string,
  changes: LogicalContactEditV2,
): TemplateContentV2 {
  const logicalContact = requireLogicalContact(content, logicalContactId);
  const number = changes.number === undefined ? logicalContact.number : normalizedContactNumber(changes.number);
  if (number !== logicalContact.number) requireUniqueContactNumber(content, number, logicalContact.id);
  const nextLogicalContact: LogicalContactV2 = {
    ...logicalContact,
    number,
    name: changes.name === undefined ? logicalContact.name : normalizedName(changes.name),
    contactType: changes.contactType === undefined ? logicalContact.contactType : normalizedContactType(changes.contactType),
  };
  const next = { ...content, logicalContacts: content.logicalContacts.map(item => item.id === logicalContact.id ? nextLogicalContact : item) };
  requireMaterializableRepeats(next);
  return next;
}

export function editContactPointV2(
  content: TemplateContentV2,
  viewId: string,
  pointId: string,
  changes: ContactPointEditV2,
): TemplateContentV2 {
  const { view, point } = requireContactPoint(content, viewId, pointId);
  const nextPoint: ViewContactPointV2 = {
    ...point,
    x: changes.x ?? point.x,
    y: changes.y ?? point.y,
    direction: changes.direction === undefined ? point.direction : normalizedContactDirection(changes.direction),
  };
  return replaceView(content, viewId, { ...view, contactPoints: view.contactPoints.map(item => item.id === pointId ? nextPoint : item) });
}

export function deleteContactPointV2(content: TemplateContentV2, viewId: string, pointId: string): TemplateContentV2 {
  const { view, point } = requireContactPoint(content, viewId, pointId);
  const nextView: TemplateViewV2 = {
    ...view,
    contactPoints: view.contactPoints.filter(item => item.id !== pointId),
    repeatPlacements: view.repeatPlacements.map(placement => ({
      ...placement,
      contactPointIds: placement.contactPointIds.filter(id => id !== pointId),
    })),
  };
  const withoutPoint = replaceView(content, viewId, nextView);
  const isReferenced = withoutPoint.views.some(item =>
    item.contactPoints.some(candidate => candidate.logicalContactId === point.logicalContactId)) ||
    withoutPoint.repeaters.some(repeater => repeater.logicalContactIds.includes(point.logicalContactId));
  return isReferenced ? withoutPoint : {
    ...withoutPoint,
    logicalContacts: withoutPoint.logicalContacts.filter(item => item.id !== point.logicalContactId),
  };
}

export function addBundlePortV2(
  content: TemplateContentV2,
  viewId: string,
  initial: NewBundlePortV2 = {},
): [TemplateContentV2, string] {
  const view = requireView(content, viewId);
  if (view.bundlePorts.length >= TEMPLATE_V2_LIMITS.contacts)
    throw new TemplateCommandV2Error("bundle_port_limit", "Достигнут лимит общих точек жгута в виде.");
  const port: BundlePortV2 = {
    id: crypto.randomUUID(),
    name: normalizedName(initial.name ?? "", `Порт жгута ${view.bundlePorts.length + 1}`),
    x: initial.x ?? constantExpressionV2(260),
    y: initial.y ?? constantExpressionV2(200),
    direction: normalizedContactDirection(initial.direction ?? "right"),
  };
  const next = replaceView(content, view.id, { ...view, bundlePorts: [...view.bundlePorts, port] });
  requireValidCommandResult(next, "invalid_bundle_port");
  requireMaterializableRepeats(next);
  return [next, port.id];
}

export function editBundlePortV2(
  content: TemplateContentV2,
  viewId: string,
  portId: string,
  changes: BundlePortEditV2,
): TemplateContentV2 {
  const { view, port } = requireBundlePort(content, viewId, portId);
  const nextPort: BundlePortV2 = {
    ...port,
    name: changes.name === undefined ? port.name : normalizedName(changes.name),
    x: changes.x ?? port.x,
    y: changes.y ?? port.y,
    direction: changes.direction === undefined ? port.direction : normalizedContactDirection(changes.direction),
  };
  const next = replaceView(content, view.id, {
    ...view,
    bundlePorts: view.bundlePorts.map(candidate => candidate.id === port.id ? nextPort : candidate),
  });
  requireValidCommandResult(next, "invalid_bundle_port");
  requireMaterializableRepeats(next);
  return next;
}

export function deleteBundlePortV2(content: TemplateContentV2, viewId: string, portId: string): TemplateContentV2 {
  const { view } = requireBundlePort(content, viewId, portId);
  const next = replaceView(content, view.id, {
    ...view,
    bundlePorts: view.bundlePorts.filter(candidate => candidate.id !== portId),
  });
  requireValidCommandResult(next, "invalid_bundle_port_delete");
  requireMaterializableRepeats(next);
  return next;
}

export function createRepeatPrototypeV2(
  content: TemplateContentV2,
  input: CreateRepeatPrototypeV2Input,
): [TemplateContentV2, RepeatPrototypeIdsV2] {
  const { view, layer, node } = requireNode(content, input.viewId, input.layerId, input.prototypeNodeId);
  requireEditableNode(layer, node);
  if (node.kind === "group")
    throw new TemplateCommandV2Error("repeat_prototype_node", "Повторяемым сегментом должен быть отдельный объект, а не группа.");
  if (layer.nodes.some(candidate => candidate.kind === "group" && candidate.geometry.childIds.includes(node.id)))
    throw new TemplateCommandV2Error("repeat_prototype_nested", "Повторяемый объект должен находиться на верхнем уровне слоя.");
  if (content.views.some(candidate => candidate.repeatPlacements.some(placement =>
    placement.prototypeGroupId === node.id || placement.contactPointIds.includes(input.prototypePointId))))
    throw new TemplateCommandV2Error("repeat_prototype_used", "Объект или точка уже участвуют в повторе.");

  const { point } = requireContactPoint(content, input.viewId, input.prototypePointId);
  if (content.repeaters.some(domain => domain.logicalContactIds.includes(point.logicalContactId)))
    throw new TemplateCommandV2Error("repeat_contact_used", "Логический контакт уже участвует в повторе.");
  const count = input.count ?? 2;
  requireRepeatCount(count);
  if (content.parameters.length >= TEMPLATE_V2_LIMITS.parameters)
    throw new TemplateCommandV2Error("parameter_limit", "Достигнут лимит параметров.");
  if (content.repeaters.length >= TEMPLATE_V2_LIMITS.repeaters)
    throw new TemplateCommandV2Error("repeater_limit", "Достигнут лимит повторов.");

  const ids: RepeatPrototypeIdsV2 = {
    groupId: crypto.randomUUID(),
    countParameterId: crypto.randomUUID(),
    repeatDomainId: crypto.randomUUID(),
  };
  const group: TemplateNodeV2 = {
    id: ids.groupId,
    kind: "group",
    layerId: layer.id,
    visible: true,
    locked: false,
    opacity: 1,
    transform: identityTransform(),
    stroke: { color: "#27445a", width: constantExpressionV2(2) },
    fill: { color: null },
    geometry: { childIds: [node.id] },
  };
  const nodeIndex = layer.nodes.findIndex(candidate => candidate.id === node.id);
  const nodes = [...layer.nodes];
  nodes.splice(nodeIndex + 1, 0, group);
  const nextView: TemplateViewV2 = {
    ...view,
    layers: view.layers.map(candidate => candidate.id === layer.id ? { ...layer, nodes } : candidate),
    repeatPlacements: [...view.repeatPlacements, {
      repeatDomainId: ids.repeatDomainId,
      prototypeGroupId: ids.groupId,
      step: input.step,
      contactPointIds: [point.id],
    }],
  };
  const next: TemplateContentV2 = {
    ...content,
    views: content.views.map(candidate => candidate.id === view.id ? nextView : candidate),
    parameters: [...content.parameters, {
      id: ids.countParameterId,
      name: "Количество повторов",
      type: "integer",
      unit: "шт",
      defaultValue: count,
      minimum: 1,
      maximum: 1_000,
      formula: null,
    }],
    repeaters: [...content.repeaters, {
      id: ids.repeatDomainId,
      countParameterId: ids.countParameterId,
      logicalContactIds: [point.logicalContactId],
    }],
  };
  requireValidCommandResult(next, "invalid_repeat_prototype");
  requireMaterializableRepeats(next);
  return [next, ids];
}

/** Places an existing repeat domain in another view using already-linked contact points. */
export function attachRepeatDomainV2(
  content: TemplateContentV2,
  input: AttachRepeatDomainV2Input,
): [TemplateContentV2, string] {
  const domain = content.repeaters.find(candidate => candidate.id === input.repeatDomainId);
  if (!domain) throw new TemplateCommandV2Error("repeat_domain_not_found", "Домен повтора не найден.");
  const { view, layer, node } = requireNode(content, input.viewId, input.layerId, input.prototypeNodeId);
  requireEditableNode(layer, node);
  if (view.repeatPlacements.some(placement => placement.repeatDomainId === domain.id))
    throw new TemplateCommandV2Error("duplicate_repeat_placement", "В выбранном виде уже есть размещение этого домена повтора.");
  if (layer.nodes.some(candidate => candidate.kind === "group" && candidate.geometry.childIds.includes(node.id)))
    throw new TemplateCommandV2Error("repeat_prototype_nested", "Прототип повтора должен находиться на верхнем уровне слоя.");

  const linkedByLogicalId = new Map(view.contactPoints.map(point => [point.logicalContactId, point]));
  const inferredPointIds = domain.logicalContactIds.map(logicalContactId => {
    const point = linkedByLogicalId.get(logicalContactId);
    if (!point)
      throw new TemplateCommandV2Error("repeat_contact_missing", "В выбранном виде отсутствует точка логического контакта домена повтора.");
    return point.id;
  });
  const requestedPointIds = input.contactPointIds === undefined ? inferredPointIds : [...input.contactPointIds];
  if (new Set(requestedPointIds).size !== requestedPointIds.length)
    throw new TemplateCommandV2Error("duplicate_reference", "Точка прототипа повтора указана повторно.");
  if (requestedPointIds.length !== inferredPointIds.length ||
      requestedPointIds.some(pointId => !inferredPointIds.includes(pointId)))
    throw new TemplateCommandV2Error("repeat_contact_mismatch", "Размещение должно включать по одной точке каждого контакта домена повтора.");
  if (content.views.some(candidate => candidate.repeatPlacements.some(placement =>
    placement.contactPointIds.some(pointId => requestedPointIds.includes(pointId)))))
    throw new TemplateCommandV2Error("repeat_contact_used", "Одна из точек уже участвует в другом размещении повтора.");

  const repeatedGroupIds = new Set(content.views.flatMap(candidate =>
    candidate.repeatPlacements.map(placement => placement.prototypeGroupId)));
  const descendants = node.kind === "group" ? groupDescendantIds(layer, node.id) : new Set<string>();
  if (repeatedGroupIds.has(node.id) || [...descendants].some(id => repeatedGroupIds.has(id)))
    throw new TemplateCommandV2Error("repeat_prototype_used", "Объект или вложенная группа уже участвует в повторе.");

  let prototypeGroupId = node.id;
  let nodes = layer.nodes;
  if (node.kind !== "group") {
    prototypeGroupId = crypto.randomUUID();
    const group: TemplateNodeV2 = {
      id: prototypeGroupId,
      kind: "group",
      layerId: layer.id,
      visible: true,
      locked: false,
      opacity: 1,
      transform: identityTransform(),
      stroke: { color: "#27445a", width: constantExpressionV2(2) },
      fill: { color: null },
      geometry: { childIds: [node.id] },
    };
    const nodeIndex = layer.nodes.findIndex(candidate => candidate.id === node.id);
    nodes = [...layer.nodes];
    nodes.splice(nodeIndex + 1, 0, group);
  }
  const pointOrder = new Map(inferredPointIds.map((id, index) => [id, index]));
  const contactPointIds = [...requestedPointIds].sort((left, right) => pointOrder.get(left)! - pointOrder.get(right)!);
  const nextView: TemplateViewV2 = {
    ...view,
    layers: view.layers.map(candidate => candidate.id === layer.id ? { ...layer, nodes } : candidate),
    repeatPlacements: [...view.repeatPlacements, {
      repeatDomainId: domain.id,
      prototypeGroupId,
      step: input.step,
      contactPointIds,
    }],
  };
  const next = replaceView(content, view.id, nextView);
  requireValidCommandResult(next, "invalid_repeat_attachment");
  requireMaterializableRepeats(next);
  return [next, prototypeGroupId];
}

export function deleteRepeatPrototypeV2(
  content: TemplateContentV2,
  repeatDomainId: string,
): TemplateContentV2 {
  const domain = content.repeaters.find(candidate => candidate.id === repeatDomainId);
  if (!domain) throw new TemplateCommandV2Error("repeat_domain_not_found", "Домен повтора не найден.");
  if (!content.views.some(view => view.repeatPlacements.some(placement => placement.repeatDomainId === repeatDomainId)))
    throw new TemplateCommandV2Error("repeat_placement_not_found", "Размещение повтора не найдено.");
  const views = content.views.map(view => {
    const placements = view.repeatPlacements.filter(placement => placement.repeatDomainId === repeatDomainId);
    if (placements.length === 0) return view;
    const groupIds = new Set(placements.map(placement => placement.prototypeGroupId));
    for (const groupId of groupIds) {
      const containingLayer = view.layers.find(layer => layer.nodes.some(node => node.id === groupId));
      const group = containingLayer?.nodes.find(node => node.id === groupId);
      if (!containingLayer || !group || group.kind !== "group" || group.geometry.childIds.length === 0)
        throw new TemplateCommandV2Error("repeat_prototype_group", "Группа повторяемого сегмента повреждена.");
      requireEditableNode(containingLayer, group);
      if (group.geometry.childIds.some(childId => !containingLayer.nodes.some(node => node.id === childId)))
        throw new TemplateCommandV2Error("repeat_prototype_child", "Исходный объект повторяемого сегмента не найден.");
    }
    return {
      ...view,
      layers: view.layers.map(layer => ({ ...layer, nodes: layer.nodes.filter(node => !groupIds.has(node.id)) })),
      repeatPlacements: view.repeatPlacements.filter(placement => placement.repeatDomainId !== repeatDomainId),
    };
  });
  const next: TemplateContentV2 = {
    ...content,
    views,
    repeaters: content.repeaters.filter(candidate => candidate.id !== repeatDomainId),
    parameters: content.parameters.filter(parameter => parameter.id !== domain.countParameterId),
    articleParameterPresets: content.articleParameterPresets.map(preset => ({
      ...preset,
      values: preset.values.filter(value => value.parameterId !== domain.countParameterId),
    })),
  };
  requireValidCommandResult(next, "invalid_repeat_delete");
  return next;
}

export function setRepeatCountV2(
  content: TemplateContentV2,
  repeatDomainId: string,
  count: number,
): TemplateContentV2 {
  requireRepeatCount(count);
  const domain = content.repeaters.find(candidate => candidate.id === repeatDomainId);
  if (!domain) throw new TemplateCommandV2Error("repeat_domain_not_found", "Домен повтора не найден.");
  const parameter = content.parameters.find(candidate => candidate.id === domain.countParameterId);
  if (!parameter || parameter.type !== "integer" || parameter.formula !== null)
    throw new TemplateCommandV2Error("repeat_count_parameter", "Параметр количества повтора повреждён.");
  const next: TemplateContentV2 = {
    ...content,
    parameters: content.parameters.map(candidate => candidate.id === parameter.id ? { ...candidate, defaultValue: count } : candidate),
  };
  requireValidCommandResult(next, "invalid_repeat_count");
  requireMaterializableRepeats(next);
  return next;
}

export function setRepeatStepV2(
  content: TemplateContentV2,
  viewId: string,
  repeatDomainId: string,
  step: PointExpressionV2,
): TemplateContentV2 {
  const view = requireView(content, viewId);
  if (!view.repeatPlacements.some(candidate => candidate.repeatDomainId === repeatDomainId))
    throw new TemplateCommandV2Error("repeat_placement_not_found", "Размещение повтора не найдено.");
  const next = replaceView(content, view.id, {
    ...view,
    repeatPlacements: view.repeatPlacements.map(candidate => candidate.repeatDomainId === repeatDomainId ? { ...candidate, step } : candidate),
  });
  requireValidCommandResult(next, "invalid_repeat_step");
  requireMaterializableRepeats(next);
  return next;
}

export function addLayerV2(content: TemplateContentV2, viewId: string, name: string): [TemplateContentV2, string] {
  const view = requireView(content, viewId);
  if (view.layers.length >= TEMPLATE_V2_LIMITS.layers)
    throw new TemplateCommandV2Error("layer_limit", "Достигнут лимит слоёв.");
  const layer: LayerV2 = { id: crypto.randomUUID(), name: normalizedName(name, `Слой ${view.layers.length + 1}`), visible: true, locked: false, nodes: [] };
  return [replaceView(content, viewId, { ...view, layers: [...view.layers, layer] }), layer.id];
}

export function renameLayerV2(content: TemplateContentV2, viewId: string, layerId: string, name: string): TemplateContentV2 {
  const { view, layer } = requireLayer(content, viewId, layerId);
  requireUnlockedLayer(layer);
  return replaceLayer(content, view, layerId, { ...layer, name: normalizedName(name) });
}

export function reorderLayerV2(content: TemplateContentV2, viewId: string, layerId: string, toIndex: number): TemplateContentV2 {
  const view = requireView(content, viewId);
  const fromIndex = view.layers.findIndex(layer => layer.id === layerId);
  if (fromIndex < 0) throw new TemplateCommandV2Error("layer_not_found", "Слой не найден.");
  requireUnlockedLayer(view.layers[fromIndex]!);
  const next = reordered(view.layers, fromIndex, toIndex);
  return next === view.layers ? content : replaceView(content, viewId, { ...view, layers: next });
}

export function setLayerVisibleV2(content: TemplateContentV2, viewId: string, layerId: string, visible: boolean): TemplateContentV2 {
  const { view, layer } = requireLayer(content, viewId, layerId);
  return layer.visible === visible ? content : replaceLayer(content, view, layerId, { ...layer, visible });
}

export function setLayerLockedV2(content: TemplateContentV2, viewId: string, layerId: string, locked: boolean): TemplateContentV2 {
  const { view, layer } = requireLayer(content, viewId, layerId);
  return layer.locked === locked ? content : replaceLayer(content, view, layerId, { ...layer, locked });
}

export function deleteLayerV2(content: TemplateContentV2, viewId: string, layerId: string): TemplateContentV2 {
  const view = requireView(content, viewId);
  if (view.layers.length === 1)
    throw new TemplateCommandV2Error("last_layer", "В виде должен остаться хотя бы один слой.");
  const layer = view.layers.find(item => item.id === layerId);
  if (!layer) throw new TemplateCommandV2Error("layer_not_found", "Слой не найден.");
  requireUnlockedLayer(layer);
  const nodeIds = new Set(layer.nodes.map(node => node.id));
  if (view.repeatPlacements.some(placement => nodeIds.has(placement.prototypeGroupId)))
    throw new TemplateCommandV2Error("layer_referenced", "Слой содержит группу-прототип повтора.");
  return replaceView(content, viewId, { ...view, layers: view.layers.filter(item => item.id !== layerId), contactPoints:view.contactPoints.filter(p=>!p.shape || !nodeIds.has(p.shape.nodeId)) });
}

export function addNodeV2(content: TemplateContentV2, viewId: string, layerId: string, node: TemplateNodeV2, atIndex?: number): TemplateContentV2 {
  const { view, layer } = requireLayer(content, viewId, layerId);
  requireUnlockedLayer(layer);
  if (node.layerId !== layerId) throw new TemplateCommandV2Error("layer_reference", "Новый объект должен ссылаться на выбранный слой.");
  if (allIds(content).has(node.id)) throw new TemplateCommandV2Error("duplicate_id", "Идентификатор объекта уже используется.");
  const index = atIndex ?? layer.nodes.length;
  if (!Number.isSafeInteger(index) || index < 0 || index > layer.nodes.length)
    throw new TemplateCommandV2Error("insert_index", "Позиция вставки выходит за границы слоя.");
  const nodes = [...layer.nodes];
  nodes.splice(index, 0, node);
  return replaceLayer(content, view, layerId, { ...layer, nodes });
}

export function addBasicNodeV2(content: TemplateContentV2, viewId: string, layerId: string, kind: BasicNodeKindV2): [TemplateContentV2, string] {
  const id = crypto.randomUUID();
  const base = {
    id, kind, layerId, visible: true, locked: false, opacity: 1, transform: identityTransform(),
    stroke: { color: "#27445a", width: constantExpressionV2(2), dash: "solid" as const }, fill: { color: null },
  };
  const node: TemplateNodeV2 = kind === "line" ? { ...base, kind, geometry: { points: [
    { x: constantExpressionV2(100), y: constantExpressionV2(100) },
    { x: constantExpressionV2(210), y: constantExpressionV2(100) },
  ], bendRadius: constantExpressionV2(0) } } : kind === "polyline" ? { ...base, kind, geometry: { points: [
    { x: constantExpressionV2(100), y: constantExpressionV2(100) },
    { x: constantExpressionV2(160), y: constantExpressionV2(100) },
    { x: constantExpressionV2(160), y: constantExpressionV2(160) },
  ], bendRadius: constantExpressionV2(0) } } : kind === "rectangle" ? { ...base, kind, geometry: {
    x: constantExpressionV2(100), y: constantExpressionV2(100), width: constantExpressionV2(140), height: constantExpressionV2(70),
    cornerRadii: [constantExpressionV2(0), constantExpressionV2(0), constantExpressionV2(0), constantExpressionV2(0)],
  } } : kind === "ellipse" ? { ...base, kind, geometry: {
    centerX: constantExpressionV2(170), centerY: constantExpressionV2(135), radiusX: constantExpressionV2(70), radiusY: constantExpressionV2(35),
  } } : kind === "bezier" ? { ...base, kind, geometry: { points: [
    { x: constantExpressionV2(100), y: constantExpressionV2(140) },
    { x: constantExpressionV2(130), y: constantExpressionV2(80) },
    { x: constantExpressionV2(190), y: constantExpressionV2(200) },
    { x: constantExpressionV2(220), y: constantExpressionV2(140) },
  ], closed: false } } : kind === "closedContour" ? { ...base, kind, geometry: { points: [
    { x: constantExpressionV2(100), y: constantExpressionV2(100) },
    { x: constantExpressionV2(220), y: constantExpressionV2(100) },
    { x: constantExpressionV2(220), y: constantExpressionV2(180) },
    { x: constantExpressionV2(100), y: constantExpressionV2(180) },
  ] } } : { ...base, kind, geometry: {
    x: constantExpressionV2(100), y: constantExpressionV2(100), text: "Текст", fontSize: constantExpressionV2(18),
  } };
  return [addNodeV2(content, viewId, layerId, node), id];
}

export function editNodeV2(content: TemplateContentV2, viewId: string, layerId: string, nodeId: string, changes: NodeEditV2): TemplateContentV2 {
  const { view, layer, node } = requireNode(content, viewId, layerId, nodeId);
  requireEditableNode(layer, node);
  const next = { ...node, ...changes, id: node.id, layerId: node.layerId, kind: node.kind } as TemplateNodeV2;
  return replaceLayer(content, view, layerId, { ...layer, nodes: layer.nodes.map(item => item.id === nodeId ? next : item) });
}

export function parameterizeNodeDimensionV2(
  content: TemplateContentV2,
  viewId: string,
  layerId: string,
  nodeId: string,
  dimension: ParameterizableNodeDimensionV2,
  input: ParameterizeNodeDimensionV2Input,
): [TemplateContentV2, string] {
  const { view, layer, node } = requireNode(content, viewId, layerId, nodeId);
  requireEditableNode(layer, node);
  const name = normalizedName(input.name);
  const unit = normalizedParameterUnit(input.unit);
  const { defaultValue, minimum, maximum } = input;
  if (![defaultValue, minimum, maximum].every(Number.isFinite) ||
      minimum <= 0 || minimum > defaultValue || defaultValue > maximum || maximum > TEMPLATE_V2_LIMITS.coordinate)
    throw new TemplateCommandV2Error("parameter_range", "Требуется диапазон 0 < минимум ≤ начальное значение ≤ максимум ≤ допустимой координаты.");
  if (content.parameters.length >= TEMPLATE_V2_LIMITS.parameters)
    throw new TemplateCommandV2Error("parameter_limit", "Достигнут лимит параметров.");
  const supported = node.kind === "rectangle" || node.kind === "image"
    ? dimension === "width" || dimension === "height"
    : node.kind === "ellipse"
      ? dimension === "radiusX" || dimension === "radiusY"
      : false;
  if (!supported)
    throw new TemplateCommandV2Error("unsupported_dimension", "Этот размер нельзя связать с параметром выбранного объекта.");
  const geometry = node.geometry as typeof node.geometry & Partial<Record<ParameterizableNodeDimensionV2, NumericExpressionV2>>;
  const expression = geometry[dimension];
  if (!expression || expression.kind !== "constant")
    throw new TemplateCommandV2Error("non_constant_dimension", "Размер уже параметризован или не является константой.");
  if (!Number.isFinite(expression.value) || expression.value < 0.001 || expression.value > TEMPLATE_V2_LIMITS.coordinate)
    throw new TemplateCommandV2Error("parameter_range", "Исходный размер должен быть от 0,001 до допустимого максимума.");
  const parameterId = crypto.randomUUID();
  const nextNode = {
    ...node,
    geometry: { ...node.geometry, [dimension]: { kind: "parameter", parameterId } },
  } as TemplateNodeV2;
  const next: TemplateContentV2 = {
    ...replaceLayer(content, view, layerId, {
      ...layer,
      nodes: layer.nodes.map(candidate => candidate.id === node.id ? nextNode : candidate),
    }),
    parameters: [...content.parameters, {
      id: parameterId,
      name,
      type: "number",
      unit,
      defaultValue,
      minimum,
      maximum,
      formula: null,
    }],
  };
  requireValidCommandResult(next, "invalid_parameterized_dimension");
  requireMaterializableRepeats(next);
  return [next, parameterId];
}

export function setTemplateParameterDefaultV2(
  content: TemplateContentV2,
  parameterId: string,
  value: number,
): TemplateContentV2 {
  const parameter = content.parameters.find(candidate => candidate.id === parameterId);
  if (!parameter) throw new TemplateCommandV2Error("parameter_not_found", "Параметр не найден.");
  if (parameter.type !== "number" && parameter.type !== "integer")
    throw new TemplateCommandV2Error("parameter_type", "Изменять числовое значение можно только у числового параметра.");
  if (parameter.formula !== null)
    throw new TemplateCommandV2Error("parameter_formula", "Значение вычисляемого параметра задаётся формулой.");
  if (!Number.isFinite(value) || parameter.type === "integer" && !Number.isSafeInteger(value))
    throw new TemplateCommandV2Error("parameter_value", "Значение не соответствует типу параметра.");
  if (parameter.minimum !== null && value < parameter.minimum || parameter.maximum !== null && value > parameter.maximum)
    throw new TemplateCommandV2Error("parameter_range", "Значение выходит за допустимый диапазон параметра.");
  const next: TemplateContentV2 = {
    ...content,
    parameters: content.parameters.map(candidate => candidate.id === parameterId ? { ...candidate, defaultValue: value } : candidate),
  };
  requireValidCommandResult(next, "invalid_parameter_default");
  requireMaterializableRepeats(next);
  return next;
}

export function setNodeLockedV2(content: TemplateContentV2, viewId: string, layerId: string, nodeId: string, locked: boolean): TemplateContentV2 {
  const { view, layer, node } = requireNode(content, viewId, layerId, nodeId);
  requireUnlockedLayer(layer);
  if (node.locked === locked) return content;
  return replaceLayer(content, view, layerId, { ...layer, nodes: layer.nodes.map(item => item.id === nodeId ? { ...item, locked } : item) });
}

export function moveNodeV2(content: TemplateContentV2, viewId: string, layerId: string, nodeId: string, deltaX: number, deltaY: number): TemplateContentV2 {
  if (!Number.isFinite(deltaX) || !Number.isFinite(deltaY))
    throw new TemplateCommandV2Error("invalid_move", "Смещение должно быть конечным числом.");
  const { view, layer, node } = requireNode(content, viewId, layerId, nodeId);
  requireEditableNode(layer, node);
  const moved = moveNodeGeometry(node, deltaX, deltaY);
  return replaceLayer(content, view, layerId, { ...layer, nodes: layer.nodes.map(item => item.id === nodeId ? moved : item) });
}

/** Resizes simple constant geometry from one Visio-style selection handle. */
export function resizeNodeV2(
  content: TemplateContentV2,
  viewId: string,
  layerId: string,
  nodeId: string,
  handle: NodeResizeHandleV2,
  deltaX: number,
  deltaY: number,
): TemplateContentV2 {
  if (![deltaX, deltaY].every(Number.isFinite))
    throw new TemplateCommandV2Error("invalid_resize", "Изменение размера должно быть конечным числом.");
  const { view, layer, node } = requireNode(content, viewId, layerId, nodeId);
  requireEditableNode(layer, node);
  const c = constantExpressionV2;
  let resized: TemplateNodeV2;
  if (node.kind === "line") {
    if (handle !== "start" && handle !== "end")
      throw new TemplateCommandV2Error("unsupported_resize_handle", "Для линии изменяются только конечные точки.");
    if (node.geometry.points.length !== 2)
      throw new TemplateCommandV2Error("unsupported_resize", "Изменение размера доступно для простой линии из двух точек.");
    const index = handle === "start" ? 0 : 1;
    const points = node.geometry.points.map((point, candidateIndex) => candidateIndex === index ? {
      x: c(requireConstant(point.x) + deltaX),
      y: c(requireConstant(point.y) + deltaY),
    } : point);
    resized = { ...node, geometry: { ...node.geometry, points } };
  } else if (node.kind === "rectangle" || node.kind === "ellipse" || node.kind === "image") {
    if (handle === "start" || handle === "end")
      throw new TemplateCommandV2Error("unsupported_resize_handle", "Выбранная точка не относится к рамке фигуры.");
    const isEllipse = node.kind === "ellipse";
    let x = isEllipse ? requireConstant(node.geometry.centerX) - requireConstant(node.geometry.radiusX) : requireConstant(node.geometry.x);
    let y = isEllipse ? requireConstant(node.geometry.centerY) - requireConstant(node.geometry.radiusY) : requireConstant(node.geometry.y);
    let width = isEllipse ? requireConstant(node.geometry.radiusX) * 2 : requireConstant(node.geometry.width);
    let height = isEllipse ? requireConstant(node.geometry.radiusY) * 2 : requireConstant(node.geometry.height);
    if (handle.includes("w")) { x += deltaX; width -= deltaX; }
    if (handle.includes("e")) width += deltaX;
    if (handle.includes("n")) { y += deltaY; height -= deltaY; }
    if (handle.includes("s")) height += deltaY;
    if (width < 1 || height < 1)
      throw new TemplateCommandV2Error("invalid_resize", "Ширина и высота фигуры должны быть не меньше 1.");
    if (node.kind === "ellipse") resized = { ...node, geometry: {
      ...node.geometry, centerX: c(x + width / 2), centerY: c(y + height / 2), radiusX: c(width / 2), radiusY: c(height / 2),
    } };
    else if (node.kind === "rectangle") resized = { ...node, geometry: { ...node.geometry, x: c(x), y: c(y), width: c(width), height: c(height) } };
    else resized = { ...node, geometry: { ...node.geometry, x: c(x), y: c(y), width: c(width), height: c(height) } };
  } else throw new TemplateCommandV2Error("unsupported_resize", "Размер этого объекта изменяется через панель параметров.");
  return replaceLayer(content, view, layerId, { ...layer, nodes: layer.nodes.map(item => item.id === nodeId ? resized : item) });
}

export function deleteNodeV2(content: TemplateContentV2, viewId: string, layerId: string, nodeId: string): TemplateContentV2 {
  const { view, layer, node } = requireNode(content, viewId, layerId, nodeId);
  requireEditableNode(layer, node);
  if (view.layers.some(item => item.nodes.some(candidate => candidate.kind === "group" && candidate.geometry.childIds.includes(nodeId))) ||
      view.repeatPlacements.some(placement => placement.prototypeGroupId === nodeId))
    throw new TemplateCommandV2Error("node_referenced", "Объект используется группой или повтором.");
  return replaceLayer(content, {...view,contactPoints:view.contactPoints.filter(p=>p.shape?.nodeId!==nodeId)}, layerId, { ...layer, nodes: layer.nodes.filter(item => item.id !== nodeId) });
}

export function reorderNodeV2(content: TemplateContentV2, viewId: string, layerId: string, nodeId: string, toIndex: number): TemplateContentV2 {
  const { view, layer, node } = requireNode(content, viewId, layerId, nodeId);
  requireEditableNode(layer, node);
  const fromIndex = layer.nodes.findIndex(item => item.id === nodeId);
  const next = reordered(layer.nodes, fromIndex, toIndex);
  return next === layer.nodes ? content : replaceLayer(content, view, layerId, { ...layer, nodes: next });
}

function moveNodeGeometry(node: TemplateNodeV2, deltaX: number, deltaY: number): TemplateNodeV2 {
  const shifted = (expression: NumericExpressionV2, delta: number) => constantExpressionV2(requireConstant(expression) + delta);
  return { ...node, transform: { ...node.transform,
    translateX: shifted(node.transform.translateX, deltaX), translateY: shifted(node.transform.translateY, deltaY) } };
}

function requireConstant(expression: NumericExpressionV2): number {
  if (expression.kind !== "constant")
    throw new TemplateCommandV2Error("non_constant_geometry", "Параметризованную геометрию нельзя перемещать как константу.");
  return expression.value;
}

function requireView(content: TemplateContentV2, viewId: string): TemplateViewV2 {
  const view = content.views.find(item => item.id === viewId);
  if (!view) throw new TemplateCommandV2Error("view_not_found", "Вид не найден.");
  return view;
}

function requireLayer(content: TemplateContentV2, viewId: string, layerId: string): { view: TemplateViewV2; layer: LayerV2 } {
  const view = requireView(content, viewId), layer = view.layers.find(item => item.id === layerId);
  if (!layer) throw new TemplateCommandV2Error("layer_not_found", "Слой не найден.");
  return { view, layer };
}

function requireNode(content: TemplateContentV2, viewId: string, layerId: string, nodeId: string): { view: TemplateViewV2; layer: LayerV2; node: TemplateNodeV2 } {
  const { view, layer } = requireLayer(content, viewId, layerId), node = layer.nodes.find(item => item.id === nodeId);
  if (!node) throw new TemplateCommandV2Error("node_not_found", "Объект не найден.");
  return { view, layer, node };
}

function requireLogicalContact(content: TemplateContentV2, logicalContactId: string): LogicalContactV2 {
  const logicalContact = content.logicalContacts.find(item => item.id === logicalContactId);
  if (!logicalContact) throw new TemplateCommandV2Error("logical_contact_not_found", "Логический контакт не найден.");
  return logicalContact;
}

function requireContactPoint(
  content: TemplateContentV2,
  viewId: string,
  pointId: string,
): { view: TemplateViewV2; point: ViewContactPointV2; logicalContact: LogicalContactV2 } {
  const view = requireView(content, viewId);
  const point = view.contactPoints.find(item => item.id === pointId);
  if (!point) throw new TemplateCommandV2Error("contact_point_not_found", "Точка логического контакта не найдена в выбранном виде.");
  return { view, point, logicalContact: requireLogicalContact(content, point.logicalContactId) };
}

function requireBundlePort(
  content: TemplateContentV2,
  viewId: string,
  portId: string,
): { view: TemplateViewV2; port: BundlePortV2 } {
  const view = requireView(content, viewId);
  const port = view.bundlePorts.find(item => item.id === portId);
  if (!port) throw new TemplateCommandV2Error("bundle_port_not_found", "Общая точка жгута не найдена в выбранном виде.");
  return { view, port };
}

function groupDescendantIds(layer: LayerV2, groupId: string, result = new Set<string>()): Set<string> {
  const group = layer.nodes.find(candidate => candidate.id === groupId);
  if (!group || group.kind !== "group") return result;
  for (const childId of group.geometry.childIds) {
    if (result.has(childId)) continue;
    result.add(childId);
    groupDescendantIds(layer, childId, result);
  }
  return result;
}

function requireUnlockedLayer(layer: LayerV2): void {
  if (layer.locked) throw new TemplateCommandV2Error("layer_locked", "Слой заблокирован.");
}

function requireEditableNode(layer: LayerV2, node: TemplateNodeV2): void {
  requireUnlockedLayer(layer);
  if (node.locked) throw new TemplateCommandV2Error("node_locked", "Объект заблокирован.");
}

function replaceView(content: TemplateContentV2, viewId: string, view: TemplateViewV2): TemplateContentV2 {
  return { ...content, views: content.views.map(item => item.id === viewId ? view : item) };
}

function replaceLayer(content: TemplateContentV2, view: TemplateViewV2, layerId: string, layer: LayerV2): TemplateContentV2 {
  return replaceView(content, view.id, { ...view, layers: view.layers.map(item => item.id === layerId ? layer : item) });
}

function reordered<T>(items: T[], fromIndex: number, toIndex: number): T[] {
  if (!Number.isSafeInteger(toIndex) || toIndex < 0 || toIndex >= items.length)
    throw new TemplateCommandV2Error("reorder_index", "Позиция выходит за границы списка.");
  if (fromIndex === toIndex) return items;
  const next = [...items], [item] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, item!);
  return next;
}

function normalizedName(value: string, fallback?: string): string {
  const result = value.trim() || fallback;
  if (!result || result.length > 256 || /[\u0000-\u001f]/.test(result))
    throw new TemplateCommandV2Error("invalid_name", "Нужно непустое название не длиннее 256 символов.");
  return result;
}

function normalizedParameterUnit(value: string | null | undefined): string | null {
  if (value === null || value === undefined || value.trim() === "") return null;
  const result = value.trim();
  if (result.length > 32 || /[\u0000-\u001f]/.test(result))
    throw new TemplateCommandV2Error("invalid_unit", "Единица должна быть не длиннее 32 символов.");
  return result;
}

function normalizedContactNumber(value: string): string {
  const result = value.trim();
  if (!result || result.length > 128 || /[\u0000-\u001f]/.test(result))
    throw new TemplateCommandV2Error("invalid_contact_number", "Нужен непустой номер контакта не длиннее 128 символов.");
  return result;
}

function normalizedContactType(value: string): string {
  const result = value.trim();
  if (result.length > 128 || /[\u0000-\u001f]/.test(result))
    throw new TemplateCommandV2Error("invalid_contact_type", "Тип контакта должен быть не длиннее 128 символов.");
  return result;
}

function normalizedContactDirection(value: ContactDirectionV2): ContactDirectionV2 {
  if (!(["left", "right", "up", "down"] as const).includes(value))
    throw new TemplateCommandV2Error("invalid_contact_direction", "Неизвестное направление точки контакта.");
  return value;
}

function requireUniqueContactNumber(content: TemplateContentV2, number: string, exceptId?: string): void {
  if (content.logicalContacts.some(contact => contact.id !== exceptId && contact.number === number))
    throw new TemplateCommandV2Error("duplicate_contact_number", `Номер контакта ${number} уже используется.`);
}

function nextContactNumber(content: TemplateContentV2): string {
  const occupied = new Set(content.logicalContacts.map(contact => contact.number));
  for (let value = 1; value <= TEMPLATE_V2_LIMITS.contacts + 1; value++) {
    const candidate = String(value);
    if (!occupied.has(candidate)) return candidate;
  }
  throw new TemplateCommandV2Error("contact_limit", "Не удалось подобрать свободный номер контакта.");
}

function requireRepeatCount(count: number): void {
  if (!Number.isSafeInteger(count) || count < 1 || count > 1_000)
    throw new TemplateCommandV2Error("repeater_count", "Количество повторов должно быть целым числом от 1 до 1000.");
}

function requireMaterializableRepeats(content: TemplateContentV2): void {
  try {
    expandTemplateRepeatsV2(content);
  } catch (error) {
    if (error instanceof TemplateRepeatV2Error) throw new TemplateCommandV2Error(error.code, error.message);
    throw error;
  }
}

function requireValidCommandResult(content: TemplateContentV2, code: string): void {
  const validation = validateTemplateContentV2(content);
  if (!validation.valid)
    throw new TemplateCommandV2Error(code, validation.diagnostics[0]?.message ?? "Команда создала некорректный шаблон.");
}

function allIds(content: TemplateContentV2): Set<string> {
  const result = new Set<string>();
  for (const item of [...content.parameters, ...content.assets.map(asset => ({ id: asset.assetId })), ...content.logicalContacts, ...content.repeaters, ...content.articleParameterPresets]) result.add(item.id);
  for (const view of content.views) {
    result.add(view.id);
    for (const layer of view.layers) { result.add(layer.id); for (const node of layer.nodes) result.add(node.id); }
    for (const point of [...view.contactPoints, ...view.bundlePorts]) result.add(point.id);
  }
  return result;
}
