import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { E4ConnectorTableEditor, setE4ConnectorColumnVisibility, updateE4ConnectorTableCell } from "./E4ConnectorTableEditor";
import { addArticleVariantsV3, addContactPointV3, addContactTypeGroupV3, newTemplateContentV3, setArticleVariantContactGroupV3 } from "./template-commands-v3";
import { createE4ConnectorSeriesTableFromV3, materializeE4ConnectorArticle } from "./e4-connector-series-table";

function tableFixture() {
  let content = newTemplateContentV3();
  let groupId: string;
  [content, groupId] = addContactTypeGroupV3(content, "Сигнальные");
  [content] = addContactPointV3(content, content.views[0]!.id, { number: "1", name: "DATA", circuitText: "CAN_H", contactTypeGroupId: groupId });
  content = addArticleVariantsV3(content, [{ sourceId: "БД.СОЕД", entityType: "connector", articleKey: "PHR-01" }]);
  content = setArticleVariantContactGroupV3(content, content.articleVariants[0]!.id, groupId, 1, [{ sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "TER-01" }]);
  return { table: createE4ConnectorSeriesTableFromV3(content), articleId: content.articleVariants[0]!.id };
}

describe("E4ConnectorTableEditor", () => {
  it("renders the fixed table, scope selector and column controls", () => {
    const fixture = tableFixture();
    const markup = renderToStaticMarkup(createElement(E4ConnectorTableEditor, {
      table: fixture.table, selectedArticleVariantId: fixture.articleId, onChange: vi.fn(),
    }));
    expect(markup).toContain("Таблица Э4");
    expect(markup).toContain("только этот артикул");
    expect(markup).toContain("всю серию");
    expect(markup).toContain("DATA");
    expect(markup).toContain("TER-01");
    expect(markup).toContain("Видимость колонок");
  });

  it("supports column visibility and scope-aware cell updates", () => {
    const fixture = tableFixture();
    const hidden = setE4ConnectorColumnVisibility(fixture.table, "circuitText", false);
    expect(hidden.columns.find(column => column.id === "circuitText")?.visible).toBe(false);
    const rowId = fixture.table.articles[0]!.rows[0]!.seriesRowId;
    const changed = updateE4ConnectorTableCell(hidden, fixture.articleId, rowId, "article", { name: "LOCAL" });
    expect(materializeE4ConnectorArticle(changed, fixture.articleId).rows[0]!.name).toBe("LOCAL");
  });
});
