// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  addArticleVariantsV3,
  addContactPointV3,
  addContactTypeGroupV3,
  newTemplateContentV3,
  setArticleVariantContactGroupV3,
} from "./template-commands-v3";
import { upgradeTemplateContentV3ToV4 } from "./template-model-v4";
import {
  createTemplateContentV5FromEditor,
  isTemplateContentV5,
  projectTemplateContentV5TableToV1,
  upgradeTemplateContentV3ToV5,
  upgradeTemplateContentV4ToV5,
  validateTemplateContentV5,
} from "./template-model-v5";
import { parseComponentTemplateContent, isTemplateContentV4, isTemplateContentV5 as isParsedTemplateContentV5 } from "./template-content";

function v3Fixture() {
  let content = newTemplateContentV3();
  let groupId: string;
  [content, groupId] = addContactTypeGroupV3(content, "Сигнальные");
  [content] = addContactPointV3(content, content.views[0]!.id, {
    number: "1", name: "DATA", contactTypeGroupId: groupId,
  });
  content = addArticleVariantsV3(content, [
    { sourceId: "technology-connectors", entityType: "connector", articleKey: "PHR-01" },
    { sourceId: "technology-connectors", entityType: "connector", articleKey: "PHR-02" },
  ]);
  content = setArticleVariantContactGroupV3(content, content.articleVariants[0]!.id, groupId, 1, [
    { sourceId: "technology-terminals", entityType: "terminal", articleKey: "SPH-002T-P0.5S" },
  ]);
  content = setArticleVariantContactGroupV3(content, content.articleVariants[1]!.id, groupId, 1, [
    { sourceId: "technology-terminals", entityType: "terminal", articleKey: "SPH-002T-P0.5S" },
    { sourceId: "technology-terminals", entityType: "terminal", articleKey: "SPH-004T-P0.5S" },
  ]);
  return { content, groupId };
}

