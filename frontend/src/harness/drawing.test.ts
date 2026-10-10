// The drawing of a position for the editor (`harnessDrawing` over `harnessApi`): the editor works on the server's own
// revision, a 409 stops writing and keeps the edits, view mode never writes, placing a library connector is a save of
// the drawing with it and the placement survives a reload.
import { describe, expect, it, vi } from "vitest";
import { ApiError, type HarnessDrawing, type HarnessPlacementGraph } from "../api";
import type { ComponentTemplate } from "./component-library/component-template-api";
import { addContactPointV2, constantExpressionV2, linkLogicalContactPointV2, newTemplateContentV2 } from "./component-library/template-commands-v2";
import type { ArticleVariantV3, TemplateContentV3 } from "./component-library/template-model-v3";
import { upgradeTemplateContentV2ToV3 } from "./component-library/template-upgrade-v3";
import { CONFLICT_CODE, READ_ONLY_CODE, READ_ONLY_TEXT, harnessDrawing, type DrawingClient, type DrawingSaveState } from "./drawing";
import { componentPlacementRequest } from "./editor/component-placement-api";
import { createConnectorInstanceFromComponentTemplateV3, type ComponentTemplatePlacementEnvelopeV3 } from "./editor/component-template-placement";
import { HarnessDesignApiError } from "./editor/design-api";
import { buildComponentTemplateViewInstances, buildProjectComponentSnapshotLookup } from "./editor/HarnessDesignEditor";
import { createEmptyHarnessDesign } from "./editor/model";
import { createInMemoryComponentTemplateCatalog } from "./testing/in-memory";

const P = "708К.4", I = "КБ1";
const VARIANT_ID = "10000000-0000-4000-8000-000000000001";
const document = { id: "h1", index: I, name: "Чертёж жгута", size: 1, stored: true, url: null, uploaded_at: "2026-10-06T10:00:00", who: "Технолог", kind: "harness" as const };
const empty = () => JSON.parse(JSON.stringify(createEmptyHarnessDesign())) as Record<string, unknown>;

function template(): ComponentTemplate & ComponentTemplatePlacementEnvelopeV3 {
  let v2 = newTemplateContentV2();
  const e4 = v2.views[0]!;
  const drawingView = v2.views[1]!;
  let pointId: string;
  [v2, pointId] = addContactPointV2(v2, e4.id, {
    number: "1", name: "Сигнал", contactType: "signal", x: constantExpressionV2(12), y: constantExpressionV2(18), direction: "right",
  });
  const logicalId = v2.views[0]!.contactPoints.find((point) => point.id === pointId)!.logicalContactId;
  [v2] = linkLogicalContactPointV2(v2, drawingView.id, logicalId, { x: constantExpressionV2(50), y: constantExpressionV2(60), direction: "right" });
  const content = upgradeTemplateContentV2ToV3(v2).content;
  const variant: ArticleVariantV3 = {
    id: VARIANT_ID, sourceId: "erp-connectors", entityType: "connector", articleKey: "XH-1", parameterValues: [],
    contactGroups: [{ contactTypeGroupId: content.contactTypeGroups[0]!.id, contactCount: 1, allowedTerminalArticleKeys: [] }],
  };
  const v3: TemplateContentV3 = { ...content, articleVariants: [...content.articleVariants, variant] };
  return {
    templateId: "20000000-0000-4000-8000-000000000002", version: 3, versionSha256: "a".repeat(64), code: "JST-XH",
    name: "JST XH", articleBindings: [{ sourceId: variant.sourceId, entityType: variant.entityType, articleKey: variant.articleKey }],
    assets: [], content: v3, createdUtc: "2026-10-06T00:00:00Z",
  };
}

/** The server as `harness_api`: stores the drawing as sent, each save is the next revision (409 on a stale one); the
 *  placement graph is read off the connectors of the stored drawing and the library. */
