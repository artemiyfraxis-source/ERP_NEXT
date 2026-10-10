// Каталог редактора жгута из справочников ERP (H-T4): GET /api/techcards/harness-catalog → снимки в форме
// TECHMAP-GRAPHER, которые читают пикеры редактора (терминалы, провода, кабели, коаксиал, защита, разъёмы).
// Образцовый каталог TECHMAP-GRAPHER не используется: источник данных — только наши базы. Всё, на что ссылается
// документ и чего нет в справочниках, помечается «нет в справочнике» (данные для экрана; создание записи — позже).
// Модуль грузится вместе с редактором (index.ts → loadHarnessCatalog), в основной чанк не попадает.
import { harnessApi } from "../api";
import type { CoveringMaterialApi, GlobalMaterial } from "./editor/covering-assets";
import type { CoveringKind } from "./editor/physical-coverings";
import { libraryTemplates } from "./library/library";
import type { HarnessEditorCatalogApi } from "./editor/HarnessDesignEditor";
import type { HarnessDesignDocument } from "./editor/model";
import { resolveTerminalRecord } from "./editor/terminal-details";
import { normalizeWireChoice, normalizeWireSectionChoice, wireDatabaseOption } from "./editor/wire-database";
import {
  snapshotReferenceCatalog,
  ReferenceCatalogApiError,
  type ReferenceCatalogAdapter,
  type ReferenceCatalogRecord,
  type ReferenceCatalogSnapshot,
} from "./reference-catalog";

export const ERP_HARNESS_CATALOG_PATH = "/api/techcards/harness-catalog";
export const ERP_HARNESS_CATALOG_CONTRACT = 1;

/** The editor's catalog sources the ERP fills, by the source id the editor asks for. */
export const erpCatalogSources = {
  wires: "technology-wires",
  terminals: "technology-terminals",
  coaxCables: "technology-coax-cables",
  coaxTerminations: "technology-coax-terminations",
  database: "technology-database",
  connectors: "technology-connectors",
} as const;

