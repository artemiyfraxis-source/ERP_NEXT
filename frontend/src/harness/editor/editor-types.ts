import type { WireEndStripProfiles } from "./model";
import type { WireBlankEnd } from "../WireBlankCatalog";

export type HarnessEditorView = "e4" | "drawing";

export type EditorTool =
  | "select"
  | "pan"
  | "connector"
  | "wire"
  | "text"
  | "graphic-contact" | "graphic-line" | "graphic-polyline" | "graphic-rectangle" | "graphic-ellipse" | "graphic-bezier" | "graphic-closed-contour" | "graphic-text"
  | "dimension-auxiliary" | "dimension" | "dimension-horizontal" | "dimension-vertical" | "position-rail";

export type EditorObjectKind = "specification-item" | "connector" | "wire" | "text" | "dimension" | "graphic-contact" | "graphic-line" | "graphic-polyline" | "graphic-rectangle" | "graphic-ellipse" | "graphic-bezier" | "graphic-closed-contour" | "graphic-text" | "physical-node" | "physical-segment" | "physical-covering" | "drawing-table" | "position-leader" | "leader-anchor" | "position-rail" | "rail-handle" | "object-index";

export interface EditorPoint {
  readonly x: number;
  readonly y: number;
}

/** Computed scene data only; never serialized into a harness document. */
export interface PhysicalPipeSceneData {
  readonly authoredPoints?: readonly EditorPoint[];
  readonly fromNodeId?: string;
  readonly toNodeId?: string;
  readonly midpoints?: readonly EditorPoint[];
  /** Gesture-start source station for each displayed midpoint. */
  readonly midpointSources?: readonly {readonly index:number;readonly point:EditorPoint}[];
  readonly midpointRegions?: readonly ("before-enter"|"enter"|"axis"|"exit"|"after-exit"|undefined)[];
  readonly controls: readonly EditorPoint[];
  readonly handles: readonly EditorPoint[];
  readonly wireIds: readonly string[];
  readonly role?: "pipe" | "joining-pipe";
  readonly joiningMember?: boolean;
  readonly memberSegmentIds?: readonly string[];
  readonly controlledHandles?: readonly number[];
  readonly controlledMidpoints?: readonly number[];
  readonly authoredHandleIndices?: readonly number[];
  readonly authoredHandleRegions?: readonly ("before-enter"|"enter"|"axis"|"exit"|"after-exit"|undefined)[];
  readonly joiningTransitionHandles?: readonly {readonly index:number;readonly memberIndex:number;readonly side:"enter"|"exit"}[];
  readonly joiningBoundaryHandles?: readonly {readonly index:number;readonly memberIndex:number;readonly boundary:"outerEnter"|"axisEnter"|"axisExit"|"outerExit"}[];
  readonly joiningTransitionMidpoints?: readonly {readonly index:number;readonly memberIndex:number;readonly side:"enter"|"exit"}[];
}
export interface PhysicalPortSceneData {
  readonly connectorId?: string;
  readonly direction: EditorPoint | null;
}

export interface EditorSceneObject {
  readonly id: string;
  readonly layerId: string;
  readonly kind: EditorObjectKind;
  readonly label: string;
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly color: string;
  readonly points?: readonly EditorPoint[];
  readonly paths?: readonly (readonly EditorPoint[])[];
  /** Frontmost spans of a round bundle; paths retain the complete route for selection. */
  readonly visibleWireStrokes?: readonly {readonly points:readonly EditorPoint[];readonly width:number;readonly radius?:number}[];
  /** Presentation only. Zero keeps sharp joins; no physical lengths change. */
  readonly routeRadius?: number;
  /** End-treatment presentation shares the wire identity and drawing layer. */
  readonly stripProfiles?: WireEndStripProfiles;
  /** Presentation-only end treatment for an independent drawing wire. */
  readonly drawingEndStyles?: Readonly<{ from: WireBlankEnd; to: WireBlankEnd }>;
  readonly pipe?: PhysicalPipeSceneData;
  readonly port?: PhysicalPortSceneData;
  readonly metadata?: Readonly<Record<string, string>>;
}

export interface EditorLayer {
  readonly id: string;
  readonly label: string;
  readonly visible: boolean;
  readonly locked: boolean;
}

export type CoaxTerminationCatalogDiagnosticCode =
  | "snapshot-identity-missing"
  | "record-identity-invalid"
  | "layers-missing"
  | "layer-invalid"
  | "layer-index-duplicate"
  | "layer-diameter-missing"
  | "layer-strip-length-missing";

export interface CoaxTerminationCatalogDiagnostic {
  readonly code: CoaxTerminationCatalogDiagnosticCode;
  readonly message: string;
  readonly layerIndex?: number;
}

/** A normalized active layer. Missing D or L is retained so an incomplete
 * published catalog row remains visible and can explain why it cannot bind. */
export interface CoaxTerminationCatalogLayer {
  readonly index: number;
  readonly diameterMm: number | null;
  readonly stripLengthMm: number | null;
}

export interface CoaxTerminationCatalogBinding {
  readonly sourceId: string;
  readonly snapshotId: string;
  readonly snapshotSha256: string;
  readonly recordId: string;
  readonly entityType: "coax-termination";
  readonly sourceKey: string;
  readonly layers: readonly {
    readonly index: number;
    readonly diameterMm: number;
    readonly stripLengthMm: number;
  }[];
}

export interface CoaxTerminationCatalogCandidate {
  readonly state: "ready" | "incomplete";
  readonly layers: readonly CoaxTerminationCatalogLayer[];
  readonly diagnostics: readonly CoaxTerminationCatalogDiagnostic[];
  /** Present only for an exact immutable snapshot and complete D+L layers. */
  readonly binding: CoaxTerminationCatalogBinding | null;
}

export interface EditorCatalogItem {
  readonly id: string;
  readonly title: string;
  readonly subtitle: string;
  readonly category: string;
  readonly accent: string;
  readonly placement?: "connector" | "reference-only";
  readonly sourceId?: string;
  readonly snapshotId?: string;
  readonly snapshotSha256?: string;
  readonly recordId?: string;
  readonly sourceKey?: string;
  readonly entityType?: string;
  readonly referenceDisplayName?: string;
  readonly outerDiameterMm?:number;
  readonly coaxTerminationCandidate?: CoaxTerminationCatalogCandidate;
  /** Library template metadata used when placing connector instances. */
  readonly templateKind?: "series" | "free";
  readonly seriesId?: string;
  readonly defaultPartNumber?: string;
  /** Exact immutable persistent template version used for a library placement. */
  readonly componentTemplateId?: string;
  readonly componentTemplateVersion?: number;
  /** All articles exposed by this family card. The editor places the family
   * with its first article and lets the selected instance switch the article. */
  readonly componentArticles?: readonly {
    readonly sourceId: string;
    readonly entityType: string;
    readonly articleKey: string;
  }[];
  readonly componentArticle?: {
    readonly sourceId: string;
    readonly entityType: string;
    readonly articleKey: string;
  };
}

export interface EditorCatalogSource {
  readonly id: string;
  readonly label: string;
  readonly description: string;
}

export interface EditorCamera {
  readonly offsetX: number;
  readonly offsetY: number;
  readonly zoom: number;
}
