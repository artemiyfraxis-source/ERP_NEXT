// Хранилище чертежа жгута в ERP для редактора: `HarnessDesignApi` поверх `kitsetsApi.harness / saveHarness`.
// Редактор считает версию своим счётчиком (`revision`), сервер — номером версии чертежа: адаптер держит серверный номер
// у себя и с ним сохраняет; чужое сохранение даёт 409 — адаптер переходит в «конфликт» и больше ничего не пишет.
// Библиотечные компоненты: шаблоны — в библиотеке ERP (`harnessApi`, миграция 0091), соединитель закрепляет шаблон и
// версию сам; разместить — сохранить чертёж с ним (сервер проверит закрепление по библиотеке).
// `readOnly` — просмотр: сохранения и размещения отклоняются, не доходя до сервера.
import { ApiError, harnessApi, kitsetsApi, type HarnessDrawing, type HarnessPlacementGraph } from "../api";
import type { ComponentTemplateApi } from "./component-library/component-template-api";
import type { ComponentPlacementApi, ComponentPlacementResult } from "./editor/component-placement-api";
import {
  HarnessDesignApiError,
  parseRecoverableHarnessDesignContent,
  type HarnessDesignApi,
  type HarnessDesignResource,
} from "./editor/design-api";
import { createEmptyHarnessDesign, type HarnessDesignDocument } from "./editor/model";
import { placementGraphOf } from "./library/library";

export type ServerSaveState = "saved" | "saving" | "conflict" | "error";

export interface ServerHarnessClient {
  harness: (project: string, index: string) => Promise<HarnessDrawing>;
  saveHarness: (project: string, index: string, design: Record<string, unknown>, revision: number | null) =>
    Promise<HarnessDrawing & { created: boolean }>;
  placements: (project: string, index: string) => Promise<HarnessPlacementGraph>;
}

export interface ServerHarnessDesignApi extends HarnessDesignApi {
  /** Сервер ответил 409: дальше сохранения не уходят, пока чертёж не перечитан. */
  readonly conflicted: boolean;
  /** Номер версии чертежа на сервере, с которым адаптер сохраняет (null — чертежа ещё нет). */
  readonly serverRevision: number | null;
  /** Размещения библиотечных компонентов этого же чертежа (общая с сохранением версия). */
  componentPlacements(templates: ComponentTemplateApi): ComponentPlacementApi;
}

export const CONFLICT_CODE = "design_revision_conflict";
export const READ_ONLY_CODE = "read_only";
export const READ_ONLY_TEXT = "Чертёж открыт только для просмотра — изменения не сохраняются";

const defaultClient: ServerHarnessClient = {
  harness: kitsetsApi.harness, saveHarness: kitsetsApi.saveHarness, placements: harnessApi.placements,
};

export function createServerHarnessDesignApi(
  options: {
    onState?: (state: ServerSaveState, message?: string) => void; client?: ServerHarnessClient; now?: () => string;
    readOnly?: boolean;
  } = {},
): ServerHarnessDesignApi {
  const client = options.client ?? defaultClient;
  const now = options.now ?? (() => new Date().toISOString());
  const emit = options.onState ?? (() => undefined);
  const readOnly = options.readOnly === true;
  let stored: number | null = null;
  let revision = 0;
  let conflicted = false;

  const resource = (harnessId: string, content: HarnessDesignDocument, warning: string | null): HarnessDesignResource => ({
    harnessId, schemaVersion: 1, revision, content, updatedUtc: now(),
    ...(warning ? { recoveryWarning: warning } : {}),
  });
  const fromDrawing = (harnessId: string, drawing: HarnessDrawing): HarnessDesignResource => {
    stored = drawing.revision;
    if (!drawing.design) return resource(harnessId, createEmptyHarnessDesign(), null);
    const parsed = parseRecoverableHarnessDesignContent(drawing.design);
    return resource(harnessId, parsed.content, parsed.warning);
  };
  const conflict = (text: string) => new HarnessDesignApiError(text, CONFLICT_CODE, revision + 1);
  const refuseReadOnly = () => new HarnessDesignApiError(READ_ONLY_TEXT, READ_ONLY_CODE, null);
  // Запись на сервер с версией последнего чтения; 409 — «конфликт», дальше ничего не пишется.
  const write = async (projectId: string, harnessId: string, design: HarnessDesignDocument) => {
    emit("saving");
    try {
      const saved = await client.saveHarness(projectId, harnessId, JSON.parse(JSON.stringify(design)), stored);
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
  const refuseStale = (expectedRevision: number) => {
    if (conflicted || expectedRevision !== revision) {
      conflicted = true;
      emit("conflict");
      throw conflict("Чертёж изменили — обновите страницу");
    }
  };

  const api: ServerHarnessDesignApi = {
    get conflicted() { return conflicted; },
    get serverRevision() { return stored; },
    async get(projectId, harnessId) {
      const drawing = await client.harness(projectId, harnessId);
      conflicted = false;
      // Тот же чертёж, что уже читали или сами сохранили (после размещения компонента редактор перечитывает его и
      // сверяет номер), — номер прежний; другой — следующий.
      if (drawing.revision !== stored) revision += 1;
      const loaded = fromDrawing(harnessId, drawing);
      emit("saved");
      return loaded;
    },
    async save(projectId, harnessId, expectedRevision, content) {
      if (readOnly) throw refuseReadOnly();
      refuseStale(expectedRevision);
      const result = fromDrawing(harnessId, await write(projectId, harnessId, content));
      emit("saved");
      return result;
    },
    componentPlacements(templates) {
      const commands = new Map<string, ComponentPlacementResult>();
      const sources = new Map<string, { templateId: string; version: number }>();
      return {
        async list(projectId, harnessId) {
          const graph = placementGraphOf(await client.placements(projectId, harnessId), projectId, harnessId);
          for (const snapshot of graph.snapshots) {
            sources.set(snapshot.snapshotId, { templateId: snapshot.sourceTemplateId, version: snapshot.sourceVersion });
          }
          return graph;
        },
        assetContentUrl(_projectId, _harnessId, snapshotId, assetId) {
          const source = sources.get(snapshotId);
          return source ? templates.assetContentUrl(source.templateId, source.version, assetId) : "";
        },
        async place(projectId, harnessId, body, document) {
          const done = commands.get(body.commandId);
          if (done) return done;
          if (readOnly) throw refuseReadOnly();
          refuseStale(body.expectedRevision);
          const binding = body.instance.libraryBinding;
          if (binding?.mode !== "template" || binding.templateId !== body.sourceTemplateId ||
              binding.templateVersion !== body.sourceVersion) {
            throw new HarnessDesignApiError("Размещение не совпадает с привязкой компонента.", "placement_invalid", null);
          }
          if (!document.connectors.some((connector) => connector.id === body.placementId)) {
            throw new HarnessDesignApiError("Компонента нет в сохраняемом чертеже.", "placement_invalid", null);
          }
          const saved = await write(projectId, harnessId, document);
          stored = saved.revision;
          emit("saved");
          const result = { commandId: body.commandId, resultingRevision: revision, snapshotId: "" };
          commands.set(body.commandId, result);
          return result;
        },
      };
    },
  };
  return api;
}
