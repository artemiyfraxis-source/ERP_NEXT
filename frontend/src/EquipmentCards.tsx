import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { failureText, referencesApi, type CrimpCodes, type TechOperationRecord } from "./api";
import { PageWindow } from "./PageWindow";
import { CrimpLinks } from "./CrimpLinks";
import { DataTable, type DataColumn } from "./DataTable";
import {
  CODE_KEY, CRIMP_TYPES, INV_KEY, MATRIX_TYPE, applicatorKindOf, codeOf, crimpTypeOfCode, NAME_KEYS, TARGETS_KEY, TOOL_KEY, crimpRecord, groupModels,
  inventoryRange, invOf, nameOf, operationsOfModel, pick, placeOf, typeOf,
  type EquipmentModel,
} from "./equipmentModel";
import "./equipmentPanel.css";
import { headerLabel, valueLabel } from "./referenceLabels";

/** БД.ОБ: the list is one row per model (owner 12.10.2026: «дубли одного и того же свернуть»), a row opens the machine
    card in a window over the list (the pick lives in `?equipment=`) — instances with «Ещё такой же», the БД.ОП operations
    tied to the model, what a crimp tool or a matrix crimps, the economics. The raw 21-column row stays under «Прочее».
    Owner 17.10.2026: every value is edited right in the card on a click, no «Править» form; a model value (name, article,
    economy, «Прочее») is written to every unit of the model at once, a unit's own value (inv. №, №, place, code) — to that
    unit. Operations are tied in the operation's card (БД.ОП «Машина» / «Инструмент» / «Апликаторы / модули»): here they
    are only listed. */

// DB_SCHEMA keys the importer stores next to the sheet's own columns — shown once, by the sheet header.
const SCHEMA_KEYS = new Set(["baseValue", "obType"]);
const SHOWN_KEYS = new Set(["Тип", "Наменование", "Наименование", "Полное наименование ", "Полное наименование", "Артикул", "Производитель",
  "Расположение", "Инв. номер", "№", "Для базы", "Описание", CODE_KEY, TOOL_KEY, TARGETS_KEY]);
const ECONOMY = /^(стоимость|коэффициент|[cс]рок окупаемости|кол-во сек|желаемый срок|потребление)/i;
const isEconomy = (key: string) => ECONOMY.test(key.trim());
const opNumber = (op: TechOperationRecord) => pick(op.fields, "Номер", "opNumber");
const opName = (op: TechOperationRecord) => pick(op.fields, "Название", "opName");
// Passport of the model: the sheet column each line edits (the first one the record has).
const PASSPORT: Array<[string, string[]]> = [["Наименование", NAME_KEYS], ["Артикул", ["Артикул"]], ["Производитель", ["Производитель"]],
  ["Тип", ["Тип"]], ["Описание", ["Описание"]]];

type SaveValue = (key: string, value: string) => Promise<void>;