function fakeServer(initial: { design: Record<string, unknown>; revision: number } | null, published = template()) {
  let stored = initial;
  const client: DrawingClient = {
    drawing: vi.fn(async (): Promise<HarnessDrawing> => stored
      ? { document, design: JSON.parse(JSON.stringify(stored.design)), revision: stored.revision, warnings: [] }
      : { document: null, design: null, revision: null, warnings: [] }),
    save: vi.fn(async (_project, _index, design, revision) => {
      if (revision !== (stored?.revision ?? null)) throw new ApiError("Чертёж жгута уже сохранил Мастер 10.10.2026 10:42 — обновите страницу", 409);
      stored = { design, revision: (stored?.revision ?? 0) + 1 };
      return { document, design: JSON.parse(JSON.stringify(design)), revision: stored.revision, warnings: [] };
    }),
    placements: vi.fn(async (): Promise<HarnessPlacementGraph> => {
      const connectors = ((stored?.design.connectors ?? []) as { id: string; libraryBinding?: { mode: string; article: Record<string, string> } }[])
        .filter((connector) => connector.libraryBinding?.mode === "template");
      const snapshotId = "50000000-0000-4000-8000-000000000005";
      return {
        placements: connectors.map((connector) => ({ placementId: connector.id, harnessId: "КБ1", snapshotId, ...connector.libraryBinding!.article,
          instance: connector, createdUtc: "2026-10-10T00:00:00Z", updatedUtc: "2026-10-10T00:00:00Z" })),
        snapshots: connectors.length ? [{ snapshotId, projectId: P, sourceTemplateId: published.templateId, sourceVersion: published.version,
          sourceVersionSha256: published.versionSha256, code: published.code, name: published.name, articleBindings: published.articleBindings,
          assets: [], schemaVersion: 3, content: published.content, createdUtc: "2026-10-10T00:00:00Z", updatedUtc: "2026-10-10T00:00:00Z" }] : [],
        unresolved: [],
      };
    }),
  };
  return { client, stored: () => stored };
}

function open(server: ReturnType<typeof fakeServer>, readOnly = false) {
  const states: DrawingSaveState[] = [];
  const drawing = harnessDrawing({ client: server.client, readOnly, templates: createInMemoryComponentTemplateCatalog(),
    onState: (state) => states.push(state) });
  return { drawing, states };
}

