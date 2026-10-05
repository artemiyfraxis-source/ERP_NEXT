// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  applyE4ConnectorRowEdit,
  createE4ConnectorSeriesTableFromV3,
  materializeE4ConnectorArticle,
  setArticleContactGroupStandardTerminal,
  validateE4ConnectorSeriesTable,
} from "./e4-connector-series-table";
import {
  addArticleVariantsV3,
  addContactPointV3,
  addContactTypeGroupV3,
  newTemplateContentV3,
  setArticleVariantContactGroupV3,
} from "./template-commands-v3";
import type { TemplateContentV3 } from "./template-model-v3";

function series(): { content: TemplateContentV3; signalId: string; powerId: string } {
  let content = newTemplateContentV3();
  let signalId: string;
  [content, signalId] = addContactTypeGroupV3(content, "Сигнальные");
  let powerId: string;
  [content, powerId] = addContactTypeGroupV3(content, "Силовые");
  [content] = addContactPointV3(content, content.views[0]!.id, {
    number: "1", name: "DATA", circuitText: "CAN_H", contactTypeGroupId: signalId,
  });
  [content] = addContactPointV3(content, content.views[0]!.id, {
    number: "2", name: "POWER", circuitText: "+24V", contactTypeGroupId: powerId,
  });
  content = addArticleVariantsV3(content, [
    { sourceId: "БД.СОЕД", entityType: "connector", articleKey: "XH-02" },
    { sourceId: "БД.СОЕД", entityType: "connector", articleKey: "XH-02-L" },
  ]);
  for (const variant of content.articleVariants) {
    content = setArticleVariantContactGroupV3(content, variant.id, signalId, 1, [
      { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "SXH-001T-P0.6" },
    ]);
    content = setArticleVariantContactGroupV3(content, variant.id, powerId, 1, []);
  }
  return { content, signalId, powerId };
}

