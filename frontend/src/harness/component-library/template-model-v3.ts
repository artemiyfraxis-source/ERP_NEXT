import {
  TEMPLATE_V2_LIMITS,
  validateTemplateContentV2,
  type ArticleParameterPresetValueV2,
  type LayerV2,
  type LogicalContactV2,
  type NumericExpressionV2,
  type RepeatDomainV2,
  type TemplateAssetV2,
  type TemplateContentV2,
  type TemplateNodeV2,
  type TemplateParameterV2,
  type TemplateV2Diagnostic,
  type TemplateViewV2,
  type ViewContactPointV2,
  type BundlePortV2,
} from "./template-model-v2";

export type TemplateViewV3 = TemplateViewV2;
export type LayerV3 = LayerV2;
export type TemplateNodeV3 = TemplateNodeV2;
export type ViewContactPointV3 = ViewContactPointV2;
export type BundlePortV3 = BundlePortV2;
export type NumericExpressionV3 = NumericExpressionV2;
export type TemplateParameterV3 = TemplateParameterV2;
export type RepeatDomainV3 = RepeatDomainV2;
export type TemplateAssetV3 = TemplateAssetV2;
export type ArticleParameterValueV3 = ArticleParameterPresetValueV2;

export interface ContactTypeGroupV3 {
  id: string;
  name: string;
}

export interface LogicalContactV3 {
  id: string;
  number: string;
  name: string;
  circuitText: string | null;
  contactTypeGroupId: string | null;
}

export interface ArticleKeyV3 {
  sourceId: string;
  entityType: string;
  articleKey: string;
}

export interface ArticleContactGroupV3 {
  contactTypeGroupId: string;
  contactCount: number;
  allowedTerminalArticleKeys: ArticleKeyV3[];
}

export interface ArticleVariantV3 extends ArticleKeyV3 {
  id: string;
  parameterValues: ArticleParameterValueV3[];
  contactGroups: ArticleContactGroupV3[] | null;
}

export interface TemplateContentV3 {
  schemaVersion: 3;
  views: TemplateViewV3[];
  logicalContacts: LogicalContactV3[];
  parameters: TemplateParameterV3[];
  repeaters: RepeatDomainV3[];
  assets: TemplateAssetV3[];
  contactTypeGroups: ContactTypeGroupV3[];
  articleVariants: ArticleVariantV3[];
}

export type TemplateV3Diagnostic = TemplateV2Diagnostic;
export interface TemplateV3Validation { readonly valid: boolean; readonly diagnostics: TemplateV3Diagnostic[]; }
export interface TemplateV3Upgrade { readonly content: TemplateContentV3; readonly diagnostics: TemplateV3Diagnostic[]; }

export interface ArticleContactCountRuleV3 {
  readonly fixedContactCount: number;
  readonly repeatStrides: readonly number[];
}

export const TEMPLATE_V3_LIMITS = Object.freeze({
  ...TEMPLATE_V2_LIMITS,
  contactTypeGroups: 128,
  articleVariants: 500,
  terminalArticlesPerGroup: 256,
});

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ROOT_KEYS = ["schemaVersion", "views", "logicalContacts", "parameters", "repeaters", "assets", "contactTypeGroups", "articleVariants"] as const;
const hasOwn = (value: Record<string, unknown>, key: string) => Object.prototype.hasOwnProperty.call(value, key);
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);

function exact(value: unknown, keys: readonly string[], path: string, diagnostics: TemplateV3Diagnostic[]): value is Record<string, unknown> {
  if (!isRecord(value)) { diagnostics.push({ code: "object_required", path, message: "Ожидается объект." }); return false; }
  const expected = new Set(keys), actual = Object.keys(value);
  for (const key of actual) if (!expected.has(key)) diagnostics.push({ code: "unexpected_key", path: `${path}.${key}`, message: "Неизвестное поле." });
  for (const key of keys) if (!hasOwn(value, key)) diagnostics.push({ code: "missing_key", path: `${path}.${key}`, message: "Обязательное поле отсутствует." });
  return actual.length === keys.length && actual.every(key => expected.has(key));
}

function uuid(value: unknown, path: string, diagnostics: TemplateV3Diagnostic[]): value is string {
  if (typeof value === "string" && UUID.test(value)) return true;
  diagnostics.push({ code: "invalid_uuid", path, message: "Требуется UUID." });
  return false;
}

