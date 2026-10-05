import {
  addAdditionalViewV2,
  addBasicNodeV2,
  addLayerV2,
  addNodeV2,
  attachRepeatDomainV2,
  constantExpressionV2,
  constantExpressionValueV2,
  createRepeatPrototypeV2,
  deleteAdditionalViewV2,
  deleteLayerV2,
  deleteNodeV2,
  deleteRepeatPrototypeV2,
  editNodeV2,
  evaluateNumericExpressionV2,
  moveNodeV2,
  resizeNodeV2,
  newTemplateContentV2,
  parameterizeNodeDimensionV2,
  renameLayerV2,
  renameViewV2,
  reorderLayerV2,
  reorderNodeV2,
  setLayerLockedV2,
  setLayerVisibleV2,
  setNodeLockedV2,
  setRepeatCountV2,
  setRepeatStepV2,
  setTemplateParameterDefaultV2,
  TemplateCommandV2Error,
  type AttachRepeatDomainV2Input,
  type BasicNodeKindV2,
  type BundlePortEditV2,
  type ContactPointEditV2,
  type CreateRepeatPrototypeV2Input,
  type ExpressionValuesV2,
  type NewBundlePortV2,
  type NodeEditV2,
  type NodeResizeHandleV2,
  type ParameterizableNodeDimensionV2,
  type ParameterizeNodeDimensionV2Input,
  type RepeatPrototypeIdsV2,
} from "./template-commands-v2";
import {
  TEMPLATE_V2_LIMITS,
  type ContactDirectionV2,
  type PointExpressionV2,
  type TemplateContentV2,
  type TemplateNodeV2,
  type TemplateViewV2,
  type TransformV2,
} from "./template-model-v2";
import {
  TEMPLATE_V3_LIMITS,
  validateTemplateContentV3Structure,
  type ArticleContactGroupV3,
  type ArticleKeyV3,
  type ArticleParameterValueV3,
  type ArticleVariantV3,
  type BundlePortV3,
  type ContactTypeGroupV3,
  type LogicalContactV3,
  type TemplateContentV3,
  type TemplateNodeV3,
  type TemplateViewV3,
  type ViewContactPointV3,
} from "./template-model-v3";
import { expandTemplateRepeatsV2, TemplateRepeatV2Error } from "./template-repeat-v2";

export type BasicNodeKindV3 = BasicNodeKindV2;
export type NodeEditV3 = NodeEditV2;
export type NodeResizeHandleV3 = NodeResizeHandleV2;
export type ExpressionValuesV3 = ExpressionValuesV2;
export type ContactPointEditV3 = ContactPointEditV2;
export type NewBundlePortV3 = NewBundlePortV2;
export type BundlePortEditV3 = BundlePortEditV2;
export type CreateRepeatPrototypeV3Input = CreateRepeatPrototypeV2Input;
export type RepeatPrototypeIdsV3 = RepeatPrototypeIdsV2;
export type AttachRepeatDomainV3Input = AttachRepeatDomainV2Input;
export type ParameterizableNodeDimensionV3 = ParameterizableNodeDimensionV2;
export type ParameterizeNodeDimensionV3Input = ParameterizeNodeDimensionV2Input;

export interface NewContactPointV3 extends ContactPointEditV3 {
  number?: string;
  name?: string;
  circuitText?: string | null;
  contactTypeGroupId?: string | null;
}

export interface LogicalContactEditV3 {
  number?: string;
  name?: string;
  circuitText?: string | null;
  contactTypeGroupId?: string | null;
}

export interface UpsertArticleVariantV3Input extends ArticleKeyV3 {
  id?: string;
  parameterValues?: readonly ArticleParameterValueV3[];
  contactGroups?: readonly ArticleContactGroupV3[] | null;
}

export class TemplateCommandV3Error extends Error {
  constructor(readonly code: string, message: string) {
    super(message);
    this.name = "TemplateCommandV3Error";
  }
}

export const constantExpressionV3 = constantExpressionV2;
export const constantExpressionValueV3 = constantExpressionValueV2;
export const evaluateNumericExpressionV3 = evaluateNumericExpressionV2;

/** Lossless projection of the shared geometry/parameter core for V2-only consumers. */
export function projectTemplateContentV3CoreToV2(content: TemplateContentV3): TemplateContentV2 {
  const groupNames = new Map(content.contactTypeGroups.map(group => [group.id, group.name]));
  return {
    schemaVersion: 2,
    views: content.views,
    logicalContacts: content.logicalContacts.map(contact => ({
      id: contact.id,
      number: contact.number,
      name: contact.name,
      contactType: contact.contactTypeGroupId === null ? "" : groupNames.get(contact.contactTypeGroupId) ?? "",
    })),
    parameters: content.parameters,
    repeaters: content.repeaters,
    assets: content.assets,
    articleParameterPresets: content.articleVariants.map(variant => ({
      id: variant.id,
      sourceId: variant.sourceId,
      entityType: variant.entityType,
      articleKey: variant.articleKey,
      values: variant.parameterValues,
    })),
  };
}

function restoreFromV2(original: TemplateContentV3, core: TemplateContentV2): TemplateContentV3 {
  const originalContacts = new Map(original.logicalContacts.map(contact => [contact.id, contact]));
  const originalVariants = new Map(original.articleVariants.map(variant => [variant.id, variant]));
  return {
    schemaVersion: 3,
    views: core.views,
    logicalContacts: core.logicalContacts.map(contact => {
      const previous = originalContacts.get(contact.id);
      return previous === undefined
        ? { id: contact.id, number: contact.number, name: contact.name, circuitText: null, contactTypeGroupId: null }
        : { ...previous, number: contact.number, name: contact.name };
    }),
    parameters: core.parameters,
    repeaters: core.repeaters,
    assets: core.assets,
    contactTypeGroups: original.contactTypeGroups,
    articleVariants: core.articleParameterPresets.map(preset => {
      const previous = originalVariants.get(preset.id);
      return {
        id: preset.id,
        sourceId: preset.sourceId,
        entityType: preset.entityType,
        articleKey: preset.articleKey,
        parameterValues: preset.values,
        contactGroups: previous?.contactGroups ?? null,
      };
    }),
  };
}

function translateError(error: unknown): never {
  if (error instanceof TemplateCommandV2Error || error instanceof TemplateRepeatV2Error)
    throw new TemplateCommandV3Error(error.code, error.message);
  throw error;
}

function requireValidInput(content: TemplateContentV3): void {
  const validation = validateTemplateContentV3Structure(content);
  if (!validation.valid)
    throw new TemplateCommandV3Error("invalid_input", validation.diagnostics[0]?.message ?? "Некорректный шаблон v3.");
}

function requireValidResult(content: TemplateContentV3, code: string): TemplateContentV3 {
  const validation = validateTemplateContentV3Structure(content);
  if (!validation.valid)
    throw new TemplateCommandV3Error(code, validation.diagnostics[0]?.message ?? "Команда создала некорректный шаблон v3.");
  try {
    expandTemplateRepeatsV2(projectTemplateContentV3CoreToV2(content));
  } catch (error) {
    translateError(error);
  }
  return content;
}

function runCore(content: TemplateContentV3, code: string, command: (core: TemplateContentV2) => TemplateContentV2): TemplateContentV3 {
  requireValidInput(content);
  try {
    return requireValidResult(restoreFromV2(content, command(projectTemplateContentV3CoreToV2(content))), code);
  } catch (error) {
    translateError(error);
  }
}

function runCoreTuple(
  content: TemplateContentV3,
  code: string,
  command: (core: TemplateContentV2) => [TemplateContentV2, string],
): [TemplateContentV3, string] {
  requireValidInput(content);
  try {
    const [core, id] = command(projectTemplateContentV3CoreToV2(content));
    return [requireValidResult(restoreFromV2(content, core), code), id];
  } catch (error) {
    translateError(error);
  }
}

function runCoreRepeatTuple(
  content: TemplateContentV3,
  code: string,
  command: (core: TemplateContentV2) => [TemplateContentV2, RepeatPrototypeIdsV3],
): [TemplateContentV3, RepeatPrototypeIdsV3] {
  requireValidInput(content);
  try {
    const [core, ids] = command(projectTemplateContentV3CoreToV2(content));
    return [requireValidResult(restoreFromV2(content, core), code), ids];
  } catch (error) {
    translateError(error);
  }
}

export function newTemplateContentV3(): TemplateContentV3 {
  const core = newTemplateContentV2();
  return requireValidResult({
    schemaVersion: 3,
    views: core.views,
    logicalContacts: [],
    parameters: [],
    repeaters: [],
    assets: [],
    contactTypeGroups: [],
    articleVariants: [],
  }, "invalid_initial_content");
}

