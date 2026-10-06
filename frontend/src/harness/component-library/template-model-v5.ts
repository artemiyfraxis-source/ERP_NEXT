import { type ArticleDrawing, type DrawingContactBinding } from "./drawing-bindings";
import { withDrawingArticleCounts } from "./drawing-array-commands";
import { materializeGenerator, validateDrawingGenerators, type DrawingGenerator } from "./drawing-generator";
import { parseConnectorSchematic, type ConnectorSchematicPresentation } from "../editor/model";
import {
  createE4ConnectorSeriesTableFromV3,
  projectE4ConnectorSeriesTableV2ToV1,
  upgradeE4ConnectorSeriesTableV1ToV2,
  type E4ConnectorSeriesTableV2,
} from "./e4-connector-series-table";
import {
  validateTemplateContentV3Structure,
  type ArticleKeyV3,
  type ArticleParameterValueV3,
  type TemplateContentV3,
  type TemplateV3Diagnostic,
} from "./template-model-v3";
import {
  validateTemplateContentV4,
  type TemplateContentV4,
} from "./template-model-v4";

export interface ArticleContactGroupV5 {
  readonly contactTypeGroupId: string;
  readonly contactCount: number;
}

export interface ArticleVariantV5 extends ArticleKeyV3 {
  readonly id: string;
  readonly parameterValues: ArticleParameterValueV3[];
  readonly contactGroups: ArticleContactGroupV5[];
}

/** Series-level terminal compatibility. One contact type may accept many terminals. */
export interface TerminalContactTypeBindingV5 {
  readonly terminalArticleKey: ArticleKeyV3;
  readonly contactTypeGroupId: string;
  readonly standard: boolean;
}

export interface TemplateContentV5 extends Omit<TemplateContentV3, "schemaVersion" | "articleVariants"> {
  readonly drawingGenerators?: DrawingGenerator[];
  readonly articleDrawings?: ArticleDrawing[];
  readonly drawingContactBindings?: DrawingContactBinding[];
  readonly e4Presentation?: ConnectorSchematicPresentation;
  readonly schemaVersion: 5;
  readonly compatibleTerminalArticleKeys: ArticleKeyV3[];
  readonly terminalContactTypeBindings?: TerminalContactTypeBindingV5[];
  readonly articleVariants: ArticleVariantV5[];
  readonly e4ConnectorTable: E4ConnectorSeriesTableV2;
}

export interface TemplateV5Validation {
  readonly valid: boolean;
  readonly diagnostics: readonly TemplateV3Diagnostic[];
}

export interface TemplateV5Upgrade {
  readonly content: TemplateContentV5;
  readonly diagnostics: readonly TemplateV3Diagnostic[];
}

const ROOT_KEYS = [
  "schemaVersion", "views", "logicalContacts", "parameters", "repeaters", "assets",
  "contactTypeGroups", "compatibleTerminalArticleKeys", "articleVariants", "e4ConnectorTable",
] as const;
const OPTIONAL_ROOT_KEYS = ["terminalContactTypeBindings", "e4Presentation", "articleDrawings", "drawingContactBindings", "drawingGenerators"] as const;
const ARTICLE_GROUP_KEYS = ["contactTypeGroupId", "contactCount"] as const;
const TABLE_KEYS = ["modelVersion", "columns", "contactTypeGroups", "seriesDefaults", "articles"] as const;
const TABLE_ARTICLE_KEYS = ["articleVariantId", "sourceId", "entityType", "articleKey", "contactGroups", "rows"] as const;
const ARTICLE_KEY_KEYS = ["sourceId", "entityType", "articleKey"] as const;
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/;
const TERMINAL_LIMIT = 256;
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const hasOwn = (value: object, key: PropertyKey) => Object.prototype.hasOwnProperty.call(value, key);
const identity = (value: ArticleKeyV3): string => `${value.sourceId}\0${value.entityType}\0${value.articleKey}`;

