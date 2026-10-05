import { createMutationHeaders, type LocalSession } from "../local-session";
import { buildApiUrl, type RuntimeConfig } from "../runtime-config";
import { parseComponentTemplateContent, type ComponentTemplateContent } from "./template-content";

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
export interface ComponentTemplateDraft {
  readonly templateId: string; readonly baseVersion: number; readonly draftRevision: number;
  readonly code: string; readonly name: string; readonly articleBindings: readonly ArticleBinding[];
  readonly assets: readonly TemplateAsset[]; readonly content: ComponentTemplateContent;
  readonly createdUtc: string; readonly updatedUtc: string;
}
export interface ComponentTemplateDraftBody {
  expectedVersion: number; expectedDraftRevision: number; code: string; name: string;
  articleBindings: ArticleBinding[]; content: ComponentTemplateContent;
}
export interface ComponentTemplateApi {
  list(): Promise<readonly ComponentTemplateSummary[]>;
  get(templateId: string): Promise<ComponentTemplate>;
  getVersion(templateId: string, version: number): Promise<ComponentTemplate>;
  getDraft(templateId: string): Promise<ComponentTemplateDraft | null>;
  saveDraft(templateId: string, body: ComponentTemplateDraftBody): Promise<ComponentTemplateDraft>;
  publishDraft(templateId: string, expectedVersion: number, expectedDraftRevision: number): Promise<ComponentTemplate>;
  create(body: { code: string; name: string; articleBindings: ArticleBinding[]; content: ComponentTemplateContent }): Promise<ComponentTemplate>;
  save(templateId: string, body: { expectedVersion: number; code: string; name: string; articleBindings: ArticleBinding[]; content: ComponentTemplateContent }): Promise<ComponentTemplate>;
  addAsset(templateId: string, body: { expectedVersion: number; fileName: string; mediaType: TemplateAsset["mediaType"]; contentBase64: string }): Promise<ComponentTemplate>;
  removeAsset(templateId: string, assetId: string, expectedVersion: number): Promise<ComponentTemplate>;
  assetContentUrl(templateId: string, version: number, assetId: string): string;
  remove(templateId: string, expectedVersion: number): Promise<void>;
}

type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
const idPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("Сервер вернул повреждённый шаблон компонента.");
  return value as Record<string, unknown>;
}
function stringField(r: Record<string, unknown>, key: string): string {
  if (typeof r[key] !== "string") throw new Error(`Поле ответа «${key}» задано неверно.`);
  return r[key] as string;
}
function integerField(r: Record<string, unknown>, key: string): number {
  if (!Number.isSafeInteger(r[key]) || Number(r[key]) < 0) throw new Error(`Поле ответа «${key}» задано неверно.`);
  return Number(r[key]);
}
function parseBinding(value: unknown): ArticleBinding {
  const r = record(value);
  return Object.freeze({ sourceId: stringField(r, "sourceId"), entityType: stringField(r, "entityType"), articleKey: stringField(r, "articleKey") });
}
function parseAsset(value: unknown): TemplateAsset {
  const r = record(value);
  const assetId = stringField(r, "assetId");
  const sha256 = stringField(r, "sha256");
  const fileName = stringField(r, "fileName");
  const mediaType = stringField(r, "mediaType");
  const sizeBytes = integerField(r, "sizeBytes");
  if (!idPattern.test(assetId) || !/^[0-9a-f]{64}$/.test(sha256) || !fileName.trim() ||
      mediaType !== "image/png") {
    throw new Error("Сервер вернул повреждённое изображение шаблона.");
  }
  return Object.freeze({ assetId, sha256, sizeBytes, fileName, mediaType: mediaType as TemplateAsset["mediaType"] });
}
function parseTemplate(value: unknown): ComponentTemplate {
  const r = record(value);
  const templateId = stringField(r, "templateId");
  if (!idPattern.test(templateId)) throw new Error("Поле ответа «templateId» задано неверно.");
  const bindings = Array.isArray(r.articleBindings) ? r.articleBindings.map(parseBinding) : [];
  const assets = Array.isArray(r.assets) ? r.assets.map(parseAsset) : [];
  const versionSha256 = stringField(r, "versionSha256");
  if (!/^[0-9a-f]{64}$/.test(versionSha256)) throw new Error("Поле ответа «versionSha256» задано неверно.");
  const content = parseComponentTemplateContent(r.content);
  return Object.freeze({ templateId, version: integerField(r, "version"), versionSha256, code: stringField(r, "code"), name: stringField(r, "name"), articleBindings: bindings, assets, content, createdUtc: stringField(r, "createdUtc"), updatedUtc: typeof r.updatedUtc === "string" ? r.updatedUtc : undefined });
}
function parseDraft(value: unknown): ComponentTemplateDraft {
  const r = record(value), templateId = stringField(r, "templateId");
  if (!idPattern.test(templateId)) throw new Error("Поле ответа «templateId» задано неверно.");
  return Object.freeze({
    templateId, baseVersion: integerField(r, "baseVersion"), draftRevision: integerField(r, "draftRevision"),
    code: stringField(r, "code"), name: stringField(r, "name"),
    articleBindings: Array.isArray(r.articleBindings) ? r.articleBindings.map(parseBinding) : [],
    assets: Array.isArray(r.assets) ? r.assets.map(parseAsset) : [],
    content: parseComponentTemplateContent(r.content),
    createdUtc: stringField(r, "createdUtc"), updatedUtc: stringField(r, "updatedUtc"),
  });
}
function parseList(value: unknown): readonly ComponentTemplateSummary[] {
  const r = record(value);
  if (!Array.isArray(r.items)) throw new Error("Сервер вернул повреждённый список шаблонов.");
  return r.items.map(item => {
    const parsed = parseTemplate({ ...record(item), versionSha256: "0".repeat(64), content: { schemaVersion: 1, views: [] } });
    return { templateId: parsed.templateId, version: parsed.version, code: parsed.code, name: parsed.name, articleBindings: parsed.articleBindings, createdUtc: parsed.createdUtc, updatedUtc: parsed.updatedUtc };
  });
}

