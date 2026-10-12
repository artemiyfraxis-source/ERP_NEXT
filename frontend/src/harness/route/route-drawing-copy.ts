// Ported from TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission (THIRD_PARTY_NOTICES.md).
// Original: src/Techmap.Client/src/manufacturing/route-drawing-copy.ts @ 0a456d2f7bd23288111e871058ebf55bf14426cd (branch codex/integration-m5-22).
// ERP changes: the route is stored on the server next to the drawing (techcard_harness_routes), so a snapshot no
// longer copies the whole drawing. A source-mode copy keeps only the hidden object IDs and is drawn over the position's
// drawing at the route's drawing revision; only an isolated copy carries its own (small) geometry document.
// Not ported: the localStorage legacy migration (migrateLegacyRouteDrawingCopy, legacyRouteDrawingCopyWarnings).
import { parseHarnessDesignDocument, type HarnessDesignDocument } from "../editor/model";
import type { EditorSceneObject } from "../editor/editor-types";
import type { WireBlankEnd } from "../WireBlankCatalog";
import { dimensionRouteKey, type DrawingDimension } from "../editor/drawing-dimensions";

export type RouteDrawingDocument = Omit<HarnessDesignDocument, "manufacturingRoute">;

export interface RouteDrawingCopy {
  /** ERP: only an isolated copy has its own geometry; absent — the position's drawing (see resolveRouteDrawingCopy). */
  readonly document?: RouteDrawingDocument;
  readonly hiddenObjectIds: readonly string[];
}

/**
 * Creates the second, self-contained copy used by the Route "Изолировать"
 * action.  A scene selection may contain a physical segment instead of its
 * material wire; both forms are accepted.  Physical routing is deliberately
 * discarded.  The selected wires remain ordinary drawing wires and keep their
 * immutable material and end-strip profiles. Endpoint coordinates are stored
 * separately, so connectors that were not selected are never pulled into the
 * detached copy.
 */
export function createIndependentIsolatedDocument(
  source: HarnessDesignDocument,
  scene: readonly EditorSceneObject[],
  selectedObjectIds: readonly string[],
  inheritedEndStyles?: ReadonlyMap<string, Readonly<{ from: WireBlankEnd; to: WireBlankEnd }>>,
): HarnessDesignDocument | null {
  const selected = new Set(selectedObjectIds);
  const wireIds = new Set<string>();
  const connectorIds = new Set<string>();
  for (const object of scene) {
    if (!selected.has(object.id)) continue;
    if (object.kind === "wire") wireIds.add(object.id);
    if (object.kind === "connector") connectorIds.add(object.id);
    if (object.kind === "physical-segment") for (const wireId of object.pipe?.wireIds ?? []) wireIds.add(wireId);
  }
  if (!wireIds.size && !connectorIds.size) return null;

  const connectors = source.connectors.filter(connector => connectorIds.has(connector.id));
  const sceneById = new Map(scene.map(object => [object.id, object]));
  const selectedWires = source.wires.filter(wire => wireIds.has(wire.id));
  const anchors = selectedWires.map(wire => {
    const points = sceneById.get(wire.id)?.points;
    if (!points || points.length < 2) throw new Error("Не удалось определить концы выбранного провода. Дождитесь загрузки рисунка.");
    return { wire, from: points[0]!, to: points.at(-1)!, fromRetained: connectorIds.has(wire.from.connectorId), toRetained: connectorIds.has(wire.to.connectorId) };
  });
  const allPoints = anchors.flatMap(item => [item.from, item.to]);
  const leftBoundary = allPoints.length ? Math.min(...allPoints.map(point => point.x)) : 0;
  const rightBoundary = Math.max(leftBoundary + 160, ...allPoints.map(point => point.x));
  const retainedX = anchors.flatMap(item => [...(item.fromRetained ? [item.from.x] : []), ...(item.toRetained ? [item.to.x] : [])]);
  const freeLeft = Math.min(leftBoundary, ...retainedX.map(x => x - 160));
  const freeRight = Math.max(rightBoundary, ...retainedX.map(x => x + 160));
  const freeWires = anchors.filter(item => !item.fromRetained && !item.toRetained)
    .sort((a, b) => a.from.y - b.from.y || a.wire.id.localeCompare(b.wire.id));
  const laneSpacing = Math.max(14, ...anchors.map(item => Number(sceneById.get(item.wire.id)?.metadata?.drawingWidth ?? 3) * 2 + 6));
  const laneOrigin = freeWires[0]?.from.y ?? 0;
  const wires = anchors.map(({ wire, from, to, fromRetained, toRetained }) => {
    let endpointFrom = { ...from }, endpointTo = { ...to };
    if (fromRetained && !toRetained) {
      endpointTo = { x: to.x < from.x ? freeLeft : freeRight, y: from.y };
    } else if (toRetained && !fromRetained) {
      endpointFrom = { x: from.x > to.x ? freeRight : freeLeft, y: to.y };
    } else if (!fromRetained && !toRetained) {
      const y = laneOrigin + freeWires.findIndex(item => item.wire.id === wire.id) * laneSpacing;
      endpointFrom = { x: from.x <= to.x ? leftBoundary : rightBoundary, y };
      endpointTo = { x: from.x <= to.x ? rightBoundary : leftBoundary, y };
    }
    return {
      ...wire,
      from: fromRetained ? wire.from : { connectorId: `isolated:${wire.id}:from`, contactId: "free" },
      to: toRetained ? wire.to : { connectorId: `isolated:${wire.id}:to`, contactId: "free" },
      colorSource: null,
      drawingRoute: [],
      drawingEndpoints: { from: endpointFrom, to: endpointTo },
      drawingEndStyles: wire.drawingEndStyles ?? inheritedEndStyles?.get(wire.id) ?? { from: "cut" as const, to: "cut" as const },
    };
  });
  if (!wires.length && !connectors.length) return null;
  const wireSet = new Set(wires.map(wire => wire.id));
  const cables = source.cables.filter(cable => cable.memberWireIds.every(id => wireSet.has(id)));
  const documents = source.drawingDocuments;
  const isolated: HarnessDesignDocument = {
    schemaVersion: source.schemaVersion,
    ...(source.requiredWriterContractVersion === undefined ? {} : { requiredWriterContractVersion: source.requiredWriterContractVersion }),
    connectors,
    wires,
    cables,
    junctions: [],
    diffPairs: source.diffPairs.filter(pair => pair.wireIds.every(id => wireSet.has(id))),
    screens: [],
    views: source.views,
    ...(source.customWireColors ? { customWireColors: source.customWireColors } : {}),
    ...(documents ? {
      drawingDocuments: {
        ...documents,
        dimensions: [],
        leaders: [],
        rails: [],
        bomOrder: [],
        bomText: {},
        specificationItems: documents.specificationItems?.filter(item => !item.objectId || wireSet.has(item.objectId) || connectorIds.has(item.objectId)),
      },
    } : {}),
  };
  // Physical bends are deliberately removed, so partial source dimensions no
  // longer have meaningful anchors. Keep each full wire measurement and bind
  // it to the independent endpoints; otherwise show the saved wire length.
  // These are annotations of the copy, never a new production measurement.
  const dimensions: DrawingDimension[] = wires.map((wire, index) => {
    const total = documents?.dimensions?.find(d => d.wireId === wire.id && d.from === 0 && d.to === d.pointCount - 1);
    return {
      id: total?.id ?? `isolated-dimension:${index}`,
      wireId: wire.id, from: 0, to: 1, pointCount: 2,
      routeKey: dimensionRouteKey(isolated, wire),
      mode: total?.mode ?? documents?.dimensionMode ?? "aligned",
      modeOverride: total?.modeOverride ?? false,
      offset: total?.offset ?? 40 + index * 18,
      lengthMm: total?.lengthMm ?? wire.lengthMm,
      auxiliary: true,
    };
  });
  const withDimensions: HarnessDesignDocument = { ...isolated, drawingDocuments: {
    ...(isolated.drawingDocuments ?? { tables: [], leaders: [], bomOrder: [] }),
    dimensions, showDimensions: documents?.showDimensions ?? true,
  } };
  // parseHarnessDesignDocument normalizes defaults and validates that no
  // dangling source references survived the projection.
  return parseHarnessDesignDocument(withDimensions);
}

