import {restoreTemplateContactNumbers,templateContactNumbers} from "./template-contact-numbering";
import { parseManufacturingRoute, type ManufacturingRoute } from "../manufacturing/route-model";
import { commonParallelSpan } from "./e4-parallel-spans";
import { segmentPointDistance } from "./segment-geometry";
import { straightLeadEnd } from "./route-lead";
import { screenCrossSections, uprightScreenBody, clearScreenSections } from "./e4-screen-spans";
import { validDrawingScale } from "./drawing-scale";
import { validateDrawingDocuments, type DrawingDocuments } from "./drawing-documents";
import { parsePhysicalTopology } from "./physical-topology-validation";
import type { PhysicalTopology } from "./physical-topology-model";
import { materializedContactWorldRepresentation } from "./materialized-contact-representation";
import { terminalArticleLabel } from "./terminal-article-label";
import { clearDecorationSpans } from "./e4-decoration-spans";
import { wireBlankEnds, type WireBlankEnd } from "../WireBlankCatalog";

export type EditorView = "e4" | "drawing";

export interface Point {
  readonly x: number;
  readonly y: number;
}

export type ConnectorContactStatus = "available" | "not-connected";

export type ConnectorLibraryContactKind = "signal" | "power" | "third";

export type ConnectorLibraryBinding =
  | { readonly mode: "series"; readonly seriesId: string; readonly partNumber: string }
  | {
      readonly mode: "template";
      readonly contactNumbering?: "source-v1";
      readonly templateId: string;
      readonly templateVersion: number;
      readonly versionSha256: string;
      readonly articleVariantId: string;
      readonly article: ComponentTemplateArticleKeySnapshot;
      readonly snapshot: ComponentTemplateMaterializedSnapshot;
    }
  | { readonly mode: "free" };

export interface ComponentTemplateArticleKeySnapshot {
  readonly sourceId: string;
  readonly entityType: string;
  readonly articleKey: string;
}

export interface ComponentTemplateAssetSnapshot {
  readonly assetId: string;
  readonly sha256: string;
  readonly sizeBytes: number;
  readonly fileName: string;
  readonly mediaType: string;
}

export interface ComponentTemplateContactRepresentationSnapshot {
  readonly viewId: string;
  readonly viewName: string;
  readonly viewKind: "e4" | "drawing" | "additional";
  readonly pointId: string;
  readonly occurrenceKey?: string;
  readonly x: number;
  readonly y: number;
  readonly direction: "left" | "right" | "up" | "down";
}

export interface ComponentTemplateContactSnapshot {
  /** Logical-contact ID for fixed contacts and the stable occurrence key for repeated contacts. */
  readonly logicalContactId: string;
  readonly prototypeLogicalContactId: string;
  readonly sourceNumber: string;
  readonly name: string;
  readonly circuitText: string | null;
  readonly contactTypeGroupId: string | null;
  readonly contactType: string;
  readonly allowedTerminalArticleKeys: readonly ComponentTemplateArticleKeySnapshot[];
  readonly representations: readonly ComponentTemplateContactRepresentationSnapshot[];
}

/**
 * Placement-owned materialization of one exact template article. The full
 * reusable template remains in the project component snapshot; this compact
 * copy is enough for the editor to reopen and route the instance without
 * following a newer library head.
 */
export interface ComponentTemplateMaterializedSnapshot {
  readonly templateId: string;
  readonly templateVersion: number;
  readonly versionSha256: string;
  readonly code: string;
  readonly name: string;
  readonly articleVariantId: string;
  readonly article: ComponentTemplateArticleKeySnapshot;
  readonly articleBindings: readonly ComponentTemplateArticleKeySnapshot[];
  readonly assets: readonly ComponentTemplateAssetSnapshot[];
  readonly contacts: readonly ComponentTemplateContactSnapshot[];
}

export interface ConnectorLibraryContact {
  readonly kind: ConnectorLibraryContactKind;
  readonly ordinal: number;
}

export type ConnectorSchematicOrientation = "contacts-left" | "contacts-right";

export const connectorBaseColumnKeys = [
  "number",
  "contactType",
  "circuit",
  "terminal",
  "wire",
  "wireSection",
  "color",
] as const;

export type ConnectorBaseColumnKey = typeof connectorBaseColumnKeys[number];

export interface ConnectorBaseColumn {
  readonly key: ConnectorBaseColumnKey;
  readonly visible: boolean;
}

export interface ConnectorCustomField {
  readonly id: string;
  readonly label: string;
  readonly visible: boolean;
}

export interface ConnectorSchematicPresentation {
  /** Instance-only E4 row order. Contact identity and drawing geometry stay canonical. */
  readonly rowOrder?: readonly string[];
  readonly showName?: boolean;
  readonly orientation: ConnectorSchematicOrientation;
  readonly baseColumns: readonly ConnectorBaseColumn[];
  readonly customFields: readonly ConnectorCustomField[];
}

export type ConnectorE4TableColumn =
  | { readonly kind: "base"; readonly key: ConnectorBaseColumnKey; readonly x: number; readonly width: number }
  | { readonly kind: "custom"; readonly id: string; readonly label: string; readonly x: number; readonly width: number };

export const templateNameColumnId = "template-name";

export function connectorContactName(connector: ConnectorInstance, contact: ConnectorContact): string {
  return contact.nameOverride ?? (connector.libraryBinding?.mode === "template"
    ? connector.libraryBinding.snapshot.contacts.find(row => row.logicalContactId === contact.logicalContactId)?.name ?? ""
    : "");
}

export interface ConnectorE4TableGeometry {
  readonly width: number;
  readonly height: number;
  readonly titleHeight: number;
  readonly headerHeight: number;
  readonly rowHeight: number;
  readonly footerHeight: number;
  readonly columns: readonly ConnectorE4TableColumn[];
  readonly contactPoints: Readonly<Record<string, Point>>;
}

export const connectorE4TableMetrics = {
  minimumWidth: 118,
  titleHeight: 24,
  headerHeight: 28,
  rowHeight: 24,
  footerHeight: 24,
  characterWidth: 6.2,
  cellHorizontalPadding: 12,
  headerControlWidth: 22,
  customColumnWidth: 64,
  baseColumnWidths: {
    number: 44,
    contactType: 60,
    circuit: 64,
    terminal: 78,
    wire: 72,
    wireSection: 80,
    color: 56,
  } satisfies Readonly<Record<ConnectorBaseColumnKey, number>>,
  maximumColumnWidth: 220,
} as const;

const connectorBaseColumnLabels: Readonly<Record<ConnectorBaseColumnKey, string>> = {
  number: "№",
  contactType: "Тип",
  circuit: "Цепь",
  terminal: "Терминал",
  wire: "Марка",
  wireSection: "Сечение",
  color: "Цвет",
};

function e4TextWidth(text: string): number {
  return Array.from(text.trim()).length * connectorE4TableMetrics.characterWidth;
}

export function connectorE4FooterWidth(libraryCode: string, partNumber: string): number {
  return Math.ceil(
    e4TextWidth(libraryCode) + e4TextWidth(partNumber) + connectorE4TableMetrics.cellHorizontalPadding * 3,
  );
}

/**
 * Shared content-based E4 column sizing for both the canvas drawing and its
 * HTML editor overlay.  It intentionally uses a deterministic font estimate:
 * persisted connector geometry and contact anchors must not depend on the
 * browser's loaded fonts or on a canvas instance.
 */
export function connectorE4TableColumnWidth(
  key: ConnectorBaseColumnKey | null,
  label: string,
  values: readonly string[],
): number {
  const minimum = key === null
    ? connectorE4TableMetrics.customColumnWidth
    : connectorE4TableMetrics.baseColumnWidths[key];
  const headerWidth = e4TextWidth(label) + connectorE4TableMetrics.cellHorizontalPadding +
    connectorE4TableMetrics.headerControlWidth;
  const contentWidth = Math.max(0, ...values.map(e4TextWidth)) + connectorE4TableMetrics.cellHorizontalPadding +
    (key === "terminal" ? 22 : 0);
  return Math.ceil(Math.min(
    connectorE4TableMetrics.maximumColumnWidth,
    Math.max(minimum, headerWidth, contentWidth),
  ));
}

export interface ConnectorContact {
  /** Undefined inherits the library name; an empty string is an intentional override. */
  readonly nameOverride?: string;
  readonly id: string;
  /** Stable template logical ID, or a repeat occurrence key for a materialized row. */
  readonly logicalContactId?: string;
  readonly number: number;
  readonly contactType: string;
  readonly circuit: string;
  readonly terminalArticle: string;
  readonly terminalDetails?: {
    readonly manufacturer: string;
    readonly series: string;
    readonly description: string;
    readonly article?: string;
    /** Catalog L+ and L- snapshots, in millimetres. L- is stored as a signed value. */
    readonly lengthPlusMm?: number | null;
    readonly lengthMinusMm?: number | null;
  };
  readonly wire: string;
  readonly wireSection?: string;
  readonly wireDiameterMm?: number;
  /** Published wire variant selected in the E4 contact table. */
  readonly materialBinding?: WireMaterialBinding;
  readonly color: string;
  /** Empty or missing means that the wire has one insulation color. */
  readonly secondaryColor?: string;
  /** Automatic values follow a directly connected contact; manual values are preserved. */
  readonly colorMode?: "auto" | "manual";
  readonly connectionStatus: ConnectorContactStatus;
  readonly customValues: Readonly<Record<string, string>>;
  readonly libraryContact?: ConnectorLibraryContact | null;
}

export interface ConnectorDrawingPlacement {
  readonly rotationDegrees?:number;
  readonly scale?:number;
  readonly drawingId: string;
  readonly visible: boolean;
  readonly offset: Point;
}

export interface ConnectorInstance {
  readonly drawingPlacements?: readonly ConnectorDrawingPlacement[];
  /** v5 uses the editable E4 table; graphical points belong to the drawing. */
  readonly e4TableMode?: boolean;
  /** Versioned compatibility supplement; never changes the pinned template. */
  readonly terminalCatalog?: ConnectorTerminalCatalog;
  readonly id: string;
  readonly designation: string;
  /** Editable footer code for a free connector. Series connectors derive it from their library. */
  readonly libraryCode?: string;
  readonly partNumber: string;
  readonly contacts: readonly ConnectorContact[];
  readonly schematic: ConnectorSchematicPresentation;
  readonly positions: Readonly<Record<EditorView, Point>>;
  readonly layerIds: Readonly<Record<EditorView, string>>;
  readonly libraryBinding?: ConnectorLibraryBinding;
}

export interface ConnectorTerminalCatalog {
  readonly templateId: string;
  readonly version: number;
  readonly versionSha256: string;
  readonly byContact: Readonly<Record<string, readonly string[]>>;
}

export function templateTerminalChoices(connector: ConnectorInstance, logicalContactId: string | undefined): readonly string[] {
  if (connector.libraryBinding?.mode !== "template" || !logicalContactId) return [];
  return [...new Set([
    ...connector.libraryBinding.snapshot.contacts.find(contact => contact.logicalContactId === logicalContactId)
      ?.allowedTerminalArticleKeys.map(article => article.articleKey) ?? [],
    ...connector.terminalCatalog?.byContact[logicalContactId] ?? [],
  ])];
}

export type WireEndpoint =
  | { readonly connectorId: string; readonly contactId: string; readonly junctionId?: never; readonly screenId?: never }
  | { readonly junctionId: string; readonly connectorId: ""; readonly contactId: ""; readonly screenId?: never }
  | { readonly screenId: string; readonly screenTerminalSide?: WireScreenEndpointSide; readonly connectorId: ""; readonly contactId: ""; readonly junctionId?: never };

export interface E4Junction {
  readonly id: string;
  readonly position: Point;
  readonly wireIds: readonly string[];
}

export interface DiffPairGroup {
  readonly id: string;
  readonly wireIds: readonly [string, string];
  readonly step: number;
  readonly amplitude: number;
  readonly variant: 1 | 2;
}

export interface WireScreenGroup {
  readonly id: string;
  readonly wireIds: readonly string[];
  readonly position: number;
  readonly label: string;
  readonly width: number;
  /** Conducting terminal display. Missing in legacy JSON means `above`. */
  readonly terminalSide?: WireScreenTerminalSide;
}

export type WireScreenTerminalSide = "above" | "below" | "both";
export type WireScreenEndpointSide = Exclude<WireScreenTerminalSide, "both">;

export type WireCrossingStyle = "none" | "bridge";

export type E4RouteLeadDirection = "left" | "right" | "up" | "down" | null;

export interface E4RouteAnchor {
  readonly leadLength?: number;
  readonly position: Point;
  readonly leadDirection: E4RouteLeadDirection;
}

