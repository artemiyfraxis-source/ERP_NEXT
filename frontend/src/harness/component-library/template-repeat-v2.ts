import { arrayPositions } from "./array-layout";
import {
  TEMPLATE_V2_LIMITS,
  repeatOccurrenceKeyV2,
  type ArticleParameterPresetV2,
  type ContactDirectionV2,
  type NumericExpressionV2,
  type ParameterValueV2,
  type RepeatDomainV2,
  type TemplateContentV2,
  type TemplateParameterV2,
  type ViewRepeatPlacementV2,
} from "./template-model-v2";

export type TemplateParameterValuesV2 =
  | Readonly<Record<string, ParameterValueV2>>
  | ReadonlyMap<string, ParameterValueV2>;

export interface TemplateParameterResolutionOptionsV2 {
  /** A preset object, or the id of a preset stored in the template. */
  readonly articlePreset?: ArticleParameterPresetV2 | string | null;
  /** Explicit instance values. These take precedence over a preset and defaults. */
  readonly overrides?: TemplateParameterValuesV2;
}

export class TemplateRepeatV2Error extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly parameterId: string | null = null,
  ) {
    super(message);
    this.name = "TemplateRepeatV2Error";
  }
}

export interface RepeatOffsetV2 {
  readonly x: number;
  readonly y: number;
}

export interface RepeatGroupOccurrenceV2 {
  readonly key: string;
  readonly prototypeGroupId: string;
  readonly offset: RepeatOffsetV2;
}

export interface RepeatContactPointOccurrenceV2 {
  /** Cross-view key based on the logical contact rather than a view point id. */
  readonly key: string;
  readonly prototypeContactPointId: string;
  readonly prototypeLogicalContactId: string;
  readonly number: string;
  readonly name: string;
  readonly contactType: string;
  readonly x: number;
  readonly y: number;
  readonly direction: ContactDirectionV2;
}

export interface RepeatNodeOccurrenceV2 {
  readonly key: string;
  readonly prototypeNodeId: string;
  readonly offset: RepeatOffsetV2;
}

export interface RepeatOccurrenceDescriptorV2 {
  readonly key: string;
  readonly repeatDomainId: string;
  readonly index: number;
  readonly offset: RepeatOffsetV2;
  readonly group: RepeatGroupOccurrenceV2;
  readonly nodes: readonly RepeatNodeOccurrenceV2[];
  readonly contactPoints: readonly RepeatContactPointOccurrenceV2[];
}

export interface RepeatPlacementExpansionV2 {
  readonly prototypeGroupId: string;
  readonly occurrences: readonly RepeatOccurrenceDescriptorV2[];
}

export interface TemplateViewRepeatExpansionV2 {
  readonly viewId: string;
  readonly placements: readonly RepeatPlacementExpansionV2[];
}

interface ExpandedViewUsageV2 {
  readonly nodes: number;
  readonly contacts: number;
}

function inputEntries(values: TemplateParameterValuesV2 | undefined): ReadonlyArray<readonly [string, ParameterValueV2]> {
  if (!values) return [];
  if (values instanceof Map) return [...values.entries()];
  const record = values as Readonly<Record<string, ParameterValueV2>>;
  return Object.keys(record).map(key => [key, record[key]!] as const);
}

function assertParameterValue(parameter: TemplateParameterV2, value: unknown): ParameterValueV2 {
  const valid = parameter.type === "integer"
    ? typeof value === "number" && Number.isSafeInteger(value)
    : parameter.type === "number"
      ? typeof value === "number" && Number.isFinite(value)
      : parameter.type === "boolean"
        ? typeof value === "boolean"
        : typeof value === "string";
  if (!valid) {
    throw new TemplateRepeatV2Error(
      "parameter_value",
      `Значение параметра «${parameter.name}» не соответствует типу ${parameter.type}.`,
      parameter.id,
    );
  }
  const typed = value as ParameterValueV2;
  if (typeof typed === "number") {
    if (!Number.isFinite(typed) || Math.abs(typed) > TEMPLATE_V2_LIMITS.coordinate) {
      throw new TemplateRepeatV2Error("parameter_value", `Значение параметра «${parameter.name}» недопустимо.`, parameter.id);
    }
    if (parameter.minimum !== null && typed < parameter.minimum) {
      throw new TemplateRepeatV2Error("parameter_range", `Значение параметра «${parameter.name}» меньше минимума.`, parameter.id);
    }
    if (parameter.maximum !== null && typed > parameter.maximum) {
      throw new TemplateRepeatV2Error("parameter_range", `Значение параметра «${parameter.name}» больше максимума.`, parameter.id);
    }
  }
  return typed;
}