function text(value: unknown, path: string, diagnostics: TemplateV3Diagnostic[], maximum: number): value is string {
  if (typeof value === "string" && value.trim() && value.length <= maximum && !/[\u0000-\u001f\u007f-\u009f]/.test(value)) return true;
  diagnostics.push({ code: "invalid_text", path, message: `Нужен непустой текст не длиннее ${maximum} символов.` });
  return false;
}

function optionalText(value: unknown, path: string, diagnostics: TemplateV3Diagnostic[], maximum: number): void {
  if (value === null) return;
  if (typeof value !== "string" || value.length > maximum || /[\u0000-\u001f\u007f-\u009f]/.test(value))
    diagnostics.push({ code: "invalid_optional_text", path, message: `Допустимы null или текст не длиннее ${maximum} символов.` });
}

function compositeKey(value: ArticleKeyV3): string {
  return `${value.sourceId}\0${value.entityType}\0${value.articleKey}`;
}

function russianContactCount(value: number): string {
  const mod100 = Math.abs(value) % 100, mod10 = mod100 % 10;
  const word = mod100 >= 11 && mod100 <= 14 ? "контактов"
    : mod10 === 1 ? "контакт"
      : mod10 >= 2 && mod10 <= 4 ? "контакта"
        : "контактов";
  return `${value} ${word}`;
}

function contactCountIssueMessage(
  groupName: string,
  requested: number,
  fixed: number,
  strides: readonly number[],
): string | null {
  if (strides.length === 0) return requested === fixed ? null
    : `Для группы «${groupName}» задано ${russianContactCount(requested)}, но шаблон содержит ${russianContactCount(fixed)} и не имеет повторяемого сегмента. Создайте прототип контакта и один домен повтора этой группы либо укажите ${fixed}.`;
  if (strides.length > 1)
    return `Для группы «${groupName}» найдено несколько доменов повтора. Оставьте один домен, чтобы количество контактов определялось однозначно.`;
  const repeatedCount = requested - fixed, stride = strides[0]!;
  return repeatedCount > 0 && repeatedCount % stride === 0 && repeatedCount / stride <= 1_000
    ? null
    : `Для группы «${groupName}» задано ${russianContactCount(requested)}. Шаблон формирует ${russianContactCount(fixed)} и повторяемый сегмент по ${stride}; итог должен иметь вид ${fixed} + ${stride} × N, где N — целое от 1 до 1000.`;
}

/** Describes how a typed template can produce the contact total of one group. */
export function articleContactCountRuleV3(
  content: Pick<TemplateContentV3, "logicalContacts" | "repeaters">,
  groupId: string,
): ArticleContactCountRuleV3 {
  const contactsById = new Map(content.logicalContacts.map(contact => [contact.id, contact]));
  const repeatedContactIds = new Set(content.repeaters.flatMap(repeater => repeater.logicalContactIds));
  const fixedContactCount = content.logicalContacts.filter(contact =>
    contact.contactTypeGroupId === groupId && !repeatedContactIds.has(contact.id)).length;
  const repeatStrides = content.repeaters.flatMap(repeater => {
    const contacts = repeater.logicalContactIds.map(id => contactsById.get(id));
    return contacts.length > 0 && contacts.every(contact => contact?.contactTypeGroupId === groupId)
      ? [contacts.length]
      : [];
  });
  return { fixedContactCount, repeatStrides };
}

/** Returns an actionable explanation when an explicit article total has no geometry source. */
export function articleContactCountIssueV3(
  content: Pick<TemplateContentV3, "logicalContacts" | "repeaters" | "contactTypeGroups">,
  groupId: string,
  requested: number,
): string | null {
  const group = content.contactTypeGroups.find(candidate => candidate.id === groupId);
  if (!group) return null;
  const rule = articleContactCountRuleV3(content, groupId);
  return contactCountIssueMessage(group.name, requested, rule.fixedContactCount, rule.repeatStrides);
}