export interface WireInstance {
  readonly id: string;
  readonly from: WireEndpoint;
  readonly to: WireEndpoint;
  readonly circuit: string;
  readonly color: string;
  /** Contact whose table color drives a direct contact-to-contact wire. */
  readonly colorSource?: WireColorSource | null;
  /** Exact immutable reference-catalog record selected for this wire. */
  readonly materialBinding?: WireMaterialBinding;
  /** Exact immutable stripping profiles selected independently for both wire ends. */
  readonly stripProfiles?: WireEndStripProfiles;
  /**
   * Endpoints for an isolated drawing wire. They intentionally do not imply
   * an electrical or physical connection and are used only in the drawing
   * view; ordinary wires omit this field.
   */
  readonly drawingEndpoints?: Readonly<{ from: Point; to: Point }>;
  /** Presentation of free ends in an isolated drawing copy. */
  readonly drawingEndStyles?: Readonly<{ from: WireBlankEnd; to: WireBlankEnd }>;
  /** Physical source length. Null means that the wire is intentionally incomplete. */
  readonly lengthMm: number | null;
  /** Signed technological correction at the `from` end, in millimetres. */
  readonly endCorrectionFromMm: number;
  /** Signed technological correction at the `to` end, in millimetres. */
  readonly endCorrectionToMm: number;
  /** Positive step used to round only the final cut length upwards. */
  readonly cutRoundingStepMm: number;
  readonly e4Route: readonly Point[];
  /** Missing in older documents and treated as automatic. */
  readonly e4RouteMode?: "auto" | "manual";
  /** Normalized distance along the complete E4 route. Missing means centered. */
  readonly e4LabelPosition?: number;
  readonly drawingRoute: readonly Point[];
  readonly layerIds: Readonly<Record<EditorView, string>>;
}

export interface WireColorSource {
  readonly connectorId: string;
  readonly contactId: string;
}

/**
 * Stable catalog identity and compact display snapshot stored with a wire.
 * The snapshot identity prevents a later catalog publication from silently
 * changing the material selected for an existing harness.
 */
export interface WireMaterialBinding {
  readonly outerDiameterMm?: number;
  readonly sourceId: string;
  readonly snapshotId: string;
  readonly snapshotSha256: string;
  readonly recordId: string;
  readonly entityType: "wire" | "cable";
  readonly sourceKey: string;
  readonly displayName: string;
}

/**
 * One physical multicore cable. Its electrical conductors remain regular
 * wires, while material consumption and cut length belong to this container.
 */
export interface CableInstance {
  readonly id: string;
  readonly memberWireIds: readonly string[];
  readonly materialBinding?: WireMaterialBinding;
  readonly lengthMm: number | null;
  readonly endCorrectionFromMm: number;
  readonly endCorrectionToMm: number;
  readonly cutRoundingStepMm: number;
  readonly sheathStrip?: { readonly fromMm: number | null; readonly toMm: number | null };
}

/** End corrections belong to the blank; stripping never adds a second material. */
export function calculateCableSheathStrip(cable: CableInstance) {
  const source = cable.sheathStrip;
  if (!source) return null;
  const end = (value: number | null, correction: number) => {
    if (value === null) return null;
    validateWirePhysicalLength(value, "Снятие оболочки");
    const result = value === 0 ? 0n : toExactMicrometres(value, "Снятие оболочки") + toExactMicrometres(correction, "Поправка");
    if (result < 0n) throw new Error("Снятие оболочки с поправкой не должно быть отрицательным.");
    return Number(result) / micrometresPerMillimetre;
  };
  const fromMm = end(source.fromMm, cable.endCorrectionFromMm);
  const toMm = end(source.toMm, cable.endCorrectionToMm);
  const totalMm = calculateWireCutLength(cable).unroundedTotalMm;
  if (cable.lengthMm !== null && toExactMicrometres(source.fromMm ?? 0, "Снятие оболочки") +
      toExactMicrometres(source.toMm ?? 0, "Снятие оболочки") > toExactMicrometres(cable.lengthMm, "Длина кабеля")) {
    throw new Error("Сумма снятия оболочки превышает длину кабеля.");
  }
  if (totalMm !== null && toExactMicrometres(fromMm ?? 0, "Снятие оболочки") +
      toExactMicrometres(toMm ?? 0, "Снятие оболочки") > toExactMicrometres(totalMm, "Длина кабеля")) {
    throw new Error("Снятие оболочки с поправками превышает длину заготовки.");
  }
  return { fromMm, toMm, totalMm, isComplete: fromMm !== null && toMm !== null && totalMm !== null };
}

export interface WireEndStripProfiles {
  readonly from?: WireStripProfileBinding;
  readonly to?: WireStripProfileBinding;
}

/**
 * Exact catalog snapshot and normalized layer geometry for one end treatment.
 * Layer indices run from the central conductor outwards. Both diameter and
 * cumulative strip length therefore grow with the index.
 */
export interface WireStripProfileBinding {
  readonly sourceId: string;
  readonly snapshotId: string;
  readonly snapshotSha256: string;
  readonly recordId: string;
  readonly entityType: "coax-termination";
  readonly sourceKey: string;
  readonly displayName: string;
  readonly layers: readonly WireStripProfileLayer[];
}

export interface WireStripProfileLayer {
  readonly index: number;
  readonly diameterMm: number;
  /** Cumulative length from the common cut end, in millimetres. */
  readonly stripLengthMm: number;
}

export interface WireStripStep {
  readonly index: number;
  readonly diameterMm: number;
  readonly cumulativeLengthMm: number;
  /** Length added outside the previous, more internal layer. */
  readonly stepLengthMm: number;
}

const maximumWireStripLayers = 64;

/** Converts cumulative L values into independently executable strip steps. */
export function calculateWireStripSteps(layers: readonly WireStripProfileLayer[]): readonly WireStripStep[] {
  const normalized = normalizeWireStripProfileLayers(layers);
  let previousMicrometres = 0n;
  return Object.freeze(normalized.map((layer) => {
    const cumulativeMicrometres = toExactMicrometres(layer.stripLengthMm, `L${layer.index}`);
    const step = Object.freeze({
      index: layer.index,
      diameterMm: layer.diameterMm,
      cumulativeLengthMm: layer.stripLengthMm,
      stepLengthMm: Number(cumulativeMicrometres - previousMicrometres) / micrometresPerMillimetre,
    });
    previousMicrometres = cumulativeMicrometres;
    return step;
  }));
}

export interface WireCutLengthCalculation {
  readonly sourceLengthMm: number | null;
  readonly endCorrectionFromMm: number;
  readonly endCorrectionToMm: number;
  readonly cutRoundingStepMm: number;
  readonly unroundedTotalMm: number | null;
  readonly cutLengthMm: number | null;
  readonly materialConsumptionMm: number | null;
  readonly isComplete: boolean;
}

export const defaultWireCutRoundingStepMm = 1;
const maximumWireLengthMm = 1_000_000_000;
const micrometresPerMillimetre = 1_000;

/** Mirrors the exact M1 domain calculation without deriving physical length from canvas geometry. */
export function calculateWireCutLength(wire: Pick<WireInstance,
  "lengthMm" | "endCorrectionFromMm" | "endCorrectionToMm" | "cutRoundingStepMm"
>): WireCutLengthCalculation {
  const sourceLengthMm = validateWirePhysicalLength(wire.lengthMm);
  const endCorrectionFromMm = validateWireCorrection(wire.endCorrectionFromMm, "Поправка начала провода");
  const endCorrectionToMm = validateWireCorrection(wire.endCorrectionToMm, "Поправка конца провода");
  const cutRoundingStepMm = validateWireRoundingStep(wire.cutRoundingStepMm);
  const common = { sourceLengthMm, endCorrectionFromMm, endCorrectionToMm, cutRoundingStepMm };
  if (sourceLengthMm === null) {
    return {
      ...common,
      unroundedTotalMm: null,
      cutLengthMm: null,
      materialConsumptionMm: null,
      isComplete: false,
    };
  }
  const totalMicrometres = toExactMicrometres(sourceLengthMm, "Длина провода") +
    toExactMicrometres(endCorrectionFromMm, "Поправка начала провода") +
    toExactMicrometres(endCorrectionToMm, "Поправка конца провода");
  if (totalMicrometres < 0n) throw new Error("Итоговая длина провода не должна быть отрицательной.");
  const stepMicrometres = toExactMicrometres(cutRoundingStepMm, "Шаг округления длины резки");
  const remainder = totalMicrometres % stepMicrometres;
  const roundedMicrometres = remainder === 0n
    ? totalMicrometres
    : totalMicrometres + stepMicrometres - remainder;
  const unroundedTotalMm = Number(totalMicrometres) / micrometresPerMillimetre;
  const cutLengthMm = Number(roundedMicrometres) / micrometresPerMillimetre;
  return {
    ...common,
    unroundedTotalMm,
    cutLengthMm,
    materialConsumptionMm: cutLengthMm,
    isComplete: true,
  };
}

export interface EditorLayer {
  readonly id: string;
  readonly name: string;
  readonly order: number;
  readonly visible: boolean;
  readonly locked: boolean;
}

export interface EditorViewState {
  readonly layers: readonly EditorLayer[];
  readonly wireCrossingStyle: WireCrossingStyle;
  /** E4 automatic/manual route geometry. Missing legacy values are orthogonal. */
  readonly e4RoutingMode?: "orthogonal" | "angular";
}

export interface HarnessDesignDocument {
  readonly drawingDocuments?: DrawingDocuments;
  readonly physicalTopology?: PhysicalTopology;
  readonly schemaVersion: 1;
  /** Minimum writer contract required to safely preserve this document. */
  readonly requiredWriterContractVersion?: 1 | 2;
  readonly manufacturingRoute?: ManufacturingRoute;
  /** User-created wire colors retained by this harness even when temporarily unused. */
  readonly customWireColors?: readonly string[];
  readonly connectors: readonly ConnectorInstance[];
  readonly wires: readonly WireInstance[];
  readonly cables: readonly CableInstance[];
  readonly junctions: readonly E4Junction[];
  readonly diffPairs: readonly DiffPairGroup[];
  readonly screens: readonly WireScreenGroup[];
  readonly views: Readonly<Record<EditorView, EditorViewState>>;
}

export const defaultE4WireLead = 24;
export const e4ScreenAlongSize = 18;

export const defaultLayerIds = {
  connectors: "connectors",
  wires: "wires",
  dimensions: "dimensions",
  connectionPoints: "connection-points",
} as const;

export function createDefaultConnectorBaseColumns(): readonly ConnectorBaseColumn[] {
  return connectorBaseColumnKeys.map((key) => ({ key, visible: true }));
}

export function connectorE4Contacts(connector: ConnectorInstance): readonly ConnectorContact[] {
  const contacts = new Map(connector.contacts.map(contact => [contact.id, contact]));
  const ordered = (connector.schematic.rowOrder ?? []).flatMap(id => {
    const contact = contacts.get(id);
    contacts.delete(id);
    return contact ? [contact] : [];
  });
  return [...ordered, ...contacts.values()];
}

export function connectorE4TableGeometry(connector: ConnectorInstance): ConnectorE4TableGeometry {
  const baseColumns: ConnectorE4TableColumn[] = connector.schematic.baseColumns
    .filter((column) => column.visible)
    .map((column) => ({
      kind: "base",
      key: column.key,
      x: 0,
      width: connectorE4TableColumnWidth(
        column.key,
        connectorBaseColumnLabels[column.key],
        [
          ...connector.contacts.map((contact) => column.key === "number" ? String(contact.number)
            : column.key === "contactType" ? contact.contactType
              : column.key === "circuit" ? contact.circuit
                : column.key === "terminal" ? terminalArticleLabel(contact.terminalArticle)
                  : column.key === "wire" ? contact.wire
                    : column.key === "wireSection" ? contact.wireSection ?? ""
                    // Colour is rendered as a swatch, so its name must not
                    // resize the table and move every connected anchor.
                    : ""),
        ],
      ),
    }));
  const customColumns: ConnectorE4TableColumn[] = connector.schematic.customFields
    .filter((field) => field.visible)
    .map((field) => ({
      kind: "custom",
      id: field.id,
      label: field.label,
      x: 0,
      width: connectorE4TableColumnWidth(
        null,
        field.label,
        connector.contacts.map((contact) => contact.customValues[field.id] ?? ""),
      ),
    }));
  if (connector.libraryBinding?.mode === "template" && connector.schematic.showName !== false) {
    const templateName: ConnectorE4TableColumn = {
      kind: "custom", id: templateNameColumnId, label: "Назначение", x: 0,
      width: connectorE4TableColumnWidth(null, "Назначение", connector.contacts.map(contact => connectorContactName(connector, contact))),
    };
    const numberIndex = baseColumns.findIndex(column => column.kind === "base" && column.key === "number");
    baseColumns.splice(numberIndex < 0 ? baseColumns.length : numberIndex + 1, 0, templateName);
  }
  const contactsFirst = connector.schematic.orientation === "contacts-left";
  const orderedColumns = contactsFirst
    ? [...baseColumns, ...customColumns]
    : [...customColumns, ...baseColumns].reverse();
  let x = 0;
  let columns = orderedColumns.map((column) => {
    const positioned = { ...column, x };
    x += column.width;
    return positioned;
  });
  const libraryCode = connector.libraryBinding?.mode === "series"
    ? connector.libraryBinding.seriesId
    : connector.libraryCode ?? "FREE";
  const width = Math.max(
    connectorE4TableMetrics.minimumWidth,
    x,
    connectorE4FooterWidth(libraryCode, connector.partNumber),
  );
  if (columns.length > 0 && width > x) {
    const flexibleIndex = columns.findIndex((column) => column.kind !== "base" || column.key !== "number");
    const growIndex = flexibleIndex < 0 ? 0 : flexibleIndex;
    columns = columns.map((column, index) => index === growIndex
      ? { ...column, width: column.width + width - x }
      : column);
    let adjustedX = 0;
    columns = columns.map((column) => {
      const positioned = { ...column, x: adjustedX };
      adjustedX += column.width;
      return positioned;
    });
  }
  const height = connectorE4TableMetrics.titleHeight + connectorE4TableMetrics.headerHeight +
    connector.contacts.length * connectorE4TableMetrics.rowHeight + connectorE4TableMetrics.footerHeight;
  const contactX = contactsFirst ? 0 : width;
  const contactPoints = Object.fromEntries(connectorE4Contacts(connector).map((contact, index) => [
    contact.id,
    {
      x: contactX,
      y: connectorE4TableMetrics.titleHeight + connectorE4TableMetrics.headerHeight +
        (index + 0.5) * connectorE4TableMetrics.rowHeight,
    },
  ]));
  return {
    width,
    height,
    titleHeight: connectorE4TableMetrics.titleHeight,
    headerHeight: connectorE4TableMetrics.headerHeight,
    rowHeight: connectorE4TableMetrics.rowHeight,
    footerHeight: connectorE4TableMetrics.footerHeight,
    columns,
    contactPoints,
  };
}

