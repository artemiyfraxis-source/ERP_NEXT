import { describe, expect, it, vi } from "vitest";
import { ApiError, type HarnessDrawing } from "../api";
import type { ComponentTemplate } from "./component-library/component-template-api";
import { createInMemoryComponentTemplateCatalog } from "./testing/in-memory";
import { addContactPointV2, constantExpressionV2, linkLogicalContactPointV2, newTemplateContentV2 } from "./component-library/template-commands-v2";
import type { ArticleVariantV3, TemplateContentV3 } from "./component-library/template-model-v3";
import { upgradeTemplateContentV2ToV3 } from "./component-library/template-upgrade-v3";
import { componentPlacementRequest } from "./editor/component-placement-api";
import { createConnectorInstanceFromComponentTemplateV3, type ComponentTemplatePlacementEnvelopeV3 } from "./editor/component-template-placement";
import { HarnessDesignApiError } from "./editor/design-api";
import { buildComponentTemplateViewInstances, buildProjectComponentSnapshotLookup } from "./editor/HarnessDesignEditor";
import { createEmptyHarnessDesign } from "./editor/model";
import {
  CONFLICT_CODE, READ_ONLY_CODE, READ_ONLY_TEXT,
  createServerHarnessDesignApi, type ServerHarnessClient, type ServerSaveState,
} from "./serverApi";

const document = { id: "h1", index: "КБ1", name: "Чертёж жгута", size: 1, stored: true, url: null, uploaded_at: "2026-10-06T10:00:00", who: "Технолог", kind: "harness" as const };
const design = () => JSON.parse(JSON.stringify(createEmptyHarnessDesign())) as Record<string, unknown>;
const drawing = (revision: number | null): HarnessDrawing => revision !== null
  ? { document, design: design(), revision, warnings: [] }
  : { document: null, design: null, revision: null, warnings: [] };

function setup(stored: number | null) {
  const states: ServerSaveState[] = [];
  const client: ServerHarnessClient = {
    harness: vi.fn(async () => drawing(stored)),
    saveHarness: vi.fn(async () => ({ ...drawing(stored === null ? 1 : stored + 1), created: stored === null })),
    placements: vi.fn(async () => ({ placements: [], snapshots: [], unresolved: [] })),
  };
  return { api: createServerHarnessDesignApi({ client, onState: (state) => states.push(state) }), client, states };
}

describe("серверный адаптер чертежа жгута", () => {
  it("нет чертежа — пустой документ, первое сохранение без версии создаёт его", async () => {
    const { api, client } = setup(null);
    const loaded = await api.get("708К.4", "КБ1");
    expect(loaded.revision).toBe(0);
    expect(loaded.content.connectors).toHaveLength(0);
    const saved = await api.save("708К.4", "КБ1", 0, loaded.content);
    expect(client.saveHarness).toHaveBeenCalledWith("708К.4", "КБ1", expect.any(Object), null);
    expect(saved.revision).toBe(1);
  });

  it("сохраняет по версии, полученной при чтении, и берёт новую из ответа", async () => {
    const { api, client, states } = setup(1);
    const loaded = await api.get("708К.4", "КБ1");
    expect(loaded.revision).toBe(1);
    const first = await api.save("708К.4", "КБ1", 1, loaded.content);
    await api.save("708К.4", "КБ1", first.revision, first.content);
    expect(vi.mocked(client.saveHarness).mock.calls.map((call) => call[3])).toEqual([1, 2]);
    expect(states).toEqual(["saved", "saving", "saved", "saving", "saved"]);
  });

  it("409 — конфликт: ошибка с кодом, дальше сохранения не уходят на сервер", async () => {
    const { api, client, states } = setup(1);
    const loaded = await api.get("708К.4", "КБ1");
    vi.mocked(client.saveHarness).mockRejectedValueOnce(new ApiError("Чертёж жгута только что сохранили", 409));
    const failure = await api.save("708К.4", "КБ1", 1, loaded.content).catch((reason) => reason);
    expect(failure).toBeInstanceOf(HarnessDesignApiError);
    expect(failure.code).toBe(CONFLICT_CODE);
    expect(api.conflicted).toBe(true);
    await expect(api.save("708К.4", "КБ1", 1, loaded.content)).rejects.toBeInstanceOf(HarnessDesignApiError);
    expect(client.saveHarness).toHaveBeenCalledTimes(1);
    expect(states.at(-1)).toBe("conflict");
    await api.get("708К.4", "КБ1");
    expect(api.conflicted).toBe(false);
  });

  it("просмотр: сохранение отклоняется, не доходя до сервера", async () => {
    const client: ServerHarnessClient = { harness: vi.fn(async () => drawing(1)), saveHarness: vi.fn(), placements: vi.fn() };
    const api = createServerHarnessDesignApi({ client, readOnly: true });
    const loaded = await api.get("708К.4", "КБ1");
    const failure = await api.save("708К.4", "КБ1", loaded.revision, loaded.content).catch((reason) => reason);
    expect(failure).toMatchObject({ code: READ_ONLY_CODE, message: READ_ONLY_TEXT });
    const placed = await api.componentPlacements(createInMemoryComponentTemplateCatalog())
      .place("708К.4", "КБ1", placementBody(template(), loaded.revision), loaded.content).catch((reason) => reason);
    expect(placed).toMatchObject({ code: READ_ONLY_CODE });
    expect(client.saveHarness).not.toHaveBeenCalled();
  });

  it("другая ошибка сервера не называется конфликтом", async () => {
    const { api, client, states } = setup(1);
    const loaded = await api.get("708К.4", "КБ1");
    vi.mocked(client.saveHarness).mockRejectedValueOnce(new ApiError("Чертёж жгута больше 20 МБ", 422));
    await expect(api.save("708К.4", "КБ1", 1, loaded.content)).rejects.toThrow("больше 20 МБ");
    expect(api.conflicted).toBe(false);
    expect(states.at(-1)).toBe("error");
  });
});

