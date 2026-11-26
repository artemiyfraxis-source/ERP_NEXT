import { useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useSection } from "./EditSection";

/* Поле с правкой на месте (владелец 26.11, v2): обычный вид — текст, карандаш при наведении; щелчок — поле ввода,
   выпадашка или дата. Enter или уход из поля сохраняет, Esc отменяет; ошибка — под полем, набранное остаётся.
   Внутри «Редактировать» блока (`EditSection`) поле сразу открыто и пишет в черновик блока. */

export type InlineKind = "text" | "tel" | "date" | "select" | "textarea";

export function InlineField({ name, label, value, display, kind = "text", options = [], editable = false, onSave, wide = false,
  dataLabel = label, maxLength, after }: {
  name: string;
  label: string;
  /** Сохранённое значение как строка формы. */
  value: string;
  /** Вид без правки; без него — само значение, пусто — серое «—». */
  display?: ReactNode;
  kind?: InlineKind;
  /** Для выпадашки: [значение, подпись]. */
  options?: Array<readonly [string, string]>;
  editable?: boolean;
  /** Правка одного поля; без неё поле правится только в блоке. */
  onSave?: (value: string) => Promise<void>;
  wide?: boolean;
  dataLabel?: string;
  maxLength?: number;
  /** Рядом со значением (копировать, скачать). */
  after?: ReactNode;
}) {
  const section = useSection();
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  // Esc закрывает поле раньше, чем приходит его blur: закрытое поле не сохраняется.
  const closed = useRef(true);
  const inSection = Boolean(section?.editing && editable);
  const open = inSection || draft !== null;
  const current = inSection ? section!.draft[name] ?? value : draft ?? value;

  const cancel = () => { closed.current = true; setDraft(null); setError(""); };
  const commit = async (next = current) => {
    if (inSection || saving || closed.current) return;
    if (next.trim() === value.trim() || !onSave) { cancel(); return; }
    setSaving(true); setError("");
    try { await onSave(next); closed.current = true; setDraft(null); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setSaving(false); }
  };
  const change = (next: string) => {
    if (inSection) section!.set(name, next);
    else { setDraft(next); setError(""); if (kind === "select") void commit(next); }
  };
  const keys = (event: KeyboardEvent<HTMLElement>) => {
    if (inSection) return;
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); cancel(); }
    if (event.key === "Enter" && (kind !== "textarea" || event.ctrlKey || event.metaKey)) { event.preventDefault(); void commit(); }
  };

  const common = {
    "aria-label": label, value: current, disabled: saving, autoFocus: !inSection, maxLength,
    onKeyDown: keys, onBlur: () => { if (!inSection && kind !== "select") void commit(); },
  };
  const editor = kind === "select"
    ? <select {...common} onChange={(event) => change(event.target.value)}>
      {options.map(([key, text]) => <option key={key} value={key}>{text}</option>)}
      {current && !options.some(([key]) => key === current) && <option value={current}>{current} (прежнее)</option>}
    </select>
    : kind === "textarea"
      ? <textarea {...common} rows={2} onChange={(event) => change(event.target.value)} />
      : <input {...common} type={kind} onChange={(event) => change(event.target.value)} />;
  const shown = display ?? (value.trim() ? value : <span className="muted">—</span>);
  const canPencil = editable && Boolean(onSave) && !section?.editing;

  return <div className={`intake-field inline-field${wide ? " wide" : ""}${open ? " open" : ""}`}>
    <span className="intake-field-label">{label}</span>
    {open
      ? <span className="inline-field-editor">{editor}{saving && <span className="muted"> Сохраняем…</span>}</span>
      : <span className="intake-field-value" data-label={dataLabel}>
        {shown}{after}
        {canPencil && <button type="button" className="inline-field-pencil" aria-label={`Изменить: ${label}`} title={`Изменить: ${label}`}
          onClick={() => { closed.current = false; setDraft(value); }} />}
      </span>}
    {error && <span className="inline-field-error" role="alert">{error}</span>}
  </div>;
}
