/* «Ручная сборка» (владелец 04.10, схема-первой — 05.10: «собрать просто блок схему и из нее вести порядок и привязывать
   операции к блокам») — четвёртый режим генератора для кастомных и нетиповых проводов. Технолог рисует схему сборки:
   итоговый блок (изделие) и блоки-полуфабрикаты, которые в него входят, и кладёт в каждый блок операции — карточки из
   библиотеки и свои (название, норма, ход операции, контроль с допуском, оснастка). Номера операций — по схеме: сначала
   блоки-входы, потом те, что их берут (при равенстве — в порядке создания блоков), внутри блока — по порядку в блоке.
   На сервер схема уходит всегда как ручная (`build_config.route`), чтобы производство шло по ней. Сервер —
   `etk_builder._build_manual`. */

import type { OperationCardSummary } from "./api";
import { recordCategory } from "./etkOperation";
import { addEdge, blockTitle, parseRoute, removeEdge, routePayload, routeProblem, type Route, type RouteEdge, type RouteOp } from "./routeLogic";
import { opTypeName, type Comp, type OpRec } from "./tcGenLogic";

export const MANUAL_MODE = "manual";
const LIBRARY_OP = "libraryOp";
const CUSTOM_OP = "customOp";
const TOOL_SECTIONS = ["Инструмент", "Оборудование", "Расходники"];

/** Карточка библиотеки операций (GET /api/techcards/templates) в выборе ручной сборки. */
export type LibraryCard = Pick<OperationCardSummary, "id" | "code" | "side" | "title" | "name" | "category" | "norms" | "first_step">;

/** Строка состава КБ на операции: сколько на одно изделие. */
export type ManualComponent = { id: string; art: string; name: string; qty: string; unit: string };

export type ManualOp = {
  /** Номер, данный операции при добавлении: не меняется при перестановке и не переиспользуется (ключ операции ЭТК). */
  seq: number;
  kind: "library" | "custom";
  templateId: string;
  code: string;
  side: string;
  label: string;
  /** На изделие, шт. */
  qty: string;
  /** Подготовка, мин (в конфиг — секундами, как у всех операций генератора). */
  prepMin: string;
  /** Операция, сек на штуку. */
  opSec: string;
  params: string;
  note: string;
  /** Полуфабрикат, который операция отдаёт дальше; пусто — «Полуфабрикат после «…»». */
  result: string;
  /** Своя операция: ход операции — шаг на строку. */
  steps: string;
  /** Своя операция: контроль — «что проверить | допуск» на строку. */
  controls: string;
  /** Своя операция: оснастка строкой, «Оборудование: …» / «Расходники: …», без раздела — инструмент. */
  tooling: string;
  components: ManualComponent[];
  /** Типовая операция генератора (есть `type`): её поля как пришли с сервера (провода, длины, зачистка, терминал, коакс…) —
      на экране только строкой, в конфиг уходят как есть; правятся норма, блок и место. */
  gen?: Record<string, unknown>;
  /** Откуда операция в КД: устойчивый ключ `harness:<объект>:<код операции>` (kdKey). Нет — операция мастера. */
  origin?: string;
  /** Поля, которые мастер правил у операции из КД: при пересчёте по КД они не трогаются (мастер главнее). */
  edited?: KdField[];
  /** Значение из КД (JSON) у правленного мастером поля — с чем сравнивать, когда КД поменяется. */
  kdBase?: Partial<Record<KdField, string>>;
  /** Что поменялось в КД при последнем пересчёте: «в КД было X → стало Y» по полю. */
  kdChanges?: KdChange[];
  /** Ключа операции больше нет в КД — операция не удаляется, только помечается «нет в КД». */
  kdGone?: boolean;
};

/** Поля операции, которые приходят из КД и сверяются при пересчёте. */
export const KD_FIELDS = ["label", "qty", "prepMin", "opSec", "params", "note", "result", "steps", "controls", "tooling", "components", "gen"] as const;
export type KdField = (typeof KD_FIELDS)[number];
export const KD_FIELD_NAMES: Record<KdField, string> = {
  label: "Название", qty: "На изделие", prepMin: "Подготовка, мин", opSec: "Операция, с/шт", params: "Параметры", note: "Примечание",
  result: "Результат", steps: "Ход операции", controls: "Контроль", tooling: "Оснастка", components: "Комплектующие", gen: "Параметры",
};
export type KdChange = { field: KdField; was: string; now: string };

/** Ключ операции в КД: объект документа жгута и код операции — по нему пересчёт находит «ту же» операцию. */
export const kdKey = (objectId: string, opCode: string) => `harness:${objectId}:${opCode}`;

const SIDES: Record<string, string> = { A: "А", B: "В", "А": "А", "В": "В" };

function num(text: unknown): number | null {
  const value = String(text ?? "").trim().replace(/\s/g, "").replace(",", ".");
  if (!value) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : Number.NaN;
}

/** «2,5» — число для поля: запятая, без хвостовых нулей. */
function text(value: number): string {
  return String(Math.round(value * 1000) / 1000).replace(".", ",");
}

/** Русское название карточки: имя карты, иначе название шаблона без кода, иначе категория по коду; со стороной. */
export function cardLabel(card: LibraryCard): string {
  const fromTitle = String(card.title || "").split("|").map((part) => part.trim()).filter(Boolean).at(-1) || "";
  const base = card.name?.trim() || fromTitle.replace(/\s*\((?:[AАBВ])\)$/, "") || recordCategory(card.code) || "Операция";
  const side = SIDES[String(card.side || "").trim()];
  return side ? `${base} (${side})` : base;
}

export function cardCategory(card: LibraryCard): string {
  return recordCategory(card.code);
}