export function addAdditionalViewV3(content: TemplateContentV3, name = ""): [TemplateContentV3, string] {
  return runCoreTuple(content, "invalid_view", core => addAdditionalViewV2(core, name));
}

export function renameViewV3(content: TemplateContentV3, viewId: string, name: string): TemplateContentV3 {
  return runCore(content, "invalid_view", core => renameViewV2(core, viewId, name));
}

export function deleteAdditionalViewV3(content: TemplateContentV3, viewId: string): TemplateContentV3 {
  return runCore(content, "invalid_view_delete", core => deleteAdditionalViewV2(core, viewId));
}

function requireView(content: TemplateContentV3, viewId: string): TemplateViewV3 {
  const view = content.views.find(candidate => candidate.id === viewId);
  if (!view) throw new TemplateCommandV3Error("view_not_found", "Вид не найден.");
  return view;
}

function replaceView(content: TemplateContentV3, view: TemplateViewV3): TemplateContentV3 {
  return { ...content, views: content.views.map(candidate => candidate.id === view.id ? view : candidate) };
}

function requireLogicalContact(content: TemplateContentV3, logicalContactId: string): LogicalContactV3 {
  const contact = content.logicalContacts.find(candidate => candidate.id === logicalContactId);
  if (!contact) throw new TemplateCommandV3Error("logical_contact_not_found", "Логический контакт не найден.");
  return contact;
}

function requireContactPoint(content: TemplateContentV3, viewId: string, pointId: string): { view: TemplateViewV3; point: ViewContactPointV3 } {
  const view = requireView(content, viewId);
  const point = view.contactPoints.find(candidate => candidate.id === pointId);
  if (!point) throw new TemplateCommandV3Error("contact_point_not_found", "Точка логического контакта не найдена в выбранном виде.");
  return { view, point };
}

function requireBundlePort(content: TemplateContentV3, viewId: string, portId: string): { view: TemplateViewV3; port: BundlePortV3 } {
  const view = requireView(content, viewId);
  const port = view.bundlePorts.find(candidate => candidate.id === portId);
  if (!port) throw new TemplateCommandV3Error("bundle_port_not_found", "Общая точка жгута не найдена в выбранном виде.");
  return { view, port };
}

function normalizedText(value: string, maximum: number, code: string, label: string, allowEmpty = false): string {
  const result = value.trim();
  if ((!allowEmpty && !result) || result.length > maximum || /[\u0000-\u001f]/.test(result))
    throw new TemplateCommandV3Error(code, `${label} должно быть ${allowEmpty ? "" : "непустым и "}не длиннее ${maximum} символов.`);
  return result;
}

function normalizedOptionalText(value: string | null, maximum: number, code: string, label: string): string | null {
  if (value === null) return null;
  if (value.length > maximum || /[\u0000-\u001f]/.test(value))
    throw new TemplateCommandV3Error(code, `${label} должно быть не длиннее ${maximum} символов.`);
  return value;
}

function normalizedDirection(value: ContactDirectionV2): ContactDirectionV2 {
  if (!( ["left", "right", "up", "down"] as const).includes(value))
    throw new TemplateCommandV3Error("invalid_contact_direction", "Неизвестное направление точки контакта.");
  return value;
}

function requireGroup(content: TemplateContentV3, groupId: string): ContactTypeGroupV3 {
  const group = content.contactTypeGroups.find(candidate => candidate.id === groupId);
  if (!group) throw new TemplateCommandV3Error("contact_type_group_not_found", "Группа типа контакта не найдена.");
  return group;
}

function nextContactNumber(content: TemplateContentV3): string {
  const occupied = new Set(content.logicalContacts.map(contact => contact.number));
  for (let number = 1; number <= TEMPLATE_V3_LIMITS.contacts + 1; number++)
    if (!occupied.has(String(number))) return String(number);
  throw new TemplateCommandV3Error("contact_limit", "Не удалось подобрать свободный номер контакта.");
}

function requireUniqueContactNumber(content: TemplateContentV3, number: string, exceptId?: string): void {
  if (content.logicalContacts.some(contact => contact.id !== exceptId && contact.number === number))
    throw new TemplateCommandV3Error("duplicate_contact_number", `Номер контакта ${number} уже используется.`);
}

export function addContactPointV3(
  content: TemplateContentV3,
  viewId: string,
  initial: NewContactPointV3 = {},
): [TemplateContentV3, string] {
  requireValidInput(content);
  const view = requireView(content, viewId);
  if (content.logicalContacts.length >= TEMPLATE_V3_LIMITS.contacts)
    throw new TemplateCommandV3Error("contact_limit", "Достигнут лимит логических контактов.");
  const number = initial.number === undefined
    ? nextContactNumber(content)
    : normalizedText(initial.number, 128, "invalid_contact_number", "Номер контакта");
  requireUniqueContactNumber(content, number);
  if (initial.contactTypeGroupId !== undefined && initial.contactTypeGroupId !== null)
    requireGroup(content, initial.contactTypeGroupId);
  const logicalContact: LogicalContactV3 = {
    id: crypto.randomUUID(),
    number,
    name: initial.name === undefined
      ? `Контакт ${number}`
      : normalizedText(initial.name, 256, "invalid_name", "Название контакта"),
    circuitText: initial.circuitText === undefined
      ? null
      : normalizedOptionalText(initial.circuitText, 4_096, "invalid_circuit_text", "Текст цепи"),
    contactTypeGroupId: initial.contactTypeGroupId ?? null,
  };
  const point: ViewContactPointV3 = {
    id: crypto.randomUUID(),
    logicalContactId: logicalContact.id,
    x: initial.x ?? constantExpressionV3(260),
    y: initial.y ?? constantExpressionV3(200),
    direction: normalizedDirection(initial.direction ?? "right"),
  };
  const next = {
    ...replaceView(content, { ...view, contactPoints: [...view.contactPoints, point] }),
    logicalContacts: [...content.logicalContacts, logicalContact],
  };
  return [requireValidResult(next, "invalid_contact_add"), point.id];
}

export function linkLogicalContactPointV3(
  content: TemplateContentV3,
  viewId: string,
  logicalContactId: string,
  initial: ContactPointEditV3 = {},
): [TemplateContentV3, string] {
  requireValidInput(content);
  const view = requireView(content, viewId);
  requireLogicalContact(content, logicalContactId);
  if (view.contactPoints.some(point => point.logicalContactId === logicalContactId))
    throw new TemplateCommandV3Error("duplicate_contact_point", "В выбранном виде уже есть точка этого логического контакта.");
  const point: ViewContactPointV3 = {
    id: crypto.randomUUID(),
    logicalContactId,
    x: initial.x ?? constantExpressionV3(260),
    y: initial.y ?? constantExpressionV3(200),
    direction: normalizedDirection(initial.direction ?? "right"),
  };
  const next = replaceView(content, { ...view, contactPoints: [...view.contactPoints, point] });
  return [requireValidResult(next, "invalid_contact_link"), point.id];
}

export function editLogicalContactV3(
  content: TemplateContentV3,
  logicalContactId: string,
  changes: LogicalContactEditV3,
): TemplateContentV3 {
  requireValidInput(content);
  const contact = requireLogicalContact(content, logicalContactId);
  const number = changes.number === undefined
    ? contact.number
    : normalizedText(changes.number, 128, "invalid_contact_number", "Номер контакта");
  requireUniqueContactNumber(content, number, contact.id);
  if (changes.contactTypeGroupId !== undefined && changes.contactTypeGroupId !== null)
    requireGroup(content, changes.contactTypeGroupId);
  const nextContact: LogicalContactV3 = {
    ...contact,
    number,
    name: changes.name === undefined
      ? contact.name
      : normalizedText(changes.name, 256, "invalid_name", "Название контакта"),
    circuitText: changes.circuitText === undefined
      ? contact.circuitText
      : normalizedOptionalText(changes.circuitText, 4_096, "invalid_circuit_text", "Текст цепи"),
    contactTypeGroupId: changes.contactTypeGroupId === undefined ? contact.contactTypeGroupId : changes.contactTypeGroupId,
  };
  return requireValidResult({
    ...content,
    logicalContacts: content.logicalContacts.map(candidate => candidate.id === contact.id ? nextContact : candidate),
  }, "invalid_contact_edit");
}

