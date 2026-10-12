import { useEffect, useMemo, useState, type ReactNode } from "react";
import { failureText, referencesApi, type CrimpCodes, type TechOperationRecord } from "./api";
import { CrimpLinks } from "./CrimpLinks";
import { DataTable, type DataColumn } from "./DataTable";
import {
  CODE_KEY, CRIMP_TOOL_TYPE, MATRIX_TYPE, OP_LINK_COLUMNS, TARGETS_KEY, TOOL_KEY, crimpRecord, groupModels,
  inventoryRange, invOf, nameOf, norm, operationField, operationsOfModel, pick, placeOf, typeOf,
  type EquipmentModel,
} from "./equipmentModel";
import { headerLabel, valueLabel } from "./referenceLabels";

/** БД.ОБ: the list is one row per model (owner 12.10.2026: «дубли одного и того же свернуть»), a row opens the machine
    card — instances with «Ещё такой же», the БД.ОП operations tied to the model (link / unlink), what a crimp tool or a
    matrix crimps, the economics. The raw 21-column row stays under «Прочее». */

// DB_SCHEMA keys the importer stores next to the sheet's own columns — shown once, by the sheet header.
const SCHEMA_KEYS = new Set(["baseValue", "obType"]);
const SHOWN_KEYS = new Set(["Тип", "Наменование", "Наименование", "Полное наименование ", "Полное наименование", "Артикул", "Производитель",
  "Расположение", "Инв. номер", "№", "Для базы", "Описание", CODE_KEY, TOOL_KEY, TARGETS_KEY]);
const ECONOMY = /^(стоимость|коэффициент|[cс]рок окупаемости|кол-во сек|желаемый срок|потребление)/i;
const isEconomy = (key: string) => ECONOMY.test(key.trim());
const CRIMP_TYPES = [CRIMP_TOOL_TYPE, MATRIX_TYPE];
const opNumber = (op: TechOperationRecord) => pick(op.fields, "Номер", "opNumber");
const opName = (op: TechOperationRecord) => pick(op.fields, "Название", "opName");

function filled(fields: Record<string, string>, keep: (key: string) => boolean): Array<[string, string]> {
  return Object.entries(fields).filter(([key, value]) => keep(key) && !SCHEMA_KEYS.has(key) && String(value ?? "").trim()
    && String(value).trim().toUpperCase() !== "FALSE");
}
function FieldList({ items }: { items: Array<[string, string]> }) {
  return <dl className="op-fields">{items.map(([key, value]) => <div key={key}><dt>{headerLabel(key)}</dt><dd className="pre-line">{
    /^https?:\/\//.test(value.trim()) ? <a href={value.trim()} target="_blank" rel="noreferrer">{value.trim()}</a> : valueLabel(key, value)
  }</dd></div>)}</dl>;
}

