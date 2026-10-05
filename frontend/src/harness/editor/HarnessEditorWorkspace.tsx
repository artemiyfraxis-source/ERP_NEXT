import type { PhysicalDragMode } from "./physical-editing";
import { InfoHint } from "../InfoHint";
import type { CoveringDragPart } from "./covering-layout";
import {type PhysicalContextAction,type PhysicalContextTarget} from "./physical-coverings";
import type { DimensionMode } from "./drawing-dimensions";
import type { DrawingGraphic } from "./drawing-documents";
import type { DrawingSnaps } from "../component-library/drawing-geometry";
import { useCallback, useEffect, useMemo, useState, type FocusEvent, type ReactNode } from "react";
import { MaterialObjectsPanel, materialObjectGroup } from "./MaterialObjectsPanel";
import {
  CanvasViewport,
  getEditorSceneBounds,
  getVisibleCableSheathScene,
  type E4ConnectableEndpoint,
  type E4SceneOverlays,
  type EditorSelectionMode,
} from "./CanvasViewport";
import type {
  ComponentTemplateViewInstance,
  ResolveComponentTemplateAssetUrl,
} from "./component-template-view-renderer";
import { CatalogDock } from "./CatalogDock";
import type { WireDatabaseOption } from "./wire-database";
import { fitEditorCameraToBounds, zoomEditorCameraAt, type EditorViewportSize } from "./editor-camera";
import { moveLayer, toggleLayerLock, toggleLayerVisibility, updateEditorObject } from "./editor-state";
import type {
  EditorCamera,
  EditorCatalogItem,
  EditorCatalogSource,
  EditorLayer,
  EditorPoint,
  EditorSceneObject,
  EditorTool,
  HarnessEditorView,
} from "./editor-types";
import { EditorToolbar, editorToolShortcut } from "./EditorToolbar";
import { E4WireSelectionMenu } from "./E4WireSelectionMenu";
import type { E4DifferentialPairState, E4ScreenState } from "./e4-wire-selection-state";
import { LayersPanel } from "./LayersPanel";
import { ObjectInspector } from "./ObjectInspector";
import type { CableInstance, WireEndStripProfiles } from "./model";
import type { WireEndTerminalCorrections } from "./terminal-details";
import type { WireBlankEnd } from "../WireBlankCatalog";
import { FreeWireEndsPanel } from "./FreeWireEndsPanel";
import { HarnessSectionNavigation, type HarnessSectionId } from "./HarnessSectionNavigation";
import "./harness-editor.css";

const defaultLayers: readonly EditorLayer[] = [
  { id: "annotations", label: "Размеры и обозначения", visible: true, locked: false },
  { id: "components", label: "Соединители", visible: true, locked: false },
  { id: "wires", label: "Провода", visible: true, locked: false },
];

const defaultObjects: readonly EditorSceneObject[] = [
  { id: "XS1", layerId: "components", kind: "connector", label: "XS1", x: 145, y: 145, width: 118, height: 72, color: "#416579" },
  { id: "XS2", layerId: "components", kind: "connector", label: "XS2", x: 590, y: 300, width: 118, height: 72, color: "#416579" },
  {
    id: "W1",
    layerId: "wires",
    kind: "wire",
    label: "W1 · 350 мм",
    x: 0,
    y: 0,
    width: 0,
    height: 0,
    color: "#bd3b38",
    points: [{ x: 263, y: 180 }, { x: 415, y: 180 }, { x: 415, y: 336 }, { x: 590, y: 336 }],
  },
  {
    id: "DIM-1",
    layerId: "annotations",
    kind: "dimension",
    label: "350 мм",
    x: 0,
    y: 0,
    width: 0,
    height: 0,
    color: "#55798e",
    points: [{ x: 270, y: 415 }, { x: 585, y: 415 }],
  },
];

const defaultCatalog: readonly EditorCatalogItem[] = [
  { id: "catalog-connector-series:xs-demo-series", title: "Серия XS", subtitle: "Артикулы XS-04 и XS-10", category: "Соединители", accent: "#3f718b", placement: "connector", templateKind: "series", seriesId: "xs-demo-series", defaultPartNumber: "XS-04" },
  { id: "catalog-connector-free", title: "Свободный соединитель", subtitle: "Независимые строки и терминалы из всей базы", category: "Соединители", accent: "#71808a", placement: "connector", templateKind: "free" },
  { id: "catalog-wire-red", title: "Провод 0,35", subtitle: "Красный · 0,35 мм²", category: "Провода", accent: "#c94b47" },
  { id: "catalog-wire-black", title: "Провод 0,50", subtitle: "Чёрный · 0,50 мм²", category: "Провода", accent: "#303a40" },
  { id: "catalog-tube", title: "ТУТ 3/1,5", subtitle: "Термоусадка · чёрная", category: "Аксессуары", accent: "#596168" },
  { id: "catalog-braid", title: "Оплётка 4–8", subtitle: "Нейлоновая · чёрная", category: "Аксессуары", accent: "#758087" },
];

const initialCamera: EditorCamera = { offsetX: 70, offsetY: 48, zoom: 1 };

export function hiddenIdsForIsolatedObjects(objects: readonly EditorSceneObject[], isolatedIds: readonly string[]): string[] {
  const isolated = new Set(isolatedIds);
  const selectedWires = new Set(objects.filter(object => object.kind === "wire" && isolated.has(object.id)).map(object => object.id));
  const selectedConnectors = new Set(objects.filter(object => object.kind === "connector" && isolated.has(object.id)).map(object => object.id));
  const selectedCoveringSegments = new Set<string>();
  for (const object of objects) {
    if (object.kind !== "physical-covering" || !isolated.has(object.id)) continue;
    try {
      const ids: unknown = JSON.parse(object.metadata?.supportSegmentIds ?? "[]");
      if (Array.isArray(ids)) for (const id of ids) if (typeof id === "string") selectedCoveringSegments.add(id);
    } catch { /* Malformed optional presentation data cannot break isolation. */ }
  }
  const supportIds = new Set<string>();
  for (const object of objects) {
    if (object.kind === "physical-segment" && (object.pipe?.wireIds.some(wireId => selectedWires.has(wireId)) || selectedCoveringSegments.has(object.id))) {
      supportIds.add(object.id);
      if (object.pipe?.fromNodeId) supportIds.add(object.pipe.fromNodeId);
      if (object.pipe?.toNodeId) supportIds.add(object.pipe.toNodeId);
    }
    if (object.kind === "physical-node" && object.port?.connectorId && selectedConnectors.has(object.port.connectorId)) {
      supportIds.add(object.id);
    }
  }
  for (const object of objects) if (object.kind === "object-index") {
    const ownerId = object.metadata?.indexObjectId ?? "";
    if (isolated.has(ownerId)) isolated.add(object.id);
    if (isolated.has(object.id)) isolated.add(ownerId);
  }
  return objects.filter(object => !isolated.has(object.id) && !supportIds.has(object.id)).map(object => object.id);
}