function error(code: string, path: string, message: string): TemplateV3Diagnostic {
  return { code, path, message };
}

function exact(
  value: unknown,
  keys: readonly string[],
  path: string,
  diagnostics: TemplateV3Diagnostic[],
): value is Record<string, unknown> {
  if (!isRecord(value)) {
    diagnostics.push(error("object_required", path, "Ожидается объект."));
    return false;
  }
  const expected = new Set(keys);
  for (const key of Object.keys(value))
    if (!expected.has(key)) diagnostics.push(error("unexpected_key", `${path}.${key}`, "Неизвестное поле."));
  for (const key of keys)
    if (!hasOwn(value, key)) diagnostics.push(error("missing_key", `${path}.${key}`, "Обязательное поле отсутствует."));
  return Object.keys(value).length === keys.length && keys.every(key => hasOwn(value, key));
}

function exactWithOptional(
  value: unknown,
  requiredKeys: readonly string[],
  optionalKeys: readonly string[],
  path: string,
  diagnostics: TemplateV3Diagnostic[],
): value is Record<string, unknown> {
  if (!isRecord(value)) {
    diagnostics.push(error("object_required", path, "Ожидается объект."));
    return false;
  }
  const allowed = new Set([...requiredKeys, ...optionalKeys]);
  for (const key of Object.keys(value))
    if (!allowed.has(key)) diagnostics.push(error("unexpected_key", `${path}.${key}`, "Неизвестное поле."));
  for (const key of requiredKeys)
    if (!hasOwn(value, key)) diagnostics.push(error("missing_key", `${path}.${key}`, "Обязательное поле отсутствует."));
  return Object.keys(value).every(key => allowed.has(key)) && requiredKeys.every(key => hasOwn(value, key));
}

function validText(value: unknown, maximum: number): value is string {
  return typeof value === "string" && Boolean(value.trim()) && value.length <= maximum && !CONTROL_CHARACTERS.test(value);
}

function validateTerminalKeys(value: unknown, diagnostics: TemplateV3Diagnostic[]): readonly ArticleKeyV3[] | null {
  const path = "$.compatibleTerminalArticleKeys";
  if (!Array.isArray(value)) {
    diagnostics.push(error("array_required", path, "Ожидается массив."));
    return null;
  }
  if (value.length > TERMINAL_LIMIT)
    diagnostics.push(error("limit", path, `Превышен лимит ${TERMINAL_LIMIT}.`));
  const result: ArticleKeyV3[] = [], identities = new Set<string>();
  value.forEach((candidate, index) => {
    const itemPath = `${path}[${index}]`;
    if (!exact(candidate, ARTICLE_KEY_KEYS, itemPath, diagnostics)) return;
    const valid = validText(candidate.sourceId, 128) && validText(candidate.entityType, 64) && validText(candidate.articleKey, 512);
    if (!valid) {
      diagnostics.push(error("invalid_article_key", itemPath, "Источник, тип и артикул терминала должны быть заполнены."));
      return;
    }
    const key: ArticleKeyV3 = {
      sourceId: candidate.sourceId as string,
      entityType: candidate.entityType as string,
      articleKey: candidate.articleKey as string,
    };
    const keyIdentity = identity(key);
    if (identities.has(keyIdentity))
      diagnostics.push(error("duplicate_terminal_article", itemPath, "Совместимый терминал повторяется в серии."));
    else identities.add(keyIdentity);
    result.push(key);
  });
  return result;
}

