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
  useEffect(() => { setText(draft.lesson); }, [draft.id, draft.lesson]);
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
  const built = items.find((draft) => draft.id === builtId);

  async function open(draft: AssemblyDraft) {
    setBusyId(draft.id); setStatus({ text: "", error: false });
    try {
      onOpen(await assemblyApi.draft(draft.id));
    } catch (reason) {
      setStatus({ text: reason instanceof Error ? reason.message : "Черновик не открыт", error: true });
    } finally { setBusyId(""); }
  }

  async function upload(file: File) {
    if (!DRAWING_TYPES.test(file.name)) { setStatus({ text: "Чертёж принимается в PDF, JPG или PNG.", error: true }); return; }
    setStatus({ text: `Загружаю «${file.name}»…`, error: false });
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      });
      await assemblyApi.uploadDrawing(project, itemIndex, file.name, dataUrl.split(",", 2)[1] || "");
      setStatus({ text: `Чертёж «${file.name}» загружен в КД позиции`, error: false });
      onUploaded();
    } catch (reason) {
      setStatus({ text: reason instanceof Error ? reason.message : "Чертёж не загружен", error: true });
    }
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

  return <section className="gsec full drafts" aria-label="Черновики с чертежей">
    <div className="gsec-head">
      <h3>Черновики с чертежей</h3>
      <span className="gsec-meta">{items.length ? `${items.length} по позиции ${itemIndex}` : `позиция ${itemIndex}`}</span>
      <span className="manual-add">
        <button type="button" className="secondary small" onClick={() => input.current?.click()}>Загрузить чертёж</button>
        <input ref={input} type="file" hidden aria-label="Файл чертежа" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
          onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void upload(file); }} />
      </span>
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {status.text && <div className={status.error ? "error-banner" : "gen-status"} role={status.error ? "alert" : "status"}>{status.text}</div>}
    {built && <div className="draft-built" role="group" aria-label="Собрано по черновику">
      <strong>Собрано по черновику от {formatDate(built.created_at)}</strong>
      <LessonEditor draft={built} onSaved={onChanged} />
    </div>}
    {items.length
      ? <DataTable rows={items} columns={columns} rowKey={(draft) => draft.id} label="Список черновиков" compactBelow={12}
        renderDetail={(draft) => <DraftDetail draft={draft} onSaved={onChanged} />} />
      : !error && <div className="empty">Черновиков по этой позиции нет — пришлите чертёж Claude.</div>}
  </section>;
}
