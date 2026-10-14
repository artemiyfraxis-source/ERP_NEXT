import { useState, type ReactNode } from "react";
import { PageWindow } from "./PageWindow";
import { ApiError, failureText, referencesApi, type TechOperationRecord, type TechOperationTab } from "./api";
import type { DataCellEdit } from "./DataTable";

/* «+ Столбец» (владелец 14.10.2026: «добавить свои столбцы»): свой столбец таблицы базы. Имя спрашивает маленькое окно,
   сервер пишет пустой столбец во все записи таблицы разом; дальше он правится в ячейках, как остальные. Удалить —
   в «Колонках», с подтверждением окном; сервер удаляет только пустой столбец. */

export const MAX_COLUMN_NAME = 60;

/** Отказ записи ячейки словами ячейки: 409 — строки перечитываются, введённое остаётся в поле. */
export const cellFailure = (reason: unknown) => new Error(reason instanceof ApiError && reason.status === 409
  ? "Ячейку уже поменяли — в таблице свежее значение. Enter запишет ваше, Esc отменит."
  : failureText(reason, "Не записано"));

/** Правка ячейки поля записи прямо в таблице — путь окна записи: одно поле со старым значением (сервер откажет, если
    запись поменяли). `anyRow` — ячейка правится и у записи без этого поля (свой столбец: строка из зеркала его не несёт). */
export function fieldCellEdit<T>(name: string, recordOf: (row: T) => TechOperationRecord | null | undefined, anyRow = false): DataCellEdit<T> {
  return {
    enabled: (row) => {
      const record = recordOf(row);
      return Boolean(record && (anyRow || name in record.fields));
    },
    value: (row) => recordOf(row)?.fields[name] ?? "",
    save: async (row, value) => {
      const record = recordOf(row);
      if (!record) throw new Error("Нет записи для правки");
      try {
        await referencesApi.editRecord(record.id, [{ field: name, old: record.fields[name] ?? "", value }]);
      } catch (reason) { throw cellFailure(reason); }
    },
  };
}

/** Правка ячейки своего столбца прямо в таблице: одно поле со старым значением, как в окне записи. */
export function ownCellEdit<T>(name: string, recordOf: (row: T) => TechOperationRecord | null | undefined): DataCellEdit<T> {
  return fieldCellEdit(name, recordOf, true);
}

export function AddColumnButton({ tab, onAdded }: { tab: TechOperationTab; onAdded: (name: string) => Promise<void> | void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const close = () => { setOpen(false); setName(""); setError(""); };
  async function save() {
    setBusy(true); setError("");
    try {
      const added = await referencesApi.addColumn(tab, name);
      close();
      await onAdded(added.name);
    } catch (reason) {
      setError(failureText(reason, "Столбец не добавлен"));
    } finally { setBusy(false); }
  }
  return <>
    <button type="button" className="secondary small" onClick={() => setOpen(true)}>+ Столбец</button>
    {open && <PageWindow label="Новый столбец" width={460} close={close}>
      <form className="panel" onSubmit={(event) => { event.preventDefault(); if (name.trim() && !busy) void save(); }}>
        <div className="manual-stock-head"><h2>Новый столбец</h2></div>
        {error && <div className="error-banner" role="alert">{error}</div>}
        <label><small>Название столбца</small>
          <input autoFocus aria-label="Название столбца" maxLength={MAX_COLUMN_NAME} value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <div className="form-actions">
          <button type="submit" className="primary" disabled={busy || !name.trim()}>Добавить столбец</button>
          <button type="button" className="secondary" onClick={close}>Отмена</button>
        </div>
      </form>
    </PageWindow>}
  </>;
}

/** «Удалить столбец» своих столбцов таблицы: `removeOf(name)` — для `DataColumn.remove` (без права правки — undefined),
    `dialog` — окно подтверждения, его экран ставит рядом с таблицей. `filled(name)` — сколько записей с данными в столбце:
    непустой столбец окно не удаляет, а говорит, что сначала очистить. */
export function useColumnRemoval(tab: TechOperationTab, canEdit: boolean, filled: (name: string) => number,
  onRemoved: (name: string) => Promise<void> | void): { removeOf: (name: string) => (() => void) | undefined; dialog: ReactNode } {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const close = () => { if (!busy) { setName(""); setError(""); } };
  const count = name ? filled(name) : 0;
  async function remove() {
    setBusy(true); setError("");
    try {
      await referencesApi.removeColumn(tab, name);
      const removed = name;
      setName("");
      await onRemoved(removed);
    } catch (reason) {
      setError(failureText(reason, "Столбец не удалён"));
    } finally { setBusy(false); }
  }
  const dialog = name ? <PageWindow label="Удалить столбец" width={460} close={close}>
    <div className="panel">
      <div className="manual-stock-head"><h2>Удалить столбец «{name}»?</h2></div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {count > 0
        ? <p className="warning-inline">В столбце есть данные: записей — {count}. Очистите эти ячейки, потом удаляйте.</p>
        : <p>Столбец пустой во всех записях — он уйдёт из таблицы и из выгрузки в Excel.</p>}
      <div className="form-actions">
        <button type="button" className="primary danger" disabled={busy || count > 0} onClick={() => void remove()}>Удалить столбец</button>
        <button type="button" className="secondary" disabled={busy} onClick={close}>Отмена</button>
      </div>
    </div>
  </PageWindow> : null;
  return { removeOf: (column) => canEdit ? () => { setError(""); setName(column); } : undefined, dialog };
}

/** Сколько записей с данными в поле `name`. */
export const filledIn = (records: Array<TechOperationRecord | null | undefined>, name: string) =>
  records.filter((record) => String(record?.fields[name] ?? "").trim()).length;
