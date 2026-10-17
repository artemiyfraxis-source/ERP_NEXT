// @vitest-environment node
import { expect, it } from "vitest";
import { addArticleVariantsV3, addContactTypeGroupV3, newTemplateContentV3, setArticleVariantContactGroupV3 } from "../component-library/template-commands-v3";
import { createE4ConnectorSeriesTableFromV3 } from "../component-library/e4-connector-series-table";
import { createTemplateContentV5FromEditor } from "../component-library/template-model-v5";
import { createConnectorInstanceFromComponentTemplate } from "./component-template-placement";
import { refreshedTemplateTerminalCatalog } from "./template-terminal-catalog";
import { applyEditorCommand } from "./commands";
import { createEmptyHarnessDesign, parseHarnessDesignDocument } from "./model";

it("adds published series terminals to an old placed article without rematerializing rows or losing manual values", () => {
  let core = newTemplateContentV3();
  let group: string;
  [core, group] = addContactTypeGroupV3(core, "Сигнал");
  core = addArticleVariantsV3(core, [{ sourceId: "БД.СОЕД", entityType: "connector", articleKey: "PHR-2" }]);
  core = setArticleVariantContactGroupV3(core, core.articleVariants[0]!.id, group, 2, []);
  const content = createTemplateContentV5FromEditor(core, createE4ConnectorSeriesTableFromV3(core, true), [], []).content;
  const template = { templateId: "template", version: 1, versionSha256: "a".repeat(64),
    code: "PH", name: "JST PH", assets: [], articleBindings: core.articleVariants, createdUtc: "2026-09-19", content };
  const placed = createConnectorInstanceFromComponentTemplate(template, {
    id: "placed", designation: "XS1", e4Position: { x: 20, y: 40 },
  });
  const next = { ...template, version: 2, versionSha256: "b".repeat(64), content: { ...content,
    compatibleTerminalArticleKeys: [{ sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "SPH-002T-P0.5S" }] } };
  let document = { ...createEmptyHarnessDesign(), connectors: [placed] };
  document = applyEditorCommand(document, { type: "update-contact", connectorId: placed.id,
    contactId: placed.contacts[0]!.id, circuit: "MANUAL" }) as typeof document;
  const before = document.connectors[0]!;
  document = applyEditorCommand(document, { type: "refresh-template-terminals", connectorId: placed.id,
    catalog: refreshedTemplateTerminalCatalog(before, next) }) as typeof document;
  document = applyEditorCommand(document, { type: "update-contact", connectorId: placed.id,
    contactId: placed.contacts[0]!.id, terminalArticle: "SPH-002T-P0.5S" }) as typeof document;
  const restored = parseHarnessDesignDocument(JSON.parse(JSON.stringify(document))).connectors[0]!;
  expect(restored.libraryBinding).toEqual(before.libraryBinding);
  expect(restored.contacts[0]).toMatchObject({ circuit: "MANUAL", terminalArticle: "SPH-002T-P0.5S" });
  expect(restored.positions).toEqual(before.positions);
  expect(() => refreshedTemplateTerminalCatalog(restored, template)).toThrow(/старой/);
  expect(() => refreshedTemplateTerminalCatalog(restored, { ...next, templateId: "other" })).toThrow(/другому/);
});
