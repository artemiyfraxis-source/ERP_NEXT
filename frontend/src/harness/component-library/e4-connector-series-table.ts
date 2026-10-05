import { materializeArticleContactRowsV3 } from "./template-article-contact-rows-v3";
import {
  validateTemplateContentV3Structure,
  type ArticleKeyV3,
  type ContactTypeGroupV3,
  type TemplateContentV3,
} from "./template-model-v3";

export type E4ConnectorBaseColumnId =
  | "number"
  | "name"
  | "circuitText"
  | "contactTypeGroupId"
  | "standardTerminalArticleKey";

export interface E4ConnectorTableColumn {
  readonly id: E4ConnectorBaseColumnId;
  readonly label: string;
  readonly visible: boolean;
}

export interface E4ConnectorRowValues {
  wire?: string;
  wireSection?: string;
  color?: string;
  secondaryColor?: string;
  customValues?: Record<string, string>;
  number: string;
  name: string;
  circuitText: string | null;
  contactTypeGroupId: string | null;
  standardTerminalArticleKey: ArticleKeyV3 | null;
}

export type E4ConnectorRowOverride = Partial<E4ConnectorRowValues>;

/** A row shared by articles that contain the same contact-type ordinal. */
export interface E4ConnectorSeriesRowDefault {
  readonly rowId: string;
  readonly values: E4ConnectorRowValues;
}

export interface E4ConnectorArticleRow {
  readonly seriesRowId: string;
  readonly overrides: E4ConnectorRowOverride;
}

export interface E4ConnectorArticleContactGroup {
  readonly contactTypeGroupId: string;
  contactCount: number;
  readonly allowedTerminalArticleKeys: readonly ArticleKeyV3[];
}

/** Schema-v5 article summary. Terminal compatibility belongs to the series. */
export interface E4ConnectorArticleContactGroupV2 {
  readonly contactTypeGroupId: string;
  contactCount: number;
}

export interface E4ConnectorArticleTable {
  readonly articleVariantId: string;
  readonly sourceId: string;
  readonly entityType: string;
  readonly articleKey: string;
  readonly contactGroups: readonly E4ConnectorArticleContactGroup[];
  readonly rows: readonly E4ConnectorArticleRow[];
}

/**
 * Domain model of the fixed E4 connector table.
 *
 * It deliberately lives next to schema v3 instead of inside it. This lets the
 * editor introduce the table workflow without changing persisted v3 documents
 * or interpreting the existing E4 canvas as table geometry.
 */
export interface E4ConnectorSeriesTable {
  readonly modelVersion: 1;
  readonly columns: readonly E4ConnectorTableColumn[];
  readonly contactTypeGroups: readonly ContactTypeGroupV3[];
  readonly seriesDefaults: readonly E4ConnectorSeriesRowDefault[];
  readonly articles: readonly E4ConnectorArticleTable[];
}

export interface E4ConnectorArticleTableV2 extends Omit<E4ConnectorArticleTable, "contactGroups"> {
  readonly contactGroups: readonly E4ConnectorArticleContactGroupV2[];
}

/** E4 table persisted by component-template schema v5. */
export interface E4ConnectorSeriesTableV2 extends Omit<E4ConnectorSeriesTable, "modelVersion" | "articles"> {
  readonly modelVersion: 2;
  readonly articles: readonly E4ConnectorArticleTableV2[];
}

export interface MaterializedE4ConnectorArticleRow extends E4ConnectorRowValues {
  readonly seriesRowId: string;
}

export interface MaterializedE4ConnectorArticle {
  readonly articleVariantId: string;
  readonly sourceId: string;
  readonly entityType: string;
  readonly articleKey: string;
  readonly contactGroups: readonly E4ConnectorArticleContactGroup[];
  readonly rows: readonly MaterializedE4ConnectorArticleRow[];
}

export type E4ConnectorRowEditScope = "article" | "series";

export interface E4ConnectorRowEdit {
  readonly articleVariantId: string;
  readonly seriesRowId: string;
  readonly scope: E4ConnectorRowEditScope;
  readonly changes: E4ConnectorRowOverride;
}

export interface E4ConnectorSeriesTableDiagnostic {
  readonly code: string;
  readonly path: string;
  readonly message: string;
  readonly severity: "error" | "warning";
}

export interface E4ConnectorSeriesTableValidation {
  readonly valid: boolean;
  readonly diagnostics: readonly E4ConnectorSeriesTableDiagnostic[];
}

