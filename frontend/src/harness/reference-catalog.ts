// Справочники для редактора жгута: только типы и чтение. Сетевой клиент TECHMAP-GRAPHER
// (reference-catalog-api.ts: импорт XLSX/Google, публикация) сюда не перенесён — данные
// приходят через адаптер, сейчас in-memory.

export type ReferenceDiagnosticSeverity = "warning" | "error";

export interface ReferenceCatalogDiagnostic {
  readonly diagnosticId: string;
  readonly severity: ReferenceDiagnosticSeverity;
  readonly code: string;
  readonly message: string;
  readonly entityType: string | null;
  readonly sourceKey: string | null;
  readonly field: string | null;
  readonly sourceLocation: string | null;
}

export interface ReferenceCatalogRecord {
  readonly recordId: string;
  readonly entityType: string;
  readonly sourceKey: string;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly sourceLocation: string | null;
}

export type ReferenceCatalogSearchRecord = ReferenceCatalogRecord;

export interface ReferenceCatalogSnapshot {
  readonly snapshotId: string;
  readonly sourceId: string;
  readonly contractVersion: number;
  readonly capturedUtc: string;
  readonly sourceKind: string;
  readonly versionFingerprint: string;
  readonly sourceUri: string | null;
  readonly sha256: string;
  readonly records: readonly ReferenceCatalogRecord[];
  readonly diagnostics: readonly ReferenceCatalogDiagnostic[];
}

export interface ReferenceCatalogSearchFilter {
  readonly field: string;
  readonly operator: "eq" | "prefix" | "exists" | "missing" | "null" | "blank";
  readonly value?: string | null;
}

export interface ReferenceCatalogSearchRequest {
  readonly text: string | null;
  readonly exactSourceKey: string | null;
  readonly entityTypes: readonly string[];
  readonly filters: readonly ReferenceCatalogSearchFilter[];
  readonly filterLogic: "all" | "any";
  readonly sort: "relevance" | "source-key-asc" | "source-key-desc" | "entity-type-asc";
  readonly pageSize: number;
  readonly cursor: string | null;
}

export interface ReferenceCatalogSearchPage {
  readonly snapshotId: string;
  readonly snapshotSha256: string;
  readonly items: readonly ReferenceCatalogSearchRecord[];
  readonly nextCursor: string | null;
}

/** Read-only access to published reference snapshots (wires, terminals, coverings, coax). */
export interface ReferenceCatalogAdapter {
  getActive(sourceId: string): Promise<ReferenceCatalogSnapshot | null>;
  searchCatalog(sourceId: string, request: ReferenceCatalogSearchRequest, signal?: AbortSignal): Promise<ReferenceCatalogSearchPage>;
}

export class ReferenceCatalogApiError extends Error {
  readonly code: string | null;

  constructor(message: string, code: string | null = null) {
    super(message);
    this.name = "ReferenceCatalogApiError";
    this.code = code;
  }
}

function abortError(): Error {
  const error = new Error("Запрос отменён.");
  error.name = "AbortError";
  return error;
}

function searchableText(record: ReferenceCatalogRecord): string {
  const values = Object.values(record.payload).filter((value) => typeof value === "string" || typeof value === "number");
  return [record.sourceKey, ...values.map(String)].join(" ").toLocaleLowerCase("ru");
}

function matchesFilter(record: ReferenceCatalogRecord, filter: ReferenceCatalogSearchFilter): boolean {
  const value = record.payload[filter.field];
  switch (filter.operator) {
    case "eq": return String(value ?? "") === String(filter.value ?? "");
    case "prefix": return String(value ?? "").startsWith(String(filter.value ?? ""));
    case "exists": return value !== undefined;
    case "missing": return value === undefined;
    case "null": return value === null;
    case "blank": return value === undefined || value === null || String(value).trim() === "";
  }
}

/** The reference books of the editor over published snapshots (ERP: `harnessApi.catalog`): one active snapshot per
 *  source, text search, offset cursor. */
export function snapshotReferenceCatalog(
  snapshots: readonly ReferenceCatalogSnapshot[] = [],
): ReferenceCatalogAdapter {
  const active = new Map(snapshots.map((snapshot) => [snapshot.sourceId, snapshot]));
  return {
    async getActive(sourceId) {
      return active.get(sourceId) ?? null;
    },
    async searchCatalog(sourceId, request, signal) {
      if (signal?.aborted) throw abortError();
      const snapshot = active.get(sourceId);
      if (!snapshot) {
        throw new ReferenceCatalogApiError("Справочник ещё не опубликован.", "catalog_active_snapshot_not_found");
      }
      const offset = request.cursor === null ? 0 : Number(request.cursor);
      if (!Number.isSafeInteger(offset) || offset < 0) {
        throw new ReferenceCatalogApiError("Страница поиска устарела. Запустите поиск повторно.", "catalog_cursor_invalid");
      }
      const text = request.text?.trim().toLocaleLowerCase("ru") ?? "";
      const types = new Set(request.entityTypes);
      const filtered = snapshot.records.filter((record) => {
        if (types.size > 0 && !types.has(record.entityType)) return false;
        if (request.exactSourceKey !== null && record.sourceKey !== request.exactSourceKey) return false;
        if (text && !searchableText(record).includes(text)) return false;
        if (request.filters.length === 0) return true;
        return request.filterLogic === "all"
          ? request.filters.every((filter) => matchesFilter(record, filter))
          : request.filters.some((filter) => matchesFilter(record, filter));
      });
      const sorted = [...filtered];
      if (request.sort === "source-key-asc" || request.sort === "relevance") sorted.sort((a, b) => a.sourceKey.localeCompare(b.sourceKey, "ru"));
      else if (request.sort === "source-key-desc") sorted.sort((a, b) => b.sourceKey.localeCompare(a.sourceKey, "ru"));
      else sorted.sort((a, b) => a.entityType.localeCompare(b.entityType) || a.sourceKey.localeCompare(b.sourceKey, "ru"));
      const pageSize = Math.max(1, Math.min(200, Math.trunc(request.pageSize) || 30));
      const items = sorted.slice(offset, offset + pageSize);
      return {
        snapshotId: snapshot.snapshotId,
        snapshotSha256: snapshot.sha256,
        items,
        nextCursor: offset + pageSize < sorted.length ? String(offset + pageSize) : null,
      };
    },
  };
}