/** Норма карточки из БД.ОП по коду (как у типового генератора), иначе — из библиотеки. Секунды. */
export function cardNorms(card: LibraryCard, opRecs: OpRec[]): { prep: number; op: number } | null {
  const code = card.code.trim().toLowerCase();
  const rec = opRecs.find((item) => (item.opNumber || "").trim().toLowerCase() === code);
  const prep = num(rec ? rec.tPrep : card.norms?.setup_seconds);
  const op = num(rec ? rec.tOp : card.norms?.operation_seconds);
  if ((prep == null || Number.isNaN(prep)) && (op == null || Number.isNaN(op))) return null;
  return { prep: prep && !Number.isNaN(prep) ? prep : 0, op: op && !Number.isNaN(op) ? op : 0 };
}

function blank(seq: number): ManualOp {
  return {
    seq, kind: "custom", templateId: "", code: "", side: "", label: "", qty: "1", prepMin: "", opSec: "",
    params: "", note: "", result: "", steps: "", controls: "", tooling: "", components: [],
  };
}

export function libraryOp(seq: number, card: LibraryCard, opRecs: OpRec[]): ManualOp {
  const norms = cardNorms(card, opRecs);
  return {
    ...blank(seq), kind: "library", templateId: card.id, code: card.code, side: card.side || "", label: cardLabel(card),
    prepMin: norms && norms.prep ? text(norms.prep / 60) : "", opSec: norms && norms.op ? text(norms.op) : "",
  };
}

/** Вид блока схемы (как у Route V2 Васи): полуфабрикат, сборка (в неё входят другие блоки), итог — изделие. */
export type BlockKind = "semiFinished" | "assembly" | "final";
export const BLOCK_KIND_NAMES: Record<BlockKind, string> = { semiFinished: "Полуфабрикат", assembly: "Сборка", final: "Итог — изделие" };

/** Блок схемы ручной сборки: свои операции (по seq, в порядке внутри блока). Итоговый — изделие, он один.
    Необязательное (старые конфиги без него): вид, если мастер его задал; штук в блоке; объекты КД; «проверено мастером». */
export type ManualBlock = {
  key: string; name: string; final: boolean; ops: number[];
  kind?: Exclude<BlockKind, "final">; quantity?: number; refs?: string[]; checked?: boolean;
};

/** Операции (по seq, порядок массива не важен — номера даёт схема), блоки в порядке создания, стрелки и выбранный блок. */
export type ManualState = {
  ops: ManualOp[]; nextSeq: number; blocks: ManualBlock[]; edges: RouteEdge[]; selected: string;
  /** Данные сборки, нужные операциям генератора (провода, свивка, лужение, зачистка, соединения, коакс…) — как пришли. */
  extra?: Record<string, unknown>;
  /** Ключи КД операций, которые мастер удалил: пересчёт по КД их не возвращает. */
  kdDropped?: string[];
};

const FINAL_KEY = "b1";

export const emptyManual = (): ManualState => ({
  ops: [], nextSeq: 1, blocks: [{ key: FINAL_KEY, name: "", final: true, ops: [] }], edges: [], selected: FINAL_KEY,
});

const freeKey = (blocks: ManualBlock[]) => `b${Math.max(0, ...blocks.map((block) => Number(/^b(\d+)$/.exec(block.key)?.[1] ?? 0))) + 1}`;

/** Порядок блоков: блок идёт после всех своих входов; из готовых — тот, что создан раньше. */
export function blockOrder(state: Pick<ManualState, "blocks" | "edges">): string[] {
  const index = new Map(state.blocks.map((block, place) => [block.key, place]));
  const waiting = new Map(state.blocks.map((block) => [block.key, 0]));
  state.edges.forEach((edge) => waiting.set(edge.to, (waiting.get(edge.to) ?? 0) + 1));
  const ready = state.blocks.filter((block) => !waiting.get(block.key)).map((block) => block.key);
  const order: string[] = [];
  while (ready.length) {
    ready.sort((a, b) => index.get(a)! - index.get(b)!);
    const key = ready.shift()!;
    order.push(key);
    state.edges.filter((edge) => edge.from === key).forEach((edge) => {
      waiting.set(edge.to, (waiting.get(edge.to) ?? 0) - 1);
      if (!waiting.get(edge.to)) ready.push(edge.to);
    });
  }
  // Цикла быть не может (стрелки проверяются при правке); если он есть — оставшиеся блоки по порядку создания.
  return order.concat(state.blocks.map((block) => block.key).filter((key) => !order.includes(key)));
}

/** Операции в порядке номеров: блоки по схеме, внутри блока — по порядку в нём. */
export function orderedOps(state: ManualState): ManualOp[] {
  const bySeq = new Map(state.ops.map((op) => [op.seq, op]));
  const byKey = new Map(state.blocks.map((block) => [block.key, block]));
  return blockOrder(state).flatMap((key) => byKey.get(key)!.ops.map((seq) => bySeq.get(seq)).filter((op): op is ManualOp => !!op));
}

/** Номер операции (1..N) по её seq. */
export function opPositions(state: ManualState): Map<number, number> {
  return new Map(orderedOps(state).map((op, index) => [op.seq, index + 1]));
}

/** Норма операции, минут на партию: подготовка + сек/шт × на изделие × партия (как сервер). */
export function opMinutes(op: ManualOp, partQty: number): number {
  const ok = (value: number | null, fallback: number) => (value == null || Number.isNaN(value) || value < 0 ? fallback : value);
  const batch = partQty > 0 ? partQty : 1;
  return (ok(num(op.prepMin), 0) * 60 + ok(num(op.opSec), 0) * ok(num(op.qty), 1) * batch) / 60;
}