function validateSelectedTerminals(
  table: unknown,
  terminals: readonly ArticleKeyV3[],
  diagnostics: TemplateV3Diagnostic[],
): void {
  if (!isRecord(table)) return;
  const compatible = new Set(terminals.map(identity));
  const validateValue = (value: unknown, path: string) => {
    if (value === null || value === undefined) return;
    if (!exact(value, ARTICLE_KEY_KEYS, path, diagnostics)) return;
    if (!validText(value.sourceId, 128) || !validText(value.entityType, 64) || !validText(value.articleKey, 512)) {
      diagnostics.push(error("invalid_article_key", path, "Источник, тип и артикул терминала должны быть заполнены."));
      return;
    }
    const key = value as unknown as ArticleKeyV3;
    if (!compatible.has(identity(key)))
      diagnostics.push(error("incompatible_standard_terminal", path, "Терминал строки отсутствует в списке совместимых терминалов серии."));
  };
  if (Array.isArray(table.seriesDefaults)) table.seriesDefaults.forEach((row, index) => {
    if (isRecord(row) && isRecord(row.values))
      validateValue(row.values.standardTerminalArticleKey, `$.e4ConnectorTable.seriesDefaults[${index}].values.standardTerminalArticleKey`);
  });
  if (Array.isArray(table.articles)) table.articles.forEach((article, articleIndex) => {
    if (!isRecord(article) || !Array.isArray(article.rows)) return;
    article.rows.forEach((row, rowIndex) => {
      if (isRecord(row) && isRecord(row.overrides) && hasOwn(row.overrides, "standardTerminalArticleKey"))
        validateValue(row.overrides.standardTerminalArticleKey, `$.e4ConnectorTable.articles[${articleIndex}].rows[${rowIndex}].overrides.standardTerminalArticleKey`);
    });
  });
}

function validateTerminalBindings(
  value: unknown,
  terminals: readonly ArticleKeyV3[],
  contactTypeGroups: unknown,
  diagnostics: TemplateV3Diagnostic[],
): void {
  if (value === undefined) return;
  const path = "$.terminalContactTypeBindings";
  if (!Array.isArray(value)) {
    diagnostics.push(error("array_required", path, "Ожидается массив."));
    return;
  }
  const compatible = new Set(terminals.map(identity));
  const groups = new Set(Array.isArray(contactTypeGroups)
    ? contactTypeGroups.filter(isRecord).map(group => group.id).filter((id): id is string => typeof id === "string")
    : []);
  const bound = new Set<string>();
  const standards = new Set<string>();
  value.forEach((candidate, index) => {
    const itemPath = `${path}[${index}]`;
    if (!exact(candidate, ["terminalArticleKey", "contactTypeGroupId", "standard"], itemPath, diagnostics)) return;
    if (!exact(candidate.terminalArticleKey, ARTICLE_KEY_KEYS, `${itemPath}.terminalArticleKey`, diagnostics)) return;
    const terminal = candidate.terminalArticleKey as unknown as ArticleKeyV3;
    const terminalIdentity = identity(terminal);
    if (!compatible.has(terminalIdentity))
      diagnostics.push(error("incompatible_terminal_binding", `${itemPath}.terminalArticleKey`, "Терминал привязки отсутствует в списке совместимых терминалов серии."));
    if (!validText(candidate.contactTypeGroupId, 128) || !groups.has(candidate.contactTypeGroupId as string))
      diagnostics.push(error("contact_type_group_not_found", `${itemPath}.contactTypeGroupId`, "Тип контакта привязки отсутствует в серии."));
    if (typeof candidate.standard !== "boolean")
      diagnostics.push(error("boolean_required", `${itemPath}.standard`, "Ожидается логическое значение."));
    if (bound.has(terminalIdentity))
      diagnostics.push(error("duplicate_terminal_binding", itemPath, "Терминал уже привязан к типу контакта."));
    else bound.add(terminalIdentity);
    if (candidate.standard === true && typeof candidate.contactTypeGroupId === "string") {
      if (standards.has(candidate.contactTypeGroupId))
        diagnostics.push(error("duplicate_standard_terminal", itemPath, "Для типа контакта можно выбрать только один стандартный терминал."));
      else standards.add(candidate.contactTypeGroupId);
    }
  });
}

