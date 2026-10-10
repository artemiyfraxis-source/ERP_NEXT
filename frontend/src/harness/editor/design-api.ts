import { normalizeE4RoutingDocument } from "./commands";
import { createEmptyHarnessDesign, parseHarnessDesignDocument, type HarnessDesignDocument } from "./model";

export interface HarnessDesignResource {
  readonly harnessId: string;
  readonly schemaVersion: 1;
  readonly revision: number;
  readonly content: HarnessDesignDocument;
  readonly updatedUtc: string;
  readonly sourceFingerprint?: string;
  readonly harnessQuantity?: number;
  /** Present when only derived E4 route geometry had to be rebuilt to open the document safely. */
  readonly recoveryWarning?: string;
}

/** Loads and saves the drawing of a harness (ERP: `drawing.ts` over `harnessApi`). */
export interface HarnessDesignApi {
  get(projectId: string, harnessId: string): Promise<HarnessDesignResource>;
  save(projectId: string, harnessId: string, expectedRevision: number, content: HarnessDesignDocument): Promise<HarnessDesignResource>;
}

export class HarnessDesignApiError extends Error {
  readonly code: string | null;
  readonly currentRevision: number | null;

  constructor(message: string, code: string | null, currentRevision: number | null) {
    super(message);
    this.name = "HarnessDesignApiError";
    this.code = code;
    this.currentRevision = currentRevision;
  }
}

export interface RecoverableHarnessDesignContent {
  readonly content: HarnessDesignDocument;
  readonly warning: string | null;
}

/**
 * Parses persisted domain data strictly, but treats an invalid E4 polyline as
 * recoverable derived state. Connector/contact/wire data is never replaced by
 * an empty document merely because an old route no longer satisfies current
 * routing rules.
 */
export function parseRecoverableHarnessDesignContent(value: unknown): RecoverableHarnessDesignContent {
  let parsed: HarnessDesignDocument;
  try {
    parsed = parseHarnessDesignDocument(value);
  } catch (initialError) {
    const source = requireRecord(value);
    if (!Array.isArray(source.wires)) throw initialError;
    let recoveredSource: Record<string, unknown> & { wires: Record<string, unknown>[] } = {
      ...source,
      wires: source.wires.map((candidate) => {
        const wire = requireRecord(candidate);
        const { e4Route: _invalidRoute, ...withoutRoute } = wire;
        return { ...withoutRoute, e4RouteMode: "auto" };
      }),
    };
    try {
      parsed = parseHarnessDesignDocument(recoveredSource);
    } catch {
      // Removing E4 routes did not repair the document, so this is a domain or
      // structural error and must remain visible instead of being concealed.
      throw initialError;
    }
    // Put back each persisted route independently. One malformed polyline must
    // not discard unrelated manual routing work from the same harness.
    for (const [index, candidate] of source.wires.entries()) {
      const originalWire = requireRecord(candidate);
      if (originalWire.e4Route === undefined) continue;
      const candidateWires = recoveredSource.wires.map((item, candidateIndex) => candidateIndex === index
        ? {
            ...item,
            e4Route: originalWire.e4Route,
            e4RouteMode: typeof originalWire.e4RouteMode === "string" ? originalWire.e4RouteMode : "auto",
          }
        : item);
      const candidateSource = { ...recoveredSource, wires: candidateWires };
      try {
        parsed = parseHarnessDesignDocument(candidateSource);
        recoveredSource = candidateSource;
      } catch {
        // This route alone violates the current route contract. Keep only this
        // wire on the safe automatically reconstructed route.
      }
    }
    const detail = initialError instanceof Error && initialError.message
      ? ` Причина: ${initialError.message}`
      : "";
    // Do not run a global reroute here: it could rewrite valid manual routes
    // while repairing an unrelated wire. The next explicit edit may reroute
    // affected automatic routes under the current collision rules.
    return {
      content: parsed,
      warning: `Схема открыта в безопасном режиме: некорректные маршруты Э4 восстановлены автоматически.${detail}`,
    };
  }
  try {
    return { content: normalizeE4RoutingDocument(parsed), warning: null };
  } catch (error) {
    const detail = error instanceof Error && error.message ? ` Причина: ${error.message}` : "";
    return {
      content: parsed,
      warning: `Схема открыта в безопасном режиме: исходная геометрия сохранена, автоматическая перетрассировка недоступна.${detail}`,
    };
  }
}

function requireRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Документ жгута повреждён.");
  }
  return value as Record<string, unknown>;
}

function requireString(value: unknown, name: string): string {
  if (typeof value !== "string" || !value) throw new Error(`Поле «${name}» снимка задано неверно.`);
  return value;
}

/**
 * Parses a stored snapshot v1 (`{harnessId, schemaVersion: 1, revision, content, updatedUtc}`).
 * Domain data goes through the same strict parser as a server response would.
 */
export function parseHarnessDesignSnapshot(value: unknown): HarnessDesignResource {
  const record = requireRecord(value);
  const harnessId = requireString(record.harnessId, "harnessId");
  if (record.schemaVersion !== 1 || !Number.isSafeInteger(record.revision) || (record.revision as number) < 0) {
    throw new Error("Снимок документа жгута повреждён: неверная версия схемы или ревизия.");
  }
  const parsedContent = parseRecoverableHarnessDesignContent(record.content);
  return {
    harnessId,
    schemaVersion: 1,
    revision: record.revision as number,
    content: parsedContent.content,
    updatedUtc: requireString(record.updatedUtc, "updatedUtc"),
    ...(typeof record.sourceFingerprint === "string" && /^[a-f0-9]{64}$/i.test(record.sourceFingerprint) ? { sourceFingerprint: record.sourceFingerprint } : {}),
    ...(Number.isSafeInteger(record.harnessQuantity) && (record.harnessQuantity as number) > 0 ? { harnessQuantity: record.harnessQuantity as number } : {}),
    ...(parsedContent.warning ? { recoveryWarning: parsedContent.warning } : {}),
  };
}