/** Параметры операции генератора одной строкой для экрана: длины, зачистка, терминал, разъём, ТУТ, коакс — только то, что задано. */
export function genParams(gen: Record<string, unknown>): string {
  const parts: string[] = [];
  const side = str(gen.side) === "A" ? "А" : str(gen.side) === "B" ? "Б" : "";
  if (side) parts.push(`сторона ${side}`);
  const wires = list(gen.wires);
  if (wires.length) {
    const lengths = [...new Set(wires.map((wire) => str(wire.length)).filter((value) => value && value !== "0"))];
    parts.push(`${wires.length} пров.${lengths.length ? ` · L ${lengths.join(" / ")} мм` : ""}`);
  }
  const strip = gen.strip && typeof gen.strip === "object" ? gen.strip as Record<string, unknown> : null;
  if (strip) {
    // Пустая длина не показывается.
    const a = strip.a ? str(strip.lenA) : "", b = strip.b ? str(strip.lenB) : "";
    const ends = [a && `А ${a}`, b && `Б ${b}`].filter(Boolean);
    if (ends.length) parts.push(`зачистка ${ends.join(", ")} мм`);
  }
  const ep = gen.ep && typeof gen.ep === "object" ? gen.ep as Record<string, unknown> : null;
  if (ep) {
    if (str(ep.termArt)) parts.push(`терминал ${str(ep.termArt)}`);
    if (str(ep.connArt)) parts.push(`разъём ${str(ep.connArt)}`);
  }
  if (str(gen.tutArt)) parts.push(`ТУТ ${str(gen.tutArt)}${str(gen.tutCount) ? ` × ${str(gen.tutCount)}` : ""}`);
  if (str(gen.wrapArt)) parts.push(`обмотка ${str(gen.wrapArt)}${str(gen.wrapLen) ? ` ${str(gen.wrapLen)} мм` : ""}`);
  if (str(gen.sealArt)) parts.push(`герметик ${str(gen.sealArt)}`);
  const marks = list(gen.marks);
  if (marks.length) parts.push(`маркировка: ${marks.map((mark) => str(mark.art || mark.name)).filter(Boolean).join(", ")}`);
  if (gen.tin) parts.push("с лужением");
  if (Number(gen.units) > 1) parts.push(`${str(gen.units)} шт.`);
  return parts.join(" · ");
}

export function opTitle(op: ManualOp, position: number): string {
  return op.label.trim() || (op.kind === "custom" ? "Своя операция" : `Операция ${position}`);
}

/** Операции для схемы (в порядке номеров): номер, название, норма — из полей формы. */
export function routeOps(ops: ManualOp[], partQty: number): RouteOp[] {
  return ops.map((op, index) => ({
    position: index + 1, key: String(op.seq), type: op.gen ? str(op.gen.type) : op.kind === "library" ? LIBRARY_OP : CUSTOM_OP, code: op.code,
    label: opTitle(op, index + 1), minutes: opMinutes(op, partQty), number: index + 1,
  }));
}

/** Схема в номерах операций (то, что рисуется и уходит на сервер) и операции для неё. Блок без операций — с пустым списком. */
export function manualRoute(state: ManualState, partQty: number): { route: Route; ops: RouteOp[] } {
  const ordered = orderedOps(state);
  const position = new Map(ordered.map((op, index) => [op.seq, index + 1]));
  return {
    route: {
      blocks: state.blocks.map((block) => ({
        key: block.key, name: block.name, final: block.final, components: [],
        ops: block.ops.map((seq) => position.get(seq)).filter((value): value is number => !!value),
      })),
      edges: state.edges.map((edge) => ({ ...edge })),
    },
    ops: routeOps(ordered, partQty),
  };
}

/** Название блока на экране: своё; итоговый — наименование изделия; иначе — по последней операции, пустой — «Блок N». */
export function manualBlockTitle(state: ManualState, key: string, finalName = ""): string {
  const { route, ops } = manualRoute(state, 1);
  const block = route.blocks.find((item) => item.key === key);
  return block ? blockTitle(block, ops, finalName) : key;
}

/** Что не так со стрелками (цикл, блок не доходит до изделия, …); пустые блоки здесь не мешают — их ловит manualProblem. */
function edgeProblem(state: ManualState, finalName = ""): string | null {
  const { route, ops } = manualRoute(state, 1);
  // Пустому блоку — временная операция с его названием, чтобы проверка схемы смотрела только на стрелки.
  const extra: RouteOp[] = [];
  const blocks = route.blocks.map((block) => {
    if (block.ops.length) return block;
    const position = ops.length + extra.length + 1;
    extra.push({ position, key: `empty-${block.key}`, type: CUSTOM_OP, code: "", label: blockTitle(block, ops, finalName), minutes: 0 });
    return { ...block, ops: [position] };
  });
  return routeProblem({ blocks, edges: route.edges }, [...ops, ...extra], finalName);
}

export function selectBlock(state: ManualState, key: string): ManualState {
  return state.blocks.some((block) => block.key === key) ? { ...state, selected: key } : state;
}

/** Новый блок-полуфабрикат — вход выбранного блока (по умолчанию — итогового); выбирается сразу. */
export function addBlock(state: ManualState, name = ""): ManualState {
  const target = state.blocks.find((block) => block.key === state.selected) ?? state.blocks.find((block) => block.final);
  const key = freeKey(state.blocks);
  return {
    ...state, blocks: [...state.blocks, { key, name, final: false, ops: [] }],
    edges: target ? [...state.edges, { from: key, to: target.key, per_unit: 1 }] : state.edges, selected: key,
  };
}

/** «+» на блоке: новый пустой блок — вход блока `to`; он выбирается сразу. */
export function addInputBlock(state: ManualState, to: string): ManualState {
  if (!state.blocks.some((block) => block.key === to)) return state;
  return addBlock({ ...state, selected: to });
}

/** Блок без своего названия или с «Блок N» — его можно назвать по первой операции. */
export function hasDefaultName(block: Pick<ManualBlock, "name">): boolean {
  return !block.name.trim() || /^Блок \d+$/.test(block.name.trim());
}

/** Назвать блок по операции, если у него ещё название по умолчанию; своё название не трогаем. */
export function nameBlockAfter(state: ManualState, key: string, label: string): ManualState {
  const block = state.blocks.find((item) => item.key === key);
  if (!block || !label.trim() || !hasDefaultName(block)) return state;
  return renameBlock(state, key, label.trim());
}

