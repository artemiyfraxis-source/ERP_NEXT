import { describe, expect, it, vi } from "vitest";
import { ApiError, type HarnessDrawing } from "../api";
import { createInMemoryComponentTemplateCatalog, type ComponentTemplate } from "./component-library/component-template-api";
import { addContactPointV2, constantExpressionV2, linkLogicalContactPointV2, newTemplateContentV2 } from "./component-library/template-commands-v2";
import type { ArticleVariantV3, TemplateContentV3 } from "./component-library/template-model-v3";
import { upgradeTemplateContentV2ToV3 } from "./component-library/template-upgrade-v3";
import { componentPlacementRequest } from "./editor/component-placement-api";
import { createConnectorInstanceFromComponentTemplateV3, type ComponentTemplatePlacementEnvelopeV3 } from "./editor/component-template-placement";
import { HarnessDesignApiError } from "./editor/design-api";
import { buildComponentTemplateViewInstances, buildProjectComponentSnapshotLookup } from "./editor/HarnessDesignEditor";
import { createEmptyHarnessDesign } from "./editor/model";
import {
  COMPONENT_PLACEMENTS, COMPONENT_SNAPSHOTS, CONFLICT_CODE, READ_ONLY_CODE, READ_ONLY_TEXT, componentGraphOf,
  createServerHarnessDesignApi, type ServerHarnessClient, type ServerSaveState,
} from "./serverApi";

const document = { id: "h1", index: "КБ1", name: "Чертёж жгута", size: 1, stored: true, url: null, uploaded_at: "2026-10-06T10:00:00", who: "Технолог", kind: "harness" as const };
const design = () => JSON.parse(JSON.stringify(createEmptyHarnessDesign())) as Record<string, unknown>;
const drawing = (version: string | null): HarnessDrawing => version
  ? { document, design: design(), version, warnings: [] }
  : { document: null, design: null, version: null, warnings: [] };

function setup(stored: string | null) {
  const states: ServerSaveState[] = [];
  const client: ServerHarnessClient = {
    harness: vi.fn(async () => drawing(stored)),
    saveHarness: vi.fn(async () => ({ ...drawing("v2"), created: stored === null })),
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
    const { api, client, states } = setup("v1");
    const loaded = await api.get("708К.4", "КБ1");
    expect(loaded.revision).toBe(1);
    const first = await api.save("708К.4", "КБ1", 1, loaded.content);
    await api.save("708К.4", "КБ1", first.revision, first.content);
    expect(vi.mocked(client.saveHarness).mock.calls.map((call) => call[3])).toEqual(["v1", "v2"]);
    expect(states).toEqual(["saved", "saving", "saved", "saving", "saved"]);
  });

  it("409 — конфликт: ошибка с кодом, дальше сохранения не уходят на сервер", async () => {
    const { api, client, states } = setup("v1");
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
    const client: ServerHarnessClient = { harness: vi.fn(async () => drawing("v1")), saveHarness: vi.fn() };
    const api = createServerHarnessDesignApi({ client, readOnly: true });
    const loaded = await api.get("708К.4", "КБ1");
    const failure = await api.save("708К.4", "КБ1", loaded.revision, loaded.content).catch((reason) => reason);
    expect(failure).toMatchObject({ code: READ_ONLY_CODE, message: READ_ONLY_TEXT });
    const placed = await api.componentPlacements(createInMemoryComponentTemplateCatalog())
      .place("708К.4", "КБ1", placementBody(template(), loaded.revision)).catch((reason) => reason);
    expect(placed).toMatchObject({ code: READ_ONLY_CODE });
    expect(client.saveHarness).not.toHaveBeenCalled();
  });

  it("другая ошибка сервера не называется конфликтом", async () => {
    const { api, client, states } = setup("v1");
    const loaded = await api.get("708К.4", "КБ1");
    vi.mocked(client.saveHarness).mockRejectedValueOnce(new ApiError("Чертёж жгута больше 20 МБ", 422));
    await expect(api.save("708К.4", "КБ1", 1, loaded.content)).rejects.toThrow("больше 20 МБ");
    expect(api.conflicted).toBe(false);
    expect(states.at(-1)).toBe("error");
  });
});

// ── Библиотечные компоненты: размещение хранится в документе КД и переживает сохранение и перезагрузку ──

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

/** Сервер как `harness_api`: хранит документ как есть, версия — по содержимому; сохранение без раздела библиотечных
 *  компонентов переносит прежний (у модели редактора такого раздела нет). */
function fakeServer(initial: Record<string, unknown>) {
  let stored = { design: initial, version: "v1", n: 1 };
  const client: ServerHarnessClient = {
    harness: vi.fn(async () => ({ document, design: JSON.parse(JSON.stringify(stored.design)), version: stored.version, warnings: [] })),
    saveHarness: vi.fn(async (_project, _index, next, version) => {
      if (version !== stored.version) throw new ApiError("Чертёж жгута уже сохранили", 409);
      const design = { ...next };
      for (const key of [COMPONENT_SNAPSHOTS, COMPONENT_PLACEMENTS]) if (!(key in design) && key in stored.design) design[key] = stored.design[key];
      stored = { design, version: `v${stored.n + 1}`, n: stored.n + 1 };
      return { document, design: JSON.parse(JSON.stringify(design)), version: stored.version, warnings: [], created: false };
    }),
  };
  return { client, stored: () => stored };
}

