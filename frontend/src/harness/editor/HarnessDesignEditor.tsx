import {coveringMaterialChanged,createGlobalCoveringPreparer} from "./global-covering-materials";
import {useCoveringAssets,withCoveringTextureUrls} from "./covering-assets";
import { orderPhysicalScene, physicalTopologyScene } from "./physical-scene";
import { volumeShadingEligible } from "./volume-shading";
import { unprojectPipeBundlePoint, pipeBundleNodePoint } from "./pipe-bundle-projection";
import { resolvePhysicalRoutePointCommand } from "./physical-route-point-command";
import { detachedWireBendInsertionIndex } from "./detached-wire-editing";
import { coveringScene, moveCovering, type CoveringDragPart } from "./covering-layout";
import { drawingWireWidth, drawingReferenceDiameter } from "./drawing-thickness";
import { buildDrawingPerimeters, type DrawingPerimeters } from "./drawing-object-perimeter";
import {SpecificationItemsPanel} from "./SpecificationItemsPanel";
import { DrawingDimensionsPanel } from "./DrawingDimensionsPanel";
import { drawingDimensionScene, dimensionRouteKey, toggleDrawingDimensions } from "./drawing-dimensions";
import { projectComponentTemplateView } from "./component-template-view-renderer";
import { materializePlacementRows } from "./component-template-placement";
import { routeCutReadiness } from "../manufacturing/route-cut-readiness";
import { DrawingTableWindows } from "./DrawingTableWindows";
import { CONTACT_SIDE_VIEW_PLACEMENT_ID, drawingLocalDirection, drawingLocalDirectionVector, drawingLocalPoint, drawingScale, DRAWING_VIEW_PLACEMENT_ID } from "./drawing-scale";
import { DrawingScaleControl } from "./DrawingScaleControl";
import { DrawingDocumentsPanel } from "./DrawingDocumentsPanel";
import { addDrawingPositions, createPositionRail, drawingDocumentScene, moveDrawingAnnotation, reconcileDrawingDocuments, setDrawingPositionsVisibility } from "./drawing-documents";
import { alignDrawingConnectorIndexes, drawingObjectIndexScene, moveDrawingIndex } from "./drawing-indices";
import { type PhysicalCovering, coveringMaterial, standardCovering, standardCoveringOver } from "./physical-coverings";
import { PhysicalTopologyPanel } from "./PhysicalTopologyPanel";
import { routePhysicalWires } from "./physical-wire-routing";
import { physicalWireDisplay, physicalWirePoints } from "./physical-wire-geometry";
import { ensureConnectorExits, branchPhysicalSegment, connectPhysicalNodeToSegment } from "./physical-topology";
import { physicalNodePoint } from "./physical-ports";
import { joiningPipeEndpoint, joiningPipeExit, migrateJoiningPipes } from "./physical-joining-pipes";

import { projectE4DrawingCompanions } from "./component-template-view-renderer";
import { freeTwistedPairPaths } from "./drawing-twisted-pair";
import { Component, useCallback, useEffect, useMemo, useRef, useState, type ErrorInfo, type ReactNode } from "react";
import type { LocalSession } from "../local-session";
import type { RuntimeConfig } from "../runtime-config";
import { applyEditorCommand, createWire, e4RoutingIssues, type EditorCommand } from "./commands";
import { InfoHint } from "../InfoHint";
import { drawingBendRadius } from "./drawing-route-path";
import { DrawingObjectProperties } from "./DrawingObjectProperties";
import { JoiningPipeEditor, type JoiningPipeDraft, beginJoiningPipe, joiningPipeDraftTopology, joiningPipeDraftHighlights, toggleJoiningPipeMember } from "./JoiningPipeEditor";
import { DocumentIcon } from "./DocumentIcon";
import { DrawingRangeControl } from "./DrawingRangeControl";
import { defaultDrawingSnaps, type DrawingSnaps } from "../component-library/drawing-geometry";
import { terminalArticleLabel } from "./terminal-article-label";
import { refreshedTemplateTerminalCatalog } from "./template-terminal-catalog";
import {
  createHarnessDesignApi,
  parseRecoverableHarnessDesignContent,
  type HarnessDesignApi,
  type HarnessDesignResource,
} from "./design-api";
import { DesignSaveCoordinator } from "./design-save-coordinator";
import {
  browserRecoveryStorage,
  readHarnessDesignRecoveryDraft,
  removeHarnessDesignRecoveryDraft,
  writeHarnessDesignRecoveryDraft,
} from "./design-recovery-draft";
import { createDesignRecoveryApi, DesignRecoverySession, type ServerRecoveryDraft } from "./design-recovery-api";
import {
  createComponentPlacementApi,
  componentPlacementRequest,
  type PlaceComponentRequest,
  type ProjectComponentPlacementGraph,
  type ProjectComponentSnapshotResource,
} from "./component-placement-api";
import { createComponentTemplateApi, type ComponentTemplateApi } from "../component-library/component-template-api";
import { isTemplateContentV3, isTemplateContentV4, isTemplateContentV5 } from "../component-library/template-content";
import {
  createConnectorInstanceFromComponentTemplateV3,
  firstPlaceableArticleVariantId,
  rematerializeComponentTemplateConnectorArticle,
} from "./component-template-placement";
import {
  materializedContactWorldRepresentation,
  selectMaterializedContactRepresentation,
} from "./materialized-contact-representation";
import type { ComponentTemplateViewInstance } from "./component-template-view-renderer";
import { useEditorReferenceCatalog, useTerminalArticleLookup, useWireDatabaseLookup } from "./editor-reference-catalog";
import { hydrateTerminalDetails, resolveTerminalDetails, wireEndTerminalCorrections } from "./terminal-details";
import type { EditorCatalogItem, EditorLayer as UiLayer, EditorSceneObject, HarnessEditorView } from "./editor-types";
import { HarnessEditorWorkspace, type EditorSaveState } from "./HarnessEditorWorkspace";
import type { HarnessSectionId } from "./HarnessSectionNavigation";
import { CableSelectionPanel } from "./CableSelectionPanel";
import { E4ConnectorInspector } from "./E4ConnectorInspector";
import { resolveConnectedWireMaterials } from "./resolve-wire-materials";
import { collectE4Diagnostics } from "./e4-diagnostics";
import {
  builtInConnectorSeries,
  createBuiltInConnectorInstance,
} from "./connector-series-demo";
import type { E4DifferentialPairState, E4ScreenState } from "./e4-wire-selection-state";
import { builtInWireColors, createCustomWireColor, resolveWireColorHex } from "./wire-reference-catalog";
import { buildWireStripProfileGeometry } from "./wire-strip-profile-geometry";
import { createEditorHistory, executeEditorCommand, redoEditorCommand, undoEditorCommand, type EditorHistory } from "./history";
import {
  connectorContactPosition,
  connectorE4Contacts,
  connectorE4TableGeometry,
  connectorContactName,
  calculateWireCutLength,
  createJunctionEndpoint,
  createScreenEndpoint,
  createOrthogonalE4Route,
  createEmptyHarnessDesign,
  findWireEndpoint,
  isJunctionEndpoint,
  isScreenEndpoint,
  wireEndpointE4Anchor,
  type EditorLayer,
  type HarnessDesignDocument,
  type WireEndpoint,
  type WireEndStripProfiles,
  type WireStripProfileBinding,
} from "./model";

import { buildHarnessSelectionIndex, resolveHarnessSelection } from "./harness-selection";
import { HarnessRelationsPanel } from "./HarnessRelationsPanel";
import { ConnectionTableSettings } from "./ConnectionTableSettings";
import { useConnectionTableSettings } from "./connection-table-settings";

export interface HarnessDesignEditorProps {
  readonly config: RuntimeConfig;
  readonly session: LocalSession;
  readonly projectId: string;
  readonly harnessId: string;
  readonly harnessDesignation: string;
  readonly initialView: HarnessEditorView;
  readonly harnessQuantity?: number;
  readonly initialReveal?: { readonly projectId: string; readonly harnessId: string; readonly objectId: string };
  readonly apiOverride?: HarnessDesignApi;
  readonly componentPlacementApiOverride?: ReturnType<typeof createComponentPlacementApi>;
  readonly onClose?: () => void;
  readonly onViewChange?: (view: HarnessEditorView) => void;
  readonly onRouteRequest?: () => void;
  readonly onSectionChange?: (section: HarnessSectionId) => void | Promise<void>;
  /** Edits an independent drawing copy in memory until the operator explicitly saves it. */
  readonly localCopy?: {
    readonly initialDocument: HarnessDesignDocument;
    readonly hiddenObjectIds?: readonly string[];
    readonly backgroundOpacity?: number;
    readonly onSave: (document: HarnessDesignDocument, hiddenObjectIds: readonly string[], backgroundOpacity: number) => void;
    readonly onCancel: () => void;
    readonly onHiddenObjectIdsChange?: (ids: readonly string[]) => void;
    readonly onBackgroundOpacityChange?: (opacity: number) => void;
    readonly onDraftChange?: (document: HarnessDesignDocument, hiddenObjectIds: readonly string[], backgroundOpacity: number) => void;
    /** Creates a detached material fragment from the current working copy. */
    readonly onIsolateObjects?: (document: HarnessDesignDocument, objectIds: readonly string[], scene?: readonly EditorSceneObject[]) => HarnessDesignDocument | null;
  };
}

export type ProjectComponentSnapshotLookup = ReadonlyMap<string, ProjectComponentSnapshotResource>;

/** Resolves every project placement to the exact immutable snapshot returned for this harness. */
export function buildProjectComponentSnapshotLookup(
  graph: ProjectComponentPlacementGraph,
): ProjectComponentSnapshotLookup {
  const snapshots = new Map(graph.snapshots.map((snapshot) => [snapshot.snapshotId, snapshot]));
  const result = new Map<string, ProjectComponentSnapshotResource>();
  for (const placement of graph.placements) {
    const snapshot = snapshots.get(placement.snapshotId);
    if (!snapshot) throw new Error("Для библиотечного компонента отсутствует закреплённый снимок.");
    if (result.has(placement.placementId)) {
      throw new Error("Сервер вернул повторяющееся размещение библиотечного компонента.");
    }
    result.set(placement.placementId, snapshot);
  }
  return result;
}

/** Keeps a graph response from an editor generation that is no longer visible out of current state. */
export function acceptProjectComponentSnapshotLookup(
  current: ProjectComponentSnapshotLookup,
  graph: ProjectComponentPlacementGraph,
  responseGeneration: number,
  currentGeneration: number,
): ProjectComponentSnapshotLookup {
  return responseGeneration === currentGeneration
    ? buildProjectComponentSnapshotLookup(graph)
    : current;
}

/** Builds render inputs only when the editable instance still matches its project-owned snapshot. */
export function buildComponentTemplateViewInstances(
  document: HarnessDesignDocument,
  snapshotsByPlacement: ProjectComponentSnapshotLookup,
): readonly ComponentTemplateViewInstance[] {
  return document.connectors.flatMap((connector): readonly ComponentTemplateViewInstance[] => {
    const binding = connector.libraryBinding;
    const snapshot = snapshotsByPlacement.get(connector.id);
    if (binding?.mode !== "template" || !snapshot ||
        (!isTemplateContentV3(snapshot.content) && !isTemplateContentV4(snapshot.content) && !isTemplateContentV5(snapshot.content))) return [];
    const articleVariant = snapshot.content.articleVariants.find((candidate) => candidate.id === binding.articleVariantId);
    const exactVersion = binding.templateId === snapshot.sourceTemplateId &&
      binding.templateVersion === snapshot.sourceVersion &&
      binding.versionSha256 === snapshot.sourceVersionSha256;
    const exactArticle = articleVariant?.sourceId.trim().normalize("NFC").toLowerCase() === binding.article.sourceId &&
      articleVariant.entityType.trim().normalize("NFC").toLowerCase() === binding.article.entityType &&
      articleVariant.articleKey === binding.article.articleKey;
    if (!exactVersion || !exactArticle) return [];
    try {
      const materialized = createConnectorInstanceFromComponentTemplateV3({
        templateId: snapshot.sourceTemplateId,
        version: snapshot.sourceVersion,
        versionSha256: snapshot.sourceVersionSha256,
        code: snapshot.code,
        name: snapshot.name,
        articleBindings: snapshot.articleBindings,
        assets: snapshot.assets,
        content: snapshot.content,
      }, {
        legacyNumberingPreview: binding.contactNumbering === undefined,
        id: connector.id,
        designation: connector.designation,
        articleVariantId: binding.articleVariantId,
        e4Position: connector.positions.e4,
        drawingPosition: connector.positions.drawing,
        layerIds: connector.layerIds,
      });
      const sortedContacts = (contacts: typeof binding.snapshot.contacts) =>
        [...contacts].sort((a,b)=>a.logicalContactId.localeCompare(b.logicalContactId));
      const exactMaterialization = materialized.libraryBinding?.mode === "template" &&
        JSON.stringify({...binding.snapshot,contacts:sortedContacts(binding.snapshot.contacts)}) ===
        JSON.stringify({...materialized.libraryBinding.snapshot,contacts:sortedContacts(materialized.libraryBinding.snapshot.contacts)});
      if (!exactMaterialization) return [];
    } catch {
      return [];
    }
    const contactWireColors: Record<string,string> = {};
    for (const wire of document.wires) for (const endpoint of [wire.from, wire.to]) if (endpoint.connectorId === connector.id) {
      const contact = connector.contacts.find(item => item.id === endpoint.contactId);
      if (contact?.logicalContactId && wire.color && !contactWireColors[contact.logicalContactId]) {
        contactWireColors[contact.logicalContactId] = wire.color;
        if (snapshot.content.schemaVersion === 5) {
          const logicalId = snapshot.content.drawingContactBindings?.find(binding => binding.seriesRowId === contact.logicalContactId)?.logicalContactId;
          if (logicalId) contactWireColors[logicalId] = wire.color;
        }
      }
    }
    return [{
      objectId: connector.id,
      snapshotId: snapshot.snapshotId,
      articleVariantId: binding.articleVariantId,
      content: snapshot.content,
      ...(Object.keys(contactWireColors).length ? {contactWireColors} : {}),
      ...(connector.drawingPlacements ? {drawingPlacements:connector.drawingPlacements} : {}),
    }];
  });
}

interface PendingComponentPlacement {
  readonly body: PlaceComponentRequest;
  readonly nextHistory: EditorHistory;
  readonly connectorId: string;
  /** Prevents a late response for a previously opened harness changing the current editor. */
  readonly loadGeneration: number;
}

interface HarnessEditorErrorBoundaryProps {
  readonly children: ReactNode;
  readonly onRecover: () => void;
  readonly onError: (message: string) => void;
}

interface HarnessEditorErrorBoundaryState {
  readonly error: Error | null;
}

/** Keeps an unexpected editor render failure visible and recoverable instead of leaving a blank page. */
export class HarnessEditorErrorBoundary extends Component<
  HarnessEditorErrorBoundaryProps,
  HarnessEditorErrorBoundaryState
> {
  override state: HarnessEditorErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): HarnessEditorErrorBoundaryState {
    return { error };
  }

  override componentDidCatch(error: Error, _info: ErrorInfo): void {
    this.props.onError(error.message || "Ошибка отображения редактора.");
  }

  override render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return <div className="he-loading error" role="alert">
      <strong>Редактор не смог отобразить последнее изменение.</strong>
      <span>{this.state.error.message}</span>
      <button type="button" onClick={() => {
        this.setState({ error: null });
        this.props.onRecover();
      }}>Вернуться к редактору</button>
    </div>;
  }
}

export function normalizeEditorSelection(
  selectedIds: readonly string[],
  selectedPrimaryId: string | null,
  availableObjectIds: ReadonlySet<string>,
): { readonly objectIds: readonly string[]; readonly primaryObjectId: string | null } {
  const objectIds = [...new Set(selectedIds)].filter((id) => availableObjectIds.has(id));
  const primaryObjectId = selectedPrimaryId && availableObjectIds.has(selectedPrimaryId)
    ? selectedPrimaryId
    : objectIds.at(-1) ?? null;
  if (primaryObjectId && !objectIds.includes(primaryObjectId)) objectIds.push(primaryObjectId);
  return { objectIds, primaryObjectId };
}

export function selectedEditorDeletionCommands(
  document: HarnessDesignDocument,
  selectedObjectIds: readonly string[],
): readonly EditorCommand[] {
  const selectedIds = new Set(selectedObjectIds);
  return [
    ...(document.drawingDocuments?.graphics?.some(g=>selectedIds.has(g.id))?[{type:"set-drawing-documents" as const,documents:{...document.drawingDocuments,graphics:document.drawingDocuments.graphics.filter(g=>!selectedIds.has(g.id))}}]:[]),
    ...document.physicalTopology?.joiningPipes?.filter(p=>selectedIds.has(p.id)).map((p):EditorCommand=>({type:"remove-physical-segment",segmentId:p.id}))??[],
    ...(document.drawingDocuments && (document.drawingDocuments.specificationItems?.some(i=>selectedIds.has(i.id))||document.drawingDocuments.tables.some(t=>selectedIds.has(t.id))||document.drawingDocuments.leaders.some(l=>selectedIds.has(l.id)||selectedIds.has(`${l.id}:anchor`))||(document.drawingDocuments.rails??[]).some(r=>selectedIds.has(r.id)||selectedIds.has(`${r.id}:start`)||selectedIds.has(`${r.id}:end`))) ? [{type:"set-drawing-documents" as const,documents:{...document.drawingDocuments,specificationItems:document.drawingDocuments.specificationItems?.filter(i=>!selectedIds.has(i.id)),tables:document.drawingDocuments.tables.filter(t=>!selectedIds.has(t.id)),leaders:document.drawingDocuments.leaders.filter(l=>!selectedIds.has(l.id)&&!selectedIds.has(`${l.id}:anchor`)),rails:(document.drawingDocuments.rails??[]).filter(r=>!selectedIds.has(r.id)&&!selectedIds.has(`${r.id}:start`)&&!selectedIds.has(`${r.id}:end`))}}] : []),
    ...document.wires
      .filter((wire) => selectedIds.has(wire.id))
      .map((wire): EditorCommand => ({ type: "remove-wire", wireId: wire.id })),
    ...(document.drawingDocuments?.dimensions?.some(d=>selectedIds.has(d.id))?[{type:"set-drawing-documents" as const,documents:{...document.drawingDocuments,dimensions:document.drawingDocuments.dimensions.filter(d=>!selectedIds.has(d.id))}}]:[]),
    ...document.connectors
      .filter((connector) => selectedIds.has(connector.id))
      .map((connector): EditorCommand => ({ type: "remove-connector", connectorId: connector.id })),
  ];
}

function toUiLayers(document: HarnessDesignDocument, view: HarnessEditorView): readonly UiLayer[] {
  return [...document.views[view].layers]
    .sort((left, right) => right.order - left.order)
    .map((layer) => ({ id: layer.id, label: layer.name, visible: layer.visible, locked: layer.locked }));
}

function contactPointForWire(
  document: HarnessDesignDocument,
  endpoint: WireEndpoint,
  otherEndpoint: WireEndpoint,
  view: HarnessEditorView,
  materializedConnectorIds?: ReadonlySet<string>,
  drawingConnectorIds?: ReadonlySet<string>,
  perimeters?: DrawingPerimeters,
) {
  // Routing, screen ports and painting must share the exact same endpoints,
  // including when the graphical snapshot has not loaded yet.
  if (view === "e4") return wireEndpointE4Anchor(document, endpoint)?.position ?? null;
  if (isJunctionEndpoint(endpoint) || isScreenEndpoint(endpoint)) return findWireEndpoint(document, endpoint, view);
  const connectorId = endpoint.connectorId;
  const contactId = endpoint.contactId;
  const otherConnectorId = isJunctionEndpoint(otherEndpoint) || isScreenEndpoint(otherEndpoint)
    ? ""
    : otherEndpoint.connectorId;
  const connector = document.connectors.find((item) => item.id === connectorId);
  const other = document.connectors.find((item) => item.id === otherConnectorId);
  if (!connector) return null;
  const useMaterialized = connector.libraryBinding?.mode === "template" &&
    (materializedConnectorIds === undefined || materializedConnectorIds.has(connector.id));
  const materialized = useMaterialized
    ? materializedContactWorldRepresentation(connector, contactId, view)
    : null;
  if (materialized) return materialized.position;
  if(view==="drawing"&&useMaterialized&&drawingConnectorIds?.has(connector.id))return null;
  const fallbackConnector = connector.libraryBinding?.mode === "template"
    ? { ...connector, libraryBinding: { mode: "free" as const } }
    : connector;
  const point = connectorContactPosition(fallbackConnector, contactId, view);
  if (!point) return null;
  const height = Math.max(72, 44 + connector.contacts.length * 16);
  const useLeft = other ? other.positions[view].x < connector.positions[view].x : false;
  return {
    x: useLeft ? connector.positions[view].x : connector.positions[view].x + 118,
    y: Math.min(point.y, connector.positions[view].y + height - 12),
  };
}