/** Убрать блок вместе с его операциями; его входы переходят к блокам, которые он питал (штуки перемножаются). */
export function removeBlock(state: ManualState, key: string): ManualState {
  const gone = state.blocks.find((block) => block.key === key);
  if (!gone || gone.final) return state;
  const ins = state.edges.filter((edge) => edge.to === key), outs = state.edges.filter((edge) => edge.from === key);
  const edges = state.edges.filter((edge) => edge.to !== key && edge.from !== key).map((edge) => ({ ...edge }));
  ins.forEach((input) => outs.forEach((output) => {
    const same = edges.find((edge) => edge.from === input.from && edge.to === output.to);
    if (same) same.per_unit += input.per_unit * output.per_unit;
    else edges.push({ from: input.from, to: output.to, per_unit: input.per_unit * output.per_unit });
  }));
  const dropped = new Set(gone.ops);
  const selected = state.selected === key ? outs[0]?.to ?? state.blocks.find((block) => block.final)!.key : state.selected;
  return withDropped(
    { ...state, ops: state.ops.filter((op) => !dropped.has(op.seq)), blocks: state.blocks.filter((block) => block.key !== key), edges, selected },
    state.ops.filter((op) => dropped.has(op.seq)),
  );
}

/** Удалённые мастером операции из КД запоминаются, чтобы пересчёт по КД не вернул их. */
function withDropped(state: ManualState, removed: ManualOp[]): ManualState {
  const keys = removed.map((op) => op.origin).filter((origin): origin is string => !!origin);
  if (!keys.length) return state;
  return { ...state, kdDropped: [...new Set([...(state.kdDropped ?? []), ...keys])] };
}

export function renameBlock(state: ManualState, key: string, name: string): ManualState {
  return { ...state, blocks: state.blocks.map((block) => (block.key === key ? { ...block, name } : block)) };
}

/** Вид блока: итоговый — «итог»; заданный мастером; иначе по схеме — есть входы → сборка, нет → полуфабрикат. */
export function blockKind(state: Pick<ManualState, "blocks" | "edges">, key: string): BlockKind {
  const block = state.blocks.find((item) => item.key === key);
  if (!block || block.final) return "final";
  return block.kind ?? (state.edges.some((edge) => edge.to === key) ? "assembly" : "semiFinished");
}

function patchBlock(state: ManualState, key: string, patch: (block: ManualBlock) => ManualBlock): ManualState {
  return { ...state, blocks: state.blocks.map((block) => (block.key === key ? patch(block) : block)) };
}

/** Задать вид блока (итоговый не меняется — он всегда изделие). */
export function setBlockKind(state: ManualState, key: string, kind: Exclude<BlockKind, "final">): ManualState {
  return patchBlock(state, key, (block) => (block.final ? block : { ...block, kind }));
}

/** Штук в блоке (больше нуля); 1 не хранится. */
export function setBlockQuantity(state: ManualState, key: string, quantity: number): ManualState {
  if (!(quantity > 0) || !Number.isFinite(quantity)) return state;
  return patchBlock(state, key, ({ quantity: _old, ...block }) => (quantity === 1 ? block : { ...block, quantity }));
}

/** «Проверено мастером» — отметка на блоке. */
export function setBlockChecked(state: ManualState, key: string, checked: boolean): ManualState {
  return patchBlock(state, key, ({ checked: _old, ...block }) => (checked ? { ...block, checked: true } : block));
}

/** Блоки, которые можно сделать входом `key`: не итоговые, ещё не входы и не идущие после него. */
export function inputCandidates(state: ManualState, key: string): ManualBlock[] {
  const downstream = new Set<string>([key]);
  const queue = [key];
  while (queue.length) {
    const next = queue.shift()!;
    state.edges.filter((edge) => edge.from === next && !downstream.has(edge.to)).forEach((edge) => { downstream.add(edge.to); queue.push(edge.to); });
  }
  return state.blocks.filter((block) => !block.final && !downstream.has(block.key) && !state.edges.some((edge) => edge.from === block.key && edge.to === key));
}

export type EdgeEdit = { state: ManualState; problem: string | null };

function withEdges(state: ManualState, edges: RouteEdge[], finalName: string): EdgeEdit {
  const next = { ...state, edges };
  const problem = edgeProblem(next, finalName);
  return problem ? { state, problem } : { state: next, problem: null };
}

/** Добавить вход `from` в блок `to`; не годится (цикл и т. п.) — схема прежняя и слова почему. */
export function addInput(state: ManualState, from: string, to: string, finalName = ""): EdgeEdit {
  return withEdges(state, addEdge(manualRoute(state, 1).route, from, to).edges, finalName);
}

/** Убрать вход; блок, который после этого не доходит до изделия, — нельзя. */
export function removeInput(state: ManualState, from: string, to: string, finalName = ""): EdgeEdit {
  return withEdges(state, removeEdge(manualRoute(state, 1).route, from, to).edges, finalName);
}

export function setPerUnit(state: ManualState, from: string, to: string, perUnit: number): ManualState {
  return { ...state, edges: state.edges.map((edge) => (edge.from === from && edge.to === to ? { ...edge, per_unit: perUnit } : edge)) };
}

function withOp(state: ManualState, op: ManualOp, blockKey: string): ManualState {
  const key = state.blocks.some((block) => block.key === blockKey) ? blockKey : state.blocks.find((block) => block.final)!.key;
  return {
    ...state, ops: [...state.ops, op], nextSeq: Math.max(state.nextSeq, op.seq + 1),
    blocks: state.blocks.map((block) => (block.key === key ? { ...block, ops: [...block.ops, op.seq] } : block)),
  };
}

/** Карточка из библиотеки — в конец блока (по умолчанию выбранного). */
export function addLibraryOp(state: ManualState, card: LibraryCard, opRecs: OpRec[], blockKey = state.selected): ManualState {
  return withOp(state, libraryOp(state.nextSeq, card, opRecs), blockKey);
}

export function addCustomOp(state: ManualState, blockKey = state.selected): ManualState {
  return withOp(state, blank(state.nextSeq), blockKey);
}

/** Значение поля КД для сравнения (JSON: строки, комплектующие, поля генератора — одинаково). */
function kdRaw(op: Pick<ManualOp, KdField>, field: KdField): string {
  return JSON.stringify(op[field] ?? null);
}

