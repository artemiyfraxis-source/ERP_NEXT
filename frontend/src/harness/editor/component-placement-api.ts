import { createMutationHeaders, type LocalSession } from "../local-session";
import { buildApiUrl, type RuntimeConfig } from "../runtime-config";
import {
  parseComponentTemplateContent,
  type ComponentTemplateContent,
} from "../component-library/template-content";
import { HarnessDesignApiError } from "./design-api";
import type { ConnectorInstance } from "./model";
import { validateConnectorLibraryMetadata } from "./model";

/** Derive the request from the same immutable binding that produced the preview. */
export function componentPlacementRequest(
  instance: ConnectorInstance, expectedRevision: number, commandId: string,
): PlaceComponentRequest {
  const binding = instance.libraryBinding;
  if (binding?.mode !== "template") throw new Error("Нет привязки к библиотечному шаблону.");
  if (binding.contactNumbering !== "source-v1") throw new Error("Перед размещением проверьте исходные номера контактов в библиотеке.");
  validateConnectorLibraryMetadata(instance);
  return {
    commandId, expectedRevision, placementId: instance.id,
    sourceTemplateId: binding.templateId, sourceVersion: binding.templateVersion,
    ...binding.article, instance,
  };
}

export interface PlaceComponentRequest {
  readonly commandId: string;
  readonly expectedRevision: number;
  readonly placementId: string;
  readonly sourceTemplateId: string;
  readonly sourceVersion: number;
  readonly sourceId: string;
  readonly entityType: string;
  readonly articleKey: string;
  readonly instance: ConnectorInstance;
}

export interface ComponentPlacementResult {
  readonly commandId: string;
  readonly resultingRevision: number;
  readonly snapshotId: string;
}

export interface ProjectComponentArticleKey {
  readonly sourceId: string;
  readonly entityType: string;
  readonly articleKey: string;
}

export interface ProjectComponentAsset {
  readonly assetId: string;
  readonly sha256: string;
  readonly sizeBytes: number;
  readonly fileName: string;
  readonly mediaType: "image/png";
}

export interface ProjectComponentSnapshotResource {
  readonly snapshotId: string;
  readonly projectId: string;
  readonly sourceTemplateId: string;
  readonly sourceVersion: number;
  readonly sourceVersionSha256: string;
  readonly code: string;
  readonly name: string;
  readonly articleBindings: readonly ProjectComponentArticleKey[];
  readonly assets: readonly ProjectComponentAsset[];
  readonly schemaVersion: number;
  readonly content: ComponentTemplateContent;
  readonly createdUtc: string;
  readonly updatedUtc: string;
}

export interface ProjectComponentPlacementResource {
  readonly placementId: string;
  readonly harnessId: string;
  readonly snapshotId: string;
  readonly sourceId: string;
  readonly entityType: string;
  readonly articleKey: string;
  readonly instance: Readonly<Record<string, unknown>>;
  readonly createdUtc: string;
  readonly updatedUtc: string;
}

export interface ProjectComponentPlacementGraph {
  readonly placements: readonly ProjectComponentPlacementResource[];
  readonly snapshots: readonly ProjectComponentSnapshotResource[];
}

const idPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export function createComponentPlacementApi(
  config: RuntimeConfig,
  session: LocalSession,
  fetcher: Fetcher = fetch,
) {
  const headers = createMutationHeaders(session);
  return {
    async list(projectId: string, harnessId: string): Promise<ProjectComponentPlacementGraph> {
      let response: Response;
      try {
        response = await fetcher(componentPlacementsUrl(config, projectId, harnessId), {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          headers: { Accept: "application/json" },
        });
      } catch {
        throw new HarnessDesignApiError("Не удалось связаться с локальным сервером.", null, null);
      }
      if (!response.ok) throw await responseError(response);
      return parseComponentPlacementGraph(await readResponseObject(response, true), projectId, harnessId);
    },
    assetContentUrl(
      projectId: string,
      harnessId: string,
      snapshotId: string,
      assetId: string,
    ): string {
      for (const [value, name] of [[snapshotId, "ID снимка"], [assetId, "ID изображения"]] as const) {
        if (!idPattern.test(value)) throw new Error(`${name} задан неверно.`);
      }
      return `${componentPlacementsUrl(config, projectId, harnessId)}/snapshots/${encodeURIComponent(snapshotId)}` +
        `/assets/${encodeURIComponent(assetId)}/content`;
    },
    async place(projectId: string, harnessId: string, body: PlaceComponentRequest): Promise<ComponentPlacementResult> {
      let response: Response;
      try {
        response = await fetcher(componentPlacementsUrl(config, projectId, harnessId), {
          method: "POST",
          credentials: "same-origin",
          cache: "no-store",
          headers,
          body: JSON.stringify(body),
        });
      } catch {
        throw new HarnessDesignApiError("Не удалось связаться с локальным сервером.", null, null);
      }
      if (!response.ok) {
        throw await responseError(response);
      }
      return parseComponentPlacementResult(await readResponseObject(response, true), body, projectId, harnessId);
    },
  };
}

