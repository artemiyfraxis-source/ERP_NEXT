import type { TechOperationRecord } from "./api";

/** БД.ОБ as models: units of one machine («ОБ.070.1…8») are one row of the list and one card (owner 12.10.2026).
    Backend twin of the numbering: `references/equipment.py`. */

export const CRIMP_TOOL_TYPE = "Обжимной инструмент";
export const MATRIX_TYPE = "Матрицы";
// «Аппликаторы» (owner 29.10.2026): every БД.ТЕР «Аппликатор» code is an applicator of one of three kinds —
// M: ОТП format, pre-stripped wires only; A: for automatic machines; S: crimps unstripped wires.
export const APPLICATOR_TYPE = "Аппликаторы";
export const CRIMP_TYPES = [CRIMP_TOOL_TYPE, MATRIX_TYPE, APPLICATOR_TYPE];
/** The record type a crimp code of БД.ТЕР belongs to: «A8» → applicator, «M10» → matrix, «S10» → hand tool. */
export const crimpTypeOfCode = (_code: string) => APPLICATOR_TYPE;
const APPLICATOR_KINDS: Record<string, string> = { M: "ОТП (зачищенные)", A: "Для автомата", S: "Незачищенные" };
/** The applicator kind by the code's letter: «M10» → «ОТП (зачищенные)»; no code — empty. */
export const applicatorKindOf = (code: string) => APPLICATOR_KINDS[code.trim().charAt(0).toUpperCase()] ?? "";
export const codeOf = (model: EquipmentModel) => model.items.map((item) => pick(item.fields, CODE_KEY)).find(Boolean) ?? "";
export const NAME_KEYS = ["Наменование", "Наименование", "Полное наименование ", "Полное наименование"];
export const CODE_KEY = "Код в базе терминалов";
export const TOOL_KEY = "Для инструмента";
export const TARGETS_KEY = "Обжимает (серии разъёмов)";
export const INV_KEY = "Инв. номер";

// «Оборудование (ПР)» is a Google-era leftover: it is plain «Оборудование» on screen even before the data is cleaned.
const TYPE_ALIASES: Record<string, string> = { "Оборудование (ПР)": "Оборудование" };

export const pick = (fields: Record<string, string>, ...keys: string[]) => {
  for (const key of keys) {
    const value = String(fields[key] ?? "").trim();
    if (value) return value;
  }
  return "";
};
export const typeOf = (record: TechOperationRecord) => {
  const type = pick(record.fields, "Тип", "obType") || "Без типа";
  return TYPE_ALIASES[type] ?? type;
};
export const nameOf = (record: TechOperationRecord) => pick(record.fields, ...NAME_KEYS, "baseValue");
export const baseOf = (record: TechOperationRecord) => pick(record.fields, "Для базы", "baseValue");
export const invOf = (record: TechOperationRecord) => pick(record.fields, INV_KEY);
export const articleOf = (record: TechOperationRecord) => pick(record.fields, "Артикул");
export const makerOf = (record: TechOperationRecord) => pick(record.fields, "Производитель").replace(/^-+$/, "");
export const placeOf = (record: TechOperationRecord) => pick(record.fields, "Расположение");
export const norm = (value: string) => value.trim().toLocaleLowerCase("ru-RU").replace(/\s+/g, " ");

export interface Inventory { prefix: string; base: number; baseText: string; suffix: string }
export function parseInventory(value: string): Inventory | null {
  const match = /^\s*([A-Za-zА-Яа-яЁё]+)\.\s*(\d+)(?:\.(\d+|[A-Za-zА-Яа-яЁё]))?\s*$/.exec(value);
  return match ? { prefix: `${match[1].toUpperCase()}.`, base: Number(match[2]), baseText: match[2], suffix: (match[3] ?? "").toUpperCase() } : null;
}
/** Sort key of an inventory number: prefix, model, unit («ОБ.79.10» after «ОБ.79.9»); the unnumbered ones last. */
export function inventoryOrder(a: string, b: string): number {
  const x = parseInventory(a), y = parseInventory(b);
  if (!x || !y) return x ? -1 : y ? 1 : a.localeCompare(b, "ru");
  const unit = (item: Inventory) => (item.suffix === "" ? 0 : /^\d+$/.test(item.suffix) ? Number(item.suffix) : 9999);
  return x.prefix.localeCompare(y.prefix, "ru") || x.base - y.base || unit(x) - unit(y);
}

