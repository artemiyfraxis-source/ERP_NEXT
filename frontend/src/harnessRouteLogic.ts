/* «Маршрут КБn» — правки маршрута v3 позиции с чертежом поверх перенесённой модели Василия (harness/route): вставка
   карточки на связь, вид, количество, операции из библиотеки ERP, раскладка рядами и подписи карточек. Модель, граф и
   время — его (route-v3-model, route-v3-graph, uml-model); здесь только то, чего у него нет или что ERP делает иначе. */

import { wireColor } from "./connectorArt";
import { formatMinutes } from "./etkRoute";
import type { WireBlankEnd } from "./harness/WireBlankCatalog";
import type { RouteSourceItem } from "./harness/route/route-source";
import { connectRouteV3, deleteRouteV3Edge, deleteRouteV3Node, editableRouteV3, refreshRouteV3 } from "./harness/route/route-v3-graph";
import { parseRouteV3, refKey, routeV3NodeQuantity, routeV3NodeSize, routeV3WireArtworkOverride, type RouteV3Document, type RouteV3Node } from "./harness/route/route-v3-model";
import { operationMinutes, type UmlOperation } from "./harness/route/uml-model";
import type { LibraryCard } from "./manualLogic";
import { cardLabel } from "./manualLogic";
import { fullHex, layoutByDepth, type CardKind, type TubeThumb, type WireThumb } from "./routeCards";
import { pluralRu } from "./tcGenLogic";

const newId = (prefix: string) => `${prefix}-${typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : Math.random().toString(36).slice(2)}`;

/** Маршрут с сервера — в правимый вид Василия (входы и своё сырьё у каждой карточки, состав пересчитан); повреждённый —
    null (страница скажет, что маршрут не прочитан, и предложит построить заново). */
export function editableRoute(raw: unknown): RouteV3Document | null {
  const parsed = parseRouteV3(raw);
  if (!parsed) return null;
  try { return refreshRouteV3(editableRouteV3(parsed)); } catch { return null; }
}

export const nodeMinutes = (node: RouteV3Node): number => (node.operations ?? []).reduce((sum, op) => sum + operationMinutes(op), 0);

/** «12 комплектующих · 1 шт · 4,5 мин» — как у Василия, по-русски с числом. */
export function nodeMeta(node: RouteV3Node): string {
  const parts = node.refs.length;
  return `${parts} ${pluralRu(parts, "комплектующая", "комплектующие", "комплектующих")} · ${formatMinutes(routeV3NodeQuantity(node))} шт · ${formatMinutes(nodeMinutes(node))} мин`;
}

/** «+» на связи «вход → цель»: новая пустая карточка «Сборка N» встаёт между ними (вход идёт в неё, она — в цель),
    цель и всё после неё сдвигаются вниз, чтобы карточка поместилась. Связь от заготовок резки переносит только свои
    провода, как было, — состав цели не меняется. */
export function insertOnLink(doc: RouteV3Document, edgeId: string, id = newId("card")): RouteV3Document {
  const edge = doc.edges.find((item) => item.id === edgeId);
  const from = edge && doc.nodes.find((node) => node.id === edge.from);
  const to = edge && doc.nodes.find((node) => node.id === edge.to);
  if (!edge || !from || !to) return doc;
  const node: RouteV3Node = {
    id, kind: "assembly", title: `Сборка ${doc.nodes.filter((item) => item.kind === "assembly").length + 1}`,
    refs: [], rawRefs: [], fragmentIds: [], inputNodeIds: [from.id],
    x: from.x, y: from.y + routeV3NodeSize(from).height + 64,
  };
  const after = new Set<string>([to.id]);
  const pending = [to.id];
  while (pending.length) {
    const next = pending.pop()!;
    for (const out of doc.edges.filter((item) => item.from === next)) if (!after.has(out.to)) { after.add(out.to); pending.push(out.to); }
  }
  const top = Math.min(...doc.nodes.filter((item) => after.has(item.id)).map((item) => item.y));
  const shift = Math.max(0, node.y + routeV3NodeSize(node).height + 64 - top);
  const edges = [
    ...doc.edges.filter((item) => item.id !== edgeId),
    { id: `edge:${from.id}:${id}`, from: from.id, to: id, ...(edge.refs ? { refs: edge.refs } : {}) },
    { id: `edge:${id}:${to.id}`, from: id, to: to.id },
  ];
  return refreshRouteV3({
    ...doc, edges,
    nodes: [...doc.nodes.map((item) => ({
      ...item, operatorConfirmed: false, y: item.y + (after.has(item.id) ? shift : 0),
      inputNodeIds: item.id === to.id ? (item.inputNodeIds ?? []).map((input) => (input === from.id ? id : input)) : item.inputNodeIds,
    })), node],
  });
}

/** «+ Добавить карточку»: пустая «Сборка N» под всеми карточками, без связей. */
export function addCard(doc: RouteV3Document, id = newId("card")): RouteV3Document {
  const bottom = Math.max(0, ...doc.nodes.map((node) => node.y + routeV3NodeSize(node).height));
  const node: RouteV3Node = { id, kind: "assembly", title: `Сборка ${doc.nodes.filter((item) => item.kind === "assembly").length + 1}`,
    refs: [], rawRefs: [], fragmentIds: [], inputNodeIds: [], x: 32, y: doc.nodes.length ? bottom + 64 : 32 };
  return { ...doc, nodes: [...doc.nodes, node] };
}

