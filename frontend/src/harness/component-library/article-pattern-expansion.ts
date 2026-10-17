/** Maximum number of article variants that one bulk expansion may create. */
export const ARTICLE_PATTERN_EXPANSION_LIMIT = 500;

export type ArticlePatternExpansionErrorCode =
  | "invalid_pattern"
  | "missing_placeholder"
  | "multiple_placeholders"
  | "empty_variables"
  | "invalid_variable"
  | "reversed_range"
  | "duplicate_article"
  | "too_many_articles"
  | "article_too_long"
  | "invalid_limit";

export class ArticlePatternExpansionError extends Error {
  readonly code: ArticlePatternExpansionErrorCode;
  readonly itemIndex: number | null;
  readonly item: string | null;

  constructor(
    code: ArticlePatternExpansionErrorCode,
    message: string,
    itemIndex: number | null = null,
    item: string | null = null,
  ) {
    super(message);
    this.name = "ArticlePatternExpansionError";
    this.code = code;
    this.itemIndex = itemIndex;
    this.item = item;
  }
}

export interface ExpandArticlePatternOptions {
  /** May lower, but cannot exceed, the application-wide variant limit. */
  readonly maximumArticleCount?: number;
}

interface ParsedVariableItem {
  readonly index: number;
  readonly source: string;
  readonly start: number;
  readonly end: number;
  readonly width: number | null;
}

const PLACEHOLDER = /X{2,}/g;
const SINGLE_VALUE = /^\d+$/;
const RANGE = /^(\d+)\s*-\s*(\d+)$/;
const CONTROL_CHARACTER = /[\u0000-\u001f\u007f-\u009f]/;
const ARTICLE_KEY_MAXIMUM_LENGTH = 512;

function fail(
  code: ArticlePatternExpansionErrorCode,
  message: string,
  itemIndex: number | null = null,
  item: string | null = null,
): never {
  throw new ArticlePatternExpansionError(code, message, itemIndex, item);
}

function safeInteger(value: string, index: number, source: string): number {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) {
    fail(
      "invalid_variable",
      `Элемент ${index + 1} содержит число за пределами безопасного диапазона.`,
      index,
      source,
    );
  }
  return parsed;
}

function paddedWidth(start: string, end: string): number | null {
  const startPadded = start.length > 1 && start.startsWith("0");
  const endPadded = end.length > 1 && end.startsWith("0");
  return startPadded || endPadded ? Math.max(start.length, end.length) : null;
}

function parseVariableItems(expression: string): readonly ParsedVariableItem[] {
  if (!expression.trim()) fail("empty_variables", "Укажите номера или диапазоны артикулов.");
  if (CONTROL_CHARACTER.test(expression))
    fail("invalid_variable", "Список номеров содержит недопустимый управляющий символ.");

  return expression.split(",").map((raw, index) => {
    const source = raw.trim();
    if (!source)
      fail("invalid_variable", `Элемент ${index + 1} списка пуст.`, index, source);

    if (SINGLE_VALUE.test(source)) {
      const value = safeInteger(source, index, source);
      return { index, source, start: value, end: value, width: source.length > 1 && source.startsWith("0") ? source.length : null };
    }

    const range = RANGE.exec(source);
    if (!range)
      fail(
        "invalid_variable",
        `Элемент «${source}» должен быть числом или диапазоном вида 1-14.`,
        index,
        source,
      );
    const startText = range[1]!;
    const endText = range[2]!;
    const start = safeInteger(startText, index, source);
    const end = safeInteger(endText, index, source);
    if (end < start)
      fail(
        "reversed_range",
        `В диапазоне «${source}» конечное значение меньше начального.`,
        index,
        source,
      );
    return { index, source, start, end, width: paddedWidth(startText, endText) };
  });
}

function requestedLimit(options: ExpandArticlePatternOptions): number {
  const limit = options.maximumArticleCount ?? ARTICLE_PATTERN_EXPANSION_LIMIT;
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > ARTICLE_PATTERN_EXPANSION_LIMIT) {
    fail(
      "invalid_limit",
      `Лимит должен быть целым числом от 1 до ${ARTICLE_PATTERN_EXPANSION_LIMIT}.`,
    );
  }
  return limit;
}

/**
 * Expands one `XX` placeholder in a series pattern using comma-separated
 * values and inclusive ranges. Padding is controlled by the variable text:
 * `1-14` produces `1`…`14`, while `01-14` produces `01`…`14`.
 */
export function expandArticlePattern(
  rawPattern: string,
  variableExpression: string,
  options: ExpandArticlePatternOptions = {},
): readonly string[] {
  const pattern = rawPattern.trim();
  if (!pattern || pattern.length > ARTICLE_KEY_MAXIMUM_LENGTH || CONTROL_CHARACTER.test(pattern))
    fail("invalid_pattern", "Шаблон артикула должен быть непустым текстом не длиннее 512 символов.");

  const placeholders = [...pattern.matchAll(PLACEHOLDER)];
  if (placeholders.length === 0)
    fail("missing_placeholder", "Добавьте в шаблон одну переменную из двух или более букв X, например PHR-XX.");
  if (placeholders.length > 1)
    fail("multiple_placeholders", "В шаблоне допустима только одна переменная XX.");

  const placeholder = placeholders[0]!;
  const placeholderIndex = placeholder.index!;
  const placeholderText = placeholder[0];
  const prefix = pattern.slice(0, placeholderIndex);
  const suffix = pattern.slice(placeholderIndex + placeholderText.length);
  const limit = requestedLimit(options);
  const items = parseVariableItems(variableExpression);
  let requestedCount = 0;
  for (const item of items) {
    requestedCount += item.end - item.start + 1;
    if (!Number.isSafeInteger(requestedCount) || requestedCount > limit)
      fail(
        "too_many_articles",
        `По шаблону получится больше ${limit} артикулов. Разделите список на несколько операций.`,
        item.index,
        item.source,
      );
  }

  const articles: string[] = [];
  const seen = new Set<string>();
  for (const item of items) {
    for (let value = item.start; value <= item.end; value += 1) {
      const variable = item.width === null ? String(value) : String(value).padStart(item.width, "0");
      const article = `${prefix}${variable}${suffix}`;
      if (article.length > ARTICLE_KEY_MAXIMUM_LENGTH)
        fail(
          "article_too_long",
          `Артикул «${article}» длиннее 512 символов.`,
          item.index,
          item.source,
        );
      if (seen.has(article))
        fail(
          "duplicate_article",
          `Артикул «${article}» создаётся больше одного раза.`,
          item.index,
          item.source,
        );
      seen.add(article);
      articles.push(article);
    }
  }
  return Object.freeze(articles);
}
