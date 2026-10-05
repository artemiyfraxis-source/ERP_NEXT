import { renderPartSvg } from "../wire-blank-artwork.mjs";
import { drawVolumeStroke, drawVolumeSurface } from "./drawing-volume";
import { commonParallelSpan, commonHorizontalPairSpan, parallelSpanWorld, parallelSpanLocal, type ParallelSpan } from "./e4-parallel-spans";
import { intersectSegments, segmentsParallel } from "./segment-geometry";
import type { PhysicalDragMode } from "./physical-editing";
import { snapPhysicalPoint, snapBendPoint, bendSnapAnchors, pipeBendSnapAnchors, joiningPipeEndpointSnapAnchors, physicalObjectSnapAnchors, physicalObjectRouteAnchors, type BendSnapState } from "./physical-editing";
import { pipeSceneControls, pipeSceneHandles, pipeSceneEditablePoints, pipeSceneWireIds } from "./physical-scene";
import { coveringHit, coveringGrips, drawCoveringSurface, warmCoveringTextures } from "./covering-renderer";
import type { CoveringDragPart, CoveringHandle } from "./covering-layout";
import { projectOntoPolyline } from "./physical-coverings";
import { traceDrawingRoute, drawingRouteHitPoints } from "./drawing-route-path";
import {standardCoveringKinds,type PhysicalContextAction,type PhysicalContextTarget} from "./physical-coverings";
import type { DimensionMode } from "./drawing-dimensions";
import type { DrawingGraphic, DrawingGraphicKind } from "./drawing-documents";
import { screenCrossSections, uprightScreenBody, clearScreenSections, type ScreenCrossSection } from "./e4-screen-spans";
import { CanvasObjectPopover } from "./CanvasObjectPopover";
import { CanvasObjectHint, type CanvasHintTarget } from "./CanvasObjectHint";
import { DrawingResizeGrip } from "./DrawingResizeGrip";
import { CONTACT_SIDE_VIEW_PLACEMENT_ID, drawingScale, DRAWING_VIEW_PLACEMENT_ID } from "./drawing-scale";
import { useEffect, useMemo, useRef, useState, type DragEvent, type MouseEvent, type PointerEvent, type ReactNode } from "react";
import { clearDecorationSpans } from "./e4-decoration-spans";
import {
  panEditorCamera,
  screenToWorld,
  zoomEditorCameraFromWheel,
  type EditorSceneBounds,
  type EditorViewportSize,
} from "./editor-camera";
import type {
  EditorCamera,
  EditorLayer,
  EditorPoint,
  EditorSceneObject,
  EditorTool,
  HarnessEditorView,
} from "./editor-types";
import {
  connectorE4FooterWidth,
  connectorE4TableColumnWidth,
  connectorE4TableMetrics,
  e4ScreenAlongSize,
} from "./model";
import { getE4WireLabelLayout, projectPointToE4WireLabelPosition } from "./e4-wire-label";
import { E4_BRIDGE_RADIUS } from "./e4-router";
import {
  snapE4ConnectorPosition,
  type E4ConnectorSnapGuides,
  type E4ConnectorSnapTarget,
} from "./e4-connector-snap";
import { resolveWireColorHex } from "./wire-reference-catalog";
import {
  buildWireStripProfileGeometry,
  type WireStripProfileGeometry,
} from "./wire-strip-profile-geometry";
import {
  ComponentTemplateImageCache,
  drawProjectedComponentTemplateView,
  projectComponentTemplateView, projectContactSideView,
  projectE4DrawingCompanion, projectE4DrawingCompanions, shortestDrawingLink,
  type ComponentTemplateViewInstance,
  type ResolveComponentTemplateAssetUrl,
} from "./component-template-view-renderer";
import {
  buildCableSheathGeometry,
  type CableSheathGeometry,
} from "./cable-sheath-geometry";
import type { CableInstance } from "./model";
import { railCatchDistance, railDistance, railParameter, snapRailEnd } from "./position-rail";
import { snapDrawingPoint, type DrawingOutline, type DrawingSnaps } from "../component-library/drawing-geometry";
import { materialObjectGroup } from "./MaterialObjectsPanel";

export interface CanvasViewportProps {
  readonly onFreeWireEndpointMove?: (wireId:string,end:"from"|"to",point:EditorPoint)=>void;
  readonly onFreeWireEndpointPreview?: (wireId:string,end:"from"|"to",point:EditorPoint|null)=>void;
  readonly onFreeWireEndpointsXChange?: (wireIds:readonly string[],end:"from"|"to",x:number)=>void;
  readonly onDetachedPairAction?: (wireIds:readonly string[],action:"twist"|"straighten")=>void;
  readonly onFreeWireEndStyleRequest?: (wireId:string,end:"from"|"to")=>void;
  readonly view: HarnessEditorView;
  readonly tool: EditorTool;
  readonly camera: EditorCamera;
  readonly objects: readonly EditorSceneObject[];
  readonly layers: readonly EditorLayer[];
  readonly selectedObjectId: string | null;
  readonly selectedObjectIds?: readonly string[];
  readonly highlightedObjectIds?: readonly string[];
  readonly foregroundWireIds?: readonly string[];
  /** Presentation-only background; these objects are painted but never hit-tested. */
  readonly backgroundObjects?: readonly EditorSceneObject[];
  readonly backgroundOpacity?: number;
  readonly cables?: readonly CableInstance[];
  readonly e4Overlays?: E4SceneOverlays;
  readonly e4RoutingMode?: "orthogonal" | "angular";
  /** Exact project snapshots keyed to connector scene-object ids. */
  readonly componentTemplateViewInstances?: readonly ComponentTemplateViewInstance[];
  /** Resolves an asset inside the exact project snapshot. */
  readonly resolveComponentTemplateAssetUrl?: ResolveComponentTemplateAssetUrl;
  readonly overlay?: ReactNode;
  readonly onPipeIntervalSelect?:(id:string,from:number,to:number)=>void;
  readonly onCoveringDrag?:(id:string,spanIndex:number,part:CoveringDragPart,start:EditorPoint,point:EditorPoint,phase:"preview"|"commit"|"cancel")=>void;
  readonly onDimensionCreate?:(wireId:string,from:number,to:number,pointCount:number,mode:DimensionMode,auxiliary?:boolean)=>void;
  readonly onPositionRailCreate?:(start:EditorPoint,end:EditorPoint,leaderIds:readonly string[])=>void;
  readonly onGraphicCreate?:(graphic:DrawingGraphic)=>void;
  readonly drawingSnaps?:DrawingSnaps;
  readonly drawingAngleStep?:number;
  readonly diagnosticOverlay?: ReactNode;
  readonly inlineEditor?: ReactNode;
  readonly onCameraChange: (camera: EditorCamera) => void;
  readonly onViewportSizeChange?: (size: EditorViewportSize) => void;
  readonly onObjectSelect: (objectId: string | null, additive?: boolean) => void;
  /** Selects all members of a linked E4 overlay in one state update. */
  readonly onObjectGroupSelect?: (objectIds: readonly string[], mode?: EditorSelectionMode) => void;
  /** Available only for editable route fragment copies. */
  readonly onObjectsIsolate?: (objectIds: readonly string[]) => void;
  readonly onDrawingScale?: (objectId:string,drawingId:string,scale:number)=>void;
  readonly onDrawingMove?: (objectId:string,drawingId:string,offset:EditorPoint)=>void;
  readonly objectProperties?: (objectId:string)=>ReactNode;
  readonly onObjectPick?: (objectId:string|null)=>void;
  readonly onObjectPickCancel?: ()=>void;
  readonly onRelatedObjectsSelect?: (ids:readonly string[])=>void;
  readonly onObjectMove?: (objectId: string, point: EditorPoint, mode?: PhysicalDragMode) => void;
  /** Shows a transient move without adding an undo entry. Passing null clears it. */
  readonly onObjectMovePreview?: (objectId: string, point: EditorPoint | null, mode?: PhysicalDragMode) => void;
  readonly onWireConnect?: (
    from: E4ConnectableEndpoint,
    to: E4ConnectableEndpoint,
  ) => void;
  readonly onWireReconnect?: (
    wireId: string,
    end: "from" | "to",
    target: E4ConnectableEndpoint,
  ) => void;
  readonly onWireConnectToWire?: (
    from: E4ConnectableEndpoint,
    targetWireId: string,
    point: EditorPoint,
  ) => void;
  readonly onWireReconnectToWire?: (
    wireId: string,
    end: "from" | "to",
    targetWireId: string,
    point: EditorPoint,
  ) => void;
  readonly onE4WireSegmentMove?: (wireId: string, segmentIndex: number, coordinate: number) => void;
  readonly onE4WireRoutePointRemove?: (wireId: string, routeIndex: number) => void;
  readonly onE4WireLabelPositionChange?: (wireId: string, position: number) => void;
  readonly onE4ScreenPositionChange?: (screenId: string, position: number) => void;
  readonly onWireToolRequest?: () => void;
  readonly onWireRoutePointPreview?: (id:string,index:number,point:EditorPoint|null, mode?:PhysicalDragMode, insert?:boolean)=>void;
  readonly onWireRoutePointMove?: (wireId: string, routeIndex: number, point: EditorPoint, mode?:PhysicalDragMode, insert?:boolean) => void;
  readonly onWireRoutePointRemove?: (wireId: string, routeIndex: number) => void;
  readonly onObjectEditRequest?: (objectId: string) => void;
  readonly onPhysicalNodesConnect?: (from:string,to:string)=>void;
  readonly onPhysicalNodeConnectToSegment?: (fromNodeId:string,segmentId:string,point:EditorPoint)=>void;
  readonly onPhysicalContextAction?: (segmentId:string,point:EditorPoint,action:PhysicalContextAction,target?:PhysicalContextTarget)=>void;
  readonly onCanvasDoubleClick?: (point: EditorPoint, ctrlKey?: boolean) => void;
  readonly onCatalogDrop: (itemId: string, point: EditorPoint) => void;
}

interface EditorViewportWheelEvent {
  readonly ctrlKey: boolean;
  readonly deltaY: number;
  readonly clientX: number;
  readonly clientY: number;
  preventDefault: () => void;
}

/** Handles Ctrl+wheel for the whole viewport, including HTML controls over the canvas. */
export function handleEditorViewportWheel(
  event: EditorViewportWheelEvent,
  camera: EditorCamera,
  canvasBounds: Pick<DOMRect, "left" | "top">,
  onCameraChange: (camera: EditorCamera) => void,
): boolean {
  if (!event.ctrlKey || event.deltaY === 0) return false;
  event.preventDefault();
  const anchor = { x: event.clientX - canvasBounds.left, y: event.clientY - canvasBounds.top };
  onCameraChange(zoomEditorCameraFromWheel(camera, anchor, event.deltaY, event.ctrlKey));
  return true;
}

export function containInlineEditorPointerEvent(event: Pick<PointerEvent<HTMLElement>, "stopPropagation">): void {
  event.stopPropagation();
}

export function isInlineEditorReadonlyTarget(target: EventTarget | null): boolean {
  const candidate = target as { closest?: (selector: string) => unknown } | null;
  return typeof candidate?.closest === "function" &&
    candidate.closest(".e4-connector-canvas-editor.is-readonly") !== null;
}

/** Controls keep their native text selection, copy and popup interaction. */
export function isInlineEditorControlTarget(target: EventTarget | null): boolean {
  const candidate = target as { closest?: (selector: string) => unknown } | null;
  return typeof candidate?.closest === "function" &&
    candidate.closest("input, textarea, select, button, [contenteditable='true']") !== null;
}

export function inlineObjectDragMoved(deltaX: number, deltaY: number, threshold = 3): boolean {
  return Number.isFinite(deltaX) && Number.isFinite(deltaY) && Math.hypot(deltaX, deltaY) >= threshold;
}

export function inlineObjectDragDestination(
  origin: EditorPoint,
  deltaX: number,
  deltaY: number,
  zoom: number,
): EditorPoint {
  const safeZoom = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  return { x: origin.x + deltaX / safeZoom, y: origin.y + deltaY / safeZoom };
}

interface PointerDrag {
  readonly kind: "pan";
  readonly pointerId: number;
  readonly clientX: number;
  readonly clientY: number;
  readonly camera: EditorCamera;
}

interface CoveringPointerDrag {readonly kind:"covering";readonly pointerId:number;readonly clientX:number;readonly clientY:number;readonly objectId:string;readonly spanIndex:number;readonly part:CoveringDragPart;readonly start:EditorPoint}
interface DrawingPointerDrag { readonly kind:"companion"; readonly pointerId:number; readonly clientX:number; readonly clientY:number; readonly objectId:string; readonly drawingId:string; readonly offset:EditorPoint; }

interface ObjectPointerDrag {
  readonly kind: "object";
  readonly pointerId: number;
  readonly clientX: number;
  readonly clientY: number;
  readonly objectId: string;
  readonly objectX: number;
  readonly objectY: number;
  readonly mode?:PhysicalDragMode;
  readonly anchors?:readonly EditorPoint[];
  readonly routeAnchors?:readonly EditorPoint[];
  readonly snapState?:BendSnapState;
}

interface WireRoutePointerDrag {
  readonly kind: "wire-route";
  readonly pointerId: number;
  readonly clientX: number;
  readonly clientY: number;
  readonly wireId: string;
  readonly routeIndex: number;
  readonly point: EditorPoint;
  readonly mode?:PhysicalDragMode;
  readonly insert?:boolean;
  readonly opMember?:boolean;
  readonly anchors?:readonly EditorPoint[];
  readonly snapState?:BendSnapState;
}

interface E4WireSegmentPointerDrag {
  readonly kind: "e4-wire-segment";
  readonly pointerId: number;
  readonly clientX: number;
  readonly clientY: number;
  readonly wireId: string;
  readonly segmentIndex: number;
  readonly orientation: E4SegmentOrientation;
  readonly coordinate: number;
}

interface E4ScreenPointerDrag {
  readonly kind: "e4-screen";
  readonly pointerId: number;
  readonly clientX: number;
  readonly clientY: number;
  readonly screenId: string;
  readonly orientation: E4SegmentOrientation;
  readonly position: number;
  readonly spanLength: number;
}

interface E4WireLabelPointerDrag {
  readonly kind: "e4-wire-label";
  readonly pointerId: number;
  readonly wireId: string;
}

interface PhysicalNodeConnectPointerDrag {
  readonly kind: "physical-node-connect";
  readonly pointerId: number;
  readonly fromNodeId: string;
  readonly clientX: number;
  readonly clientY: number;
  moved: boolean;
}

export type E4SegmentOrientation = "horizontal" | "vertical";

export interface E4WireSegment {
  readonly index: number;
  readonly start: EditorPoint;
  readonly end: EditorPoint;
  readonly orientation: E4SegmentOrientation;
}

export interface E4WireSegmentHit extends E4WireSegment {
  readonly wireId: string;
  readonly point: EditorPoint;
  readonly distance: number;
}

export interface OrthogonalProjection {
  readonly point: EditorPoint;
  readonly distance: number;
  readonly position: number;
  readonly orientation: E4SegmentOrientation;
}

export interface E4JunctionOverlay {
  readonly id: string;
  readonly position: EditorPoint;
  readonly wireIds: readonly string[];
}

export interface E4DifferentialPairOverlay {
  readonly id: string;
  readonly wireIds: readonly [string, string];
  readonly step: number;
  readonly amplitude: number;
  readonly variant?: 1 | 2;
}

export interface E4ScreenOverlay {
  readonly id: string;
  readonly wireIds: readonly string[];
  readonly position: number;
  readonly label: string;
  readonly width: number;
  readonly terminalSide?: "above" | "below" | "both";
}

export type E4ConnectableEndpoint =
  | { readonly connectorId: string; readonly contactIndex: number }
  | { readonly screenId: string; readonly screenTerminalSide?: "above" | "below" };

export interface E4SceneOverlays {
  readonly crossingStyle: "none" | "bridge";
  readonly junctions: readonly E4JunctionOverlay[];
  readonly diffPairs: readonly E4DifferentialPairOverlay[];
  readonly screens: readonly E4ScreenOverlay[];
}

function finitePoint(point: EditorPoint | undefined): point is EditorPoint {
  return point !== undefined && Number.isFinite(point.x) && Number.isFinite(point.y);
}

function samePoint(left: EditorPoint, right: EditorPoint): boolean {
  return Math.abs(left.x - right.x) < 0.000001 && Math.abs(left.y - right.y) < 0.000001;
}

function orthogonalE4Bend(point: EditorPoint, original: EditorPoint, route: readonly EditorPoint[]): EditorPoint {
  const index = route.findIndex(candidate => samePoint(candidate, original));
  if (index <= 0 || index >= route.length - 1) return point;
  const previous = route[index - 1]!;
  const next = route[index + 1]!;
  // A corner is defined by its two neighbours. Choosing one shared axis for
  // each adjacent segment keeps both shoulders orthogonal even when a legacy
  // route already contains a diagonal segment or a carried junction.
  const candidates = [
    { x: previous.x, y: next.y },
    { x: next.x, y: previous.y },
  ];
  return candidates.sort((left, right) =>
    Math.hypot(left.x - point.x, left.y - point.y) -
    Math.hypot(right.x - point.x, right.y - point.y))[0]!;
}

/** The scene owns routing. Painting and hit testing use exactly the same supplied points. */
export function getE4WireRoute(object: EditorSceneObject): readonly EditorPoint[] {
  return object.points ?? [];
}

export function e4WireSegments(points: readonly EditorPoint[]): readonly E4WireSegment[] {
  const result: E4WireSegment[] = [];
  for (let index = 0; index < points.length - 1; index += 1) {
    const start = points[index];
    const end = points[index + 1];
    if (!start || !end || samePoint(start, end)) continue;
    if (start.x !== end.x && start.y !== end.y) continue;
    result.push({
      index,
      start,
      end,
      orientation: start.y === end.y ? "horizontal" : "vertical",
    });
  }
  return result;
}

export function projectPointToOrthogonalSegment(
  point: EditorPoint,
  start: EditorPoint,
  end: EditorPoint,
): OrthogonalProjection | null {
  if (start.y === end.y) {
    const minimum = Math.min(start.x, end.x);
    const maximum = Math.max(start.x, end.x);
    const x = Math.max(minimum, Math.min(maximum, point.x));
    const length = maximum - minimum;
    return {
      point: { x, y: start.y },
      distance: Math.hypot(point.x - x, point.y - start.y),
      position: length === 0 ? 0 : (x - minimum) / length,
      orientation: "horizontal",
    };
  }
  if (start.x === end.x) {
    const minimum = Math.min(start.y, end.y);
    const maximum = Math.max(start.y, end.y);
    const y = Math.max(minimum, Math.min(maximum, point.y));
    const length = maximum - minimum;
    return {
      point: { x: start.x, y },
      distance: Math.hypot(point.x - start.x, point.y - y),
      position: length === 0 ? 0 : (y - minimum) / length,
      orientation: "vertical",
    };
  }
  return null;
}

function movableE4Segment(segments: readonly E4WireSegment[], segment: E4WireSegment): boolean {
  const position = segments.findIndex((candidate) => candidate.index === segment.index);
  const previous = segments[position - 1];
  const next = segments[position + 1];
  return previous !== undefined && next !== undefined &&
    previous.index === segment.index - 1 && next.index === segment.index + 1 &&
    previous.orientation !== segment.orientation && next.orientation !== segment.orientation;
}

function hitTestE4WireSegments(
  objects: readonly EditorSceneObject[],
  layers: readonly EditorLayer[],
  point: EditorPoint,
  zoom: number,
  internalOnly: boolean,
  excludedWireId?: string,
): E4WireSegmentHit | null {
  const layerMap = new Map(layers.map((layer) => [layer.id, layer]));
  const tolerance = 8 / zoom;
  for (const object of [...objectsInPaintOrder(objects, layers)].reverse()) {
    if (object.kind !== "wire" || object.id === excludedWireId) continue;
    const layer = layerMap.get(object.layerId);
    if (layer?.visible !== true || (internalOnly && layer.locked)) continue;
    if(!internalOnly){
      const points=getE4WireRoute(object);
      for(let index=0;index<points.length-1;index++){
        const start=points[index]!,end=points[index+1]!,dx=end.x-start.x,dy=end.y-start.y,length=dx*dx+dy*dy;
        if(!length)continue;
        const t=Math.max(0,Math.min(1,((point.x-start.x)*dx+(point.y-start.y)*dy)/length));
        const projected={x:start.x+t*dx,y:start.y+t*dy},distance=Math.hypot(point.x-projected.x,point.y-projected.y);
        if(distance<=tolerance)return {start,end,index,wireId:object.id,point:projected,distance,orientation:Math.abs(dx)>=Math.abs(dy)?"horizontal":"vertical"};
      }
      continue;
    }
    const segments = e4WireSegments(getE4WireRoute(object));
    for (const segment of segments) {
      if (internalOnly && !movableE4Segment(segments, segment)) continue;
      const projection = projectPointToOrthogonalSegment(point, segment.start, segment.end);
      if (projection && projection.distance <= tolerance) {
        return { ...segment, wireId: object.id, point: projection.point, distance: projection.distance };
      }
    }
  }
  return null;
}

export function hitTestE4WireSegment(
  objects: readonly EditorSceneObject[],
  layers: readonly EditorLayer[],
  point: EditorPoint,
  zoom: number,
): E4WireSegmentHit | null {
  return hitTestE4WireSegments(objects, layers, point, zoom, true);
}

export function hitTestWireSegment(
  objects: readonly EditorSceneObject[],
  layers: readonly EditorLayer[],
  point: EditorPoint,
  zoom: number,
  excludedWireId?: string,
): E4WireSegmentHit | null {
  return hitTestE4WireSegments(objects, layers, point, zoom, false, excludedWireId);
}


function parseStringArray(value: unknown): readonly string[] | null {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) return null;
  return value;
}

export function parseE4SceneOverlays(objects: readonly EditorSceneObject[]): E4SceneOverlays {
  const metadata = objects.find((object) => object.metadata?.view === "e4" &&
    (object.metadata.junctions !== undefined || object.metadata.diffPairs !== undefined ||
      object.metadata.screens !== undefined || object.metadata.crossingStyle !== undefined))?.metadata;
  const junctionsValue = parseJson(metadata?.junctions);
  const diffPairsValue = parseJson(metadata?.diffPairs);
  const screensValue = parseJson(metadata?.screens);
  const junctions: E4JunctionOverlay[] = Array.isArray(junctionsValue) ? junctionsValue.flatMap((item) => {
    if (!isRecord(item) || typeof item.id !== "string" || !isRecord(item.position)) return [];
    const position = { x: Number(item.position.x), y: Number(item.position.y) };
    const wireIds = parseStringArray(item.wireIds);
    return finitePoint(position) && wireIds ? [{ id: item.id, position, wireIds }] : [];
  }) : [];
  const diffPairs: E4DifferentialPairOverlay[] = Array.isArray(diffPairsValue) ? diffPairsValue.flatMap((item) => {
    if (!isRecord(item) || typeof item.id !== "string") return [];
    const wireIds = parseStringArray(item.wireIds);
    const step = Number(item.step);
    const amplitude = Number(item.amplitude);
    const variant = item.variant === 2 ? 2 : 1;
    return wireIds?.length === 2 && step > 0 && amplitude > 0
      ? [{ id: item.id, wireIds: [wireIds[0]!, wireIds[1]!], step, amplitude, variant }]
      : [];
  }) : [];
  const screens: E4ScreenOverlay[] = Array.isArray(screensValue) ? screensValue.flatMap((item) => {
    if (!isRecord(item) || typeof item.id !== "string") return [];
    const wireIds = parseStringArray(item.wireIds);
    const position = Number(item.position);
    const width = Number(item.width);
    return wireIds && wireIds.length > 0 && position >= 0 && position <= 1 && width > 0
      ? [{
        id: item.id,
        wireIds,
        position,
        width,
        label: typeof item.label === "string" ? item.label : "",
        terminalSide: item.terminalSide === "below" || item.terminalSide === "both" ? item.terminalSide : "above",
      }]
      : [];
  }) : [];
  return {
    crossingStyle: metadata?.crossingStyle === "bridge" ? "bridge" : "none",
    junctions,
    diffPairs,
    screens,
  };
}

export interface E4WireCrossing {
  readonly point: EditorPoint;
  readonly overWireId: string;
  readonly underWireId: string;
  readonly overOrientation: E4SegmentOrientation;
  readonly overDirection?:EditorPoint;
  readonly underDirection?:EditorPoint;
}

export interface E4BridgeGeometry {
  readonly clearStart: EditorPoint;
  readonly clearEnd: EditorPoint;
  readonly coloredStart: EditorPoint;
  readonly arcStart: EditorPoint;
  readonly arcEnd: EditorPoint;
  readonly coloredEnd: EditorPoint;
}

/** Geometry with coloured legs overlapping the exact butt-capped clear span. */
export function getE4BridgeGeometry(
  crossing: Pick<E4WireCrossing, "point" | "overOrientation" | "overDirection">,
  radius = E4_BRIDGE_RADIUS,
): E4BridgeGeometry {
  const { point, overOrientation } = crossing;
  if(crossing.overDirection){
    const u=crossing.overDirection,at=(n:number)=>({x:point.x+u.x*n,y:point.y+u.y*n});
    return {clearStart:at(-radius-1),clearEnd:at(radius+1),coloredStart:at(-radius-2),coloredEnd:at(radius+2),arcStart:at(-radius),arcEnd:at(radius)};
  }
  if (overOrientation === "horizontal") {
    return {
      clearStart: { x: point.x - radius - 1, y: point.y },
      clearEnd: { x: point.x + radius + 1, y: point.y },
      coloredStart: { x: point.x - radius - 2, y: point.y },
      arcStart: { x: point.x - radius, y: point.y },
      arcEnd: { x: point.x + radius, y: point.y },
      coloredEnd: { x: point.x + radius + 2, y: point.y },
    };
  }
  return {
    clearStart: { x: point.x, y: point.y - radius - 1 },
    clearEnd: { x: point.x, y: point.y + radius + 1 },
    coloredStart: { x: point.x, y: point.y - radius - 2 },
    arcStart: { x: point.x, y: point.y - radius },
    arcEnd: { x: point.x, y: point.y + radius },
    coloredEnd: { x: point.x, y: point.y + radius + 2 },
  };
}

function pointMatches(point: EditorPoint, candidate: EditorPoint, tolerance = 0.001): boolean {
  return Math.hypot(point.x - candidate.x, point.y - candidate.y) <= tolerance;
}

export function getE4WireCrossings(
  objects: readonly EditorSceneObject[],
  layers: readonly EditorLayer[],
  junctions: readonly E4JunctionOverlay[] = [],
): readonly E4WireCrossing[] {
  const paintOrder = objectsInPaintOrder(objects, layers).filter((object) => object.kind === "wire");
  const result: E4WireCrossing[] = [];
  for (let overIndex = 1; overIndex < paintOrder.length; overIndex += 1) {
    const over = paintOrder[overIndex]!;
    const segments=(o:EditorSceneObject)=>(o.points??[]).slice(1).map((end,i)=>({start:o.points![i]!,end}));
    const overSegments = segments(over);
    for (let underIndex = 0; underIndex < overIndex; underIndex += 1) {
      const under = paintOrder[underIndex]!;
      const underSegments = segments(under);
      for (const overSegment of overSegments) {
        for (const underSegment of underSegments) {
          const hit=intersectSegments(overSegment,underSegment);
          if(hit.kind!=="point"||segmentsParallel(overSegment,underSegment))continue;
          const point=hit.point;
          const isJunction = junctions.some((junction) => pointMatches(junction.position, point));
          if (isJunction) continue;
          if (result.some((item) => item.overWireId === over.id && item.underWireId === under.id && pointMatches(item.point, point))) continue;
          const diagonal=[overSegment,underSegment].some(s=>s.start.x!==s.end.x&&s.start.y!==s.end.y);
          const direction=(s:typeof overSegment)=>{const n=Math.hypot(s.end.x-s.start.x,s.end.y-s.start.y),sign=s.end.x<s.start.x||s.end.x===s.start.x&&s.end.y<s.start.y?-1:1;return {x:(s.end.x-s.start.x)/n*sign,y:(s.end.y-s.start.y)/n*sign};};
          result.push({ point, overWireId: over.id, underWireId: under.id, overOrientation: overSegment.start.y===overSegment.end.y?"horizontal":"vertical",
            ...(diagonal?{overDirection:direction(overSegment),underDirection:direction(underSegment)}:{}) });
        }
      }
    }
  }
  return result;
}

export type E4ParallelSpan = ParallelSpan;

export function findE4CommonParallelSpan(objects:readonly EditorSceneObject[],wireIds:readonly string[]):E4ParallelSpan|null {
  return commonParallelSpan(wireIds.map(id=>({id,points:objects.find(o=>o.id===id&&o.kind==="wire")?.points??[]})));
}
export interface E4ScreenLayout {
  readonly id: string;
  readonly wireIds: readonly string[];
  readonly center: EditorPoint;
  readonly orientation: E4SegmentOrientation;
  readonly alongSize: number;
  readonly crossSize: number;
  readonly span: ScreenCrossSection;
  readonly spans: readonly ScreenCrossSection[];
  readonly pathLength: number;
  readonly bodyConnectionPoint: EditorPoint;
  readonly connectionPoint: EditorPoint;
  readonly terminalSide: "above" | "below";
  readonly terminals: readonly {
    readonly side: "above" | "below";
    readonly bodyConnectionPoint: EditorPoint;
    readonly connectionPoint: EditorPoint;
  }[];
}

export interface E4DifferentialPairMotif {
  readonly from: number;
  readonly center: number;
  readonly to: number;
  /** Coloured horizontal/vertical legs overlap the erased base conductors here. */
  readonly coloredFrom: number;
  readonly coloredTo: number;
}

export interface E4DifferentialPairLayout {
  readonly id: string;
  readonly wireIds: readonly [string, string];
  readonly variant: 1 | 2;
  readonly span: E4ParallelSpan;
  readonly crossMinimum: number;
  readonly crossMaximum: number;
  readonly motifs: readonly E4DifferentialPairMotif[];
}

