// Поля строки БД.ОП на карточке операции: лист хранит ~25 колонок под своими заголовками (регистр и сокращения как на
// листе), экран показывает нужные, сгруппированными и по-русски. Скрытие — только в интерфейсе: в `fields` остаётся
// каждый ключ, неизвестная колонка попадает в «Прочее» и не пропадает.
import { cleanHeader, headerLabel } from "./referenceLabels";

/** Колонки БД.ОП, которые схема дублирует под своими ключами (opNumber = «Номер» …): показываются один раз, по листу. */
export const OP_SCHEMA_KEYS = new Set(["opNumber", "opName", "tOp", "tPrep", "tMachine"]);

export type OpGroupId = "ident" | "equip" | "norms" | "nodes" | "other" | "raw";
export type OpField = {
  key: string; label: string; value: string; readOnly?: boolean; unit?: string; opType?: boolean; hint?: string;
  /** Показывать и пустым (узлы станка «Погонной» операции). */
  keep?: boolean;
};
export type OpGroup = { id: OpGroupId; title: string; fields: OpField[]; note?: string };

export const OP_GROUP_TITLES: Record<OpGroupId, string> = {
  ident: "Идентификация", equip: "Оборудование и оснастка", norms: "Нормы времени", nodes: "Узлы станка и ручные работы",
  other: "Прочее", raw: "Исходные данные расчёта (ERP не использует)",
};
const ORDER: OpGroupId[] = ["ident", "equip", "norms", "nodes", "other", "raw"];
const LABEL_ORDER = [
  "Код", "Название", "Профессия", "Разряд", "Машина", "Инструмент", "Апликаторы / модули", "Тип операции", "Время операции", "Время подготовки",
  "Время машины", "Скорость проката", "Скорость работы инструмента", "Кол-во операций инструмента",
  "Ручные работы: взять ПФ", "Ручные работы", "Ручные работы: снять ПФ", "Расход на настройку",
];

/** Типы операции листа БД.ОП и что они значат для времени. */
export const OP_TYPES: Array<{ value: string; hint: string }> = [
  { value: "Погонный", hint: "по длине провода" },
  { value: "Переменный", hint: "по количеству, время зависит от изделия" },
  { value: "Статичный", hint: "фиксированное на штуку" },
];
export const opTypeHint = (type: string) => OP_TYPES.find((item) => item.value === type.trim())?.hint ?? "";

const ROLL_HINT = "Подача провода: время подачи = длина / скорость. В листе подписано «сек/м», считается как м/с.";
export const NODES_NOTE = "Время — большее из ручных работ (взять + работы + снять ПФ) и машины (ход инструмента × ходов; у погонной ещё длина / прокат).";

// Колонки, которые не заполнены ни в одной строке или не несут смысла (владелец 14.10): на экране скрыты.
const DEAD_EXACT = new Set(["программа", "провод", "разъем", "разъём", "инструкция", "отказы", "полуфабрикат"]);
const DEAD_PREFIX = ["время доп"];
// Заполнены, но расчёт ERP их не читает (цены, «Время Чел»): одна свёрнутая группа.
const RAW_PREFIX = ["время чел", "уд.цена"];
// Узлы станка, по которым ЭТК считает время «Погонной» резки, и ручные работы (владелец 15.10).
const NODE_PREFIX = { roll: "скорость проката", tool: "скорость работы инструмента", toolOps: "кол-во операций инструмента" };

type Spec = Omit<OpField, "key" | "value"> & { group: OpGroupId; unit?: string; basisUnit?: boolean };
const norm = (key: string) => cleanHeader(key).toLocaleLowerCase("ru-RU");

