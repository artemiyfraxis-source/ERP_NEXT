import type { EditorPoint, EditorSceneObject } from "./editor-types";
import { buildDrawingBom, type DrawingDocuments } from "./drawing-documents";
import { buildDrawingObjectIndices } from "./drawing-object-indices";
import { defaultLayerIds, type HarnessDesignDocument, type Point } from "./model";
import { projectComponentTemplateView, type ComponentTemplateViewInstance, type ResolveComponentTemplateAssetUrl } from "./component-template-view-renderer";

const indexId = (objectId: string) => `object-index:${objectId}`;

function pathMidpoint(points: readonly EditorPoint[]): EditorPoint | null {
  if (points.length < 2) return points[0] ?? null;
  const lengths = points.slice(1).map((point, index) => Math.hypot(point.x - points[index]!.x, point.y - points[index]!.y));
  const total = lengths.reduce((sum, length) => sum + length, 0);
  if (!total) return points[0]!;
  let remaining = total / 2;
  for (let index = 0; index < lengths.length; index += 1) {
    const length = lengths[index]!;
    if (remaining <= length) {
      const from = points[index]!, to = points[index + 1]!;
      const t = length ? remaining / length : 0;
      return { x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t };
    }
    remaining -= length;
  }
  return points.at(-1)!;
}

function defaultIndexPosition(object: EditorSceneObject): EditorPoint | null {
  if (object.kind === "connector") return { x: object.x + object.width + 6, y: object.y + 12 };
  const path = object.paths?.reduce<readonly EditorPoint[] | undefined>((longest, points) =>
    !longest || points.length > longest.length ? points : longest, undefined) ?? object.points ?? [];
  const middle = pathMidpoint(path);
  return middle ? { x: middle.x + 8, y: middle.y - 7 } : null;
}

function indexAnchor(object: EditorSceneObject): EditorPoint | null {
  const path = object.paths?.reduce<readonly EditorPoint[] | undefined>((longest, points) =>
    !longest || points.length > longest.length ? points : longest, undefined) ?? object.points ?? [];
  return pathMidpoint(path) ?? (object.kind === "connector"
    ? { x: object.x + object.width / 2, y: object.y + object.height / 2 }
    : null);
}

/**
 * A material index keeps a short leader near the normal of its represented
 * path. The user may choose the side, but cannot turn it into an arbitrary
 * diagonal that loses the visual association with the material.
 */
function leaderAnchorAndNormal(object: EditorSceneObject, label: EditorPoint): { anchor: EditorPoint; normal: EditorPoint } | null {
  if (object.kind === "connector") {
    const center = { x: object.x + object.width / 2, y: object.y + object.height / 2 };
    const direction = { x: label.x - center.x, y: label.y - center.y };
    if (Math.abs(direction.x) >= Math.abs(direction.y)) {
      const normal = { x: direction.x >= 0 ? 1 : -1, y: 0 };
      return { anchor: { x: center.x + normal.x * object.width / 2, y: center.y }, normal };
    }
    const normal = { x: 0, y: direction.y >= 0 ? 1 : -1 };
    return { anchor: { x: center.x, y: center.y + normal.y * object.height / 2 }, normal };
  }
  const path = object.paths?.reduce<readonly EditorPoint[] | undefined>((longest, points) =>
    !longest || points.length > longest.length ? points : longest, undefined) ?? object.points ?? [];
  if (path.length < 2) {
    const anchor = indexAnchor(object);
    return anchor ? { anchor, normal: { x: 0, y: -1 } } : null;
  }
  let best: { anchor: EditorPoint; dx: number; dy: number; distance: number } | null = null;
  for (let index = 1; index < path.length; index += 1) {
    const start = path[index - 1]!, end = path[index]!;
    const dx = end.x - start.x, dy = end.y - start.y, lengthSquared = dx * dx + dy * dy;
    if (!lengthSquared) continue;
    const t = Math.max(0, Math.min(1, ((label.x - start.x) * dx + (label.y - start.y) * dy) / lengthSquared));
    const anchor = { x: start.x + dx * t, y: start.y + dy * t };
    const distance = Math.hypot(label.x - anchor.x, label.y - anchor.y);
    if (!best || distance < best.distance) best = { anchor, dx, dy, distance };
  }
  if (!best) return null;
  const length = Math.hypot(best.dx, best.dy);
  const left = { x: -best.dy / length, y: best.dx / length };
  const right = { x: -left.x, y: -left.y };
  const toLabel = { x: label.x - best.anchor.x, y: label.y - best.anchor.y };
  const normal = left.x * toLabel.x + left.y * toLabel.y >= 0 ? left : right;
  return { anchor: best.anchor, normal };
}