describe("template content v5 contract", () => {
  it("persists compatible seals and rejects a row selection outside the series list", () => {
    const content = upgradeTemplateContentV3ToV5(v3Fixture().content).content;
    const seals = [{ article: "7165-0100", color: "Красный", section: "0,35–0,5", insulationDiameter: "1,4–1,8 мм" }];
    const saved = { ...content, compatibleSeals: seals };
    expect(validateTemplateContentV5(JSON.parse(JSON.stringify(saved))).valid).toBe(true);
    const broken = structuredClone(saved);
    broken.e4ConnectorTable.seriesDefaults[0]!.values.customValues = { seal: "NOT-COMPATIBLE" };
    expect(validateTemplateContentV5(broken).diagnostics).toContainEqual(expect.objectContaining({ code: "incompatible_seal" }));
  });

  it("migrates v3 terminal compatibility into one deterministic series list", () => {
    const source = v3Fixture().content;
    const before = structuredClone(source);
    const result = upgradeTemplateContentV3ToV5(source).content;

    expect(source).toEqual(before);
    expect(result.compatibleTerminalArticleKeys.map(item => item.articleKey))
      .toEqual(["SPH-002T-P0.5S", "SPH-004T-P0.5S"]);
    expect(result.articleVariants.flatMap(variant => variant.contactGroups ?? []))
      .toEqual(expect.arrayContaining([expect.not.objectContaining({ allowedTerminalArticleKeys: expect.anything() })]));
    expect(result.e4ConnectorTable.modelVersion).toBe(2);
    expect(result.e4ConnectorTable.articles.flatMap(article => article.contactGroups))
      .toEqual(expect.arrayContaining([expect.not.objectContaining({ allowedTerminalArticleKeys: expect.anything() })]));
    expect(isTemplateContentV5(result)).toBe(true);
  });

  it("preserves v4 row defaults and overrides while removing duplicated terminal arrays", () => {
    const v4 = upgradeTemplateContentV3ToV4(v3Fixture().content).content;
    const selected = v4.articleVariants[0]!.contactGroups![0]!.allowedTerminalArticleKeys[0]!;
    v4.e4ConnectorTable.articles[0]!.rows[0]!.overrides.standardTerminalArticleKey = { ...selected };

    const result = upgradeTemplateContentV4ToV5(v4).content;

    expect(result.e4ConnectorTable.articles[0]!.rows[0]!.overrides.standardTerminalArticleKey).toEqual(selected);
    expect(validateTemplateContentV5(result)).toEqual({ valid: true, diagnostics: [] });
  });

  it("persists several compatible terminals for one type with only one standard", () => {
    const { content, groupId } = v3Fixture();
    const table = upgradeTemplateContentV3ToV4(content).content.e4ConnectorTable;
    const terminals = content.articleVariants[1]!.contactGroups![0]!.allowedTerminalArticleKeys;
    const result = createTemplateContentV5FromEditor(content, table, terminals, terminals.map((terminal, index) => ({
      terminalArticleKey: terminal,
      contactTypeGroupId: groupId,
      standard: index === 1,
    }))).content;

    expect(result.terminalContactTypeBindings).toEqual([
      expect.objectContaining({ contactTypeGroupId: groupId, standard: false }),
      expect.objectContaining({ contactTypeGroupId: groupId, standard: true }),
    ]);
    expect(projectTemplateContentV5TableToV1(result).articles[0]!.contactGroups[0]!.allowedTerminalArticleKeys)
      .toHaveLength(2);
    expect(validateTemplateContentV5(result)).toEqual({ valid: true, diagnostics: [] });
  });

  it("keeps old v5 without terminal bindings readable", () => {
    const result = upgradeTemplateContentV3ToV5(v3Fixture().content).content;
    delete (result as unknown as { terminalContactTypeBindings?: unknown }).terminalContactTypeBindings;

    expect(validateTemplateContentV5(result)).toEqual({ valid: true, diagnostics: [] });
    expect(projectTemplateContentV5TableToV1(result).articles[0]!.contactGroups[0]!.allowedTerminalArticleKeys)
      .toHaveLength(result.compatibleTerminalArticleKeys.length);
  });

  it("materializes missing v3 article configuration as a v5 contactGroups array", () => {
    const source = v3Fixture().content;
    source.articleVariants[0]!.contactGroups = null;

    const result = upgradeTemplateContentV3ToV5(source).content;

    expect(result.articleVariants[0]!.contactGroups).toEqual(
      result.e4ConnectorTable.articles[0]!.contactGroups,
    );
    expect(Array.isArray(result.articleVariants[0]!.contactGroups)).toBe(true);
    expect(validateTemplateContentV5(result)).toEqual({ valid: true, diagnostics: [] });
  });

  it("serializes a newly added editor article with array contactGroups", () => {
    const source = v3Fixture().content;
    source.articleVariants[0]!.contactGroups = null;
    const table = upgradeTemplateContentV3ToV4(source).content.e4ConnectorTable;

    const result = createTemplateContentV5FromEditor(source, table, []).content;

    expect(result.articleVariants[0]!.contactGroups).toEqual(
      result.e4ConnectorTable.articles[0]!.contactGroups,
    );
    expect(validateTemplateContentV5(JSON.parse(JSON.stringify(result)))).toEqual({ valid: true, diagnostics: [] });
  });

  it("rejects null article contactGroups at the client boundary", () => {
    const content = upgradeTemplateContentV3ToV5(v3Fixture().content).content;
    const broken = structuredClone(content) as unknown as Record<string, any>;
    broken.articleVariants[0].contactGroups = null;

    expect(validateTemplateContentV5(broken).diagnostics).toContainEqual(expect.objectContaining({
      code: "array_required",
      path: "$.articleVariants[0].contactGroups",
    }));
  });

  it("rejects legacy per-article arrays, duplicates and a selected terminal outside the series list", () => {
    const content = upgradeTemplateContentV3ToV5(v3Fixture().content).content;
    const broken = structuredClone(content) as unknown as Record<string, any>;
    broken.articleVariants[0].contactGroups[0].allowedTerminalArticleKeys = [];
    broken.compatibleTerminalArticleKeys.push({ ...broken.compatibleTerminalArticleKeys[0] });
    broken.e4ConnectorTable.articles[0].rows[0].overrides.standardTerminalArticleKey = {
      sourceId: "technology-terminals", entityType: "terminal", articleKey: "OTHER",
    };

    const diagnostics = validateTemplateContentV5(broken).diagnostics;
    expect(diagnostics).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: "unexpected_key", path: expect.stringContaining("allowedTerminalArticleKeys") }),
      expect.objectContaining({ code: "duplicate_terminal_article" }),
      expect.objectContaining({ code: "incompatible_standard_terminal" }),
    ]));
  });

  it("requires table modelVersion 2 and count-only table contact groups", () => {
    const content = upgradeTemplateContentV3ToV5(v3Fixture().content).content;
    const broken = structuredClone(content) as unknown as Record<string, any>;
    broken.e4ConnectorTable.modelVersion = 1;
    broken.e4ConnectorTable.articles[0].contactGroups[0].allowedTerminalArticleKeys = [];

    const diagnostics = validateTemplateContentV5(broken).diagnostics;
    expect(diagnostics).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: "model_version" }),
      expect.objectContaining({ code: "unexpected_key", path: expect.stringContaining("e4ConnectorTable.articles") }),
    ]));
  });

  it("keeps strict v4 readable beside v5", () => {
    const v4 = upgradeTemplateContentV3ToV4(v3Fixture().content).content;
    const v5 = upgradeTemplateContentV4ToV5(v4).content;

    expect(isTemplateContentV4(parseComponentTemplateContent(v4))).toBe(true);
    expect(isParsedTemplateContentV5(parseComponentTemplateContent(v5))).toBe(true);
  });
});
