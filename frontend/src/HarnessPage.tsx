import { Suspense, useEffect, useRef, useState } from "react";
import { failureText, harnessApi, kitsetsApi } from "./api";
import { HarnessEditorLazy, loadHarnessDrawing, type DrawingSaveState, type HarnessDrawingPorts, type HarnessEditorCatalogApi } from "./harness";
import { HarnessHistory } from "./HarnessHistory";
import { closeHarness, openLibrary } from "./harnessNav";
import { readFileBase64 } from "./ProjectsPage";
import { formatMoment } from "./format";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";

const STATE_TEXT: Record<DrawingSaveState, string> = {
  saved: "Сохранено",
  saving: "Сохраняю…",
  conflict: "Не сохранено — чертёж изменили",
  error: "Не сохранено",
};

/** The user's edits a 409 refused, kept for this tab (survives a reload of the page, not the browser). */
interface ConflictCopy { readonly design: Record<string, unknown>; readonly at: string; readonly message: string }

const copyKey = (project: string, index: string) => `harness-conflict:${project}:${index}`;
function readCopy(project: string, index: string): ConflictCopy | null {
  try {
    const raw = window.sessionStorage.getItem(copyKey(project, index));
    const value = raw ? JSON.parse(raw) as ConflictCopy : null;
    return value && typeof value.design === "object" && value.design ? value : null;
  } catch { return null; }
}
function writeCopy(project: string, index: string, copy: ConflictCopy | null) {
  try {
    if (copy) window.sessionStorage.setItem(copyKey(project, index), JSON.stringify(copy));
    else window.sessionStorage.removeItem(copyKey(project, index));
  } catch { /* no storage (private window): the copy stays in memory only */ }
}
/** The server's 409 text without its advice to reload: the bar offers the two ways out itself. */
const conflictReason = (message: string) => {
  const reason = message.split(" — ")[0]!.replace(/[.\s]+$/, "");
  return reason ? reason.charAt(0).toLowerCase() + reason.slice(1) : "чертёж изменили в другом окне";
};

/** Чертёж жгута на весь экран: редактор над чертежом из КД позиции, справочники и библиотека компонентов ERP.
 *  Без права «Проекты: правка» — тот же чертёж в режиме просмотра. Чужое сохранение (409) не теряет правки: они
 *  остаются на экране и в копии вкладки, на выбор «Взять их версию» или «Сохранить мою поверх». */
