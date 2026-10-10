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