/** Graph failures are retryable without replacing the editable harness document. */
export function ComponentGraphErrorAlert({
  message,
  onRetry,
}: {
  readonly message: string;
  readonly onRetry: () => void;
}) {
  return <div className="he-save-message" role="alert">
    <span>{message}</span>
    <button type="button" onClick={onRetry}>Повторить загрузку видов</button>
  </div>;
}

export function designToScene(
  document: HarnessDesignDocument,
  view: HarnessEditorView,
  diagnosticObjectIds: ReadonlySet<string> = new Set(),
  materializedConnectorIds?: ReadonlySet<string>,
  quantity=1,
  drawingConnectorIds?: ReadonlySet<string>,
  perimeters?: DrawingPerimeters,
): readonly EditorSceneObject[] {
  const connectors: EditorSceneObject[] = document.connectors.map((connector) => {
    const displayedContacts = view === "e4" ? connectorE4Contacts(connector) : connector.contacts;
    const geometry = view === "e4" ? connectorE4TableGeometry(connector) : null;
    const seriesBinding = connector.libraryBinding?.mode === "series" ? connector.libraryBinding : null;
    const metadata: Record<string, string> = { contactCount: String(connector.contacts.length) };
    if (connector.libraryBinding?.mode === "template" &&
        (materializedConnectorIds === undefined || materializedConnectorIds.has(connector.id))) {
      const materializedContactPoints = displayedContacts.map((contact) => {
        const representation = selectMaterializedContactRepresentation(connector, contact.id, view);
        return representation ? {
          ...(view==="drawing"?drawingLocalPoint(representation,connector.drawingPlacements):{x:representation.x,y:representation.y}),
          direction: view === "drawing"
            ? drawingLocalDirection(representation.direction, connector.drawingPlacements)
            : representation.direction,
          ...(view === "drawing" && connector.drawingPlacements?.some(placement =>
            placement.drawingId === DRAWING_VIEW_PLACEMENT_ID && placement.rotationDegrees)
            ? { directionVector: drawingLocalDirectionVector(representation.direction, connector.drawingPlacements) }
            : {}),
          status: contact.connectionStatus,
        } : null;
      });
      if (view==="drawing" && drawingConnectorIds?.has(connector.id) || materializedContactPoints.some((point) => point !== null)) {
        metadata.materializedContactPoints = JSON.stringify(materializedContactPoints);
      }
    }
    if (view === "e4" && geometry) {
      const columnIds = geometry.columns.map((column) => column.kind === "base"
        ? column.key
        : `custom:${column.id}`);
      const customLabels = Object.fromEntries(geometry.columns.flatMap((column) =>
        column.kind === "custom" ? [[`custom:${column.id}`, column.label]] : []));
      Object.assign(metadata, {
        view: "e4",
        orientation: connector.schematic.orientation === "contacts-left" ? "left" : "right",
        designation: connector.designation,
        libraryCode: seriesBinding
          ? builtInConnectorSeries.find((series) => series.id === seriesBinding.seriesId)?.name ?? seriesBinding.seriesId
          : connector.libraryCode ?? "FREE",
        partNumber: connector.partNumber,
        columns: JSON.stringify(columnIds),
        columnWidths: JSON.stringify(geometry.columns.map(column => column.width)),
        columnLabels: JSON.stringify(customLabels),
        rows: JSON.stringify(displayedContacts.map((contact) => ({
            number: contact.number,
            contactType: contact.contactType,
            circuit: contact.circuit,
            terminal: terminalArticleLabel(contact.terminalArticle),
            name: connectorContactName(connector, contact),
            wire: contact.wire,
            wireSection: contact.wireSection ?? "",
            color: contact.color,
            secondaryColor: contact.secondaryColor ?? "",
            status: contact.connectionStatus,
            customValues: contact.customValues,
          }))),
        ...(diagnosticObjectIds.has(connector.id) ? { diagnostic: "error" } : {}),
      });
    }
    return {
      id: connector.id,
      layerId: connector.layerIds[view],
      kind: "connector" as const,
      label: connector.designation,
      x: connector.positions[view].x,
      y: connector.positions[view].y,
      width: geometry?.width ?? 118,
      height: geometry?.height ?? Math.max(72, 44 + connector.contacts.length * 16),
      color: "#416579",
      metadata,
    };
  });
  const wires: EditorSceneObject[] = document.wires.flatMap((wire, index) => {
    const start = view === "drawing" && wire.drawingEndpoints ? wire.drawingEndpoints.from : contactPointForWire(document, wire.from, wire.to, view, materializedConnectorIds,drawingConnectorIds);
    const end = view === "drawing" && wire.drawingEndpoints ? wire.drawingEndpoints.to : contactPointForWire(document, wire.to, wire.from, view, materializedConnectorIds,drawingConnectorIds);
    if (!start || !end) return [];
    const physicalPoints = view === "drawing" ? physicalWirePoints(document, wire.id, start, end) : null;
    const wireDisplay = view === "drawing" && physicalPoints ? physicalWireDisplay(document,wire.id,start,end) : undefined;
    const roundRoute = wireDisplay && document.physicalTopology?.routes.find(route=>route.wireId===wire.id)?.steps.some(step=>document.physicalTopology?.segments.find(segment=>segment.id===step.segmentId)?.mode==="round");
    const wireWidth = drawingWireWidth(document,wire);
    const points = view === "drawing" ? physicalPoints ?? [start, ...wire.drawingRoute, end] : [start, ...wire.e4Route, end];
    const fromAnchor = view === "e4" ? wireEndpointE4Anchor(document, wire.from) : null;
    const toAnchor = view === "e4" ? wireEndpointE4Anchor(document, wire.to) : null;
    const cutLength = calculateWireCutLength(wire);
    const stripProfileDisplayWarning = view === "drawing"
      ? wireStripProfileDisplayWarning(points, wire.stripProfiles)
      : null;
    const singlePipeRoute = view === "drawing" ? document.physicalTopology?.routes.find(route => route.wireId === wire.id && route.steps.length === 1) : undefined;
    const localVolume = singlePipeRoute ? document.physicalTopology?.segments.find(segment => segment.id === singlePipeRoute.steps[0]!.segmentId)?.volumeShading : undefined;
    return [{
      id: wire.id,
      layerId: wire.layerIds[view],
      kind: "wire" as const,
      label: `${wire.circuit || `W${index + 1}`}${view === "drawing" && !wire.drawingEndpoints && !physicalPoints && !wire.drawingRoute.length ? " · маршрут не задан" : ""}`,
      x: 0,
      y: 0,
      width: 0,
      height: 0,
      color: wire.color,
      points,
      // Isolated routes keep their own editable polyline points, but they still
      // use the drawing bend radius for rendering.  A zero radius made every
      // detached corner sharp and made the radius control appear ineffective.
      ...(view === "drawing" ? {routeRadius: drawingBendRadius(document)} : {}),
      ...(wireDisplay ? {paths:wireDisplay.selectionPaths,visibleWireStrokes:roundRoute||wireDisplay.twisted?wireDisplay.visibleStrokes:wireDisplay.paths.map(points=>({points,width:wireWidth}))} : {}),
      ...(view === "drawing" && wire.stripProfiles ? { stripProfiles: wire.stripProfiles } : {}),
      ...(view === "drawing" && wire.drawingEndStyles ? { drawingEndStyles: wire.drawingEndStyles } : {}),
      metadata: {
        ...(localVolume !== undefined ? { volumeShading: String(localVolume) } : {}),
        drawingWidth:String(wireWidth),
        physicalRoute: String(!!physicalPoints),
        routeMissing: String(view === "drawing" && !wire.drawingEndpoints && !physicalPoints && wire.drawingRoute.length === 0),
        lengthKnown: String(cutLength.isComplete),
        lengthMm: cutLength.sourceLengthMm === null ? "" : String(cutLength.sourceLengthMm),
        endCorrectionFromMm: String(cutLength.endCorrectionFromMm),
        endCorrectionToMm: String(cutLength.endCorrectionToMm),
        cutRoundingStepMm: String(cutLength.cutRoundingStepMm),
        cutLengthMm: cutLength.cutLengthMm === null ? "" : String(cutLength.cutLengthMm),
        materialStatus: cutLength.materialConsumptionMm === null ? "excluded" : "included",
        freeFrom: String(!!wire.drawingEndpoints && !document.connectors.some(connector => connector.id === wire.from.connectorId)),
        freeTo: String(!!wire.drawingEndpoints && !document.connectors.some(connector => connector.id === wire.to.connectorId)),
        detachedDrawing: String(!!wire.drawingEndpoints),
        drawingPairId: document.diffPairs.find(pair => pair.wireIds.includes(wire.id))?.id ?? "",
        drawingPairWireIds: JSON.stringify(document.diffPairs.find(pair => pair.wireIds.includes(wire.id))?.wireIds ?? []),
        materialSourceKey: wire.materialBinding?.sourceKey ?? "",
        materialRecordId: wire.materialBinding?.recordId ?? "",
        wireMark: [wire.from,wire.to].flatMap(end=>"connectorId" in end
          ? document.connectors.find(connector=>connector.id===end.connectorId)?.contacts.filter(contact=>contact.id===end.contactId)??[] : [])
          .find(contact=>contact.wire.trim())?.wire??"",
        materialDisplayName: wire.materialBinding?.displayName ?? "",
        materialEntityType: wire.materialBinding?.entityType ?? "",
        e4LabelPosition: String(wire.e4LabelPosition ?? 0.5),
        ...(stripProfileDisplayWarning ? { stripProfileDisplayWarning } : {}),
        ...(view === "e4" ? {
          view: "e4",
          e4RouteMode: wire.e4RouteMode ?? "auto",
          fromSide: fromAnchor?.leadDirection ?? "",
          toSide: toAnchor?.leadDirection ?? "",
          leadLength: "24",
        } : {}),
      },
    }];
  });
  if (view === "drawing") for (const group of document.diffPairs) {
    const pair = group.wireIds.map(id => wires.find(wire => wire.id === id));
    if (pair.some(wire => !wire || wire.metadata?.physicalRoute === "true")) continue;
    const twisted = freeTwistedPairPaths(group, pair.map(wire => ({
      id: wire!.id, points: wire!.points ?? [], width: Number(wire!.metadata?.drawingWidth ?? 2.5),
    })), document.drawingDocuments?.physicalScale ?? 1, group.wireIds.every(id => document.wires.find(w => w.id === id)?.drawingEndpoints));
    for (const wire of pair) {
      const display = twisted.get(wire!.id);
      if (!display) continue;
      const index = wires.findIndex(item => item.id === wire!.id);
      wires[index] = { ...wire!, paths: [display.path], visibleWireStrokes: display.strokes };
    }
  }
  const dimensions: EditorSceneObject[] = view === "drawing" ? drawingDimensionScene(document,wires,perimeters) : [];
  const physical = view === "drawing" ? physicalTopologyScene(document) : [];
  const coverings: EditorSceneObject[] = view === "drawing" ? coveringScene(document) : [];
  const drawingPhysical= view === "drawing" ? orderPhysicalScene(document,[...physical,...wires,...coverings]) : [];
  const objects=[...connectors, ...(view === "drawing" ? drawingPhysical : wires), ...dimensions, ...drawingDocumentScene(document,quantity,perimeters,view)];
  return view === "drawing" ? [...objects,...drawingObjectIndexScene(document,objects)] : objects;
}

type WireUpdateCommand = Extract<EditorCommand, { readonly type: "update-wire" }>;

export type WireMaterialCatalogUpdateResult =
  | { readonly ok: true; readonly command: WireUpdateCommand }
  | { readonly ok: false; readonly error: string };

/** Builds an immutable material assignment without depending on editor or React state. */
export function wireMaterialUpdateFromCatalogItem(
  item: EditorCatalogItem,
  selectedWireId: string | null,
): WireMaterialCatalogUpdateResult {
  if (!selectedWireId) {
    return { ok: false, error: "Сначала выберите один провод, затем дважды щёлкните материал в справочнике." };
  }
  if (item.entityType !== "wire" && item.entityType !== "cable") {
    return { ok: false, error: "Выбранная справочная позиция не является проводом или кабелем." };
  }
  if (!item.sourceId || !item.snapshotId || !item.snapshotSha256 || !item.recordId || !item.sourceKey) {
    return { ok: false, error: "Справочная позиция не содержит данных опубликованной версии." };
  }
  return {
    ok: true,
    command: {
      type: "update-wire",
      wireId: selectedWireId,
      materialBinding: {
        sourceId: item.sourceId,
        snapshotId: item.snapshotId,
        snapshotSha256: item.snapshotSha256,
        recordId: item.recordId,
        entityType: item.entityType,
        sourceKey: item.sourceKey,
        displayName: item.referenceDisplayName || item.sourceKey,
    ...(item.outerDiameterMm===undefined?{}:{outerDiameterMm:item.outerDiameterMm}),
      },
    },
  };
}

export async function loadComponentTemplateForPlacement(
  api: ComponentTemplateApi,
  templateId: string,
  catalogVersion: number,
) {
  const draft = await api.getDraft(templateId);
  return draft
    ? api.publishDraft(templateId, draft.baseVersion, draft.draftRevision)
    : api.getVersion(templateId, catalogVersion);
}

/** Read-only artwork for route previews whose copy contains connectors without project placements. */
const EMPTY_TEMPLATE_INSTANCES: readonly ComponentTemplateViewInstance[] = [];
export function useLocalCopyTemplateViews(
  document: HarnessDesignDocument | undefined,
  config: RuntimeConfig,
  session: LocalSession,
  projectId: string,
  enabled = true,
  projectInstances: readonly ComponentTemplateViewInstance[] = EMPTY_TEMPLATE_INSTANCES,
  projectAssetUrl?: (snapshotId: string, assetId: string) => string,
) {
  const api = useMemo(() => createComponentTemplateApi(config, session), [config, session]);
  const [snapshots, setSnapshots] = useState<ProjectComponentSnapshotLookup>(() => new Map());
  const [error, setError] = useState("");
  useEffect(() => {
    if (!enabled || !document) return;
    let cancelled = false;
    const bindings = document.connectors.flatMap(connector => connector.libraryBinding?.mode === "template" && !projectInstances.some(instance => instance.objectId === connector.id)
      ? [{connectorId: connector.id, binding: connector.libraryBinding}]
      : []);
    void Promise.all(bindings.map(async ({connectorId, binding}) => {
      const template = await loadComponentTemplateForLocalCopy(api, binding.templateId, binding.templateVersion);
      if (template.versionSha256 !== binding.versionSha256) throw new Error("Версия библиотечного рисунка изменилась.");
      return [connectorId, localTemplateSnapshot(projectId, template)] as const;
    })).then(entries => {
      if (!cancelled) { setSnapshots(new Map(entries)); setError(""); }
    }).catch(cause => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : "Не удалось загрузить библиотечные рисунки копии.");
    });
    return () => { cancelled = true; };
  }, [api, document?.connectors, enabled, projectId, projectInstances]);
  const instances = useMemo(() => enabled && document
    ? [...projectInstances, ...buildComponentTemplateViewInstances(document, snapshots).filter(instance => !projectInstances.some(project => project.objectId === instance.objectId))] : projectInstances,
    [document, enabled, snapshots, projectInstances]);
  const resolveAssetUrl = useCallback((snapshotId: string, assetId: string) => {
    const snapshot = [...snapshots.values()].find(item => item.snapshotId === snapshotId);
    return snapshot ? api.assetContentUrl(snapshot.sourceTemplateId, snapshot.sourceVersion, assetId)
      : projectAssetUrl?.(snapshotId, assetId) ?? "";
  }, [api, snapshots, projectAssetUrl]);
  return {instances, resolveAssetUrl, error};
}

/** Local drawing copies may read a published snapshot but must never publish a library draft. */
export function loadComponentTemplateForLocalCopy(
  api: ComponentTemplateApi,
  templateId: string,
  catalogVersion: number,
) {
  return api.getVersion(templateId, catalogVersion);
}

/** Adapts a read-only published template response to the renderer snapshot contract. */
export function localTemplateSnapshot(
  projectId: string,
  template: Awaited<ReturnType<ComponentTemplateApi["getVersion"]>>,
): ProjectComponentSnapshotResource {
  return {
    snapshotId: crypto.randomUUID(), projectId, sourceTemplateId: template.templateId,
    sourceVersion: template.version, sourceVersionSha256: template.versionSha256,
    code: template.code, name: template.name, articleBindings: template.articleBindings,
    assets: template.assets, schemaVersion: template.content.schemaVersion, content: template.content,
    createdUtc: template.createdUtc, updatedUtc: template.updatedUtc ?? template.createdUtc,
  };
}

export function saveLocalDrawingCopy(
  document: HarnessDesignDocument,
  hiddenObjectIds: readonly string[],
  backgroundOpacity: number,
  onSave: (document: HarnessDesignDocument, hiddenObjectIds: readonly string[], backgroundOpacity: number) => void,
): void {
  onSave(structuredClone(document), [...hiddenObjectIds], backgroundOpacity);
}

type CableUpdateCommand = Extract<EditorCommand, { readonly type: "update-cable" }>;

export function cableMaterialUpdateFromCatalogItem(
  item: EditorCatalogItem,
  cableId: string | null,
): { readonly ok: true; readonly command: CableUpdateCommand } | { readonly ok: false; readonly error: string } {
  if (!cableId) return { ok: false, error: "Сначала создайте кабель из выбранных проводов." };
  if (item.entityType !== "cable") return { ok: false, error: "Для общего материала выберите кабель." };
  if (!item.sourceId || !item.snapshotId || !item.snapshotSha256 || !item.recordId || !item.sourceKey) {
    return { ok: false, error: "Справочная позиция не содержит данных опубликованной версии." };
  }
  return { ok: true, command: { type: "update-cable", cableId, materialBinding: {
    sourceId: item.sourceId, snapshotId: item.snapshotId, snapshotSha256: item.snapshotSha256,
    recordId: item.recordId, entityType: "cable", sourceKey: item.sourceKey,
    displayName: item.referenceDisplayName || item.sourceKey,
    ...(item.outerDiameterMm===undefined?{}:{outerDiameterMm:item.outerDiameterMm}),
  } } };
}

/** Reports saved end treatments that cannot be projected on the current route. */
export function wireStripProfileDisplayWarning(
  points: readonly { readonly x: number; readonly y: number }[],
  profiles: WireEndStripProfiles | undefined,
): string | null {
  if (!profiles) return null;
  const hiddenEnds = (["from", "to"] as const).filter((end) => {
    const profile = profiles[end];
    return profile !== undefined && buildWireStripProfileGeometry(points, end, profile) === null;
  });
  return hiddenEnds.length > 0 ? hiddenEnds.join(",") : null;
}

type WireStripProfileCommand = Extract<EditorCommand, { readonly type: "set-wire-strip-profile" }>;

export type WireStripProfileCatalogUpdateResult =
  | { readonly ok: true; readonly command: WireStripProfileCommand }
  | { readonly ok: false; readonly error: string };

/** Builds an exact end-treatment assignment from a published БД.КОАКС row. */
export function wireStripProfileUpdateFromCatalogItem(
  item: EditorCatalogItem,
  selectedWireId: string | null,
  end: "from" | "to",
  disabled = false,
): WireStripProfileCatalogUpdateResult {
  if (!selectedWireId) {
    return { ok: false, error: "Сначала выберите один провод на чертеже, затем профиль разделки." };
  }
  if (disabled) return { ok: false, error: "Слой выбранного провода заблокирован." };
  if (item.entityType !== "coax-termination" || !item.coaxTerminationCandidate) {
    return { ok: false, error: "Выбранная справочная позиция не является профилем разделки." };
  }
  const candidate = item.coaxTerminationCandidate;
  if (candidate.state !== "ready" || !candidate.binding) {
    return {
      ok: false,
      error: candidate.diagnostics[0]?.message || "Профиль разделки заполнен не полностью.",
    };
  }
  const profile: WireStripProfileBinding = {
    ...candidate.binding,
    displayName: item.referenceDisplayName || item.title || candidate.binding.sourceKey,
  };
  return { ok: true, command: { type: "set-wire-strip-profile", wireId: selectedWireId, end, profile } };
}

