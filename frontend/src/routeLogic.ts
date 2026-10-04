/* Схема полуфабрикатов (ARCH-TRACK-1): блок = один полуфабрикат из одной и более операций, стрелка = «сколько штук
   полуфабриката берёт следующий блок». Модель и проверки повторяют backend `techcards/domain/route.py`; сервер всё
   равно проверяет схему при сборке (422 «Схема полуфабрикатов: …»). Операции — по номерам (1 = первая в ЭТК). */

import type { EtkRouteBlock, EtkRouteEdge } from "./api";
import { etkRouteOf } from "./etkRoute";

/** Правимая часть `document.route` — то, что уходит в `build_config.route` (итоги считаются заново). */
export type RouteBlock = Pick<EtkRouteBlock, "key" | "name" | "ops" | "final" | "components">;
export type RouteEdge = EtkRouteEdge;
export type Route = { blocks: RouteBlock[]; edges: RouteEdge[] };

/** Операция, которая попадёт в сборку: position — номер в ЭТК, key — устойчивый ключ формы, minutes — норма на партию,
    number — номер в списке операций формы (отличается от position, когда у операции выше нет шаблона). */
export type RouteOp = { position: number; key: string; type: string; code: string; label: string; minutes: number | null; number?: number };

export type BlockTotals = {
  minutes: number | null;   // Σ норм операций блока на партию
  unit: number | null;      // на одну штуку полуфабриката блока
  rolled: number | null;    // на штуку, со всеми входами: unit + Σ per_unit × rolled входа
  inputs: number;           // входящих полуфабрикатов на один: Σ per_unit входящих стрелок
  opCount: number;          // операций в блоке и во всех блоках до него
};

const TUT_CUT = "cutTut";
const TUT_TAKERS = new Set(["insTut", "endCap"]);
const SOURCE_TYPES = new Set(["cutWire", "cutWireStrip", "cutWireCrimp", "coaxCut", "coaxCutTut"]);

/** «0,3333» — штук на стрелке: запятая, до четырёх знаков, без хвостовых нулей (минуты — `formatMinutes` из etkRoute). */
export function formatPerUnit(value: number): string {
  return String(Math.round(value * 10000) / 10000).replace(".", ",");
}

export function parsePositive(text: string): number | null {
  const number = Number(text.trim().replace(",", "."));
  return text.trim() !== "" && Number.isFinite(number) && number > 0 ? number : null;
}

function ordered(route: Route): Route {
  const first = (block: RouteBlock) => (block.ops.length ? Math.min(...block.ops) : 0);
  const blocks = route.blocks
    .map((block) => ({ ...block, ops: [...block.ops].sort((a, b) => a - b) }))
    .sort((a, b) => first(a) - first(b) || a.key.localeCompare(b.key));
  const order = new Map(blocks.map((block, index) => [block.key, index]));
  const edges = [...route.edges].sort((a, b) => (order.get(a.from) ?? -1) - (order.get(b.from) ?? -1)
    || (order.get(a.to) ?? -1) - (order.get(b.to) ?? -1));
  return { blocks, edges };
}

/** Схема без правок: по блоку на операцию, цепочка по тем же правилам, что у ЭТК (резка начинает ветку, кусочки ТУТ
    ждут «Надеть ТУТ» / заделку, остальные операции забирают все открытые ветки). */
export function deriveRoute(ops: RouteOp[]): Route {
  if (!ops.length) return { blocks: [], edges: [] };
  const lastPosition = ops[ops.length - 1].position;
  const blocks: RouteBlock[] = [];
  const edges: RouteEdge[] = [];
  let heads: string[] = [];
  let tut: string[] = [];
  for (const op of ops) {
    const key = `b${op.position}`;
    blocks.push({ key, name: "", ops: [op.position], final: op.position === lastPosition, components: [] });
    if (op.type === TUT_CUT) { tut.push(key); continue; }
    if (SOURCE_TYPES.has(op.type) || op.code.toUpperCase().startsWith("CUT_")) { heads.push(key); continue; }
    const inputs = heads.concat(TUT_TAKERS.has(op.type) ? tut : []);
    if (TUT_TAKERS.has(op.type)) tut = [];
    inputs.forEach((from) => edges.push({ from, to: key, per_unit: 1 }));
    heads = [key];
  }
  const finalKey = `b${lastPosition}`;
  heads.concat(tut).filter((key) => key !== finalKey).forEach((from) => edges.push({ from, to: finalKey, per_unit: 1 }));
  return ordered({ blocks, edges });
}

