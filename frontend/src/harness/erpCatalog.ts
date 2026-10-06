// Каталог редактора жгута из справочников ERP (H-T4): GET /api/techcards/harness-catalog → снимки в форме
// TECHMAP-GRAPHER, которые читают пикеры редактора (терминалы, провода, кабели, коаксиал, защита, разъёмы).
// Образцовый каталог TECHMAP-GRAPHER не используется: источник данных — только наши базы. Всё, на что ссылается
// документ и чего нет в справочниках, помечается «нет в справочнике» (данные для экрана; создание записи — позже).
// Модуль грузится вместе с редактором (index.ts → loadErpHarnessCatalog), в основной чанк не попадает.
import { api } from "../api";
import { createInMemoryComponentTemplateCatalog } from "./component-library/component-template-api";
import { createInMemoryCoveringMaterialApi } from "./editor/covering-assets";
import type { HarnessEditorCatalogApi } from "./editor/HarnessDesignEditor";
import type { HarnessDesignDocument } from "./editor/model";
import { resolveTerminalRecord } from "./editor/terminal-details";
import { normalizeWireChoice, normalizeWireSectionChoice, wireDatabaseOption } from "./editor/wire-database";
import {
  createInMemoryReferenceCatalog,
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

export const fetchErpHarnessCatalog: ErpCatalogLoader = async () =>
  parseErpHarnessCatalog(await api<unknown>(ERP_HARNESS_CATALOG_PATH));

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
      adapter: createInMemoryReferenceCatalog(catalog.snapshots.filter((snapshot) => snapshot.records.length > 0)),
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

/** `catalogOverride` of the editor: reference books from the ERP. The component library and covering textures
 * have no ERP storage yet, so they stay empty. */
export function createErpHarnessEditorCatalog(load: ErpCatalogLoader = fetchErpHarnessCatalog): HarnessEditorCatalogApi {
  return {
    templates: createInMemoryComponentTemplateCatalog(),
    references: createErpReferenceCatalog(load),
    materials: createInMemoryCoveringMaterialApi(),
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