/** Translates inspector metadata into a command while preserving omitted-versus-null length semantics. */
export function editorWireUpdateCommand(
  selected: EditorSceneObject,
  previous: EditorSceneObject,
): WireUpdateCommand {
  const parseMetadataNumber = (key: string): number | undefined => {
    const value = selected.metadata?.[key];
    if (value === undefined || value.trim() === "") return undefined;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  };
  const lengthKnownChanged = selected.metadata?.lengthKnown !== previous.metadata?.lengthKnown;
  const lengthChanged = selected.metadata?.lengthMm !== previous.metadata?.lengthMm;
  const lengthMm = parseMetadataNumber("lengthMm");
  const endCorrectionFromMm = parseMetadataNumber("endCorrectionFromMm");
  const endCorrectionToMm = parseMetadataNumber("endCorrectionToMm");
  const cutRoundingStepMm = parseMetadataNumber("cutRoundingStepMm");
  return {
    type: "update-wire",
    wireId: selected.id,
    circuit: selected.label === previous.label ? undefined : selected.label,
    color: selected.color === previous.color ? undefined : selected.color,
    lengthMm: lengthKnownChanged && selected.metadata?.lengthKnown === "false"
      ? null
      : selected.metadata?.lengthKnown === "true" && (lengthKnownChanged || lengthChanged) && lengthMm !== undefined
        ? lengthMm
        : undefined,
    endCorrectionFromMm: selected.metadata?.endCorrectionFromMm !== previous.metadata?.endCorrectionFromMm
      ? endCorrectionFromMm : undefined,
    endCorrectionToMm: selected.metadata?.endCorrectionToMm !== previous.metadata?.endCorrectionToMm
      ? endCorrectionToMm : undefined,
    cutRoundingStepMm: selected.metadata?.cutRoundingStepMm !== previous.metadata?.cutRoundingStepMm
      ? cutRoundingStepMm : undefined,
  };
}

function fromUiLayers(layers: readonly UiLayer[], previous: readonly EditorLayer[]): readonly EditorLayer[] {
  return layers.map((layer, index) => ({
    id: layer.id,
    name: previous.find((item) => item.id === layer.id)?.name ?? layer.label,
    order: layers.length - index - 1,
    visible: layer.visible,
    locked: layer.locked,
  }));
}

export function snapRoutePoint(
  start: { readonly x: number; readonly y: number },
  point: { readonly x: number; readonly y: number },
  enabled: boolean,
  angleStep = Math.PI / 12,
) {
  if (!enabled) return point;
  const dx = point.x - start.x;
  const dy = point.y - start.y;
  const distance = Math.hypot(dx, dy);
  const angle = Math.round(Math.atan2(dy, dx) / angleStep) * angleStep;
  return { x: start.x + Math.cos(angle) * distance, y: start.y + Math.sin(angle) * distance };
}