function defaultLayers(): readonly EditorLayer[] {
  return [
    { id: defaultLayerIds.connectionPoints, name: "Точки соединения", order: 3, visible: true, locked: false },
    { id: defaultLayerIds.dimensions, name: "Размеры", order: 2, visible: true, locked: false },
    { id: defaultLayerIds.connectors, name: "Соединители", order: 1, visible: true, locked: false },
    { id: defaultLayerIds.wires, name: "Провода", order: 0, visible: true, locked: false },
  ];
}

export function createEmptyHarnessDesign(): HarnessDesignDocument {
  return {
    schemaVersion: 1,
    customWireColors: [],
    connectors: [],
    wires: [],
    cables: [],
    junctions: [],
    diffPairs: [],
    screens: [],
    views: {
      e4: { layers: defaultLayers(), wireCrossingStyle: "none", e4RoutingMode: "orthogonal" },
      drawing: { layers: defaultLayers(), wireCrossingStyle: "none" },
    },
  };
}

export function parseHarnessDesignDocument(value: unknown): HarnessDesignDocument {
  const record = requireRecord(value, "Сервер вернул повреждённый документ жгута.");
  if (record.requiredWriterContractVersion !== undefined && record.requiredWriterContractVersion !== 1 && record.requiredWriterContractVersion !== 2) {
    throw new Error("Для этого документа требуется более новая версия приложения.");
  }
  if (record.schemaVersion !== 1 || !Array.isArray(record.connectors) || !Array.isArray(record.wires)) {
    throw new Error("Версия или состав документа жгута не поддерживаются.");
  }
  const wireValues = record.wires;
  const views = requireRecord(record.views, "Представления документа жгута заданы неверно.");
  let document: HarnessDesignDocument = {
    schemaVersion: 1,
    ...(record.requiredWriterContractVersion === 1 || record.requiredWriterContractVersion === 2 ? { requiredWriterContractVersion: record.requiredWriterContractVersion } : {}),
    ...(record.manufacturingRoute !== undefined ? { manufacturingRoute: parseManufacturingRoute(record.manufacturingRoute) } : {}),
    customWireColors: parseCustomWireColors(record.customWireColors),
    connectors: record.connectors.map(parseConnector),
    wires: wireValues.map(parseWire),
    cables: record.cables === undefined ? [] : parseCables(record.cables),
    junctions: record.junctions === undefined ? [] : parseJunctions(record.junctions),
    diffPairs: record.diffPairs === undefined ? [] : parseDiffPairs(record.diffPairs),
    screens: record.screens === undefined ? [] : parseScreens(record.screens),
    views: { e4: parseView(views.e4, true), drawing: parseView(views.drawing, false) },
  };
  document = {
    ...document,
    customWireColors: normalizeCustomWireColors([
      ...(document.customWireColors ?? []),
      ...document.connectors.flatMap((connector) => connector.contacts
        .flatMap((contact) => [contact.color, contact.secondaryColor ?? ""])),
    ]),
  };
  document = { ...document, physicalTopology: parsePhysicalTopology(record.physicalTopology, document) };
  document = {...document,drawingDocuments:validateDrawingDocuments(record.drawingDocuments,document)};
  const connectorIds = new Set(document.connectors.map((connector) => connector.id));
  if (connectorIds.size !== document.connectors.length) throw new Error("ID соединителей должны быть уникальны.");
  const wireIds = new Set(document.wires.map((wire) => wire.id));
  if (wireIds.size !== document.wires.length) throw new Error("ID проводов должны быть уникальны.");
  validateCables(document.cables, wireIds);
  const junctionIds = new Set(document.junctions.map((junction) => junction.id));
  if (junctionIds.size !== document.junctions.length) throw new Error("ID узлов соединения должны быть уникальны.");
  for (const wire of document.wires) {
    for (const endpoint of [wire.from, wire.to]) {
      if (isJunctionEndpoint(endpoint)) {
        if (!junctionIds.has(endpoint.junctionId)) throw new Error("Провод ссылается на отсутствующий узел соединения.");
      } else if (isScreenEndpoint(endpoint)) {
        if (!document.screens.some((screen) => screen.id === endpoint.screenId) ||
            !wireScreenConnectionPoint(document, endpoint.screenId, new Set(), endpoint.screenTerminalSide)) {
          throw new Error("Провод ссылается на отсутствующую точку подключения экрана.");
        }
      } else {
        const connector = document.connectors.find((item) => item.id === endpoint.connectorId);
        if (!connector?.contacts.some((contact) => contact.id === endpoint.contactId) && !wire.drawingEndpoints) {
          throw new Error("Провод ссылается на отсутствующую точку подключения.");
        }
      }
    }
  }
  document = {
    ...document,
    wires: document.wires.map((wire) => ({
      ...wire,
      colorSource: normalizeWireColorSource(document, wire),
    })),
  };
  document = {
    ...document,
    wires: document.wires.map((wire, index) => {
      const wireRecord = requireRecord(wireValues[index], "Провод задан неверно.");
      if (wireRecord.e4Route !== undefined || wire.drawingEndpoints) return wire;
      const start = wireEndpointE4Anchor(document, wire.from);
      const end = wireEndpointE4Anchor(document, wire.to);
      if (!start || !end) throw new Error("Точки подключения маршрута Э4 не найдены.");
      return { ...wire, e4Route: createOrthogonalE4Route(start, end), e4RouteMode: "auto" };
    }),
  };
  for (const [index, wireValue] of wireValues.entries()) {
    const wireRecord = requireRecord(wireValue, "Провод задан неверно.");
    const wire = document.wires[index]!;
    if (wireRecord.e4Route !== undefined && !wire.drawingEndpoints) {
      const start = wireEndpointE4Anchor(document, wire.from);
      const end = wireEndpointE4Anchor(document, wire.to);
      if (!start || !end) throw new Error("Точки подключения маршрута Э4 не найдены.");
      const screenBranch = [wire.from, wire.to].some(isScreenEndpoint) && [wire.from, wire.to].some(isJunctionEndpoint);
      validateE4Polyline(start, wire.e4Route, end, screenBranch ? 0 : defaultE4WireLead);
    }
  }
  validateParsedGroups(document, wireIds);
  validateJunctions(document);
  validateJunctionCircuitComponents(document.wires, document.junctions);
  return document;
}

function parseCustomWireColors(value: unknown): readonly string[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new Error("Пользовательские цвета проводов заданы неверно.");
  }
  return normalizeCustomWireColors(value as readonly string[]);
}

function normalizeCustomWireColors(values: readonly string[]): readonly string[] {
  return [...new Set(values.flatMap((value) => {
    const normalized = value.trim().toUpperCase();
    return /^#[0-9A-F]{6}$/.test(normalized) ? [normalized] : [];
  }))];
}

export function isJunctionEndpoint(endpoint: WireEndpoint): endpoint is Extract<WireEndpoint, { junctionId: string }> {
  return "junctionId" in endpoint;
}

export function createJunctionEndpoint(junctionId: string): WireEndpoint {
  return { junctionId, connectorId: "", contactId: "" };
}

export function isScreenEndpoint(endpoint: WireEndpoint): endpoint is Extract<WireEndpoint, { screenId: string }> {
  return "screenId" in endpoint;
}

export function createScreenEndpoint(screenId: string, screenTerminalSide?: WireScreenEndpointSide): WireEndpoint {
  return screenTerminalSide
    ? { screenId, screenTerminalSide, connectorId: "", contactId: "" }
    : { screenId, connectorId: "", contactId: "" };
}

export function wireEndpointE4Anchor(document: HarnessDesignDocument, endpoint: WireEndpoint): E4RouteAnchor | null {
  return wireEndpointE4AnchorInternal(document, endpoint, new Set());
}

function wireEndpointE4AnchorInternal(
  document: HarnessDesignDocument,
  endpoint: WireEndpoint,
  resolvingScreenIds: ReadonlySet<string>,
): E4RouteAnchor | null {
  if (isJunctionEndpoint(endpoint)) {
    const junction = document.junctions.find((item) => item.id === endpoint.junctionId);
    return junction ? { position: junction.position, leadDirection: null } : null;
  }
  if (isScreenEndpoint(endpoint)) {
    const geometry = wireScreenConnectionGeometry(
      document,
      endpoint.screenId,
      resolvingScreenIds,
      endpoint.screenTerminalSide,
    );
    return geometry ? {
      position: geometry.connectionPoint,
      leadLength: 8,
      leadDirection: geometry.orientation === "horizontal"
        ? geometry.terminalSide === "above" ? "up" : "down"
        : geometry.terminalSide === "above" ? "left" : "right",
    } : null;
  }
  const connector = document.connectors.find((item) => item.id === endpoint.connectorId);
  if (!connector) return null;
  const materialized = materializedContactWorldRepresentation(connector, endpoint.contactId, "e4");
  const position = materialized?.position ?? connectorContactPosition(connector, endpoint.contactId, "e4");
  return position ? {
    position,
    leadDirection: materialized?.direction ??
      (connector.schematic.orientation === "contacts-left" ? "left" : "right"),
  } : null;
}

export interface WireScreenConnectionGeometry {
  readonly center: Point;
  readonly bodyConnectionPoint: Point;
  readonly connectionPoint: Point;
  readonly terminalSide: WireScreenEndpointSide;
  readonly terminals: readonly {
    readonly side: WireScreenEndpointSide;
    readonly bodyConnectionPoint: Point;
    readonly connectionPoint: Point;
  }[];
  readonly orientation: "horizontal" | "vertical";
  readonly alongSize: number;
  readonly crossSize: number;
}