describe("библиотечные компоненты в чертеже ERP", () => {
  it("размещение, автосохранение с новым местом, перезагрузка — компонент и его вид на месте", async () => {
    const published = template();
    const templates = createInMemoryComponentTemplateCatalog({ templates: [published] });
    const server = fakeServer(design());
    const api = createServerHarnessDesignApi({ client: server.client, newId: () => "50000000-0000-4000-8000-000000000005" });
    const loaded = await api.get("708К.4", "КБ1");
    const placements = api.componentPlacements(templates);
    const body = placementBody(published, loaded.revision);

    const result = await placements.place("708К.4", "КБ1", body);
    // Редактор подтверждает размещение повторным чтением: тот же номер, соединитель в документе.
    const confirmed = await api.get("708К.4", "КБ1");
    expect(confirmed.revision).toBe(result.resultingRevision);
    expect(confirmed.content.connectors.map((connector) => connector.id)).toEqual([body.placementId]);
    expect(server.stored().design[COMPONENT_PLACEMENTS]).toHaveLength(1);

    // Мастер двигает компонент и его рисунок — автосохранение уходит без раздела библиотечных компонентов.
    const [connector] = confirmed.content.connectors;
    const moved = { ...confirmed.content, connectors: [{ ...connector!,
      positions: { e4: { x: 340, y: 220 }, drawing: { x: 600, y: 410 } },
      drawingPlacements: [{ drawingId: "drawing-view", visible: true, offset: { x: 15, y: -5 }, scale: 1.5 }] }] };
    await api.save("708К.4", "КБ1", confirmed.revision, moved);
    expect(vi.mocked(server.client.saveHarness).mock.calls.at(-1)![2]).not.toHaveProperty(COMPONENT_PLACEMENTS);

    // Перезагрузка страницы: новый адаптер читает чертёж и закреплённые снимки из КД.
    const fresh = createServerHarnessDesignApi({ client: server.client });
    const reloaded = await fresh.get("708К.4", "КБ1");
    const graph = await fresh.componentPlacements(createInMemoryComponentTemplateCatalog()).list("708К.4", "КБ1");
    const lookup = buildProjectComponentSnapshotLookup(graph);
    expect(lookup.get(body.placementId)?.snapshotId).toBe(result.snapshotId);
    expect(reloaded.content.connectors[0]!.positions).toEqual(moved.connectors[0]!.positions);
    expect(reloaded.content.connectors[0]!.drawingPlacements).toEqual(moved.connectors[0]!.drawingPlacements);
    // Вид компонента строится из закреплённого снимка, а не резервной таблицей.
    expect(buildComponentTemplateViewInstances(reloaded.content, lookup)).toHaveLength(1);
  });

  it("чужое сохранение между чтением и размещением — конфликт, сервер не тронут", async () => {
    const published = template();
    const server = fakeServer(design());
    const api = createServerHarnessDesignApi({ client: server.client });
    const loaded = await api.get("708К.4", "КБ1");
    vi.mocked(server.client.harness).mockResolvedValueOnce({ document, design: design(), version: "v9", warnings: [] });
    const failure = await api.componentPlacements(createInMemoryComponentTemplateCatalog({ templates: [published] }))
      .place("708К.4", "КБ1", placementBody(published, loaded.revision)).catch((reason) => reason);
    expect(failure).toMatchObject({ code: CONFLICT_CODE });
    expect(server.client.saveHarness).not.toHaveBeenCalled();
  });

  it("размещения из экспорта TECHMAP-GRAPHER с чужим id жгута читаются как этого жгута", () => {
    const published = template();
    const body = placementBody(published, 1);
    const graph = componentGraphOf({
      [COMPONENT_SNAPSHOTS]: [{ snapshotId: "50000000-0000-4000-8000-000000000005", sourceTemplateId: published.templateId,
        sourceVersion: 3, sourceVersionSha256: published.versionSha256, schemaVersion: published.content.schemaVersion,
        code: "JST-XH", name: "JST XH", articleBindings: published.articleBindings, assets: [], content: published.content,
        createdUtc: "2026-10-01T00:00:00Z", updatedUtc: "2026-10-01T00:00:00Z" },
      { snapshotId: "60000000-0000-4000-8000-000000000006" }],
      [COMPONENT_PLACEMENTS]: [{ placementId: body.placementId, harnessId: "h-kb1", snapshotId: "50000000-0000-4000-8000-000000000005",
        sourceId: body.sourceId, entityType: body.entityType, articleKey: body.articleKey, instance: body.instance,
        createdUtc: "2026-10-01T00:00:00Z", updatedUtc: "2026-10-01T00:00:00Z" }],
    }, "708К.4", "КБ1");
    expect(graph.placements.map((placement) => placement.harnessId)).toEqual(["КБ1"]);
    expect(graph.snapshots.map((snapshot) => snapshot.projectId)).toEqual(["708К.4"]);
  });
});