/** Removes the terminal arrays duplicated by the v4 table without touching row edits. */
export function upgradeE4ConnectorSeriesTableV1ToV2(
  table: E4ConnectorSeriesTable,
): E4ConnectorSeriesTableV2 {
  return {
    modelVersion: 2,
    columns: table.columns.map(column => ({ ...column })),
    contactTypeGroups: table.contactTypeGroups.map(group => ({ ...group })),
    seriesDefaults: table.seriesDefaults.map(row => ({ rowId: row.rowId, values: cloneRowValues(row.values) })),
    articles: table.articles.map(article => ({
      articleVariantId: article.articleVariantId,
      sourceId: article.sourceId,
      entityType: article.entityType,
      articleKey: article.articleKey,
      contactGroups: article.contactGroups.map(group => ({
        contactTypeGroupId: group.contactTypeGroupId,
        contactCount: group.contactCount,
      })),
      rows: article.rows.map(row => ({ seriesRowId: row.seriesRowId, overrides: cloneOverride(row.overrides) })),
    })),
  };
}

/** Builds the legacy in-memory editor projection for a persisted v5 table. */
export function projectE4ConnectorSeriesTableV2ToV1(
  table: E4ConnectorSeriesTableV2,
  compatibleTerminalArticleKeys: readonly ArticleKeyV3[],
  terminalContactTypeGroupIds: ReadonlyMap<string, string> | null = null,
): E4ConnectorSeriesTable {
  return {
    modelVersion: 1,
    columns: table.columns.map(column => ({ ...column })),
    contactTypeGroups: table.contactTypeGroups.map(group => ({ ...group })),
    seriesDefaults: table.seriesDefaults.map(row => ({ rowId: row.rowId, values: cloneRowValues(row.values) })),
    articles: table.articles.map(article => ({
      articleVariantId: article.articleVariantId,
      sourceId: article.sourceId,
      entityType: article.entityType,
      articleKey: article.articleKey,
      contactGroups: article.contactGroups.map(group => ({
        contactTypeGroupId: group.contactTypeGroupId,
        contactCount: group.contactCount,
        allowedTerminalArticleKeys: compatibleTerminalArticleKeys
          .filter(terminal => terminalContactTypeGroupIds === null || terminalContactTypeGroupIds.get(articleIdentity(terminal)) === group.contactTypeGroupId)
          .map(cloneArticleKey),
      })),
      rows: article.rows.map(row => ({ seriesRowId: row.seriesRowId, overrides: cloneOverride(row.overrides) })),
    })),
  };
}

export class E4ConnectorSeriesTableError extends Error {
  constructor(readonly code: string, message: string) {
    super(message);
    this.name = "E4ConnectorSeriesTableError";
  }
}

export const E4_CONNECTOR_BASE_COLUMNS: readonly E4ConnectorTableColumn[] = Object.freeze([
  Object.freeze({ id: "number", label: "№", visible: true }),
  Object.freeze({ id: "name", label: "Назначение", visible: true }),
  Object.freeze({ id: "circuitText", label: "Цепь", visible: true }),
  Object.freeze({ id: "contactTypeGroupId", label: "Тип", visible: true }),
  Object.freeze({ id: "standardTerminalArticleKey", label: "Стандартный контакт", visible: true }),
]);

const COLUMN_IDS = new Set<E4ConnectorBaseColumnId>(E4_CONNECTOR_BASE_COLUMNS.map(column => column.id));
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/;
const hasOwn = (value: object, key: PropertyKey) => Object.prototype.hasOwnProperty.call(value, key);
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const articleIdentity = (value: ArticleKeyV3) => `${value.sourceId}\0${value.entityType}\0${value.articleKey}`;

function cloneArticleKey(value: ArticleKeyV3): ArticleKeyV3 {
  return { sourceId: value.sourceId, entityType: value.entityType, articleKey: value.articleKey };
}

function cloneRowValues(value: E4ConnectorRowValues): E4ConnectorRowValues {
  return {
    ...value,
    standardTerminalArticleKey: value.standardTerminalArticleKey === null
      ? null
      : cloneArticleKey(value.standardTerminalArticleKey),
  };
}

function cloneOverride(value: E4ConnectorRowOverride): E4ConnectorRowOverride {
  return {
    ...value,
    ...(hasOwn(value, "standardTerminalArticleKey") ? {
      standardTerminalArticleKey: value.standardTerminalArticleKey === null || value.standardTerminalArticleKey === undefined
        ? value.standardTerminalArticleKey
        : cloneArticleKey(value.standardTerminalArticleKey),
    } : {}),
  };
}

function sameArticleKey(left: ArticleKeyV3 | null, right: ArticleKeyV3 | null): boolean {
  return left === right || (left !== null && right !== null && articleIdentity(left) === articleIdentity(right));
}

function sameValue<K extends keyof E4ConnectorRowValues>(
  key: K,
  left: E4ConnectorRowValues[K],
  right: E4ConnectorRowValues[K],
): boolean {
  return key === "standardTerminalArticleKey"
    ? sameArticleKey(left as ArticleKeyV3 | null, right as ArticleKeyV3 | null)
    : left === right;
}

function materializedValues(
  defaults: E4ConnectorRowValues,
  overrides: E4ConnectorRowOverride,
): E4ConnectorRowValues {
  return cloneRowValues({ ...defaults, ...overrides });
}

