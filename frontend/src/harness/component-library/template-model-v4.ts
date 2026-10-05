import {
  createE4ConnectorSeriesTableFromV3,
  validateE4ConnectorSeriesTable,
  type E4ConnectorSeriesTable,
  type E4ConnectorSeriesTableDiagnostic,
} from "./e4-connector-series-table";
import {
  validateTemplateContentV3Structure,
  type ArticleKeyV3,
  type TemplateContentV3,
  type TemplateV3Diagnostic,
} from "./template-model-v3";

export interface TemplateContentV4 extends Omit<TemplateContentV3, "schemaVersion"> {
  readonly schemaVersion: 4;
  readonly e4ConnectorTable: E4ConnectorSeriesTable;
}

export interface TemplateV4Validation {
  readonly valid: boolean;
  readonly diagnostics: readonly (TemplateV3Diagnostic | E4ConnectorSeriesTableDiagnostic)[];
}

export interface TemplateV4Upgrade {
  readonly content: TemplateContentV4;
  readonly diagnostics: readonly (TemplateV3Diagnostic | E4ConnectorSeriesTableDiagnostic)[];
}

const ROOT_KEYS = [
  "schemaVersion", "views", "logicalContacts", "parameters", "repeaters", "assets",
  "contactTypeGroups", "articleVariants", "e4ConnectorTable",
] as const;
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const articleIdentity = (value: ArticleKeyV3): string =>
  `${value.sourceId}\0${value.entityType}\0${value.articleKey}`;

function error(code: string, path: string, message: string): E4ConnectorSeriesTableDiagnostic {
  return { code, path, message, severity: "error" };
}

function projectV3Core(value: Record<string, unknown>): unknown {
  return {
    schemaVersion: 3,
    views: value.views,
    logicalContacts: value.logicalContacts,
    parameters: value.parameters,
    repeaters: value.repeaters,
    assets: value.assets,
    contactTypeGroups: value.contactTypeGroups,
    articleVariants: value.articleVariants,
  };
}

function sameArticleKeys(left: readonly ArticleKeyV3[], right: readonly ArticleKeyV3[]): boolean {
  if (left.length !== right.length) return false;
  const rightKeys = new Set(right.map(articleIdentity));
  return left.every(item => rightKeys.has(articleIdentity(item)));
}

/**
 * Ensures that duplicated series metadata has one unambiguous value in v4.
 * Table rows may contain v4-only per-article edits, while series groups,
 * article identities, counts and terminal compatibility must mirror the v3
 * compatibility core exactly.
 */
function crossValidateV3AndTable(
  core: TemplateContentV3,
  table: E4ConnectorSeriesTable,
  independentE4: boolean,
): E4ConnectorSeriesTableDiagnostic[] {
  const diagnostics: E4ConnectorSeriesTableDiagnostic[] = [];
  const coreGroups = new Map(core.contactTypeGroups.map(group => [group.id, group]));
  const tableGroups = new Map(table.contactTypeGroups.map(group => [group.id, group]));
  if (coreGroups.size !== tableGroups.size)
    diagnostics.push(error("e4_group_set_mismatch", "$.e4ConnectorTable.contactTypeGroups", "Типы контактов таблицы Э4 не совпадают с типами серии."));
  for (const [id, group] of coreGroups) {
    const tableGroup = tableGroups.get(id);
    if (!tableGroup || tableGroup.name !== group.name)
      diagnostics.push(error("e4_group_mismatch", "$.e4ConnectorTable.contactTypeGroups", `Тип контакта «${group.name}» не синхронизирован с серией.`));
  }

  let expected: E4ConnectorSeriesTable;
  try {
    expected = independentE4 ? {
      ...table,
      articles: core.articleVariants.map(variant => ({
        articleVariantId: variant.id, sourceId: variant.sourceId, entityType: variant.entityType,
        articleKey: variant.articleKey, rows: [],
        contactGroups: core.contactTypeGroups.map(group => variant.contactGroups?.find(config => config.contactTypeGroupId === group.id)
          ?? { contactTypeGroupId: group.id, contactCount: 0, allowedTerminalArticleKeys: [] }),
      })),
    } : createE4ConnectorSeriesTableFromV3(core);
  } catch (caught) {
    diagnostics.push(error(
      "e4_core_materialization_failed",
      "$.articleVariants",
      caught instanceof Error ? caught.message : "Не удалось подготовить эталон таблицы Э4.",
    ));
    return diagnostics;
  }
  const expectedArticles = new Map(expected.articles.map(article => [article.articleVariantId, article]));
  const actualArticles = new Map(table.articles.map(article => [article.articleVariantId, article]));
  if (expectedArticles.size !== actualArticles.size)
    diagnostics.push(error("e4_article_set_mismatch", "$.e4ConnectorTable.articles", "Набор артикулов таблицы Э4 не совпадает с серией."));

  for (const [variantId, expectedArticle] of expectedArticles) {
    const actual = actualArticles.get(variantId);
    const path = `$.e4ConnectorTable.articles[articleVariantId=${variantId}]`;
    if (!actual) {
      diagnostics.push(error("e4_article_missing", path, `В таблице Э4 отсутствует артикул «${expectedArticle.articleKey}».`));
      continue;
    }
    if (articleIdentity(actual) !== articleIdentity(expectedArticle))
      diagnostics.push(error("e4_article_identity_mismatch", path, `Реквизиты артикула «${expectedArticle.articleKey}» не синхронизированы с серией.`));
    const expectedConfigs = new Map(expectedArticle.contactGroups.map(group => [group.contactTypeGroupId, group]));
    const actualConfigs = new Map(actual.contactGroups.map(group => [group.contactTypeGroupId, group]));
    if (expectedConfigs.size !== actualConfigs.size)
      diagnostics.push(error("e4_article_groups_mismatch", `${path}.contactGroups`, "Набор типов контактов артикула не синхронизирован с серией."));
    for (const [groupId, expectedConfig] of expectedConfigs) {
      const actualConfig = actualConfigs.get(groupId);
      if (!actualConfig || actualConfig.contactCount !== expectedConfig.contactCount ||
          !sameArticleKeys(actualConfig.allowedTerminalArticleKeys, expectedConfig.allowedTerminalArticleKeys)) {
        diagnostics.push(error(
          "e4_article_group_mismatch",
          `${path}.contactGroups`,
          `Количество или совместимые терминалы типа «${coreGroups.get(groupId)?.name ?? groupId}» не синхронизированы с серией.`,
        ));
      }
    }
  }
  return diagnostics;
}

