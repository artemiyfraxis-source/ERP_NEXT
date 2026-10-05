import { createMutationHeaders, type LocalSession } from "../local-session";
import { buildApiUrl, type RuntimeConfig } from "../runtime-config";
import { normalizeE4RoutingDocument } from "./commands";
import { parseHarnessDesignDocument, type HarnessDesignDocument } from "./model";

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

type DesignFetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export function createHarnessDesignApi(
  config: RuntimeConfig,
  session: LocalSession,
  fetcher: DesignFetcher = fetch,
): HarnessDesignApi {
  const headers = createMutationHeaders(session);
  const resource = (projectId: string, harnessId: string) =>
    `projects/${encodeURIComponent(projectId)}/harnesses/${encodeURIComponent(harnessId)}/design`;

  async function request(
    path: string,
    init: RequestInit,
  ): Promise<HarnessDesignResource> {
    let response: Response;
    try {
      response = await fetcher(buildApiUrl(config, path), {
        credentials: "same-origin",
        cache: "no-store",
        ...init,
      });
    } catch {
      throw new HarnessDesignApiError("Не удалось связаться с локальным сервером.", null, null);
    }
    if (!response.ok) throw await parseError(response);
    let value: unknown;
    try {
      value = await response.json();
    } catch {
      throw new HarnessDesignApiError("Сервер вернул нечитаемый документ жгута.", null, null);
    }
    return parseResource(value);
  }

  return {
    get: (projectId, harnessId) => request(resource(projectId, harnessId), {
      method: "GET",
      headers: { Accept: "application/json" },
    }),
    save: async (projectId, harnessId, expectedRevision, content) => {
      const validatedContent = parseRecoverableHarnessDesignContent(content).content;
      return request(resource(projectId, harnessId), {
        method: "PUT",
        headers,
        body: JSON.stringify({ expectedRevision, schemaVersion: 1, writerContractVersion: 2, content: validatedContent }),
      });
    },
  };
}

function parseResource(value: unknown): HarnessDesignResource {
  const record = requireRecord(value);
  const harnessId = requireString(record.harnessId, "harnessId");
  if (!/^[0-9a-f-]{36}$/i.test(harnessId) || record.schemaVersion !== 1 ||
      !Number.isSafeInteger(record.revision) || (record.revision as number) < 0) {
    throw new Error("Сервер вернул повреждённые метаданные документа жгута.");
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

async function parseError(response: Response): Promise<HarnessDesignApiError> {
  try {
    const value = requireRecord(await response.json());
    const code = typeof value.error === "string" ? value.error : null;
    const revision = Number.isSafeInteger(value.currentRevision) && (value.currentRevision as number) >= 0
      ? value.currentRevision as number : null;
    const message = code === "design_revision_conflict"
      ? "Документ изменился после открытия. Перезагрузите жгут перед повторным сохранением."
      : code === "harness_not_found" || code === "project_not_found"
        ? "Проект или жгут не найден."
        : typeof value.message === "string" && value.message.trim()
          ? value.message
          : `Сервер не выполнил запрос (HTTP ${response.status}).`;
    return new HarnessDesignApiError(message, code, revision);
  } catch {
    return new HarnessDesignApiError(`Сервер не выполнил запрос (HTTP ${response.status}).`, null, null);
  }
}

function requireRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Сервер вернул повреждённый документ жгута.");
  }
  return value as Record<string, unknown>;
}

function requireString(value: unknown, name: string): string {
  if (typeof value !== "string" || !value) throw new Error(`Поле ответа «${name}» задано неверно.`);
  return value;
}
