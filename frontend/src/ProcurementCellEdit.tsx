import { useState, type KeyboardEvent } from "react";
import { ApiError, failureText, procurementApi, type ProcurementLine } from "./api";
import { formatDay } from "./format";

/* Правка «Комментария», DATASHEET и дат строки закупки прямо в ячейке (SUPPLYMAP писал их в лист руками).
   Клик по ячейке открывает поле; Enter или уход из поля сохраняет строку по её версии, Esc отменяет.
   409 — строку уже изменили: ячейка говорит об этом, а страница перечитывает именно эту строку;
   422 — текст сервера. Права на строку (своя, подходящий статус) окончательно решает сервер:
   страница лишь не предлагает правку там, где она заведомо закрыта (`canEditNote`, `canEditExpected`). */

export const CONFLICT_TEXT = "Строку изменили — обновите";

/** Комментарий: от «В работе», не на «Новый» и «Отменено» (NOTE_EDITABLE). */
const NOTE_CLOSED = new Set(["Новый", "Отменено"]);
/** Ожид. дата: от «Жду счет» до «Карантин» (EXPECTED_ARRIVAL_EDITABLE). */
const EXPECTED_OPEN = new Set(["Жду счет", "В оплату", "В пути", "Карантин"]);

export const canEditNote = (line: ProcurementLine) => !NOTE_CLOSED.has(line.status);
export const canEditExpected = (line: ProcurementLine) => EXPECTED_OPEN.has(line.status);
/** Факт. дата: от «В пути» и дальше, включая «Готово» (ACTUAL_ARRIVAL_EDITABLE); необязательна и стирается. */
const ACTUAL_OPEN = new Set(["В пути", "Карантин", "Готово"]);
export const canEditActual = (line: ProcurementLine) => ACTUAL_OPEN.has(line.status);

/** Что страница даёт ячейкам: куда положить свежую строку и как перечитать строку после 409. */
export interface LineEditing {
  onSaved: (line: ProcurementLine) => void;
  onConflict: (line: ProcurementLine) => void | Promise<unknown>;
}

export type LineField = "note" | "datasheet" | "expected_arrival" | "actual_arrival";

/** Сохраняет одно поле строки; при отказе бросает Error с текстом для ячейки. */
export async function saveLineField(line: ProcurementLine, field: LineField, value: string, editing: LineEditing): Promise<void> {
  let saved: ProcurementLine;
  try {
    const result = field === "note"
      ? await procurementApi.setNote(line.id, line.version, value)
      : field === "datasheet"
        ? await procurementApi.setDatasheet(line.id, line.version, value)
      : field === "actual_arrival"
        ? await procurementApi.setActualArrival(line.id, line.version, value)
        : await procurementApi.setExpectedArrival(line.id, line.version, value);
    saved = result.line;
  } catch (reason) {
    if (reason instanceof ApiError && reason.status === 409) {
      try { await editing.onConflict(line); } catch { /* перечитать не вышло — ячейка всё равно скажет */ }
      throw new Error(CONFLICT_TEXT);
    }
    throw new Error(failureText(reason, "Не сохранилось"));
  }
  editing.onSaved(saved);
}

type CellKind = "text" | "date";