function constrainedIndexPosition(object: EditorSceneObject, requested: EditorPoint): { position: EditorPoint; leader: { anchor: EditorPoint; normal: EditorPoint } | null } {
  const leader = leaderAnchorAndNormal(object, requested);
  if (!leader) return { position: requested, leader: null };
  const distance = Math.hypot(requested.x - leader.anchor.x, requested.y - leader.anchor.y);
  const desired = distance ? { x: (requested.x - leader.anchor.x) / distance, y: (requested.y - leader.anchor.y) / distance } : leader.normal;
  const angle = Math.acos(Math.max(-1, Math.min(1, desired.x * leader.normal.x + desired.y * leader.normal.y)));
  if (angle <= Math.PI / 18) return { position: requested, leader };
  const sign = leader.normal.x * desired.y - leader.normal.y * desired.x >= 0 ? 1 : -1;
  const c = Math.cos(Math.PI / 18), s = Math.sin(Math.PI / 18) * sign;
  const direction = { x: leader.normal.x * c - leader.normal.y * s, y: leader.normal.x * s + leader.normal.y * c };
  return { position: { x: leader.anchor.x + direction.x * distance, y: leader.anchor.y + direction.y * distance }, leader };
}

/** Library pictures can extend beyond a connector's legacy scene rectangle.
 * Persist their visual bounds on the index so its leader follows the picture,
 * not that obsolete rectangle, during both drawing and dragging. */
function connectorIndexOwner(object: EditorSceneObject, metadata?: Readonly<Record<string, string>>): EditorSceneObject {
  if (object.kind !== "connector" || !metadata) return object;
  const x = Number(metadata.indexOwnerMinX), y = Number(metadata.indexOwnerMinY);
  const maxX = Number(metadata.indexOwnerMaxX), maxY = Number(metadata.indexOwnerMaxY);
  if (![x, y, maxX, maxY].every(Number.isFinite) || maxX <= x || maxY <= y) return object;
  return { ...object, x, y, width: maxX - x, height: maxY - y };
}

function bomIndexes(document: HarnessDesignDocument): Map<string, string> {
  const result = new Map<string, string>();
  const labelableIds = new Set([
    ...document.connectors.map(item => item.id),
    ...document.wires.map(item => item.id),
    ...document.cables.map(item => item.id),
    ...(document.physicalTopology?.coverings ?? []).map(item => item.id),
  ]);
  for (const row of buildDrawingBom(document)) {
    // A terminal row shares the connector's object ID but is not its index.
    if (["terminal", "terminal-unpinned"].includes(JSON.parse(row.key)[0])) continue;
    const indexes = row.index.split(/,\s*/);
    let index = 0;
    for (const objectId of row.objectIds) {
      if (!labelableIds.has(objectId)) continue;
      if (!result.has(objectId)) result.set(objectId, indexes[index] ?? row.index);
      index += 1;
    }
  }
  for (const cable of document.cables) {
    const cableIndex = result.get(cable.id);
    if (cableIndex) for (const wireId of cable.memberWireIds) result.set(wireId, cableIndex);
  }
  return result;
}

