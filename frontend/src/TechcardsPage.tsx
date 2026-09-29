import { useEffect, useMemo, useState } from "react";
import {
  techcardsApi,
  type OperationCardDetail,
  type OperationCardSummary,
  type GrapherImportResult,
  type Principal,
  type ProjectTechcard,
  type TechcardRevision,
} from "./api";

type EtkRow = { name?: string; art?: string; norm?: string; unit?: string };
type EtkStep = { number?: string; kind?: string; text?: string; tolerance?: string };
type Housing = {
  title: string; art: string; rows: number; cols: number; n: number; color: string;
  conf: string; note?: string; grid: number[][]; round?: Array<{ pin: number; x: number; y: number }> | null;
};
type EtkWireField = { name?: string; art?: string; pins?: number[] };
type EtkOperation = {
  position?: number;
  type?: string;
  code?: string;
  title?: string;
  connector?: string;
  card?: null | { name?: string; steps?: EtkStep[] };
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

function TemplateDetail({ template }: { template: OperationCardDetail }) {
  return <article className="panel template-detail">
    <div className="panel-heading"><strong>{template.title}</strong><span>{template.category}</span></div>
    {template.document.safety && <p className="warning-inline">{template.document.safety}</p>}
    <ol className="etk-steps">{template.document.steps.map((step, index) =>
      <li key={`${step.number}:${index}`}><span>{step.kind}</span>{step.text}{step.tolerance && <small>Допуск: {step.tolerance}</small>}</li>
    )}</ol>
    {template.document.defects && <p><strong>Критерии брака</strong><br />{template.document.defects}</p>}
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
  const [selectedId, setSelectedId] = useState("");
  // ?project= — opened as «Карточка проекта» from the production plan: only that project's cards.
  const [projectFilter, setProjectFilter] = useState(() => new URLSearchParams(window.location.search).get("project") ?? "");
  const [revisions, setRevisions] = useState<TechcardRevision[]>([]);
  const [form, setForm] = useState({ project: "", item_index: "", product_name: "", source_calculation: "", deadline: "" });
  const [configText, setConfigText] = useState("{}");
  const [grapher, setGrapher] = useState<GrapherImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const canEdit = principal.permissions.techcards === "edit" && principal.roles.some((role) => role === "engineer" || role === "admin");
  const selected = useMemo(() => cards.find((card) => card.id === selectedId) ?? null, [cards, selectedId]);
  const shownCards = useMemo(() => {
    const project = projectFilter.trim().toLocaleUpperCase("ru-RU");
    return project ? cards.filter((card) => card.project.toLocaleUpperCase("ru-RU") === project) : cards;
  }, [cards, projectFilter]);
  const filteredTemplates = useMemo(() => {
    const query = templateQuery.trim().toLocaleLowerCase("ru-RU");
    return query ? templates.filter((item) => `${item.code} ${item.name} ${item.category}`.toLocaleLowerCase("ru-RU").includes(query)) : templates;
  }, [templates, templateQuery]);

  async function load() {
    try {
      const [projectResult, libraryResult] = await Promise.all([
        showProjects ? techcardsApi.list() : Promise.resolve({ items: [] as ProjectTechcard[] }),
        showLibrary ? techcardsApi.templates() : Promise.resolve({ items: [] as OperationCardSummary[], metadata: null }),
      ]);
      setCards((current) => current.length ? current : projectResult.items);
      const wanted = projectFilter.trim().toLocaleUpperCase("ru-RU");
      const first = projectResult.items.find((card) => !wanted || card.project.toLocaleUpperCase("ru-RU") === wanted) ?? projectResult.items[0];
      setSelectedId((current) => current || first?.id || "");
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
    if (!selectedTemplateId) { setTemplateDetail(null); return; }
    void techcardsApi.template(selectedTemplateId).then(setTemplateDetail).catch(() => setTemplateDetail(null));
  }, [selectedTemplateId]);

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
    {projectFilter && <div className="plan-acts"><span>Проект {projectFilter}</span><button className="secondary small" onClick={() => setProjectFilter("")}>Все проекты</button></div>}
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
      {shownCards.map((card) => <tr key={card.id} className={selectedId === card.id ? "selected" : ""} onClick={() => setSelectedId(card.id)}><td><strong>{card.project}</strong></td><td>{card.item_index} · {card.product_name}</td><td>{card.status}</td><td>{card.revision || "—"}</td><td>{card.deadline || "—"}</td></tr>)}
      {!shownCards.length && <tr><td colSpan={5} className="empty">{projectFilter ? `Карточек проекта ${projectFilter} нет.` : "Карточек проектов пока нет."}</td></tr>}
    </tbody></table></div>
    {selected && <article className="panel project-etk">
      <div className="panel-heading"><strong>{selected.project} · {selected.item_index}</strong><span>{selected.status} · ревизия {selected.revision || "—"}</span></div>
      {warnings(selected.document).map((warning) => <div className="warning-inline" key={warning}>{warning}</div>)}
      <div className="etk-route">{operations(selected.document).map((operation, index) => <EtkOperationCard operation={operation} key={`${operation.code}:${operation.connector}:${index}`} />)}</div>
      <TopologyView value={topology(selected.document)} />
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
    </>}
    {showLibrary && <>
    <header className="page-head library-head"><h2>Библиотека операций</h2><span className="page-count">{templates.length}</span></header>
    <p className="page-sub">Снимок SUPPLYMAP {libraryCommit ? libraryCommit.slice(0, 8) : "не загружен"}; карточка сохраняется внутри каждой собранной ЭТК.</p>
    <div className="toolbar"><input aria-label="Поиск шаблона" placeholder="Код, название или категория" value={templateQuery} onChange={(event) => setTemplateQuery(event.target.value)} /></div>
    <div className="table-wrap template-table"><table><thead><tr><th>Код</th><th>Операция</th><th>Категория</th><th>Шаги</th><th>Оснастка</th></tr></thead><tbody>
      {filteredTemplates.map((template) => <tr key={template.id} className={selectedTemplateId === template.id ? "selected" : ""} onClick={() => setSelectedTemplateId(template.id)}><td><strong>{template.code}</strong>{template.side && <small>сторона {template.side}</small>}</td><td>{template.name}</td><td>{template.category}</td><td>{template.steps}</td><td>{template.tooling}</td></tr>)}
      {!filteredTemplates.length && <tr><td colSpan={5} className="empty">Шаблоны не найдены.</td></tr>}
    </tbody></table></div>
    {templateDetail && <TemplateDetail template={templateDetail} />}
    </>}
  </section>;
}
