// Схема полуфабрикатов ЭТК (`document.route`, ARCH-TRACK-1): полоса «Маршрут» на экране и строка в печати. Показывается
// только у ветвящейся схемы — линейная ЭТК рисуется как раньше. Чистая логика без React: экран и etkPrint.ts берут
// одни и те же блоки, номера и подписи.
import type { EtkRoute, EtkRouteBlock } from "./api";

export interface EtkRouteFeed {
  /** Номер блока-источника в полосе (по топологическому порядку). */
  number: number;
  name: string;
  /** «×2» только когда на штуку нужно не одна штука источника. */
  times: string;
}

export interface EtkRouteChip {
  key: string;
  /** Порядковый номер в полосе: источники раньше потребителей. */
  number: number;
  name: string;
  ops: number;
  /** 1-based позиция первой операции блока в ЭТК; null — у блока нет операций. */
  first: number | null;
  /** «0,5 мин/шт» либо пусто, когда времени в схеме нет. */
  perUnit: string;
  /** «на изделие» с учётом всех входов: время и число операций. */
  rolledMinutes: number;
  rolledOps: number;
  feeds: EtkRouteFeed[];
  final: boolean;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function num(value: unknown): number {
  const parsed = typeof value === "number" ? value : Number(String(value ?? "").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
}

/** `document.route` из ответа API; нет схемы (старая ЭТК до версии 3) или она пустая — null. */
export function etkRouteOf(document: Record<string, unknown> | null | undefined): EtkRoute | null {
  const raw = record(document?.route);
  if (!Array.isArray(raw.blocks) || !raw.blocks.length) return null;
  const blocks = raw.blocks.map(record).filter((block) => typeof block.key === "string" && block.key).map((block): EtkRouteBlock => ({
    key: String(block.key),
    name: String(block.name ?? ""),
    ops: Array.isArray(block.ops) ? block.ops.map(num).filter((position) => position > 0) : [],
    final: block.final === true,
    components: Array.isArray(block.components) ? block.components : [],
    outputs: num(block.outputs) || 1,
    inputs: num(block.inputs),
    minutes: num(block.minutes),
    unit_minutes: num(block.unit_minutes),
    rolled_minutes: num(block.rolled_minutes),
    rolled_ops: num(block.rolled_ops),
    ...(block.kit === true ? { kit: true } : {}),
    ...(typeof block.label === "string" && block.label ? { label: block.label } : {}),
  }));
  if (!blocks.length) return null;
  const known = new Set(blocks.map((block) => block.key));
  const edges = (Array.isArray(raw.edges) ? raw.edges : []).map(record)
    .filter((edge) => known.has(String(edge.from)) && known.has(String(edge.to)))
    .map((edge) => ({
      from: String(edge.from), to: String(edge.to), per_unit: num(edge.per_unit) || 1,
      ...(typeof edge.item === "string" && edge.item.trim() ? { item: edge.item.trim() } : {}),
    }));
  return {
    source: raw.source === "manual" || raw.source === "generated" ? raw.source : "derived",
    final: String(raw.final ?? blocks.find((block) => block.final)?.key ?? ""),
    blocks,
    edges,
  };
}

/** Ветвится ли схема: у какого-то блока больше одного входа или выхода, либо больше одного начального блока. Одна цепочка
 *  (в том числе из одного блока) — линейная ЭТК, полоса не нужна. */
export function etkRouteBranches(route: EtkRoute | null): boolean {
  if (!route) return false;
  const incoming = new Map<string, number>();
  const outgoing = new Map<string, number>();
  route.edges.forEach((edge) => {
    incoming.set(edge.to, (incoming.get(edge.to) ?? 0) + 1);
    outgoing.set(edge.from, (outgoing.get(edge.from) ?? 0) + 1);
  });
  const roots = route.blocks.filter((block) => !incoming.has(block.key)).length;
  return [...incoming.values(), ...outgoing.values()].some((count) => count > 1) || roots > 1;
}

/** Блоки в топологическом порядке (Кан): источники раньше потребителей, при равенстве — как в схеме (по первой операции). */
function topological(route: EtkRoute): EtkRouteBlock[] {
  const left = new Map(route.blocks.map((block) => [block.key, 0]));
  route.edges.forEach((edge) => left.set(edge.to, (left.get(edge.to) ?? 0) + 1));
  const ordered: EtkRouteBlock[] = [];
  const done = new Set<string>();
  while (ordered.length < route.blocks.length) {
    const ready = route.blocks.find((block) => !done.has(block.key) && left.get(block.key) === 0);
    // Цикл в схеме (бэкенд такую не отдаёт): остаток идёт как есть, чтобы полоса не пропала.
    const block = ready ?? route.blocks.find((item) => !done.has(item.key));
    if (!block) break;
    done.add(block.key);
    ordered.push(block);
    route.edges.filter((edge) => edge.from === block.key).forEach((edge) => left.set(edge.to, (left.get(edge.to) ?? 0) - 1));
  }
  return ordered;
}

export function formatMinutes(value: number): string {
  return String(Math.round(value * 100) / 100).replace(".", ",");
}

/** Блоки полосы в порядке «источники → потребители» с номерами и тем, что чем питается. */
export function etkRouteChips(route: EtkRoute): EtkRouteChip[] {
  const blocks = topological(route);
  const number = new Map(blocks.map((block, index) => [block.key, index + 1]));
  const byKey = new Map(blocks.map((block) => [block.key, block]));
  return blocks.map((block): EtkRouteChip => ({
    key: block.key,
    number: number.get(block.key) ?? 0,
    name: block.name.trim() || `Блок ${number.get(block.key)}`,
    ops: block.ops.length,
    first: block.ops.length ? Math.min(...block.ops) : null,
    perUnit: block.unit_minutes > 0 ? `${formatMinutes(block.unit_minutes)} мин/шт` : "",
    rolledMinutes: block.rolled_minutes,
    rolledOps: block.rolled_ops,
    feeds: route.edges.filter((edge) => edge.to === block.key)
      .flatMap((edge) => { const source = byKey.get(edge.from); return source ? [{ edge, source }] : []; })
      .sort((a, b) => (number.get(a.source.key) ?? 0) - (number.get(b.source.key) ?? 0))
      .map(({ edge, source }) => ({
        number: number.get(source.key) ?? 0,
        name: source.name.trim() || `Блок ${number.get(source.key)}`,
        times: edge.per_unit === 1 ? "" : `×${String(edge.per_unit).replace(".", ",")}`,
      })),
    final: block.final || block.key === route.final,
  }));
}

export function pluralBlocks(count: number): string {
  const last = count % 10;
  const tens = count % 100;
  const word = tens >= 11 && tens <= 14 ? "блоков" : last === 1 ? "блок" : last >= 2 && last <= 4 ? "блока" : "блоков";
  return `${count} ${word}`;
}

/** «4 блока · на изделие 5,2 мин · 9 оп.» — итог схемы по финальному блоку (время и операции со всеми входами). */
export function etkRouteSummary(chips: EtkRouteChip[]): string {
  const last = chips.find((chip) => chip.final);
  return [
    pluralBlocks(chips.length),
    last && last.rolledMinutes > 0 ? `на изделие ${formatMinutes(last.rolledMinutes)} мин` : "",
    last && last.rolledOps > 0 ? `${last.rolledOps} оп.` : "",
  ].filter(Boolean).join(" · ");
}

export function etkRouteFeedText(feed: EtkRouteFeed): string {
  return `${feed.number} ${feed.name}${feed.times ? ` (${feed.times})` : ""}`;
}

/** Одна строка для печати под шапкой: та же полоса текстом. Линейная схема и её отсутствие — пустая строка. */
export function etkRouteLine(route: EtkRoute | null): string {
  if (!route || !etkRouteBranches(route)) return "";
  const chips = etkRouteChips(route);
  return `Маршрут полуфабрикатов (${etkRouteSummary(chips)}): ${chips.map((chip) => {
    const feeds = chip.feeds.map((feed) => `${feed.number}${feed.times ? ` (${feed.times})` : ""}`).join(", ");
    const meta = [chip.ops ? `${chip.ops} оп.` : "", chip.perUnit, feeds ? `из ${feeds}` : "", chip.final ? "итог" : ""].filter(Boolean).join(" · ");
    return `${chip.number} ${chip.name}${meta ? ` (${meta})` : ""}`;
  }).join("; ")}`;
}

/* ── ПФ ЭТК (владелец + Василий 13.10): ЭТК режется на полуфабрикаты; ПФ — это блок схемы (`document.route`), не
   отдельная сущность. Порядок `block.ops` — порядок работы внутри ПФ, номера операций ЭТК не меняются («05, 03» в одном
   ПФ можно). Считается здесь из документа; сервер считает то же (`pf_documents` в techcards/domain/route.py). */

export type EtkPfRow = { name?: string; art?: string; norm?: string; unit?: string };

export interface EtkPfInput {
  /** Номер ПФ-источника и его название. */
  number: number;
  name: string;
  /** «×2» только когда на штуку нужно не одна штука. */
  times: string;
}

export interface EtkPfGroup {
  key: string;
  /** ПФ-n: по порядку блоков схемы (по первому номеру операции). */
  number: number;
  name: string;
  final: boolean;
  /** Позиции операций ЭТК (1-based) в порядке работы. */
  ops: number[];
  /** Комплектующие всех операций ПФ, один артикул сложен. */
  components: EtkPfRow[];
  /** Полуфабрикаты из других ПФ (входящие стрелки). */
  inputs: EtkPfInput[];
  /** Что ПФ отдаёт дальше — результат его последней операции. */
  output: EtkPfRow[];
  /** Норма ПФ, мин на партию: Σ норм его операций. */
  minutes: number;
}

export type EtkPfOperation = { components?: unknown; result?: unknown; time?: unknown; times?: unknown };
type PfRoute = { blocks: Array<{ key: string; name: string; ops: number[]; final: boolean }>; edges: Array<{ from: string; to: string; per_unit: number }> };

function rows(value: unknown): EtkPfRow[] {
  return (Array.isArray(value) ? value : []).map(record).map((row) => ({
    ...(row.name != null ? { name: String(row.name) } : {}), ...(row.art != null ? { art: String(row.art) } : {}),
    ...(row.norm != null ? { norm: String(row.norm) } : {}), ...(row.unit != null ? { unit: String(row.unit) } : {}),
  }));
}

/** Название результата последней операции блока, если оно одно; иначе пусто (берётся имя блока схемы). */
function resultNameOf(block: { ops: number[] }, operations: EtkPfOperation[]): string {
  const last = operations[block.ops[block.ops.length - 1] - 1];
  const list = last ? rows(last.result) : [];
  return list.length === 1 ? (list[0].name ?? "").trim() : "";
}

/** Норма операции ЭТК, мин на партию (как `_operation_minutes` на сервере): Σ строк времени, без них — секунды карты. */
export function operationMinutes(operation: EtkPfOperation | null | undefined, batch: number): number {
  const time = (Array.isArray(operation?.time) ? operation.time : []).map(record);
  if (time.length) return time.reduce((sum, row) => sum + num(row.norm), 0);
  const times = record(operation?.times);
  return (num(times.operation_seconds) * (batch > 0 ? batch : 1) + num(times.setup_seconds)) / 60;
}

/** Сложить строки одного артикула (без артикула — названия) и единицы; строка без числа остаётся своей. */
function sumRows(list: EtkPfRow[]): EtkPfRow[] {
  const result: EtkPfRow[] = [];
  const index = new Map<string, EtkPfRow>();
  for (const row of list) {
    const id = (row.art || row.name || "").trim();
    const value = Number(String(row.norm ?? "").replace(",", "."));
    const counted = row.norm != null && row.norm !== "" && Number.isFinite(value);
    const key = `${id}\u0000${(row.unit || "").trim()}`;
    const same = id && counted ? index.get(key) : undefined;
    if (same) {
      same.norm = String(Math.round((num(same.norm) + value) * 10000) / 10000).replace(".", ",");
      continue;
    }
    const copy = { ...row };
    if (id && counted) index.set(key, copy);
    result.push(copy);
  }
  return result;
}

/** ПФ документа: по блоку схемы на ПФ в порядке схемы, с входом (комплектующие и полуфабрикаты других ПФ), выходом
 *  и нормой. Схема — сохранённая или правимая на экране (её блоки без итогов: норма считается по операциям). */
export function etkPfGroups(route: PfRoute | null, operations: EtkPfOperation[], batch = 1): EtkPfGroup[] {
  if (!route) return [];
  const first = (ops: number[]) => (ops.length ? Math.min(...ops) : Number.MAX_SAFE_INTEGER);
  const blocks = [...route.blocks].sort((a, b) => first(a.ops) - first(b.ops) || a.key.localeCompare(b.key));
  const number = new Map(blocks.map((block, index) => [block.key, index + 1]));
  const byKey = new Map(blocks.map((block) => [block.key, block]));
  return blocks.map((block): EtkPfGroup => {
    const own = block.ops.map((position) => operations[position - 1]).filter((operation): operation is EtkPfOperation => !!operation);
    const last = own[own.length - 1];
    return {
      key: block.key,
      number: number.get(block.key) ?? 0,
      name: block.name.trim(),
      final: block.final,
      ops: [...block.ops],
      components: sumRows(own.flatMap((operation) => rows(operation.components))),
      inputs: route.edges.filter((edge) => edge.to === block.key && byKey.has(edge.from))
        .map((edge) => ({
          number: number.get(edge.from) ?? 0,
          // Название входа — результат последней операции ПФ-источника, слово в слово (как в его «Результате»).
          name: resultNameOf(byKey.get(edge.from)!, operations) || byKey.get(edge.from)!.name.trim(),
          times: edge.per_unit === 1 ? "" : `×${String(edge.per_unit).replace(".", ",")}`,
        }))
        .sort((a, b) => a.number - b.number),
      output: last ? rows(last.result) : [],
      minutes: own.reduce((sum, operation) => sum + operationMinutes(operation, batch), 0),
    };
  });
}

/** Номер операции ЭТК в списке: «01», «05». */
export function opNumber(position: number): string {
  return String(position).padStart(2, "0");
}

/** Откуда фитеру брать комплектующие операции. */
export const ETK_PART_SOURCE = "со склада по комплектации";

/** Откуда фитеру брать полуфабрикат на входе операции `position`: внутри ПФ — с предыдущей операции этого ПФ; первая
 *  операция ПФ берёт коробки других ПФ («из ПФ-2, коробка с операции 03»). Номера ПФ — как в списке ПФ ЭТК (по первой
 *  операции). Без схемы — с предыдущей операции списка; у самой первой источника нет. */
export function etkSemiSource(route: EtkRoute | null, position: number): string {
  const block = route?.blocks.find((item) => item.ops.includes(position));
  if (!route || !block) return position > 1 ? `с операции ${opNumber(position - 1)}` : "";
  const at = block.ops.indexOf(position);
  if (at > 0) return `с операции ${opNumber(block.ops[at - 1])}`;
  const first = (ops: number[]) => (ops.length ? Math.min(...ops) : Number.MAX_SAFE_INTEGER);
  const order = [...route.blocks].sort((a, b) => first(a.ops) - first(b.ops) || a.key.localeCompare(b.key));
  const byKey = new Map(route.blocks.map((item) => [item.key, item]));
  const feeds = route.edges.filter((edge) => edge.to === block.key && byKey.has(edge.from)).map((edge) => {
    const source = byKey.get(edge.from)!;
    const number = order.findIndex((item) => item.key === source.key) + 1;
    const lastOp = source.ops[source.ops.length - 1];
    return `ПФ-${number}${lastOp ? `, коробка с операции ${opNumber(lastOp)}` : ""}`;
  });
  return feeds.length ? `из ${feeds.join("; ")}` : position > 1 ? `с операции ${opNumber(position - 1)}` : "";
}