function validateV5Shapes(value: Record<string, unknown>, diagnostics: TemplateV3Diagnostic[]): boolean {
  let exactShapes = exactWithOptional(value, ROOT_KEYS, OPTIONAL_ROOT_KEYS, "$", diagnostics);
  if (!Array.isArray(value.articleVariants)) {
    diagnostics.push(error("array_required", "$.articleVariants", "Ожидается массив."));
    exactShapes = false;
  } else value.articleVariants.forEach((variant, articleIndex) => {
    if (!isRecord(variant)) return;
    if (!Array.isArray(variant.contactGroups)) {
      diagnostics.push(error("array_required", `$.articleVariants[${articleIndex}].contactGroups`, "Ожидается массив."));
      exactShapes = false;
      return;
    }
    variant.contactGroups.forEach((group, groupIndex) => {
      if (!exact(group, ARTICLE_GROUP_KEYS, `$.articleVariants[${articleIndex}].contactGroups[${groupIndex}]`, diagnostics))
        exactShapes = false;
    });
  });
  if (!exact(value.e4ConnectorTable, TABLE_KEYS, "$.e4ConnectorTable", diagnostics)) return false;
  if (value.e4ConnectorTable.modelVersion !== 2) {
    diagnostics.push(error("model_version", "$.e4ConnectorTable.modelVersion", "Поддерживается modelVersion 2."));
    exactShapes = false;
  }
  if (!Array.isArray(value.e4ConnectorTable.articles)) return false;
  value.e4ConnectorTable.articles.forEach((article, articleIndex) => {
    const articlePath = `$.e4ConnectorTable.articles[${articleIndex}]`;
    if (!exact(article, TABLE_ARTICLE_KEYS, articlePath, diagnostics)) { exactShapes = false; return; }
    if (!Array.isArray(article.contactGroups)) return;
    article.contactGroups.forEach((group, groupIndex) => {
      if (!exact(group, ARTICLE_GROUP_KEYS, `${articlePath}.contactGroups[${groupIndex}]`, diagnostics)) exactShapes = false;
    });
  });
  return exactShapes;
}

/** Builds a transient v4 document so the mature geometry/table checks stay shared. */
function projectV5ToV4(value: Record<string, unknown>, terminals: readonly ArticleKeyV3[]): unknown {
  const projected = structuredClone(value);
  const bindings = Array.isArray(projected.terminalContactTypeBindings)
    ? new Map(projected.terminalContactTypeBindings.filter(isRecord).map(binding => {
      const terminal = isRecord(binding.terminalArticleKey) ? binding.terminalArticleKey as unknown as ArticleKeyV3 : null;
      return terminal && typeof binding.contactTypeGroupId === "string" ? [identity(terminal), binding.contactTypeGroupId] : ["", ""];
    }))
    : null;
  const terminalsForGroup = (groupId: unknown) => terminals.filter(terminal =>
    bindings === null || bindings.get(identity(terminal)) === groupId);
  projected.schemaVersion = 4;
  delete projected.compatibleTerminalArticleKeys;
  delete projected.terminalContactTypeBindings;
  delete projected.e4Presentation;
  delete projected.articleDrawings;
  delete projected.drawingContactBindings;
  delete projected.drawingGenerators;
  const configuredArticleIds = new Set<string>();
  if (Array.isArray(projected.articleVariants)) for (const variant of projected.articleVariants) {
    if (!isRecord(variant) || !Array.isArray(variant.contactGroups)) continue;
    if (typeof variant.id === "string") configuredArticleIds.add(variant.id);
    for (const group of variant.contactGroups)
      if (isRecord(group)) group.allowedTerminalArticleKeys = structuredClone(terminalsForGroup(group.contactTypeGroupId));
  }
  if (isRecord(projected.e4ConnectorTable)) {
    projected.e4ConnectorTable.modelVersion = 1;
    if (Array.isArray(projected.e4ConnectorTable.articles)) for (const article of projected.e4ConnectorTable.articles) {
      if (!isRecord(article) || !Array.isArray(article.contactGroups)) continue;
      for (const group of article.contactGroups)
        if (isRecord(group)) group.allowedTerminalArticleKeys = typeof article.articleVariantId === "string" && configuredArticleIds.has(article.articleVariantId)
          ? structuredClone(terminalsForGroup(group.contactTypeGroupId)) : [];
    }
  }
  return projected;
}

