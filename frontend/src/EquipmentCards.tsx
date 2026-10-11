import { useMemo, useState, type ReactNode } from "react";
import type { TechOperationRecord } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { headerLabel, valueLabel } from "./referenceLabels";

/** БД.ОБ as a dense table (audit БД-2: 192 cards were 6 500 px of scrolling), a row opens the card: by «Тип» chips,
    every sheet column in the open card, plus the БД.ОП operations that name the unit as «Машина» / «Инструмент» /
    «Апликаторы / модули» by its «Для базы» value (the same key SUPPLYMAP's БД.ОП dropdowns use). */

// DB_SCHEMA keys the importer stores next to the sheet's own columns — shown once, by the sheet header.
const SCHEMA_KEYS = new Set(["baseValue", "obType"]);
const OP_LINK_COLUMNS = ["Машина", "Инструмент", "Апликаторы / модули"];

const pick = (fields: Record<string, string>, ...keys: string[]) => {
  for (const key of keys) {
    const value = String(fields[key] ?? "").trim();
    if (value) return value;
  }
  return "";
};
const typeOf = (record: TechOperationRecord) => pick(record.fields, "Тип", "obType") || "Без типа";
const nameOf = (record: TechOperationRecord) => pick(record.fields, "Наменование", "Наименование", "Полное наименование ", "Полное наименование", "baseValue");
const baseOf = (record: TechOperationRecord) => pick(record.fields, "Для базы", "baseValue");
const invOf = (record: TechOperationRecord) => pick(record.fields, "Инв. номер");
const norm = (value: string) => value.trim().toLocaleLowerCase("ru-RU").replace(/\s+/g, " ");

function filledColumns(fields: Record<string, string>): Array<[string, string]> {
  return Object.entries(fields).filter(([key, value]) => !SCHEMA_KEYS.has(key) && String(value ?? "").trim() && String(value).trim().toUpperCase() !== "FALSE");
}

export function EquipmentCards({ records, operations, canEdit, onEdit, toolbar }: {
  records: TechOperationRecord[];
  operations: TechOperationRecord[];
  canEdit: boolean;
  onEdit: (record: TechOperationRecord) => void;
  toolbar?: ReactNode;
}) {
  const [openId, setOpenId] = useState("");
  const usedBy = useMemo(() => {
    const byBase = new Map<string, string[]>();
    operations.forEach((op) => {
      const code = pick(op.fields, "opNumber", "Номер");
      if (!code) return;
      OP_LINK_COLUMNS.forEach((column) => {
        const value = norm(op.fields[column] ?? "");
        if (!value) return;
        const list = byBase.get(value) ?? [];
        if (!list.includes(code)) list.push(code);
        byBase.set(value, list);
      });
    });
    return byBase;
  }, [operations]);
  const opsOf = (record: TechOperationRecord) => (baseOf(record) ? usedBy.get(norm(baseOf(record))) ?? [] : []);
  const types = useMemo(() => {
    const counts = new Map<string, number>();
    records.forEach((record) => counts.set(typeOf(record), (counts.get(typeOf(record)) ?? 0) + 1));
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [records]);
  // Пустые строки листа (ни номера, ни названия) — в конец: они не должны открывать таблицу.
  const rows = useMemo(() => [...records].sort((a, b) => Number(!(invOf(a) || nameOf(a))) - Number(!(invOf(b) || nameOf(b)))), [records]);
  const open = openId ? records.find((record) => record.id === openId) : undefined;

  if (open) {
    const ops = opsOf(open);
    return <article className="panel template-detail tr-paper">
      <div className="form-actions"><button className="secondary" onClick={() => setOpenId("")}>← Всё оборудование</button>
        {canEdit && <button className="primary" onClick={() => onEdit(open)}>Править</button>}</div>
      <div className="tr-head">
        <div><small>Инв. номер</small><b>{invOf(open) || "—"}</b></div>
        <div className="tr-head-name"><small>Наименование</small><b>{nameOf(open) || "—"}</b></div>
        <div><small>Тип</small><b>{typeOf(open)}</b></div>
        <div><small>Расположение</small><b>{valueLabel("Расположение", pick(open.fields, "Расположение")) || "—"}</b></div>
      </div>
      <div className="tr-block"><h4>Строка БД.ОБ <span>{filledColumns(open.fields).length} полей</span></h4>
        <dl className="op-fields">{filledColumns(open.fields).map(([key, value]) => <div key={key}><dt>{headerLabel(key)}</dt><dd className="pre-line">{
          /^https?:\/\//.test(value.trim()) ? <a href={value.trim()} target="_blank" rel="noreferrer">{value.trim()}</a> : valueLabel(key, value)
        }</dd></div>)}</dl>
      </div>
      <div className="tr-block"><h4>Операции БД.ОП с этим оборудованием <span>{ops.length}</span></h4>
        {ops.length ? <div className="tr-chips">{ops.map((code) => <span key={code} className="tr-code eq-op">{code}</span>)}</div>
          : <div className="empty">{operations.length ? "Ни одна операция БД.ОП не ссылается на «Для базы» этой строки." : "БД.ОП не загружена."}</div>}
      </div>
    </article>;
  }

  const columns: DataColumn<TechOperationRecord>[] = [
    { key: "inv", title: "Инв. номер", value: (record) => invOf(record) },
    { key: "name", title: "Наименование", value: (record) => nameOf(record) },
    { key: "type", title: "Тип", value: typeOf, filter: true },
    { key: "article", title: "Артикул", value: (record) => pick(record.fields, "Артикул") },
    { key: "maker", title: "Производитель", value: (record) => pick(record.fields, "Производитель").replace(/^-+$/, "") },
    { key: "place", title: "Расположение", value: (record) => valueLabel("Расположение", pick(record.fields, "Расположение")), filter: true },
    { key: "ops", title: "Операций", numeric: true, value: (record) => opsOf(record).length, hint: "Сколько операций БД.ОП ссылаются на эту единицу в «Машина», «Инструмент» или «Апликаторы / модули»" },
  ];
  const presets = types.map(([name]) => ({ label: name, test: (record: TechOperationRecord) => typeOf(record) === name }));
  return <DataTable
    rows={rows}
    columns={columns}
    rowKey={(record) => record.id}
    storageKey="db:equipment"
    label="Оборудование"
    exportName="Оборудование"
    searchPlaceholder="Поиск: инв. номер, название, артикул, производитель…"
    presets={presets}
    presetCountsFollowSearch
    onRowClick={(record) => setOpenId(record.id)}
    rowTitle={(record) => `Открыть: ${nameOf(record) || invOf(record)}`}
    toolbar={toolbar}
    emptyText={records.length ? "Ничего не найдено." : "Записей нет."}
  />;
}