function componentPlacementsUrl(config: RuntimeConfig, projectId: string, harnessId: string): string {
  return buildApiUrl(config,
    `projects/${encodeURIComponent(projectId)}/harnesses/${encodeURIComponent(harnessId)}/component-placements`);
}

async function responseError(response: Response): Promise<HarnessDesignApiError> {
  const value = await readResponseObject(response, false);
  const code = typeof value?.error === "string" ? value.error : null;
  const currentRevision = value && Number.isSafeInteger(value.currentRevision) && Number(value.currentRevision) >= 0
    ? Number(value.currentRevision) : null;
  const message = code === "design_revision_conflict"
    ? "Документ изменился после открытия. Перезагрузите жгут перед повторным размещением."
    : typeof value?.message === "string" && value.message.trim()
      ? value.message : `Сервер не выполнил запрос (HTTP ${response.status}).`;
  return new HarnessDesignApiError(message, code, currentRevision);
}

export function parseComponentPlacementGraph(
  value: unknown,
  projectId: string,
  harnessId: string,
): ProjectComponentPlacementGraph {
  const envelope = requireRecord(value);
  if (!Array.isArray(envelope.placements) || !Array.isArray(envelope.snapshots)) {
    throw new Error("Сервер вернул повреждённый список библиотечных компонентов.");
  }
  const snapshots = envelope.snapshots.map(candidate => parseSnapshot(candidate, projectId));
  const snapshotIds = new Set(snapshots.map(snapshot => snapshot.snapshotId));
  if (snapshotIds.size !== snapshots.length) {
    throw new Error("Сервер вернул повторяющиеся снимки библиотечных компонентов.");
  }
  const placements = envelope.placements.map(candidate => parsePlacement(candidate, harnessId, snapshotIds));
  if (new Set(placements.map(placement => placement.placementId)).size !== placements.length) {
    throw new Error("Сервер вернул повторяющиеся размещения библиотечных компонентов.");
  }
  if (new Set(placements.map(placement => placement.snapshotId)).size !== snapshots.length) {
    throw new Error("Сервер вернул снимок, который не используется этим жгутом.");
  }
  return Object.freeze({ placements: Object.freeze(placements), snapshots: Object.freeze(snapshots) });
}

function parseSnapshot(value: unknown, projectId: string): ProjectComponentSnapshotResource {
  const item = requireRecord(value);
  const snapshotId = uuidField(item, "snapshotId");
  const responseProjectId = stringField(item, "projectId");
  if (responseProjectId !== projectId) throw new Error("Снимок принадлежит другому проекту.");
  const schemaVersion = positiveIntegerField(item, "schemaVersion");
  const content = parseComponentTemplateContent(item.content);
  if (content.schemaVersion !== schemaVersion) throw new Error("Версия содержимого снимка не совпадает с контрактом.");
  const bindings = arrayField(item, "articleBindings").map(parseArticleKey);
  const assets = arrayField(item, "assets").map(parseAsset);
  requireUnique(bindings.map(articleIdentity), "Артикулы снимка должны быть уникальны.");
  requireUnique(assets.map(asset => asset.assetId), "Изображения снимка должны быть уникальны.");
  return Object.freeze({
    snapshotId,
    projectId: responseProjectId,
    sourceTemplateId: uuidField(item, "sourceTemplateId"),
    sourceVersion: positiveIntegerField(item, "sourceVersion"),
    sourceVersionSha256: sha256Field(item, "sourceVersionSha256"),
    code: stringField(item, "code"),
    name: stringField(item, "name"),
    articleBindings: Object.freeze(bindings),
    assets: Object.freeze(assets),
    schemaVersion,
    content,
    createdUtc: stringField(item, "createdUtc"),
    updatedUtc: stringField(item, "updatedUtc"),
  });
}

function parsePlacement(
  value: unknown,
  harnessId: string,
  snapshotIds: ReadonlySet<string>,
): ProjectComponentPlacementResource {
  const item = requireRecord(value);
  const placementId = uuidField(item, "placementId");
  if (stringField(item, "harnessId") !== harnessId) throw new Error("Размещение принадлежит другому жгуту.");
  const snapshotId = uuidField(item, "snapshotId");
  if (!snapshotIds.has(snapshotId)) throw new Error("Для размещения отсутствует закреплённый снимок.");
  const instance = requireRecord(item.instance);
  if (stringField(instance, "id") !== placementId) throw new Error("ID экземпляра не совпадает с размещением.");
  return Object.freeze({
    placementId,
    harnessId,
    snapshotId,
    sourceId: stringField(item, "sourceId"),
    entityType: stringField(item, "entityType"),
    articleKey: stringField(item, "articleKey"),
    instance: Object.freeze(instance),
    createdUtc: stringField(item, "createdUtc"),
    updatedUtc: stringField(item, "updatedUtc"),
  });
}