/** Схема из `build_config.route` (то, что ушло на сервер): разбор общий с полосой «Маршрут» в ЭТК; не схема — null. */
export function parseRoute(raw: unknown): Route | null {
  const parsed = etkRouteOf({ route: raw });
  if (!parsed) return null;
  return ordered({
    blocks: parsed.blocks.map(({ key, name, ops, final, components }) => ({ key, name: name.trim(), ops, final, components })),
    edges: parsed.edges,
  });
}

/** Тело `build_config.route`: имя только если его правили руками (пустое сервер берёт из результата последней операции). */
export function routePayload(route: Route): Record<string, unknown> {
  const sorted = ordered(route);
  return {
    blocks: sorted.blocks.map((block) => ({
      key: block.key,
      ...(block.name.trim() ? { name: block.name.trim() } : {}),
      ops: block.ops, final: block.final,
      ...(block.components.length ? { components: block.components } : {}),
    })),
    edges: sorted.edges.map((edge) => ({ from: edge.from, to: edge.to, per_unit: edge.per_unit })),
  };
}

/** Название блока на экране: своё, у итогового — наименование изделия, иначе — название последней операции. */
export function blockTitle(block: RouteBlock, ops: RouteOp[], finalName = ""): string {
  if (block.name.trim()) return block.name.trim();
  if (block.final && finalName.trim()) return finalName.trim();
  const last = block.ops.length ? Math.max(...block.ops) : 0;
  return ops.find((op) => op.position === last)?.label || `Блок ${block.key.replace(/^b/, "")}`;
}

/** Порядок Кана: блоки, у которых все входы уже стоят. Короче числа блоков — в схеме есть цикл. */
function kahn(route: Route): string[] {
  const incoming = new Map(route.blocks.map((block) => [block.key, 0]));
  route.edges.forEach((edge) => incoming.set(edge.to, (incoming.get(edge.to) ?? 0) + 1));
  const ready = route.blocks.filter((block) => !incoming.get(block.key)).map((block) => block.key);
  const order: string[] = [];
  while (ready.length) {
    const key = ready.shift()!;
    order.push(key);
    route.edges.filter((edge) => edge.from === key).forEach((edge) => {
      incoming.set(edge.to, (incoming.get(edge.to) ?? 0) - 1);
      if (!incoming.get(edge.to)) ready.push(edge.to);
    });
  }
  return order;
}

function topoOrder(route: Route): string[] | null {
  const order = kahn(route);
  return order.length === route.blocks.length ? order : null;
}

