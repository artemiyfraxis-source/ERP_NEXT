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
  "Код", "Название", "Машина", "Инструмент", "Апликаторы / модули", "Тип операции", "Время операции", "Время подготовки",
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
export const NODES_NOTE = "Ручные работы — справочно: в расчётное время ЭТК не входят.";

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

/** Время на штуку «Погонной» операции по узлам станка, как считает ЭТК: подача L / скорость проката + инструмент
 *  (время хода × ходы). null — тип не «Погонный» или скорость проката не задана: тогда время из «Время операции». */
export function linearPiece(fields: Record<string, string>, lengthMm: number): { feed: number; tool: number; total: number } | null {
  if (normBasis(column(fields, "тип операции")) !== "meter") return null;
  const roll = number(column(fields, NODE_PREFIX.roll));
  if (roll <= 0) return null;
  const feed = Math.max(0, lengthMm) / 1000 / roll;
  const tool = number(column(fields, NODE_PREFIX.tool)) * number(column(fields, NODE_PREFIX.toolOps));
  return { feed, tool, total: feed + tool };
}

/** Поля строки по группам. Скрытые (мёртвые колонки, дубли схемы) не возвращаются; пустые группы тоже. */
export function groupOpFields(fields: Record<string, string>): OpGroup[] {
  const type = Object.entries(fields).find(([key]) => spec(key)?.opType)?.[1] ?? "";
  const linear = normBasis(type) === "meter";
  const groups = new Map<OpGroupId, OpField[]>();
  for (const [key, raw] of Object.entries(fields)) {
    if (OP_SCHEMA_KEYS.has(key)) continue;
    const found = spec(key);
    if (found === null) continue;
    const group: OpGroupId = found?.group ?? "other";
    const label = found?.label ?? headerLabel(key);
    const field: OpField = {
      key, value: String(raw ?? ""), label, readOnly: found?.readOnly, opType: found?.opType, hint: found?.hint,
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
