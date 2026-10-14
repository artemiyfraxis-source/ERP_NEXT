import { useEffect, useMemo, useState, type ReactNode } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDayMonth } from "./format";
import { EtkSheet, GeneratorPane } from "./ProjectEtk";
import { OperationCard } from "./OperationCard";
import { NewOperationDialog, type NewOperationResult } from "./NewOperationDialog";
import { StatusChip } from "./StatusChip";
import { pfPending } from "./pfPropose";
import { RECORD_CATEGORIES, recordCategory } from "./etkOperation";
import {
  referencesApi,
  techcardsApi,
  type OperationCardDetail,
  type OperationCardSummary,
  type OperationRecordEdit,
  type Principal,
  type ProjectTechcard,
  type TechOperationRecord,
} from "./api";
import { TcTemplateSheets } from "./TcTemplateSheets";
import { AddColumnButton, filledIn, ownCellEdit, useColumnRemoval } from "./CustomColumn";

const CATEGORY_ORDER = [...new Set([...RECORD_CATEGORIES.map(([, name]) => name), "Прочее"])];
// Где стороны (A)/(B) расходятся, чья сторона — запись (backend operation_cards.RECORD_SIDE_PICK).
const RECORD_SIDE_PICK: Record<string, string> = { SDR_COAX_housing: "B", SDR_COAX_pin: "A" };



function opCode(fields: Record<string, string>) { return (fields.opNumber || fields["Номер"] || "").trim(); }

/** Cards of «Операции»: one per operation code — the templates' codes plus БД.ОП codes without a template (as
    `operationEntries` builds them), for the tab's count in «База данных». */
export function operationCardCount(templates: Array<{ code: string }>, opRows: Array<{ fields: Record<string, string> }>): number {
  const codes = new Set(templates.map((item) => item.code));
  opRows.forEach((row) => { const code = opCode(row.fields); if (code) codes.add(code); });
  return codes.size;
}

type OperationEntry = { key: string; code: string; name: string; template?: OperationCardSummary; op?: Record<string, string>; record?: TechOperationRecord };

/** Свои столбцы БД.ОП («+ Столбец», владелец 14.10): видны в таблице операций сразу за кодом и правятся в ячейках у строк
    с записью БД.ОП (у шаблона без строки БД.ОП ячейка не правится). */
type OwnColumns = { names: string[]; canEdit: boolean; onEdited: () => Promise<unknown> | void; removeOf: (name: string) => (() => void) | undefined };

/** Операции таблицей (аудит БД-2: 139 карточек — это 6 500 px прокрутки, справочник читается строками): шаблоны ТК +
    операции БД.ОП без шаблона; категории — чипы с числом, строка целиком открывает карточку операции. */