export function editContactPointV3(
  content: TemplateContentV3,
  viewId: string,
  pointId: string,
  changes: ContactPointEditV3,
): TemplateContentV3 {
  requireValidInput(content);
  const { view, point } = requireContactPoint(content, viewId, pointId);
  const nextPoint: ViewContactPointV3 = {
    ...point,
    x: changes.x ?? point.x,
    y: changes.y ?? point.y,
    direction: changes.direction === undefined ? point.direction : normalizedDirection(changes.direction),
  };
  return requireValidResult(replaceView(content, {
    ...view,
    contactPoints: view.contactPoints.map(candidate => candidate.id === point.id ? nextPoint : candidate),
  }), "invalid_contact_point_edit");
}

export function deleteContactPointV3(content: TemplateContentV3, viewId: string, pointId: string): TemplateContentV3 {
  requireValidInput(content);
  const { view, point } = requireContactPoint(content, viewId, pointId);
  const withoutPoint = replaceView(content, {
    ...view,
    contactPoints: view.contactPoints.filter(candidate => candidate.id !== point.id),
    repeatPlacements: view.repeatPlacements.map(placement => ({
      ...placement,
      contactPointIds: placement.contactPointIds.filter(id => id !== point.id),
    })),
  });
  const referenced = withoutPoint.views.some(candidate =>
    candidate.contactPoints.some(other => other.logicalContactId === point.logicalContactId)) ||
    withoutPoint.repeaters.some(repeater => repeater.logicalContactIds.includes(point.logicalContactId));
  const next = referenced ? withoutPoint : {
    ...withoutPoint,
    logicalContacts: withoutPoint.logicalContacts.filter(contact => contact.id !== point.logicalContactId),
  };
  return requireValidResult(next, "invalid_contact_delete");
}

export function addBundlePortV3(
  content: TemplateContentV3,
  viewId: string,
  initial: NewBundlePortV3 = {},
): [TemplateContentV3, string] {
  requireValidInput(content);
  const view = requireView(content, viewId);
  if (view.bundlePorts.length >= TEMPLATE_V3_LIMITS.contacts)
    throw new TemplateCommandV3Error("bundle_port_limit", "Достигнут лимит общих точек жгута в виде.");
  const port: BundlePortV3 = {
    id: crypto.randomUUID(),
    name: initial.name === undefined || initial.name.trim() === ""
      ? `Порт жгута ${view.bundlePorts.length + 1}`
      : normalizedText(initial.name, 256, "invalid_name", "Название порта"),
    x: initial.x ?? constantExpressionV3(260),
    y: initial.y ?? constantExpressionV3(200),
    direction: normalizedDirection(initial.direction ?? "right"),
  };
  const next = replaceView(content, { ...view, bundlePorts: [...view.bundlePorts, port] });
  return [requireValidResult(next, "invalid_bundle_port"), port.id];
}

export function editBundlePortV3(
  content: TemplateContentV3,
  viewId: string,
  portId: string,
  changes: BundlePortEditV3,
): TemplateContentV3 {
  requireValidInput(content);
  const { view, port } = requireBundlePort(content, viewId, portId);
  const nextPort: BundlePortV3 = {
    ...port,
    name: changes.name === undefined ? port.name : normalizedText(changes.name, 256, "invalid_name", "Название порта"),
    x: changes.x ?? port.x,
    y: changes.y ?? port.y,
    direction: changes.direction === undefined ? port.direction : normalizedDirection(changes.direction),
  };
  return requireValidResult(replaceView(content, {
    ...view,
    bundlePorts: view.bundlePorts.map(candidate => candidate.id === port.id ? nextPort : candidate),
  }), "invalid_bundle_port");
}

export function deleteBundlePortV3(content: TemplateContentV3, viewId: string, portId: string): TemplateContentV3 {
  requireValidInput(content);
  const { view, port } = requireBundlePort(content, viewId, portId);
  return requireValidResult(replaceView(content, {
    ...view,
    bundlePorts: view.bundlePorts.filter(candidate => candidate.id !== port.id),
  }), "invalid_bundle_port_delete");
}

export function addContactTypeGroupV3(content: TemplateContentV3, name: string): [TemplateContentV3, string] {
  requireValidInput(content);
  if (content.contactTypeGroups.length >= TEMPLATE_V3_LIMITS.contactTypeGroups)
    throw new TemplateCommandV3Error("contact_type_group_limit", "Достигнут лимит групп типов контактов.");
  const normalized = normalizedText(name, 128, "invalid_contact_type_group_name", "Название группы");
  if (content.contactTypeGroups.some(group => group.name.trim().toLowerCase() === normalized.toLowerCase()))
    throw new TemplateCommandV3Error("duplicate_contact_type_group_name", "Название группы типов контактов уже используется.");
  const group: ContactTypeGroupV3 = { id: crypto.randomUUID(), name: normalized };
  return [requireValidResult({ ...content, contactTypeGroups: [...content.contactTypeGroups, group] }, "invalid_contact_type_group"), group.id];
}

export function renameContactTypeGroupV3(content: TemplateContentV3, groupId: string, name: string): TemplateContentV3 {
  requireValidInput(content);
  const group = requireGroup(content, groupId);
  const normalized = normalizedText(name, 128, "invalid_contact_type_group_name", "Название группы");
  if (content.contactTypeGroups.some(candidate => candidate.id !== group.id && candidate.name.trim().toLowerCase() === normalized.toLowerCase()))
    throw new TemplateCommandV3Error("duplicate_contact_type_group_name", "Название группы типов контактов уже используется.");
  return requireValidResult({
    ...content,
    contactTypeGroups: content.contactTypeGroups.map(candidate => candidate.id === group.id ? { ...candidate, name: normalized } : candidate),
  }, "invalid_contact_type_group");
}

export function deleteContactTypeGroupV3(content: TemplateContentV3, groupId: string): TemplateContentV3 {
  requireValidInput(content);
  requireGroup(content, groupId);
  return requireValidResult({
    ...content,
    contactTypeGroups: content.contactTypeGroups.filter(candidate => candidate.id !== groupId),
    logicalContacts: content.logicalContacts.map(contact => contact.contactTypeGroupId === groupId
      ? { ...contact, contactTypeGroupId: null } : contact),
    articleVariants: content.articleVariants.map(variant => ({ ...variant,
      contactGroups: variant.contactGroups?.filter(group => group.contactTypeGroupId !== groupId) ?? null,
    })),
  }, "invalid_contact_type_group_delete");
}

function normalizedArticleKey(value: ArticleKeyV3): ArticleKeyV3 {
  return {
    sourceId: normalizedText(value.sourceId, 128, "invalid_article_key", "Источник артикула"),
    entityType: normalizedText(value.entityType, 64, "invalid_article_key", "Тип сущности артикула"),
    articleKey: normalizedText(value.articleKey, 512, "invalid_article_key", "Ключ артикула"),
  };
}

function articleIdentity(value: ArticleKeyV3): string {
  return `${value.sourceId}\0${value.entityType}\0${value.articleKey}`;
}

function cloneContactGroups(value: readonly ArticleContactGroupV3[] | null): ArticleContactGroupV3[] | null {
  return value === null ? null : value.map(group => ({
    ...group,
    allowedTerminalArticleKeys: group.allowedTerminalArticleKeys.map(key => ({ ...key })),
  }));
}

export function upsertArticleVariantV3(
  content: TemplateContentV3,
  input: UpsertArticleVariantV3Input,
): [TemplateContentV3, string] {
  requireValidInput(content);
  const key = normalizedArticleKey(input);
  const byId = input.id === undefined ? undefined : content.articleVariants.find(variant => variant.id === input.id);
  const byKey = content.articleVariants.find(variant => articleIdentity(variant) === articleIdentity(key));
  if (byId !== undefined && byKey !== undefined && byId.id !== byKey.id)
    throw new TemplateCommandV3Error("duplicate_article_variant", "Вариант с таким ключом уже существует.");
  if (byId === undefined && input.id !== undefined && content.articleVariants.length >= TEMPLATE_V3_LIMITS.articleVariants)
    throw new TemplateCommandV3Error("article_variant_limit", "Достигнут лимит вариантов артикула.");
  if (byId === undefined && byKey === undefined && content.articleVariants.length >= TEMPLATE_V3_LIMITS.articleVariants)
    throw new TemplateCommandV3Error("article_variant_limit", "Достигнут лимит вариантов артикула.");
  const existing = byId ?? byKey;
  const id = existing?.id ?? input.id ?? crypto.randomUUID();
  const variant: ArticleVariantV3 = {
    id,
    ...key,
    parameterValues: input.parameterValues === undefined
      ? existing?.parameterValues.map(value => ({ ...value })) ?? []
      : input.parameterValues.map(value => ({ ...value })),
    contactGroups: input.contactGroups === undefined
      ? cloneContactGroups(existing?.contactGroups ?? null)
      : cloneContactGroups(input.contactGroups),
  };
  const variants = existing === undefined
    ? [...content.articleVariants, variant]
    : content.articleVariants.map(candidate => candidate.id === existing.id ? variant : candidate);
  return [requireValidResult({ ...content, articleVariants: variants }, "invalid_article_variant"), id];
}