export function getE4DifferentialPairLayout(
  group: E4DifferentialPairOverlay,
  objects: readonly EditorSceneObject[],
): E4DifferentialPairLayout | null {
  const common = commonHorizontalPairSpan(group.wireIds.map(id => ({id,points:objects.find(o=>o.id===id&&o.kind==="wire")?.points??[]})));
  if (!common) return null;
  const tables=objects.filter(object=>object.kind==="connector").map(table=>{
    if(!common.direction)return table;
    const corners=[{x:table.x,y:table.y},{x:table.x+table.width,y:table.y},{x:table.x,y:table.y+table.height},{x:table.x+table.width,y:table.y+table.height}].map(p=>parallelSpanLocal(common,p));
    const x=Math.min(...corners.map(p=>p.x)),y=Math.min(...corners.map(p=>p.y));
    return {x,y,width:Math.max(...corners.map(p=>p.x))-x,height:Math.max(...corners.map(p=>p.y))-y};
  });
  // Paint the pair directly on the shared horizontal segment of the real
  // routes. A wide legacy/manual bundle remains wide until its routes are
  // replanned; shifting only the painted motif would detach it from the wires.
  const rawCrossGap = Math.abs(common.crossMaximum - common.crossMinimum);
  const crossGap = rawCrossGap > 1e-6 ? rawCrossGap : Math.max(group.amplitude * 2, 1);
  const compactCrossCenter = (common.crossMinimum + common.crossMaximum) / 2;
  const compactCommon: E4ParallelSpan = {
    ...common,
    crossMinimum: compactCrossCenter - crossGap / 2,
    crossMaximum: compactCrossCenter + crossGap / 2,
  };
  const span = clearDecorationSpans([compactCommon], tables, 10,
    item => Math.max(group.amplitude * 2, item.crossMaximum - item.crossMinimum))
    .sort((a, b) => (b.end - b.start) - (a.end - a.start))[0];
  if (!span) return null;
  const available = span.end - span.start;
  // Keep the visible pitch at the smallest readable E4 increment. The stored
  // physical pitch may be larger, but it must not make a long common span look
  // like a handful of stretched loops.
  const visualStep = Math.max(connectorE4TableMetrics.rowHeight, group.step);
  const count = Math.max(1, Math.floor(available / visualStep));
  const spacing = available / count;
  const motifLength = Math.min(16, Math.max(8, spacing * 0.3));
  const crossCenter = (span.crossMinimum + span.crossMaximum) / 2;
  const crossMinimum = span.crossMinimum === span.crossMaximum
    ? crossCenter - group.amplitude
    : span.crossMinimum;
  const crossMaximum = span.crossMinimum === span.crossMaximum
    ? crossCenter + group.amplitude
    : span.crossMaximum;
  return {
    id: group.id,
    wireIds: group.wireIds,
    variant: group.variant ?? 1,
    span,
    crossMinimum,
    crossMaximum,
    motifs: Array.from({ length: count }, (_, index) => {
      const center = span.start + spacing * (index + 0.5);
      return {
        from: Math.max(span.start, center - motifLength / 2),
        center,
        to: Math.min(span.end, center + motifLength / 2),
        coloredFrom: Math.max(span.start, center - motifLength / 2 - 2),
        coloredTo: Math.min(span.end, center + motifLength / 2 + 2),
      };
    }),
  };
}

export function getE4ScreenLayout(
  screen: E4ScreenOverlay,
  objects: readonly EditorSceneObject[],
): E4ScreenLayout | null {
  const candidates = screenCrossSections(screen.wireIds.map(id => {
    const wire=objects.find(object=>object.id===id && object.kind==="wire");
    return {id,points:wire ? getE4WireRoute(wire) : []};
  }));
  const spans = clearScreenSections(candidates, objects.filter(object => object.kind === "connector"),screen.width,e4ScreenAlongSize);
  if (spans.length === 0) return null;
  const orderedSpans = [...spans].sort((a,b)=>a.firstWireDirection*(a.start-b.start));
  const pathLength = orderedSpans.reduce((sum, item) => sum + (item.end - item.start), 0);
  const requestedDistance = pathLength * Math.max(0, Math.min(1, screen.position));
  let distance = 0;
  let span = orderedSpans[0]!;
  for (const candidate of orderedSpans) {
    const length = candidate.end - candidate.start;
    if (requestedDistance <= distance + length || candidate === orderedSpans.at(-1)) {
      span = candidate;
      break;
    }
    distance += length;
  }
  const spanOffset = Math.max(0, Math.min(span.end - span.start, requestedDistance - distance));
  const along = span.firstWireDirection === 1 ? span.start + spanOffset : span.end - spanOffset;
  const {center,crossSize,alongSize}=uprightScreenBody(span,along,screen.width,e4ScreenAlongSize);
  const configuredSide = screen.terminalSide ?? "above";
  const sides: readonly ("above" | "below")[] = configuredSide === "both"
    ? ["above", "below"]
    : [configuredSide];
  const terminals = sides.map((side) => {
    const direction = side === "above" ? -1 : 1;
    const bodyConnectionPoint = { x: center.x, y: center.y + direction * crossSize / 2 };
    const connectionPoint = bodyConnectionPoint;
    return { side, bodyConnectionPoint, connectionPoint };
  });
  const terminal = terminals[0]!;
  return {
    id: screen.id,
    wireIds: screen.wireIds,
    center,
    orientation: "horizontal",
    alongSize,
    crossSize,
    span,
    spans: orderedSpans,
    pathLength,
    bodyConnectionPoint: terminal.bodyConnectionPoint,
    connectionPoint: terminal.connectionPoint,
    terminalSide: terminal.side,
    terminals,
  };
}

export function hitTestE4DifferentialPair(
  groups: readonly E4DifferentialPairOverlay[],
  objects: readonly EditorSceneObject[],
  point: EditorPoint,
  zoom: number,
  layers?: readonly EditorLayer[],
): E4DifferentialPairLayout | null {
  const tolerance = 6 / zoom;
  const visibleObjects = layers ? objectsInPaintOrder(objects, layers) : objects;
  for (const group of [...groups].reverse()) {
    if (layers && !isE4OverlayVisible(group.wireIds, objects, layers)) continue;
    const layout = getE4DifferentialPairLayout(group, visibleObjects);
    if (!layout) continue;
    const local=parallelSpanLocal(layout.span,point);
    const along = local.x;
    const cross = local.y;
    if (cross < layout.crossMinimum - tolerance || cross > layout.crossMaximum + tolerance) continue;
    if (layout.motifs.some((motif) => along >= motif.from - tolerance && along <= motif.to + tolerance)) {
      return layout;
    }
  }
  return null;
}

/**
 * Finds common spans by route order, preserving bends shared by a routed
 * bundle. If routes do not have a compatible segment sequence the caller
 * falls back to the longest single span for backwards compatibility.
 */
function getE4ScreenPositionForPoint(layout: E4ScreenLayout, point: EditorPoint): number {
  let accumulated = 0;
  let bestDistance = Number.POSITIVE_INFINITY;
  let bestAlong = 0;
  for (const span of layout.spans) {
    const minimum = span.start;
    const maximum = span.end;
    const axis = span.orientation === "horizontal" ? point.x : point.y;
    const along = Math.max(minimum, Math.min(maximum, axis));
    const distance = Math.abs(axis - along);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestAlong = accumulated + (span.firstWireDirection === 1 ? along - minimum : maximum - along);
    }
    accumulated += maximum - minimum;
  }
  return layout.pathLength <= 0 ? 0 : Math.max(0, Math.min(1, bestAlong / layout.pathLength));
}

function segmentAxisDirection(segment: E4WireSegment, orientation: E4SegmentOrientation): 1 | -1 {
  const delta = orientation === "horizontal" ? segment.end.x - segment.start.x : segment.end.y - segment.start.y;
  return delta >= 0 ? 1 : -1;
}

export function hitTestE4Screen(
  screens: readonly E4ScreenOverlay[],
  objects: readonly EditorSceneObject[],
  point: EditorPoint,
  zoom: number,
  layers?: readonly EditorLayer[],
): E4ScreenLayout | null {
  const tolerance = 5 / zoom;
  const visibleObjects = layers ? objectsInPaintOrder(objects, layers) : objects;
  for (const screen of [...screens].reverse()) {
    const layout = getE4ScreenLayout(screen, visibleObjects);
    if (!layout) continue;
    const halfWidth = (layout.orientation === "horizontal" ? layout.alongSize : layout.crossSize) / 2 + tolerance;
    const halfHeight = (layout.orientation === "horizontal" ? layout.crossSize : layout.alongSize) / 2 + tolerance;
    const normalizedX = (point.x - layout.center.x) / halfWidth;
    const normalizedY = (point.y - layout.center.y) / halfHeight;
    if (normalizedX * normalizedX + normalizedY * normalizedY <= 1) return layout;
  }
  return null;
}

export function hitTestE4ScreenConnection(
  screens: readonly E4ScreenOverlay[],
  objects: readonly EditorSceneObject[],
  point: EditorPoint,
  zoom: number,
  layers?: readonly EditorLayer[],
): { readonly screenId: string; readonly screenTerminalSide: "above" | "below" } | null {
  const tolerance = 9 / Math.max(zoom, 0.01);
  const visibleObjects = layers ? objectsInPaintOrder(objects, layers) : objects;
  for (const screen of [...screens].reverse()) {
    if (layers && !isE4OverlayVisible(screen.wireIds, objects, layers)) continue;
    const layout = getE4ScreenLayout(screen, visibleObjects);
    const terminal = layout?.terminals.find((item) =>
      Math.hypot(point.x - item.connectionPoint.x, point.y - item.connectionPoint.y) <= tolerance);
    if (terminal) {
      return { screenId: screen.id, screenTerminalSide: terminal.side };
    }
  }
  return null;
}

export function hitTestE4WireLabel(
  objects: readonly EditorSceneObject[],
  layers: readonly EditorLayer[],
  point: EditorPoint,
  zoom: number,
): { readonly wireId: string } | null {
  const tolerance = 3 / Math.max(zoom, 0.01);
  for (const object of [...objectsInPaintOrder(objects, layers)].reverse()) {
    if (object.kind !== "wire" || !object.label) continue;
    const rawPosition = Number(object.metadata?.e4LabelPosition ?? "0.5");
    const position = Number.isFinite(rawPosition) && rawPosition >= 0 && rawPosition <= 1 ? rawPosition : 0.5;
    const layout = getE4WireLabelLayout(getE4WireRoute(object), object.label, position);
    if (layout && point.x >= layout.x - tolerance && point.x <= layout.x + layout.width + tolerance &&
        point.y >= layout.y - tolerance && point.y <= layout.y + layout.height + tolerance) {
      return { wireId: object.id };
    }
  }
  return null;
}

function pointToSegmentDistance(point: EditorPoint, start: EditorPoint, end: EditorPoint): number {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  if (dx === 0 && dy === 0) return Math.hypot(point.x - start.x, point.y - start.y);
  const t = Math.max(0, Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(point.x - (start.x + t * dx), point.y - (start.y + t * dy));
}

/** Decoration geometry never substitutes the physical wire route. */
export function getDrawingWireStripProfileGeometries(
  object: EditorSceneObject,
  view?: HarnessEditorView,
): readonly WireStripProfileGeometry[] {
  if (view !== "drawing" || object.kind !== "wire" || !object.stripProfiles) return [];
  return (["from", "to"] as const).flatMap((end) => {
    const profile = object.stripProfiles?.[end];
    const geometry = profile && buildWireStripProfileGeometry(object.points ?? [], end, profile);
    return geometry ? [geometry] : [];
  });
}

function polygonContainsPoint(
  polygon: readonly EditorPoint[],
  point: EditorPoint,
  tolerance: number,
): boolean {
  let positive = false;
  let negative = false;
  for (let index = 0; index < polygon.length; index += 1) {
    const start = polygon[index]!;
    const end = polygon[(index + 1) % polygon.length]!;
    if (pointToSegmentDistance(point, start, end) <= tolerance) return true;
    const cross = (end.x - start.x) * (point.y - start.y) - (end.y - start.y) * (point.x - start.x);
    positive ||= cross > 0;
    negative ||= cross < 0;
  }
  return !(positive && negative);
}

function containsPoint(
  object: EditorSceneObject,
  point: EditorPoint,
  tolerance: number,
  view?: HarnessEditorView,
): boolean {
  if (object.kind === "object-index") return point.x >= object.x - tolerance && point.x <= object.x + object.width + tolerance && point.y >= object.y - tolerance && point.y <= object.y + object.height + tolerance;
  if(object.kind.startsWith("graphic-")){const p=object.points??[],width=Number(object.metadata?.graphicWidth??2)/2+tolerance,k=object.metadata?.graphicKind;if(k==="contact")return !!p[0]&&Math.hypot(point.x-p[0].x,point.y-p[0].y)<=Math.max(6,width);if(k==="text"){const fontSize=Number(object.metadata?.graphicFontSize??16);return point.x>=object.x-tolerance&&point.x<=object.x+Math.max(fontSize*1.8,object.label.length*fontSize*.65)+tolerance&&point.y>=object.y-fontSize-tolerance&&point.y<=object.y+fontSize*.2+tolerance;}if(k==="rectangle")return point.x>=Math.min(p[0]?.x??0,p[1]?.x??0)-tolerance&&point.x<=Math.max(p[0]?.x??0,p[1]?.x??0)+tolerance&&point.y>=Math.min(p[0]?.y??0,p[1]?.y??0)-tolerance&&point.y<=Math.max(p[0]?.y??0,p[1]?.y??0)+tolerance;if(k==="ellipse"&&p[0]&&p[1]){const center={x:(p[0].x+p[1].x)/2,y:(p[0].y+p[1].y)/2},rx=Math.abs(p[1].x-p[0].x)/2,ry=Math.abs(p[1].y-p[0].y)/2;return rx>0&&ry>0&&Math.abs(((point.x-center.x)/rx)**2+((point.y-center.y)/ry)**2-1)<=Math.max(.2,width/Math.max(rx,ry));}return p.slice(1).some((v,i)=>pointToSegmentDistance(point,p[i]!,v)<=width)||k==="closedContour"&&pointToSegmentDistance(point,p.at(-1)!,p[0]!)<=width;}
  if(object.kind==="position-rail")return !!object.points?.[0]&&!!object.points?.[1]&&pointToSegmentDistance(point,object.points[0],object.points[1])<=tolerance;
  if(object.kind==="dimension"&&object.metadata?.boundDimension==="true"&&Math.hypot(point.x-object.x,point.y-object.y+7)<=Math.max(16,tolerance))return true;
  if(view==="drawing"&&object.kind==="wire"&&object.visibleWireStrokes)return object.visibleWireStrokes.some(stroke=>{const curve=drawingRouteHitPoints(stroke.points,stroke.radius??object.routeRadius);return curve.slice(1).some((p,i)=>pointToSegmentDistance(point,curve[i]!,p)<=stroke.width/2+Math.min(tolerance,2));});
  if(view==="drawing"&&object.kind==="wire"&&object.paths)return object.paths.some(path=>{const curve=drawingRouteHitPoints(path,object.routeRadius);return curve.slice(1).some((p,i)=>pointToSegmentDistance(point,curve[i]!,p)<=tolerance);});
  if (object.kind === "physical-covering" && object.metadata?.surfaces) return coveringHit(object,point,tolerance)!==null;
  if (object.kind === "physical-covering" || object.kind === "physical-segment") return (object.paths ?? [object.points ?? []]).some(path => {const curve=drawingRouteHitPoints(path,object.routeRadius);return curve.slice(1).some((p,i)=>pointToSegmentDistance(point,curve[i]!,p)<=tolerance+object.width/2);});
  if (object.kind === "wire" || object.kind === "dimension") {
    if (getDrawingWireStripProfileGeometries(object, view).some((geometry) =>
      geometry.primitives.some((primitive) => polygonContainsPoint(primitive.polygon, point, tolerance)))) return true;
    const points = view === "e4" && object.kind === "wire" ? getE4WireRoute(object) : view === "drawing" && object.kind === "wire" ? drawingRouteHitPoints(object.points ?? [],object.routeRadius) : object.points ?? [];
    for (let index = 1; index < points.length; index += 1) {
      const start = points[index - 1];
      const end = points[index];
      if (start && end && pointToSegmentDistance(point, start, end) <= tolerance) return true;
    }
    return false;
  }
  for (const contact of connectorCanvasContactPoints(object, view)) {
    if (!contact || contact.status !== "not-connected") continue;
    const cross = contactCrossCenter(contact);
    if (Math.hypot(point.x - cross.x, point.y - cross.y) <= contact.crossSize + tolerance) return true;
  }
  const e4Layout = view === "drawing" ? null : getE4ConnectorLayout(object);
  const width = e4Layout?.width ?? object.width;
  const height = e4Layout?.height ?? object.height;
  if (point.x >= object.x - tolerance && point.x <= object.x + width + tolerance &&
      point.y >= object.y - tolerance && point.y <= object.y + height + tolerance) return true;
  if (!e4Layout) return false;
  return e4Layout.rows.some((row, rowIndex) => {
    const marker = e4ContactMarker(row.status, e4Layout.connectionSide);
    if (!marker) return false;
    const anchor = e4Layout.contactPoints[rowIndex]!;
    const crossCenter = { x: anchor.x + marker.crossCenter.x, y: anchor.y + marker.crossCenter.y };
    // The marker is a single semantic object. Centre its hit target on the X
    // instead of deriving a thin target from the decorative lead and strokes.
    return Math.hypot(point.x - crossCenter.x, point.y - crossCenter.y) <= marker.crossSize + tolerance;
  });
}

export function hitTestWireRoutePoint(
  object: EditorSceneObject | undefined,
  point: EditorPoint,
  zoom: number,
): number | null {
  if (object?.kind !== "wire" && object?.kind !== "physical-segment") return null;
  if (object.metadata?.physicalRoute === "true") return null;
  const points: readonly EditorPoint[] = object.kind === "physical-segment" ? [object.points![0]!, ...pipeSceneHandles(object), object.points!.at(-1)!] : object.points ?? [];
  const tolerance = 10 / zoom;
  let nearest: number | null = null, distance = tolerance;
  for (let pointIndex = 1; pointIndex < points.length - 1; pointIndex += 1) {
    if(object.pipe?.controlledHandles?.includes(pointIndex-1))continue;
    const candidate = points[pointIndex]!;
    const delta = Math.hypot(point.x - candidate.x, point.y - candidate.y);
    if (delta <= distance) { nearest = pointIndex - 1; distance = delta; }
  }
  return nearest;
}

/** Visible member bends share the same consecutive numbering as authored bends. */
export function numberedPipeBendHandles(object:EditorSceneObject):readonly number[] {
  if(object.kind!=="physical-segment")return [];
  return pipeSceneHandles(object).flatMap((_,index)=>object.pipe?.controlledHandles?.includes(index)?[]:[index]);
}

export function pipeMidpoints(object:EditorSceneObject):readonly {index:number;point:EditorPoint}[] {
  if(object.kind!=="physical-segment")return [];
  if(object.pipe?.midpoints)return object.pipe.midpoints.flatMap((point,index)=>
    object.pipe?.controlledMidpoints?.includes(index)||!object.pipe?.joiningTransitionMidpoints?.some(item=>item.index===index)&&pipeSceneHandles(object).some((handle,handleIndex)=>
      !object.pipe?.controlledHandles?.includes(handleIndex)&&Math.hypot(point.x-handle.x,point.y-handle.y)<=10)?[]:[{point,index}]);
  const points=[object.points![0]!,...pipeSceneHandles(object),object.points!.at(-1)!];
  return points.slice(1).map((p,i)=>({index:i,point:{x:(p.x+points[i]!.x)/2,y:(p.y+points[i]!.y)/2}}));
}

export function e4Midpoints(object:EditorSceneObject):readonly {index:number;point:EditorPoint}[] {
  if(object.kind!=="wire"||!object.points)return [];
  return object.points.slice(1).map((p,i)=>({index:i,point:{x:(p.x+object.points![i]!.x)/2,y:(p.y+object.points![i]!.y)/2}}));
}

export function hitTestWireEnd(
  object: EditorSceneObject | undefined,
  point: EditorPoint,
  zoom: number,
): "from" | "to" | null {
  if (object?.kind !== "wire") return null;
  const points = object.points ?? [];
  if (points.length < 2) return null;
  const tolerance = 10 / zoom;
  if (Math.hypot(point.x - points[0]!.x, point.y - points[0]!.y) <= tolerance) return "from";
  const last = points.at(-1)!;
  return Math.hypot(point.x - last.x, point.y - last.y) <= tolerance ? "to" : null;
}

function contactCount(object: EditorSceneObject): number {
  const value = Number(object.metadata?.contactCount ?? "0");
  return Number.isSafeInteger(value) && value > 0 ? value : 0;
}

const e4BaseColumnIds = ["color", "wireSection", "wire", "terminal", "circuit", "contactType", "number"] as const;
type E4BaseColumnId = typeof e4BaseColumnIds[number];
type E4ColumnId = E4BaseColumnId | `custom:${string}`;
type E4ConnectionSide = "left" | "right";

interface E4ContactRow {
  readonly number: number;
  readonly type: string;
  readonly circuit: string;
  readonly terminal: string;
  readonly wire: string;
  readonly wireSection: string;
  readonly color: string;
  readonly secondaryColor: string;
  readonly name?: string;
  readonly status: "available" | "not-connected";
  readonly customValues: Readonly<Record<string, string>>;
}

export interface E4ConnectorColumnLayout {
  readonly id: E4ColumnId;
  readonly label: string;
  readonly x: number;
  readonly width: number;
}

export interface E4ConnectorLayout {
  readonly designation: string;
  readonly libraryCode: string;
  readonly partNumber: string;
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly titleHeight: number;
  readonly headerHeight: number;
  readonly rowHeight: number;
  readonly footerHeight: number;
  readonly connectionSide: E4ConnectionSide;
  readonly columns: readonly E4ConnectorColumnLayout[];
  readonly rows: readonly E4ContactRow[];
  readonly contactPoints: readonly EditorPoint[];
}

const e4ColumnLabels: Readonly<Record<E4BaseColumnId, string>> = {
  color: "Цвет",
  wire: "Марка",
  wireSection: "Сечение",
  terminal: "Терминал",
  circuit: "Цепь",
  contactType: "Тип",
  number: "№",
};

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseJson(value: string | undefined): unknown {
  if (!value) return null;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return null;
  }
}

function isE4ColumnId(value: unknown): value is E4ColumnId {
  return typeof value === "string" &&
    ((e4BaseColumnIds as readonly string[]).includes(value) || /^custom:[^:]+$/.test(value));
}

function isCustomE4ColumnId(value: E4ColumnId): value is `custom:${string}` {
  return value.startsWith("custom:");
}

function parseE4Columns(value: string | undefined, connectionSide: E4ConnectionSide): readonly E4ColumnId[] {
  const parsed = parseJson(value);
  const unique = Array.isArray(parsed) && parsed.every(isE4ColumnId) && new Set(parsed).size === parsed.length
    ? parsed
    : null;
  const defaultColumns: readonly E4ColumnId[] = connectionSide === "right"
    ? e4BaseColumnIds
    : [...e4BaseColumnIds].reverse();
  if (!unique) return defaultColumns;
  return unique;
}

function parseStringRecord(value: unknown): Readonly<Record<string, string>> | null {
  if (!isRecord(value)) return value === undefined ? {} : null;
  if (Object.values(value).some((item) => typeof item !== "string")) return null;
  return value as Readonly<Record<string, string>>;
}

function parseE4Rows(value: string | undefined): readonly E4ContactRow[] | null {
  const parsed = parseJson(value);
  if (!Array.isArray(parsed)) return null;
  const rows: E4ContactRow[] = [];
  for (const item of parsed) {
    if (!isRecord(item) || !Number.isSafeInteger(item.number) || (item.number as number) < 1) return null;
    const contactType = typeof item.contactType === "string" ? item.contactType : item.type;
    const secondaryColor = item.secondaryColor === undefined ? "" : item.secondaryColor;
    const name = item.name === undefined ? "" : item.name;
    const textValues = [contactType, item.circuit, item.terminal, item.wire, item.wireSection ?? "", item.color, secondaryColor];
    const customValues = parseStringRecord(item.customValues ?? item.values);
    if (!textValues.every((entry) => typeof entry === "string") || typeof name !== "string" || customValues === null ||
        (item.status !== "available" && item.status !== "not-connected")) return null;
    rows.push({
      number: item.number as number,
      type: contactType as string,
      circuit: item.circuit as string,
      terminal: item.terminal as string,
      wire: item.wire as string,
      wireSection: (item.wireSection as string | undefined) ?? "",
      color: item.color as string,
      secondaryColor: secondaryColor as string,
      name,
      status: item.status,
      customValues,
    });
  }
  return rows;
}

function e4CellText(row: E4ContactRow, column: E4ColumnId): string {
  if (column === "number") return String(row.number);
  if (column === "custom:template-name") return row.name ?? "";
  if (isCustomE4ColumnId(column)) return row.customValues[column.slice("custom:".length)] ?? "";
  if (column === "contactType") return row.type;
  if (column === "color") return [row.color, row.secondaryColor].filter(Boolean).join(" / ");
  return row[column];
}

export interface E4ContactMarkerGeometry {
  readonly lineStart: EditorPoint;
  readonly lineEnd: EditorPoint;
  readonly crossCenter: EditorPoint;
  readonly crossSize: number;
}

/**
 * Returns the geometry for the dedicated E4 "not connected" glyph.  The
 * contact remains the anchor; a short lead and a diagonal cross are drawn
 * outside the connector.  This deliberately does not use a text "--X"
 * marker, so the glyph mirrors with the connector's connection side.
 */
export function e4ContactMarker(
  status: E4ContactRow["status"],
  connectionSide: E4ConnectionSide,
): E4ContactMarkerGeometry | null {
  if (status !== "not-connected") return null;
  const direction = connectionSide === "left" ? -1 : 1;
  const lineLength = 12;
  const crossOffset = 16;
  return {
    lineStart: { x: 0, y: 0 },
    lineEnd: { x: direction * lineLength, y: 0 },
    crossCenter: { x: direction * crossOffset, y: 0 },
    crossSize: 5,
  };
}

/**
 * E4 connector metadata contract:
 * { view: "e4", orientation: "left" | "right", designation: string,
 *   partNumber?: string, columns: JSON.stringify(E4ColumnId[]),
 *   columnLabels?: JSON.stringify(Record<string, string>),
 *   rows: JSON.stringify({ number, contactType, circuit, terminal, wire, color, secondaryColor,
 *     status: "available" | "not-connected", customValues: Record<string, string> }[]) }.
 * The orientation names the outer contact side. Invalid metadata deliberately
 * returns null so legacy contactCount connectors keep their compact renderer.
 */
export function getE4ConnectorLayout(object: EditorSceneObject): E4ConnectorLayout | null {
  if (object.kind !== "connector" || object.metadata?.view !== "e4") return null;
  const connectionSide = object.metadata.orientation === "left"
    ? "left"
    : object.metadata.orientation === "right" ? "right" : null;
  const rows = parseE4Rows(object.metadata.rows);
  if (!connectionSide || rows === null) return null;
  const columnIds = parseE4Columns(object.metadata.columns, connectionSide);
  const columnLabels = parseStringRecord(parseJson(object.metadata.columnLabels)) ?? {};
  const designation = object.metadata.designation?.trim() || object.label;
  const libraryCode = object.metadata.libraryCode?.trim() || "FREE";
  const partNumber = object.metadata.partNumber?.trim() ?? "";
  const suppliedWidths = parseJson(object.metadata.columnWidths);
  const authoritativeWidths = Array.isArray(suppliedWidths) && suppliedWidths.length === columnIds.length &&
    suppliedWidths.every(value => typeof value === "number" && Number.isFinite(value) && value > 0) ? suppliedWidths as number[] : null;
  const widths = new Map<E4ColumnId, number>(columnIds.map((column, index) => {
    const label = columnLabels[column] ?? (isCustomE4ColumnId(column) ? column.slice("custom:".length) : e4ColumnLabels[column]);
    const values = rows.map((row) => e4CellText(row, column));
    return [
      column,
      authoritativeWidths?.[index] ?? connectorE4TableColumnWidth(isCustomE4ColumnId(column) ? null : column, label, column === "color" ? [] : values),
    ];
  }));
  const columnWidth = columnIds.reduce((total, column) => total + (widths.get(column) ?? 0), 0);
  const width = authoritativeWidths ? object.width : Math.max(118, columnWidth, connectorE4FooterWidth(libraryCode, partNumber));
  const titleHeight = 24;
  const headerHeight = 28;
  const rowHeight = 24;
  const footerHeight = 24;
  const height = titleHeight + headerHeight + rows.length * rowHeight + footerHeight;
  const widthRemainder = width - columnWidth;
  const flexibleColumn = columnIds.findIndex((column) => column !== "number");
  const growColumn = flexibleColumn < 0 ? 0 : flexibleColumn;
  let columnX = object.x;
  const columns = columnIds.map((id, index) => {
    const columnWidth = widths.get(id)! + (index === (growColumn < 0 ? 0 : growColumn) ? widthRemainder : 0);
    const column = {
      id,
      label: columnLabels[id] ?? (isCustomE4ColumnId(id) ? id.slice("custom:".length) : e4ColumnLabels[id]),
      x: columnX,
      width: columnWidth,
    };
    columnX += column.width;
    return column;
  });
  const contactX = connectionSide === "left" ? object.x : object.x + width;
  const contactPoints = rows.map((_, index) => ({
    x: contactX,
    y: object.y + titleHeight + headerHeight + index * rowHeight + rowHeight / 2,
  }));
  return {
    designation,
    libraryCode,
    partNumber,
    x: object.x,
    y: object.y,
    width,
    height,
    titleHeight,
    headerHeight,
    rowHeight,
    footerHeight,
    connectionSide,
    columns,
    rows,
    contactPoints,
  };
}

function legacyConnectorContactPoints(object: EditorSceneObject): readonly EditorPoint[] {
  const count = contactCount(object);
  if (object.kind !== "connector" || count === 0) return [];
  const spacing = (object.height - 44) / count;
  return Array.from({ length: count }, (_, index) => ({
    x: object.x + object.width,
    y: object.y + 28 + index * spacing,
  }));
}

