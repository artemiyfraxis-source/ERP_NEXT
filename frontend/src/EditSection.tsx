import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

/* Блок с правкой «всё сразу» (владелец 26.11, v2): «Редактировать» в шапке блока — все поля блока становятся полями
   ввода, «Сохранить» / «Отмена», пока есть несохранённое — метка «● не сохранено» и вопрос при уходе со страницы.
   Поля блока — `InlineField`: вне правки блока каждое правится само (карандаш). */

export interface SectionMode {
  editing: boolean;
  draft: Record<string, string>;
  set: (name: string, value: string) => void;
}
export const SectionContext = createContext<SectionMode | null>(null);
export const useSection = () => useContext(SectionContext);

/** Блоки с несохранённой правкой (по области: id отправки) — экран спрашивает перед сворачиванием и сменой вкладки. */
const unsaved = new Map<symbol, string>();
export const hasUnsaved = (scope?: string) => [...unsaved.values()].some((value) => scope === undefined || value === scope);
/** true — можно уходить: несохранённого нет или человек согласился его потерять. */
export const leaveUnsaved = (scope?: string) => !hasUnsaved(scope) || window.confirm("Есть несохранённые правки — закрыть без сохранения?");

/** Изменённые поля черновика относительно сохранённых значений (пробелы по краям не считаются правкой). */
export function changedValues(draft: Record<string, string>, values: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(draft).filter(([name, value]) => value.trim() !== (values[name] ?? "").trim()));
}

export function EditSection({ title, editable, values, onSave, children, extra, className = "intake-panel", scope = "" }: {
  title: string;
  /** Есть хоть одно поле, которое можно править: без него кнопки «Редактировать» нет. */
  editable: boolean;
  /** Сохранённые значения полей блока (по `name` полей). */
  values: Record<string, string>;
  /** Сохраняет только изменённые поля; отказ — текст ошибки под блоком, блок остаётся в правке. */
  onSave: (changed: Record<string, string>) => Promise<void>;
  children: ReactNode;
  /** Действия блока рядом с заголовком (скачать, «Доставка КБ»…). */
  extra?: ReactNode;
  className?: string;
  /** Область для вопроса «закрыть без сохранения?» (id отправки). */
  scope?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const changed = changedValues(draft, values);
  const dirty = editing && Object.keys(changed).length > 0;

  useEffect(() => {
    if (!dirty) return;
    const key = Symbol(title);
    unsaved.set(key, scope);
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", guard);
    return () => { unsaved.delete(key); window.removeEventListener("beforeunload", guard); };
  }, [dirty, scope, title]);

  const start = () => { setDraft({}); setError(""); setEditing(true); };
  const cancel = () => { setDraft({}); setError(""); setEditing(false); };
  const save = async () => {
    if (saving) return;
    if (!Object.keys(changed).length) { cancel(); return; }
    setSaving(true); setError("");
    try { await onSave(changed); setDraft({}); setEditing(false); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setSaving(false); }
  };
  const mode: SectionMode = { editing, draft, set: (name, value) => setDraft((current) => ({ ...current, [name]: value })) };

  return <section className={`${className} edit-section${editing ? " editing" : ""}`} aria-label={title}>
    <header className="edit-section-head">
      <h4>{title}</h4>
      {dirty && <span className="edit-section-dirty" role="status">● не сохранено</span>}
      <span className="edit-section-actions">
        {!editing && extra}
        {editable && !editing && <button type="button" className="link-button edit-section-start" aria-label={`Редактировать: ${title}`} onClick={start}>Редактировать</button>}
        {editing && <>
          <button type="button" className="secondary small" disabled={saving} onClick={cancel}>Отмена</button>
          <button type="button" className="primary small" disabled={saving} onClick={() => void save()}>{saving ? "Сохраняем…" : "Сохранить"}</button>
        </>}
      </span>
    </header>
    <form className="edit-section-body" onSubmit={(event) => { event.preventDefault(); void save(); }}
      onKeyDown={(event) => { if (editing && event.key === "Escape") { event.stopPropagation(); cancel(); } }}>
      <SectionContext.Provider value={mode}>{children}</SectionContext.Provider>
      {editing && <button type="submit" hidden aria-hidden="true" tabIndex={-1} />}
    </form>
    {error && <p className="edit-section-error error-banner" role="alert">{error}</p>}
  </section>;
}
