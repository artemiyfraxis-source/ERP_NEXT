import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { TemplateSeriesPanelV3, formatTerminalArticleKeysV3, parseTerminalArticleKeysV3, type TemplateSeriesPanelV3Props } from "./TemplateSeriesPanelV3";
import {
  addBasicNodeV3,
  addContactPointV3,
  addContactTypeGroupV3,
  deleteContactPointV3,
  deleteContactTypeGroupV3,
  editLogicalContactV3,
  newTemplateContentV3,
  removeArticleVariantContactGroupV3,
  removeArticleVariantV3,
  renameContactTypeGroupV3,
  setArticleVariantContactGroupV3,
  TemplateCommandV3Error,
  upsertArticleVariantV3,
} from "./template-commands-v3";
import { validateTemplateContentV3, type ArticleKeyV3, type TemplateContentV3 } from "./template-model-v3";

const seriesTerminals = (content: TemplateContentV3): ArticleKeyV3[] => {
  const result: ArticleKeyV3[] = [], seen = new Set<string>();
  for (const terminal of content.articleVariants.flatMap(variant =>
    (variant.contactGroups ?? []).flatMap(group => group.allowedTerminalArticleKeys))) {
    const identity = `${terminal.sourceId}\0${terminal.entityType}\0${terminal.articleKey}`;
    if (!seen.has(identity)) { seen.add(identity); result.push(terminal); }
  }
  return result;
};

const renderSeriesPanel = (content: TemplateContentV3, overrides: Partial<TemplateSeriesPanelV3Props> = {}) => renderToStaticMarkup(createElement(TemplateSeriesPanelV3, {
  content,
  compatibleTerminalArticleKeys: seriesTerminals(content),
  selectedArticleVariantId: content.articleVariants[0]?.id,
  onAddContactTypeGroup: vi.fn(),
  onRenameContactTypeGroup: vi.fn(),
  onDeleteContactTypeGroup: vi.fn(),
  onAddArticleVariants: vi.fn(),
  onDeleteArticleVariant: vi.fn(),
  onSetArticleContactGroup: vi.fn(),
  onRemoveArticleContactGroup: vi.fn(),
  ...overrides,
}));