const maximumCopyBytes = 1024 * 1024;
const fail = (): never => { throw new Error("Копия чертежа в маршруте повреждена или превышает допустимый размер."); };

function inspect(value: unknown, depth = 0, count = { value: 0 }): void {
  if (++count.value > 100_000 || depth > 128) fail();
  if (Array.isArray(value)) {
    if (value.length > 10_000) fail();
    value.forEach(item => inspect(item, depth + 1, count));
  } else if (value !== null && typeof value === "object") {
    if (Object.prototype.hasOwnProperty.call(value, "manufacturingRoute")) fail();
    for (const [key, item] of Object.entries(value)) {
      if (key.length > 256) fail();
      inspect(item, depth + 1, count);
    }
  }
}

/** A bounded, independent snapshot. Route state is never copied back into the design.
 * ERP: `document` is present only in an isolated copy; a source copy is the hidden IDs alone. */
export function parseRouteDrawingCopy(value: unknown): RouteDrawingCopy {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail();
  const candidate = value as Record<string, unknown>;
  if (!("hiddenObjectIds" in candidate) || Object.keys(candidate).some(key => key !== "document" && key !== "hiddenObjectIds")) return fail();
  if (!Array.isArray(candidate.hiddenObjectIds) || candidate.hiddenObjectIds.length > 10_000 ||
    candidate.hiddenObjectIds.some(id => typeof id !== "string" || !id.trim() || id.length > 128) ||
    new Set(candidate.hiddenObjectIds).size !== candidate.hiddenObjectIds.length) return fail();
  const hiddenObjectIds = [...candidate.hiddenObjectIds] as string[];
  if (candidate.document === undefined) return { hiddenObjectIds };
  inspect(candidate.document);
  let serialized: string;
  try { serialized = JSON.stringify(candidate.document); } catch { return fail(); }
  if (new TextEncoder().encode(serialized).length > maximumCopyBytes) return fail();
  const document = parseHarnessDesignDocument(JSON.parse(serialized));
  const { manufacturingRoute: _route, ...copy } = document;
  return { document: copy, hiddenObjectIds };
}

/** ERP: a source-mode copy — what to hide on the position's drawing; the drawing itself is not copied. */
export function createRouteDrawingCopy(hiddenObjectIds: readonly string[] = []): RouteDrawingCopy {
  return parseRouteDrawingCopy({ hiddenObjectIds });
}

/** ERP: an isolated copy — its own bounded geometry (from createIndependentIsolatedDocument) plus hidden IDs. */
export function createIsolatedRouteDrawingCopy(document: HarnessDesignDocument, hiddenObjectIds: readonly string[] = []): RouteDrawingCopy {
  const { manufacturingRoute: _route, ...source } = document;
  return parseRouteDrawingCopy({ document: source, hiddenObjectIds });
}

/** ERP: the drawing a copy is shown on — its own geometry, or the position's drawing (the revision the route was
 * built on). */
export function resolveRouteDrawingCopy(copy: RouteDrawingCopy, drawing: HarnessDesignDocument): RouteDrawingDocument {
  if (copy.document) return copy.document;
  const { manufacturingRoute: _route, ...source } = drawing;
  return source;
}

