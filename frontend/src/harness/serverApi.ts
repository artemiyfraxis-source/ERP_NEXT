// Хранилище чертежа жгута в ERP для редактора: `HarnessDesignApi` поверх `kitsetsApi.harness / saveHarness`.
// Редактор считает версию числом (`revision`), сервер — хэшем содержимого: адаптер держит хэш у себя и по нему
// сохраняет; чужое сохранение даёт 409 — адаптер переходит в «конфликт» и больше ничего не пишет.
// Библиотечные компоненты (закреплённый снимок шаблона, по которому рисуется соединитель) хранятся в том же документе
// КД — `componentSnapshots` / `componentPlacements` (как в экспорте TECHMAP-GRAPHER, только этого жгута): размещение
// пишет их вместе с соединителем одним сохранением, обычное автосохранение их не трогает (сервер переносит прежние).
// `readOnly` — просмотр: сохранения и размещения отклоняются, не доходя до сервера.
import { ApiError, kitsetsApi, type HarnessDrawing } from "../api";
import type { ComponentTemplateApi } from "./component-library/component-template-api";
import {
  parseComponentPlacementGraph,
  type ComponentPlacementApi,
  type ComponentPlacementResult,
  type ProjectComponentPlacementGraph,
} from "./editor/component-placement-api";
import {
  HarnessDesignApiError,
  parseRecoverableHarnessDesignContent,
  type HarnessDesignApi,
  type HarnessDesignResource,
} from "./editor/design-api";
import { createEmptyHarnessDesign, type HarnessDesignDocument } from "./editor/model";

export type ServerSaveState = "saved" | "saving" | "conflict" | "error";

export interface ServerHarnessClient {
  harness: (project: string, index: string) => Promise<HarnessDrawing>;
  saveHarness: (project: string, index: string, design: Record<string, unknown>, version: string | null) =>
    Promise<HarnessDrawing & { created: boolean }>;
}

export interface ServerHarnessDesignApi extends HarnessDesignApi {
  /** Сервер ответил 409: дальше сохранения не уходят, пока чертёж не перечитан. */
  readonly conflicted: boolean;
  /** Размещения библиотечных компонентов этого же чертежа (общая с сохранением версия). */
  componentPlacements(templates: ComponentTemplateApi): ComponentPlacementApi;
}

export const CONFLICT_CODE = "design_revision_conflict";
export const READ_ONLY_CODE = "read_only";
export const READ_ONLY_TEXT = "Чертёж открыт только для просмотра — изменения не сохраняются";
export const COMPONENT_SNAPSHOTS = "componentSnapshots";
export const COMPONENT_PLACEMENTS = "componentPlacements";

type Row = Record<string, unknown>;
const rows = (value: unknown): Row[] =>
  Array.isArray(value) ? value.filter((item): item is Row => typeof item === "object" && item !== null && !Array.isArray(item)) : [];

/** Библиотечные компоненты жгута из документа КД: размещения этого жгута и снимки, которые они закрепили. Жгут и
 *  проект — текущие (в импорте из TECHMAP-GRAPHER у размещений свой id жгута). */
export function componentGraphOf(design: Row | null, projectId: string, harnessId: string): ProjectComponentPlacementGraph {
  const placements: Row[] = rows(design?.[COMPONENT_PLACEMENTS]).map((item) => ({ ...item, harnessId }));
  const used = new Set(placements.map((item) => item.snapshotId));
  const snapshots = rows(design?.[COMPONENT_SNAPSHOTS]).filter((item) => used.has(item.snapshotId))
    .map((item) => ({ ...item, projectId }));
  return parseComponentPlacementGraph({ placements, snapshots }, projectId, harnessId);
}

