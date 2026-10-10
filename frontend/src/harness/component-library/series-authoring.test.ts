import { describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { addSeriesArticles, applySeriesTerminalsToEditor, reconcileE4ConnectorTable } from "./ComponentLibrary";
import { addContactTypeGroupV3, deleteContactTypeGroupV3, newTemplateContentV3 } from "./template-commands-v3";
import { applyE4ConnectorRowEdit, materializeE4ConnectorArticle } from "./e4-connector-series-table";
import { createTemplateContentV5FromEditor, projectTemplateContentV5TableToV1 } from "./template-model-v5";
import { E4ArticlePreview } from "./E4ArticlePreview";
import { TemplateSeriesPanelV3 } from "./TemplateSeriesPanelV3";
import { createConnectorInstanceFromComponentTemplateV3 } from "../editor/component-template-placement";

const terminal = (articleKey: string) => ({ sourceId: "technology-terminals", entityType: "terminal", articleKey });
function fixture() {
  const content = addSeriesArticles(newTemplateContentV3(), [2, 4].map(contactCount => ({
    sourceId: "БД.СОЕД", entityType: "connector", articleKey: `JST-${contactCount}`, contactCount,
  })));
  const groupId = content.contactTypeGroups[0]!.id;
  const terminals = [terminal("T-A"), terminal("T-B")];
  const bindings = terminals.map((terminalArticleKey, index) => ({ terminalArticleKey, contactTypeGroupId: groupId, standard: index === 0 }));
  const projected = applySeriesTerminalsToEditor(content, reconcileE4ConnectorTable(content), terminals, bindings, true);
  return { ...projected, groupId, terminals, bindings };
}

describe("series authoring", () => {
  it("changes series standard without overwriting a manual article terminal, then persists and places it", () => {
    const f = fixture();
    const article = f.table.articles[0]!;
    const rowId = article.rows[0]!.seriesRowId;
    const edited = applyE4ConnectorRowEdit(f.table, { articleVariantId: article.articleVariantId, seriesRowId: rowId, scope: "article", changes: { standardTerminalArticleKey: null, circuitText: "MANUAL" } });
    const bindings = f.bindings.map(binding => ({ ...binding, standard: binding.terminalArticleKey.articleKey === "T-B" }));
    const next = applySeriesTerminalsToEditor(f.content, edited, f.terminals, bindings, true);
    const rows = materializeE4ConnectorArticle(next.table, article.articleVariantId).rows;
    expect(rows[0]).toMatchObject({ standardTerminalArticleKey: null, circuitText: "MANUAL" });
    expect(rows[1]!.standardTerminalArticleKey).toEqual(terminal("T-B"));
    expect(materializeE4ConnectorArticle(next.table, f.table.articles[1]!.articleVariantId).rows.every(row => row.standardTerminalArticleKey?.articleKey === "T-B")).toBe(true);
    const content = createTemplateContentV5FromEditor(next.content, next.table, f.terminals, bindings).content;
    const restored = projectTemplateContentV5TableToV1(JSON.parse(JSON.stringify(content)));
    expect(materializeE4ConnectorArticle(restored, article.articleVariantId).rows).toEqual(rows);
    const placed = createConnectorInstanceFromComponentTemplateV3({ templateId: crypto.randomUUID(), version: 2, versionSha256: "a".repeat(64), code: "JST", name: "Series", articleBindings: content.articleVariants, content, assets: [] }, { id: "test", designation: "XS1", e4Position: { x: 0, y: 0 }, articleVariantId: article.articleVariantId });
    expect(placed.contacts.map(contact => contact.terminalArticle)).toEqual(["", "T-B"]);
    expect(placed.contacts[0]!.circuit).toBe("MANUAL");
  });

  it("deletes an in-use type and leaves a valid series without its rows", () => {
    const f = fixture();
    const next = deleteContactTypeGroupV3(f.content, f.groupId);
    const table = reconcileE4ConnectorTable(next, f.table);
    expect(table.articles.every(article => article.rows.length === 0)).toBe(true);
    expect(() => createTemplateContentV5FromEditor(next, table, f.terminals, [])).not.toThrow();
  });

  it("does not multiply pattern counts by the number of contact types", () => {
    let content = newTemplateContentV3();
    [content] = addContactTypeGroupV3(content, "Сигнальные");
    [content] = addContactTypeGroupV3(content, "Силовые");
    content = addSeriesArticles(content, [{ sourceId: "db", entityType: "connector", articleKey: "X-12", contactCount: 12 }]);
    expect(reconcileE4ConnectorTable(content).articles[0]!.rows).toHaveLength(12);
  });

  it("shows assigned terminal type and standard using the shared identity", () => {
    const f = fixture();
    const identity = (key: typeof f.terminals[number]) => `${key.sourceId}\0${key.entityType}\0${key.articleKey}`;
    const markup = renderToStaticMarkup(createElement(TemplateSeriesPanelV3, {
      content: f.content, compatibleTerminalArticleKeys: f.terminals,
      terminalContactTypeGroupIds: { [identity(f.terminals[0]!)]: f.groupId },
      standardTerminalIdentities: new Set([identity(f.terminals[0]!)]),
      onSetTerminalContactTypeGroup: vi.fn(), onSetSeriesStandardTerminal: vi.fn(),
      onAddContactTypeGroup: vi.fn(), onRenameContactTypeGroup: vi.fn(), onDeleteContactTypeGroup: vi.fn(), onAddArticleVariants: vi.fn(), onDeleteArticleVariant: vi.fn(), onSetArticleContactGroup: vi.fn(), onRemoveArticleContactGroup: vi.fn(),
    }));
    expect(markup).toContain(`<option value="${f.groupId}" selected="">Сигнальные</option>`);
    expect(markup).toContain('aria-label="Стандартный терминал 1" checked=""');
  });

  it("renders editable graphic cells and scope without a duplicate table editor", () => {
    const f = fixture();
    const content = createTemplateContentV5FromEditor(f.content, f.table, f.terminals, f.bindings).content;
    const markup = renderToStaticMarkup(createElement(E4ArticlePreview, { content, table: f.table, articleId: content.articleVariants[0]!.id, code: "JST", name: "Series", assets: [], disabled: false, onChange: vi.fn(), onTableChange: vi.fn() }));
    expect(markup).toContain('aria-label="Цепь, контакт 1"');
    expect(markup).toContain('aria-label="Назначение, контакт 1"');
    expect(markup).toContain('value="T-A" selected=""');
    expect(markup).toContain('aria-label="Область применения правки"');
    expect(markup).not.toContain('class="e4-table-editor"');
  });

  it("shows the database terminal article in the library preview and preserves its selection key", () => {
    const f = fixture();
    const key = "3:JST|14:SPH-002T-P0.5S|0:|3:PHR";
    const terminals = [terminal(key)];
    const bindings = [{ terminalArticleKey: terminals[0]!, contactTypeGroupId: f.groupId, standard: true }];
    const next = applySeriesTerminalsToEditor(f.content, f.table, terminals, bindings, true);
    const content = createTemplateContentV5FromEditor(next.content, next.table, terminals, bindings).content;
    const markup = renderToStaticMarkup(createElement(E4ArticlePreview, {
      content, table: next.table, articleId: content.articleVariants[0]!.id, code: "JST", name: "Series",
      assets: [], disabled: false, onChange: vi.fn(), onTableChange: vi.fn(),
    }));
    expect(markup).toContain(`<option value="${key}" selected="">SPH-002T-P0.5S</option>`);
    expect(markup).not.toContain(`>${key}</`);
  });
});