/** Derives the screen body and its explicit conducting ports from the screened routes. */
export function wireScreenConnectionGeometry(
  document: HarnessDesignDocument,
  screenId: string,
  resolvingScreenIds: ReadonlySet<string> = new Set(),
  preferredTerminalSide?: WireScreenEndpointSide,
): WireScreenConnectionGeometry | null {
  if (resolvingScreenIds.has(screenId)) return null;
  const screen = document.screens.find((item) => item.id === screenId);
  if (!screen) return null;
  const nextResolving = new Set([...resolvingScreenIds, screenId]);
  let spans = screenCrossSections(screen.wireIds.map(wireId => {
    const wire = document.wires.find(item => item.id === wireId);
    const start = wire && wireEndpointE4AnchorInternal(document, wire.from, nextResolving)?.position;
    const end = wire && wireEndpointE4AnchorInternal(document, wire.to, nextResolving)?.position;
    return {id:wireId, points:wire && start && end ? [start,...wire.e4Route,end] : []};
  }));
  spans = clearScreenSections(spans, document.connectors.map(connector => ({
    ...connector.positions.e4, ...connectorE4TableGeometry(connector),
  })), screen.width,e4ScreenAlongSize);
  if (spans.length === 0) return null;
  spans.sort((left, right) => left.firstWireDirection * (left.start - right.start));
  const pathLength = spans.reduce((sum, span) => sum + span.end - span.start, 0);
  const requestedDistance = pathLength * Math.max(0, Math.min(1, screen.position));
  let accumulated = 0;
  let selected = spans[0]!;
  for (const span of spans) {
    if (requestedDistance <= accumulated + span.end - span.start || span === spans.at(-1)) {
      selected = span;
      break;
    }
    accumulated += span.end - span.start;
  }
  const spanOffset = Math.max(0, Math.min(selected.end - selected.start, requestedDistance - accumulated));
  const along = selected.firstWireDirection === 1 ? selected.start + spanOffset : selected.end - spanOffset;
  const {center,alongSize,crossSize}=uprightScreenBody(selected,along,screen.width,e4ScreenAlongSize);
  const configuredSide = screen.terminalSide ?? "above";
  const terminalSides: readonly WireScreenEndpointSide[] = configuredSide === "both"
    ? ["above", "below"]
    : [configuredSide];
  const terminals = terminalSides.map((side) => {
    const direction = side === "above" ? -1 : 1;
    const bodyConnectionPoint = { x: center.x, y: center.y + direction * crossSize / 2 };
    // The port belongs to the oval itself. A wire only becomes connected after
    // the user explicitly starts or ends it on this perimeter point.
    const connectionPoint = bodyConnectionPoint;
    return { side, bodyConnectionPoint, connectionPoint };
  });
  const terminal = preferredTerminalSide === undefined
    ? terminals[0]!
    : terminals.find((item) => item.side === preferredTerminalSide);
  // Never silently move a persisted endpoint to the opposite port when the
  // screen mode changes. Such a document/command must be repaired explicitly.
  if (!terminal) return null;
  return {
    center,
    bodyConnectionPoint: terminal.bodyConnectionPoint,
    connectionPoint: terminal.connectionPoint,
    terminalSide: terminal.side,
    terminals,
    orientation: "horizontal",
    alongSize,
    crossSize,
  };
}

export function wireScreenConnectionPoint(
  document: HarnessDesignDocument,
  screenId: string,
  resolvingScreenIds: ReadonlySet<string> = new Set(),
  preferredTerminalSide?: WireScreenEndpointSide,
): Point | null {
  return wireScreenConnectionGeometry(document, screenId, resolvingScreenIds, preferredTerminalSide)?.connectionPoint ?? null;
}

export function validateE4Polyline(
  start:E4RouteAnchor,intermediate:readonly Point[],end:E4RouteAnchor,minimumLead=defaultE4WireLead,
):void {
  if(!Number.isFinite(minimumLead)||minimumLead<0)throw new Error("Минимальный прямой участок задан неверно.");
  const points=[start.position,...intermediate,end.position];
  for(let i=0;i<points.length;i++){
    const p=points[i]!;
    if(!Number.isFinite(p.x)||!Number.isFinite(p.y)||i>0&&Math.hypot(p.x-points[i-1]!.x,p.y-points[i-1]!.y)<1e-8)throw new Error("Маршрут Э4 должен состоять из ненулевых сегментов.");
  }
  validateLead(start,straightLeadEnd(points),minimumLead);
  validateLead(end,straightLeadEnd([...points].reverse()),minimumLead);
}

export function validateOrthogonalE4Route(
  start: E4RouteAnchor,
  intermediate: readonly Point[],
  end: E4RouteAnchor,
  minimumLead = defaultE4WireLead,
): void {
  if (!Number.isFinite(minimumLead) || minimumLead < 0) throw new Error("Минимальный прямой участок задан неверно.");
  const points = [start.position, ...intermediate, end.position];
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1]!;
    const current = points[index]!;
    if ((previous.x === current.x) === (previous.y === current.y)) {
      throw new Error("Маршрут Э4 должен состоять из ненулевых ортогональных сегментов.");
    }
  }
  validateLead(start, straightLeadEnd(points), minimumLead);
  validateLead(end, straightLeadEnd([...points].reverse()), minimumLead);
}

export function createOrthogonalE4Route(
  start: E4RouteAnchor,
  end: E4RouteAnchor,
  minimumLead = defaultE4WireLead,
): readonly Point[] {
  const startLead = leadPoint(start, minimumLead);
  const endLead = leadPoint(end, minimumLead);
  const candidates: Point[] = [];
  if (!samePoint(start.position, startLead)) candidates.push(startLead);
  if (startLead.x !== endLead.x && startLead.y !== endLead.y) candidates.push({ x: endLead.x, y: startLead.y });
  if (!samePoint(startLead, endLead)) candidates.push(endLead);
  const intermediate = candidates.filter((point, index) => {
    const previous = index === 0 ? start.position : candidates[index - 1]!;
    return !samePoint(point, previous) && !samePoint(point, end.position);
  });
  validateOrthogonalE4Route(start, intermediate, end, minimumLead);
  return intermediate;
}

function validateLead(anchor: E4RouteAnchor, adjacent: Point, minimumLead: number): void {
  minimumLead = Math.min(minimumLead, anchor.leadLength ?? minimumLead);
  if (anchor.leadDirection === null) return;
  const horizontal = anchor.leadDirection === "left" || anchor.leadDirection === "right";
  const distance = anchor.leadDirection === "left"
    ? anchor.position.x - adjacent.x
    : anchor.leadDirection === "right"
      ? adjacent.x - anchor.position.x
      : anchor.leadDirection === "up"
        ? anchor.position.y - adjacent.y
        : adjacent.y - anchor.position.y;
  const aligned = horizontal ? adjacent.y === anchor.position.y : adjacent.x === anchor.position.x;
  if (!aligned || distance < minimumLead) {
    throw new Error("Маршрут Э4 должен иметь прямой участок наружу от контакта.");
  }
}

function leadPoint(anchor: E4RouteAnchor, minimumLead: number): Point {
  minimumLead = Math.min(minimumLead, anchor.leadLength ?? minimumLead);
  if (anchor.leadDirection === "left") return { x: anchor.position.x - minimumLead, y: anchor.position.y };
  if (anchor.leadDirection === "right") return { x: anchor.position.x + minimumLead, y: anchor.position.y };
  if (anchor.leadDirection === "up") return { x: anchor.position.x, y: anchor.position.y - minimumLead };
  if (anchor.leadDirection === "down") return { x: anchor.position.x, y: anchor.position.y + minimumLead };
  return anchor.position;
}

function samePoint(left: Point, right: Point): boolean {
  return left.x === right.x && left.y === right.y;
}

function validateParsedGroups(
  document: HarnessDesignDocument,
  wireIds: ReadonlySet<string>,
): void {
  const { diffPairs, screens } = document;
  if (new Set(diffPairs.map((group) => group.id)).size !== diffPairs.length ||
      new Set(screens.map((group) => group.id)).size !== screens.length) {
    throw new Error("ID групп проводов должны быть уникальны.");
  }
  for (const group of [...diffPairs, ...screens]) {
    if (group.wireIds.some((wireId) => !wireIds.has(wireId))) {
      throw new Error("Группа ссылается на отсутствующий провод.");
    }
  }
  const pairedWireIds = diffPairs.flatMap((group) => [...group.wireIds]);
  if (new Set(pairedWireIds).size !== pairedWireIds.length) {
    throw new Error("Провод не может входить в несколько дифференциальных пар.");
  }
  for (const group of diffPairs) if (!wireGroupHasCommonE4ParallelSpan(document, group.wireIds)) {
    throw new Error("Провода дифференциальной пары должны иметь общий параллельный участок.");
  }
  for (const screen of screens) if (!wireGroupHasScreenCrossSection(document, screen.wireIds)) {
    throw new Error("Провода экрана должны иметь общий поперечный охват.");
  }
  for (const screen of screens) if (screen.wireIds.some((wireId) => {
    const wire = document.wires.find((item) => item.id === wireId);
    return wire && [wire.from, wire.to].some((endpoint) => isScreenEndpoint(endpoint) && endpoint.screenId === screen.id);
  })) throw new Error("Провод, подключённый к экрану, не может одновременно входить в его охват.");
}

function validateJunctionCircuitComponents(wires: readonly WireInstance[], junctions: readonly E4Junction[]): void {
  const visited = new Set<string>();
  for (const wire of wires) {
    if (visited.has(wire.id)) continue;
    const component = collectConnectedWireIds(wire.id, wires, junctions);
    for (const wireId of component) visited.add(wireId);
    const circuits = new Set(wires.filter((item) => component.has(item.id) && item.circuit).map((item) => item.circuit));
    if (circuits.size > 1) throw new Error("Провода одной цепи через узел соединения имеют разные обозначения цепи.");
  }
}

function collectConnectedWireIds(
  wireId: string,
  wires: readonly WireInstance[],
  junctions: readonly E4Junction[],
): Set<string> {
  const result = new Set([wireId]);
  let size = -1;
  while (size !== result.size) {
    size = result.size;
    for (const junction of junctions) if (junction.wireIds.some((id) => result.has(id))) {
      for (const id of junction.wireIds) result.add(id);
    }
    const screenIds = new Set(wires.filter((wire) => result.has(wire.id)).flatMap((wire) =>
      [wire.from, wire.to].flatMap((endpoint) => isScreenEndpoint(endpoint) ? [endpoint.screenId] : [])));
    for (const wire of wires) if ([wire.from, wire.to].some((endpoint) =>
      isScreenEndpoint(endpoint) && screenIds.has(endpoint.screenId))) result.add(wire.id);
  }
  return result;
}

export function connectorContactPosition(
  connector: ConnectorInstance,
  contactId: string,
  view: EditorView,
): Point | null {
  const index = connector.contacts.findIndex((contact) => contact.id === contactId);
  if (index < 0) return null;
  const materialized = materializedContactWorldRepresentation(connector, contactId, view);
  if (materialized) return materialized.position;
  const origin = connector.positions[view];
  if (view === "e4") {
    const point = connectorE4TableGeometry(connector).contactPoints[contactId];
    return point ? { x: origin.x + point.x, y: origin.y + point.y } : null;
  }
  return { x: origin.x + 118, y: origin.y + 28 + index * 16 };
}

export function findWireEndpoint(
  document: HarnessDesignDocument,
  endpoint: WireEndpoint,
  view: EditorView,
): Point | null {
  if (isJunctionEndpoint(endpoint)) {
    return view === "e4" ? document.junctions.find((item) => item.id === endpoint.junctionId)?.position ?? null : null;
  }
  if (isScreenEndpoint(endpoint)) {
    return view === "e4"
      ? wireScreenConnectionPoint(document, endpoint.screenId, new Set(), endpoint.screenTerminalSide)
      : null;
  }
  const connector = document.connectors.find((item) => item.id === endpoint.connectorId);
  return connector ? connectorContactPosition(connector, endpoint.contactId, view) : null;
}