function EditableCell({ line, field, kind, label, value, shown, editing, canEdit, disabled, optional }: {
  line: ProcurementLine;
  field: LineField;
  kind: CellKind;
  label: string;
  /** Значение в виде, в котором его правят: текст или 2026-10-04. */
  value: string;
  /** Как значение выглядит в покое. */
  shown: string;
  editing: LineEditing;
  canEdit: boolean;
  disabled?: boolean;
  /** Поле необязательное: пустое значение сохраняется (стирает дату). */
  optional?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const ariaLabel = `${label} ${line.legacy_id}`;

  function start() {
    setDraft(value);
    setError("");
    setOpen(true);
  }

  function cancel() {
    setOpen(false);
    setError("");
  }

  async function commit() {
    if (saving) return;
    const next = kind === "text" ? draft.trim() : draft;
    // Ожид. дату стереть нельзя (сервер хранит её только заполненной); факт. дату — можно. Без изменений — сохранять нечего.
    if (next === value || (kind === "date" && !next && !optional)) { cancel(); return; }
    setSaving(true);
    setError("");
    try {
      await saveLineField(line, field, next, editing);
      setOpen(false);
    } catch (reason) {
      const text = reason instanceof Error ? reason.message : "Не сохранилось";
      setError(text);
      // Строка уже перечитана с сервера: поле закрываем, чтобы показать свежее значение, а не черновик на старой версии.
      if (text === CONFLICT_TEXT) setOpen(false);
    } finally {
      setSaving(false);
    }
  }

  function keys(event: KeyboardEvent) {
    if (event.key === "Escape") { event.preventDefault(); cancel(); return; }
    if (event.key === "Enter" && !(kind === "text" && event.shiftKey)) { event.preventDefault(); void commit(); }
  }

  const failure = error && <small className="cell-edit-error" role="alert">{error}</small>;

  if (!canEdit || disabled) {
    return <>{shown}{failure}</>;
  }
  if (!open) {
    return (
      <>
        <button type="button" className="cell-edit" aria-label={ariaLabel} title={`Изменить: ${label}`} onClick={start}>
          {shown || <span className="cell-edit-empty">{kind === "date" ? "дд.мм.гггг" : "добавить"}</span>}
        </button>
        {failure}
      </>
    );
  }
  return (
    <>
      {kind === "text" ? (
        <textarea
          className="cell-edit-input" aria-label={ariaLabel} rows={3} autoFocus value={draft} disabled={saving}
          onChange={(event) => setDraft(event.target.value)} onBlur={() => void commit()} onKeyDown={keys}
        />
      ) : (
        <input
          className="cell-edit-input" type="date" aria-label={ariaLabel} autoFocus value={draft} disabled={saving}
          onChange={(event) => setDraft(event.target.value)} onBlur={() => void commit()} onKeyDown={keys}
        />
      )}
      {failure}
    </>
  );
}

/** КОММЕНТАРИЙ строки: свободная заметка закупщика. */
export function NoteCell({ line, editing, canEdit = true, disabled }: { line: ProcurementLine; editing?: LineEditing; canEdit?: boolean; disabled?: boolean }) {
  if (!editing) return <>{line.note ?? ""}</>;
  return (
    <EditableCell
      line={line} field="note" kind="text" label="Комментарий" value={line.note ?? ""} shown={line.note ?? ""}
      editing={editing} canEdit={canEdit && canEditNote(line)} disabled={disabled}
    />
  );
}

const WEB_LINK = /^https?:\/\/\S+$/i;

/** DATASHEET в покое: у ссылки — адрес сайта («example.com»), иначе текст как есть. */
export function datasheetLabel(value: string | null | undefined): string {
  const text = String(value ?? "").trim();
  if (!WEB_LINK.test(text)) return text;
  try { return new URL(text).hostname.replace(/^www\./, ""); } catch { return text; }
}

/** DATASHEET строки: ссылка на описание позиции или текст. Правится там же и тем же, что комментарий. */
export function DatasheetCell({ line, editing, canEdit = true, disabled }: { line: ProcurementLine; editing?: LineEditing; canEdit?: boolean; disabled?: boolean }) {
  const value = line.datasheet ?? "";
  const link = WEB_LINK.test(value.trim()) ? value.trim() : "";
  const open = link && <a className="cell-link" href={link} target="_blank" rel="noreferrer" title={link}>открыть</a>;
  if (!editing || !canEdit || !canEditNote(line) || disabled) {
    return link ? <a href={link} target="_blank" rel="noreferrer" title={link}>{datasheetLabel(value)}</a> : <>{value}</>;
  }
  return (
    <>
      <EditableCell
        line={line} field="datasheet" kind="text" label="DATASHEET" value={value} shown={datasheetLabel(value)}
        editing={editing} canEdit
      />
      {open}
    </>
  );
}

/** ОЖИД. ДАТА строки: когда поставщик обещает привезти; закупщик двигает её до прихода товара. */
export function ExpectedDateCell({ line, editing, canEdit = true, disabled }: { line: ProcurementLine; editing?: LineEditing; canEdit?: boolean; disabled?: boolean }) {
  const shown = formatDay(line.expected_arrival);
  if (!editing) return <>{shown}</>;
  return (
    <EditableCell
      line={line} field="expected_arrival" kind="date" label="Ожид. дата" value={line.expected_arrival ?? ""} shown={shown}
      editing={editing} canEdit={canEdit && canEditExpected(line)} disabled={disabled}
    />
  );
}

/** ФАКТ. ДАТА: день приёмки. Заполняет приёмка; закупщик может проставить или поправить её сам (необязательно). */
export function ActualDateCell({ line, editing, canEdit = true, disabled }: { line: ProcurementLine; editing?: LineEditing; canEdit?: boolean; disabled?: boolean }) {
  const shown = formatDay(line.actual_arrival);
  if (!editing) return <>{shown}</>;
  return (
    <EditableCell
      line={line} field="actual_arrival" kind="date" label="Факт. дата" value={line.actual_arrival ?? ""} shown={shown}
      editing={editing} canEdit={canEdit && canEditActual(line)} disabled={disabled} optional
    />
  );
}