function presetFor(
  content: Pick<TemplateContentV2, "articleParameterPresets">,
  preset: ArticleParameterPresetV2 | string | null | undefined,
): ArticleParameterPresetV2 | null {
  if (preset === null || preset === undefined) return null;
  if (typeof preset !== "string") return preset;
  const found = content.articleParameterPresets.find(candidate => candidate.id === preset);
  if (!found) throw new TemplateRepeatV2Error("preset_missing", `Набор параметров ${preset} не найден.`);
  return found;
}

/**
 * Resolves parameters with the precedence overrides > article preset > formula > default.
 * Formula AST nodes are interpreted directly; executable strings and eval are never used.
 */
export function resolveTemplateParameterValuesV2(
  content: Pick<TemplateContentV2, "parameters" | "articleParameterPresets">,
  options: TemplateParameterResolutionOptionsV2 = {},
): ReadonlyMap<string, ParameterValueV2> {
  const parameters = new Map<string, TemplateParameterV2>();
  for (const parameter of content.parameters) {
    if (parameters.has(parameter.id)) {
      throw new TemplateRepeatV2Error("duplicate_parameter", `Параметр ${parameter.id} объявлен повторно.`, parameter.id);
    }
    parameters.set(parameter.id, parameter);
  }

  const supplied = new Map<string, ParameterValueV2>();
  const preset = presetFor(content, options.articlePreset);
  for (const entry of preset?.values ?? []) {
    const parameter = parameters.get(entry.parameterId);
    if (!parameter) throw new TemplateRepeatV2Error("parameter_missing", `Параметр ${entry.parameterId} из набора не найден.`, entry.parameterId);
    if (supplied.has(entry.parameterId)) {
      throw new TemplateRepeatV2Error("duplicate_parameter", `Параметр ${entry.parameterId} повторяется в наборе.`, entry.parameterId);
    }
    supplied.set(entry.parameterId, assertParameterValue(parameter, entry.value));
  }
  for (const [parameterId, value] of inputEntries(options.overrides)) {
    const parameter = parameters.get(parameterId);
    if (!parameter) throw new TemplateRepeatV2Error("parameter_missing", `Переопределяемый параметр ${parameterId} не найден.`, parameterId);
    supplied.set(parameterId, assertParameterValue(parameter, value));
  }

  const resolved = new Map<string, ParameterValueV2>();
  const resolving = new Set<string>();

  const parameterValue = (parameterId: string): ParameterValueV2 => {
    if (resolved.has(parameterId)) return resolved.get(parameterId)!;
    const parameter = parameters.get(parameterId);
    if (!parameter) throw new TemplateRepeatV2Error("parameter_missing", `Параметр ${parameterId} не найден.`, parameterId);
    if (resolving.has(parameterId)) {
      throw new TemplateRepeatV2Error("expression_cycle", `Формула параметра «${parameter.name}» образует цикл.`, parameter.id);
    }
    resolving.add(parameterId);
    try {
      const value = supplied.has(parameterId)
        ? supplied.get(parameterId)!
        : parameter.formula === null
          ? assertParameterValue(parameter, parameter.defaultValue)
          : assertParameterValue(parameter, evaluate(parameter.formula, parameterValue, parameter.id),);
      resolved.set(parameterId, value);
      return value;
    } finally {
      resolving.delete(parameterId);
    }
  };

  for (const parameter of content.parameters) parameterValue(parameter.id);
  return resolved;
}

function evaluate(
  expression: NumericExpressionV2,
  parameterValue: (parameterId: string) => ParameterValueV2,
  ownerParameterId: string | null,
): number {
  let nodes = 0;
  const visit = (current: NumericExpressionV2, depth: number): number => {
    nodes += 1;
    if (depth > TEMPLATE_V2_LIMITS.expressionDepth || nodes > TEMPLATE_V2_LIMITS.expressionNodes) {
      throw new TemplateRepeatV2Error("expression_limit", "Формула превышает допустимый размер.", ownerParameterId);
    }
    let value: number;
    switch (current.kind) {
      case "constant":
        value = current.value;
        break;
      case "parameter": {
        const parameter = parameterValue(current.parameterId);
        if (typeof parameter !== "number" || !Number.isFinite(parameter)) {
          throw new TemplateRepeatV2Error("parameter_value", `Параметр ${current.parameterId} не является числовым.`, current.parameterId);
        }
        value = parameter;
        break;
      }
      case "negate":
        value = -visit(current.operand, depth + 1);
        break;
      case "binary": {
        const left = visit(current.left, depth + 1);
        const right = visit(current.right, depth + 1);
        if (current.operator === "divide" && right === 0) {
          throw new TemplateRepeatV2Error("division_by_zero", "Деление на ноль в формуле запрещено.", ownerParameterId);
        }
        value = current.operator === "add" ? left + right
          : current.operator === "subtract" ? left - right
            : current.operator === "multiply" ? left * right
              : left / right;
        break;
      }
    }
    if (!Number.isFinite(value) || Math.abs(value) > TEMPLATE_V2_LIMITS.coordinate) {
      throw new TemplateRepeatV2Error("expression_result", "Результат формулы выходит за допустимый диапазон.", ownerParameterId);
    }
    return value;
  };
  return visit(expression, 1);
}