function parseConnector(value: unknown): ConnectorInstance {
  const record = requireRecord(value, "Соединитель задан неверно.");
  if (!Array.isArray(record.contacts)) throw new Error("Контакты соединителя заданы неверно.");
  const positions = requireRecord(record.positions, "Координаты соединителя заданы неверно.");
  const layerIds = requireRecord(record.layerIds, "Слои соединителя заданы неверно.");
  const parsedContacts = record.contacts.map((contactValue) => {
    const contact = requireRecord(contactValue, "Контакт соединителя задан неверно.");
    return {
      id: requireText(contact.id, "ID контакта"),
      ...(contact.nameOverride === undefined ? {} : { nameOverride: requireString(contact.nameOverride, "Назначение контакта") }),
      logicalContactId: contact.logicalContactId === undefined
        ? undefined
        : requireText(contact.logicalContactId, "Логический ID контакта"),
      number: requireInteger(contact.number, "Номер контакта", 1, 300),
      contactType: optionalString(contact.contactType, "Тип контакта"),
      circuit: requireString(contact.circuit, "Цепь контакта"),
      terminalArticle: optionalString(contact.terminalArticle, "Артикул терминала"),
      ...(contact.terminalDetails === undefined ? {} : { terminalDetails: parseTerminalDetails(contact.terminalDetails) }),
      wire: optionalString(contact.wire, "Провод контакта"),
      ...(contact.wireDiameterMm === undefined ? {} : {wireDiameterMm:parseOuterDiameter(contact.wireDiameterMm)}),
      ...(contact.wireSection === undefined ? {} : { wireSection: optionalString(contact.wireSection, "Сечение провода") }),
      ...(contact.materialBinding === undefined ? {} : { materialBinding: parseWireMaterialBinding(contact.materialBinding) }),
      color: optionalString(contact.color, "Цвет провода контакта"),
      secondaryColor: optionalString(contact.secondaryColor, "Второй цвет провода контакта"),
      ...(contact.colorMode === undefined ? {} : { colorMode: parseContactColorMode(contact.colorMode) }),
      connectionStatus: parseContactStatus(contact.connectionStatus),
      customValues: parseCustomValues(contact.customValues),
      libraryContact: parseConnectorLibraryContact(contact.libraryContact),
    };
  });
  if (new Set(parsedContacts.map((contact) => contact.id)).size !== parsedContacts.length ||
      new Set(parsedContacts.map((contact) => contact.number)).size !== parsedContacts.length) {
    throw new Error("Контакты соединителя должны иметь уникальные ID и номера.");
  }
  const designation = requireBoundedText(record.designation, "Обозначение соединителя", 120);
  const partNumber = record.partNumber === undefined
    ? designation
    : requireBoundedText(record.partNumber, "Артикул шаблона соединителя", 512);
  const libraryBinding = parseConnectorLibraryBinding(record.libraryBinding) ?? { mode: "free" };
  const contacts = parsedContacts.map((contact) => ({
    ...contact,
    libraryContact: contact.libraryContact ?? null,
  }));
  const schematic = parseConnectorSchematic(record.schematic);
  if (schematic.rowOrder?.some(id => !contacts.some(contact => contact.id === id))) {
    throw new Error("Порядок строк ссылается на отсутствующий контакт.");
  }
  const customFieldIds = new Set(schematic.customFields.map((field) => field.id));
  for (const contact of contacts) {
    if (Object.keys(contact.customValues).some((fieldId) => !customFieldIds.has(fieldId))) {
      throw new Error("Значение контакта ссылается на отсутствующее справочное поле.");
    }
  }
  let connector: ConnectorInstance = {
    ...(record.drawingPlacements === undefined ? {} : {drawingPlacements: parseDrawingPlacements(record.drawingPlacements)}),
    ...(record.e4TableMode === true ? { e4TableMode: true } : {}),
    ...(record.terminalCatalog === undefined ? {} : { terminalCatalog: parseConnectorTerminalCatalog(record.terminalCatalog) }),
    id: requireText(record.id, "ID соединителя"),
    designation,
    libraryCode: record.libraryCode === undefined
      ? "FREE"
      : requireBoundedText(record.libraryCode, "Код свободного блока", 120),
    partNumber,
    contacts,
    schematic,
    positions: { e4: parsePoint(positions.e4), drawing: parsePoint(positions.drawing) },
    layerIds: {
      e4: requireText(layerIds.e4, "Слой соединителя Э4"),
      drawing: requireText(layerIds.drawing, "Слой соединителя чертежа"),
    },
    libraryBinding,
  };
  validateConnectorLibraryMetadata(connector);
  connector = restoreTemplateContactNumbers(connector);
  validateConnectorLibraryMetadata(connector);
  // Expanding an older table must keep its contact anchors in place, otherwise
  // persisted manual routes can become invalid before the editor even opens.
  const rawSchematic = record.schematic === undefined ? null : requireRecord(record.schematic, "Настройки таблицы заданы неверно.");
  const oldColumns = rawSchematic?.baseColumns;
  const hasSectionColumn = Array.isArray(oldColumns) && oldColumns.some(column => column.key === "wireSection");
  if (!hasSectionColumn && schematic.orientation === "contacts-right" &&
      connector.contacts.every(contact => !materializedContactWorldRepresentation(connector, contact.id, "e4"))) {
    const oldWidth = connectorE4TableGeometry({ ...connector, schematic: { ...schematic,
      baseColumns: schematic.baseColumns.filter(column => column.key !== "wireSection") } }).width;
    const shift = connectorE4TableGeometry(connector).width - oldWidth;
    if (shift) return { ...connector, positions: { ...connector.positions, e4: { ...connector.positions.e4, x: connector.positions.e4.x - shift } } };
  }
  return connector;
}

export function parseDrawingPlacements(value:unknown):ConnectorDrawingPlacement[] {
  if(!Array.isArray(value) || value.length>2000) throw new Error("Некорректный список рисунков компонента.");
  const result=value.map(item=>{
    const record=requireRecord(item,"Некорректный рисунок.");
    if(typeof record.visible!=="boolean") throw new Error("Некорректная видимость рисунка.");
    if(record.rotationDegrees!==undefined && (typeof record.rotationDegrees!=="number" || !Number.isFinite(record.rotationDegrees)||Math.abs(record.rotationDegrees)>360))throw new Error("Некорректный угол рисунка.");
    if(record.scale!==undefined && (typeof record.scale!=="number" || !validDrawingScale(record.scale))) throw new Error("Масштаб рисунка: 5–2000%.");
    return {drawingId:requireText(record.drawingId,"ID рисунка"),visible:record.visible,offset:parsePoint(record.offset),...(record.scale===undefined?{}:{scale:record.scale as number}),...(record.rotationDegrees===undefined?{}:{rotationDegrees:record.rotationDegrees as number})};
  });
  if(new Set(result.map(p=>p.drawingId)).size!==result.length) throw new Error("Повторный ID рисунка.");
  return result;
}

/** Validates additive library metadata without requiring the external series catalog. */
export function validateConnectorLibraryMetadata(connector: ConnectorInstance): void {
  const binding = connector.libraryBinding;
  if (binding === undefined || binding.mode === "free") {
    if (connector.contacts.some((contact) => contact.libraryContact !== undefined && contact.libraryContact !== null)) {
      throw new Error("Свободный соединитель не должен ссылаться на позиции библиотечной серии.");
    }
    return;
  }
  if (binding.mode === "template") {
    validateComponentTemplateBinding(connector, binding);
    return;
  }
  if (!binding.seriesId.trim() || !binding.partNumber.trim()) {
    throw new Error("Привязка соединителя к серии задана неверно.");
  }
  if (connector.partNumber !== binding.partNumber) {
    throw new Error("Артикул соединителя не совпадает с выбранным артикулом серии.");
  }
  const nextOrdinal: Record<ConnectorLibraryContactKind, number> = { signal: 1, power: 1, third: 1 };
  const kindOrder: Readonly<Record<ConnectorLibraryContactKind, number>> = { signal: 0, power: 1, third: 2 };
  let previousKindOrder = 0;
  connector.contacts.forEach((contact, index) => {
    const position = contact.libraryContact;
    if (!position) throw new Error("У библиотечного контакта отсутствует позиция в серии.");
    const order = kindOrder[position.kind];
    if (order === undefined || !Number.isSafeInteger(position.ordinal) || position.ordinal < 1 ||
        order < previousKindOrder || position.ordinal !== nextOrdinal[position.kind]) {
      throw new Error("Позиции библиотечных контактов заданы непоследовательно.");
    }
    if (contact.number !== index + 1 ||
        contact.id !== `${connector.id}:contact:${position.kind}:${position.ordinal}`) {
      throw new Error("ID или номер библиотечного контакта не соответствует его позиции в серии.");
    }
    nextOrdinal[position.kind] += 1;
    previousKindOrder = order;
  });
}

function parseConnectorLibraryBinding(value: unknown): ConnectorLibraryBinding | undefined {
  if (value === undefined) return undefined;
  const record = requireRecord(value, "Привязка соединителя к библиотечной серии задана неверно.");
  if (record.mode === "free") return { mode: "free" };
  if (record.mode === "template") {
    if (record.contactNumbering !== undefined && record.contactNumbering !== "source-v1")
      throw new Error("Неизвестная версия нумерации контактов шаблона. Обновите приложение.");
    const binding = {
      mode: "template" as const,
      ...(record.contactNumbering === "source-v1" ? {contactNumbering: "source-v1" as const} : {}),
      templateId: requireText(record.templateId, "ID шаблона компонента"),
      templateVersion: requireInteger(record.templateVersion, "Версия шаблона компонента", 1, 1_000_000),
      versionSha256: parseSha256(record.versionSha256, "Хэш версии шаблона компонента"),
      articleVariantId: requireText(record.articleVariantId, "ID варианта артикула"),
      article: parseTemplateArticleKey(record.article, "Артикул экземпляра компонента"),
      snapshot: parseComponentTemplateSnapshot(record.snapshot),
    };
    validateTemplateBindingSnapshotIdentity(binding);
    return binding;
  }
  if (record.mode !== "series") throw new Error("Режим библиотечного соединителя задан неверно.");
  return {
    mode: "series",
    seriesId: requireText(record.seriesId, "ID серии соединителя"),
    partNumber: requireText(record.partNumber, "Артикул соединителя серии"),
  };
}

function validateComponentTemplateBinding(
  connector: ConnectorInstance,
  binding: Extract<ConnectorLibraryBinding, { readonly mode: "template" }>,
): void {
  validateTemplateBindingSnapshotIdentity(binding);
  if (connector.terminalCatalog && connector.terminalCatalog.templateId !== binding.templateId) {
    throw new Error("Список терминалов принадлежит другому шаблону.");
  }
  if (connector.partNumber !== binding.article.articleKey) {
    throw new Error("Артикул соединителя не совпадает с закреплённым вариантом шаблона.");
  }
  if (connector.libraryCode !== binding.snapshot.code) {
    throw new Error("Код соединителя не совпадает с закреплённым шаблоном.");
  }
  if (connector.contacts.length !== binding.snapshot.contacts.length) {
    throw new Error("Число контактов не совпадает с закреплённым вариантом шаблона.");
  }
  const logicalIds = new Set<string>();
  if (binding.contactNumbering !== undefined && binding.contactNumbering !== "source-v1")
    throw new Error("Неизвестная версия нумерации контактов шаблона.");
  const sourceByLogicalId = new Map(binding.snapshot.contacts.map(contact => [contact.logicalContactId, contact]));
  if (sourceByLogicalId.size !== binding.snapshot.contacts.length)
    throw new Error("Логические ID материализованных контактов должны быть уникальны.");
  const numbers = binding.contactNumbering === "source-v1"
    ? templateContactNumbers(binding.snapshot.contacts.map(contact => contact.sourceNumber))
    : connector.contacts.map((_, index) => index + 1);
  connector.contacts.forEach((contact, index) => {
    const snapshotContact = sourceByLogicalId.get(contact.logicalContactId!);
    if (!contact.logicalContactId || logicalIds.has(contact.logicalContactId) ||
        !snapshotContact ||
        contact.id !== `${connector.id}:contact:${contact.logicalContactId}` ||
        contact.number !== (binding.contactNumbering === "source-v1" ? Number(snapshotContact.sourceNumber) : numbers[index]) || contact.libraryContact !== null) {
      throw new Error("Контакты не соответствуют закреплённой материализации шаблона.");
    }
    logicalIds.add(contact.logicalContactId);
    if (contact.terminalArticle && !templateTerminalChoices(connector, contact.logicalContactId).includes(contact.terminalArticle)) {
      throw new Error("Терминал контакта не входит в список совместимых терминалов закреплённого шаблона.");
    }
  });
}

function parseConnectorTerminalCatalog(value: unknown): ConnectorTerminalCatalog {
  const record = requireRecord(value, "Список терминалов задан неверно.");
  const byContact = requireRecord(record.byContact, "Контакты списка терминалов заданы неверно.");
  return {
    templateId: requireText(record.templateId, "ID шаблона терминалов"),
    version: requireInteger(record.version, "Версия списка терминалов", 1, 1_000_000),
    versionSha256: parseSha256(record.versionSha256, "Хэш списка терминалов"),
    byContact: Object.fromEntries(Object.entries(byContact).map(([key, values]) => {
      if (!Array.isArray(values) || values.length > 256) throw new Error("Список терминалов контакта задан неверно.");
      return [key, [...new Set(values.map(value => requireBoundedText(value, "Артикул терминала", 512)))]];
    })),
  };
}

function validateTemplateBindingSnapshotIdentity(
  binding: Extract<ConnectorLibraryBinding, { readonly mode: "template" }>,
): void {
  const snapshot = binding.snapshot;
  if (snapshot.templateId !== binding.templateId ||
      snapshot.templateVersion !== binding.templateVersion ||
      snapshot.versionSha256 !== binding.versionSha256 ||
      snapshot.articleVariantId !== binding.articleVariantId ||
      !sameTemplateArticleKey(snapshot.article, binding.article)) {
    throw new Error("Привязка не совпадает с закреплённым снимком шаблона.");
  }
  if (!snapshot.articleBindings.some((candidate) => sameTemplateArticleKey(candidate, snapshot.article))) {
    throw new Error("Артикул экземпляра отсутствует в закреплённом индексе шаблона.");
  }
}

function sameTemplateArticleKey(
  left: ComponentTemplateArticleKeySnapshot,
  right: ComponentTemplateArticleKeySnapshot,
): boolean {
  return left.sourceId === right.sourceId && left.entityType === right.entityType &&
    left.articleKey === right.articleKey;
}