export interface MaterializedConnectorContactPoint extends EditorPoint {
  readonly direction: "left" | "right" | "up" | "down";
  readonly directionVector?: EditorPoint;
  readonly status: "available" | "not-connected";
}

interface ConnectorCanvasContactPoint extends MaterializedConnectorContactPoint {
  readonly secondaryPoint: EditorPoint | null;
  readonly crossOffset: number;
  readonly crossSize: number;
}

/** Parses local article-materialized coordinates without compacting contact indexes. */
export function getMaterializedConnectorContactPoints(
  object: EditorSceneObject,
): readonly (MaterializedConnectorContactPoint | null)[] | null {
  if (object.kind !== "connector" || !object.metadata?.materializedContactPoints) return null;
  const parsed = parseJson(object.metadata.materializedContactPoints);
  if (!Array.isArray(parsed)) return null;
  const result: (MaterializedConnectorContactPoint | null)[] = [];
  for (const candidate of parsed) {
    if (candidate === null) {
      result.push(null);
      continue;
    }
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return null;
    const record = candidate as Record<string, unknown>;
    if (typeof record.x !== "number" || !Number.isFinite(record.x) ||
        typeof record.y !== "number" || !Number.isFinite(record.y) ||
        !["left", "right", "up", "down"].includes(String(record.direction)) ||
        (record.directionVector !== undefined && (!record.directionVector || typeof record.directionVector !== "object" ||
          typeof (record.directionVector as EditorPoint).x !== "number" || !Number.isFinite((record.directionVector as EditorPoint).x) ||
          typeof (record.directionVector as EditorPoint).y !== "number" || !Number.isFinite((record.directionVector as EditorPoint).y))) ||
        !["available", "not-connected"].includes(String(record.status))) return null;
    result.push({
      x: object.x + record.x,
      y: object.y + record.y,
      direction: record.direction as MaterializedConnectorContactPoint["direction"],
      ...(record.directionVector ? {directionVector:record.directionVector as EditorPoint} : {}),
      status: record.status as MaterializedConnectorContactPoint["status"],
    });
  }
  return result;
}

function connectorCanvasContactPoints(
  object: EditorSceneObject,
  view: HarnessEditorView | undefined,
): readonly (ConnectorCanvasContactPoint | null)[] {
  const materialized = getMaterializedConnectorContactPoints(object);
  const e4Layout = view === "drawing" ? null : getE4ConnectorLayout(object);
  const legacy = e4Layout ? e4Layout.contactPoints : legacyConnectorContactPoints(object);
  const count = Math.max(materialized?.length ?? 0, legacy.length);
  return Array.from({ length: count }, (_, index): ConnectorCanvasContactPoint | null => {
    const pinned = materialized?.[index];
    if (pinned) return {
      ...pinned,
      secondaryPoint: null,
      crossOffset: 12,
      crossSize: 3,
    };
    if(view==="drawing"&&materialized)return null;
    const fallback = legacy[index];
    if (!fallback) return null;
    if (e4Layout) return {
      ...fallback,
      direction: e4Layout.connectionSide,
      status: e4Layout.rows[index]?.status ?? "available",
      secondaryPoint: null,
      crossOffset: 16,
      crossSize: 5,
    };
    return {
      ...fallback,
      direction: "right",
      status: "available",
      secondaryPoint: { x: object.x, y: fallback.y },
      crossOffset: 12,
      crossSize: 3,
    };
  });
}

function contactOutward(direction: MaterializedConnectorContactPoint["direction"]): EditorPoint {
  return direction === "left" ? { x: -1, y: 0 }
    : direction === "right" ? { x: 1, y: 0 }
      : direction === "up" ? { x: 0, y: -1 }
        : { x: 0, y: 1 };
}

function contactCrossCenter(point: ConnectorCanvasContactPoint): EditorPoint {
  const outward = point.directionVector ?? contactOutward(point.direction);
  return {
    x: point.x + outward.x * point.crossOffset,
    y: point.y + outward.y * point.crossOffset,
  };
}

export function drawSelectedConnectorContacts(context: CanvasRenderingContext2D, object: EditorSceneObject, view: HarnessEditorView, zoom: number): void {
  context.save();context.lineWidth=2/zoom;context.strokeStyle="#007da8";context.fillStyle="#e1faff";
  for (const contact of connectorCanvasContactPoints(object,view)) {
    if (!contact) continue;
    for (const point of [contact, ...(contact.secondaryPoint?[contact.secondaryPoint]:[])]) {
      context.beginPath();context.arc(point.x,point.y,6/zoom,0,Math.PI*2);context.fill();context.stroke();
      context.beginPath();context.arc(point.x,point.y,2/zoom,0,Math.PI*2);context.fillStyle="#007da8";context.fill();context.fillStyle="#e1faff";
    }
  }
  context.restore();
}


function drawConnectorContactOverrides(
  context: CanvasRenderingContext2D,
  object: EditorSceneObject,
  view: HarnessEditorView,
  includeFallback: boolean,
): void {
  const points = connectorCanvasContactPoints(object, view);
  const materialized = getMaterializedConnectorContactPoints(object);
  context.save();
  context.lineCap = "round";
  for (let index = 0; index < points.length; index += 1) {
    const point = points[index];
    if (!point) continue;
    if (!includeFallback && !materialized?.[index]) continue;
    if (point.status === "available") {
      context.fillStyle = object.color;
      context.beginPath();
      context.arc(point.x, point.y, 3.5, 0, Math.PI * 2);
      context.fill();
      if (point.secondaryPoint) {
        context.beginPath();
        context.arc(point.secondaryPoint.x, point.secondaryPoint.y, 3.5, 0, Math.PI * 2);
        context.fill();
      }
      continue;
    }
    const cross = contactCrossCenter(point);
    context.strokeStyle = "#2c3fbd";
    context.lineWidth = 1.7;
    context.beginPath();
    context.moveTo(point.x, point.y);
    context.lineTo(cross.x, cross.y);
    context.moveTo(cross.x - point.crossSize, cross.y - point.crossSize);
    context.lineTo(cross.x + point.crossSize, cross.y + point.crossSize);
    context.moveTo(cross.x - point.crossSize, cross.y + point.crossSize);
    context.lineTo(cross.x + point.crossSize, cross.y - point.crossSize);
    context.stroke();
  }
  context.restore();
}

export function hitTestConnectorContact(
  objects: readonly EditorSceneObject[],
  layers: readonly EditorLayer[],
  point: EditorPoint,
  zoom: number,
  view?: HarnessEditorView,
): { readonly connectorId: string; readonly contactIndex: number } | null {
  const layerMap = new Map(layers.map((layer) => [layer.id, layer]));
  const tolerance = 10 / zoom;
  for (const object of [...objects].reverse()) {
    if (object.kind !== "connector" || layerMap.get(object.layerId)?.visible !== true) continue;
    const points = connectorCanvasContactPoints(object, view);
    for (let index = 0; index < points.length; index += 1) {
      const candidate = points[index]!;
      if (!candidate || candidate.status === "not-connected") continue;
      if (Math.hypot(point.x - candidate.x, point.y - candidate.y) <= tolerance ||
          candidate.secondaryPoint !== null &&
          Math.hypot(point.x - candidate.secondaryPoint.x, point.y - candidate.secondaryPoint.y) <= tolerance) {
        return { connectorId: object.id, contactIndex: index };
      }
    }
  }
  return null;
}

export function objectsInPaintOrder(
  objects: readonly EditorSceneObject[],
  layers: readonly EditorLayer[],
  view?: HarnessEditorView,
): readonly EditorSceneObject[] {
  const objectGroups = new Map<string, EditorSceneObject[]>();
  for (const object of objects) {
    const group = objectGroups.get(object.layerId) ?? [];
    group.push(object);
    objectGroups.set(object.layerId, group);
  }

  const result: EditorSceneObject[] = [];
  for (const layer of [...layers].reverse()) {
    if (layer.visible) result.push(...(objectGroups.get(layer.id) ?? []));
  }
  // Connection points are interaction anchors, not ordinary artwork. Keep them
  // above every user-reordered layer so wires, covers and tables cannot hide them.
  const connectionPoints = result.filter((object) => object.kind === "physical-node");
  const artwork = result.filter((object) => object.kind !== "physical-node");
  // Drawing connector pictures are underlays; preserve user order within each pass.
  return view === "drawing"
    ? [...artwork.filter(object => object.kind === "connector"), ...artwork.filter(object => object.kind !== "connector" && object.kind !== "object-index"), ...connectionPoints, ...artwork.filter(object => object.kind === "object-index")]
    : [...artwork, ...connectionPoints];
}

export interface VisibleCableSheathScene {
  readonly geometries: readonly CableSheathGeometry[];
  readonly incompatibleCableIds: readonly string[];
}

/** Resolves visible drawing sheaths without using hidden conductors as geometry. */
export function getVisibleCableSheathScene(
  cables: readonly CableInstance[],
  objects: readonly EditorSceneObject[],
  layers: readonly EditorLayer[],
): VisibleCableSheathScene {
  const layerMap = new Map(layers.map((layer) => [layer.id, layer]));
  const geometries: CableSheathGeometry[] = [];
  const incompatibleCableIds: string[] = [];
  for (const cable of cables) {
    const strip = cable.sheathStrip;
    if (strip && (strip.fromMm === null || strip.toMm === null || cable.lengthMm === null ||
        Math.round((strip.fromMm + strip.toMm) * 1000) === Math.round(cable.lengthMm * 1000))) continue;
    const members = cable.memberWireIds.map((wireId) =>
      objects.find((object) => object.id === wireId && object.kind === "wire"));
    if (members.some((member) => member && layerMap.get(member.layerId)?.visible !== true)) continue;
    const geometry = buildCableSheathGeometry(cable, objects);
    if (geometry) geometries.push(geometry);
    else incompatibleCableIds.push(cable.id);
  }
  return { geometries, incompatibleCableIds };
}

/** Selects the painted sheath contour while leaving its inner conductors easy to hit. */
export function hitTestCableSheath(
  geometries: readonly CableSheathGeometry[],
  point: EditorPoint,
  zoom: number,
): CableSheathGeometry | null {
  const safeZoom = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  const tolerance = 7 / safeZoom;
  for (let index = geometries.length - 1; index >= 0; index -= 1) {
    const geometry = geometries[index]!;
    if (point.x < geometry.bounds.x - tolerance ||
        point.x > geometry.bounds.x + geometry.bounds.width + tolerance ||
        point.y < geometry.bounds.y - tolerance ||
        point.y > geometry.bounds.y + geometry.bounds.height + tolerance) continue;
    if (geometry.edges.some(([start, end]) => pointToSegmentDistance(point, start, end) <= tolerance)) {
      return geometry;
    }
  }
  return null;
}

export function hitTestEditorScene(
  objects: readonly EditorSceneObject[],
  layers: readonly EditorLayer[],
  point: EditorPoint,
  zoom: number,
  view?: HarnessEditorView,
  componentTemplateViewInstances: readonly ComponentTemplateViewInstance[] = [],
  resolveComponentTemplateAssetUrl?: ResolveComponentTemplateAssetUrl,
): string | null {
  const paintOrder = objectsInPaintOrder(objects, layers, view);
  const componentViews = new Map(componentTemplateViewInstances.map(instance => [instance.objectId, instance]));
  const tolerance = 7 / zoom;
  if (view === "drawing") {
    const objectIndex = [...paintOrder].reverse().find(object => object.kind === "object-index" && containsPoint(object, point, tolerance, view));
    if (objectIndex) return objectIndex.id;
  }
  const node = [...paintOrder].reverse().find(o => o.kind === "physical-node" && containsPoint(o, point, tolerance, view));
  if (node) return node.id;
  if (view === "drawing") {
    const position=[...paintOrder].reverse().find(o=>(o.kind==="position-leader"||o.kind==="leader-anchor"||o.kind==="rail-handle")&&containsPoint(o,point,tolerance,view));
    if(position)return position.id;
    const rail=[...paintOrder].reverse().find(o=>o.kind==="position-rail"&&containsPoint(o,point,tolerance,view));
    if(rail)return rail.id;
    const contact = hitTestConnectorContact(paintOrder, layers, point, zoom, view);
    if (contact) return contact.connectorId;
    const annotation=[...paintOrder].reverse().find(o=>(o.kind==="dimension"||o.kind==="physical-covering"||o.kind==="drawing-table")&&containsPoint(o,point,tolerance,view));
    if(annotation)return annotation.id;
    const pipe = [...paintOrder].reverse().find(o => o.kind === "physical-segment" && containsPoint(o, point, tolerance, view));
    if (pipe) return pipe.id;
    const visibleWire=[...paintOrder].reverse().find(o=>o.kind==="wire"&&!!o.visibleWireStrokes&&containsPoint(o,point,tolerance,view));
    if(visibleWire)return visibleWire.id;
  }
  for (let index = paintOrder.length - 1; index >= 0; index -= 1) {
    const object = paintOrder[index];
    const instance = object?.kind === "connector" ? componentViews.get(object.id) : undefined;
    const companions = object && instance && view === "e4" ? projectE4DrawingCompanions(instance, object, getE4ConnectorLayout(object)?.width ?? object.width, resolveComponentTemplateAssetUrl) : [];
    if(companions.some(companion=>companion.visible && point.x >= companion.bounds.minX-tolerance && point.x <= companion.bounds.maxX+tolerance && point.y >= companion.bounds.minY-tolerance && point.y <= companion.bounds.maxY+tolerance)) return object!.id;
    const projection = object && instance && view
      ? projectComponentTemplateView(instance, view, { x: object.x, y: object.y }, resolveComponentTemplateAssetUrl)
      : null;
    const contactSide = object && instance && view === "drawing"
      ? projectContactSideView(instance, { x: object.x, y: object.y }, projection?.bounds ?? {
        minX: object.x, minY: object.y, maxX: object.x + object.width, maxY: object.y + object.height,
      }, resolveComponentTemplateAssetUrl)
      : null;
    if (contactSide?.visible && point.x >= contactSide.drawing.bounds.minX - tolerance && point.x <= contactSide.drawing.bounds.maxX + tolerance &&
        point.y >= contactSide.drawing.bounds.minY - tolerance && point.y <= contactSide.drawing.bounds.maxY + tolerance) return object!.id;
    if (projection && point.x >= projection.bounds.minX - tolerance && point.x <= projection.bounds.maxX + tolerance &&
        point.y >= projection.bounds.minY - tolerance && point.y <= projection.bounds.maxY + tolerance) return object!.id;
    if(view==="drawing"&&object?.kind==="wire"&&object.metadata?.physicalRoute==="true"&&paintOrder.some(o=>o.kind==="physical-segment"&&containsPoint(o,point,tolerance,view)))continue;
    if (object && !(projection && view === "drawing") && containsPoint(object, point, tolerance, view)) return object.id;
  }
  return null;
}

function roundedRectangle(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  const r = Math.min(radius, width / 2, height / 2);
  context.beginPath();
  context.moveTo(x + r, y);
  context.lineTo(x + width - r, y);
  context.quadraticCurveTo(x + width, y, x + width, y + r);
  context.lineTo(x + width, y + height - r);
  context.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  context.lineTo(x + r, y + height);
  context.quadraticCurveTo(x, y + height, x, y + height - r);
  context.lineTo(x, y + r);
  context.quadraticCurveTo(x, y, x + r, y);
  context.closePath();
}

function drawGrid(context: CanvasRenderingContext2D, width: number, height: number, camera: EditorCamera) {
  context.fillStyle = "#f8fafb";
  context.fillRect(0, 0, width, height);
  const minorStep = 20 * camera.zoom;
  const majorStep = 100 * camera.zoom;
  if (minorStep >= 8) {
    context.fillStyle = "#dfe6ea";
    const startX = ((camera.offsetX % minorStep) + minorStep) % minorStep;
    const startY = ((camera.offsetY % minorStep) + minorStep) % minorStep;
    for (let x = startX; x < width; x += minorStep) {
      for (let y = startY; y < height; y += minorStep) context.fillRect(Math.round(x), Math.round(y), 1, 1);
    }
  }
  context.strokeStyle = "#d6e0e5";
  context.lineWidth = 1;
  context.beginPath();
  const majorX = ((camera.offsetX % majorStep) + majorStep) % majorStep;
  const majorY = ((camera.offsetY % majorStep) + majorStep) % majorStep;
  for (let x = majorX; x < width; x += majorStep) {
    context.moveTo(Math.round(x) + 0.5, 0);
    context.lineTo(Math.round(x) + 0.5, height);
  }
  for (let y = majorY; y < height; y += majorStep) {
    context.moveTo(0, Math.round(y) + 0.5);
    context.lineTo(width, Math.round(y) + 0.5);
  }
  context.stroke();
}

function drawE4CellText(
  context: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  width: number,
  height: number,
) {
  context.save();
  context.beginPath();
  context.rect(x + 3, y + 1, Math.max(0, width - 6), Math.max(0, height - 2));
  context.clip();
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(text, x + width / 2, y + height / 2);
  context.restore();
}

function drawE4ColorCell(
  context: CanvasRenderingContext2D,
  row: E4ContactRow,
  column: E4ConnectorColumnLayout,
  y: number,
  height: number,
) {
  const label = e4CellText(row, "color");
  if (!label) return;
  const swatchWidth = Math.min(26, Math.max(14, column.width - 12));
  const swatchHeight = Math.min(13, height - 7);
  const swatchX = column.x + 5;
  const swatchY = y + (height - swatchHeight) / 2;
  const primary = resolveWireColorHex(row.color, undefined, "#d9e2e7");
  const secondary = row.secondaryColor
    ? resolveWireColorHex(row.secondaryColor, undefined, "#d9e2e7")
    : "";
  context.save();
  context.beginPath();
  context.rect(column.x + 1, y + 1, column.width - 2, height - 2);
  context.clip();
  if (secondary) {
    context.fillStyle = primary;
    context.beginPath();
    context.moveTo(swatchX, swatchY);
    context.lineTo(swatchX + swatchWidth, swatchY);
    context.lineTo(swatchX + swatchWidth, swatchY + swatchHeight);
    context.closePath();
    context.fill();
    context.fillStyle = secondary;
    context.beginPath();
    context.moveTo(swatchX, swatchY);
    context.lineTo(swatchX, swatchY + swatchHeight);
    context.lineTo(swatchX + swatchWidth, swatchY + swatchHeight);
    context.closePath();
    context.fill();
  } else {
    context.fillStyle = primary;
    context.fillRect(swatchX, swatchY, swatchWidth, swatchHeight);
  }
  context.strokeStyle = "#8297a2";
  context.lineWidth = 1;
  context.strokeRect(swatchX, swatchY, swatchWidth, swatchHeight);
  if (column.width - swatchWidth >= 36) {
    context.fillStyle = "#284957";
    context.font = "500 8px Inter, Arial, sans-serif";
    context.textAlign = "left";
    context.textBaseline = "middle";
    context.fillText(label, swatchX + swatchWidth + 4, y + height / 2);
  }
  context.restore();
}