function numericValue(values: ReadonlyMap<string, ParameterValueV2>, expression: NumericExpressionV2): number {
  return evaluate(expression, parameterId => {
    if (!values.has(parameterId)) throw new TemplateRepeatV2Error("parameter_missing", `Параметр ${parameterId} не разрешён.`, parameterId);
    return values.get(parameterId)!;
  }, null);
}

function repeatCountError(): TemplateRepeatV2Error {
  return new TemplateRepeatV2Error("repeater_count", "Количество повторов должно быть целым числом от 1 до 1000.");
}

function assertCount(value: unknown): asserts value is number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1 || value > 1_000) throw repeatCountError();
}

function occurrenceNumber(prototype: string, index: number, stride: number): string {
  if (/^(0|[1-9]\d*)$/.test(prototype)) {
    const numeric = Number(prototype);
    if (Number.isSafeInteger(numeric + index * stride)) return String(numeric + index * stride);
  }
  return `${prototype}-${index + 1}`;
}

function prototypeMemberIds(
  content: TemplateContentV2,
  viewId: string,
  placement: ViewRepeatPlacementV2,
): ReadonlySet<string> {
  const view = content.views.find(candidate => candidate.id === viewId);
  if (!view) throw new TemplateRepeatV2Error("view_missing", `Вид ${viewId} не найден.`);
  const nodes = new Map(view.layers.flatMap(layer => layer.nodes).map(node => [node.id, node]));
  const prototype = nodes.get(placement.prototypeGroupId);
  if (!prototype || prototype.kind !== "group") {
    throw new TemplateRepeatV2Error("prototype_group_missing", "Группа-прототип повтора не найдена.");
  }
  const members = new Set<string>();
  const visit = (nodeId: string) => {
    if (members.has(nodeId)) return;
    members.add(nodeId);
    const node = nodes.get(nodeId);
    if (!node) throw new TemplateRepeatV2Error("prototype_node_missing", `Узел прототипа ${nodeId} не найден.`);
    if (node.kind === "group") node.geometry.childIds.forEach(visit);
  };
  visit(prototype.id);
  return members;
}

function expandedViewUsageV2(
  content: TemplateContentV2,
  viewId: string,
  expansions: readonly RepeatPlacementExpansionV2[],
): ExpandedViewUsageV2 {
  const view = content.views.find(candidate => candidate.id === viewId);
  if (!view) throw new TemplateRepeatV2Error("view_missing", `Вид ${viewId} не найден.`);
  const suppressedNodes = new Set<string>();
  const suppressedPoints = new Set<string>();
  let repeatedNodes = 0;
  let repeatedContacts = 0;
  view.repeatPlacements.forEach((placement, index) => {
    const members = prototypeMemberIds(content, view.id, placement);
    members.forEach(id => suppressedNodes.add(id));
    placement.contactPointIds.forEach(id => suppressedPoints.add(id));
    const occurrences = expansions[index]?.occurrences;
    if (!occurrences) throw new TemplateRepeatV2Error("repeat_expansion_missing", "Размещение повтора не было развёрнуто.");
    repeatedNodes += occurrences.length * members.size;
    repeatedContacts += occurrences.reduce((sum, occurrence) => sum + occurrence.contactPoints.length, 0);
  });
  return {
    nodes: view.layers.reduce((sum, layer) => sum + layer.nodes.length, 0) - suppressedNodes.size + repeatedNodes,
    contacts: view.contactPoints.length - suppressedPoints.size + repeatedContacts,
  };
}

/**
 * Expands one view placement atomically. All values, references and the final count
 * are checked before the first occurrence descriptor is allocated.
 */