// ── Библиотечные компоненты: соединитель закрепляет версию шаблона библиотеки ERP ──

const VARIANT_ID = "10000000-0000-4000-8000-000000000001";

function template(): ComponentTemplate & ComponentTemplatePlacementEnvelopeV3 {
  let v2 = newTemplateContentV2();
  const e4 = v2.views[0]!;
  const drawingView = v2.views[1]!;
  let pointId: string;
  [v2, pointId] = addContactPointV2(v2, e4.id, {
    number: "1", name: "Сигнал", contactType: "signal",
    x: constantExpressionV2(12), y: constantExpressionV2(18), direction: "right",
  });
  const logicalId = v2.views[0]!.contactPoints.find((point) => point.id === pointId)!.logicalContactId;
  [v2] = linkLogicalContactPointV2(v2, drawingView.id, logicalId, {
    x: constantExpressionV2(50), y: constantExpressionV2(60), direction: "right",
  });
  const content = upgradeTemplateContentV2ToV3(v2).content;
  const variant: ArticleVariantV3 = {
    id: VARIANT_ID, sourceId: "БД.СОЕД", entityType: "connector", articleKey: "XH-1", parameterValues: [],
    contactGroups: [{ contactTypeGroupId: content.contactTypeGroups[0]!.id, contactCount: 1, allowedTerminalArticleKeys: [] }],
  };
  const v3: TemplateContentV3 = { ...content, articleVariants: [...content.articleVariants, variant] };
  return {
    templateId: "20000000-0000-4000-8000-000000000002", version: 3, versionSha256: "a".repeat(64), code: "JST-XH",
    name: "JST XH", articleBindings: [{ sourceId: variant.sourceId, entityType: variant.entityType, articleKey: variant.articleKey }],
    assets: [], content: v3, createdUtc: "2026-10-06T00:00:00Z",
  };
}

function placementBody(published: ReturnType<typeof template>, revision: number) {
  const instance = createConnectorInstanceFromComponentTemplateV3(published, {
    id: "30000000-0000-4000-8000-000000000003", designation: "XS1", e4Position: { x: 100, y: 100 }, articleVariantId: VARIANT_ID,
  });
  return componentPlacementRequest(instance, revision, "40000000-0000-4000-8000-000000000004");
}

/** Сервер как `harness_api` после миграции 0091: хранит чертёж как есть, каждое сохранение — следующая версия;
 *  граф размещений строит из соединителей чертежа и библиотеки (закрепление — на самом соединителе). */