export function createServerHarnessDesignApi(
  options: {
    onState?: (state: ServerSaveState, message?: string) => void; client?: ServerHarnessClient; now?: () => string;
    newId?: () => string; readOnly?: boolean;
  } = {},
): ServerHarnessDesignApi {
  const client = options.client ?? kitsetsApi;
  const now = options.now ?? (() => new Date().toISOString());
  const newId = options.newId ?? (() => crypto.randomUUID());
  const emit = options.onState ?? (() => undefined);
  const readOnly = options.readOnly === true;
  let version: string | null = null;
  let revision = 0;
  let conflicted = false;

  const resource = (harnessId: string, content: HarnessDesignDocument, warning: string | null): HarnessDesignResource => ({
    harnessId, schemaVersion: 1, revision, content, updatedUtc: now(),
    ...(warning ? { recoveryWarning: warning } : {}),
  });
  const fromDrawing = (harnessId: string, drawing: HarnessDrawing): HarnessDesignResource => {
    version = drawing.version;
    if (!drawing.design) return resource(harnessId, createEmptyHarnessDesign(), null);
    const parsed = parseRecoverableHarnessDesignContent(drawing.design);
    return resource(harnessId, parsed.content, parsed.warning);
  };
  const conflict = (text: string) => new HarnessDesignApiError(text, CONFLICT_CODE, revision + 1);
  const refuseReadOnly = () => new HarnessDesignApiError(READ_ONLY_TEXT, READ_ONLY_CODE, null);
  // Запись на сервер с версией последнего чтения; 409 — «конфликт», дальше ничего не пишется.
  const write = async (projectId: string, harnessId: string, design: Row) => {
    emit("saving");
    try {
      const saved = await client.saveHarness(projectId, harnessId, design, version);
      revision += 1;
      return saved;
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        conflicted = true;
        emit("conflict", reason.message);
        throw conflict(reason.message);
      }
      emit("error", reason instanceof Error ? reason.message : undefined);
      throw reason;
    }
  };

  const api: ServerHarnessDesignApi = {
    get conflicted() { return conflicted; },
    async get(projectId, harnessId) {
      const drawing = await client.harness(projectId, harnessId);
      conflicted = false;
      // Тот же чертёж, что уже читали или сами сохранили (после размещения компонента редактор перечитывает его и
      // сверяет номер), — номер прежний; другой — следующий.
      if (drawing.version !== version) revision += 1;
      const loaded = fromDrawing(harnessId, drawing);
      emit("saved");
      return loaded;
    },
    async save(projectId, harnessId, expectedRevision, content) {
      if (readOnly) throw refuseReadOnly();
      if (conflicted || expectedRevision !== revision) {
        conflicted = true;
        emit("conflict");
        throw conflict("Чертёж изменили — обновите страницу");
      }
      const saved = await write(projectId, harnessId, JSON.parse(JSON.stringify(content)));
      const result = fromDrawing(harnessId, saved);
      emit("saved");
      return result;
    },
    componentPlacements(templates) {
      const commands = new Map<string, ComponentPlacementResult>();
      const sources = new Map<string, { templateId: string; version: number }>();
      return {
        async list(projectId, harnessId) {
          const drawing = await client.harness(projectId, harnessId);
          const graph = componentGraphOf(drawing.design, projectId, harnessId);
          for (const snapshot of graph.snapshots) {
            sources.set(snapshot.snapshotId, { templateId: snapshot.sourceTemplateId, version: snapshot.sourceVersion });
          }
          return graph;
        },
        assetContentUrl(_projectId, _harnessId, snapshotId, assetId) {
          // Изображения снимков в ERP не хранятся: берутся из библиотеки, пока шаблон в ней есть.
          const source = sources.get(snapshotId);
          return source ? templates.assetContentUrl(source.templateId, source.version, assetId) : "";
        },
        async place(projectId, harnessId, body) {
          const done = commands.get(body.commandId);
          if (done) return done;
          if (readOnly) throw refuseReadOnly();
          if (conflicted || body.expectedRevision !== revision) {
            conflicted = true;
            emit("conflict");
            throw conflict("Чертёж изменили — обновите страницу");
          }
          const binding = body.instance.libraryBinding;
          if (binding?.mode !== "template" || binding.templateId !== body.sourceTemplateId ||
              binding.templateVersion !== body.sourceVersion) {
            throw new HarnessDesignApiError("Размещение не совпадает с привязкой компонента.", "placement_invalid", null);
          }
          const template = await templates.getVersion(body.sourceTemplateId, body.sourceVersion);
          if (template.versionSha256 !== binding.versionSha256) {
            throw new HarnessDesignApiError("Версия библиотечного компонента изменилась.", "placement_invalid", null);
          }
          const drawing = await client.harness(projectId, harnessId);
          if (drawing.version !== version) {
            conflicted = true;
            emit("conflict");
            throw conflict("Чертёж изменили — обновите страницу");
          }
          const design: Row = drawing.design ?? JSON.parse(JSON.stringify(createEmptyHarnessDesign()));
          const connectors = rows(design.connectors);
          if (connectors.some((connector) => connector.id === body.placementId)) {
            throw new HarnessDesignApiError("Компонент уже размещён в жгуте.", "placement_duplicate", null);
          }
          const timestamp = now();
          const snapshotId = newId();
          const snapshot = {
            snapshotId, projectId,
            sourceTemplateId: template.templateId, sourceVersion: template.version, sourceVersionSha256: template.versionSha256,
            code: template.code, name: template.name, articleBindings: template.articleBindings, assets: template.assets,
            schemaVersion: template.content.schemaVersion, content: template.content,
            createdUtc: timestamp, updatedUtc: timestamp,
          };
          const placement = {
            placementId: body.placementId, harnessId, snapshotId,
            sourceId: body.sourceId, entityType: body.entityType, articleKey: body.articleKey,
            instance: body.instance, createdUtc: timestamp, updatedUtc: timestamp,
          };
          const next: Row = JSON.parse(JSON.stringify({
            ...design,
            connectors: [...connectors, body.instance],
            [COMPONENT_SNAPSHOTS]: [...rows(design[COMPONENT_SNAPSHOTS]), snapshot],
            [COMPONENT_PLACEMENTS]: [...rows(design[COMPONENT_PLACEMENTS]), placement],
          }));
          const saved = await write(projectId, harnessId, next);
          version = saved.version;
          sources.set(snapshotId, { templateId: template.templateId, version: template.version });
          emit("saved");
          const result = { commandId: body.commandId, resultingRevision: revision, snapshotId };
          commands.set(body.commandId, result);
          return result;
        },
      };
    },
  };
  return api;
}
