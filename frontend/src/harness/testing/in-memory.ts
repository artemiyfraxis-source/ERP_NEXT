// Test doubles of the editor's ports — for tests only, never imported by the app (the ERP implementations: the
// drawing and library over `harnessApi`, the reference books over the ERP catalog). Ported from TECHMAP-GRAPHER
// (© Василий, SaveOurSouls, used with the author's permission) where its in-memory stores served the same purpose.
import type { ComponentTemplate, ComponentTemplateApi } from "../component-library/component-template-api";
import type { CoveringMaterialApi, GlobalMaterial } from "../editor/covering-assets";
import type { CoveringTextureEntry } from "../editor/covering-library";
import {
  HarnessDesignApiError,
  parseHarnessDesignSnapshot,
  parseRecoverableHarnessDesignContent,
  type HarnessDesignApi,
  type HarnessDesignResource,
} from "../editor/design-api";
import type {
  ComponentPlacementApi,
  ComponentPlacementResult,
  ProjectComponentPlacementResource,
  ProjectComponentSnapshotResource,
} from "../editor/component-placement-api";
import type { HarnessEditorCatalogApi } from "../editor/HarnessDesignEditor";
import { createEmptyHarnessDesign, type HarnessDesignDocument } from "../editor/model";
import { snapshotReferenceCatalog } from "../reference-catalog";

export interface InMemoryHarnessDesignApi extends HarnessDesignApi {
  /** Snapshot v1 JSON of the stored document, or null when nothing was saved yet. */
  exportSnapshot(projectId: string, harnessId: string): string | null;
  /** Replaces the stored document with a snapshot v1 JSON (validated). */
  importSnapshot(projectId: string, json: string): HarnessDesignResource;
}

/** Keeps snapshot v1 JSON strings in memory: every read parses the stored JSON, every save validates, checks the
 *  revision and bumps it. */
export function createInMemoryHarnessDesignApi(
  initial: readonly { readonly projectId: string; readonly snapshot: string }[] = [],
  now: () => string = () => new Date().toISOString(),
): InMemoryHarnessDesignApi {
  const store = new Map<string, string>();
  const key = (projectId: string, harnessId: string) => `${projectId}\u0000${harnessId}`;
  const read = (projectId: string, harnessId: string): HarnessDesignResource => {
    const json = store.get(key(projectId, harnessId));
    if (json === undefined) {
      return { harnessId, schemaVersion: 1, revision: 0, content: createEmptyHarnessDesign(), updatedUtc: now() };
    }
    return parseHarnessDesignSnapshot(JSON.parse(json));
  };
  const api: InMemoryHarnessDesignApi = {
    async get(projectId, harnessId) {
      return read(projectId, harnessId);
    },
    async save(projectId, harnessId, expectedRevision, content) {
      const current = read(projectId, harnessId);
      if (current.revision !== expectedRevision) {
        throw new HarnessDesignApiError("Документ изменился после открытия. Перезагрузите жгут перед повторным сохранением.",
          "design_revision_conflict", current.revision);
      }
      const validated = parseRecoverableHarnessDesignContent(JSON.parse(JSON.stringify(content))).content;
      store.set(key(projectId, harnessId), JSON.stringify({
        harnessId, schemaVersion: 1, revision: current.revision + 1, content: validated, updatedUtc: now(),
      }));
      return read(projectId, harnessId);
    },
    exportSnapshot(projectId, harnessId) {
      return store.get(key(projectId, harnessId)) ?? null;
    },
    importSnapshot(projectId, json) {
      const parsed = parseHarnessDesignSnapshot(JSON.parse(json));
      store.set(key(projectId, parsed.harnessId), json);
      return parsed;
    },
  };
  for (const item of initial) api.importSnapshot(item.projectId, item.snapshot);
  return api;
}

