import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  TemplateSeriesPanelV3,
  articleAddPreviewV3,
  appendTerminalArticleKeyV3,
  articleContactGroupEditorValueV3,
  contactTypeNameExistsV3,
  formatTerminalArticleKeysV3,
  parseTerminalArticleKeysV3,
  readableTerminalArticleV3,
  updateTerminalArticleKeyV3,
  patternContactCount,
} from "./TemplateSeriesPanelV3";
import { addContactPointV3, addContactTypeGroupV3, newTemplateContentV3, upsertArticleVariantV3 } from "./template-commands-v3";
import { upgradeTemplateContentV2ToV3 } from "./template-upgrade-v3";
import { newTemplateContentV2 } from "./template-commands-v2";
import { addSeriesArticles } from "./ComponentLibrary";

describe("TemplateSeriesPanelV3", () => {
  it("derives independent pin count from each XX article value", () => {
    expect(patternContactCount("JST-XX", "JST-12")).toBe(12);
    expect(patternContactCount("JST-XX-A", "JST-07-A")).toBe(7);
  });

  it("stores XX count in the first article contact group", () => {
    const content = upgradeTemplateContentV2ToV3(newTemplateContentV2()).content;
    const next = addSeriesArticles(content, [{ sourceId: "БД.СОЕД", entityType: "connector", articleKey: "JST-12", contactCount: 12 }]);
    expect(next.articleVariants[0]?.contactGroups?.[0]?.contactCount).toBe(12);
  });
  it("recognizes contact type duplicates ignoring case and surrounding spaces", () => {
    const content = newTemplateContentV3();
    content.contactTypeGroups.push({ id: crypto.randomUUID(), name: "Сигнальные" });
    expect(contactTypeNameExistsV3(content, " сигнальные ")).toBe(true);
    expect(contactTypeNameExistsV3(content, "Силовые")).toBe(false);
    expect(contactTypeNameExistsV3(content, " ")).toBe(false);
  });

  it("previews a pattern using fixed connector catalog identity and reports collisions in Russian", () => {
    const content = newTemplateContentV3();
    content.articleVariants.push({ id: crypto.randomUUID(), sourceId: "БД.СОЕД", entityType: "connector", articleKey: "PHR-02", parameterValues: [], contactGroups: null });
    expect(articleAddPreviewV3(content, "pattern", "", "PHR-XX", "01-03")).toEqual({
      articles: [], error: "Артикул «PHR-02» уже есть в этой серии.",
    });
    expect(articleAddPreviewV3(content, "pattern", "", "PHR-XX", "01, 03")).toEqual({
      articles: ["PHR-01", "PHR-03"], error: null,
    });
  });

  it("renders contact groups, article variants and editable terminal configuration", () => {
    const content = upgradeTemplateContentV2ToV3(newTemplateContentV2()).content;
    const groupId = crypto.randomUUID();
    content.contactTypeGroups.push({ id: groupId, name: "Сигнальные" });
    content.articleVariants.push({ id: crypto.randomUUID(), sourceId: "БД.СОЕД", entityType: "connector", articleKey: "B2B-XH-A", parameterValues: [], contactGroups: [{ contactTypeGroupId: groupId, contactCount: 2, allowedTerminalArticleKeys: [{ sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "SXH-001T-P0.6" }] }] });
    const markup = renderToStaticMarkup(createElement(TemplateSeriesPanelV3, {
      content, onAddContactTypeGroup: vi.fn(), onRenameContactTypeGroup: vi.fn(), onDeleteContactTypeGroup: vi.fn(),
      onAddArticleVariants: vi.fn(), onDeleteArticleVariant: vi.fn(), onSetArticleContactGroup: vi.fn(), onRemoveArticleContactGroup: vi.fn(),
      compatibleTerminalArticleKeys: [{ sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "SXH-001T-P0.6" }],
      onChangeCompatibleTerminalArticleKeys: vi.fn(),
      selectedArticleVariantId: content.articleVariants[0]!.id,
    }));
    expect(markup).toContain("Настройка серии");
    expect(markup).toContain("Типы контактов");
    expect(markup).toContain("Силовые");
    expect(markup).toContain("Дополнительные");
    expect(markup).toContain("Другой тип");
    expect(markup).toContain("Тип «Сигнальные» уже добавлен.");
    expect(markup).toContain('<button type="submit" disabled="">+ Тип</button>');
    expect(markup).toContain("Сигнальные");
    expect(markup).toContain("B2B-XH-A");
    expect(markup).toContain("SXH-001T-P0.6");
    expect(markup).toContain('aria-label="Таблица артикулов серии"');
    expect(markup).toContain("Сигнальные, шт.");
    expect(markup).toContain('aria-label="Количество контактов Сигнальные артикула B2B-XH-A"');
    expect(markup).toContain(`data-article-variant-id="${content.articleVariants[0]!.id}"`);
    expect(markup).toContain('aria-label="Открыть таблицу Э4 артикула B2B-XH-A"');
    expect(markup).toContain('tabindex="0"');
    expect(markup).toContain('aria-label="Удалить артикул B2B-XH-A"');
    expect(markup).toContain("не имеет повторяемого сегмента");
    expect(markup).toContain("Создайте прототип контакта и один домен повтора");
    expect(markup).toContain("либо укажите 0");
    expect(markup).toContain('aria-invalid="true"');
    expect(markup).toContain("Совместимые терминалы серии");
    expect(markup).not.toContain("Артикул для предпросмотра");
    expect(markup).toContain("Один артикул");
    expect(markup).toContain("По шаблону");
    expect(markup).toContain('name="article-add-mode"');
    expect(markup).not.toContain("B2B-XH-A: 2 контакта");
    expect(markup).not.toContain("Материализованные строки контактов");
    expect(markup).not.toContain("NET-DATA+");
    expect(markup).toContain('class="series-v3-terminal-table"');
    expect(markup).toContain("<th>Артикул</th><th>Тип контакта</th><th>Стандартный</th>");
    expect(markup).not.toContain("<th>Источник</th>");
    expect(markup).not.toContain("Стандартный терминал для типа");

    expect(markup.indexOf('series-v3-groups')).toBeLessThan(markup.indexOf('series-v3-series-terminals'));
    expect(markup.indexOf('series-v3-series-terminals')).toBeLessThan(markup.indexOf('series-v3-variants'));
    expect(markup).toContain('role="tooltip"');

  });

  it("does not duplicate a reference connector through the manual fallback", () => {
    const content = newTemplateContentV3();
    content.articleVariants.push({
      id: crypto.randomUUID(), sourceId: "technology-connectors", entityType: "connector", articleKey: "B2B-XH-A",
      parameterValues: [], contactGroups: null,
    });
    expect(articleAddPreviewV3(content, "single", " B2B-XH-A ", "", "")).toEqual({
      articles: [], error: "Артикул «B2B-XH-A» уже есть в этой серии.",
    });
  });

  it("renders one compact quantity column for every contact type", () => {
    const content = newTemplateContentV3();
    content.contactTypeGroups.push(
      { id: "signal", name: "Сигнальные" },
      { id: "power", name: "Силовые" },
    );
    content.articleVariants.push({
      id: "article", sourceId: "БД.СОЕД", entityType: "connector", articleKey: "X-02",
      parameterValues: [], contactGroups: [
        { contactTypeGroupId: "signal", contactCount: 2, allowedTerminalArticleKeys: [] },
        { contactTypeGroupId: "power", contactCount: 1, allowedTerminalArticleKeys: [] },
      ],
    });
    const markup = renderToStaticMarkup(createElement(TemplateSeriesPanelV3, {
      content, onAddContactTypeGroup: vi.fn(), onRenameContactTypeGroup: vi.fn(), onDeleteContactTypeGroup: vi.fn(),
      onAddArticleVariants: vi.fn(), onDeleteArticleVariant: vi.fn(), onSetArticleContactGroup: vi.fn(), onRemoveArticleContactGroup: vi.fn(),
    }));
    expect(markup).toContain("Сигнальные, шт.");
    expect(markup).toContain("Силовые, шт.");
    expect(markup.match(/series-v3-count-cell/g)).toHaveLength(2);
    expect(markup).not.toContain('class="series-v3-variant"');
  });

  it("renders manual articles without the removed connector search", () => {
    const content = newTemplateContentV3();
    content.articleVariants.push({
      id: "existing", sourceId: "technology-connectors", entityType: "connector", articleKey: "B2B-XH-A",
      parameterValues: [], contactGroups: null,
    });
    const markup = renderToStaticMarkup(createElement(TemplateSeriesPanelV3, {
      content,
      onAddContactTypeGroup: vi.fn(), onRenameContactTypeGroup: vi.fn(), onDeleteContactTypeGroup: vi.fn(),
      onAddArticleVariants: vi.fn(), onDeleteArticleVariant: vi.fn(), onSetArticleContactGroup: vi.fn(), onRemoveArticleContactGroup: vi.fn(),
    }));

    expect(markup).not.toContain('aria-label="Поиск артикула в справочнике соединителей"');
    expect(markup).not.toContain('aria-label="Артикулы из справочника соединителей"');
    expect(markup).toContain("B2B-XH-A");
    expect(markup).not.toContain("B10B-XH-A");
    expect(markup).not.toContain("Уже в серии");
    expect(markup).not.toContain("+ Добавить");
    expect(markup).toContain("Один артикул");
    expect(markup).toContain("По шаблону");
  });

  it("parses terminal keys without silently accepting invalid or duplicate rows", () => {
    expect(parseTerminalArticleKeysV3("БД.ТЕР|terminal|A\nБД.ТЕР|terminal|B")).toEqual([
      { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "A" },
      { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "B" },
    ]);
    expect(parseTerminalArticleKeysV3("БД.ТЕР|terminal|A; БД.ТЕР|terminal|A")).toBeNull();
    expect(parseTerminalArticleKeysV3("только артикул")).toBeNull();
    expect(formatTerminalArticleKeysV3([{ sourceId: "db", entityType: "terminal", articleKey: "T1" }])).toBe("db|terminal|T1");
  });

  it("round-trips delimiters through the legacy import/export helper", () => {
    const keys = [{ sourceId: "БД|ТЕР", entityType: "terminal", articleKey: "T;1\\A" }];
    const encoded = formatTerminalArticleKeysV3(keys);
    expect(parseTerminalArticleKeysV3(encoded)).toEqual(keys);
  });

  it("formats a composite terminal key as manufacturer, article and series", () => {
    expect(readableTerminalArticleV3("3:JST|14:SPH-002T-P0.5S|0:|3:PHR")).toBe("JST SPH-002T-P0.5S PHR");
    expect(readableTerminalArticleV3("SXH-001T-P0.6")).toBe("SXH-001T-P0.6");
  });

  it("updates one structured terminal row atomically and rejects incomplete duplicates", () => {
    const keys = [
      { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "T-1" },
      { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "T-2" },
    ];
    expect(updateTerminalArticleKeyV3(keys, 0, { articleKey: "T-1-A" })).toEqual([
      { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "T-1-A" }, keys[1],
    ]);
    expect(updateTerminalArticleKeyV3(keys, 0, { articleKey: "" })).toBeNull();
    expect(updateTerminalArticleKeyV3(keys, 0, { articleKey: "T-2" })).toBeNull();
    expect(appendTerminalArticleKeyV3(keys, { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "T-3" })).toEqual([
      ...keys, { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "T-3" },
    ]);
  });

  it("reports inherited contact counts separately from explicit overrides", () => {
    const initial = newTemplateContentV3();
    const [grouped, groupId] = addContactTypeGroupV3(initial, "Сигнальные");
    const [withContact] = addContactPointV3(grouped, grouped.views[0]!.id, { contactTypeGroupId: groupId });
    const [withVariant, variantId] = upsertArticleVariantV3(withContact, { sourceId: "БД.СОЕД", entityType: "connector", articleKey: "X-1" });
    expect(articleContactGroupEditorValueV3(withVariant, variantId, groupId)).toMatchObject({ contactCount: 1, inherited: true, configured: false });
  });
});
