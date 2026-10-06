import type { EditorLayer, EditorSceneObject } from "./editor-types";

export function updateEditorObject(
  objects: readonly EditorSceneObject[],
  objectId: string,
  patch: Partial<Pick<EditorSceneObject, "label" | "x" | "y" | "color" | "metadata">>,
): readonly EditorSceneObject[] {
  return objects.map((object) => object.id === objectId ? { ...object, ...patch } : object);
}

export function toggleLayerVisibility(layers: readonly EditorLayer[], layerId: string): readonly EditorLayer[] {
  return layers.map((layer) => layer.id === layerId ? { ...layer, visible: !layer.visible } : layer);
}

export function toggleLayerLock(layers: readonly EditorLayer[], layerId: string): readonly EditorLayer[] {
  return layers.map((layer) => layer.id === layerId ? { ...layer, locked: !layer.locked } : layer);
}

export function moveLayer(
  layers: readonly EditorLayer[],
  layerId: string,
  targetIndex: number,
): readonly EditorLayer[] {
  const sourceIndex = layers.findIndex((layer) => layer.id === layerId);
  if (sourceIndex < 0 || targetIndex < 0 || targetIndex >= layers.length || sourceIndex === targetIndex) {
    return layers;
  }

  const next = [...layers];
  const [moved] = next.splice(sourceIndex, 1);
  if (!moved) return layers;
  next.splice(targetIndex, 0, moved);
  return next;
}