function collectCoreIds(value: Record<string, unknown>): string[] {
  const result: string[] = [];
  const add = (candidate: unknown) => { if (typeof candidate === "string" && UUID.test(candidate)) result.push(candidate); };
  if (Array.isArray(value.parameters)) value.parameters.forEach(item => { if (isRecord(item)) add(item.id); });
  if (Array.isArray(value.repeaters)) value.repeaters.forEach(item => { if (isRecord(item)) add(item.id); });
  if (Array.isArray(value.assets)) value.assets.forEach(item => { if (isRecord(item)) add(item.assetId); });
  if (Array.isArray(value.logicalContacts)) value.logicalContacts.forEach(item => { if (isRecord(item)) add(item.id); });
  if (Array.isArray(value.views)) value.views.forEach(view => {
    if (!isRecord(view)) return;
    add(view.id);
    if (Array.isArray(view.contactPoints)) view.contactPoints.forEach(point => { if (isRecord(point)) add(point.id); });
    if (Array.isArray(view.bundlePorts)) view.bundlePorts.forEach(port => { if (isRecord(port)) add(port.id); });
    if (Array.isArray(view.layers)) view.layers.forEach(layer => {
      if (!isRecord(layer)) return;
      add(layer.id);
      if (Array.isArray(layer.nodes)) layer.nodes.forEach(node => { if (isRecord(node)) add(node.id); });
    });
  });
  return result;
}

function validateContactTypeGroups(
  rawGroups: unknown[],
  diagnostics: TemplateV3Diagnostic[],
  occupiedIds: Set<string>,
): Map<string, string> {
  const groups = new Map<string, string>(), normalizedNames = new Set<string>();
  rawGroups.forEach((raw, index) => {
    const path = `$.contactTypeGroups[${index}]`;
    if (!exact(raw, ["id", "name"], path, diagnostics)) return;
    if (uuid(raw.id, `${path}.id`, diagnostics)) {
      if (occupiedIds.has(raw.id)) diagnostics.push({ code: "duplicate_id", path: `${path}.id`, message: "ID уже используется." });
      else { occupiedIds.add(raw.id); groups.set(raw.id, typeof raw.name === "string" ? raw.name : ""); }
    }
    if (text(raw.name, `${path}.name`, diagnostics, 128)) {
      const normalized = raw.name.trim().toLowerCase();
      if (normalizedNames.has(normalized)) diagnostics.push({ code: "duplicate_contact_type_group_name", path: `${path}.name`, message: "Название группы типов контактов повторяется." });
      else normalizedNames.add(normalized);
    }
  });
  return groups;
}

function validateLogicalContactsV3(rawContacts: unknown[], groupIds: ReadonlySet<string>, diagnostics: TemplateV3Diagnostic[]): Map<string, string | null> {
  const result = new Map<string, string | null>(), numbers = new Set<string>();
  rawContacts.forEach((raw, index) => {
    const path = `$.logicalContacts[${index}]`;
    if (!exact(raw, ["id", "number", "name", "circuitText", "contactTypeGroupId"], path, diagnostics)) return;
    const id = uuid(raw.id, `${path}.id`, diagnostics) ? raw.id : null;
    if (text(raw.number, `${path}.number`, diagnostics, 128)) {
      if (numbers.has(raw.number)) diagnostics.push({ code: "duplicate_contact_number", path: `${path}.number`, message: "Номер логического контакта повторяется." });
      else numbers.add(raw.number);
    }
    text(raw.name, `${path}.name`, diagnostics, 256);
    optionalText(raw.circuitText, `${path}.circuitText`, diagnostics, 4_096);
    let groupId: string | null = null;
    if (raw.contactTypeGroupId !== null) {
      if (uuid(raw.contactTypeGroupId, `${path}.contactTypeGroupId`, diagnostics)) {
        groupId = raw.contactTypeGroupId;
        if (!groupIds.has(groupId)) diagnostics.push({ code: "missing_contact_type_group", path: `${path}.contactTypeGroupId`, message: "Группа типа контакта не найдена." });
      }
    }
    if (id) result.set(id, groupId);
  });
  return result;
}

function validateArticleKey(raw: unknown, path: string, diagnostics: TemplateV3Diagnostic[]): ArticleKeyV3 | null {
  if (!exact(raw, ["sourceId", "entityType", "articleKey"], path, diagnostics)) return null;
  const sourceId = raw.sourceId, entityType = raw.entityType, articleKey = raw.articleKey;
  const sourceOk = text(sourceId, `${path}.sourceId`, diagnostics, 128);
  const entityOk = text(entityType, `${path}.entityType`, diagnostics, 64);
  const articleOk = text(articleKey, `${path}.articleKey`, diagnostics, 512);
  return sourceOk && entityOk && articleOk
    ? { sourceId, entityType, articleKey }
    : null;
}