export function EquipmentCards({ records, operations, canEdit, onEdit, onCreate, onChanged, onNotice, onError, focusId, toolbar }: {
  records: TechOperationRecord[];
  operations: TechOperationRecord[];
  canEdit: boolean;
  onEdit: (record: TechOperationRecord) => void;
  onCreate: (preset?: Record<string, string>) => void;
  onChanged: () => void;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
  /** A record to open (from the «Аппликатор» link in БД.ТЕР). */
  focusId?: string;
  toolbar?: ReactNode;
}) {
  const [openId, setOpenId] = useState(focusId ?? "");
  const [busy, setBusy] = useState(false);
  const [codes, setCodes] = useState<CrimpCodes | null>(null);
  const [opQuery, setOpQuery] = useState("");
  useEffect(() => { if (focusId) setOpenId(focusId); }, [focusId]);
  useEffect(() => {
    let live = true;
    referencesApi.crimpCodes().then((result) => { if (live) setCodes(result); }).catch(() => { if (live) setCodes(null); });
    return () => { live = false; };
  }, [records]);

  const models = useMemo(() => groupModels(records).sort((a, b) => Number(!a.name) - Number(!b.name)), [records]);
  const types = useMemo(() => {
    const counts = new Map<string, number>();
    records.forEach((record) => counts.set(typeOf(record), (counts.get(typeOf(record)) ?? 0) + 1));
    CRIMP_TYPES.forEach((type) => { if (!counts.has(type)) counts.set(type, 0); });
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [records]);
  // Matrices that name one of the model's inventory numbers in «Для инструмента».
  const matricesOf = (model: EquipmentModel) => records.filter((record) => typeOf(record) === MATRIX_TYPE
    && model.items.some((item) => invOf(item) && pick(record.fields, TOOL_KEY) === invOf(item)));
  const opsOf = (model: EquipmentModel) => operationsOfModel(model, operations);
  const open = openId ? models.find((model) => model.items.some((item) => item.id === openId)) : undefined;

  async function guarded(action: () => Promise<unknown>, done: string) {
    if (busy) return;
    setBusy(true);
    try { await action(); onNotice(done); onChanged(); } catch (reason) { onError(failureText(reason, "Не записано")); } finally { setBusy(false); }
  }

  if (open) {
    const field = operationField(open.type);
    const linked = opsOf(open);
    const linkedIds = new Set(linked.map((op) => op.id));
    const text = opQuery.trim() ? norm(opQuery) : "";
    const candidates = text.length < 2 ? [] : operations.filter((op) => !linkedIds.has(op.id)
      && norm(`${opNumber(op)} ${opName(op)}`).includes(text)).slice(0, 8);
    const last = open.items[open.items.length - 1];
    const crimp = CRIMP_TYPES.includes(open.type) ? crimpRecord(open) : undefined;
    const economy = filled(last.fields, isEconomy);
    const rest = filled(last.fields, (key) => !SHOWN_KEYS.has(key) && !isEconomy(key));
    const place = open.places.map((item) => valueLabel("Расположение", item)).join(", ");
    const setOperation = (op: TechOperationRecord, column: string, value: string) =>
      referencesApi.editRecord(op.id, [{ field: column, old: op.fields[column] ?? "", value }]);

    // Коды терминалов есть только у обжимного инструмента и матриц (owner 12.10.2026).
    const withCodes = open.type === CRIMP_TOOL_TYPE || open.type === MATRIX_TYPE;
    return <article className="panel template-detail tr-paper eq-card">
      <div className="form-actions"><button className="secondary" onClick={() => { setOpenId(""); setOpQuery(""); }}>← Всё оборудование</button>
        {canEdit && <button className="secondary" onClick={() => onEdit(open.items[0])}>Править{open.items.length > 1 ? ` ${invOf(open.items[0])}` : ""}</button>}
        {canEdit && invOf(last) && <button className="primary" disabled={busy}
          onClick={() => void guarded(async () => { const copy = await referencesApi.duplicateRecord(last.id); setOpenId(copy.id); }, "Добавлен ещё один экземпляр")}>Ещё такой же</button>}</div>
      <div className="tr-head">
        <div className="tr-head-name"><small>Наименование</small><b>{open.name || "—"}</b></div>
        <div><small>Артикул</small><b>{open.article || "—"}</b></div>
        <div><small>Производитель</small><b>{open.maker || "—"}</b></div>
        <div><small>Тип</small><b>{open.type}</b></div>
        <div><small>Расположение</small><b>{place || "—"}</b></div>
      </div>
      {pick(last.fields, "Описание") && <p className="eq-line pre-line">{pick(last.fields, "Описание")}</p>}

      <div className="tr-block"><h4>Экземпляры <span>{open.items.length}</span></h4>
        <table className="eq-table"><thead><tr><th>Инв. номер</th><th>№</th><th>Расположение</th>{withCodes && <th>Код терминалов</th>}<th /></tr></thead>
          <tbody>{open.items.map((item) => <tr key={item.id} className={canEdit ? "eq-row" : undefined} onClick={canEdit ? () => onEdit(item) : undefined}>
            <td className="tnum"><b>{invOf(item) || "—"}</b></td><td className="tnum">{pick(item.fields, "№") || "—"}</td>
            <td>{valueLabel("Расположение", placeOf(item)) || "—"}</td>{withCodes && <td className="tnum">{pick(item.fields, CODE_KEY) || "—"}</td>}
            <td className="eq-act">{canEdit && <button type="button" className="secondary small" onClick={(event) => { event.stopPropagation(); onEdit(item); }}>Править</button>}</td>
          </tr>)}</tbody></table>
      </div>

      <div className="tr-block"><h4>Операции <span>{linked.length}</span></h4>
        {linked.length ? <table className="eq-table"><thead><tr><th>Номер</th><th>Название</th><th /></tr></thead>
          <tbody>{linked.map((op) => <tr key={op.id}><td className="tnum">{opNumber(op)}</td><td>{opName(op)}</td>
            <td className="eq-act">{canEdit && <button type="button" className="secondary small" disabled={busy}
              onClick={() => void guarded(async () => {
                for (const column of OP_LINK_COLUMNS) if (norm(op.fields[column] ?? "") === norm(open.base)) await setOperation(op, column, "");
              }, `Операция ${opNumber(op)} отвязана`)}>Отвязать</button>}</td></tr>)}</tbody></table>
          : <div className="empty">{operations.length ? "Ни одна операция БД.ОП не привязана к этой модели." : "БД.ОП не загружена."}</div>}
        {canEdit && open.base && operations.length > 0 && <div className="eq-add">
          <input type="search" aria-label="Найти операцию" placeholder="Привязать операцию: номер или название…" value={opQuery} onChange={(event) => setOpQuery(event.target.value)} />
          {candidates.map((op) => {
            const current = (op.fields[field] ?? "").trim();
            return <div className="eq-hit" key={op.id}>
              <span><b className="tnum">{opNumber(op)}</b> {opName(op)}{current && <span className="muted"> · сейчас: {current}</span>}</span>
              <button type="button" className="secondary small" disabled={busy}
                onClick={() => void guarded(async () => { await setOperation(op, field, open.base); setOpQuery(""); }, `Операция ${opNumber(op)} привязана`)}>{current ? "Заменить" : "Привязать"}</button>
            </div>;
          })}
          {text.length >= 2 && candidates.length === 0 && <div className="empty">Операции не найдены.</div>}
        </div>}
      </div>

      {open.type !== MATRIX_TYPE && matricesOf(open).length > 0 && <div className="tr-block"><h4>Матрицы к этому инструменту <span>{matricesOf(open).length}</span></h4>
        <div className="tr-chips">{matricesOf(open).map((matrix) => <button key={matrix.id} type="button" className="eq-op eq-chip-btn" onClick={() => setOpenId(matrix.id)}>
          <span className="tnum">{invOf(matrix)}</span> {nameOf(matrix)}</button>)}</div></div>}

      {crimp && <CrimpLinks model={open} tool={crimp} siblings={records} canEdit={canEdit} onChanged={onChanged} onEditTool={onEdit} />}

      {economy.length > 0 && <div className="tr-block"><h4>Экономика</h4><FieldList items={economy} /></div>}
      {rest.length > 0 && <details className="tr-block eq-rest"><summary>Прочее <span className="muted">({rest.length})</span></summary><FieldList items={rest} /></details>}
    </article>;
  }

  const columns: DataColumn<EquipmentModel>[] = [
    { key: "inv", title: "Инв. номер", value: (model) => model.items.map(invOf).filter(Boolean).join(" "),
      render: (model) => <span className="tnum" title={model.items.map(invOf).join(", ")}>{inventoryRange(model.items.map(invOf)) || "—"}</span> },
    { key: "name", title: "Наименование", value: (model) => model.name, grow: true },
    { key: "count", title: "Единиц", numeric: true, value: (model) => model.items.length, render: (model) => model.items.length > 1 ? `${model.items.length} шт.` : "1 шт." },
    { key: "type", title: "Тип", value: (model) => model.type, filter: true },
    { key: "article", title: "Артикул", value: (model) => model.article },
    { key: "maker", title: "Производитель", value: (model) => model.maker },
    { key: "place", title: "Расположение", value: (model) => model.places.map((item) => valueLabel("Расположение", item)).join(", "), filter: true },
    { key: "ops", title: "Операций", numeric: true, value: (model) => opsOf(model).length, hint: "Сколько операций БД.ОП привязано к этой модели" },
  ];
  const presets = types.map(([name]) => ({ label: name, test: (model: EquipmentModel) => model.type === name }));
  const missing = codes?.missing ?? [];
  return <>
    {missing.length > 0 && <details className="eq-missing">
      <summary>Коды обжима без записи <span className="muted">({missing.length})</span></summary>
      <div className="eq-missing-list">{missing.map((item) => <span key={item.code} className="tr-code eq-op" title={`Указан у ${item.count} контактов в БД.ТЕР`}>
        <span className="tnum">{item.code}</span>{canEdit && <button type="button" className="eq-x" aria-label={`Создать запись для кода ${item.code}`}
          onClick={() => onCreate({ Тип: CRIMP_TOOL_TYPE, [CODE_KEY]: item.code })}>＋</button>}</span>)}</div>
    </details>}
    <DataTable
      rows={models}
      columns={columns}
      rowKey={(model) => model.key}
      storageKey="db:equipment-models"
      label="Оборудование"
      exportName="Оборудование"
      searchPlaceholder="Поиск: инв. номер, название, артикул, производитель…"
      presets={presets}
      presetCountsFollowSearch
      onRowClick={(model) => setOpenId(model.items[0].id)}
      rowTitle={(model) => `Открыть: ${model.name || invOf(model.items[0])}`}
      toolbar={toolbar}
      emptyText={records.length ? "Ничего не найдено." : "Записей нет."}
    />
  </>;
}