/**
 * Adds a batch of new article variants as one immutable command.
 *
 * The whole input is normalized and checked before any variant is created, so
 * a collision in the existing series or inside the batch leaves the source
 * content untouched. This is used by the series pattern editor to avoid a
 * partially-expanded list when one generated article is invalid.
 */
export function addArticleVariantsV3(
  content: TemplateContentV3,
  inputs: readonly UpsertArticleVariantV3Input[],
): TemplateContentV3 {
  requireValidInput(content);
  if (inputs.length === 0)
    throw new TemplateCommandV3Error("empty_article_batch", "Не задано ни одного артикула для добавления.");
  if (inputs.length > TEMPLATE_V3_LIMITS.articleVariants || content.articleVariants.length + inputs.length > TEMPLATE_V3_LIMITS.articleVariants)
    throw new TemplateCommandV3Error("article_variant_limit", "Превышен лимит артикулов серии.");

  const existing = new Set(content.articleVariants.map(articleIdentity));
  const batch = new Set<string>();
  const normalized = inputs.map((input, index) => {
    if (input.id !== undefined)
      throw new TemplateCommandV3Error("article_batch_id_not_allowed", `В пакетном добавлении нельзя изменять существующий вариант (строка ${index + 1}).`);
    const key = normalizedArticleKey(input);
    const identity = articleIdentity(key);
    if (existing.has(identity) || batch.has(identity))
      throw new TemplateCommandV3Error("duplicate_article_variant", `Вариант с ключом «${key.articleKey}» уже есть в серии или повторяется в списке.`);
    batch.add(identity);
    return key;
  });

  const variants: ArticleVariantV3[] = normalized.map(key => ({
    id: crypto.randomUUID(),
    ...key,
    parameterValues: [],
    contactGroups: null,
  }));
  return requireValidResult({ ...content, articleVariants: [...content.articleVariants, ...variants] }, "invalid_article_variant_batch");
}

export function removeArticleVariantV3(content: TemplateContentV3, variantId: string): TemplateContentV3 {
  requireValidInput(content);
  if (!content.articleVariants.some(variant => variant.id === variantId))
    throw new TemplateCommandV3Error("article_variant_not_found", "Вариант артикула не найден.");
  return requireValidResult({
    ...content,
    articleVariants: content.articleVariants.filter(variant => variant.id !== variantId),
  }, "invalid_article_variant_delete");
}

function requireArticleVariant(content: TemplateContentV3, variantId: string): ArticleVariantV3 {
  const variant = content.articleVariants.find(candidate => candidate.id === variantId);
  if (!variant) throw new TemplateCommandV3Error("article_variant_not_found", "Вариант артикула не найден.");
  return variant;
}

function inferredContactGroupConfigs(content: TemplateContentV3): ArticleContactGroupV3[] {
  const repeatedContactIds = new Set(content.repeaters.flatMap(repeater => repeater.logicalContactIds));
  const counts = new Map(content.contactTypeGroups.map(group => [group.id, 0]));
  for (const contact of content.logicalContacts)
    if (contact.contactTypeGroupId !== null && !repeatedContactIds.has(contact.id))
      counts.set(contact.contactTypeGroupId, (counts.get(contact.contactTypeGroupId) ?? 0) + 1);
  for (const repeater of content.repeaters) {
    const parameter = content.parameters.find(candidate => candidate.id === repeater.countParameterId);
    const count = parameter?.type === "integer" && typeof parameter.defaultValue === "number" ? parameter.defaultValue : 0;
    for (const logicalContactId of repeater.logicalContactIds) {
      const groupId = content.logicalContacts.find(contact => contact.id === logicalContactId)?.contactTypeGroupId;
      if (groupId !== null && groupId !== undefined)
        counts.set(groupId, (counts.get(groupId) ?? 0) + count);
    }
  }
  return content.contactTypeGroups.map(group => ({
    contactTypeGroupId: group.id,
    contactCount: counts.get(group.id) ?? 0,
    allowedTerminalArticleKeys: [],
  }));
}

export function setArticleVariantContactGroupV3(
  content: TemplateContentV3,
  variantId: string,
  contactTypeGroupId: string,
  contactCount: number,
  allowedTerminalArticleKeys: readonly ArticleKeyV3[],
): TemplateContentV3 {
  requireValidInput(content);
  const variant = requireArticleVariant(content, variantId);
  requireGroup(content, contactTypeGroupId);
  if (!Number.isSafeInteger(contactCount) || contactCount < 0 || contactCount > TEMPLATE_V3_LIMITS.contacts)
    throw new TemplateCommandV3Error("invalid_contact_count", `Количество контактов должно быть целым числом от 0 до ${TEMPLATE_V3_LIMITS.contacts}.`);
  if (allowedTerminalArticleKeys.length > TEMPLATE_V3_LIMITS.terminalArticlesPerGroup)
    throw new TemplateCommandV3Error("terminal_article_limit", "Превышен лимит совместимых артикулов терминалов.");
  const terminalKeys = allowedTerminalArticleKeys.map(normalizedArticleKey);
  if (new Set(terminalKeys.map(articleIdentity)).size !== terminalKeys.length)
    throw new TemplateCommandV3Error("duplicate_terminal_article", "Артикул терминала повторяется в группе.");
  const existingConfigs = cloneContactGroups(variant.contactGroups) ?? inferredContactGroupConfigs(content);
  const config: ArticleContactGroupV3 = { contactTypeGroupId, contactCount, allowedTerminalArticleKeys: terminalKeys };
  const contactGroups = existingConfigs.some(group => group.contactTypeGroupId === contactTypeGroupId)
    ? existingConfigs.map(group => group.contactTypeGroupId === contactTypeGroupId ? config : group)
    : [...existingConfigs, config];
  const nextVariant = { ...variant, contactGroups };
  return requireValidResult({
    ...content,
    articleVariants: content.articleVariants.map(candidate => candidate.id === variant.id ? nextVariant : candidate),
  }, "invalid_article_contact_group");
}

export const setArticleVariantContactGroupConfigV3 = setArticleVariantContactGroupV3;

export function removeArticleVariantContactGroupV3(
  content: TemplateContentV3,
  variantId: string,
  contactTypeGroupId: string,
): TemplateContentV3 {
  requireValidInput(content);
  const variant = requireArticleVariant(content, variantId);
  if (variant.contactGroups === null || !variant.contactGroups.some(group => group.contactTypeGroupId === contactTypeGroupId))
    throw new TemplateCommandV3Error("article_contact_group_not_found", "Настройка группы контактов варианта не найдена.");
  const remaining = variant.contactGroups.filter(group => group.contactTypeGroupId !== contactTypeGroupId);
  const nextVariant: ArticleVariantV3 = { ...variant, contactGroups: remaining.length === 0 ? null : remaining };
  return requireValidResult({
    ...content,
    articleVariants: content.articleVariants.map(candidate => candidate.id === variant.id ? nextVariant : candidate),
  }, "invalid_article_contact_group_delete");
}

export const removeArticleVariantContactGroupConfigV3 = removeArticleVariantContactGroupV3;

export function createRepeatPrototypeV3(
  content: TemplateContentV3,
  input: CreateRepeatPrototypeV3Input,
): [TemplateContentV3, RepeatPrototypeIdsV3] {
  return runCoreRepeatTuple(content, "invalid_repeat_prototype", core => createRepeatPrototypeV2(core, input));
}

export function attachRepeatDomainV3(
  content: TemplateContentV3,
  input: AttachRepeatDomainV3Input,
): [TemplateContentV3, string] {
  return runCoreTuple(content, "invalid_repeat_attachment", core => attachRepeatDomainV2(core, input));
}

export const attachRepeatDomainCrossViewV3 = attachRepeatDomainV3;

export function deleteRepeatPrototypeV3(content: TemplateContentV3, repeatDomainId: string): TemplateContentV3 {
  return runCore(content, "invalid_repeat_delete", core => deleteRepeatPrototypeV2(core, repeatDomainId));
}

export function setRepeatCountV3(content: TemplateContentV3, repeatDomainId: string, count: number): TemplateContentV3 {
  return runCore(content, "invalid_repeat_count", core => setRepeatCountV2(core, repeatDomainId, count));
}

export function setRepeatStepV3(
  content: TemplateContentV3,
  viewId: string,
  repeatDomainId: string,
  step: PointExpressionV2,
): TemplateContentV3 {
  return runCore(content, "invalid_repeat_step", core => setRepeatStepV2(core, viewId, repeatDomainId, step));
}