/** Правка операции. У операции из КД поля, которые мастер поменял, отмечаются в `edited` (и запоминается их значение
    из КД), чтобы пересчёт по КД их больше не трогал. */
export function updateOp(state: ManualState, seq: number, patch: Partial<ManualOp>): ManualState {
  return { ...state, ops: state.ops.map((op) => (op.seq === seq ? markEdited(op, patch) : op)) };
}

function markEdited(op: ManualOp, patch: Partial<ManualOp>): ManualOp {
  const next = { ...op, ...patch };
  if (!op.origin) return next;
  const was = op.edited ?? [];
  const changed = KD_FIELDS.filter((field) => field in patch && !was.includes(field) && kdRaw(op, field) !== kdRaw(next, field));
  if (!changed.length) return next;
  const kdBase = { ...op.kdBase };
  changed.forEach((field) => { kdBase[field] ??= kdRaw(op, field); });
  return { ...next, edited: [...was, ...changed], kdBase };
}

export function removeOp(state: ManualState, seq: number): ManualState {
  return withDropped({
    ...state, ops: state.ops.filter((op) => op.seq !== seq),
    blocks: state.blocks.map((block) => ({ ...block, ops: block.ops.filter((item) => item !== seq) })),
  }, state.ops.filter((op) => op.seq === seq));
}

/** Сдвинуть операцию внутри её блока на `delta` мест (−1 — выше). */
export function moveOp(state: ManualState, seq: number, delta: number): ManualState {
  return {
    ...state,
    blocks: state.blocks.map((block) => {
      const from = block.ops.indexOf(seq);
      const to = from + delta;
      if (from < 0 || to < 0 || to >= block.ops.length) return block;
      const ops = [...block.ops];
      const [item] = ops.splice(from, 1);
      ops.splice(to, 0, item);
      return { ...block, ops };
    }),
  };
}

/** Значение поля КД словами для подсказки «было → стало»: строки как есть (до 60 знаков), комплектующие списком. */
export function kdText(field: KdField, raw: string | undefined): string {
  let value: unknown = null;
  try { value = raw == null ? null : JSON.parse(raw); } catch { value = raw; }
  let shown = "";
  if (field === "components") shown = list(value).map((comp) => [str(comp.art || comp.name), str(comp.qty), str(comp.unit)].filter(Boolean).join(" ")).join(", ");
  else if (field === "gen") shown = value && typeof value === "object" ? genParams(value as Record<string, unknown>) || "заданы" : "";
  else shown = str(value).split("\n").map((line) => line.trim()).filter(Boolean).join(" / ");
  if (!shown) return "пусто";
  return shown.length > 60 ? `${shown.slice(0, 59)}…` : shown;
}

/** «Название: в КД было «Резка» → стало «Резка с зачисткой»» — подсказка на операции. */
export function kdChangeText(change: KdChange): string {
  return `${KD_FIELD_NAMES[change.field]}: в КД было «${change.was}» → стало «${change.now}»`;
}

export const NEW_FROM_KD = "Новое из КД";

/** Операция из КД с полями КД `kd`: поля, которые мастер не правил, берутся из КД; правленные остаются его, только
    запоминается новое значение КД. Что поменялось — в `kdChanges` (по полю, последнее изменение). Ничего — та же операция. */
function refreshOp(op: ManualOp, kd: ManualOp): ManualOp {
  const edited = op.edited ?? [];
  const changes: KdChange[] = [];
  const next: ManualOp = { ...op, kind: kd.kind, templateId: kd.templateId, code: kd.code, side: kd.side };
  const kdBase = { ...op.kdBase };
  for (const field of KD_FIELDS) {
    const now = kdRaw(kd, field);
    if (edited.includes(field)) {
      const base = kdBase[field];
      if (base !== undefined && base !== now) changes.push({ field, was: kdText(field, base), now: kdText(field, now) });
      kdBase[field] = now;
      continue;
    }
    const current = kdRaw(op, field);
    if (current === now) continue;
    changes.push({ field, was: kdText(field, current), now: kdText(field, now) });
    if (kd[field] === undefined) delete next[field];
    else (next as Record<KdField, unknown>)[field] = structuredClone(kd[field]);
  }
  const sameMeta = next.kind === op.kind && next.templateId === op.templateId && next.code === op.code && next.side === op.side;
  if (!changes.length && !op.kdGone && sameMeta && JSON.stringify(kdBase) === JSON.stringify(op.kdBase ?? {})) return op;
  delete next.kdGone;
  if (edited.length) next.kdBase = kdBase;
  if (changes.length) {
    const fields = new Set(changes.map((change) => change.field));
    next.kdChanges = [...(op.kdChanges ?? []).filter((change) => !fields.has(change.field)), ...changes];
  }
  return next;
}

/** Куда в блоке встаёт новая операция из КД: после ближайшей предыдущей по КД, иначе перед ближайшей следующей, иначе в конец. */
function insertAt(blockOps: number[], siblings: Array<string | undefined>, at: number, originOf: Map<number, string | undefined>, seq: number): number[] {
  const index = (origin: string | undefined) => (origin ? blockOps.findIndex((item) => originOf.get(item) === origin) : -1);
  for (let back = at - 1; back >= 0; back -= 1) {
    const found = index(siblings[back]);
    if (found >= 0) return [...blockOps.slice(0, found + 1), seq, ...blockOps.slice(found + 1)];
  }
  for (let ahead = at + 1; ahead < siblings.length; ahead += 1) {
    const found = index(siblings[ahead]);
    if (found >= 0) return [...blockOps.slice(0, found), seq, ...blockOps.slice(found)];
  }
  return [...blockOps, seq];
}