function validateDrawingBindings(value: Record<string, unknown>, diagnostics: TemplateV3Diagnostic[]) {
  const ids = (items: unknown, key: string) => new Set(Array.isArray(items) ? items.filter(isRecord).map(item => item[key]) : []);
  const articles = ids(value.articleVariants, "id"), contacts = ids(value.logicalContacts, "id");
  const views = Array.isArray(value.views) ? value.views.filter(isRecord) : [];
  const nodes = new Set(views.flatMap(view => Array.isArray(view.layers) ? view.layers.filter(isRecord).flatMap(layer => Array.isArray(layer.nodes) ? layer.nodes.filter(isRecord).map(node => node.id) : []) : []));
  const points = new Set(views.flatMap(view => Array.isArray(view.contactPoints) ? view.contactPoints.filter(isRecord).map(point => point.id) : []));
  const rows = ids(isRecord(value.e4ConnectorTable) ? value.e4ConnectorTable.seriesDefaults : [], "rowId");
  for (const key of ["articleDrawings", "drawingContactBindings"] as const) {
    const items = value[key]; if (items === undefined) continue;
    if (!Array.isArray(items)) { diagnostics.push(error("array_required", `$.${key}`, "Ожидается массив.")); continue; }
    const seen = new Set<unknown>(), seenRows = new Set<unknown>();
    items.forEach((item, index) => {
      const path = `$.${key}[${index}]`;
      if (key === "articleDrawings") {
        if (!exactWithOptional(item, ["articleVariantId", "nodeIds", "contactPointIds"], ["target","viewId","bundlePortIds"], path, diagnostics)) return;
        if(item.target !== undefined && !["e4","drawing","route"].includes(String(item.target))) diagnostics.push(error("invalid_drawing_target",path,"Неизвестный раздел рисунка."));
        const view=item.viewId===undefined ? undefined : views.find(v=>v.id===item.viewId);
        if((item.target!==undefined || item.viewId!==undefined) && (!view || item.target===undefined)) diagnostics.push(error("invalid_drawing_view",path,"Вид рисунка отсутствует."));
        if(item.bundlePortIds!==undefined) {
          const ports=view && Array.isArray(view.bundlePorts)?view.bundlePorts.filter(isRecord):[];
          if(!Array.isArray(item.bundlePortIds)||item.bundlePortIds.length>1||item.bundlePortIds.some(id=>!ports.some(p=>p.id===id))||item.bundlePortIds.length>0&&(item.target!=="drawing"||!Array.isArray(item.contactPointIds)||item.contactPointIds.length>0))diagnostics.push(error("invalid_bundle_drawing",path,"Один общий контакт допустим только в чертеже, без отдельных контактов."));
        }
        const identity=`${item.articleVariantId}:${item.target ?? "legacy"}`;
        if (!articles.has(item.articleVariantId) || seen.has(identity)) diagnostics.push(error("invalid_drawing_article", path, "Артикул рисунка отсутствует или повторяется."));
        seen.add(identity);
        for (const [field, allowed] of [["nodeIds", nodes], ["contactPointIds", points]] as const) {
          const values = item[field];
          if (!Array.isArray(values) || values.some(id => !allowed.has(id) || view && !(field==="nodeIds" ? (Array.isArray(view.layers) ? view.layers.filter(isRecord).flatMap(l=>Array.isArray(l.nodes)?l.nodes.filter(isRecord):[]).some(n=>n.id===id) : false) : (Array.isArray(view.contactPoints) && view.contactPoints.filter(isRecord).some(p=>p.id===id)))) || new Set(values).size !== values.length)
            diagnostics.push(error("invalid_drawing_selection", `${path}.${field}`, "Объекты рисунка отсутствуют или повторяются."));
        }
      } else {
        if (!exact(item, ["logicalContactId", "seriesRowId"], path, diagnostics)) return;
        if (!contacts.has(item.logicalContactId) || !rows.has(item.seriesRowId) || seen.has(item.logicalContactId) || seenRows.has(item.seriesRowId))
          diagnostics.push(error("invalid_drawing_contact", path, "Связь контакта со строкой отсутствует или повторяется."));
        seen.add(item.logicalContactId); seenRows.add(item.seriesRowId);
      }
    });
  }
}