export function addLayerV3(content: TemplateContentV3, viewId: string, name: string): [TemplateContentV3, string] {
  return runCoreTuple(content, "invalid_layer", core => addLayerV2(core, viewId, name));
}

export function renameLayerV3(content: TemplateContentV3, viewId: string, layerId: string, name: string): TemplateContentV3 {
  return runCore(content, "invalid_layer", core => renameLayerV2(core, viewId, layerId, name));
}

export function reorderLayerV3(content: TemplateContentV3, viewId: string, layerId: string, toIndex: number): TemplateContentV3 {
  return runCore(content, "invalid_layer", core => reorderLayerV2(core, viewId, layerId, toIndex));
}

export function setLayerVisibleV3(content: TemplateContentV3, viewId: string, layerId: string, visible: boolean): TemplateContentV3 {
  return runCore(content, "invalid_layer", core => setLayerVisibleV2(core, viewId, layerId, visible));
}

export function setLayerLockedV3(content: TemplateContentV3, viewId: string, layerId: string, locked: boolean): TemplateContentV3 {
  return runCore(content, "invalid_layer", core => setLayerLockedV2(core, viewId, layerId, locked));
}

export function deleteLayerV3(content: TemplateContentV3, viewId: string, layerId: string): TemplateContentV3 {
  return runCore(content, "invalid_layer_delete", core => deleteLayerV2(core, viewId, layerId));
}

export function addNodeV3(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
  node: TemplateNodeV3,
  atIndex?: number,
): TemplateContentV3 {
  return runCore(content, "invalid_node", core => addNodeV2(core, viewId, layerId, node as TemplateNodeV2, atIndex));
}

export function addBasicNodeV3(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
  kind: BasicNodeKindV3,
): [TemplateContentV3, string] {
  return runCoreTuple(content, "invalid_node", core => addBasicNodeV2(core, viewId, layerId, kind));
}

export function editNodeV3(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
  nodeId: string,
  changes: NodeEditV3,
): TemplateContentV3 {
  return runCore(content, "invalid_node", core => editNodeV2(core, viewId, layerId, nodeId, changes));
}

export function parameterizeNodeDimensionV3(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
  nodeId: string,
  dimension: ParameterizableNodeDimensionV3,
  input: ParameterizeNodeDimensionV3Input,
): [TemplateContentV3, string] {
  return runCoreTuple(content, "invalid_parameterized_dimension", core =>
    parameterizeNodeDimensionV2(core, viewId, layerId, nodeId, dimension, input));
}

export function setTemplateParameterDefaultV3(content: TemplateContentV3, parameterId: string, value: number): TemplateContentV3 {
  return runCore(content, "invalid_parameter_default", core => setTemplateParameterDefaultV2(core, parameterId, value));
}

export function setNodeLockedV3(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
  nodeId: string,
  locked: boolean,
): TemplateContentV3 {
  return runCore(content, "invalid_node", core => setNodeLockedV2(core, viewId, layerId, nodeId, locked));
}

type PointNodeV3 = Extract<TemplateNodeV3, { kind: "line" | "polyline" | "bezier" | "closedContour" }>;

function requireEditablePointNode(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
  nodeId: string,
): { view: TemplateViewV3; layer: TemplateViewV3["layers"][number]; node: PointNodeV3 } {
  requireValidInput(content);
  const view = requireView(content, viewId);
  const layer = view.layers.find(candidate => candidate.id === layerId);
  if (!layer) throw new TemplateCommandV3Error("layer_not_found", "Слой не найден.");
  const node = layer.nodes.find(candidate => candidate.id === nodeId);
  if (!node) throw new TemplateCommandV3Error("node_not_found", "Объект не найден.");
  if (layer.locked) throw new TemplateCommandV3Error("layer_locked", "Слой заблокирован.");
  if (node.locked) throw new TemplateCommandV3Error("node_locked", "Объект заблокирован.");
  if (node.kind !== "line" && node.kind !== "polyline" && node.kind !== "bezier" && node.kind !== "closedContour")
    throw new TemplateCommandV3Error("unsupported_point_geometry", "Точки этого объекта нельзя редактировать.");
  return { view, layer, node };
}

function requirePointIndex(points: readonly PointExpressionV2[], pointIndex: number): void {
  if (!Number.isSafeInteger(pointIndex) || pointIndex < 0 || pointIndex >= points.length)
    throw new TemplateCommandV3Error("point_index", "Точка с таким индексом не найдена.");
}

function requireFiniteCoordinate(value: number, code: string): void {
  if (!Number.isFinite(value) || value < -TEMPLATE_V2_LIMITS.coordinate || value > TEMPLATE_V2_LIMITS.coordinate)
    throw new TemplateCommandV3Error(code, `Координата должна быть конечным числом от ${-TEMPLATE_V2_LIMITS.coordinate} до ${TEMPLATE_V2_LIMITS.coordinate}.`);
}

function requireConstantPoint(point: PointExpressionV2): { x: number; y: number } {
  if (point.x.kind !== "constant" || point.y.kind !== "constant")
    throw new TemplateCommandV3Error("non_constant_geometry", "Параметризованную точку нельзя редактировать как константу.");
  return { x: point.x.value, y: point.y.value };
}

function replacePointNode(
  content: TemplateContentV3,
  view: TemplateViewV3,
  layer: TemplateViewV3["layers"][number],
  node: PointNodeV3,
): TemplateContentV3 {
  return requireValidResult(replaceView(content, {
    ...view,
    layers: view.layers.map(candidate => candidate.id === layer.id ? {
      ...layer,
      nodes: layer.nodes.map(candidate => candidate.id === node.id ? node : candidate),
    } : candidate),
  }), "invalid_node_points");
}

function withPointGeometry(node: PointNodeV3, points: PointExpressionV2[]): PointNodeV3 {
  if (node.kind === "line") return { ...node, geometry: { ...node.geometry, points } };
  if (node.kind === "polyline") return { ...node, geometry: { ...node.geometry, points } };
  if (node.kind === "bezier") return { ...node, geometry: { ...node.geometry, points } };
  return { ...node, geometry: { points } };
}

/** Moves one constant line/polyline endpoint, bend, or Bezier control/end point. */
export function moveNodePointV3(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
  nodeId: string,
  pointIndex: number,
  deltaX: number,
  deltaY: number,
): TemplateContentV3 {
  if (!Number.isFinite(deltaX) || !Number.isFinite(deltaY))
    throw new TemplateCommandV3Error("invalid_point_move", "Смещение точки должно быть конечным числом.");
  const { view, layer, node } = requireEditablePointNode(content, viewId, layerId, nodeId);
  requirePointIndex(node.geometry.points, pointIndex);
  const current = requireConstantPoint(node.geometry.points[pointIndex]!);
  const x = current.x + deltaX, y = current.y + deltaY;
  requireFiniteCoordinate(x, "invalid_point_move");
  requireFiniteCoordinate(y, "invalid_point_move");
  if (deltaX === 0 && deltaY === 0) return content;
  const points = node.geometry.points.map((point, index) => index === pointIndex
    ? { x: constantExpressionV3(x), y: constantExpressionV3(y) }
    : point);
  return replacePointNode(content, view, layer, withPointGeometry(node, points));
}

/** Inserts one constant bend after the selected line/polyline segment. */
export function insertNodePointV3(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
  nodeId: string,
  segmentIndex: number,
  x: number,
  y: number,
): TemplateContentV3 {
  requireFiniteCoordinate(x, "invalid_point_insert");
  requireFiniteCoordinate(y, "invalid_point_insert");
  const { view, layer, node } = requireEditablePointNode(content, viewId, layerId, nodeId);
  if (node.kind === "bezier")
    throw new TemplateCommandV3Error("unsupported_point_insert", "Добавление отдельных точек кривой Безье не поддерживается.");
  const segmentCount = node.kind === "closedContour" ? node.geometry.points.length : node.geometry.points.length - 1;
  if (!Number.isSafeInteger(segmentIndex) || segmentIndex < 0 || segmentIndex >= segmentCount)
    throw new TemplateCommandV3Error("segment_index", "Сегмент с таким индексом не найден.");
  if (node.geometry.points.length >= 512)
    throw new TemplateCommandV3Error("point_limit", "Линия не может содержать больше 512 точек.");
  const points = [...node.geometry.points];
  const insertionIndex = node.kind === "closedContour" && segmentIndex === node.geometry.points.length - 1
    ? node.geometry.points.length
    : segmentIndex + 1;
  points.splice(insertionIndex, 0, { x: constantExpressionV3(x), y: constantExpressionV3(y) });
  return replacePointNode(content, view, layer, withPointGeometry(node, points));
}

