import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { failureText, referencesApi, type CrimpCodes, type TechOperationRecord } from "./api";
import { CrimpLinks } from "./CrimpLinks";
import { DataTable, type DataColumn } from "./DataTable";
import {
  CODE_KEY, CRIMP_TOOL_TYPE, MATRIX_TYPE, OP_LINK_COLUMNS, TARGETS_KEY, TOOL_KEY, crimpRecord, groupModels,
  inventoryRange, invOf, nameOf, norm, operationField, operationsOfModel, pick, placeOf, typeOf,
  type EquipmentModel,
} from "./equipmentModel";
import "./equipmentPanel.css";
import { headerLabel, valueLabel } from "./referenceLabels";

/** БД.ОБ: the list is one row per model (owner 12.10.2026: «дубли одного и того же свернуть»), a row opens the machine
    card in a right-hand panel beside the list (master–detail; the pick lives in `?equipment=`) — instances with «Ещё такой же», the БД.ОП operations tied to the model (link / unlink), what a crimp tool or a
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
// Values the sheet computes: read-only, marked as derived.
const DERIVED = /окупаемост|стоимость 1 сек/i;
function EconomyList({ items }: { items: Array<[string, string]> }) {
  return <dl className="eq-econ">{items.map(([key, value]) => {
    const derived = DERIVED.test(key);
    return <div key={key} className={derived ? "derived" : undefined}>
      <dt>{headerLabel(key)}</dt>
      <dd className="tnum">{valueLabel(key, value)}{derived && <span className="eq-calc" title="Считается по таблице, здесь не правится">расчёт</span>}</dd>
    </div>;
  })}</dl>;
}
function FieldList({ items }: { items: Array<[string, string]> }) {
  return <dl className="op-fields">{items.map(([key, value]) => <div key={key}><dt>{headerLabel(key)}</dt><dd className="pre-line">{
    /^https?:\/\//.test(value.trim()) ? <a href={value.trim()} target="_blank" rel="noreferrer">{value.trim()}</a> : valueLabel(key, value)
  }</dd></div>)}</dl>;
}

export function EquipmentCards({ records, operations, canEdit, onEdit, onCreate, onChanged, onNotice, onError, focusId, toolbar, editor, onSelect }: {
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
  /** The edit / create form: shown in the right-hand panel instead of the card, the list stays on the left. */
  editor?: ReactNode;
  /** A row was picked or the card closed: the page drops an open editor. */
  onSelect?: () => void;
}) {
  const [openId, setOpenId] = useState(() => focusId || new URLSearchParams(window.location.search).get("equipment") || "");
  const panelRef = useRef<HTMLElement>(null);
  const [busy, setBusy] = useState(false);
  const [codes, setCodes] = useState<CrimpCodes | null>(null);
  const [opQuery, setOpQuery] = useState("");
  // The picked machine stays in the address, so a reload reopens its card (same as «Проекты» → `?project=`).
  const select = (id: string) => {
    setOpenId(id);
    setOpQuery("");
    onSelect?.();
    try {
      const url = new URL(window.location.href);
      if (id) url.searchParams.set("equipment", id); else url.searchParams.delete("equipment");
      window.history.replaceState(window.history.state, "", url);
    } catch { /* address is not writable */ }
  };
  useEffect(() => { if (focusId) select(focusId); }, [focusId]); // eslint-disable-line react-hooks/exhaustive-deps
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
  const openKey = open?.key;
  // Esc closes the card; switching machines starts the panel from its top.
  useEffect(() => {
    if (!openKey) return;
    panelRef.current?.scrollTo?.({ top: 0 });
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented || (event.target as HTMLElement | null)?.closest?.("[role='dialog']")) return;
      select("");
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [openKey]); // eslint-disable-line react-hooks/exhaustive-deps

  async function guarded(action: () => Promise<unknown>, done: string) {
    if (busy) return;
    setBusy(true);
    try { await action(); onNotice(done); onChanged(); } catch (reason) { onError(failureText(reason, "Не записано")); } finally { setBusy(false); }
  }

  let panel: ReactNode = null;
  if (editor) {
    panel = <aside className="eq-panel" aria-label="Правка оборудования" ref={panelRef}>
      <article className="panel eq-card eq-editor">{editor}</article>
    </aside>;
  } else if (open) {
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
    const subtitle = [open.article, open.maker, open.type, place].filter(Boolean).join(" · ");
    const setOperation = (op: TechOperationRecord, column: string, value: string) =>
      referencesApi.editRecord(op.id, [{ field: column, old: op.fields[column] ?? "", value }]);
    const edit = onEdit;

    // Коды терминалов есть только у обжимного инструмента и матриц (owner 12.10.2026).
    const withCodes = open.type === CRIMP_TOOL_TYPE || open.type === MATRIX_TYPE;
    panel = <aside className="eq-panel" aria-label="Карточка оборудования" ref={panelRef}>
      <article className="panel eq-card">
        <header className="eq-head">
          <div className="eq-title"><b>{open.name || "—"}</b>{subtitle && <span className="muted">{subtitle}</span>}</div>
          <div className="eq-head-act">
            {canEdit && <button className="secondary small" onClick={() => edit(open.items[0])}>Править{open.items.length > 1 ? ` ${invOf(open.items[0])}` : ""}</button>}
            {canEdit && invOf(last) && <button className="secondary small" disabled={busy}
              onClick={() => void guarded(async () => { const copy = await referencesApi.duplicateRecord(last.id); select(copy.id); }, "Добавлен ещё один экземпляр")}>Ещё такой же</button>}
            <button type="button" className="eq-close" aria-label="Закрыть карточку" title="Закрыть (Esc)" onClick={() => select("")}>×</button>
          </div>
        </header>
        {pick(last.fields, "Описание") && <p className="eq-line pre-line">{pick(last.fields, "Описание")}</p>}

        <div className="tr-block"><h4>Экземпляры <span>{open.items.length}</span></h4>
          <table className="eq-table"><thead><tr><th>Инв. №</th><th className="num">№</th><th>Расположение</th>{withCodes && <th>Код терминалов</th>}</tr></thead>
            <tbody>{open.items.map((item) => <tr key={item.id} className={canEdit ? "eq-row" : undefined} onClick={canEdit ? () => edit(item) : undefined}
              title={canEdit ? `Править ${invOf(item)}` : undefined}>
              <td className="tnum"><b>{invOf(item) || "—"}</b></td><td className="tnum num">{pick(item.fields, "№") || "—"}</td>
              <td>{valueLabel("Расположение", placeOf(item)) || "—"}</td>{withCodes && <td className="tnum">{pick(item.fields, CODE_KEY) || "—"}</td>}
            </tr>)}</tbody></table>
        </div>

        <div className="tr-block"><h4>Операции <span>{linked.length}</span></h4>
          {linked.length ? <ul className="eq-ops">{linked.map((op) => <li key={op.id}>
            <span><b className="tnum">{opNumber(op)}</b>{opName(op) && <> — {opName(op)}</>}</span>
            {canEdit && <button type="button" className="eq-x" disabled={busy} aria-label={`Отвязать операцию ${opNumber(op)}`} title="Отвязать"
              onClick={() => void guarded(async () => {
                for (const column of OP_LINK_COLUMNS) if (norm(op.fields[column] ?? "") === norm(open.base)) await setOperation(op, column, "");
              }, `Операция ${opNumber(op)} отвязана`)}>✕</button>}</li>)}</ul>
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
          <div className="tr-chips">{matricesOf(open).map((matrix) => <button key={matrix.id} type="button" className="eq-op eq-chip-btn" onClick={() => select(matrix.id)}>
            <span className="tnum">{invOf(matrix)}</span> {nameOf(matrix)}</button>)}</div></div>}

        {crimp && <CrimpLinks model={open} tool={crimp} siblings={records} canEdit={canEdit} onChanged={onChanged} onEditTool={onEdit} />}

        {economy.length > 0 && <div className="tr-block"><h4>Экономика</h4><EconomyList items={economy} /></div>}
        {rest.length > 0 && <details className="tr-block eq-rest"><summary>Прочее <span className="muted">({rest.length})</span></summary><FieldList items={rest} /></details>}
      </article>
    </aside>;
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
  return <div className={open || editor ? "eq-split has-card" : "eq-split"}>
    <div className="eq-list">
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
        onRowClick={(model) => select(model.items[0].id)}
        rowClassName={(model) => open && model.key === open.key ? "eq-selected" : ""}
        rowTitle={(model) => `Открыть: ${model.name || invOf(model.items[0])}`}
        toolbar={toolbar}
        emptyText={records.length ? "Ничего не найдено." : "Записей нет."}
      />
    </div>
    {panel}
  </div>;
}