export function expandRepeatPlacementV2(
  content: TemplateContentV2,
  domain: RepeatDomainV2,
  placement: ViewRepeatPlacementV2,
  options: TemplateParameterResolutionOptionsV2 = {},
): readonly RepeatOccurrenceDescriptorV2[] {
  if (placement.repeatDomainId !== domain.id) {
    throw new TemplateRepeatV2Error("repeat_domain_mismatch", "Размещение относится к другому домену повтора.");
  }

  let values: ReadonlyMap<string, ParameterValueV2>;
  try {
    values = resolveTemplateParameterValuesV2(content, options);
  } catch (error) {
    if (error instanceof TemplateRepeatV2Error && error.parameterId === domain.countParameterId) throw repeatCountError();
    throw error;
  }
  const count = values.get(domain.countParameterId);
  assertCount(count);

  const stepX = numericValue(values, placement.step.x);
  const stepY = numericValue(values, placement.step.y);
  const arrayOffsets = placement.arrayLayout ? arrayPositions(count, { ...placement.arrayLayout, numbering: placement.arrayLayout.numbering === "snake" ? "snake" : "new-row" }, stepX, stepY) : null;
  const lastOffsetX = (count - 1) * stepX;
  const lastOffsetY = (count - 1) * stepY;
  if (![lastOffsetX, lastOffsetY].every(value => Number.isFinite(value) && Math.abs(value) <= TEMPLATE_V2_LIMITS.coordinate)) {
    throw new TemplateRepeatV2Error("occurrence_offset", "Смещение повторяемого сегмента выходит за допустимый диапазон.");
  }

  const logicalContacts = new Map(content.logicalContacts.map(contact => [contact.id, contact]));
  const placementView = content.views.find(view => view.repeatPlacements.some(candidate => candidate === placement ||
    candidate.repeatDomainId === placement.repeatDomainId && candidate.prototypeGroupId === placement.prototypeGroupId));
  if (!placementView) throw new TemplateRepeatV2Error("repeat_placement_missing", "Размещение повтора не найдено в виде.");
  const allNodes = placementView.layers.flatMap(layer => layer.nodes);
  const prototype = allNodes.find(node => node.id === placement.prototypeGroupId);
  if (!prototype || prototype.kind !== "group") {
    throw new TemplateRepeatV2Error("prototype_group_missing", "Группа-прототип повтора не найдена.");
  }
  if (allNodes.some(node => node.kind === "group" && node.id !== prototype.id && node.geometry.childIds.includes(prototype.id))) {
    throw new TemplateRepeatV2Error("prototype_group_nested", "Группа-прототип повтора должна находиться на верхнем уровне слоя.");
  }
  const descendants = prototypeMemberIds(content, placementView.id, placement);
  if (count * descendants.size > TEMPLATE_V2_LIMITS.nodes || count * placement.contactPointIds.length > TEMPLATE_V2_LIMITS.contacts) {
    throw new TemplateRepeatV2Error("expanded_budget", "Развёрнутый шаблон превышает допустимый размер.");
  }
  const contactPoints = new Map(placementView.contactPoints.map(point => [point.id, point]));
  const domainOrder = new Map(domain.logicalContactIds.map((id, index) => [id, index]));
  const prototypes = placement.contactPointIds.map((pointId, placementIndex) => {
    const point = contactPoints.get(pointId);
    if (!point) throw new TemplateRepeatV2Error("contact_point_missing", `Точка контакта ${pointId} не найдена.`);
    const logical = logicalContacts.get(point.logicalContactId);
    if (!logical) throw new TemplateRepeatV2Error("logical_contact_missing", `Логический контакт ${point.logicalContactId} не найден.`);
    const logicalIndex = domainOrder.get(logical.id) ?? (placement.arrayLayout && domain.logicalContactIds.length===0 ? placementIndex : undefined);
    if (logicalIndex === undefined) {
      throw new TemplateRepeatV2Error("repeat_contact_mismatch", `Контакт ${logical.id} не входит в домен повтора.`);
    }
    return {
      point,
      logical,
      logicalIndex,
      placementIndex,
      x: numericValue(values, point.x),
      y: numericValue(values, point.y),
    };
  }).sort((left, right) => left.logicalIndex - right.logicalIndex || left.placementIndex - right.placementIndex);

  const occurrences: RepeatOccurrenceDescriptorV2[] = [];
  const stride = domain.logicalContactIds.length || (placement.arrayLayout ? placement.contactPointIds.length : 0);
  for (let index = 0; index < count; index += 1) {
    const offset = Object.freeze(arrayOffsets?.[index] ?? { x: index * stepX, y: index * stepY });
    const key = repeatOccurrenceKeyV2(domain.id, index, placement.prototypeGroupId);
    const group = Object.freeze({ key, prototypeGroupId: placement.prototypeGroupId, offset });
    const nodes = Object.freeze([...descendants].filter(prototypeNodeId => prototypeNodeId !== prototype.id).map(prototypeNodeId => Object.freeze({
      key: repeatOccurrenceKeyV2(domain.id, index, prototypeNodeId),
      prototypeNodeId,
      offset,
    })));
    const points = prototypes.map(({ point, logical, x, y }) => Object.freeze({
      key: repeatOccurrenceKeyV2(domain.id, index, logical.id),
      prototypeContactPointId: point.id,
      prototypeLogicalContactId: logical.id,
      number: occurrenceNumber(logical.number, index, stride),
      name: logical.name,
      contactType: logical.contactType,
      x: x + offset.x,
      y: y + offset.y,
      direction: point.direction,
    }));
    occurrences.push(Object.freeze({
      key,
      repeatDomainId: domain.id,
      index,
      offset,
      group,
      nodes,
      contactPoints: Object.freeze(points),
    }));
  }
  return Object.freeze(occurrences);
}