/** Removes one internal line/polyline bend while preserving both endpoints. */
export function deleteNodePointV3(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
  nodeId: string,
  pointIndex: number,
): TemplateContentV3 {
  const { view, layer, node } = requireEditablePointNode(content, viewId, layerId, nodeId);
  if (node.kind === "bezier")
    throw new TemplateCommandV3Error("unsupported_point_delete", "Удаление отдельных точек кривой Безье не поддерживается.");
  requirePointIndex(node.geometry.points, pointIndex);
  if (node.kind === "closedContour") {
    if (node.geometry.points.length <= 3)
      throw new TemplateCommandV3Error("minimum_points", "В замкнутом контуре должно остаться не меньше трёх точек.");
    const points = node.geometry.points.filter((_, index) => index !== pointIndex);
    return replacePointNode(content, view, layer, withPointGeometry(node, points));
  }
  if (pointIndex === 0 || pointIndex === node.geometry.points.length - 1)
    throw new TemplateCommandV3Error("endpoint_delete", "Конечные точки линии удалить нельзя.");
  if (node.geometry.points.length <= 2)
    throw new TemplateCommandV3Error("minimum_points", "В линии должно остаться не меньше двух точек.");
  const points = node.geometry.points.filter((_, index) => index !== pointIndex);
  return replacePointNode(content, view, layer, withPointGeometry(node, points));
}

export function moveNodeV3(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
  nodeId: string,
  deltaX: number,
  deltaY: number,
): TemplateContentV3 {
  return runCore(content, "invalid_node", core => moveNodeV2(core, viewId, layerId, nodeId, deltaX, deltaY));
}

export function resizeNodeV3(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
  nodeId: string,
  handle: NodeResizeHandleV3,
  deltaX: number,
  deltaY: number,
): TemplateContentV3 {
  return runCore(content, "invalid_node", core => resizeNodeV2(core, viewId, layerId, nodeId, handle, deltaX, deltaY));
}

export function deleteNodeV3(content: TemplateContentV3, viewId: string, layerId: string, nodeId: string): TemplateContentV3 {
  return runCore(content, "invalid_node_delete", core => deleteNodeV2(core, viewId, layerId, nodeId));
}

export function reorderNodeV3(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
  nodeId: string,
  toIndex: number,
): TemplateContentV3 {
  return runCore(content, "invalid_node", core => reorderNodeV2(core, viewId, layerId, nodeId, toIndex));
}

type GroupNodeV3 = Extract<TemplateNodeV3, { kind: "group" }>;
type RootOrderDirectionV3 = "forward" | "backward";

const identityTransformV3 = (): TransformV2 => ({
  translateX: constantExpressionV3(0), translateY: constantExpressionV3(0),
  rotationDegrees: constantExpressionV3(0), scaleX: constantExpressionV3(1), scaleY: constantExpressionV3(1),
});

function requireLayerForTreeCommand(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
): { view: TemplateViewV3; layer: TemplateViewV3["layers"][number] } {
  requireValidInput(content);
  const view = requireView(content, viewId);
  const layer = view.layers.find(candidate => candidate.id === layerId);
  if (!layer) throw new TemplateCommandV3Error("layer_not_found", "Слой не найден.");
  if (layer.locked) throw new TemplateCommandV3Error("layer_locked", "Слой заблокирован.");
  return { view, layer };
}

function ownedNodeIds(nodes: readonly TemplateNodeV3[]): Set<string> {
  return new Set(nodes.flatMap(node => node.kind === "group" ? node.geometry.childIds : []));
}

function requireRootNode(
  layer: TemplateViewV3["layers"][number],
  ownedIds: ReadonlySet<string>,
  nodeId: string,
): TemplateNodeV3 {
  const node = layer.nodes.find(candidate => candidate.id === nodeId);
  if (!node) throw new TemplateCommandV3Error("node_not_found", "Объект не найден.");
  if (ownedIds.has(node.id))
    throw new TemplateCommandV3Error("node_not_root", "Команда доступна только для объектов верхнего уровня.");
  if (node.locked) throw new TemplateCommandV3Error("node_locked", "Объект заблокирован.");
  return node;
}

function nodeSubtreeContainsAnyIdV3(
  node: TemplateNodeV3,
  nodesById: ReadonlyMap<string, TemplateNodeV3>,
  targetIds: ReadonlySet<string>,
  visiting = new Set<string>(),
): boolean {
  if (targetIds.has(node.id)) return true;
  if (node.kind !== "group" || visiting.has(node.id)) return false;
  const nextVisiting = new Set(visiting);
  nextVisiting.add(node.id);
  return node.geometry.childIds.some(childId => {
    const child = nodesById.get(childId);
    return child ? nodeSubtreeContainsAnyIdV3(child, nodesById, targetIds, nextVisiting) : false;
  });
}

function requireNoRepeatPrototypeInSubtreeV3(
  node: TemplateNodeV3,
  layer: TemplateViewV3["layers"][number],
  view: TemplateViewV3,
): void {
  const prototypeIds = new Set(view.repeatPlacements.map(placement => placement.prototypeGroupId));
  if (nodeSubtreeContainsAnyIdV3(node, new Map(layer.nodes.map(candidate => [candidate.id, candidate])), prototypeIds))
    throw new TemplateCommandV3Error(
      "repeat_prototype_group",
      "Объект с группой-прототипом повтора нельзя включать в структурную операцию.",
    );
}

/** Wraps two or more adjacent root nodes in an identity group without changing their paint order. */
export function groupRootNodesV3(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
  nodeIds: readonly string[],
): [TemplateContentV3, string] {
  const { view, layer } = requireLayerForTreeCommand(content, viewId, layerId);
  if (nodeIds.length < 2)
    throw new TemplateCommandV3Error("group_selection", "Для группировки выберите не меньше двух объектов.");
  const selectedIds = new Set(nodeIds);
  if (selectedIds.size !== nodeIds.length)
    throw new TemplateCommandV3Error("duplicate_node", "Один объект выбран для группировки несколько раз.");
  if (layer.nodes.length >= TEMPLATE_V3_LIMITS.nodes)
    throw new TemplateCommandV3Error("node_limit", "Достигнут лимит объектов шаблона.");
  const ownedIds = ownedNodeIds(layer.nodes);
  const selected = nodeIds.map(id => requireRootNode(layer, ownedIds, id));
  const roots = layer.nodes.filter(node => !ownedIds.has(node.id));
  const selectedRootIndexes = roots
    .map((node, index) => selectedIds.has(node.id) ? index : -1)
    .filter(index => index >= 0);
  const firstSelectedRoot = Math.min(...selectedRootIndexes);
  const lastSelectedRoot = Math.max(...selectedRootIndexes);
  if (lastSelectedRoot - firstSelectedRoot + 1 !== selectedRootIndexes.length)
    throw new TemplateCommandV3Error(
      "non_contiguous_group_selection",
      "Группировать можно только соседние объекты, чтобы не изменить порядок отрисовки.",
    );
  selected.forEach(node => requireNoRepeatPrototypeInSubtreeV3(node, layer, view));

  const childIds = layer.nodes.filter(node => selectedIds.has(node.id)).map(node => node.id);
  const insertionIndex = Math.max(...childIds.map(id => layer.nodes.findIndex(node => node.id === id))) + 1;
  const groupId = crypto.randomUUID();
  const group: GroupNodeV3 = {
    id: groupId, kind: "group", layerId: layer.id, visible: true, locked: false, opacity: 1,
    transform: identityTransformV3(),
    stroke: { color: "#27445a", width: constantExpressionV3(2), dash: "solid" },
    fill: { color: null }, geometry: { childIds },
  };
  const nodes = [...layer.nodes];
  nodes.splice(insertionIndex, 0, group);
  const result = replaceView(content, {
    ...view,
    layers: view.layers.map(candidate => candidate.id === layer.id ? { ...layer, nodes } : candidate),
  });
  return [requireValidResult(result, "invalid_group"), groupId];
}

interface MatrixV3 { a: number; b: number; c: number; d: number; e: number; f: number }

interface BoundsV3 { minX: number; minY: number; maxX: number; maxY: number }

function constantGeometryValue(expression: PointExpressionV2["x"]): number {
  if (expression.kind !== "constant")
    throw new TemplateCommandV3Error("non_constant_geometry", "Параметризованную геометрию нельзя безопасно повернуть.");
  return expression.value;
}

function boundsFromPoints(points: readonly PointExpressionV2[]): BoundsV3 {
  const evaluated = points.map(point => ({
    x: constantGeometryValue(point.x),
    y: constantGeometryValue(point.y),
  }));
  const xs = evaluated.map(point => point.x), ys = evaluated.map(point => point.y);
  return { minX: Math.min(...xs), minY: Math.min(...ys), maxX: Math.max(...xs), maxY: Math.max(...ys) };
}

