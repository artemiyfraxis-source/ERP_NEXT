/* «Ручная сборка» (владелец 04.10) — четвёртый режим генератора для кастомных и нетиповых проводов: технолог сам
   набирает список операций — карточки из библиотеки и свои (название, норма, ход операции, контроль с допуском,
   оснастка), а схема полуфабрикатов из них — по блоку на операцию цепочкой, правится как обычно и уходит на сервер
   всегда как ручная (`build_config.route`), чтобы производство шло по ней. Сервер — `etk_builder._build_manual`. */

import type { OperationCardSummary } from "./api";
import { recordCategory } from "./etkOperation";
import { parseRoute, routePayload, routeProblem, type Route, type RouteBlock, type RouteOp } from "./routeLogic";
import type { Comp, OpRec } from "./tcGenLogic";

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
};

/** route: ручная правка схемы (null — цепочка по списку); notice — почему правки схемы сброшены. */
export type ManualState = { ops: ManualOp[]; nextSeq: number; route: Route | null; notice: string };

export const emptyManual = (): ManualState => ({ ops: [], nextSeq: 1, route: null, notice: "" });

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

const freeKey = (blocks: RouteBlock[]) => `b${Math.max(0, ...blocks.map((block) => Number(/^b(\d+)$/.exec(block.key)?.[1] ?? 0))) + 1}`;

function sortBlocks(route: Route): Route {
  const first = (block: RouteBlock) => (block.ops.length ? Math.min(...block.ops) : 0);
  return {
    blocks: route.blocks.map((block) => ({ ...block, ops: [...block.ops].sort((a, b) => a - b) }))
      .sort((a, b) => first(a) - first(b) || a.key.localeCompare(b.key)),
    edges: route.edges,
  };
}

/** Схема, правленная под старый список, — под новый: операции в блоках идут за своей операцией (по seq); блок, оставшийся
    без операций, убирается, его входы переходят к его выходам; новая операция — новым итоговым блоком после прежнего.
    Не сходится — null (цепочка по списку). */
function remapRoute(route: Route | null, before: ManualOp[], after: ManualOp[]): { route: Route | null; reset: boolean } {
  if (!route) return { route: null, reset: false };
  const position = new Map(after.map((op, index) => [op.seq, index + 1]));
  let blocks: RouteBlock[] = route.blocks.map((block) => ({
    ...block, ops: block.ops.map((pos) => position.get(before[pos - 1]?.seq ?? -1)).filter((pos): pos is number => !!pos),
  }));
  let edges = route.edges.map((edge) => ({ ...edge }));
  for (const gone of blocks.filter((block) => !block.ops.length)) {
    const ins = edges.filter((edge) => edge.to === gone.key), outs = edges.filter((edge) => edge.from === gone.key);
    edges = edges.filter((edge) => edge.to !== gone.key && edge.from !== gone.key);
    ins.forEach((input) => outs.forEach((output) => {
      const same = edges.find((edge) => edge.from === input.from && edge.to === output.to);
      if (same) same.per_unit += input.per_unit * output.per_unit;
      else edges.push({ from: input.from, to: output.to, per_unit: input.per_unit * output.per_unit });
    }));
    blocks = blocks.filter((block) => block.key !== gone.key);
    if (gone.final && ins.length === 1) blocks = blocks.map((block) => (block.key === ins[0].from ? { ...block, final: true } : block));
  }
  const covered = new Set(blocks.flatMap((block) => block.ops));
  after.forEach((_, index) => {
    if (covered.has(index + 1)) return;
    const previous = blocks.find((block) => block.final);
    const key = freeKey(blocks);
    blocks = blocks.map((block) => (block.final ? { ...block, final: false } : block)).concat({ key, name: "", ops: [index + 1], final: true, components: [] });
    if (previous) edges.push({ from: previous.key, to: key, per_unit: 1 });
  });
  const next = sortBlocks({ blocks, edges });
  if (!after.length || routeProblem(next, routeOps(after, 1))) return { route: null, reset: true };
  return { route: next, reset: false };
}