function repeatedContactModel(
  repeaters: unknown[],
  contactGroups: ReadonlyMap<string, string | null>,
  diagnostics: TemplateV3Diagnostic[],
): { readonly repeatedContactIds: Set<string>; readonly repeatCountParameterIds: Set<string>; readonly stridesByGroup: Map<string, number[]> } {
  const repeatedContactIds = new Set<string>(), repeatCountParameterIds = new Set<string>(), stridesByGroup = new Map<string, number[]>();
  repeaters.forEach((raw, domainIndex) => {
    if (!isRecord(raw) || typeof raw.countParameterId !== "string" || !Array.isArray(raw.logicalContactIds)) return;
    if (raw.logicalContactIds.length === 0) return; // Pure graphical repeat, no electrical count constraint.
    repeatCountParameterIds.add(raw.countParameterId);
    let groupId: string | null = null, homogeneous = true, stride = 0;
    raw.logicalContactIds.forEach((candidate, contactIndex) => {
      if (typeof candidate !== "string") { homogeneous = false; return; }
      repeatedContactIds.add(candidate);
      const candidateGroup = contactGroups.get(candidate);
      if (candidateGroup === undefined) { homogeneous = false; return; }
      if (candidateGroup === null) {
        homogeneous = false;
        diagnostics.push({
          code: "repeat_contact_group_missing",
          path: `$.repeaters[${domainIndex}].logicalContactIds[${contactIndex}]`,
          message: "Каждый контакт повтора должен принадлежать группе типов.",
        });
        return;
      }
      if (groupId === null) groupId = candidateGroup;
      else if (groupId !== candidateGroup) {
        homogeneous = false;
        diagnostics.push({
          code: "mixed_repeat_contact_groups",
          path: `$.repeaters[${domainIndex}].logicalContactIds`,
          message: "Один повтор не может содержать контакты разных групп типов.",
        });
      }
      stride += 1;
    });
    if (raw.logicalContactIds.length === 0) {
      homogeneous = false;
      diagnostics.push({
        code: "repeat_contact_group_missing",
        path: `$.repeaters[${domainIndex}].logicalContactIds`,
        message: "Повтор должен содержать хотя бы один контакт с группой типов.",
      });
    }
    if (homogeneous && groupId && stride > 0) {
      const strides = stridesByGroup.get(groupId) ?? [];
      strides.push(stride);
      stridesByGroup.set(groupId, strides);
    }
  });
  return { repeatedContactIds, repeatCountParameterIds, stridesByGroup };
}

