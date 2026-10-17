// Подмены сети и образец шаблона для тестов экрана «Библиотека компонентов».
import type { ReferenceCatalogAdapter } from "../reference-catalog";
import { ReferenceCatalogApiError } from "../reference-catalog";
import { TemplateConflictError, type ComponentLibraryApi, type ComponentPublishInput } from "./component-library-api";
import type { ComponentTemplate, ComponentTemplateSummary } from "./component-template-api";
import { createE4ConnectorSeriesTableFromV3 } from "./e4-connector-series-table";
import { addContactPointV3, newTemplateContentV3, upsertArticleVariantV3 } from "./template-commands-v3";
import { createTemplateContentV5FromEditor } from "./template-model-v5";

export const TEMPLATE_ID = "11111111-1111-4111-8111-111111111111";

/** Справочники ERP, в которых ничего не опубликовано (поиск терминалов отвечает «не загружен»). */
export const emptyReferences: ReferenceCatalogAdapter = {
  getActive: async () => null,
  searchCatalog: async () => { throw new ReferenceCatalogApiError("нет справочника", "catalog_active_snapshot_not_found"); },
};

/** Модель шаблона v5 с одним артикулом и двумя контактами. */
export function sampleModel() {
  let content = newTemplateContentV3();
  const view = content.views[0]!;
  [content] = addContactPointV3(content, view.id, { number: "1", name: "Питание" });
  [content] = addContactPointV3(content, view.id, { number: "2", name: "Земля" });
  [content] = upsertArticleVariantV3(content, { sourceId: "technology-connectors", entityType: "connector", articleKey: "XH-02" });
  return createTemplateContentV5FromEditor(content, createE4ConnectorSeriesTableFromV3(content), []).content;
}

export function sampleTemplate(version = 1): ComponentTemplate {
  return {
    templateId: TEMPLATE_ID, version, code: "JST XH", name: "Разъёмы JST XH", articleBindings: [],
    createdUtc: "2026-10-10T10:00:00Z", updatedUtc: "2026-10-10T10:00:00Z", hasImage: false,
    versionSha256: "a".repeat(64), assets: [], content: sampleModel(),
  };
}

const summaryOf = (template: ComponentTemplate): ComponentTemplateSummary => ({
  templateId: template.templateId, version: template.version, code: template.code, name: template.name,
  articleBindings: template.articleBindings, createdUtc: template.createdUtc, updatedUtc: template.updatedUtc, hasImage: template.hasImage,
});

export interface InMemoryComponentLibrary extends ComponentLibraryApi {
  readonly published: Array<{ templateId: string | null; baseRevision: number | null; input: ComponentPublishInput }>;
  /** Версия, которую «уже изменил другой» — следующая запись получит 409. */
  conflictWith: { who: string; version: number } | null;
  /** Ответ на запись: причина отказа вместо новой версии. */
  failWith: Error | null;
}

export function createInMemoryComponentLibraryApi(initial: readonly ComponentTemplate[] = [sampleTemplate()]): InMemoryComponentLibrary {
  const templates = new Map(initial.map(item => [item.templateId, item]));
  const api: InMemoryComponentLibrary = {
    published: [], conflictWith: null, failWith: null,
    list: async () => [...templates.values()].map(summaryOf),
    get: async templateId => {
      const found = templates.get(templateId);
      if (!found) throw new Error("Шаблон не найден");
      return found;
    },
    create: async input => {
      if (api.failWith) throw api.failWith;
      api.published.push({ templateId: null, baseRevision: null, input });
      const created: ComponentTemplate = { ...sampleTemplate(1), templateId: crypto.randomUUID(), code: input.code, name: input.name, content: input.model, hasImage: Boolean(input.pngBase64) };
      templates.set(created.templateId, created);
      return created;
    },
    publish: async (templateId, baseRevision, input) => {
      api.published.push({ templateId, baseRevision, input });
      if (api.failWith) throw api.failWith;
      if (api.conflictWith) throw new TemplateConflictError(`Шаблон уже изменил ${api.conflictWith.who} — версия ${api.conflictWith.version}`);
      const next: ComponentTemplate = { ...templates.get(templateId)!, version: baseRevision + 1, code: input.code, name: input.name, content: input.model, hasImage: Boolean(input.pngBase64) };
      templates.set(templateId, next);
      return next;
    },
    assetContentUrl: (templateId, version, assetId) => `/assets/${templateId}/${version}/${assetId}`,
  };
  return api;
}

/** Другой сотрудник опубликовал свою версию: следующая запись с устаревшей версией получает 409, а чтение — его шаблон. */
export function publishTheirs(api: InMemoryComponentLibrary, templateId: string, who: string, changes: Partial<ComponentTemplate>) {
  const get = api.get;
  api.get = async id => (id === templateId ? { ...(await get(id)), ...changes } : get(id));
  api.conflictWith = { who, version: changes.version ?? 2 };
}