export function validateTemplateContentV4(value: unknown, independentE4 = false): TemplateV4Validation {
  if (!isRecord(value)) return {
    valid: false,
    diagnostics: [error("object_required", "$", "Содержимое шаблона v4 должно быть объектом.")],
  };
  const diagnostics: (TemplateV3Diagnostic | E4ConnectorSeriesTableDiagnostic)[] = [];
  const expectedKeys = new Set<string>(ROOT_KEYS);
  for (const key of Object.keys(value))
    if (!expectedKeys.has(key)) diagnostics.push(error("unexpected_key", `$.${key}`, "Неизвестное поле."));
  for (const key of ROOT_KEYS)
    if (!Object.prototype.hasOwnProperty.call(value, key)) diagnostics.push(error("missing_key", `$.${key}`, "Обязательное поле отсутствует."));
  if (value.schemaVersion !== 4)
    diagnostics.push(error("schema_version", "$.schemaVersion", "Поддерживается schemaVersion 4."));

  const coreValidation = validateTemplateContentV3Structure(projectV3Core(value));
  diagnostics.push(...coreValidation.diagnostics);
  const tableValidation = validateE4ConnectorSeriesTable(value.e4ConnectorTable);
  diagnostics.push(...tableValidation.diagnostics.map(item => ({
    ...item,
    path: item.path.replace(/^\$/, "$.e4ConnectorTable"),
  })));
  if (coreValidation.valid && tableValidation.valid)
    diagnostics.push(...crossValidateV3AndTable(projectV3Core(value) as TemplateContentV3, value.e4ConnectorTable as E4ConnectorSeriesTable, independentE4));
  return {
    valid: diagnostics.every(item => "severity" in item ? item.severity !== "error" : false),
    diagnostics,
  };
}

export function isTemplateContentV4(value: unknown): value is TemplateContentV4 {
  return isRecord(value) && value.schemaVersion === 4 && validateTemplateContentV4(value).valid;
}

export function upgradeTemplateContentV3ToV4(content: TemplateContentV3): TemplateV4Upgrade {
  const validation = validateTemplateContentV3Structure(content);
  if (!validation.valid) throw new Error(validation.diagnostics[0]?.message ?? "Некорректный шаблон v3.");
  const result: TemplateContentV4 = {
    ...structuredClone(content),
    schemaVersion: 4,
    e4ConnectorTable: createE4ConnectorSeriesTableFromV3(content),
  };
  const upgradedValidation = validateTemplateContentV4(result);
  if (!upgradedValidation.valid)
    throw new Error(upgradedValidation.diagnostics[0]?.message ?? "Не удалось подготовить шаблон v4.");
  return { content: result, diagnostics: upgradedValidation.diagnostics };
}