/** Что не так со схемой, словами для технолога; null — схема годится. Те же правила, что у сервера. */
export function routeProblem(route: Route, ops: RouteOp[], finalName = ""): string | null {
  if (!route.blocks.length) return "нет ни одного блока";
  const byKey = new Map<string, RouteBlock>();
  const title = (block: RouteBlock) => `«${blockTitle(block, ops, finalName)}»`;
  for (const block of route.blocks) {
    if (byKey.has(block.key)) return `ключ блока «${block.key}» повторяется`;
    byKey.set(block.key, block);
    if (!block.ops.length) return `в блоке ${title(block)} нет операций`;
  }
  const known = new Set(ops.map((op) => op.position));
  const owner = new Map<number, RouteBlock>();
  for (const block of route.blocks) {
    for (const position of block.ops) {
      if (!known.has(position)) return `операции ${position} нет в техкарте (операций ${known.size})`;
      const other = owner.get(position);
      if (other) return `операция ${position} входит в несколько блоков — ${title(other)} и ${title(block)}`;
      owner.set(position, block);
    }
  }
  const missing = ops.map((op) => op.position).filter((position) => !owner.has(position));
  if (missing.length) return `${missing.length === 1 ? "операция" : "операции"} ${missing.join(", ")} не ${missing.length === 1 ? "входит" : "входят"} ни в один блок`;
  const finals = route.blocks.filter((block) => block.final);
  if (!finals.length) return "не отмечен итоговый блок (изделие)";
  if (finals.length > 1) return `итоговый блок должен быть один, отмечено: ${finals.map(title).join(", ")}`;
  const pairs = new Set<string>();
  for (const edge of route.edges) {
    const source = byKey.get(edge.from), target = byKey.get(edge.to);
    if (!source) return `стрелка ведёт из неизвестного блока «${edge.from}»`;
    if (!target) return `стрелка ведёт в неизвестный блок «${edge.to}»`;
    if (source === target) return `стрелка из блока ${title(source)} в него же`;
    if (pairs.has(`${edge.from}>${edge.to}`)) return `стрелка ${title(source)} → ${title(target)} повторяется`;
    pairs.add(`${edge.from}>${edge.to}`);
    if (!(edge.per_unit > 0)) return `количество на стрелке ${title(source)} → ${title(target)} должно быть больше нуля`;
    if (source.final) return `из итогового блока ${title(source)} не может выходить стрелка`;
  }
  const order = kahn(route);
  if (order.length < route.blocks.length) {
    // Каждый оставшийся блок имеет оставшийся вход: идём по входам назад, пока не вернёмся в уже пройденный блок.
    const stuck = new Set(route.blocks.map((block) => block.key).filter((key) => !order.includes(key)));
    const path = [[...stuck][0]];
    while (path.indexOf(path[path.length - 1]) === path.length - 1) {
      path.push(route.edges.find((edge) => edge.to === path[path.length - 1] && stuck.has(edge.from))!.from);
    }
    const cycle = path.slice(path.indexOf(path[path.length - 1])).reverse();
    return "схема зациклена — " + cycle.map((key) => title(byKey.get(key)!)).join(" → ");
  }
  const reached = new Set([finals[0].key]);
  const queue = [finals[0].key];
  while (queue.length) {
    const key = queue.shift()!;
    route.edges.filter((edge) => edge.to === key && !reached.has(edge.from)).forEach((edge) => { reached.add(edge.from); queue.push(edge.from); });
  }
  const lost = route.blocks.filter((block) => !reached.has(block.key));
  if (lost.length) return `${lost.length === 1 ? "блок" : "блоки"} ${lost.map(title).join(", ")} не ${lost.length === 1 ? "доходит" : "доходят"} до итогового блока`;
  return null;
}

/** Время и входы каждого блока: минуты операций берутся на партию, на штуку — делением на партию. */
export function routeTotals(route: Route, ops: RouteOp[], quantity: number): Map<string, BlockTotals> {
  const batch = quantity > 0 ? quantity : 1;
  const timed = ops.some((op) => op.minutes != null);
  const minutes = new Map(ops.map((op) => [op.position, op.minutes ?? 0]));
  const byKey = new Map(route.blocks.map((block) => [block.key, block]));
  const totals = new Map<string, BlockTotals>();
  const upstream = new Map<string, Set<number>>();
  for (const key of topoOrder(route) ?? []) {
    const block = byKey.get(key)!;
    const incoming = route.edges.filter((edge) => edge.to === key && totals.has(edge.from));
    const own = block.ops.reduce((sum, position) => sum + (minutes.get(position) ?? 0), 0);
    const unit = own / batch;
    upstream.set(key, new Set([...block.ops, ...incoming.flatMap((edge) => [...(upstream.get(edge.from) ?? [])])]));
    totals.set(key, {
      minutes: timed ? own : null,
      unit: timed ? unit : null,
      rolled: timed ? unit + incoming.reduce((sum, edge) => sum + edge.per_unit * (totals.get(edge.from)!.rolled ?? 0), 0) : null,
      inputs: incoming.reduce((sum, edge) => sum + edge.per_unit, 0),
      opCount: upstream.get(key)!.size,
    });
  }
  return totals;
}

/** Линейная схема — одна цепочка блоков (у каждого не больше одного входа и выхода): её рисуем строкой. */
export function routeChain(route: Route): string[] | null {
  if (route.blocks.length < 2 || route.edges.length !== route.blocks.length - 1) return null;
  const ins = new Map<string, number>(), outs = new Map<string, number>();
  for (const edge of route.edges) {
    ins.set(edge.to, (ins.get(edge.to) ?? 0) + 1);
    outs.set(edge.from, (outs.get(edge.from) ?? 0) + 1);
  }
  if ([...ins.values(), ...outs.values()].some((count) => count > 1)) return null;
  const order = topoOrder(route);
  return order && order.length === route.blocks.length ? order : null;
}

