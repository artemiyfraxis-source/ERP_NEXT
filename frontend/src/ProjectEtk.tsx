import { useEffect, useRef, useState, type ReactNode } from "react";
import { TcGenerator } from "./TcGenerator";
import {
  kitsetsApi,
  techcardsApi,
  type KitsetBom,
  type OperationRecordEdit,
  type Principal,
  type ProjectTechcard,
  type TechcardRevision,
  failureText,
} from "./api";
import { etkChips, etkDrawHarness, etkFigure, type EtkTopologyData } from "./etkDraw";
import { etkPrintPages } from "./etkPrint";
import { defectPicto, parseDefects, solderMode, toolingSections, type SolderMode } from "./etkOperation";
import { openPrintDocument } from "./print";
import { SAFETY_HEAD, TcCardEditForm } from "./TcCardEditForm";

/* Электронная ТК сборки проекта и её генератор — вкладки «Генератор» и «ТК КБn» карточки проекта (SUPPLYMAP
   PortalApp kitCardRender_ → mountGen_ / renderTc_ → renderEtk_). */
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
    <strong>{housing.title}</strong><small>{housing.art}</small>
    {housing.note && <small>{housing.note}</small>}
  </div>;
}

/** Схема жгута один раз, компактной SVG-картинкой, как в SUPPLYMAP (etkDrawHarness_): разъёмы, пучок жил, число
    проводов и длина. Раскладку контактов показывает только операция монтажа, где она нужна. */