export function validateTemplateContentV5(value: unknown): TemplateV5Validation {
  if (!isRecord(value)) return { valid: false, diagnostics: [error("object_required", "$", "Содержимое шаблона v5 должно быть объектом.")] };
  const diagnostics: TemplateV3Diagnostic[] = [];
  if (hasOwn(value, "e4Presentation")) {
    try { parseConnectorSchematic(value.e4Presentation); }
    catch { diagnostics.push(error("invalid_e4_presentation", "$.e4Presentation", "Некорректная преднастройка таблицы Э4.")); }
  }
  validateDrawingBindings(value, diagnostics);
  const exactShapes = validateV5Shapes(value, diagnostics);
  if (value.schemaVersion !== 5)
    diagnostics.push(error("schema_version", "$.schemaVersion", "Поддерживается schemaVersion 5."));
  const terminals = validateTerminalKeys(value.compatibleTerminalArticleKeys, diagnostics);
  if (terminals !== null) {
    validateSelectedTerminals(value.e4ConnectorTable, terminals, diagnostics);
    validateTerminalBindings(value.terminalContactTypeBindings, terminals, value.contactTypeGroups, diagnostics);
  }
  if (exactShapes && terminals !== null) {
    const projected = projectV5ToV4(value, terminals);
    const legacyValidation = validateTemplateContentV4(projected, true);
    diagnostics.push(...legacyValidation.diagnostics.filter(item =>
      !("severity" in item) || item.severity === "error").map(item => ({
      code: item.code,
      path: item.path,
      message: item.message,
    })));
  }
  if (!diagnostics.length && value.drawingGenerators !== undefined) {
    try {
      const content = value as unknown as TemplateContentV5, core = projectTemplateContentV5ToV3(content), table = projectTemplateContentV5TableToV1(content);
      validateDrawingGenerators(core, table, value.drawingGenerators);
      for (const g of content.drawingGenerators ?? []) for (const article of g.articles) materializeGenerator(core, table, content.drawingContactBindings ?? [], g, article.articleId);
    }
    catch (caught) { diagnostics.push(error("invalid_drawing_generator", "$.drawingGenerators", (caught as Error).message)); }
  }
  return { valid: diagnostics.length === 0, diagnostics };
}

export function isTemplateContentV5(value: unknown): value is TemplateContentV5 {
  return isRecord(value) && value.schemaVersion === 5 && validateTemplateContentV5(value).valid;
}

function compatibleTerminalsFromV4(content: TemplateContentV4): ArticleKeyV3[] {
  const result: ArticleKeyV3[] = [], seen = new Set<string>();
  for (const variant of content.articleVariants) for (const group of variant.contactGroups ?? []) {
    for (const terminal of group.allowedTerminalArticleKeys) {
      const key = identity(terminal);
      if (!seen.has(key)) {
        seen.add(key);
        result.push({ ...terminal });
      }
    }
  }
  return result;
}