function parseArticleKey(value: unknown): ProjectComponentArticleKey {
  const item = requireRecord(value);
  return Object.freeze({
    sourceId: stringField(item, "sourceId"),
    entityType: stringField(item, "entityType"),
    articleKey: stringField(item, "articleKey"),
  });
}

function parseAsset(value: unknown): ProjectComponentAsset {
  const item = requireRecord(value);
  const mediaType = stringField(item, "mediaType");
  if (mediaType !== "image/png") throw new Error("Тип изображения снимка не поддерживается.");
  return Object.freeze({
    assetId: uuidField(item, "assetId"),
    sha256: sha256Field(item, "sha256"),
    sizeBytes: nonNegativeIntegerField(item, "sizeBytes"),
    fileName: stringField(item, "fileName"),
    mediaType,
  });
}

function arrayField(value: Record<string, unknown>, name: string): unknown[] {
  if (!Array.isArray(value[name])) throw new Error(`Поле ответа «${name}» задано неверно.`);
  return value[name];
}

function uuidField(value: Record<string, unknown>, name: string): string {
  const result = stringField(value, name);
  if (!idPattern.test(result)) throw new Error(`Поле ответа «${name}» задано неверно.`);
  return result;
}

function sha256Field(value: Record<string, unknown>, name: string): string {
  const result = stringField(value, name);
  if (!/^[0-9a-f]{64}$/.test(result)) throw new Error(`Поле ответа «${name}» задано неверно.`);
  return result;
}

function positiveIntegerField(value: Record<string, unknown>, name: string): number {
  const result = nonNegativeIntegerField(value, name);
  if (result < 1) throw new Error(`Поле ответа «${name}» задано неверно.`);
  return result;
}

function nonNegativeIntegerField(value: Record<string, unknown>, name: string): number {
  if (!Number.isSafeInteger(value[name]) || Number(value[name]) < 0) {
    throw new Error(`Поле ответа «${name}» задано неверно.`);
  }
  return Number(value[name]);
}

function articleIdentity(value: ProjectComponentArticleKey): string {
  return `${value.sourceId}\0${value.entityType}\0${value.articleKey}`;
}

function requireUnique(values: readonly string[], message: string): void {
  if (new Set(values).size !== values.length) throw new Error(message);
}

/**
 * Decode the placement envelope at the trust boundary. Placement is an atomic
 * command, so accepting a partial or stale response would leave the editor's
 * in-memory history out of sync with the server. Keep this parser exported for
 * focused contract tests without exposing the transport implementation.
 */
export function parseComponentPlacementResult(
  value: unknown,
  request: Pick<PlaceComponentRequest, "commandId" | "expectedRevision" | "placementId">,
  projectId?: string,
  harnessId?: string,
): ComponentPlacementResult {
  const envelope = requireRecord(value);
  const commandId = requireString(envelope.commandId, "commandId");
  const expectedRevision = requireNonNegativeInteger(envelope.expectedRevision, "expectedRevision");
  const resultingRevision = requireNonNegativeInteger(envelope.resultingRevision, "resultingRevision");
  const snapshot = requireRecord(envelope.snapshot);
  const snapshotId = requireString(snapshot.snapshotId, "snapshot.snapshotId");
  const placement = requireRecord(envelope.placement);
  const placementId = requireString(placement.placementId, "placement.placementId");
  const placementSnapshotId = requireString(placement.snapshotId, "placement.snapshotId");
  const instance = requireRecord(placement.instance);
  const instanceId = requireString(instance.id, "placement.instance.id");
  if (!idPattern.test(commandId) || commandId !== request.commandId ||
      expectedRevision !== request.expectedRevision ||
      resultingRevision !== request.expectedRevision + 1 ||
      !idPattern.test(snapshotId) || !idPattern.test(placementId) ||
      placementId !== request.placementId || placementSnapshotId !== snapshotId ||
      instanceId !== placementId ||
      (projectId !== undefined && snapshot.projectId !== projectId) ||
      (harnessId !== undefined && placement.harnessId !== harnessId)) {
    throw new Error("Сервер вернул повреждённый результат размещения компонента.");
  }
  return { commandId, resultingRevision, snapshotId };
}

async function readResponseObject(response: Response, required: boolean): Promise<Record<string, unknown>> {
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    if (!required) return {};
    throw new Error("Сервер вернул пустой или нечитаемый результат размещения компонента.");
  }
  if (!required && (value === null || typeof value !== "object" || Array.isArray(value))) return {};
  return requireRecord(value);
}

function requireRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Сервер вернул повреждённый результат размещения компонента.");
  }
  return value as Record<string, unknown>;
}

function stringField(value: Record<string, unknown>, name: string): string {
  return requireString(value[name], name);
}

function requireString(value: unknown, name: string): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`Поле ответа «${name}» задано неверно.`);
  return value;
}

function requireNonNegativeInteger(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0) throw new Error(`Поле ответа «${name}» задано неверно.`);
  return Number(value);
}