function rowIdForOrdinal(contactTypeGroupId: string | null, ordinal: number): string {
  return `contact-group:${contactTypeGroupId ?? "unassigned"}:${ordinal}`;
}

function requireArticle(table: E4ConnectorSeriesTable, articleVariantId: string): E4ConnectorArticleTable {
  const article = table.articles.find(candidate => candidate.articleVariantId === articleVariantId);
  if (!article) throw new E4ConnectorSeriesTableError("article_not_found", "Артикул серии не найден.");
  return article;
}

function requireSeriesRow(table: E4ConnectorSeriesTable, seriesRowId: string): E4ConnectorSeriesRowDefault {
  const row = table.seriesDefaults.find(candidate => candidate.rowId === seriesRowId);
  if (!row) throw new E4ConnectorSeriesTableError("row_not_found", "Строка таблицы Э4 не найдена.");
  return row;
}

function normalizedText(value: string, maximum: number, field: string): string {
  const result = value.trim();
  if (!result || result.length > maximum || CONTROL_CHARACTERS.test(result))
    throw new E4ConnectorSeriesTableError("invalid_row_value", `${field} должно быть непустым текстом не длиннее ${maximum} символов.`);
  return result;
}

function normalizedOptionalText(value: string | null, maximum: number, field: string): string | null {
  if (value === null || value.trim() === "") return null;
  if (value.length > maximum || CONTROL_CHARACTERS.test(value))
    throw new E4ConnectorSeriesTableError("invalid_row_value", `${field} должно быть текстом не длиннее ${maximum} символов.`);
  return value.trim();
}

function normalizedArticleKey(value: ArticleKeyV3 | null): ArticleKeyV3 | null {
  if (value === null) return null;
  return {
    sourceId: normalizedText(value.sourceId, 128, "Источник контакта"),
    entityType: normalizedText(value.entityType, 64, "Тип контакта"),
    articleKey: normalizedText(value.articleKey, 512, "Артикул контакта"),
  };
}

function normalizedChanges(
  table: E4ConnectorSeriesTable,
  changes: E4ConnectorRowOverride,
): E4ConnectorRowOverride {
  const result: E4ConnectorRowOverride = {};
  for (const key of ["wire", "wireSection", "color", "secondaryColor"] as const)
    if (hasOwn(changes, key)) result[key] = normalizedOptionalText(changes[key] ?? "", 512, key) ?? "";
  if (changes.customValues) result.customValues = Object.fromEntries(Object.entries(changes.customValues).map(([key, text]) =>
    [normalizedText(key, 128, "Поле"), normalizedOptionalText(text, 4096, "Значение") ?? ""]));
  if (hasOwn(changes, "number")) result.number = normalizedText(changes.number ?? "", 128, "Номер контакта");
  if (hasOwn(changes, "name")) result.name = normalizedText(changes.name ?? "", 256, "Назначение контакта");
  if (hasOwn(changes, "circuitText")) result.circuitText = normalizedOptionalText(changes.circuitText ?? null, 4_096, "Цепь");
  if (hasOwn(changes, "contactTypeGroupId")) {
    const groupId = changes.contactTypeGroupId ?? null;
    if (groupId !== null && !table.contactTypeGroups.some(group => group.id === groupId))
      throw new E4ConnectorSeriesTableError("contact_type_group_not_found", "Выбранный тип контакта не найден в серии.");
    result.contactTypeGroupId = groupId;
  }
  if (hasOwn(changes, "standardTerminalArticleKey"))
    result.standardTerminalArticleKey = normalizedArticleKey(changes.standardTerminalArticleKey ?? null);
  return result;
}

function compactOverride(
  defaults: E4ConnectorRowValues,
  override: E4ConnectorRowOverride,
): E4ConnectorRowOverride {
  const result: E4ConnectorRowOverride = {};
  for (const key of Object.keys(override) as (keyof E4ConnectorRowValues)[]) {
    const value = override[key];
    if (value !== undefined && !sameValue(key, value as never, defaults[key] as never))
      Object.assign(result, { [key]: value });
  }
  return cloneOverride(result);
}

function recountArticle(
  seriesDefaults: readonly E4ConnectorSeriesRowDefault[],
  article: E4ConnectorArticleTable,
  groups: readonly ContactTypeGroupV3[],
): E4ConnectorArticleTable {
  const defaultsById = new Map(seriesDefaults.map(row => [row.rowId, row.values]));
  const counts = new Map(groups.map(group => [group.id, 0]));
  for (const row of article.rows) {
    const defaults = defaultsById.get(row.seriesRowId);
    if (!defaults) continue;
    const groupId = materializedValues(defaults, row.overrides).contactTypeGroupId;
    if (groupId !== null && counts.has(groupId)) counts.set(groupId, (counts.get(groupId) ?? 0) + 1);
  }
  const previous = new Map(article.contactGroups.map(group => [group.contactTypeGroupId, group]));
  return {
    ...article,
    contactGroups: groups.map(group => ({
      contactTypeGroupId: group.id,
      contactCount: counts.get(group.id) ?? 0,
      allowedTerminalArticleKeys: (previous.get(group.id)?.allowedTerminalArticleKeys ?? []).map(cloneArticleKey),
    })),
  };
}