/** «ОБ.070.1–8» for a run of units, «ОБ.044» for one, «ОБ.025, ОБ.026» for two numbers of different models. */
export function inventoryRange(numbers: string[]): string {
  const sorted = [...new Set(numbers.filter(Boolean))].sort(inventoryOrder);
  if (sorted.length <= 1) return sorted[0] ?? "";
  const first = parseInventory(sorted[0]), last = parseInventory(sorted[sorted.length - 1]);
  if (first && last && first.prefix === last.prefix && first.base === last.base && /^\d+$/.test(last.suffix)) {
    return sorted.length === 2 ? sorted.join(", ") : `${sorted[0]}–${last.suffix}`;
  }
  return sorted.length === 2 ? sorted.join(", ") : `${sorted[0]} … ${sorted[sorted.length - 1]}`;
}

export interface EquipmentModel {
  key: string;
  items: TechOperationRecord[]; // units, by inventory number
  name: string;
  article: string;
  maker: string;
  type: string;
  places: string[];
  base: string;
}

/** One model = the same name + article + maker. A unit without a name stands alone (never merged by its number). */
export function groupModels(records: TechOperationRecord[]): EquipmentModel[] {
  const byKey = new Map<string, TechOperationRecord[]>();
  for (const record of records) {
    const name = nameOf(record);
    const key = name ? `${norm(name)}|${norm(articleOf(record))}|${norm(makerOf(record))}` : `#${record.id}`;
    byKey.set(key, [...(byKey.get(key) ?? []), record]);
  }
  return [...byKey.entries()].map(([key, items]) => {
    const sorted = [...items].sort((a, b) => inventoryOrder(invOf(a), invOf(b)));
    const first = sorted[0];
    return {
      key, items: sorted, name: nameOf(first), article: articleOf(first), maker: makerOf(first), type: typeOf(first),
      places: [...new Set(sorted.map(placeOf).filter(Boolean))], base: baseOf(first),
    };
  });
}

/** Rows of the БД.ОП that name a model in the column that suits its type. */
export function operationField(type: string): string {
  if (type === MATRIX_TYPE) return "Апликаторы / модули";
  if (type === "Инструмент" || type === CRIMP_TOOL_TYPE) return "Инструмент";
  return "Машина";
}
export const OP_LINK_COLUMNS = ["Машина", "Инструмент", "Апликаторы / модули"];

export function operationsOfModel(model: EquipmentModel, operations: TechOperationRecord[]): TechOperationRecord[] {
  const base = norm(model.base);
  if (!base) return [];
  return operations.filter((op) => OP_LINK_COLUMNS.some((column) => norm(op.fields[column] ?? "") === base));
}

/** The crimp-tool record of a model: the unit that owns the code, else the first one. */
export function crimpRecord(model: EquipmentModel): TechOperationRecord {
  return model.items.find((item) => pick(item.fields, CODE_KEY)) ?? model.items[0];
}

export function parseTargets(value: string): Array<{ kind: string; key: string; label: string }> {
  try {
    const items = JSON.parse(value || "[]") as Array<Record<string, string>>;
    return Array.isArray(items) ? items.filter((item) => item?.key).map((item) => ({ kind: item.kind ?? "", key: item.key, label: item.label ?? item.key })) : [];
  } catch { return []; }
}

/** Tokens of a БД.ТЕР «Аппликатор» cell: a code is a whole token (S1 is not S10); a word like «возможно» makes the
    codes after it doubtful. Twin of `equipment.tokens`. */
export function applicatorTokens(value: string): Array<{ code: string; doubtful: boolean }> {
  const out: Array<{ code: string; doubtful: boolean }> = [];
  let noise = false;
  for (const piece of value.split(/[/,;\s]+/).filter(Boolean)) {
    const code = piece.toUpperCase();
    if (/^[A-Z]\d{1,3}$/.test(code)) out.push({ code, doubtful: noise }); else noise = true;
  }
  return out;
}
