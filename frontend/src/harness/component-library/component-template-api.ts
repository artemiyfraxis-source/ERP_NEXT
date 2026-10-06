import type { ComponentTemplateContent } from "./template-content";

// Библиотека компонентов для редактора жгута — только чтение опубликованных версий.
// Авторинг шаблонов (черновики, публикация, загрузка изображений) из TECHMAP-GRAPHER не перенесён.

export interface ArticleBinding { readonly sourceId: string; readonly entityType: string; readonly articleKey: string; }
export interface TemplateAsset {
  readonly assetId: string;
  readonly sha256: string;
  readonly sizeBytes: number;
  readonly fileName: string;
  readonly mediaType: "image/png";
}
export interface ComponentTemplateSummary {
  readonly templateId: string; readonly version: number; readonly code: string; readonly name: string;
  readonly articleBindings: readonly ArticleBinding[]; readonly createdUtc: string; readonly updatedUtc?: string;
}
export interface ComponentTemplate extends ComponentTemplateSummary {
  readonly versionSha256: string;
  readonly assets: readonly TemplateAsset[];
  readonly content: ComponentTemplateContent;
}

/** Published, immutable component templates the editor may place into a harness. */
export interface ComponentTemplateApi {
  list(): Promise<readonly ComponentTemplateSummary[]>;
  /** Latest published version of a template. */
  get(templateId: string): Promise<ComponentTemplate>;
  getVersion(templateId: string, version: number): Promise<ComponentTemplate>;
  assetContentUrl(templateId: string, version: number, assetId: string): string;
}

export interface InMemoryComponentTemplateCatalogOptions {
  /** Every published version; the highest version per template is the latest one. */
  readonly templates?: readonly ComponentTemplate[];
  /** Image URLs (data: or blob:) by asset id. */
  readonly assetUrls?: Readonly<Record<string, string>>;
}

export function createInMemoryComponentTemplateCatalog(
  options: InMemoryComponentTemplateCatalogOptions = {},
): ComponentTemplateApi {
  const versions = [...(options.templates ?? [])];
  const latest = (templateId: string) => versions
    .filter((template) => template.templateId === templateId)
    .sort((a, b) => b.version - a.version)[0];
  return Object.freeze({
    async list() {
      const ids = [...new Set(versions.map((template) => template.templateId))];
      return ids.map((id) => {
        const template = latest(id)!;
        return {
          templateId: template.templateId, version: template.version, code: template.code, name: template.name,
          articleBindings: template.articleBindings, createdUtc: template.createdUtc, updatedUtc: template.updatedUtc,
        };
      });
    },
    async get(templateId: string) {
      const template = latest(templateId);
      if (!template) throw new Error("Шаблон компонента не найден.");
      return template;
    },
    async getVersion(templateId: string, version: number) {
      if (!Number.isSafeInteger(version) || version < 1) throw new Error("Версия шаблона задана неверно.");
      const template = versions.find((item) => item.templateId === templateId && item.version === version);
      if (!template) throw new Error("Версия шаблона компонента не найдена.");
      return template;
    },
    assetContentUrl(_templateId: string, _version: number, assetId: string) {
      return options.assetUrls?.[assetId] ?? "";
    },
  });
}
