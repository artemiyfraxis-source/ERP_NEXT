import type { ComponentTemplate } from "../component-library/component-template-api";
import { createConnectorInstanceFromComponentTemplate } from "./component-template-placement";
import { templateTerminalChoices, type ConnectorInstance, type ConnectorTerminalCatalog } from "./model";

/** Refresh compatibility only, leaving the project-owned materialization intact. */
export function refreshedTemplateTerminalCatalog(connector: ConnectorInstance, template: ComponentTemplate): ConnectorTerminalCatalog {
  const binding = connector.libraryBinding;
  if (binding?.mode !== "template" || binding.templateId !== template.templateId) {
    throw new Error("Список терминалов принадлежит другому шаблону.");
  }
  if (template.version < (connector.terminalCatalog?.version ?? binding.templateVersion)) {
    throw new Error("Нельзя обновить терминалы из более старой версии.");
  }
  if (template.content.schemaVersion < 3) throw new Error("Шаблон не содержит серии контактов.");
  const content = template.content;
  if (!("articleVariants" in content)) throw new Error("Шаблон не содержит артикулы.");
  const variant = content.articleVariants.find(item => item.articleKey === binding.article.articleKey &&
    item.sourceId.toLowerCase() === binding.article.sourceId.toLowerCase() &&
    item.entityType.toLowerCase() === binding.article.entityType.toLowerCase());
  if (!variant) throw new Error("Закреплённый артикул отсутствует в текущей версии серии.");
  const latest = createConnectorInstanceFromComponentTemplate({ ...template, content }, {
    id: connector.id, designation: connector.designation, articleVariantId: variant.id,
    e4Position: connector.positions.e4, drawingPosition: connector.positions.drawing,
  });
  if (latest.libraryBinding?.mode !== "template") throw new Error("Нет материализации серии.");
  const rows = new Map(latest.libraryBinding.snapshot.contacts.map(row => [row.logicalContactId, row]));
  return {
    templateId: template.templateId, version: template.version, versionSha256: template.versionSha256,
    byContact: Object.fromEntries(binding.snapshot.contacts.map(row => {
      const updated = rows.get(row.logicalContactId);
      // A different row/type is not a compatible update; never match by index.
      const allowed = updated && updated.contactTypeGroupId === row.contactTypeGroupId
        ? updated.allowedTerminalArticleKeys.map(item => item.articleKey) : [];
      return [row.logicalContactId, [...new Set([
        ...templateTerminalChoices(connector, row.logicalContactId), ...allowed,
      ])]];
    })),
  };
}
