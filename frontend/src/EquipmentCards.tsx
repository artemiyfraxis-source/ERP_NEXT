import { useMemo, useState, type ReactNode } from "react";
import type { TechOperationRecord } from "./api";

/** БД.ОБ as cards, the way «Операции» shows БД.ОП (owner 29.09.2026 «с оборудованием то же самое»): grouped by «Тип»,
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
  const [chip, setChip] = useState("");
  const [query, setQuery] = useState("");
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
  const needle = query.trim().toLocaleLowerCase("ru-RU");
  const searched = useMemo(() => !needle ? records
    : records.filter((record) => Object.values(record.fields).join(" ").toLocaleLowerCase("ru-RU").includes(needle)), [records, needle]);
  const types = useMemo(() => {
    const counts = new Map<string, number>();
    searched.forEach((record) => counts.set(typeOf(record), (counts.get(typeOf(record)) ?? 0) + 1));
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [searched]);
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
        <div><small>Расположение</small><b>{pick(open.fields, "Расположение") || "—"}</b></div>
      </div>
      <div className="tr-block"><h4>Строка БД.ОБ <span>{filledColumns(open.fields).length} полей</span></h4>
        <dl className="op-fields">{filledColumns(open.fields).map(([key, value]) => <div key={key}><dt>{key}</dt><dd className="pre-line">{
          /^https?:\/\//.test(value.trim()) ? <a href={value.trim()} target="_blank" rel="noreferrer">{value.trim()}</a> : value
        }</dd></div>)}</dl>
      </div>
      <div className="tr-block"><h4>Операции БД.ОП с этим оборудованием <span>{ops.length}</span></h4>
        {ops.length ? <div className="tr-chips">{ops.map((code) => <span key={code} className="tr-code eq-op">{code}</span>)}</div>
          : <div className="empty">{operations.length ? "Ни одна операция БД.ОП не ссылается на «Для базы» этой строки." : "БД.ОП не загружена."}</div>}
      </div>
    </article>;
  }

  return <>
    <div className="tr-toolbar">
      <input aria-label="Поиск: Оборудование" placeholder="Инв. номер, название, артикул, производитель…" value={query} onChange={(event) => setQuery(event.target.value)} />
      <span className="tr-toolbar-end">{toolbar}</span>
    </div>
    <div className="tr-chips">
      <button className={chip ? "" : "on"} onClick={() => setChip("")}>Все · {searched.length}</button>
      {types.map(([name, n]) => <button key={name} className={chip === name ? "on" : ""} onClick={() => setChip(chip === name ? "" : name)}>{name} · {n}</button>)}
    </div>
    {!searched.length && <div className="empty">{records.length ? "Ничего не найдено." : "Записей нет."}</div>}
    {types.filter(([name]) => !chip || chip === name).map(([name, n]) => <section className="tr-group" key={name}>
      <h3>{name} <span>{n}</span></h3>
      <div className="tr-grid">
        {searched.filter((record) => typeOf(record) === name).map((record) => {
          const ops = opsOf(record);
          const sub = [pick(record.fields, "Артикул"), pick(record.fields, "Производитель").replace(/^-+$/, "")].filter(Boolean).join(" · ");
          return <button type="button" key={record.id} className="tr-card" onClick={() => setOpenId(record.id)}>
            <strong className="tr-code">{invOf(record) || "без инв. номера"}</strong>
            <span className="tr-name">{nameOf(record) || "—"}</span>
            {sub && <span className="tr-first">{sub}</span>}
            <small className="tr-meta">{[pick(record.fields, "Расположение"), ops.length ? `в ${ops.length} опер.` : ""].filter(Boolean).join(" · ") || "—"}</small>
          </button>;
        })}
      </div>
    </section>)}
  </>;
}