/** Expands every repeat placement of a view and enforces one aggregate render budget. */
export function expandTemplateViewRepeatsV2(
  content: TemplateContentV2,
  viewId: string,
  options: TemplateParameterResolutionOptionsV2 = {},
): readonly RepeatPlacementExpansionV2[] {
  const view = content.views.find(candidate => candidate.id === viewId);
  if (!view) throw new TemplateRepeatV2Error("view_missing", `Вид ${viewId} не найден.`);
  const result: RepeatPlacementExpansionV2[] = [];
  const prototypePointIds = new Set(view.repeatPlacements.flatMap(placement => placement.contactPointIds));
  const displayedNumbers = new Map<string, string>();
  const registerNumber = (number: string, owner: string) => {
    const normalized = number.trim();
    const previous = displayedNumbers.get(normalized);
    if (previous && previous !== owner) {
      throw new TemplateRepeatV2Error("duplicate_expanded_contact_number", `Отображаемый номер контакта ${normalized} используется повторно.`);
    }
    displayedNumbers.set(normalized, owner);
  };
  const logicalContacts = new Map(content.logicalContacts.map(contact => [contact.id, contact]));
  for (const point of view.contactPoints) {
    if (prototypePointIds.has(point.id)) continue;
    const logical = logicalContacts.get(point.logicalContactId);
    if (!logical) throw new TemplateRepeatV2Error("logical_contact_missing", `Логический контакт ${point.logicalContactId} не найден.`);
    registerNumber(logical.number, `point:${point.id}`);
  }
  for (const placement of view.repeatPlacements) {
    const domain = content.repeaters.find(candidate => candidate.id === placement.repeatDomainId);
    if (!domain) throw new TemplateRepeatV2Error("repeat_domain_missing", `Домен ${placement.repeatDomainId} не найден.`);
    const occurrences = expandRepeatPlacementV2(content, domain, placement, options);
    for (const occurrence of occurrences) {
      for (const point of occurrence.contactPoints) registerNumber(point.number, point.key);
    }
    result.push(Object.freeze({ prototypeGroupId: placement.prototypeGroupId, occurrences }));
  }
  const usage = expandedViewUsageV2(content, view.id, result);
  if (usage.nodes > TEMPLATE_V2_LIMITS.nodes || usage.contacts > TEMPLATE_V2_LIMITS.contacts) {
    throw new TemplateRepeatV2Error("expanded_budget", "Развёрнутый вид превышает допустимый размер.");
  }
  return Object.freeze(result);
}

/** Expands all views atomically and enforces one aggregate materialized budget. */
export function expandTemplateRepeatsV2(
  content: TemplateContentV2,
  options: TemplateParameterResolutionOptionsV2 = {},
): readonly TemplateViewRepeatExpansionV2[] {
  // Materialization is also the save-time semantic gate. Resolve the complete
  // parameter graph even when this template has no repeat placements.
  resolveTemplateParameterValuesV2(content, options);
  const result: TemplateViewRepeatExpansionV2[] = [];
  let nodeOccurrences = 0;
  let pointOccurrences = 0;
  for (const view of content.views) {
    const placements = expandTemplateViewRepeatsV2(content, view.id, options);
    const usage = expandedViewUsageV2(content, view.id, placements);
    nodeOccurrences += usage.nodes;
    pointOccurrences += usage.contacts;
    if (nodeOccurrences > TEMPLATE_V2_LIMITS.nodes || pointOccurrences > TEMPLATE_V2_LIMITS.contacts) {
      throw new TemplateRepeatV2Error("expanded_budget", "Развёрнутый шаблон превышает допустимый размер.");
    }
    result.push(Object.freeze({ viewId: view.id, placements }));
  }
  return Object.freeze(result);
}
