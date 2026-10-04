import { useEffect, useMemo, useState, type ReactNode } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { ProjectEtk, ToolingBySection, editDraft } from "./ProjectEtk";
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
import { SAFETY_HEAD, TcCardEditForm } from "./TcCardEditForm";
import { TcTemplateSheets } from "./TcTemplateSheets";

const CATEGORY_ORDER = [...new Set([...RECORD_CATEGORIES.map(([, name]) => name), "Прочее"])];
// Где стороны (A)/(B) расходятся, чья сторона — запись (backend operation_cards.RECORD_SIDE_PICK).
const RECORD_SIDE_PICK: Record<string, string> = { SDR_COAX_housing: "B", SDR_COAX_pin: "A" };


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

/** Cards of «Операции»: one per operation code — the templates' codes plus БД.ОП codes without a template (as
    `operationEntries` builds them), for the tab's count in «База данных». */
export function operationCardCount(templates: Array<{ code: string }>, opRows: Array<{ fields: Record<string, string> }>): number {
  const codes = new Set(templates.map((item) => item.code));
  opRows.forEach((row) => { const code = opCode(row.fields); if (code) codes.add(code); });
  return codes.size;
}

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
            <strong className="tr-name">{entry.name || recordCategory(entry.code)}</strong>
            <span className="tr-code">{entry.code}</span>
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
    <p className="page-sub">Снимок SUPPLYMAP {libraryCommit ? libraryCommit.slice(0, 8) : "не загружен"} · пунктиром — операции БД.ОП без шаблона листа.</p>
    {sheetsOpen ? <TcTemplateSheets canEdit={canEditTemplates} onBack={() => setSheetsOpen(false)} onChanged={() => {
      void techcardsApi.templates().then((library) => setTemplates(library.items)).catch(() => undefined);
    }} /> : selectedOp ? <OpOnlyDetail record={selectedOp} canEdit={canEditOps} onSaved={replaceOp} onBack={() => setSelectedOpCode("")} /> : templateDetail && selectedTemplateId ? (editingTemplate ? <TcCardEditForm
      code={templateDetail.code}
      initial={editDraft(templateDetail.name, templateDetail.document)}
      saveNote="Сохранение — в библиотеку, в ТК проектов при следующей сборке"
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