function terminalBindingsFromV4(content: TemplateContentV4): TerminalContactTypeBindingV5[] {
  const groupByTerminal = new Map<string, string>();
  for (const variant of content.articleVariants) for (const group of variant.contactGroups ?? [])
    for (const terminal of group.allowedTerminalArticleKeys)
      if (!groupByTerminal.has(identity(terminal))) groupByTerminal.set(identity(terminal), group.contactTypeGroupId);
  const standardByGroup = new Map<string, ArticleKeyV3>();
  const defaults = new Map(content.e4ConnectorTable.seriesDefaults.map(row => [row.rowId, row.values]));
  for (const article of content.e4ConnectorTable.articles) for (const row of article.rows) {
    const values = { ...defaults.get(row.seriesRowId), ...row.overrides };
    if (values.contactTypeGroupId && values.standardTerminalArticleKey)
      standardByGroup.set(values.contactTypeGroupId, values.standardTerminalArticleKey);
  }
  return compatibleTerminalsFromV4(content).flatMap(terminal => {
    const terminalId = identity(terminal);
    const contactTypeGroupId = [...standardByGroup].find(([, standard]) => identity(standard) === terminalId)?.[0]
      ?? groupByTerminal.get(terminalId);
    return contactTypeGroupId ? [{
      terminalArticleKey: { ...terminal },
      contactTypeGroupId,
      standard: identity(standardByGroup.get(contactTypeGroupId) ?? { sourceId: "", entityType: "", articleKey: "" }) === terminalId,
    }] : [];
  });
}

export function upgradeTemplateContentV4ToV5(content: TemplateContentV4): TemplateV5Upgrade {
  const validation = validateTemplateContentV4(content);
  if (!validation.valid) throw new Error(validation.diagnostics[0]?.message ?? "Некорректный шаблон v4.");
  const compatibleTerminalArticleKeys = compatibleTerminalsFromV4(content);
  const tableGroups = new Map(content.e4ConnectorTable.articles.map(article => [article.articleVariantId, article.contactGroups]));
  const articleVariants: ArticleVariantV5[] = content.articleVariants.map(variant => ({
    id: variant.id,
    sourceId: variant.sourceId,
    entityType: variant.entityType,
    articleKey: variant.articleKey,
    parameterValues: variant.parameterValues.map(value => ({ ...value })),
    contactGroups: (tableGroups.get(variant.id) ?? []).map(group => ({
      contactTypeGroupId: group.contactTypeGroupId,
      contactCount: group.contactCount,
    })),
  }));
  const { schemaVersion: _schemaVersion, articleVariants: _articleVariants, e4ConnectorTable: oldTable, ...core } = content;
  const result: TemplateContentV5 = {
    ...structuredClone(core),
    schemaVersion: 5,
    compatibleTerminalArticleKeys,
    terminalContactTypeBindings: terminalBindingsFromV4(content),
    articleVariants,
    e4ConnectorTable: upgradeE4ConnectorSeriesTableV1ToV2(oldTable),
  };
  const upgradedValidation = validateTemplateContentV5(result);
  if (!upgradedValidation.valid)
    throw new Error(upgradedValidation.diagnostics[0]
      ? `${upgradedValidation.diagnostics[0].path}: ${upgradedValidation.diagnostics[0].message}`
      : "Не удалось подготовить шаблон v5.");
  return { content: result, diagnostics: upgradedValidation.diagnostics };
}

export function upgradeTemplateContentV3ToV5(content: TemplateContentV3): TemplateV5Upgrade {
  const validation = validateTemplateContentV3Structure(content);
  if (!validation.valid) throw new Error(validation.diagnostics[0]?.message ?? "Некорректный шаблон v3.");
  const v4: TemplateContentV4 = {
    ...structuredClone(content),
    schemaVersion: 4,
    e4ConnectorTable: createE4ConnectorSeriesTableFromV3(content),
  };
  return upgradeTemplateContentV4ToV5(v4);
}

