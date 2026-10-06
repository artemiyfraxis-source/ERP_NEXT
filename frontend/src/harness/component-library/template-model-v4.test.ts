// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  addArticleVariantsV3,
  addContactPointV3,
  addContactTypeGroupV3,
  newTemplateContentV3,
  setArticleVariantContactGroupV3,
} from "./template-commands-v3";
import {
  isTemplateContentV4,
  upgradeTemplateContentV3ToV4,
  validateTemplateContentV4,
} from "./template-model-v4";

function v4Fixture() {
  let content = newTemplateContentV3();
  let signalId: string;
  [content, signalId] = addContactTypeGroupV3(content, "Сигнальные");
  [content] = addContactPointV3(content, content.views[0]!.id, {
    number: "1", name: "DATA", contactTypeGroupId: signalId,
  });
  content = addArticleVariantsV3(content, [{
    sourceId: "БД.СОЕД", entityType: "connector", articleKey: "PHR-01",
  }]);
  content = setArticleVariantContactGroupV3(
    content,
    content.articleVariants[0]!.id,
    signalId,
    1,
    [{ sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "SPH-002T-P0.5S" }],
  );
  return upgradeTemplateContentV3ToV4(content).content;
}

describe("template content v4 contract", () => {
  it("produces a strict, readable persisted document without changing v3 compatibility data", () => {
    const content = v4Fixture();
    expect(isTemplateContentV4(content)).toBe(true);
    expect(validateTemplateContentV4(content)).toEqual({ valid: true, diagnostics: [] });
    expect(content.articleVariants[0]!.articleKey).toBe("PHR-01");
    expect(content.e4ConnectorTable.articles[0]!.articleVariantId).toBe(content.articleVariants[0]!.id);
  });

  it("rejects an article identity changed only in the E4 table", () => {
    const content = v4Fixture();
    const broken = {
      ...content,
      e4ConnectorTable: {
        ...content.e4ConnectorTable,
        articles: content.e4ConnectorTable.articles.map((article, index) =>
          index === 0 ? { ...article, articleKey: "PHR-99" } : article),
      },
    };
    const validation = validateTemplateContentV4(broken);
    expect(validation.valid).toBe(false);
    expect(validation.diagnostics).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: "e4_article_identity_mismatch" }),
    ]));
  });

  it("rejects compatible terminals changed only in the E4 table", () => {
    const content = v4Fixture();
    const broken = {
      ...content,
      e4ConnectorTable: {
        ...content.e4ConnectorTable,
        articles: content.e4ConnectorTable.articles.map((article, index) => index === 0 ? {
          ...article,
          contactGroups: article.contactGroups.map((group, groupIndex) => groupIndex === 0 ? {
            ...group,
            allowedTerminalArticleKeys: [{
              sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "OTHER",
            }],
          } : group),
        } : article),
      },
    };
    const validation = validateTemplateContentV4(broken);
    expect(validation.valid).toBe(false);
    expect(validation.diagnostics).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: "e4_article_group_mismatch" }),
    ]));
  });
});
