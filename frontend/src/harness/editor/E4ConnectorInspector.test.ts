// @vitest-environment node
import { applyEditorCommand } from "./commands";
import { createEditorHistory, executeEditorCommand, undoEditorCommand } from "./history";
import { parseHarnessDesignDocument, connectorContactName } from "./model";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  E4ConnectorInspector,
  updateWireQueryState,
  wireColorSwatchBackground,
} from "./E4ConnectorInspector";
import {
  builtInConnectorSeries,
  createBuiltInConnectorInstance,
} from "./connector-series-demo";
import { builtInWireColors } from "./wire-reference-catalog";
import { connectorE4TableGeometry, createEmptyHarnessDesign, type ConnectorInstance } from "./model";
import { designToScene } from "./HarnessDesignEditor";
import { getE4ConnectorLayout, hitTestConnectorContact } from "./CanvasViewport";

function openingTag(markup: string, ariaLabel: string): string {
  const marker = `aria-label="${ariaLabel}"`;
  const markerIndex = markup.indexOf(marker);
  expect(markerIndex).toBeGreaterThanOrEqual(0);
  return markup.slice(markup.lastIndexOf("<", markerIndex), markup.indexOf(">", markerIndex) + 1);
}

function selectMarkup(markup: string, ariaLabel: string): string {
  const marker = `aria-label="${ariaLabel}"`;
  const markerIndex = markup.indexOf(marker);
  expect(markerIndex).toBeGreaterThanOrEqual(0);
  const start = markup.lastIndexOf("<select", markerIndex);
  const end = markup.indexOf("</select>", markerIndex);
  return markup.slice(start, end + "</select>".length);
}

function templateConnector(reverseRuntimeContacts = false): ConnectorInstance {
  const base = createBuiltInConnectorInstance("catalog-connector-free", {
    id: "template1", designation: "X1", e4Position: { x: 20, y: 30 }, freeContactCount: 2,
  });
  const article = { sourceId: "БД.СОЕД", entityType: "connector", articleKey: "XH-2" };
  const contacts = base.contacts.map((contact, index) => ({
    ...contact,
    id: `${base.id}:contact:logical-${index + 1}`,
    logicalContactId: `logical-${index + 1}`,
    contactType: index === 0 ? "сигнальный" : "силовой",
    libraryContact: null,
  }));
  const snapshotContacts = contacts.map((contact, index) => ({
    logicalContactId: contact.logicalContactId!,
    prototypeLogicalContactId: `prototype-${index + 1}`,
    sourceNumber: String(index + 1),
    name: `Контакт ${index + 1}`,
    circuitText: null,
    contactTypeGroupId: `group-${index + 1}`,
    contactType: contact.contactType,
    allowedTerminalArticleKeys: [{
      sourceId: "БД.ТЕР",
      entityType: "terminal",
      articleKey: index === 0 ? "T-1" : "T-2",
    }],
    representations: [],
  }));
  return {
    ...base,
    libraryCode: "JST-XH",
    partNumber: article.articleKey,
    contacts: reverseRuntimeContacts ? [...contacts].reverse() : contacts,
    libraryBinding: {
      mode: "template",
      templateId: "template-1",
      templateVersion: 3,
      versionSha256: "a".repeat(64),
      articleVariantId: "variant-1",
      article,
      snapshot: {
        templateId: "template-1",
        templateVersion: 3,
        versionSha256: "a".repeat(64),
        code: "JST-XH",
        name: "JST XH",
        articleVariantId: "variant-1",
        article,
        articleBindings: [article],
        assets: [],
        contacts: snapshotContacts,
      },
    },
  };
}