export interface ErpHarnessCatalog {
  readonly contractVersion: number;
  readonly capturedUtc: string;
  readonly snapshots: readonly ReferenceCatalogSnapshot[];
  readonly counts: Readonly<Record<string, number>>;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isRecord(value: unknown): value is ReferenceCatalogRecord {
  return isObject(value) && typeof value.recordId === "string" && typeof value.entityType === "string" &&
    typeof value.sourceKey === "string" && isObject(value.payload);
}

function isSnapshot(value: unknown): value is ReferenceCatalogSnapshot {
  return isObject(value) && typeof value.snapshotId === "string" && typeof value.sourceId === "string" &&
    typeof value.sha256 === "string" && Array.isArray(value.records) && value.records.every(isRecord) &&
    Array.isArray(value.diagnostics);
}

/** Checks the server answer before the editor trusts it; a wrong shape is an error, not an empty catalog. */
export function parseErpHarnessCatalog(value: unknown): ErpHarnessCatalog {
  if (!isObject(value) || !Array.isArray(value.snapshots) || !value.snapshots.every(isSnapshot)) {
    throw new ReferenceCatalogApiError("Справочники ERP пришли в неверном формате.", "catalog_invalid");
  }
  if (value.contractVersion !== ERP_HARNESS_CATALOG_CONTRACT) {
    throw new ReferenceCatalogApiError("Версия справочников ERP не подходит редактору. Обновите страницу.", "catalog_contract");
  }
  return {
    contractVersion: value.contractVersion,
    capturedUtc: typeof value.capturedUtc === "string" ? value.capturedUtc : "",
    snapshots: value.snapshots,
    counts: isObject(value.counts) ? value.counts as Record<string, number> : {},
  };
}

export type ErpCatalogLoader = () => Promise<ErpHarnessCatalog>;

export const fetchErpHarnessCatalog: ErpCatalogLoader = async () => parseErpHarnessCatalog(await harnessApi.catalog());

export interface ErpReferenceCatalog extends ReferenceCatalogAdapter {
  /** The loaded catalog (fetched once; a failed load is retried on the next call). */
  readonly catalog: () => Promise<ErpHarnessCatalog>;
}

/** Reference books of the editor over the ERP catalog. A source with no records counts as not published,
 * so the pickers say «Справочник … ещё не опубликован» instead of showing an empty list. */
export function createErpReferenceCatalog(load: ErpCatalogLoader = fetchErpHarnessCatalog): ErpReferenceCatalog {
  let pending: Promise<{ catalog: ErpHarnessCatalog; adapter: ReferenceCatalogAdapter }> | null = null;
  const loaded = () => {
    pending ??= load().then((catalog) => ({
      catalog,
      adapter: snapshotReferenceCatalog(catalog.snapshots.filter((snapshot) => snapshot.records.length > 0)),
    }));
    pending.catch(() => { pending = null; });
    return pending;
  };
  return {
    catalog: async () => (await loaded()).catalog,
    async getActive(sourceId) {
      return (await loaded()).adapter.getActive(sourceId);
    },
    async searchCatalog(sourceId, request, signal) {
      return (await loaded()).adapter.searchCatalog(sourceId, request, signal);
    },
  };
}

/** The editor's catalog: reference books, the component library and the covering materials — all from the ERP. */
export function createErpHarnessEditorCatalog(load: ErpCatalogLoader = fetchErpHarnessCatalog): HarnessEditorCatalogApi {
  const references = createErpReferenceCatalog(load);
  return { templates: libraryTemplates(), references, materials: erpCoveringMaterials(references) };
}

// ── Covering materials: БД.МАТ ───────────────────────────────────────────────────────────────────────────────────

const COVERING_KINDS: readonly CoveringKind[] = ["heat-shrink", "nylon", "braid", "metal-braid", "tape", "band"];
/** How a covering of each kind is drawn: a hatch of the ERP set (hatching.ts), a line and a tint. Drawing data
 *  (stored in the document), not screen colours. */
const KIND_STYLE: Record<CoveringKind, { hatchCode: string; angle: number; scale: number; line: string; tint: string }> = {
  "heat-shrink": { hatchCode: "Ш1", angle: 45, scale: 1, line: "#3d4a5c", tint: "#3d4a5c" },
  nylon: { hatchCode: "Ш4", angle: 0, scale: 1, line: "#3d4a5c", tint: "#3d4a5c" },
  braid: { hatchCode: "Ш3", angle: 45, scale: 1, line: "#3d4a5c", tint: "#3d4a5c" },
  "metal-braid": { hatchCode: "Ш5", angle: 0, scale: 1, line: "#3d4a5c", tint: "#3d4a5c" },
  tape: { hatchCode: "Ш6", angle: 45, scale: 1, line: "#3d4a5c", tint: "#3d4a5c" },
  band: { hatchCode: "Ш7", angle: 0, scale: 1, line: "#3d4a5c", tint: "#3d4a5c" },
};
/** «Цвет» of БД.МАТ → the covering's fill. */
const COVER_COLORS: readonly (readonly [RegExp, string])[] = [
  [/чёрн|черн/i, "#2b2f33"], [/красн/i, "#b84a3c"], [/син/i, "#3d6aa8"], [/бел/i, "#eef1f2"], [/жёлт|желт/i, "#d8b23a"],
  [/зел/i, "#4f8a4b"], [/прозр/i, "#d9e6ec"], [/сер/i, "#8a949b"],
];

/** Covering materials of the editor from БД.МАТ (the protective coverings of the ERP catalog): one per record with a
 *  covering kind, drawn with the ERP hatching set. A new covering of a kind takes the first material of that kind by
 *  name. Textures are not stored in the ERP: materials are hatches only. */
export function erpCoveringMaterials(references: ErpReferenceCatalog): CoveringMaterialApi {
  return {
    async listMaterials() {
      const catalog = await references.catalog();
      const records = catalog.snapshots.find((snapshot) => snapshot.sourceId === erpCatalogSources.database)?.records ?? [];
      return records
        .filter((record) => record.entityType === "protective-covering" &&
          COVERING_KINDS.includes(record.payload.coveringKind as CoveringKind))
        .sort((a, b) => a.sourceKey.localeCompare(b.sourceKey, "ru"))
        .map((record): GlobalMaterial => {
          const kind = record.payload.coveringKind as CoveringKind;
          const style = KIND_STYLE[kind];
          const color = COVER_COLORS.find(([pattern]) => pattern.test(String(record.payload.color ?? "")))?.[1];
          return {
            materialId: record.recordId, name: record.sourceKey, lineColor: style.line, lineWidth: 1,
            textureAngle: style.angle, textureScale: style.scale, tint: style.tint, hatchCode: style.hatchCode,
            hatchLineWidth: 1, coveringKind: kind, updatedUtc: catalog.capturedUtc, revision: 1,
            ...(color ? { backgroundColor: color } : {}),
          };
        });
    },
    async pinTexture() {
      throw new Error("Текстуры материалов в ERP не хранятся — материалы БД.МАТ рисуются штриховкой.");
    },
    async listTextures() {
      return [];
    },
  };
}

// ── «нет в справочнике» ──────────────────────────────────────────────────────────────────────────────────────────

export const NOT_IN_REFERENCES = "нет в справочнике";

export type MissingReferenceKind = "connector" | "terminal" | "wire" | "cable" | "coax-termination" | "protective-covering";

export const missingReferenceKindLabels: Readonly<Record<MissingReferenceKind, string>> = {
  connector: "Разъём",
  terminal: "Терминал",
  wire: "Провод",
  cable: "Кабель",
  "coax-termination": "Разделка коаксиала",
  "protective-covering": "Защита",
};

export interface MissingReference {
  readonly kind: MissingReferenceKind;
  /** What the document names: an article, a wire mark, a record key. */
  readonly key: string;
  /** Where the document uses it, e.g. «X1, конт. 3». */
  readonly places: readonly string[];
  readonly status: typeof NOT_IN_REFERENCES;
}

/** Same normalisation as the ERP base of connectors (`connector_housings.normalize_article`). */
export function normalizeConnectorArticle(value: string): string {
  return value.trim().toUpperCase()
    .replace(/^(MOLEX|JST|HIROSE|JAE|TE(?:\s+CONNECTIVITY)?|CONNFLY|AMASS|AMP)\s+/, "")
    .replace(/\s+/g, "");
}

function regex(pattern: unknown): RegExp | null {
  if (typeof pattern !== "string" || !pattern) return null;
  try {
    return new RegExp(pattern, "i");
  } catch {
    return null;
  }
}

/** True when the ERP base of connectors draws this part number (a family pattern matches it, after the analogues). */
export function connectorKnown(article: string, records: readonly ReferenceCatalogRecord[]): boolean {
  let normalized = normalizeConnectorArticle(article);
  if (!normalized) return true;
  for (const record of records) {
    if (record.entityType !== "connector-alias") continue;
    const pattern = regex(record.payload.pattern);
    const replacement = record.payload.replacement;
    if (pattern && typeof replacement === "string" && pattern.test(normalized)) {
      normalized = normalizeConnectorArticle(normalized.replace(pattern, replacement));
    }
  }
  return records.some((record) => record.entityType === "connector-family" && regex(record.payload.pattern)?.test(normalized));
}

/** Everything the document references that our reference books do not hold, one entry per kind and key. */
export function findMissingReferences(
  document: HarnessDesignDocument,
  snapshots: readonly ReferenceCatalogSnapshot[],
): readonly MissingReference[] {
  const records = snapshots.flatMap((snapshot) => snapshot.records);
  const keys = new Map<string, Set<string>>();
  for (const record of records) {
    if (!keys.has(record.entityType)) keys.set(record.entityType, new Set());
    keys.get(record.entityType)!.add(record.sourceKey);
  }
  const has = (entityType: string, key: string) => keys.get(entityType)?.has(key) ?? false;
  const terminals = records.filter((record) => record.entityType === "terminal");
  const wireOptions = records
    .filter((record) => record.entityType === "wire" || record.entityType === "cable")
    .map((record) => wireDatabaseOption(record));
  const wireKnown = (mark: string, section: string | undefined) => {
    const markKey = normalizeWireChoice(mark);
    const sectionKey = normalizeWireSectionChoice(section ?? "");
    return wireOptions.some((option) => (normalizeWireChoice(option.mark) === markKey ||
      normalizeWireChoice(option.label) === markKey) &&
      (!sectionKey || normalizeWireSectionChoice(option.section) === sectionKey));
  };

  const found = new Map<string, { kind: MissingReferenceKind; key: string; places: string[] }>();
  const missing = (kind: MissingReferenceKind, key: string, place: string) => {
    const id = `${kind}\0${key}`;
    const entry = found.get(id) ?? { kind, key, places: [] };
    if (!entry.places.includes(place)) entry.places.push(place);
    found.set(id, entry);
  };
  const binding = (value: { readonly entityType: string; readonly sourceKey: string } | undefined, place: string) => {
    if (value && !has(value.entityType, value.sourceKey)) {
      missing(value.entityType as MissingReferenceKind, value.sourceKey, place);
    }
  };

  for (const connector of document.connectors) {
    const name = connector.designation || connector.partNumber || "разъём";
    if (connector.partNumber.trim() && !connectorKnown(connector.partNumber, records)) {
      missing("connector", connector.partNumber.trim(), name);
    }
    for (const contact of connector.contacts) {
      const place = `${name}, конт. ${contact.number}`;
      if (contact.terminalArticle.trim() && !resolveTerminalRecord(contact.terminalArticle, terminals)) {
        missing("terminal", contact.terminalArticle.trim(), place);
      }
      if (contact.materialBinding) binding(contact.materialBinding, place);
      else if (contact.wire.trim() && !wireKnown(contact.wire, contact.wireSection)) {
        missing("wire", [contact.wire.trim(), contact.wireSection?.trim()].filter(Boolean).join(" · "), place);
      }
    }
  }
  for (const wire of document.wires) {
    const place = `провод ${wire.circuit || wire.id}`;
    binding(wire.materialBinding, place);
    binding(wire.stripProfiles?.from, `${place}, начало`);
    binding(wire.stripProfiles?.to, `${place}, конец`);
  }
  for (const cable of document.cables) binding(cable.materialBinding, `кабель ${cable.id}`);
  for (const covering of document.physicalTopology?.coverings ?? []) {
    binding(covering.material, covering.name || "защита");
  }
  return [...found.values()].map((entry) => ({ ...entry, status: NOT_IN_REFERENCES }));
}