/** Builds the first table-model snapshot from a current, unchanged v3 document. */
export function createE4ConnectorSeriesTableFromV3(content: TemplateContentV3, independentE4 = false): E4ConnectorSeriesTable {
  const validation = validateTemplateContentV3Structure(content);
  if (!validation.valid)
    throw new E4ConnectorSeriesTableError("invalid_v3_content", validation.diagnostics[0]?.message ?? "Некорректный шаблон v3.");

  const defaults = new Map<string, E4ConnectorSeriesRowDefault>();
  const articles: E4ConnectorArticleTable[] = [];

  for (const variant of content.articleVariants) {
    let v3Rows;
    try {
      v3Rows = materializeArticleContactRowsV3(content, variant);
    } catch (caught) {
      if (independentE4 && variant.contactGroups !== null) {
        let number = 0;
        v3Rows = variant.contactGroups.flatMap(group => Array.from({ length: group.contactCount }, () => ({
          number: String(++number), name: `Контакт ${number}`, circuitText: null,
          contactTypeGroupId: group.contactTypeGroupId,
        })));
      } else {
      throw new E4ConnectorSeriesTableError(
        "article_materialization_failed",
        `Не удалось подготовить таблицу Э4 для артикула «${variant.articleKey}»: ${caught instanceof Error ? caught.message : "неизвестная ошибка"}`,
      );
      }
    }
    const ordinals = new Map<string, number>();
    const rows: E4ConnectorArticleRow[] = [];
    for (const row of v3Rows) {
      const ordinalKey = row.contactTypeGroupId ?? "unassigned";
      const ordinal = (ordinals.get(ordinalKey) ?? 0) + 1;
      ordinals.set(ordinalKey, ordinal);
      const rowId = rowIdForOrdinal(row.contactTypeGroupId, ordinal);
      const values: E4ConnectorRowValues = {
        number: row.number,
        name: row.name,
        circuitText: row.circuitText,
        contactTypeGroupId: row.contactTypeGroupId,
        standardTerminalArticleKey: null,
      };
      const seriesDefault = defaults.get(rowId);
      if (!seriesDefault) defaults.set(rowId, { rowId, values: cloneRowValues(values) });
      rows.push({
        seriesRowId: rowId,
        overrides: seriesDefault === undefined ? {} : compactOverride(seriesDefault.values, values),
      });
    }
    const materializedCounts = new Map(content.contactTypeGroups.map(group => [group.id, 0]));
    for (const row of v3Rows)
      if (row.contactTypeGroupId !== null)
        materializedCounts.set(row.contactTypeGroupId, (materializedCounts.get(row.contactTypeGroupId) ?? 0) + 1);
    const configuredGroups = new Map((variant.contactGroups ?? []).map(group => [group.contactTypeGroupId, group]));
    articles.push({
      articleVariantId: variant.id,
      sourceId: variant.sourceId,
      entityType: variant.entityType,
      articleKey: variant.articleKey,
      contactGroups: content.contactTypeGroups.map(group => ({
        contactTypeGroupId: group.id,
        contactCount: materializedCounts.get(group.id) ?? 0,
        allowedTerminalArticleKeys: (configuredGroups.get(group.id)?.allowedTerminalArticleKeys ?? []).map(cloneArticleKey),
      })),
      rows,
    });
  }

  // An empty series still needs editable defaults when it already has contacts.
  if (content.articleVariants.length === 0) {
    const ordinals = new Map<string, number>();
    for (const contact of content.logicalContacts) {
      const ordinalKey = contact.contactTypeGroupId ?? "unassigned";
      const ordinal = (ordinals.get(ordinalKey) ?? 0) + 1;
      ordinals.set(ordinalKey, ordinal);
      const rowId = rowIdForOrdinal(contact.contactTypeGroupId, ordinal);
      defaults.set(rowId, { rowId, values: {
        number: contact.number,
        name: contact.name,
        circuitText: contact.circuitText,
        contactTypeGroupId: contact.contactTypeGroupId,
        standardTerminalArticleKey: null,
      } });
    }
  }

  return {
    modelVersion: 1,
    columns: E4_CONNECTOR_BASE_COLUMNS.map(column => ({ ...column })),
    contactTypeGroups: content.contactTypeGroups.map(group => ({ ...group })),
    seriesDefaults: [...defaults.values()],
    articles,
  };
}