/**
 * Пересчёт схемы мастера по новой схеме из КД (`derived`) — правило слияния:
 * 1) нового ключа КД ещё нет — операция добавляется в блок, который предлагает КД (тот же ключ блока; итог — в итог); если
 *    мастер этот блок убрал или переделал — в блок «Новое из КД» (создаётся входом итога); удалённые мастером не возвращаются;
 * 2) ключ есть — поля КД обновляются, кроме правленных мастером (мастер главнее); что поменялось — подсказка «было → стало»;
 * 3) блоки мастера, их порядок, операции в них и стрелки не трогаются (только добавляются новые);
 * 4) ключа больше нет в КД — операция остаётся с отметкой «нет в КД»;
 * 5) операции без ключа КД (свои у мастера) не трогаются.
 */
export function rederive(manual: ManualState, derived: ManualState): ManualState {
  const fresh = new Map<string, ManualOp>();
  derived.ops.forEach((op) => { if (op.origin && !fresh.has(op.origin)) fresh.set(op.origin, op); });
  let state: ManualState = {
    ...manual,
    ops: manual.ops.map((op) => {
      if (!op.origin) return op;
      const kd = fresh.get(op.origin);
      if (!kd) return op.kdGone ? op : { ...op, kdGone: true };
      return refreshOp(op, kd);
    }),
  };
  const known = new Set(manual.ops.map((op) => op.origin).filter(Boolean));
  const dropped = new Set(manual.kdDropped ?? []);
  const ownerOf = new Map(derived.blocks.flatMap((block) => block.ops.map((seq) => [seq, block] as const)));
  const derivedOrigin = new Map(derived.ops.map((op) => [op.seq, op.origin]));
  for (const kdOp of orderedOps(derived)) {
    const origin = kdOp.origin;
    if (!origin || known.has(origin) || dropped.has(origin)) continue;
    known.add(origin);
    const from = ownerOf.get(kdOp.seq);
    const finalKey = state.blocks.find((block) => block.final)!.key;
    let target = from?.final ? finalKey : state.blocks.find((block) => from && block.key === from.key && !block.final)?.key;
    if (!target) {
      let spare = state.blocks.find((block) => !block.final && block.name === NEW_FROM_KD);
      if (!spare) {
        spare = { key: freeKey(state.blocks), name: NEW_FROM_KD, final: false, ops: [] };
        state = { ...state, blocks: [...state.blocks, spare], edges: [...state.edges, { from: spare.key, to: finalKey, per_unit: 1 }] };
      }
      target = spare.key;
    }
    const seq = state.nextSeq;
    const { edited: _e, kdBase: _b, kdChanges: _c, kdGone: _g, ...clean } = structuredClone(kdOp);
    const originOf = new Map(state.ops.map((op) => [op.seq, op.origin]));
    const siblings = (from?.ops ?? []).map((item) => derivedOrigin.get(item));
    const place = (from?.ops ?? []).indexOf(kdOp.seq);
    state = {
      ...state, ops: [...state.ops, { ...clean, seq }], nextSeq: seq + 1,
      blocks: state.blocks.map((block) => (block.key === target ? { ...block, ops: insertAt(block.ops, siblings, place, originOf, seq) } : block)),
    };
  }
  return state;
}

/** Состав КБ на операции: отметить строку — со своим количеством из состава, снять — убрать. */
export function toggleComponent(state: ManualState, seq: number, comp: Comp): ManualState {
  const op = state.ops.find((item) => item.seq === seq);
  if (!op) return state;
  const has = op.components.some((item) => item.id === comp.id);
  const unit = /провод|кабел|wire|cable/i.test(comp.type || "") ? "М." : "Шт.";
  return updateOp(state, seq, {
    components: has ? op.components.filter((item) => item.id !== comp.id)
      : [...op.components, { id: comp.id, art: comp.art || comp.name, name: comp.name, qty: String(comp.qty ?? "1").replace(".", ","), unit }],
  });
}

/** Старый список без схемы: по блоку на операцию цепочкой, последняя — изделие. Операция из `together` («одновременно с
    предыдущей», флаг до 05.10) начинает свою ветку без входа; следующая обычная забирает все открытые ветки. */
export function chainRoute(ops: RouteOp[], together: ReadonlySet<number> = new Set()): Route {
  const key = (op: RouteOp) => `b${op.position}`;
  const edges: Route["edges"] = [];
  let heads: string[] = [];
  ops.forEach((op, index) => {
    if (index > 0 && together.has(op.position)) { heads.push(key(op)); return; }
    heads.forEach((from) => edges.push({ from, to: key(op), per_unit: 1 }));
    heads = [key(op)];
  });
  return {
    blocks: ops.map((op, index) => ({ key: key(op), name: "", ops: [op.position], final: index === ops.length - 1, components: [] })),
    edges,
  };
}

/** Что мешает собрать, словами для технолога (сервер проверяет то же); null — можно собирать. */
export function manualProblem(state: ManualState, finalName = ""): string | null {
  const ordered = orderedOps(state);
  if (!ordered.length) return "Ручная сборка: добавьте хотя бы одну операцию — из библиотеки или свою.";
  for (const [index, op] of ordered.entries()) {
    const position = index + 1;
    if (op.kind === "custom" && !op.label.trim()) return `Ручная сборка: операция ${position} — укажите название своей операции.`;
    const named = `Ручная сборка: операция ${position} «${opTitle(op, position)}»`;
    const qty = num(op.qty);
    if (qty != null && !(qty > 0)) return `${named} — количество на изделие должно быть числом больше нуля.`;
    const prep = num(op.prepMin);
    if (prep != null && !(prep >= 0)) return `${named} — подготовка должна быть числом минут не меньше нуля.`;
    const sec = num(op.opSec);
    if (sec != null && !(sec >= 0)) return `${named} — время на штуку должно быть числом секунд не меньше нуля.`;
    for (const comp of op.components) {
      const amount = num(comp.qty);
      if (amount != null && !(amount > 0)) return `${named} — количество «${comp.art || comp.name}» должно быть числом больше нуля.`;
    }
  }
  const known = new Set(state.ops.map((op) => op.seq));
  const empty = state.blocks.find((block) => !block.ops.some((seq) => known.has(seq)));
  if (empty) return `Ручная сборка: блок «${manualBlockTitle(state, empty.key, finalName)}» без операций — добавьте операцию или удалите блок.`;
  const { route, ops } = manualRoute(state, 1);
  const wrong = routeProblem(route, ops, finalName);
  return wrong ? `Схема сборки: ${wrong}` : null;
}