export function createTemplateContentV5FromEditor(
  content: TemplateContentV3,
  table: TemplateContentV4["e4ConnectorTable"],
  compatibleTerminalArticleKeys: readonly ArticleKeyV3[],
  terminalContactTypeBindings: readonly TerminalContactTypeBindingV5[] | null = [],
  e4Presentation?: ConnectorSchematicPresentation,
  articleDrawings?: readonly ArticleDrawing[],
  drawingContactBindings?: readonly DrawingContactBinding[],
  drawingGenerators?: readonly DrawingGenerator[],
): TemplateV5Upgrade {
  content = withDrawingArticleCounts(content);
  const coreValidation = validateTemplateContentV3Structure(content);
  if (!coreValidation.valid) throw new Error(coreValidation.diagnostics[0]?.message ?? "Некорректный шаблон редактора.");
  const tableGroups = new Map(table.articles.map(article => [article.articleVariantId, article.contactGroups]));
  const articleVariants: ArticleVariantV5[] = content.articleVariants.map(variant => ({
    id: variant.id,
    sourceId: variant.sourceId,
    entityType: variant.entityType,
    articleKey: variant.articleKey,
    parameterValues: variant.parameterValues.map(value => ({ ...value })),
    contactGroups: (tableGroups.get(variant.id) ?? []).map(group => ({
      contactTypeGroupId: group.contactTypeGroupId,
      contactCount: group.contactCount,
    })),
  }));
  const { schemaVersion: _schemaVersion, articleVariants: _articleVariants, ...core } = content;
  const result: TemplateContentV5 = {
    ...structuredClone(core),
    schemaVersion: 5,
    compatibleTerminalArticleKeys: compatibleTerminalArticleKeys.map(item => ({ ...item })),
    ...(terminalContactTypeBindings === null ? {} : { terminalContactTypeBindings: terminalContactTypeBindings.map(binding => ({
      terminalArticleKey: { ...binding.terminalArticleKey },
      contactTypeGroupId: binding.contactTypeGroupId,
      standard: binding.standard,
    })) }),
    articleVariants,
    e4ConnectorTable: upgradeE4ConnectorSeriesTableV1ToV2(table),
    ...(articleDrawings ? {articleDrawings: structuredClone(articleDrawings) as ArticleDrawing[]} : {}),
    ...(drawingContactBindings ? {drawingContactBindings: structuredClone(drawingContactBindings) as DrawingContactBinding[]} : {}),
    ...(drawingGenerators ? {drawingGenerators: structuredClone(drawingGenerators) as DrawingGenerator[]} : {}),
    ...(e4Presentation ? { e4Presentation: parseConnectorSchematic(e4Presentation) } : {}),
  };
  const validation = validateTemplateContentV5(result);
  if (!validation.valid) throw new Error(validation.diagnostics[0]?.message ?? "Не удалось подготовить шаблон v5.");
  return { content: result, diagnostics: validation.diagnostics };
}

/** Projects v5 into the existing reusable v3 editor core. */
export function projectTemplateContentV5ToV3(content: TemplateContentV5): TemplateContentV3 {
  const { schemaVersion: _schemaVersion, compatibleTerminalArticleKeys, terminalContactTypeBindings: _bindings, e4ConnectorTable: _table, e4Presentation: _presentation, articleDrawings: _drawings, drawingContactBindings: _contactBindings, drawingGenerators: _generators, ...core } = content;
  return withDrawingArticleCounts({
    ...structuredClone(core),
    schemaVersion: 3,
    articleVariants: content.articleVariants.map(variant => ({
      ...structuredClone(variant),
      contactGroups: variant.contactGroups.map(group => ({
        ...group,
        allowedTerminalArticleKeys: compatibleTerminalArticleKeys.filter(terminal =>
          content.terminalContactTypeBindings === undefined || content.terminalContactTypeBindings.some(binding =>
            binding.contactTypeGroupId === group.contactTypeGroupId && identity(binding.terminalArticleKey) === identity(terminal)))
          .map(terminal => ({ ...terminal })),
      })),
    })),
  });
}

export function projectTemplateContentV5TableToV1(content: TemplateContentV5) {
  const bindings = content.terminalContactTypeBindings;
  const groupIds = bindings === undefined ? null : new Map(bindings.map(binding => [identity(binding.terminalArticleKey), binding.contactTypeGroupId]));
  return projectE4ConnectorSeriesTableV2ToV1(content.e4ConnectorTable, content.compatibleTerminalArticleKeys, groupIds);
}