export function HarnessDesignEditor({
  config,
  session,
  projectId,
  harnessId,
  harnessDesignation,
  initialView, harnessQuantity = 1, initialReveal,
  apiOverride,
  componentPlacementApiOverride,
  onClose,
  onViewChange,
  onRouteRequest,
  onSectionChange,
  localCopy,
}: HarnessDesignEditorProps) {
  // Subscribe the scene projection as well as the left-panel editor to the
  // global preference, so a column change is visible immediately on the field.
  const connectionTableSettings = useConnectionTableSettings();
  const api = useMemo(() => apiOverride ?? createHarnessDesignApi(config, session), [apiOverride, config, session]);
  const recoveryApi = useMemo(() => createDesignRecoveryApi(config, session), [config, session]);
  const recoverySession = useMemo(() => new DesignRecoverySession(recoveryApi, projectId, harnessId), [recoveryApi, projectId, harnessId]);
  const [recoveryError, setRecoveryError] = useState("");
  const recoveryCleanupWarning = "Документ сохранён, но очистка аварийной копии не выполнена. Копия сохранена для проверки.";
  const recoveryBusy = useRef(false);
  const recoveryArchives = useRef<Pick<ServerRecoveryDraft, "draftId" | "sequence">[]>([]);
  const componentTemplateApi = useMemo(() => createComponentTemplateApi(config, session), [config, session]);
  const componentPlacementApi = useMemo(
    () => componentPlacementApiOverride ?? createComponentPlacementApi(config, session),
    [componentPlacementApiOverride, config, session],
  );
  const prepareCoverings=useMemo(()=>createGlobalCoveringPreparer(config,session,projectId),[config,session,projectId]);
  const preparingCovering=useRef(false);
  const catalog = useEditorReferenceCatalog(config, session);
  const terminalLookup = useTerminalArticleLookup(config, session);
  const wireLookup = useWireDatabaseLookup(config, session);
  const [view, setView] = useState<HarnessEditorView>(localCopy ? "drawing" : initialView);
  const [resource, setResource] = useState<HarnessDesignResource | null>(null);
  const [history, setHistory] = useState<EditorHistory | null>(null);
  const [hiddenObjectIds, setHiddenObjectIds] = useState<readonly string[]>(localCopy?.hiddenObjectIds ?? []);
  const [backgroundOpacity, setBackgroundOpacity] = useState(localCopy?.backgroundOpacity ?? 1);
  const textureAssets=useCoveringAssets(config,session,projectId,!!history?.present.drawingDocuments?.coveringLibrary?.textures.length,history?.present.drawingDocuments?.coveringLibrary?.textures.map(t=>t.sha256).join(",")??"");
  const [selectedObjectId, setSelectedObjectId] = useState<string | null>(null);
  const [selectedObjectIds, setSelectedObjectIds] = useState<readonly string[]>([]);
  const graphicsClipboardRef=useRef<readonly import("./drawing-documents").DrawingGraphic[]>([]);
  const [drawingSnaps,setDrawingSnaps]=useState<DrawingSnaps>(defaultDrawingSnaps);
  const [graphicAngleStep,setGraphicAngleStep]=useState(15);
  const [relatedSourceIds, setRelatedSourceIds] = useState<readonly string[]>([]);
  const [wholeNet, setWholeNet] = useState(false);
  const [joiningPipeDraft, setJoiningPipeDraft] = useState<JoiningPipeDraft | null>(null);
  const [revealRequest, setRevealRequest] = useState<{token: number; objectIds: readonly string[]} | undefined>();
  const selectionIndex = useMemo(() => history ? buildHarnessSelectionIndex(history.present) : null, [history?.present]);
  const related = useMemo(() => selectionIndex ? resolveHarnessSelection(selectionIndex, relatedSourceIds.length ? relatedSourceIds : selectedObjectIds, wholeNet) : {wireIds: [], componentIds: [], rowIds: [], unresolvedIds: []}, [selectionIndex, relatedSourceIds, selectedObjectIds, wholeNet]);
  const [activeWireStripEnd, setActiveWireStripEnd] = useState<"from" | "to">("from");
  const [editingObjectId, setEditingObjectId] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<EditorSaveState>("saved");
  const [e4Detached, setE4Detached] = useState(false);
  const [refreshingTerminals, setRefreshingTerminals] = useState(false);
  const [uiFailureNonce, setUiFailureNonce] = useState(0);
  const [movePreview, setMovePreview] = useState<{
    readonly objectId: string;
    readonly point: { readonly x: number; readonly y: number };
    readonly mode?: import("./physical-editing").PhysicalDragMode;
  } | null>(null);
  const [message, setMessage] = useState("Загружаем документ жгута…");
  const [recoveryDrafts, setRecoveryDrafts] = useState<readonly {
    readonly id: string;
    readonly sequence?: number;
    readonly content: HarnessDesignDocument;
    readonly baseRevision: number;
    readonly serverContent?: unknown;
    readonly serverRevision?: number;
  }[]>([]);
  const [componentSnapshotsByPlacement, setComponentSnapshotsByPlacement] =
    useState<ProjectComponentSnapshotLookup>(() => new Map());
  const localTemplateLoading = useRef(new Set<string>());
  const localTemplateSnapshotIds = useRef(new Set<string>());
  const [componentGraphLoaded, setComponentGraphLoaded] = useState(false);
  const [componentGraphMessage, setComponentGraphMessage] = useState("");
  const historyRef = useRef<EditorHistory | null>(null);
  const resourceRef = useRef<HarnessDesignResource | null>(null);
  const savedJsonRef = useRef("");
  const savingRef = useRef(false);
  const saveCoordinatorRef = useRef<DesignSaveCoordinator | null>(null);
  const loadGeneration = useRef(0);
  const componentGraphRequestGeneration = useRef(0);
  const previewFrameRef = useRef<number | null>(null);
  const pendingMovePreviewRef = useRef<typeof movePreview>(null);
  const placementBusyRef = useRef(false);
  const placementOperationRef = useRef<symbol | null>(null);
  const pendingPlacementRef = useRef<PendingComponentPlacement | null>(null);
  const [placementBusy, setPlacementBusy] = useState(false);
  const [placementPending, setPlacementPending] = useState(false);
  const materialResolutionGeneration = useRef<number | null>(null);

  useEffect(() => { historyRef.current = history; }, [history]);
  const draftChangeRef = useRef(localCopy?.onDraftChange);
  draftChangeRef.current = localCopy?.onDraftChange;
  useEffect(() => {
    if (history) draftChangeRef.current?.(history.present, hiddenObjectIds, backgroundOpacity);
  }, [history?.present, hiddenObjectIds, backgroundOpacity]);
  useEffect(() => { resourceRef.current = resource; }, [resource]);
  useEffect(() => setView(localCopy ? "drawing" : initialView), [initialView, localCopy]);

  // Library presets contain mark and section text but older documents do not
  // contain the published record ID. Resolve that text once the active wire
  // snapshot is available, then update the connected conductor and both ends
  // in a single undo step.
  useEffect(() => {
    if (!history || wireLookup.databaseOptions.length === 0 || placementBusy || placementPending ||
        materialResolutionGeneration.current === loadGeneration.current) return;
    materialResolutionGeneration.current = loadGeneration.current;
    const resolved = resolveConnectedWireMaterials(history.present, wireLookup.databaseOptions);
    if (resolved === history.present) return;
    const next = { past: [...history.past, history.present].slice(-100), present: resolved, future: [] };
    historyRef.current = next;
    setHistory(next);
  }, [history, placementBusy, placementPending, wireLookup.databaseOptions]);

  // Descriptive catalog data enriches legacy contacts without changing their
  // selected articles or adding user-visible undo steps. Keep history coherent.
  useEffect(() => {
    if (!history || !terminalLookup.records.length || placementBusy || placementPending) return;
    const present = hydrateTerminalDetails(history.present, terminalLookup.records);
    if (present === history.present) return;
    const next = { present, past: history.past.map(doc => hydrateTerminalDetails(doc, terminalLookup.records)),
      future: history.future.map(doc => hydrateTerminalDetails(doc, terminalLookup.records)) };
    historyRef.current = next;
    setHistory(next);
  }, [history, terminalLookup.records, placementBusy, placementPending]);

  const refreshComponentGraph = useCallback(async (editorGeneration: number): Promise<void> => {
    const requestGeneration = ++componentGraphRequestGeneration.current;
    try {
      const graph = await componentPlacementApi.list(projectId, harnessId);
      if (editorGeneration !== loadGeneration.current ||
          requestGeneration !== componentGraphRequestGeneration.current) return;
      setComponentSnapshotsByPlacement((current) =>
        acceptProjectComponentSnapshotLookup(current, graph, editorGeneration, loadGeneration.current));
      setComponentGraphMessage("");
      setComponentGraphLoaded(true);
    } catch (error) {
      if (editorGeneration !== loadGeneration.current ||
          requestGeneration !== componentGraphRequestGeneration.current) return;
      setComponentSnapshotsByPlacement(new Map());
      setComponentGraphMessage(error instanceof Error
        ? `Не удалось загрузить закреплённые виды компонентов: ${error.message}`
        : "Не удалось загрузить закреплённые виды компонентов.");
      setComponentGraphLoaded(true);
    }
  }, [componentPlacementApi, harnessId, projectId]);
  const resolveComponentTemplateAssetUrl = useCallback(
    (snapshotId: string, assetId: string) => {
      if (localCopy && localTemplateSnapshotIds.current.has(snapshotId)) {
        const snapshot = [...componentSnapshotsByPlacement.values()].find(item => item.snapshotId === snapshotId);
        if (snapshot) return componentTemplateApi.assetContentUrl(snapshot.sourceTemplateId, snapshot.sourceVersion, assetId);
      }
      return componentPlacementApi.assetContentUrl(projectId, harnessId, snapshotId, assetId);
    },
    [componentPlacementApi, componentSnapshotsByPlacement, componentTemplateApi, harnessId, projectId, localCopy],
  );

  useEffect(() => {
    if (!localCopy || !history || !componentGraphLoaded) return;
    const generation = loadGeneration.current;
    for (const connector of history.present.connectors) {
      const binding = connector.libraryBinding;
      if (binding?.mode !== "template" || componentSnapshotsByPlacement.has(connector.id) || localTemplateLoading.current.has(connector.id)) continue;
      localTemplateLoading.current.add(connector.id);
      void loadComponentTemplateForLocalCopy(componentTemplateApi, binding.templateId, binding.templateVersion).then(template => {
        if (generation !== loadGeneration.current) return;
        if (template.versionSha256 !== binding.versionSha256) throw new Error("Версия библиотечного рисунка изменилась.");
        const snapshot = localTemplateSnapshot(projectId, template);
        localTemplateSnapshotIds.current.add(snapshot.snapshotId);
        setComponentSnapshotsByPlacement(current => new Map(current).set(connector.id, snapshot));
      }).catch(error => {
        if (generation === loadGeneration.current) setMessage(error instanceof Error ? error.message : "Не удалось загрузить библиотечный рисунок копии.");
      }).finally(() => localTemplateLoading.current.delete(connector.id));
    }
  }, [localCopy, history?.present.connectors, componentSnapshotsByPlacement, componentTemplateApi, projectId, componentGraphLoaded]);

  useEffect(() => {
    const generation = ++loadGeneration.current;
    placementOperationRef.current = null;
    placementBusyRef.current = false;
    pendingPlacementRef.current = null;
    setPlacementBusy(false);
    setPlacementPending(false);
    setResource(null);
    setHistory(null);
    setSelectedObjectId(null);
    setSelectedObjectIds([]);
    setRelatedSourceIds([]); setWholeNet(false); setRevealRequest(undefined);
    setE4Detached(false);
    setEditingObjectId(null);
    setMovePreview(null);
    setComponentSnapshotsByPlacement(new Map());
    localTemplateLoading.current.clear();
    localTemplateSnapshotIds.current.clear();
    setComponentGraphLoaded(false);
    setComponentGraphMessage("");
    setMessage("Загружаем документ жгута…");
    setRecoveryDrafts([]);
    setRecoveryError("");
    recoveryArchives.current = [];
    setSaveState("saved");
    if (localCopy) {
      const copied = structuredClone(localCopy.initialDocument);
      const localResource: HarnessDesignResource = {
        harnessId, schemaVersion: 1, revision: 0, content: copied,
        updatedUtc: new Date().toISOString(),
      };
      setResource(localResource);
      setHistory(createEditorHistory(copied));
      savedJsonRef.current = JSON.stringify(copied);
      setHiddenObjectIds(localCopy.hiddenObjectIds ?? []);
      setBackgroundOpacity(localCopy.backgroundOpacity ?? 1);
      setMessage("");
      void refreshComponentGraph(generation);
      return () => { loadGeneration.current += 1; componentGraphRequestGeneration.current += 1; };
    }
    const journalRequest = recoveryApi.list(projectId, harnessId).catch(() => {
      if (generation === loadGeneration.current) setRecoveryError("Аварийный журнал недоступен. Восстановление после смены порта не гарантировано; сохраните копию перед закрытием.");
      return [];
    });
    const designRequest = Promise.all([api.get(projectId, harnessId), journalRequest]).then(([loaded, journal]) => {
      if (generation !== loadGeneration.current) return;
      if (initialReveal?.projectId === projectId && initialReveal.harnessId === harnessId) {
        const found = resolveHarnessSelection(buildHarnessSelectionIndex(loaded.content), [initialReveal.objectId]);
        setRelatedSourceIds([initialReveal.objectId]);
        setRevealRequest({token: Date.now(), objectIds: [...found.wireIds, ...found.componentIds]});
      }
      const savedJson = JSON.stringify(loaded.content);
      let recoveryMessage = loaded.recoveryWarning ?? "";
      const candidates: typeof recoveryDrafts[number][] = [];
      for (const item of journal) {
        try {
          const parsed = parseRecoverableHarnessDesignContent(item.content).content;
          if (JSON.stringify(parsed) === savedJson) {
            // The server has acknowledged these exact contents. Older clients
            // could leave this journal behind when their DELETE URL was rejected.
            void recoveryApi.remove(projectId, harnessId, item.draftId, item.sequence).catch(() => {
              if (generation === loadGeneration.current) setRecoveryError(recoveryCleanupWarning);
            });
            continue;
          }
          candidates.push({ id: item.draftId, sequence: item.sequence, content: parsed,
            baseRevision: item.baseRevision, serverContent: item.serverContent, serverRevision: item.serverRevision });
        } catch { setRecoveryError("Одна из аварийных копий повреждена и сохранена на диске без изменений."); }
      }
      const draft = readHarnessDesignRecoveryDraft(browserRecoveryStorage(), projectId, harnessId);
      if (draft) {
        try {
          const recoveredDraft = parseRecoverableHarnessDesignContent(draft.content);
          if (JSON.stringify(recoveredDraft.content) !== savedJson) {
            if (!candidates.some(c => JSON.stringify(c.content) === JSON.stringify(recoveredDraft.content))) candidates.unshift({ id: "browser", content: recoveredDraft.content, baseRevision: draft.baseRevision, serverContent: loaded.content, serverRevision: loaded.revision });
            const revisionWarning = draft.baseRevision === loaded.revision
              ? ""
              : ` Черновик создан от ревизии ${draft.baseRevision}, на сервере уже ревизия ${loaded.revision}; после восстановления внимательно проверьте изменения.`;
            recoveryMessage = `Найдены несохранённые изменения этого жгута. Серверный документ открыт без изменений; восстановите черновик кнопкой ниже.${revisionWarning}`;
          } else {
            removeHarnessDesignRecoveryDraft(browserRecoveryStorage(), projectId, harnessId);
          }
        } catch {
          recoveryMessage = `${recoveryMessage ? `${recoveryMessage} ` : ""}Найден локальный черновик, но он повреждён и не применён.`;
        }
      }
      setRecoveryDrafts(candidates);
      setResource(loaded);
      setHistory(createEditorHistory(reconcileDrawingDocuments(loaded.content, migrateJoiningPipes(loaded.content))));
      savedJsonRef.current = savedJson;
      setMessage(recoveryMessage);
    }).catch((error: unknown) => {
      if (generation !== loadGeneration.current) return;
      setSaveState("error");
      setMessage(error instanceof Error ? error.message : "Не удалось загрузить документ жгута.");
    });

    const componentGraphRequest = refreshComponentGraph(generation);
    void Promise.allSettled([designRequest, componentGraphRequest]);
    return () => {
      loadGeneration.current += 1;
      componentGraphRequestGeneration.current += 1;
    };
  }, [api, harnessId, projectId, recoveryApi, refreshComponentGraph, localCopy?.initialDocument]);

  const saveOnce = useCallback(async (): Promise<boolean> => {
      if (localCopy) return true;
      savingRef.current = true;
      try {
        const currentHistory = historyRef.current;
        const currentResource = resourceRef.current;
        if (!currentHistory || !currentResource) return true;
        const content = currentHistory.present;
        const serialized = JSON.stringify(content);
        if (serialized === savedJsonRef.current) return true;
        setSaveState("saving");
        const saved = await api.save(projectId, harnessId, currentResource.revision, content);
        const savedJson = JSON.stringify(saved.content);
        savedJsonRef.current = savedJson;
        resourceRef.current = saved;
        setResource(saved);
        if (historyRef.current === currentHistory) {
          const acknowledgedHistory = { ...currentHistory, present: saved.content };
          historyRef.current = acknowledgedHistory;
          setHistory(acknowledgedHistory);
          setSaveState("saved");
          if (!recoveryDrafts.some(d => d.id === "browser")) removeHarnessDesignRecoveryDraft(browserRecoveryStorage(), projectId, harnessId);
          const generation = loadGeneration.current;
          const archivedAtSave = recoveryArchives.current.slice();
          void recoverySession.clear().then(() => recoveryApi.removeMany(projectId, harnessId, archivedAtSave)).then((removedIds) => {
            recoveryArchives.current = recoveryArchives.current.filter(draft => !removedIds.includes(draft.draftId));
            if (generation === loadGeneration.current) {
              setRecoveryDrafts(current => current.filter(draft => !removedIds.includes(draft.id)));
              setRecoveryError(current => current === recoveryCleanupWarning ? "" : current);
            }
          }).catch(() => {
            if (generation === loadGeneration.current) setRecoveryError(recoveryCleanupWarning);
          });
        } else {
          setSaveState("changed");
        }
        setMessage(saved.recoveryWarning ?? "");
        return true;
      } catch (error: unknown) {
        setSaveState("error");
        setMessage(error instanceof Error ? error.message : "Не удалось сохранить документ жгута.");
        return false;
      } finally {
        savingRef.current = false;
      }
  }, [api, harnessId, projectId, recoveryApi, recoveryDrafts, recoverySession, localCopy]);

  const flushSave = useCallback((): Promise<boolean> => {
    if (!saveCoordinatorRef.current) {
      saveCoordinatorRef.current = new DesignSaveCoordinator(saveOnce);
    }
    return saveCoordinatorRef.current.flush();
  }, [saveOnce]);

  useEffect(() => {
    saveCoordinatorRef.current = null;
  }, [saveOnce]);

  useEffect(() => {
    if (!history) return;
    if (localCopy) {
      setSaveState(JSON.stringify(history.present) === savedJsonRef.current ? "saved" : "changed");
      return;
    }
    if (JSON.stringify(history.present) === savedJsonRef.current) {
      if (!savingRef.current) setSaveState("saved");
      return;
    }
    const baseRevision = resourceRef.current?.revision;
    const recoveryGeneration = loadGeneration.current;
    if (baseRevision !== undefined) {
      const localWritten = !recoveryDrafts.some(d => d.id === "browser") && writeHarnessDesignRecoveryDraft(
        browserRecoveryStorage(),
        projectId,
        harnessId,
        baseRevision,
        history.present,
      );
      if (!localWritten) setRecoveryError("Локальная аварийная копия недоступна или содержит прежний конфликт. Ожидается запись серверного журнала; не закрывайте редактор.");
      void recoverySession.write(baseRevision, history.present).then(() => {
        if (recoveryGeneration !== loadGeneration.current) return;
        setRecoveryError(localWritten ? "" : "Аварийная копия записана на сервере. Резервная запись в браузере недоступна.");
      }).catch(() => {
        if (recoveryGeneration !== loadGeneration.current) return;
        setRecoveryError(localWritten
          ? "Серверный аварийный журнал не записан. Копия есть только в этом браузере и на этом порту; сохраните документ перед закрытием."
          : "Аварийная копия не записана ни в браузере, ни на сервере. Не закрывайте редактор; сохраните документ или скачайте копию.");
      });
    }
    setSaveState("changed");
    const timer = window.setTimeout(() => void flushSave(), 650);
    return () => window.clearTimeout(timer);
  }, [flushSave, harnessId, history, projectId, recoveryDrafts, recoverySession, localCopy]);

  useEffect(() => {
    if (!history) return;
    const availableObjectIds = new Set([
      ...history.present.connectors.map((item) => item.id),
      ...history.present.connectors.map((item) => `object-index:${item.id}`),
      ...history.present.wires.map((item) => item.id),
      ...history.present.wires.map((item) => `object-index:${item.id}`),
      ...history.present.drawingDocuments?.specificationItems?.map(i=>i.id) ?? [],
      ...history.present.drawingDocuments?.tables.map(t=>t.id) ?? [],
      ...history.present.drawingDocuments?.dimensions?.map(d=>d.id)??[],
      ...history.present.drawingDocuments?.leaders.flatMap(l=>[l.id,`${l.id}:anchor`]) ?? [],
      ...history.present.drawingDocuments?.rails?.flatMap(r=>[r.id,`${r.id}:start`,`${r.id}:end`]) ?? [],
      ...history.present.physicalTopology?.coverings?.map(c => c.id) ?? [],
      ...history.present.physicalTopology?.coverings?.map(c => `object-index:${c.id}`) ?? [],
      ...history.present.physicalTopology?.nodes.map(n => n.id) ?? [],
      ...history.present.physicalTopology?.segments.map(n => n.id) ?? [],
      ...history.present.physicalTopology?.joiningPipes?.flatMap(p=>[p.id,`${p.id}:from`,`${p.id}:to`])??[],
    ]);
    const normalized = normalizeEditorSelection(selectedObjectIds, selectedObjectId, availableObjectIds);
    if (normalized.primaryObjectId !== selectedObjectId) setSelectedObjectId(normalized.primaryObjectId);
    if (normalized.objectIds.length !== selectedObjectIds.length ||
        normalized.objectIds.some((id, index) => id !== selectedObjectIds[index])) {
      setSelectedObjectIds(normalized.objectIds);
    }
  }, [history, selectedObjectId, selectedObjectIds]);

  useEffect(() => {
    if (editingObjectId && (view !== "e4" || editingObjectId !== selectedObjectId)) setEditingObjectId(null);
  }, [editingObjectId, selectedObjectId, view]);

  useEffect(() => setActiveWireStripEnd("from"), [selectedObjectId]);

  const componentTemplateViewInstances = useMemo(() => history
    ? buildComponentTemplateViewInstances(history.present, componentSnapshotsByPlacement) : [],
    [history?.present, componentSnapshotsByPlacement]);
  const drawingPerimeters=useMemo(()=>buildDrawingPerimeters(componentTemplateViewInstances),[componentTemplateViewInstances]);

  const [selectedPipeInterval,setSelectedPipeInterval]=useState<{id:string;from:number;to:number}|null>(null);
  const [coveringPreview,setCoveringPreview]=useState<PhysicalCovering|null>(null);
  const [thicknessPreview,setThicknessPreview]=useState<number|null>(null);
  const [pipeOpacityPreview,setPipeOpacityPreview]=useState<number|null>(null);
  const [bendRadiusPreview,setBendRadiusPreview]=useState<number|null>(null);
  const [leaderScalePreview,setLeaderScalePreview]=useState<number|null>(null);
  const [indexScalePreview,setIndexScalePreview]=useState<number|null>(null);
  const [dimensionScalePreview,setDimensionScalePreview]=useState<number|null>(null);
  const [minimumOverlapPreview,setMinimumOverlapPreview]=useState<number|null>(null);
  const [opCoveringEdgePreview,setOpCoveringEdgePreview]=useState<number|null>(null);
  const [coveringRatioPreview,setCoveringRatioPreview]=useState<number|null>(null);
  const [pairPitchPreview,setPairPitchPreview]=useState<{id:string;step:number}|null>(null);
  const [pipePreview,setPipePreview]=useState<{id:string;index:number;point:{x:number;y:number};mode?:import("./physical-editing").PhysicalDragMode;insert?:boolean}|null>(null);
  const [freeEndPreviews,setFreeEndPreviews]=useState<readonly {wireId:string;end:"from"|"to";position:{x:number;y:number}}[]>([]);
  const previewResult = useMemo(() => {
    if (!history) return { document: null, error: null };
    if(freeEndPreviews.length){
      try{return {document:freeEndPreviews.reduce((document, preview)=>applyEditorCommand(document,{type:"set-wire-drawing-endpoint",...preview}),history.present),error:null};}
      catch(error){return {document:history.present,error:error instanceof Error?error.message:"Не удалось переместить конец провода."};}
    }
    if(pairPitchPreview)return {document:{...history.present,diffPairs:history.present.diffPairs.map(pair=>pair.id===pairPitchPreview.id?{...pair,step:pairPitchPreview.step}:pair)},error:null};
    if(bendRadiusPreview!==null)return {document:{...history.present,drawingDocuments:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),bendRadius:bendRadiusPreview}},error:null};
    if(leaderScalePreview!==null)return {document:{...history.present,drawingDocuments:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),leaderScale:leaderScalePreview}},error:null};
    if(indexScalePreview!==null)return {document:{...history.present,drawingDocuments:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),indexScale:indexScalePreview}},error:null};
    if(dimensionScalePreview!==null)return {document:{...history.present,drawingDocuments:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),dimensionScale:dimensionScalePreview}},error:null};
    if(minimumOverlapPreview!==null)return {document:{...history.present,drawingDocuments:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),minimumCoveringOverlapPx:minimumOverlapPreview}},error:null};
    if(opCoveringEdgePreview!==null)return {document:{...history.present,drawingDocuments:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),opCoveringEdgePx:opCoveringEdgePreview}},error:null};
    if(coveringRatioPreview!==null)return {document:{...history.present,drawingDocuments:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),coveringDiameterRatio:coveringRatioPreview}},error:null};
    if(coveringPreview&&history.present.physicalTopology)return {document:{...history.present,physicalTopology:{...history.present.physicalTopology,coverings:history.present.physicalTopology.coverings?.map(c=>c.id===coveringPreview.id?coveringPreview:c)}},error:null};
    if(pipeOpacityPreview!==null)return {document:{...history.present,drawingDocuments:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),pipeOpacity:1-pipeOpacityPreview/100}},error:null};
    if(thicknessPreview!==null)return {document:{...history.present,drawingDocuments:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),physicalScale:thicknessPreview}},error:null};
    if(pipePreview&&view==="e4"){
      try{return {document:applyEditorCommand(history.present,{type:"edit-e4-bend",wireId:pipePreview.id,index:pipePreview.index,position:pipePreview.point,mode:pipePreview.mode??"carry",insert:pipePreview.insert}),error:null};}
      catch(error){return {document:history.present,error:error instanceof Error?error.message:"Не удалось изменить перегиб Э4."};}
    }
    if(pipePreview&&view==="drawing"&&history.present.wires.some(w=>w.id===pipePreview.id&&w.drawingEndpoints)){
      try{return {document:applyEditorCommand(history.present,{type:"edit-detached-wire-bend",wireId:pipePreview.id,index:pipePreview.index,position:pipePreview.point,insert:pipePreview.insert}),error:null};}
      catch(error){return {document:history.present,error:error instanceof Error?error.message:"Не удалось изменить перегиб."};}
    }
    if(pipePreview&&history.present.physicalTopology) {
      try{const command=resolvePhysicalRoutePointCommand(history.present,pipePreview.id,pipePreview.index,pipePreview.point,pipePreview.mode,pipePreview.insert);
        return {document:command?applyEditorCommand(history.present,command):history.present,error:null};}
      catch(error){return {document:history.present,error:error instanceof Error?error.message:"Не удалось изменить перегиб."};}
    }
    if (!movePreview) return { document: history.present, error: null };
    try {
      if(movePreview.objectId.startsWith("object-index:")){
        const indexScene=alignDrawingConnectorIndexes(history.present,designToScene(history.present,"drawing"),componentTemplateViewInstances,resolveComponentTemplateAssetUrl);
        const index=moveDrawingIndex(history.present,movePreview.objectId,movePreview.point,indexScene);
        if(index)return {document:{...history.present,drawingDocuments:index},error:null};
      }
      const annotation=moveDrawingAnnotation(history.present,movePreview.objectId,movePreview.point,drawingPerimeters);
      if(annotation)return {document:{...history.present,drawingDocuments:annotation},error:null};
      const topology = history.present.physicalTopology;
      const joiningPipe=topology?.joiningPipes?.find(pipe=>pipe.id===movePreview.objectId);
      if(joiningPipe){
        return {document:applyEditorCommand(history.present,{type:"move-joining-pipe",pipeId:joiningPipe.id,delta:movePreview.point}),error:null};
      }
      const opEndpoint=joiningPipeEndpoint(topology,movePreview.objectId);
      if(opEndpoint){
        return {document:applyEditorCommand(history.present,{type:"update-joining-pipe",pipeId:opEndpoint.pipe.id,[opEndpoint.side==="from"?"start":"end"]:{x:movePreview.point.x+5,y:movePreview.point.y+5}}),error:null};
      }
      const opExit=joiningPipeExit(topology,movePreview.objectId);
      if(opExit)return {document:applyEditorCommand(history.present,{type:"update-joining-pipe-exit",pipeId:opExit.pipe.id,side:opExit.side,position:{x:movePreview.point.x+5,y:movePreview.point.y+5}}),error:null};
      const node = topology?.nodes.find(n => n.id === movePreview.objectId);
      if (node && topology) {
        const original=physicalNodePoint(history.present,node),display=pipeBundleNodePoint(history.present,node.id,original);
        return { document: applyEditorCommand(history.present, {type:"move-physical-node",nodeId:node.id,position:{x:movePreview.point.x+5+original.x-display.x,y:movePreview.point.y+5+original.y-display.y},mode:movePreview.mode??"carry"}), error:null };
      }
      return { document: applyEditorCommand(history.present, {
        type: "move-connector",
        connectorId: movePreview.objectId,
        view,
        position: movePreview.point,
        physicalDragMode:movePreview.mode??"carry",
      }), error: null };
    } catch (error) {
      // An impossible placement still follows the pointer, but no previous
      // wire trace is drawn beneath it. Releasing it leaves the saved scene
      // intact and the normal command reports why it could not be placed.
      return {
        document: {
          ...history.present,
          connectors: history.present.connectors.map((connector) => connector.id === movePreview.objectId
            ? { ...connector, positions: { ...connector.positions, [view]: movePreview.point } }
            : connector),
          wires: history.present.wires.filter((wire) => ![wire.from, wire.to].some((endpoint) =>
            !isJunctionEndpoint(endpoint) && !isScreenEndpoint(endpoint) && endpoint.connectorId === movePreview.objectId)),
        },
        error: error instanceof Error ? error.message : "Трассировка невозможна.",
      };
    }
  }, [history, movePreview, view, pipePreview, freeEndPreviews, drawingPerimeters, componentTemplateViewInstances, resolveComponentTemplateAssetUrl, coveringPreview, thicknessPreview, pipeOpacityPreview, leaderScalePreview, indexScalePreview, dimensionScalePreview, minimumOverlapPreview, opCoveringEdgePreview, bendRadiusPreview, coveringRatioPreview, pairPitchPreview]);

  const routingIssues = useMemo(() => view === "e4" && history
    ? e4RoutingIssues(history.present) : [], [history?.present, view]);
  const diagnostics = useMemo(() => view === "e4" && history ? collectE4Diagnostics(history.present) : [], [history?.present, view]);
  const drawingTemplateIds = useMemo(() => new Set(view === "drawing" ? componentTemplateViewInstances
    .filter(i => projectComponentTemplateView(i, "drawing", {x:0,y:0})?.commands.length).map(i => i.objectId) : []),
    [view, componentTemplateViewInstances]);
  const volumeEligible = useMemo(() => {
    return history ? volumeShadingEligible(history.present) : false;
  }, [history?.present]);

  const run = useCallback((command: EditorCommand): boolean => {
    if (placementBusyRef.current || pendingPlacementRef.current) return false;
    const current = historyRef.current;
    if (!current) return false;
    if(!localCopy && command.type==="set-physical-topology"&&!command.coveringLibrary&&command.topology.coverings?.some(c=>coveringMaterialChanged(current.present,c))){
      if(preparingCovering.current){setMessage("Подождите: закрепляем материал оболочки.");return false;}
      preparingCovering.current=true;const generation=loadGeneration.current;setMessage("Закрепляем материал оболочки…");
      void prepareCoverings(current.present,command).then(prepared=>{
        if(loadGeneration.current!==generation)return;
        if(historyRef.current?.present!==current.present)throw new Error("Чертёж изменился во время загрузки материала. Повторите добавление оболочки.");
        const next=executeEditorCommand(current,prepared);historyRef.current=next;setHistory(next);setMessage("");
        const added=prepared.topology.coverings?.find(c=>!current.present.physicalTopology?.coverings?.some(old=>old.id===c.id));
        if(added){setSelectedObjectId(added.id);setSelectedObjectIds([added.id]);}
      }).catch(error=>setMessage(error instanceof Error?error.message:"Не удалось закрепить материал.")).finally(()=>{preparingCovering.current=false});
      return false;
    }
    try {
      let next = executeEditorCommand(current, command);
      // Component/article replacement and E4 edits can change the contact text
      // without creating a new wire. Reconcile the connected conductor on every
      // command while the active wire snapshot is available.
      if (wireLookup.databaseOptions.length && ["update-contact", "add-wire", "apply-template-article",
          "apply-connector-article", "reconnect-wire"].includes(command.type)) next = { ...next,
        present: resolveConnectedWireMaterials(next.present, wireLookup.databaseOptions) };
      historyRef.current = next;
      setHistory(next);
      setMessage("");
      return true;
    } catch (error: unknown) {
      setMessage(error instanceof Error ? error.message : "Не удалось изменить документ жгута.");
      return false;
    }
  }, [prepareCoverings, localCopy, wireLookup.databaseOptions]);

  const initializedExits = useRef(new Set<string>());
  useEffect(() => {
    if (view !== "drawing" || !history || placementBusy || placementPending) return;
    const pending = history.present.connectors.filter(c=>!initializedExits.current.has(c.id));
    if(!pending.length)return;
    if (localCopy) return;
    const topology = ensureConnectorExits(history.present);
    if (topology === history.present.physicalTopology || run({ type: "set-physical-topology", topology }))
      history.present.connectors.forEach(c=>initializedExits.current.add(c.id));
  }, [view, history, placementBusy, placementPending, run]);

  const previewObjectMove = useCallback((objectId: string, point: { readonly x: number; readonly y: number } | null, mode?:import("./physical-editing").PhysicalDragMode) => {
    pendingMovePreviewRef.current = point ? { objectId, point, mode } : null;
    if (!point) {
      if (previewFrameRef.current !== null) cancelAnimationFrame(previewFrameRef.current);
      previewFrameRef.current = null;
      setMovePreview(null);
      return;
    }
    if (previewFrameRef.current !== null) return;
    previewFrameRef.current = requestAnimationFrame(() => {
      previewFrameRef.current = null;
      setMovePreview(pendingMovePreviewRef.current);
    });
  }, []);

  useEffect(() => {
    if (!history) return;
    const upgrade = (document: HarnessDesignDocument) => {
      let changed = document;
      for (const connector of document.connectors) {
        if (!connector.e4TableMode && componentSnapshotsByPlacement.get(connector.id)?.content.schemaVersion === 5) {
          changed = applyEditorCommand(changed, { type: "use-e4-table", connectorId: connector.id });
        }
      }
      return changed;
    };
    try {
      const present = upgrade(history.present);
      if (present === history.present) return;
      // A geometry migration is not an operator edit. Upgrade the undo states
      // as well, otherwise Ctrl+Z would immediately reapply the same migration.
      const changed = { present, past: history.past.map(upgrade), future: history.future.map(upgrade) };
      historyRef.current = changed;
      setHistory(changed);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось восстановить табличные точки Э4.");
    }
  }, [history, componentSnapshotsByPlacement]);

  useEffect(() => () => {
    if (previewFrameRef.current !== null) cancelAnimationFrame(previewFrameRef.current);
  }, []);

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      const current = historyRef.current;
      if (!current || JSON.stringify(current.present) === savedJsonRef.current) return;
      event.preventDefault();
      event.returnValue = true;
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, []);

  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if(joiningPipeDraft){if(event.key==='Escape'){event.preventDefault();setJoiningPipeDraft(null);}return;}
      if (placementBusyRef.current || pendingPlacementRef.current) return;
      if (event.key === "Escape" && editingObjectId) {
        event.preventDefault();
        setEditingObjectId(null);
        return;
      }
      if (target?.matches("input, textarea, select, [contenteditable='true']")) return;
      if (event.key === "Escape") { setRelatedSourceIds([]); setSelectedObjectId(null); setSelectedObjectIds([]); return; }
      if ((event.key === "Delete" || event.key === "Backspace") && selectedObjectIds.length > 0) {
        const current = historyRef.current?.present;
        if (!current) return;
        const commands = selectedEditorDeletionCommands(current, selectedObjectIds);
        if (commands.length > 0) event.preventDefault();
        for (const command of commands) run(command);
        if (commands.length > 0) {
          setSelectedObjectId(null);
          setSelectedObjectIds([]);
        }
        return;
      }
      if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
      const key = event.key.toLocaleLowerCase();
      const current=historyRef.current?.present;
      if(key==="c"&&current){const selected=new Set(selectedObjectIds);graphicsClipboardRef.current=(current.drawingDocuments?.graphics??[]).filter(g=>selected.has(g.id));if(graphicsClipboardRef.current.length){event.preventDefault();return;}}
      if(key==="v"&&current&&graphicsClipboardRef.current.length){event.preventDefault();const docs=current.drawingDocuments??{tables:[],leaders:[],bomOrder:[]};const copies=graphicsClipboardRef.current.map(g=>({...g,id:crypto.randomUUID(),view,points:g.points.map(p=>({x:p.x+20,y:p.y+20}))}));run({type:"set-drawing-documents",documents:{...docs,graphics:[...(docs.graphics??[]),...copies]}});setSelectedObjectIds(copies.map(g=>g.id));setSelectedObjectId(copies.at(-1)?.id??null);return;}
      if((event.key==="["||event.key==="]")&&current){const selected=new Set(selectedObjectIds),delta=event.key==="]"?15:-15,graphics=current.drawingDocuments?.graphics??[],changed=graphics.filter(g=>selected.has(g.id));if(changed.length){event.preventDefault();const docs=current.drawingDocuments!;run({type:"set-drawing-documents",documents:{...docs,graphics:graphics.map(g=>{if(!selected.has(g.id))return g;if(g.kind==="text")return {...g,angle:((g.angle??0)+delta+360)%360};const cx=g.points.reduce((s,p)=>s+p.x,0)/g.points.length,cy=g.points.reduce((s,p)=>s+p.y,0)/g.points.length,a=delta*Math.PI/180,c=Math.cos(a),sn=Math.sin(a);return {...g,points:g.points.map(p=>({x:cx+(p.x-cx)*c-(p.y-cy)*sn,y:cy+(p.x-cx)*sn+(p.y-cy)*c}))};})}});return;}}
      if (key === "z" && !event.shiftKey) {
        event.preventDefault();
        setHistory((current) => current ? undoEditorCommand(current) : current);
      } else if (key === "y" || (key === "z" && event.shiftKey)) {
        event.preventDefault();
        setHistory((current) => current ? redoEditorCommand(current) : current);
      }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [editingObjectId, run, selectedObjectIds,joiningPipeDraft]);

  if (!history || !resource) {
    return <div className={`he-loading ${saveState === "error" ? "error" : ""}`} role="status">{message}</div>;
  }

  const diagnosticObjectIds = new Set(diagnostics.map((diagnostic) => diagnostic.target.objectId));
  const materializedConnectorIds = new Set(componentTemplateViewInstances.map((instance) => instance.objectId));
  const hasComponentGraphIntegrityMismatch = history.present.connectors.some((connector) =>
    connector.libraryBinding?.mode === "template" && componentSnapshotsByPlacement.has(connector.id) &&
    !materializedConnectorIds.has(connector.id));
  const componentGraphIntegrityMessage = componentGraphMessage || (hasComponentGraphIntegrityMismatch
    ? "Закреплённые контактные данные компонента не совпадают со снимком проекта. Используется резервное отображение."
    : "");
  const sourceDocument=previewResult.document??history.present;
  const drawingDocument=view!=="drawing"?sourceDocument:{...sourceDocument,connectors:sourceDocument.connectors.map(connector=>{
    const instance=componentTemplateViewInstances.find(i=>i.objectId===connector.id),binding=connector.libraryBinding;
    if(!instance||binding?.mode!=="template")return connector;
    const rows=materializePlacementRows(instance.content,instance.articleVariantId);
    return {...connector,libraryBinding:{...binding,snapshot:{...binding.snapshot,contacts:binding.snapshot.contacts.map(contact=>({...contact,representations:rows.find(r=>r.key===contact.logicalContactId)?.representations??[]}))}}};
  })};
  const scene = alignDrawingConnectorIndexes(drawingDocument, designToScene(
    drawingDocument,
    view,
    diagnosticObjectIds,
    materializedConnectorIds,
    harnessQuantity,
    drawingTemplateIds,
    drawingPerimeters,
  ),componentTemplateViewInstances,resolveComponentTemplateAssetUrl);
  const layers = toUiLayers(history.present, view);
  const selectedConnector = view === "e4" && selectedObjectId
    ? history.present.connectors.find((connector) => connector.id === selectedObjectId) ?? null
    : null;
  const selectedConnectorLayer = selectedConnector
    ? layers.find((layer) => layer.id === selectedConnector.layerIds.e4)
    : null;
  const selectedSeriesId = selectedConnector?.libraryBinding?.mode === "series"
    ? selectedConnector.libraryBinding.seriesId
    : null;
  const selectedConnectorSeries = selectedSeriesId
    ? builtInConnectorSeries.find((series) => series.id === selectedSeriesId)
    : undefined;
  const selectedTemplateSnapshot = selectedConnector?.libraryBinding?.mode === "template"
    ? componentSnapshotsByPlacement.get(selectedConnector.id) ?? null
    : null;
  const selectedTemplateArticleOptions = selectedTemplateSnapshot &&
      (isTemplateContentV3(selectedTemplateSnapshot.content) || isTemplateContentV4(selectedTemplateSnapshot.content) || isTemplateContentV5(selectedTemplateSnapshot.content))
    ? selectedTemplateSnapshot.content.articleVariants.map((article) => ({
        articleVariantId: article.id,
        articleKey: article.articleKey,
      }))
    : [];
  const selectTemplateArticle = selectedConnector && selectedTemplateSnapshot &&
      (isTemplateContentV3(selectedTemplateSnapshot.content) || isTemplateContentV4(selectedTemplateSnapshot.content) || isTemplateContentV5(selectedTemplateSnapshot.content))
    ? (articleVariantId: string) => {
        try {
          if (selectedConnector.libraryBinding?.mode === "template" &&
              selectedConnector.libraryBinding.articleVariantId === articleVariantId) return;
          const content = selectedTemplateSnapshot.content;
          if (!isTemplateContentV3(content) && !isTemplateContentV4(content) && !isTemplateContentV5(content)) return;
          const connector = rematerializeComponentTemplateConnectorArticle(selectedConnector, {
            templateId: selectedTemplateSnapshot.sourceTemplateId,
            version: selectedTemplateSnapshot.sourceVersion,
            versionSha256: selectedTemplateSnapshot.sourceVersionSha256,
            code: selectedTemplateSnapshot.code,
            name: selectedTemplateSnapshot.name,
            articleBindings: selectedTemplateSnapshot.articleBindings,
            assets: selectedTemplateSnapshot.assets,
            content,
          }, articleVariantId);
          run({ type: "apply-template-article", connectorId: selectedConnector.id, connector });
        } catch (error) {
          setMessage(error instanceof Error ? error.message : "Не удалось выбрать артикул семейства.");
        }
      }
    : undefined;
  const refreshTemplateTerminals = selectedConnector && selectedTemplateSnapshot
      ? async () => {
        if (refreshingTerminals) return;
        const generation = loadGeneration.current;
        const connectorId = selectedConnector.id;
        setRefreshingTerminals(true);
        try {
          const latest = await componentTemplateApi.get(selectedTemplateSnapshot.sourceTemplateId);
          if (generation !== loadGeneration.current) return;
          const current = historyRef.current?.present.connectors.find(item => item.id === connectorId);
          if (!current) return;
          const catalog = refreshedTemplateTerminalCatalog(current, latest);
          run({ type: "refresh-template-terminals", connectorId, catalog });
        } catch (error) {
          if (generation === loadGeneration.current) setMessage(error instanceof Error ? error.message : "Не удалось обновить совместимые терминалы.");
        } finally {
          setRefreshingTerminals(false);
        }
      }
    : undefined;
  const customWireColorHexes = [...new Set([
    ...(history.present.customWireColors ?? []),
    ...history.present.connectors.flatMap((connector) => connector.contacts
      .flatMap((contact) => [contact.color, contact.secondaryColor ?? ""])),
  ].flatMap((value) => {
    const normalized = value.trim().toUpperCase();
    return /^#[0-9A-F]{6}$/.test(normalized) ? [normalized] : [];
  }))];
  const editorWireColors = [
    ...builtInWireColors,
    ...customWireColorHexes.map((hex) => createCustomWireColor(hex)),
  ];
  const placeCatalogItem = async (item: EditorCatalogItem, point?: { readonly x: number; readonly y: number }) => {
    const spec=history.present.drawingDocuments?.specificationItems?.find(i=>i.id===selectedObjectId);
    if(view==="drawing"&&spec&&item.sourceId&&item.snapshotId&&item.snapshotSha256&&item.recordId&&item.sourceKey){
      const documents=history.present.drawingDocuments!;
      run({type:"set-drawing-documents",documents:{...documents,specificationItems:documents.specificationItems!.map(i=>i.id===spec.id?{...i,kind:"manual",designation:item.sourceKey!,name:item.referenceDisplayName||item.title,sourceIdentity:JSON.stringify([item.sourceId,item.snapshotId,item.snapshotSha256,item.recordId,item.entityType,item.sourceKey])}:i)}});return;
    }
    if (item.entityType === "protective-covering") {
      const t = history.present.physicalTopology;
      if (view !== "drawing" || !t) { setMessage("Сначала создайте и выберите физические участки на Чертеже."); return; }
      try {
        const material = coveringMaterial(item);
        const existing = t.coverings?.find(c => selectedObjectIds.includes(c.id));
        const spans = t.segments.filter(s => selectedObjectIds.includes(s.id)).map(s => ({segmentId:s.id,from:0,to:1}));
        if (!existing && !spans.length) throw new Error("Выберите участки или оболочку для назначения защиты.");
        run({type:"set-physical-topology",topology:{...t,coverings:existing ? t.coverings?.map(c=>c.id===existing.id?{...c,material,name:material.displayName}:c) : [...t.coverings ?? [],{id:crypto.randomUUID(),name:material.displayName,material,spans,width:0,color:"#687e8a",lengthMm:null}]}});
      } catch(error) { setMessage(error instanceof Error ? error.message : "Не удалось назначить защиту."); }
      return;
    }
    if (item.placement === "reference-only" && item.entityType === "coax-termination") {
      if (view !== "drawing") {
        setMessage("Профиль разделки назначается проводу только в режиме «Чертёж».");
        return;
      }
      const selectedWireId = selectedObjectIds.length === 1 &&
        history.present.wires.some((wire) => wire.id === selectedObjectIds[0])
        ? selectedObjectIds[0]!
        : null;
      const selectedWire = selectedWireId
        ? history.present.wires.find((wire) => wire.id === selectedWireId)
        : undefined;
      const locked = selectedWire
        ? history.present.views.drawing.layers.some((layer) => layer.id === selectedWire.layerIds.drawing && layer.locked)
        : false;
      const result = wireStripProfileUpdateFromCatalogItem(item, selectedWireId, activeWireStripEnd, locked);
      if (!result.ok) setMessage(result.error);
      else run(result.command);
      return;
    }
    if (item.placement === "reference-only" && (item.entityType === "wire" || item.entityType === "cable")) {
      if (item.entityType === "cable" && selectedCable) {
        const result = cableMaterialUpdateFromCatalogItem(item, selectedCable.id);
        if (!result.ok) setMessage(result.error);
        else run(result.command);
        return;
      }
      const selectedWireId = selectedObjectIds.length === 1 &&
        history.present.wires.some((wire) => wire.id === selectedObjectIds[0])
        ? selectedObjectIds[0]!
        : null;
      const result = wireMaterialUpdateFromCatalogItem(item, selectedWireId);
      if (!result.ok) setMessage(result.error);
      else {
        const option=wireLookup.databaseOptions.find(candidate=>candidate.id===item.recordId);
        const selected=history.present.wires.find(wire=>wire.id===selectedWireId);
        const endpoint=selected&&[selected.from,selected.to].find(end=>"connectorId" in end);
        if(option?.materialBinding&&endpoint&&"connectorId" in endpoint)run({
          type:"update-contact",connectorId:endpoint.connectorId,contactId:endpoint.contactId,
          wire:option.mark,wireSection:option.section,wireDiameterMm:option.diameterMm??null,
          materialBinding:option.materialBinding,...(option.color?{color:option.color}:{}),
        });
        else run(result.command);
      }
      return;
    }
    if (item.placement !== "connector") return;
    const generation = loadGeneration.current;
    const id = crypto.randomUUID();
    const index = history.present.connectors.length;
    let preview: ReturnType<typeof createBuiltInConnectorInstance>;
    let isPersistentTemplate = false;
    if (item.componentTemplateId && item.componentTemplateVersion) {
      try {
        const template = localCopy
          ? await loadComponentTemplateForLocalCopy(componentTemplateApi, item.componentTemplateId, item.componentTemplateVersion)
          : await loadComponentTemplateForPlacement(
            componentTemplateApi, item.componentTemplateId, item.componentTemplateVersion);
        if (generation !== loadGeneration.current) return;
        if (!isTemplateContentV3(template.content) && !isTemplateContentV4(template.content) && !isTemplateContentV5(template.content)) {
          throw new Error("Для размещения в жгуте требуется шаблон v3 или v4.");
        }
        // A catalog card represents the whole series. Its cached article
        // metadata may predate the loaded immutable version, whose first real
        // variant is the deterministic initial selection.
        const variantId = firstPlaceableArticleVariantId(template.content);
        const variant = template.content.articleVariants.find(candidate => candidate.id === variantId);
        if (!variant) throw new Error("В библиотечном шаблоне нет варианта артикула для размещения.");
        preview = createConnectorInstanceFromComponentTemplateV3({
          templateId: template.templateId,
          version: template.version,
          versionSha256: template.versionSha256,
          code: template.code,
          name: template.name,
          articleBindings: template.articleBindings,
          assets: template.assets,
          content: template.content,
        }, {
          id,
          designation: `XS${index + 1}`,
          articleVariantId: variant.id,
          e4Position: { x: 0, y: 0 },
        });
        isPersistentTemplate = true;
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "Не удалось разместить библиотечный компонент.");
        return;
      }
    } else {
      preview = createBuiltInConnectorInstance(item.id, {
        id,
        designation: `XS${index + 1}`,
        e4Position: { x: 0, y: 0 },
        partNumber: item.defaultPartNumber,
      });
    }
    const nextE4Y = history.present.connectors.reduce((bottom, connector) => Math.max(
      bottom,
      connector.positions.e4.y + connectorE4TableGeometry(connector).height + 90,
    ), 100);
    const placement = point ?? { x: 120, y: nextE4Y };
    const command: EditorCommand = {
      type: "add-connector",
      connector: { ...preview, positions: { e4: placement, drawing: placement } },
    };
    if (isPersistentTemplate && !localCopy) {
      const currentHistory = historyRef.current;
      const currentResource = resourceRef.current;
      if (!currentHistory || !currentResource) return;
      if (!(await flushSave())) return;
      if (generation !== loadGeneration.current) return;
      const latestHistory = historyRef.current;
      const latestResource = resourceRef.current;
      if (!latestHistory || !latestResource) return;
      let nextHistory: EditorHistory;
      try {
        nextHistory = executeEditorCommand(latestHistory, command);
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "Не удалось разместить компонент.");
        return;
      }
      const pending: PendingComponentPlacement = pendingPlacementRef.current ?? {
        body: componentPlacementRequest(command.connector, latestResource.revision, crypto.randomUUID()),
        nextHistory,
        connectorId: preview.id,
        loadGeneration: generation,
      };
      pendingPlacementRef.current = pending;
      setPlacementPending(true);
      try {
        const result = await componentPlacementApi.place(projectId, harnessId, pending.body);
        const authoritative = await api.get(projectId, harnessId);
        if (pending.loadGeneration !== loadGeneration.current || pendingPlacementRef.current !== pending) return;
        nextHistory = pending.nextHistory;
        if (authoritative.revision !== result.resultingRevision ||
            !authoritative.content.connectors.some((connector) => connector.id === pending.connectorId)) {
          throw new Error("Сервер не подтвердил компонент в документе жгута.");
        }
        historyRef.current = nextHistory;
        setHistory(nextHistory);
        const saved = { ...authoritative, content: nextHistory.present };
        resourceRef.current = saved;
        setResource(saved);
        savedJsonRef.current = JSON.stringify(nextHistory.present);
        setSaveState("saved");
        pendingPlacementRef.current = null;
        setPlacementPending(false);
        void refreshComponentGraph(pending.loadGeneration);
      } catch (error) {
        // The exact command is kept for the next activation. If the response was
        // lost after the server committed it, the idempotent command journal
        // returns the already accepted result instead of creating a duplicate.
        if (pending.loadGeneration === loadGeneration.current && pendingPlacementRef.current === pending) {
          setMessage(error instanceof Error ? error.message : "Не удалось закрепить компонент в проекте.");
        }
        return;
      }
    } else {
      run(command);
    }
    setSelectedObjectId(id);
    setSelectedObjectIds([id]);
  };

  const retryPendingPlacement = async () => {
    if (placementBusyRef.current) return;
    const pending = pendingPlacementRef.current;
    if (!pending || pending.loadGeneration !== loadGeneration.current) return;
    const operation = Symbol("component-placement-retry");
    placementOperationRef.current = operation;
    placementBusyRef.current = true;
    setPlacementBusy(true);
    try {
      const result = await componentPlacementApi.place(projectId, harnessId, pending.body);
      const authoritative = await api.get(projectId, harnessId);
      if (pending.loadGeneration !== loadGeneration.current || pendingPlacementRef.current !== pending) return;
      if (authoritative.revision !== result.resultingRevision ||
          !authoritative.content.connectors.some((connector) => connector.id === pending.connectorId)) {
        throw new Error("Сервер не подтвердил компонент в документе жгута.");
      }
      historyRef.current = pending.nextHistory;
      setHistory(pending.nextHistory);
      const saved = { ...authoritative, content: pending.nextHistory.present };
      resourceRef.current = saved;
      setResource(saved);
      savedJsonRef.current = JSON.stringify(pending.nextHistory.present);
      pendingPlacementRef.current = null;
      setPlacementPending(false);
      void refreshComponentGraph(pending.loadGeneration);
      setSaveState("saved");
      setMessage("");
      setSelectedObjectId(pending.connectorId);
      setSelectedObjectIds([pending.connectorId]);
    } catch (error) {
      if (pending.loadGeneration === loadGeneration.current && pendingPlacementRef.current === pending) {
        setMessage(error instanceof Error ? error.message : "Не удалось закрепить компонент в проекте.");
      }
    } finally {
      if (placementOperationRef.current === operation) {
        placementOperationRef.current = null;
        placementBusyRef.current = false;
        setPlacementBusy(false);
      }
    }
  };

  const addCatalogItem = async (item: EditorCatalogItem, point?: { readonly x: number; readonly y: number }) => {
    if (placementBusyRef.current) return;
    if (pendingPlacementRef.current) {
      await retryPendingPlacement();
      return;
    }
    const persistent = Boolean(item.componentTemplateId && item.componentTemplateVersion);
    if (!persistent) {
      await placeCatalogItem(item, point);
      return;
    }
    if (placementBusyRef.current) return;
    const operation = Symbol("component-placement");
    placementOperationRef.current = operation;
    placementBusyRef.current = true;
    setPlacementBusy(true);
    try {
      await placeCatalogItem(item, point);
    } finally {
      if (placementOperationRef.current === operation) {
        placementOperationRef.current = null;
        placementBusyRef.current = false;
        setPlacementBusy(false);
      }
    }
  };

  const addRoutePoint = (point: { readonly x: number; readonly y: number }, ctrlKey = false) => {
    if (view !== "drawing" || !selectedObjectId) return;
    const topology = history.present.physicalTopology;
    const segment = topology?.segments.find(s => s.id === selectedObjectId);
    if (topology && segment) return;
    const wire = history.present.wires.find((item) => item.id === selectedObjectId);
    if (!wire || topology?.routes.some(r => r.wireId === wire.id)) return;
    if(wire.drawingEndpoints){
      const index=detachedWireBendInsertionIndex(wire,point);
      const anchor=index===0?wire.drawingEndpoints.from:wire.drawingRoute[index-1]!;
      const next=snapRoutePoint(anchor,point,ctrlKey,Math.PI/12);
      run({type:"edit-detached-wire-bend",wireId:wire.id,index,position:next,insert:true});
      return;
    }
    const renderedWire = scene.find((item) => item.id === wire.id);
    const start = wire.drawingRoute.at(-1) ?? renderedWire?.points?.[0] ??
      findWireEndpoint(history.present, wire.from, "drawing");
    if (!start) return;
    const next = snapRoutePoint(start, point, ctrlKey, Math.PI / 6);
    run({ type: "set-wire-route", wireId: wire.id, route: [...wire.drawingRoute, next] });
  };

  const createRoutedWire = (id: string, from: WireEndpoint, to: WireEndpoint) => {
    const contactValues = [from, to].flatMap((endpoint) => {
      if (isJunctionEndpoint(endpoint) || isScreenEndpoint(endpoint)) return [];
      const connector = history.present.connectors.find((item) => item.id === endpoint.connectorId);
      const contact = connector?.contacts.find((item) => item.id === endpoint.contactId);
      return contact ? [contact] : [];
    });
    const circuit = contactValues.find((contact) => contact.circuit.trim())?.circuit ?? "";
    const colorName = contactValues.find((contact) => contact.color.trim())?.color ?? "";
    const colorContact = contactValues.find((contact) => contact.color.trim());
    const colorSource = colorContact
      ? [from, to].find((endpoint) => !isJunctionEndpoint(endpoint) && !isScreenEndpoint(endpoint) &&
        endpoint.contactId === colorContact.id)
      : !isJunctionEndpoint(from) && !isScreenEndpoint(from) ? from : undefined;
    // A newly connected end starts with the terminal's L+ allowance.  The
    // inspector keeps the resulting value editable and can replace it with L-.
    const terminalCorrections = wireEndTerminalCorrections(history.present, { from, to }, terminalLookup.records);
    const wire = createWire(
      id, from, to, null, circuit, resolveWireColorHex(colorName),
      terminalCorrections.from?.plusMm ?? 0, terminalCorrections.to?.plusMm ?? 0, 1,
      colorSource ? { connectorId: colorSource.connectorId, contactId: colorSource.contactId } : undefined,
    );
    const start = wireEndpointE4Anchor(history.present, from);
    const end = wireEndpointE4Anchor(history.present, to);
    return start && end ? { ...wire, e4Route: createOrthogonalE4Route(start, end) } : wire;
  };

  const selectedWireIds = selectedObjectIds.filter((id) => history.present.wires.some((wire) => wire.id === id));
  const selectedCable = history.present.cables.find((cable) =>
    cable.memberWireIds.length === selectedWireIds.length &&
    cable.memberWireIds.every((wireId) => selectedWireIds.includes(wireId))) ?? null;
  const selectedWireForCorrections = selectedObjectId
    ? history.present.wires.find(wire => wire.id === selectedObjectId)
    : undefined;
  const selectedTerminalCorrections = selectedWireForCorrections
    ? wireEndTerminalCorrections(history.present, selectedWireForCorrections, terminalLookup.records)
    : undefined;
  const selectedWiresLocked = selectedWireIds.some((wireId) => {
    const wire = history.present.wires.find((item) => item.id === wireId);
    return wire ? history.present.views.drawing.layers.some((layer) => layer.id === wire.layerIds.drawing && layer.locked) : false;
  });
  const selectedDiffPair = history.present.diffPairs.find((group) =>
    group.wireIds.length === selectedWireIds.length && group.wireIds.every((id) => selectedWireIds.includes(id))) ?? null;
  const selectedScreen = history.present.screens.find((group) =>
    group.wireIds.length === selectedWireIds.length && group.wireIds.every((id) => selectedWireIds.includes(id))) ?? null;

  const changeDiffPair = (state: E4DifferentialPairState | null) => {
    if (selectedWireIds.length !== 2) return;
    if (state === null) {
      if (selectedDiffPair) run({ type: "remove-diff-pair", groupId: selectedDiffPair.id });
      return;
    }
    if (selectedDiffPair) {
      run({
        type: "update-diff-pair",
        groupId: selectedDiffPair.id,
        variant: state.variant,
        step: state.twistPitchMm,
      });
      return;
    }
    run({
      type: "create-diff-pair",
      group: {
        id: crypto.randomUUID(),
        wireIds: [selectedWireIds[0]!, selectedWireIds[1]!],
        variant: state.variant,
        step: state.twistPitchMm,
        amplitude: 7,
      },
    });
  };

  const changeScreen = (state: E4ScreenState | null) => {
    if (selectedWireIds.length < 1) return;
    if (state === null) {
      if (selectedScreen) run({ type: "remove-screen", screenId: selectedScreen.id });
      return;
    }
    const position = state.positionPercent / 100;
    if (selectedScreen) {
      run({ type: "update-screen", screenId: selectedScreen.id, position, terminalSide: state.terminalSide });
    } else {
      run({
        type: "create-screen",
        screen: { id: crypto.randomUUID(), wireIds: selectedWireIds, position, label: "Экран", width: 46, terminalSide: state.terminalSide },
      });
    }
  };

  const positionsVisible = !!history?.present.drawingDocuments?.leaders.some(leader => !leader.hidden);
  const isolateLocalObjects = (objectIds: readonly string[]) => {
    if (!localCopy?.onIsolateObjects) return;
    const current = historyRef.current?.present;
    if (!current) return;
    const detached = localCopy.onIsolateObjects(current, objectIds, scene);
    if (!detached) return;
    const next = createEditorHistory(detached);
    historyRef.current = next;
    setHistory(next);
    setHiddenObjectIds([]);
    setBackgroundOpacity(0);
    setSelectedObjectId(null);
    setSelectedObjectIds([]);
    setMessage("Создан изолированный фрагмент. Его геометрия не связана с исходным маршрутом.");
  };

  return (
    <div className={`he-host ${placementBusy ? "is-placement-busy" : ""}`}>
      {message && <div className="he-save-message" role="alert">{message.startsWith("Схема открыта в безопасном режиме")
        ? <>Маршруты требуют проверки. <InfoHint>{message} Редактирование доступно; перемещение блока повторяет поиск маршрутов.</InfoHint></>
        : message}</div>}
      {routingIssues.length > 0 && <div className="he-save-message" role="status">
        Трассировка: {routingIssues.length} требуют перестроения.
        <InfoHint>Изменения сохраняются. Раздвиньте блоки: при каждом перемещении выполняется поиск маршрутов.
          {routingIssues.map((issue) => <div key={issue.wireId}>{issue.wireId}: {issue.message}</div>)}
        </InfoHint>
      </div>}
      {recoveryError && <div className="he-save-message" role="alert">{recoveryError}
        <button type="button" onClick={() => {
          const url = URL.createObjectURL(new Blob([JSON.stringify(history.present, null, 2)], { type: "application/json" }));
          const link = document.createElement("a"); link.href = url; link.download = `harness-${harnessId}-recovery.json`; link.click();
          window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        }}>Скачать текущую копию</button>
      </div>}
      {recoveryDrafts.map(draft => <div key={draft.id} className="he-recovery-draft" role="alert">
        <span>Аварийная копия от ревизии {draft.baseRevision}. Текущий серверный документ не заменён.</span>
        <button type="button" onClick={() => void (async () => {
          if (recoveryBusy.current) return;
          recoveryBusy.current = true;
          const beforeRestore = historyRef.current;
          const generation = loadGeneration.current;
          try {
            const archived: Pick<ServerRecoveryDraft, "draftId" | "sequence">[] = [];
            if (beforeRestore && JSON.stringify(beforeRestore.present) !== savedJsonRef.current) {
              // The current editor can itself contain unsaved edits when another copy is selected.
              const draftId = crypto.randomUUID();
              await recoveryApi.put(projectId, harnessId, draftId, 1,
                resourceRef.current?.revision ?? 0, beforeRestore.present);
              archived.push({ draftId, sequence: 1 });
            }
            // Archive both alternatives before applying the recovery copy.
            const draftId = crypto.randomUUID();
            await recoveryApi.put(projectId, harnessId, draftId, 1, draft.baseRevision, draft.content);
            archived.push({ draftId, sequence: 1 });
            if (generation !== loadGeneration.current) return;
            if (historyRef.current !== beforeRestore) {
              setRecoveryError("Во время подготовки восстановления документ изменился. Обе копии сохранены; повторите восстановление после завершения изменений.");
              return;
            }
            recoverySession.preserve();
            recoveryArchives.current.push(...archived);
            const next = createEditorHistory(migrateJoiningPipes(draft.content));
            historyRef.current = next; setHistory(next);
            setRecoveryDrafts(current => current.filter(item => item.id !== draft.id));
            if (draft.id === "browser") removeHarnessDesignRecoveryDraft(browserRecoveryStorage(), projectId, harnessId);
            setMessage("Аварийная копия восстановлена; исходная и серверная версии сохранены в журнале.");
          } catch { setRecoveryError("Восстановление отменено: не удалось сохранить обе конфликтующие копии в журнале."); }
          finally { recoveryBusy.current = false; }
        })()}>Восстановить</button>
        <button type="button" onClick={() => {
          const url = URL.createObjectURL(new Blob([JSON.stringify(draft, null, 2)], { type: "application/json" }));
          const link = document.createElement("a"); link.href = url; link.download = `harness-${harnessId}-conflict.json`; link.click();
          window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        }}>Скачать обе версии</button>
        <button type="button" onClick={() => void (async () => {
          try {
            if (draft.id === "browser") removeHarnessDesignRecoveryDraft(browserRecoveryStorage(), projectId, harnessId);
            else await recoveryApi.remove(projectId, harnessId, draft.id, draft.sequence!);
            setRecoveryDrafts(current => current.filter(item => item.id !== draft.id));
          } catch { setRecoveryError("Копия изменилась или журнал недоступен. Аварийная копия не удалена."); }
        })()}>Удалить эту аварийную копию</button>
      </div>)}
      {view==="drawing" && drawingDocument.connectors.some(c=>c.libraryBinding?.mode==="template"&&materializedConnectorIds.has(c.id)&&componentTemplateViewInstances.some(i=>i.objectId===c.id&&projectComponentTemplateView(i,"drawing",{x:0,y:0})?.commands.length)&&c.libraryBinding.snapshot.contacts.some(p=>!p.representations.some(r=>r.viewKind==="drawing"))) && <div className="he-save-message" role="alert">В рисунке не заданы точки части контактов. Откройте рисунок артикула в библиотеке, свяжите контакты с колонкой № либо задайте общий выход для чертежа и обновите компонент.</div>}
      {componentGraphIntegrityMessage && <ComponentGraphErrorAlert
        message={componentGraphIntegrityMessage}
        onRetry={() => void refreshComponentGraph(loadGeneration.current)}
      />}
      <HarnessEditorErrorBoundary
        key={`${harnessId}:${uiFailureNonce}`}
        onError={(failure) => setMessage(`Ошибка отображения: ${failure}`)}
        onRecover={() => {
          setEditingObjectId(null);
          setMovePreview(null);
          setUiFailureNonce((value) => value + 1);
        }}
      ><HarnessEditorWorkspace
        harnessId={harnessId}
        harnessDesignation={harnessDesignation}
        view={view}
        objects={withCoveringTextureUrls(scene,textureAssets.urls).map(object => (object.kind === "wire" || object.kind === "physical-segment" || object.kind === "physical-covering") ? {...object,metadata:{...object.metadata,volumeShading:object.metadata?.volumeShading??String(history.present.drawingDocuments?.volumeShading !== false)}} : object)}
        backgroundObjects={localCopy ? withCoveringTextureUrls(scene.filter(object => hiddenObjectIds.includes(object.id)), textureAssets.urls) : undefined}
        backgroundOpacity={localCopy ? backgroundOpacity : undefined}
        layers={layers}
        catalogItems={catalog.items}
        catalogSources={catalog.sources}
        selectedCatalogSourceId={catalog.selectedSourceId}
        catalogQuery={catalog.query}
        catalogLoadState={catalog.loadState}
        catalogMessage={catalog.message}
        catalogHasMore={catalog.hasMore}
        selectedObjectId={selectedObjectId}
        selectedObjectIds={selectedObjectIds}
        terminalCorrections={selectedTerminalCorrections}
        onTerminalCorrectionApply={(wireId, end, correctionMm) => run({
          type: "update-wire", wireId,
          ...(end === "from" ? { endCorrectionFromMm: correctionMm } : { endCorrectionToMm: correctionMm }),
        })}
        highlightedObjectIds={joiningPipeDraft&&history.present.physicalTopology?joiningPipeDraftHighlights(history.present,joiningPipeDraft):[...related.wireIds,...related.componentIds,...relatedSourceIds]}
        foregroundWireIds={relatedSourceIds.length?related.wireIds:selectedObjectIds.filter(id=>history.present.wires.some(wire=>wire.id===id))}
        onObjectPick={view==='drawing'&&joiningPipeDraft?id=>{if(id&&history.present.physicalTopology)setJoiningPipeDraft(toggleJoiningPipeMember(history.present,joiningPipeDraft,id));}:undefined}
        onObjectPickCancel={()=>setJoiningPipeDraft(null)}
        revealRequest={revealRequest}
        objectProperties={view==="drawing"?id=>
          <DrawingObjectProperties document={history.present} objectId={id} selectedIds={selectedObjectIds} onCommand={run} instances={componentTemplateViewInstances} wireMaterialOptions={wireLookup.databaseOptions} onSelect={id=>{setSelectedObjectId(id);setSelectedObjectIds([id]);}}
            contactSideView={(() => { const placement=history.present.connectors.find(connector=>connector.id===id)?.drawingPlacements?.find(item=>item.drawingId===CONTACT_SIDE_VIEW_PLACEMENT_ID); return {exists:!!placement,visible:placement?.visible,scale:placement ? drawingScale([placement],CONTACT_SIDE_VIEW_PLACEMENT_ID) : undefined}; })()}
            contactSideViewActions={{
              onAdd:()=>run({type:"set-contact-side-view",connectorId:id}),
              onSelect:()=>{setSelectedObjectId(id);setSelectedObjectIds([id]);},
              onShow:()=>run({type:"set-contact-side-view",connectorId:id,visible:true}),
              onHide:()=>run({type:"set-contact-side-view",connectorId:id,visible:false}),
              onScaleChange:scale=>run({type:"set-contact-side-view",connectorId:id,scale}),
            }}
            onBundleEdit={bundleId=>{const topology=history.present.physicalTopology;if(topology)setJoiningPipeDraft(beginJoiningPipe(history.present,bundleId,selectedObjectIds));}}/>:undefined}
        documentActions={<>
          {view === "e4" && <>
            <ConnectionTableSettings />
            <section className="he-utility-section he-controls-section" aria-labelledby="he-e4-routing-heading">
              <h3 id="he-e4-routing-heading">Маршрути Э4</h3>
              <button
                type="button"
                className="ui-control he-control-action"
                aria-pressed={history.present.views.e4.e4RoutingMode === "angular"}
                onClick={() => run({ type: "set-e4-routing-mode", mode: history.present.views.e4.e4RoutingMode === "angular" ? "orthogonal" : "angular" })}
              >
                {history.present.views.e4.e4RoutingMode === "angular" ? "Углы 30° включены" : "Разрешить углы 30°"}
              </button>
              <InfoHint>Выключено: автоматическая трассировка и ручное перемещение используют только горизонтальные и вертикальные участки. Включено: новые автоматические маршруты и перетягивание точек допускают направления, кратные 30°.</InfoHint>
            </section>
          </>}
          {view === "drawing" && <section className="he-utility-section he-controls-section" aria-labelledby="he-controls-heading">
            <h3 id="he-controls-heading">Настройки чертежа</h3>
          {joiningPipeDraft&&history.present.physicalTopology&&<JoiningPipeEditor document={history.present} draft={joiningPipeDraft} onChange={setJoiningPipeDraft} onCancel={()=>setJoiningPipeDraft(null)} onSave={()=>{try{const topology=joiningPipeDraftTopology(history.present,joiningPipeDraft);if(run({type:'set-physical-topology',topology})){setSelectedObjectId(joiningPipeDraft.id);setSelectedObjectIds([joiningPipeDraft.id]);setJoiningPipeDraft(null);}}catch(error){setMessage(error instanceof Error?error.message:'Не удалось сохранить состав группы.');}}}/>}
          <DrawingRangeControl label="Прозрачность П/ОП" accessibleLabel="Прозрачность пайпов и объединяющих пайпов" min={0} max={100} step={1} digits={0} unit="%" value={pipeOpacityPreview??Math.round((1-(history.present.drawingDocuments?.pipeOpacity??.72))*100)} onPreview={setPipeOpacityPreview} onCommit={transparency=>{const pipeOpacity=1-transparency/100;if(pipeOpacity!==(history.present.drawingDocuments?.pipeOpacity??.72))run({type:"set-drawing-documents",documents:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),pipeOpacity}});}} hint="Единая прозрачность всех пайпов и объединяющих пайпов на чертеже. Электрические связи и геометрия не меняются."/>
          <DrawingRangeControl label="Толщина" accessibleLabel="Масштаб толщины проводов" min={.2} max={8} step={.05} value={thicknessPreview??history.present.drawingDocuments?.physicalScale??1} onPreview={setThicknessPreview} onCommit={physicalScale=>{if(physicalScale!==(history.present.drawingDocuments?.physicalScale??1))run({type:"set-drawing-documents",documents:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),physicalScale}});}} hint={`Опорный диаметр: ${drawingReferenceDiameter(history.present)} мм. Отношения диаметров сохраняются.`}/>
          {history.present.diffPairs.map((pair,index)=><DrawingRangeControl key={pair.id} label={history.present.diffPairs.length===1?"Шаг витой пары":`Шаг пары ${index+1}`} accessibleLabel={`Шаг витой пары ${index+1}`} min={5} max={Math.max(150,Math.ceil(pair.step/50)*50)} step={1} digits={0} unit=" мм" value={pairPitchPreview?.id===pair.id?pairPitchPreview.step:pair.step} onPreview={step=>setPairPitchPreview(step===null?null:{id:pair.id,step})} onCommit={step=>{if(step!==pair.step)run({type:"update-diff-pair",groupId:pair.id,step});}} hint={`Провода ${pair.wireIds.map(id=>history.present.wires.find(wire=>wire.id===id)?.circuit||id).join(" и ")}. Шаг сохраняется в паре Э4; на чертеже учитывается масштаб толщины.`}/>)}
          <DrawingRangeControl label="Диаметры 1:" unit="" digits={1} accessibleLabel="Соотношение диаметров оболочек" min={1.1} max={4} step={.1} value={coveringRatioPreview??history.present.drawingDocuments?.coveringDiameterRatio??2} onPreview={setCoveringRatioPreview} onCommit={coveringDiameterRatio=>{if(coveringDiameterRatio!==(history.present.drawingDocuments?.coveringDiameterRatio??2))run({type:"set-drawing-documents",documents:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),coveringDiameterRatio}});}} hint="Глобальное правило 1:x для соседних оболочек. При увеличении ширины переходы сохраняют форму; локальные ширины и материал не меняются."/>
          <DrawingRangeControl label="Радиус" accessibleLabel="Радиус изгибов чертежа" min={0} max={200} step={1} digits={0} unit="" value={bendRadiusPreview??drawingBendRadius(history.present)} onPreview={setBendRadiusPreview} onCommit={bendRadius=>{if(bendRadius!==drawingBendRadius(history.present))run({type:"set-drawing-documents",documents:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),bendRadius}});}} hint="Радиус в координатах чертежа: 0 — острый угол. На коротких плечах радиус автоматически уменьшается. Заданные длины проводов и точки перегиба сохраняются."/>
          <DrawingRangeControl label="Позиции" accessibleLabel="Масштаб позиционных обозначений" min={.25} max={4} step={.05} value={leaderScalePreview??history.present.drawingDocuments?.leaderScale??1} onPreview={setLeaderScalePreview} onCommit={leaderScale=>{if(leaderScale!==(history.present.drawingDocuments?.leaderScale??1))run({type:"set-drawing-documents",documents:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),leaderScale}});}} hint="Размер кружков, номеров и точек выносок. Ручное положение сохраняется. Escape отменяет изменение; отпускание ползунка сохраняет его одним шагом отмены."/>
          <DrawingRangeControl label="Индексы ×" accessibleLabel="Масштаб индексов объектов" min={.25} max={4} step={.05} value={indexScalePreview??history.present.drawingDocuments?.indexScale??1} onPreview={setIndexScalePreview} onCommit={indexScale=>{if(indexScale!==(history.present.drawingDocuments?.indexScale??1))run({type:"set-drawing-documents",documents:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),indexScale}});}} hint="Общий размер индексов соединителей, проводов и оболочек. Размер сохраняется в этом чертеже."/>
          <DrawingRangeControl label="Размеры ×" accessibleLabel="Масштаб размерных обозначений" min={.25} max={4} step={.05} value={dimensionScalePreview??history.present.drawingDocuments?.dimensionScale??1} onPreview={setDimensionScalePreview} onCommit={dimensionScale=>{if(dimensionScale!==(history.present.drawingDocuments?.dimensionScale??1))run({type:"set-drawing-documents",documents:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),dimensionScale}});}} hint="Общий размер текста, стрелок, точек и линий размеров. Значения длин и привязки не меняются."/>
          <DrawingRangeControl label="Мин. нахлёст" accessibleLabel="Минимальная длина новой оболочки поверх оболочки" min={20} max={500} step={5} digits={0} unit="px" value={minimumOverlapPreview??history.present.drawingDocuments?.minimumCoveringOverlapPx??100} onPreview={setMinimumOverlapPreview} onCommit={minimumCoveringOverlapPx=>{if(minimumCoveringOverlapPx!==(history.present.drawingDocuments?.minimumCoveringOverlapPx??100))run({type:"set-drawing-documents",documents:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),minimumCoveringOverlapPx}});}} hint="Новая оболочка занимает 5% длины нижней, но не меньше заданного значения; если нижняя короче, покрывается доступный участок."/>
          <DrawingRangeControl label="Выступ ОП" accessibleLabel="Ширина выступа оболочки ОП на переходе" min={0} max={24} step={1} digits={0} unit="px" value={opCoveringEdgePreview??history.present.drawingDocuments?.opCoveringEdgePx??2} onPreview={setOpCoveringEdgePreview} onCommit={opCoveringEdgePx=>{if(opCoveringEdgePx!==(history.present.drawingDocuments?.opCoveringEdgePx??2))run({type:"set-drawing-documents",documents:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),opCoveringEdgePx}});}} hint="Дополнительная ширина краёв оболочки на переходе ОП↔П. Нулевое значение убирает выступ; значение сохраняется в этом чертеже."/>
          <label>Размеры<select aria-label="Общее направление размеров" value={history.present.drawingDocuments?.dimensionMode??"aligned"} onChange={e=>run({type:"set-drawing-documents",documents:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),dimensionMode:e.target.value as import("./drawing-dimensions").DimensionMode}})}><option value="aligned">Между концами</option><option value="horizontal">Горизонтально</option><option value="vertical">Вертикально</option><option value="path">Вдоль пайпа</option></select></label>
          <button type="button" className="ui-control he-control-action" aria-pressed={history.present.drawingDocuments?.showDimensions??!!history.present.drawingDocuments?.dimensions?.length} onClick={()=>run({type:"set-drawing-documents",documents:toggleDrawingDimensions(history.present)})}><span className="he-doc-icon"><DocumentIcon kind="dimensions" /></span>Отобразить размеры</button>
          <button type="button" className="ui-control he-control-action" disabled={!volumeEligible} aria-pressed={history.present.drawingDocuments?.volumeShading!==false} onClick={()=>{const documents=history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]};run({type:"set-drawing-documents",documents:{...documents,volumeShading:documents.volumeShading===false}})}}><span className="he-doc-icon"><DocumentIcon kind="volume" /></span>Объёмный свет</button>
          <InfoHint>Затемнение краёв и светлая середина пайпов, проводов и покрытий. Сохраняется для этого чертежа; отключение не меняет материалы, цвета и размеры.</InfoHint>
        </section>}
          <section className="he-utility-section he-documents-section" aria-labelledby="he-documents-heading">
            <h3 id="he-documents-heading">Документы</h3>
            {view === "drawing" && <button type="button" className="ui-control he-document-action" onClick={()=>run({type:"set-drawing-documents",documents:addDrawingPositions(history.present,drawingPerimeters)})}><span className="he-doc-icon"><DocumentIcon kind="positions" /></span>Добавить позиции</button>}
            {view === "drawing" && <button type="button" className="ui-control he-document-action he-document-toggle" aria-pressed={positionsVisible} aria-label={positionsVisible?"Скрыть позиции":"Показать позиции"} title={positionsVisible?"Скрыть позиции на чертеже":"Показать позиции на чертеже"} onClick={()=>run({type:"set-drawing-documents",documents:setDrawingPositionsVisibility(history.present,!positionsVisible,drawingPerimeters)})}><span className="he-doc-icon"><DocumentIcon kind="visibility" visible={positionsVisible} /></span>Позиции</button>}
            {(["connections","cut","bom"] as const).map(kind=><button type="button" className="ui-control he-document-action" key={kind} disabled={kind==="cut"&&!routeCutReadiness(history.present,resource.sourceFingerprint,saveState!=="saved").ready} title={kind==="cut"?routeCutReadiness(history.present,resource.sourceFingerprint,saveState!=="saved").message:undefined} onClick={()=>{if(kind!=="connections"&&view!=="drawing"){setView("drawing");onViewChange?.("drawing");}const documents=history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]};if(!documents.tables.some(t=>t.kind===kind))run({type:"set-drawing-documents",documents:{...documents,tables:[...documents.tables,{id:crypto.randomUUID(),kind,position:{x:20,y:20},dock:"bottom",width:960,height:300}]}});}}><span className="he-doc-icon"><DocumentIcon kind={kind} /></span>{kind==="bom"?"Спецификация":kind==="cut"?"Карта резки":"Таблица соединений"}</button>)}
          </section>
        </>}
        drawingWindows={camera=><DrawingTableWindows connectionTableSettings={connectionTableSettings} sourceFingerprint={resource.sourceFingerprint} wireOptions={wireLookup.options} wireDatabaseOptions={wireLookup.databaseOptions} onWireSearch={wireLookup.search} perimeters={drawingPerimeters} view={view} document={history.present} camera={camera} quantity={harnessQuantity} revision={resource.revision} unsaved={saveState!=="saved"} selectedIds={[...selectedObjectIds,...related.rowIds]} onChange={documents=>run({type:"set-drawing-documents",documents})} onCommand={run} onReveal={ids=>{setRelatedSourceIds(ids);setSelectedObjectId(null);setSelectedObjectIds([]);}}/>}
        onDimensionCreate={(wireId,from,to,pointCount,mode,auxiliary)=>{
          const wire=history.present.wires.find(w=>w.id===wireId),segment=history.present.physicalTopology?.segments.find(s=>s.id===wireId);if(!wire&&!segment)return;
          const id=crypto.randomUUID(),documents=history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]};
          if(segment){
            if(run({type:"add-visible-pipe-dimension",id,segmentId:segment.id,from,to,pointCount,mode:history.present.drawingDocuments?.dimensionMode??mode,auxiliary})){setSelectedObjectId(id);setSelectedObjectIds([id]);}
            return;
          }
          if(run({type:"set-drawing-documents",documents:{...documents,dimensions:[...documents.dimensions??[],{id,wireId,auxiliary,modeOverride:false,from:Math.min(from,to),to:Math.max(from,to),pointCount,routeKey:dimensionRouteKey(history.present,wire!),mode,offset:40,lengthMm:null}]}})){setSelectedObjectId(id);setSelectedObjectIds([id]);}
        }}
        onPositionRailCreate={(start,end,leaderIds)=>{
          const documents=history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]};
          const rail={id:crypto.randomUUID(),start,end,leaderIds};
          run({type:"set-drawing-documents",documents:createPositionRail(documents,rail)});
        }}
        onGraphicCreate={graphic=>{const documents=history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]};if(run({type:"set-drawing-documents",documents:{...documents,graphics:[...(documents.graphics??[]),graphic]}})){setSelectedObjectId(graphic.id);setSelectedObjectIds([graphic.id]);}}}
        drawingSnaps={drawingSnaps}
        drawingAngleStep={graphicAngleStep}
        selectedGraphic={history.present.drawingDocuments?.graphics?.some(g=>selectedObjectIds.includes(g.id))??false}
        canPasteGraphic={graphicsClipboardRef.current.length>0}
        onGraphicCopy={()=>{graphicsClipboardRef.current=(history.present.drawingDocuments?.graphics??[]).filter(g=>selectedObjectIds.includes(g.id));}}
        onGraphicPaste={()=>{const documents=history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]},copies=graphicsClipboardRef.current.map(g=>({...g,id:crypto.randomUUID(),view,points:g.points.map(p=>({x:p.x+20,y:p.y+20}))}));if(copies.length&&run({type:"set-drawing-documents",documents:{...documents,graphics:[...(documents.graphics??[]),...copies]}})){setSelectedObjectIds(copies.map(g=>g.id));setSelectedObjectId(copies.at(-1)?.id??null);}}}
        onGraphicDelete={()=>{const commands=selectedEditorDeletionCommands(history.present,selectedObjectIds);commands.filter(c=>c.type==="set-drawing-documents"&&c.documents.graphics!==undefined).forEach(run);setSelectedObjectIds([]);setSelectedObjectId(null);}}
        onUndo={()=>setHistory(current=>current?undoEditorCommand(current):current)}
        angleStep={graphicAngleStep}
        onAngleStepChange={setGraphicAngleStep}
        onDrawingSnapsChange={setDrawingSnaps}
        relationPanel={()=><>{view === "drawing" && <PhysicalTopologyPanel mode="actions" document={history.present} selectedId={selectedObjectId} selectedIds={selectedObjectIds} onChange={topology => run({ type: "set-physical-topology", topology })} onSelect={(id,additive) => { setRelatedSourceIds([]); setSelectedObjectId(id); setSelectedObjectIds(additive ? [...new Set([...selectedObjectIds,id])] : [id]); }} />}{view==="drawing"&&<SpecificationItemsPanel documents={history.present.drawingDocuments} selectedId={selectedObjectId} onChange={documents=>run({type:"set-drawing-documents",documents})} onSelect={id=>{setSelectedObjectId(id);setSelectedObjectIds([id]);}}/>}{view==="drawing"&&<DrawingDimensionsPanel pipeInterval={selectedPipeInterval} document={history.present} selectedId={selectedObjectId} onChange={documents=>run({type:"set-drawing-documents",documents})}/>} {<DrawingDocumentsPanel connectionTableSettings={connectionTableSettings} sourceFingerprint={resource.sourceFingerprint} unsaved={saveState!=="saved"} wireOptions={wireLookup.options} wireDatabaseOptions={wireLookup.databaseOptions} onWireSearch={wireLookup.search} perimeters={drawingPerimeters} availableKinds={view==="drawing"?undefined:["connections"]} document={history.present} quantity={harnessQuantity} selectedId={selectedObjectId} selectedIds={[...selectedObjectIds,...related.wireIds,...related.componentIds,...related.rowIds]} onChange={documents=>run({type:"set-drawing-documents",documents})} onCommand={run} onReveal={ids=>{setRelatedSourceIds(ids);setSelectedObjectId(null);setSelectedObjectIds([]);}} />}<HarnessRelationsPanel sourceFingerprint={resource.sourceFingerprint} showCut={view==="drawing"} onOpenCut={view==="drawing"?()=>run({type:"set-drawing-documents",documents:{...(history.present.drawingDocuments??{tables:[],leaders:[],bomOrder:[]}),tables:[...(history.present.drawingDocuments?.tables??[]),{id:crypto.randomUUID(),kind:"cut",position:{x:20,y:20}}]}}):undefined} revision={resource.revision} onCommand={run} document={history.present} projectId={projectId} harnessId={harnessId} quantity={harnessQuantity} related={related} wholeNet={wholeNet} onWholeNet={setWholeNet} unsaved={saveState !== "saved"} hiddenCount={related.wireIds.filter(id => { const wire = history.present.wires.find(w => w.id === id); return wire && layers.some(layer => layer.id === wire.layerIds[view] && !layer.visible); }).length}
          onClear={() => {setRelatedSourceIds([]); setSelectedObjectId(null); setSelectedObjectIds([]);}}
          onReveal={id => {
            const found = id && selectionIndex ? resolveHarnessSelection(selectionIndex, [id], wholeNet) : related;
            if (id) { setRelatedSourceIds([id]); setSelectedObjectId(null); setSelectedObjectIds([]); }
            setEditingObjectId(null); setView("drawing"); onViewChange?.("drawing");
            setRevealRequest({token: Date.now(), objectIds: [...found.wireIds, ...found.componentIds]});
          }} />
        </>}
        cables={(previewResult.document ?? history.present).cables}
        e4Overlays={view === "e4" ? {
          crossingStyle: history.present.views.e4.wireCrossingStyle,
          junctions: history.present.junctions,
          diffPairs: history.present.diffPairs,
          screens: history.present.screens,
        } : undefined}
        e4RoutingMode={history.present.views.e4.e4RoutingMode ?? "orthogonal"}
        componentTemplateViewInstances={componentTemplateViewInstances}
        resolveComponentTemplateAssetUrl={resolveComponentTemplateAssetUrl}
        saveState={saveState}
        onSaveRequest={localCopy ? () => {
          const current = historyRef.current?.present;
          if (current) {
            try {
              saveLocalDrawingCopy(current, hiddenObjectIds, backgroundOpacity, localCopy.onSave);
              setSaveState("saved");
              setMessage("");
            } catch (error) {
              setSaveState("error");
              setMessage(error instanceof Error ? error.message : "Не удалось сохранить фрагмент.");
            }
          }
        } : () => void flushSave()}
        localCopyControls={localCopy ? {
          hiddenObjectIds, backgroundOpacity,
          onHiddenObjectIdsChange: ids => { setHiddenObjectIds(ids); localCopy.onHiddenObjectIdsChange?.(ids); },
          onBackgroundOpacityChange: opacity => { setBackgroundOpacity(opacity); localCopy.onBackgroundOpacityChange?.(opacity); },
          onObjectsIsolate: localCopy.onIsolateObjects ? isolateLocalObjects : undefined,
          onCancel: localCopy.onCancel,
        } : undefined}
        onDrawingScale={(connectorId,drawingId,scale)=>run({type:"set-drawing-placement",connectorId,drawingId,scale})}
        onDrawingMove={(connectorId,drawingId,offset)=>run({type:"set-drawing-placement",connectorId,drawingId,offset})}
        propertyInspector={selectedObjectId && history.present.drawingDocuments?.graphics?.some(g=>g.id===selectedObjectId) ? <DrawingObjectProperties document={history.present} objectId={selectedObjectId} selectedIds={selectedObjectIds} onCommand={run} instances={componentTemplateViewInstances} wireMaterialOptions={wireLookup.databaseOptions} onSelect={id=>{setSelectedObjectId(id);setSelectedObjectIds([id]);}}/> : selectedObjectId && (history.present.drawingDocuments?.specificationItems?.some(i=>i.id===selectedObjectId) || history.present.drawingDocuments?.dimensions?.some(d=>d.id===selectedObjectId) || history.present.drawingDocuments?.tables.some(t=>t.id===selectedObjectId) || history.present.drawingDocuments?.leaders.some(l=>l.id===selectedObjectId||`${l.id}:anchor`===selectedObjectId) || history.present.drawingDocuments?.rails?.some(r=>selectedObjectId===r.id||selectedObjectId===`${r.id}:start`||selectedObjectId===`${r.id}:end`) || history.present.physicalTopology?.coverings?.some(c=>c.id===selectedObjectId) || history.present.physicalTopology?.nodes.some(n=>n.id===selectedObjectId) || history.present.physicalTopology?.segments.some(s=>s.id===selectedObjectId) || history.present.physicalTopology?.joiningPipes?.some(p=>p.id===selectedObjectId)) ? <></> : selectedConnector ? (<>
          {view==="e4" && (()=>{
            const instance=componentTemplateViewInstances.find(i=>i.objectId===selectedConnector.id);
            const drawings=instance ? projectE4DrawingCompanions(instance,{x:0,y:0},300,resolveComponentTemplateAssetUrl) : [];
            return drawings.length ? <section className="he-companion-list" aria-label="Рисунки компонента"><header className="ui-section-heading"><strong>Рисунки артикула</strong><InfoHint>Показать или скрыть рисунок на Э4. Перетаскивайте рисунок мышью; прямая пунктирная линия связывает его с таблицей. Группа фигур перемещается как один рисунок.</InfoHint></header>{drawings.map(d=><label key={d.drawingId}><input type="checkbox" aria-label={`Показать ${d.label}`} checked={d.visible} disabled={selectedConnectorLayer?.locked===true} onChange={e=>run({type:"set-drawing-placement",connectorId:selectedConnector.id,drawingId:d.drawingId,visible:e.target.checked})}/>{d.label}<DrawingScaleControl label={`Масштаб ${d.label}`} value={drawingScale(selectedConnector.drawingPlacements,d.drawingId)} disabled={selectedConnectorLayer?.locked===true} onChange={scale=>run({type:"set-drawing-placement",connectorId:selectedConnector.id,drawingId:d.drawingId,scale})}/></label>)}</section> : null;
          })()}
          <E4ConnectorInspector
            connector={selectedConnector}
            onRefreshTerminals={refreshTemplateTerminals}
            refreshingTerminals={refreshingTerminals}
            series={selectedConnectorSeries}
            templateArticleOptions={selectedTemplateArticleOptions}
            onTemplateArticleSelect={selectTemplateArticle}
            terminalArticles={terminalLookup.articles}
            terminalDetails={(article) => resolveTerminalDetails(article, terminalLookup.records)}
            wireOptions={wireLookup.options} wireMaterialOptions={wireLookup.databaseOptions} wireLookupMessage={wireLookup.message} onWireSearch={wireLookup.search}
            onTerminalSearch={terminalLookup.search}
            wireColors={editorWireColors}
            disabled={selectedConnectorLayer?.locked === true}
            onCommand={run}
          />
        </>) : view === "drawing" && selectedWireIds.length >= 2 ? (
          <CableSelectionPanel
            selectedWireIds={selectedWireIds}
            cable={selectedCable}
            disabled={selectedWiresLocked}
            onCreate={() => run({ type: "add-cable", cable: {
              id: crypto.randomUUID(), memberWireIds: selectedWireIds, lengthMm: null,
              endCorrectionFromMm: 0, endCorrectionToMm: 0, cutRoundingStepMm: 1,
            } })}
            onUpdate={(patch) => selectedCable && run({ type: "update-cable", cableId: selectedCable.id, ...patch })}
            onMaterialClear={() => selectedCable && run({ type: "update-cable", cableId: selectedCable.id, materialBinding: null })}
            onRemove={() => selectedCable && run({ type: "remove-cable", cableId: selectedCable.id })}
          />
        ) : undefined}
        canvasEditor={selectedConnector ? (
          <E4ConnectorInspector
            connector={selectedConnector}
            onRefreshTerminals={refreshTemplateTerminals}
            refreshingTerminals={refreshingTerminals}
            series={selectedConnectorSeries}
            templateArticleOptions={selectedTemplateArticleOptions}
            onTemplateArticleSelect={selectTemplateArticle}
            terminalArticles={terminalLookup.articles}
            terminalDetails={(article) => resolveTerminalDetails(article, terminalLookup.records)}
            wireOptions={wireLookup.options} wireMaterialOptions={wireLookup.databaseOptions} wireLookupMessage={wireLookup.message} onWireSearch={wireLookup.search}
            onTerminalSearch={terminalLookup.search}
            wireColors={editorWireColors}
            disabled={selectedConnectorLayer?.locked === true}
            onCommand={run}
            mode="canvas"
            editing={editingObjectId === selectedConnector.id}
            onEditingChange={(editing) => setEditingObjectId(editing ? selectedConnector.id : null)}
          />
        ) : undefined}
        diagnostics={[...diagnostics.map((diagnostic) => ({
          id: diagnostic.id,
          objectId: diagnostic.target.objectId,
          label: diagnostic.designation,
          message: diagnostic.message,
        })), ...routingIssues.map((issue) => ({
          id: `routing:${issue.wireId}`, objectId: issue.wireId,
          label: history.present.wires.find((wire) => wire.id === issue.wireId)?.circuit || issue.wireId,
          message: issue.message,
        }))]}
        previewMessage={previewResult.error}
        onViewChange={(nextView) => {
          setJoiningPipeDraft(null);
          setEditingObjectId(null);
          setView(nextView);
          onViewChange?.(nextView);
        }}
        onRouteRequest={!localCopy && onRouteRequest ? async () => {
          if (await flushSave()) onRouteRequest();
        } : undefined}
        onSectionChange={!localCopy && onSectionChange ? async section => {
          if (section === "e4" || section === "drawing") return;
          if (await flushSave()) await onSectionChange(section);
        } : undefined}
        onSelectedObjectChange={(objectId) => {
          setRelatedSourceIds([]);
          setSelectedObjectId(objectId);
          setEditingObjectId((current) => current === objectId ? current : null);
        }}
        onSelectedObjectIdsChange={setSelectedObjectIds}
        onCatalogItemActivate={addCatalogItem}
        onCatalogSourceChange={catalog.selectSource}
        onCatalogQueryChange={catalog.changeQuery}
        onCatalogLoadMore={catalog.loadMore}
        onCatalogRetry={catalog.retry}
        onWireMaterialClear={(wireId) => run({ type: "update-wire", wireId, materialBinding: null })}
        wireMaterialOptions={wireLookup.databaseOptions}
        onWireMaterialSelect={(wireId,option)=>{
          if(!option.materialBinding)return;
          const wire=history.present.wires.find(item=>item.id===wireId);
          const end=wire&&[wire.from,wire.to].find(endpoint=>"connectorId" in endpoint);
          if(end&&"connectorId" in end)run({type:"update-contact",connectorId:end.connectorId,contactId:end.contactId,
            wire:option.mark,wireSection:option.section,wireDiameterMm:option.diameterMm??null,
            materialBinding:option.materialBinding,...(option.color?{color:option.color}:{})});
          else run({type:"update-wire",wireId,materialBinding:option.materialBinding});
        }}
        selectedWireStripProfiles={selectedObjectIds.length === 1
          ? history.present.wires.find((wire) => wire.id === selectedObjectIds[0])?.stripProfiles
          : undefined}
        activeWireStripEnd={activeWireStripEnd}
        onActiveWireStripEndChange={setActiveWireStripEnd}
        onWireStripProfileClear={(wireId, end) => run({ type: "set-wire-strip-profile", wireId, end, profile: null })}
        onDrawingEndStyleChange={(wireId, end, style) => run({ type: "set-wire-drawing-end-style", wireId, end, style })}
        onDrawingEndStylesChange={(wireIds, end, style) => run({ type: "set-wire-drawing-end-styles", wireIds, end, style })}
        onDrawingEndEndpointChange={(wireId, end, position) => run({ type: "set-wire-drawing-endpoint", wireId, end, position })}
        onDrawingEndBulkXChange={(wireIds, end, x) => run({ type: "set-wire-drawing-endpoints-x", wireIds, end, x })}
        onRelatedObjectsSelect={ids=>{setRelatedSourceIds(ids);setSelectedObjectId(null);setSelectedObjectIds([]);}}
        onObjectMove={(objectId, point, mode="carry") => {
          const index=moveDrawingIndex(history.present,objectId,point,scene);
          if(index){run({type:"set-drawing-documents",documents:index});return;}
          const annotation=moveDrawingAnnotation(history.present,objectId,point,drawingPerimeters,drawingSnaps);
          if(annotation){run({type:"set-drawing-documents",documents:annotation});return;}
          const topology = history.present.physicalTopology;
          const joiningPipe=topology?.joiningPipes?.find(pipe=>pipe.id===objectId);
          if(joiningPipe){run({type:"move-joining-pipe",pipeId:joiningPipe.id,delta:point});return;}
          const opEndpoint=joiningPipeEndpoint(topology,objectId);
          if(opEndpoint){run({type:"update-joining-pipe",pipeId:opEndpoint.pipe.id,[opEndpoint.side==="from"?"start":"end"]:{x:point.x+5,y:point.y+5}});return;}
          const opExit=joiningPipeExit(topology,objectId);
          if(opExit){run({type:"update-joining-pipe-exit",pipeId:opExit.pipe.id,side:opExit.side,position:{x:point.x+5,y:point.y+5}});return;}
          const node = topology?.nodes.find(n => n.id === objectId);
          if (topology && node) { const original=physicalNodePoint(history.present,node),display=pipeBundleNodePoint(history.present,node.id,original);
            run({type:"move-physical-node",nodeId:node.id,position:{x:point.x+5+original.x-display.x,y:point.y+5+original.y-display.y},mode}); }
          else run({type:"move-connector",connectorId:objectId,view,position:point,physicalDragMode:mode});
        }}
        onPipeIntervalSelect={(id,from,to)=>setSelectedPipeInterval({id,from,to})}
        onCoveringDrag={(id,spanIndex,part,start,point,phase)=>{
          if(phase==="cancel"){setCoveringPreview(null);return;}
          const covering=moveCovering(history.present,id,spanIndex,part,start,point);if(!covering)return;
          if(phase==="preview")setCoveringPreview(covering);
          else {setCoveringPreview(null);const t=history.present.physicalTopology!;run({type:"set-physical-topology",topology:{...t,coverings:t.coverings?.map(c=>c.id===id?covering:c)}});}
        }}
        onPhysicalNodesConnect={(from,to)=>{const t=history.present.physicalTopology;if(t&&from!==to){const existing=t.segments.find(s=>s.from===from&&s.to===to||s.from===to&&s.to===from);const id=existing?.id??crypto.randomUUID();if(existing||run({type:"set-physical-topology",topology:routePhysicalWires(history.present,{...t,segments:[...t.segments,{id,from,to,path: { kind: "routed" as const, points: [] }}]})})){setSelectedObjectId(id);setSelectedObjectIds([id]);}}}}
        onPhysicalNodeConnectToSegment={(fromNodeId,segmentId,point)=>{const t=history.present.physicalTopology;if(!t)return;try{const ids={junction:crypto.randomUUID(),segment:crypto.randomUUID(),continuation:crypto.randomUUID()};const topology=routePhysicalWires(history.present,connectPhysicalNodeToSegment(history.present,fromNodeId,segmentId,unprojectPipeBundlePoint(history.present,segmentId,point),ids));if(run({type:"set-physical-topology",topology})){setSelectedObjectId(ids.segment);setSelectedObjectIds([ids.segment]);}}catch(error){setMessage(error instanceof Error?error.message:"Не удалось присоединить точку к пайпу.");}}}
        onPhysicalContextAction={(segmentId,point,action,target="segment")=>{
          const t=history.present.physicalTopology;if(!t)return;
          if(target!=="covering")point=unprojectPipeBundlePoint(history.present,segmentId,point);
          if(action==="remove-pipe"){
            if(run({type:"remove-physical-segment",segmentId})){setSelectedObjectId(null);setSelectedObjectIds([]);}
            return;
          }
          if(action==="branch"){
            try{const ids={junction:crypto.randomUUID(),continuation:crypto.randomUUID(),tip:crypto.randomUUID(),branch:crypto.randomUUID()};
            if(run({type:"set-physical-topology",topology:branchPhysicalSegment(history.present,segmentId,point,ids)})){setSelectedObjectId(ids.tip);setSelectedObjectIds([ids.tip]);}}
            catch(error){setMessage(error instanceof Error?error.message:"Не удалось создать ответвление.");}return;
          }
          const source=target==="covering"?t.coverings?.find(c=>c.id===segmentId):undefined;
          if(target==="covering"&&!source)return;
          const covering=source?standardCoveringOver(history.present,source,action,crypto.randomUUID(),point):standardCovering(history.present,segmentId,point,action,crypto.randomUUID());
          if(run({type:"set-physical-topology",topology:{...t,coverings:[...t.coverings??[],covering]}})){setSelectedObjectId(covering.id);setSelectedObjectIds([covering.id]);}
        }}
        onObjectMovePreview={previewObjectMove}
        onObjectEditRequest={(objectId) => {
          setSelectedObjectId(objectId);
          setSelectedObjectIds([objectId]);
          setEditingObjectId(objectId);
        }}
        onWireConnect={(from, to) => {
          const resolveEndpoint = (endpoint: typeof from): WireEndpoint | null => {
            if ("screenId" in endpoint) return createScreenEndpoint(endpoint.screenId, endpoint.screenTerminalSide);
            const connector = history.present.connectors.find((item) => item.id === endpoint.connectorId);
            const contact = connector && (view === "e4" ? connectorE4Contacts(connector) : connector.contacts)[endpoint.contactIndex];
            return contact ? { connectorId: endpoint.connectorId, contactId: contact.id } : null;
          };
          const fromEndpoint = resolveEndpoint(from);
          const toEndpoint = resolveEndpoint(to);
          if (!fromEndpoint || !toEndpoint) return;
          const id = crypto.randomUUID();
          run({
            type: "add-wire",
            wire: createRoutedWire(id, fromEndpoint, toEndpoint),
          });
          setSelectedObjectId(id);
          setSelectedObjectIds([id]);
        }}
        onWireReconnect={(wireId, end, target) => {
          const endpoint: WireEndpoint | null = "screenId" in target
            ? createScreenEndpoint(target.screenId, target.screenTerminalSide)
            : (() => {
              const connector = history.present.connectors.find((item) => item.id === target.connectorId);
              const contact = connector && (view === "e4" ? connectorE4Contacts(connector) : connector.contacts)[target.contactIndex];
              return contact ? { connectorId: target.connectorId, contactId: contact.id } : null;
            })();
          if (!endpoint) return;
          const existing = history.present.wires.find(item => item.id === wireId);
          const terminalCorrection = existing
            ? wireEndTerminalCorrections(history.present, { ...existing, [end]: endpoint }, terminalLookup.records)[end]?.plusMm
            : undefined;
          const reconnected = run({
            type: "reconnect-wire",
            wireId,
            end,
            endpoint,
          });
          if (reconnected && terminalCorrection !== undefined && terminalCorrection !== null) run({
            type: "update-wire", wireId,
            ...(end === "from" ? { endCorrectionFromMm: terminalCorrection } : { endCorrectionToMm: terminalCorrection }),
          });
          setSelectedObjectId(wireId);
          setSelectedObjectIds([wireId]);
        }}
        onWireConnectToWire={(from, targetWireId, point) => {
          const fromEndpoint: WireEndpoint | null = "screenId" in from
            ? createScreenEndpoint(from.screenId, from.screenTerminalSide)
            : (() => {
              const connector = history.present.connectors.find((item) => item.id === from.connectorId);
              const contact = connector && (view === "e4" ? connectorE4Contacts(connector) : connector.contacts)[from.contactIndex];
              return contact ? { connectorId: from.connectorId, contactId: contact.id } : null;
            })();
          const targetWire = history.present.wires.find((item) => item.id === targetWireId);
          if (!fromEndpoint || !targetWire) return;
          const wireId = crypto.randomUUID();
          const existingJunction = history.present.junctions.find((junction) =>
            junction.wireIds.includes(targetWireId) &&
            Math.hypot(junction.position.x - point.x, junction.position.y - point.y) < 0.01);
          const junctionId = existingJunction?.id ?? crypto.randomUUID();
          const toEndpoint = createJunctionEndpoint(junctionId);
          const terminalCorrections = wireEndTerminalCorrections(history.present, { from: fromEndpoint, to: toEndpoint }, terminalLookup.records);
          const base = createWire(wireId, fromEndpoint, toEndpoint, null, targetWire.circuit, "#334155",
            terminalCorrections.from?.plusMm ?? 0, terminalCorrections.to?.plusMm ?? 0);
          const start = wireEndpointE4Anchor(history.present, fromEndpoint);
          const branchWire = start ? {
            ...base,
            e4Route: createOrthogonalE4Route(start, { position: point, leadDirection: null }),
          } : base;
          const succeeded = existingJunction
            ? run({ type: "add-wire", wire: branchWire, targetWireId })
            : run({
              type: "create-junction",
              junction: { id: junctionId, position: point, wireIds: [targetWireId, wireId] },
              branchWire,
            });
          if (succeeded) {
            setSelectedObjectId(wireId);
            setSelectedObjectIds([wireId]);
          }
        }}
        onWireReconnectToWire={(wireId, end, targetWireId, point) => {
          const existingJunction = history.present.junctions.find((junction) =>
            junction.wireIds.includes(targetWireId) &&
            Math.hypot(junction.position.x - point.x, junction.position.y - point.y) < 0.01);
          const succeeded = existingJunction
            ? run({
              type: "connect-wire-to-wire",
              wireId,
              end,
              targetWireId,
              junctionId: existingJunction.id,
              position: existingJunction.position,
            })
            : run({
              type: "connect-wire-to-wire",
              wireId,
              end,
              targetWireId,
              junctionId: crypto.randomUUID(),
              position: point,
            });
          if (succeeded) {
            setSelectedObjectId(wireId);
            setSelectedObjectIds([wireId]);
          }
        }}
        onE4WireSegmentMove={(wireId, segmentIndex, coordinate) => run({
          type: "move-e4-wire-segment",
          wireId,
          segmentIndex,
          position: { x: coordinate, y: coordinate },
          detached: e4Detached,
        })}
        e4Detached={e4Detached}
        onE4DetachedChange={setE4Detached}
        onE4Reroute={() => {
          if (run({ type: "reroute-e4-wires", wireIds: selectedWireIds })) setE4Detached(false);
        }}
        onE4WireRoutePointRemove={(wireId, routeIndex) => run({
          type: "remove-e4-wire-route-point",
          wireId,
          pointIndex: routeIndex,
        })}
        onE4WireLabelPositionChange={(wireId, position) => run({
          type: "set-e4-wire-label-position",
          wireId,
          position,
        })}
        onE4ScreenPositionChange={(screenId, position) => run({
          type: "update-screen",
          screenId,
          position,
        })}
        onE4CrossingStyleChange={(style) => run({ type: "set-wire-crossing-style", view: "e4", style })}
        onE4DifferentialPairChange={changeDiffPair}
        onE4ScreenChange={changeScreen}
        onE4ClearGroup={() => {
          for (const group of history.present.diffPairs.filter((item) =>
            item.wireIds.some((id) => selectedWireIds.includes(id)))) {
            run({ type: "remove-diff-pair", groupId: group.id });
          }
          for (const screen of history.present.screens.filter((item) =>
            item.wireIds.some((id) => selectedWireIds.includes(id)))) {
            run({ type: "remove-screen", screenId: screen.id });
          }
        }}
        onFreeWireEndpointMove={(wireId,end,position)=>{setFreeEndPreviews([]);run({type:"set-wire-drawing-endpoint",wireId,end,position});}}
        onFreeWireEndpointsXChange={(wireIds,end,x)=>{setFreeEndPreviews([]);run({type:"set-wire-drawing-endpoints-x",wireIds,end,x});}}
        onFreeWireEndpointPreview={(wireId,end,position)=>setFreeEndPreviews(current=>position
          ? [...current.filter(preview=>preview.wireId!==wireId||preview.end!==end),{wireId,end,position}]
          : current.filter(preview=>preview.wireId!==wireId||preview.end!==end))}
        onDetachedPairAction={(wireIds,action)=>run({type:"set-detached-wire-pair",wireIds,action})}
        onWireRoutePointPreview={(id,index,point,mode,insert)=>setPipePreview(point&&(view==="e4"||history.present.wires.some(w=>w.id===id&&w.drawingEndpoints)||history.present.physicalTopology?.segments.some(s=>s.id===id)||history.present.physicalTopology?.joiningPipes?.some(p=>p.id===id))?{id,index,point,mode,insert}:null)}
        onWireRoutePointMove={(wireId, routeIndex, point, mode="carry", insert=false) => {
          if(view==="e4"){run({type:"edit-e4-bend",wireId,index:routeIndex,position:point,mode,insert});return;}
          const topology = history.present.physicalTopology;
          const op=topology?.joiningPipes?.find(p=>p.id===wireId);
          if(op){run({type:"edit-joining-pipe-bend",pipeId:wireId,index:routeIndex,position:point,mode,insert});return;}
          const segment = topology?.segments.find(s => s.id === wireId);
          if (topology && segment) {const command=resolvePhysicalRoutePointCommand(history.present,wireId,routeIndex,point,mode,insert);if(command)run(command);return;}
          const wire = history.present.wires.find((item) => item.id === wireId);
          if(wire?.drawingEndpoints){run({type:"edit-detached-wire-bend",wireId,index:routeIndex,position:point,insert});return;}
          if (!wire || routeIndex < 0 || routeIndex >= wire.drawingRoute.length) return;
          const route = wire.drawingRoute.map((item, index) => index === routeIndex ? point : item);
          run({ type: "set-wire-route", wireId, route });
        }}
        onWireRoutePointRemove={(wireId, routeIndex) => {
          const topology = history.present.physicalTopology;
          if(topology?.joiningPipes?.some(p=>p.id===wireId)){run({type:"edit-joining-pipe-bend",pipeId:wireId,index:routeIndex,position:{x:0,y:0},mode:"adjacent",remove:true});return;}
          const segment = topology?.segments.find(s => s.id === wireId);
          if (topology && segment) { const sceneObject=physicalTopologyScene(history.present).find(o=>o.id===wireId);
            const transition=sceneObject?.pipe?.joiningTransitionHandles?.find(handle=>handle.index===routeIndex);
            if(transition){const pipe=topology.joiningPipes?.find(p=>p.members[transition.memberIndex]?.segmentIds.includes(wireId));if(pipe)run({type:"update-joining-pipe-member-bend",pipeId:pipe.id,memberIndex:transition.memberIndex,side:transition.side,clear:true});return;}
            const authoredIndex=sceneObject?.pipe?.authoredHandleIndices?.[routeIndex];if(authoredIndex===-1)return;
            run({type:"remove-physical-bend",segmentId:wireId,index:(authoredIndex??routeIndex+1)-1}); return; }
          const wire = history.present.wires.find((item) => item.id === wireId);
          if(wire?.drawingEndpoints){run({type:"remove-detached-wire-bend",wireId,index:routeIndex});return;}
          if (!wire || routeIndex < 0 || routeIndex >= wire.drawingRoute.length) return;
          run({ type: "set-wire-route", wireId, route: wire.drawingRoute.filter((_, index) => index !== routeIndex) });
        }}
        onCanvasDoubleClick={addRoutePoint}
        onObjectsChange={(objects) => {
          const selected = selectedObjectId ? objects.find((item) => item.id === selectedObjectId) : null;
          if (!selected || selected.id.startsWith("dimension:")) return;
          const previous = scene.find((item) => item.id === selected.id);
          if (!previous) return;
          if (selected.kind === "connector") {
            if (selected.x !== previous.x || selected.y !== previous.y) {
              run({ type: "move-connector", connectorId: selected.id, view, position: { x: selected.x, y: selected.y } });
            } else if (selected.label !== previous.label) {
              run({ type: "update-connector", connectorId: selected.id, designation: selected.label });
            }
          } else if (selected.kind === "wire") {
            run(editorWireUpdateCommand(selected, previous));
          }
        }}
        onLayersChange={(nextLayers) => run({
          type: "replace-layers",
          view,
          layers: fromUiLayers(nextLayers, history.present.views[view].layers),
        })}
        onClose={localCopy ? localCopy.onCancel : onClose ? async () => {
          if (await flushSave()) onClose();
        } : undefined}
      /></HarnessEditorErrorBoundary>
      {(placementBusy || placementPending) && <div className={placementBusy ? "he-placement-busy" : "he-placement-pending"} role="status" aria-live="polite">
        {placementBusy
          ? "Закрепляем версию компонента в жгуте…"
          : <><span>Ответ сервера не получен. Повтор запроса безопасен и не создаст копию.</span><button type="button" onClick={() => void retryPendingPlacement()}>Повторить</button></>}
      </div>}
    </div>
  );
}
