import type { EditorCamera, EditorPoint } from "./editor-types";

// The fixed selector starts at 25%, while fit-to-screen can choose a smaller
// value for tall E4 tables and Ctrl+wheel must keep working smoothly from it.
export const minimumEditorZoom = 0.01;
export const maximumEditorZoom = 4;
export const editorZoomPresets = [0.25, 0.5, 1, 1.5, 2] as const;

export interface EditorViewportSize {
  readonly width: number;
  readonly height: number;
}

export interface EditorSceneBounds {
  readonly minX: number;
  readonly minY: number;
  readonly maxX: number;
  readonly maxY: number;
}

export function clampEditorZoom(value: number): number {
  if (Number.isNaN(value)) return minimumEditorZoom;
  return Math.min(maximumEditorZoom, Math.max(minimumEditorZoom, value));
}

export function screenToWorld(camera: EditorCamera, point: EditorPoint): EditorPoint {
  return {
    x: (point.x - camera.offsetX) / camera.zoom,
    y: (point.y - camera.offsetY) / camera.zoom,
  };
}

export function worldToScreen(camera: EditorCamera, point: EditorPoint): EditorPoint {
  return {
    x: point.x * camera.zoom + camera.offsetX,
    y: point.y * camera.zoom + camera.offsetY,
  };
}

export function panEditorCamera(camera: EditorCamera, deltaX: number, deltaY: number): EditorCamera {
  return {
    ...camera,
    offsetX: camera.offsetX + deltaX,
    offsetY: camera.offsetY + deltaY,
  };
}

export function zoomEditorCameraAt(
  camera: EditorCamera,
  anchor: EditorPoint,
  zoom: number,
): EditorCamera {
  const nextZoom = clampEditorZoom(zoom);
  const anchoredWorldPoint = screenToWorld(camera, anchor);
  return {
    zoom: nextZoom,
    offsetX: anchor.x - anchoredWorldPoint.x * nextZoom,
    offsetY: anchor.y - anchoredWorldPoint.y * nextZoom,
  };
}

export function zoomEditorCameraFromWheel(
  camera: EditorCamera,
  anchor: EditorPoint,
  deltaY: number,
  ctrlKey: boolean,
): EditorCamera {
  if (!ctrlKey || !Number.isFinite(deltaY) || deltaY === 0) return camera;
  const factor = deltaY < 0 ? 1.12 : 1 / 1.12;
  return zoomEditorCameraAt(camera, anchor, camera.zoom * factor);
}

/**
 * Centers the visible scene in a viewport while keeping a small, predictable
 * margin around it.  Bounds are in world coordinates; viewport dimensions and
 * the returned camera offsets are in CSS pixels.
 */
export function fitEditorCameraToBounds(
  camera: EditorCamera,
  bounds: EditorSceneBounds | null,
  viewport: EditorViewportSize,
  margin = 48,
): EditorCamera {
  const width = Number.isFinite(viewport.width) ? Math.max(1, viewport.width) : 1;
  const height = Number.isFinite(viewport.height) ? Math.max(1, viewport.height) : 1;
  if (!bounds || ![bounds.minX, bounds.minY, bounds.maxX, bounds.maxY].every(Number.isFinite)) return camera;
  const minX = Math.min(bounds.minX, bounds.maxX);
  const maxX = Math.max(bounds.minX, bounds.maxX);
  const minY = Math.min(bounds.minY, bounds.maxY);
  const maxY = Math.max(bounds.minY, bounds.maxY);
  const sceneWidth = Math.max(1, maxX - minX);
  const sceneHeight = Math.max(1, maxY - minY);
  const safeMargin = Number.isFinite(margin) ? Math.max(0, margin) : 0;
  const availableWidth = Math.max(1, width - safeMargin * 2);
  const availableHeight = Math.max(1, height - safeMargin * 2);
  const zoom = clampEditorZoom(Math.min(availableWidth / sceneWidth, availableHeight / sceneHeight));
  return {
    zoom,
    offsetX: width / 2 - ((minX + maxX) / 2) * zoom,
    offsetY: height / 2 - ((minY + maxY) / 2) * zoom,
  };
}
