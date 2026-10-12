// Ported from TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission (THIRD_PARTY_NOTICES.md).
// Original: src/Techmap.Client/src/manufacturing-v3/route-v3-model.ts @ 0a456d2f7bd23288111e871058ebf55bf14426cd (branch codex/integration-m5-22).
import type { HarnessDesignDocument } from "../editor/model";
import type { EditorSceneObject } from "../editor/editor-types";
import { buildRouteSourceItems, type RouteSourceRef } from "../manufacturing/route-source";
import { createRouteDrawingCopy, parseRouteDrawingCopy, type RouteDrawingCopy } from "../manufacturing/route-drawing-copy";
import type { UmlOperation } from "../uml/uml-model";
import type { RouteV2Drawing } from "../manufacturing-v2/route-v2-model";

export const ROUTE_V3_NODE_DEFAULT_WIDTH = 280;
export const ROUTE_V3_NODE_DEFAULT_HEIGHT = 264;
export const ROUTE_V3_NODE_MIN_WIDTH = 220;
export const ROUTE_V3_NODE_MIN_HEIGHT = 170;
export const ROUTE_V3_NODE_MAX_WIDTH = 720;
export const ROUTE_V3_NODE_MAX_HEIGHT = 720;

export interface RouteV3Fragment {
  readonly id: string;
  readonly title: string;
  readonly mode: "source" | "isolated";
  readonly refs: readonly RouteSourceRef[];
  /** Source scene IDs, independent of edits to the isolated drawing. */
  readonly objectIds: readonly string[];
  readonly backgroundOpacity: number;
  readonly drawingCopy: RouteDrawingCopy;
  readonly isolatedDrawingCopy?: RouteDrawingCopy;
  readonly createdAt: string;
}
export interface RouteV3Node {
  readonly id: string;
  readonly kind: "semiFinished" | "assembly" | "final";
  readonly title: string;
  readonly fragmentIds: readonly string[];
  readonly refs: readonly RouteSourceRef[];
  readonly x: number;
  readonly y: number;
  readonly width?: number;
  readonly height?: number;
  readonly quantity?: number;
  readonly inputNodeIds?: readonly string[];
  readonly operations?: readonly UmlOperation[];
  readonly operatorConfirmed?: boolean;
  readonly rawRefs?: readonly RouteSourceRef[];
  readonly drawing?: RouteV2Drawing;
}
export interface RouteV3WireArtworkOverride {
  readonly wireId: string;
  readonly color?: string;
  readonly start?: import("../WireBlankCatalog").WireBlankEnd;
  readonly end?: import("../WireBlankCatalog").WireBlankEnd;
}
export interface RouteV3Document {
  readonly version: 1;
  readonly preparedRefs: readonly RouteSourceRef[];
  readonly fragments: readonly RouteV3Fragment[];
  readonly nodes: readonly RouteV3Node[];
  readonly edges: readonly { id: string; from: string; to: string; refs?: readonly RouteSourceRef[] }[];
  readonly generated: boolean;
  readonly backgroundOpacity: number;
  readonly graphEdited?: boolean;
  /** Sparse V3-only overrides. Missing entry always follows the current E4 model. */
  readonly wireArtwork?: readonly RouteV3WireArtworkOverride[];
}
export const refKey = (ref: RouteSourceRef): string => `${ref.kind}:${ref.id}`;
export const routeV3StorageKey = (projectId: string, harnessId: string): string => `techmap.route-v3.${projectId}.${harnessId}`;
export const fragmentDrawingCopy = (fragment: RouteV3Fragment): RouteDrawingCopy => fragment.isolatedDrawingCopy ?? fragment.drawingCopy;
const clampSize = (value: unknown, fallback: number, min: number, max: number): number => typeof value === "number" && Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallback;
export function routeV3NodeSize(node: Pick<RouteV3Node, "width" | "height">): { width: number; height: number } {
  return { width: clampSize(node.width, ROUTE_V3_NODE_DEFAULT_WIDTH, ROUTE_V3_NODE_MIN_WIDTH, ROUTE_V3_NODE_MAX_WIDTH), height: clampSize(node.height, ROUTE_V3_NODE_DEFAULT_HEIGHT, ROUTE_V3_NODE_MIN_HEIGHT, ROUTE_V3_NODE_MAX_HEIGHT) };
}
export function routeV3ResizeNodeSize(width: number, height: number): { width: number; height: number } {
  return { width: clampSize(width, ROUTE_V3_NODE_DEFAULT_WIDTH, ROUTE_V3_NODE_MIN_WIDTH, ROUTE_V3_NODE_MAX_WIDTH), height: clampSize(height, ROUTE_V3_NODE_DEFAULT_HEIGHT, ROUTE_V3_NODE_MIN_HEIGHT, ROUTE_V3_NODE_MAX_HEIGHT) };
}
export function routeV3ConnectionPort(node: Pick<RouteV3Node, "x" | "y" | "width" | "height">, side: "top" | "bottom"): { x: number; y: number } {
  const size = routeV3NodeSize(node); return { x: node.x + size.width / 2, y: node.y + (side === "top" ? 0 : size.height) };
}
export const routeV3NodeQuantity = (node: Pick<RouteV3Node, "quantity">): number => typeof node.quantity === "number" && Number.isFinite(node.quantity) && node.quantity > 0 ? node.quantity : 1;
export const routeV3NodeOperations = (node: Pick<RouteV3Node, "operations">): readonly UmlOperation[] => node.operations ?? [];