const lines = (value: string) => value.split("\n").map((line) => line.trim()).filter(Boolean);
const seconds = (minutes: string) => {
  const value = num(minutes);
  return value == null || Number.isNaN(value) ? "" : String(Math.round(value * 60 * 100) / 100);
};
const plain = (value: string) => {
  const number = num(value);
  return number == null || Number.isNaN(number) ? value.trim() : String(number);
};

/** Ход своей операции: шаги, затем контроль (допуск — после «|»). */
export function stepsPayload(op: ManualOp) {
  return [
    ...lines(op.steps).map((line) => ({ kind: "Операция", text: line, tolerance: "" })),
    ...lines(op.controls).map((line) => {
      const [what, ...rest] = line.split("|");
      return { kind: "Контроль", text: what.trim(), tolerance: rest.join("|").trim() };
    }).filter((step) => step.text),
  ];
}

/** Оснастка своей операции: «Раздел: название», без известного раздела — инструмент. */
export function toolingPayload(op: ManualOp) {
  return lines(op.tooling).map((line) => {
    const match = /^([^:]{3,20}):\s*(.+)$/.exec(line);
    const section = match ? TOOL_SECTIONS.find((item) => item.toLowerCase() === match[1].trim().toLowerCase()) : undefined;
    return section && match ? { section, name: match[2].trim() } : { section: TOOL_SECTIONS[0], name: line };
  });
}

/** Тело сборки ручного режима: операции в порядке номеров + схема (всегда ручная; на блоке — комплектующие его операций). */
export function manualConfig(state: ManualState, base: Record<string, unknown>): Record<string, unknown> {
  const ordered = orderedOps(state);
  const ops = ordered.map((op, index) => {
    const common = {
      seq: op.seq, label: op.label.trim() || opTitle(op, index + 1), qty: plain(op.qty) || "1",
      tPrep: seconds(op.prepMin), tOp: plain(op.opSec),
      ...(op.params.trim() ? { params: op.params.trim() } : {}),
      ...(op.note.trim() ? { note: op.note.trim() } : {}),
      ...(op.result.trim() ? { result: op.result.trim() } : {}),
      ...(op.components.length ? { components: op.components.map((comp) => ({ ...comp, qty: plain(comp.qty) || "1" })) } : {}),
    };
    // Операция генератора: свои поля как есть, правятся номер, название и норма; «на изделие» у неё нет.
    if (op.gen) {
      return { ...op.gen, seq: op.seq, label: op.label.trim() || opTitle(op, index + 1), tPrep: seconds(op.prepMin), tOp: plain(op.opSec), ...kdPayload(op) };
    }
    return op.kind === "library"
      ? { type: LIBRARY_OP, templateId: op.templateId, opNum: op.code, side: op.side, ...common, ...kdPayload(op) }
      : { type: CUSTOM_OP, ...common, steps: stepsPayload(op), tooling: toolingPayload(op), ...kdPayload(op) };
  });
  const { route: scheme } = manualRoute(state, Number(base.partQty) || 1);
  const route = routePayload({
    ...scheme,
    blocks: scheme.blocks.map((block) => ({
      ...block,
      components: block.ops.flatMap((position) => (ordered[position - 1]?.components || []).map((comp) => ({ id: comp.id, art: comp.art, name: comp.name, qty: plain(comp.qty) || "1" }))),
    })),
  });
  // Вид, штуки, объекты КД и «проверено мастером» — на блоке схемы, только заданные (сервер лишние ключи блока не читает).
  const own = new Map(state.blocks.map((block) => [block.key, block]));
  const blocks = (route.blocks as Array<Record<string, unknown>>).map((block) => {
    const item = own.get(String(block.key));
    if (!item) return block;
    return {
      ...block,
      ...(item.kind && !item.final ? { kind: item.kind } : {}),
      ...(item.quantity != null && item.quantity !== 1 ? { quantity: item.quantity } : {}),
      ...(item.refs?.length ? { refs: [...item.refs] } : {}),
      ...(item.checked ? { checked: true } : {}),
    };
  });
  return {
    ...state.extra, mode: MANUAL_MODE, assemblyIndex: base.assemblyIndex, assemblyName: base.assemblyName, partQty: base.partQty, ops,
    route: { ...route, blocks }, ...(state.kdDropped?.length ? { kdDropped: [...state.kdDropped] } : {}),
  };
}

/** Связь операции с КД в конфиг — только заданное. */
function kdPayload(op: ManualOp): Record<string, unknown> {
  return {
    ...(op.origin ? { origin: op.origin } : {}),
    ...(op.edited?.length ? { edited: [...op.edited] } : {}),
    ...(op.kdBase && Object.keys(op.kdBase).length ? { kdBase: { ...op.kdBase } } : {}),
    ...(op.kdChanges?.length ? { kdChanges: op.kdChanges.map((change) => ({ ...change })) } : {}),
    ...(op.kdGone ? { kdGone: true } : {}),
  };
}

const isKdField = (value: unknown): value is KdField => (KD_FIELDS as readonly unknown[]).includes(value);

/** Связь операции с КД из сохранённого конфига: непонятное отбрасывается, старые конфиги без неё — без изменений. */
function kdFromConfig(item: Record<string, unknown>): Partial<ManualOp> {
  const origin = str(item.origin).trim();
  if (!origin) return {};
  const edited = [...new Set(Array.isArray(item.edited) ? item.edited.filter(isKdField) : [])];
  const rawBase = item.kdBase && typeof item.kdBase === "object" ? item.kdBase as Record<string, unknown> : {};
  const kdBase = Object.fromEntries(Object.entries(rawBase).filter(([field, value]) => isKdField(field) && typeof value === "string")) as Partial<Record<KdField, string>>;
  const kdChanges = list(item.kdChanges).filter((change) => isKdField(change.field))
    .map((change) => ({ field: change.field as KdField, was: str(change.was), now: str(change.now) }));
  return {
    origin,
    ...(edited.length ? { edited } : {}),
    ...(Object.keys(kdBase).length ? { kdBase } : {}),
    ...(kdChanges.length ? { kdChanges } : {}),
    ...(item.kdGone === true ? { kdGone: true } : {}),
  };
}

