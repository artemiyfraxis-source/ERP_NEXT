// Чертёж жгута позиции для редактора: чтение и сохранение (`harnessApi.drawing / save`) и библиотечные соединители
// (`harnessApi.placements`). Номер версии редактора — номер версии чертежа на сервере (0 — чертежа ещё нет или он старше
// истории). Чужое сохранение — 409: дальше ничего не пишется, а то, что редактор пытался сохранить, остаётся
// (`mine`) — страница предлагает «Взять их версию» или «Сохранить мою поверх».
import { ApiError, harnessApi, type HarnessDrawing, type HarnessPlacementGraph } from "../api";
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

export type DrawingSaveState = "saved" | "saving" | "conflict" | "error";

export const CONFLICT_CODE = "design_revision_conflict";
export const READ_ONLY_CODE = "read_only";
/** What the editor shows on a 409: the page above it offers «Взять их версию» / «Сохранить мою поверх». */
export const CONFLICT_TEXT = "Чертёж сохранили в другом окне — правки не сохранены, выберите действие над чертежом";
export const READ_ONLY_TEXT = "Чертёж открыт только для просмотра — изменения не сохраняются";

/** What the drawing needs from the server (`harnessApi`; a test passes its own). */
export interface DrawingClient {
  drawing: (project: string, index: string) => Promise<HarnessDrawing>;
  save: (project: string, index: string, design: Record<string, unknown>, revision: number | null) => Promise<HarnessDrawing>;
  placements: (project: string, index: string) => Promise<HarnessPlacementGraph>;
}

export interface HarnessDrawingPorts {
  readonly api: HarnessDesignApi;
  readonly placements: ComponentPlacementApi;
  /** The revision on the server the editor works on (null — no drawing yet). */
  readonly revision: () => number | null;
  /** After a 409: the drawing as the editor last tried to save it (the user's edits); null — nothing unsaved. */
  readonly mine: () => HarnessDesignDocument | null;
}

export function harnessDrawing(options: {
  templates: ComponentTemplateApi; readOnly?: boolean; client?: DrawingClient;
  onState?: (state: DrawingSaveState, message?: string) => void; now?: () => string;
}): HarnessDrawingPorts {
  const client = options.client ?? harnessApi;
  const emit = options.onState ?? (() => undefined);
  const now = options.now ?? (() => new Date().toISOString());
  const readOnly = options.readOnly === true;
  let revision = 0;
  let exists = false;
  let conflicted = false;
  let mine: HarnessDesignDocument | null = null;

  const resource = (harnessId: string, drawing: HarnessDrawing): HarnessDesignResource => {
    revision = drawing.revision ?? 0;
    exists = drawing.document !== null;
    if (!drawing.design) return { harnessId, schemaVersion: 1, revision, content: createEmptyHarnessDesign(), updatedUtc: now() };
    const parsed = parseRecoverableHarnessDesignContent(drawing.design);
    return { harnessId, schemaVersion: 1, revision, content: parsed.content, updatedUtc: now(),
      ...(parsed.warning ? { recoveryWarning: parsed.warning } : {}) };
  };
  const conflict = (text: string) => new HarnessDesignApiError(text, CONFLICT_CODE, null);
  const refuse = (expected: number, content: HarnessDesignDocument) => {
    if (readOnly) throw new HarnessDesignApiError(READ_ONLY_TEXT, READ_ONLY_CODE, null);
    if (conflicted || expected !== revision) {
      conflicted = true;
      mine = content;
      emit("conflict");
      throw conflict(CONFLICT_TEXT);
    }
  };
  // One write with the revision of the last read; 409 — the drawing changed: keep what was being saved.
  const write = async (projectId: string, harnessId: string, content: HarnessDesignDocument) => {
    emit("saving");
    try {
      const saved = resource(harnessId, await client.save(projectId, harnessId, JSON.parse(JSON.stringify(content)),
        exists ? revision : null));
      mine = null;
      emit("saved");
      return saved;
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        conflicted = true;
        mine = content;
        emit("conflict", reason.message);
        throw conflict(CONFLICT_TEXT);
      }
      emit("error", reason instanceof Error ? reason.message : undefined);
      throw reason;
    }
  };

  const api: HarnessDesignApi = {
    async get(projectId, harnessId) {
      const loaded = resource(harnessId, await client.drawing(projectId, harnessId));
      conflicted = false;
      emit("saved");
      return loaded;
    },
    async save(projectId, harnessId, expectedRevision, content) {
      refuse(expectedRevision, content);
      return write(projectId, harnessId, content);
    },
  };

  const commands = new Map<string, ComponentPlacementResult>();
  const sources = new Map<string, { templateId: string; version: number }>();
  const placements: ComponentPlacementApi = {
    async list(projectId, harnessId) {
      const graph = placementGraphOf(await client.placements(projectId, harnessId), projectId, harnessId);
      for (const snapshot of graph.snapshots) {
        sources.set(snapshot.snapshotId, { templateId: snapshot.sourceTemplateId, version: snapshot.sourceVersion });
      }
      return graph;
    },
    assetContentUrl(_projectId, _harnessId, snapshotId, assetId) {
      const source = sources.get(snapshotId);
      return source ? options.templates.assetContentUrl(source.templateId, source.version, assetId) : "";
    },
    // Placing a library connector = saving the drawing with it; the server checks the pinned revision in the library.
    async place(projectId, harnessId, body, document) {
      const done = commands.get(body.commandId);
      if (done) return done;
      refuse(body.expectedRevision, document);
      const binding = body.instance.libraryBinding;
      if (binding?.mode !== "template" || binding.templateId !== body.sourceTemplateId ||
          binding.templateVersion !== body.sourceVersion ||
          !document.connectors.some((connector) => connector.id === body.placementId)) {
        throw new HarnessDesignApiError("Размещение не совпадает с привязкой компонента.", "placement_invalid", null);
      }
      const saved = await write(projectId, harnessId, document);
      const result = { commandId: body.commandId, resultingRevision: saved.revision, snapshotId: "" };
      commands.set(body.commandId, result);
      return result;
    },
  };

  return { api, placements, revision: () => (exists ? revision : null), mine: () => mine };
}