export function createInitialRouteV3(source: HarnessDesignDocument): RouteV3Document {
  return { version: 1, preparedRefs: buildRouteSourceItems(source).filter(item => item.ref.kind === "covering" || item.ref.kind === "cable" || item.ref.kind === "wire" && !item.cableId).map(item => item.ref), fragments: [], nodes: [], edges: [], generated: false, backgroundOpacity: .2 };
}
export function routeV3WireArtworkOverride(document: RouteV3Document, wireId: string): RouteV3WireArtworkOverride | undefined { return document.wireArtwork?.find(value => value.wireId === wireId); }
export function setRouteV3WireArtworkOverride(document: RouteV3Document, override: RouteV3WireArtworkOverride): RouteV3Document {
  const current = document.wireArtwork ?? [];
  return { ...document, wireArtwork: [...current.filter(value => value.wireId !== override.wireId), override] };
}
export function resetRouteV3WireArtwork(document: RouteV3Document, wireIds: readonly string[]): RouteV3Document {
  const ids = new Set(wireIds), current = document.wireArtwork?.filter(value => !ids.has(value.wireId)) ?? [];
  return { ...document, ...(current.length ? { wireArtwork: current } : { wireArtwork: undefined }) };
}
export function appendRouteV3Fragment(document: RouteV3Document, fragment: RouteV3Fragment): RouteV3Document {
  if (!fragment.objectIds.length) throw new Error("Выберите объекты для слепка.");
  const exists = document.fragments.some(item => item.id === fragment.id);
  return { ...document, fragments: exists ? document.fragments.map(item => item.id === fragment.id ? fragment : item) : [...document.fragments, fragment], generated: false, nodes: document.graphEdited ? document.nodes : [], edges: document.graphEdited ? document.edges : [] };
}
export function removeRouteV3Fragment(document: RouteV3Document, id: string): RouteV3Document {
  return { ...document, fragments: document.fragments.filter(item => item.id !== id), nodes: document.graphEdited ? document.nodes.map(n => ({ ...n, fragmentIds: n.fragmentIds.filter(f => f !== id) })) : [], edges: document.graphEdited ? document.edges : [], generated: false };
}
export function revealedRouteV3Ids(document: RouteV3Document): string[] {
  return [...new Set(document.fragments.flatMap(fragment => fragment.objectIds))];
}

/** Resolve only explicitly selected physical objects, never merely highlighted ones. */
export function captureRouteV3Selection(source: HarnessDesignDocument, scene: readonly EditorSceneObject[], selected: readonly string[]) {
  const ids = new Set(selected.filter(id => scene.some(object => object.id === id)));
  for (const object of scene) {
    if (!ids.has(object.id)) continue;
    if (object.metadata?.indexObjectId) ids.add(object.metadata.indexObjectId);
    if (object.kind === "physical-segment") for (const wireId of object.pipe?.wireIds ?? []) ids.add(wireId);
  }
  // Index labels are part of the same visual object and must follow its visibility.
  for (const object of scene) if (object.metadata?.indexObjectId && ids.has(object.metadata.indexObjectId)) ids.add(object.id);
  const refs = buildRouteSourceItems(source).filter(item => ids.has(item.ref.id) || item.ref.kind === "cable" && source.cables.find(cable => cable.id === item.ref.id)?.memberWireIds.some(wireId => ids.has(wireId))).map(item => item.ref);
  return { objectIds: [...ids], refs, drawingCopy: createRouteDrawingCopy(source, scene.filter(object => !ids.has(object.id)).map(object => object.id)) };
}