function unionBounds(bounds: readonly BoundsV3[]): BoundsV3 {
  return {
    minX: Math.min(...bounds.map(value => value.minX)),
    minY: Math.min(...bounds.map(value => value.minY)),
    maxX: Math.max(...bounds.map(value => value.maxX)),
    maxY: Math.max(...bounds.map(value => value.maxY)),
  };
}

function transformPoint(matrix: MatrixV3, x: number, y: number): { x: number; y: number } {
  return { x: matrix.a * x + matrix.c * y + matrix.e, y: matrix.b * x + matrix.d * y + matrix.f };
}

function transformBounds(bounds: BoundsV3, matrix: MatrixV3): BoundsV3 {
  const points = [
    transformPoint(matrix, bounds.minX, bounds.minY),
    transformPoint(matrix, bounds.maxX, bounds.minY),
    transformPoint(matrix, bounds.maxX, bounds.maxY),
    transformPoint(matrix, bounds.minX, bounds.maxY),
  ];
  return {
    minX: Math.min(...points.map(point => point.x)), minY: Math.min(...points.map(point => point.y)),
    maxX: Math.max(...points.map(point => point.x)), maxY: Math.max(...points.map(point => point.y)),
  };
}

function localNodeBounds(
  node: TemplateNodeV3,
  nodesById: ReadonlyMap<string, TemplateNodeV3>,
  visiting: Set<string>,
): BoundsV3 {
  if (node.kind === "group") {
    if (visiting.has(node.id))
      throw new TemplateCommandV3Error("group_cycle", "Циклическую группу нельзя повернуть.");
    visiting.add(node.id);
    const childBounds = node.geometry.childIds.map(id => {
      const child = nodesById.get(id);
      if (!child) throw new TemplateCommandV3Error("missing_group_child", "Группа ссылается на отсутствующий объект.");
      const matrix = constantTransformMatrix(child.transform);
      if (Math.abs(matrix.a * matrix.d - matrix.b * matrix.c) < 1e-12)
        throw new TemplateCommandV3Error("degenerate_transform", "Вырожденное преобразование нельзя безопасно повернуть.");
      return transformBounds(localNodeBounds(child, nodesById, visiting), matrix);
    });
    visiting.delete(node.id);
    if (childBounds.length === 0)
      throw new TemplateCommandV3Error("degenerate_geometry", "Пустую группу нельзя повернуть.");
    return unionBounds(childBounds);
  }
  if (node.kind === "line" || node.kind === "polyline") {
    constantGeometryValue(node.geometry.bendRadius);
    return boundsFromPoints(node.geometry.points);
  }
  if (node.kind === "bezier") {
    const points = node.geometry.points.map(p => ({x:constantGeometryValue(p.x),y:constantGeometryValue(p.y)}));
    const extrema = [...points.filter((_,i) => i % 3 === 0)];
    for (let i=0;i+3<points.length;i+=3) {
      const [p0,p1,p2,p3] = points.slice(i,i+4) as [typeof points[number],typeof points[number],typeof points[number],typeof points[number]];
      for (const key of ["x","y"] as const) {
        const a=-p0[key]+3*p1[key]-3*p2[key]+p3[key], b=2*(p0[key]-2*p1[key]+p2[key]), c=p1[key]-p0[key];
        const discriminant=b*b-4*a*c;
        const roots=Math.abs(a)<1e-12 ? (Math.abs(b)<1e-12 ? [] : [-c/b]) : discriminant<0 ? [] : [(-b+Math.sqrt(discriminant))/(2*a),(-b-Math.sqrt(discriminant))/(2*a)];
        for (const t of roots) if(t>0 && t<1) { const u=1-t; extrema.push({x:u*u*u*p0.x+3*u*u*t*p1.x+3*u*t*t*p2.x+t*t*t*p3.x,y:u*u*u*p0.y+3*u*u*t*p1.y+3*u*t*t*p2.y+t*t*t*p3.y}); }
      }
    }
    return {minX:Math.min(...extrema.map(p=>p.x)),minY:Math.min(...extrema.map(p=>p.y)),maxX:Math.max(...extrema.map(p=>p.x)),maxY:Math.max(...extrema.map(p=>p.y))};
  }
  if (node.kind === "closedContour")
    return boundsFromPoints(node.geometry.points);
  if (node.kind === "rectangle") {
    const x = constantGeometryValue(node.geometry.x), y = constantGeometryValue(node.geometry.y);
    const width = constantGeometryValue(node.geometry.width), height = constantGeometryValue(node.geometry.height);
    node.geometry.cornerRadii.forEach(constantGeometryValue);
    if (width <= 0 || height <= 0)
      throw new TemplateCommandV3Error("degenerate_geometry", "Прямоугольник должен иметь положительные размеры.");
    return { minX: x, minY: y, maxX: x + width, maxY: y + height };
  }
  if (node.kind === "ellipse") {
    const centerX = constantGeometryValue(node.geometry.centerX), centerY = constantGeometryValue(node.geometry.centerY);
    const radiusX = constantGeometryValue(node.geometry.radiusX), radiusY = constantGeometryValue(node.geometry.radiusY);
    if (radiusX <= 0 || radiusY <= 0)
      throw new TemplateCommandV3Error("degenerate_geometry", "Эллипс должен иметь положительные радиусы.");
    return { minX: centerX - radiusX, minY: centerY - radiusY, maxX: centerX + radiusX, maxY: centerY + radiusY };
  }
  if (node.kind === "text") {
    const x = constantGeometryValue(node.geometry.x), y = constantGeometryValue(node.geometry.y);
    const fontSize = constantGeometryValue(node.geometry.fontSize);
    if (fontSize <= 0)
      throw new TemplateCommandV3Error("degenerate_geometry", "Текст должен иметь положительный размер шрифта.");
    const width = node.geometry.text.length * fontSize * 0.6;
    return { minX: x, minY: y - fontSize, maxX: x + width, maxY: y };
  }
  const x = constantGeometryValue(node.geometry.x), y = constantGeometryValue(node.geometry.y);
  const width = constantGeometryValue(node.geometry.width), height = constantGeometryValue(node.geometry.height);
  if (width <= 0 || height <= 0)
    throw new TemplateCommandV3Error("degenerate_geometry", "Изображение должно иметь положительные размеры.");
  return { minX: x, minY: y, maxX: x + width, maxY: y + height };
}

function constantTransformMatrix(transform: TransformV2): MatrixV3 {
  const read = (value: TransformV2[keyof TransformV2]): number => {
    if (value.kind !== "constant")
      throw new TemplateCommandV3Error("non_constant_transform", "Параметризованное преобразование нельзя безопасно разгруппировать.");
    return value.value;
  };
  const tx = read(transform.translateX), ty = read(transform.translateY);
  const rotation = read(transform.rotationDegrees) * Math.PI / 180;
  const sx = read(transform.scaleX), sy = read(transform.scaleY);
  const cos = Math.cos(rotation), sin = Math.sin(rotation);
  return { a: cos * sx, b: sin * sx, c: -sin * sy, d: cos * sy, e: tx, f: ty };
}

function multiplyMatrices(left: MatrixV3, right: MatrixV3): MatrixV3 {
  return {
    a: left.a * right.a + left.c * right.b,
    b: left.b * right.a + left.d * right.b,
    c: left.a * right.c + left.c * right.d,
    d: left.b * right.c + left.d * right.d,
    e: left.a * right.e + left.c * right.f + left.e,
    f: left.b * right.e + left.d * right.f + left.f,
  };
}

/** Same pivot for interactive preview and committed rotation. */
export function rootNodeRotationCenterV3(node: TemplateNodeV3, nodes: readonly TemplateNodeV3[]): {x:number;y:number} {
  const bounds = localNodeBounds(node, new Map(nodes.map(item => [item.id,item])), new Set());
  return transformPoint(constantTransformMatrix(node.transform), (bounds.minX+bounds.maxX)/2, (bounds.minY+bounds.maxY)/2);
}

