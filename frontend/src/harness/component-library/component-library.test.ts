import { describe, expect, it } from "vitest";
import { ReferenceCatalogApiError, type ReferenceCatalogSearchPage } from "../reference-catalog";
import { addLegacyArticleBindingsToV3, articleBindingsFromTemplateV3, canPublishTemplate, connectorArticleInputs, connectorArticleSearchRequest, createTemplateImageNodeV2, isTemplateAssetReferencedV2, isTemplateUndoShortcut, nextTemplateSelectionV2, pushTemplateUndo, searchTerminalArticles, terminalArticleInputs, terminalArticleSearchRequest, terminalCatalogLabel } from "./ComponentLibrary";
import { addNodeV2, newTemplateContentV2 } from "./template-commands-v2";
import { validateTemplateContentV2 } from "./template-model-v2";
import {
  addBasicNodeV3,
  insertNodePointV3,
  moveNodePointV3,
  newTemplateContentV3,
  upsertArticleVariantV3,
} from "./template-commands-v3";
import { createE4ConnectorSeriesTableFromV3 } from "./e4-connector-series-table";
import { createTemplateContentV5FromEditor, projectTemplateContentV5ToV3 } from "./template-model-v5";

describe("component library UI", () => {
  it("keeps at least 100 Ctrl+Z states", () => {
    let stack: ReturnType<typeof newTemplateContentV3>[] = [];
    const states = Array.from({ length: 101 }, () => newTemplateContentV3());
    for (const state of states) stack = pushTemplateUndo(stack, state);
    expect(stack).toHaveLength(100);
    expect(stack[0]).toBe(states[1]);
    expect(stack.at(-1)).toBe(states[100]);
  });
  it("supports replacement, additive toggle, and blank selection semantics", () => {
    expect(nextTemplateSelectionV2(["a"], "b", false)).toEqual(["b"]);
    expect(nextTemplateSelectionV2(["a"], "b", true)).toEqual(["a", "b"]);
    expect(nextTemplateSelectionV2(["a", "b"], "a", true)).toEqual(["b"]);
    expect(nextTemplateSelectionV2(["a"], null, false)).toEqual([]);
  });
  it("builds active connector lookup requests and maps unique connector records", () => {
    expect(connectorArticleSearchRequest("  XH  ")).toMatchObject({
      text: "XH", entityTypes: ["connector"], sort: "relevance", pageSize: 30, cursor: null,
    });
    const record = (entityType: string, sourceKey: string) => ({
      recordId: crypto.randomUUID(), entityType, sourceKey, payload: {}, sourceLocation: null,
    });
    expect(connectorArticleInputs([
      record("connector", " B2B-XH-A "),
      record("connector", "B2B-XH-A"),
      record("terminal", "SXH-001T-P0.6"),
      record("connector", "B10B-XH-A"),
    ])).toEqual([
      { sourceId: "technology-connectors", entityType: "connector", articleKey: "B2B-XH-A" },
      { sourceId: "technology-connectors", entityType: "connector", articleKey: "B10B-XH-A" },
    ]);
  });
  it("builds terminal lookup requests and maps only unique terminal records", () => {
    expect(terminalArticleSearchRequest("  SXH  ")).toMatchObject({
      text: "SXH", entityTypes: ["terminal"], sort: "relevance", pageSize: 30, cursor: null,
    });
    const record = (entityType: string, sourceKey: string) => ({
      recordId: crypto.randomUUID(), entityType, sourceKey, payload: {}, sourceLocation: null,
    });
    expect(terminalArticleInputs([
      record("terminal", " SXH-001T-P0.6 "),
      record("terminal", "SXH-001T-P0.6"),
      record("connector", "B2B-XH-A"),
      record("terminal", "SXH-002T-P0.6"),
    ])).toEqual([
      { sourceId: "technology-terminals", entityType: "terminal", articleKey: "SXH-001T-P0.6" },
      { sourceId: "technology-terminals", entityType: "terminal", articleKey: "SXH-002T-P0.6" },
    ]);
  });
  it("falls back to the active combined database when the dedicated terminal snapshot is absent", async () => {
    const record = {
      recordId: crypto.randomUUID(), entityType: "terminal", sourceKey: "3:JST|18:SPH-002T-P0.5S|0:|3:PHR",
      payload: { reelArticle: "SPH-002T-P0.5S" }, sourceLocation: null,
    };
    const page: ReferenceCatalogSearchPage = {
      snapshotId: crypto.randomUUID(), snapshotSha256: "a".repeat(64), items: [record], nextCursor: null,
    };
    const sources: string[] = [];
    const api = { searchCatalog: async (sourceId: string) => {
      sources.push(sourceId);
      if (sourceId === "technology-terminals")
        throw new ReferenceCatalogApiError("missing", "catalog_active_snapshot_not_found");
      return page;
    } };

    await expect(searchTerminalArticles(api, "SPH")).resolves.toEqual([{
      sourceId: "technology-database", entityType: "terminal", articleKey: record.sourceKey,
    }]);
    expect(sources).toEqual(["technology-terminals", "technology-database"]);
  });
  it("shows a Russian empty-base message only when both terminal sources are absent", async () => {
    const api = { searchCatalog: async () => {
      throw new ReferenceCatalogApiError("missing", "catalog_active_snapshot_not_found");
    } };
    await expect(searchTerminalArticles(api, "SPH")).rejects.toThrow(
      "Справочник терминалов не загружен. Загрузите и опубликуйте БД.ТЕР в разделе «Справочники».",
    );

    const unavailable = { searchCatalog: async () => {
      throw new ReferenceCatalogApiError("offline", "server_unavailable");
    } };
    await expect(searchTerminalArticles(unavailable, "SPH")).rejects.toThrow("offline");
  });
  it("connects Ctrl+Z and Cmd+Z to template undo", () => {
    expect(isTemplateUndoShortcut({ ctrlKey: true, metaKey: false, key: "z" })).toBe(true);
    expect(isTemplateUndoShortcut({ ctrlKey: false, metaKey: true, key: "Z" })).toBe(true);
    expect(isTemplateUndoShortcut({ ctrlKey: false, metaKey: false, key: "z" })).toBe(false);
    expect(isTemplateUndoShortcut({ ctrlKey: true, metaKey: false, key: "z", target: { tagName: "INPUT" } as unknown as EventTarget })).toBe(false);
    expect(isTemplateUndoShortcut({ ctrlKey: true, metaKey: false, key: "z", target: { tagName: "textarea" } as unknown as EventTarget })).toBe(false);
    expect(isTemplateUndoShortcut({ ctrlKey: true, metaKey: false, key: "z", target: { isContentEditable: true } as unknown as EventTarget })).toBe(false);
  });
  it("shows technology terminal composite keys as manufacturer, article and series", () => {
    const record = {
      recordId: crypto.randomUUID(), entityType: "terminal",
      sourceKey: "3:JST|14:SPH-002T-P0.5S|0:|3:PHR",
      payload: { manufacturer: "JST", reelArticle: "SPH-002T-P0.5S", series: "PHR" },
      sourceLocation: null,
    };
    expect(terminalCatalogLabel(record)).toBe("JST SPH-002T-P0.5S PHR");
    expect(terminalCatalogLabel({ ...record, payload: {} })).toBe("JST SPH-002T-P0.5S PHR");
    const bagOnly = {
      ...record,
      sourceKey: "6:Атлант|0:|10:1/02506-02|7:1/02506",
      payload: { manufacturer: "Атлант", reelArticle: "", bagArticle: "1/02506-02", series: "1/02506" },
    };
    expect(terminalCatalogLabel(bagOnly)).toBe("Атлант 1/02506-02 1/02506");
    expect(terminalCatalogLabel({ ...bagOnly, payload: {} })).toBe("Атлант 1/02506-02 1/02506");
  });

  it("publishes only complete changed templates for users who may edit and pauses on a conflict", () => {
    const ready = { canEdit: true, dirty: true, busy: false, conflict: false, assetMismatch: false, code: "JST PH", name: "Шаговый" };
    expect(canPublishTemplate(ready)).toBe(true);
    expect(canPublishTemplate({ ...ready, dirty: false })).toBe(false);
    expect(canPublishTemplate({ ...ready, canEdit: false })).toBe(false);
    expect(canPublishTemplate({ ...ready, conflict: true })).toBe(false);
    expect(canPublishTemplate({ ...ready, busy: true })).toBe(false);
    expect(canPublishTemplate({ ...ready, assetMismatch: true })).toBe(false);
    expect(canPublishTemplate({ ...ready, code: " " })).toBe(false);
  });

  it("uses v3 article variants as the saved lookup index and imports legacy bindings once", () => {
    const legacy = [
      { sourceId: "db", entityType: "connector", articleKey: "A-01" },
      { sourceId: "db", entityType: "connector", articleKey: "A-02" },
    ];
    let content = addLegacyArticleBindingsToV3(newTemplateContentV3(), legacy);
    content = addLegacyArticleBindingsToV3(content, legacy);
    [content] = upsertArticleVariantV3(content, {
      id: content.articleVariants[0]!.id,
      sourceId: "db",
      entityType: "connector",
      articleKey: "A-01-RENAMED",
    });

    expect(content.articleVariants).toHaveLength(2);
    expect(articleBindingsFromTemplateV3(content)).toEqual([
      { sourceId: "db", entityType: "connector", articleKey: "A-01-RENAMED" },
      { sourceId: "db", entityType: "connector", articleKey: "A-02" },
    ]);
  });

  it("creates a valid underlay image and detects its references", () => {
    const content = newTemplateContentV2();
    const view = content.views[0]!;
    const layer = view.layers[0]!;
    const assetId = crypto.randomUUID();
    const node = createTemplateImageNodeV2(assetId, layer.id);
    const withAsset = { ...content, assets: [{ assetId, fileName: "reference.png", mediaType: "image/png", sha256: "a".repeat(64), sizeBytes: 512 }] };
    const placed = addNodeV2(withAsset, view.id, layer.id, node, 0);

    expect(node.geometry).toMatchObject({ assetId, cropX: 0, cropY: 0, cropWidth: 1, cropHeight: 1, underlay: true });
    expect(node.opacity).toBe(1);
    expect(placed.views[0]!.layers[0]!.nodes[0]).toBe(node);
    expect(isTemplateAssetReferencedV2(withAsset, assetId)).toBe(false);
    expect(isTemplateAssetReferencedV2(placed, assetId)).toBe(true);
    expect(validateTemplateContentV2(placed)).toEqual({ valid: true, diagnostics: [] });
  });

  it("creates advanced path primitives and preserves edited points through the saved v5 envelope", () => {
    const initial = newTemplateContentV3();
    const view = initial.views[1]!;
    const layer = view.layers[0]!;
    const [withPolyline, polylineId] = addBasicNodeV3(initial, view.id, layer.id, "polyline");
    const [withBezier, bezierId] = addBasicNodeV3(withPolyline, view.id, layer.id, "bezier");
    const [withContour, contourId] = addBasicNodeV3(withBezier, view.id, layer.id, "closedContour");
    const moved = moveNodePointV3(withContour, view.id, layer.id, polylineId, 1, 15, -10);
    const edited = insertNodePointV3(moved, view.id, layer.id, contourId, 3, 80, 140);

    const nodes = edited.views[1]!.layers[0]!.nodes;
    expect(nodes.map(node => node.kind)).toEqual(["polyline", "bezier", "closedContour"]);
    const polyline = nodes.find(node => node.id === polylineId)!;
    const bezier = nodes.find(node => node.id === bezierId)!;
    const contour = nodes.find(node => node.id === contourId)!;
    if (polyline.kind !== "polyline" || bezier.kind !== "bezier" || contour.kind !== "closedContour")
      throw new Error("Expected advanced path primitives.");
    expect(polyline.geometry.points[1]).toMatchObject({ x: { value: 175 }, y: { value: 90 } });
    expect(bezier.geometry.points).toHaveLength(4);
    expect(contour.geometry.points.at(-1)).toMatchObject({ x: { value: 80 }, y: { value: 140 } });

    const envelope = createTemplateContentV5FromEditor(
      edited,
      createE4ConnectorSeriesTableFromV3(edited),
      [],
    ).content;
    const restored = projectTemplateContentV5ToV3(JSON.parse(JSON.stringify(envelope)));
    expect(restored.views[1]!.layers[0]!.nodes).toEqual(nodes);
  });
});