export function layersWithIsolatedLayer(layers: readonly EditorLayer[], layerId: string): EditorLayer[] {
  return layers.map(layer => ({ ...layer, visible: layer.id === layerId }));
}

export type EditorSaveState = "saved" | "saving" | "changed" | "error";

export interface HarnessEditorWorkspaceProps {
  readonly harnessId: string;
  readonly harnessDesignation: string;
  readonly view?: HarnessEditorView;
  readonly objects?: readonly EditorSceneObject[];
  readonly layers?: readonly EditorLayer[];
  readonly catalogItems?: readonly EditorCatalogItem[];
  readonly catalogSources?: readonly EditorCatalogSource[];
  readonly selectedCatalogSourceId?: string;
  readonly catalogQuery?: string;
  readonly catalogLoadState?: "idle" | "loading" | "loading-more" | "ready" | "unpublished" | "error";
  readonly catalogMessage?: string | null;
  readonly catalogHasMore?: boolean;
  readonly selectedObjectId?: string | null;
  readonly selectedObjectIds?: readonly string[];
  readonly highlightedObjectIds?: readonly string[];
  readonly foregroundWireIds?: readonly string[];
  readonly relationPanel?: ReactNode | ((tool:EditorTool,onToolChange:(tool:EditorTool)=>void)=>ReactNode);
  readonly onPipeIntervalSelect?:(id:string,from:number,to:number)=>void;
  readonly onCoveringDrag?:(id:string,spanIndex:number,part:CoveringDragPart,start:EditorPoint,point:EditorPoint,phase:"preview"|"commit"|"cancel")=>void;
  readonly onDimensionCreate?:(wireId:string,from:number,to:number,pointCount:number,mode:DimensionMode,auxiliary?:boolean)=>void;
  readonly onPositionRailCreate?:(start:EditorPoint,end:EditorPoint,leaderIds:readonly string[])=>void;
  readonly onGraphicCreate?:(graphic:DrawingGraphic)=>void;
  readonly drawingSnaps?:DrawingSnaps;
  readonly drawingAngleStep?:number;
  readonly selectedGraphic?:boolean; readonly canPasteGraphic?:boolean; readonly onGraphicCopy?:()=>void; readonly onGraphicPaste?:()=>void; readonly onGraphicDelete?:()=>void; readonly onUndo?:()=>void; readonly angleStep?:number; readonly onAngleStepChange?:(degrees:number)=>void; readonly onDrawingSnapsChange?:(snaps:DrawingSnaps)=>void;
  readonly documentActions?: ReactNode;
  readonly drawingWindows?: (camera:EditorCamera)=>ReactNode;
  readonly revealRequest?: { readonly token: number; readonly objectIds: readonly string[] };
  readonly cables?: readonly CableInstance[];
  readonly saveState?: EditorSaveState;
  readonly onSaveRequest?: () => void;
  readonly localCopyControls?: {
    readonly hiddenObjectIds: readonly string[];
    readonly backgroundOpacity: number;
    readonly onHiddenObjectIdsChange: (ids: readonly string[]) => void;
    readonly onBackgroundOpacityChange: (opacity: number) => void;
    readonly onObjectsIsolate?: (objectIds: readonly string[]) => void;
    readonly onCancel: () => void;
  };
  readonly backgroundObjects?: readonly EditorSceneObject[];
  readonly backgroundOpacity?: number;
  readonly onViewChange?: (view: HarnessEditorView) => void;
  /** Opens the manufacturing route document from the editor header. */
  readonly onRouteRequest?: () => void;
  /** Switches to any harness document from the shared editor navigation. */
  readonly onSectionChange?: (section: HarnessSectionId) => void | Promise<void>;
  readonly onObjectsChange?: (objects: readonly EditorSceneObject[]) => void;
  readonly onLayersChange?: (layers: readonly EditorLayer[]) => void;
  readonly onSelectedObjectChange?: (objectId: string | null) => void;
  readonly onSelectedObjectIdsChange?: (objectIds: readonly string[]) => void;
  readonly onCatalogItemActivate?: (item: EditorCatalogItem, point?: EditorPoint) => void;
  readonly onCatalogSourceChange?: (sourceId: string) => void;
  readonly onCatalogQueryChange?: (query: string) => void;
  readonly onCatalogLoadMore?: () => void;
  readonly onCatalogRetry?: () => void;
  readonly onWireMaterialClear?: (wireId: string) => void;
  readonly wireMaterialOptions?: readonly WireDatabaseOption[];
  readonly onWireMaterialSelect?: (wireId:string,option:WireDatabaseOption)=>void;
  readonly selectedWireStripProfiles?: WireEndStripProfiles;
  readonly activeWireStripEnd?: "from" | "to";
  readonly onActiveWireStripEndChange?: (end: "from" | "to") => void;
  readonly onWireStripProfileClear?: (wireId: string, end: "from" | "to") => void;
  readonly terminalCorrections?: WireEndTerminalCorrections;
  readonly onTerminalCorrectionApply?: (wireId: string, end: "from" | "to", correctionMm: number) => void;
  readonly onDrawingEndStyleChange?: (wireId: string, end: "from" | "to", style: WireBlankEnd) => void;
  readonly onDrawingEndStylesChange?: (wireIds: readonly string[], end: "from" | "to", style: WireBlankEnd) => void;
  readonly onDrawingEndEndpointChange?: (wireId: string, end: "from" | "to", position: EditorPoint) => void;
  readonly onDrawingEndBulkXChange?: (wireIds: readonly string[], end: "from" | "to", x: number) => void;
  readonly onFreeWireEndpointMove?: (wireId: string, end: "from" | "to", point: EditorPoint) => void;
  readonly onFreeWireEndpointsXChange?: (wireIds: readonly string[], end: "from" | "to", x: number) => void;
  readonly onFreeWireEndpointPreview?: (wireId: string, end: "from" | "to", point: EditorPoint | null) => void;
  readonly onDetachedPairAction?: (wireIds: readonly string[], action: "twist" | "straighten") => void;
  readonly objectProperties?:(objectId:string)=>ReactNode;
  readonly onObjectPick?:(objectId:string|null)=>void;
  readonly onObjectPickCancel?:()=>void;
  readonly onRelatedObjectsSelect?: (ids:readonly string[])=>void;
  readonly onObjectMove?: (objectId: string, point: EditorPoint, mode?: PhysicalDragMode) => void;
  readonly onDrawingScale?: (objectId:string,drawingId:string,scale:number)=>void;
  readonly onDrawingMove?: (objectId:string,drawingId:string,offset:EditorPoint)=>void;
  readonly onObjectMovePreview?: (objectId: string, point: EditorPoint | null, mode?: PhysicalDragMode) => void;
  readonly onObjectEditRequest?: (objectId: string) => void;
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
  readonly e4Overlays?: E4SceneOverlays;
  readonly componentTemplateViewInstances?: readonly ComponentTemplateViewInstance[];
  readonly resolveComponentTemplateAssetUrl?: ResolveComponentTemplateAssetUrl;
  readonly onE4CrossingStyleChange?: (style: "none" | "bridge") => void;
  readonly onE4DifferentialPairChange?: (state: E4DifferentialPairState | null) => void;
  readonly onE4ScreenChange?: (state: E4ScreenState | null) => void;
  readonly onE4ClearGroup?: () => void;
  readonly e4Detached?: boolean;
  readonly e4RoutingMode?: "orthogonal" | "angular";
  readonly onE4DetachedChange?: (detached: boolean) => void;
  readonly onE4Reroute?: () => void;
  readonly onWireRoutePointPreview?: (id:string,index:number,point:EditorPoint|null, mode?:PhysicalDragMode, insert?:boolean)=>void;
  readonly onWireRoutePointMove?: (wireId: string, routeIndex: number, point: EditorPoint, mode?:PhysicalDragMode, insert?:boolean) => void;
  readonly onWireRoutePointRemove?: (wireId: string, routeIndex: number) => void;
  readonly onPhysicalNodesConnect?: (from:string,to:string)=>void;
  readonly onPhysicalNodeConnectToSegment?: (fromNodeId:string,segmentId:string,point:EditorPoint)=>void;
  readonly onPhysicalContextAction?: (segmentId:string,point:EditorPoint,action:PhysicalContextAction,target?:PhysicalContextTarget)=>void;
  readonly onCanvasDoubleClick?: (point: EditorPoint, ctrlKey?: boolean) => void;
  readonly propertyInspector?: ReactNode;
  readonly canvasEditor?: ReactNode;
  readonly diagnostics?: readonly {
    readonly id: string;
    readonly objectId: string;
    readonly label: string;
    readonly message: string;
  }[];
  readonly previewMessage?: string | null;
  readonly onClose?: () => void;
}