/** Sets an absolute rotation for one root node while preserving its world-space geometric center. */
export function setRootNodeRotationAroundCenterV3(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
  nodeId: string,
  rotationDegrees: number,
): TemplateContentV3 {
  if (!Number.isFinite(rotationDegrees) || Math.abs(rotationDegrees) > TEMPLATE_V2_LIMITS.coordinate)
    throw new TemplateCommandV3Error("invalid_rotation", "Угол поворота должен быть конечным числом в допустимом диапазоне.");
  const { view, layer } = requireLayerForTreeCommand(content, viewId, layerId);
  const node = requireRootNode(layer, ownedNodeIds(layer.nodes), nodeId);
  requireNoRepeatPrototypeInSubtreeV3(node, layer, view);
  const currentMatrix = constantTransformMatrix(node.transform);
  if (Math.abs(currentMatrix.a * currentMatrix.d - currentMatrix.b * currentMatrix.c) < 1e-12)
    throw new TemplateCommandV3Error("degenerate_transform", "Вырожденное преобразование нельзя безопасно повернуть.");
  const bounds = localNodeBounds(node, new Map(layer.nodes.map(candidate => [candidate.id, candidate])), new Set());
  if (![bounds.minX, bounds.minY, bounds.maxX, bounds.maxY].every(Number.isFinite) ||
      (Math.abs(bounds.maxX - bounds.minX) < 1e-12 && Math.abs(bounds.maxY - bounds.minY) < 1e-12))
    throw new TemplateCommandV3Error("degenerate_geometry", "Вырожденную геометрию нельзя безопасно повернуть.");
  if (node.transform.rotationDegrees.kind !== "constant" || node.transform.scaleX.kind !== "constant" ||
      node.transform.scaleY.kind !== "constant" || node.transform.translateX.kind !== "constant" ||
      node.transform.translateY.kind !== "constant")
    throw new TemplateCommandV3Error("non_constant_transform", "Параметризованное преобразование нельзя безопасно повернуть.");
  if (node.transform.rotationDegrees.value === rotationDegrees) return content;

  const centerX = (bounds.minX + bounds.maxX) / 2, centerY = (bounds.minY + bounds.maxY) / 2;
  const worldCenter = transformPoint(currentMatrix, centerX, centerY);
  const radians = rotationDegrees * Math.PI / 180;
  const scaledX = centerX * node.transform.scaleX.value, scaledY = centerY * node.transform.scaleY.value;
  const translateX = worldCenter.x - (Math.cos(radians) * scaledX - Math.sin(radians) * scaledY);
  const translateY = worldCenter.y - (Math.sin(radians) * scaledX + Math.cos(radians) * scaledY);
  if (![translateX, translateY].every(value => Number.isFinite(value) && Math.abs(value) <= TEMPLATE_V2_LIMITS.coordinate))
    throw new TemplateCommandV3Error("invalid_transform", "Поворот выводит объект за допустимый диапазон координат.");
  const replacement: TemplateNodeV3 = { ...node, transform: {
    ...node.transform,
    translateX: constantExpressionV3(translateX),
    translateY: constantExpressionV3(translateY),
    rotationDegrees: constantExpressionV3(rotationDegrees),
  } };
  return requireValidResult(replaceView(content, {
    ...view,
    layers: view.layers.map(candidate => candidate.id === layer.id ? {
      ...layer,
      nodes: layer.nodes.map(candidate => candidate.id === node.id ? replacement : candidate),
    } : candidate),
  }), "invalid_rotation");
}

function matrixToTransform(matrix: MatrixV3): TransformV2 {
  const scaleX = Math.hypot(matrix.a, matrix.b);
  const determinant = matrix.a * matrix.d - matrix.b * matrix.c;
  if (scaleX < 1e-12 || Math.abs(determinant) < 1e-12)
    throw new TemplateCommandV3Error("non_decomposable_transform", "Вырожденное преобразование нельзя безопасно разгруппировать.");
  const scaleY = determinant / scaleX;
  const rotation = Math.atan2(matrix.b, matrix.a);
  const expectedC = -Math.sin(rotation) * scaleY;
  const expectedD = Math.cos(rotation) * scaleY;
  const tolerance = 1e-8 * Math.max(1, Math.abs(matrix.c), Math.abs(matrix.d));
  if (Math.abs(matrix.c - expectedC) > tolerance || Math.abs(matrix.d - expectedD) > tolerance)
    throw new TemplateCommandV3Error("non_decomposable_transform", "Композиция преобразований создаёт сдвиг (skew) и не может быть сохранена текущим форматом TRS.");
  const values = [matrix.e, matrix.f, rotation * 180 / Math.PI, scaleX, scaleY];
  if (values.some(value => !Number.isFinite(value) || Math.abs(value) > TEMPLATE_V2_LIMITS.coordinate))
    throw new TemplateCommandV3Error("invalid_transform", "Результирующее преобразование выходит за допустимый диапазон.");
  return {
    translateX: constantExpressionV3(matrix.e), translateY: constantExpressionV3(matrix.f),
    rotationDegrees: constantExpressionV3(rotation * 180 / Math.PI),
    scaleX: constantExpressionV3(scaleX), scaleY: constantExpressionV3(scaleY),
  };
}

/** Removes one root group and bakes its constant TRS and opacity into its direct children. */
export function ungroupRootNodeV3(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
  groupId: string,
): TemplateContentV3 {
  const { view, layer } = requireLayerForTreeCommand(content, viewId, layerId);
  const ownedIds = ownedNodeIds(layer.nodes);
  const node = requireRootNode(layer, ownedIds, groupId);
  if (node.kind !== "group") throw new TemplateCommandV3Error("node_not_group", "Выбранный объект не является группой.");
  requireNoRepeatPrototypeInSubtreeV3(node, layer, view);
  if (node.opacity !== 1)
    throw new TemplateCommandV3Error(
      "group_compositing",
      "Группу с общей прозрачностью нельзя безопасно разгруппировать без изменения наложения фигур.",
    );
  const childIds = new Set(node.geometry.childIds);
  const childrenInPaintOrder = layer.nodes.filter(candidate => childIds.has(candidate.id));
  const children = new Map(node.geometry.childIds.map(id => [id, layer.nodes.find(candidate => candidate.id === id)]));
  if ([...children.values()].some(child => !child))
    throw new TemplateCommandV3Error("missing_group_child", "Группа ссылается на отсутствующий объект.");
  if ([...children.values()].some(child => child!.locked))
    throw new TemplateCommandV3Error("node_locked", "Один из дочерних объектов заблокирован.");
  const parentMatrix = constantTransformMatrix(node.transform);
  const replacements = new Map(childrenInPaintOrder.map(child => {
    const id = child.id;
    return [id, {
      ...child,
      visible: node.visible && child.visible,
      opacity: node.opacity * child.opacity,
      transform: matrixToTransform(multiplyMatrices(parentMatrix, constantTransformMatrix(child.transform))),
    } satisfies TemplateNodeV3] as const;
  }));
  const groupIndex = layer.nodes.findIndex(candidate => candidate.id === node.id);
  const removedBeforeGroup = layer.nodes.slice(0, groupIndex).filter(candidate => replacements.has(candidate.id)).length;
  const insertionIndex = groupIndex - removedBeforeGroup;
  const nodes = layer.nodes.filter(candidate => candidate.id !== node.id && !replacements.has(candidate.id));
  nodes.splice(insertionIndex, 0, ...childrenInPaintOrder.map(child => replacements.get(child.id)!));
  return requireValidResult(replaceView(content, {
    ...view,
    layers: view.layers.map(candidate => candidate.id === layer.id ? { ...layer, nodes } : candidate),
  }), "invalid_ungroup");
}

/** Moves one root node by one root-level paint-order step, ignoring owned child storage entries. */
export function reorderRootNodeStepV3(
  content: TemplateContentV3,
  viewId: string,
  layerId: string,
  nodeId: string,
  direction: RootOrderDirectionV3,
): TemplateContentV3 {
  const { view, layer } = requireLayerForTreeCommand(content, viewId, layerId);
  if (direction !== "forward" && direction !== "backward")
    throw new TemplateCommandV3Error("root_order_direction", "Неизвестное направление изменения порядка.");
  const ownedIds = ownedNodeIds(layer.nodes);
  requireRootNode(layer, ownedIds, nodeId);
  const roots = layer.nodes.filter(node => !ownedIds.has(node.id));
  const rootIndex = roots.findIndex(node => node.id === nodeId);
  const adjacentIndex = rootIndex + (direction === "forward" ? 1 : -1);
  if (adjacentIndex < 0 || adjacentIndex >= roots.length) return content;
  const adjacentId = roots[adjacentIndex]!.id;
  const leftIndex = layer.nodes.findIndex(node => node.id === nodeId);
  const rightIndex = layer.nodes.findIndex(node => node.id === adjacentId);
  const nodes = [...layer.nodes];
  [nodes[leftIndex], nodes[rightIndex]] = [nodes[rightIndex]!, nodes[leftIndex]!];
  return requireValidResult(replaceView(content, {
    ...view,
    layers: view.layers.map(candidate => candidate.id === layer.id ? { ...layer, nodes } : candidate),
  }), "invalid_root_order");
}