/** Spec колонки; null — скрыть, undefined — неизвестная («Прочее»). */
function spec(key: string): Spec | null | undefined {
  const name = norm(key);
  if (name === "номер") return { group: "ident", label: "Код", readOnly: true };
  if (name === "название") return { group: "ident", label: "Название" };
  // Комплект ТД (МК «Проф.» и «Р»): ключи схемы без колонки листа.
  if (key === "opProfession") return { group: "ident", label: "Профессия", hint: "Пусто — по ЕТКС" };
  if (key === "opGrade") return { group: "ident", label: "Разряд", hint: "1–6; пусто — по ЕТКС" };
  if (name === "машина") return { group: "equip", label: "Машина" };
  if (name === "инструмент") return { group: "equip", label: "Инструмент" };
  if (name === "апликаторы / модули") return { group: "equip", label: "Апликаторы / модули" };
  if (name === "тип операции") return { group: "norms", label: "Тип операции", opType: true };
  if (name.startsWith("время операции")) return { group: "norms", label: "Время операции", basisUnit: true };
  if (name.startsWith("время подготовки")) return { group: "norms", label: "Время подготовки", unit: "с" };
  if (name.startsWith("время машины")) return { group: "norms", label: "Время машины", basisUnit: true };
  if (name.startsWith(NODE_PREFIX.roll)) return { group: "nodes", label: "Скорость проката", unit: "м/с (подача)", hint: ROLL_HINT };
  if (name.startsWith(NODE_PREFIX.tool)) return { group: "nodes", label: "Скорость работы инструмента", unit: "с на ход" };
  if (name.startsWith(NODE_PREFIX.toolOps)) return { group: "nodes", label: "Кол-во операций инструмента", unit: "ходов на шт" };
  if (name.startsWith("время ручных работ для взятия")) return { group: "nodes", label: "Ручные работы: взять ПФ", unit: "с" };
  if (name.startsWith("время ручных работ для снятия")) return { group: "nodes", label: "Ручные работы: снять ПФ", unit: "с" };
  if (name.startsWith("время ручных работ")) return { group: "nodes", label: "Ручные работы", unit: "с" };
  if (name.startsWith("расход на настройку")) return { group: "nodes", label: "Расход на настройку", unit: "м или шт" };
  if (RAW_PREFIX.some((prefix) => name.startsWith(prefix))) return { group: "raw", label: headerLabel(key) };
  if (DEAD_EXACT.has(name) || DEAD_PREFIX.some((prefix) => name.startsWith(prefix))) return null;
  return undefined;
}

/** Норма на операцию или на метр: «Погонный» — на метр, «Статичный»/«Переменный» — на операцию (слова в БД не меняются). */
export function normBasis(type: string): "meter" | "operation" | "" {
  const word = type.trim().toLocaleLowerCase("ru-RU");
  if (!word) return "";
  return word.startsWith("погон") ? "meter" : "operation";
}

export function timeUnit(type: string): string {
  const basis = normBasis(type);
  return basis === "meter" ? "с на метр" : basis === "operation" ? "с на операцию" : "с";
}

/** Значение колонки по началу её заголовка (заголовки листа — с единицами и разным регистром). */
function column(fields: Record<string, string>, prefix: string): string {
  return Object.entries(fields).find(([key]) => norm(key).startsWith(prefix))?.[1] ?? "";
}

const number = (value: string) => { const parsed = Number(String(value ?? "").trim().replace(",", ".")); return Number.isFinite(parsed) ? parsed : 0; };

/** Время на штуку «Погонной» операции, как считает ЭТК: большее из машины — подача L / скорость проката + инструмент
 *  (время хода × ходы) — и ручных работ. null — тип не «Погонный» или скорость проката не задана: тогда время из
 *  «Время операции». */
export function linearPiece(fields: Record<string, string>, lengthMm: number): { feed: number; tool: number; hands: number; total: number } | null {
  if (!timeByLength(fields)) return null;
  const { roll, take, manual, put } = cycleNumbers(fields);
  const feed = Math.max(0, lengthMm) / 1000 / number(roll);
  const tool = number(column(fields, NODE_PREFIX.tool)) * number(column(fields, NODE_PREFIX.toolOps));
  const hands = number(take) + number(manual) + number(put);
  return { feed, tool, hands, total: Math.max(feed + tool, hands) };
}

/** Циклограмма строки БД.ОП (лист «Циклограмма операций», владелец 18.10): подготовка первой, затем ручные работы и узлы
 *  станка. `label` — подпись поля карточки (spec), `title` — заголовок столбца таблицы. */
export const CYCLE_COLUMNS: Array<{ id: string; label: string; title: string; hint?: string }> = [
  { id: "prep", label: "Время подготовки", title: "Подготовка, с" },
  { id: "take", label: "Ручные работы: взять ПФ", title: "Взять ПФ, с" },
  { id: "manual", label: "Ручные работы", title: "Ручные, с" },
  { id: "put", label: "Ручные работы: снять ПФ", title: "Снять ПФ, с" },
  { id: "roll", label: "Скорость проката", title: "Прокат, м/с", hint: "Подача провода у погонной операции: время подачи = длина / прокат" },
  { id: "tool", label: "Скорость работы инструмента", title: "Ход инстр., с", hint: "Скорость работы инструмента, с на ход" },
  { id: "toolOps", label: "Кол-во операций инструмента", title: "Ходов", hint: "Кол-во операций инструмента на штуку" },
];

/** Ключ поля строки по подписи карточки (заголовки листа пишутся по-разному). */
export function opFieldKey(fields: Record<string, string>, label: string): string | undefined {
  return Object.keys(fields).find((key) => !OP_SCHEMA_KEYS.has(key) && spec(key)?.label === label);
}