/** Resolves one article by merging its row overrides with series defaults. */
export function materializeE4ConnectorArticle(
  table: E4ConnectorSeriesTable,
  articleVariantId: string,
): MaterializedE4ConnectorArticle {
  const article = requireArticle(table, articleVariantId);
  const defaultsById = new Map(table.seriesDefaults.map(row => [row.rowId, row.values]));
  return {
    articleVariantId: article.articleVariantId,
    sourceId: article.sourceId,
    entityType: article.entityType,
    articleKey: article.articleKey,
    contactGroups: article.contactGroups.map(group => ({
      ...group,
      allowedTerminalArticleKeys: group.allowedTerminalArticleKeys.map(cloneArticleKey),
    })),
    rows: article.rows.map(row => {
      const defaults = defaultsById.get(row.seriesRowId);
      if (!defaults) throw new E4ConnectorSeriesTableError("row_not_found", `Не найдены общие значения строки «${row.seriesRowId}».`);
      return { seriesRowId: row.seriesRowId, ...materializedValues(defaults, row.overrides) };
    }),
  };
}

/**
 * Applies a table-cell edit immutably. Series edits clear the same fields from
 * article overrides, which makes "use for the whole series" deterministic.
 */
export function applyE4ConnectorRowEdit(
  table: E4ConnectorSeriesTable,
  edit: E4ConnectorRowEdit,
): E4ConnectorSeriesTable {
  const article = requireArticle(table, edit.articleVariantId);
  const defaults = requireSeriesRow(table, edit.seriesRowId);
  if (!article.rows.some(row => row.seriesRowId === edit.seriesRowId))
    throw new E4ConnectorSeriesTableError("article_row_not_found", "Выбранный артикул не содержит эту строку.");
  const changes = normalizedChanges(table, edit.changes);
  if (Object.keys(changes).length === 0) return table;

  if (edit.scope === "series") {
    const nextDefaults = table.seriesDefaults.map(row => row.rowId === defaults.rowId
      ? { ...row, values: materializedValues(row.values, changes) }
      : { ...row, values: cloneRowValues(row.values) });
    const changedFields = new Set(Object.keys(changes));
    const articles = table.articles.map(candidate => {
      const rows = candidate.rows.map(row => {
        if (row.seriesRowId !== defaults.rowId) return { ...row, overrides: cloneOverride(row.overrides) };
        const remaining = Object.fromEntries(Object.entries(row.overrides).filter(([key]) => !changedFields.has(key))) as E4ConnectorRowOverride;
        return { ...row, overrides: compactOverride(nextDefaults.find(item => item.rowId === row.seriesRowId)!.values, remaining) };
      });
      return recountArticle(nextDefaults, { ...candidate, rows }, table.contactTypeGroups);
    });
    return {
      ...table,
      columns: table.columns.map(column => ({ ...column })),
      contactTypeGroups: table.contactTypeGroups.map(group => ({ ...group })),
      seriesDefaults: nextDefaults,
      articles,
    };
  }

  const articles = table.articles.map(candidate => {
    if (candidate.articleVariantId !== article.articleVariantId) return candidate;
    const rows = candidate.rows.map(row => row.seriesRowId === edit.seriesRowId
      ? { ...row, overrides: compactOverride(defaults.values, { ...row.overrides, ...changes }) }
      : row);
    return recountArticle(table.seriesDefaults, { ...candidate, rows }, table.contactTypeGroups);
  });
  return { ...table, articles };
}

/**
 * Assigns one standard terminal to every row of a contact type in one article.
 * The operation is intentionally article-scoped: different connector articles
 * in a series may accept different terminals even when they share a row layout.
 */
export function setArticleContactGroupStandardTerminal(
  table: E4ConnectorSeriesTable,
  articleVariantId: string,
  contactTypeGroupId: string,
  terminal: ArticleKeyV3 | null,
): E4ConnectorSeriesTable {
  const article = requireArticle(table, articleVariantId);
  const group = article.contactGroups.find(item => item.contactTypeGroupId === contactTypeGroupId);
  if (!group)
    throw new E4ConnectorSeriesTableError("article_contact_group_not_found", "Выбранный тип контакта не найден в артикуле.");
  const normalizedTerminal = normalizedArticleKey(terminal);
  if (normalizedTerminal !== null && !group.allowedTerminalArticleKeys.some(item => sameArticleKey(item, normalizedTerminal)))
    throw new E4ConnectorSeriesTableError("incompatible_standard_terminal", "Стандартный терминал отсутствует в списке допустимых для этого типа.");

  const defaultsById = new Map(table.seriesDefaults.map(row => [row.rowId, row.values]));
  let affected = 0;
  const rows = article.rows.map(row => {
    const defaults = defaultsById.get(row.seriesRowId);
    if (!defaults)
      throw new E4ConnectorSeriesTableError("row_not_found", `Не найдены общие значения строки «${row.seriesRowId}».`);
    if (materializedValues(defaults, row.overrides).contactTypeGroupId !== contactTypeGroupId)
      return { ...row, overrides: cloneOverride(row.overrides) };
    affected += 1;
    return {
      ...row,
      overrides: compactOverride(defaults, {
        ...row.overrides,
        standardTerminalArticleKey: normalizedTerminal,
      }),
    };
  });
  if (affected === 0)
    throw new E4ConnectorSeriesTableError("article_contact_group_empty", "В выбранном артикуле нет контактов этого типа.");
  return {
    ...table,
    articles: table.articles.map(candidate => candidate.articleVariantId === articleVariantId
      ? recountArticle(table.seriesDefaults, { ...candidate, rows }, table.contactTypeGroups)
      : candidate),
  };
}