export type RouteLayout = { cells: Map<string, { layer: number; row: number }>; layers: number; rows: number };

/** Слои слева направо: слой блока — самый длинный путь до него от блоков без входов. Строки в слое — по среднему
    положению входов, чтобы стрелки не лезли друг на друга. */
export function routeLayout(route: Route): RouteLayout {
  const order = topoOrder(route) ?? route.blocks.map((block) => block.key);
  const layer = new Map<string, number>();
  order.forEach((key) => layer.set(key, route.edges.filter((edge) => edge.to === key && layer.has(edge.from))
    .reduce((deep, edge) => Math.max(deep, layer.get(edge.from)! + 1), 0)));
  const first = new Map(route.blocks.map((block) => [block.key, block.ops.length ? Math.min(...block.ops) : 0]));
  const layers = Math.max(0, ...layer.values()) + 1;
  const cells = new Map<string, { layer: number; row: number }>();
  let rows = 1;
  for (let index = 0; index < layers; index += 1) {
    const keys = route.blocks.map((block) => block.key).filter((key) => layer.get(key) === index);
    const center = (key: string) => {
      const from = route.edges.filter((edge) => edge.to === key && cells.has(edge.from)).map((edge) => cells.get(edge.from)!.row);
      return from.length ? from.reduce((sum, row) => sum + row, 0) / from.length : Number.POSITIVE_INFINITY;
    };
    keys.sort((a, b) => {
      const gap = center(a) === center(b) ? 0 : center(a) < center(b) ? -1 : 1;
      return gap || (first.get(a) ?? 0) - (first.get(b) ?? 0);
    });
    keys.forEach((key, row) => cells.set(key, { layer: index, row }));
    rows = Math.max(rows, keys.length);
  }
  return { cells, layers, rows };
}

/** Блоки, с которыми блок соединён стрелкой: до него и после него. */
export function neighbours(route: Route, key: string) {
  const byKey = new Map(route.blocks.map((block) => [block.key, block]));
  return {
    before: route.edges.filter((edge) => edge.to === key).map((edge) => byKey.get(edge.from)).filter((block): block is RouteBlock => !!block),
    after: route.edges.filter((edge) => edge.from === key).map((edge) => byKey.get(edge.to)).filter((block): block is RouteBlock => !!block),
  };
}

/** Блоки, от которых можно вести новый вход в `key`: не итоговые, ещё не соединённые и не идущие после него. */
export function inputCandidates(route: Route, key: string): RouteBlock[] {
  const downstream = new Set<string>([key]);
  const queue = [key];
  while (queue.length) {
    const next = queue.shift()!;
    route.edges.filter((edge) => edge.from === next && !downstream.has(edge.to)).forEach((edge) => { downstream.add(edge.to); queue.push(edge.to); });
  }
  return route.blocks.filter((block) => !block.final && !downstream.has(block.key) && !route.edges.some((edge) => edge.from === block.key && edge.to === key));
}

function sumEdges(edges: RouteEdge[]): RouteEdge[] {
  const result: RouteEdge[] = [];
  for (const edge of edges) {
    const same = result.find((item) => item.from === edge.from && item.to === edge.to);
    if (same) same.per_unit += edge.per_unit; else result.push({ ...edge });
  }
  return result;
}

/** Склеить соседние блоки: операции вместе, остаётся ключ, название и «итоговость» нижнего (того, что берёт полуфабрикат);
    стрелки обоих переходят к склеенному, общие складываются. */
export function mergeBlocks(route: Route, upstream: string, downstream: string): Route {
  const up = route.blocks.find((block) => block.key === upstream);
  const down = route.blocks.find((block) => block.key === downstream);
  if (!up || !down) return route;
  const merged: RouteBlock = { key: down.key, name: down.name, ops: [...up.ops, ...down.ops].sort((a, b) => a - b), final: down.final, components: [...up.components, ...down.components] };
  const edges = route.edges
    .filter((edge) => !(edge.from === upstream && edge.to === downstream))
    .map((edge) => ({ ...edge, from: edge.from === upstream ? downstream : edge.from, to: edge.to === upstream ? downstream : edge.to }));
  return ordered({ blocks: route.blocks.filter((block) => block.key !== upstream && block.key !== down.key).concat(merged), edges: sumEdges(edges) });
}