function parseComponentTemplateSnapshot(value: unknown): ComponentTemplateMaterializedSnapshot {
  const record = requireRecord(value, "Закреплённый снимок шаблона задан неверно.");
  if (!Array.isArray(record.articleBindings) || !Array.isArray(record.assets) || !Array.isArray(record.contacts)) {
    throw new Error("Состав закреплённого снимка шаблона задан неверно.");
  }
  const articleBindings = record.articleBindings.map((candidate) =>
    parseTemplateArticleKey(candidate, "Артикул индекса шаблона"));
  requireUniqueTemplateArticleKeys(articleBindings, "Артикулы индекса шаблона должны быть уникальны.");
  const assets = record.assets.map(parseTemplateAssetSnapshot);
  if (new Set(assets.map((asset) => asset.assetId)).size !== assets.length) {
    throw new Error("Ресурсы закреплённого шаблона должны иметь уникальные ID.");
  }
  const contacts = record.contacts.map(parseTemplateContactSnapshot);
  if (new Set(contacts.map((contact) => contact.logicalContactId)).size !== contacts.length) {
    throw new Error("Логические ID материализованных контактов должны быть уникальны.");
  }
  return deepFreezeTemplateSnapshot({
    templateId: requireText(record.templateId, "ID закреплённого шаблона"),
    templateVersion: requireInteger(record.templateVersion, "Версия закреплённого шаблона", 1, 1_000_000),
    versionSha256: parseSha256(record.versionSha256, "Хэш закреплённого шаблона"),
    code: requireBoundedText(record.code, "Код закреплённого шаблона", 120),
    name: requireBoundedText(record.name, "Название закреплённого шаблона", 256),
    articleVariantId: requireText(record.articleVariantId, "ID варианта артикула закреплённого шаблона"),
    article: parseTemplateArticleKey(record.article, "Артикул закреплённого шаблона"),
    articleBindings,
    assets,
    contacts,
  });
}

function parseTemplateArticleKey(value: unknown, name: string): ComponentTemplateArticleKeySnapshot {
  const record = requireRecord(value, `${name} задан неверно.`);
  return {
    sourceId: requireBoundedText(record.sourceId, `${name}: источник`, 128),
    entityType: requireBoundedText(record.entityType, `${name}: тип сущности`, 64),
    articleKey: requireBoundedText(record.articleKey, `${name}: ключ`, 512),
  };
}

function requireUniqueTemplateArticleKeys(
  values: readonly ComponentTemplateArticleKeySnapshot[],
  message: string,
): void {
  const keys = values.map((value) => `${value.sourceId}\0${value.entityType}\0${value.articleKey}`);
  if (new Set(keys).size !== keys.length) throw new Error(message);
}

function parseTemplateAssetSnapshot(value: unknown): ComponentTemplateAssetSnapshot {
  const record = requireRecord(value, "Ресурс закреплённого шаблона задан неверно.");
  return {
    assetId: requireText(record.assetId, "ID ресурса закреплённого шаблона"),
    sha256: parseSha256(record.sha256, "Хэш ресурса закреплённого шаблона"),
    sizeBytes: requireInteger(record.sizeBytes, "Размер ресурса закреплённого шаблона", 0, 10 * 1024 * 1024),
    fileName: requireBoundedText(record.fileName, "Имя ресурса закреплённого шаблона", 255),
    mediaType: requireBoundedText(record.mediaType, "Тип ресурса закреплённого шаблона", 120),
  };
}

function parseTemplateContactSnapshot(value: unknown): ComponentTemplateContactSnapshot {
  const record = requireRecord(value, "Материализованный контакт шаблона задан неверно.");
  if (!Array.isArray(record.allowedTerminalArticleKeys) || !Array.isArray(record.representations)) {
    throw new Error("Состав материализованного контакта шаблона задан неверно.");
  }
  const allowedTerminalArticleKeys = record.allowedTerminalArticleKeys.map((candidate) =>
    parseTemplateArticleKey(candidate, "Допустимый терминал"));
  requireUniqueTemplateArticleKeys(allowedTerminalArticleKeys, "Допустимые терминалы контакта должны быть уникальны.");
  const representations = record.representations.map(parseTemplateContactRepresentationSnapshot);
  return {
    logicalContactId: requireText(record.logicalContactId, "Логический ID материализованного контакта"),
    prototypeLogicalContactId: requireText(record.prototypeLogicalContactId, "ID прототипа материализованного контакта"),
    sourceNumber: requireBoundedText(record.sourceNumber, "Номер материализованного контакта", 128),
    name: requireBoundedText(record.name, "Имя материализованного контакта", 256),
    circuitText: record.circuitText === null ? null : requireString(record.circuitText, "Цепь материализованного контакта"),
    contactTypeGroupId: record.contactTypeGroupId === null
      ? null
      : requireText(record.contactTypeGroupId, "ID группы типа материализованного контакта"),
    contactType: requireString(record.contactType, "Тип материализованного контакта"),
    allowedTerminalArticleKeys,
    representations,
  };
}

function parseTemplateContactRepresentationSnapshot(
  value: unknown,
): ComponentTemplateContactRepresentationSnapshot {
  const record = requireRecord(value, "Представление материализованного контакта задано неверно.");
  if (record.viewKind !== "e4" && record.viewKind !== "drawing" && record.viewKind !== "additional") {
    throw new Error("Вид представления материализованного контакта задан неверно.");
  }
  if (record.direction !== "left" && record.direction !== "right" &&
      record.direction !== "up" && record.direction !== "down") {
    throw new Error("Направление представления материализованного контакта задано неверно.");
  }
  return {
    viewId: requireText(record.viewId, "ID вида материализованного контакта"),
    viewName: requireBoundedText(record.viewName, "Название вида материализованного контакта", 256),
    viewKind: record.viewKind,
    pointId: requireText(record.pointId, "ID точки материализованного контакта"),
    occurrenceKey: record.occurrenceKey === undefined
      ? undefined
      : requireText(record.occurrenceKey, "Ключ повторной точки материализованного контакта"),
    x: requireNumber(record.x, "Координата X материализованного контакта"),
    y: requireNumber(record.y, "Координата Y материализованного контакта"),
    direction: record.direction,
  };
}

function deepFreezeTemplateSnapshot(
  snapshot: ComponentTemplateMaterializedSnapshot,
): ComponentTemplateMaterializedSnapshot {
  for (const article of snapshot.articleBindings) Object.freeze(article);
  for (const terminal of snapshot.contacts.flatMap((contact) => contact.allowedTerminalArticleKeys)) Object.freeze(terminal);
  for (const asset of snapshot.assets) Object.freeze(asset);
  for (const contact of snapshot.contacts) {
    for (const terminal of contact.allowedTerminalArticleKeys) Object.freeze(terminal);
    for (const representation of contact.representations) Object.freeze(representation);
    Object.freeze(contact.allowedTerminalArticleKeys);
    Object.freeze(contact.representations);
    Object.freeze(contact);
  }
  Object.freeze(snapshot.article);
  Object.freeze(snapshot.articleBindings);
  Object.freeze(snapshot.assets);
  Object.freeze(snapshot.contacts);
  return Object.freeze(snapshot);
}

function parseSha256(value: unknown, name: string): string {
  const result = requireString(value, name);
  if (!/^[0-9a-f]{64}$/.test(result)) throw new Error(`${name} задан неверно.`);
  return result;
}

function parseConnectorLibraryContact(value: unknown): ConnectorLibraryContact | null | undefined {
  if (value === undefined) return undefined;
  if (value === null) return null;
  const record = requireRecord(value, "Позиция контакта в библиотечной серии задана неверно.");
  if (record.kind !== "signal" && record.kind !== "power" && record.kind !== "third") {
    throw new Error("Тип контакта библиотечной серии задан неверно.");
  }
  return {
    kind: record.kind,
    ordinal: requireInteger(record.ordinal, "Порядковый номер контакта серии", 1, 300),
  };
}

export function parseConnectorSchematic(value: unknown): ConnectorSchematicPresentation {
  if (value === undefined) {
    return { orientation: "contacts-right", baseColumns: createDefaultConnectorBaseColumns(), customFields: [] };
  }
  const record = requireRecord(value, "Представление соединителя на схеме задано неверно.");
  const orientation = record.orientation === "contacts-left" || record.orientation === "contacts-right"
    ? record.orientation
    : (() => { throw new Error("Ориентация соединителя на схеме задана неверно."); })();
  const baseColumns = parseBaseColumns(record.baseColumns);
  const customFields = record.customFields === undefined
    ? []
    : parseCustomFields(record.customFields);
  if (record.showName !== undefined && typeof record.showName !== "boolean") throw new Error("Видимость назначения задана неверно.");
  if (record.rowOrder !== undefined && (!Array.isArray(record.rowOrder) ||
      record.rowOrder.some(id => typeof id !== "string" || !id.trim()) ||
      new Set(record.rowOrder).size !== record.rowOrder.length)) {
    throw new Error("Порядок строк контактов задан неверно.");
  }
  return { orientation, baseColumns, customFields,
    ...(record.rowOrder === undefined ? {} : { rowOrder: record.rowOrder as string[] }),
    ...(record.showName === undefined ? {} : { showName: record.showName as boolean }) };
}

function parseBaseColumns(value: unknown): readonly ConnectorBaseColumn[] {
  if (value === undefined) return createDefaultConnectorBaseColumns();
  if (!Array.isArray(value)) throw new Error("Базовые колонки соединителя заданы неверно.");
  const byKey = new Map<ConnectorBaseColumnKey, ConnectorBaseColumn>();
  for (const columnValue of value) {
    const column = requireRecord(columnValue, "Базовая колонка соединителя задана неверно.");
    if (!connectorBaseColumnKeys.includes(column.key as ConnectorBaseColumnKey) || typeof column.visible !== "boolean") {
      throw new Error("Базовая колонка соединителя задана неверно.");
    }
    const key = column.key as ConnectorBaseColumnKey;
    if (byKey.has(key)) throw new Error("Базовые колонки соединителя не должны повторяться.");
    byKey.set(key, { key, visible: column.visible });
  }
  return connectorBaseColumnKeys.map((key) => byKey.get(key) ?? { key, visible: key === "wireSection" ? byKey.get("wire")?.visible ?? true : true });
}

function parseCustomFields(value: unknown): readonly ConnectorCustomField[] {
  if (!Array.isArray(value)) throw new Error("Справочные поля соединителя заданы неверно.");
  const fields = value.map((fieldValue) => {
    const field = requireRecord(fieldValue, "Справочное поле соединителя задано неверно.");
    if (typeof field.visible !== "boolean") throw new Error("Видимость справочного поля задана неверно.");
    return {
      id: requireText(field.id, "ID справочного поля"),
      label: requireBoundedText(field.label, "Название справочного поля", 120),
      visible: field.visible,
    };
  });
  if (new Set(fields.map((field) => field.id)).size !== fields.length) {
    throw new Error("ID справочных полей должны быть уникальны.");
  }
  return fields;
}

function parseContactStatus(value: unknown): ConnectorContactStatus {
  if (value === undefined) return "available";
  if (value !== "available" && value !== "not-connected") {
    throw new Error("Статус подключения контакта задан неверно.");
  }
  return value;
}

function parseCustomValues(value: unknown): Readonly<Record<string, string>> {
  if (value === undefined) return {};
  const record = requireRecord(value, "Значения справочных полей контакта заданы неверно.");
  return Object.fromEntries(Object.entries(record).map(([key, item]) => [
    requireText(key, "ID справочного поля"),
    requireString(item, "Значение справочного поля"),
  ]));
}