function OperationsTable({ entries, onOpen, toolbar, own }: { entries: OperationEntry[]; onOpen: (entry: OperationEntry) => void; toolbar?: ReactNode; own?: OwnColumns }) {
  const seconds = (entry: OperationEntry) => entry.template?.norms?.operation_seconds || entry.op?.tOp || "";
  const columns: DataColumn<OperationEntry>[] = [
    { key: "name", title: "Операция", value: (entry) => entry.name || recordCategory(entry.code) },
    { key: "code", title: "Код", value: (entry) => entry.code, render: (entry) => <span className="tr-code">{entry.code}</span> },
    { key: "category", title: "Категория", value: (entry) => recordCategory(entry.code), filter: true },
    ...(own?.names ?? []).map((name): DataColumn<OperationEntry> => ({
      key: `own:${name}`, title: name, value: (entry) => entry.op?.[name] ?? "", wrap: true, maxWidth: 160,
      edit: own?.canEdit ? ownCellEdit<OperationEntry>(name, (entry) => entry.record) : undefined,
      remove: own?.removeOf(name),
    })),
    { key: "steps", title: "Шагов", numeric: true, value: (entry) => entry.template?.steps ?? "" },
    { key: "controls", title: "Контролей", numeric: true, value: (entry) => entry.template ? entry.template.controls ?? 0 : "" },
    { key: "tooling", title: "Оснастки", numeric: true, value: (entry) => entry.template?.tooling ?? "" },
    { key: "seconds", title: "Время, с", numeric: true, value: (entry) => seconds(entry),
      render: (entry) => seconds(entry) ? seconds(entry)
        : entry.template && entry.template.norms_known && !entry.template.norms ? <b className="danger-text">нет норм</b> : <span className="cell-empty">—</span> },
    { key: "machine", title: "Машина", value: (entry) => entry.op?.["Машина"]?.trim() ?? "",
      render: (entry) => entry.op?.["Машина"]?.trim() ? <span className="db-clip" title={entry.op["Машина"]}>{entry.op["Машина"].trim()}</span> : <span className="cell-empty">—</span> },
    { key: "type", title: "Тип операции", value: (entry) => entry.op?.["Тип операции"]?.trim() ?? "", hidden: true },
    { key: "template", title: "Шаблон ТК", filter: true, value: (entry) => entry.template ? "есть" : "нет шаблона",
      render: (entry) => entry.template ? <span className="muted">есть</span> : <StatusChip status="нет шаблона" tone="warning" /> },
    { key: "changed", title: "Правка", value: (entry) => entry.template?.changed_at ?? "",
      render: (entry) => entry.template?.changed_at ? <span className="tr-edited">{formatDayMonth(entry.template.changed_at)}</span> : <span className="cell-empty">—</span> },
    // Текст шагов — только для поиска (флюс, жало, машина…), в таблице скрыт.
    { key: "text", title: "Текст шагов", hidden: true, value: (entry) => [entry.template?.first_step, entry.template?.step_text, ...Object.values(entry.op ?? {})].filter(Boolean).join(" ") },
  ];
  const presets = useMemo(() => {
    const names = new Set(entries.map((entry) => recordCategory(entry.code)));
    return CATEGORY_ORDER.filter((name) => names.has(name)).map((name) => ({ label: name, test: (entry: OperationEntry) => recordCategory(entry.code) === name }));
  }, [entries]);
  return <DataTable
    rows={entries}
    columns={columns}
    rowKey={(entry) => entry.key}
    storageKey="db:operations"
    label="Операции"
    exportName="Операции"
    presets={presets}
    presetCountsFollowSearch
    onRowClick={onOpen}
    rowTitle={(entry) => `Открыть: ${entry.name || entry.code}`}
    rowLabel={(entry) => entry.code}
    onEdited={own?.onEdited}
    toolbar={toolbar}
    emptyText="Ничего не найдено."
  />;
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
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  // БД.ОП rows (only with the reference-book grant): op-only operations become cards too, and every card shows its row.
  const [opRows, setOpRows] = useState<TechOperationRecord[]>([]);
  const [selectedOpCode, setSelectedOpCode] = useState("");
  const [templateDetail, setTemplateDetail] = useState<OperationCardDetail | null>(null);
  const [sheetsOpen, setSheetsOpen] = useState(false);
  const [newOpOpen, setNewOpOpen] = useState(false);
  // Код только что созданной операции: её карточка открывается сразу в «Править».
  const [freshCode, setFreshCode] = useState("");
  // ЭТК-3: под выданной ЭТК генератор свёрнут, пока не нажали «Пересобрать».
  const [rebuilding, setRebuilding] = useState(false);
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
  const issued = selected?.status === "Выдана";
  useEffect(() => setRebuilding(false), [selectedId]);
  const shownCards = useMemo(() => {
    const project = projectFilter.trim().toLocaleUpperCase("ru-RU");
    return project ? cards.filter((card) => card.project.toLocaleUpperCase("ru-RU") === project) : cards;
  }, [cards, projectFilter]);
  const cardColumns: DataColumn<ProjectTechcard>[] = [
    { key: "project", title: "Проект", value: (card) => card.project },
    { key: "item", title: "Изделие", value: (card) => `${card.item_index} · ${card.product_name}`, grow: true },
    { key: "status", title: "Статус", value: (card) => card.status, render: (card) => <StatusChip status={card.status} /> },
    { key: "revision", title: "Ревизия", value: (card) => card.revision || "", numeric: true },
    // ЭТК со схемой «по ПФ на операцию» — технолог проходит их в «Править ПФ» (владелец 13.10).
    { key: "pf", title: "ПФ", value: (card) => (pfPending(card) ? "не настроены" : card.document ? "настроены" : ""),
      render: (card) => (pfPending(card) ? <StatusChip status="ПФ не настроены" tone="warning" /> : card.document ? <span className="muted">настроены</span> : "") },
    { key: "deadline", title: "Срок", value: (card) => card.deadline ?? "" },
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
    const entries: OperationEntry[] = records.map((item) => ({ key: item.id, code: item.code, name: item.name, template: item, op: opByCode.get(item.code)?.fields, record: opByCode.get(item.code) }));
    const known = new Set(records.map((item) => item.code));
    opByCode.forEach((record, code) => {
      if (!known.has(code)) entries.push({ key: `op:${code}`, code, name: record.fields.opName || record.fields["Название"] || "", op: record.fields, record });
    });
    return entries;
  }, [records, opByCode]);
  const selectedOp = selectedOpCode ? opByCode.get(selectedOpCode) : undefined;
  const canEditOps = principal.permissions.erpref === "edit";
  const canCreateOp = canEditTemplates && canEditOps;
  const [opOwn, setOpOwn] = useState<string[]>([]);
  async function loadOps() {
    const result = await referencesApi.techOperations("op", "", 5000);
    setOpRows(result.records);
    setOpOwn(result.custom_columns ?? []);
  }
  const opRemoval = useColumnRemoval("op", canEditOps, (name) => filledIn(opRows, name), async (name) => {
    await loadOps().catch(() => undefined);
    setError(""); setNotice(`Столбец «${name}» удалён`);
  });
  async function opCreated(result: NewOperationResult) {
    setNewOpOpen(false);
    setOpRows((rows) => [...rows.filter((row) => row.id !== result.record.id), result.record]);
    const library = await techcardsApi.templates().then((value) => value.items, () => templates);
    setTemplates(library);
    const made = result.templates[0];
    setFreshCode(result.code);
    setNotice(`Операция ${result.code} создана`);
    if (made) { setSelectedOpCode(""); setSelectedTemplateId(made.id); } else setSelectedOpCode(result.code);
  }
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
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить техкарты");
    }
  }

  useEffect(() => { void load(); }, []);
  useEffect(() => {
    if (!showLibrary || !principal.permissions.erpref) return;
    void loadOps().catch(() => undefined);
  }, [showLibrary, principal.permissions.erpref]);
  useEffect(() => {
    if (!selectedTemplateId) { setTemplateDetail(null); return; }
    void techcardsApi.template(selectedTemplateId).then(setTemplateDetail).catch(() => setTemplateDetail(null));
  }, [selectedTemplateId]);

  async function saveTemplate(draft: OperationRecordEdit) {
    if (!templateDetail) return;
    const saved = await techcardsApi.saveTemplate(templateDetail.id, draft);
    setTemplateDetail(saved);
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
        { label: "ПФ не настроены", test: (card) => card.status === "Выдана" && pfPending(card) },
      ]}
      rowClassName={(card) => (selectedId === card.id ? "selected" : "")}
      onRowClick={(card) => setSelectedId(card.id)}
      rowTitle={(card) => `Открыть ЭТК: ${card.project} · ${card.item_index}`}
      emptyText={projectFilter ? `Карточек проекта ${projectFilter} нет.` : cards.length ? "Ничего не найдено." : "Техкарт пока нет: их заводит «Генератор» в карточке проекта."}
    />}
    {selected && <article className="panel project-etk tc-etk">
      <EtkSheet card={selected} principal={principal} onChange={replaceCard} onRebuild={issued ? () => setRebuilding(true) : undefined} />
      {canGenerate && (!issued || rebuilding) && <>
        {issued && <div className="tc-gen-head"><strong>Пересборка ЭТК</strong><button className="secondary small" onClick={() => setRebuilding(false)}>Свернуть генератор</button></div>}
        <GeneratorPane key={selected.id} card={selected} principal={principal} onChange={replaceCard} onDone={(updated) => {
          // Выданная ЭТК снова сворачивает генератор; итог сборки остаётся строкой наверху.
          setRebuilding(false);
          setNotice(updated.status !== "Выдана" ? "Черновик ЭТК собран — выдаёт технолог" : updated.revision > 1 ? `Техкарта пересобрана · ревизия ${updated.revision}` : "Техкарта создана");
        }} />
      </>}
    </article>}
    </> : <>
    {!embedded && <header className="page-head"><h1>Шаблоны техкарт</h1></header>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    </>}
    {showLibrary && <>
    {!embedded && <header className="page-head library-head"><h2>Библиотека операций</h2><span className="page-count">{records.length}</span></header>}
    {sheetsOpen ? <TcTemplateSheets canEdit={canEditTemplates} onBack={() => setSheetsOpen(false)} onChanged={() => {
      void techcardsApi.templates().then((library) => setTemplates(library.items)).catch(() => undefined);
    }} /> : selectedOp ? <OperationCard code={opCode(selectedOp.fields)} name={selectedOp.fields.opName || selectedOp.fields["Название"] || ""} op={selectedOp}
      canEditTemplate={false} canEditOp={canEditOps} initialEdit={freshCode === selectedOpCode} onBack={() => { setSelectedOpCode(""); setFreshCode(""); }} onSaveTemplate={async () => undefined} onOpSaved={replaceOp}
    /> : templateDetail && selectedTemplateId ? <OperationCard code={templateDetail.code} name={templateDetail.name} template={templateDetail} op={opByCode.get(templateDetail.code)}
      canEditTemplate={canEditTemplates} canEditOp={canEditOps} initialEdit={freshCode === templateDetail.code} onBack={() => { setSelectedTemplateId(""); setFreshCode(""); }} onSaveTemplate={saveTemplate} onOpSaved={replaceOp}
    /> : <>
    <OperationsTable entries={operationEntries}
      own={{ names: opOwn, canEdit: canEditOps, onEdited: () => loadOps().catch(() => undefined), removeOf: opRemoval.removeOf }}
      toolbar={<>{canCreateOp && <button type="button" className="primary small" onClick={() => setNewOpOpen(true)}>Новая операция</button>}<button type="button" className="secondary small" title="Каталог листов БД.ТК, из которого собирает генератор" onClick={() => setSheetsOpen(true)}>Шаблоны-листы</button>
        {canEditOps && opRows.length > 0 && <AddColumnButton tab="op" onAdded={async (name) => { await loadOps().catch(() => undefined); setError(""); setNotice(`Столбец «${name}» добавлен`); }} />}</>} onOpen={(entry) => {
      if (entry.template) { setSelectedOpCode(""); setSelectedTemplateId(entry.template.id); } else setSelectedOpCode(entry.code);
    }} />
    </>}
    {opRemoval.dialog}
    {newOpOpen && <NewOperationDialog operations={operationEntries.map((entry) => ({ code: entry.code, name: entry.name }))} onClose={() => setNewOpOpen(false)} onCreated={(result) => void opCreated(result)} />}
    </>}
  </section>;
}
