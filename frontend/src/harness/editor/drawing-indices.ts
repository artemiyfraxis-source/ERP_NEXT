import type { EditorPoint, EditorSceneObject } from "./editor-types";
import { buildDrawingBom, type DrawingDocuments } from "./drawing-documents";
import { buildDrawingObjectIndices } from "./drawing-object-indices";
import type { HarnessDesignDocument, Point } from "./model";
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
    const x = position.x + offset.x, y = position.y + offset.y;
    const width = Math.max(16, label.length * 8 * scale + 8 * scale), height = 20 * scale;
    return [{
      id: indexId(object.id),
      layerId: object.layerId,
      kind: "object-index" as const,
      label,
      x,
      y,
      width,
      height,
      color: "#17384b",
      metadata: { indexObjectId: object.id, indexBaseX: String(position.x), indexBaseY: String(position.y), indexScale: String(scale) },
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
    return { ...object, x: base.x + offset.x, y: base.y + offset.y, metadata: { ...object.metadata, indexBaseX: String(base.x), indexBaseY: String(base.y) } };
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
  const docs = document.drawingDocuments ?? { tables: [], leaders: [], bomOrder: [] };
  const offset: EditorPoint = { x: point.x - baseX, y: point.y - baseY };
  return { ...docs, indexOffsets: { ...docs.indexOffsets, [objectId]: offset } };
}
