import { ApiError, harnessApi, type HarnessComponent } from "../../api";
import type { ComponentTemplate, ComponentTemplateSummary } from "./component-template-api";
import { parseComponentTemplateContent, type ComponentTemplateContent } from "./template-content";

/** Что экран отправляет на сервер: шаблон целиком (модель TECHMAP-GRAPHER v5) и необязательная картинка разъёма. */
export interface ComponentPublishInput {
  readonly code: string;
  readonly name: string;
  readonly model: ComponentTemplateContent;
  /** PNG разъёма, base64; null — картинку не менять. */
  readonly pngBase64: string | null;
}

/** 409 сервера: шаблон уже изменил другой сотрудник. Текст — «Шаблон уже изменил <кто> — версия N». */
export class TemplateConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TemplateConflictError";
  }
}

/** Библиотека компонентов для экрана: чтение каталога и запись новых версий. Черновиков на сервере нет. */
export interface ComponentLibraryApi {
  list(): Promise<readonly ComponentTemplateSummary[]>;
  /** Последняя версия шаблона. */
  get(templateId: string): Promise<ComponentTemplate>;
  /** Новый шаблон — версия 1. */
  create(input: ComponentPublishInput): Promise<ComponentTemplate>;
  /** Версия N+1; `baseRevision` — версия, с которой правили (устаревшая — TemplateConflictError). */
  publish(templateId: string, baseRevision: number, input: ComponentPublishInput): Promise<ComponentTemplate>;
  assetContentUrl(templateId: string, version: number, assetId: string): string;
}

function asTemplate(row: HarnessComponent): ComponentTemplate {
  return { ...row, content: parseComponentTemplateContent(row.content) };
}

const body = (input: ComponentPublishInput) => ({
  code: input.code, name: input.name, model: input.model as unknown as Record<string, unknown>,
  ...(input.pngBase64 ? { png_base64: input.pngBase64 } : {}),
});

async function guarded<T>(request: Promise<T>): Promise<T> {
  try { return await request; }
  catch (reason) {
    if (reason instanceof ApiError && reason.status === 409) throw new TemplateConflictError(reason.message);
    throw reason;
  }
}

export function createComponentLibraryApi(): ComponentLibraryApi {
  return {
    list: () => harnessApi.components(),
    get: async templateId => asTemplate(await harnessApi.component(templateId)),
    create: async input => asTemplate(await guarded(harnessApi.createComponent(body(input)))),
    publish: async (templateId, baseRevision, input) =>
      asTemplate(await guarded(harnessApi.publishComponentVersion(templateId, baseRevision, body(input)))),
    assetContentUrl: (templateId, version, assetId) => harnessApi.componentAssetUrl(templateId, version, assetId),
  };
}
