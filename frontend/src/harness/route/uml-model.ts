// Ported from TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission (THIRD_PARTY_NOTICES.md).
// Original: src/Techmap.Client/src/uml/uml-model.ts @ 0a456d2f7bd23288111e871058ebf55bf14426cd (branch codex/integration-m5-22).
// ERP changes: Route V2 is not ported, so createUmlFromRouteV2 takes any route graph of that shape (Route v3 fits).
/** ERP: the part of a Route V2 / v3 document the UML import reads. */
export interface UmlRouteSource {
  readonly nodes: readonly { readonly id: string; readonly title: string; readonly x: number; readonly y: number }[];
  readonly edges: readonly { readonly id: string; readonly from: string; readonly to: string }[];
}
type RouteV2Document = UmlRouteSource;

export type UmlOperationKind = "running" | "static";
export interface UmlOperation {
  readonly id: string;
  readonly title: string;
  readonly kind: UmlOperationKind;
  readonly lengthMm: number;
  readonly speedMmPerMinute: number;
  readonly employees: number;
  readonly quantity: number;
  readonly minutesEach: number;
}
export interface UmlNode {
  readonly id: string;
  readonly title: string;
  readonly sourceNodeId: string | null;
  readonly x: number;
  readonly y: number;
  readonly operations: readonly UmlOperation[];
}
export interface UmlEdge { readonly id: string; readonly from: string; readonly to: string; }
export interface UmlDocument { readonly version: 1; readonly nodes: readonly UmlNode[]; readonly edges: readonly UmlEdge[]; }

export function umlStorageKey(projectId: string, harnessId: string): string { return `techmap.uml.${projectId}.${harnessId}`; }
const id = (prefix: string) => `${prefix}-${typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : Math.random().toString(36).slice(2)}`;

export function operationMinutes(operation: UmlOperation): number {
  if (operation.kind === "running") return operation.lengthMm > 0 && operation.speedMmPerMinute > 0 && operation.employees > 0
    ? operation.lengthMm / operation.speedMmPerMinute / operation.employees : 0;
  return Math.max(0, operation.quantity) * Math.max(0, operation.minutesEach);
}

export function nodeMinutes(node: UmlNode): number { return node.operations.reduce((sum, operation) => sum + operationMinutes(operation), 0); }
export function documentMinutes(document: UmlDocument): number { return document.nodes.reduce((sum, node) => sum + nodeMinutes(node), 0); }

export function createUmlFromRouteV2(route: RouteV2Document): UmlDocument {
  return { version: 1, nodes: route.nodes.map(node => ({ id: `uml-${node.id}`, title: node.title, sourceNodeId: node.id, x: node.x, y: node.y, operations: [] })), edges: route.edges.map(edge => ({ id: `uml-${edge.id}`, from: `uml-${edge.from}`, to: `uml-${edge.to}` })) };
}

export function parseUml(value: unknown): UmlDocument | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<UmlDocument>;
  if (candidate.version !== 1 || !Array.isArray(candidate.nodes) || !Array.isArray(candidate.edges)) return null;
  const nodes = candidate.nodes.filter(node => node && typeof node === "object" && typeof node.id === "string" && typeof node.title === "string").map(node => ({
    id: node.id, title: node.title, sourceNodeId: typeof node.sourceNodeId === "string" ? node.sourceNodeId : null, x: Number.isFinite(node.x) ? Number(node.x) : 40, y: Number.isFinite(node.y) ? Number(node.y) : 40,
    operations: Array.isArray(node.operations) ? (node.operations as unknown[]).filter((operation): operation is Record<string, unknown> => Boolean(operation) && typeof operation === "object" && typeof (operation as Record<string, unknown>).id === "string").map(operation => ({ id: String(operation.id), title: typeof operation.title === "string" ? operation.title : "Операция", kind: operation.kind === "static" ? "static" : "running", lengthMm: Number.isFinite(operation.lengthMm as number) ? Number(operation.lengthMm) : 0, speedMmPerMinute: Number.isFinite(operation.speedMmPerMinute as number) && Number(operation.speedMmPerMinute) > 0 ? Number(operation.speedMmPerMinute) : 1000, employees: Number.isFinite(operation.employees as number) && Number(operation.employees) > 0 ? Number(operation.employees) : 1, quantity: Number.isFinite(operation.quantity as number) && Number(operation.quantity) > 0 ? Number(operation.quantity) : 1, minutesEach: Number.isFinite(operation.minutesEach as number) ? Number(operation.minutesEach) : 1 })) : [],
  })) as UmlNode[];
  const ids = new Set(nodes.map(node => node.id));
  const edges = candidate.edges.filter(edge => edge && typeof edge === "object" && typeof edge.id === "string" && typeof edge.from === "string" && typeof edge.to === "string" && ids.has(edge.from) && ids.has(edge.to) && edge.from !== edge.to).map(edge => ({ id: edge.id, from: edge.from, to: edge.to })) as UmlEdge[];
  return { version: 1, nodes, edges };
}

export { id as umlId };