describe("E4 connector series table", () => {
  it("persists separate mark and section with article and series edit scopes", () => {
    let table = createE4ConnectorSeriesTableFromV3(series().content);
    const first = table.articles[0]!;
    const second = table.articles[1]!;
    const seriesRowId = first.rows[0]!.seriesRowId;
    table = applyE4ConnectorRowEdit(table, { articleVariantId: first.articleVariantId, seriesRowId, scope: "article", changes: { wire: "TEST", wireSection: "0,35" } });
    expect(materializeE4ConnectorArticle(table, first.articleVariantId).rows[0]).toMatchObject({ wire: "TEST", wireSection: "0,35" });
    expect(materializeE4ConnectorArticle(table, second.articleVariantId).rows[0]!.wireSection).toBeUndefined();
    table = applyE4ConnectorRowEdit(table, { articleVariantId: first.articleVariantId, seriesRowId, scope: "series", changes: { wire: "CABLE", wireSection: "3C x 0,5" } });
    table = JSON.parse(JSON.stringify(table));
    expect(validateE4ConnectorSeriesTable(table).valid).toBe(true);
    for (const article of table.articles) expect(materializeE4ConnectorArticle(table, article.articleVariantId).rows[0]).toMatchObject({ wire: "CABLE", wireSection: "3C x 0,5" });
  });
  it("creates article rows and fixed base columns without changing v3", () => {
    const fixture = series();
    const before = structuredClone(fixture.content);
    const table = createE4ConnectorSeriesTableFromV3(fixture.content);

    expect(fixture.content).toEqual(before);
    expect(table.columns.map(column => column.id)).toEqual([
      "number", "name", "circuitText", "contactTypeGroupId", "standardTerminalArticleKey",
    ]);
    expect(table.articles.map(article => article.articleKey)).toEqual(["XH-02", "XH-02-L"]);
    expect(materializeE4ConnectorArticle(table, table.articles[0]!.articleVariantId).rows).toMatchObject([
      { number: "1", name: "DATA", circuitText: "CAN_H", contactTypeGroupId: fixture.signalId },
      { number: "2", name: "POWER", circuitText: "+24V", contactTypeGroupId: fixture.powerId },
    ]);
    expect(validateE4ConnectorSeriesTable(table)).toEqual({ valid: true, diagnostics: [] });
  });

  it("keeps an article edit local and stores only an override", () => {
    const table = createE4ConnectorSeriesTableFromV3(series().content);
    const first = table.articles[0]!;
    const second = table.articles[1]!;
    const rowId = first.rows[0]!.seriesRowId;
    const edited = applyE4ConnectorRowEdit(table, {
      articleVariantId: first.articleVariantId,
      seriesRowId: rowId,
      scope: "article",
      changes: { name: "CAN High only for first article" },
    });

    expect(materializeE4ConnectorArticle(edited, first.articleVariantId).rows[0]!.name)
      .toBe("CAN High only for first article");
    expect(materializeE4ConnectorArticle(edited, second.articleVariantId).rows[0]!.name).toBe("DATA");
    expect(edited.articles[0]!.rows[0]!.overrides).toEqual({ name: "CAN High only for first article" });
    expect(table.articles[0]!.rows[0]!.overrides).toEqual({});
  });

  it("applies a row value to the whole series and clears prior overrides", () => {
    const table = createE4ConnectorSeriesTableFromV3(series().content);
    const first = table.articles[0]!;
    const second = table.articles[1]!;
    const rowId = first.rows[0]!.seriesRowId;
    const withLocal = applyE4ConnectorRowEdit(table, {
      articleVariantId: first.articleVariantId,
      seriesRowId: rowId,
      scope: "article",
      changes: { circuitText: "LOCAL" },
    });
    const forSeries = applyE4ConnectorRowEdit(withLocal, {
      articleVariantId: first.articleVariantId,
      seriesRowId: rowId,
      scope: "series",
      changes: { circuitText: "SERIES" },
    });

    expect(materializeE4ConnectorArticle(forSeries, first.articleVariantId).rows[0]!.circuitText).toBe("SERIES");
    expect(materializeE4ConnectorArticle(forSeries, second.articleVariantId).rows[0]!.circuitText).toBe("SERIES");
    expect(forSeries.articles[0]!.rows[0]!.overrides).toEqual({});
  });

  it("validates duplicate contact numbers, unknown types, counts and terminal compatibility", () => {
    const fixture = series();
    const table = createE4ConnectorSeriesTableFromV3(fixture.content);
    const article = table.articles[0]!;
    const broken = structuredClone(table);
    broken.articles[0]!.rows[1]!.overrides.number = "1";
    broken.articles[1]!.rows[1]!.overrides.contactTypeGroupId = crypto.randomUUID();
    broken.articles[0]!.rows[0]!.overrides.standardTerminalArticleKey = {
      sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "NOT-ALLOWED",
    };
    broken.articles[0]!.contactGroups.find(group => group.contactTypeGroupId === fixture.signalId)!.contactCount = -1;

    const result = validateE4ConnectorSeriesTable(broken);
    expect(result.valid).toBe(false);
    expect(result.diagnostics.map(item => item.code)).toEqual(expect.arrayContaining([
      "missing_contact_type_group",
      "invalid_contact_count",
      "duplicate_contact_number",
      "incompatible_standard_terminal",
      "contact_count_mismatch",
    ]));
    expect(() => materializeE4ConnectorArticle(table, article.articleVariantId)).not.toThrow();
  });

  it("rejects an incompatible standard terminal when applying an edit with an unknown type", () => {
    const table = createE4ConnectorSeriesTableFromV3(series().content);
    const article = table.articles[0]!;
    expect(() => applyE4ConnectorRowEdit(table, {
      articleVariantId: article.articleVariantId,
      seriesRowId: article.rows[0]!.seriesRowId,
      scope: "article",
      changes: { contactTypeGroupId: crypto.randomUUID() },
    })).toThrow("Выбранный тип контакта не найден в серии");
  });

  it("assigns a compatible standard terminal to every row of one article contact type", () => {
    const fixture = series();
    const table = createE4ConnectorSeriesTableFromV3(fixture.content);
    const first = table.articles[0]!;
    const second = table.articles[1]!;
    const terminal = first.contactGroups.find(group => group.contactTypeGroupId === fixture.signalId)!
      .allowedTerminalArticleKeys[0]!;

    const edited = setArticleContactGroupStandardTerminal(
      table, first.articleVariantId, fixture.signalId, terminal,
    );

    expect(materializeE4ConnectorArticle(edited, first.articleVariantId).rows[0]!.standardTerminalArticleKey)
      .toEqual(terminal);
    expect(materializeE4ConnectorArticle(edited, second.articleVariantId).rows[0]!.standardTerminalArticleKey)
      .toBeNull();
    expect(validateE4ConnectorSeriesTable(edited).valid).toBe(true);
    expect(table.articles[0]!.rows[0]!.overrides).toEqual({});
  });

  it("rejects a group-wide terminal that is not compatible with the selected article", () => {
    const fixture = series();
    const table = createE4ConnectorSeriesTableFromV3(fixture.content);
    expect(() => setArticleContactGroupStandardTerminal(
      table,
      table.articles[0]!.articleVariantId,
      fixture.signalId,
      { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "OTHER" },
    )).toThrow("отсутствует в списке допустимых");
  });
});
