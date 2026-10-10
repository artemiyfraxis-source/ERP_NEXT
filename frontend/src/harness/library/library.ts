// Библиотека компонентов жгута в ERP (таблица harness_component_templates, миграция 0091): шаблоны разъёмов по ключу
// справочника и номеру версии. Редактор читает её через `harnessApi` — список для каталога, версию для размещения,
// рисунки версий; соединитель на чертеже закрепляет шаблон и версию сам, копий шаблонов в чертеже нет.
import { harnessApi, type HarnessComponent, type HarnessPlacementGraph } from "../../api";
import type { ComponentTemplate, ComponentTemplateApi } from "../component-library/component-template-api";
import { parseComponentTemplateContent } from "../component-library/template-content";
import { parseComponentPlacementGraph, type ProjectComponentPlacementGraph } from "../editor/component-placement-api";

/** A library revision as the editor places it; the model is checked before the editor trusts it. */
export function componentTemplateOf(item: HarnessComponent): ComponentTemplate {
  return {
    templateId: item.templateId, version: item.version, code: item.code, name: item.name,
    articleBindings: item.articleBindings, createdUtc: item.createdUtc, updatedUtc: item.updatedUtc,
    versionSha256: item.versionSha256, assets: item.assets, content: parseComponentTemplateContent(item.content),
  };
}

/** The component library of the editor (`catalog.templates`). */
export function libraryTemplates(): ComponentTemplateApi {
  return {
    async list() {
      return (await harnessApi.components()).map((item) => ({
        templateId: item.templateId, version: item.version, code: item.code, name: item.name,
        articleBindings: item.articleBindings, createdUtc: item.createdUtc, updatedUtc: item.updatedUtc,
      }));
    },
    async get(templateId) {
      return componentTemplateOf(await harnessApi.component(templateId));
    },
    async getVersion(templateId, version) {
      if (!Number.isSafeInteger(version) || version < 1) throw new Error("Версия шаблона задана неверно.");
      return componentTemplateOf(await harnessApi.component(templateId, version));
    },
    assetContentUrl: harnessApi.componentAssetUrl,
  };
}

/** The placements of a saved drawing as the editor reads them: ids of this project and harness (the server names the
 *  position as it stores it), every snapshot pinned by a connector. */
export function placementGraphOf(graph: HarnessPlacementGraph, projectId: string, harnessId: string): ProjectComponentPlacementGraph {
  return parseComponentPlacementGraph({
    placements: graph.placements.map((row) => ({ ...row, harnessId })),
    snapshots: graph.snapshots.map((row) => ({ ...row, projectId })),
  }, projectId, harnessId);
}
