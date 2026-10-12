// Ported from TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission (THIRD_PARTY_NOTICES.md).
// Original: src/Techmap.Client/src/manufacturing/route-drawing-copy.ts @ 0a456d2f7bd23288111e871058ebf55bf14426cd (branch codex/integration-m5-22).
import { parseHarnessDesignDocument, type HarnessDesignDocument } from "../editor/model";
import type { EditorSceneObject } from "../editor/editor-types";
import type { WireBlankEnd } from "../WireBlankCatalog";
import { dimensionRouteKey, type DrawingDimension } from "../editor/drawing-dimensions";

export interface RouteDrawingCopy {
  readonly document: Omit<HarnessDesignDocument, "manufacturingRoute">;
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

/** A bounded, independent snapshot. Route state is never copied back into the design. */
export function parseRouteDrawingCopy(value: unknown): RouteDrawingCopy {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail();
  const candidate = value as Record<string, unknown>;
  if (Object.keys(candidate).length !== 2 || !("document" in candidate) || !("hiddenObjectIds" in candidate)) return fail();
  if (!Array.isArray(candidate.hiddenObjectIds) || candidate.hiddenObjectIds.length > 10_000 ||
    candidate.hiddenObjectIds.some(id => typeof id !== "string" || !id.trim() || id.length > 128) ||
    new Set(candidate.hiddenObjectIds).size !== candidate.hiddenObjectIds.length) return fail();
  inspect(candidate.document);
  let serialized: string;
  try { serialized = JSON.stringify(candidate.document); } catch { return fail(); }
  if (new TextEncoder().encode(serialized).length > maximumCopyBytes) return fail();
  const document = parseHarnessDesignDocument(JSON.parse(serialized));
  const { manufacturingRoute: _route, ...copy } = document;
  return { document: copy, hiddenObjectIds: [...candidate.hiddenObjectIds] };
}

export function createRouteDrawingCopy(document: HarnessDesignDocument, hiddenObjectIds: readonly string[] = []): RouteDrawingCopy {
  const { manufacturingRoute: _route, ...source } = document;
  return parseRouteDrawingCopy({ document: source, hiddenObjectIds });
}

/** Legacy migration keeps visibility by stable IDs; old overlay geometry remains a preview fallback. */
export function migrateLegacyRouteDrawingCopy(document: HarnessDesignDocument, drawingObjects: readonly { readonly id: string; readonly hidden: boolean; readonly points: readonly { readonly x: number; readonly y: number }[] }[]): RouteDrawingCopy {
  const base = createRouteDrawingCopy(document, drawingObjects.filter(item => item.hidden).map(item => item.id));
  const states = new Map(drawingObjects.map(item => [item.id, item]));
  const connectors = base.document.connectors.map(connector => {
    const state = states.get(connector.id), point = state?.points[0];
    if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return connector;
    return { ...connector, positions: { ...connector.positions, drawing: { ...point } } };
  });
  const wires = base.document.wires.map(wire => {
    const state = states.get(wire.id);
    if (!state || state.points.length !== wire.drawingRoute.length + 2 || state.points.some(point => !Number.isFinite(point.x) || !Number.isFinite(point.y))) return wire;
    return { ...wire, drawingRoute: state.points.slice(1, -1).map(point => ({ ...point })) };
  });
  const topology = base.document.physicalTopology;
  const physicalTopology = topology && { ...topology,
    nodes: topology.nodes.map(node => {
      const state = states.get(node.id), point = state?.points[0];
      return !node.connectorId && point && Number.isFinite(point.x) && Number.isFinite(point.y) ? { ...node, position: { ...point } } : node;
    }),
    segments: topology.segments.map(segment => {
      const state = states.get(segment.id);
      return state && state.points.length === segment.path.points.length + 2 && state.points.slice(1, -1).every(point => Number.isFinite(point.x) && Number.isFinite(point.y))
        ? { ...segment, path: { ...segment.path, points: state.points.slice(1, -1).map(point => ({ ...point })) } } : segment;
    }),
    joiningPipes: topology.joiningPipes?.map(pipe => {
      const state = states.get(pipe.id);
      return state && state.points.length === pipe.path.points.length + 2 && state.points.every(point => Number.isFinite(point.x) && Number.isFinite(point.y))
        ? { ...pipe, start: { ...state.points[0]! }, end: { ...state.points.at(-1)! }, path: { ...pipe.path, points: state.points.slice(1, -1).map(point => ({ ...point })) } } : pipe;
    }),
  };
  try { return createRouteDrawingCopy({ ...base.document, connectors, wires, ...(physicalTopology ? { physicalTopology } : {}) }, base.hiddenObjectIds); }
  catch { return base; }
}

/** IDs whose legacy overlay geometry has no lossless persisted-model mapping. */
export function legacyRouteDrawingCopyWarnings(document: HarnessDesignDocument, drawingObjects: readonly { readonly id: string; readonly points: readonly { readonly x: number; readonly y: number }[] }[]): readonly string[] {
  const migratable = new Map<string, (count: number) => boolean>();
  for (const connector of document.connectors) migratable.set(connector.id, count => count === 1);
  for (const wire of document.wires) migratable.set(wire.id, count => count === wire.drawingRoute.length + 2);
  for (const node of document.physicalTopology?.nodes ?? []) if (!node.connectorId) migratable.set(node.id, count => count === 1);
  for (const segment of document.physicalTopology?.segments ?? []) migratable.set(segment.id, count => count === segment.path.points.length + 2);
  for (const pipe of document.physicalTopology?.joiningPipes ?? []) migratable.set(pipe.id, count => count === pipe.path.points.length + 2);
  return drawingObjects.filter(item => item.points.length > 0 && !migratable.get(item.id)?.(item.points.length)).map(item => item.id);
}