/** A value edited in place: a click turns it into a field; Enter or leaving the field writes it, Esc puts it back. */
export function InlineValue({ label, value, display, editable, multiline, onSave }: {
  label: string; value: string; display?: ReactNode; editable: boolean; multiline?: boolean; onSave: (value: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  if (!editable) return <>{display ?? (value || "—")}</>;
  if (draft === null) {
    return <button type="button" className="eq-val" aria-label={`${label}: ${value || "не задано"} — править`} onClick={() => setDraft(value)}>
      {display ?? (value || <span className="cell-empty">—</span>)}
    </button>;
  }
  const commit = () => { const next = draft.trim(); setDraft(null); if (next !== value.trim()) void onSave(next); };
  const keys = (event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setDraft(null); }
    if (event.key === "Enter" && !(multiline && event.shiftKey)) { event.preventDefault(); commit(); }
  };
  return multiline
    ? <textarea className="eq-input" aria-label={label} autoFocus rows={3} value={draft} onChange={(event) => setDraft(event.target.value)} onBlur={commit} onKeyDown={keys} />
    : <input className="eq-input" aria-label={label} autoFocus value={draft} onChange={(event) => setDraft(event.target.value)} onBlur={commit} onKeyDown={keys} />;
}

function filled(fields: Record<string, string>, keep: (key: string) => boolean): Array<[string, string]> {
  return Object.entries(fields).filter(([key, value]) => keep(key) && !SCHEMA_KEYS.has(key) && String(value ?? "").trim()
    && String(value).trim().toUpperCase() !== "FALSE");
}
// Values the sheet computes: read-only, marked as derived.
const DERIVED = /окупаемост|стоимость 1 сек/i;
function EconomyList({ items, save }: { items: Array<[string, string]>; save?: SaveValue }) {
  return <dl className="eq-econ">{items.map(([key, value]) => {
    const derived = DERIVED.test(key);
    return <div key={key} className={derived ? "derived" : undefined}>
      <dt>{headerLabel(key)}</dt>
      <dd className="tnum"><InlineValue label={headerLabel(key)} value={value} display={valueLabel(key, value)} editable={Boolean(save) && !derived}
        onSave={(next) => save!(key, next)} />{derived && <span className="eq-calc" title="Считается по таблице, здесь не правится">расчёт</span>}</dd>
    </div>;
  })}</dl>;
}
function FieldList({ items, save }: { items: Array<[string, string]>; save?: SaveValue }) {
  return <dl className="op-fields">{items.map(([key, value]) => <div key={key}><dt>{headerLabel(key)}</dt><dd className="pre-line">{
    /^https?:\/\//.test(value.trim()) && !save ? <a href={value.trim()} target="_blank" rel="noreferrer">{value.trim()}</a>
      : <InlineValue label={headerLabel(key)} value={value} display={valueLabel(key, value)} editable={Boolean(save)} multiline={value.length > 60} onSave={(next) => save!(key, next)} />
  }</dd></div>)}</dl>;
}

export function EquipmentCards({ records, operations, canEdit, onEdit, onCreate, onChanged, onNotice, onError, focusId, toolbar, editor, onSelect, onCancelEdit }: {
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
  /** The edit / create form: shown in a window over the list (owner 14.10: edit forms open as windows). */
  editor?: ReactNode;
  /** A row was picked or the card closed: the page drops an open editor. */
  onSelect?: () => void;
  /** Esc in the edit window: the page drops the editor (same as its «Отмена»). */
  onCancelEdit?: () => void;
}) {
  const [openId, setOpenId] = useState(() => focusId || new URLSearchParams(window.location.search).get("equipment") || "");
  const [busy, setBusy] = useState(false);
  const [codes, setCodes] = useState<CrimpCodes | null>(null);
  // The picked machine stays in the address, so a reload reopens its card (same as «Проекты» → `?project=`).
  const select = (id: string) => {
    setOpenId(id);
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
  async function guarded(action: () => Promise<unknown>, done: string) {
    if (busy) return;
    setBusy(true);
    try { await action(); onNotice(done); onChanged(); } catch (reason) { onError(failureText(reason, "Не записано")); } finally { setBusy(false); }
  }

  let panel: ReactNode = null;
  if (editor) {
    panel = <PageWindow label="Правка оборудования" width={860} close={() => onCancelEdit?.()}>
      <article className="panel eq-card eq-editor">{editor}</article>
    </PageWindow>;
  } else if (open) {
    const model = open;
    const linked = opsOf(open);
    const last = open.items[open.items.length - 1];
    const crimp = CRIMP_TYPES.includes(open.type) ? crimpRecord(open) : undefined;
    const economy = filled(last.fields, isEconomy);
    const rest = filled(last.fields, (key) => !SHOWN_KEYS.has(key) && !isEconomy(key));
    const place = open.places.map((item) => valueLabel("Расположение", item)).join(", ");
    const subtitle = [open.article, open.maker, open.type, place].filter(Boolean).join(" · ");
    // A model value goes to every unit that differs (each with its own old value); a unit's own value — to that unit.
    const saveModel: SaveValue = (key, value) => guarded(async () => {
      for (const item of model.items) {
        const old = item.fields[key] ?? "";
        if (old.trim() !== value) await referencesApi.editRecord(item.id, [{ field: key, old, value }]);
      }
    }, model.items.length > 1 ? `Записано на все экземпляры: ${model.items.length}` : "Записано");
    const saveUnit = (item: TechOperationRecord): SaveValue => (key, value) =>
      guarded(() => referencesApi.editRecord(item.id, [{ field: key, old: item.fields[key] ?? "", value }]), `${invOf(item) || "Экземпляр"}: записано`);
    const passport = PASSPORT.map(([label, keys]) => [label, keys.find((key) => key in last.fields)] as const)
      .filter((entry): entry is readonly [string, string] => Boolean(entry[1]));
    const unitCell = (item: TechOperationRecord, key: string, label: string, shown?: string) => key in item.fields
      ? <InlineValue label={`${label} ${invOf(item)}`} value={pick(item.fields, key)} display={shown} editable={canEdit} onSave={(next) => saveUnit(item)(key, next)} />
      : (shown || "—");

    // Коды терминалов есть только у обжимного инструмента, матриц и аппликаторов (owner 12.10, 29.10.2026).
    const withCodes = CRIMP_TYPES.includes(open.type);
    panel = <PageWindow label="Карточка оборудования" width={760} dismissable close={() => select("")}>
      <article className="panel eq-card">
        <header className="eq-head">
          <div className="eq-title"><b>{open.name || "—"}</b>{subtitle && <span className="muted">{subtitle}</span>}</div>
          <div className="eq-head-act">
            {canEdit && invOf(last) && <button className="secondary small" disabled={busy}
              onClick={() => void guarded(async () => { const copy = await referencesApi.duplicateRecord(last.id); select(copy.id); }, "Добавлен ещё один экземпляр")}>Ещё такой же</button>}
            <button type="button" className="eq-close" aria-label="Закрыть карточку" title="Закрыть (Esc)" onClick={() => select("")}>×</button>
          </div>
        </header>
        {canEdit && passport.length > 0 && <div className="tr-block"><h4>Модель{open.items.length > 1 && <span>правка — на все экземпляры</span>}</h4>
          <dl className="op-fields">{passport.map(([label, key]) => <div key={key}><dt>{label}</dt><dd className="pre-line">
            <InlineValue label={label} value={pick(last.fields, key)} display={key === "Тип" ? valueLabel(key, pick(last.fields, key)) : undefined} editable
              multiline={key === "Описание"} onSave={(next) => saveModel(key, next)} /></dd></div>)}</dl></div>}
        {!canEdit && pick(last.fields, "Описание") && <p className="eq-line pre-line">{pick(last.fields, "Описание")}</p>}

        <div className="tr-block"><h4>Экземпляры <span>{open.items.length}</span></h4>
          <table className="eq-table"><thead><tr><th>Инв. №</th><th className="num">№</th><th>Расположение</th>{withCodes && <th>Код терминалов</th>}</tr></thead>
            <tbody>{open.items.map((item) => <tr key={item.id}>
              <td className="tnum"><b>{unitCell(item, INV_KEY, "Инв. №", invOf(item))}</b></td><td className="tnum num">{unitCell(item, "№", "№")}</td>
              <td>{unitCell(item, "Расположение", "Расположение", valueLabel("Расположение", placeOf(item)))}</td>
              {withCodes && <td className="tnum">{unitCell(item, CODE_KEY, "Код терминалов")}</td>}
            </tr>)}</tbody></table>
        </div>

        <div className="tr-block"><h4>Операции <span>{linked.length}</span></h4>
          {linked.length ? <ul className="eq-ops">{linked.map((op) => <li key={op.id}>
            <span><b className="tnum">{opNumber(op)}</b>{opName(op) && <> — {opName(op)}</>}</span></li>)}</ul>
            : <div className="empty">{operations.length ? "Операций с этой моделью нет." : "БД.ОП не загружена."}</div>}
          <p className="eq-hint">Привязка — в карточке операции: «Машина», «Инструмент», «Апликаторы / модули».</p>
        </div>

        {open.type !== MATRIX_TYPE && matricesOf(open).length > 0 && <div className="tr-block"><h4>Матрицы к этому инструменту <span>{matricesOf(open).length}</span></h4>
          <div className="tr-chips">{matricesOf(open).map((matrix) => <button key={matrix.id} type="button" className="eq-op eq-chip-btn" onClick={() => select(matrix.id)}>
            <span className="tnum">{invOf(matrix)}</span> {nameOf(matrix)}</button>)}</div></div>}

        {crimp && <CrimpLinks model={open} tool={crimp} siblings={records} canEdit={canEdit} onChanged={onChanged} onEditTool={onEdit} />}

        {economy.length > 0 && <div className="tr-block"><h4>Экономика</h4><EconomyList items={economy} save={canEdit ? saveModel : undefined} /></div>}
        {rest.length > 0 && <details className="tr-block eq-rest"><summary>Прочее <span className="muted">({rest.length})</span></summary><FieldList items={rest} save={canEdit ? saveModel : undefined} /></details>}
      </article>
    </PageWindow>;
  }

  const columns: DataColumn<EquipmentModel>[] = [
    { key: "inv", title: "Инв. номер", value: (model) => model.items.map(invOf).filter(Boolean).join(" "),
      render: (model) => <span className="tnum" title={model.items.map(invOf).join(", ")}>{inventoryRange(model.items.map(invOf)) || "—"}</span> },
    { key: "name", title: "Наименование", value: (model) => model.name, grow: true },
    { key: "count", title: "Единиц", numeric: true, value: (model) => model.items.length, render: (model) => model.items.length > 1 ? `${model.items.length} шт.` : "1 шт." },
    { key: "type", title: "Тип", value: (model) => model.type, filter: true },
    { key: "code", title: "Код", value: (model) => codeOf(model), render: (model) => <span className="tnum">{codeOf(model) || "—"}</span> },
    { key: "kind", title: "Вид", value: (model) => applicatorKindOf(codeOf(model)), filter: true },
    { key: "article", title: "Артикул", value: (model) => model.article },
    { key: "maker", title: "Производитель", value: (model) => model.maker },
    { key: "place", title: "Расположение", value: (model) => model.places.map((item) => valueLabel("Расположение", item)).join(", "), filter: true },
    { key: "ops", title: "Операций", numeric: true, value: (model) => opsOf(model).length, hint: "Сколько операций БД.ОП привязано к этой модели" },
  ];
  const presets = types.map(([name]) => ({ label: name, test: (model: EquipmentModel) => model.type === name }));
  const missing = codes?.missing ?? [];
  return <div className="eq-split">
    <div className="eq-list">
      {missing.length > 0 && <details className="eq-missing">
        <summary>Коды обжима без записи <span className="muted">({missing.length})</span></summary>
        <div className="eq-missing-list">{missing.map((item) => <span key={item.code} className="tr-code eq-op" title={`Указан у ${item.count} контактов в БД.ТЕР`}>
          <span className="tnum">{item.code}</span>{canEdit && <button type="button" className="eq-x" aria-label={`Создать запись для кода ${item.code}`}
            onClick={() => onCreate({ Тип: crimpTypeOfCode(item.code), [CODE_KEY]: item.code })}>＋</button>}</span>)}</div>
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