function fakeServer(published: ReturnType<typeof template>) {
  let stored = { design: design(), revision: 1 };
  const client: ServerHarnessClient = {
    harness: vi.fn(async () => ({ document, design: JSON.parse(JSON.stringify(stored.design)), revision: stored.revision, warnings: [] })),
    saveHarness: vi.fn(async (_project, _index, next, revision) => {
      if (revision !== stored.revision) throw new ApiError("Чертёж жгута уже сохранили", 409);
      stored = { design: next, revision: stored.revision + 1 };
      return { document, design: JSON.parse(JSON.stringify(next)), revision: stored.revision, warnings: [], created: false };
    }),
    placements: vi.fn(async () => {
      const connectors = (stored.design.connectors as { id: string; libraryBinding?: { mode: string; templateVersion: number; article: Record<string, string> } }[])
        .filter((connector) => connector.libraryBinding?.mode === "template" && connector.libraryBinding.templateVersion === published.version);
      const snapshotId = "50000000-0000-4000-8000-000000000005";
      return {
        placements: connectors.map((connector) => ({ placementId: connector.id, harnessId: "КБ1", snapshotId,
          ...connector.libraryBinding!.article, instance: connector, createdUtc: "2026-10-10T00:00:00Z", updatedUtc: "2026-10-10T00:00:00Z" })),
        snapshots: connectors.length ? [{ snapshotId, projectId: "708К.4", sourceTemplateId: published.templateId,
          sourceVersion: published.version, sourceVersionSha256: published.versionSha256, code: published.code,
          name: published.name, articleBindings: published.articleBindings, assets: [], schemaVersion: 3,
          content: published.content, createdUtc: "2026-10-10T00:00:00Z", updatedUtc: "2026-10-10T00:00:00Z" }] : [],
        unresolved: [],
      };
    }),
  };
  return { client, stored: () => stored };
}

describe("библиотечные компоненты в чертеже ERP", () => {
  it("размещение — сохранение чертежа с соединителем; автосохранение и перезагрузка — вид компонента на месте", async () => {
    const published = template();
    const server = fakeServer(published);
    const api = createServerHarnessDesignApi({ client: server.client });
    const loaded = await api.get("708К.4", "КБ1");
    const body = placementBody(published, loaded.revision);
    const placed = { ...loaded.content, connectors: [body.instance] };

    const result = await api.componentPlacements(createInMemoryComponentTemplateCatalog()).place("708К.4", "КБ1", body, placed);
    const confirmed = await api.get("708К.4", "КБ1");
    expect(confirmed.revision).toBe(result.resultingRevision);
    expect(confirmed.content.connectors.map((connector) => connector.id)).toEqual([body.placementId]);
    expect(server.stored().design).not.toHaveProperty("componentSnapshots");

    const [connector] = confirmed.content.connectors;
    const moved = { ...confirmed.content, connectors: [{ ...connector!, positions: { e4: { x: 340, y: 220 }, drawing: { x: 600, y: 410 } } }] };
    await api.save("708К.4", "КБ1", confirmed.revision, moved);

    const fresh = createServerHarnessDesignApi({ client: server.client });
    const reloaded = await fresh.get("708К.4", "КБ1");
    const lookup = buildProjectComponentSnapshotLookup(await fresh.componentPlacements(createInMemoryComponentTemplateCatalog()).list("708К.4", "КБ1"));
    expect(reloaded.content.connectors[0]!.positions).toEqual(moved.connectors[0]!.positions);
    expect(buildComponentTemplateViewInstances(reloaded.content, lookup)).toHaveLength(1);
  });

  it("размещение на устаревшей версии — конфликт, сервер не тронут", async () => {
    const published = template();
    const server = fakeServer(published);
    const api = createServerHarnessDesignApi({ client: server.client });
    const loaded = await api.get("708К.4", "КБ1");
    const body = placementBody(published, loaded.revision + 1);
    const failure = await api.componentPlacements(createInMemoryComponentTemplateCatalog())
      .place("708К.4", "КБ1", body, { ...loaded.content, connectors: [body.instance] }).catch((reason) => reason);
    expect(failure).toMatchObject({ code: CONFLICT_CODE });
    expect(server.client.saveHarness).not.toHaveBeenCalled();
  });
});