function validateArticleVariants(
  rawVariants: unknown[],
  rawRepeaters: unknown[],
  contactGroups: ReadonlyMap<string, string | null>,
  validGroups: ReadonlyMap<string, string>,
  diagnostics: TemplateV3Diagnostic[],
  occupiedIds: Set<string>,
  validateMaterializedCounts: boolean,
): void {
  const identities = new Set<string>();
  const validGroupIds = new Set(validGroups.keys());
  const repeated = repeatedContactModel(rawRepeaters, contactGroups, diagnostics);
  const fixedCounts = new Map<string, number>([...validGroupIds].map(id => [id, 0]));
  for (const [contactId, groupId] of contactGroups) if (groupId && !repeated.repeatedContactIds.has(contactId)) fixedCounts.set(groupId, (fixedCounts.get(groupId) ?? 0) + 1);
  const fixedUngroupedCount = [...contactGroups].filter(([contactId, groupId]) => groupId === null && !repeated.repeatedContactIds.has(contactId)).length;

  rawVariants.forEach((raw, index) => {
    const path = `$.articleVariants[${index}]`;
    if (!exact(raw, ["id", "sourceId", "entityType", "articleKey", "parameterValues", "contactGroups"], path, diagnostics)) return;
    if (uuid(raw.id, `${path}.id`, diagnostics)) {
      if (occupiedIds.has(raw.id)) diagnostics.push({ code: "duplicate_id", path: `${path}.id`, message: "ID уже используется." });
      else occupiedIds.add(raw.id);
    }
    const sourceId = raw.sourceId, entityType = raw.entityType, articleKey = raw.articleKey;
    const sourceOk = text(sourceId, `${path}.sourceId`, diagnostics, 128);
    const entityOk = text(entityType, `${path}.entityType`, diagnostics, 64);
    const articleOk = text(articleKey, `${path}.articleKey`, diagnostics, 512);
    if (sourceOk && entityOk && articleOk) {
      const identity = compositeKey({ sourceId, entityType, articleKey });
      if (identities.has(identity)) diagnostics.push({ code: "duplicate_article_variant", path: `${path}.articleKey`, message: "Вариант с таким ключом уже существует." });
      else identities.add(identity);
    }
    if (!Array.isArray(raw.parameterValues)) diagnostics.push({ code: "array_required", path: `${path}.parameterValues`, message: "Ожидается массив." });
    if (raw.contactGroups === null) return;
    if (!Array.isArray(raw.contactGroups)) { diagnostics.push({ code: "array_required", path: `${path}.contactGroups`, message: "Ожидается массив или null." }); return; }

    if (Array.isArray(raw.parameterValues) && raw.parameterValues.some(entry => isRecord(entry) && typeof entry.parameterId === "string" && repeated.repeatCountParameterIds.has(entry.parameterId)))
      diagnostics.push({ code: "variant_repeat_count_conflict", path: `${path}.parameterValues`, message: "Явная конфигурация групп контактов не может одновременно переопределять количество повторов." });

    const configuredCounts = new Map<string, number>(), configuredCountPaths = new Map<string, string>();
    const configuredGroupIds = new Set<string>();
    raw.contactGroups.forEach((rawGroup, groupIndex) => {
      const groupPath = `${path}.contactGroups[${groupIndex}]`;
      if (!exact(rawGroup, ["contactTypeGroupId", "contactCount", "allowedTerminalArticleKeys"], groupPath, diagnostics)) return;
      let groupId: string | null = null;
      if (uuid(rawGroup.contactTypeGroupId, `${groupPath}.contactTypeGroupId`, diagnostics)) {
        groupId = rawGroup.contactTypeGroupId;
        if (!validGroupIds.has(groupId)) diagnostics.push({ code: "missing_contact_type_group", path: `${groupPath}.contactTypeGroupId`, message: "Группа типа контакта не найдена." });
        if (configuredGroupIds.has(groupId)) diagnostics.push({ code: "duplicate_variant_contact_group", path: `${groupPath}.contactTypeGroupId`, message: "Группа контактов повторяется в варианте." });
        else configuredGroupIds.add(groupId);
      }
      if (!Number.isSafeInteger(rawGroup.contactCount) || Number(rawGroup.contactCount) < 0 || Number(rawGroup.contactCount) > TEMPLATE_V2_LIMITS.contacts)
        diagnostics.push({ code: "invalid_contact_count", path: `${groupPath}.contactCount`, message: `Количество контактов должно быть целым числом от 0 до ${TEMPLATE_V2_LIMITS.contacts}.` });
      else if (groupId && !configuredCounts.has(groupId)) {
        configuredCounts.set(groupId, Number(rawGroup.contactCount));
        configuredCountPaths.set(groupId, `${groupPath}.contactCount`);
      }
      if (!Array.isArray(rawGroup.allowedTerminalArticleKeys)) { diagnostics.push({ code: "array_required", path: `${groupPath}.allowedTerminalArticleKeys`, message: "Ожидается массив." }); return; }
      if (rawGroup.allowedTerminalArticleKeys.length > TEMPLATE_V3_LIMITS.terminalArticlesPerGroup)
        diagnostics.push({ code: "limit", path: `${groupPath}.allowedTerminalArticleKeys`, message: `Превышен лимит ${TEMPLATE_V3_LIMITS.terminalArticlesPerGroup}.` });
      const terminalKeys = new Set<string>();
      rawGroup.allowedTerminalArticleKeys.forEach((terminal, terminalIndex) => {
        const terminalPath = `${groupPath}.allowedTerminalArticleKeys[${terminalIndex}]`;
        const key = validateArticleKey(terminal, terminalPath, diagnostics);
        if (!key) return;
        const identity = compositeKey(key);
        if (terminalKeys.has(identity)) diagnostics.push({ code: "duplicate_terminal_article", path: terminalPath, message: "Артикул терминала повторяется в группе." });
        else terminalKeys.add(identity);
      });
    });

    if (!validateMaterializedCounts) return;

    for (const groupId of validGroupIds) {
      const target = configuredCounts.get(groupId) ?? 0, fixed = fixedCounts.get(groupId) ?? 0;
      const strides = repeated.stridesByGroup.get(groupId) ?? [];
      const issue = contactCountIssueMessage(validGroups.get(groupId) ?? groupId, target, fixed, strides);
      if (issue) diagnostics.push({
        code: strides.length > 1 ? "ambiguous_contact_repeat" : "unproducible_contact_count",
        path: configuredCountPaths.get(groupId) ?? `${path}.contactGroups`,
        message: issue,
      });
    }
    const materializedRows = fixedUngroupedCount + [...validGroupIds]
      .reduce((sum, groupId) => sum + (configuredCounts.get(groupId) ?? 0), 0);
    if (materializedRows > TEMPLATE_V2_LIMITS.contacts) {
      diagnostics.push({
        code: "article_contact_row_budget",
        path: `${path}.contactGroups`,
        message: `Артикул не может содержать больше ${TEMPLATE_V2_LIMITS.contacts} материализованных строк контактов.`,
      });
    }
  });
}

