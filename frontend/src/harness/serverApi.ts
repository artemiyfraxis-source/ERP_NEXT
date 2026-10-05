// Хранилище чертежа жгута в ERP для редактора: `HarnessDesignApi` поверх `kitsetsApi.harness / saveHarness`.
// Редактор считает версию числом (`revision`), сервер — хэшем содержимого: адаптер держит хэш у себя и по нему
// сохраняет; чужое сохранение даёт 409 — адаптер переходит в «конфликт» и больше ничего не пишет.
import { ApiError, kitsetsApi, type HarnessDrawing } from "../api";
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
}

export const CONFLICT_CODE = "design_revision_conflict";

export function createServerHarnessDesignApi(
  options: { onState?: (state: ServerSaveState, message?: string) => void; client?: ServerHarnessClient; now?: () => string } = {},
): ServerHarnessDesignApi {
  const client = options.client ?? kitsetsApi;
  const now = options.now ?? (() => new Date().toISOString());
  const emit = options.onState ?? (() => undefined);
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

  return {
    get conflicted() { return conflicted; },
    async get(projectId, harnessId) {
      const drawing = await client.harness(projectId, harnessId);
      conflicted = false;
      revision = drawing.document ? 1 : 0;
      const loaded = fromDrawing(harnessId, drawing);
      emit("saved");
      return loaded;
    },
    async save(projectId, harnessId, expectedRevision, content) {
      const conflict = (text: string) => new HarnessDesignApiError(text, CONFLICT_CODE, revision + 1);
      if (conflicted || expectedRevision !== revision) {
        conflicted = true;
        emit("conflict");
        throw conflict("Чертёж изменили — обновите страницу");
      }
      emit("saving");
      try {
        const saved = await client.saveHarness(projectId, harnessId, JSON.parse(JSON.stringify(content)), version);
        revision += 1;
        const result = fromDrawing(harnessId, saved);
        emit("saved");
        return result;
      } catch (reason) {
        if (reason instanceof ApiError && reason.status === 409) {
          conflicted = true;
          emit("conflict", reason.message);
          throw conflict(reason.message);
        }
        emit("error", reason instanceof Error ? reason.message : undefined);
        throw reason;
      }
    },
  };
}
