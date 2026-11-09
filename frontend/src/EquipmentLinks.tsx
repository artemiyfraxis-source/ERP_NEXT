import { useState } from "react";
import type { EquipmentLinksReport, EquipmentModelInfo, TechOperationRecord } from "./api";
import { ROLE_LABELS, inventoryOrder, invOf, nameOf, servedUnits } from "./equipmentModel";
import type { EquipmentModel } from "./equipmentModel";

/** Связи оборудования по id (владелец 05.11.2026): «Обслуживает» обеспечивающего оборудования и блок «Без привязки» —
    всё, что сервер не смог привязать к модели однозначно. */

const REASONS: Record<string, string> = {
  unmatched: "нет такой модели", ambiguous: "несколько моделей", supporting: "обеспечивающее оборудование",
};
const unitLabel = (unit: TechOperationRecord) => [invOf(unit), nameOf(unit)].filter(Boolean).join(" ");

/** «Обслуживает» of every unit of a supporting model: the served machines, a picker to change the list. */
export function ServesEditor({ model, records, canEdit, busy, save }: {
  model: EquipmentModel; records: TechOperationRecord[]; canEdit: boolean; busy: boolean;
  save: (unit: TechOperationRecord, unitIds: string[]) => Promise<void>;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const machines = records.filter((record) => (record.equipment_role ?? "operational") === "operational")
    .sort((a, b) => inventoryOrder(invOf(a), invOf(b)));
  const needle = query.trim().toLocaleLowerCase("ru-RU");
  const found = machines.filter((record) => unitLabel(record).toLocaleLowerCase("ru-RU").includes(needle));
  const start = (unit: TechOperationRecord) => { setEditing(unit.id); setPicked(new Set(unit.serves_unit_ids ?? [])); setQuery(""); };
  const toggle = (id: string) => setPicked((current) => { const next = new Set(current); if (!next.delete(id)) next.add(id); return next; });
  return <div className="tr-block"><h4>Обслуживает</h4>
    <table className="eq-table"><thead><tr><th>Экземпляр</th><th>Обслуживаемые машины</th>{canEdit && <th />}</tr></thead>
      <tbody>{model.items.map((unit) => {
        const served = servedUnits(unit, records);
        return <tr key={unit.id}>
          <td className="tnum"><b>{invOf(unit) || "—"}</b></td>
          <td>{editing === unit.id
            ? <div className="eq-serves-pick">
              <input className="eq-input" aria-label="Найти машину" placeholder="Найти машину…" value={query} onChange={(event) => setQuery(event.target.value)} />
              <ul className="eq-serves-list">{found.map((machine) => <li key={machine.id}><label>
                <input type="checkbox" checked={picked.has(machine.id)} onChange={() => toggle(machine.id)} /> <span className="tnum">{invOf(machine)}</span> {nameOf(machine)}</label></li>)}</ul>
            </div>
            : served.length ? <span className="tr-chips">{served.map((machine) => <span key={machine.id} className="eq-op"><span className="tnum">{invOf(machine)}</span> {nameOf(machine)}</span>)}</span>
              : <span className="muted">Не задано</span>}</td>
          {canEdit && <td className="eq-act">{editing === unit.id
            ? <><button type="button" className="primary small" disabled={busy}
              onClick={() => void save(unit, [...picked]).then(() => setEditing(null))}>Сохранить</button> <button type="button" className="secondary small" onClick={() => setEditing(null)}>Отмена</button></>
            : <button type="button" className="secondary small" disabled={busy} onClick={() => start(unit)}>Выбрать машины</button>}</td>}
        </tr>;
      })}</tbody></table>
  </div>;
}

/** «Без привязки»: what the report could not link by id, and the roles nobody confirmed. */
export function EquipmentUnlinked({ report, canEdit, busy, records, confirm, openModel }: {
  report: EquipmentLinksReport | null; canEdit: boolean; busy: boolean; records: TechOperationRecord[];
  confirm: (info: EquipmentModelInfo) => Promise<void>; openModel: (info: EquipmentModelInfo) => void;
}) {
  if (!report) return <div className="empty">Отчёт о связях недоступен.</div>;
  const unitOf = (id: string) => records.find((record) => record.id === id);
  const total = report.operations.length + report.tools.length + report.templates.length + report.models_without_units.length
    + report.models_units_disagree.length + report.roles_unconfirmed.length;
  if (!total) return <div className="empty">Всё привязано: непривязанных ячеек и неподтверждённых видов нет.</div>;
  const modelRow = (info: EquipmentModelInfo, withConfirm: boolean) => <tr key={info.id} className={info.unit_ids.length ? "clickable" : undefined}
    onClick={() => openModel(info)}>
    <td><b>{info.display || info.name}</b></td><td>{ROLE_LABELS[info.role]}</td><td className="num tnum">{info.operations}</td>
    <td className="tnum">{info.inventory_numbers.filter(Boolean).join(", ") || "—"}</td>
    {withConfirm && canEdit && <td className="eq-act"><button type="button" className="secondary small" disabled={busy}
      onClick={(event) => { event.stopPropagation(); void confirm(info); }}>Подтвердить «{ROLE_LABELS[info.role].toLowerCase()}»</button></td>}
  </tr>;
  const modelsTable = (title: string, items: EquipmentModelInfo[], withConfirm = false) => items.length > 0 && <div className="tr-block eq-unlinked">
    <h4>{title} <span>{items.length}</span></h4>
    <table className="eq-table"><thead><tr><th>Модель</th><th>Вид</th><th className="num">Операций</th><th>Инв. №</th>{withConfirm && canEdit && <th />}</tr></thead>
      <tbody>{items.map((info) => modelRow(info, withConfirm))}</tbody></table></div>;
  return <div className="eq-unlinked-wrap">
    {report.operations.length > 0 && <div className="tr-block eq-unlinked"><h4>Ячейки операций без модели <span>{report.operations.length}</span></h4>
      <table className="eq-table"><thead><tr><th>Операция</th><th>Столбец</th><th>Текст в ячейке</th><th>Причина</th><th>Подходят</th></tr></thead>
        <tbody>{report.operations.map((item) => <tr key={`${item.operation_id}:${item.column}:${item.position}`}>
          <td className="tnum"><b>{item.number || "—"}</b></td><td>{item.column}</td><td>{item.text}</td><td>{REASONS[item.reason] ?? item.reason}</td>
          <td>{item.candidates.map((candidate) => candidate.display).join("; ") || "—"}</td></tr>)}</tbody></table></div>}
    {report.tools.length > 0 && <div className="tr-block eq-unlinked"><h4>«Для инструмента» без единицы <span>{report.tools.length}</span></h4>
      <table className="eq-table"><thead><tr><th>Единица</th><th>Текст</th><th>Причина</th></tr></thead>
        <tbody>{report.tools.map((item) => { const unit = unitOf(item.unit_id); return <tr key={item.unit_id}>
          <td className="tnum"><b>{item.inventory || (unit ? unitLabel(unit) : "—")}</b></td><td>{item.text}</td><td>{REASONS[item.reason]}</td></tr>; })}</tbody></table></div>}
    {report.templates.length > 0 && <div className="tr-block eq-unlinked"><h4>Карты: оборудование без модели <span>{report.templates.length}</span></h4>
      <table className="eq-table"><thead><tr><th>Карта</th><th>Оборудование</th><th>Инв. №</th><th>Причина</th></tr></thead>
        <tbody>{report.templates.map((item) => <tr key={`${item.template_id}:${item.position}`}>
          <td className="tnum">{item.template_id}</td><td>{item.tool_name || "—"}</td><td className="tnum">{item.inventory_number || "—"}</td><td>{REASONS[item.reason]}</td></tr>)}</tbody></table></div>}
    {modelsTable("Вид не подтверждён", report.roles_unconfirmed, true)}
    {modelsTable("Модели без единиц", report.models_without_units)}
    {modelsTable("Единицы модели расходятся по названию, артикулу, производителю", report.models_units_disagree)}
  </div>;
}