function freeKey(route: Route): string {
  const used = route.blocks.map((block) => Number(/^b(\d+)$/.exec(block.key)?.[1] ?? 0));
  return `b${Math.max(0, ...used) + 1}`;
}

/** Разделить блок после операции `after`: новый блок — операции до неё (получает входы блока), старый — остальные
    (сохраняет ключ, название, выходы); между ними стрелка «1 шт». */
export function splitBlock(route: Route, key: string, after: number): Route {
  const block = route.blocks.find((item) => item.key === key);
  if (!block) return route;
  const head = block.ops.filter((position) => position <= after);
  const tail = block.ops.filter((position) => position > after);
  if (!head.length || !tail.length) return route;
  const created: RouteBlock = { key: freeKey(route), name: "", ops: head, final: false, components: block.components };
  const blocks = route.blocks.map((item) => (item.key === key ? { ...item, ops: tail, components: [] } : item)).concat(created);
  const edges = route.edges.map((edge) => (edge.to === key ? { ...edge, to: created.key } : edge)).concat({ from: created.key, to: key, per_unit: 1 });
  return ordered({ blocks, edges });
}

/** Перенести операцию в другой блок (из блока, где она не единственная). */
export function moveOp(route: Route, position: number, to: string): Route {
  const from = route.blocks.find((block) => block.ops.includes(position));
  if (!from || from.key === to || from.ops.length < 2 || !route.blocks.some((block) => block.key === to)) return route;
  return ordered({
    blocks: route.blocks.map((block) => (block.key === from.key ? { ...block, ops: block.ops.filter((item) => item !== position) }
      : block.key === to ? { ...block, ops: [...block.ops, position] } : block)),
    edges: route.edges,
  });
}

export function renameBlock(route: Route, key: string, name: string): Route {
  return { ...route, blocks: route.blocks.map((block) => (block.key === key ? { ...block, name } : block)) };
}

export function setPerUnit(route: Route, from: string, to: string, perUnit: number): Route {
  return { ...route, edges: route.edges.map((edge) => (edge.from === from && edge.to === to ? { ...edge, per_unit: perUnit } : edge)) };
}

export function addEdge(route: Route, from: string, to: string): Route {
  return ordered({ ...route, edges: route.edges.concat({ from, to, per_unit: 1 }) });
}

export function removeEdge(route: Route, from: string, to: string): Route {
  return { ...route, edges: route.edges.filter((edge) => !(edge.from === from && edge.to === to)) };
}

/** Нормы операций ЭТК, минут на партию (как `_operation_minutes` на сервере); документ не от этих же операций — null. */
export function documentMinutes(document: Record<string, unknown> | null | undefined, types: string[]): { minutes: number[]; quantity: number } | null {
  const list = document && Array.isArray(document.operations) ? document.operations as Array<Record<string, unknown>> : null;
  if (!list || list.length !== types.length || list.some((item, index) => !item || String(item.type ?? "") !== types[index])) return null;
  const num = (value: unknown) => { const n = Number(String(value ?? "").replace(",", ".")); return Number.isFinite(n) ? n : 0; };
  const config = (document!.build_config && typeof document!.build_config === "object" ? document!.build_config : {}) as Record<string, unknown>;
  const kit = (document!.kit && typeof document!.kit === "object" ? document!.kit : {}) as Record<string, unknown>;
  const quantity = num(config.partQty) || num(kit.part_quantity);
  const batch = quantity > 0 ? quantity : 1;
  const minutes = list.map((item) => {
    const rows = (Array.isArray(item.time) ? item.time : []).filter((row) => row && typeof row === "object") as Array<Record<string, unknown>>;
    if (rows.length) return rows.reduce((sum, row) => sum + num(row.norm), 0);
    const times = (item.times && typeof item.times === "object" ? item.times : {}) as Record<string, unknown>;
    return (num(times.operation_seconds) * batch + num(times.setup_seconds)) / 60;
  });
  return { minutes, quantity: batch };
}