describe("template series v3 end-to-end workflow", () => {
  it("builds, displays, renames and removes a series without losing logical-contact metadata", () => {
    const initial = newTemplateContentV3();
    const [withSignal, signalId] = addContactTypeGroupV3(initial, "Сигнальные");
    const [withPower, powerId] = addContactTypeGroupV3(withSignal, "Силовые");
    const [withSpare, spareId] = addContactTypeGroupV3(withPower, "Резерв");
    const renamedGroups = renameContactTypeGroupV3(withSpare, powerId, "Питание");
    const e4 = renamedGroups.views.find(view => view.kind === "e4")!;
    const [withSignalContact, signalPointId] = addContactPointV3(renamedGroups, e4.id, {
      number: "1",
      name: "CAN H",
      circuitText: "CAN_H",
      contactTypeGroupId: signalId,
    });
    const [withBothContacts, powerPointId] = addContactPointV3(withSignalContact, e4.id, {
      number: "2",
      name: "+24 V",
      circuitText: "+24V_MAIN",
      contactTypeGroupId: powerId,
    });
    const signalLogicalId = withBothContacts.views[0]!.contactPoints.find(point => point.id === signalPointId)!.logicalContactId;
    const powerLogicalId = withBothContacts.views[0]!.contactPoints.find(point => point.id === powerPointId)!.logicalContactId;
    const editedContacts = editLogicalContactV3(withBothContacts, signalLogicalId, { name: "CAN High" });
    const beforeVariant = structuredClone(editedContacts);

    const [withVariant, variantId] = upsertArticleVariantV3(editedContacts, {
      sourceId: "БД.СОЕД",
      entityType: "connector",
      articleKey: "B3B-XH-A",
    });
    const parsedTerminals = parseTerminalArticleKeysV3(
      "БД.ТЕР|terminal|SXH-001T-P0.6\nБД.ТЕР|terminal|SXH-002T-P0.6",
    );
    expect(parsedTerminals).not.toBeNull();
    const withSignalConfig = setArticleVariantContactGroupV3(
      withVariant,
      variantId,
      signalId,
      1,
      parsedTerminals!,
    );
    const withPowerConfig = setArticleVariantContactGroupV3(
      withSignalConfig,
      variantId,
      powerId,
      1,
      [{ sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "SVH-21T-P1.1" }],
    );
    const withZeroConfig = setArticleVariantContactGroupV3(
      withPowerConfig,
      variantId,
      spareId,
      0,
      [{ sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "RESERVED-FUTURE" }],
    );
    const [withRenamedArticle, sameVariantId] = upsertArticleVariantV3(withZeroConfig, {
      id: variantId,
      sourceId: "БД.СОЕД",
      entityType: "connector",
      articleKey: "B3B-XH-A-LF-SN",
    });
    const [withDrawingObject] = addBasicNodeV3(
      withRenamedArticle,
      withRenamedArticle.views[1]!.id,
      withRenamedArticle.views[1]!.layers[0]!.id,
      "rectangle",
    );

    expect(editedContacts).toEqual(beforeVariant);
    expect(sameVariantId).toBe(variantId);
    expect(withDrawingObject.logicalContacts).toEqual(withBothContacts.logicalContacts.map(contact =>
      contact.id === signalLogicalId ? { ...contact, name: "CAN High" } : contact));
    expect(withDrawingObject.logicalContacts.find(contact => contact.id === signalLogicalId)).toMatchObject({
      circuitText: "CAN_H",
      contactTypeGroupId: signalId,
    });
    expect(withDrawingObject.logicalContacts.find(contact => contact.id === powerLogicalId)).toMatchObject({
      circuitText: "+24V_MAIN",
      contactTypeGroupId: powerId,
    });
    expect(withDrawingObject.articleVariants[0]!.contactGroups).toEqual([
      { contactTypeGroupId: signalId, contactCount: 1, allowedTerminalArticleKeys: parsedTerminals },
      { contactTypeGroupId: powerId, contactCount: 1, allowedTerminalArticleKeys: [{ sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "SVH-21T-P1.1" }] },
      { contactTypeGroupId: spareId, contactCount: 0, allowedTerminalArticleKeys: [{ sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "RESERVED-FUTURE" }] },
    ]);
    expect(formatTerminalArticleKeysV3(parsedTerminals!)).toBe(
      "БД.ТЕР|terminal|SXH-001T-P0.6\nБД.ТЕР|terminal|SXH-002T-P0.6",
    );
    expect(validateTemplateContentV3(withDrawingObject)).toEqual({ valid: true, diagnostics: [] });

    const markup = renderSeriesPanel(withDrawingObject);
    expect(markup).toContain("B3B-XH-A-LF-SN");
    expect(markup).toContain("Питание");
    expect(markup).toContain("RESERVED-FUTURE");
    expect(markup).toContain('value="0"');

    const withoutSpareConfig = removeArticleVariantContactGroupV3(withDrawingObject, variantId, spareId);
    const withoutSpareGroup = deleteContactTypeGroupV3(withoutSpareConfig, spareId);
    expect(withoutSpareGroup.articleVariants[0]!.contactGroups?.map(group => group.contactTypeGroupId)).toEqual([
      signalId,
      powerId,
    ]);
    expect(withoutSpareGroup.contactTypeGroups.some(group => group.id === spareId)).toBe(false);
    expect(validateTemplateContentV3(withoutSpareGroup).valid).toBe(true);

    expect(deleteContactTypeGroupV3(withoutSpareGroup, signalId).logicalContacts.every(contact => contact.contactTypeGroupId !== signalId)).toBe(true);

    const withoutArticle = removeArticleVariantV3(withoutSpareGroup, variantId);
    const withoutSignalPoint = deleteContactPointV3(withoutArticle, e4.id, signalPointId);
    const withoutPowerPoint = deleteContactPointV3(withoutSignalPoint, e4.id, powerPointId);
    const withoutSignalGroup = deleteContactTypeGroupV3(withoutPowerPoint, signalId);
    const empty = deleteContactTypeGroupV3(withoutSignalGroup, powerId);

    expect(empty.articleVariants).toEqual([]);
    expect(empty.logicalContacts).toEqual([]);
    expect(empty.contactTypeGroups).toEqual([]);
    expect(validateTemplateContentV3(empty)).toEqual({ valid: true, diagnostics: [] });
  });

  it("keeps panel input and command validation aligned for terminal rows and zero quantities", () => {
    const validRows = [
      "БД.ТЕР|terminal|T-1",
      "БД.ТЕР|terminal|T-2",
    ].join(";");
    const parsed = parseTerminalArticleKeysV3(validRows);
    expect(parsed).toEqual([
      { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "T-1" },
      { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "T-2" },
    ] satisfies ArticleKeyV3[]);

    const initial = newTemplateContentV3();
    const [withGroup, groupId] = addContactTypeGroupV3(initial, "Опциональные");
    const [withVariant, variantId] = upsertArticleVariantV3(withGroup, {
      sourceId: "БД.СОЕД",
      entityType: "connector",
      articleKey: "OPTIONAL-0",
    });
    const configured = setArticleVariantContactGroupV3(withVariant, variantId, groupId, 0, parsed!);

    expect(configured.articleVariants[0]!.contactGroups![0]).toEqual({
      contactTypeGroupId: groupId,
      contactCount: 0,
      allowedTerminalArticleKeys: parsed,
    });
    expect(validateTemplateContentV3(configured).valid).toBe(true);
    expect(parseTerminalArticleKeysV3("БД.ТЕР|terminal|T-1;БД.ТЕР|terminal|T-1")).toBeNull();
    expect(() => setArticleVariantContactGroupV3(withVariant, variantId, groupId, -1, []))
      .toThrowError(expect.objectContaining({ code: "invalid_contact_count" }));
  });

  it("shows terminal catalog results and one compatibility table for the series", () => {
    const [withGroup, groupId] = addContactTypeGroupV3(newTemplateContentV3(), "Сигнальные");
    const [withContact] = addContactPointV3(withGroup, withGroup.views[0]!.id, { contactTypeGroupId: groupId });
    const [withVariant, variantId] = upsertArticleVariantV3(withContact, {
      sourceId: "technology-connectors", entityType: "connector", articleKey: "B2B-XH-A",
    });
    const standard = { sourceId: "technology-terminals", entityType: "terminal", articleKey: "SXH-001T-P0.6" };
    const configured = setArticleVariantContactGroupV3(withVariant, variantId, groupId, 1, [standard]);
    const markup = renderSeriesPanel(configured, {
      terminalArticleQuery: "SXH",
      terminalArticleSuggestions: [standard, { ...standard, articleKey: "SXH-002T-P0.6" }],
      terminalArticleSearchState: "ready",
      compatibleTerminalArticleKeys: [standard],
      onChangeCompatibleTerminalArticleKeys: vi.fn(),
    });

    expect(markup).toContain("Добавить терминал");
    expect(markup).toContain("SXH-002T-P0.6");
    expect(markup).toContain("Совместимые терминалы серии");
    expect(markup).toContain("<th>Артикул</th><th>Тип контакта</th><th>Стандартный</th>");
    expect(markup).not.toContain("Стандартный терминал для типа");
  });

  it("keeps every controlled group edit in content when another group changes", () => {
    const initial = newTemplateContentV3();
    const [withSignal, signalId] = addContactTypeGroupV3(initial, "Сигнальные");
    const [withPower, powerId] = addContactTypeGroupV3(withSignal, "Силовые");
    const [withSignalContact] = addContactPointV3(withPower, withPower.views[0]!.id, { contactTypeGroupId: signalId });
    const [withBothContacts] = addContactPointV3(withSignalContact, withSignalContact.views[0]!.id, { contactTypeGroupId: powerId });
    const [withVariant, variantId] = upsertArticleVariantV3(withBothContacts, {
      sourceId: "БД.СОЕД", entityType: "connector", articleKey: "CONTROLLED-2",
    });
    const signalTerminal = { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "SIG-1" };
    const powerTerminal = { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "PWR-1" };

    const signalEdited = setArticleVariantContactGroupV3(withVariant, variantId, signalId, 1, [signalTerminal]);
    const bothEdited = setArticleVariantContactGroupV3(signalEdited, variantId, powerId, 1, [powerTerminal]);

    expect(bothEdited.articleVariants[0]!.contactGroups).toEqual([
      { contactTypeGroupId: signalId, contactCount: 1, allowedTerminalArticleKeys: [signalTerminal] },
      { contactTypeGroupId: powerId, contactCount: 1, allowedTerminalArticleKeys: [powerTerminal] },
    ]);
  });

  it("rejects duplicate article identities and duplicate terminal keys without changing the source", () => {
    const initial = newTemplateContentV3();
    const [withGroup, groupId] = addContactTypeGroupV3(initial, "Сигнальные");
    const [withFirst, firstId] = upsertArticleVariantV3(withGroup, {
      sourceId: "БД.СОЕД", entityType: "connector", articleKey: "A-01",
    });
    const [withSecond, secondId] = upsertArticleVariantV3(withFirst, {
      sourceId: "БД.СОЕД", entityType: "connector", articleKey: "A-02",
    });
    const beforeDuplicateArticle = structuredClone(withSecond);

    expect(() => upsertArticleVariantV3(withSecond, {
      id: secondId, sourceId: "БД.СОЕД", entityType: "connector", articleKey: "A-01",
    })).toThrowError(expect.objectContaining({ code: "duplicate_article_variant" }));
    expect(withSecond).toEqual(beforeDuplicateArticle);

    const terminal = { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "T-01" };
    const beforeDuplicateTerminal = structuredClone(withSecond);
    expect(() => setArticleVariantContactGroupV3(withSecond, firstId, groupId, 0, [terminal, { ...terminal }]))
      .toThrowError(expect.objectContaining({ code: "duplicate_terminal_article" }));
    expect(withSecond).toEqual(beforeDuplicateTerminal);
  });
});
