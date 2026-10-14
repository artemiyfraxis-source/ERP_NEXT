// Поля строки БД.ОП на карточке операции: лист хранит ~25 колонок под своими заголовками (регистр и сокращения как на
// листе), экран показывает нужные, сгруппированными и по-русски. Скрытие — только в интерфейсе: в `fields` остаётся
// каждый ключ, неизвестная колонка попадает в «Прочее» и не пропадает.
import { cleanHeader, headerLabel } from "./referenceLabels";

/** Колонки БД.ОП, которые схема дублирует под своими ключами (opNumber = «Номер» …): показываются один раз, по листу. */
export const OP_SCHEMA_KEYS = new Set(["opNumber", "opName", "tOp", "tPrep", "tMachine"]);

export type OpGroupId = "ident" | "equip" | "norms" | "other" | "raw";
export type OpField = { key: string; label: string; value: string; readOnly?: boolean; unit?: string; basis?: boolean };
export type OpGroup = { id: OpGroupId; title: string; fields: OpField[] };

export const OP_GROUP_TITLES: Record<OpGroupId, string> = {
  ident: "Идентификация", equip: "Оборудование и оснастка", norms: "Нормы времени", other: "Прочее",
  raw: "Исходные данные расчёта (ERP не использует)",
};
const ORDER: OpGroupId[] = ["ident", "equip", "norms", "other", "raw"];
const LABEL_ORDER = ["Код", "Название", "Машина", "Инструмент", "Апликаторы / модули", "Время операции", "Время подготовки", "Время машины", "Норма на"];

// Колонки, которые не заполнены ни в одной строке или не несут смысла (владелец 14.10): на экране скрыты.
const DEAD_EXACT = new Set(["программа", "провод", "разъем", "разъём", "инструкция", "отказы", "полуфабрикат"]);
const DEAD_PREFIX = ["время доп"];
// Заполнены, но расчёт ERP их не читает: одна свёрнутая группа.
const RAW_PREFIX = ["время чел", "уд.цена", "расход на настройку", "время ручных работ", "скорость проката", "скорость работы инструмента", "кол-во операций инструмента"];

type Spec = { group: OpGroupId; label: string; readOnly?: boolean; unit?: "s" | "basis"; basis?: boolean };
const norm = (key: string) => cleanHeader(key).toLocaleLowerCase("ru-RU");

/** Spec колонки; null — скрыть, undefined — неизвестная («Прочее»). */
function spec(key: string): Spec | null | undefined {
  const name = norm(key);
  if (name === "номер") return { group: "ident", label: "Код", readOnly: true };
  if (name === "название") return { group: "ident", label: "Название" };
  if (name === "машина") return { group: "equip", label: "Машина" };
  if (name === "инструмент") return { group: "equip", label: "Инструмент" };
  if (name === "апликаторы / модули") return { group: "equip", label: "Апликаторы / модули" };
  if (name.startsWith("время операции")) return { group: "norms", label: "Время операции", unit: "basis" };
  if (name.startsWith("время подготовки")) return { group: "norms", label: "Время подготовки", unit: "s" };
  if (name.startsWith("время машины")) return { group: "norms", label: "Время машины", unit: "basis" };
  if (name === "тип операции") return { group: "norms", label: "Норма на", basis: true };
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

/** Слово для БД при выборе «на операцию / на метр»: подходящее значение остаётся (Переменный не превращается в Статичный). */
export function typeForBasis(current: string, basis: "meter" | "operation" | ""): string {
  if (!basis) return "";
  if (normBasis(current) === basis) return current;
  return basis === "meter" ? "Погонный" : "Статичный";
}

export function timeUnit(type: string): string {
  const basis = normBasis(type);
  return basis === "meter" ? "с на метр" : basis === "operation" ? "с на операцию" : "с";
}

/** Поля строки по группам. Скрытые (мёртвые колонки, дубли схемы) не возвращаются; пустые группы тоже. */
export function groupOpFields(fields: Record<string, string>): OpGroup[] {
  const type = Object.entries(fields).find(([key]) => spec(key)?.basis)?.[1] ?? "";
  const groups = new Map<OpGroupId, OpField[]>();
  for (const [key, raw] of Object.entries(fields)) {
    if (OP_SCHEMA_KEYS.has(key)) continue;
    const found = spec(key);
    if (found === null) continue;
    const group: OpGroupId = found?.group ?? "other";
    const field: OpField = {
      key, value: String(raw ?? ""), label: found?.label ?? headerLabel(key), readOnly: found?.readOnly, basis: found?.basis,
      unit: found?.unit === "s" ? "с" : found?.unit === "basis" ? timeUnit(type) : undefined,
    };
    groups.set(group, [...(groups.get(group) ?? []), field]);
  }
  // Основные группы — в порядке листа ЭТК (не в порядке колонок БД.ОП), прочие — как пришли.
  const rank = (field: OpField) => { const at = LABEL_ORDER.indexOf(field.label); return at < 0 ? LABEL_ORDER.length : at; };
  return ORDER.filter((id) => groups.get(id)?.length).map((id) => ({
    id, title: OP_GROUP_TITLES[id], fields: id === "other" || id === "raw" ? groups.get(id)! : [...groups.get(id)!].sort((a, b) => rank(a) - rank(b)),
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