export function drawingObjectIndexScene(
  document: HarnessDesignDocument,
  baseObjects: readonly EditorSceneObject[],
): EditorSceneObject[] {
  const docs = document.drawingDocuments;
  const scale = Math.max(.25, Math.min(4, docs?.indexScale ?? 1));
  const persisted = docs?.indexOffsets ?? {};
  const byBom = bomIndexes(document);
  const generated = buildDrawingObjectIndices(baseObjects);
  return baseObjects.flatMap(object => {
    if (object.kind !== "connector" && object.kind !== "wire" && object.kind !== "physical-covering") return [];
    const position = defaultIndexPosition(object);
    if (!position) return [];
    const label = byBom.get(object.id) ?? generated.get(object.id) ?? (object.kind === "connector" ? object.label : undefined);
    if (!label) return [];
    const offset = persisted[object.id] ?? { x: 0, y: 0 };
    const requested = { x: position.x + offset.x, y: position.y + offset.y };
    const constrained = constrainedIndexPosition(object, requested);
    const x = constrained.position.x, y = constrained.position.y, leader = constrained.leader;
    const width = Math.max(16, label.length * 8 * scale + 8 * scale), height = 20 * scale;
    return [{
      id: indexId(object.id),
      layerId: defaultLayerIds.materialIndexes,
      kind: "object-index" as const,
      label,
      x,
      y,
      width,
      height,
      color: "#17384b",
      ...(leader ? { points: [leader.anchor, constrained.position] } : {}),
      metadata: { indexObjectId: object.id, indexBaseX: String(position.x), indexBaseY: String(position.y), indexScale: String(scale), ...(leader ? { indexAnchorX: String(leader.anchor.x), indexAnchorY: String(leader.anchor.y) } : {}) },
    }];
  });
}

export function alignDrawingConnectorIndexes(
  document: HarnessDesignDocument,
  scene: readonly EditorSceneObject[],
  instances: readonly ComponentTemplateViewInstance[],
  resolveAssetUrl?: ResolveComponentTemplateAssetUrl,
): EditorSceneObject[] {
  const connectors = new Map(scene.filter(object => object.kind === "connector").map(object => [object.id, object]));
  const views = new Map(instances.map(instance => [instance.objectId, instance]));
  return scene.map(object => {
    if (object.kind !== "object-index") return object;
    const ownerId = object.metadata?.indexObjectId;
    const owner = ownerId ? connectors.get(ownerId) : undefined;
    const instance = ownerId ? views.get(ownerId) : undefined;
    if (!owner || !instance) return object;
    const projection = projectComponentTemplateView(instance, "drawing", { x: owner.x, y: owner.y }, resolveAssetUrl);
    if (!projection) return object;
    const base = { x: projection.bounds.maxX + 6, y: projection.bounds.minY + 12 };
    const offset = document.drawingDocuments?.indexOffsets?.[owner.id] ?? { x: 0, y: 0 };
    const requested = { x: base.x + offset.x, y: base.y + offset.y };
    const ownerForIndex = {
      ...owner,
      x: projection.bounds.minX,
      y: projection.bounds.minY,
      width: projection.bounds.maxX - projection.bounds.minX,
      height: projection.bounds.maxY - projection.bounds.minY,
    };
    const constrained = constrainedIndexPosition(ownerForIndex, requested);
    return { ...object, x: constrained.position.x, y: constrained.position.y, metadata: {
      ...object.metadata, indexBaseX: String(base.x), indexBaseY: String(base.y),
      indexOwnerMinX: String(projection.bounds.minX), indexOwnerMinY: String(projection.bounds.minY),
      indexOwnerMaxX: String(projection.bounds.maxX), indexOwnerMaxY: String(projection.bounds.maxY),
      ...(constrained.leader ? { indexAnchorX: String(constrained.leader.anchor.x), indexAnchorY: String(constrained.leader.anchor.y) } : {}),
    }, ...(constrained.leader ? { points: [constrained.leader.anchor, constrained.position] } : {}) };
  });
}

export function moveDrawingIndex(
  document: HarnessDesignDocument,
  labelObjectId: string,
  point: Point,
  currentScene: readonly EditorSceneObject[],
): DrawingDocuments | null {
  if (!labelObjectId.startsWith("object-index:")) return null;
  const label = currentScene.find(object => object.id === labelObjectId && object.kind === "object-index");
  const objectId = label?.metadata?.indexObjectId;
  const baseX = Number(label?.metadata?.indexBaseX), baseY = Number(label?.metadata?.indexBaseY);
  if (!objectId || !Number.isFinite(baseX) || !Number.isFinite(baseY)) return null;
  const owner = currentScene.find(object => object.id === objectId && object.kind !== "object-index");
  const desired = owner
    ? constrainedIndexPosition(connectorIndexOwner(owner, label?.metadata), { x: point.x, y: point.y }).position
    : point;
  const docs = document.drawingDocuments ?? { tables: [], leaders: [], bomOrder: [] };
  const offset: EditorPoint = { x: desired.x - baseX, y: desired.y - baseY };
  return { ...docs, indexOffsets: { ...docs.indexOffsets, [objectId]: offset } };
}