function drawE4Connector(
  context: CanvasRenderingContext2D,
  object: EditorSceneObject,
  layout: E4ConnectorLayout,
  selected: boolean,
) {
  const headerY = layout.y + layout.titleHeight;
  const bodyY = headerY + layout.headerHeight;
  const footerY = bodyY + layout.rows.length * layout.rowHeight;
  context.fillStyle = "#ffffff";
  context.fillRect(layout.x, layout.y, layout.width, layout.height);
  context.fillStyle = "#e9f0f3";
  context.fillRect(layout.x, layout.y, layout.width, layout.titleHeight);
  context.fillStyle = "#f4f7f8";
  context.fillRect(layout.x, headerY, layout.width, layout.headerHeight);
  context.fillStyle = "#f4f7f8";
  context.fillRect(layout.x, footerY, layout.width, layout.footerHeight);

  const hasDiagnostic = object.metadata?.diagnostic === "error";
  // E4 tables keep the crisp selected outline even when they are not the
  // active object.  The table is dense and its 1.5px default border becomes
  // soft at common canvas zoom levels; the C2 reference shows the readable
  // 3px blue outline as the steady table treatment.  Selection still controls
  // interaction handles in the final canvas pass.
  context.strokeStyle = hasDiagnostic ? "#c43d3d" : "#087bb4";
  context.lineWidth = 3;
  context.strokeRect(layout.x, layout.y, layout.width, layout.height);
  if (hasDiagnostic) {
    context.save();
    context.fillStyle = "#c43d3d";
    context.beginPath();
    context.arc(layout.x + layout.width - 12, layout.y + layout.titleHeight / 2, 8, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = "#fff";
    context.font = "800 11px Inter, Arial, sans-serif";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText("!", layout.x + layout.width - 12, layout.y + layout.titleHeight / 2 + 0.5);
    context.restore();
  }
  context.lineWidth = 1;
  context.strokeStyle = "#9fb2bc";
  context.beginPath();
  context.moveTo(layout.x, headerY);
  context.lineTo(layout.x + layout.width, headerY);
  context.moveTo(layout.x, bodyY);
  context.lineTo(layout.x + layout.width, bodyY);
  context.moveTo(layout.x, footerY);
  context.lineTo(layout.x + layout.width, footerY);
  for (const column of layout.columns.slice(1)) {
    context.moveTo(column.x, headerY);
    context.lineTo(column.x, footerY);
  }
  for (let index = 1; index < layout.rows.length; index += 1) {
    const rowY = bodyY + index * layout.rowHeight;
    context.moveTo(layout.x, rowY);
    context.lineTo(layout.x + layout.width, rowY);
  }
  context.stroke();

  context.fillStyle = "#17384b";
  context.font = "800 12px Inter, Arial, sans-serif";
  drawE4CellText(context, layout.designation, layout.x + layout.width * 0.3, layout.y, layout.width * 0.4, layout.titleHeight);
  context.fillStyle = "#2584a8";
  context.font = "700 8px Inter, Arial, sans-serif";
  context.textAlign = "left";
  context.textBaseline = "middle";
  context.fillText("⊕ Добавить…", layout.x + 6, layout.y + layout.titleHeight / 2);

  context.font = "700 9px Inter, Arial, sans-serif";
  context.textAlign = "left";
  context.textBaseline = "middle";
  const codeWidth = Math.min(layout.width * 0.35, Math.max(34, context.measureText(layout.libraryCode).width + 14));
  context.save();
  context.beginPath();
  context.rect(layout.x + 5, footerY + 1, Math.max(0, codeWidth - 10), layout.footerHeight - 2);
  context.clip();
  context.fillStyle = "#405f6e";
  context.fillText(layout.libraryCode, layout.x + 7, footerY + layout.footerHeight / 2);
  context.restore();
  context.save();
  context.beginPath();
  context.rect(layout.x + codeWidth, footerY + 1, Math.max(0, layout.width - codeWidth - 7), layout.footerHeight - 2);
  context.clip();
  context.fillStyle = "#d62f45";
  context.fillText(layout.partNumber, layout.x + codeWidth + 3, footerY + layout.footerHeight / 2);
  context.restore();

  context.fillStyle = "#405f6e";
  context.font = "700 10px Inter, Arial, sans-serif";
  for (const column of layout.columns) {
    drawE4CellText(context, column.label, column.x, headerY, column.width, layout.headerHeight);
  }

  context.fillStyle = "#284957";
  context.font = "500 10px Inter, Arial, sans-serif";
  layout.rows.forEach((row, rowIndex) => {
    const rowY = bodyY + rowIndex * layout.rowHeight;
    for (const column of layout.columns) {
      if (column.id === "color") drawE4ColorCell(context, row, column, rowY, layout.rowHeight);
      else drawE4CellText(context, e4CellText(row, column.id), column.x, rowY, column.width, layout.rowHeight);
    }
    if (getMaterializedConnectorContactPoints(object)?.[rowIndex]) return;
    const point = layout.contactPoints[rowIndex]!;
    const marker = e4ContactMarker(row.status, layout.connectionSide);
    if (marker) {
      context.save();
      context.strokeStyle = "#2c3fbd";
      context.lineWidth = 1.7;
      context.lineCap = "round";
      context.beginPath();
      context.moveTo(point.x + marker.lineStart.x, point.y + marker.lineStart.y);
      context.lineTo(point.x + marker.lineEnd.x, point.y + marker.lineEnd.y);
      context.stroke();
      context.beginPath();
      context.moveTo(
        point.x + marker.crossCenter.x - marker.crossSize,
        point.y + marker.crossCenter.y - marker.crossSize,
      );
      context.lineTo(
        point.x + marker.crossCenter.x + marker.crossSize,
        point.y + marker.crossCenter.y + marker.crossSize,
      );
      context.moveTo(
        point.x + marker.crossCenter.x - marker.crossSize,
        point.y + marker.crossCenter.y + marker.crossSize,
      );
      context.lineTo(
        point.x + marker.crossCenter.x + marker.crossSize,
        point.y + marker.crossCenter.y - marker.crossSize,
      );
      context.stroke();
      context.restore();
    } else {
      context.fillStyle = object.color;
      context.beginPath();
      context.arc(point.x, point.y, 3.5, 0, Math.PI * 2);
      context.fill();
    }
    context.fillStyle = "#284957";
    context.font = "500 10px Inter, Arial, sans-serif";
  });
}

const stripProfilePalette = ["#d6ad65", "#e4ebef", "#a9b7c0", "#7a929e", "#b8c9c2"] as const;
const stripProfileOutline = "#344b59";
const stripProfileStrokeWidth = 1.5;

export interface FreeWireEndpointRef {
  readonly wireId: string;
  readonly end: "from" | "to";
  readonly point: EditorPoint;
}

export interface EditorSelectionRectangle {
  readonly start: EditorPoint;
  readonly end: EditorPoint;
}

function normalizedSelectionRectangle({start,end}:EditorSelectionRectangle) {
  return {minX:Math.min(start.x,end.x),minY:Math.min(start.y,end.y),maxX:Math.max(start.x,end.x),maxY:Math.max(start.y,end.y)};
}

function rectangleOverlaps(left:ReturnType<typeof normalizedSelectionRectangle>,right:ReturnType<typeof normalizedSelectionRectangle>):boolean {
  return left.minX<=right.maxX&&left.maxX>=right.minX&&left.minY<=right.maxY&&left.maxY>=right.minY;
}

function pointInSelectionRectangle(point:EditorPoint,rectangle:ReturnType<typeof normalizedSelectionRectangle>):boolean {
  return point.x>=rectangle.minX&&point.x<=rectangle.maxX&&point.y>=rectangle.minY&&point.y<=rectangle.maxY;
}

function orientation(a:EditorPoint,b:EditorPoint,c:EditorPoint):number {
  return (b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x);
}

function pointOnSegment(point:EditorPoint,start:EditorPoint,end:EditorPoint):boolean {
  return Math.abs(orientation(start,end,point))<.000001&&point.x>=Math.min(start.x,end.x)&&point.x<=Math.max(start.x,end.x)&&point.y>=Math.min(start.y,end.y)&&point.y<=Math.max(start.y,end.y);
}

function segmentsIntersect(a:EditorPoint,b:EditorPoint,c:EditorPoint,d:EditorPoint):boolean {
  const abC=orientation(a,b,c),abD=orientation(a,b,d),cdA=orientation(c,d,a),cdB=orientation(c,d,b);
  if((abC>0)!==(abD>0)&&(cdA>0)!==(cdB>0))return true;
  return abC===0&&pointOnSegment(c,a,b)||abD===0&&pointOnSegment(d,a,b)||cdA===0&&pointOnSegment(a,c,d)||cdB===0&&pointOnSegment(b,c,d);
}

function pathIntersectsSelectionRectangle(points:readonly EditorPoint[],rectangle:ReturnType<typeof normalizedSelectionRectangle>,closed=false):boolean {
  if(points.some(point=>pointInSelectionRectangle(point,rectangle)))return true;
  const corners:EditorPoint[]=[{x:rectangle.minX,y:rectangle.minY},{x:rectangle.maxX,y:rectangle.minY},{x:rectangle.maxX,y:rectangle.maxY},{x:rectangle.minX,y:rectangle.maxY}];
  const edges=[...points.slice(1).map((end,index)=>[points[index]!,end] as const),...(closed&&points.length>2?[[points.at(-1)!,points[0]!] as const]:[])];
  return edges.some(([start,end])=>corners.some((corner,index)=>segmentsIntersect(start,end,corner,corners[(index+1)%corners.length]!)));
}

function ellipseSelectionPath(start:EditorPoint,end:EditorPoint):EditorPoint[] {
  const center={x:(start.x+end.x)/2,y:(start.y+end.y)/2},radiusX=Math.abs(end.x-start.x)/2,radiusY=Math.abs(end.y-start.y)/2;
  return Array.from({length:33},(_,index)=>{const angle=index/32*Math.PI*2;return{x:center.x+Math.cos(angle)*radiusX,y:center.y+Math.sin(angle)*radiusY};});
}

/** Any touched visible object is a candidate for window selection. */
export function objectIntersectsSelectionRectangle(object:EditorSceneObject,selection:EditorSelectionRectangle):boolean {
  const rectangle=normalizedSelectionRectangle(selection),points=object.points??[],kind=object.metadata?.graphicKind;
  if(kind==="text"){
    const fontSize=Number(object.metadata?.graphicFontSize??16),width=Math.max(fontSize*1.8,object.label.length*fontSize*.65);
    return rectangleOverlaps(rectangle,{minX:object.x,minY:object.y-fontSize,maxX:object.x+width,maxY:object.y+fontSize*.2});
  }
  if(kind==="contact"){
    const point=points[0]??{x:object.x,y:object.y};
    return rectangleOverlaps(rectangle,{minX:point.x-6,minY:point.y-6,maxX:point.x+6,maxY:point.y+6});
  }
  if(kind==="rectangle")return rectangleOverlaps(rectangle,{minX:Math.min(points[0]?.x??object.x,points[1]?.x??object.x+object.width),minY:Math.min(points[0]?.y??object.y,points[1]?.y??object.y+object.height),maxX:Math.max(points[0]?.x??object.x,points[1]?.x??object.x+object.width),maxY:Math.max(points[0]?.y??object.y,points[1]?.y??object.y+object.height)});
  if(kind==="ellipse"&&points[0]&&points[1])return pathIntersectsSelectionRectangle(ellipseSelectionPath(points[0],points[1]),rectangle);
  const paths=object.paths??(points.length?[points]:[]);
  if(paths.some(path=>pathIntersectsSelectionRectangle(path,rectangle,kind==="closedContour")))return true;
  return rectangleOverlaps(rectangle,{minX:object.x,minY:object.y,maxX:object.x+Math.max(1,object.width),maxY:object.y+Math.max(1,object.height)});
}

export function selectionRectangleObjectIds(objects:readonly EditorSceneObject[],layers:readonly EditorLayer[],selection:EditorSelectionRectangle):readonly string[] {
  const visible=new Set(layers.filter(layer=>layer.visible).map(layer=>layer.id));
  return objects.filter(object=>visible.has(object.layerId)&&objectIntersectsSelectionRectangle(object,selection)).map(object=>object.id);
}

export type EditorSelectionMode = "replace" | "add" | "toggle";

export interface FreeWireEndAxis {
  readonly end: "from" | "to";
  readonly wireIds: readonly string[];
  readonly x: number;
  readonly minY: number;
  readonly maxY: number;
}

/** Free ends that can share a vertical alignment axis. End orientation and
 * layer are part of compatibility; Y remains independent while X is shared. */
export function freeWireEndpointRefs(objects: readonly EditorSceneObject[], selectedIds: ReadonlySet<string>, layers: readonly EditorLayer[]): FreeWireEndpointRef[] {
  return objects.flatMap(object => {
    if (object.kind !== "wire" || !selectedIds.has(object.id) || !layers.some(layer => layer.id === object.layerId && layer.visible && !layer.locked)) return [];
    return (["from", "to"] as const).flatMap(end => {
      const point = end === "from" ? object.points?.[0] : object.points?.at(-1);
      return point && object.metadata?.[end === "from" ? "freeFrom" : "freeTo"] === "true"
        ? [{ wireId: object.id, end, point }]
        : [];
    });
  });
}

export function freeWireEndAxes(refs: readonly FreeWireEndpointRef[]): FreeWireEndAxis[] {
  return (["from", "to"] as const).flatMap(end => {
    const side = refs.filter(ref => ref.end === end);
    if (side.length < 2) return [];
    return [{ end, wireIds: side.map(ref => ref.wireId),
      x: side.reduce((sum, ref) => sum + ref.point.x, 0) / side.length,
      minY: Math.min(...side.map(ref => ref.point.y)),
      maxY: Math.max(...side.map(ref => ref.point.y)) }];
  });
}

export function freeWireEndpointAxis(refs: readonly FreeWireEndpointRef[]): number | null {
  return freeWireEndAxes(refs)[0]?.x ?? null;
}

export function snapFreeWireEndpointX(point: EditorPoint, refs: readonly FreeWireEndpointRef[], wireId: string, end: "from" | "to", tolerance = 8): EditorPoint {
  const candidates = refs.filter(ref => ref.end === end && ref.wireId !== wireId && Math.abs(ref.point.x - point.x) <= tolerance)
    .sort((a, b) => Math.abs(a.point.x - point.x) - Math.abs(b.point.x - point.x));
  return candidates.length ? { x: candidates[0]!.point.x, y: point.y } : point;
}

export interface WireDrawingEndArtworkPlacement {
  readonly x: number;
  readonly y: number;
  readonly rotation: number;
  readonly scale: number;
}

/** The artwork's jacket edge is x=222 in its viewBox.  Keeping that edge at
 * the authored endpoint places all of the terminal outside the wire route. */
export function wireDrawingEndArtworkPlacement(origin: EditorPoint, adjacent: EditorPoint, drawingWidth: number): WireDrawingEndArtworkPlacement | null {
  const dx = adjacent.x - origin.x, dy = adjacent.y - origin.y;
  const length = Math.hypot(dx, dy);
  if (!length) return null;
  const scale = Math.min(Math.max(drawingWidth, 3) / 52, length / 280);
  return {
    x: origin.x - dx / length * 222 * scale,
    y: origin.y - dy / length * 222 * scale,
    rotation: Math.atan2(dy, dx),
    scale,
  };
}

function drawWireDrawingEndStyles(context: CanvasRenderingContext2D, object: EditorSceneObject, selected: boolean, cache: ComponentTemplateImageCache): void {
  const points = object.points ?? [];
  const styles = object.drawingEndStyles;
  if (!styles || points.length < 2) return;
  context.save();
  const radius = Math.max(2.5, Number(object.metadata?.drawingWidth ?? 2) * (selected ? 0.9 : 0.75));
  (['from', 'to'] as const).forEach((end) => {
    if (object.metadata?.[`free${end === "from" ? "From" : "To"}`] === "false") return;
    const origin = end === 'from' ? points[0]! : points[points.length - 1]!;
    const adjacent = end === 'from' ? points[1]! : points[points.length - 2]!;
    const dx = adjacent.x - origin.x, dy = adjacent.y - origin.y;
    const length = Math.hypot(dx, dy);
    if (!length || styles[end] === 'cut') return;
    const svg = renderPartSvg(styles[end], object.color);
    const entry = cache.get('data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg));
    if (entry.state === 'loaded') {
      const placement = wireDrawingEndArtworkPlacement(origin, adjacent, Number(object.metadata?.drawingWidth ?? 3));
      if (!placement) return;
      context.save(); context.translate(placement.x, placement.y); context.rotate(placement.rotation);
      context.scale(placement.scale, placement.scale); context.drawImage(entry.image, 0, -120, 1200, 240); context.restore();
      return;
    }
    const nx = -dy / length, ny = dx / length;
    const depth = Math.min(12, Math.max(5, length * 0.12));
    const tip = { x: origin.x - dx / length * depth, y: origin.y - dy / length * depth };
    context.strokeStyle = selected ? '#1179ac' : '#344b59';
    context.fillStyle = styles[end] === 'tin' ? '#b8c7cc' : styles[end] === 'copper' ? '#ca7e4a' : styles[end] === 'sealed' || styles[end] === 'sealed-pin' ? '#7a929e' : object.color;
    context.lineWidth = 1.2;
    context.beginPath();
    context.moveTo(origin.x + nx * radius, origin.y + ny * radius);
    context.lineTo(tip.x + nx * radius * 1.2, tip.y + ny * radius * 1.2);
    context.lineTo(tip.x - nx * radius * 1.2, tip.y - ny * radius * 1.2);
    context.lineTo(origin.x - nx * radius, origin.y - ny * radius);
    context.closePath(); context.fill(); context.stroke();
    if (styles[end] === 'terminal' || styles[end] === 'sealed' || styles[end] === 'sealed-pin') {
      context.beginPath(); context.arc(tip.x, tip.y, radius * 1.25, 0, Math.PI * 2); context.stroke();
    }
    if (styles[end] === 'sealed-pin') {
      context.beginPath(); context.moveTo(tip.x, tip.y); context.lineTo(tip.x - dx / length * radius * 2.2, tip.y - dy / length * radius * 2.2); context.stroke();
    }
  });
  context.restore();
}

function drawWireStripProfiles(
  context: CanvasRenderingContext2D,
  object: EditorSceneObject,
  selected: boolean,
): void {
  context.save();
  context.setLineDash([]);
  context.lineWidth = stripProfileStrokeWidth;
  context.lineJoin = "miter";
  context.strokeStyle = selected ? "#1179ac" : stripProfileOutline;
  for (const geometry of getDrawingWireStripProfileGeometries(object, "drawing")) {
    geometry.primitives.forEach((primitive, index) => {
      context.beginPath();
      primitive.polygon.forEach((point, index) => index === 0
        ? context.moveTo(point.x, point.y)
        : context.lineTo(point.x, point.y));
      context.closePath();
      context.fillStyle = index === geometry.primitives.length - 1
        ? object.color
        : stripProfilePalette[(primitive.layerIndex - 1) % stripProfilePalette.length]!;
      context.fill();
      if (object.metadata?.volumeShading === "true") {
        drawVolumeSurface(context, primitive.polygon, primitive.centerline);
        context.beginPath();
        primitive.polygon.forEach((point, i) => i === 0 ? context.moveTo(point.x, point.y) : context.lineTo(point.x, point.y));
        context.closePath();
      }
      context.stroke();
    });
  }
  context.restore();
}

export function drawEditorSceneObject(
  context: CanvasRenderingContext2D,
  object: EditorSceneObject,
  selected: boolean,
  view: HarnessEditorView,
  componentTemplateViewInstance?: ComponentTemplateViewInstance,
  resolveComponentTemplateAssetUrl?: ResolveComponentTemplateAssetUrl,
  componentTemplateImageCache = new ComponentTemplateImageCache(),
  minimumStrokePixels = 0,
) {
  context.save();
  if (object.kind === "object-index") {
    const scale = Number(object.metadata?.indexScale ?? 1);
    context.font = `700 ${13 * scale}px Inter, Arial, sans-serif`;
    context.textAlign = "left";
    context.textBaseline = "middle";
    const padX = 4 * scale, height = 18 * scale, width = Math.max(object.width, context.measureText(object.label).width + padX * 2);
    context.fillStyle = "rgba(255,255,255,.94)";
    context.fillRect(object.x, object.y, width, height);
    context.fillStyle = selected ? "#1179ac" : object.color;
    context.fillText(object.label, object.x + padX, object.y + height / 2);
    context.restore(); return;
  }
  if(object.kind.startsWith("graphic-")){const p=object.points??[],kind=object.metadata?.graphicKind;context.strokeStyle=selected?"#1179ac":object.color;context.fillStyle=context.strokeStyle;context.lineWidth=selected?Number(object.metadata?.graphicWidth??2)+1:Number(object.metadata?.graphicWidth??2);context.lineCap="round";context.lineJoin="round";context.beginPath();if(kind==="contact"){const q=p[0]!;context.arc(q.x,q.y,5,0,Math.PI*2);context.moveTo(q.x-9,q.y);context.lineTo(q.x+9,q.y);context.moveTo(q.x,q.y-9);context.lineTo(q.x,q.y+9);context.stroke();}else if(kind==="text"){const q=p[0]!;context.translate(q.x,q.y);context.rotate(Number(object.metadata?.graphicAngle??0)*Math.PI/180);const fontSize=Number(object.metadata?.graphicFontSize??16),fontFamily=object.metadata?.graphicFontFamily??"Arial";context.font=`${fontSize}px ${fontFamily}`;context.fillText(object.metadata?.graphicText??object.label,0,0);context.restore();return;}else if(kind==="rectangle"){const a=p[0]!,b=p[1]!;context.strokeRect(Math.min(a.x,b.x),Math.min(a.y,b.y),Math.abs(a.x-b.x),Math.abs(a.y-b.y));}else if(kind==="ellipse"){const a=p[0]!,b=p[1]!;context.ellipse((a.x+b.x)/2,(a.y+b.y)/2,Math.abs(a.x-b.x)/2,Math.abs(a.y-b.y)/2,0,0,Math.PI*2);context.stroke();}else if(kind==="bezier"&&p.length===4){context.moveTo(p[0]!.x,p[0]!.y);context.bezierCurveTo(p[1]!.x,p[1]!.y,p[2]!.x,p[2]!.y,p[3]!.x,p[3]!.y);context.stroke();}else{p.forEach((q,i)=>i?context.lineTo(q.x,q.y):context.moveTo(q.x,q.y));if(kind==="closedContour")context.closePath();context.stroke();}context.restore();return;}
  if(object.kind==="drawing-table") {
    const widths=JSON.parse(object.metadata?.widths ?? "[]") as number[],headers=JSON.parse(object.metadata?.headers ?? "[]") as string[],rows=JSON.parse(object.metadata?.rows ?? "[]") as string[][];
    context.fillStyle="#fff";context.fillRect(object.x,object.y,object.width,object.height);context.strokeStyle=selected?"#1179ac":object.color;context.lineWidth=selected?2:1;context.strokeRect(object.x,object.y,object.width,object.height);
    context.fillStyle=object.color;context.font="bold 12px Arial";context.fillText(object.label,object.x+8,object.y+17);
    const paintRow=(row:string[],y:number,height:number,bold:boolean)=>{let x=object.x;context.font=`${bold?"bold ":""}11px Arial`;row.forEach((value,i)=>{context.strokeRect(x,y,widths[i]!,height);context.save();context.beginPath();context.rect(x+3,y+1,widths[i]!-6,height-2);context.clip();context.fillText(value,x+5,y+height/2+4);context.restore();x+=widths[i]!;});};
    paintRow(headers,object.y+24,28,true);rows.forEach((r,i)=>paintRow(r,object.y+52+i*32,32,false));context.restore();return;
  }
  if(object.kind==="position-leader") {
    const a=object.points?.[0],b=object.points?.[1],radius=object.width/2,scale=radius/12;
    if(a&&b){context.strokeStyle=object.color;context.lineWidth=(selected?2:1)*scale;context.beginPath();context.moveTo(a.x,a.y);context.lineTo(b.x,b.y);context.stroke();context.beginPath();context.arc(b.x,b.y,radius,0,Math.PI*2);context.fillStyle="#fff";context.fill();context.stroke();context.fillStyle=object.color;context.textAlign="center";context.textBaseline="middle";context.font=`${12*scale}px Arial`;context.fillText(object.label,b.x,b.y,radius*1.6);}
    context.restore();return;
  }
  if(object.kind==="position-rail") {
    const a=object.points?.[0],b=object.points?.[1];
    if(a&&b){context.strokeStyle=selected?"#1179ac":object.color;context.lineWidth=selected?2:1;context.setLineDash([9,5,2,5]);context.beginPath();context.moveTo(a.x,a.y);context.lineTo(b.x,b.y);context.stroke();}
    context.restore();return;
  }
  if(object.kind==="rail-handle") {const radius=object.width/2;context.fillStyle=selected?"#1179ac":object.color;context.beginPath();context.arc(object.x+radius,object.y+radius,radius,0,Math.PI*2);context.fill();context.restore();return;}
  if(object.kind==="leader-anchor") {const radius=object.width/2;context.fillStyle=selected?"#1179ac":object.color;context.beginPath();context.arc(object.x+radius,object.y+radius,radius,0,Math.PI*2);context.fill();context.restore();return;}
  if(object.kind==="physical-covering") {drawCoveringSurface(context,object,selected);context.restore();return;}
  if(object.kind==="physical-segment"){
    const points=object.points??[];context.lineJoin="round";context.lineCap="round";
    context.globalAlpha*=Math.max(0,Math.min(1,Number(object.metadata?.opacity??(object.pipe?.role==="joining-pipe"?1:.72))));
    traceDrawingRoute(context,points,object.routeRadius);
    if(selected){context.strokeStyle="#1179ac";context.lineWidth=object.width+2;context.stroke();}
    context.strokeStyle=object.color;context.lineWidth=object.width;context.stroke();
    if(object.metadata?.volumeShading === "true") drawVolumeStroke(context,object.width);

    context.restore();return;
  }
  if(view==="drawing"&&object.kind==="wire"&&object.paths){
    context.lineJoin="round";context.lineCap="round";const lineWidth=Number(object.metadata?.drawingWidth??2);context.lineWidth=selected?lineWidth+1:lineWidth;
    for(const stroke of object.visibleWireStrokes??object.paths.map(points=>({points,width:lineWidth,radius:undefined}))){traceDrawingRoute(context,stroke.points,stroke.radius??object.routeRadius);strokeE4Wire(context,object.color,selected?stroke.width+1:stroke.width);if(object.metadata?.volumeShading === "true")drawVolumeStroke(context,stroke.width);}
    drawWireStripProfiles(context, object, selected);
    drawWireDrawingEndStyles(context, object, selected, componentTemplateImageCache);
    context.restore();return;
  }
  if (object.kind === "physical-node") {
    const x=object.x+5,y=object.y+5;
    // A movable exit point, without an uneditable decorative fitting.
    context.fillStyle=selected?"#00a0b7":object.color;
    context.strokeStyle=object.metadata?.nodeRole === "connector-exit" ? "#8a4b00" : "#17485d";
    context.lineWidth=1.5;
    context.beginPath();context.arc(x,y,5,0,Math.PI*2);context.fill();
    context.stroke();
    context.restore(); return;
  }
  if (object.kind === "connector" && componentTemplateViewInstance) {
    if (view === "e4") {
      const layout=getE4ConnectorLayout(object);
      for(const companion of projectE4DrawingCompanions(componentTemplateViewInstance,object,layout?.width ?? object.width,resolveComponentTemplateAssetUrl).filter(d=>d.visible)) {
        const [from,to]=shortestDrawingLink({minX:object.x,minY:object.y,maxX:object.x+(layout?.width ?? object.width),maxY:object.y+(layout?.height ?? object.height)},companion.bounds);
        context.save();context.strokeStyle="#7b8996";context.lineWidth=1;context.setLineDash([4,4]);context.beginPath();context.moveTo(from.x,from.y);context.lineTo(to.x,to.y);context.stroke();context.restore();
        drawProjectedComponentTemplateView(context,companion,componentTemplateImageCache,selected);
      }
    }
    const projection = projectComponentTemplateView(
      componentTemplateViewInstance, view, { x: object.x, y: object.y }, resolveComponentTemplateAssetUrl,
    );
    if (projection) {
      drawProjectedComponentTemplateView(context, projection, componentTemplateImageCache, selected, minimumStrokePixels);
      drawConnectorContactOverrides(context, object, view, true);
      if (view === "drawing") {
        const contactSide = projectContactSideView(componentTemplateViewInstance, { x: object.x, y: object.y }, projection.bounds, resolveComponentTemplateAssetUrl);
        if (contactSide?.visible) {
          const [from,to] = contactSide.link;
          context.save();context.strokeStyle="#7b8996";context.lineWidth=1;context.setLineDash([4,4]);context.beginPath();context.moveTo(from.x,from.y);context.lineTo(to.x,to.y);context.stroke();context.restore();
          drawProjectedComponentTemplateView(context, contactSide.drawing, componentTemplateImageCache, selected, minimumStrokePixels);
        }
      }
      context.restore();
      return;
    }
  }
  if(object.kind==="dimension"&&object.metadata?.boundDimension==="true"&&object.points&&object.points.length>=4){
    const points=object.points,p=points[1]!,q=points.at(-2)!,line=points.slice(1,-1);
    const scale=Math.max(.25,Math.min(4,Number(object.metadata.dimensionScale??1)||1));
    context.strokeStyle=selected?"#1179ac":object.color;context.fillStyle=context.strokeStyle;context.lineWidth=(selected?2:1)*scale;
    context.setLineDash(object.metadata.auxiliary==="true"?[4,3]:[]);context.beginPath();points.forEach((p,i)=>i?context.lineTo(p.x,p.y):context.moveTo(p.x,p.y));context.stroke();
    for(const [point,next,dot] of [[p,line[1]!,object.metadata.dotStart],[q,line.at(-2)!,object.metadata.dotEnd]] as const){
      context.beginPath();if(dot==="true")context.arc(point.x,point.y,2.5*scale,0,Math.PI*2);
      else {const len=Math.hypot(next.x-point.x,next.y-point.y)||1,dx=(next.x-point.x)/len,dy=(next.y-point.y)/len;context.moveTo(point.x,point.y);context.lineTo(point.x+dx*8*scale-dy*3*scale,point.y+dy*8*scale+dx*3*scale);context.lineTo(point.x+dx*8*scale+dy*3*scale,point.y+dy*8*scale-dx*3*scale);context.closePath();}context.fill();
    }
    const mid=Math.floor((line.length-1)/2),a=line[mid]!,b=line[mid+1]!,angle=Math.atan2(b.y-a.y,b.x-a.x);
    context.translate((a.x+b.x)/2,(a.y+b.y)/2);context.rotate(angle>Math.PI/2||angle< -Math.PI/2?angle+Math.PI:angle);context.font=`600 ${12*scale}px Inter, Arial, sans-serif`;context.textAlign="center";context.fillText(object.label,0,-7*scale);context.restore();return;
  }
  if (object.kind === "specification-item") {
    context.strokeStyle=selected?"#1179ac":object.color;context.fillStyle="#fff";context.lineWidth=selected?3:1.5;
    context.fillRect(object.x,object.y,object.width,object.height);context.strokeRect(object.x,object.y,object.width,object.height);
    context.font="12px Arial";context.fillStyle=object.color;context.fillText(object.label,object.x+6,object.y+24,object.width-12);context.restore();return;
  }
  if (object.kind === "wire" || object.kind === "dimension") {
    const points = view === "e4" && object.kind === "wire" ? getE4WireRoute(object) : object.points ?? [];
    if (points.length >= 2) {
      context.beginPath();
      if(view==="drawing"&&object.kind==="wire")traceDrawingRoute(context,points,object.routeRadius);
      else points.forEach((point, index) => index === 0 ? context.moveTo(point.x, point.y) : context.lineTo(point.x, point.y));
      context.strokeStyle = selected ? "#1179ac" : object.color;
      context.lineWidth = view==="drawing"&&object.kind==="wire"?Number(object.metadata?.drawingWidth??3)+(selected?1:0):selected?4:object.kind==="wire"?3:1.5;
      if (object.kind === "dimension" || object.metadata?.routeMissing === "true") context.setLineDash([7, 5]);
      if (view === "e4" && object.kind === "wire" && !selected) strokeE4Wire(context, object.color);
      else context.stroke();
      context.setLineDash([]);
      if (view === "drawing" && object.kind === "wire") {
        if(object.metadata?.volumeShading === "true") drawVolumeStroke(context,Number(object.metadata?.drawingWidth??3));
        drawWireStripProfiles(context, object, selected);
        drawWireDrawingEndStyles(context, object, selected, componentTemplateImageCache);
      }
      if (selected) {
        context.fillStyle = "#ffffff";
        context.strokeStyle = "#1179ac";
        for (const point of points) {
          context.beginPath();
          context.arc(point.x, point.y, 4.5, 0, Math.PI * 2);
          context.fill();
          context.stroke();
        }
      }
      const rawLabelPosition = Number(object.metadata?.e4LabelPosition ?? "0.5");
      const labelPosition = Number.isFinite(rawLabelPosition) && rawLabelPosition >= 0 && rawLabelPosition <= 1
        ? rawLabelPosition
        : 0.5;
      const labelLayout = view === "e4" && object.kind === "wire" && object.label
        ? getE4WireLabelLayout(points, object.label, labelPosition)
        : null;
      const middle = points[Math.floor(points.length / 2)];
      if (labelLayout) {
        context.fillStyle = "rgba(255, 255, 255, 0.94)";
        roundedRectangle(context, labelLayout.x, labelLayout.y, labelLayout.width, labelLayout.height, 3);
        context.fill();
        context.font = "600 12px Inter, Arial, sans-serif";
        context.fillStyle = "#34566a";
        context.textAlign = "center";
        context.textBaseline = "middle";
        context.fillText(object.label, labelLayout.x + labelLayout.width / 2, labelLayout.y + labelLayout.height / 2);
        context.textAlign = "start";
        context.textBaseline = "alphabetic";
      } else if (view !== "e4" && middle && object.label) {
        context.font = "600 12px Inter, Arial, sans-serif";
        context.fillStyle = "#34566a";
        context.fillText(object.label, middle.x + 8, middle.y - 9);
      }
    }
  } else if (object.kind === "connector") {
    const e4Layout = view === "e4" ? getE4ConnectorLayout(object) : null;
    if (e4Layout) {
      drawE4Connector(context, object, e4Layout, selected);
      drawConnectorContactOverrides(context, object, view, false);
      context.restore();
      return;
    }
    roundedRectangle(context, object.x, object.y, object.width, object.height, 7);
    context.fillStyle = "#ffffff";
    context.fill();
    context.strokeStyle = selected ? "#087bb4" : object.color;
    context.lineWidth = selected ? 3 : 2;
    context.stroke();
    if(view!=="drawing"){
      context.fillStyle = "#17384b";
      context.font = "700 13px Inter, Arial, sans-serif";
      context.fillText(object.label, object.x + 12, object.y + 22);
    }
    context.fillStyle = object.color;
    const materializedPoints = getMaterializedConnectorContactPoints(object);
    const points = legacyConnectorContactPoints(object);
    for (let index = 0; index < points.length; index += 1) {
      if (view==="drawing"&&materializedPoints || materializedPoints?.[index]) continue;
      const point = points[index]!;
      context.beginPath();
      context.arc(point.x, point.y, 3.5, 0, Math.PI * 2);
      context.fill();
      context.beginPath();
      context.arc(object.x, point.y, 3.5, 0, Math.PI * 2);
      context.fill();
      context.fillStyle = "#55717f";
      context.font = "500 9px Inter, Arial, sans-serif";
      context.fillText(String(index + 1), object.x + 9, point.y + 3);
      context.fillStyle = object.color;
    }
    if (materializedPoints) drawConnectorContactOverrides(context, object, view, false);
  } else {
    context.fillStyle = object.color;
    context.font = "600 14px Inter, Arial, sans-serif";
    context.fillText(object.label, object.x, object.y + 16);
    if (selected) {
      context.strokeStyle = "#087bb4";
      context.setLineDash([5, 4]);
      context.strokeRect(object.x - 6, object.y - 5, object.width + 12, object.height + 10);
    }
  }
  context.restore();
}

const E4_WHITE_WIRE_OUTLINE = "#53636c";

function isWhiteWireColor(color: string): boolean {
  const normalized = color.trim().toLowerCase().replace(/\s+/g, "");
  return normalized === "white" || normalized === "#fff" || normalized === "#ffffff" ||
    normalized === "#ffffffff" || normalized === "rgb(255,255,255)" || normalized === "rgba(255,255,255,1)";
}

/** Keeps a white E4 conductor visible on the pale canvas without changing its actual colour. */
function strokeE4Wire(context: CanvasRenderingContext2D, color: string, width = 3): void {
  if (isWhiteWireColor(color)) {
    context.strokeStyle = E4_WHITE_WIRE_OUTLINE;
    context.lineWidth = width + 2;
    context.stroke();
  }
  context.strokeStyle = color;
  context.lineWidth = width;
  context.stroke();
}

function drawE4BridgeCrossings(
  context: CanvasRenderingContext2D,
  crossings: readonly E4WireCrossing[],
  objects: readonly EditorSceneObject[],
) {
  const radius = E4_BRIDGE_RADIUS;
  for (const crossing of crossings) {
    const over = objects.find((object) => object.id === crossing.overWireId);
    const under = objects.find((object) => object.id === crossing.underWireId);
    if (!over || !under) continue;
    const geometry = getE4BridgeGeometry(crossing, radius);
    context.save();
    context.lineJoin = "round";
    // Remove the straight portion which the bridge replaces, then restore the
    // perpendicular conductor through the cleared centre. This keeps its
    // colour continuous instead of leaving the white break created by the
    // former eraser-only implementation.
    context.strokeStyle = "#f8fafb";
    context.lineWidth = 7;
    context.lineCap = "butt";
    context.beginPath();
    context.moveTo(geometry.clearStart.x, geometry.clearStart.y);
    context.lineTo(geometry.clearEnd.x, geometry.clearEnd.y);
    context.stroke();
    context.lineCap = "butt";
    context.beginPath();
    if(crossing.underDirection){
      const u=crossing.underDirection;context.moveTo(crossing.point.x-u.x*5,crossing.point.y-u.y*5);context.lineTo(crossing.point.x+u.x*5,crossing.point.y+u.y*5);
    } else if (crossing.overOrientation === "horizontal") {
      context.moveTo(crossing.point.x, crossing.point.y - 5);
      context.lineTo(crossing.point.x, crossing.point.y + 5);
    } else {
      context.moveTo(crossing.point.x - 5, crossing.point.y);
      context.lineTo(crossing.point.x + 5, crossing.point.y);
    }
    strokeE4Wire(context, under.color);
    // A halo which follows the raised path establishes layer order. Drawing
    // the same path in colour afterwards keeps the bridge and both feet whole.
    context.strokeStyle = "#f8fafb";
    context.lineWidth = 7;
    context.beginPath();
    context.moveTo(geometry.coloredStart.x, geometry.coloredStart.y);
    context.lineTo(geometry.arcStart.x, geometry.arcStart.y);
    if(crossing.overDirection){
      const u=crossing.overDirection,n={x:u.y,y:-u.x},p=crossing.point;
      context.bezierCurveTo(p.x-u.x*radius/2+n.x*radius,p.y-u.y*radius/2+n.y*radius,p.x+u.x*radius/2+n.x*radius,p.y+u.y*radius/2+n.y*radius,geometry.arcEnd.x,geometry.arcEnd.y);    } else if (crossing.overOrientation === "horizontal") {
      context.bezierCurveTo(
        crossing.point.x - radius / 2, crossing.point.y - radius,
        crossing.point.x + radius / 2, crossing.point.y - radius,
        crossing.point.x + radius, crossing.point.y,
      );
    } else {
      context.bezierCurveTo(
        crossing.point.x + radius, crossing.point.y - radius / 2,
        crossing.point.x + radius, crossing.point.y + radius / 2,
        crossing.point.x, crossing.point.y + radius,
      );
    }
    context.lineTo(geometry.coloredEnd.x, geometry.coloredEnd.y);
    context.stroke();
    context.lineCap = "round";
    strokeE4Wire(context, over.color);
    context.restore();
  }
}

export function drawE4DifferentialPairs(
  context: CanvasRenderingContext2D,
  groups: readonly E4DifferentialPairOverlay[],
  objects: readonly EditorSceneObject[],
) {
  for (const group of groups) {
    const layout = getE4DifferentialPairLayout(group, objects);
    if (!layout) continue;
    const span = layout.span;
    const first = objects.find((object) => object.id === group.wireIds[0]);
    const second = objects.find((object) => object.id === group.wireIds[1]);
    if (!first || !second) continue;
    for (const [motifIndex, motif] of layout.motifs.entries()) {
      const { from, to, coloredFrom, coloredTo, center: along } = motif;
      const crossingLength = to - from;
      context.save();
      if(span.direction){const u=span.direction;context.transform(u.x,u.y,-u.y,u.x,0,0);}
      context.lineJoin = "round";
      context.lineCap = "butt";
      context.strokeStyle = "#f8fafb";
      context.lineWidth = 7;
      for (const crossValue of [layout.crossMinimum, layout.crossMaximum]) {
        context.beginPath();
        if (span.orientation === "horizontal") {
          context.moveTo(from, crossValue);
          context.lineTo(to, crossValue);
        } else {
          context.moveTo(crossValue, from);
          context.lineTo(crossValue, to);
        }
        context.stroke();
      }
      context.lineCap = "round";
      const traceMotif = (reverse: boolean, extendBefore: boolean, extendAfter: boolean) => {
        const crossStart = reverse ? layout.crossMaximum : layout.crossMinimum;
        const crossEnd = reverse ? layout.crossMinimum : layout.crossMaximum;
        context.beginPath();
        if (span.orientation === "horizontal") {
          context.moveTo(extendBefore ? coloredFrom : from, crossStart);
          context.lineTo(from, crossStart);
          if (layout.variant === 2) context.lineTo(to, crossEnd);
          else context.bezierCurveTo(along - crossingLength / 4, crossStart, along + crossingLength / 4, crossEnd, to, crossEnd);
          context.lineTo(extendAfter ? coloredTo : to, crossEnd);
        } else {
          context.moveTo(crossStart, extendBefore ? coloredFrom : from);
          context.lineTo(crossStart, from);
          if (layout.variant === 2) context.lineTo(crossEnd, to);
          else context.bezierCurveTo(crossStart, along - crossingLength / 4, crossEnd, along + crossingLength / 4, crossEnd, to);
          context.lineTo(crossEnd, extendAfter ? coloredTo : to);
        }
      };
      // Each X represents two wires swapping lanes. Before the first motif the
      // colours belong to their original lanes; after it they belong to the
      // opposite lanes, and every following motif swaps them back. Repaint the
      // straight spans between motifs with the colour of the physical wire
      // which currently occupies that lane, avoiding a colour splice at the
      // feet of an X.
      const firstSegment=span.segmentByWireId[first.id]!;
      const firstOnMinimum=Math.abs(parallelSpanLocal(span,firstSegment.start).y-layout.crossMinimum)<1e-6;
      const firstStartsOnMinimum = (motifIndex % 2 === 0)===firstOnMinimum;
      traceMotif(!firstStartsOnMinimum, true, true);
      // Both conductors need a halo against the scene painted below. Without
      // this first stroke, a third wire running between the pair's lanes looks
      // electrically joined to the first diagonal at their intersection.
      context.strokeStyle = "#f8fafb";
      context.lineWidth = 7;
      context.lineCap = "butt";
      context.stroke();
      traceMotif(!firstStartsOnMinimum, true, true);
      context.lineCap = "round";
      strokeE4Wire(context, first.color);
      traceMotif(firstStartsOnMinimum, true, true);
      context.strokeStyle = "#f8fafb";
      context.lineWidth = 7;
      context.lineCap = "butt";
      context.stroke();
      context.lineCap = "round";
      strokeE4Wire(context, second.color);
      const nextMotif = layout.motifs[motifIndex + 1];
      const straightEnd = nextMotif?.from ?? span.end;
      if (straightEnd > to) {
        const drawLane = (cross: number, color: string) => {
          context.beginPath();
          if (span.orientation === "horizontal") {
            context.moveTo(to, cross);
            context.lineTo(straightEnd, cross);
          } else {
            context.moveTo(cross, to);
            context.lineTo(cross, straightEnd);
          }
          context.lineCap = "butt";
          strokeE4Wire(context, color);
        };
        const afterSwap = firstStartsOnMinimum;
        drawLane(layout.crossMinimum, afterSwap ? second.color : first.color);
        drawLane(layout.crossMaximum, afterSwap ? first.color : second.color);
      }
      context.restore();
    }
  }
}

function drawE4Junctions(context: CanvasRenderingContext2D, junctions: readonly E4JunctionOverlay[]) {
  context.save();
  context.fillStyle = "#183b4d";
  for (const junction of junctions) {
    context.beginPath();
    context.arc(junction.position.x, junction.position.y, 5, 0, Math.PI * 2);
    context.fill();
  }
  context.restore();
}

function drawE4Screens(
  context: CanvasRenderingContext2D,
  screens: readonly E4ScreenOverlay[],
  objects: readonly EditorSceneObject[],
) {
  for (const screen of screens) {
    const layout = getE4ScreenLayout(screen, objects);
    if (!layout) continue;
    const width = layout.orientation === "horizontal" ? layout.alongSize : layout.crossSize;
    const height = layout.orientation === "horizontal" ? layout.crossSize : layout.alongSize;
    context.beginPath();
    context.ellipse(layout.center.x, layout.center.y, width / 2, height / 2, 0, 0, Math.PI * 2);
    context.fillStyle = "rgba(226, 232, 236, 0.5)";
    context.fill();
    context.strokeStyle = "#506d7c";
    context.lineWidth = 1.5;
    context.stroke();
    for (const terminal of layout.terminals) {
      context.beginPath();
      context.arc(terminal.connectionPoint.x, terminal.connectionPoint.y, 3.5, 0, Math.PI * 2);
      context.fillStyle = "#183b4d";
      context.fill();
    }
  }
}

function isE4OverlayVisible(
  wireIds: readonly string[],
  objects: readonly EditorSceneObject[],
  layers: readonly EditorLayer[],
): boolean {
  if (wireIds.length === 0) return false;
  const layerMap = new Map(layers.map((layer) => [layer.id, layer]));
  return wireIds.every((wireId) => {
    const wire = objects.find((object) => object.id === wireId && object.kind === "wire");
    return wire !== undefined && layerMap.get(wire.layerId)?.visible === true;
  });
}

export function getVisibleE4SceneOverlays(
  overlays: E4SceneOverlays,
  objects: readonly EditorSceneObject[],
  layers: readonly EditorLayer[],
): E4SceneOverlays {
  return {
    crossingStyle: overlays.crossingStyle,
    junctions: overlays.junctions.filter((junction) =>
      isE4OverlayVisible(junction.wireIds, objects, layers)),
    diffPairs: overlays.diffPairs.filter((group) =>
      isE4OverlayVisible(group.wireIds, objects, layers)),
    screens: overlays.screens.filter((screen) =>
      isE4OverlayVisible(screen.wireIds, objects, layers)),
  };
}

function expandSceneBounds(
  bounds: EditorSceneBounds | null,
  minX: number,
  minY: number,
  maxX: number,
  maxY: number,
): EditorSceneBounds {
  if (!bounds) return { minX, minY, maxX, maxY };
  return {
    minX: Math.min(bounds.minX, minX),
    minY: Math.min(bounds.minY, minY),
    maxX: Math.max(bounds.maxX, maxX),
    maxY: Math.max(bounds.maxY, maxY),
  };
}

/** The index stays outside the projected library drawing, including rotations. */
export function drawingConnectorIndexLabel(object:EditorSceneObject,bounds:EditorSceneBounds) {
  const text=object.label.trim(),x=bounds.minX+5,y=bounds.minY-8;
  return {text,x,y,minX:x,minY:y-15,maxX:x+Math.max(28,text.length*8),maxY:y+3};
}

/** Returns world-space bounds for everything painted in the current view. */
export function getEditorSceneBounds(
  objects: readonly EditorSceneObject[],
  layers: readonly EditorLayer[],
  view: HarnessEditorView,
  e4Overlays?: E4SceneOverlays,
  componentTemplateViewInstances: readonly ComponentTemplateViewInstance[] = [],
  resolveComponentTemplateAssetUrl?: ResolveComponentTemplateAssetUrl,
  cables: readonly CableInstance[] = [],
): EditorSceneBounds | null {
  let bounds: EditorSceneBounds | null = null;
  const visibleObjects = objectsInPaintOrder(objects, layers);
  const componentViews = new Map(componentTemplateViewInstances.map(instance => [instance.objectId, instance]));
  for (const object of visibleObjects) {
    if (object.kind === "wire" || object.kind === "dimension" || object.kind === "physical-segment" || object.kind === "physical-covering") {
      const points = view === "e4" && object.kind === "wire" ? getE4WireRoute(object) : object.points ?? [];
      for (const point of points) {
        bounds = expandSceneBounds(bounds, point.x, point.y, point.x, point.y);
      }
      for (const geometry of getDrawingWireStripProfileGeometries(object, view)) {
        for (const primitive of geometry.primitives) {
          for (const point of primitive.polygon) {
            bounds = expandSceneBounds(
              bounds,
              point.x - stripProfileStrokeWidth / 2,
              point.y - stripProfileStrokeWidth / 2,
              point.x + stripProfileStrokeWidth / 2,
              point.y + stripProfileStrokeWidth / 2,
            );
          }
        }
      }
      if (view === "e4" && object.kind === "wire" && object.label) {
        const rawPosition = Number(object.metadata?.e4LabelPosition ?? "0.5");
        const position = Number.isFinite(rawPosition) && rawPosition >= 0 && rawPosition <= 1 ? rawPosition : 0.5;
        const label = getE4WireLabelLayout(points, object.label, position);
        if (label) bounds = expandSceneBounds(bounds, label.x, label.y, label.x + label.width, label.y + label.height);
      }
      continue;
    }
    const instance = object.kind === "connector" ? componentViews.get(object.id) : undefined;
    if (instance && view === "e4") {
      const companion = projectE4DrawingCompanion(instance, object, getE4ConnectorLayout(object)?.width ?? object.width, resolveComponentTemplateAssetUrl);
      if (companion) bounds = expandSceneBounds(bounds, companion.bounds.minX, companion.bounds.minY, companion.bounds.maxX, companion.bounds.maxY);
    }
    const projection = instance
      ? projectComponentTemplateView(instance, view, { x: object.x, y: object.y }, resolveComponentTemplateAssetUrl)
      : null;
    if (instance && view === "drawing") {
      const contactSide = projectContactSideView(instance,{x:object.x,y:object.y},projection?.bounds??{
        minX:object.x,minY:object.y,maxX:object.x+object.width,maxY:object.y+object.height,
      },resolveComponentTemplateAssetUrl);
      if (contactSide?.visible) bounds = expandSceneBounds(bounds,contactSide.drawing.bounds.minX,contactSide.drawing.bounds.minY,contactSide.drawing.bounds.maxX,contactSide.drawing.bounds.maxY);
    }
    if(view==="drawing"&&object.kind==="connector"&&object.label.trim()&&!objects.some(candidate=>candidate.kind==="object-index"&&candidate.metadata?.indexObjectId===object.id)){
      const label=drawingConnectorIndexLabel(object,projection?.bounds??{minX:object.x,minY:object.y,maxX:object.x+object.width,maxY:object.y+object.height});
      bounds=expandSceneBounds(bounds,label.minX,label.minY,label.maxX,label.maxY);
    }
    if (projection) {
      bounds = expandSceneBounds(
        bounds,
        projection.bounds.minX,
        projection.bounds.minY,
        projection.bounds.maxX,
        projection.bounds.maxY,
      );
      for (const point of connectorCanvasContactPoints(object, view)) {
        if (!point) continue;
        const cross = point.status === "not-connected" ? contactCrossCenter(point) : point;
        const markerRadius = point.status === "not-connected" ? point.crossSize : 4;
        bounds = expandSceneBounds(
          bounds,
          Math.min(point.x, cross.x) - markerRadius,
          Math.min(point.y, cross.y) - markerRadius,
          Math.max(point.x, cross.x) + markerRadius,
          Math.max(point.y, cross.y) + markerRadius,
        );
        if (point.secondaryPoint) bounds = expandSceneBounds(
          bounds,
          point.secondaryPoint.x - 4,
          point.secondaryPoint.y - 4,
          point.secondaryPoint.x + 4,
          point.secondaryPoint.y + 4,
        );
      }
      continue;
    }
    const e4Layout = view === "e4" ? getE4ConnectorLayout(object) : null;
    const width = e4Layout?.width ?? object.width;
    const height = e4Layout?.height ?? object.height;
    let minX = object.x;
    let maxX = object.x + Math.max(1, width);
    if (e4Layout) {
      const hasDisconnectedContact = e4Layout.rows.some((row) => row.status === "not-connected");
      if (hasDisconnectedContact) {
        if (e4Layout.connectionSide === "left") minX -= 21;
        else maxX += 21;
      }
    }
    bounds = expandSceneBounds(bounds, minX, object.y, maxX, object.y + Math.max(1, height));
    for (const point of connectorCanvasContactPoints(object, view)) {
      if (!point) continue;
      const cross = point.status === "not-connected" ? contactCrossCenter(point) : point;
      const markerRadius = point.status === "not-connected" ? point.crossSize : 4;
      bounds = expandSceneBounds(
        bounds,
        Math.min(point.x, cross.x) - markerRadius,
        Math.min(point.y, cross.y) - markerRadius,
        Math.max(point.x, cross.x) + markerRadius,
        Math.max(point.y, cross.y) + markerRadius,
      );
      if (point.secondaryPoint) bounds = expandSceneBounds(
        bounds,
        point.secondaryPoint.x - 4,
        point.secondaryPoint.y - 4,
        point.secondaryPoint.x + 4,
        point.secondaryPoint.y + 4,
      );
    }
  }

  if (view !== "e4") {
    for (const geometry of getVisibleCableSheathScene(cables, visibleObjects, layers).geometries) {
      bounds = expandSceneBounds(
        bounds,
        geometry.bounds.x,
        geometry.bounds.y,
        geometry.bounds.x + geometry.bounds.width,
        geometry.bounds.y + geometry.bounds.height,
      );
    }
    return bounds;
  }
  const visibleOverlays = getVisibleE4SceneOverlays(
    e4Overlays ?? parseE4SceneOverlays(objects),
    objects,
    layers,
  );
  for (const junction of visibleOverlays.junctions) {
    bounds = expandSceneBounds(
      bounds,
      junction.position.x - 5,
      junction.position.y - 5,
      junction.position.x + 5,
      junction.position.y + 5,
    );
  }
  for (const group of visibleOverlays.diffPairs) {
    const layout = getE4DifferentialPairLayout(group, visibleObjects);
    if (!layout) continue;
    for(const along of [layout.span.start,layout.span.end])for(const cross of [layout.crossMinimum,layout.crossMaximum]){
      const p=parallelSpanWorld(layout.span,along,cross);bounds=expandSceneBounds(bounds,p.x,p.y,p.x,p.y);
    }
  }
  for (const screen of visibleOverlays.screens) {
    const layout = getE4ScreenLayout(screen, visibleObjects);
    if (!layout) continue;
    const width = layout.orientation === "horizontal" ? layout.alongSize : layout.crossSize;
    const height = layout.orientation === "horizontal" ? layout.crossSize : layout.alongSize;
    bounds = expandSceneBounds(
      bounds,
      layout.center.x - width / 2,
      layout.center.y - height / 2,
      layout.center.x + width / 2,
      layout.center.y + height / 2,
    );
    for (const terminal of layout.terminals) bounds = expandSceneBounds(
      bounds,
      terminal.connectionPoint.x - 4,
      terminal.connectionPoint.y - 4,
      terminal.connectionPoint.x + 4,
      terminal.connectionPoint.y + 4,
    );
  }
  return bounds;
}

export function redrawCanvas(
  canvas: HTMLCanvasElement,
  view: HarnessEditorView,
  camera: EditorCamera,
  objects: readonly EditorSceneObject[],
  layers: readonly EditorLayer[],
  selectedObjectIds: ReadonlySet<string>,
  cables: readonly CableInstance[] = [],
  e4Overlays?: E4SceneOverlays,
  alignmentGuides?: E4ConnectorSnapGuides,
  componentTemplateViewInstances: readonly ComponentTemplateViewInstance[] = [],
  resolveComponentTemplateAssetUrl?: ResolveComponentTemplateAssetUrl,
  componentTemplateImageCache = new ComponentTemplateImageCache(),
  highlightedObjectIds: readonly string[] = [],
  physicalNodePreview?: { readonly from: EditorPoint; readonly to: EditorPoint } | null,
  foregroundWireIds:readonly string[] = [],
  backgroundObjects:readonly EditorSceneObject[] = [],
  backgroundOpacity = 0,
) {
  const context = canvas.getContext("2d");
  if (!context) return;
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.round(canvas.clientWidth));
  const height = Math.max(1, Math.round(canvas.clientHeight));
  const pixelWidth = Math.round(width * ratio);
  const pixelHeight = Math.round(height * ratio);
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  drawGrid(context, width, height, camera);
  context.save();
  context.translate(camera.offsetX, camera.offsetY);
  context.scale(camera.zoom, camera.zoom);
  const componentViews = new Map(componentTemplateViewInstances.map(instance => [instance.objectId, instance]));
  if (backgroundOpacity > 0 && backgroundObjects.length > 0) {
    context.save();
    context.globalAlpha = Math.max(0, Math.min(1, backgroundOpacity));
    for (const object of objectsInPaintOrder(backgroundObjects, layers, view)) {
      drawEditorSceneObject(context, object, false, view,
        object.kind === "connector" ? componentViews.get(object.id) : undefined,
        resolveComponentTemplateAssetUrl, componentTemplateImageCache,
        view === "drawing" ? ratio * .9 : 0);
    }
    context.restore();
  }
  if (view === "drawing") {
    drawCableSheaths(
      context,
      getVisibleCableSheathScene(cables, objects, layers).geometries,
      selectedObjectIds,
      camera.zoom,
      new Set(objects.filter(object => object.kind === "wire" && object.metadata?.volumeShading === "true").map(object => object.id)),
    );
  }
  const highlighted = new Set(highlightedObjectIds);
  for (const object of objectsInPaintOrder(objects, layers, view)) {
    if (highlighted.has(object.id) && object.kind === "wire") {
      const points = view === "e4" ? getE4WireRoute(object) : object.points ?? [];
      context.save(); context.strokeStyle = "#f2af28"; context.globalAlpha = .65;
      context.lineWidth = 9 / Math.max(.5, camera.zoom); context.lineJoin = "round"; context.beginPath();
      for(const path of view==="drawing"?object.paths??[points]:[points]) {if(view==="drawing")traceDrawingRoute(context,path,object.routeRadius);else {context.beginPath();path.forEach((point,i)=>i?context.lineTo(point.x,point.y):context.moveTo(point.x,point.y));}context.stroke();} context.restore();
    }
    if(highlighted.has(object.id)&&object.kind!=="wire") {
      context.save();context.strokeStyle="#f2af28";context.globalAlpha=.7;context.lineWidth=6/Math.max(.5,camera.zoom);
      if(object.kind==="physical-covering"||object.kind==="physical-segment") {for(const path of object.paths??[object.points??[]]){traceDrawingRoute(context,path,object.routeRadius);context.stroke();}}
      else {
        const instance=componentViews.get(object.id);
        const projection=instance&&view==="drawing"?projectComponentTemplateView(instance,view,object,resolveComponentTemplateAssetUrl):null;
        const bounds=projection?.bounds;
        if(bounds)context.strokeRect(bounds.minX-4,bounds.minY-4,bounds.maxX-bounds.minX+8,bounds.maxY-bounds.minY+8);
        else context.strokeRect(object.x-4,object.y-4,object.width+8,object.height+8);
      }
      context.restore();
    }
    drawEditorSceneObject(
      context,
      object,
      selectedObjectIds.has(object.id),
      view,
      object.kind === "connector" ? componentViews.get(object.id) : undefined,
      resolveComponentTemplateAssetUrl,
      componentTemplateImageCache,
      view === "drawing" ? ratio * .9 : 0,
    );
  }
  if (physicalNodePreview) {
    context.save();
    context.strokeStyle = "#1179ac";
    context.globalAlpha = 0.65;
    context.lineWidth = 2 / Math.max(0.5, camera.zoom);
    context.setLineDash([7 / Math.max(0.5, camera.zoom), 5 / Math.max(0.5, camera.zoom)]);
    context.beginPath();
    context.moveTo(physicalNodePreview.from.x, physicalNodePreview.from.y);
    context.lineTo(physicalNodePreview.to.x, physicalNodePreview.to.y);
    context.stroke();
    context.restore();
  }
  if (view === "drawing") {
    for (const object of objectsInPaintOrder(objects,layers)) {
      if (object.kind !== "physical-node" && object.kind !== "physical-segment") continue;
      const points: EditorPoint[] = object.kind === "physical-node" ? [{x:object.x+5,y:object.y+5}] : [object.points![0]!,...pipeSceneHandles(object),object.points!.at(-1)!];
      const bendHandles=object.kind==="physical-segment"?numberedPipeBendHandles(object):[];
      if(object.kind==="physical-segment")for(const {point:p} of pipeMidpoints(object)){
        context.save();context.globalAlpha=.35;context.fillStyle="#1179ac";context.beginPath();context.arc(p.x,p.y,4/camera.zoom,0,Math.PI*2);context.fill();context.restore();
      }
      context.save(); context.lineWidth=2/camera.zoom; context.strokeStyle="#006f99";
      points.forEach((p,i)=>{
        const controlled=object.pipe?.controlledHandles?.includes(i-1)===true;
        // OP boundary stations shape the projected route, but are not bends.
        if(object.kind==="physical-segment"&&controlled) return;
        const transition=object.kind==="physical-segment"&&object.pipe?.joiningTransitionHandles?.some(handle=>handle.index===i-1)===true;
        context.beginPath();context.arc(p.x,p.y,(object.kind==="physical-node"?6:transition?5.5:controlled?4:5)/camera.zoom,0,Math.PI*2);
        context.fillStyle=object.kind==="physical-node"?object.color:"#fff";context.fill();context.stroke();
        const bendNumber=bendHandles.indexOf(i-1)+1;
        if(bendNumber>0){context.font=`${10/camera.zoom}px Arial`;context.fillStyle="#17485d";context.fillText(String(bendNumber),p.x+8/camera.zoom,p.y-8/camera.zoom);}
      });
      if(object.kind==="physical-node"){const vector = object.port?.direction;if(vector){const length=12/camera.zoom,c={x:points[0]!.x+vector.x*length,y:points[0]!.y+vector.y*length};context.beginPath();context.moveTo(points[0]!.x,points[0]!.y);context.lineTo(c.x,c.y);context.stroke();context.beginPath();context.moveTo(c.x,c.y);context.lineTo(c.x-vector.x*4/camera.zoom-vector.y*3/camera.zoom,c.y-vector.y*4/camera.zoom+vector.x*3/camera.zoom);context.moveTo(c.x,c.y);context.lineTo(c.x-vector.x*4/camera.zoom+vector.y*3/camera.zoom,c.y-vector.y*4/camera.zoom-vector.x*3/camera.zoom);context.stroke();}}
      context.restore();
    }
  }
  if (view === "e4") {
    const overlays = e4Overlays ?? parseE4SceneOverlays(objects);
    const visibleOverlays = getVisibleE4SceneOverlays(overlays, objects, layers);
    if (overlays.crossingStyle === "bridge") {
      const pairKeys = new Set(visibleOverlays.diffPairs.map(pair => [...pair.wireIds].sort().join("\u0000")));
      const crossings = getE4WireCrossings(objects, layers, visibleOverlays.junctions).filter(crossing =>
        !pairKeys.has([crossing.overWireId, crossing.underWireId].sort().join("\u0000")));
      drawE4BridgeCrossings(context, crossings, objects);
    }
    drawE4DifferentialPairs(context, visibleOverlays.diffPairs, objects);
    drawE4Junctions(context, visibleOverlays.junctions);
    drawE4Screens(context, visibleOverlays.screens, objects);
    drawE4ConnectorAlignmentGuides(context, alignmentGuides, camera.zoom);
    for(const wire of objectsInPaintOrder(objects,layers).filter(o=>o.kind==="wire"&&selectedObjectIds.has(o.id))){
      context.save();context.fillStyle="#1179ac";
      for(const {point:p} of e4Midpoints(wire)){context.globalAlpha=.35;context.beginPath();context.arc(p.x,p.y,4/camera.zoom,0,Math.PI*2);context.fill();}
      context.globalAlpha=1;context.lineWidth=1.5/camera.zoom;context.strokeStyle="#006f99";
      for(const p of (wire.points??[]).slice(1,-1)){context.beginPath();context.arc(p.x,p.y,4/camera.zoom,0,Math.PI*2);context.fillStyle="white";context.fill();context.stroke();}
      context.restore();
    }
  }
  // A selected electrical wire is the complete route, including conductors
  // hidden by the first-hit projection. Draw it after every pipe and sleeve.
  if(view==="drawing")for(const object of objectsInPaintOrder(objects,layers,view)){
    if(object.kind!=="wire"||!foregroundWireIds.includes(object.id))continue;
    context.save();context.lineCap="round";context.lineJoin="round";
    const width=Number(object.metadata?.drawingWidth??2.5);
    for(const path of object.paths??[object.points??[]]){
      if(path.length<2)continue;
      traceDrawingRoute(context,path,object.routeRadius);
      context.strokeStyle="#fff";context.lineWidth=width+6/Math.max(.5,camera.zoom);context.stroke();
      traceDrawingRoute(context,path,object.routeRadius);
      context.strokeStyle="#cf5e00";context.lineWidth=width+3/Math.max(.5,camera.zoom);context.stroke();
      traceDrawingRoute(context,path,object.routeRadius);strokeE4Wire(context,object.color,width);
    }
    context.restore();
  }
  // Contact marks and drawing indexes are a final interaction pass.
  // No wire, sleeve, picture, table or E4 overlay can paint over them.
  for (const object of objectsInPaintOrder(objects, layers, view)) {
    if (object.kind !== "connector") continue;
    drawConnectorContactOverrides(context, object, view, true);
    if (selectedObjectIds.has(object.id)) drawSelectedConnectorContacts(context, object, view, camera.zoom);
  }
  if(view==="drawing"){
    const indexed=new Set(objects.filter(object=>object.kind==="object-index").map(object=>object.metadata?.indexObjectId));
    context.save();context.fillStyle="#17384b";context.font="700 13px Inter, Arial, sans-serif";
    for(const object of objectsInPaintOrder(objects,layers,view)){
      if(object.kind!=="connector"||!object.label.trim()||indexed.has(object.id))continue;
      const instance=componentViews.get(object.id),projection=instance?projectComponentTemplateView(instance,"drawing",{x:object.x,y:object.y},resolveComponentTemplateAssetUrl):null;
      const label=drawingConnectorIndexLabel(object,projection?.bounds??{minX:object.x,minY:object.y,maxX:object.x+object.width,maxY:object.y+object.height});
      context.fillText(label.text,label.x,label.y);
    }
    context.restore();
  }
  context.restore();
}

export function drawCableSheaths(
  context: CanvasRenderingContext2D,
  geometries: readonly CableSheathGeometry[],
  selectedObjectIds: ReadonlySet<string>,
  zoom = 1,
  shadedWireIds: ReadonlySet<string> = new Set(),
): void {
  const safeZoom = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  for (const geometry of geometries) {
    const selected = geometry.memberWireIds.length > 0 &&
      geometry.memberWireIds.every((wireId) => selectedObjectIds.has(wireId));
    context.save();
    context.beginPath();
    geometry.polygon.forEach((point, index) => index === 0
      ? context.moveTo(point.x, point.y)
      : context.lineTo(point.x, point.y));
    context.closePath();
    context.fillStyle = selected ? "rgba(17, 121, 172, 0.13)" : "rgba(82, 105, 119, 0.08)";
    context.fill();
    if (geometry.memberWireIds.some(id => shadedWireIds.has(id))) {
      drawVolumeSurface(context, geometry.polygon, geometry.centerline);
      context.beginPath();
      geometry.polygon.forEach((point, index) => index === 0 ? context.moveTo(point.x, point.y) : context.lineTo(point.x, point.y));
      context.closePath();
    }
    context.strokeStyle = selected ? "#1179ac" : "#607d8b";
    context.lineWidth = (selected ? 3 : 2) / safeZoom;
    context.setLineDash(selected ? [] : [8 / safeZoom, 4 / safeZoom]);
    context.stroke();
    context.restore();
  }
}

function drawE4ConnectorAlignmentGuides(
  context: CanvasRenderingContext2D,
  guides: E4ConnectorSnapGuides | undefined,
  zoom: number,
) {
  if (!guides?.horizontal && !guides?.vertical) return;
  const safeZoom = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  context.save();
  context.strokeStyle = "#e04f9c";
  context.lineWidth = 1 / safeZoom;
  context.setLineDash([4 / safeZoom, 3 / safeZoom]);
  context.beginPath();
  if (guides.vertical) {
    context.moveTo(guides.vertical.x, guides.vertical.fromY);
    context.lineTo(guides.vertical.x, guides.vertical.toY);
  }
  if (guides.horizontal) {
    context.moveTo(guides.horizontal.fromX, guides.horizontal.y);
    context.lineTo(guides.horizontal.toX, guides.horizontal.y);
  }
  context.stroke();
  context.restore();
}

function e4ConnectorSnapTarget(object: EditorSceneObject): E4ConnectorSnapTarget | null {
  const layout = getE4ConnectorLayout(object);
  if (!layout) return null;
  return {
    id: object.id,
    x: layout.x,
    y: layout.y,
    width: layout.width,
    height: layout.height,
    contactSide: layout.connectionSide,
    firstContactY: layout.contactPoints[0]?.y,
    contactRowsY: layout.contactPoints.map(point => point.y),
  };
}

export function CanvasViewport({
  onFreeWireEndpointMove, onFreeWireEndpointsXChange, onFreeWireEndpointPreview, onDetachedPairAction, onFreeWireEndStyleRequest,
  view,
  tool,
  camera,
  objects,
  layers,
  selectedObjectId,
  selectedObjectIds,
  highlightedObjectIds = [],
  foregroundWireIds = [],
  backgroundObjects = [],
  backgroundOpacity = 0,
  cables = [],
  e4Overlays,
  e4RoutingMode = "orthogonal",
  componentTemplateViewInstances = [],
  resolveComponentTemplateAssetUrl,
  overlay,onDimensionCreate,onPositionRailCreate,onGraphicCreate,drawingSnaps={corners:true,contours:true,tangents:true},drawingAngleStep=15,
  diagnosticOverlay,
  inlineEditor,
  onCameraChange,
  onViewportSizeChange,
  onObjectSelect,
  onObjectGroupSelect,
  onObjectsIsolate,
  objectProperties, onObjectPick, onObjectPickCancel, onRelatedObjectsSelect, onObjectMove, onDrawingMove, onDrawingScale,
  onObjectMovePreview, onPipeIntervalSelect, onCoveringDrag,
  onWireConnect,
  onWireReconnect,
  onWireConnectToWire,
  onWireReconnectToWire,
  onE4WireSegmentMove,
  onE4WireRoutePointRemove,
  onE4WireLabelPositionChange,
  onE4ScreenPositionChange,
  onWireToolRequest,
  onWireRoutePointMove, onWireRoutePointPreview,
  onWireRoutePointRemove,
  onObjectEditRequest,
  onCanvasDoubleClick, onPhysicalContextAction, onPhysicalNodesConnect, onPhysicalNodeConnectToSegment,
  onCatalogDrop,
}: CanvasViewportProps) {
  const frameRef = useRef<HTMLDivElement>(null);
  const freeEndDrag = useRef<{wireId:string;end:"from"|"to";pointerId:number;group?:readonly FreeWireEndpointRef[]}|null>(null);
  useEffect(()=>{
    const cancel=(event:KeyboardEvent)=>{if(event.key!=="Escape"||!freeEndDrag.current)return;const d=freeEndDrag.current;freeEndDrag.current=null;for(const ref of d.group??[{wireId:d.wireId,end:d.end}])onFreeWireEndpointPreview?.(ref.wireId,ref.end,null);};
    window.addEventListener("keydown",cancel);
    return ()=>window.removeEventListener("keydown",cancel);
  },[onFreeWireEndpointPreview]);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const componentTemplateImageCacheRef = useRef<ComponentTemplateImageCache | null>(null);
  if (!componentTemplateImageCacheRef.current) {
    componentTemplateImageCacheRef.current = new ComponentTemplateImageCache();
  }
  const dragRef = useRef<CoveringPointerDrag | PointerDrag | ObjectPointerDrag | DrawingPointerDrag | WireRoutePointerDrag |
    E4WireSegmentPointerDrag | E4WireLabelPointerDrag | E4ScreenPointerDrag | PhysicalNodeConnectPointerDrag | null>(null);
  const inlineDragRef = useRef<ObjectPointerDrag | null>(null);
  const inlineCaptureRef = useRef<HTMLDivElement | null>(null);
  const inlineDragActivatedRef = useRef(false);
  const suppressInlineDoubleClickUntilRef = useRef(0);
  const [inlineDragOffset, setInlineDragOffset] = useState<EditorPoint | null>(null);
  const [drawingPreview,setDrawingPreview]=useState<{objectId:string;drawingId:string;offset:EditorPoint}|null>(null);
  const [scalePreview,setScalePreview]=useState<{objectId:string;drawingId:string;scale:number}|null>(null);
  const displayInstances=componentTemplateViewInstances.map(instance=>{
    const change=drawingPreview?.objectId===instance.objectId?drawingPreview:scalePreview?.objectId===instance.objectId?scalePreview:null;
    if(!change)return instance;
    const previous=instance.drawingPlacements?.find(p=>p.drawingId===change.drawingId)??{drawingId:change.drawingId,visible:true,offset:{x:0,y:0}};
    return {...instance,drawingPlacements:[...(instance.drawingPlacements??[]).filter(p=>p.drawingId!==change.drawingId),{...previous,...("scale" in change?{scale:change.scale}:{offset:change.offset})}]};
  });
  const [dimensionStart,setDimensionStart]=useState<{wireId:string;index:number;point:EditorPoint;pointCount:number}[]>([]);
  const [railDraft,setRailDraft]=useState<{start:EditorPoint;end:EditorPoint;leaderIds:readonly string[]}|null>(null);
  const [dimensionMessage,setDimensionMessage]=useState("");
  const [graphicStart,setGraphicStart]=useState<EditorPoint|null>(null);
  const [graphicPathDraft,setGraphicPathDraft]=useState<{kind:"line"|"polyline"|"closedContour";points:EditorPoint[]}|null>(null);
  const [graphicPreview,setGraphicPreview]=useState<DrawingGraphic|null>(null);
  const [selectionRectangle,setSelectionRectangle]=useState<EditorSelectionRectangle|null>(null);
  const [selectionCandidateIds,setSelectionCandidateIds]=useState<readonly string[]>([]);
  const selectionDragRef=useRef<{pointerId:number;start:EditorPoint;clientX:number;clientY:number;mode:EditorSelectionMode}|null>(null);
  useEffect(()=>{setDimensionStart([]);setDimensionMessage("");},[tool,view]);
  useEffect(()=>{setRailDraft(null);},[tool,view]);
  useEffect(()=>{setGraphicStart(null);setGraphicPathDraft(null);setGraphicPreview(null);setSelectionRectangle(null);setSelectionCandidateIds([]);selectionDragRef.current=null;},[tool,view]);
  const [wireStart, setWireStart] = useState<E4ConnectableEndpoint | null>(null);
  const [wireReconnect, setWireReconnect] = useState<{ readonly wireId: string; readonly end: "from" | "to" } | null>(null);
  const [wireLabelPreview, setWireLabelPreview] = useState<{ readonly wireId: string; readonly position: number } | null>(null);
  const [screenPositionPreview, setScreenPositionPreview] = useState<{ readonly screenId: string; readonly position: number } | null>(null);
  const [connectorAlignmentGuides, setConnectorAlignmentGuides] = useState<E4ConnectorSnapGuides>({});
  const [physicalGuide,setPhysicalGuide]=useState<readonly EditorPoint[]|undefined>();
  const displayObjects = wireLabelPreview
    ? objects.map((object) => object.id === wireLabelPreview.wireId ? {
      ...object,
      metadata: { ...object.metadata, e4LabelPosition: String(wireLabelPreview.position) },
    } : object)
    : objects;
  const activeSelectedIds = selectedObjectIds ?? (selectedObjectId ? [selectedObjectId] : []);
  const selectedSet = new Set(activeSelectedIds);
  const paintObjects=graphicPreview?[...displayObjects,{...graphicPreview,id:"graphic-preview",kind:(`graphic-${graphicPreview.kind.replace("closedContour","closed-contour")}`) as EditorSceneObject["kind"],layerId:view==="drawing"?"dimensions":"connectors",label:graphicPreview.text??graphicPreview.kind,x:Math.min(...graphicPreview.points.map(point=>point.x)),y:Math.min(...graphicPreview.points.map(point=>point.y)),width:Math.max(1,Math.abs((graphicPreview.points.at(-1)?.x??0)-(graphicPreview.points[0]?.x??0))),height:Math.max(1,Math.abs((graphicPreview.points.at(-1)?.y??0)-(graphicPreview.points[0]?.y??0))),color:graphicPreview.color??"#253b4a",metadata:{graphicKind:graphicPreview.kind,graphicView:graphicPreview.view,graphicText:graphicPreview.text??"",graphicAngle:String(graphicPreview.angle??0),graphicFontFamily:graphicPreview.fontFamily??"Arial",graphicFontSize:String(graphicPreview.fontSize??16),graphicWidth:String(graphicPreview.width??2)}}]:displayObjects;
  const paintSelectedSet=graphicPreview?new Set([...selectedSet,"graphic-preview"]):selectedSet;
  const effectiveHighlights=[...highlightedObjectIds,...selectionCandidateIds];
  const freeEndpointRefs = freeWireEndpointRefs(objects, selectedSet, layers);
  const freeEndAxes = freeWireEndAxes(freeEndpointRefs);
  const snapRefs = freeWireEndpointRefs(objects, new Set(objects.map(object => object.id)), layers);
  const pairActionIds=(id:string):readonly string[]=>{
    const selected=objects.filter(o=>o.kind==="wire"&&selectedSet.has(o.id));
    if(selected.length===2)return selected.map(o=>o.id);
    try{const ids:unknown=JSON.parse(objects.find(o=>o.id===id)?.metadata?.drawingPairWireIds??"null");return Array.isArray(ids)&&ids.length===2&&ids.every(id=>typeof id==="string"&&objects.some(o=>o.id===id&&o.kind==="wire"))?ids:[];}catch{return [];}
  };
  const sourceOverlays = useMemo(
    () => e4Overlays ?? parseE4SceneOverlays(objects),
    [e4Overlays, objects],
  );
  const overlays = useMemo(() => screenPositionPreview ? {
    ...sourceOverlays,
    screens: sourceOverlays.screens.map((screen) => screen.id === screenPositionPreview.screenId
      ? { ...screen, position: screenPositionPreview.position }
      : screen),
  } : sourceOverlays, [screenPositionPreview, sourceOverlays]);
  const cableSheathScene = useMemo(
    () => view === "drawing"
      ? getVisibleCableSheathScene(cables, displayObjects, layers)
      : { geometries: [], incompatibleCableIds: [] },
    [cables, displayObjects, layers, view],
  );
  const selectLinkedE4Group = (wireIds: readonly string[]) => {
    if (onObjectGroupSelect) onObjectGroupSelect(wireIds);
    else onObjectSelect(wireIds.at(-1) ?? null, false);
  };
  const selectedObject = objects.find(object => object.id === selectedObjectId);
  const selectedComponentTemplateInstance = componentTemplateViewInstances.find(instance => instance.objectId === selectedObjectId);
  const selectedHasComponentTemplateView = !!selectedObject && !!selectedComponentTemplateInstance &&
    projectComponentTemplateView(
      selectedComponentTemplateInstance,
      view,
      { x: selectedObject.x, y: selectedObject.y },
      resolveComponentTemplateAssetUrl,
    ) !== null;
  const inlineObject = view === "e4" && tool === "select" && selectedObjectId && !selectedHasComponentTemplateView
    ? objects.find((object) => object.id === selectedObjectId && object.kind === "connector") ?? null
    : null;
  const inlineLayout = inlineObject ? getE4ConnectorLayout(inlineObject) : null;
  const [physicalStart,setPhysicalStart]=useState<string|null>(null);
  const [physicalNodePreview,setPhysicalNodePreview]=useState<{from:EditorPoint;to:EditorPoint}|null>(null);

  useEffect(() => {
    if (tool !== "wire") {
      setWireStart(null);
      setWireReconnect(null);
    }
  }, [tool]);

  useEffect(() => {
    const drag = inlineDragRef.current;
    inlineDragRef.current = null;
    if (drag) {
      onObjectMovePreview?.(drag.objectId, null);
      const target=inlineCaptureRef.current;
      if(target?.hasPointerCapture(drag.pointerId))target.releasePointerCapture(drag.pointerId);
    }
    inlineCaptureRef.current = null;
    inlineDragActivatedRef.current = false;
    setInlineDragOffset(null);
    setConnectorAlignmentGuides({});
  }, [inlineObject?.id, tool, view]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const redraw = () => {
      redrawCanvas(
        canvas,
        view,
        camera,
        paintObjects,
        layers,
        paintSelectedSet,
        cables,
        overlays,
        connectorAlignmentGuides,
        displayInstances,
        resolveComponentTemplateAssetUrl,
        componentTemplateImageCacheRef.current!,
        effectiveHighlights,
        physicalNodePreview,
        foregroundWireIds,
        backgroundObjects,
        backgroundOpacity,
      );
      onViewportSizeChange?.({
        width: Math.max(1, Math.round(canvas.clientWidth)),
        height: Math.max(1, Math.round(canvas.clientHeight)),
      });
    };
    const stopTextures=warmCoveringTextures(redraw,[...paintObjects,...backgroundObjects]);
    componentTemplateImageCacheRef.current!.setInvalidate(redraw);
    redraw();
    const observer = new ResizeObserver(redraw);
    observer.observe(canvas);
    return () => {
      stopTextures();
      observer.disconnect();
      componentTemplateImageCacheRef.current?.setInvalidate(null);
    };
  }, [effectiveHighlights, foregroundWireIds, backgroundObjects, backgroundOpacity, cables, camera, displayInstances, connectorAlignmentGuides, paintObjects, inlineObject?.id, layers, onViewportSizeChange, overlays, resolveComponentTemplateAssetUrl, selectedObjectIds, selectedObjectId, view, physicalNodePreview]);

  useEffect(() => {
    const frame = frameRef.current;
    const canvas = canvasRef.current;
    if (!frame || !canvas) return;
    const wheel = (event: globalThis.WheelEvent) => {
      handleEditorViewportWheel(event, camera, canvas.getBoundingClientRect(), onCameraChange);
    };
    frame.addEventListener("wheel", wheel, { passive: false, capture: true });
    return () => frame.removeEventListener("wheel", wheel, true);
  }, [camera, onCameraChange]);

  useEffect(() => {
    const cancelConnection = () => {
      const drag = dragRef.current;
      if (drag) {
        if(drag.kind==="wire-route")onWireRoutePointPreview?.(drag.wireId,drag.routeIndex,null);
        if(drag.kind==="object")onObjectMovePreview?.(drag.objectId,null);
        if(drag.kind==="covering")onCoveringDrag?.(drag.objectId,drag.spanIndex,drag.part,drag.start,drag.start,"cancel");
        dragRef.current = null;
        const canvas = canvasRef.current;
        if (canvas?.hasPointerCapture(drag.pointerId)) canvas.releasePointerCapture(drag.pointerId);
      }
      const inline = inlineDragRef.current;
      inlineDragRef.current = null;
      if(inline){
        onObjectMovePreview?.(inline.objectId,null);
        const target=inlineCaptureRef.current;
        if(target?.hasPointerCapture(inline.pointerId))target.releasePointerCapture(inline.pointerId);
      }
      inlineCaptureRef.current=null;
      inlineDragActivatedRef.current=false;
      setInlineDragOffset(null);
      setConnectorAlignmentGuides({});
      setDrawingPreview(null);
      setWireLabelPreview(null);
      setScreenPositionPreview(null);
      setPhysicalStart(null);
      setPhysicalNodePreview(null);
      setPhysicalGuide(undefined);
      setGraphicStart(null);
      setGraphicPathDraft(null);
      setGraphicPreview(null);
      setSelectionRectangle(null);
      setSelectionCandidateIds([]);
      selectionDragRef.current=null;
    };
    cancelConnection();
    const keydown = (event: KeyboardEvent) => { if (event.key === "Escape") cancelConnection(); };
    window.addEventListener("keydown", keydown);
    return () => { window.removeEventListener("keydown", keydown); cancelConnection(); };
  }, [tool, view]);
  const [physicalMenu,setPhysicalMenu]=useState<{id:string;point:EditorPoint;x:number;y:number; node?:boolean; wires?:boolean; isolationIds?:readonly string[]}|null>(null);
  useEffect(()=>{if(onObjectPick)setPhysicalMenu(null);},[!!onObjectPick]);
  const [hoverTarget,setHoverTarget]=useState<CanvasHintTarget|null>(null);
  useEffect(()=>{setHoverTarget(null);setPhysicalMenu(null);},[view,tool,camera]);
  useEffect(()=>{
    if(hoverTarget&&!objects.some(o=>o.id===hoverTarget.id))setHoverTarget(null);
    if(physicalMenu&&!objects.some(o=>o.id===physicalMenu.id))setPhysicalMenu(null);
  },[objects,hoverTarget,physicalMenu]);
  const localPoint = (clientX: number, clientY: number): EditorPoint => {
    const bounds = canvasRef.current?.getBoundingClientRect();
    return { x: clientX - (bounds?.left ?? 0), y: clientY - (bounds?.top ?? 0) };
  };

  const snappedObjectDestination = (objectId: string, destination: EditorPoint, snapEnabled = false): EditorPoint => {
    if (view !== "e4") {
      setConnectorAlignmentGuides({});
      const drag=dragRef.current;
      const result=drag?.kind==='object'&&drag.routeAnchors?.length
        ?snapBendPoint(destination,drag.routeAnchors,snapEnabled,7/camera.zoom,{x:drag.objectX,y:drag.objectY},Math.PI/6,drag.snapState)
        :snapPhysicalPoint(destination,drag?.kind==="object"?drag.anchors??[]:[],snapEnabled,7/camera.zoom,Math.PI/6);
      setPhysicalGuide(result.guide);
      return result.point;
    }
    const movingObject = objects.find((object) => object.id === objectId);
    const moving = movingObject ? e4ConnectorSnapTarget(movingObject) : null;
    if (!moving) {
      setConnectorAlignmentGuides({});
      return destination;
    }
    const result = snapE4ConnectorPosition(
      moving,
      objects.flatMap((object) => {
        if (layers.find((layer) => layer.id === object.layerId)?.visible === false) return [];
        const target = e4ConnectorSnapTarget(object);
        return target ? [target] : [];
      }),
      destination,
      camera.zoom,
    );
    setConnectorAlignmentGuides(result.guides);
    return result.position;
  };

  const snapGraphicPoint = (rawPoint:EditorPoint,anchor?:EditorPoint,angleSnap=true):EditorPoint => {
    const targets:DrawingOutline[]=objects.filter(o=>o.points?.length).map(o=>({id:o.id,points:[...(o.points??[])],closed:["closedContour","ellipse","rectangle"].includes(o.metadata?.graphicKind??""),corners:o.points as {x:number;y:number}[]}));
    const featurePoint=snapDrawingPoint(rawPoint,targets,drawingSnaps,8/camera.zoom,anchor);
    if(!anchor||!angleSnap||drawingAngleStep<=0)return featurePoint;
    const dx=featurePoint.x-anchor.x,dy=featurePoint.y-anchor.y,r=Math.hypot(dx,dy),step=drawingAngleStep*Math.PI/180,a=Math.round(Math.atan2(dy,dx)/step)*step;
    return {x:anchor.x+r*Math.cos(a),y:anchor.y+r*Math.sin(a)};
  };

  const finishGraphicPath=()=>{
    const draft=graphicPathDraft;
    if(!draft)return;
    if(draft.points.length>=2&&onGraphicCreate){
      const kind=draft.kind==="line"&&draft.points.length>2?"polyline":draft.kind;
      onGraphicCreate({id:crypto.randomUUID(),view,kind,points:draft.points,color:"#253b4a",width:2});
    }
    setGraphicPathDraft(null);setGraphicStart(null);setGraphicPreview(null);
  };

  const pointerDown = (event: PointerEvent<HTMLCanvasElement>) => {
    if(event.button===0&&view==="drawing"&&tool==="select"&&(onFreeWireEndpointMove||onFreeWireEndpointsXChange)&&!onObjectPick){
      const p=screenToWorld(camera,localPoint(event.clientX,event.clientY));
      const axis = freeEndAxes.find(axis => Math.abs(axis.x - p.x) <= 8 / camera.zoom &&
        Math.abs((axis.minY + axis.maxY) / 2 - p.y) <= 9 / camera.zoom);
      if (axis && onFreeWireEndpointsXChange) {
        const hit = freeEndpointRefs.find(ref => ref.wireId === axis.wireIds[0] && ref.end === axis.end)!;
        const axisRefs = freeEndpointRefs.filter(ref => axis.wireIds.includes(ref.wireId) && ref.end === axis.end);
        event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId);
        freeEndDrag.current = { wireId: hit.wireId, end: hit.end, pointerId: event.pointerId, group: axisRefs };
        return;
      }
      const hit=objects.filter(o=>o.kind==="wire"&&layers.some(l=>l.id===o.layerId&&l.visible&&!l.locked)).flatMap(o=>(["from","to"] as const).flatMap(end=>{
        const point=end==="from"?o.points?.[0]:o.points?.at(-1);
        return o.metadata?.[end==="from"?"freeFrom":"freeTo"]==="true"&&point&&Math.hypot(point.x-p.x,point.y-p.y)<=7/camera.zoom?[{wireId:o.id,end,pointerId:event.pointerId}]:[];
      }))[0];
      if(hit){event.preventDefault();event.currentTarget.setPointerCapture(event.pointerId);freeEndDrag.current=hit;onObjectSelect(hit.wireId,false);return;}
    }
    setHoverTarget(null);
    if(event.button===2){
      return;
    }
    setPhysicalMenu(null);
    if(event.button===0&&onObjectPick){
      const point=screenToWorld(camera,localPoint(event.clientX,event.clientY));
      const selectable=objects.filter(o=>layers.some(l=>l.id===o.layerId&&!l.locked));
      onObjectPick(hitTestEditorScene(selectable,layers,point,camera.zoom,view,componentTemplateViewInstances,resolveComponentTemplateAssetUrl));
      return;
    }
    if(event.button===0&&tool==="select"&&!onObjectPick){
      const point=screenToWorld(camera,localPoint(event.clientX,event.clientY));
      const hit=hitTestEditorScene(objects,layers,point,camera.zoom,view,componentTemplateViewInstances,resolveComponentTemplateAssetUrl);
      if(!hit){
        event.currentTarget.setPointerCapture(event.pointerId);
        selectionDragRef.current={pointerId:event.pointerId,start:point,clientX:event.clientX,clientY:event.clientY,mode:event.shiftKey?"add":event.ctrlKey||event.metaKey?"toggle":"replace"};
        setSelectionRectangle({start:point,end:point});setSelectionCandidateIds([]);return;
      }
    }
    if(event.button===0&&view==="drawing"&&tool==="position-rail"){
      const point=screenToWorld(camera,localPoint(event.clientX,event.clientY));
      if(!railDraft){setRailDraft({start:point,end:point,leaderIds:[]});return;}
      const end=snapRailEnd(railDraft.start,point);
      if(Math.hypot(end.x-railDraft.start.x,end.y-railDraft.start.y)>=24){
        const ids=[...new Set([...railDraft.leaderIds,...objects.filter(o=>o.kind==="position-leader"&&railParameter({start:railDraft.start,end, id:"preview", leaderIds:[]},{x:o.x+o.width/2,y:o.y+o.height/2})>=0&&railParameter({start:railDraft.start,end,id:"preview",leaderIds:[]},{x:o.x+o.width/2,y:o.y+o.height/2})<=1&&railDistance({start:railDraft.start,end},{x:o.x+o.width/2,y:o.y+o.height/2})<=railCatchDistance/camera.zoom).map(o=>o.id)])];
        onPositionRailCreate?.(railDraft.start,end,ids);
      }
      setRailDraft(null);return;
    }
    if(event.button===0&&tool.startsWith("graphic-")&&onGraphicCreate){
      const rawPoint=screenToWorld(camera,localPoint(event.clientX,event.clientY)),kind=tool.slice(8).replace("closed-contour","closedContour") as DrawingGraphicKind,anchor=graphicPathDraft?.points.at(-1)??graphicStart??undefined,point=snapGraphicPoint(rawPoint,anchor,kind!=="rectangle"&&kind!=="ellipse");
      if(kind==="contact"||kind==="text"){const text=kind==="text"?window.prompt("Текст фигуры","Текст"):undefined;if(kind==="text"&&!text?.trim())return;onGraphicCreate({id:crypto.randomUUID(),view,kind,points:[point],...(kind==="text"?{text:text!.trim(),angle:0,fontFamily:"Arial",fontSize:16}:{}),color:"#253b4a",width:2});return;}
      if(kind==="line"||kind==="polyline"||kind==="closedContour"){
        const previous=graphicPathDraft?.points.at(-1),same=previous&&Math.hypot(previous.x-point.x,previous.y-point.y)<.01;
        if(graphicPathDraft&&same&&graphicPathDraft.points.length>=2){finishGraphicPath();return;}
        const first=graphicPathDraft?.points[0],closes=first&&graphicPathDraft!.points.length>=2&&Math.hypot(first.x-point.x,first.y-point.y)<=8/camera.zoom;
        if(closes){onGraphicCreate({id:crypto.randomUUID(),view,kind:"closedContour",points:[...graphicPathDraft!.points,first],color:"#253b4a",width:2});setGraphicPathDraft(null);setGraphicStart(null);setGraphicPreview(null);return;}
        const next=graphicPathDraft?{...graphicPathDraft,points:same?graphicPathDraft.points:[...graphicPathDraft.points,point]}:{kind,points:[point]};
        setGraphicPathDraft(next);setGraphicStart(next.points[0]??null);setGraphicPreview({id:"preview",view,kind:next.kind,points:[next.points[0]!,next.points[0]!],color:"#253b4a",width:2});return;
      }
      if(!graphicStart){setGraphicStart(point);setGraphicPreview({id:"preview",view,kind,points:[point,point],color:"#253b4a",width:2});return;}
      const dx=point.x-graphicStart.x,dy=point.y-graphicStart.y;
      if(kind==="rectangle"||kind==="ellipse"){
        const radius=event.ctrlKey?Math.hypot(dx,dy):undefined,points=kind==="ellipse"?[{x:graphicStart.x-(radius??Math.abs(dx)),y:graphicStart.y-(radius??Math.abs(dy))},{x:graphicStart.x+(radius??Math.abs(dx)),y:graphicStart.y+(radius??Math.abs(dy))}]:[graphicStart,point];
        onGraphicCreate({id:crypto.randomUUID(),view,kind,points,color:"#253b4a",width:2});setGraphicStart(null);setGraphicPreview(null);return;
      }
      const kindPoints=kind==="bezier"?[graphicStart,{x:graphicStart.x+dx/3,y:graphicStart.y+dy},{x:graphicStart.x+2*dx/3,y:graphicStart.y-dy},point]:[graphicStart,point];
      onGraphicCreate({id:crypto.randomUUID(),view,kind,points:kindPoints,color:"#253b4a",width:2});setGraphicStart(null);setGraphicPreview(null);return;
    }
    if(event.button===0&&view==="drawing"&&tool==="wire"&&onPhysicalNodesConnect){
      const point=screenToWorld(camera,localPoint(event.clientX,event.clientY));
      const node=objects.find(o=>o.kind==="physical-node"&&!o.metadata?.joiningPipe&&layers.some(l=>l.id===o.layerId&&l.visible&&!l.locked)&&containsPoint(o,point,8/camera.zoom,view));
      if(node){
        event.currentTarget.setPointerCapture(event.pointerId);
        dragRef.current={kind:"physical-node-connect",pointerId:event.pointerId,fromNodeId:node.id,clientX:event.clientX,clientY:event.clientY,moved:false};
        setPhysicalNodePreview({from:{x:node.x+5,y:node.y+5},to:{x:node.x+5,y:node.y+5}});
        return;
      }
    }
    if(event.button===0&&event.shiftKey&&view==="drawing"&&tool==="select") {
      const local=localPoint(event.clientX,event.clientY),point=screenToWorld(camera,local);
      const id=hitTestEditorScene(objects,layers,point,camera.zoom,view);
      const pipe=objects.find(o=>o.id===id&&o.kind==="physical-segment");
      const grip=pipe&&(hitTestWireRoutePoint(pipe,point,camera.zoom)!==null||pipeMidpoints(pipe).some(h=>Math.hypot(h.point.x-point.x,h.point.y-point.y)<=7/camera.zoom));
      if(pipe&&!grip) {onObjectSelect(id,false);setPhysicalMenu({id:id!,point,x:event.clientX,y:event.clientY,wires:true});return;}
    }
    if(event.button===0&&view==="drawing"&&tool==="select"&&!event.ctrlKey&&!event.shiftKey&&onCoveringDrag){
      const point=screenToWorld(camera,localPoint(event.clientX,event.clientY)),grip=gripAt(point);
      const pipeGrip=objects.some(o=>o.metadata?.joiningPipe&&o.kind==="physical-node"&&layers.some(l=>l.id===o.layerId&&l.visible&&!l.locked)&&containsPoint(o,point,7/camera.zoom,view))||objects.some(o=>o.kind==="physical-segment"&&layers.some(l=>l.id===o.layerId&&l.visible&&!l.locked)&&(hitTestWireRoutePoint(o,point,camera.zoom)!==null||pipeMidpoints(o).some(h=>Math.hypot(h.point.x-point.x,h.point.y-point.y)<=7/camera.zoom)));
      const cover=[...objects].reverse().find(o=>o.kind==="physical-covering"&&layers.some(l=>l.id===o.layerId&&l.visible&&!l.locked)&&coveringHit(o,point,4/camera.zoom)!==null);
      if((grip?.part.startsWith("transition-")||!pipeGrip)&&(grip||cover)){const objectId=grip?.objectId??cover!.id,spanIndex=grip?.spanIndex??coveringHit(cover!,point,4/camera.zoom)!;
        setHoverGrip(null);
        onObjectSelect(objectId,false);event.currentTarget.setPointerCapture(event.pointerId);dragRef.current={kind:"covering",pointerId:event.pointerId,clientX:event.clientX,clientY:event.clientY,objectId,spanIndex,part:grip?.part??"body",start:point};return;}
    }
    const shouldPan = tool === "pan" || event.button === 1;
    if (shouldPan) {
      event.currentTarget.setPointerCapture(event.pointerId);
      dragRef.current = {
        kind: "pan",
        pointerId: event.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
        camera,
      };
      return;
    }
    if(view==="drawing"&&tool.startsWith("dimension")&&event.button===0){
      const point=screenToWorld(camera,localPoint(event.clientX,event.clientY));
      const candidates=objects.filter(o=>(o.kind==="physical-segment"||o.kind==="wire"&&o.metadata?.physicalRoute!=="true")&&layers.some(l=>l.id===o.layerId&&l.visible&&!l.locked)).flatMap(w=>(w.kind==="physical-segment"?pipeSceneEditablePoints(w):w.points??[]).map((p,index)=>({wireId:w.id,index,point:p,pointCount:w.kind==="physical-segment"?pipeSceneEditablePoints(w).length:w.points!.length}))).filter(c=>(c.index===0||c.index===c.pointCount-1)&&Math.hypot(c.point.x-point.x,c.point.y-point.y)*camera.zoom<=12).sort((a,b)=>Number(b.wireId===selectedObjectId)-Number(a.wireId===selectedObjectId));
      if(!candidates.length){setDimensionMessage("Укажите конец участка");return;}
      if(!dimensionStart.length){setDimensionStart(candidates);setDimensionMessage("Укажите вторую точку того же пайпа или провода");return;}
      const end=candidates.find(c=>dimensionStart.some(a=>a.wireId===c.wireId&&a.index!==c.index));
      const start=end&&dimensionStart.find(a=>a.wireId===end.wireId&&a.index!==end.index);
      if(!end||!start){setDimensionMessage("Нужны две разные точки одного пайпа или свободного провода");return;}
      onDimensionCreate?.(end.wireId,start.index,end.index,end.pointCount,tool==="dimension-horizontal"?"horizontal":tool==="dimension-vertical"?"vertical":"aligned",tool==="dimension-auxiliary");
      setDimensionStart([]);return;
    }
    if (tool === "wire") {
      const worldPoint = screenToWorld(camera, localPoint(event.clientX, event.clientY));
      const endpoint = (view === "e4"
        ? hitTestE4ScreenConnection(overlays.screens, objects, worldPoint, camera.zoom, layers)
        : null) ?? hitTestConnectorContact(
        objects,
        layers,
        worldPoint,
        camera.zoom,
        view,
      );
      if (wireReconnect) {
        if (endpoint) {
          onWireReconnect?.(wireReconnect.wireId, wireReconnect.end, endpoint);
        } else {
          const target = hitTestWireSegment(objects, layers, worldPoint, camera.zoom, wireReconnect.wireId);
          if (target) onWireReconnectToWire?.(
            wireReconnect.wireId,
            wireReconnect.end,
            target.wireId,
            target.point,
          );
        }
        setWireReconnect(null);
        return;
      }
      if (!wireStart) {
        const selectedWire = objects.find((item) => item.id === selectedObjectId);
        const selectedLayer = selectedWire ? layers.find((item) => item.id === selectedWire.layerId) : null;
        const wireEnd = selectedLayer?.locked === true ? null : hitTestWireEnd(selectedWire, worldPoint, camera.zoom);
        if (wireEnd && selectedWire) {
          setWireReconnect({ wireId: selectedWire.id, end: wireEnd });
          return;
        }
      }
      if (!endpoint) {
        if (wireStart) {
          const target = hitTestWireSegment(objects, layers, worldPoint, camera.zoom);
          if (target && onWireConnectToWire) {
            onWireConnectToWire(wireStart, target.wireId, target.point);
            setWireStart(null);
          }
        }
        return;
      }
      if (!wireStart) {
        setWireStart(endpoint);
        if ("screenId" in endpoint) {
          const screen = overlays.screens.find((item) => item.id === endpoint.screenId);
          if (screen) selectLinkedE4Group(screen.wireIds);
        } else {
          onObjectSelect(endpoint.connectorId, false);
        }
      } else {
        const sameEndpoint = "screenId" in wireStart
          ? "screenId" in endpoint && wireStart.screenId === endpoint.screenId &&
            (wireStart.screenTerminalSide ?? "above") === (endpoint.screenTerminalSide ?? "above")
          : "connectorId" in endpoint && wireStart.connectorId === endpoint.connectorId &&
            wireStart.contactIndex === endpoint.contactIndex;
        if (!sameEndpoint) {
          onWireConnect?.(wireStart, endpoint);
        }
        setWireStart(null);
      }
      return;
    }
    if (tool === "select") {
      const worldPoint = screenToWorld(camera, localPoint(event.clientX, event.clientY));
      if((view==="e4" || view==="drawing") && onDrawingMove && event.button===0) {
        for(const object of [...objects].reverse()) {
          const layer=layers.find(l=>l.id===object.layerId),instance=componentTemplateViewInstances.find(i=>i.objectId===object.id);
          if(object.kind!=="connector" || !instance || !layer?.visible || layer.locked) continue;
          if(view==="e4") {
            const drawing=projectE4DrawingCompanions(instance,object,getE4ConnectorLayout(object)?.width ?? object.width,resolveComponentTemplateAssetUrl).reverse().find(d=>d.visible && worldPoint.x>=d.bounds.minX && worldPoint.x<=d.bounds.maxX && worldPoint.y>=d.bounds.minY && worldPoint.y<=d.bounds.maxY);
            if(drawing) {event.currentTarget.setPointerCapture(event.pointerId);onObjectSelect(object.id,false);dragRef.current={kind:"companion",pointerId:event.pointerId,clientX:event.clientX,clientY:event.clientY,objectId:object.id,drawingId:drawing.drawingId,offset:drawing.offset};return;}
          } else {
            const connectorDrawing=projectComponentTemplateView(instance,"drawing",object,resolveComponentTemplateAssetUrl);
            const contactSide=projectContactSideView(instance,{x:object.x,y:object.y},connectorDrawing?.bounds??{minX:object.x,minY:object.y,maxX:object.x+object.width,maxY:object.y+object.height},resolveComponentTemplateAssetUrl);
            const placement=instance.drawingPlacements?.find(item=>item.drawingId===CONTACT_SIDE_VIEW_PLACEMENT_ID);
            if(contactSide?.visible && placement && worldPoint.x>=contactSide.drawing.bounds.minX && worldPoint.x<=contactSide.drawing.bounds.maxX && worldPoint.y>=contactSide.drawing.bounds.minY && worldPoint.y<=contactSide.drawing.bounds.maxY) {
              event.currentTarget.setPointerCapture(event.pointerId);onObjectSelect(object.id,false);dragRef.current={kind:"companion",pointerId:event.pointerId,clientX:event.clientX,clientY:event.clientY,objectId:object.id,drawingId:CONTACT_SIDE_VIEW_PLACEMENT_ID,offset:placement.offset};return;
            }
          }
        }
      }
      if (view === "e4") {
        const screen = hitTestE4Screen(overlays.screens, objects, worldPoint, camera.zoom, layers);
        const endpoint = hitTestE4ScreenConnection(overlays.screens, objects, worldPoint, camera.zoom, layers) ??
          (screen ? null : hitTestConnectorContact(objects, layers, worldPoint, camera.zoom, view));
        if (endpoint && onWireToolRequest) {
          setWireStart(endpoint);
          if ("screenId" in endpoint) {
            const screen = overlays.screens.find((item) => item.id === endpoint.screenId);
            if (screen) selectLinkedE4Group(screen.wireIds);
          } else {
            onObjectSelect(endpoint.connectorId, false);
          }
          onWireToolRequest();
          return;
        }
        if (screen) {
          selectLinkedE4Group(screen.wireIds);
          if (onE4ScreenPositionChange) {
            event.currentTarget.setPointerCapture(event.pointerId);
            dragRef.current = {
              kind: "e4-screen",
              pointerId: event.pointerId,
              clientX: event.clientX,
              clientY: event.clientY,
              screenId: screen.id,
              orientation: screen.span.orientation,
              position: overlays.screens.find((item) => item.id === screen.id)?.position ?? 0.5,
              spanLength: screen.pathLength,
            };
          }
          return;
        }
        const differentialPair = hitTestE4DifferentialPair(
          overlays.diffPairs,
          objects,
          worldPoint,
          camera.zoom,
          layers,
        );
        if (differentialPair) {
          selectLinkedE4Group(differentialPair.wireIds);
          return;
        }
        const wireLabel = onE4WireLabelPositionChange
          ? hitTestE4WireLabel(objects, layers, worldPoint, camera.zoom)
          : null;
        const selectedWire=objects.find(o=>o.id===selectedObjectId&&o.kind==="wire"&&layers.some(l=>l.id===o.layerId&&l.visible&&!l.locked));
        if(selectedWire&&onWireRoutePointMove){
          const corner=hitTestWireRoutePoint(selectedWire,worldPoint,camera.zoom);
          const middle=corner===null?e4Midpoints(selectedWire).find(h=>Math.hypot(h.point.x-worldPoint.x,h.point.y-worldPoint.y)<=7/camera.zoom):undefined;
          const index=corner??middle?.index;
          if(index!==undefined&&index!==null){
            const points=selectedWire.points!,point=middle?.point??points[index+1]!;
            event.currentTarget.setPointerCapture(event.pointerId);
            dragRef.current={kind:"wire-route",pointerId:event.pointerId,clientX:event.clientX,clientY:event.clientY,wireId:selectedWire.id,routeIndex:index,point,
              mode:event.shiftKey?"adjacent":"carry",insert:!!middle,anchors:bendSnapAnchors(points,index,!!middle,event.shiftKey?"adjacent":"carry"),snapState:{}};
            return;
          }
        }
        if (wireLabel) {
          const wire = objects.find((item) => item.id === wireLabel.wireId);
          const layer = wire ? layers.find((item) => item.id === wire.layerId) : null;
          onObjectSelect(wireLabel.wireId, false);
          if (layer?.locked !== true) {
            event.currentTarget.setPointerCapture(event.pointerId);
            dragRef.current = { kind: "e4-wire-label", pointerId: event.pointerId, wireId: wireLabel.wireId };
          }
          return;
        }
        const segment = onE4WireSegmentMove
          ? hitTestE4WireSegment(objects, layers, worldPoint, camera.zoom)
          : null;
        if (segment) {
          const additive = event.ctrlKey || event.shiftKey;
          const togglingOff = additive && selectedSet.has(segment.wireId);
          onObjectSelect(segment.wireId, additive);
          if (togglingOff) return;
          // The body selects; authoring now belongs to corner/midpoint handles.
          if(onWireRoutePointMove)return;
          event.currentTarget.setPointerCapture(event.pointerId);
          dragRef.current = {
            kind: "e4-wire-segment",
            pointerId: event.pointerId,
            clientX: event.clientX,
            clientY: event.clientY,
            wireId: segment.wireId,
            segmentIndex: segment.index,
            orientation: segment.orientation,
            coordinate: segment.orientation === "horizontal" ? segment.start.y : segment.start.x,
          };
          return;
        }
      }
      if (view === "drawing") {
        const pipes=objects.filter(o=>o.kind==="physical-segment"&&layers.some(l=>l.id===o.layerId&&l.visible&&!l.locked)).sort((a,b)=>Number(b.pipe?.role==="joining-pipe")-Number(a.pipe?.role==="joining-pipe"));
        const nodeHit=objects.some(o=>o.kind==="physical-node"&&layers.some(l=>l.id===o.layerId&&l.visible)&&containsPoint(o,worldPoint,7/camera.zoom,view));
        const corner=nodeHit?undefined:pipes.map(o=>({o,index:hitTestWireRoutePoint(o,worldPoint,camera.zoom)})).find(h=>h.index!==null);
        const middle=corner||nodeHit?undefined:pipes.flatMap(o=>pipeMidpoints(o).map(h=>({...h,o}))).find(h=>Math.hypot(h.point.x-worldPoint.x,h.point.y-worldPoint.y)<=7/camera.zoom);
        const pipe=corner?.o??middle?.o,index=corner?.index??middle?.index;
        if(pipe&&index!==null&&index!==undefined){
          const points=[pipe.points![0]!,...pipeSceneHandles(pipe),pipe.points!.at(-1)!];
          const point=middle?.point??points[index+1]!;
          onObjectSelect(pipe.id,false);event.currentTarget.setPointerCapture(event.pointerId);
          dragRef.current={kind:"wire-route",pointerId:event.pointerId,clientX:event.clientX,clientY:event.clientY,wireId:pipe.id,routeIndex:index,point,
            mode:event.shiftKey?"adjacent":"carry",insert:!!middle,opMember:!!pipe.pipe?.joiningMember,
            anchors:pipe.pipe?pipeBendSnapAnchors(pipe.pipe,points,index,!!middle,event.shiftKey?"adjacent":"carry",point):bendSnapAnchors(points,index,!!middle,event.shiftKey?"adjacent":"carry",point),snapState:{}};
          return;
        }
        const cableSheath = hitTestCableSheath(cableSheathScene.geometries, worldPoint, camera.zoom);
        if (cableSheath && !objects.some(o=>o.kind==="physical-segment"&&containsPoint(o,worldPoint,7/camera.zoom,view))) {
          selectLinkedE4Group(cableSheath.memberWireIds);
          return;
        }
        const selectedWire = objects.find((item) => item.id === selectedObjectId);
        const selectedLayer = selectedWire ? layers.find((item) => item.id === selectedWire.layerId) : null;
        const cornerIndex = selectedLayer?.locked === true || selectedWire?.kind==="wire"&&objects.some(o=>o.kind==="physical-segment"&&containsPoint(o,worldPoint,7/camera.zoom,view))
          ? null
          : hitTestWireRoutePoint(selectedWire, worldPoint, camera.zoom);
        const detachedMiddle=cornerIndex===null&&selectedWire?.metadata?.detachedDrawing==="true"&&selectedLayer?.visible&& !selectedLayer.locked ? e4Midpoints(selectedWire).find(h=>Math.hypot(h.point.x-worldPoint.x,h.point.y-worldPoint.y)<=7/camera.zoom):undefined;
        const routeIndex=cornerIndex??detachedMiddle?.index??null;
        const routePoint = detachedMiddle?.point ?? (routeIndex === null ? null : (selectedWire?.kind==="physical-segment"?[selectedWire.points![0]!,...pipeSceneHandles(selectedWire)]:selectedWire?.points)?.[routeIndex + 1]);
        if (selectedWire && routeIndex !== null && routePoint) {
          event.currentTarget.setPointerCapture(event.pointerId);
          dragRef.current = {
            kind: "wire-route",
            pointerId: event.pointerId,
            clientX: event.clientX,
            clientY: event.clientY,
            wireId: selectedWire.id,
            routeIndex,
            point: routePoint,
            insert:!!detachedMiddle,
            mode:selectedWire.metadata?.detachedDrawing==="true"?"adjacent":undefined,
            anchors:selectedWire.metadata?.detachedDrawing==="true"?bendSnapAnchors(selectedWire.points??[],routeIndex,!!detachedMiddle,"adjacent"):undefined,
            snapState:{},
          };
          return;
        }
      }
      const objectId = hitTestEditorScene(
        objects,
        layers,
        worldPoint,
        camera.zoom,
        view,
        componentTemplateViewInstances,
        resolveComponentTemplateAssetUrl,
      );
      const table=objects.find(o=>o.id===objectId&&o.kind==="drawing-table");
      if(table && worldPoint.y>=table.y+52 && onRelatedObjectsSelect) {
        const rows=JSON.parse(table.metadata?.rowObjectIds ?? "[]") as string[][];
        const ids=rows[Math.floor((worldPoint.y-table.y-52)/32)];if(ids){onRelatedObjectsSelect(ids);return;}
      }
      const selectedObject = objects.find((item) => item.id === selectedObjectId);
      const preserveWireForRoutePoint = view === "drawing" && objectId === null &&
        (selectedObject?.kind === "wire" || selectedObject?.kind === "physical-segment") && onCanvasDoubleClick !== undefined;
      if (!preserveWireForRoutePoint) {
        const mode:EditorSelectionMode=event.shiftKey?"add":event.ctrlKey||event.metaKey?"toggle":"replace";
        if(onObjectGroupSelect)onObjectGroupSelect(objectId?[objectId]:[],mode);
        else onObjectSelect(objectId,mode==="toggle");
      }
      const object = objects.find((item) => item.id === objectId);
      const layer = object ? layers.find((item) => item.id === object.layerId) : null;
      if(object?.kind==="physical-segment"&&layer?.locked!==true){const controls=pipeSceneControls(object);const index=projectOntoPolyline(controls,worldPoint).index;onPipeIntervalSelect?.(object.id,Math.max(0,index-1),index);}
      if (object && (object.kind.startsWith("graphic-") || object.kind === "object-index" || object.kind === "dimension" || object.kind === "connector" || object.kind === "specification-item" || object.kind === "physical-node" || object.kind === "drawing-table" || object.kind === "position-leader" || object.kind === "leader-anchor" || object.kind === "position-rail" || object.kind === "rail-handle" || object.kind === "physical-segment" && object.pipe?.role === "joining-pipe") && layer?.locked !== true && onObjectMove) {
        event.currentTarget.setPointerCapture(event.pointerId);
        dragRef.current = {
          kind: "object",
          pointerId: event.pointerId,
          clientX: event.clientX,
          clientY: event.clientY,
          objectId: object.id,
          objectX: object.x,
          objectY: object.y,
          mode:event.shiftKey?"adjacent":"carry",
          anchors:view==="drawing"&&object.metadata?.joiningPipe?joiningPipeEndpointSnapAnchors(objects,object):view==="drawing"&&object.metadata?.bundleMember!=="true"&&object.pipe?.role!=="joining-pipe"?physicalObjectSnapAnchors(objects.filter(o=>layers.some(l=>l.id===o.layerId&&l.visible)),object):undefined,
          routeAnchors:view==='drawing'&&!object.metadata?.joiningPipe&&(object.kind==="physical-node"||object.metadata?.bundleMember!=="true")&&object.pipe?.role!=="joining-pipe"?physicalObjectRouteAnchors(objects,object,event.shiftKey?'adjacent':'carry'):undefined,
          snapState:{},
        };
      }
    }
  };

  const [hoverGrip,setHoverGrip]=useState<CoveringHandle|null>(null);
  const gripAt=(point:EditorPoint):CoveringHandle|null=>[...objects].reverse().filter(o=>o.kind==="physical-covering"&&layers.some(l=>l.id===o.layerId&&l.visible&&!l.locked)).flatMap(o=>coveringGrips(o)).find(g=>(g.pointMarker?Math.hypot(point.x-g.point.x,point.y-g.point.y):pointToSegmentDistance(point,{x:g.point.x-g.normal.x*g.halfWidth,y:g.point.y-g.normal.y*g.halfWidth},{x:g.point.x+g.normal.x*g.halfWidth,y:g.point.y+g.normal.y*g.halfWidth}))<=8/camera.zoom)??null;
  const pointerMove = (event: PointerEvent<HTMLCanvasElement>) => {
    if(selectionDragRef.current?.pointerId===event.pointerId){
      const drag=selectionDragRef.current,end=screenToWorld(camera,localPoint(event.clientX,event.clientY));
      const selection={start:drag.start,end};setSelectionRectangle(selection);
      setSelectionCandidateIds(selectionRectangleObjectIds(objects,layers,selection));return;
    }
    if(graphicStart&&tool.startsWith("graphic-")&&["graphic-rectangle","graphic-ellipse","graphic-bezier"].includes(tool)){
      const kind=tool.slice(8).replace("closed-contour","closedContour") as DrawingGraphicKind,point=snapGraphicPoint(screenToWorld(camera,localPoint(event.clientX,event.clientY)),graphicStart,kind!=="rectangle"&&kind!=="ellipse"),dx=point.x-graphicStart.x,dy=point.y-graphicStart.y;
      if(kind==="rectangle"||kind==="ellipse"){
        const radius=kind==="ellipse"&&event.ctrlKey?Math.hypot(dx,dy):undefined,points=kind==="ellipse"?[{x:graphicStart.x-(radius??Math.abs(dx)),y:graphicStart.y-(radius??Math.abs(dy))},{x:graphicStart.x+(radius??Math.abs(dx)),y:graphicStart.y+(radius??Math.abs(dy))}]:[graphicStart,point];
        setGraphicPreview({id:"preview",view,kind,points,color:"#253b4a",width:2});
      }
    } else if(graphicPathDraft&&tool.startsWith("graphic-")&&["line","polyline","closedContour"].includes(graphicPathDraft.kind)){
      const point=snapGraphicPoint(screenToWorld(camera,localPoint(event.clientX,event.clientY)),graphicPathDraft.points.at(-1));
      setGraphicPreview({id:"preview",view,kind:graphicPathDraft.kind,points:[...graphicPathDraft.points,point],color:"#253b4a",width:2});
    }
    if(freeEndDrag.current?.pointerId===event.pointerId){const d=freeEndDrag.current;const raw=screenToWorld(camera,localPoint(event.clientX,event.clientY));if(d.group){for(const ref of d.group)onFreeWireEndpointPreview?.(ref.wireId,ref.end,{x:raw.x,y:ref.point.y});}else{const snapped=snapFreeWireEndpointX(raw,snapRefs,d.wireId,d.end,8/camera.zoom);onFreeWireEndpointPreview?.(d.wireId,d.end,snapped);}return;}
    if(railDraft&&view==="drawing"&&tool==="position-rail"){
      const point=screenToWorld(camera,localPoint(event.clientX,event.clientY)),end=snapRailEnd(railDraft.start,point);
      const ids=objects.filter(o=>o.kind==="position-leader"&&railParameter({start:railDraft.start,end,id:"preview",leaderIds:[]},{x:o.x+o.width/2,y:o.y+o.height/2})>=0&&railParameter({start:railDraft.start,end,id:"preview",leaderIds:[]},{x:o.x+o.width/2,y:o.y+o.height/2})<=1&&railDistance({start:railDraft.start,end},{x:o.x+o.width/2,y:o.y+o.height/2})<=railCatchDistance/camera.zoom).map(o=>o.id);
      setRailDraft(previous=>previous?{...previous,end,leaderIds:ids}:null);
      return;
    }
    const drag = dragRef.current;
    if(!drag&&!physicalMenu&&view==="drawing"&&tool==="select"){
      const point=screenToWorld(camera,localPoint(event.clientX,event.clientY)),id=hitTestEditorScene(objects,layers,point,camera.zoom,view,componentTemplateViewInstances,resolveComponentTemplateAssetUrl);
      const object=objects.find(o=>o.id===id),eligible=object&&["connector","wire","physical-node","physical-segment","physical-covering"].includes(object.kind);
      setHoverTarget(previous=>!eligible?null:previous?.id===id&&Math.hypot(previous.x-event.clientX,previous.y-event.clientY)<4?previous:{id:object.id,label:object.label||"Объект",x:event.clientX,y:event.clientY});
    }else setHoverTarget(null);
    if (!drag) {const hit=view==="drawing"&&tool==="select"?gripAt(screenToWorld(camera,localPoint(event.clientX,event.clientY))):null;setHoverGrip(hit);event.currentTarget.style.cursor=hit?"ew-resize":"";return;}
    if(drag.pointerId !== event.pointerId)return;
    if(drag.kind==="physical-node-connect") {
      if(inlineObjectDragMoved(event.clientX-drag.clientX,event.clientY-drag.clientY)) drag.moved=true;
      const point=screenToWorld(camera,localPoint(event.clientX,event.clientY));
      const source=objects.find(o=>o.id===drag.fromNodeId&&o.kind==="physical-node");
      const target=objects.find(o=>o.kind==="physical-node"&&!o.metadata?.joiningPipe&&o.id!==drag.fromNodeId&&layers.some(l=>l.id===o.layerId&&l.visible&&!l.locked)&&containsPoint(o,point,8/camera.zoom,view));
      const targetSegment=target?undefined:objects.find(o=>o.kind==="physical-segment"&&o.pipe?.role!=="joining-pipe"&&layers.some(l=>l.id===o.layerId&&l.visible&&!l.locked)&&containsPoint(o,point,7/camera.zoom,view));
      const segmentPoint=targetSegment?projectOntoPolyline(targetSegment.points??[],point).point:null;
      setPhysicalNodePreview({from:source?{x:source.x+5,y:source.y+5}:point,to:target?{x:target.x+5,y:target.y+5}:segmentPoint??point});
      return;
    }
    if(drag.kind==="covering"){onCoveringDrag?.(drag.objectId,drag.spanIndex,drag.part,drag.start,screenToWorld(camera,localPoint(event.clientX,event.clientY)),"preview");return;}
    if(drag.kind==="companion") { setDrawingPreview({objectId:drag.objectId,drawingId:drag.drawingId,offset:{x:drag.offset.x+(event.clientX-drag.clientX)/camera.zoom,y:drag.offset.y+(event.clientY-drag.clientY)/camera.zoom}});
    } else if (drag.kind === "pan") {
      onCameraChange(panEditorCamera(drag.camera, event.clientX - drag.clientX, event.clientY - drag.clientY));
    } else if (drag.kind === "object" && inlineObjectDragMoved(event.clientX - drag.clientX, event.clientY - drag.clientY)) {
      const destination = objects.find(o=>o.id===drag.objectId)?.metadata?.joiningPipeExit
        ? inlineObjectDragDestination({ x: drag.objectX, y: drag.objectY }, event.clientX - drag.clientX, event.clientY - drag.clientY, camera.zoom)
        : snappedObjectDestination(drag.objectId, inlineObjectDragDestination(
        { x: drag.objectX, y: drag.objectY },
        event.clientX - drag.clientX,
        event.clientY - drag.clientY,
        camera.zoom,
      ), event.ctrlKey);
      if (onObjectMovePreview) onObjectMovePreview(drag.objectId, destination,drag.mode);
      else setInlineDragOffset({
        x: (destination.x - drag.objectX) * camera.zoom,
        y: (destination.y - drag.objectY) * camera.zoom,
      });
    } else if (drag.kind === "wire-route") {
      if(!inlineObjectDragMoved(event.clientX-drag.clientX,event.clientY-drag.clientY))return;
      const angularSnap = view === "e4" ? e4RoutingMode === "angular" || event.ctrlKey : event.ctrlKey;
      const result=snapBendPoint({x:drag.point.x+(event.clientX-drag.clientX)/camera.zoom,y:drag.point.y+(event.clientY-drag.clientY)/camera.zoom},drag.anchors??[],angularSnap,7/camera.zoom,undefined,Math.PI/6,drag.snapState,drag.opMember?"nearest-compatible":"all");
      setPhysicalGuide(result.guide);
      onWireRoutePointPreview?.(drag.wireId,drag.routeIndex,result.point,drag.mode,drag.insert);
    } else if (drag.kind === "e4-wire-label") {
      const wire = objects.find((item) => item.id === drag.wireId);
      const position = wire
        ? projectPointToE4WireLabelPosition(getE4WireRoute(wire), screenToWorld(camera, localPoint(event.clientX, event.clientY)))
        : null;
      if (position !== null) setWireLabelPreview({ wireId: drag.wireId, position });
    } else if (drag.kind === "e4-screen") {
      const screen = overlays.screens.find((item) => item.id === drag.screenId);
      const layout = screen ? getE4ScreenLayout(screen, objects) : null;
      const worldPoint = screenToWorld(camera, localPoint(event.clientX, event.clientY));
      if (layout) {
        setScreenPositionPreview({
          screenId: drag.screenId,
          position: getE4ScreenPositionForPoint(layout, worldPoint),
        });
      }
    }
  };

  const endPointer = (event: PointerEvent<HTMLCanvasElement>) => {
    if(selectionDragRef.current?.pointerId===event.pointerId){
      const drag=selectionDragRef.current,selection={start:drag.start,end:screenToWorld(camera,localPoint(event.clientX,event.clientY))},ids=selectionRectangleObjectIds(objects,layers,selection);
      if(onObjectGroupSelect)onObjectGroupSelect(ids,drag.mode);
      else if(drag.mode==="replace")onObjectSelect(ids.at(-1)??null,false);else for(const id of ids)onObjectSelect(id,drag.mode==="toggle");
      setSelectionRectangle(null);setSelectionCandidateIds([]);selectionDragRef.current=null;
      if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);return;
    }
    if(freeEndDrag.current?.pointerId===event.pointerId){const d=freeEndDrag.current;freeEndDrag.current=null;const raw=screenToWorld(camera,localPoint(event.clientX,event.clientY));if(d.group&&onFreeWireEndpointsXChange){for(const ref of d.group)onFreeWireEndpointPreview?.(ref.wireId,ref.end,null);onFreeWireEndpointsXChange([...new Set(d.group.map(ref=>ref.wireId))],d.end,raw.x);}else{const snapped=snapFreeWireEndpointX(raw,snapRefs,d.wireId,d.end,8/camera.zoom);onFreeWireEndpointPreview?.(d.wireId,d.end,null);onFreeWireEndpointMove?.(d.wireId,d.end,snapped);}if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);return;}
    if (dragRef.current?.pointerId !== event.pointerId) return;
    if(dragRef.current?.kind==="physical-node-connect") {
      const drag=dragRef.current;
      const point=screenToWorld(camera,localPoint(event.clientX,event.clientY));
      const target=objects.find(o=>o.kind==="physical-node"&&!o.metadata?.joiningPipe&&o.id!==drag.fromNodeId&&layers.some(l=>l.id===o.layerId&&l.visible&&!l.locked)&&containsPoint(o,point,8/camera.zoom,view));
      const targetSegment=target?undefined:objects.find(o=>o.kind==="physical-segment"&&o.pipe?.role!=="joining-pipe"&&layers.some(l=>l.id===o.layerId&&l.visible&&!l.locked)&&containsPoint(o,point,7/camera.zoom,view));
      const moved=drag.moved||inlineObjectDragMoved(event.clientX-drag.clientX,event.clientY-drag.clientY);
      if(moved&&target) {
        onPhysicalNodesConnect?.(drag.fromNodeId,target.id);
        setPhysicalStart(null);
      } else if (moved && targetSegment) {
        onPhysicalNodeConnectToSegment?.(drag.fromNodeId,targetSegment.id,projectOntoPolyline(targetSegment.points??[],point).point);
        setPhysicalStart(null);
      } else if(!moved) {
        if(physicalStart&&physicalStart!==drag.fromNodeId){onPhysicalNodesConnect?.(physicalStart,drag.fromNodeId);setPhysicalStart(null);}
        else setPhysicalStart(drag.fromNodeId);
      } else setPhysicalStart(null);
      setPhysicalNodePreview(null);
    } else if(dragRef.current?.kind==="covering") {const drag=dragRef.current;onCoveringDrag?.(drag.objectId,drag.spanIndex,drag.part,drag.start,screenToWorld(camera,localPoint(event.clientX,event.clientY)),inlineObjectDragMoved(event.clientX-drag.clientX,event.clientY-drag.clientY)?"commit":"cancel");
    } else if(dragRef.current?.kind==="companion") {
      const drag=dragRef.current;
      if(inlineObjectDragMoved(event.clientX-drag.clientX,event.clientY-drag.clientY)) onDrawingMove?.(drag.objectId,drag.drawingId,{x:drag.offset.x+(event.clientX-drag.clientX)/camera.zoom,y:drag.offset.y+(event.clientY-drag.clientY)/camera.zoom});
      setDrawingPreview(null);
    } else if (dragRef.current?.kind === "object") {
      const drag = dragRef.current;
      const deltaX = event.clientX - drag.clientX;
      const deltaY = event.clientY - drag.clientY;
      if (inlineObjectDragMoved(deltaX, deltaY)) {
        onObjectMove?.(drag.objectId, objects.find(o=>o.id===drag.objectId)?.metadata?.joiningPipeExit
          ? inlineObjectDragDestination({ x: drag.objectX, y: drag.objectY }, deltaX, deltaY, camera.zoom)
          : snappedObjectDestination(
          drag.objectId,
          inlineObjectDragDestination({ x: drag.objectX, y: drag.objectY }, deltaX, deltaY, camera.zoom),
          event.ctrlKey,
        ),drag.mode);
      }
      onObjectMovePreview?.(drag.objectId, null);
      setConnectorAlignmentGuides({});
      setPhysicalGuide(undefined);
    } else if (dragRef.current?.kind === "wire-route") {
      const drag = dragRef.current;
      onWireRoutePointPreview?.(drag.wireId,drag.routeIndex,null);
      const moved=inlineObjectDragMoved(event.clientX-drag.clientX,event.clientY-drag.clientY);
      if(drag.insert||moved) {
        const rawPoint = moved ? snapBendPoint({
        x: drag.point.x + (event.clientX - drag.clientX) / camera.zoom,
        y: drag.point.y + (event.clientY - drag.clientY) / camera.zoom,
        }, drag.anchors ?? [], view === "e4" ? e4RoutingMode === "angular" || event.ctrlKey : event.ctrlKey, 7 / camera.zoom, undefined, Math.PI / 6, drag.snapState,drag.opMember?"nearest-compatible":"all").point : drag.point;
        onWireRoutePointMove?.(drag.wireId, drag.routeIndex, view === "e4" && e4RoutingMode === "orthogonal" ? orthogonalE4Bend(rawPoint, drag.point, objects.find(item => item.id === drag.wireId)?.points ?? []) : rawPoint, drag.mode, drag.insert);
      }
      setPhysicalGuide(undefined);
    } else if (dragRef.current?.kind === "e4-wire-segment") {
      const drag = dragRef.current;
      const pixelDelta = drag.orientation === "horizontal"
        ? event.clientY - drag.clientY
        : event.clientX - drag.clientX;
      if (Math.abs(pixelDelta) >= 1) {
        onE4WireSegmentMove?.(drag.wireId, drag.segmentIndex, drag.coordinate + pixelDelta / camera.zoom);
      }
    } else if (dragRef.current?.kind === "e4-wire-label") {
      const drag = dragRef.current;
      const wire = objects.find((item) => item.id === drag.wireId);
      const position = wire
        ? projectPointToE4WireLabelPosition(getE4WireRoute(wire), screenToWorld(camera, localPoint(event.clientX, event.clientY)))
        : null;
      if (position !== null) onE4WireLabelPositionChange?.(drag.wireId, position);
      setWireLabelPreview(null);
    } else if (dragRef.current?.kind === "e4-screen") {
      const drag = dragRef.current;
      if (screenPositionPreview?.screenId === drag.screenId) {
        onE4ScreenPositionChange?.(drag.screenId, screenPositionPreview.position);
      } else {
        const screen = overlays.screens.find((item) => item.id === drag.screenId);
        const layout = screen ? getE4ScreenLayout(screen, objects) : null;
        const worldPoint = screenToWorld(camera, localPoint(event.clientX, event.clientY));
        if (layout) {
          onE4ScreenPositionChange?.(drag.screenId, getE4ScreenPositionForPoint(layout, worldPoint));
        } else {
          const pixelDelta = drag.orientation === "horizontal"
            ? event.clientX - drag.clientX
            : event.clientY - drag.clientY;
          if (Math.abs(pixelDelta) >= 1) {
            const position = Math.max(0, Math.min(1, drag.position + pixelDelta / camera.zoom / drag.spanLength));
            onE4ScreenPositionChange?.(drag.screenId, position);
          }
        }
      }
      setScreenPositionPreview(null);
    }
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const cancelPointer = (event: PointerEvent<HTMLCanvasElement>) => {
    if(selectionDragRef.current?.pointerId===event.pointerId){selectionDragRef.current=null;setSelectionRectangle(null);setSelectionCandidateIds([]);if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);return;}
    if(freeEndDrag.current?.pointerId===event.pointerId){const d=freeEndDrag.current;freeEndDrag.current=null;for(const ref of d.group??[{wireId:d.wireId,end:d.end}])onFreeWireEndpointPreview?.(ref.wireId,ref.end,null);if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);return;}
    setPhysicalGuide(undefined);
    if (dragRef.current?.pointerId !== event.pointerId) return;
    if(dragRef.current.kind==="covering"){const d=dragRef.current;onCoveringDrag?.(d.objectId,d.spanIndex,d.part,d.start,d.start,"cancel");}
    if(dragRef.current.kind==="companion") setDrawingPreview(null);
    if (dragRef.current.kind === "object") {
      onObjectMovePreview?.(dragRef.current.objectId, null);
      setConnectorAlignmentGuides({});
    }
    if (dragRef.current.kind === "wire-route") onWireRoutePointPreview?.(dragRef.current.wireId,dragRef.current.routeIndex,null);
    if (dragRef.current.kind === "e4-wire-label") setWireLabelPreview(null);
    if (dragRef.current.kind === "e4-screen") setScreenPositionPreview(null);
    if (dragRef.current.kind === "physical-node-connect") {setPhysicalNodePreview(null);setPhysicalStart(null);}
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const allowDrop = (event: DragEvent<HTMLCanvasElement>) => {
    if (!event.dataTransfer.types.includes("application/x-techmap-catalog-item")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  };

  const drop = (event: DragEvent<HTMLCanvasElement>) => {
    const itemId = event.dataTransfer.getData("application/x-techmap-catalog-item");
    if (!itemId) return;
    event.preventDefault();
    onCatalogDrop(itemId, screenToWorld(camera, localPoint(event.clientX, event.clientY)));
  };

  const doubleClick = (event: MouseEvent<HTMLCanvasElement>) => {
    if(onObjectPick)return;
    const point = screenToWorld(camera, localPoint(event.clientX, event.clientY));
    if(tool==="select"){
      const objectId=hitTestEditorScene(objects,layers,point,camera.zoom,view,componentTemplateViewInstances,resolveComponentTemplateAssetUrl),object=objects.find(item=>item.id===objectId);
      if(object?.metadata?.graphicKind==="text"){onObjectSelect(object.id,false);onObjectEditRequest?.(object.id);return;}
    }
    if (view === "e4" && tool === "select" && onE4WireRoutePointRemove) {
      const selectedWire = objects.find((item) => item.id === selectedObjectId);
      const selectedLayer = selectedWire ? layers.find((item) => item.id === selectedWire.layerId) : null;
      const routeIndex = selectedLayer?.locked === true
        ? null
        : hitTestWireRoutePoint(selectedWire, point, camera.zoom);
      if (selectedWire && routeIndex !== null) {
        onE4WireRoutePointRemove(selectedWire.id, routeIndex);
        return;
      }
    }
    if (view === "e4" && tool === "select" && onObjectEditRequest) {
      const objectId = hitTestEditorScene(
        objects,
        layers,
        point,
        camera.zoom,
        view,
        componentTemplateViewInstances,
        resolveComponentTemplateAssetUrl,
      );
      const object = objects.find((item) => item.id === objectId);
      const layer = object ? layers.find((item) => item.id === object.layerId) : null;
      if (object?.kind === "connector" && layer?.locked !== true) {
        onObjectSelect(object.id, false);
        onObjectEditRequest(object.id);
        return;
      }
    }
    if (view === "drawing") {
      const selectedWire = objects.find((item) => item.id === selectedObjectId);
      const selectedLayer = selectedWire ? layers.find((item) => item.id === selectedWire.layerId) : null;
      const routeIndex = selectedLayer?.locked === true
        ? null
        : hitTestWireRoutePoint(selectedWire, point, camera.zoom);
      if (selectedWire && routeIndex !== null) {
        onWireRoutePointRemove?.(selectedWire.id, routeIndex);
        return;
      }
    }
    if(layers.some(l=>l.locked&&objects.some(o=>o.id===selectedObjectId&&o.layerId===l.id)))return;
    onCanvasDoubleClick?.(point, event.ctrlKey);
  };

  const inlinePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    containInlineEditorPointerEvent(event);
    if (isInlineEditorControlTarget(event.target)) return;
    if (event.button === 0 && view === "e4" && tool === "select" && onWireToolRequest) {
      const endpoint = hitTestConnectorContact(
        objects,
        layers,
        screenToWorld(camera, localPoint(event.clientX, event.clientY)),
        camera.zoom,
        view,
      );
      if (endpoint) {
        setWireStart(endpoint);
        onObjectSelect(endpoint.connectorId, false);
        onWireToolRequest();
        return;
      }
    }
    if (event.button !== 0 || tool !== "select" || !inlineObject ||
        !isInlineEditorReadonlyTarget(event.target)) return;
    const layer = layers.find((item) => item.id === inlineObject.layerId);
    if (layer?.locked === true) return;
    if (event.detail >= 2 && Date.now() > suppressInlineDoubleClickUntilRef.current && onObjectEditRequest) {
      onObjectEditRequest(inlineObject.id);
      return;
    }
    if (!onObjectMove) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    inlineCaptureRef.current=event.currentTarget;
    inlineDragRef.current = {
      kind: "object",
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      objectId: inlineObject.id,
      objectX: inlineObject.x,
      objectY: inlineObject.y,
      mode:event.shiftKey?"adjacent":"carry",
    };
    inlineDragActivatedRef.current = false;
  };

  const inlinePointerUp = (event: PointerEvent<HTMLDivElement>) => {
    containInlineEditorPointerEvent(event);
    if (inlineDragRef.current?.pointerId !== event.pointerId) return;
    const drag = inlineDragRef.current;
    const deltaX = event.clientX - drag.clientX;
    const deltaY = event.clientY - drag.clientY;
    if (inlineObjectDragMoved(deltaX, deltaY)) {
      onObjectMove?.(drag.objectId, snappedObjectDestination(
        drag.objectId,
        inlineObjectDragDestination({ x: drag.objectX, y: drag.objectY }, deltaX, deltaY, camera.zoom),
        event.ctrlKey,
      ),drag.mode);
    }
    onObjectMovePreview?.(drag.objectId, null);
    setConnectorAlignmentGuides({});
    if (inlineDragActivatedRef.current) suppressInlineDoubleClickUntilRef.current = Date.now() + 500;
    setInlineDragOffset(null);
    inlineDragRef.current = null;
    inlineCaptureRef.current = null;
    inlineDragActivatedRef.current = false;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const inlinePointerCancel = (event: PointerEvent<HTMLDivElement>) => {
    containInlineEditorPointerEvent(event);
    if (inlineDragRef.current?.pointerId !== event.pointerId) return;
    onObjectMovePreview?.(inlineDragRef.current.objectId, null);
    setConnectorAlignmentGuides({});
    setInlineDragOffset(null);
    inlineDragRef.current = null;
    inlineCaptureRef.current = null;
    inlineDragActivatedRef.current = false;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const inlinePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    containInlineEditorPointerEvent(event);
    const drag = inlineDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - drag.clientX;
    const deltaY = event.clientY - drag.clientY;
    if (!inlineObjectDragMoved(deltaX, deltaY)) return;
    inlineDragActivatedRef.current = true;
    event.preventDefault();
    const destination = snappedObjectDestination(drag.objectId, inlineObjectDragDestination(
      { x: drag.objectX, y: drag.objectY },
      deltaX,
      deltaY,
      camera.zoom,
    ), event.ctrlKey);
    if (onObjectMovePreview) {
      setInlineDragOffset(null);
      onObjectMovePreview(drag.objectId, destination,drag.mode);
    } else {
      setInlineDragOffset({
        x: (destination.x - drag.objectX) * camera.zoom,
        y: (destination.y - drag.objectY) * camera.zoom,
      });
    }
  };

  const inlineDoubleClick = (event: MouseEvent<HTMLDivElement>) => {
    event.stopPropagation();
    if (isInlineEditorControlTarget(event.target)) return;
    if (!inlineObject || Date.now() <= suppressInlineDoubleClickUntilRef.current) {
      event.preventDefault();
      return;
    }
    const layer = layers.find((item) => item.id === inlineObject.layerId);
    if (tool === "select" && layer?.locked !== true) onObjectEditRequest?.(inlineObject.id);
  };

  return (
    <div ref={frameRef} className={`he-canvas-frame tool-${tool}`}>
      <canvas
        ref={canvasRef}
        className="he-canvas"
        tabIndex={0}
        aria-label={`${view === "e4" ? "Поле схемы Э4" : "Поле чертежа"}. Масштаб ${Math.round(camera.zoom * 100)} процентов`}
        onPointerDown={pointerDown}
        onKeyDown={event=>{if(onObjectPick&&event.key==='Escape'){event.stopPropagation();onObjectPickCancel?.();}}}
        onPointerMove={pointerMove}
        onPointerLeave={()=>setHoverTarget(null)}
        onPointerUp={endPointer}
        onPointerCancel={cancelPointer}
        onLostPointerCapture={cancelPointer}
        onDragOver={allowDrop}
        onDrop={drop}
        onDoubleClick={doubleClick}
        onContextMenu={event=>{
          if(onObjectPick){event.preventDefault();return;}
          if(tool.startsWith("graphic-")&&graphicPathDraft){event.preventDefault();finishGraphicPath();return;}
          if(view!=="drawing"||(!onPhysicalContextAction&&!objectProperties&&!onObjectsIsolate&&!onDetachedPairAction&&!onFreeWireEndStyleRequest))return;
          event.preventDefault();
          setHoverTarget(null);
          const point=screenToWorld(camera,localPoint(event.clientX,event.clientY));
          // Endpoint controls remain selectable when a dimension extension or
          // an underpassing twist stroke lies at the same coordinate.
          const freeEndWire=objects.find(o=>o.kind==="wire"&&selectedSet.has(o.id)&&layers.some(l=>l.id===o.layerId&&l.visible)&&(["from","to"] as const).some(end=>{
            const p=end==="from"?o.points?.[0]:o.points?.at(-1);
            return o.metadata?.[end==="from"?"freeFrom":"freeTo"]==="true"&&p&&Math.hypot(p.x-point.x,p.y-point.y)<=7/camera.zoom;
          }));
          const id=freeEndWire?.id??hitTestEditorScene(objects,layers,point,camera.zoom,view,componentTemplateViewInstances,resolveComponentTemplateAssetUrl);
          const object=objects.find(o=>o.id===id) ?? (onObjectsIsolate ? objects.find(o=>selectedSet.has(o.id)) : undefined),layer=layers.find(l=>l.id===object?.layerId);
          if(!object||layer?.locked||(!onObjectsIsolate&&!["wire","connector","physical-node","physical-segment","physical-covering"].includes(object.kind))){setPhysicalMenu(null);return;}
          const isolationIds=selectedSet.has(object.id) ? activeSelectedIds.filter(selectedId=>objects.some(o=>o.id===selectedId)) : [object.id];
          const hasMaterialSelection = isolationIds.some(selectedId => {
            const selectedObject = objects.find(candidate => candidate.id === selectedId);
            return selectedObject && materialObjectGroup(selectedObject.kind) !== null;
          });
          if (onObjectsIsolate && !hasMaterialSelection) { setPhysicalMenu(null); return; }
          if(!selectedSet.has(object.id))onObjectSelect(object.id,false);
          setPhysicalMenu({id:object.id,point,node:object.kind==="physical-node",x:event.clientX,y:event.clientY,isolationIds});
        }}
      />
      {(graphicPathDraft?.points[0]??graphicStart)&&<svg style={{position:"absolute",inset:0,width:"100%",height:"100%",pointerEvents:"none"}} aria-label="Первая точка фигуры"><circle cx={(graphicPathDraft?.points[0]??graphicStart)!.x*camera.zoom+camera.offsetX} cy={(graphicPathDraft?.points[0]??graphicStart)!.y*camera.zoom+camera.offsetY} r="4" fill="#fff" stroke="#1179ac" strokeWidth="2"/></svg>}
      {selectionRectangle&&<svg style={{position:"absolute",inset:0,width:"100%",height:"100%",pointerEvents:"none"}} aria-label="Рамка выделения"><rect x={Math.min(selectionRectangle.start.x,selectionRectangle.end.x)*camera.zoom+camera.offsetX} y={Math.min(selectionRectangle.start.y,selectionRectangle.end.y)*camera.zoom+camera.offsetY} width={Math.abs(selectionRectangle.end.x-selectionRectangle.start.x)*camera.zoom} height={Math.abs(selectionRectangle.end.y-selectionRectangle.start.y)*camera.zoom} fill="rgba(17,121,172,.10)" stroke="#1179ac" strokeWidth="1" strokeDasharray="5 3"/></svg>}
      {physicalGuide&&<svg style={{position:"absolute",inset:0,width:"100%",height:"100%",pointerEvents:"none"}} aria-label="Привязка трассы"><polyline points={physicalGuide.map(p=>`${p.x*camera.zoom+camera.offsetX},${p.y*camera.zoom+camera.offsetY}`).join(" ")} fill="none" stroke="#ca5697" strokeWidth="1" strokeDasharray="5 4"/></svg>}
      {railDraft&&<svg style={{position:"absolute",inset:0,width:"100%",height:"100%",pointerEvents:"none"}} aria-label="Предпросмотр линии позиций"><line x1={railDraft.start.x*camera.zoom+camera.offsetX} y1={railDraft.start.y*camera.zoom+camera.offsetY} x2={railDraft.end.x*camera.zoom+camera.offsetX} y2={railDraft.end.y*camera.zoom+camera.offsetY} stroke="#1179ac" strokeWidth="2" strokeDasharray="9 5 2 5"/><circle cx={railDraft.start.x*camera.zoom+camera.offsetX} cy={railDraft.start.y*camera.zoom+camera.offsetY} r="4" fill="#1179ac"/></svg>}
      {tool==="select" && onDrawingScale && objects.filter(o=>o.id===selectedObjectId&&o.kind==="connector"&&layers.some(l=>l.id===o.layerId&&l.visible&&!l.locked)).flatMap(object=>{
        const instance=displayInstances.find(i=>i.objectId===object.id);if(!instance)return [];
        const drawings=view==="e4"?projectE4DrawingCompanions(instance,object,getE4ConnectorLayout(object)?.width??object.width,resolveComponentTemplateAssetUrl).filter(d=>d.visible):[];
        const projected=view==="drawing"?projectComponentTemplateView(instance,view,object,resolveComponentTemplateAssetUrl):null;
        const contactSide=view==="drawing"?projectContactSideView(instance,{x:object.x,y:object.y},projected?.bounds??{minX:object.x,minY:object.y,maxX:object.x+object.width,maxY:object.y+object.height},resolveComponentTemplateAssetUrl):null;
        const targets=view==="e4"?drawings.map(d=>({id:d.drawingId,x:d.bounds.maxX,y:d.bounds.minY,ax:d.bounds.minX,ay:d.bounds.maxY})) : [
          ...(projected?[{id:DRAWING_VIEW_PLACEMENT_ID,x:projected.bounds.maxX,y:projected.bounds.maxY,ax:object.x,ay:object.y}]:[]),
          ...(contactSide?.visible?[{id:CONTACT_SIDE_VIEW_PLACEMENT_ID,x:contactSide.drawing.bounds.maxX,y:contactSide.drawing.bounds.maxY,ax:contactSide.drawing.bounds.minX,ay:contactSide.drawing.bounds.minY}]:[]),
        ];
        return targets.map(t=><DrawingResizeGrip key={`${object.id}:${t.id}`} x={t.x*camera.zoom+camera.offsetX} y={t.y*camera.zoom+camera.offsetY} vx={(t.x-t.ax)*camera.zoom} vy={(t.y-t.ay)*camera.zoom} scale={drawingScale(instance.drawingPlacements,t.id)} preview={scale=>setScalePreview(scale===null?null:{objectId:object.id,drawingId:t.id,scale})} commit={scale=>onDrawingScale(object.id,t.id,scale)}/>);
      })}
      <div className="he-canvas-status" aria-live="polite">
        <span>{Math.round(camera.zoom * 100)}%</span>
        <span>{tool === "position-rail" ? railDraft ? `Нанизано позиций: ${railDraft.leaderIds.length}. Второй щелчок закрепит линию` : "Первый щелчок задаёт начало линии позиций"
          : tool === "wire"
          ? physicalStart ? "Выберите узел или общий выход для завершения канала" : wireReconnect ? "Выберите новый контакт для конца провода"
            : wireStart ? "Выберите второй контакт"
              : "Выберите два контакта; конец выбранного провода можно переподключить"
          : tool === "pan" ? "Тяните поле мышью"
            : objects.find((item) => item.id === selectedObjectId)?.kind === "wire"
              ? view === "drawing"
                ? "Точки трассы: перетащить; двойной щелчок — удалить"
                : "Точки: перенос · середина: добавить · Shift: соседние участки"
              : "Ctrl + колесо — масштаб"}</span>
      </div>
      {view==="drawing"&&tool.startsWith("dimension")&&<><svg className="he-dimension-targets" aria-hidden="true">{objects.filter(o=>(o.kind==="physical-segment"||o.kind==="wire"&&o.metadata?.physicalRoute!=="true")&&layers.some(l=>l.id===o.layerId&&l.visible)).flatMap(w=>(w.kind==="physical-segment"?pipeSceneEditablePoints(w):w.points??[]).map((p,i)=>i!==0&&i!==(w.kind==="physical-segment"?pipeSceneEditablePoints(w):w.points??[]).length-1?null:<circle key={`${w.id}:${i}`} cx={p.x*camera.zoom+camera.offsetX} cy={p.y*camera.zoom+camera.offsetY} r={dimensionStart.some(a=>a.wireId===w.id&&a.index===i)?6:4} fill="white" stroke="#167caf" strokeWidth="2"/>))}</svg><div className="he-dimension-help" role="status">{dimensionMessage||"Выберите два конца участка"}</div></>}
      {hoverGrip&&view==="drawing"&&<svg className="he-covering-grip" aria-hidden="true"><line x1={(hoverGrip.point.x-hoverGrip.normal.x*hoverGrip.halfWidth)*camera.zoom+camera.offsetX} y1={(hoverGrip.point.y-hoverGrip.normal.y*hoverGrip.halfWidth)*camera.zoom+camera.offsetY} x2={(hoverGrip.point.x+hoverGrip.normal.x*hoverGrip.halfWidth)*camera.zoom+camera.offsetX} y2={(hoverGrip.point.y+hoverGrip.normal.y*hoverGrip.halfWidth)*camera.zoom+camera.offsetY} stroke="#00a9db" strokeWidth="6"/><circle cx={hoverGrip.point.x*camera.zoom+camera.offsetX} cy={hoverGrip.point.y*camera.zoom+camera.offsetY} r="5" fill="white" stroke="#007ca8" strokeWidth="2"/></svg>}
      {view==="drawing"&&tool==="select"&&(onFreeWireEndpointMove||onFreeWireEndpointsXChange)&&<svg aria-label="Свободные концы проводов" style={{position:"absolute",inset:0,width:"100%",height:"100%",pointerEvents:"none"}}>
        {freeEndpointRefs.map(ref=><circle key={`${ref.wireId}:${ref.end}`} cx={ref.point.x*camera.zoom+camera.offsetX} cy={ref.point.y*camera.zoom+camera.offsetY} r={5} fill="white" stroke="#167caf" strokeWidth={2}/>)}
        {freeEndAxes.map(axis=>{const y=(axis.minY+axis.maxY)/2;return <g key={`axis:${axis.end}:${axis.wireIds.join(",")}`}><line x1={axis.x*camera.zoom+camera.offsetX} y1={axis.minY*camera.zoom+camera.offsetY-10} x2={axis.x*camera.zoom+camera.offsetX} y2={axis.maxY*camera.zoom+camera.offsetY+10} stroke="#167caf" strokeWidth={1.5} strokeDasharray="5 4"/><circle cx={axis.x*camera.zoom+camera.offsetX} cy={y*camera.zoom+camera.offsetY} r={8} fill="white" stroke="#167caf" strokeWidth={2}/><path d={`M ${axis.x*camera.zoom+camera.offsetX-3} ${y*camera.zoom+camera.offsetY-3} L ${axis.x*camera.zoom+camera.offsetX+3} ${y*camera.zoom+camera.offsetY} L ${axis.x*camera.zoom+camera.offsetX-3} ${y*camera.zoom+camera.offsetY+3}`} fill="none" stroke="#167caf" strokeWidth={1.5}/></g>;})}
      </svg>}
      {view==="drawing"&&tool==="select"&&<svg aria-label="Изгибы свободных проводов" style={{position:"absolute",inset:0,width:"100%",height:"100%",pointerEvents:"none"}}>{objects.filter(o=>o.metadata?.detachedDrawing==="true"&&selectedSet.has(o.id)&&layers.some(l=>l.id===o.layerId&&l.visible&&!l.locked)).flatMap(o=>e4Midpoints(o).map(h=><circle key={`${o.id}:${h.index}`} cx={h.point.x*camera.zoom+camera.offsetX} cy={h.point.y*camera.zoom+camera.offsetY} r={4} fill="#1179ac" opacity={.35}/>))}</svg>}
      <CanvasObjectHint target={physicalMenu?null:hoverTarget}/>
      {physicalMenu&&objects.some(o=>o.id===physicalMenu.id)&&<CanvasObjectPopover key={physicalMenu.id+(physicalMenu.wires?":wires":"")} x={physicalMenu.x} y={physicalMenu.y} label={physicalMenu.wires?"Провода пайпа":"Свойства объекта"} onClose={()=>setPhysicalMenu(null)}>
        {physicalMenu.wires ? <><table className="he-pipe-wires"><thead><tr><th>Провод</th><th>Цепь</th></tr></thead><tbody>{pipeSceneWireIds(objects.find(o=>o.id===physicalMenu.id)).map(id=>{const w=objects.find(o=>o.id===id);return <tr key={id}><td><button className="he-wire-row" onClick={()=>onRelatedObjectsSelect?.([id])}><i style={{background:resolveWireColorHex(w?.color??"")}}/>{`W${objects.filter(o=>o.kind==="wire").findIndex(o=>o.id===id)+1}`}</button></td><td>{w?.label}</td></tr>;})}</tbody></table>{pipeSceneWireIds(objects.find(o=>o.id===physicalMenu.id)).length===0&&<span>Нет назначенных проводов</span>}</> : <>
          {onObjectsIsolate&&physicalMenu.isolationIds?.length&&<button type="button" className="ui-control" onClick={()=>{onObjectsIsolate(physicalMenu.isolationIds!);setPhysicalMenu(null);}}>Изолировать</button>}
          {onDetachedPairAction&&pairActionIds(physicalMenu.id).length===2&&(["twist","straighten"] as const).map(action=><button key={action} type="button" className="ui-control" onClick={()=>{onDetachedPairAction(pairActionIds(physicalMenu.id),action);setPhysicalMenu(null);}}>{action==="twist"?"Свить пару":"Распрямить пару"}</button>)}
          {onFreeWireEndStyleRequest&&(["from","to"] as const).filter(end=>objects.find(o=>o.id===physicalMenu.id)?.metadata?.[end==="from"?"freeFrom":"freeTo"]==="true").map(end=><button key={end} type="button" className="ui-control" onClick={()=>{onFreeWireEndStyleRequest(physicalMenu.id,end);setPhysicalMenu(null);}}>{end==="from"?"Оконцовка начала":"Оконцовка конца"}</button>)}
          {objectProperties?.(physicalMenu.id)}
          {!objectProperties&&onPhysicalContextAction&&objects.some(o=>o.id===physicalMenu.id&&o.kind==="physical-segment")&&<button type="button" className="ui-control" onClick={()=>{onPhysicalContextAction(physicalMenu.id,physicalMenu.point,"remove-pipe");setPhysicalMenu(null);}}>Удалить пайп</button>}
          {physicalMenu.node ? onPhysicalNodesConnect&&<button type="button" className="ui-control" disabled={objects.filter(o=>o.kind==="physical-node"&&!o.metadata?.joiningPipe&&selectedSet.has(o.id)).length!==2} onClick={()=>{const nodes=objects.filter(o=>o.kind==="physical-node"&&!o.metadata?.joiningPipe&&selectedSet.has(o.id));if(nodes.length===2)onPhysicalNodesConnect?.(nodes[0]!.id,nodes[1]!.id);setPhysicalMenu(null);}}>Пайп между двумя узлами</button> : onPhysicalContextAction&&objects.some(o=>o.id===physicalMenu.id&&(o.kind==="physical-segment"||o.kind==="physical-covering"))&&<details className="he-covering-actions"><summary>Оболочки и ответвление</summary><div className="he-context-actions">{([...(objects.find(o=>o.id===physicalMenu.id)?.kind==="physical-segment"&&objects.find(o=>o.id===physicalMenu.id)?.pipe?.role!=="joining-pipe"?["branch" as const]:[]),...standardCoveringKinds] as const).map(action=><button type="button" className="ui-control" key={action} onClick={()=>{const target=objects.find(o=>o.id===physicalMenu.id);onPhysicalContextAction?.(physicalMenu.id,physicalMenu.point,action,target?.kind==="physical-covering"?"covering":"segment");setPhysicalMenu(null);}}>{action==="branch"?"Т-ответвление":action}</button>)}</div></details>}
        </>}
      </CanvasObjectPopover>}
      {overlay && <div className="he-e4-wire-popover">{overlay}</div>}
      {diagnosticOverlay && <div className="he-e4-diagnostic-popover">{diagnosticOverlay}</div>}
      {inlineEditor && inlineObject && inlineLayout && (
        <div
          className={`he-e4-inline-editor ${inlineObject.metadata?.diagnostic === "error" ? "has-error" : ""}`}
          style={{
            left: inlineLayout.x * camera.zoom + camera.offsetX + (inlineDragOffset?.x ?? 0),
            top: inlineLayout.y * camera.zoom + camera.offsetY + (inlineDragOffset?.y ?? 0),
            width: inlineLayout.width,
            height: inlineLayout.height,
            transform: `scale(${camera.zoom})`,
          }}
          onPointerDown={inlinePointerDown}
          onPointerMove={inlinePointerMove}
          onPointerUp={inlinePointerUp}
          onPointerCancel={inlinePointerCancel}
          onLostPointerCapture={inlinePointerCancel}
          onDoubleClick={inlineDoubleClick}
        >{inlineEditor}</div>
      )}
      <ul className="visually-hidden" aria-label="Объекты на поле">
        {objectsInPaintOrder(objects, layers, view).map((object) => <li key={object.id}>{object.label}</li>)}
      </ul>
    </div>
  );
}
