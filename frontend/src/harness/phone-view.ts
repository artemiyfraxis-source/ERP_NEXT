// Чертёж жгута на телефоне — только просмотр: поле с прокруткой и масштабом, без инструментов правки.
// Отдельный маленький модуль: его читает и страница чертежа (шапка), и сам редактор (ленивый чанк).
import { useSyncExternalStore } from "react";
import { panEditorCamera, zoomEditorCameraAt } from "./editor/editor-camera";
import type { EditorCamera, EditorPoint } from "./editor/editor-types";

/** Телефон: палец и узкий экран (или телефон боком — низкий экран), либо совсем узкое окно. Планшет 12,7" сюда не
 *  попадает: он шире 760 px и выше 500 px. */
export const PHONE_VIEW_QUERY = "(pointer: coarse) and (max-width: 760px), (pointer: coarse) and (max-height: 500px), (max-width: 480px)";

function phoneQuery(): MediaQueryList | null {
  try {
    return typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(PHONE_VIEW_QUERY) : null;
  } catch { return null; }
}

function subscribe(onChange: () => void): () => void {
  const query = phoneQuery();
  if (!query) return () => {};
  query.addEventListener?.("change", onChange);
  return () => query.removeEventListener?.("change", onChange);
}

export const isPhoneView = (): boolean => phoneQuery()?.matches ?? false;

/** true на телефоне; меняется при повороте экрана или изменении окна. */
export function usePhoneView(): boolean {
  return useSyncExternalStore(subscribe, isPhoneView, () => false);
}

/** Камера при щипке двумя пальцами: масштаб — по отношению расстояний между пальцами, точка между пальцами остаётся
 *  под пальцами (зум вокруг начальной середины, затем сдвиг на смещение середины). */
export function pinchCamera(start: EditorCamera, from: readonly [EditorPoint, EditorPoint], to: readonly [EditorPoint, EditorPoint]): EditorCamera {
  const distance = ([a, b]: readonly [EditorPoint, EditorPoint]) => Math.hypot(b.x - a.x, b.y - a.y);
  const middle = ([a, b]: readonly [EditorPoint, EditorPoint]) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
  const startDistance = distance(from);
  const ratio = startDistance > 0 ? distance(to) / startDistance : 1;
  const startMiddle = middle(from), nextMiddle = middle(to);
  const zoomed = zoomEditorCameraAt(start, startMiddle, start.zoom * ratio);
  return panEditorCamera(zoomed, nextMiddle.x - startMiddle.x, nextMiddle.y - startMiddle.y);
}