function parseWire(value: unknown): WireInstance {
  const record = requireRecord(value, "Провод задан неверно.");
  const layerIds = requireRecord(record.layerIds, "Слои провода заданы неверно.");
  if (!Array.isArray(record.drawingRoute) || (record.e4Route !== undefined && !Array.isArray(record.e4Route))) {
    throw new Error("Трасса провода задана неверно.");
  }
  const lengthMm = record.lengthMm === undefined || record.lengthMm === null
    ? null
    : validateWirePhysicalLength(requireNumber(record.lengthMm, "Длина провода"));
  const endCorrectionFromMm = record.endCorrectionFromMm === undefined
    ? 0
    : validateWireCorrection(requireNumber(record.endCorrectionFromMm, "Поправка начала провода"), "Поправка начала провода");
  const endCorrectionToMm = record.endCorrectionToMm === undefined
    ? 0
    : validateWireCorrection(requireNumber(record.endCorrectionToMm, "Поправка конца провода"), "Поправка конца провода");
  const cutRoundingStepMm = record.cutRoundingStepMm === undefined
    ? defaultWireCutRoundingStepMm
    : validateWireRoundingStep(requireNumber(record.cutRoundingStepMm, "Шаг округления длины резки"));
  calculateWireCutLength({ lengthMm, endCorrectionFromMm, endCorrectionToMm, cutRoundingStepMm });
  const drawingEndpoints = record.drawingEndpoints === undefined ? undefined : (() => {
    const endpoints = requireRecord(record.drawingEndpoints, "Свободные концы рисунка заданы неверно.");
    return { from: parsePoint(endpoints.from), to: parsePoint(endpoints.to) };
  })();
  const drawingEndStyles = record.drawingEndStyles === undefined ? undefined : (() => {
    const styles = requireRecord(record.drawingEndStyles, "Режимы отображения концов заданы неверно.");
    if (!wireBlankEnds.includes(styles.from as WireBlankEnd) || !wireBlankEnds.includes(styles.to as WireBlankEnd)) {
      throw new Error("Режим отображения конца провода задан неверно.");
    }
    return { from: styles.from as WireBlankEnd, to: styles.to as WireBlankEnd };
  })();
  return {
    id: requireText(record.id, "ID провода"),
    from: parseEndpoint(record.from),
    to: parseEndpoint(record.to),
    circuit: requireString(record.circuit, "Цепь провода"),
    color: requireText(record.color, "Цвет провода"),
    colorSource: record.colorSource === undefined || record.colorSource === null
      ? record.colorSource : parseWireColorSource(record.colorSource),
    materialBinding: record.materialBinding === undefined
      ? undefined : parseWireMaterialBinding(record.materialBinding),
    stripProfiles: record.stripProfiles === undefined
      ? undefined : parseWireEndStripProfiles(record.stripProfiles),
    ...(drawingEndpoints ? { drawingEndpoints } : {}),
    ...(drawingEndStyles ? { drawingEndStyles } : {}),
    lengthMm,
    endCorrectionFromMm,
    endCorrectionToMm,
    cutRoundingStepMm,
    e4Route: record.e4Route === undefined ? [] : record.e4Route.map(parsePoint),
    e4RouteMode: record.e4RouteMode === undefined || record.e4RouteMode === "auto"
      ? "auto"
      : record.e4RouteMode === "manual" ? "manual" : (() => { throw new Error("Режим трассы Э4 задан неверно."); })(),
    e4LabelPosition: record.e4LabelPosition === undefined
      ? 0.5
      : parseNormalizedPosition(record.e4LabelPosition, "Положение обозначения провода"),
    drawingRoute: record.drawingRoute.map(parsePoint),
    layerIds: {
      e4: requireText(layerIds.e4, "Слой провода Э4"),
      drawing: requireText(layerIds.drawing, "Слой провода чертежа"),
    },
  };
}

function parseCables(value: unknown): readonly CableInstance[] {
  if (!Array.isArray(value)) throw new Error("Кабели заданы неверно.");
  return value.map(normalizeCableInstance);
}

/** Validates and copies a physical multicore cable before it enters a document. */
export function normalizeCableInstance(value: unknown): CableInstance {
  const record = requireRecord(value, "Кабель задан неверно.");
  if (!Array.isArray(record.memberWireIds)) throw new Error("Состав жил кабеля задан неверно.");
  const memberWireIds = record.memberWireIds.map((item) => requireText(item, "ID жилы кабеля"));
  if (new Set(memberWireIds).size !== memberWireIds.length) {
    throw new Error("Состав жил кабеля не должен содержать повторяющиеся провода.");
  }
  const materialBinding = record.materialBinding === undefined
    ? undefined
    : parseWireMaterialBinding(record.materialBinding);
  if (materialBinding !== undefined && materialBinding.entityType !== "cable") {
    throw new Error("Материал многожильного кабеля должен иметь тип cable.");
  }
  const lengthMm = record.lengthMm === undefined || record.lengthMm === null
    ? null
    : validateWirePhysicalLength(requireNumber(record.lengthMm, "Длина кабеля"), "Длина кабеля");
  const endCorrectionFromMm = record.endCorrectionFromMm === undefined
    ? 0
    : validateWireCorrection(requireNumber(record.endCorrectionFromMm, "Поправка начала кабеля"), "Поправка начала кабеля");
  const endCorrectionToMm = record.endCorrectionToMm === undefined
    ? 0
    : validateWireCorrection(requireNumber(record.endCorrectionToMm, "Поправка конца кабеля"), "Поправка конца кабеля");
  const cutRoundingStepMm = record.cutRoundingStepMm === undefined
    ? defaultWireCutRoundingStepMm
    : validateWireRoundingStep(requireNumber(record.cutRoundingStepMm, "Шаг округления длины резки кабеля"));
  calculateWireCutLength({ lengthMm, endCorrectionFromMm, endCorrectionToMm, cutRoundingStepMm });
  const sheathRecord = record.sheathStrip === undefined ? undefined : requireRecord(record.sheathStrip, "Снятие оболочки задано неверно.");
  const sheathStrip = sheathRecord === undefined ? undefined : Object.freeze({
    fromMm: sheathRecord.fromMm === null ? null : validateWirePhysicalLength(requireNumber(sheathRecord.fromMm, "Снятие оболочки начала")),
    toMm: sheathRecord.toMm === null ? null : validateWirePhysicalLength(requireNumber(sheathRecord.toMm, "Снятие оболочки конца")),
  });
  const cable = Object.freeze({
    id: requireText(record.id, "ID кабеля"),
    memberWireIds: Object.freeze(memberWireIds),
    materialBinding,
    lengthMm,
    endCorrectionFromMm,
    endCorrectionToMm,
    cutRoundingStepMm,
    ...(sheathStrip === undefined ? {} : { sheathStrip }),
  });
  calculateCableSheathStrip(cable);
  return cable;
}

function validateCables(cables: readonly CableInstance[], wireIds: ReadonlySet<string>): void {
  if (new Set(cables.map((cable) => cable.id)).size !== cables.length) {
    throw new Error("ID кабелей должны быть уникальны.");
  }
  const assignedWireIds = new Set<string>();
  for (const cable of cables) {
    for (const wireId of cable.memberWireIds) {
      if (!wireIds.has(wireId)) throw new Error("Кабель ссылается на отсутствующий провод.");
      if (assignedWireIds.has(wireId)) throw new Error("Провод может входить только в один кабель.");
      assignedWireIds.add(wireId);
    }
  }
}

export function parseOuterDiameter(value:unknown):number {
  if(typeof value!=="number"||!Number.isFinite(value)||value<=0||value>1000)throw new Error("Диаметр изоляции должен быть больше 0 и не больше 1000 мм.");
  return value;
}

function parseWireMaterialBinding(value: unknown): WireMaterialBinding {
  const record = requireRecord(value, "Привязка материала провода задана неверно.");
  if (record.entityType !== "wire" && record.entityType !== "cable") {
    throw new Error("Тип материала провода задан неверно.");
  }
  return Object.freeze({
    ...(record.outerDiameterMm===undefined?{}:{outerDiameterMm:parseOuterDiameter(record.outerDiameterMm)}),
    sourceId: requireBoundedText(record.sourceId, "Источник материала провода", 128),
    snapshotId: parseNonEmptyGuid(record.snapshotId, "ID снимка материала провода"),
    snapshotSha256: parseSha256(record.snapshotSha256, "Хэш снимка материала провода"),
    recordId: parseSha256(record.recordId, "ID записи материала провода"),
    entityType: record.entityType,
    sourceKey: requireBoundedText(record.sourceKey, "Ключ материала провода", 512),
    displayName: requireBoundedText(record.displayName, "Название материала провода", 256),
  });
}

function parseWireEndStripProfiles(value: unknown): WireEndStripProfiles | undefined {
  const record = requireRecord(value, "Профили разделки концов провода заданы неверно.");
  const from = record.from === undefined ? undefined : normalizeWireStripProfileBinding(record.from);
  const to = record.to === undefined ? undefined : normalizeWireStripProfileBinding(record.to);
  return from === undefined && to === undefined ? undefined : Object.freeze({ from, to });
}

/** Validates and snapshots a catalog profile before it is stored in a command result. */
export function normalizeWireStripProfileBinding(value: unknown): WireStripProfileBinding {
  const record = requireRecord(value, "Привязка профиля разделки задана неверно.");
  if (record.entityType !== "coax-termination") {
    throw new Error("Тип профиля разделки задан неверно.");
  }
  if (!Array.isArray(record.layers)) throw new Error("Слои профиля разделки заданы неверно.");
  const layers = normalizeWireStripProfileLayers(record.layers.map((item) => {
    const layer = requireRecord(item, "Слой профиля разделки задан неверно.");
    return {
      index: requireInteger(layer.index, "Номер слоя профиля разделки", 1, Number.MAX_SAFE_INTEGER),
      diameterMm: requireNumber(layer.diameterMm, "Диаметр слоя профиля разделки"),
      stripLengthMm: requireNumber(layer.stripLengthMm, "Длина слоя профиля разделки"),
    };
  }));
  return Object.freeze({
    sourceId: requireBoundedText(record.sourceId, "Источник профиля разделки", 128),
    snapshotId: parseNonEmptyGuid(record.snapshotId, "ID снимка профиля разделки"),
    snapshotSha256: parseSha256(record.snapshotSha256, "Хэш снимка профиля разделки"),
    recordId: parseSha256(record.recordId, "ID записи профиля разделки"),
    entityType: record.entityType,
    sourceKey: requireBoundedText(record.sourceKey, "Ключ профиля разделки", 512),
    displayName: requireBoundedText(record.displayName, "Название профиля разделки", 256),
    layers,
  });
}

function normalizeWireStripProfileLayers(layers: readonly WireStripProfileLayer[]): readonly WireStripProfileLayer[] {
  if (layers.length < 1 || layers.length > maximumWireStripLayers) {
    throw new Error(`Профиль разделки должен содержать от 1 до ${maximumWireStripLayers} слоёв.`);
  }
  let previousIndex = 0;
  let previousDiameterMicrometres = 0n;
  let previousLengthMicrometres = 0n;
  const normalized = layers.map((layer) => {
    if (!Number.isSafeInteger(layer.index) || layer.index < 1) {
      throw new Error("Номер слоя профиля разделки должен быть положительным целым числом.");
    }
    if (layer.index <= previousIndex) {
      throw new Error("Номера слоёв профиля разделки должны быть уникальны и расположены по возрастанию.");
    }
    const diameterMicrometres = validateWireStripMeasurement(layer.diameterMm, `D${layer.index}`);
    const lengthMicrometres = validateWireStripMeasurement(layer.stripLengthMm, `L${layer.index}`);
    if (diameterMicrometres <= previousDiameterMicrometres) {
      throw new Error("Диаметры слоёв профиля разделки должны строго возрастать.");
    }
    if (lengthMicrometres <= previousLengthMicrometres) {
      throw new Error("Длины слоёв профиля разделки должны строго возрастать.");
    }
    previousIndex = layer.index;
    previousDiameterMicrometres = diameterMicrometres;
    previousLengthMicrometres = lengthMicrometres;
    return Object.freeze({ index: layer.index, diameterMm: layer.diameterMm, stripLengthMm: layer.stripLengthMm });
  });
  return Object.freeze(normalized);
}

function validateWireStripMeasurement(value: number, name: string): bigint {
  if (!Number.isFinite(value) || value <= 0 || value > maximumWireLengthMm) {
    throw new Error(`${name} должна быть положительным числом в миллиметрах.`);
  }
  return toExactMicrometres(value, name);
}

function parseWireColorSource(value: unknown): WireColorSource {
  const record = requireRecord(value, "Источник цвета провода задан неверно.");
  return {
    connectorId: requireText(record.connectorId, "Соединитель источника цвета"),
    contactId: requireText(record.contactId, "Контакт источника цвета"),
  };
}

function parseContactColorMode(value: unknown): "auto" | "manual" {
  if (value === "auto" || value === "manual") return value;
  throw new Error("Режим цвета контакта задан неверно.");
}

function normalizeWireColorSource(document: HarnessDesignDocument, wire: WireInstance): WireColorSource | null | undefined {
  if (isJunctionEndpoint(wire.from) || isScreenEndpoint(wire.from) ||
      isJunctionEndpoint(wire.to) || isScreenEndpoint(wire.to)) return undefined;
  if (wire.colorSource === null) return null;
  const endpoints = [wire.from, wire.to] as const;
  const source = wire.colorSource && endpoints.some((endpoint) =>
    endpoint.connectorId === wire.colorSource!.connectorId && endpoint.contactId === wire.colorSource!.contactId)
    ? wire.colorSource
    : endpoints.find((endpoint) => document.connectors.find((connector) => connector.id === endpoint.connectorId)
      ?.contacts.find((contact) => contact.id === endpoint.contactId)?.color.trim()) ?? endpoints[0];
  return { connectorId: source.connectorId, contactId: source.contactId };
}

function validateWirePhysicalLength(value: number | null, name = "Длина провода"): number | null {
  if (value === null) return null;
  if (!Number.isFinite(value) || value < 0 || value > maximumWireLengthMm) {
    throw new Error(`${name} должна быть неотрицательным числом в миллиметрах или неизвестной.`);
  }
  toExactMicrometres(value, name);
  return value;
}

function validateWireCorrection(value: number, name: string): number {
  if (!Number.isFinite(value) || Math.abs(value) > maximumWireLengthMm) {
    throw new Error(`${name} задана неверно.`);
  }
  toExactMicrometres(value, name);
  return value;
}