function projectV3ToV2(value: Record<string, unknown>, groupNames: ReadonlyMap<string, string>): TemplateContentV2 | null {
  if (![value.views, value.logicalContacts, value.parameters, value.repeaters, value.assets, value.articleVariants].every(Array.isArray)) return null;
  const contacts = (value.logicalContacts as unknown[]).map(raw => {
    if (!isRecord(raw)) return raw as LogicalContactV2;
    return {
      id: raw.id,
      number: raw.number,
      name: raw.name,
      contactType: typeof raw.contactTypeGroupId === "string" ? groupNames.get(raw.contactTypeGroupId) ?? "" : "",
    } as LogicalContactV2;
  });
  const presets = (value.articleVariants as unknown[]).map(raw => {
    if (!isRecord(raw)) return raw;
    return { id: raw.id, sourceId: raw.sourceId, entityType: raw.entityType, articleKey: raw.articleKey, values: raw.parameterValues };
  });
  return {
    schemaVersion: 2,
    views: value.views as TemplateContentV2["views"],
    logicalContacts: contacts,
    parameters: value.parameters as TemplateContentV2["parameters"],
    repeaters: value.repeaters as TemplateContentV2["repeaters"],
    assets: value.assets as TemplateContentV2["assets"],
    articleParameterPresets: presets as TemplateContentV2["articleParameterPresets"],
  };
}

function mapCoreDiagnostic(diagnostic: TemplateV2Diagnostic): TemplateV3Diagnostic {
  return {
    ...diagnostic,
    path: diagnostic.path.replace("$.articleParameterPresets", "$.articleVariants").replace(".values", ".parameterValues"),
  };
}

function validateTemplateContentV3Internal(value: unknown, validateMaterializedCounts: boolean): TemplateV3Validation {
  const diagnostics: TemplateV3Diagnostic[] = [];
  if (!exact(value, ROOT_KEYS, "$", diagnostics)) return { valid: false, diagnostics };
  if (value.schemaVersion !== 3) diagnostics.push({ code: "schema_version", path: "$.schemaVersion", message: "Поддерживается только schemaVersion 3." });
  for (const key of ["views", "logicalContacts", "parameters", "repeaters", "assets", "contactTypeGroups", "articleVariants"] as const)
    if (!Array.isArray(value[key])) diagnostics.push({ code: "array_required", path: `$.${key}`, message: "Ожидается массив." });
  if (diagnostics.length) return { valid: false, diagnostics };

  const groups = value.contactTypeGroups as unknown[], contacts = value.logicalContacts as unknown[], variants = value.articleVariants as unknown[];
  if (groups.length > TEMPLATE_V3_LIMITS.contactTypeGroups) diagnostics.push({ code: "limit", path: "$.contactTypeGroups", message: `Превышен лимит ${TEMPLATE_V3_LIMITS.contactTypeGroups}.` });
  if (variants.length > TEMPLATE_V3_LIMITS.articleVariants) diagnostics.push({ code: "limit", path: "$.articleVariants", message: `Превышен лимит ${TEMPLATE_V3_LIMITS.articleVariants}.` });

  const occupiedIds = new Set(collectCoreIds(value));
  const groupNames = validateContactTypeGroups(groups, diagnostics, occupiedIds);
  const contactGroups = validateLogicalContactsV3(contacts, new Set(groupNames.keys()), diagnostics);
  validateArticleVariants(variants, value.repeaters as unknown[], contactGroups, groupNames, diagnostics, occupiedIds, validateMaterializedCounts);

  const core = projectV3ToV2(value, groupNames);
  if (core) diagnostics.push(...validateTemplateContentV2(core).diagnostics.map(mapCoreDiagnostic));
  return { valid: diagnostics.length === 0, diagnostics };
}

/** Structural command boundary; it permits temporarily stale article contact totals while the template is edited. */
export function validateTemplateContentV3Structure(value: unknown): TemplateV3Validation {
  return validateTemplateContentV3Internal(value, false);
}

/** Strict client boundary for the persisted component-template schema v3. */
export function validateTemplateContentV3(value: unknown): TemplateV3Validation {
  return validateTemplateContentV3Internal(value, true);
}
