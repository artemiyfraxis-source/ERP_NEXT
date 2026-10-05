// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  addContactPointV2,
  constantExpressionV2,
  linkLogicalContactPointV2,
  newTemplateContentV2,
} from "./component-library/template-commands-v2";
import { upgradeTemplateContentV2ToV3 } from "./component-library/template-upgrade-v3";
import type { ArticleVariantV3, TemplateContentV3 } from "./component-library/template-model-v3";
import {
  createInMemoryComponentPlacementApi,
  createInMemoryComponentTemplateCatalog,
  createInMemoryHarnessDesignApi,
  createInMemoryReferenceCatalog,
  HarnessDesignApiError,
  ReferenceCatalogApiError,
  type ComponentTemplate,
  type ReferenceCatalogSnapshot,
} from "./adapters";
import { componentPlacementRequest } from "./editor/component-placement-api";
import { createConnectorInstanceFromComponentTemplateV3, type ComponentTemplatePlacementEnvelopeV3 } from "./editor/component-template-placement";
import { buildComponentTemplateViewInstances, buildProjectComponentSnapshotLookup } from "./editor/HarnessDesignEditor";

const projectId = "p-1";
const harnessId = "h-1";
const VARIANT_ID = "10000000-0000-4000-8000-000000000001";

function template(): ComponentTemplate & ComponentTemplatePlacementEnvelopeV3 {
  let v2 = newTemplateContentV2();
  const e4 = v2.views[0]!;
  const drawing = v2.views[1]!;
  let pointId: string;
  [v2, pointId] = addContactPointV2(v2, e4.id, {
    number: "1", name: "Сигнал", contactType: "signal",
    x: constantExpressionV2(12), y: constantExpressionV2(18), direction: "right",
  });
  const logicalId = v2.views[0]!.contactPoints.find((point) => point.id === pointId)!.logicalContactId;
  [v2] = linkLogicalContactPointV2(v2, drawing.id, logicalId, {
    x: constantExpressionV2(50), y: constantExpressionV2(60), direction: "right",
  });
  const content = upgradeTemplateContentV2ToV3(v2).content;
  const variant: ArticleVariantV3 = {
    id: VARIANT_ID, sourceId: "БД.СОЕД", entityType: "connector", articleKey: "XH-1",
    parameterValues: [],
    contactGroups: [{ contactTypeGroupId: content.contactTypeGroups[0]!.id, contactCount: 1, allowedTerminalArticleKeys: [] }],
  };
  const v3: TemplateContentV3 = { ...content, articleVariants: [...content.articleVariants, variant] };
  return {
    templateId: "template-1", version: 3, versionSha256: "a".repeat(64), code: "JST-XH", name: "JST XH",
    articleBindings: [{ sourceId: variant.sourceId, entityType: variant.entityType, articleKey: variant.articleKey }],
    assets: [], content: v3, createdUtc: "2026-10-06T00:00:00Z",
  };
}

function placementBody(published: ComponentTemplate & ComponentTemplatePlacementEnvelopeV3, revision: number, commandId = crypto.randomUUID()) {
  const instance = createConnectorInstanceFromComponentTemplateV3(published, {
    id: crypto.randomUUID(), designation: "XS1", e4Position: { x: 100, y: 100 }, articleVariantId: VARIANT_ID,
  });
  return componentPlacementRequest(instance, revision, commandId);
}

describe("in-memory adapters of the harness editor", () => {
  it("places a library component atomically: the design gains the connector and one revision", async () => {
    const design = createInMemoryHarnessDesignApi();
    const published = template();
    const templates = createInMemoryComponentTemplateCatalog({ templates: [published] });
    const placements = createInMemoryComponentPlacementApi(design, templates);
    const body = placementBody(published, 0);

    const result = await placements.place(projectId, harnessId, body);
    const stored = await design.get(projectId, harnessId);

    expect(result.resultingRevision).toBe(1);
    expect(stored.revision).toBe(1);
    expect(stored.content.connectors.map((connector) => connector.id)).toEqual([body.placementId]);
    const graph = await placements.list(projectId, harnessId);
    const lookup = buildProjectComponentSnapshotLookup(graph);
    expect(lookup.get(body.placementId)?.snapshotId).toBe(result.snapshotId);
    // The pinned snapshot materializes the placed connector exactly (no fallback rendering).
    expect(buildComponentTemplateViewInstances(stored.content, lookup)).toHaveLength(1);
  });

  it("returns the same result for a repeated command instead of placing a duplicate", async () => {
    const design = createInMemoryHarnessDesignApi();
    const published = template();
    const placements = createInMemoryComponentPlacementApi(design, createInMemoryComponentTemplateCatalog({ templates: [published] }));
    const body = placementBody(published, 0, "11111111-1111-4111-8111-111111111111");
    const first = await placements.place(projectId, harnessId, body);
    const second = await placements.place(projectId, harnessId, body);
    expect(second).toEqual(first);
    expect((await design.get(projectId, harnessId)).content.connectors).toHaveLength(1);
  });

  it("rejects a placement against a stale revision without touching the design", async () => {
    const design = createInMemoryHarnessDesignApi();
    const published = template();
    const placements = createInMemoryComponentPlacementApi(design, createInMemoryComponentTemplateCatalog({ templates: [published] }));
    const error = await placements.place(projectId, harnessId, placementBody(published, 5)).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(HarnessDesignApiError);
    expect(error).toMatchObject({ code: "design_revision_conflict", currentRevision: 0 });
    expect((await design.get(projectId, harnessId)).content.connectors).toHaveLength(0);
    expect((await placements.list(projectId, harnessId)).placements).toHaveLength(0);
  });

  it("serves only published template versions; the latest one by default", async () => {
    const v3 = template();
    const catalog = createInMemoryComponentTemplateCatalog({ templates: [{ ...v3, version: 2 }, v3] });
    expect((await catalog.list()).map((item) => item.version)).toEqual([3]);
    expect((await catalog.get("template-1")).version).toBe(3);
    expect((await catalog.getVersion("template-1", 2)).version).toBe(2);
    await expect(catalog.getVersion("template-1", 9)).rejects.toThrow(/не найдена/);
  });

  it("searches a reference snapshot by text and entity type with a stable cursor", async () => {
    const snapshot: ReferenceCatalogSnapshot = {
      snapshotId: "s-1", sourceId: "technology-wires", contractVersion: 1, capturedUtc: "", sourceKind: "memory",
      versionFingerprint: "", sourceUri: null, sha256: "c".repeat(64), diagnostics: [],
      records: ["ПуГВ 0,5", "ПуГВ 0,75", "МГТФ 0,35"].map((key, index) => ({
        recordId: String(index), entityType: "wire", sourceKey: key, payload: { name: key }, sourceLocation: null,
      })),
    };
    const references = createInMemoryReferenceCatalog([snapshot]);
    const request = { text: "пугв", exactSourceKey: null, entityTypes: ["wire"], filters: [], filterLogic: "all" as const,
      sort: "source-key-asc" as const, pageSize: 1, cursor: null };
    const first = await references.searchCatalog("technology-wires", request);
    expect(first.items.map((item) => item.sourceKey)).toEqual(["ПуГВ 0,5"]);
    const second = await references.searchCatalog("technology-wires", { ...request, cursor: first.nextCursor });
    expect(second.items.map((item) => item.sourceKey)).toEqual(["ПуГВ 0,75"]);
    expect(second.nextCursor).toBeNull();
    await expect(references.searchCatalog("technology-terminals", request)).rejects.toBeInstanceOf(ReferenceCatalogApiError);
  });
});
