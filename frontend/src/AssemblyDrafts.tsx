import { useEffect, useRef, useState } from "react";
import { assemblyApi, type AssemblyDraft } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { pluralBlocks } from "./etkRoute";
import { formatDate, formatDay } from "./format";

/* «Черновики с чертежей» (владелец 05.10): схему по чертежу позиции набрал Claude или агент на сервере; мастер открывает
   черновик в «Ручной сборке», правит и собирает — сборка завершает черновик, и пара «черновик → итог» с уроком
   («что поправили и почему») становится образцом корпуса. */

const SOURCES: Record<string, string> = { "claude-code": "Claude", "server-agent": "агент", manual: "вручную" };
const DRAWING_TYPES = /\.(pdf|jpe?g|png)$/i;
const PDF_TYPE = /\.pdf$/i;

/** «Чертёж (1)+2.pdf»: имя первого файла без расширения и сколько файлов к нему сшито. */
export const mergedDrawingName = (first: string, count: number) => `${first.replace(/\.[^.]+$/, "")}${count > 1 ? `+${count - 1}` : ""}.pdf`;
const fileSize = (bytes: number) => (bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1).replace(".", ",")} МБ` : `${Math.max(1, Math.round(bytes / 1024))} КБ`);

export const draftSource = (source: string) => SOURCES[source] || source;
export const draftStatus = (status: string) => (status === "final" ? "образец" : "черновик");

/** «12 оп. · 4 блока» по конфигу «Ручной сборки» (черновика или итога). */
export function configSummary(config: Record<string, unknown> | null | undefined): string {
  if (!config) return "";
  const ops = Array.isArray(config.ops) ? config.ops.length : 0;
  const route = config.route && typeof config.route === "object" ? config.route as { blocks?: unknown } : null;
  const blocks = Array.isArray(route?.blocks) ? route.blocks.length : 0;
  return `${ops} оп.${blocks ? ` · ${pluralBlocks(blocks)}` : ""}`;
}

/** «Что поправили и почему» — к паре черновик/итог; пустой урок тоже сохраняется. */
function LessonEditor({ draft, onSaved }: { draft: AssemblyDraft; onSaved: (draft: AssemblyDraft) => void }) {
  const [text, setText] = useState(draft.lesson);
  const [state, setState] = useState<{ text: string; error: boolean }>({ text: "", error: false });
  const [busy, setBusy] = useState(false);
  // Сброс поля при смене черновика — прямо в рендере: эффект при монтировании мог сработать после ввода и стереть его.
  const [shown, setShown] = useState({ id: draft.id, lesson: draft.lesson });
  if (shown.id !== draft.id || shown.lesson !== draft.lesson) {
    setShown({ id: draft.id, lesson: draft.lesson });
    setText(draft.lesson);
  }
  async function save() {
    setBusy(true);
    try {
      onSaved(await assemblyApi.setLesson(draft.id, text));
      setState({ text: "Урок сохранён", error: false });
    } catch (reason) {
      setState({ text: reason instanceof Error ? reason.message : "Урок не сохранён", error: true });
    } finally { setBusy(false); }
  }
  return <div className="draft-lesson">
    <label className="gfield"><span className="gfield-label">Что поправили и почему</span>
      <textarea aria-label="Что поправили и почему" rows={3} value={text} onChange={(event) => setText(event.target.value)} /></label>
    <div className="draft-lesson-foot">
      <button type="button" className="secondary small" disabled={busy} onClick={() => void save()}>Сохранить урок</button>
      {state.text && <span className={state.error ? "tone-danger" : "ok-text"} role={state.error ? "alert" : "status"}>{state.text}</span>}
    </div>
  </div>;
}

/** Раскрытая строка: заметка, у образца — черновик и итог рядом (операций, блоков) и урок. */
function DraftDetail({ draft, onSaved }: { draft: AssemblyDraft; onSaved: (draft: AssemblyDraft) => void }) {
  const [full, setFull] = useState<AssemblyDraft | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (draft.status !== "final") return;
    let live = true;
    void assemblyApi.draft(draft.id).then((result) => { if (live) setFull(result); })
      .catch((reason) => { if (live) setError(reason instanceof Error ? reason.message : "Образец не прочитан"); });
    return () => { live = false; };
  }, [draft.id, draft.status]);
  return <div className="draft-detail">
    {draft.note && <p className="draft-note">{draft.note}</p>}
    {draft.status === "final" && <>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!full && !error && <div className="empty">Загрузка…</div>}
      {full && <dl className="draft-pair">
        <div><dt>Черновик</dt><dd>{configSummary(full.draft_config) || "—"}</dd></div>
        <div><dt>Итог{full.final_revision ? ` · ревизия ${full.final_revision}` : ""}</dt><dd>{configSummary(full.final_config) || "—"}</dd></div>
      </dl>}
      <LessonEditor draft={draft} onSaved={onSaved} />
    </>}
  </div>;
}

/** Полоса «Собрано по черновику…» с полем урока: на вкладке «ТК КБn», пока урок не записан (черновик читается по id). */
export function PendingLesson({ draftId }: { draftId: string }) {
  const [draft, setDraft] = useState<AssemblyDraft | null>(null);
  useEffect(() => {
    let live = true;
    setDraft(null);
    void assemblyApi.draft(draftId).then((result) => { if (live) setDraft(result); }).catch(() => undefined);
    return () => { live = false; };
  }, [draftId]);
  if (!draft) return null;
  return <div className="draft-built draft-strip" role="group" aria-label="Собрано по черновику">
    <strong>Собрано по черновику от {formatDay(draft.created_at)} — запишите, что поправили</strong>
    <LessonEditor draft={draft} onSaved={setDraft} />
  </div>;
}

export function AssemblyDrafts({ project, itemIndex, items, error, openId, builtId, onOpen, onChanged, onUploaded }: {
  project: string;
  itemIndex: string;
  items: AssemblyDraft[];
  error: string;
  /** Черновик, открытый в «Ручной сборке»: сборка завершит его. */
  openId: string;
  /** Черновик, только что завершённый сборкой: под ним поле «Что поправили и почему». */
  builtId: string;
  onOpen: (draft: AssemblyDraft) => void;
  onChanged: (draft: AssemblyDraft) => void;
  onUploaded: () => void;
}) {
  const [status, setStatus] = useState<{ text: string; error: boolean }>({ text: "", error: false });
  const [busyId, setBusyId] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const [staged, setStaged] = useState<File[]>([]);
  const [customName, setCustomName] = useState<string | null>(null);
  const [busyStage, setBusyStage] = useState(false);
  const [dragging, setDragging] = useState(false);
  const mergedName = customName ?? (staged.length ? mergedDrawingName(staged[0].name, staged.length) : "");
  const built = items.find((draft) => draft.id === builtId);

  async function open(draft: AssemblyDraft) {
    setBusyId(draft.id); setStatus({ text: "", error: false });
    try {
      onOpen(await assemblyApi.draft(draft.id));
    } catch (reason) {
      setStatus({ text: reason instanceof Error ? reason.message : "Черновик не открыт", error: true });
    } finally { setBusyId(""); }
  }

  function readBase64(file: File): Promise<string> {
    return new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve((String(reader.result || "").split(",", 2)[1]) || "");
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }

  async function upload(file: File) {
    if (!DRAWING_TYPES.test(file.name)) { setStatus({ text: "Чертёж принимается в PDF, JPG или PNG.", error: true }); return; }
    setStatus({ text: `Загружаю «${file.name}»…`, error: false });
    try {
      await assemblyApi.uploadDrawing(project, itemIndex, file.name, await readBase64(file));
      setStatus({ text: `Чертёж «${file.name}» загружен в КД позиции`, error: false });
      onUploaded();
    } catch (reason) {
      setStatus({ text: reason instanceof Error ? reason.message : "Чертёж не загружен", error: true });
    }
  }

  /** Один файл — сразу в КД, как раньше; несколько — в список «сшить»: порядок и имя правит человек. */
  function pick(picked: File[]) {
    if (!picked.length) return;
    if (picked.length === 1 && !staged.length) { void upload(picked[0]); return; }
    const all = [...staged, ...picked];
    const foreign = all.find((file) => !PDF_TYPE.test(file.name));
    if (foreign) {
      setStatus({ text: `Несколько файлов сшиваются только из PDF — «${foreign.name}» другого формата.`, error: true });
      return;
    }
    setStatus({ text: "", error: false });
    setStaged(all);
  }

  function move(index: number, step: number) {
    const target = index + step;
    if (target < 0 || target >= staged.length) return;
    const next = [...staged];
    [next[index], next[target]] = [next[target], next[index]];
    setStaged(next);
  }

  async function uploadStaged() {
    const files = staged;
    setBusyStage(true);
    setStatus({ text: files.length > 1 ? `Сшиваю ${files.length} файлов…` : `Загружаю «${files[0].name}»…`, error: false });
    try {
      if (files.length === 1) {
        await assemblyApi.uploadDrawing(project, itemIndex, files[0].name, await readBase64(files[0]));
        setStatus({ text: `Чертёж «${files[0].name}» загружен в КД позиции`, error: false });
      } else {
        const parts = await Promise.all(files.map(async (file) => ({ file_name: file.name, content_base64: await readBase64(file) })));
        const stored = await assemblyApi.uploadMergedDrawing(project, itemIndex, mergedName, parts);
        const pages = stored.pages.reduce((sum, part) => sum + part.pages, 0);
        setStatus({ text: `Чертёж «${mergedName}» сшит из ${files.length} файлов (${pages} стр.) и загружен в КД позиции`, error: false });
      }
      setStaged([]); setCustomName(null);
      onUploaded();
    } catch (reason) {
      // Список остаётся: после правки (убрать битый файл) можно загрузить снова.
      setStatus({ text: reason instanceof Error ? reason.message : "Чертёж не загружен", error: true });
    } finally { setBusyStage(false); }
  }

  const columns: DataColumn<AssemblyDraft>[] = [
    { key: "date", title: "Дата", value: (draft) => draft.created_at, render: (draft) => formatDay(draft.created_at) },
    { key: "source", title: "Источник", value: (draft) => draftSource(draft.source) },
    { key: "drawing", title: "Чертёж", value: (draft) => (draft.drawing_url ? "есть" : ""),
      render: (draft) => (draft.drawing_url
        ? <a href={draft.drawing_url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>Чертёж</a> : "—") },
    { key: "status", title: "Статус", value: (draft) => draftStatus(draft.status),
      render: (draft) => <span className={draft.status === "final" ? "tone-ok" : "tone-info"}>{draftStatus(draft.status)}</span> },
    { key: "note", title: "Заметка", value: (draft) => draft.note, wrap: true,
      render: (draft) => <span className="draft-note-cell" title={draft.note || undefined}>{draft.note}</span> },
    { key: "open", title: "", value: () => "",
      render: (draft) => <button type="button" className="secondary small draft-open" disabled={busyId === draft.id || openId === draft.id}
        aria-label={`Открыть черновик от ${formatDate(draft.created_at)}`}
        onClick={(event) => { event.stopPropagation(); void open(draft); }}>{openId === draft.id ? "Открыт" : "Открыть"}</button> },
  ];

  return <section className={`gsec full drafts${dragging ? " is-drop" : ""}`} aria-label="Черновики с чертежей"
    onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
    onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false); }}
    onDrop={(event) => { event.preventDefault(); setDragging(false); pick([...event.dataTransfer.files]); }}>
    <div className="gsec-head">
      <h3>Черновики с чертежей</h3>
      <span className="gsec-meta">{items.length ? `${items.length} по позиции ${itemIndex}` : `позиция ${itemIndex}`}</span>
      <span className="manual-add">
        <button type="button" className="secondary small" onClick={() => input.current?.click()}>Загрузить чертёж</button>
        <input ref={input} type="file" hidden multiple aria-label="Файл чертежа" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
          onChange={(event) => { const picked = [...(event.target.files ?? [])]; event.target.value = ""; pick(picked); }} />
      </span>
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {staged.length > 0 && <div className="draft-stage" role="group" aria-label="Файлы к загрузке">
      <ol className="draft-stage-list">
        {staged.map((file, index) => <li key={`${file.name}-${index}`}>
          <span className="draft-stage-name" title={file.name}>{index + 1}. {file.name}</span>
          <span className="draft-stage-size">{fileSize(file.size)}</span>
          <span className="draft-stage-tools">
            <button type="button" className="secondary small" aria-label={`Выше: ${file.name}`} title="Выше" disabled={busyStage || index === 0} onClick={() => move(index, -1)}>↑</button>
            <button type="button" className="secondary small" aria-label={`Ниже: ${file.name}`} title="Ниже" disabled={busyStage || index === staged.length - 1} onClick={() => move(index, 1)}>↓</button>
            <button type="button" className="secondary small" aria-label={`Убрать: ${file.name}`} title="Убрать из списка" disabled={busyStage}
              onClick={() => { const next = staged.filter((_, at) => at !== index); setStaged(next); if (!next.length) setCustomName(null); }}>×</button>
          </span>
        </li>)}
      </ol>
      <div className="draft-stage-foot">
        {staged.length > 1 && <label className="gfield"><span className="gfield-label">Имя сшитого файла</span>
          <input aria-label="Имя сшитого файла" value={mergedName} onChange={(event) => setCustomName(event.target.value)} /></label>}
        <button type="button" className="primary small" disabled={busyStage || (staged.length > 1 && !mergedName.trim())} onClick={() => void uploadStaged()}>
          {staged.length > 1 ? `Загрузить (${staged.length} файлов → один PDF)` : "Загрузить (1 файл)"}</button>
        <button type="button" className="secondary small" disabled={busyStage} onClick={() => { setStaged([]); setCustomName(null); }}>Отмена</button>
      </div>
    </div>}
    {status.text && <div className={status.error ? "error-banner" : "gen-status"} role={status.error ? "alert" : "status"}>{status.text}</div>}
    {built && <div className="draft-built" role="group" aria-label="Собрано по черновику">
      <strong>Собрано по черновику от {formatDate(built.created_at)}</strong>
      <LessonEditor draft={built} onSaved={onChanged} />
    </div>}
    {items.length
      ? <DataTable rows={items} columns={columns} rowKey={(draft) => draft.id} label="Список черновиков" compactBelow={12}
        renderDetail={(draft) => <DraftDetail draft={draft} onSaved={onChanged} />} />
      : !error && <div className="empty">Черновиков по этой позиции нет. Загрузите чертёж (можно несколько PDF — их сошьют в один) и попросите Claude набрать по нему схему: черновик появится здесь.</div>}
  </section>;
}
