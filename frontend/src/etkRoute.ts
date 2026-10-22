// Схема полуфабрикатов ЭТК (`document.route`, ARCH-TRACK-1): схема ПФ на экране — навигация ЭТК (EtkRouteDiagram, и у
// линейной схемы) — и таблица «Полуфабрикаты» в печати. Чистая логика без React: экран и etkPrint.ts берут одни и те же
// блоки, номера и подписи.
import type { EtkRoute, EtkRouteBlock } from "./api";
import { formatQty } from "./format";

export interface EtkRouteFeed {
  /** Номер блока-источника в полосе (по топологическому порядку). */
  number: number;
  /** Уровень источника: «1.2». */
  label: string;
  name: string;
  /** «×2» только когда на штуку нужно не одна штука источника. */
  times: string;
}

export interface EtkRouteChip {
  key: string;
  /** Порядковый номер в полосе: источники раньше потребителей. */
  number: number;
  /** Уровень на схеме: «2» или «2.1». */
  label: string;
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

type LevelRoute = { blocks: Array<{ key: string; ops: number[] }>; edges: Array<{ from: string; to: string }> };

/** Уровень каждого блока: самый длинный путь от начального блока (начальные — уровень 1, изделие — последний). Метка
 *  «N», когда на уровне один блок, и «N.k», когда несколько (k — по первой операции). Считается так же, как сервер
 *  (`route_levels`): старые схемы без `label` показывают те же уровни, а правка ПФ на экране — свежие. */
export function etkRouteLevels(route: LevelRoute): Map<string, { level: number; label: string }> {
  const level = new Map(route.blocks.map((block) => [block.key, 1]));
  for (let pass = 0; pass < route.blocks.length; pass += 1) {
    let changed = false;
    route.edges.forEach((edge) => {
      const from = level.get(edge.from), to = level.get(edge.to);
      if (from != null && to != null && from + 1 > to) { level.set(edge.to, from + 1); changed = true; }
    });
    if (!changed) break;
  }
  const first = (ops: number[]) => (ops.length ? Math.min(...ops) : Number.MAX_SAFE_INTEGER);
  const members = new Map<number, string[]>();
  [...route.blocks].sort((a, b) => first(a.ops) - first(b.ops) || a.key.localeCompare(b.key))
    .forEach((block) => members.set(level.get(block.key)!, [...(members.get(level.get(block.key)!) ?? []), block.key]));
  const result = new Map<string, { level: number; label: string }>();
  members.forEach((keys, number) => keys.forEach((key, index) =>
    result.set(key, { level: number, label: keys.length === 1 ? String(number) : `${number}.${index + 1}` })));
  return result;
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
  const levels = etkRouteLevels(route);
  const labelOf = (key: string) => levels.get(key)?.label ?? "";
  return blocks.map((block): EtkRouteChip => ({
    key: block.key,
    number: number.get(block.key) ?? 0,
    label: labelOf(block.key),
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
        label: labelOf(source.key),
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

export interface EtkDiagramInput {
  /** Уровень ПФ-источника: «1.2». */
  label: string;
  /** Что приходит: подпись схемы без «×1» («НВ4 0,5 красный; НВ4 0,5 черный»), иначе название источника. */
  text: string;
  /** «×2», когда на штуку нужно больше одной. */
  times: string;
  kit: boolean;
}

export interface EtkDiagramBox {
  key: string;
  label: string;
  name: string;
  ops: number;
  first: number | null;
  perUnit: string;
  final: boolean;
  kit: boolean;
  /** Входящие передачи — списком внутри блока-потребителя (стрелки комплекта не подписываются). */
  inputs: EtkDiagramInput[];
}

export interface EtkDiagramEdge {
  from: string;
  to: string;
  /** Что идёт по стрелке: подпись схемы, без неё «×N» (старая схема), иначе пусто — для подсказки. */
  text: string;
  /** Источник — блок комплекта: стрелка тонкая и без подписи, содержимое — в блоке-потребителе. */
  kit: boolean;
  /** «×N», когда на штуку нужно больше одной; подпись на стрелке не комплекта. */
  count: string;
}

export interface EtkDiagram {
  /** Колонки слева направо — уровни схемы; блоки колонки — в порядке, где стрелки пересекаются реже всего. */
  columns: Array<{ level: number; boxes: EtkDiagramBox[] }>;
  edges: EtkDiagramEdge[];
}

/** Подпись передачи для строки внутри блока: «×1» лишнее, «; » — запятой. */
export function etkInputText(text: string): string {
  return text.replace(/\s*×\s*1(?![\d,.])/g, "").replace(/;\s*/g, ", ").replace(/\s{2,}/g, " ").trim();
}

/** Порядок блоков в колонках по барицентру соседей (Сугияма): блок встаёт среди своих связанных, а не по метке — стрелки
 *  между колонками пересекаются реже. Несколько проходов вперёд и назад, при равенстве — прежний порядок. */
function orderColumns(columns: string[][], edges: Array<{ from: string; to: string }>): string[][] {
  const result = columns.map((column) => [...column]);
  const rank = (index: number, size: number) => (index + 0.5) / size;
  const place = (column: number, neighbours: (key: string) => string[], side: number) => {
    const at = new Map(result[side].map((key, index) => [key, rank(index, result[side].length)]));
    const own = new Map(result[column].map((key, index) => [key, rank(index, result[column].length)]));
    const score = (key: string) => {
      const found = neighbours(key).map((other) => at.get(other)).filter((value): value is number => value != null);
      return found.length ? found.reduce((sum, value) => sum + value, 0) / found.length : own.get(key)!;
    };
    const keep = new Map(result[column].map((key, index) => [key, index]));
    result[column] = [...result[column]].sort((a, b) => score(a) - score(b) || keep.get(a)! - keep.get(b)!);
  };
  for (let pass = 0; pass < 4; pass += 1) {
    for (let column = 1; column < result.length; column += 1) place(column, (key) => edges.filter((edge) => edge.to === key).map((edge) => edge.from), column - 1);
    for (let column = result.length - 2; column >= 0; column -= 1) place(column, (key) => edges.filter((edge) => edge.from === key).map((edge) => edge.to), column + 1);
  }
  return result;
}

/** Число пересечений стрелок между соседними колонками при данном порядке блоков (проверка порядка и тесты). */
export function etkDiagramCrossings(diagram: EtkDiagram): number {
  const index = new Map(diagram.columns.flatMap((column) => column.boxes.map((box, at) => [box.key, { level: column.level, at }] as const)));
  const arrows = diagram.edges.flatMap((edge) => {
    const from = index.get(edge.from), to = index.get(edge.to);
    return from && to && from.level < to.level ? [{ from, to }] : [];
  });
  let count = 0;
  arrows.forEach((a, i) => arrows.slice(i + 1).forEach((b) => {
    if (a.from.level !== b.from.level || a.to.level !== b.to.level) return;
    if ((a.from.at - b.from.at) * (a.to.at - b.to.at) < 0) count += 1;
  }));
  return count;
}

/** Схема ПФ как сеть: колонка на уровень, блок на ПФ, стрелка на передачу. */
export function etkRouteDiagram(route: EtkRoute): EtkDiagram {
  const levels = etkRouteLevels(route);
  const chips = new Map(etkRouteChips(route).map((chip) => [chip.key, chip]));
  const kit = new Map(route.blocks.map((block) => [block.key, block.kit === true]));
  const byLevel = new Map<number, string[]>();
  [...route.blocks].sort((a, b) => levels.get(a.key)!.label.localeCompare(levels.get(b.key)!.label, "ru", { numeric: true }))
    .forEach((block) => { const level = levels.get(block.key)!.level; byLevel.set(level, [...(byLevel.get(level) ?? []), block.key]); });
  const keys = [...byLevel.keys()].sort((a, b) => a - b);
  const ordered = orderColumns(keys.map((level) => byLevel.get(level)!), route.edges);
  const times = (value: number) => (value === 1 ? "" : `×${String(value).replace(".", ",")}`);
  const edges = route.edges.map((edge): EtkDiagramEdge => ({
    from: edge.from, to: edge.to, kit: kit.get(edge.from) === true, count: times(edge.per_unit),
    text: edge.item || times(edge.per_unit),
  }));
  const columns = keys.map((level, at) => ({
    level,
    boxes: ordered[at].map((key): EtkDiagramBox => {
      const chip = chips.get(key)!;
      const inputs = route.edges.filter((edge) => edge.to === key && chips.has(edge.from)).map((edge): EtkDiagramInput => ({
        label: levels.get(edge.from)?.label ?? "",
        text: etkInputText(edge.item || "") || chips.get(edge.from)!.name,
        times: edge.item ? "" : times(edge.per_unit),
        kit: kit.get(edge.from) === true,
      })).sort((a, b) => a.label.localeCompare(b.label, "ru", { numeric: true }));
      return { key, label: chip.label, name: chip.name, ops: chip.ops, first: chip.first, perUnit: chip.perUnit, final: chip.final, kit: kit.get(key) === true, inputs };
    }),
  }));
  return { columns, edges };
}

/** Схема ПФ задания (владелец 20.10, на 713К.4 вся схема не входит в планшет): путь монтажника — ПФ, из которых он
 *  берёт детали, его ПФ `key` и дальше до изделия. Номера, названия и входы блоков — как во всей схеме. */
export function etkRoutePathDiagram(route: EtkRoute, key: string): EtkDiagram {
  const keep = new Set([key]);
  route.edges.forEach((edge) => { if (edge.to === key) keep.add(edge.from); });
  for (let next = [key]; next.length;) {
    next = route.edges.filter((edge) => next.includes(edge.from) && !keep.has(edge.to)).map((edge) => edge.to);
    next.forEach((item) => keep.add(item));
  }
  const full = new Map(etkRouteDiagram(route).columns.flatMap((column) => column.boxes).map((box) => [box.key, box]));
  const part = etkRouteDiagram({ ...route, blocks: route.blocks.filter((block) => keep.has(block.key)),
    edges: route.edges.filter((edge) => keep.has(edge.from) && keep.has(edge.to)) });
  return { ...part, columns: part.columns.map((column) => ({ ...column, boxes: column.boxes.map((box) => full.get(box.key) ?? box) })) };
}

export function etkRouteFeedText(feed: EtkRouteFeed): string {
  return `ПФ ${feed.label} ${feed.name}${feed.times ? ` (${feed.times})` : ""}`;
}

/* ── ПФ ЭТК (владелец + Василий 13.10): ЭТК режется на полуфабрикаты; ПФ — это блок схемы (`document.route`), не
   отдельная сущность. Порядок `block.ops` — порядок работы внутри ПФ, номера операций ЭТК не меняются («05, 03» в одном
   ПФ можно). Считается здесь из документа; сервер считает то же (`pf_documents` в techcards/domain/route.py). */

export type EtkPfRow = { name?: string; art?: string; norm?: string; unit?: string };

export interface EtkPfInput {
  /** Номер ПФ-источника (порядок в списке), его уровень и название. */
  number: number;
  label: string;
  name: string;
  /** «×2» только когда на штуку нужно не одна штука. */
  times: string;
}

export interface EtkPfGroup {
  key: string;
  /** Порядок блока в списке (по первому номеру операции). */
  number: number;
  /** Уровень на схеме: «ПФ 1.1» — метка «1.1». */
  label: string;
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

/** Норма операции ЭТК, мин на партию (как `_operation_minutes` на сервере): Σ строк времени, без них — секунды карты
 *  на штуку × партия; подготовка в норму не входит (владелец 18.10). */
export function operationMinutes(operation: EtkPfOperation | null | undefined, batch: number): number {
  const time = (Array.isArray(operation?.time) ? operation.time : []).map(record);
  if (time.length) return time.reduce((sum, row) => sum + num(row.norm), 0);
  const times = record(operation?.times);
  return (num(times.operation_seconds) * (batch > 0 ? batch : 1)) / 60;
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
  const levels = etkRouteLevels(route);
  const labelOf = (key: string) => levels.get(key)?.label ?? "";
  return blocks.map((block): EtkPfGroup => {
    const own = block.ops.map((position) => operations[position - 1]).filter((operation): operation is EtkPfOperation => !!operation);
    const last = own[own.length - 1];
    return {
      key: block.key,
      number: number.get(block.key) ?? 0,
      label: labelOf(block.key),
      name: block.name.trim(),
      final: block.final,
      ops: [...block.ops],
      components: sumRows(own.flatMap((operation) => rows(operation.components))),
      inputs: route.edges.filter((edge) => edge.to === block.key && byKey.has(edge.from))
        .map((edge) => ({
          number: number.get(edge.from) ?? 0,
          label: labelOf(edge.from),
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

/** Сколько штук каждый ПФ отдаёт на партию `batch` изделий: изделие — партия, источник — сумма по его стрелкам
 *  «штук на одну» потребителя. Нужна выходу ПФ, у результата которого нет нормы (типовая сборка пишет её только
 *  изделию), — как у 713К: «→ … 500 шт» у каждого ПФ. */
export function etkPfNeed(route: Pick<EtkRoute, "blocks" | "edges">, batch: number): Map<string, number> {
  const need = new Map<string, number>();
  const visit = (key: string, seen: Set<string>): number => {
    const known = need.get(key);
    if (known != null) return known;
    const block = route.blocks.find((item) => item.key === key);
    if (!block || seen.has(key)) return 0;
    const out = route.edges.filter((edge) => edge.from === key);
    const value = block.final && !out.length ? batch
      : out.reduce((sum, edge) => sum + visit(edge.to, new Set([...seen, key])) * (edge.per_unit > 0 ? edge.per_unit : 1), 0);
    need.set(key, value);
    return value;
  };
  route.blocks.forEach((block) => visit(block.key, new Set()));
  return need;
}

/** Комплектующие ПФ строками входа (схема без блоков комплекта — типовая сборка; у «Сложной» они приходят своими
 *  блоками): название и сколько на одно изделие («Колодка XH 4 — 1 шт», «НВ4 0,5 красный — 0,6 м»). */
export function etkPfComponentInputs(group: Pick<EtkPfGroup, "components">, batch: number): Array<{ text: string; qty: string; title: string }> {
  return group.components.filter((row) => (row.name || row.art || "").trim()).map((row) => {
    const value = num(row.norm) / (batch > 0 ? batch : 1);
    const name = (row.name || row.art || "").trim();
    return {
      text: name,
      qty: value > 0 ? formatQty(Math.round(value * 1000) / 1000, row.unit || "шт") : "",
      title: [name, row.art && row.art.trim() !== name ? row.art.trim() : ""].filter(Boolean).join(" · "),
    };
  });
}

/** Что ПФ отдаёт дальше — коротким названием: результат его последней операции, но не накопленная строка
 *  («Провода нарезаны: …», длиннее 48 знаков) — тогда имя ПФ. Нет результата — пусто. */
export function etkPfOutName(group: Pick<EtkPfGroup, "name" | "output">): string {
  const output = group.output[0];
  if (!output) return "";
  const name = (output.name ?? "").trim();
  return name && name.length <= 48 && !name.includes(": ") ? name : group.name || name;
}

/** Номер операции ЭТК в списке: «01», «05». */
export function opNumber(position: number): string {
  return String(position).padStart(2, "0");
}

/** «Откуда» входа построчно: общая строка источников («из ПФ 1.2, коробка с операции 02; ПФ 1.6, …») — по строке на
 *  источник, «ПФ 1.2 · коробка оп. 02». Один источник («с операции 03») — одна строка. */
export function etkSourceLines(text: string): string[] {
  return text.replace(/^из\s+/, "").split(/;\s*/).map((part) => part.trim()).filter(Boolean)
    .map((part) => part.replace(/,\s*коробка с операции\s*/, " · коробка оп. "));
}

/** Откуда фитеру брать комплектующие операции. */
export const ETK_PART_SOURCE = "со склада по комплектации";

/** То же с номером заявки комплектации (`kit_request` строки; несколько — через «, »): «… REQ.26.0000123». Нет заявки — без номера. */
export function etkPartSource(row: { kit_request?: unknown }): string {
  const request = String(row.kit_request ?? "").trim();
  return request ? `${ETK_PART_SOURCE} ${request}` : ETK_PART_SOURCE;
}

/** Откуда фитеру брать полуфабрикат на входе операции `position`: внутри ПФ — с предыдущей операции этого ПФ; первая
 *  операция ПФ берёт коробки других ПФ («из ПФ 1.2, коробка с операции 03»). Номера ПФ — уровни схемы, как в списке ПФ ЭТК. Без схемы — с предыдущей операции списка; у самой первой источника нет. */
export function etkSemiSource(route: EtkRoute | null, position: number): string {
  const block = route?.blocks.find((item) => item.ops.includes(position));
  if (!route || !block) return position > 1 ? `с операции ${opNumber(position - 1)}` : "";
  const at = block.ops.indexOf(position);
  if (at > 0) return `с операции ${opNumber(block.ops[at - 1])}`;
  const levels = etkRouteLevels(route);
  const byKey = new Map(route.blocks.map((item) => [item.key, item]));
  const feeds = route.edges.filter((edge) => edge.to === block.key && byKey.has(edge.from)).map((edge) => {
    const source = byKey.get(edge.from)!;
    const lastOp = source.ops[source.ops.length - 1];
    return { label: levels.get(source.key)?.label ?? "", lastOp };
  }).sort((a, b) => a.label.localeCompare(b.label, "ru", { numeric: true }))
    .map(({ label, lastOp }) => `ПФ ${label}${lastOp ? `, коробка с операции ${opNumber(lastOp)}` : ""}`);
  return feeds.length ? `из ${feeds.join("; ")}` : position > 1 ? `с операции ${opNumber(position - 1)}` : "";
}

/** Номера операций подряд — диапазоном: [6,7,8,14] → «06–08·14». */
export function etkOpRuns(numbers: number[], show: (value: number) => string): string {
  const runs: Array<[number, number]> = [];
  numbers.forEach((value) => {
    const last = runs[runs.length - 1];
    if (last && value === last[1] + 1) last[1] = value;
    else runs.push([value, value]);
  });
  return runs.map(([from, to]) => (from === to ? show(from) : `${show(from)}${to === from + 1 ? "·" : "–"}${show(to)}`)).join("·");
}
