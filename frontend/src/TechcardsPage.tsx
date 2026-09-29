import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { TcGenerator } from "./TcGenerator";
import {
  kitsetsApi,
  referencesApi,
  techcardsApi,
  type KitsetBom,
  type OperationCardDetail,
  type OperationCardSummary,
  type OperationRecordEdit,
  type Principal,
  type ProjectTechcard,
  type TechcardRevision,
  type TechOperationRecord,
} from "./api";
import { etkChips, etkFigure } from "./etkDraw";
import { etkPrintPages } from "./etkPrint";
import { openPrintDocument } from "./print";
import { SAFETY_HEAD, TcCardEditForm } from "./TcCardEditForm";
import { TcTemplateSheets } from "./TcTemplateSheets";

type EtkRow = { name?: string; art?: string; kd?: string; grn?: string; norm?: string; unit?: string };
type EtkStep = { number?: string; kind?: string; text?: string; tolerance?: string };
type Housing = {
  title: string; art: string; rows: number; cols: number; n: number; color: string;
  conf: string; note?: string; grid: number[][]; round?: Array<{ pin: number; x: number; y: number }> | null;
};
type EtkWireField = { name?: string; art?: string; pins?: number[] };
type EtkTool = {
  section?: string; number?: string; name?: string; inventory_number?: string; program?: string;
  parameters?: string; norm?: string; unit?: string;
};
type EtkOperation = {
  position?: number;
  type?: string;
  code?: string;
  side?: string;
  title?: string;
  connector?: string;
  edited?: { at?: string; by?: string };
  card?: null | { name?: string; safety?: string; defects?: string; steps?: EtkStep[]; tooling?: EtkTool[] };
  components?: EtkRow[];
  semis?: EtkRow[];
  result?: EtkRow[];
  time?: EtkRow[];
  fields?: { wires?: EtkWireField[] };
  housing?: Housing;
};
type EtkTopology = {
  nodes?: Array<{ id: string; conn: string; side?: string; kind?: string; housing?: Housing }>;
  edges?: Array<{ from: string; to: string; wires: string[]; count: number; length: number }>;
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function operations(document: ProjectTechcard["document"]): EtkOperation[] {
  const value = document?.operations;
  return Array.isArray(value) ? value as EtkOperation[] : [];
}

function warnings(document: ProjectTechcard["document"]): string[] {
  const value = document?.warnings;
  return Array.isArray(value) ? value.map(String) : [];
}

function topology(document: ProjectTechcard["document"]): EtkTopology | null {
  const value = document?.topology;
  return value && typeof value === "object" ? value as EtkTopology : null;
}

function wireColor(value: string) {
  let hash = 0;
  for (const char of value) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return `hsl(${hash % 360} 58% 58%)`;
}

function HousingView({ housing, wires = [] }: { housing: Housing; wires?: EtkWireField[] }) {
  const byPin = new Map<number, string>();
  wires.forEach((wire) => (wire.pins ?? []).forEach((pin) => byPin.set(pin, wire.art || wire.name || "провод")));
  const pin = (number: number, className = "") => <span
    key={number} className={`housing-pin ${byPin.has(number) ? "occupied" : ""} ${className}`}
    title={byPin.has(number) ? `Контакт ${number}: ${byPin.get(number)}` : `Контакт ${number}`}
    style={byPin.has(number) ? { background: wireColor(byPin.get(number) || "") } : undefined}
  >{number}</span>;
  return <div className="housing-view">
    <small>Со стороны проводов · ориентир сверху</small>
    {housing.round?.length ? <div className="housing-round" style={{ background: housing.color }}>
      {housing.round.map((item) => <span key={item.pin} style={{ left: `${50 + item.x * 38}%`, top: `${50 + item.y * 38}%` }}>{pin(item.pin)}</span>)}
    </div> : <div className="housing-grid" style={{ background: housing.color, gridTemplateColumns: `repeat(${housing.cols}, 34px)` }}>
      {housing.grid.flat().map((number, index) => number ? pin(number) : <span className="housing-pin empty" key={`empty:${index}`} />)}
    </div>}
    <strong>{housing.title}</strong><small>{housing.art}{housing.conf !== "high" ? ` · раскладка ${housing.conf === "medium" ? "средней" : "низкой"} уверенности` : ""}</small>
    {housing.note && <small>{housing.note}</small>}
  </div>;
}

function TopologyView({ value }: { value: EtkTopology | null }) {
  if (!value?.nodes?.length) return null;
  return <section className="etk-topology"><h3>Схема жгута</h3>
    <div className="topology-nodes">{value.nodes.map((node) => <article key={node.id}>
      {node.housing ? <HousingView housing={node.housing} /> : <strong>{node.conn}</strong>}
      {node.kind === "splice" && <small>Узел сростков</small>}
    </article>)}</div>
    <div className="topology-edges">{value.edges?.map((edge) => <span key={`${edge.from}:${edge.to}`}><strong>{edge.from} → {edge.to}</strong> · {edge.count} пров.{edge.length ? ` · ${edge.length} мм` : ""} · {edge.wires.join(", ")}</span>)}</div>
  </section>;
}

function defaultConfig(card: ProjectTechcard): Record<string, unknown> {
  const saved = record(card.document?.build_config);
  if (Object.keys(saved).length) return saved;
  return {
    projectNo: card.project,
    assemblyIndex: card.item_index,
    assemblyName: card.product_name,
    partQty: 1,
    wires: [],
    ops: [],
  };
}

function Rows({ title, rows }: { title: string; rows?: EtkRow[] }) {
  if (!rows?.length) return null;
  return <div className="etk-block"><strong>{title}</strong>{rows.map((row, index) =>
    <span key={`${row.name || row.art}:${index}`}>{row.name || row.art || "—"}<small>{row.norm || "—"} {row.unit || ""}</small></span>
  )}</div>;
}

/** Комплектующие (SUPPLYMAP etkPartRow_): артикул к применению, под ним «по КД», наименование по артикулу, ГРН партии. */
function PartRows({ rows }: { rows?: EtkRow[] }) {
  if (!rows?.length) return null;
  return <div className="etk-block"><strong>Комплектующие</strong>{rows.map((row, index) =>
    <span key={`${row.art || row.name}:${index}`}>
      {row.art || row.name || "—"}{row.kd && <small>по КД: {row.kd}, аналог</small>}
      {row.art && row.name && <small>{row.name}</small>}{row.grn && <small>ГРН {row.grn}</small>}
      <small>{row.norm || "—"} {row.unit || ""}</small>
    </span>
  )}</div>;
}

function EtkOperationCard({ operation }: { operation: EtkOperation }) {
  const raw = operation as Record<string, unknown>;
  // Корпус при монтаже в разъём рисует HousingView ниже; остальные эскизы — SVG-строки etkDraw (как в печати).
  const figure = operation.housing && operation.type === "insTerm" ? "" : etkFigure(raw);
  return <article className="etk-operation">
    <header><span className="operation-number">{operation.position || "—"}</span><div><strong>{operation.code || "Без кода"}</strong><small>{operation.card?.name || operation.title || operation.connector || ""}</small></div></header>
    <div className="etk-chips" aria-label="Параметры операции">{etkChips(raw).map(([label, value]) =>
      <span key={label}><small>{label}</small>{value}</span>)}</div>
    <div className="etk-flow">
      <PartRows rows={operation.components} />
      <Rows title="Вход" rows={operation.semis} />
      <Rows title="Результат" rows={operation.result} />
      <Rows title="Время, мин" rows={operation.time} />
    </div>
    {operation.card?.steps?.length ? <ol className="etk-steps">{operation.card.steps.map((step, index) =>
      <li key={`${step.number}:${index}`}><span>{step.kind || "Операция"}</span>{step.text}{step.tolerance && <small>Допуск: {step.tolerance}</small>}</li>
    )}</ol> : <div className="warning-inline">Карточка операции отсутствует в библиотеке.</div>}
    {figure && <div className="etk-block etk-figure"><strong>Эскиз</strong><div className="etk-fig" dangerouslySetInnerHTML={{ __html: figure }} /></div>}
    {operation.housing && operation.type === "insTerm" && <HousingView housing={operation.housing} wires={operation.fields?.wires} />}
  </article>;
}

/** Same key as backend project_cards.operation_key: code::connector::side. */
function etkOperationKey(operation: EtkOperation): string {
  return `${String(operation.code ?? "").trim()}::${String(operation.connector ?? "").trim()}::${String(operation.side ?? "").trim()}`;
}

function etkTitle(operation: EtkOperation): string {
  return operation.card?.name || operation.title || operation.type || operation.code || "Операция";
}

function toolSub(tool: EtkTool): string {
  return [
    tool.program && tool.program !== "-" ? `прогр. ${tool.program}` : "",
    tool.parameters || "",
    tool.norm ? `${tool.norm}${tool.unit ? ` ${tool.unit}` : ""}` : "",
  ].filter(Boolean).join(" · ");
}

/** Оснастка по разделам: номер считается внутри раздела (SUPPLYMAP renderEtk_ / renderCard). */
function ToolingBySection({ tooling }: { tooling: EtkTool[] }) {
  if (!tooling.length) return <div className="empty">Оснастка не указана.</div>;
  const sections = [...new Set(tooling.map((tool) => tool.section || ""))];
  return <div className="tr-tooling">{sections.map((section) => <div className="tr-sec" key={section}>
    {section && <div className="tr-sec-h">{section}</div>}
    {tooling.filter((tool) => (tool.section || "") === section).map((tool, index) => <div className="tr-tool" key={`${section}:${index}`}>
      <span className="tr-n">{tool.number}</span>
      <span>{tool.name}{tool.inventory_number && <span className="tr-inv">{tool.inventory_number}</span>}
        {toolSub(tool) && <small>{toolSub(tool)}</small>}</span>
    </div>)}
  </div>)}</div>;
}

function editDraft(name: string, card: { safety?: string; defects?: string; revision?: string; steps?: EtkStep[]; tooling?: EtkTool[] }): OperationRecordEdit {
  return {
    name,
    safety: String(card.safety || "").replace(SAFETY_HEAD, ""),
    defects: card.defects || "",
    revision: card.revision || "",
    steps: (card.steps || []).map((step) => ({ kind: step.kind || "Операция", text: step.text || "", tolerance: step.tolerance || "" })),
    tooling: (card.tooling || []).map((tool) => ({
      section: tool.section || "Инструмент", name: tool.name || "", inventory_number: tool.inventory_number || "",
      program: tool.program || "", parameters: tool.parameters || "", norm: tool.norm || "", unit: tool.unit || "",
    })),
  };
}

/** Электронная ТК проекта с навигатором «← →» по операциям и правкой операции (SUPPLYMAP renderEtk_). */
function EtkView({ card, canEdit, revisions, onSaved }: {
  card: ProjectTechcard;
  canEdit: boolean;
  revisions: TechcardRevision[];
  onSaved: (updated: ProjectTechcard) => void;
}) {
  const list = operations(card.document);
  const [selected, setSelected] = useState(0);
  const [editing, setEditing] = useState(false);
  if (!list.length) return null;
  const index = Math.min(selected, list.length - 1);
  const operation = list[index];
  const next = list[index + 1];

  async function save(draft: OperationRecordEdit) {
    const updated = await techcardsApi.editOperation(card.id, card.version, etkOperationKey(operation), {
      ...operation,
      card: { ...operation.card, safety: draft.safety, defects: draft.defects, steps: draft.steps, tooling: draft.tooling },
    });
    setEditing(false);
    onSaved(updated);
  }

  return <div className="etk-view">
    <div className="etk-head">
      <button className="secondary small" onClick={() => openPrintDocument(`ТК ${card.project} ${card.item_index}`, etkPrintPages(card, revisions))}>Печать / PDF</button>
      <strong>Электронная ТК</strong><span>ревизия {card.revision || "—"} · {list.length} операций</span>
    </div>
    <div className="etk-wrap">
      <nav className="etk-nav-route" aria-label="Маршрут ЭТК">{list.map((item, position) =>
        <button key={`${etkOperationKey(item)}:${position}`} className={position === index ? "on" : ""} onClick={() => { setSelected(position); setEditing(false); }}>
          <span className="etk-no">{String(item.position || position + 1).padStart(2, "0")}</span>
          <span>{etkTitle(item)}{item.edited ? " ✏️" : ""}{item.connector && <small>{item.connector}</small>}</span>
        </button>)}
      </nav>
      <div className="etk-main">
        {editing && operation.card ? <TcCardEditForm
          code={operation.code || ""}
          nameEditable={false}
          initial={editDraft(etkTitle(operation) + (operation.connector ? ` — ${operation.connector}` : ""), operation.card)}
          hint={`Правка ТК проекта ${card.project} ${card.item_index}: сохранение поднимет ревизию до ${card.revision + 1} и запишется в журнал. Карточка операции в библиотеке не меняется; при пересборке эта правка сохранится. Комплектующие, нормы и время считает генератор — их меняют через конфигурацию сборки.`}
          onCancel={() => setEditing(false)}
          onSave={save}
        /> : <>
          <div className="etk-kicker">Операция {operation.position || index + 1} из {list.length} · <strong>{operation.code}</strong>
            {operation.edited && <b className="tr-edited"> · правлена {operation.edited.at}{operation.edited.by ? ` · ${operation.edited.by}` : ""}</b>}
            {canEdit && operation.card && card.status === "Выдана" && <button className="secondary small" onClick={() => setEditing(true)}>Править</button>}
          </div>
          <EtkOperationCard operation={operation} />
          {operation.card?.tooling?.length ? <div className="etk-block"><strong>Оснастка</strong><ToolingBySection tooling={operation.card.tooling} /></div> : null}
          <div className="etk-nav">
            <button className="secondary" disabled={!index} onClick={() => setSelected(index - 1)}>← Назад</button>
            <button className="secondary" disabled={!next} onClick={() => setSelected(index + 1)}>Далее: {next ? etkTitle(next) : "—"} →</button>
          </div>
        </>}
      </div>
    </div>
  </div>;
}

/** Категория карточки по префиксу кода (SUPPLYMAP PortalApp.html TC_REC_CATS_ / tcRecCat_). */
const RECORD_CATEGORIES: Array<[string, string]> = [
  ["CUT", "Резка"], ["STRIP", "Разделка"], ["TWIST", "Свивка"], ["PRS", "Опрессовка"], ["SDR", "Пайка и лужение"],
  ["INS", "Монтаж"], ["HEAT", "Усадка"], ["US", "УЗ-сварка"], ["MOLD", "Литьё"], ["PLASM", "Литьё"],
];
const CATEGORY_ORDER = [...new Set([...RECORD_CATEGORIES.map(([, name]) => name), "Прочее"])];
// Где стороны (A)/(B) расходятся, чья сторона — запись (backend operation_cards.RECORD_SIDE_PICK).
const RECORD_SIDE_PICK: Record<string, string> = { SDR_COAX_housing: "B", SDR_COAX_pin: "A" };

export function recordCategory(code: string): string {
  const prefix = String(code).split("_")[0];
  return RECORD_CATEGORIES.find(([key]) => key === prefix)?.[1] ?? "Прочее";
}

function shortDate(value: string): string {
  return new Date(value).toLocaleDateString("ru-RU").slice(0, 5);
}

function recordMeta(item: OperationCardSummary) {
  const norms = item.norms_known ? (item.norms ? (item.norms.operation_seconds ? ` · ${item.norms.operation_seconds} с` : "") : null) : "";
  return <>
    {item.steps} шаг.{item.controls ? ` · ${item.controls} контр.` : ""} · {item.tooling} оснастки
    {norms === null ? <> · <b className="danger-text">нет норм</b></> : norms}
    {item.changed_at && <> · <b className="tr-edited">правлена {shortDate(item.changed_at)}</b></>}
  </>;
}

// БД.ОП columns the DB_SCHEMA duplicates under its own keys (opNumber = «Номер» …) — shown once, by the sheet name.
const OP_SCHEMA_KEYS = new Set(["opNumber", "opName", "tOp", "tPrep", "tMachine"]);

function opCode(fields: Record<string, string>) { return (fields.opNumber || fields["Номер"] || "").trim(); }

function opColumns(fields: Record<string, string>): Array<[string, string]> {
  return Object.entries(fields).filter(([key, value]) => !OP_SCHEMA_KEYS.has(key) && String(value ?? "").trim() && String(value).trim().toUpperCase() !== "FALSE");
}

/** Every filled БД.ОП column of the operation: machine, program, semi-product, wire, connector, tool, times, prices, type.
    With «Справочник» edit the row is corrected here, as on the old БД.ОП tab (PortalRef.gs whitelist edit). */
function OpFields({ record, canEdit, onSaved }: { record: TechOperationRecord; canEdit?: boolean; onSaved?: (record: TechOperationRecord) => void }) {
  const [draft, setDraft] = useState<Record<string, string> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const rows = opColumns(record.fields);
  async function save() {
    if (!draft) return;
    const edits = Object.entries(draft).filter(([key, value]) => (record.fields[key] ?? "") !== value)
      .map(([field, value]) => ({ field, old: record.fields[field] ?? "", value }));
    if (!edits.length) { setDraft(null); return; }
    setBusy(true); setError("");
    try { onSaved?.(await referencesApi.editRecord(record.id, edits)); setDraft(null); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Строка БД.ОП не сохранена"); }
    finally { setBusy(false); }
  }
  return <div className="tr-block"><h4>Строка БД.ОП <span>{rows.length} полей</span>
    {canEdit && !draft && <> <button type="button" className="secondary small" onClick={() => setDraft(Object.fromEntries(Object.entries(record.fields).filter(([key]) => !OP_SCHEMA_KEYS.has(key))))}>Править строку</button></>}</h4>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {draft ? <>
      <div className="reference-fields db-form">{Object.entries(draft).map(([key, value]) => <label key={key}><small>{key}</small>
        <input aria-label={`БД.ОП: ${key}`} value={value} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>)}</div>
      <div className="form-actions"><button className="primary" disabled={busy} onClick={() => void save()}>Сохранить строку</button> <button className="secondary" onClick={() => setDraft(null)}>Отмена</button></div>
    </> : rows.length ? <dl className="op-fields">{rows.map(([key, value]) => <div key={key}><dt>{key}</dt><dd className="pre-line">{value}</dd></div>)}</dl>
      : <div className="empty">В БД.ОП строка пустая.</div>}
  </div>;
}

/** An operation that exists only in БД.ОП: no БД.ТК sheet, so the generator has nothing to put on a techcard page. */
function OpOnlyDetail({ record, canEdit, onSaved, onBack }: { record: TechOperationRecord; canEdit: boolean; onSaved: (record: TechOperationRecord) => void; onBack: () => void }) {
  const fields = record.fields;
  return <article className="panel template-detail tr-paper">
    <div className="form-actions"><button className="secondary" onClick={onBack}>← Все операции</button></div>
    <div className="tr-head">
      <div><small>Операция</small><b>{opCode(fields)}</b></div>
      <div className="tr-head-name"><small>Наименование</small><b>{fields.opName || fields["Название"] || "—"}</b></div>
      <div><small>Время операции</small><b>{fields.tOp ? `${fields.tOp} с` : "—"}</b></div>
      <div><small>Подготовка</small><b>{fields.tPrep ? `${fields.tPrep} с` : "—"}</b></div>
    </div>
    <div className="warning-inline">Шаблона листа (БД.ТК) у этой операции нет: шагов, оснастки и контроля нет, генератор не выводит её отдельным листом техкарты. Нормы времени из БД.ОП в расчёт идут.</div>
    <OpFields record={record} canEdit={canEdit} onSaved={onSaved} />
  </article>;
}

type OperationEntry = { key: string; code: string; name: string; template?: OperationCardSummary; op?: Record<string, string> };

/** Операции карточками (SUPPLYMAP openTcRecords): шаблоны ТК + операции БД.ОП без шаблона, по категориям. */
function OperationCards({ entries, onOpen, toolbar }: { entries: OperationEntry[]; onOpen: (entry: OperationEntry) => void; toolbar?: ReactNode }) {
  const [chip, setChip] = useState("");
  const [query, setQuery] = useState("");
  const needle = query.trim().toLocaleLowerCase("ru-RU");
  const searched = useMemo(() => !needle ? entries : entries.filter((entry) => [
    entry.code, entry.name, entry.template?.first_step, entry.template?.step_text, ...Object.values(entry.op ?? {}),
  ].join(" ").toLocaleLowerCase("ru-RU").includes(needle)), [entries, needle]);
  const counts = useMemo(() => {
    const byName = new Map<string, number>();
    searched.forEach((entry) => byName.set(recordCategory(entry.code), (byName.get(recordCategory(entry.code)) ?? 0) + 1));
    return byName;
  }, [searched]);
  const shown = CATEGORY_ORDER.filter((name) => counts.has(name) && (!chip || chip === name));
  const missing = entries.filter((entry) => !entry.template).length;
  return <>
    <div className="tr-toolbar">
      <input className="search-input" aria-label="Поиск: Операции" placeholder="Код, название, шаг, машина…" value={query} onChange={(event) => setQuery(event.target.value)} />
      {missing > 0 && <small className="tr-meta">без шаблона ТК: {missing}</small>}
      <span className="tr-toolbar-end">{toolbar}</span>
    </div>
    <div className="tr-chips">
      <button className={chip ? "" : "on"} onClick={() => setChip("")}>Все · {searched.length}</button>
      {CATEGORY_ORDER.filter((name) => counts.has(name)).map((name) => <button key={name} className={chip === name ? "on" : ""} onClick={() => setChip(chip === name ? "" : name)}>{name} · {counts.get(name)}</button>)}
    </div>
    {!shown.length && <div className="empty">Ничего не найдено.</div>}
    {shown.map((name) => <section className="tr-group" key={name}>
      <h3>{name} <span>{counts.get(name)}</span></h3>
      <div className="tr-grid">
        {searched.filter((entry) => recordCategory(entry.code) === name).map((entry) => {
          const machine = entry.op?.["Машина"]?.trim();
          const type = entry.op?.["Тип операции"]?.trim();
          return <button type="button" key={entry.key} className={entry.template ? "tr-card" : "tr-card tr-card-op"} onClick={() => onOpen(entry)}>
            <strong className="tr-code">{entry.code}</strong>
            <span className="tr-name">{entry.name || "—"}</span>
            {entry.template ? (entry.template.first_step && <span className="tr-first">{entry.template.first_step}</span>)
              : <span className="tr-first"><b className="danger-text">нет шаблона ТК</b> · только строка БД.ОП</span>}
            {(machine || type) && <span className="tr-meta">{[machine, type].filter(Boolean).join(" · ")}</span>}
            {entry.template ? <small className="tr-meta">{recordMeta(entry.template)}</small>
              : entry.op?.tOp ? <small className="tr-meta">{entry.op.tOp} с</small> : null}
          </button>;
        })}
      </div>
    </section>)}
  </>;
}

/** Просмотр: карточка в форме листа техкарты (SUPPLYMAP openTcRecords → renderCard). */
function TemplateDetail({ template, op, canEdit, canEditOp, onOpSaved, onBack, onEdit }: {
  template: OperationCardDetail; op?: TechOperationRecord; canEdit: boolean; canEditOp: boolean; onOpSaved: (record: TechOperationRecord) => void;
  onBack: () => void; onEdit: () => void;
}) {
  const norms = template.norms;
  const known = template.norms_known;
  return <article className="panel template-detail tr-paper">
    <div className="form-actions"><button className="secondary" onClick={onBack}>← Все операции</button>{canEdit && <button className="primary" onClick={onEdit}>Править</button>}</div>
    <div className="tr-head">
      <div><small>Операция</small><b>{template.code}</b></div>
      <div className="tr-head-name"><small>Наименование</small><b>{template.name}</b></div>
      <div><small>Время операции</small><b>{known ? (norms ? `${norms.operation_seconds || "—"} с` : <span className="danger-text">нет в БД.ОП</span>) : "справочник не загружен"}</b></div>
      <div><small>Подготовка</small><b>{norms ? `${norms.setup_seconds || "—"} с` : "—"}</b></div>
    </div>
    {template.document.safety && <div className="warning-inline"><strong>Требование безопасности</strong><br />{template.document.safety.replace(SAFETY_HEAD, "")}</div>}
    <div className="tr-block"><h4>Оснастка и расходники</h4><ToolingBySection tooling={template.document.tooling} /></div>
    <div className="tr-block"><h4>Вход и результат</h4><div className="empty">Комплектующие, полуфабрикат на входе, результат и расчётное время подставляет генератор при сборке техкарты по проекту.</div></div>
    <div className="tr-block"><h4>Ход операции <span>{template.document.steps.length}</span></h4>
      <table className="tr-steps"><thead><tr><th>№</th><th>Содержание</th><th>Допуск</th></tr></thead><tbody>
        {template.document.steps.map((step, index) => <tr key={`${step.number}:${index}`} className={step.kind === "Контроль" ? "ctl" : ""}>
          <td>{step.number}</td><td><small>{step.kind}</small><div className="pre-line">{step.text}</div></td>
          <td>{step.tolerance && step.tolerance !== "-" ? step.tolerance : "—"}</td>
        </tr>)}
      </tbody></table>
    </div>
    {template.document.defects && <div className="tr-block"><h4>Типовые дефекты</h4><p>{template.document.defects}</p></div>}
    {op && <OpFields record={op} canEdit={canEditOp} onSaved={onOpSaved} />}
    <div className="tr-foot">{template.changed_at ? `Изменено ${new Date(template.changed_at).toLocaleString("ru-RU")}${template.changed_by ? ` · ${template.changed_by}` : ""}` : "Перенесено из шаблона БД.ТК, в ERP не правилась"}</div>
  </article>;
}

/** Электронная ТК сборки: генератор, сборка, выдача и ревизии. Живёт в карточке проекта (вкладки «Генератор» и
    «ТК КБn»), как в SUPPLYMAP: с ТК работают из проекта, отдельного списка техкарт там нет. */
export function ProjectEtk({ card, principal, onChange }: { card: ProjectTechcard; principal: Principal; onChange: (card: ProjectTechcard) => void }) {
  const [revisions, setRevisions] = useState<TechcardRevision[]>([]);
  const [configText, setConfigText] = useState("{}");
  const generatedConfig = useRef<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [kit, setKit] = useState<{ data: KitsetBom | null; error: string }>({ data: null, error: "" });
  const canEdit = principal.permissions.techcards === "edit";
  const canGenerate = Boolean(principal.permissions.tcgen);
  useEffect(() => {
    setConfigText(JSON.stringify(defaultConfig(card), null, 2));
    void techcardsApi.revisions(card.id).then((result) => setRevisions(result.items)).catch(() => setRevisions([]));
  }, [card.id, card.version]);
  useEffect(() => {
    // SUPPLYMAP portalTcGenData → tcLoadBomFromKit: состав из набора проекта + строки листа КБn расчёта;
    // набора или сборки нет — генератор показывает ошибку.
    setKit({ data: null, error: "" });
    let live = true;
    void kitsetsApi.bom(card.project, card.item_index)
      .then((result) => { if (live) setKit({ data: result, error: "" }); })
      .catch((reason) => { if (live) setKit({ data: null, error: reason instanceof Error ? reason.message : "Состав сборки не прочитан" }); });
    return () => { live = false; };
  }, [card.project, card.item_index]);
  async function build(config: Record<string, unknown>) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const updated = await techcardsApi.autoBuild(card.id, card.version, config);
      onChange(updated);
      const warningCount = warnings(updated.document).length;
      setNotice(`${card.status === "Выдана" ? "ЭТК пересобрана новой ревизией" : "Черновик ЭТК собран"}${warningCount ? ` · предупреждений: ${warningCount}` : ""}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "ЭТК не собрана");
    } finally { setBusy(false); }
  }

  async function autoBuild() {
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(configText) as Record<string, unknown>;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Конфигурация содержит неверный JSON");
      return;
    }
    await build(config);
  }

  async function discardDraft() {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      onChange(await techcardsApi.discardDraft(card.id, card.version));
      setNotice("Черновик ЭТК удалён");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Черновик не удалён");
    } finally { setBusy(false); }
  }

  async function issue() {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      onChange(await techcardsApi.issue(card.id, card.version));
      setNotice("ЭТК выдана, ревизия 1 зафиксирована");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "ЭТК не выдана");
    } finally { setBusy(false); }
  }

  return <>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    <article className="panel project-etk">
      <div className="panel-heading"><strong>{card.project} · {card.item_index}</strong><span>{card.status} · ревизия {card.revision || "—"}</span></div>
      {warnings(card.document).map((warning) => <div className="warning-inline" key={warning}>{warning}</div>)}
      <TopologyView value={topology(card.document)} />
      <EtkView key={card.id} card={card} canEdit={canEdit} revisions={revisions} onSaved={(updated) => {
        onChange(updated);
        setNotice(`Операция сохранена${updated.status === "Выдана" ? ` · ревизия ${updated.revision}` : ""}`);
      }} />
      {!operations(card.document).length && <div className="etk-empty">ЭТК ещё не собрана — заполни генератор ниже и нажми «Создать техкарты».</div>}
      {canGenerate && <TcGenerator card={card} kit={kit.data} kitError={kit.error} busy={busy} onBuild={build} onDiscard={discardDraft} onConfig={(config) => { generatedConfig.current = config; }} />}
      {canGenerate && <details className="build-config"><summary>JSON-конфигурация (для эксперта)</summary>
        <div className="form-actions"><button className="secondary small" onClick={() => { if (generatedConfig.current) setConfigText(JSON.stringify(generatedConfig.current, null, 2)); }}>Взять из формы</button></div>
        <label>Config генератора SUPPLYMAP<textarea aria-label="Конфигурация автоматической сборки" rows={14} value={configText} onChange={(event) => setConfigText(event.target.value)} /></label>
        <div className="form-actions"><button className="secondary" disabled={busy} onClick={() => void autoBuild()}>Собрать ЭТК автоматически</button></div>
      </details>}
      {canEdit && card.status === "Черновик" && card.document && <div className="form-actions"><button className="primary" disabled={busy} onClick={() => void issue()}>Выдать ЭТК</button></div>}
      <div className="panel-heading"><strong>Журнал ревизий</strong><span>{revisions.length}</span></div>
      {revisions.map((revision) => <div className="panel-heading" key={revision.id}><span>Ревизия {revision.revision} · {revision.reason}</span><span>{revision.actor_name} · {new Date(revision.occurred_at).toLocaleString("ru-RU")}</span></div>)}
      {!revisions.length && <div className="etk-empty">Выданных ревизий пока нет.</div>}
    </article>
  </>;
}

/** «← проект»: назад в карточку проекта, откуда открыли ЭТК. */
function backToProject(project: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("section", "projects");
  url.searchParams.delete("card");
  url.searchParams.set("project", project);
  window.history.pushState({}, "", url);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

/** NAV-1: «Техкарты проектов» and «Шаблоны техкарт» are separate sections, as in SUPPLYMAP; without `view` both show.
    `embedded`: the templates view as a tab of «База данных» — the tab bar is the heading. */
export function TechcardsPage({ principal, view, embedded = false }: { principal: Principal; view?: "projects" | "templates"; embedded?: boolean }) {
  const showProjects = view !== "templates";
  const showLibrary = view !== "projects";
  const [cards, setCards] = useState<ProjectTechcard[]>([]);
  const [templates, setTemplates] = useState<OperationCardSummary[]>([]);
  const [libraryCommit, setLibraryCommit] = useState("");
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  // БД.ОП rows (only with the reference-book grant): op-only operations become cards too, and every card shows its row.
  const [opRows, setOpRows] = useState<TechOperationRecord[]>([]);
  const [selectedOpCode, setSelectedOpCode] = useState("");
  const [templateDetail, setTemplateDetail] = useState<OperationCardDetail | null>(null);
  const [editingTemplate, setEditingTemplate] = useState(false);
  const [sheetsOpen, setSheetsOpen] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  // ?project= — opened as «Карточка проекта» from the production plan: only that project's cards.
  const [projectFilter, setProjectFilter] = useState(() => new URLSearchParams(window.location.search).get("project") ?? "");
  // ?card= — ЭТК открыта из карточки проекта (вкладки «Генератор» / «ТК КБn»): как в SUPPLYMAP, видна только она.
  const [linkedCard] = useState(() => new URLSearchParams(window.location.search).get("card") ?? "");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  // ID-3: the matrix alone decides — «Техкарты проектов» edit for cards, «Генератор техкарт» (tcgen) for the builder.
  const canEdit = principal.permissions.techcards === "edit";
  const canEditTemplates = principal.permissions.erptctpl === "edit";
  const canGenerate = Boolean(principal.permissions.tcgen);
  const selected = useMemo(() => cards.find((card) => card.id === selectedId) ?? null, [cards, selectedId]);
  const shownCards = useMemo(() => {
    const project = projectFilter.trim().toLocaleUpperCase("ru-RU");
    return project ? cards.filter((card) => card.project.toLocaleUpperCase("ru-RU") === project) : cards;
  }, [cards, projectFilter]);
  const cardColumns: DataColumn<ProjectTechcard>[] = [
    { key: "project", title: "Проект", value: (card) => card.project,
      render: (card) => <button className="link-button" onClick={() => setSelectedId(card.id)}><strong>{card.project}</strong></button> },
    { key: "item", title: "Изделие", value: (card) => `${card.item_index} · ${card.product_name}`,
      render: (card) => <button className="link-button" onClick={() => setSelectedId(card.id)}>{card.item_index} · {card.product_name}</button> },
    { key: "status", title: "Статус", value: (card) => card.status },
    { key: "revision", title: "Ревизия", value: (card) => card.revision || "—", numeric: true },
    { key: "deadline", title: "Срок", value: (card) => card.deadline || "—" },
  ];
  // Одна запись на код операции: пара (A)/(B) — одна карточка (SUPPLYMAP tcRecordsByCode_).
  const records = useMemo(() => {
    const byCode = new Map<string, OperationCardSummary>();
    templates.forEach((item) => {
      if (!byCode.has(item.code) || RECORD_SIDE_PICK[item.code] === item.side) byCode.set(item.code, item);
    });
    return [...byCode.values()];
  }, [templates]);
  const opByCode = useMemo(() => {
    const byCode = new Map<string, TechOperationRecord>();
    opRows.forEach((row) => { const code = opCode(row.fields); if (code && !byCode.has(code)) byCode.set(code, row); });
    return byCode;
  }, [opRows]);
  // Operations and templates are one entity keyed by code: 107 templates ⊂ 139 БД.ОП rows (owner 29.09.2026 «лучше на карточки»).
  const operationEntries = useMemo<OperationEntry[]>(() => {
    const entries: OperationEntry[] = records.map((item) => ({ key: item.id, code: item.code, name: item.name, template: item, op: opByCode.get(item.code)?.fields }));
    const known = new Set(records.map((item) => item.code));
    opByCode.forEach(({ fields }, code) => {
      if (!known.has(code)) entries.push({ key: `op:${code}`, code, name: fields.opName || fields["Название"] || "", op: fields });
    });
    return entries;
  }, [records, opByCode]);
  const selectedOp = selectedOpCode ? opByCode.get(selectedOpCode) : undefined;
  const canEditOps = principal.permissions.erpref === "edit";
  const replaceOp = (record: TechOperationRecord) => setOpRows((rows) => rows.map((row) => (row.id === record.id ? record : row)));

  async function load() {
    try {
      const [projectResult, libraryResult] = await Promise.all([
        showProjects ? techcardsApi.list() : Promise.resolve({ items: [] as ProjectTechcard[] }),
        showLibrary ? techcardsApi.templates() : Promise.resolve({ items: [] as OperationCardSummary[], metadata: null }),
      ]);
      setCards((current) => current.length ? current : projectResult.items);
      // «Проекты» → «Генератор»/«ТК КБn» открывают здесь карточку изделия: ?card=<id>; план — ?project=<№>.
      const linked = new URLSearchParams(window.location.search).get("card") || "";
      const wanted = projectFilter.trim().toLocaleUpperCase("ru-RU");
      const first = projectResult.items.find((card) => !wanted || card.project.toLocaleUpperCase("ru-RU") === wanted) ?? projectResult.items[0];
      setSelectedId((current) => current || (projectResult.items.some((card) => card.id === linked) ? linked : "") || first?.id || "");
      setTemplates(libraryResult.items);
      setLibraryCommit(libraryResult.metadata?.source_commit || "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить техкарты");
    }
  }

  useEffect(() => { void load(); }, []);
  useEffect(() => {
    if (!showLibrary || !principal.permissions.erpref) return;
    void referencesApi.techOperations("op", "", 5000).then((result) => setOpRows(result.records)).catch(() => undefined);
  }, [showLibrary, principal.permissions.erpref]);
  useEffect(() => {
    setEditingTemplate(false);
    if (!selectedTemplateId) { setTemplateDetail(null); return; }
    void techcardsApi.template(selectedTemplateId).then(setTemplateDetail).catch(() => setTemplateDetail(null));
  }, [selectedTemplateId]);

  async function saveTemplate(draft: OperationRecordEdit) {
    if (!templateDetail) return;
    const saved = await techcardsApi.saveTemplate(templateDetail.id, draft);
    setTemplateDetail(saved);
    setEditingTemplate(false);
    setNotice(`Карточка ${saved.code} сохранена`);
    const library = await techcardsApi.templates();
    setTemplates(library.items);
  }

  function replaceCard(updated: ProjectTechcard) {
    setCards((current) => current.map((card) => card.id === updated.id ? updated : card));
  }

  return <section className={embedded ? "techcards-page embedded" : "page techcards-page"}>
    {showProjects ? <>
    {linkedCard && selected ? <header className="page-head">
      <button className="secondary small" onClick={() => backToProject(selected.project)}>← {selected.project}</button>
      <h1>Техкарта {selected.item_index}</h1>
    </header> : <header className="page-head"><h1>Техкарты проектов</h1><span className="page-count">{cards.length}</span></header>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {projectFilter && <div className="plan-acts"><span>Проект {projectFilter}</span><button className="secondary small" onClick={() => setProjectFilter("")}>Все проекты</button></div>}
    {!(linkedCard && selected) && <DataTable
      rows={shownCards}
      columns={cardColumns}
      rowKey={(card) => card.id}
      storageKey="techcards-projects"
      label="Техкарты проектов"
      exportName="Техкарты проектов"
      presets={[
        { label: "Черновики", test: (card) => card.status === "Черновик" },
        { label: "Выданные", test: (card) => card.status === "Выдана" },
      ]}
      rowClassName={(card) => (selectedId === card.id ? "selected" : "")}
      emptyText={projectFilter ? `Карточек проекта ${projectFilter} нет.` : cards.length ? "Ничего не найдено." : "Техкарт пока нет: их заводит «Генератор» в карточке проекта."}
    />}
    {selected && <ProjectEtk card={selected} principal={principal} onChange={replaceCard} />}
    </> : <>
    {!embedded && <header className="page-head"><h1>Шаблоны техкарт</h1></header>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    </>}
    {showLibrary && <>
    {!embedded && <header className="page-head library-head"><h2>Библиотека операций</h2><span className="page-count">{records.length}</span></header>}
    <p className="page-sub">Снимок SUPPLYMAP {libraryCommit ? libraryCommit.slice(0, 8) : "не загружен"}; карточка сохраняется внутри каждой собранной ЭТК. Карточка операции — то, из чего собирается лист техкарты: шаги, контроль с допусками, оснастка, безопасность, дефекты и строка БД.ОП (машина, программа, провод, разъём, время). Пунктиром — операции БД.ОП без шаблона листа. Открой карточку, чтобы посмотреть{canEdit ? " или поправить" : ""}.</p>
    {sheetsOpen ? <TcTemplateSheets canEdit={canEditTemplates} onBack={() => setSheetsOpen(false)} onChanged={() => {
      void techcardsApi.templates().then((library) => setTemplates(library.items)).catch(() => undefined);
    }} /> : selectedOp ? <OpOnlyDetail record={selectedOp} canEdit={canEditOps} onSaved={replaceOp} onBack={() => setSelectedOpCode("")} /> : templateDetail && selectedTemplateId ? (editingTemplate ? <TcCardEditForm
      code={templateDetail.code}
      initial={editDraft(templateDetail.name, templateDetail.document)}
      hint="Правки пишутся в библиотеку ERP и попадают в ТК проектов при следующей сборке; при обновлении снимка SUPPLYMAP правленая карточка сохраняется. Пустые шаги и строки оснастки при сохранении отбрасываются, номера проставятся сами."
      onCancel={() => setEditingTemplate(false)}
      onSave={saveTemplate}
    /> : <TemplateDetail template={templateDetail} op={opByCode.get(templateDetail.code)} canEditOp={canEditOps} onOpSaved={replaceOp} canEdit={canEditTemplates} onBack={() => setSelectedTemplateId("")} onEdit={() => setEditingTemplate(true)} />) : <>
    <OperationCards entries={operationEntries} toolbar={<button type="button" className="secondary small" title="Каталог листов БД.ТК, из которого собирает генератор" onClick={() => setSheetsOpen(true)}>Шаблоны-листы</button>} onOpen={(entry) => {
      if (entry.template) { setSelectedOpCode(""); setSelectedTemplateId(entry.template.id); } else setSelectedOpCode(entry.code);
    }} />
    </>}
    </>}
  </section>;
}