function validateWireRoundingStep(value: number): number {
  if (!Number.isFinite(value) || value <= 0 || value > maximumWireLengthMm) {
    throw new Error("Шаг округления длины резки должен быть положительным числом в миллиметрах.");
  }
  toExactMicrometres(value, "Шаг округления длины резки");
  return value;
}

function toExactMicrometres(value: number, name: string): bigint {
  const match = /^(-?)(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?$/i.exec(value.toString());
  if (!match) {
    throw new Error(`${name} должна задаваться с точностью не более 0,001 мм.`);
  }
  const sign = match[1] === "-" ? -1n : 1n;
  const fraction = match[3] ?? "";
  const exponent = Number(match[4] ?? "0");
  const digits = BigInt(`${match[2]}${fraction}`);
  const micrometreExponent = exponent - fraction.length + 3;
  if (micrometreExponent >= 0) {
    return sign * digits * 10n ** BigInt(micrometreExponent);
  }
  const divisor = 10n ** BigInt(-micrometreExponent);
  if (digits % divisor !== 0n) {
    throw new Error(`${name} должна задаваться с точностью не более 0,001 мм.`);
  }
  return sign * (digits / divisor);
}

function parseNormalizedPosition(value: unknown, name: string): number {
  const number = requireNumber(value, name);
  if (number < 0 || number > 1) throw new Error(`${name} должно быть от 0 до 1.`);
  return number;
}

function parseEndpoint(value: unknown): WireEndpoint {
  const record = requireRecord(value, "Конец провода задан неверно.");
  if (record.junctionId !== undefined) return createJunctionEndpoint(requireText(record.junctionId, "Узел конца провода"));
  if (record.screenId !== undefined) {
    const side = record.screenTerminalSide;
    if (side !== undefined && side !== "above" && side !== "below") {
      throw new Error("Сторона точки подключения экрана задана неверно.");
    }
    return createScreenEndpoint(requireText(record.screenId, "Экран конца провода"), side);
  }
  return {
    connectorId: requireText(record.connectorId, "Соединитель конца провода"),
    contactId: requireText(record.contactId, "Контакт конца провода"),
  };
}

function parseView(value: unknown, isE4 = false): EditorViewState {
  const record = requireRecord(value, "Представление документа задано неверно.");
  if (!Array.isArray(record.layers)) throw new Error("Слои представления заданы неверно.");
  const layers = record.layers.map((layerValue) => {
    const layer = requireRecord(layerValue, "Слой задан неверно.");
    if (typeof layer.visible !== "boolean" || typeof layer.locked !== "boolean") {
      throw new Error("Состояние слоя задано неверно.");
    }
    return {
      id: requireText(layer.id, "ID слоя"),
      name: requireText(layer.name, "Имя слоя"),
      order: requireInteger(layer.order, "Порядок слоя", 0, 10_000),
      visible: layer.visible,
      locked: layer.locked,
    };
  });
  if (new Set(layers.map((layer) => layer.id)).size !== layers.length ||
      new Set(layers.map((layer) => layer.order)).size !== layers.length) {
    throw new Error("Слои должны иметь уникальные ID и порядок.");
  }
  // Documents created before M4-93 have no dedicated connection-point layer.
  // Add it on read so physical nodes stay visible above wires and coverings while
  // preserving every user-controlled visibility/lock state of existing layers.
  if (!layers.some((layer) => layer.id === defaultLayerIds.connectionPoints)) {
    const occupiedOrders = new Set(layers.map(layer => layer.order));
    let nextOrder = 10000;
    while (occupiedOrders.has(nextOrder) && nextOrder >= 0) nextOrder--;
    if (nextOrder < 0) throw new Error("Нет свободного порядка для слоя точек соединения.");
    layers.push({ id: defaultLayerIds.connectionPoints, name: "Точки соединения", order: nextOrder, visible: true, locked: false });
  }
  const wireCrossingStyle = record.wireCrossingStyle === undefined ? "none" : record.wireCrossingStyle;
  if (wireCrossingStyle !== "none" && wireCrossingStyle !== "bridge") {
    throw new Error("Режим пересечения проводов задан неверно.");
  }
  const e4RoutingMode = record.e4RoutingMode === undefined ? "orthogonal" : record.e4RoutingMode;
  if (e4RoutingMode !== "orthogonal" && e4RoutingMode !== "angular") {
    throw new Error("Режим угловой трассировки Э4 задан неверно.");
  }
  return isE4 ? { layers, wireCrossingStyle, e4RoutingMode } : { layers, wireCrossingStyle };
}

function parseJunctions(value: unknown): readonly E4Junction[] {
  if (!Array.isArray(value)) throw new Error("Узлы соединения заданы неверно.");
  return value.map((item) => {
    const record = requireRecord(item, "Узел соединения задан неверно.");
    if (!Array.isArray(record.wireIds) || record.wireIds.length < 2) {
      throw new Error("Узел соединения должен объединять не менее двух проводов.");
    }
    const wireIds = record.wireIds.map((id) => requireText(id, "ID провода узла соединения"));
    if (new Set(wireIds).size !== wireIds.length) throw new Error("Провода узла соединения не должны повторяться.");
    return { id: requireText(record.id, "ID узла соединения"), position: parsePoint(record.position), wireIds };
  });
}

function validateJunctions(document: HarnessDesignDocument): void {
  const wires = new Map(document.wires.map((wire) => [wire.id, wire]));
  for (const junction of document.junctions) {
    for (const wireId of junction.wireIds) {
      const wire = wires.get(wireId);
      if (!wire) throw new Error("Узел соединения ссылается на отсутствующий провод.");
      const endsAtJunction = [wire.from, wire.to].some((endpoint) =>
        isJunctionEndpoint(endpoint) && endpoint.junctionId === junction.id);
      if (!endsAtJunction && !wireE4PathContainsPoint(document, wire, junction.position)) {
        throw new Error("Линия провода должна проходить через узел соединения.");
      }
    }
  }
  for (const wire of document.wires) {
    for (const endpoint of [wire.from, wire.to]) if (isJunctionEndpoint(endpoint)) {
      const junction = document.junctions.find((item) => item.id === endpoint.junctionId);
      if (!junction?.wireIds.includes(wire.id)) throw new Error("Узел соединения не содержит завершающийся в нём провод.");
    }
  }
}

export function wireE4PathContainsPoint(
  document: HarnessDesignDocument,
  wire: WireInstance,
  point: Point,
): boolean {
  const start = wireEndpointE4Anchor(document, wire.from)?.position;
  const end = wireEndpointE4Anchor(document, wire.to)?.position;
  if (!start || !end) return false;
  const points = [start, ...wire.e4Route, end];
  return points.slice(1).some((current, index) => pointOnSegment(point, points[index]!, current));
}

export function wireGroupHasScreenCrossSection(document:HarnessDesignDocument,wireIds:readonly string[]):boolean {
  return screenCrossSections(wireIds.map(id=>{
    const wire=document.wires.find(w=>w.id===id);
    const a=wire&&wireEndpointE4Anchor(document,wire.from),b=wire&&wireEndpointE4Anchor(document,wire.to);
    return {id,points:wire&&a&&b?[a.position,...wire.e4Route,b.position]:[]};
  })).length>0;
}

export function wireGroupHasCommonE4ParallelSpan(
  document: HarnessDesignDocument,
  wireIds: readonly string[],
): boolean {
  // Independent drawing copies have no E4 anchors at their free ends.
  // Pair membership survives subsequent drawing edits; its visible spans are
  // resolved from the drawing geometry by the renderer.
  if (wireIds.length >= 2 && wireIds.every(id => document.wires.find(w => w.id === id)?.drawingEndpoints)) return true;
  return commonParallelSpan(wireIds.map(id=>{
    const w=document.wires.find(w=>w.id===id),a=w&&wireEndpointE4Anchor(document,w.from),b=w&&wireEndpointE4Anchor(document,w.to);
    return {id,points:w&&a&&b?[a.position,...w.e4Route,b.position]:[]};
  }))!==null;
}
function pointOnSegment(point: Point, start: Point, end: Point): boolean { return segmentPointDistance(point,{start,end})<1e-7; }

function parseDiffPairs(value: unknown): readonly DiffPairGroup[] {
  if (!Array.isArray(value)) throw new Error("Дифференциальные пары заданы неверно.");
  return value.map((item) => {
    const record = requireRecord(item, "Дифференциальная пара задана неверно.");
    if (!Array.isArray(record.wireIds) || record.wireIds.length !== 2) throw new Error("Дифференциальная пара должна содержать два провода.");
    const wireIds = record.wireIds.map((id) => requireText(id, "ID провода")) as [string, string];
    if (wireIds[0] === wireIds[1]) throw new Error("Дифференциальная пара должна содержать два разных провода.");
    const variant = record.variant === undefined ? 1 : record.variant;
    if (variant !== 1 && variant !== 2) throw new Error("Вид дифференциальной пары задан неверно.");
    return { id: requireText(record.id, "ID дифференциальной пары"), wireIds, step: requirePositive(record.step, "Шаг пары"), amplitude: requirePositive(record.amplitude, "Амплитуда пары"), variant };
  });
}

function parseScreens(value: unknown): readonly WireScreenGroup[] {
  if (!Array.isArray(value)) throw new Error("Экраны проводов заданы неверно.");
  return value.map((item) => {
    const record = requireRecord(item, "Экран проводов задан неверно.");
    if (!Array.isArray(record.wireIds) || record.wireIds.length < 1) throw new Error("Экран должен содержать хотя бы один провод.");
    const wireIds = record.wireIds.map((id) => requireText(id, "ID провода"));
    if (new Set(wireIds).size !== wireIds.length) throw new Error("Провода экрана не должны повторяться.");
    const position = requireNumber(record.position, "Положение экрана");
    if (position < 0 || position > 1) throw new Error("Положение экрана должно быть от 0 до 1.");
    const terminalSide = record.terminalSide === undefined ? "above" : record.terminalSide;
    if (terminalSide !== "above" && terminalSide !== "below" && terminalSide !== "both") {
      throw new Error("Сторона вывода экрана задана неверно.");
    }
    return { id: requireText(record.id, "ID экрана"), wireIds, position, label: requireBoundedText(record.label, "Обозначение экрана", 120), width: requirePositive(record.width, "Ширина экрана"), terminalSide };
  });
}

function requirePositive(value: unknown, name: string): number {
  const result = requireNumber(value, name);
  if (result <= 0) throw new Error(`${name} должно быть положительным числом.`);
  return result;
}

function parsePoint(value: unknown): Point {
  const record = requireRecord(value, "Координата задана неверно.");
  return { x: requireNumber(record.x, "Координата X"), y: requireNumber(record.y, "Координата Y") };
}

function requireRecord(value: unknown, message: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error(message);
  return value as Record<string, unknown>;
}

function parseTerminalDetails(value: unknown): NonNullable<ConnectorContact["terminalDetails"]> {
  const record = requireRecord(value, "Данные терминала заданы неверно.");
  return {
    manufacturer: requireString(record.manufacturer, "Производитель терминала"),
    series: requireString(record.series, "Серия терминала"),
    description: requireString(record.description, "Описание терминала"),
    ...(record.article === undefined ? {} : { article: requireString(record.article, "Артикул терминала") }),
    ...(record.lengthPlusMm === undefined ? {} : { lengthPlusMm: optionalCorrection(record.lengthPlusMm, "L+") }),
    ...(record.lengthMinusMm === undefined ? {} : { lengthMinusMm: optionalCorrection(record.lengthMinusMm, "L-") }),
  };
}

function optionalCorrection(value: unknown, name: string): number | null {
  if (value === null) return null;
  const parsed = requireNumber(value, name);
  if (Math.abs(parsed) > 1_000_000_000) throw new Error(`${name} терминала задана неверно.`);
  return parsed;
}

function requireString(value: unknown, name: string): string {
  if (typeof value !== "string" || value.length > 1024) throw new Error(`${name} задано неверно.`);
  return value;
}

function optionalString(value: unknown, name: string): string {
  return value === undefined ? "" : requireString(value, name);
}

function requireText(value: unknown, name: string): string {
  const result = requireString(value, name);
  if (!result.trim()) throw new Error(`${name} не заполнено.`);
  return result;
}

function requireBoundedText(value: unknown, name: string, maximumLength: number): string {
  const result = requireText(value, name);
  if (result.length > maximumLength) throw new Error(`${name} задано неверно.`);
  return result;
}

function requireNumber(value: unknown, name: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`${name} задано неверно.`);
  return value;
}

function requireInteger(value: unknown, name: string, minimum: number, maximum: number): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) {
    throw new Error(`${name} задано неверно.`);
  }
  return value as number;
}

function parseNonEmptyGuid(value: unknown, name: string): string {
  const result = requireString(value, name);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(result) ||
      /^0{8}-0{4}-0{4}-0{4}-0{12}$/.test(result)) {
    throw new Error(`${name} задан неверно.`);
  }
  return result.toLowerCase();
}