function diagnostic(
  diagnostics: E4ConnectorSeriesTableDiagnostic[],
  code: string,
  path: string,
  message: string,
  severity: "error" | "warning" = "error",
): void {
  diagnostics.push({ code, path, message, severity });
}

function validText(value: unknown, maximum: number): value is string {
  return typeof value === "string" && Boolean(value.trim()) && value.length <= maximum && !CONTROL_CHARACTERS.test(value);
}

function parseArticleKey(
  value: unknown,
  path: string,
  diagnostics: E4ConnectorSeriesTableDiagnostic[],
): ArticleKeyV3 | null {
  if (!isRecord(value)) {
    diagnostic(diagnostics, "invalid_article_key", path, "Артикул должен быть объектом.");
    return null;
  }
  if (!validText(value.sourceId, 128) || !validText(value.entityType, 64) || !validText(value.articleKey, 512)) {
    diagnostic(diagnostics, "invalid_article_key", path, "Источник, тип и артикул должны быть заполнены.");
    return null;
  }
  return { sourceId: value.sourceId, entityType: value.entityType, articleKey: value.articleKey };
}

function validateRowFields(
  value: unknown,
  path: string,
  diagnostics: E4ConnectorSeriesTableDiagnostic[],
  groupIds: ReadonlySet<string>,
  partial: boolean,
): E4ConnectorRowOverride | null {
  if (!isRecord(value)) {
    diagnostic(diagnostics, "invalid_row", path, "Значения строки должны быть объектом.");
    return null;
  }
  const allowed = new Set<keyof E4ConnectorRowValues>(["number", "name", "circuitText", "contactTypeGroupId", "standardTerminalArticleKey", "wire", "wireSection", "color", "secondaryColor", "customValues"]);
  for (const key of ["wire", "wireSection", "color", "secondaryColor"]) if (hasOwn(value, key) &&
    (typeof value[key] !== "string" || value[key].length > 512 || CONTROL_CHARACTERS.test(value[key])))
    diagnostic(diagnostics, "invalid_row_value", `${path}.${key}`, "Неверное значение поля.");
  if (hasOwn(value, "customValues") && (!isRecord(value.customValues) || Object.entries(value.customValues).some(([key, text]) =>
    !validText(key, 128) || typeof text !== "string" || text.length > 4096 || CONTROL_CHARACTERS.test(text))))
    diagnostic(diagnostics, "invalid_row_value", `${path}.customValues`, "Неверные дополнительные поля.");
  for (const key of Object.keys(value))
    if (!allowed.has(key as keyof E4ConnectorRowValues)) diagnostic(diagnostics, "unexpected_row_field", `${path}.${key}`, "Неизвестное поле строки.");
  if (!partial || hasOwn(value, "number"))
    if (!validText(value.number, 128)) diagnostic(diagnostics, "invalid_contact_number", `${path}.number`, "Заполните номер контакта.");
  if (!partial || hasOwn(value, "name"))
    if (!validText(value.name, 256)) diagnostic(diagnostics, "invalid_contact_name", `${path}.name`, "Заполните назначение контакта.");
  if (!partial || hasOwn(value, "circuitText"))
    if (value.circuitText !== null && (typeof value.circuitText !== "string" || value.circuitText.length > 4_096 || CONTROL_CHARACTERS.test(value.circuitText)))
      diagnostic(diagnostics, "invalid_circuit_text", `${path}.circuitText`, "Название цепи задано неверно.");
  if (!partial || hasOwn(value, "contactTypeGroupId")) {
    if (value.contactTypeGroupId !== null && (typeof value.contactTypeGroupId !== "string" || !groupIds.has(value.contactTypeGroupId)))
      diagnostic(diagnostics, "missing_contact_type_group", `${path}.contactTypeGroupId`, "Тип контакта не найден в серии.");
  }
  if (!partial || hasOwn(value, "standardTerminalArticleKey"))
    if (value.standardTerminalArticleKey !== null) parseArticleKey(value.standardTerminalArticleKey, `${path}.standardTerminalArticleKey`, diagnostics);
  return value as E4ConnectorRowOverride;
}