export function HarnessPage({ project, index, canEdit, onBack = () => closeHarness(project) }: {
  project: string; index: string; canEdit: boolean; onBack?: () => void;
}) {
  const [title, setTitle] = useState("");
  const [ports, setPorts] = useState<HarnessDrawingPorts | null>(null);
  const [catalog, setCatalog] = useState<HarnessEditorCatalogApi | null>(null);
  const [state, setState] = useState<DrawingSaveState>("saved");
  const [copy, setCopy] = useState<ConflictCopy | null>(() => (canEdit ? readCopy(project, index) : null));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [generation, setGeneration] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let live = true;
    kitsetsApi.get(project)
      .then((card) => { if (live) setTitle(card.assemblies.find((item) => item.index === index)?.name ?? ""); })
      .catch((reason) => { if (live) setError(failureText(reason, "Позиция не прочитана")); });
    return () => { live = false; };
  }, [project, index]);

  // The editor and its model load as their own chunk; «Взять их версию» / a restore start them over (generation).
  useEffect(() => {
    let live = true;
    setPorts(null); setState("saved");
    void loadHarnessDrawing().then(({ harnessDrawing, createErpHarnessEditorCatalog }) => {
      if (!live) return;
      const erp = createErpHarnessEditorCatalog();
      const drawing = harnessDrawing({
        templates: erp.templates, readOnly: !canEdit,
        onState: (next, message) => {
          if (!live) return;
          setState(next);
          const mine = drawing.mine();
          if (next === "conflict" && mine) {
            const kept = { design: mine as unknown as Record<string, unknown>, at: new Date().toISOString(),
              message: message ?? "Чертёж изменили в другом окне" };
            writeCopy(project, index, kept);
            setCopy((previous) => ({ ...kept, message: message ?? previous?.message ?? kept.message }));
          }
        },
      });
      setCatalog(erp);
      setPorts(drawing);
    }).catch((reason) => { if (live) setError(failureText(reason, "Редактор не загрузился")); });
    return () => { live = false; };
  }, [project, index, canEdit, generation]);

  function restart() { setGeneration((value) => value + 1); }

  function takeTheirs() {
    writeCopy(project, index, null);
    setCopy(null); setError(""); setNote("Открыта сохранённая версия, ваши правки отброшены.");
    restart();
  }

  // «Сохранить мою поверх»: the kept edits become the next revision after the one on the server now.
  async function saveMine() {
    if (!copy) return;
    setBusy(true); setError("");
    try {
      const fresh = await harnessApi.drawing(project, index);
      const saved = await harnessApi.save(project, index, copy.design, fresh.revision);
      writeCopy(project, index, null);
      setCopy(null); setNote(`Ваша версия сохранена поверх — версия ${saved.revision}.`);
      restart();
    } catch (reason) { setError(failureText(reason, "Не сохранено")); }
    finally { setBusy(false); }
  }

  async function importZip(file: File | undefined) {
    if (!file) return;
    if (!window.confirm(`Загрузить «${file.name}»? Чертёж ${index} в ERP будет заменён содержимым файла.`)) return;
    setError(""); setNote(`Загружаю ${file.name}…`);
    try {
      const current = await harnessApi.drawing(project, index);
      await harnessApi.import(project, index, { file_name: file.name, content_b64: await readFileBase64(file), revision: current.revision });
      setNote("Чертёж загружен.");
      restart();
    } catch (reason) { setNote(""); setError(failureText(reason, "Чертёж не загружен")); }
  }

  // «Сохранить версию»: автосохранения одного сотрудника за 10 минут — одна строка истории; кнопка закрепляет текущую.
  async function checkpoint() {
    if (!ports) return;
    setError("");
    try {
      const fixed = await harnessApi.checkpoint(project, index, ports.revision());
      setNote(fixed.revision ? `Версия ${fixed.revision} сохранена в истории.` : "Чертёж ещё не сохранён — сохранять в историю нечего.");
    } catch (reason) { setNote(""); setError(failureText(reason, "Версия не сохранена")); }
  }

  const heading = `${index}${title ? ` ${title}` : ""}`;
  const tone: Tone = state === "conflict" || state === "error" ? "danger" : state === "saving" ? "warning" : "success";
  return <section className="harness-page" aria-label={`Чертёж жгута ${heading}`}>
    <header className="harness-page-head">
      <button className="secondary small" onClick={onBack}>← Назад</button>
      <h1>{heading}</h1>
      <span className="muted">Чертёж жгута</span>
      {canEdit
        ? <span className="harness-save" role="status"><StatusChip tone={tone}>{STATE_TEXT[state]}</StatusChip></span>
        : <span className="harness-save" role="status" title="Правят сотрудники с правом «Проекты: правка»"><StatusChip tone="neutral">Только просмотр</StatusChip></span>}
      <span className="harness-page-actions">
        {note && <span className="muted harness-page-note">{note}</span>}
        {canEdit && <button className="secondary small" disabled={!ports || state !== "saved" || Boolean(copy)} onClick={() => void checkpoint()}
          title="Текущая сохранённая версия останется в истории отдельной строкой">Сохранить версию</button>}
        <button className="secondary small" onClick={() => setHistoryOpen(true)}>История версий</button>
        <button className="secondary small" onClick={() => openLibrary(project, index)}
          title="Шаблоны разъёмов жгута: список, редактор, публикация версий">Библиотека компонентов</button>
        <a className="secondary small button-link" href={harnessApi.exportUrl(project, index)} download
          title="Чертёж в формате TECHMAP-GRAPHER (.techmap-project.zip)">Скачать .zip</a>
        {canEdit && <button className="secondary small" onClick={() => fileRef.current?.click()}
          title="Заменить чертёж файлом .techmap-project.zip из TECHMAP-GRAPHER">Загрузить .zip</button>}
      </span>
      <input ref={fileRef} type="file" accept=".zip" hidden data-testid="harness-zip" onChange={(event) => { void importZip(event.target.files?.[0]); event.target.value = ""; }} />
    </header>
    {copy && <div className="harness-conflict" role="alert">
      <span><b>Ваши правки от {formatMoment(copy.at)} не сохранены</b> — {conflictReason(copy.message)}. Правки остались на экране:
        возьмите сохранённую версию или сохраните свою поверх неё.</span>
      <button className="secondary small" disabled={busy} onClick={takeTheirs}>Взять их версию</button>
      <button className="primary small" disabled={busy} onClick={() => void saveMine()}
        title="Ваш чертёж станет новой версией; сохранённая сейчас останется в «Истории версий»">Сохранить мою поверх</button>
    </div>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="harness-page-body">
      {!ports || !catalog ? <div className="empty">Загрузка…</div>
        : <Suspense fallback={<div className="empty">Загрузка редактора…</div>}>
          <HarnessEditorLazy key={generation} projectId={project} harnessId={index} harnessDesignation={heading} initialView="e4"
            api={ports.api} placements={ports.placements} catalog={catalog} readOnly={!canEdit} />
        </Suspense>}
    </div>
    {historyOpen && <HarnessHistory project={project} index={index} canEdit={canEdit} onClose={() => setHistoryOpen(false)}
      onRestored={(from, revision) => { setError(""); setNote(`Восстановлена версия ${from} — теперь это версия ${revision}.`); restart(); }} />}
  </section>;
}