export const removeLink = (doc: RouteV3Document, edgeId: string) => deleteRouteV3Edge(doc, edgeId);
export const removeCard = (doc: RouteV3Document, id: string) => deleteRouteV3Node(doc, id);
/** Вход из карточки `from` в `to`; цикл — ошибка с русской причиной (connectRouteV3). */
export const connectCards = (doc: RouteV3Document, from: string, to: string) => {
  if (doc.nodes.find((node) => node.id === from)?.kind === "final") throw new Error("Из финала связь не выходит — готовое изделие никуда не идёт.");
  return connectRouteV3(doc, from, to, `edge:${from}:${to}`);
};
export const moveCard = (doc: RouteV3Document, id: string, x: number, y: number): RouteV3Document =>
  ({ ...doc, nodes: doc.nodes.map((node) => (node.id === id ? { ...node, x, y } : node)) });

/** «Расставить»: раскладка Василия (ряд — глубина), каждая карточка своего размера. */
export function autoLayout(doc: RouteV3Document): RouteV3Document {
  const byId = new Map(doc.nodes.map((node) => [node.id, node]));
  const places = layoutByDepth(doc.nodes.map((node) => node.id), doc.edges, (id) => routeV3NodeSize(byId.get(id)!));
  return { ...doc, nodes: doc.nodes.map((node) => ({ ...node, ...places.get(node.id)! })) };
}

/** Правка карточки (название, вид, количество, операции): состав пересчитывается, отметки «проверено» снимаются — как
    у Василия. Финал один и из него ничего не выходит — иначе ошибка с причиной, правка не применяется. */
export function updateCard(doc: RouteV3Document, id: string, patch: Partial<Pick<RouteV3Node, "title" | "kind" | "quantity" | "operations">>): RouteV3Document {
  if (patch.kind === "final") {
    const other = doc.nodes.find((node) => node.kind === "final" && node.id !== id);
    if (other) throw new Error(`Финал уже есть — «${other.title}». Сначала смените его вид.`);
    if (doc.edges.some((edge) => edge.from === id)) throw new Error("Из финала связь не выходит — уберите связи этой карточки дальше.");
  }
  return refreshRouteV3({ ...doc, nodes: doc.nodes.map((node) => ({ ...node, ...(node.id === id ? patch : {}), operatorConfirmed: false })) });
}

/** Операция из библиотеки ERP: штучная, на одну штуку — норма «на штуку» карточки (БД.ОП), код карточки — для подачи
    в техкарту. Нормы нет — 1 мин, технолог поправит. */
export function libraryOperation(card: LibraryCard): UmlOperation {
  const seconds = Number(String(card.norms?.operation_seconds ?? "").replace(",", "."));
  return {
    id: newId("op"), title: cardLabel(card), kind: "static", lengthMm: 0, speedMmPerMinute: 1000, employees: 1, quantity: 1,
    minutesEach: Number.isFinite(seconds) && seconds > 0 ? Math.round((seconds / 60) * 1000) / 1000 : 1,
    ...(card.code ? { code: card.code } : {}), templateId: card.id,
  };
}

export function ownOperation(): UmlOperation {
  return { id: newId("op"), title: "Своя операция", kind: "static", lengthMm: 0, speedMmPerMinute: 1000, employees: 1, quantity: 1, minutesEach: 1 };
}

/* ── Рисунки заготовок резки ───────────────────────────────────────────────────────────────────────────────────── */

const ENDS: readonly WireBlankEnd[] = ["cut", "copper", "tin", "terminal", "sealed", "sealed-pin"];
const colorOf = (value: string | null) => (value && /^#[0-9a-f]{3}([0-9a-f]{3})?$/i.test(value) ? fullHex(value) : wireColor(value ?? ""));

/** Нарезанные провода карточки: «XS1:1 → XS3:1», цвет жилы и концы — вид проводов маршрута (Э4 или ручной). */
export function wireThumbs(doc: RouteV3Document, node: RouteV3Node, items: readonly RouteSourceItem[]): WireThumb[] {
  const byKey = new Map(items.map((item) => [refKey(item.ref), item]));
  return node.refs.filter((ref) => ref.kind === "wire").flatMap((ref): WireThumb[] => {
    const item = byKey.get(refKey(ref));
    if (!item) return [];
    const manual = routeV3WireArtworkOverride(doc, ref.id);
    const end = (terminal: string, chosen: WireBlankEnd | undefined): WireBlankEnd =>
      chosen && ENDS.includes(chosen) ? chosen : terminal ? "terminal" : item.stripProfiles ? "copper" : "cut";
    // «XS1:1 → XS3:1 · цепь» — на рисунке без цепи, она в подсказке.
    const route = item.title.split(" · ")[0];
    return [{
      key: ref.id, label: route, title: [item.title, item.material, item.lengthMm ? `${formatMinutes(item.lengthMm)} мм` : ""].filter(Boolean).join(" · "),
      color: manual?.color ?? colorOf(item.color), left: end(item.terminalFrom, manual?.start), right: end(item.terminalTo, manual?.end),
    }];
  });
}

/** Нарезанные оболочки и кабели: название и длина резки. */
export function sleeveThumbs(node: RouteV3Node, items: readonly RouteSourceItem[]): TubeThumb[] {
  const byKey = new Map(items.map((item) => [refKey(item.ref), item]));
  return node.refs.filter((ref) => ref.kind === "covering" || ref.kind === "cable").flatMap((ref) => {
    const item = byKey.get(refKey(ref));
    return item ? [{ key: ref.id, label: `${item.title}${item.lengthMm ? ` · ${formatMinutes(item.lengthMm)} мм` : ""}`, count: 0 }] : [];
  });
}

/** Карточка заготовок резки (её рисуют провода и оболочки, а не слепок чертежа). */
export const isPrepared = (node: RouteV3Node) => node.id.startsWith("prepared:");

export const KIND_OPTIONS: CardKind[] = ["semiFinished", "assembly", "final"];
