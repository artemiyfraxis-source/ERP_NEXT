import { useEffect, useMemo, useState } from "react";
import {
  techcardsApi,
  type OperationCardDetail,
  type OperationCardSummary,
  type OperationRecordEdit,
  type GrapherImportResult,
  type Principal,
  type ProjectTechcard,
  type TechcardRevision,
} from "./api";
import { etkPrintPages } from "./etkPrint";
import { openPrintDocument } from "./print";
import { SAFETY_HEAD, TcCardEditForm } from "./TcCardEditForm";

type EtkRow = { name?: string; art?: string; norm?: string; unit?: string };
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

function EtkOperationCard({ operation }: { operation: EtkOperation }) {
  return <article className="etk-operation">
    <header><span className="operation-number">{operation.position || "—"}</span><div><strong>{operation.code || "Без кода"}</strong><small>{operation.card?.name || operation.title || operation.connector || ""}</small></div></header>
    <div className="etk-flow">
      <Rows title="Комплектующие" rows={operation.components} />
      <Rows title="Вход" rows={operation.semis} />
      <Rows title="Результат" rows={operation.result} />
      <Rows title="Время, мин" rows={operation.time} />
    </div>
    {operation.card?.steps?.length ? <ol className="etk-steps">{operation.card.steps.map((step, index) =>
      <li key={`${step.number}:${index}`}><span>{step.kind || "Операция"}</span>{step.text}{step.tolerance && <small>Допуск: {step.tolerance}</small>}</li>
    )}</ol> : <div className="warning-inline">Карточка операции отсутствует в библиотеке.</div>}
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
          hint={`Правка ТК проекта ${card.project} ${card.item_index}: ${card.status === "Выдана" ? `сохранение поднимет ревизию до ${card.revision + 1} и запишется в журнал` : "ЭТК ещё не выдана — ревизия не меняется"}. Карточка операции в библиотеке не меняется; при пересборке эта правка сохранится. Комплектующие, нормы и время считает генератор — их меняют через конфигурацию сборки.`}
          onCancel={() => setEditing(false)}
          onSave={save}
        /> : <>
          <div className="etk-kicker">Операция {operation.position || index + 1} из {list.length} · <strong>{operation.code}</strong>
            {operation.edited && <b className="tr-edited"> · правлена {operation.edited.at}{operation.edited.by ? ` · ${operation.edited.by}` : ""}</b>}
            {canEdit && operation.card && <button className="secondary small" onClick={() => setEditing(true)}>Править</button>}
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

/** Просмотр: карточка в форме листа техкарты (SUPPLYMAP openTcRecords → renderCard). */
function TemplateDetail({ template, canEdit, onBack, onEdit }: {
  template: OperationCardDetail; canEdit: boolean; onBack: () => void; onEdit: () => void;
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
    <div className="tr-foot">{template.changed_at ? `Изменено ${new Date(template.changed_at).toLocaleString("ru-RU")}${template.changed_by ? ` · ${template.changed_by}` : ""}` : "Перенесено из шаблона БД.ТК, в ERP не правилась"}</div>
  </article>;
}

/** NAV-1: «Техкарты проектов» and «Шаблоны техкарт» are separate sections, as in SUPPLYMAP; without `view` both show. */
export function TechcardsPage({ principal, view }: { principal: Principal; view?: "projects" | "templates" }) {
  const showProjects = view !== "templates";
  const showLibrary = view !== "projects";
  const [cards, setCards] = useState<ProjectTechcard[]>([]);
  const [templates, setTemplates] = useState<OperationCardSummary[]>([]);
  const [libraryCommit, setLibraryCommit] = useState("");
  const [templateQuery, setTemplateQuery] = useState("");
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [templateDetail, setTemplateDetail] = useState<OperationCardDetail | null>(null);
  const [templateCategory, setTemplateCategory] = useState("");
  const [editingTemplate, setEditingTemplate] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [revisions, setRevisions] = useState<TechcardRevision[]>([]);
  const [form, setForm] = useState({ project: "", item_index: "", product_name: "", source_calculation: "", deadline: "" });
  const [configText, setConfigText] = useState("{}");
  const [grapher, setGrapher] = useState<GrapherImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const canEdit = principal.permissions.techcards === "edit" && principal.roles.some((role) => role === "engineer" || role === "admin");
  const selected = useMemo(() => cards.find((card) => card.id === selectedId) ?? null, [cards, selectedId]);
  // Одна запись на код операции: пара (A)/(B) — одна карточка (SUPPLYMAP tcRecordsByCode_).
  const records = useMemo(() => {
    const byCode = new Map<string, OperationCardSummary>();
    templates.forEach((item) => {
      if (!byCode.has(item.code) || RECORD_SIDE_PICK[item.code] === item.side) byCode.set(item.code, item);
    });
    return [...byCode.values()];
  }, [templates]);
  const categories = useMemo(() => CATEGORY_ORDER.filter((name) => records.some((item) => recordCategory(item.code) === name)), [records]);
  const filteredTemplates = useMemo(() => {
    const query = templateQuery.trim().toLocaleLowerCase("ru-RU");
    return records.filter((item) => (!templateCategory || recordCategory(item.code) === templateCategory) &&
      (!query || `${item.code} ${item.name} ${item.step_text ?? ""}`.toLocaleLowerCase("ru-RU").includes(query)));
  }, [records, templateQuery, templateCategory]);

  async function load() {
    try {
      const [projectResult, libraryResult] = await Promise.all([
        showProjects ? techcardsApi.list() : Promise.resolve({ items: [] as ProjectTechcard[] }),
        showLibrary ? techcardsApi.templates() : Promise.resolve({ items: [] as OperationCardSummary[], metadata: null }),
      ]);
      setCards((current) => current.length ? current : projectResult.items);
      // «Проекты» → «Генератор»/«ТК КБn» открывают здесь карточку изделия: ?card=<id>.
      const linked = new URLSearchParams(window.location.search).get("card") || "";
      setSelectedId((current) => current || (projectResult.items.some((card) => card.id === linked) ? linked : "") || projectResult.items[0]?.id || "");
      setTemplates(libraryResult.items);
      setLibraryCommit(libraryResult.metadata?.source_commit || "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить техкарты");
    }
  }

  useEffect(() => { void load(); }, []);
  useEffect(() => {
    if (!selected) { setRevisions([]); return; }
    setConfigText(JSON.stringify(defaultConfig(selected), null, 2));
    void techcardsApi.revisions(selected.id).then((result) => setRevisions(result.items)).catch(() => setRevisions([]));
  }, [selectedId, selected?.version]);
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

  async function createCard() {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const created = await techcardsApi.create({ ...form, deadline: form.deadline || null });
      setCards((current) => [...current, created]);
      setSelectedId(created.id);
      setForm({ project: "", item_index: "", product_name: "", source_calculation: "", deadline: "" });
      setNotice("Карточка проекта создана");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Карточка не создана");
    } finally { setBusy(false); }
  }

  async function autoBuild() {
    if (!selected || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const config = JSON.parse(configText) as Record<string, unknown>;
      const updated = await techcardsApi.autoBuild(selected.id, selected.version, config);
      replaceCard(updated);
      const warningCount = warnings(updated.document).length;
      setNotice(`${selected.status === "Выдана" ? "ЭТК пересобрана новой ревизией" : "Черновик ЭТК собран"}${warningCount ? ` · предупреждений: ${warningCount}` : ""}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "ЭТК не собрана");
    } finally { setBusy(false); }
  }

  async function importGrapher(file: File) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      });
      const result = await techcardsApi.importGrapher(file.name, dataUrl.split(",", 2)[1] || "");
      setGrapher(result);
      setNotice(`Чертёж прочитан · жгутов: ${result.harnesses.length}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Экспорт TECHMAP-GRAPHER не разобран");
    } finally { setBusy(false); }
  }

  function applyHarness(harness: GrapherImportResult["harnesses"][number]) {
    try {
      const current = JSON.parse(configText) as Record<string, unknown>;
      const wires = harness.groups.map((group) => ({
        name: group.material, art: group.material, qty: group.quantity,
        length: group.length_mm ?? 0, cores: group.cores,
        jacketStrip: group.jacket_mm,
        grapher: {
          source_ids: group.source_ids, ends: group.ends, cut_mm: group.cut_mm,
          correction_from_mm: group.correction_from_mm,
          correction_to_mm: group.correction_to_mm,
        },
      }));
      setConfigText(JSON.stringify({
        ...current,
        assemblyIndex: harness.designation || current.assemblyIndex,
        partQty: harness.quantity || current.partQty,
        wires,
        grapherImport: {
          project: grapher?.project.designation || "", pairs: harness.pairs,
          connectors: harness.connectors, warnings: harness.warnings,
        },
      }, null, 2));
      setNotice(`Кабели из ${harness.designation || "чертежа"} подставлены в конфигурацию`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Текущая конфигурация содержит неверный JSON");
    }
  }

  async function issue() {
    if (!selected || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      replaceCard(await techcardsApi.issue(selected.id, selected.version));
      setNotice("ЭТК выдана, ревизия 1 зафиксирована");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "ЭТК не выдана");
    } finally { setBusy(false); }
  }

  return <section className="page techcards-page">
    {showProjects ? <>
    <header className="page-head"><h1>Техкарты проектов</h1><span className="page-count">{cards.length}</span></header>
    <p className="page-sub">ЭТК собирается сервером из конфигурации изделия и снимка библиотеки операций; выданные версии остаются неизменяемыми.</p>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {canEdit && <article className="panel">
      <div className="panel-heading"><strong>Новая карточка изделия</strong></div>
      <div className="form-grid">
        <label>Проект<input aria-label="Проект" value={form.project} onChange={(event) => setForm({ ...form, project: event.target.value })} /></label>
        <label>Индекс изделия<input aria-label="Индекс изделия" value={form.item_index} onChange={(event) => setForm({ ...form, item_index: event.target.value })} /></label>
        <label>Наименование<input aria-label="Наименование" value={form.product_name} onChange={(event) => setForm({ ...form, product_name: event.target.value })} /></label>
        <label>Исходный расчёт<input aria-label="Исходный расчёт" value={form.source_calculation} onChange={(event) => setForm({ ...form, source_calculation: event.target.value })} /></label>
        <label>Срок<input aria-label="Срок" type="date" value={form.deadline} onChange={(event) => setForm({ ...form, deadline: event.target.value })} /></label>
      </div>
      <div className="form-actions"><button className="primary" disabled={busy || !form.project || !form.item_index || !form.product_name} onClick={() => void createCard()}>Создать карточку</button></div>
    </article>}
    <div className="table-wrap"><table><thead><tr><th>Проект</th><th>Изделие</th><th>Статус</th><th>Ревизия</th><th>Срок</th></tr></thead><tbody>
      {cards.map((card) => <tr key={card.id} className={selectedId === card.id ? "selected" : ""} onClick={() => setSelectedId(card.id)}><td><strong>{card.project}</strong></td><td>{card.item_index} · {card.product_name}</td><td>{card.status}</td><td>{card.revision || "—"}</td><td>{card.deadline || "—"}</td></tr>)}
      {!cards.length && <tr><td colSpan={5} className="empty">Карточек проектов пока нет.</td></tr>}
    </tbody></table></div>
    {selected && <article className="panel project-etk">
      <div className="panel-heading"><strong>{selected.project} · {selected.item_index}</strong><span>{selected.status} · ревизия {selected.revision || "—"}</span></div>
      {warnings(selected.document).map((warning) => <div className="warning-inline" key={warning}>{warning}</div>)}
      <TopologyView value={topology(selected.document)} />
      <EtkView key={selected.id} card={selected} canEdit={canEdit} revisions={revisions} onSaved={(updated) => {
        replaceCard(updated);
        setNotice(`Операция сохранена${updated.status === "Выдана" ? ` · ревизия ${updated.revision}` : ""}`);
      }} />
      {!operations(selected.document).length && <div className="empty">ЭТК ещё не собрана.</div>}
      {canEdit && <details className="build-config"><summary>Конфигурация автоматической сборки</summary>
        <div className="grapher-import">
          <label>Экспорт TECHMAP-GRAPHER<input aria-label="Экспорт TECHMAP-GRAPHER" type="file" accept=".json,.zip,.techmap-project.zip" disabled={busy} onChange={(event) => { const file = event.target.files?.[0]; if (file) void importGrapher(file); }} /></label>
          {grapher && <div className="grapher-harnesses">{grapher.harnesses.map((harness) => <div key={harness.designation}>
            <span><strong>{harness.designation || "Без индекса"}</strong> · кабелей/проводов {harness.groups.length} · пар {harness.pairs}</span>
            <button className="secondary small" onClick={() => applyHarness(harness)}>Подставить в «Кабели»</button>
            {harness.warnings.map((warning) => <small className="danger-text" key={warning}>{warning}</small>)}
          </div>)}</div>}
        </div>
        <label>Config генератора SUPPLYMAP<textarea aria-label="Конфигурация автоматической сборки" rows={14} value={configText} onChange={(event) => setConfigText(event.target.value)} /></label>
      </details>}
      {canEdit && <div className="form-actions"><button className="secondary" disabled={busy} onClick={() => void autoBuild()}>Собрать ЭТК автоматически</button>{selected.status === "Черновик" && selected.document && <button className="primary" disabled={busy} onClick={() => void issue()}>Выдать ЭТК</button>}</div>}
      <div className="panel-heading"><strong>Журнал ревизий</strong><span>{revisions.length}</span></div>
      {revisions.map((revision) => <div className="panel-heading" key={revision.id}><span>Ревизия {revision.revision} · {revision.reason}</span><span>{revision.actor_name} · {new Date(revision.occurred_at).toLocaleString("ru-RU")}</span></div>)}
      {!revisions.length && <div className="empty">Выданных ревизий пока нет.</div>}
    </article>}
    </> : <>
    <header className="page-head"><h1>Шаблоны техкарт</h1></header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    </>}
    {showLibrary && <>
    <header className="page-head library-head"><h2>Библиотека операций</h2><span className="page-count">{records.length}</span></header>
    <p className="page-sub">Снимок SUPPLYMAP {libraryCommit ? libraryCommit.slice(0, 8) : "не загружен"}; карточка сохраняется внутри каждой собранной ЭТК. Карточка операции — то, из чего собирается лист техкарты: шаги, контроль с допусками, оснастка, безопасность, дефекты. Открой карточку, чтобы посмотреть{canEdit ? " или поправить" : ""}.</p>
    {templateDetail && selectedTemplateId ? (editingTemplate ? <TcCardEditForm
      code={templateDetail.code}
      initial={editDraft(templateDetail.name, templateDetail.document)}
      hint="Правки пишутся в библиотеку ERP и попадают в ТК проектов при следующей сборке; при обновлении снимка SUPPLYMAP правленая карточка сохраняется. Пустые шаги и строки оснастки при сохранении отбрасываются, номера проставятся сами."
      onCancel={() => setEditingTemplate(false)}
      onSave={saveTemplate}
    /> : <TemplateDetail template={templateDetail} canEdit={canEdit} onBack={() => setSelectedTemplateId("")} onEdit={() => setEditingTemplate(true)} />) : <>
    <div className="toolbar"><input aria-label="Поиск шаблона" placeholder="Поиск по коду, названию, тексту шага…" value={templateQuery} onChange={(event) => setTemplateQuery(event.target.value)} /></div>
    <div className="tr-chips">
      <button className={templateCategory ? "" : "on"} onClick={() => setTemplateCategory("")}>Все · {records.length}</button>
      {categories.map((name) => <button key={name} className={templateCategory === name ? "on" : ""} onClick={() => setTemplateCategory(name)}>{name} · {records.filter((item) => recordCategory(item.code) === name).length}</button>)}
    </div>
    {categories.map((name) => {
      const items = filteredTemplates.filter((item) => recordCategory(item.code) === name);
      if (!items.length) return null;
      return <div className="tr-group" key={name}><h3>{name} <span>{items.length}</span></h3><div className="tr-grid">
        {items.map((item) => <button className="tr-card" key={item.id} onClick={() => setSelectedTemplateId(item.id)}>
          <strong className="tr-code">{item.code}</strong>
          <span className="tr-name">{item.name}</span>
          {item.first_step && <span className="tr-first">{item.first_step}</span>}
          <small className="tr-meta">{recordMeta(item)}</small>
        </button>)}
      </div></div>;
    })}
    {!filteredTemplates.length && <div className="empty">Ничего не найдено.</div>}
    </>}
    </>}
  </section>;
}
