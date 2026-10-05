import type { ReferenceCatalogSearchRecord } from "../reference-catalog-api";
import type { HarnessDesignDocument, ConnectorContact, WireEndpoint, WireInstance } from "./model";
import { terminalArticleLabel } from "./terminal-article-label";

export type TerminalDetails = NonNullable<ConnectorContact["terminalDetails"]>;

/** Signed values that can be copied from the terminal catalog into a wire end. */
export interface TerminalLengthCorrectionOptions {
  readonly terminalArticle: string;
  readonly plusMm: number | null;
  readonly minusMm: number | null;
}

export interface WireEndTerminalCorrections {
  readonly from?: TerminalLengthCorrectionOptions;
  readonly to?: TerminalLengthCorrectionOptions;
}

function text(payload: Readonly<Record<string, unknown>>, ...keys: string[]): string {
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

export function terminalDetailsFromRecord(record: ReferenceCatalogSearchRecord): TerminalDetails {
  const plusMm = numericCatalogValue(record.payload, "lengthPlusMm");
  const minusMagnitudeMm = numericCatalogValue(record.payload, "lengthMinusMm");
  return {
    manufacturer: text(record.payload, "manufacturer", "Производитель", "изготовитель"),
    series: text(record.payload, "series", "Серия", "Марка"),
    description: text(record.payload, "description", "Текстовое описание", "Описание", "productName", "Product Name", "Наименование", "name", "Название"),
    article: text(record.payload, "reelArticle", "article", "Артикул", "bagArticle") || terminalArticleLabel(record.sourceKey),
    ...(plusMm === null ? {} : { lengthPlusMm: plusMm }),
    ...(minusMagnitudeMm === null ? {} : { lengthMinusMm: -minusMagnitudeMm }),
  };
}

function articleAliases(record: ReferenceCatalogSearchRecord): string[] {
  return [record.sourceKey, terminalArticleLabel(record.sourceKey),
    text(record.payload, "reelArticle", "article", "Артикул", "bagArticle")]
    .filter(Boolean).map(value => value.trim().toLocaleLowerCase("ru-RU"));
}

/** Resolve exact catalog keys first; a bare article is usable only if unique. */
export function resolveTerminalRecord(
  article: string,
  records: readonly ReferenceCatalogSearchRecord[],
): ReferenceCatalogSearchRecord | undefined {
  const terminals = records.filter(record => record.entityType === "terminal");
  const exact = terminals.find(record => record.sourceKey === article);
  if (exact) return exact;
  const normalized = article.trim().toLocaleLowerCase("ru-RU");
  if (!normalized) return undefined;
  const matches = terminals.filter(record => articleAliases(record).includes(normalized));
  const identities = new Map(matches.map(record => {
    const manufacturer = text(record.payload, "manufacturer", "Производитель").toLocaleLowerCase("ru-RU");
    const series = text(record.payload, "series", "Серия").toLocaleLowerCase("ru-RU");
    const identity = manufacturer && series ? `${manufacturer}\0${series}\0${normalized}` : record.sourceKey;
    return [identity, record] as const;
  }));
  return identities.size === 1 ? identities.values().next().value : undefined;
}

/** Resolve the presentation snapshot retained with a connector contact. */
export function resolveTerminalDetails(
  article: string,
  records: readonly ReferenceCatalogSearchRecord[],
): TerminalDetails | undefined {
  const record = resolveTerminalRecord(article, records);
  return record ? terminalDetailsFromRecord(record) : undefined;
}

function numericCatalogValue(payload: Readonly<Record<string, unknown>>, key: string): number | null {
  const value = payload[key];
  let parsed = Number.NaN;
  if (typeof value === "number") parsed = value;
  if (typeof value === "string") {
    const normalized = value.trim();
    if (normalized) parsed = Number(normalized.replace(",", "."));
  }
  return Number.isFinite(parsed) ? Math.abs(parsed) : null;
}

/** L+ always adds material; L- always subtracts it, even if catalog cells omit a sign. */
export function terminalLengthCorrectionOptions(
  article: string,
  records: readonly ReferenceCatalogSearchRecord[],
): TerminalLengthCorrectionOptions | undefined {
  const record = resolveTerminalRecord(article, records);
  if (!record) return undefined;
  const plusMm = numericCatalogValue(record.payload, "lengthPlusMm");
  const minusMagnitudeMm = numericCatalogValue(record.payload, "lengthMinusMm");
  return {
    terminalArticle: terminalArticleLabel(article),
    plusMm,
    minusMm: minusMagnitudeMm === null ? null : -minusMagnitudeMm,
  };
}

function terminalCorrectionAtEndpoint(
  document: HarnessDesignDocument,
  endpoint: WireEndpoint,
  records: readonly ReferenceCatalogSearchRecord[],
): TerminalLengthCorrectionOptions | undefined {
  if ("junctionId" in endpoint || "screenId" in endpoint) return undefined;
  const contact = document.connectors.find(connector => connector.id === endpoint.connectorId)
    ?.contacts.find(candidate => candidate.id === endpoint.contactId);
  if (!contact?.terminalArticle) return undefined;
  const saved = contact.terminalDetails;
  if (saved?.lengthPlusMm !== undefined || saved?.lengthMinusMm !== undefined) {
    return {
      terminalArticle: saved.article || terminalArticleLabel(contact.terminalArticle),
      plusMm: saved.lengthPlusMm ?? null,
      minusMm: saved.lengthMinusMm === undefined || saved.lengthMinusMm === null ? null : -Math.abs(saved.lengthMinusMm),
    };
  }
  return terminalLengthCorrectionOptions(contact.terminalArticle, records);
}

/** Looks up the terminals electrically connected to both ends of one wire. */
export function wireEndTerminalCorrections(
  document: HarnessDesignDocument,
  wire: Pick<WireInstance, "from" | "to">,
  records: readonly ReferenceCatalogSearchRecord[],
): WireEndTerminalCorrections {
  const from = terminalCorrectionAtEndpoint(document, wire.from, records);
  const to = terminalCorrectionAtEndpoint(document, wire.to, records);
  return { ...(from ? { from } : {}), ...(to ? { to } : {}) };
}

/** Fill metadata for legacy contacts only when an exact catalog key is available. */
export function hydrateTerminalDetails(
  document: HarnessDesignDocument,
  records: readonly ReferenceCatalogSearchRecord[],
): HarnessDesignDocument {
  let changed = false;
  const connectors = document.connectors.map(connector => {
    let connectorChanged = false;
    const contacts = connector.contacts.map(contact => {
      if (contact.terminalDetails || !contact.terminalArticle) return contact;
      const details = resolveTerminalDetails(contact.terminalArticle, records);
      if (!details || ![details.manufacturer, details.series, details.description, details.article ?? ""].some(value => value.trim())) return contact;
      changed = connectorChanged = true;
      return { ...contact, terminalDetails: details };
    });
    return connectorChanged ? { ...connector, contacts } : connector;
  });
  return changed ? { ...document, connectors } : document;
}