describe("чертёж позиции в редакторе", () => {
  it("нет чертежа — пустой документ, первое сохранение без версии создаёт его", async () => {
    const server = fakeServer(null);
    const { drawing } = open(server);
    const loaded = await drawing.api.get(P, I);
    expect(loaded.revision).toBe(0);
    expect(drawing.revision()).toBeNull();
    const saved = await drawing.api.save(P, I, 0, loaded.content);
    expect(server.client.save).toHaveBeenCalledWith(P, I, expect.any(Object), null);
    expect(saved.revision).toBe(1);
    expect(drawing.revision()).toBe(1);
  });

  it("номер версии редактора — номер версии сервера", async () => {
    const server = fakeServer({ design: empty(), revision: 7 });
    const { drawing, states } = open(server);
    const loaded = await drawing.api.get(P, I);
    expect(loaded.revision).toBe(7);
    const first = await drawing.api.save(P, I, 7, loaded.content);
    await drawing.api.save(P, I, first.revision, first.content);
    expect(vi.mocked(server.client.save).mock.calls.map((call) => call[3])).toEqual([7, 8]);
    expect(states).toEqual(["saved", "saving", "saved", "saving", "saved"]);
  });

  it("409: дальше ничего не пишется, последние правки остаются (`mine`)", async () => {
    const server = fakeServer({ design: empty(), revision: 1 });
    const { drawing, states } = open(server);
    const loaded = await drawing.api.get(P, I);
    vi.mocked(server.client.save).mockRejectedValueOnce(new ApiError("Чертёж жгута уже сохранил Мастер", 409));
    const failure = await drawing.api.save(P, I, 1, loaded.content).catch((reason) => reason);
    expect(failure).toBeInstanceOf(HarnessDesignApiError);
    expect(failure.code).toBe(CONFLICT_CODE);
    const later = { ...loaded.content, customWireColors: ["#ABCDEF"] };
    await expect(drawing.api.save(P, I, 1, later)).rejects.toMatchObject({ code: CONFLICT_CODE });
    expect(server.client.save).toHaveBeenCalledTimes(1);
    expect(drawing.mine()?.customWireColors).toEqual(["#ABCDEF"]);
    expect(states.at(-1)).toBe("conflict");
  });

  it("просмотр: сохранение и размещение отклоняются, не доходя до сервера", async () => {
    const server = fakeServer({ design: empty(), revision: 1 });
    const { drawing } = open(server, true);
    const loaded = await drawing.api.get(P, I);
    await expect(drawing.api.save(P, I, loaded.revision, loaded.content)).rejects.toMatchObject({ code: READ_ONLY_CODE, message: READ_ONLY_TEXT });
    const body = componentPlacementRequest(createConnectorInstanceFromComponentTemplateV3(template(), {
      id: "30000000-0000-4000-8000-000000000003", designation: "XS1", e4Position: { x: 0, y: 0 }, articleVariantId: VARIANT_ID,
    }), loaded.revision, "40000000-0000-4000-8000-000000000004");
    await expect(drawing.placements.place(P, I, body, { ...loaded.content, connectors: [body.instance] })).rejects.toMatchObject({ code: READ_ONLY_CODE });
    expect(server.client.save).not.toHaveBeenCalled();
  });

  it("размещение библиотечного разъёма — сохранение чертежа с ним; после перезагрузки вид компонента на месте", async () => {
    const published = template();
    const server = fakeServer({ design: empty(), revision: 1 }, published);
    const { drawing } = open(server);
    const loaded = await drawing.api.get(P, I);
    const body = componentPlacementRequest(createConnectorInstanceFromComponentTemplateV3(published, {
      id: "30000000-0000-4000-8000-000000000003", designation: "XS1", e4Position: { x: 100, y: 100 }, articleVariantId: VARIANT_ID,
    }), loaded.revision, "40000000-0000-4000-8000-000000000004");
    const placed = { ...loaded.content, connectors: [body.instance] };
    const result = await drawing.placements.place(P, I, body, placed);
    expect(await drawing.placements.place(P, I, body, placed)).toEqual(result);  // a repeated command is not placed twice
    const confirmed = await drawing.api.get(P, I);
    expect(confirmed.revision).toBe(result.resultingRevision);
    expect(server.stored()!.design).not.toHaveProperty("componentSnapshots");

    const fresh = open(server).drawing;
    const reloaded = await fresh.api.get(P, I);
    const lookup = buildProjectComponentSnapshotLookup(await fresh.placements.list(P, I));
    expect(buildComponentTemplateViewInstances(reloaded.content, lookup)).toHaveLength(1);
  });

  it("размещение на устаревшей версии — конфликт, сервер не тронут", async () => {
    const server = fakeServer({ design: empty(), revision: 4 });
    const { drawing } = open(server);
    const loaded = await drawing.api.get(P, I);
    const body = componentPlacementRequest(createConnectorInstanceFromComponentTemplateV3(template(), {
      id: "30000000-0000-4000-8000-000000000003", designation: "XS1", e4Position: { x: 0, y: 0 }, articleVariantId: VARIANT_ID,
    }), loaded.revision - 1, "40000000-0000-4000-8000-000000000004");
    await expect(drawing.placements.place(P, I, body, { ...loaded.content, connectors: [body.instance] })).rejects.toMatchObject({ code: CONFLICT_CODE });
    expect(server.client.save).not.toHaveBeenCalled();
  });
});