/** Every selected source object depends on its latest earlier saved state. */
export function generateRouteV3(document: RouteV3Document, source?: HarnessDesignDocument, _sceneIds: readonly string[] = []): RouteV3Document {
  const nodes: RouteV3Node[] = [];
  const edges: { id: string; from: string; to: string }[] = [];
  const owner = new Map<string, string>();
  const depths = new Map<string, number>();
  const rows = new Map<number, number>();
  const addNode = (node: Omit<RouteV3Node, "x" | "y">, parents: readonly string[]) => {
    const depth = parents.length ? 1 + Math.max(...parents.map(id => depths.get(id) ?? 0)) : 0;
    const column = rows.get(depth) ?? 0;
    nodes.push({ ...node, x: 32 + column * 304, y: 32 + depth * 328 });
    rows.set(depth, column + 1); depths.set(node.id, depth);
    for (const from of parents) edges.push({ id: `edge:${from}:${node.id}`, from, to: node.id });
  };
  for (const kind of ["wire", "cable", "covering"] as const) {
    const refs = document.preparedRefs.filter(ref => ref.kind === kind);
    if (!refs.length) continue;
    const id = `prepared:${kind}`;
    addNode({ id, kind: "semiFinished", title: kind === "wire" ? "Нарезанные провода" : kind === "covering" ? "Нарезанные оболочки" : "Нарезанные кабели", refs, fragmentIds: [] }, []);
    for (const ref of refs) owner.set(refKey(ref), id);
  }
  for (const fragment of document.fragments) {
    const parents = new Set(fragment.refs.map(ref => owner.get(refKey(ref))).filter((id): id is string => !!id));
    // A PF is indivisible. Partial reuse would falsely claim its complete assembly.
    const keys = new Set(fragment.refs.map(refKey));
    for (const parent of parents) {
      const prior = nodes.find(node => node.id === parent)!;
      if (prior.fragmentIds.length && prior.refs.some(ref => !keys.has(refKey(ref)))) throw new Error(`«${fragment.title}» использует часть полуфабриката «${prior.title}». Включите его полный состав в слепок.`);
    }
    const nodeId = `fragment:${fragment.id}`;
    addNode({ id: nodeId, kind: parents.size ? "assembly" : "semiFinished", title: fragment.title, refs: fragment.refs, fragmentIds: [fragment.id] }, [...parents]);
    for (const ref of fragment.refs) owner.set(refKey(ref), nodeId);
  }
  // No fictitious completed harness when some material is still unrepresented.
  const allRefs = source ? buildRouteSourceItems(source).map(item => item.ref) : [];
  const captured = new Set(document.fragments.flatMap(fragment => fragment.refs.map(refKey)));
  // Scene projections also contain derived labels, dimensions and topology
  // helpers. They are not production inputs, so they must not prevent the
  // final route once every addressable source ref is represented.
  if (allRefs.length && allRefs.every(ref => captured.has(refKey(ref)))) {
    const leaves = nodes.filter(node => node.fragmentIds.length && !edges.some(edge => edge.from === node.id)).map(node => node.id);
    addNode({ id: "route-v3:final", kind: "final", title: "Готовый жгут", refs: allRefs, fragmentIds: [] }, leaves);
  }
  return { ...document, nodes, edges, generated: true, ...(document.graphEdited !== undefined ? { graphEdited: false } : {}) };
}

