// Ported from TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission (THIRD_PARTY_NOTICES.md).
// Original: src/Techmap.Client/src/manufacturing-v3/route-v3-graph.ts @ 0a456d2f7bd23288111e871058ebf55bf14426cd (branch codex/integration-m5-22).
import type { RouteSourceRef } from "../manufacturing/route-source";
import { refKey, routeV3NodeSize, type RouteV3Document, type RouteV3Node } from "./route-v3-model";

const unique = (refs: readonly RouteSourceRef[]) => [...new Map(refs.map(ref => [refKey(ref), ref])).values()];

/** Prepared blanks can supply a subset. Assembly edges transfer a whole PF,
 * including later changes to its composition. */
export function editableRouteV3(doc: RouteV3Document): RouteV3Document {
  const edges = doc.edges.map(edge => {
    const to = doc.nodes.find(n => n.id === edge.to)!;
    if (to.rawRefs !== undefined || edge.refs !== undefined) return edge;
    const from = doc.nodes.find(n => n.id === edge.from)!;
    if (!from.id.startsWith("prepared:")) return edge;
    const keys = new Set(to.refs.map(refKey));
    return { ...edge, refs: from.refs.filter(ref => keys.has(refKey(ref))) };
  });
  return { ...doc, edges, nodes: doc.nodes.map(node => {
    if (node.rawRefs !== undefined) return node;
    const incoming = edges.filter(e => e.to === node.id);
    const inherited = new Set(incoming.flatMap(e => e.refs ?? doc.nodes.find(n => n.id === e.from)!.refs).map(refKey));
    return { ...node, rawRefs: node.refs.filter(ref => !inherited.has(refKey(ref))), inputNodeIds: incoming.map(e => e.from) };
  }) };
}

export function routeV3Composition(doc: RouteV3Document): Map<string, readonly RouteSourceRef[]> {
  const result = new Map<string, readonly RouteSourceRef[]>(), visiting = new Set<string>();
  const byId = new Map(doc.nodes.map(n => [n.id, n]));
  const visit = (id: string): readonly RouteSourceRef[] => {
    if (result.has(id)) return result.get(id)!;
    if (visiting.has(id)) throw new Error("Связь создаёт цикл зависимостей.");
    visiting.add(id);
    const node = byId.get(id)!;
    const inputs = doc.edges.filter(e => e.to === id && (!node.inputNodeIds || node.inputNodeIds.includes(e.from)));
    const refs = unique([...(node.rawRefs ?? node.refs), ...inputs.flatMap(edge => {
      const allowed = edge.refs && new Set(edge.refs.map(refKey));
      return visit(edge.from).filter(ref => !allowed || allowed.has(refKey(ref)));
    })]);
    visiting.delete(id); result.set(id, refs); return refs;
  };
  for (const node of doc.nodes) visit(node.id);
  return result;
}
export function refreshRouteV3(doc: RouteV3Document): RouteV3Document {
  const refs = routeV3Composition(doc);
  return { ...doc, nodes: doc.nodes.map(node => ({ ...node, refs: refs.get(node.id)! })) };
}
export function connectRouteV3(doc: RouteV3Document, from: string, to: string, id: string): RouteV3Document {
  if (from === to) throw new Error("Нельзя связать карточку с собой.");
  if (!doc.nodes.some(n => n.id === from) || !doc.nodes.some(n => n.id === to)) throw new Error("Карточка отсутствует.");
  if (doc.edges.some(e => e.from === from && e.to === to)) return doc;
  // Check every edge, including deselected material inputs.
  const pending = [to], seen = new Set<string>();
  while (pending.length) { const next = pending.pop()!; if (next === from) throw new Error("Связь создаёт цикл зависимостей."); if (seen.has(next)) continue; seen.add(next); pending.push(...doc.edges.filter(e => e.from === next).map(e => e.to)); }
  return refreshRouteV3({ ...doc, edges: [...doc.edges, { id, from, to }], nodes: doc.nodes.map(n => n.id === to ? { ...n, operatorConfirmed: false, inputNodeIds: [...new Set([...(n.inputNodeIds ?? []), from])] } : { ...n, operatorConfirmed: false }) });
}
export function deleteRouteV3Edge(doc: RouteV3Document, id: string): RouteV3Document {
  const edge = doc.edges.find(e => e.id === id);
  return refreshRouteV3({ ...doc, edges: doc.edges.filter(e => e.id !== id), nodes: doc.nodes.map(n => ({ ...n, operatorConfirmed: false, inputNodeIds: edge && n.id === edge.to ? n.inputNodeIds?.filter(i => i !== edge.from) : n.inputNodeIds })) });
}
export function deleteRouteV3Node(doc: RouteV3Document, id: string): RouteV3Document {
  return refreshRouteV3({ ...doc, nodes: doc.nodes.filter(n => n.id !== id).map(n => ({ ...n, operatorConfirmed: false, inputNodeIds: n.inputNodeIds?.filter(i => i !== id) })), edges: doc.edges.filter(e => e.from !== id && e.to !== id) });
}
export function insertRouteV3Node(doc: RouteV3Document, parentId: string | null, id: string): RouteV3Document {
  const parent = doc.nodes.find(n => n.id === parentId);
  const outgoing = doc.edges.filter(e => e.from === parentId);
  const node: RouteV3Node = { id, title: `Сборка ${doc.nodes.filter(n => n.kind === "assembly").length + 1}`, kind: "assembly", refs: [], rawRefs: [], fragmentIds: [], inputNodeIds: parent ? [parent.id] : [], x: parent?.x ?? 32, y: parent ? parent.y + routeV3NodeSize(parent).height + 64 : Math.max(0, ...doc.nodes.map(n => n.y + routeV3NodeSize(n).height)) + 64 };
  const descendants = new Set<string>(); const pending = outgoing.map(e => e.to);
  while (pending.length) { const next = pending.pop()!; if (descendants.has(next)) continue; descendants.add(next); pending.push(...doc.edges.filter(e => e.from === next).map(e => e.to)); }
  const minY = Math.min(...doc.nodes.filter(n => descendants.has(n.id)).map(n => n.y));
  const shift = Math.max(0, node.y + routeV3NodeSize(node).height + 64 - minY);
  return refreshRouteV3({ ...doc, nodes: [...doc.nodes.map(n => ({ ...n, operatorConfirmed: false, y: n.y + (descendants.has(n.id) ? shift : 0), inputNodeIds: outgoing.some(e => e.to === n.id) ? n.inputNodeIds?.map(i => i === parentId ? id : i) : n.inputNodeIds })), node], edges: [...doc.edges.map(e => e.from === parentId ? { ...e, from: id } : e), ...(parent ? [{ id: `edge:${id}`, from: parent.id, to: id }] : [])] });
}
