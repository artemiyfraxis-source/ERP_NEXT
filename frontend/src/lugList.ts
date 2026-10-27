/* «Наконечники» (владелец 26.10.2026: «тип наконечник, а не терминал… и в базе их бы тоже отдельно сделать, в другую БД —
   наконечники»): строки БД.ТЕР со списком «lug» (бэкенд references/lugs.py). Столбцы — только те, что у наконечников
   заполнены (сервер их и присылает); здесь — блоки по семейству (НКИ, НШВИ, РПИ-М…), порядок «сечение, затем артикул»,
   сечение и AWG «от–до» одной колонкой. */
import type { DataColumn } from "./DataTable";
import type { TechOperationRecord } from "./api";
import { headerKey } from "./referenceLabels";
import { compareNames, compareNumbers, firstNumber } from "./refSort";

export const LUG_SORT_HINT = "семейство, затем сечение и артикул";
const NO_FAMILY = "Без семейства";

const fieldOf = (fields: string[], ...keys: string[]) => fields.find((field) => keys.includes(headerKey(field)));
const value = (record: TechOperationRecord, field?: string) => (field ? String(record.fields[field] ?? "").trim() : "");

/** Семейство наконечника — наименование листа («НКИ», «РПИ-М», «FDD»); блок списка. */
export function lugFamily(record: TechOperationRecord): string {
  const fields = Object.keys(record.fields);
  return value(record, fieldOf(fields, "product name", "tercomponent")) || value(record, fieldOf(fields, "series", "terseries")) || NO_FAMILY;
}

const mm2From = (record: TechOperationRecord) => firstNumber(value(record, fieldOf(Object.keys(record.fields), "от мм2", "mm2from")));
const article = (record: TechOperationRecord) => {
  const fields = Object.keys(record.fields);
  return value(record, fieldOf(fields, "артикул контакта (reel)", "terarticle")) || value(record, fieldOf(fields, "артикул контакта (bag)", "terarticlebag"));
};

/** Порядок по умолчанию: семейство по алфавиту («Без семейства» в конце), внутри — сечение «от», затем артикул. */
export function lugRows(records: TechOperationRecord[]): TechOperationRecord[] {
  return [...records].sort((a, b) => {
    const [fa, fb] = [lugFamily(a), lugFamily(b)];
    return Number(fa === NO_FAMILY) - Number(fb === NO_FAMILY) || compareNames(fa, fb)
      || compareNumbers(mm2From(a), mm2From(b)) || compareNames(article(a), article(b));
  });
}

const range = (from: string, to: string) => (from && to && from !== to ? `${from}–${to}` : from || to);

/** Столбцы «Наконечников»: «От мм2 / До мм2» → «Сечение, мм²», «От AWG / До AWG» → «AWG» (исходные — в «Колонках»,
    скрыты), сразу за артикулами; артикулы — «Артикул (катушка) / (пакет)», без слова «контакта». */
export function mergeLugColumns<T extends TechOperationRecord>(columns: DataColumn<T>[], fields: string[]): DataColumn<T>[] {
  const [mmFrom, mmTo] = [fieldOf(fields, "от мм2", "mm2from"), fieldOf(fields, "до мм2", "mm2to")];
  const [awgFrom, awgTo] = [fieldOf(fields, "от awg", "awgfrom"), fieldOf(fields, "до awg", "awgto")];
  const reel = fieldOf(fields, "артикул контакта (reel)", "артикул контакта", "terarticle");
  const bag = fieldOf(fields, "артикул контакта (bag)", "terarticlebag");
  const sizeText = (record: T) => range(value(record, mmFrom), value(record, mmTo));
  const awgText = (record: T) => range(value(record, awgFrom), value(record, awgTo));
  const merged: DataColumn<T>[] = [
    ...(mmFrom || mmTo ? [{ key: "__mm2", title: "Сечение, мм²", value: sizeText, render: (record: T) => sizeText(record) || "—" }] : []),
    ...(awgFrom || awgTo ? [{ key: "__awg", title: "AWG", value: awgText, render: (record: T) => awgText(record) || "—" }] : []),
  ];
  const folded = new Set([mmFrom, mmTo, awgFrom, awgTo].filter(Boolean) as string[]);
  const rest = columns.map((column) => folded.has(column.key) ? { ...column, hidden: true }
    : column.key === reel ? { ...column, title: "Артикул (катушка)" }
    : column.key === bag ? { ...column, title: "Артикул (пакет)" } : column);
  const anchor = Math.max(rest.findIndex((column) => column.key === reel), rest.findIndex((column) => column.key === bag));
  if (!merged.length) return rest;
  return anchor < 0 ? [...merged, ...rest] : [...rest.slice(0, anchor + 1), ...merged, ...rest.slice(anchor + 1)];
}