/** Reject the whole draft instead of silently dropping damaged snapshots. */
export function parseRouteV3(value: unknown): RouteV3Document | null {
  try {
    const v = value as RouteV3Document;
    if (!v || v.version !== 1 || !Array.isArray(v.fragments) || v.fragments.length > 500 || !Array.isArray(v.preparedRefs) || !Array.isArray(v.nodes) || !Array.isArray(v.edges) || typeof v.generated !== "boolean") return null;
    if (v.graphEdited !== undefined && typeof v.graphEdited !== "boolean") return null;
    const opacity = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n) && n >= 0 && n <= 1;
    const text = (n: unknown): n is string => typeof n === "string" && n.trim().length > 0 && n.length <= 512;
    const refs = (items: readonly RouteSourceRef[]) => Array.isArray(items) && items.every(ref => ref && ["wire", "cable", "covering", "connector"].includes(ref.kind) && text(ref.id)) && new Set(items.map(refKey)).size === items.length;
    if (!opacity(v.backgroundOpacity) || !refs(v.preparedRefs)) return null;
    if (v.wireArtwork !== undefined && (!Array.isArray(v.wireArtwork) || v.wireArtwork.some(value => !value || !text(value.wireId) || (value.color !== undefined && !/^#[0-9a-f]{6}$/i.test(value.color)) || (value.start !== undefined && !["cut", "copper", "tin", "terminal", "sealed", "sealed-pin"].includes(value.start)) || (value.end !== undefined && !["cut", "copper", "tin", "terminal", "sealed", "sealed-pin"].includes(value.end))) || new Set(v.wireArtwork.map(value => value.wireId)).size !== v.wireArtwork.length)) return null;
    const ids = new Set<string>();
    const fragments: RouteV3Fragment[] = [];
    for (const f of v.fragments) {
      if (!f || !text(f.id) || ids.has(f.id) || !text(f.title) || !["source", "isolated"].includes(f.mode) || !refs(f.refs) || !Array.isArray(f.objectIds) || !f.objectIds.length || f.objectIds.some((id: unknown) => !text(id)) || !opacity(f.backgroundOpacity) || !text(f.createdAt)) return null;
      const drawingCopy = parseRouteDrawingCopy(f.drawingCopy);
      const isolatedDrawingCopy = f.isolatedDrawingCopy === undefined ? undefined : parseRouteDrawingCopy(f.isolatedDrawingCopy);
      if (f.mode === "isolated" && !isolatedDrawingCopy) return null;
      fragments.push({ ...f, objectIds: [...new Set(f.objectIds)], drawingCopy, ...(isolatedDrawingCopy ? { isolatedDrawingCopy } : {}) }); ids.add(f.id);
    }
    const nodeIds = new Set<string>();
    for (const node of v.nodes) {
      if (!node || !text(node.id) || nodeIds.has(node.id) || !text(node.title) || !["semiFinished", "assembly", "final"].includes(node.kind) || !refs(node.refs) || !Number.isFinite(node.x) || !Number.isFinite(node.y) || !Array.isArray(node.fragmentIds) || node.fragmentIds.some((id: unknown) => !ids.has(String(id)))) return null;
      nodeIds.add(node.id);
      if (node.rawRefs !== undefined && !refs(node.rawRefs)) return null;
      if (node.operatorConfirmed !== undefined && typeof node.operatorConfirmed !== "boolean") return null;
      if (node.drawing) { if (!opacity(node.drawing.backgroundOpacity)) return null; if (node.drawing.drawingCopy) parseRouteDrawingCopy(node.drawing.drawingCopy); if (node.drawing.isolatedDrawingCopy) parseRouteDrawingCopy(node.drawing.isolatedDrawingCopy); }
      if ([node.width, node.height, node.quantity].some(n => n !== undefined && (typeof n !== "number" || !Number.isFinite(n) || n <= 0))) return null;
      if (node.inputNodeIds !== undefined && (!Array.isArray(node.inputNodeIds) || node.inputNodeIds.some((id: unknown) => !text(id)))) return null;
      if (node.operations !== undefined && (!Array.isArray(node.operations) || node.operations.some((op: UmlOperation) => !op || !text(op.id) || typeof op.title !== "string" || !["running", "static"].includes(op.kind) || [op.lengthMm,op.speedMmPerMinute,op.employees,op.quantity,op.minutesEach].some(n => !Number.isFinite(n) || n < 0)))) return null;
    }
    const visited = new Set<string>(), visiting = new Set<string>(), edgeIds = new Set<string>();
    for (const edge of v.edges) { if (!edge || !text(edge.id) || edgeIds.has(edge.id) || !nodeIds.has(edge.from) || !nodeIds.has(edge.to) || edge.from === edge.to || edge.refs !== undefined && !refs(edge.refs)) return null; edgeIds.add(edge.id); }
    const visit = (id: string): boolean => { if (visiting.has(id)) return false; if (visited.has(id)) return true; visiting.add(id); if (v.edges.filter(e => e.from === id).some(e => !visit(e.to))) return false; visiting.delete(id); visited.add(id); return true; };
    if ([...nodeIds].some(id => !visit(id))) return null;
    return { ...v, fragments };
  } catch { return null; }
}