function withOps(state: ManualState, ops: ManualOp[], nextSeq = state.nextSeq): ManualState {
  const { route, reset } = remapRoute(state.route, state.ops, ops);
  return { ops, nextSeq, route, notice: reset ? "Список операций изменился — схема снова цепочкой по списку, ручные правки схемы сброшены." : state.notice };
}

export function addLibraryOp(state: ManualState, card: LibraryCard, opRecs: OpRec[]): ManualState {
  return withOps(state, [...state.ops, libraryOp(state.nextSeq, card, opRecs)], state.nextSeq + 1);
}

export function addCustomOp(state: ManualState): ManualState {
  return withOps(state, [...state.ops, blank(state.nextSeq)], state.nextSeq + 1);
}

export function updateOp(state: ManualState, seq: number, patch: Partial<ManualOp>): ManualState {
  return { ...state, ops: state.ops.map((op) => (op.seq === seq ? { ...op, ...patch } : op)) };
}

export function removeOp(state: ManualState, seq: number): ManualState {
  return withOps(state, state.ops.filter((op) => op.seq !== seq));
}

/** Сдвинуть операцию на `delta` мест (−1 — выше). Ручная схема остаётся при своих блоках. */
export function moveOp(state: ManualState, seq: number, delta: number): ManualState {
  const from = state.ops.findIndex((op) => op.seq === seq);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= state.ops.length) return state;
  const ops = [...state.ops];
  const [item] = ops.splice(from, 1);
  ops.splice(to, 0, item);
  return withOps(state, ops);
}

