// ERP's own module (not ported): the picture of a route card on the ERP route canvas — the position's drawing painted by
// the ported canvas core (`redrawCanvas` of CanvasViewport, read-only: nothing is hit-tested or edited), the parts outside
// the card painted as a dimmed background, the way the author's snapshot preview shows a semi-finished product.
import { useEffect, useRef } from "react";
import { getEditorSceneBounds, redrawCanvas } from "../editor/CanvasViewport";
import { materializePlacementRows } from "../editor/component-template-placement";
import { ComponentTemplateImageCache, projectComponentTemplateView, type ComponentTemplateViewInstance, type ResolveComponentTemplateAssetUrl } from "../editor/component-template-view-renderer";
import { buildDrawingPerimeters } from "../editor/drawing-object-perimeter";
import { alignDrawingConnectorIndexes } from "../editor/drawing-indices";
import { fitEditorCameraToBounds, type EditorSceneBounds } from "../editor/editor-camera";
import type { EditorLayer, EditorSceneObject } from "../editor/editor-types";
import { designToScene } from "../editor/HarnessDesignEditor";
import type { CableInstance, HarnessDesignDocument } from "../editor/model";

export interface SnapshotScene {
  readonly objects: readonly EditorSceneObject[];
  readonly layers: readonly EditorLayer[];
  readonly cables: readonly CableInstance[];
  readonly instances: readonly ComponentTemplateViewInstance[];
  readonly resolve?: ResolveComponentTemplateAssetUrl;
  readonly bounds: EditorSceneBounds | null;
}

/** The drawing view of a document the way the editor builds it: library connectors from their pinned snapshots,
 *  connector indexes aligned to their pictures. One scene per document — every card of the canvas reuses it. */
export function snapshotScene(design: HarnessDesignDocument, instances: readonly ComponentTemplateViewInstance[] = [],
  resolve?: ResolveComponentTemplateAssetUrl): SnapshotScene {
  const materialized = new Set(instances.map((instance) => instance.objectId));
  const drawingIds = new Set(instances.filter((instance) => projectComponentTemplateView(instance, "drawing", { x: 0, y: 0 })?.commands.length)
    .map((instance) => instance.objectId));
  const document: HarnessDesignDocument = { ...design, connectors: design.connectors.map((connector) => {
    const instance = instances.find((item) => item.objectId === connector.id), binding = connector.libraryBinding;
    if (!instance || binding?.mode !== "template") return connector;
    const rows = materializePlacementRows(instance.content, instance.articleVariantId);
    return { ...connector, libraryBinding: { ...binding, snapshot: { ...binding.snapshot, contacts: binding.snapshot.contacts.map((contact) =>
      ({ ...contact, representations: rows.find((row) => row.key === contact.logicalContactId)?.representations ?? [] })) } } };
  }) };
  const objects = alignDrawingConnectorIndexes(document,
    designToScene(document, "drawing", new Set(), materialized, 1, drawingIds, buildDrawingPerimeters(instances)), instances, resolve);
  const layers: EditorLayer[] = [...design.views.drawing.layers].sort((left, right) => right.order - left.order)
    .map((layer) => ({ id: layer.id, label: layer.name, visible: layer.visible, locked: layer.locked }));
  return { objects, layers, cables: design.cables, instances, resolve,
    bounds: getEditorSceneBounds(objects, layers, "drawing", undefined, instances, resolve, design.cables) };
}

/** A card picture: the whole drawing fitted into the box, `hidden` objects dimmed to `opacity`. A thumbnail never
 *  breaks the canvas — a paint error leaves the box empty. */
export function RouteSnapshot({ scene, hidden, label, opacity = 0.14 }: {
  scene: SnapshotScene; hidden: ReadonlySet<string>; label: string; opacity?: number;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return undefined;
    const cache = new ComponentTemplateImageCache();
    const shown = scene.objects.filter((object) => !hidden.has(object.id));
    const dimmed = scene.objects.filter((object) => hidden.has(object.id));
    const paint = () => {
      const width = canvas.clientWidth, height = canvas.clientHeight;
      if (!width || !height) return;
      const camera = fitEditorCameraToBounds({ zoom: 1, offsetX: 0, offsetY: 0 }, scene.bounds, { width, height }, 8);
      try {
        redrawCanvas(canvas, "drawing", camera, shown, scene.layers, new Set(), scene.cables, undefined, undefined, scene.instances,
          scene.resolve, cache, [], null, [], dimmed, opacity, { skipGrid: true, pixelRatio: Math.max(2, window.devicePixelRatio || 1) });
      } catch { /* the box stays empty */ }
    };
    cache.setInvalidate(paint);
    paint();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(paint);
    observer?.observe(canvas);
    return () => { observer?.disconnect(); cache.setInvalidate(null); };
  }, [scene, hidden, opacity]);
  return <canvas ref={ref} className="rc-snapshot" role="img" aria-label={label} />;
}