export function createComponentTemplateApi(config: RuntimeConfig, session: LocalSession, fetcher: Fetcher = fetch): ComponentTemplateApi {
  const headers = createMutationHeaders(session);
  async function request<T>(path: string, init: RequestInit, parse: (value: unknown) => T): Promise<T> {
    let response: Response;
    try { response = await fetcher(buildApiUrl(config, `component-templates${path}`), { credentials: "same-origin", cache: "no-store", ...init }); }
    catch { throw new Error("Не удалось связаться с локальным сервером."); }
    if (!response.ok) {
      let detail = `Сервер не выполнил запрос (HTTP ${response.status}).`;
      try { const body = record(await response.json()); if (body.error === "component_template_version_conflict") detail = `Шаблон уже изменён (актуальная версия ${String(body.currentVersion ?? "?")}). Обновите его перед сохранением.`; else if (body.error === "component_template_draft_conflict") detail = `Черновик открыт в другой вкладке (актуальная ревизия ${String(body.currentVersion ?? "?")}). Обновите шаблон перед сохранением.`; else if (typeof body.message === "string" && body.message.trim()) detail = body.message; } catch { /* status fallback */ }
      throw new Error(detail);
    }
    if (response.status === 204) return undefined as T;
    return parse(await response.json());
  }
  const api: ComponentTemplateApi = {
    list: () => request("", { method: "GET", headers: { Accept: "application/json" } }, parseList),
    get: id => request(`/${encodeURIComponent(id)}`, { method: "GET", headers: { Accept: "application/json" } }, parseTemplate),
    getVersion: (id, version) => {
      if (!Number.isSafeInteger(version) || version < 1) throw new Error("Версия шаблона задана неверно.");
      return request(`/${encodeURIComponent(id)}/versions/${version}`, { method: "GET", headers: { Accept: "application/json" } }, parseTemplate);
    },
    getDraft: id => request(`/${encodeURIComponent(id)}/draft`, { method: "GET", headers: { Accept: "application/json" } }, parseDraft).then(value => value ?? null),
    saveDraft: (id, body) => request(`/${encodeURIComponent(id)}/draft`, { method: "PUT", headers, body: JSON.stringify(body) }, parseDraft),
    publishDraft: (id, expectedVersion, expectedDraftRevision) => request(`/${encodeURIComponent(id)}/draft/publish`, { method: "POST", headers, body: JSON.stringify({ expectedVersion, expectedDraftRevision }) }, parseTemplate),
    create: body => request("", { method: "POST", headers, body: JSON.stringify(body) }, parseTemplate),
    save: (id, body) => request(`/${encodeURIComponent(id)}`, { method: "PUT", headers, body: JSON.stringify(body) }, parseTemplate),
    addAsset: (id, body) => request(`/${encodeURIComponent(id)}/assets`, { method: "POST", headers, body: JSON.stringify(body) }, parseTemplate),
    removeAsset: (id, assetId, expectedVersion) => request(`/${encodeURIComponent(id)}/assets/${encodeURIComponent(assetId)}`, { method: "DELETE", headers, body: JSON.stringify({ expectedVersion }) }, parseTemplate),
    assetContentUrl: (id, version, assetId) => buildApiUrl(config, `component-templates/${encodeURIComponent(id)}/versions/${version}/assets/${encodeURIComponent(assetId)}/content`),
    remove: async (id, expectedVersion) => { await request(`/${encodeURIComponent(id)}`, { method: "DELETE", headers, body: JSON.stringify({ expectedVersion }) }, () => undefined); },
  };
  return Object.freeze(api);
}
