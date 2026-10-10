import { describe, expect, it } from "vitest";
import {
  ARTICLE_PATTERN_EXPANSION_LIMIT,
  ArticlePatternExpansionError,
  expandArticlePattern,
} from "./article-pattern-expansion";

function expansionError(action: () => unknown): ArticlePatternExpansionError {
  try {
    action();
  } catch (error) {
    expect(error).toBeInstanceOf(ArticlePatternExpansionError);
    return error as ArticlePatternExpansionError;
  }
  throw new Error("Expected article pattern expansion to fail.");
}

describe("article pattern expansion", () => {
  it("expands comma-separated values and inclusive ranges in input order", () => {
    const articles = expandArticlePattern("PHR-XX", "1-14, 16, 18-20");

    expect(articles).toHaveLength(18);
    expect(articles.slice(0, 4)).toEqual(["PHR-1", "PHR-2", "PHR-3", "PHR-4"]);
    expect(articles.slice(-5)).toEqual(["PHR-14", "PHR-16", "PHR-18", "PHR-19", "PHR-20"]);
  });

  it("preserves explicitly requested zero padding for ranges and single values", () => {
    expect(expandArticlePattern("PHR-XX", "01-14").slice(0, 3)).toEqual(["PHR-01", "PHR-02", "PHR-03"]);
    expect(expandArticlePattern("A-XX-Z", "007, 09-11")).toEqual(["A-007-Z", "A-09-Z", "A-10-Z", "A-11-Z"]);
  });

  it("uses a run of at least two X characters without treating the X in XH as a variable", () => {
    expect(expandArticlePattern("XH-PHR-XXX", "2")).toEqual(["XH-PHR-2"]);
  });

  it.each([
    ["PHR", "1", "missing_placeholder"],
    ["XX-PHR-XX", "1", "multiple_placeholders"],
    ["PHR-XX", "", "empty_variables"],
    ["PHR-XX", "1,,2", "invalid_variable"],
    ["PHR-XX", "one", "invalid_variable"],
    ["PHR-XX", "5-2", "reversed_range"],
    ["PHR-XX", "1-3, 3", "duplicate_article"],
    ["PHR-XX", "9007199254740992", "invalid_variable"],
  ] as const)("rejects invalid input %s / %s with %s", (pattern, variables, code) => {
    expect(expansionError(() => expandArticlePattern(pattern, variables)).code).toBe(code);
  });

  it("reports the offending list item for actionable validation", () => {
    expect(expansionError(() => expandArticlePattern("PHR-XX", "1-3, 9-4"))).toMatchObject({
      code: "reversed_range",
      itemIndex: 1,
      item: "9-4",
    });
  });

  it("allows the application limit and rejects a larger expansion before materializing it", () => {
    expect(expandArticlePattern("A-XX", `1-${ARTICLE_PATTERN_EXPANSION_LIMIT}`)).toHaveLength(ARTICLE_PATTERN_EXPANSION_LIMIT);
    expect(expansionError(() => expandArticlePattern("A-XX", `1-${ARTICLE_PATTERN_EXPANSION_LIMIT + 1}`)).code)
      .toBe("too_many_articles");
  });

  it("supports a lower caller-supplied limit and validates the limit itself", () => {
    expect(expansionError(() => expandArticlePattern("A-XX", "1-4", { maximumArticleCount: 3 })).code)
      .toBe("too_many_articles");
    expect(expansionError(() => expandArticlePattern("A-XX", "1", { maximumArticleCount: 0 })).code)
      .toBe("invalid_limit");
    expect(expansionError(() => expandArticlePattern("A-XX", "1", { maximumArticleCount: ARTICLE_PATTERN_EXPANSION_LIMIT + 1 })).code)
      .toBe("invalid_limit");
  });

  it("rejects generated article keys longer than the catalog boundary", () => {
    const pattern = `${"A".repeat(510)}XX`;
    expect(expansionError(() => expandArticlePattern(pattern, "100")).code).toBe("article_too_long");
  });

  it("returns an immutable result", () => {
    expect(Object.isFrozen(expandArticlePattern("A-XX", "1,2"))).toBe(true);
  });
});