export function setRoute(state: ManualState, route: Route | null): ManualState {
  return { ...state, route, notice: "" };
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

/** Норма операции, минут на партию: подготовка + сек/шт × на изделие × партия (как сервер). */
export function opMinutes(op: ManualOp, partQty: number): number {
  const ok = (value: number | null, fallback: number) => (value == null || Number.isNaN(value) || value < 0 ? fallback : value);
  const batch = partQty > 0 ? partQty : 1;
  return (ok(num(op.prepMin), 0) * 60 + ok(num(op.opSec), 0) * ok(num(op.qty), 1) * batch) / 60;
}

export function opTitle(op: ManualOp, position: number): string {
  return op.label.trim() || (op.kind === "custom" ? "Своя операция" : `Операция ${position}`);
}

/** Операции для схемы: номер = место в списке, норма — из полей формы. */
export function routeOps(ops: ManualOp[], partQty: number): RouteOp[] {
  return ops.map((op, index) => ({
    position: index + 1, key: String(op.seq), type: op.kind === "library" ? LIBRARY_OP : CUSTOM_OP, code: op.code,
    label: opTitle(op, index + 1), minutes: opMinutes(op, partQty), number: index + 1,
  }));
}

/** Схема без правок: по блоку на операцию, цепочкой по списку, последняя — изделие. */
export function chainRoute(ops: RouteOp[]): Route {
  return {
    blocks: ops.map((op, index) => ({ key: `b${op.position}`, name: "", ops: [op.position], final: index === ops.length - 1, components: [] })),
    edges: ops.slice(1).map((op, index) => ({ from: `b${ops[index].position}`, to: `b${op.position}`, per_unit: 1 })),
  };
}

/** Что мешает собрать список, словами для технолога (сервер проверяет то же); null — можно собирать. */
export function manualProblem(state: ManualState): string | null {
  if (!state.ops.length) return "Ручная сборка: добавьте хотя бы одну операцию — из библиотеки или свою.";
  for (const [index, op] of state.ops.entries()) {
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
  return null;
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

/** Тело сборки ручного режима: операции списком + схема (всегда ручная; на блоке — комплектующие его операций). */
export function manualConfig(state: ManualState, base: Record<string, unknown>): Record<string, unknown> {
  const partQty = Number(base.partQty) || 1;
  const ops = state.ops.map((op, index) => {
    const common = {
      seq: op.seq, label: op.label.trim() || opTitle(op, index + 1), qty: plain(op.qty) || "1",
      tPrep: seconds(op.prepMin), tOp: plain(op.opSec),
      ...(op.params.trim() ? { params: op.params.trim() } : {}),
      ...(op.note.trim() ? { note: op.note.trim() } : {}),
      ...(op.result.trim() ? { result: op.result.trim() } : {}),
      ...(op.components.length ? { components: op.components.map((comp) => ({ ...comp, qty: plain(comp.qty) || "1" })) } : {}),
    };
    return op.kind === "library"
      ? { type: LIBRARY_OP, templateId: op.templateId, opNum: op.code, side: op.side, ...common }
      : { type: CUSTOM_OP, ...common, steps: stepsPayload(op), tooling: toolingPayload(op) };
  });
  const scheme = state.route ?? chainRoute(routeOps(state.ops, partQty));
  const route = routePayload({
    ...scheme,
    blocks: scheme.blocks.map((block) => ({
      ...block,
      components: block.ops.flatMap((position) => (state.ops[position - 1]?.components || []).map((comp) => ({ id: comp.id, art: comp.art, name: comp.name, qty: plain(comp.qty) || "1" }))),
    })),
  });
  return { mode: MANUAL_MODE, assemblyIndex: base.assemblyIndex, assemblyName: base.assemblyName, partQty: base.partQty, ops, route };
}

function str(value: unknown): string {
  return value == null ? "" : String(value);
}

function list(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") as Array<Record<string, unknown>> : [];
}

/** Список ручной сборки из сохранённого `build_config` карты (режим запоминается за КБ); не ручная — null. */
export function manualFromConfig(config: unknown): ManualState | null {
  const saved = config && typeof config === "object" ? config as Record<string, unknown> : null;
  if (!saved || saved.mode !== MANUAL_MODE) return null;
  const used = new Set<number>();
  const ops = list(saved.ops).map((item, index): ManualOp => {
    let seq = Number(item.seq);
    if (!Number.isInteger(seq) || seq <= 0 || used.has(seq)) seq = Math.max(index + 1, ...used) + 1;
    used.add(seq);
    const steps = list(item.steps);
    const control = (step: Record<string, unknown>) => /контрол/i.test(str(step.kind));
    const prep = num(item.tPrep);
    return {
      ...blank(seq), kind: item.type === LIBRARY_OP ? "library" : "custom", templateId: str(item.templateId), code: str(item.opNum),
      side: str(item.side), label: str(item.label), qty: str(item.qty || "1").replace(".", ","),
      prepMin: prep == null || Number.isNaN(prep) ? "" : text(prep / 60), opSec: str(item.tOp).replace(".", ","),
      params: str(item.params), note: str(item.note), result: str(item.result),
      steps: steps.filter((step) => !control(step)).map((step) => str(step.text)).join("\n"),
      controls: steps.filter(control).map((step) => [str(step.text), str(step.tolerance)].filter(Boolean).join(" | ")).join("\n"),
      tooling: list(item.tooling).map((tool) => (str(tool.section) && str(tool.section) !== TOOL_SECTIONS[0] ? `${str(tool.section)}: ${str(tool.name)}` : str(tool.name))).join("\n"),
      components: list(item.components).map((comp) => ({ id: str(comp.id), art: str(comp.art), name: str(comp.name), qty: str(comp.qty || "1").replace(".", ","), unit: str(comp.unit) || "Шт." })),
    };
  });
  const parsed = parseRoute(saved.route);
  const ops1 = routeOps(ops, Number(saved.partQty) || 1);
  const chain = JSON.stringify(routePayload(chainRoute(ops1)));
  const plainRoute = parsed ? { ...parsed, blocks: parsed.blocks.map((block) => ({ ...block, components: [] })) } : null;
  const route = plainRoute && !routeProblem(plainRoute, ops1) && JSON.stringify(routePayload(plainRoute)) !== chain ? plainRoute : null;
  return { ops, nextSeq: Math.max(0, ...ops.map((op) => op.seq)) + 1, route, notice: "" };
}