/** Необязательное блока из сохранённой схемы (по ключу блока): вид, штуки, объекты КД, «проверено мастером». */
function blockFromConfig(raw: Record<string, unknown> | undefined): Partial<ManualBlock> {
  if (!raw) return {};
  const quantity = Number(raw.quantity);
  const refs = [...new Set(Array.isArray(raw.refs) ? raw.refs.filter((ref): ref is string => typeof ref === "string" && !!ref.trim()) : [])];
  return {
    ...(raw.kind === "semiFinished" || raw.kind === "assembly" ? { kind: raw.kind } : {}),
    ...(Number.isFinite(quantity) && quantity > 0 && quantity !== 1 ? { quantity } : {}),
    ...(refs.length ? { refs } : {}),
    ...(raw.checked === true ? { checked: true } : {}),
  };
}

function str(value: unknown): string {
  return value == null ? "" : String(value);
}

function list(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") as Array<Record<string, unknown>> : [];
}

/** Схема ручной сборки из сохранённого `build_config` карты (режим запоминается за КБ); не ручная — null. Сохранённая схема
    — блоками; без схемы (или она не сходится со списком) — старая цепочка по списку с отметками «одновременно с предыдущей». */
export function manualFromConfig(config: unknown): ManualState | null {
  const saved = config && typeof config === "object" ? config as Record<string, unknown> : null;
  if (!saved || saved.mode !== MANUAL_MODE) return null;
  const used = new Set<number>();
  const items = list(saved.ops);
  const ops = items.map((item, index): ManualOp => {
    let seq = Number(item.seq);
    if (!Number.isInteger(seq) || seq <= 0 || used.has(seq)) seq = Math.max(index + 1, ...used) + 1;
    used.add(seq);
    const steps = list(item.steps);
    const control = (step: Record<string, unknown>) => /контрол/i.test(str(step.kind));
    const prep = num(item.tPrep);
    const kindOf = str(item.type);
    if (kindOf && kindOf !== LIBRARY_OP && kindOf !== CUSTOM_OP) {
      const { seq: _seq, label: _label, tPrep: _tPrep, tOp: _tOp, origin: _o, edited: _e, kdBase: _b, kdChanges: _c, kdGone: _g, ...rest } = item;
      return {
        ...blank(seq), kind: "custom", templateId: str(item.templateId), code: str(item.opNum), side: str(item.side),
        label: str(item.label) || opTypeName(kindOf), qty: "1",
        prepMin: prep == null || Number.isNaN(prep) ? "" : text(prep / 60), opSec: str(item.tOp).replace(".", ","), gen: rest,
        ...kdFromConfig(item),
      };
    }
    return {
      ...blank(seq), kind: item.type === LIBRARY_OP ? "library" : "custom", templateId: str(item.templateId), code: str(item.opNum),
      side: str(item.side), label: str(item.label), qty: str(item.qty || "1").replace(".", ","),
      prepMin: prep == null || Number.isNaN(prep) ? "" : text(prep / 60), opSec: str(item.tOp).replace(".", ","),
      params: str(item.params), note: str(item.note), result: str(item.result),
      steps: steps.filter((step) => !control(step)).map((step) => str(step.text)).join("\n"),
      controls: steps.filter(control).map((step) => [str(step.text), str(step.tolerance)].filter(Boolean).join(" | ")).join("\n"),
      tooling: list(item.tooling).map((tool) => (str(tool.section) && str(tool.section) !== TOOL_SECTIONS[0] ? `${str(tool.section)}: ${str(tool.name)}` : str(tool.name))).join("\n"),
      components: list(item.components).map((comp) => ({ id: str(comp.id), art: str(comp.art), name: str(comp.name), qty: str(comp.qty || "1").replace(".", ","), unit: str(comp.unit) || "Шт." })),
      ...kdFromConfig(item),
    };
  });
  const nextSeq = Math.max(0, ...ops.map((op) => op.seq)) + 1;
  const { mode: _mode, ops: _ops, route: _route, assemblyIndex: _index, assemblyName: _name, partQty: _qty, materialsSheet: _sheet, kdDropped: _dropped, ...extra } = saved;
  const withExtra = Object.keys(extra).length ? { extra } : {};
  const droppedKeys = [...new Set(Array.isArray(saved.kdDropped) ? saved.kdDropped.filter((key): key is string => typeof key === "string" && !!key.trim()) : [])];
  const withDroppedKeys = droppedKeys.length ? { kdDropped: droppedKeys } : {};
  if (!ops.length) return { ...emptyManual(), nextSeq, ...withExtra, ...withDroppedKeys };
  const positioned = routeOps(ops, Number(saved.partQty) || 1);
  const parsed = parseRoute(saved.route);
  const together = new Set(items.flatMap((item, index) => ((item.parallel === true || item.link === "parallel") && index > 0 ? [index + 1] : [])));
  const fromSaved = !!parsed && !routeProblem(parsed, positioned);
  const route = fromSaved ? parsed! : chainRoute(positioned, together);
  const rawBlocks = new Map(fromSaved ? list((saved.route as Record<string, unknown>).blocks).map((block) => [str(block.key).trim(), block] as const) : []);
  const blocks: ManualBlock[] = route.blocks.map((block) => {
    const { kind, ...meta } = blockFromConfig(rawBlocks.get(block.key));
    return {
      key: block.key, name: block.name, final: block.final, ops: [...block.ops].sort((a, b) => a - b).map((position) => ops[position - 1].seq),
      ...(kind && !block.final ? { kind } : {}), ...meta,
    };
  });
  return { ops, nextSeq, blocks, edges: route.edges.map((edge) => ({ ...edge })), selected: blocks.find((block) => block.final)!.key, ...withExtra, ...withDroppedKeys };
}