/** Validates persisted or imported table data, including its materialized articles. */
export function validateE4ConnectorSeriesTable(value: unknown): E4ConnectorSeriesTableValidation {
  const diagnostics: E4ConnectorSeriesTableDiagnostic[] = [];
  if (!isRecord(value)) return { valid: false, diagnostics: [{
    code: "object_required", path: "$", message: "Таблица Э4 должна быть объектом.", severity: "error",
  }] };
  if (value.modelVersion !== 1) diagnostic(diagnostics, "model_version", "$.modelVersion", "Поддерживается modelVersion 1.");
  for (const key of ["columns", "contactTypeGroups", "seriesDefaults", "articles"])
    if (!Array.isArray(value[key])) diagnostic(diagnostics, "array_required", `$.${key}`, "Ожидается список.");
  if (![value.columns, value.contactTypeGroups, value.seriesDefaults, value.articles].every(Array.isArray))
    return { valid: false, diagnostics };

  const columnIds = new Set<string>();
  const columns = value.columns as unknown[];
  const contactTypeGroups = value.contactTypeGroups as unknown[];
  const seriesDefaults = value.seriesDefaults as unknown[];
  const articles = value.articles as unknown[];
  columns.forEach((raw, index) => {
    const path = `$.columns[${index}]`;
    if (!isRecord(raw) || typeof raw.id !== "string" || !COLUMN_IDS.has(raw.id as E4ConnectorBaseColumnId)) {
      diagnostic(diagnostics, "invalid_column", path, "Неизвестная базовая колонка."); return;
    }
    if (columnIds.has(raw.id)) diagnostic(diagnostics, "duplicate_column", `${path}.id`, "Колонка повторяется.");
    columnIds.add(raw.id);
    if (!validText(raw.label, 128)) diagnostic(diagnostics, "invalid_column_label", `${path}.label`, "Название колонки задано неверно.");
    if (typeof raw.visible !== "boolean") diagnostic(diagnostics, "invalid_column_visibility", `${path}.visible`, "Видимость колонки задана неверно.");
  });
  for (const id of COLUMN_IDS) if (!columnIds.has(id)) diagnostic(diagnostics, "missing_column", "$.columns", `Отсутствует базовая колонка «${id}».`);

  const groupIds = new Set<string>(), groupNames = new Set<string>();
  contactTypeGroups.forEach((raw, index) => {
    const path = `$.contactTypeGroups[${index}]`;
    if (!isRecord(raw) || !validText(raw.id, 128) || !validText(raw.name, 128)) {
      diagnostic(diagnostics, "invalid_contact_type_group", path, "Тип контакта задан неверно."); return;
    }
    if (groupIds.has(raw.id)) diagnostic(diagnostics, "duplicate_contact_type_group", `${path}.id`, "Тип контакта повторяется.");
    groupIds.add(raw.id);
    const name = raw.name.trim().toLocaleLowerCase("ru");
    if (groupNames.has(name)) diagnostic(diagnostics, "duplicate_contact_type_group_name", `${path}.name`, "Название типа контакта повторяется.");
    groupNames.add(name);
  });

  const defaultsById = new Map<string, E4ConnectorRowValues>();
  seriesDefaults.forEach((raw, index) => {
    const path = `$.seriesDefaults[${index}]`;
    if (!isRecord(raw) || !validText(raw.rowId, 512)) {
      diagnostic(diagnostics, "invalid_series_row", path, "Общая строка серии задана неверно."); return;
    }
    if (defaultsById.has(raw.rowId)) diagnostic(diagnostics, "duplicate_series_row", `${path}.rowId`, "Общая строка серии повторяется.");
    const before = diagnostics.length;
    const fields = validateRowFields(raw.values, `${path}.values`, diagnostics, groupIds, false);
    if (fields && diagnostics.length === before) defaultsById.set(raw.rowId, fields as E4ConnectorRowValues);
  });

  const articleIds = new Set<string>(), articleKeys = new Set<string>();
  articles.forEach((raw, articleIndex) => {
    const path = `$.articles[${articleIndex}]`;
    if (!isRecord(raw)) { diagnostic(diagnostics, "invalid_article", path, "Артикул задан неверно."); return; }
    const key = parseArticleKey(raw, path, diagnostics);
    if (!validText(raw.articleVariantId, 128)) diagnostic(diagnostics, "invalid_article_id", `${path}.articleVariantId`, "ID артикула задан неверно.");
    else if (articleIds.has(raw.articleVariantId)) diagnostic(diagnostics, "duplicate_article_id", `${path}.articleVariantId`, "ID артикула повторяется.");
    else articleIds.add(raw.articleVariantId);
    if (key) {
      const identity = articleIdentity(key);
      if (articleKeys.has(identity)) diagnostic(diagnostics, "duplicate_article", `${path}.articleKey`, "Артикул повторяется в серии.");
      articleKeys.add(identity);
    }
    if (!Array.isArray(raw.contactGroups) || !Array.isArray(raw.rows)) {
      if (!Array.isArray(raw.contactGroups)) diagnostic(diagnostics, "array_required", `${path}.contactGroups`, "Ожидается список настроек типов.");
      if (!Array.isArray(raw.rows)) diagnostic(diagnostics, "array_required", `${path}.rows`, "Ожидается список строк.");
      return;
    }

    const configs = new Map<string, { count: number; terminals: Set<string> }>();
    raw.contactGroups.forEach((group, groupIndex) => {
      const groupPath = `${path}.contactGroups[${groupIndex}]`;
      if (!isRecord(group) || typeof group.contactTypeGroupId !== "string" || !groupIds.has(group.contactTypeGroupId)) {
        diagnostic(diagnostics, "missing_contact_type_group", `${groupPath}.contactTypeGroupId`, "Тип контакта не найден в серии."); return;
      }
      if (configs.has(group.contactTypeGroupId)) diagnostic(diagnostics, "duplicate_article_contact_group", `${groupPath}.contactTypeGroupId`, "Настройка типа повторяется в артикуле.");
      if (!Number.isSafeInteger(group.contactCount) || Number(group.contactCount) < 0 || Number(group.contactCount) > 2_000)
        diagnostic(diagnostics, "invalid_contact_count", `${groupPath}.contactCount`, "Количество контактов должно быть целым числом от 0 до 2000.");
      if (!Array.isArray(group.allowedTerminalArticleKeys)) {
        diagnostic(diagnostics, "array_required", `${groupPath}.allowedTerminalArticleKeys`, "Ожидается список допустимых контактов."); return;
      }
      const terminals = new Set<string>();
      group.allowedTerminalArticleKeys.forEach((terminal, terminalIndex) => {
        const parsed = parseArticleKey(terminal, `${groupPath}.allowedTerminalArticleKeys[${terminalIndex}]`, diagnostics);
        if (!parsed) return;
        const identity = articleIdentity(parsed);
        if (terminals.has(identity)) diagnostic(diagnostics, "duplicate_terminal_article", `${groupPath}.allowedTerminalArticleKeys[${terminalIndex}]`, "Допустимый контакт повторяется.");
        terminals.add(identity);
      });
      configs.set(group.contactTypeGroupId, { count: Number(group.contactCount), terminals });
    });

    const materializedRows: E4ConnectorRowValues[] = [], rowRefs = new Set<string>();
    raw.rows.forEach((row, rowIndex) => {
      const rowPath = `${path}.rows[${rowIndex}]`;
      if (!isRecord(row) || typeof row.seriesRowId !== "string" || !defaultsById.has(row.seriesRowId)) {
        diagnostic(diagnostics, "missing_series_row", `${rowPath}.seriesRowId`, "Общие значения строки не найдены."); return;
      }
      if (rowRefs.has(row.seriesRowId)) diagnostic(diagnostics, "duplicate_article_row", `${rowPath}.seriesRowId`, "Строка повторяется в артикуле.");
      rowRefs.add(row.seriesRowId);
      const before = diagnostics.length;
      const overrides = validateRowFields(row.overrides, `${rowPath}.overrides`, diagnostics, groupIds, true);
      if (overrides && diagnostics.length === before)
        materializedRows.push(materializedValues(defaultsById.get(row.seriesRowId)!, overrides));
    });

    const numbers = new Set<string>(), actualCounts = new Map<string, number>();
    for (const [rowIndex, row] of materializedRows.entries()) {
      const number = row.number.trim().toLocaleLowerCase("ru");
      if (numbers.has(number)) diagnostic(diagnostics, "duplicate_contact_number", `${path}.rows[${rowIndex}]`, `Номер контакта «${row.number}» повторяется.`);
      numbers.add(number);
      if (row.contactTypeGroupId === null) {
        diagnostic(diagnostics, "contact_type_unassigned", `${path}.rows[${rowIndex}]`, `Для контакта №${row.number} не выбран тип.`, "warning");
        if (row.standardTerminalArticleKey !== null)
          diagnostic(diagnostics, "terminal_without_contact_type", `${path}.rows[${rowIndex}]`, "Стандартный контакт нельзя выбрать без типа.");
        continue;
      }
      actualCounts.set(row.contactTypeGroupId, (actualCounts.get(row.contactTypeGroupId) ?? 0) + 1);
      if (row.standardTerminalArticleKey !== null) {
        const config = configs.get(row.contactTypeGroupId);
        if (!config?.terminals.has(articleIdentity(row.standardTerminalArticleKey)))
          diagnostic(diagnostics, "incompatible_standard_terminal", `${path}.rows[${rowIndex}]`, "Стандартный контакт отсутствует в списке допустимых для этого типа.");
      }
    }
    for (const groupId of groupIds) {
      const declared = configs.get(groupId)?.count ?? 0, actual = actualCounts.get(groupId) ?? 0;
      if (declared !== actual) diagnostic(diagnostics, "contact_count_mismatch", `${path}.contactGroups`, `Для типа контакта указано ${declared}, а в таблице найдено ${actual}.`);
    }
  });

  return { valid: diagnostics.every(item => item.severity !== "error"), diagnostics };
}