/** Published template versions in memory; the highest version per template is the latest one. */
export function createInMemoryComponentTemplateCatalog(
  options: { readonly templates?: readonly ComponentTemplate[]; readonly assetUrls?: Readonly<Record<string, string>> } = {},
): ComponentTemplateApi {
  const versions = [...(options.templates ?? [])];
  const latest = (templateId: string) => versions
    .filter((template) => template.templateId === templateId)
    .sort((a, b) => b.version - a.version)[0];
  return Object.freeze({
    async list() {
      return [...new Set(versions.map((template) => template.templateId))].map((id) => {
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

/** Placements over the in-memory drawing store: `place` stores the drawing with the placed connector under the same
 *  revision check, and the graph is read back from its connectors — as the ERP server does. */
export function createInMemoryComponentPlacementApi(
  design: InMemoryHarnessDesignApi,
  templates: ComponentTemplateApi,
  now: () => string = () => new Date().toISOString(),
): ComponentPlacementApi {
  const pinned = new Map<string, ProjectComponentSnapshotResource>();
  const commands = new Map<string, ComponentPlacementResult>();
  return {
    async list(projectId, harnessId) {
      const { content } = await design.get(projectId, harnessId);
      const placements: ProjectComponentPlacementResource[] = [];
      for (const connector of content.connectors) {
        const binding = connector.libraryBinding;
        const snapshot = binding?.mode === "template" ? pinned.get(`${binding.templateId}@${binding.templateVersion}`) : undefined;
        if (binding?.mode !== "template" || !snapshot) continue;
        placements.push({
          placementId: connector.id, harnessId, snapshotId: snapshot.snapshotId, ...binding.article,
          instance: connector as unknown as Readonly<Record<string, unknown>>,
          createdUtc: snapshot.createdUtc, updatedUtc: snapshot.updatedUtc,
        });
      }
      const used = new Set(placements.map((item) => item.snapshotId));
      return { placements, snapshots: [...pinned.values()].filter((item) => used.has(item.snapshotId)).map((item) => ({ ...item, projectId })) };
    },
    assetContentUrl(_projectId, _harnessId, snapshotId, assetId) {
      const snapshot = [...pinned.values()].find((item) => item.snapshotId === snapshotId);
      return snapshot ? templates.assetContentUrl(snapshot.sourceTemplateId, snapshot.sourceVersion, assetId) : "";
    },
    async place(projectId, harnessId, body, document: HarnessDesignDocument) {
      const done = commands.get(body.commandId);
      if (done) return done;
      const binding = body.instance.libraryBinding;
      if (binding?.mode !== "template" || binding.templateId !== body.sourceTemplateId ||
          binding.templateVersion !== body.sourceVersion) {
        throw new HarnessDesignApiError("Размещение не совпадает с привязкой компонента.", "placement_invalid", null);
      }
      const template = await templates.getVersion(body.sourceTemplateId, body.sourceVersion);
      if (template.versionSha256 !== binding.versionSha256) {
        throw new HarnessDesignApiError("Версия библиотечного компонента изменилась.", "placement_invalid", null);
      }
      if (!document.connectors.some((connector) => connector.id === body.placementId)) {
        throw new HarnessDesignApiError("Компонента нет в сохраняемом чертеже.", "placement_invalid", null);
      }
      const saved = await design.save(projectId, harnessId, body.expectedRevision, document);
      const key = `${template.templateId}@${template.version}`;
      const timestamp = now();
      const snapshot = pinned.get(key) ?? {
        snapshotId: crypto.randomUUID(), projectId,
        sourceTemplateId: template.templateId, sourceVersion: template.version, sourceVersionSha256: template.versionSha256,
        code: template.code, name: template.name, articleBindings: template.articleBindings, assets: template.assets,
        schemaVersion: template.content.schemaVersion, content: template.content, createdUtc: timestamp, updatedUtc: timestamp,
      };
      pinned.set(key, snapshot);
      const result = { commandId: body.commandId, resultingRevision: saved.revision, snapshotId: snapshot.snapshotId };
      commands.set(body.commandId, result);
      return result;
    },
  };
}

/** Covering materials and project textures in memory. */
export function createInMemoryCoveringMaterialApi(
  materials: readonly GlobalMaterial[] = [],
  textures: Readonly<Record<string, { readonly entry: CoveringTextureEntry; readonly url: string }>> = {},
): CoveringMaterialApi {
  return {
    async listMaterials() { return materials; },
    async pinTexture(_projectId, material) {
      const found = textures[material.materialId];
      if (!found) throw new Error("Не загружена текстура материала.");
      return { ...found.entry, name: material.name };
    },
    async listTextures() { return Object.values(textures); },
  };
}

/** The editor's catalog in memory: empty unless parts are given. */
export function createInMemoryHarnessEditorCatalog(parts: Partial<HarnessEditorCatalogApi> = {}): HarnessEditorCatalogApi {
  return {
    templates: parts.templates ?? createInMemoryComponentTemplateCatalog(),
    references: parts.references ?? snapshotReferenceCatalog(),
    materials: parts.materials ?? createInMemoryCoveringMaterialApi(),
  };
}

/** Editor props for a test: an in-memory drawing, library and catalog bound together. */
export function inMemoryEditorPorts(parts: Partial<HarnessEditorCatalogApi> = {}, design = createInMemoryHarnessDesignApi()) {
  const catalog = createInMemoryHarnessEditorCatalog(parts);
  return { api: design, placements: createInMemoryComponentPlacementApi(design, catalog.templates), catalog };
}