export function reconcileWorkspaceSelection(
  selectedObjectIds: readonly string[],
  selectedObjectId: string | null,
  objects: readonly EditorSceneObject[],
): { readonly objectIds: readonly string[]; readonly primaryObjectId: string | null } {
  const availableIds = new Set(objects.filter((object) => !object.id.startsWith("dimension:")).map((object) => object.id));
  const objectIds = [...new Set(selectedObjectIds)].filter((id) => availableIds.has(id));
  const primaryObjectId = selectedObjectId && availableIds.has(selectedObjectId)
    ? selectedObjectId
    : objectIds.at(-1) ?? null;
  if (primaryObjectId && !objectIds.includes(primaryObjectId)) objectIds.push(primaryObjectId);
  return { objectIds, primaryObjectId };
}

export function reconcileWorkspaceGroupSelection(
  objectIds: readonly string[],
  objects: readonly EditorSceneObject[],
): { readonly objectIds: readonly string[]; readonly primaryObjectId: string | null } {
  const available = new Set(objects.filter((object) => object.kind !== "dimension").map((object) => object.id));
  const nextObjectIds = [...new Set(objectIds)].filter((id) => available.has(id));
  return { objectIds: nextObjectIds, primaryObjectId: nextObjectIds.at(-1) ?? null };
}

const saveLabels: Readonly<Record<EditorSaveState, string>> = {
  saved: "Сохранено",
  saving: "Сохраняем…",
  changed: "Есть изменения",
  error: "Ошибка сохранения",
};

function selectEditorTextField(event: FocusEvent<HTMLElement>): void {
  const target = event.target;
  if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) return;
  if (target instanceof HTMLInputElement && ["checkbox", "color", "file", "range"].includes(target.type)) return;
  requestAnimationFrame(() => {
    if (document.activeElement === target) target.select();
  });
}