describe("E4 connector inline editing", () => {
  it("keeps pinned template contacts canonical while rendering and picking the reordered E4 rows", () => {
    const connector = templateConnector();
    const document = { ...createEmptyHarnessDesign(), connectors: [connector] };
    const changed = applyEditorCommand(document, { type: "move-contact-row", connectorId: connector.id, contactId: connector.contacts[0]!.id, direction: 1 });
    const moved = changed.connectors[0]!;
    expect(moved.contacts).toEqual(connector.contacts);
    expect(moved.libraryBinding).toEqual(connector.libraryBinding);
    expect(() => parseHarnessDesignDocument(changed)).not.toThrow();
    const scene = designToScene(changed, "e4");
    const layout = getE4ConnectorLayout(scene[0]!)!;
    expect(layout.rows.map(r => r.number)).toEqual([2, 1]);
    expect(hitTestConnectorContact(scene, changed.views.e4.layers.map(layer => ({ ...layer, label: layer.name })), layout.contactPoints[0]!, 1, "e4")).toEqual({ connectorId: connector.id, contactIndex: 0 });
    const markup = renderToStaticMarkup(createElement(E4ConnectorInspector, { connector: moved, mode: "canvas", editing: true, disabled: false, onCommand: vi.fn() }));
    expect(openingTag(markup, "Переместить контакт 2 вверх")).toContain('disabled=""');
    expect(openingTag(markup, "Переместить контакт 1 вверх")).not.toContain('disabled=""');
    expect(openingTag(markup, "Переместить контакт 1 вниз")).toContain('disabled=""');
    const readonly = renderToStaticMarkup(createElement(E4ConnectorInspector, { connector: moved, mode: "canvas", disabled: false, onCommand: vi.fn() }));
    expect(readonly).not.toContain("Переместить контакт");
    const library = renderToStaticMarkup(createElement(E4ConnectorInspector, { connector: moved, mode: "canvas", editing: true, disabled: false, onCommand: vi.fn(), templateAuthoring: { groups: [], names: {}, numbers: {}, showName: true, onNameVisibilityChange: vi.fn(), onNumberChange: vi.fn(), onNameChange: vi.fn() } }));
    expect(library).not.toContain("Переместить контакт");
  });
  it("selects saved wire colors regardless of case and Russian spelling", () => {
    const connector = templateConnector();
    const markup = renderToStaticMarkup(createElement(E4ConnectorInspector, {
      connector: {...connector, contacts: connector.contacts.map(c => ({...c, color: "Красный", secondaryColor: "Чёрный"}))},
      disabled: false, editing: true, mode: "canvas", onCommand: vi.fn(),
    }));
    expect(selectMarkup(markup, "Основной цвет, контакт 1")).toContain('value="красный" selected=""');
    expect(selectMarkup(markup, "Второй цвет, контакт 1")).toContain('value="черный" selected=""');
  });
  it("updates and dismisses wire suggestions without reading a pooled event", () => {
    const initial = Object.freeze({ other: "UL1061 30AWG" });
    const focused = updateWireQueryState(initial, "contact-1", "НВ-4 0,2 мм²");
    expect(focused).toEqual({ other: "UL1061 30AWG", "contact-1": "НВ-4 0,2 мм²" });
    expect(initial).toEqual({ other: "UL1061 30AWG" });
    expect(updateWireQueryState(focused, "contact-1", null)).toEqual({ other: "UL1061 30AWG" });
    expect(updateWireQueryState(initial, "missing", null)).toBe(initial);
  });

  it("offers all sections for a mark even when the current search page is narrower", () => {
    const connector = createBuiltInConnectorInstance("catalog-connector-free", {
      id: "wire-choices", designation: "X1", e4Position: { x: 0, y: 0 }, freeContactCount: 1,
    });
    const selected = { ...connector, contacts: connector.contacts.map(contact => ({ ...contact, wire: "МГТФ", wireSection: "0,35" })) };
    const options = [
      { id: "one", mark: "МГТФ", section: "0,35", label: "МГТФ · 0,35", detail: "" },
      { id: "two", mark: "МГТФ", section: "0,5", label: "МГТФ · 0,5", detail: "" },
      { id: "three", mark: "НВ-4", section: "0,35", label: "НВ-4 · 0,35", detail: "" },
    ];
    const markup = renderToStaticMarkup(createElement(E4ConnectorInspector, {
      connector: selected, disabled: false, editing: true, mode: "canvas", onCommand: vi.fn(),
      wireOptions: options.slice(0, 1), wireMaterialOptions: options,
    }));
    const sections = selectMarkup(markup, "Сечение, контакт 1");
    expect(sections).toContain('value="0,5"');
    expect(sections).not.toContain('value="1,0"');
  });

  it("keeps mono and two-color swatches deterministic", () => {
    expect(wireColorSwatchBackground("красный", "", builtInWireColors)).toBe("#D32F2F");
    expect(wireColorSwatchBackground("красный", "черный", builtInWireColors)).toBe(
      "linear-gradient(225deg, #D32F2F 0 49%, #8da0aa 49% 51%, #202124 51% 100%)",
    );
  });

  it("renders the series article selector in the side panel", () => {
    const connector = createBuiltInConnectorInstance("catalog-connector-series:xs-demo-series", {
      id: "xs1", designation: "XS1", e4Position: { x: 20, y: 30 },
    });
    const markup = renderToStaticMarkup(createElement(E4ConnectorInspector, {
      connector,
      series: builtInConnectorSeries[0],
      disabled: false,
      onCommand: vi.fn(),
    }));

    expect(markup).toContain("Артикул серии");
    expect(markup).toContain("XS-04");
    expect(markup).toContain("XS-10");
    expect(markup).toContain("Поля таблицы · скрыть / показать");
    expect(markup).not.toContain("Добавить строку контакта");
  });

  it("edits free rows on the object and offers terminals from the whole reference", () => {
    const connector = createBuiltInConnectorInstance("catalog-connector-free", {
      id: "free1", designation: "XS9", e4Position: { x: 20, y: 30 }, freeContactCount: 2,
    });
    const markup = renderToStaticMarkup(createElement(E4ConnectorInspector, {
      connector,
      terminalArticles: ["M39029/57-354", "TER-002"],
      disabled: false,
      onCommand: vi.fn(),
      mode: "canvas",
      editing: true,
    }));

    expect(markup).toContain("Поля соединителя XS9");
    expect(markup).toContain("Добавить строку");
    expect(markup).toContain("terminal-articles-free1");
    expect(markup).toContain("M39029/57-354");
    expect(markup).toContain("Скрыть поле");
    expect(markup).toContain("Редактирование включено. Escape — закончить");
    expect(markup).toContain("is-editing");
    expect(markup).toContain("e4cce-title-add");
    expect(markup).toMatch(/class="e4cce-title-add"[^>]*>⊕ Добавить строку/);
    expect(markup).not.toMatch(/class="e4cce-title-add"[^>]*disabled/);
    expect(markup).toContain("e4cce-footer-code");
    expect(markup).toContain("e4cce-footer-article");
    expect(markup).toContain('aria-label="Код свободного блока"');
    expect(markup).toContain('aria-label="Артикул свободного блока"');
    expect(markup).toContain('maxLength="512"');
    expect(markup).toContain("e4cce-wire-picker");
    expect(markup).toContain("XS9");
  });

  it("keeps add-row immediately available on a selected free block before field editing", () => {
    const connector = createBuiltInConnectorInstance("catalog-connector-free", {
      id: "free1", designation: "XS9", e4Position: { x: 20, y: 30 }, freeContactCount: 2,
    });
    const markup = renderToStaticMarkup(createElement(E4ConnectorInspector, {
      connector,
      disabled: false,
      onCommand: vi.fn(),
      mode: "canvas",
      editing: false,
    }));

    expect(markup).toMatch(/class="e4cce-title-add"[^>]*>⊕ Добавить строку/);
    expect(markup).not.toMatch(/class="e4cce-title-add"[^>]*disabled/);
  });

  it("locks series number and type rows and filters terminals by contact kind", () => {
    const connector = createBuiltInConnectorInstance("catalog-connector-series:xs-demo-series", {
      id: "xs1", designation: "XS1", e4Position: { x: 20, y: 30 },
    });
    const markup = renderToStaticMarkup(createElement(E4ConnectorInspector, {
      connector,
      series: builtInConnectorSeries[0],
      disabled: false,
      onCommand: vi.fn(),
      mode: "canvas",
    }));

    expect(markup).toContain("Двойной клик — редактировать");
    expect(markup).toContain("сигнальный");
    expect(markup).toContain("силовой");
    expect(markup).toContain("коаксиальный");
    expect(markup).toContain("Строки из артикула");
  });

  it("renders a template placement with immutable library structure and compatible terminal choices", () => {
    const markup = renderToStaticMarkup(createElement(E4ConnectorInspector, {
      connector: templateConnector(),
      disabled: false,
      onCommand: vi.fn(),
      mode: "canvas",
      editing: true,
    }));

    expect(markup).toContain("Строки из артикула");
    expect(markup).toMatch(/class="e4cce-title-add"[^>]*disabled/);
    expect(markup).not.toContain('aria-label="Код свободного блока"');
    expect(markup).not.toContain('aria-label="Артикул свободного блока"');
    expect(markup).toContain("JST-XH");
    expect(markup).toContain("XH-2");
    expect(markup).toContain("Контакт 1");
    expect(markup).toContain("Назначение");
    expect(markup).toContain("T-1");
    expect(markup).toContain("T-2");
    expect(markup).toContain("Номер и тип заданы закреплённым шаблоном");
    expect(markup).toContain('aria-label="Цепь, контакт 1"');
    expect(markup).toContain('aria-label="Марка, контакт 1"');
    expect(markup).toContain('aria-label="Сечение, контакт 1"');
    expect(openingTag(markup, "№, контакт 1")).toContain("disabled");
    expect(openingTag(markup, "Тип, контакт 1")).toContain("disabled");
    expect(openingTag(markup, "Удалить контакт 1")).toContain("disabled");
    expect(openingTag(markup, "Цепь, контакт 1")).not.toContain("disabled");
    expect(openingTag(markup, "Марка, контакт 1")).not.toContain("disabled");
    expect(openingTag(markup, "Сечение, контакт 1")).not.toContain("disabled");
    expect(openingTag(markup, "Цвет, контакт 1")).not.toContain("disabled");
    expect(openingTag(markup, "Терминал, контакт 1")).not.toContain("disabled");
  });

  it("keeps the composite terminal identity while rendering only its article", () => {
    const connector = templateConnector();
    const composite = "3:JST|14:SPH-002T-P0.5S|0:|3:PHR";
    const changed = {
      ...connector,
      contacts: connector.contacts.map((contact, index) => index === 0
        ? { ...contact, terminalArticle: composite }
        : contact),
      libraryBinding: connector.libraryBinding?.mode === "template" ? {
        ...connector.libraryBinding,
        snapshot: {
          ...connector.libraryBinding.snapshot,
          contacts: connector.libraryBinding.snapshot.contacts.map(contact => ({
            ...contact,
            allowedTerminalArticleKeys: [{ sourceId: "БД.ТЕР", entityType: "terminal", articleKey: composite }],
          })),
        },
      } : connector.libraryBinding,
    } as ConnectorInstance;
    const markup = renderToStaticMarkup(createElement(E4ConnectorInspector, {
      connector: changed, disabled: false, onCommand: vi.fn(), mode: "canvas", editing: false,
    }));
    expect(markup).toContain("SPH-002T-P0.5S");
    expect(markup).not.toContain(composite);
    const editable = renderToStaticMarkup(createElement(E4ConnectorInspector, {
      connector: changed, disabled: false, onCommand: vi.fn(), mode: "canvas", editing: true,
    }));
    expect(selectMarkup(editable, "Терминал, контакт 1"))
      .toContain(`<option value="${composite}" selected="">SPH-002T-P0.5S</option>`);
    const object = designToScene({ ...createEmptyHarnessDesign(), connectors: [changed] }, "e4")[0]!;
    const layout = getE4ConnectorLayout(object)!;
    expect(layout.rows[0]!.terminal).toBe("SPH-002T-P0.5S");
    expect(layout.width).toBe(connectorE4TableGeometry(changed).width);
    expect(changed.contacts[0]!.terminalArticle).toBe(composite);
  });

  it("maps template terminal choices by logical contact ID", () => {
    const markup = renderToStaticMarkup(createElement(E4ConnectorInspector, {
      connector: templateConnector(true),
      disabled: false,
      onCommand: vi.fn(),
      mode: "canvas",
      editing: true,
    }));

    const secondContact = selectMarkup(markup, "Терминал, контакт 2");
    expect(secondContact).toContain("T-2");
    expect(secondContact).not.toContain("T-1");
  });

  it("shows a template article as read-only in the side panel", () => {
    const markup = renderToStaticMarkup(createElement(E4ConnectorInspector, {
      connector: templateConnector(),
      disabled: false,
      onCommand: vi.fn(),
    }));

    expect(markup).toContain("Артикул шаблона");
    expect(markup).toContain("XH-2");
    expect(markup).toContain("закреплены версией шаблона");
    expect(markup).not.toContain('value="XH-2"');
  });

  it("shows one family article selector when persistent variants are available", () => {
    const connector = templateConnector();
    if (connector.libraryBinding?.mode !== "template") throw new Error("template fixture expected");
    const markup = renderToStaticMarkup(createElement(E4ConnectorInspector, {
      connector,
      disabled: false,
      onCommand: vi.fn(),
      templateArticleOptions: [
        { articleVariantId: connector.libraryBinding.articleVariantId, articleKey: "XH-2" },
        { articleVariantId: "variant-xh-10", articleKey: "XH-10" },
      ],
      onTemplateArticleSelect: vi.fn(),
    }));

    expect(selectMarkup(markup, "Артикул шаблона")).toContain("XH-2");
    expect(selectMarkup(markup, "Артикул шаблона")).toContain("XH-10");
    expect(markup).toContain("Артикул выбирается из семейства");
    expect(markup).not.toContain("закреплены версией шаблона");
  });

  it("renders actual controls only after the canvas enters editing mode", () => {
    const connector = createBuiltInConnectorInstance("catalog-connector-series:xs-demo-series", {
      id: "xs1", designation: "XS1", e4Position: { x: 20, y: 30 },
    });
    const markup = renderToStaticMarkup(createElement(E4ConnectorInspector, {
      connector,
      series: builtInConnectorSeries[0],
      disabled: false,
      onCommand: vi.fn(),
      mode: "canvas",
      editing: true,
    }));

    expect(markup).toContain("is-editing");
    expect(markup).toContain("Редактирование включено. Escape — закончить");
    expect(markup).toContain('aria-label="Обозначение соединителя"');
    expect(markup).toContain("TERM-SIG-05");
    expect(markup).toContain("Номер и тип заданы артикулом серии");
    expect(markup).toContain("Основной цвет");
    expect(markup).toContain("Второй цвет");
    expect(markup).toContain("Пусто · одноцветный");
    expect(markup).toContain("Автоматический цвет");
    expect(markup).toContain("Новый цвет");
    expect(markup).toContain('aria-expanded="false"');
    expect(markup).toContain('popover="manual" class="anchored-popover e4cce-color-popover"');
    expect(markup).toContain("e4cce-wire-picker");
  });
});