const cycleValue = (fields: Record<string, string>, label: string) => fields[opFieldKey(fields, label) ?? ""] ?? "";
const sheetNumber = (value: number) => value.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: false });

const cycleNumbers = (fields: Record<string, string>) => Object.fromEntries(CYCLE_COLUMNS
  .map((item) => [item.id, cycleValue(fields, item.label)])) as Record<string, string>;

/** Погонная операция с прокатом: время зависит от длины провода — его считает ЭТК (`linearPiece`), не одна цифра. */
export function timeByLength(fields: Record<string, string>): boolean {
  return normBasis(column(fields, "тип операции")) === "meter" && number(cycleNumbers(fields).roll) > 0;
}

/** Время из циклограммы, как считает лист (сверено со всеми 139 строками БД.ОП): руки — взять ПФ + ручные + снять ПФ,
 *  машина — ход инструмента × ходов, операция — большее из двух. Прокат у статичной/переменной не читается. null —
 *  циклограмма пуста (время вводится руками) или время зависит от длины (`timeByLength`). */
export function cycleTimes(fields: Record<string, string>): { human: number; machine: number; operation: number } | null {
  if (timeByLength(fields)) return null;
  const { take, manual, put, tool, toolOps } = cycleNumbers(fields);
  if (![take, manual, put, tool, toolOps].some((value) => String(value ?? "").trim())) return null;
  const human = number(take) + number(manual) + number(put);
  const machine = number(tool) * number(toolOps);
  return { human, machine, operation: Math.max(human, machine) };
}

/** Строка с пересчитанными «Время Чел» / «Время машины» / «Время операции» — как формулы листа после правки ячейки. */
export function withCycleTimes(fields: Record<string, string>): Record<string, string> {
  const times = cycleTimes(fields);
  if (!times) return fields;
  const next = { ...fields };
  const put = (prefix: string, value: number) => {
    const key = Object.keys(fields).find((item) => !OP_SCHEMA_KEYS.has(item) && norm(item).startsWith(prefix));
    if (key) next[key] = sheetNumber(value);
  };
  put("время чел", times.human);
  put("время машины", times.machine);
  put("время операции", times.operation);
  return next;
}

/** Поля строки по группам. Скрытые (мёртвые колонки, дубли схемы) не возвращаются; пустые группы тоже. */
export function groupOpFields(fields: Record<string, string>): OpGroup[] {
  const type = Object.entries(fields).find(([key]) => spec(key)?.opType)?.[1] ?? "";
  const linear = normBasis(type) === "meter";
  // Есть циклограмма — «Время операции» и «Время машины» считаются из неё, руками не правятся.
  const fromCycle = Boolean(cycleTimes(fields));
  const groups = new Map<OpGroupId, OpField[]>();
  for (const [key, raw] of Object.entries(fields)) {
    if (OP_SCHEMA_KEYS.has(key)) continue;
    const found = spec(key);
    if (found === null) continue;
    const group: OpGroupId = found?.group ?? "other";
    const label = found?.label ?? headerLabel(key);
    const field: OpField = {
      key, value: String(raw ?? ""), label, readOnly: found?.readOnly || (fromCycle && ["Время операции", "Время машины"].includes(label)), opType: found?.opType, hint: found?.hint,
      unit: found?.basisUnit ? timeUnit(type) : found?.unit,
      keep: linear && group === "nodes" && !label.startsWith("Ручные") && label !== "Расход на настройку",
    };
    groups.set(group, [...(groups.get(group) ?? []), field]);
  }
  // Основные группы — в порядке листа ЭТК (не в порядке колонок БД.ОП), прочие — как пришли.
  const rank = (field: OpField) => { const at = LABEL_ORDER.indexOf(field.label); return at < 0 ? LABEL_ORDER.length : at; };
  return ORDER.filter((id) => groups.get(id)?.length).map((id) => ({
    id, title: OP_GROUP_TITLES[id], fields: id === "other" || id === "raw" ? groups.get(id)! : [...groups.get(id)!].sort((a, b) => rank(a) - rank(b)),
    ...(id === "nodes" && groups.get(id)!.some((field) => field.label.startsWith("Ручные")) ? { note: NODES_NOTE } : {}),
  }));
}

/** Заполненное значение для показа: пусто и FALSE листа — не значение. */
export function hasValue(value: string): boolean {
  const text = String(value ?? "").trim();
  return Boolean(text) && text.toUpperCase() !== "FALSE";
}

/** Только изменённые поля (`old` — как было в записи). */
export function changedFields(record: Record<string, string>, draft: Record<string, string>): Array<{ field: string; old: string; value: string }> {
  return Object.entries(draft).filter(([key, value]) => (record[key] ?? "") !== value)
    .map(([field, value]) => ({ field, old: record[field] ?? "", value }));
}