function HarnessView({ value }: { value: EtkTopology | null }) {
  const svg = etkDrawHarness(value as EtkTopologyData | null);
  if (!svg) return null;
  return <details className="etk-schema" open>
    <summary>Схема жгута</summary>
    <div className="etk-harness" dangerouslySetInnerHTML={{ __html: svg }} />
  </details>;
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

/** Операция ЭТК в порядке, который задал производство (SUPPLYMAP renderEtk_, доведённый): безопасность (свёрнута) →
 *  1 вход: комплектующие и полуфабрикаты → 2 инструмент, оборудование, расходники + режим пайки (жало, температура) →
 *  3 ход операции → 4 типовые дефекты с пиктограммами → 5 эскиз → 6 результат и расчётное время. */
function EtkOperationCard({ operation, number, total, actions }: { operation: EtkOperation; number: number; total: number; actions?: ReactNode }) {
  const raw = operation as Record<string, unknown>;
  // Раскладка корпуса при монтаже — только если известно, какой провод в какой контакт: пустая сетка повторяла бы
  // схему жгута. Остальные эскизы — SVG-строки etkDraw (как в печати).
  const pinned = (operation.fields?.wires ?? []).some((wire) => wire.pins?.length);
  const housing = operation.housing && operation.type === "insTerm" ? operation.housing : null;
  const figure = housing ? "" : etkFigure(raw);
  const tooling = operation.card?.tooling ?? [];
  const solder = solderMode(raw);
  const safety = String(operation.card?.safety || "").replace(SAFETY_HEAD, "").trim();
  const resultLabel = String(raw.result_label || "") || "Результат";
  const hasInput = Boolean(operation.components?.length || operation.semis?.length);
  const hasResult = Boolean(operation.result?.length || operation.time?.length);
  // Номера разделов подряд по показанным блокам (эскиза бывает нет).
  let shown = 0;
  const next = () => ++shown;
  return <article className="etk-op">
    <header className="etk-op-head">
      <span className="operation-number">{number}</span>
      <div>
        <h3>{etkTitle(operation)}{operation.connector && !/^\d+$/.test(operation.connector) && <span className="etk-conn"> — {operation.connector}</span>}</h3>
        <small><code>{operation.code || "без кода"}</code> · операция {number} из {total}
          {operation.edited && <b className="tr-edited"> · правлена {operation.edited.at}{operation.edited.by ? ` · ${operation.edited.by}` : ""}</b>}</small>
      </div>
      {actions}
    </header>
    {etkChips(raw).length > 0 && <div className="etk-chips" aria-label="Параметры операции">{etkChips(raw).map(([label, value]) =>
      <span key={label}><small>{label}</small>{value}</span>)}</div>}
    {safety && <details className="etk-safety"><summary>Требование безопасности</summary><div>{safety}</div></details>}
    {!operation.card && <div className="warning-inline">Карточка операции отсутствует в библиотеке.</div>}
    <section className="etk-sec" aria-label="Вход">
      <h4><span className="etk-sec-n">{next()}</span>Вход</h4>
      {hasInput ? <div className="etk-flow">
        <PartRows rows={operation.components} />
        <Rows title="Полуфабрикат на входе" rows={operation.semis} />
      </div> : <div className="empty">Комплектующих и полуфабрикатов на входе нет.</div>}
    </section>
    <section className="etk-sec" aria-label="Инструмент, оборудование, расходники">
      <h4><span className="etk-sec-n">{next()}</span>Инструмент, оборудование, расходники</h4>
      {solder && <SolderPanel mode={solder} />}
      <ToolingBySection tooling={tooling} />
    </section>
    {operation.card?.steps?.length ? <section className="etk-sec" aria-label="Ход операции">
      <h4><span className="etk-sec-n">{next()}</span>Ход операции</h4>
      <ol className="etk-steps">{operation.card.steps.map((step, index) =>
        <li key={`${step.number}:${index}`} className={step.kind && step.kind !== "Операция" ? "ctl" : ""}>{step.kind && step.kind !== "Операция" && <span>{step.kind}</span>}{step.text}{step.tolerance && step.tolerance.trim() !== "-" && <small>Допуск: {step.tolerance}</small>}</li>
      )}</ol></section> : null}
    {operation.card?.defects && <DefectsView text={operation.card.defects} number={next()} />}
    {(figure || (housing && pinned)) && <section className="etk-sec" aria-label="Эскиз">
      <h4><span className="etk-sec-n">{next()}</span>Эскиз</h4>
      {figure && <div className="etk-fig" dangerouslySetInnerHTML={{ __html: figure }} />}
      {housing && pinned && <HousingView housing={housing} wires={operation.fields?.wires} />}
    </section>}
    {hasResult && <section className="etk-sec" aria-label="Результат">
      <h4><span className="etk-sec-n">{next()}</span>Результат</h4>
      <div className="etk-flow">
        <Rows title={resultLabel} rows={operation.result} />
        <Rows title="Расчётное время, мин" rows={operation.time} />
      </div>
    </section>}
  </article>;
}

/** Режим пайки: жало и температура обязательны. Не из карты операции — помечено «по умолчанию ERP». */
function SolderPanel({ mode }: { mode: SolderMode }) {
  const assumed = <span className="etk-assumed" title="В карте операции не указано — значение по умолчанию ERP для Sn63Pb37. Уточнить технологу и вписать в карту операции.">по умолчанию, уточнить</span>;
  return <div className="etk-solder" aria-label="Режим пайки">
    <div><small>{mode.method === "bath" ? "Паяльник" : "Жало паяльника"}</small><b>{mode.tip}</b>{mode.tipAssumed && assumed}</div>
    <div><small>{mode.method === "bath" ? "Температура ванны" : mode.method === "induction" ? "Нагрев" : "Температура жала"}</small>
      <b>{mode.temperature}{mode.tolerance && ` (${mode.tolerance})`}</b>{mode.temperatureAssumed && assumed}</div>
  </div>;
}

/** Типовые дефекты карточкой с пиктограммой на каждый дефект (в Google — сплошной текст). */
function DefectsView({ text, number }: { text: string; number: number }) {
  const parsed = parseDefects(text);
  const count = parsed.groups.reduce((sum, group) => sum + group.items.length, 0);
  return <details className="etk-sec etk-defects" open aria-label="Типовые дефекты">
    <summary><h4><span className="etk-sec-n">{number}</span>Типовые дефекты{count ? ` · ${count}` : ""}</h4></summary>
    {count ? <>
      {parsed.intro.length > 0 && <small className="etk-def-intro">{parsed.intro.join(" · ")}</small>}
      {parsed.groups.map((group, index) => <div className="etk-def-group" key={`${group.title}:${index}`}>
        {group.title && <div className="tr-sec-h">{group.title}</div>}
        <div className="etk-def-grid">{group.items.map((item, position) => <div className="etk-def" key={`${item.name}:${position}`}>
          <span className="etk-def-pic" dangerouslySetInnerHTML={{ __html: defectPicto(item.kind) }} />
          <div><b>{item.name}</b><small>{item.text}</small></div>
        </div>)}</div>
      </div>)}
    </> : <div className="etk-def-text">{text}</div>}
  </details>;
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

/** Оснастка по разделам: инструмент → оборудование → расходники → прочее; номер считается внутри раздела
 *  (SUPPLYMAP renderEtk_ / renderCard). */
export function ToolingBySection({ tooling }: { tooling: EtkTool[] }) {
  if (!tooling.length) return <div className="empty">Инструмент, оборудование и расходники не указаны.</div>;
  const sections = toolingSections(tooling.map((tool) => tool.section || ""));
  return <div className="tr-tooling">{sections.map((section) => <div className="tr-sec" key={section}>
    {section && <div className="tr-sec-h">{section}</div>}
    {tooling.filter((tool) => (tool.section || "") === section).map((tool, index) => <div className="tr-tool" key={`${section}:${index}`}>
      <span className="tr-n">{tool.number}</span>
      <span>{tool.name}{tool.inventory_number && <span className="tr-inv">{tool.inventory_number}</span>}
        {toolSub(tool) && <small>{toolSub(tool)}</small>}</span>
    </div>)}
  </div>)}</div>;
}

export function editDraft(name: string, card: { safety?: string; defects?: string; revision?: string; steps?: EtkStep[]; tooling?: EtkTool[] }): OperationRecordEdit {
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


/** Электронная ТК: слева маршрут, справа выбранная операция, «← →» по маршруту и правка операции (SUPPLYMAP renderEtk_). */
function EtkView({ card, canEdit, onSaved }: { card: ProjectTechcard; canEdit: boolean; onSaved: (updated: ProjectTechcard) => void }) {
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

  return <div className="etk-wrap">
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
        <EtkOperationCard operation={operation} number={operation.position || index + 1} total={list.length}
          actions={canEdit && operation.card && card.status === "Выдана" ? <button className="secondary small" onClick={() => setEditing(true)}>✏️ Править</button> : null} />
        <div className="etk-nav">
          <button className="secondary small" disabled={!index} onClick={() => setSelected(index - 1)}>← Назад</button>
          <button className="secondary small" disabled={!next} onClick={() => setSelected(index + 1)}>Далее: {next ? etkTitle(next) : "—"} →</button>
        </div>
      </>}
    </div>
  </div>;
}

function stamp(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
}

/** Вкладка «ТК КБn»: одна строка о карте и действия, схема жгута один раз, маршрут слева и операция справа, журнал
    ревизий свёрнут. Правят операции здесь («✏️ Править»); пересборка — в «Генераторе». */
export function EtkSheet({ card, principal, title, onChange, onRebuild }: {
  card: ProjectTechcard; principal: Principal; title?: ReactNode;
  onChange: (card: ProjectTechcard) => void; onRebuild?: () => void;
}) {
  const [revisions, setRevisions] = useState<TechcardRevision[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const canEdit = principal.permissions.techcards === "edit";
  const canGenerate = Boolean(principal.permissions.tcgen);
  const list = operations(card.document);
  const issued = card.status === "Выдана";
  useEffect(() => {
    void techcardsApi.revisions(card.id).then((result) => setRevisions(result.items)).catch(() => setRevisions([]));
  }, [card.id, card.version]);

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

  return <div className="etk-sheet">
    <div className="etk-top">
      <div className="etk-title">{title ?? <strong>{card.project} · {card.item_index}</strong>}
        <span className="muted">{list.length} операций · {issued ? `ревизия ${card.revision}${card.issued_at ? ` · выдана ${stamp(card.issued_at)}` : ""}` : "черновик, не выдана"}</span></div>
      {list.length > 0 && <button className="secondary small" onClick={() => openPrintDocument(`ТК ${card.project} ${card.item_index}`, etkPrintPages(card, revisions))}>🖨 Печать / PDF</button>}
      {canGenerate && onRebuild && <button className="secondary small" onClick={onRebuild}>⚙️ Пересобрать</button>}
      {canEdit && !issued && card.document && <button className="primary small" disabled={busy} onClick={() => void issue()}>Выдать ЭТК</button>}
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {warnings(card.document).length > 0 && <details className="etk-warnings"><summary>⚠ Предупреждения сборки · {warnings(card.document).length}</summary>
      {warnings(card.document).map((warning) => <div key={warning}>{warning}</div>)}</details>}
    <HarnessView value={topology(card.document)} />
    <EtkView key={card.id} card={card} canEdit={canEdit} onSaved={(updated) => {
      onChange(updated);
      setNotice(`Операция сохранена${updated.status === "Выдана" ? ` · ревизия ${updated.revision}` : ""}`);
    }} />
    {!list.length && <div className="etk-empty">ЭТК ещё не собрана — открой «Генератор» и нажми «Создать».</div>}
    {revisions.length > 0 && <details className="etk-revs"><summary>Журнал ревизий · {revisions.length}</summary>
      {revisions.map((revision) => <div key={revision.id}><span>Ревизия {revision.revision} · {revision.reason}</span><span className="muted">{revision.actor_name} · {stamp(revision.occurred_at)}</span></div>)}
    </details>}
  </div>;
}

/** «Генератор» сборки: форма и ОДНА кнопка. Нет ТК — «Создать» (сборка ЭТК и выдача сразу, как файл техкарты в
    SUPPLYMAP: отдельного шага утверждения там нет). ТК уже выдана — «Пересобрать» с подтверждением, новой ревизией.
    Служебное — свод материалов, удаление черновика, JSON-конфигурация — свёрнуто в «Ещё». */
export function GeneratorPane({ card, principal, onChange, onDone }: {
  card: ProjectTechcard; principal: Principal; onChange: (card: ProjectTechcard) => void; onDone?: (card: ProjectTechcard) => void;
}) {
  const [configText, setConfigText] = useState("{}");
  const generatedConfig = useRef<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [kit, setKit] = useState<{ data: KitsetBom | null; error: string }>({ data: null, error: "" });
  const canIssue = principal.permissions.techcards === "edit";
  useEffect(() => { setConfigText(JSON.stringify(defaultConfig(card), null, 2)); }, [card.id, card.version]);
  useEffect(() => {
    // SUPPLYMAP portalTcGenData → tcLoadBomFromKit: состав из набора проекта + строки листа КБn расчёта;
    // набора или сборки нет — генератор показывает ошибку.
    setKit({ data: null, error: "" });
    let live = true;
    void kitsetsApi.bom(card.project, card.item_index)
      .then((result) => { if (live) setKit({ data: result, error: "" }); })
      .catch((reason) => { if (live) setKit({ data: null, error: failureText(reason, "Состав сборки не прочитан") }); });
    return () => { live = false; };
  }, [card.project, card.item_index]);

  async function build(config: Record<string, unknown>) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      let updated = await techcardsApi.autoBuild(card.id, card.version, config);
      const rebuilt = updated.status === "Выдана";
      if (!rebuilt && canIssue) {
        try { updated = await techcardsApi.issue(updated.id, updated.version); }
        catch (reason) {
          onChange(updated);
          setError(`Техкарта собрана черновиком, но не выдана: ${reason instanceof Error ? reason.message : "ошибка"}`);
          return;
        }
      }
      onChange(updated);
      const warningCount = warnings(updated.document).length;
      setNotice(`${rebuilt ? `Техкарта пересобрана · ревизия ${updated.revision}` : updated.status === "Выдана" ? "Техкарта создана" : "Черновик ЭТК собран — выдаёт технолог"}${warningCount ? ` · предупреждений: ${warningCount}` : ""}`);
      onDone?.(updated);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "ЭТК не собрана");
    } finally { setBusy(false); }
  }

  async function fromJson() {
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

  return <div className="gen-pane">
    <TcGenerator card={card} kit={kit.data} kitError={kit.error} busy={busy} onBuild={build} onDiscard={discardDraft}
      onConfig={(config) => { generatedConfig.current = config; }}
      /* JSON-конфигурация — служебная, только админу (владелец 29.09: «что это такое и зачем»). */
      more={!principal.roles.includes("admin") ? undefined : <div className="build-config">
        <label>JSON-конфигурация генератора (для эксперта)<textarea aria-label="Конфигурация автоматической сборки" rows={12} value={configText} onChange={(event) => setConfigText(event.target.value)} /></label>
        <div className="form-actions">
          <button className="secondary small" onClick={() => { if (generatedConfig.current) setConfigText(JSON.stringify(generatedConfig.current, null, 2)); }}>Взять из формы</button>
          <button className="secondary small" disabled={busy} onClick={() => void fromJson()}>Собрать по JSON</button>
        </div>
      </div>} />
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
  </div>;
}

/** Старая ссылка `?section=techcards&card=`: та же ТК и её генератор одной страницей. */
export function ProjectEtk({ card, principal, onChange }: { card: ProjectTechcard; principal: Principal; onChange: (card: ProjectTechcard) => void }) {
  return <article className="panel project-etk">
    <EtkSheet card={card} principal={principal} onChange={onChange} />
    {principal.permissions.tcgen && <GeneratorPane card={card} principal={principal} onChange={onChange} />}
  </article>;
}

/** Карточка ТК по id — из общего списка (отдельной ручки на одну карту нет). */
export function useTechcard(cardId: string) {
  const [card, setCard] = useState<ProjectTechcard | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setCard(null); setError("");
    if (!cardId) return;
    void techcardsApi.get(cardId)
      .then((found) => { if (live) setCard(found); })
      .catch((reason) => { if (live) setError(failureText(reason, "Техкарта не открылась")); });
    return () => { live = false; };
  }, [cardId]);
  return { card, setCard, error };
}