describe("purpose column in E4", () => {
  const hasName = (connector: ConnectorInstance) => connectorE4TableGeometry(connector).columns.some(column => column.kind === "custom" && column.id === "template-name");
  it("allows editing a placed name and provides local and document visibility controls", () => {
    const connector = templateConnector();
    const markup = renderToStaticMarkup(createElement(E4ConnectorInspector, { connector, disabled: false, onCommand: vi.fn(), mode: "canvas", editing: true }));
    expect(openingTag(markup, "Назначение, контакт 1")).not.toContain("disabled");
    expect(openingTag(markup, "Скрыть поле Назначение")).not.toContain("disabled");
    const panel = renderToStaticMarkup(createElement(E4ConnectorInspector, { connector, disabled: false, onCommand: vi.fn() }));
    expect(panel).toContain("Скрыть назначение во всей схеме");
    expect(panel).toContain("Скрыть поле «Назначение»");
  });

  it("edits and clears names without changing the library snapshot, and updates the scene", () => {
    const connector = templateConnector();
    let document: ReturnType<typeof createEmptyHarnessDesign> = { ...createEmptyHarnessDesign(), connectors: [connector] };
    for (const nameOverride of ["Питание устройства", ""]) {
      document = applyEditorCommand(document, { type: "update-contact", connectorId: connector.id, contactId: connector.contacts[0]!.id, nameOverride });
      const restored = parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)));
      expect(connectorContactName(restored.connectors[0]!, restored.connectors[0]!.contacts[0]!)).toBe(nameOverride);
      expect(restored.connectors[0]!.libraryBinding).toEqual({...connector.libraryBinding,contactNumbering:"source-v1"});
      const scene = designToScene(document, "e4");
      expect(JSON.parse(scene[0]!.metadata!.rows as string)[0].name).toBe(nameOverride);
    }
  });

  it("hides locally or across the document, preserves values, and undoes the whole operation", () => {
    const first = templateConnector();
    const second = { ...templateConnector(), id: "other" };
    const document = { ...createEmptyHarnessDesign(), connectors: [first, second] };
    const local = applyEditorCommand(document, { type: "set-name-column-visibility", connectorId: first.id, visible: false });
    expect(local.connectors.map(hasName)).toEqual([false, true]);
    const hiddenMarkup = renderToStaticMarkup(createElement(E4ConnectorInspector, { connector: local.connectors[0]!, disabled: false, onCommand: vi.fn(), mode: "canvas" }));
    expect(hiddenMarkup).not.toContain("Назначение");
    const hidden = executeEditorCommand(createEditorHistory(document), { type: "set-name-column-visibility", connectorId: first.id, visible: false, scope: "document" });
    expect(hidden.present.connectors.map(hasName)).toEqual([false, false]);
    expect(undoEditorCommand(hidden).present).toEqual(document);
    const shown = applyEditorCommand(hidden.present, { type: "set-name-column-visibility", connectorId: first.id, visible: true, scope: "document" });
    expect(shown.connectors.map(hasName)).toEqual([true, true]);
    expect(shown.connectors[0]!.contacts).toEqual(first.contacts);
  });
});
