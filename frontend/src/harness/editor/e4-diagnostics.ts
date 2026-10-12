import type { ConnectorInstance, HarnessDesignDocument } from "./model";

export type E4DiagnosticCode = "duplicate-connector-designation";

export interface E4DiagnosticTarget {
  readonly view: "e4";
  readonly kind: "connector";
  readonly objectId: string;
}

export interface DuplicateConnectorDesignationDiagnostic {
  readonly id: string;
  readonly code: "duplicate-connector-designation";
  readonly severity: "error";
  readonly message: string;
  readonly designation: string;
  readonly target: E4DiagnosticTarget;
  readonly conflictingObjectIds: readonly string[];
}

export type E4Diagnostic = DuplicateConnectorDesignationDiagnostic;

/**
 * Returns a stable comparison key for an electrical designation. Designations
 * are identifiers, so casing and whitespace around an entered value do not
 * make two otherwise equal values distinct.
 */
export function normalizeConnectorDesignation(designation: string): string {
  return designation.trim().normalize("NFC").toLocaleUpperCase("ru-RU");
}

/**
 * Reports one diagnostic for every connector that belongs to a duplicate
 * designation group. The function only reads the connector collection and
 * does not reject or rewrite the entered values.
 */
export function diagnoseDuplicateConnectorDesignations(
  connectors: readonly ConnectorInstance[],
): readonly DuplicateConnectorDesignationDiagnostic[] {
  const groups = new Map<string, ConnectorInstance[]>();
  for (const connector of connectors) {
    const key = normalizeConnectorDesignation(connector.designation);
    if (!key) continue;
    const group = groups.get(key);
    if (group) group.push(connector);
    else groups.set(key, [connector]);
  }

  return connectors.flatMap((connector) => {
    const key = normalizeConnectorDesignation(connector.designation);
    const group = groups.get(key);
    if (!key || !group || group.length < 2) return [];
    const designation = connector.designation.trim();
    return [{
      id: `duplicate-connector-designation:${connector.id}`,
      code: "duplicate-connector-designation" as const,
      severity: "error" as const,
      message: `Обозначение «${designation}» используется несколькими соединителями.`,
      designation,
      target: { view: "e4" as const, kind: "connector" as const, objectId: connector.id },
      conflictingObjectIds: group.filter((item) => item.id !== connector.id).map((item) => item.id),
    }];
  });
}

export function collectE4Diagnostics(
  document: Pick<HarnessDesignDocument, "connectors">,
): readonly E4Diagnostic[] {
  return diagnoseDuplicateConnectorDesignations(document.connectors);
}