export function HarnessEditorWorkspace({
  harnessId,
  harnessDesignation,
  view: controlledView,
  objects: controlledObjects,
  layers: controlledLayers,
  catalogItems = defaultCatalog,
  catalogSources,
  selectedCatalogSourceId,
  catalogQuery,
  catalogLoadState,
  catalogMessage,
  catalogHasMore,
  selectedObjectId: controlledSelectedObjectId,
  selectedObjectIds: controlledSelectedObjectIds,
  highlightedObjectIds = [], foregroundWireIds = [], relationPanel, revealRequest, documentActions, drawingWindows,onDimensionCreate,onPositionRailCreate,onGraphicCreate,drawingSnaps,drawingAngleStep,selectedGraphic,canPasteGraphic,onGraphicCopy,onGraphicPaste,onGraphicDelete,onUndo,angleStep,onAngleStepChange,onDrawingSnapsChange,onPipeIntervalSelect,onCoveringDrag,
  cables = [],
  saveState = "saved",
  onSaveRequest,
  localCopyControls,
  backgroundObjects,
  backgroundOpacity,
  onViewChange,
  onObjectsChange,
  onLayersChange,
  onSelectedObjectChange,onObjectPick,onObjectPickCancel,
  onSelectedObjectIdsChange,
  onCatalogItemActivate,
  onCatalogSourceChange,
  onCatalogQueryChange,
  onCatalogLoadMore,
  onCatalogRetry,
  onWireMaterialClear,
  wireMaterialOptions,
  onWireMaterialSelect,
  selectedWireStripProfiles,
  activeWireStripEnd = "from",
  onActiveWireStripEndChange,
  onWireStripProfileClear,
  onDrawingEndStyleChange,
  onDrawingEndStylesChange,
  onDrawingEndEndpointChange,
  onDrawingEndBulkXChange,
  onFreeWireEndpointMove,
  onFreeWireEndpointsXChange,
  onFreeWireEndpointPreview,
  onDetachedPairAction,
  objectProperties, onRelatedObjectsSelect,
  onObjectMove,
  onObjectMovePreview, onDrawingMove, onDrawingScale,
  onObjectEditRequest,
  onWireConnect,
  onWireReconnect,
  onWireConnectToWire,
  onWireReconnectToWire,
  onE4WireSegmentMove,
  onE4WireRoutePointRemove,
  onE4WireLabelPositionChange,
  onE4ScreenPositionChange,
  e4Overlays,
  componentTemplateViewInstances,
  resolveComponentTemplateAssetUrl,
  onE4CrossingStyleChange,
  onE4DifferentialPairChange,
  onE4ScreenChange,
  onE4ClearGroup,
  e4Detached,
  e4RoutingMode = "orthogonal",
  onE4DetachedChange,
  onE4Reroute,
  onWireRoutePointMove, onWireRoutePointPreview,
  onWireRoutePointRemove,
  onCanvasDoubleClick, onPhysicalContextAction, onPhysicalNodesConnect, onPhysicalNodeConnectToSegment,
  terminalCorrections, onTerminalCorrectionApply,
  propertyInspector,
  canvasEditor,
  diagnostics = [],
  previewMessage,
  onClose,
  onRouteRequest,
  onSectionChange,
}: HarnessEditorWorkspaceProps) {
  const [localView, setLocalView] = useState<HarnessEditorView>("e4");
  const [tool, setTool] = useState<EditorTool>("select");
  const [localObjects, setLocalObjects] = useState(defaultObjects);
  const [localLayers, setLocalLayers] = useState(defaultLayers);
  const [localSelectedObjectId, setLocalSelectedObjectId] = useState<string | null>("W1");
  const [localSelectedObjectIds, setLocalSelectedObjectIds] = useState<readonly string[]>(["W1"]);
  const [camera, setCamera] = useState(initialCamera);
  const [viewportSize, setViewportSize] = useState<EditorViewportSize>({ width: 860, height: 560 });
  const [inspectorTab, setInspectorTab] = useState<"properties" | "layers">("properties");
  const [freeWireEndFocus, setFreeWireEndFocus] = useState<{ readonly wireId: string; readonly end: "from" | "to"; readonly requestId: number } | null>(null);
  const [catalogExpanded, setCatalogExpanded] = useState(true);
  const [utilityPanelOpen, setUtilityPanelOpen] = useState(() => typeof window === "undefined" || window.innerWidth > 1100);
  const [inspectorOpen, setInspectorOpen] = useState(() => typeof window === "undefined" || window.innerWidth > 1100);
  const [materialPanelOpen, setMaterialPanelOpen] = useState(() => Boolean(localCopyControls) && (typeof window === "undefined" || window.innerWidth > 1100));

  const view = controlledView ?? localView;
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (document.querySelector('dialog[open], [popover]:popover-open')) return;
      if (event.target instanceof Element && event.target !== document.body && !event.target.closest('.harness-editor')) return;
      if (event.defaultPrevented || (event.target instanceof Element && event.target.closest(
        'input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="dialog"],[popover]:popover-open',
      ))) return;
      const next = editorToolShortcut(event, view);
      if (next) { event.preventDefault(); setTool(next); }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [view]);
  const objects = controlledObjects ?? localObjects;
  const layers = controlledLayers ?? localLayers;
  const selectedObjectId = controlledSelectedObjectId === undefined
    ? localSelectedObjectId
    : controlledSelectedObjectId;
  const selectedObjectIds = controlledSelectedObjectIds ?? localSelectedObjectIds;
  useEffect(() => {
    const normalized = reconcileWorkspaceSelection(selectedObjectIds, selectedObjectId, objects);
    const idsChanged = normalized.objectIds.length !== selectedObjectIds.length ||
      normalized.objectIds.some((id, index) => id !== selectedObjectIds[index]);
    if (!idsChanged && normalized.primaryObjectId === selectedObjectId) return;
    if (controlledSelectedObjectId === undefined) setLocalSelectedObjectId(normalized.primaryObjectId);
    if (controlledSelectedObjectIds === undefined) setLocalSelectedObjectIds(normalized.objectIds);
    onSelectedObjectChange?.(normalized.primaryObjectId);
    onSelectedObjectIdsChange?.(normalized.objectIds);
  }, [
    controlledSelectedObjectId,
    controlledSelectedObjectIds,
    objects,
    onSelectedObjectChange,
    onSelectedObjectIdsChange,
    selectedObjectId,
    selectedObjectIds,
  ]);
  const selectedObject = useMemo(
    () => objects.find((object) => object.id === selectedObjectId) ?? null,
    [objects, selectedObjectId],
  );
  const selectedLayer = layers.find((layer) => layer.id === selectedObject?.layerId);
  const selectedWireIds = selectedObjectIds.filter((id) => objects.some((object) => object.id === id && object.kind === "wire"));
  const selectedFreeWireKey = selectedWireIds.filter(id => {
    const object = objects.find(item => item.id === id);
    return object?.metadata?.freeFrom === "true" || object?.metadata?.freeTo === "true";
  }).join("\u0000");
  useEffect(() => {
    if (view === "drawing" && selectedFreeWireKey) {
      setInspectorOpen(true);
      if (typeof window !== "undefined" && window.innerWidth <= 1100) {
        setUtilityPanelOpen(false);
        setMaterialPanelOpen(false);
      }
    }
  }, [selectedFreeWireKey, view]);
  const requestFreeWireEndStyle = (wireId: string, end: "from" | "to") => {
    selectObject(wireId);
    setInspectorTab("properties");
    setInspectorOpen(true);
    setFreeWireEndFocus(current => ({ wireId, end, requestId: (current?.requestId ?? 0) + 1 }));
    if (window.innerWidth <= 1100) {
      setMaterialPanelOpen(false);
      setUtilityPanelOpen(false);
    }
  };
  const selectedDiffPair = e4Overlays?.diffPairs.find((group) =>
    group.wireIds.length === selectedWireIds.length && group.wireIds.every((id) => selectedWireIds.includes(id))) ?? null;
  const selectedScreen = e4Overlays?.screens.find((group) =>
    group.wireIds.length === selectedWireIds.length && group.wireIds.every((id) => selectedWireIds.includes(id))) ?? null;
  const canClearSelectedGroup = Boolean(
    e4Overlays?.diffPairs.some((group) => group.wireIds.some((id) => selectedWireIds.includes(id))) ||
    e4Overlays?.screens.some((group) => group.wireIds.some((id) => selectedWireIds.includes(id))),
  );
  const e4WireMenu = view === "e4" && tool === "select" && selectedWireIds.length > 0 ? (
    <E4WireSelectionMenu
      selectedWireIds={selectedWireIds}
      crossingStyle={e4Overlays?.crossingStyle ?? "none"}
      differentialPair={selectedDiffPair ? {
        variant: selectedDiffPair.variant ?? 1,
        twistPitchMm: selectedDiffPair.step,
      } : null}
      screen={selectedScreen ? {
        positionPercent: Math.round(selectedScreen.position * 100),
        terminalSide: selectedScreen.terminalSide ?? "above",
      } : null}
      canClearGroup={canClearSelectedGroup}
      disabled={selectedWireIds.some((id) => layers.find((layer) =>
        layer.id === objects.find((object) => object.id === id)?.layerId)?.locked === true)}
      onCrossingStyleChange={(style) => onE4CrossingStyleChange?.(style)}
      onDifferentialPairChange={(state) => onE4DifferentialPairChange?.(state)}
      onScreenChange={(state) => onE4ScreenChange?.(state)}
      onClearGroup={() => onE4ClearGroup?.()}
      detached={e4Detached}
      onDetachedChange={onE4DetachedChange}
      onReroute={onE4Reroute}
    />
  ) : null;

  const changeView = (nextView: HarnessEditorView) => {
    if (controlledView === undefined) setLocalView(nextView);
    onViewChange?.(nextView);
    if (nextView === "e4" && tool.startsWith("dimension")) setTool("select");
  };

  const changeObjects = (nextObjects: readonly EditorSceneObject[]) => {
    if (controlledObjects === undefined) setLocalObjects(nextObjects);
    onObjectsChange?.(nextObjects);
  };

  const changeLayers = (nextLayers: readonly EditorLayer[]) => {
    if (controlledLayers === undefined) setLocalLayers(nextLayers);
    onLayersChange?.(nextLayers);
  };

  const isolateCopyObjects = (objectIds: readonly string[]) => {
    if (!localCopyControls || !objectIds.length) return;
    if (localCopyControls.onObjectsIsolate) {
      localCopyControls.onObjectsIsolate(objectIds);
      return;
    }
    const isolatedIds = new Set(objectIds);
    const layerIds = new Set(objects.filter(object => isolatedIds.has(object.id)).map(object => object.layerId));
    if (!layerIds.size) return;
    if (layers.some(layer => layerIds.has(layer.id) && !layer.visible)) {
      changeLayers(layers.map(layer => layerIds.has(layer.id) ? { ...layer, visible: true } : layer));
    }
    localCopyControls.onHiddenObjectIdsChange(hiddenIdsForIsolatedObjects(objects, objectIds));
    localCopyControls.onBackgroundOpacityChange(0);
  };

  const showAllCopyObjects = () => {
    if (!localCopyControls) return;
    if (layers.some(layer => !layer.visible)) changeLayers(layers.map(layer => ({ ...layer, visible: true })));
    localCopyControls.onHiddenObjectIdsChange([]);
  };

  const selectObject = (objectId: string | null, additive = false) => {
    const currentIds = selectedObjectIds.filter((id) => objects.some((object) => object.id === id));
    let nextIds: readonly string[];
    if (!objectId) {
      nextIds = [];
    } else if (!additive) {
      nextIds = [objectId];
    } else if (currentIds.includes(objectId)) {
      nextIds = currentIds.filter((id) => id !== objectId);
    } else {
      nextIds = [...currentIds, objectId];
    }
    const primaryId = nextIds.at(-1) ?? null;
    if (controlledSelectedObjectId === undefined) setLocalSelectedObjectId(primaryId);
    if (controlledSelectedObjectIds === undefined) setLocalSelectedObjectIds(nextIds);
    onSelectedObjectChange?.(primaryId);
    onSelectedObjectIdsChange?.(nextIds);
  };

  const selectObjectGroup = (objectIds: readonly string[], mode:EditorSelectionMode="replace") => {
    const incoming=reconcileWorkspaceGroupSelection(objectIds, objects).objectIds;
    const current=selectedObjectIds.filter(id=>objects.some(object=>object.id===id));
    const nextIds=mode==="replace"?incoming:mode==="add"?[...new Set([...current,...incoming])]:current.filter(id=>!incoming.includes(id)).concat(incoming.filter(id=>!current.includes(id)));
    const next = reconcileWorkspaceGroupSelection(nextIds, objects);
    if (controlledSelectedObjectId === undefined) setLocalSelectedObjectId(next.primaryObjectId);
    if (controlledSelectedObjectIds === undefined) setLocalSelectedObjectIds(next.objectIds);
    onSelectedObjectChange?.(next.primaryObjectId);
    onSelectedObjectIdsChange?.(next.objectIds);
  };

  const activateCatalogItem = (item: EditorCatalogItem, point?: EditorPoint) => {
    onCatalogItemActivate?.(item, point);
    if (controlledObjects !== undefined) return;
    const placement = point ?? { x: 385, y: 245 };
    const nextObject: EditorSceneObject = {
      id: `${item.id}-${objects.length + 1}`,
      layerId: "components",
      kind: "connector",
      label: item.title,
      x: Math.round(placement.x - 55),
      y: Math.round(placement.y - 34),
      width: 110,
      height: 68,
      color: item.accent,
    };
    changeObjects([...objects, nextObject]);
    selectObject(nextObject.id);
  };

  const droppedCatalogItem = (itemId: string, point: EditorPoint) => {
    const item = catalogItems.find((candidate) => candidate.id === itemId);
    if (item) activateCatalogItem(item, point);
  };

  const viewportObjects = useMemo(
    () => objects.filter((object) => (view !== "e4" || object.kind !== "dimension") && !localCopyControls?.hiddenObjectIds.includes(object.id)),
    [objects, view, localCopyControls?.hiddenObjectIds],
  );
  const rememberViewportSize = useCallback((size: EditorViewportSize) => {
    setViewportSize((current) => current.width === size.width && current.height === size.height ? current : size);
  }, []);
  const fitView = () => setCamera((current) => fitEditorCameraToBounds(
    current,
    getEditorSceneBounds(viewportObjects, layers, view, e4Overlays,
      componentTemplateViewInstances, resolveComponentTemplateAssetUrl, cables),
    viewportSize,
  ));
  const setZoom = (zoom: number) => setCamera((current) =>
    zoomEditorCameraAt(current, { x: viewportSize.width / 2, y: viewportSize.height / 2 }, zoom));
  const changeZoom = (factor: number) => setCamera((current) =>
    zoomEditorCameraAt(current, { x: viewportSize.width / 2, y: viewportSize.height / 2 }, current.zoom * factor));
  const focusObject = (objectId: string) => {
    selectObject(objectId);
    const object = objects.find((candidate) => candidate.id === objectId);
    if (!object) return;
    setCamera((current) => ({
      ...current,
      offsetX: viewportSize.width / 2 - (object.x + object.width / 2) * current.zoom,
      offsetY: viewportSize.height / 2 - (object.y + object.height / 2) * current.zoom,
    }));
  };
  const diagnosticOverlay = view === "e4" && diagnostics.length > 0 ? (
    <section className="he-e4-diagnostic-list" aria-label="Ошибки схемы Э4">
      <header><strong>Ошибки</strong><span>{diagnostics.length}</span></header>
      {diagnostics.map((diagnostic) => (
        <button type="button" key={diagnostic.id} onClick={() => focusObject(diagnostic.objectId)}>
          <span aria-hidden="true">!</span>
          <span><strong>{diagnostic.label}</strong><small>{diagnostic.message}</small></span>
        </button>
      ))}
    </section>
  ) : undefined;
  const incompatibleCableIds = view === "drawing"
    ? getVisibleCableSheathScene(cables, viewportObjects, layers).incompatibleCableIds
    : [];
  const cableSheathWarning = incompatibleCableIds.length > 0
    ? `Общая оболочка не показана для ${incompatibleCableIds.length === 1
      ? `кабеля ${incompatibleCableIds[0]}`
      : `кабелей ${incompatibleCableIds.join(", ")}`}: у жил нет однозначного общего участка.`
    : null;

  useEffect(() => {
    if (!revealRequest) return;
    const bounds = getEditorSceneBounds(objects.filter(object => revealRequest.objectIds.includes(object.id)), layers, view);
    if (bounds) setCamera(current => fitEditorCameraToBounds(current, bounds, viewportSize));
  }, [revealRequest]);

  return (
    <section className="harness-editor" data-harness-id={harnessId} aria-label={`Редактор жгута ${harnessDesignation}`} onFocusCapture={selectEditorTextField}>
      <header className="he-header">
        {!localCopyControls ? (
          <HarnessSectionNavigation
            active={view}
            onNavigate={async section => {
              if (section === "e4" || section === "drawing") changeView(section);
              else if (section === "route" && onRouteRequest) await onRouteRequest();
              else await onSectionChange?.(section);
            }}
            onHome={onClose}
          />
        ) : (
          <div className="he-header-identity">
            <div>
              <span>ПРОЕКТ / ЖГУТ</span>
              <strong>{harnessDesignation}</strong>
            </div>
          </div>
        )}
        {localCopyControls && <button
          className={`he-save-state ${saveState}`}
          type="button"
          onClick={onSaveRequest}
          disabled={!onSaveRequest || saveState === "saved" || saveState === "saving"}
          title={saveState === "error" ? "Повторить сохранение" : "Сохранить сейчас"}
        >
          <span aria-hidden="true" />Сохранить фрагмент
        </button>}
        {localCopyControls && <button className="he-back" type="button" onClick={localCopyControls.onCancel}>Отмена</button>}
      </header>

      <div className={`he-workspace ${view === "drawing" ? "he-workspace-drawing" : ""} ${utilityPanelOpen ? "he-utility-open" : "he-utility-closed"} ${inspectorOpen ? "he-inspector-open" : "he-inspector-closed"} ${materialPanelOpen ? "he-material-open" : "he-material-closed"}`}>
        <aside className={`he-utility-panel ${utilityPanelOpen ? "open" : "closed"}`} aria-label="Параметры и документы">
          <button className="he-utility-toggle" type="button" title={utilityPanelOpen ? "Скрыть панель" : "Показать параметры и документы"} onClick={() => { setUtilityPanelOpen(value => !value); if (window.innerWidth <= 1100) setInspectorOpen(false); }} aria-expanded={utilityPanelOpen} aria-controls="he-utility-content">
            <span aria-hidden="true">{utilityPanelOpen ? "‹" : "›"}</span>
            <span>{utilityPanelOpen ? "Скрыть панель" : "Параметры"}</span>
          </button>
          {localCopyControls && <button
            className="he-copy-objects-toggle"
            type="button"
            aria-label={materialPanelOpen ? "Скрыть объекты" : "Показать объекты"}
            title={materialPanelOpen ? "Скрыть объекты" : "Показать объекты"}
            aria-expanded={materialPanelOpen}
            aria-controls="he-material-panel"
            onClick={() => {
              setMaterialPanelOpen(open => !open);
              if (window.innerWidth <= 1100) {
                setInspectorOpen(false);
                setUtilityPanelOpen(false);
              }
            }}
          >Объекты</button>}
          <div className="he-utility-content" id="he-utility-content" hidden={!utilityPanelOpen}>
            {!localCopyControls && <button
              className={`he-save-state he-save-state-utility ${saveState}`}
              type="button"
              onClick={onSaveRequest}
              disabled={!onSaveRequest || saveState === "saved" || saveState === "saving"}
              title={saveState === "error" ? "Повторить сохранение" : "Сохранить сейчас"}
            >
              <span aria-hidden="true" />{saveLabels[saveState]}
            </button>}
            {!localCopyControls && <section className="he-utility-section he-controls-section" aria-label="Панели редактора">
              <button type="button" className="ui-control he-control-action" aria-pressed={materialPanelOpen} onClick={() => { setMaterialPanelOpen(open => !open); if (window.innerWidth <= 1100) { setInspectorOpen(false); } }}>
                {materialPanelOpen ? "Скрыть объекты" : "Показать объекты"}
              </button>
              <button type="button" className="ui-control he-control-action" aria-pressed={inspectorOpen} onClick={() => { setInspectorOpen(open => !open); if (window.innerWidth <= 1100) { setMaterialPanelOpen(false); } }}>
                {inspectorOpen ? "Скрыть свойства и слои" : "Показать свойства и слои"}
              </button>
            </section>}
            <div className="he-view-options">
              <InfoHint>Перемещайте точки свободно. Удерживайте Ctrl для привязки к углам с шагом 30°. Shift меняет редактирование соседних плеч; Ctrl и Shift можно удерживать вместе.</InfoHint>
            </div>
            {localCopyControls && <section className="he-utility-section he-controls-section" aria-label="Видимость фрагмента">
              <h3>Видимость фрагмента</h3>
              <button type="button" className="ui-control he-control-action" onClick={() => localCopyControls.onHiddenObjectIdsChange(objects.filter(object => selectedObjectIds.includes(object.id)).map(object => object.id).length
                ? [...new Set([...localCopyControls.hiddenObjectIds, ...selectedObjectIds])] : localCopyControls.hiddenObjectIds)} disabled={!selectedObjectIds.length}>Скрыть выбранные</button>
              <button type="button" className="ui-control he-control-action" onClick={() => isolateCopyObjects(selectedObjectIds)} disabled={!selectedObjectIds.length}>Только выбранные</button>
              <button type="button" className="ui-control he-control-action" onClick={showAllCopyObjects}>Показать все</button>
              <label>Фон жгута: {Math.round(localCopyControls.backgroundOpacity * 100)}%
                <input type="range" min="0" max="100" value={Math.round(localCopyControls.backgroundOpacity * 100)} onChange={event => localCopyControls.onBackgroundOpacityChange(Number(event.target.value) / 100)} />
              </label>
            </section>}
            {documentActions}
          </div>
        </aside>
        <EditorToolbar
          view={view}
          activeTool={tool}
          zoom={camera.zoom}
          selectedGraphic={selectedGraphic}
          canPasteGraphic={canPasteGraphic}
          onCopy={onGraphicCopy}
          onPaste={onGraphicPaste}
          onDelete={onGraphicDelete}
          onUndo={onUndo}
          angleStep={angleStep}
          onAngleStepChange={onAngleStepChange}
          snaps={drawingSnaps}
          onSnapsChange={onDrawingSnapsChange}
          onToolChange={setTool}
          onZoomIn={() => changeZoom(1.2)}
          onZoomOut={() => changeZoom(1 / 1.2)}
          onZoomChange={setZoom}
          onFitView={fitView}
        />
        <CanvasViewport
          view={view}
          tool={tool}
          camera={camera}
          objects={drawingWindows && view==="drawing"?viewportObjects.filter(o=>o.kind!=="drawing-table"):viewportObjects}
          layers={layers}
          selectedObjectId={selectedObjectId}
          selectedObjectIds={selectedObjectIds}
          highlightedObjectIds={highlightedObjectIds}
          foregroundWireIds={foregroundWireIds}
          backgroundObjects={backgroundObjects}
          backgroundOpacity={backgroundOpacity}
          cables={cables}
          e4Overlays={e4Overlays}
          e4RoutingMode={e4RoutingMode}
          componentTemplateViewInstances={componentTemplateViewInstances}
          resolveComponentTemplateAssetUrl={resolveComponentTemplateAssetUrl}
          overlay={e4WireMenu}
          diagnosticOverlay={diagnosticOverlay}
          onPipeIntervalSelect={onPipeIntervalSelect} onCoveringDrag={onCoveringDrag}
          onDimensionCreate={(...args)=>{onDimensionCreate?.(...args);setTool("select");}}
          onPositionRailCreate={(...args)=>{onPositionRailCreate?.(...args);setTool("select");}}
          onGraphicCreate={onGraphicCreate}
          drawingSnaps={drawingSnaps}
          drawingAngleStep={drawingAngleStep}
          onCameraChange={setCamera}
          onViewportSizeChange={rememberViewportSize}
          onObjectSelect={selectObject}
          onObjectGroupSelect={selectObjectGroup}
          onObjectsIsolate={localCopyControls ? isolateCopyObjects : undefined}
          objectProperties={objectProperties}
          onObjectPick={onObjectPick}
          onObjectPickCancel={onObjectPickCancel}
          onRelatedObjectsSelect={onRelatedObjectsSelect}
          onObjectMove={onObjectMove}
          onDrawingScale={onDrawingScale}
          onDrawingMove={onDrawingMove}
          onObjectMovePreview={onObjectMovePreview}
          onObjectEditRequest={onObjectEditRequest}
          onWireConnect={onWireConnect}
          onWireReconnect={onWireReconnect}
          onWireConnectToWire={onWireConnectToWire}
          onWireReconnectToWire={onWireReconnectToWire}
          onE4WireSegmentMove={onE4WireSegmentMove}
          onE4WireRoutePointRemove={onE4WireRoutePointRemove}
          onE4WireLabelPositionChange={onE4WireLabelPositionChange}
          onE4ScreenPositionChange={onE4ScreenPositionChange}
          onWireToolRequest={() => setTool("wire")}
          onWireRoutePointPreview={onWireRoutePointPreview}
          onWireRoutePointMove={onWireRoutePointMove}
          onWireRoutePointRemove={onWireRoutePointRemove}
          onCanvasDoubleClick={onCanvasDoubleClick}
          onPhysicalContextAction={onPhysicalContextAction}
          onPhysicalNodesConnect={onPhysicalNodesConnect}
          onPhysicalNodeConnectToSegment={onPhysicalNodeConnectToSegment}
          onFreeWireEndpointMove={onFreeWireEndpointMove ?? onDrawingEndEndpointChange}
          onFreeWireEndpointsXChange={onFreeWireEndpointsXChange ?? onDrawingEndBulkXChange}
          onFreeWireEndpointPreview={onFreeWireEndpointPreview}
          onDetachedPairAction={onDetachedPairAction}
          onFreeWireEndStyleRequest={requestFreeWireEndStyle}
          onCatalogDrop={droppedCatalogItem}
          inlineEditor={canvasEditor}
        />
        {drawingWindows && <div className="he-table-window-layer">{drawingWindows(camera)}</div>}
        {(previewMessage || cableSheathWarning) && <div className="he-routing-preview-error" role="status">
          {previewMessage || cableSheathWarning}
        </div>}

        <aside className="he-right-panel" aria-label="Настройки редактора" hidden={!inspectorOpen}>
          <div className="he-inspector-tabs" role="tablist" aria-label="Панель объекта">
            <button type="button" role="tab" aria-selected={inspectorTab === "properties"} className={inspectorTab === "properties" ? "active" : ""} onClick={() => setInspectorTab("properties")}>Свойства</button>
            <button type="button" role="tab" aria-selected={inspectorTab === "layers"} className={inspectorTab === "layers" ? "active" : ""} onClick={() => setInspectorTab("layers")}>Слои <span>{layers.length}</span></button>
          </div>
          <div className="he-inspector-content">
            {view === "drawing" && onDrawingEndStyleChange && onDrawingEndStylesChange && onDrawingEndEndpointChange && <FreeWireEndsPanel objects={objects} selectedIds={selectedObjectIds} disabled={selectedWireIds.some(id => layers.some(layer => layer.locked && layer.id === objects.find(object => object.id === id)?.layerId))} onStyle={onDrawingEndStyleChange} onStyles={onDrawingEndStylesChange} onEndpoint={onDrawingEndEndpointChange} onBulkX={onDrawingEndBulkXChange} focusTarget={freeWireEndFocus} />}
            {typeof relationPanel==="function"?relationPanel(tool,setTool):relationPanel}
            {inspectorTab === "properties" ? (
              propertyInspector ?? (
                <ObjectInspector
                  view={view}
                  selectedObject={selectedObject}
                  disabled={selectedLayer?.locked === true}
                  onChange={(objectId, patch) => changeObjects(updateEditorObject(objects, objectId, patch))}
                  onWireMaterialClear={onWireMaterialClear}
                  wireMaterialOptions={wireMaterialOptions}
                  onWireMaterialSelect={onWireMaterialSelect}
                  wireStripProfiles={selectedWireStripProfiles}
                  activeWireStripEnd={activeWireStripEnd}
                  onActiveWireStripEndChange={onActiveWireStripEndChange}
                  onWireStripProfileClear={onWireStripProfileClear}
                  terminalCorrections={terminalCorrections}
                  onTerminalCorrectionApply={onTerminalCorrectionApply}
                />
              )
            ) : (
              <><LayersPanel
                layers={layers}
                onVisibilityToggle={(layerId) => changeLayers(toggleLayerVisibility(layers, layerId))}
                onLockToggle={(layerId) => changeLayers(toggleLayerLock(layers, layerId))}
                onMove={(layerId, targetIndex) => changeLayers(moveLayer(layers, layerId, targetIndex))}
              />{localCopyControls && <section className="he-copy-visibility" aria-label="Видимость объектов">
                <div className="he-copy-visibility-heading">
                  <h3>Служебные объекты</h3>
                  <button
                    type="button"
                    className="ui-control he-copy-action"
                    onClick={showAllCopyObjects}
                    disabled={!localCopyControls.hiddenObjectIds.length && layers.every(layer => layer.visible)}
                  >Показать все</button>
                </div>
                <div className="he-copy-object-list">
                  {objects.filter(object => !materialObjectGroup(object.kind)).map(object => {
                    const visible = !localCopyControls.hiddenObjectIds.includes(object.id);
                    const label = object.label || object.id;
                    return <div className="he-copy-object-row" key={object.id}>
                      <label className="he-copy-object-name">
                        <input
                          type="checkbox"
                          checked={visible}
                          onChange={event => {
                            if (event.target.checked && layers.some(layer => layer.id === object.layerId && !layer.visible)) {
                              changeLayers(layers.map(layer => layer.id === object.layerId ? { ...layer, visible: true } : layer));
                            }
                            localCopyControls.onHiddenObjectIdsChange(event.target.checked
                              ? localCopyControls.hiddenObjectIds.filter(id => id !== object.id)
                              : [...localCopyControls.hiddenObjectIds, object.id]);
                          }}
                        />
                        <span title={label}>{label}</span>
                      </label>
                    </div>;
                  })}
                </div>
              </section>}</>
            )}
          </div>
        </aside>

        <aside id="he-material-panel" className="he-material-panel" aria-label="Список материальных объектов" hidden={!materialPanelOpen}>
          <MaterialObjectsPanel
            objects={objects}
            hiddenObjectIds={localCopyControls?.hiddenObjectIds ?? []}
            selectedObjectIds={selectedObjectIds}
            onObjectSelect={selectObject}
            onVisibilityChange={localCopyControls ? (objectId, visible) => {
              const object = objects.find(item => item.id === objectId);
              if (visible && object && layers.some(layer => layer.id === object.layerId && !layer.visible)) {
                changeLayers(layers.map(layer => layer.id === object.layerId ? { ...layer, visible: true } : layer));
              }
              localCopyControls.onHiddenObjectIdsChange(visible
                ? localCopyControls.hiddenObjectIds.filter(id => id !== objectId)
                : [...new Set([...localCopyControls.hiddenObjectIds, objectId])]);
            } : undefined}
          />
        </aside>

        {view !== "drawing" && <CatalogDock
          items={catalogItems}
          sources={catalogSources}
          selectedSourceId={selectedCatalogSourceId}
          query={catalogQuery}
          loadState={catalogLoadState}
          message={catalogMessage}
          hasMore={catalogHasMore}
          expanded={catalogExpanded}
          onExpandedChange={setCatalogExpanded}
          onActivate={activateCatalogItem}
          onSourceChange={onCatalogSourceChange}
          onQueryChange={onCatalogQueryChange}
          onLoadMore={onCatalogLoadMore}
          onRetry={onCatalogRetry}
        />}
      </div>
    </section>
  );
}

export { defaultCatalog as harnessEditorDemoCatalog, defaultLayers as harnessEditorDemoLayers, defaultObjects as harnessEditorDemoObjects };
