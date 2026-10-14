import { useState } from "react";
import { PageWindow } from "./PageWindow";
import { ApiError, failureText, referencesApi, type TechOperationRecord, type TechOperationTab } from "./api";
import type { DataCellEdit } from "./DataTable";

/* «+ Столбец» (владелец 14.10.2026: «добавить свои столбцы»): свой столбец таблицы базы. Имя спрашивает маленькое окно,
   сервер пишет пустой столбец во все записи таблицы разом; дальше он правится в ячейках, как остальные. */

export const MAX_COLUMN_NAME = 60;

/** Отказ записи ячейки словами ячейки: 409 — строки перечитываются, введённое остаётся в поле. */
export const cellFailure = (reason: unknown) => new Error(reason instanceof ApiError && reason.status === 409
  ? "Ячейку уже поменяли — в таблице свежее значение. Enter запишет ваше, Esc отменит."
  : failureText(reason, "Не записано"));

/** Правка ячейки своего столбца прямо в таблице: одно поле со старым значением, как в окне записи. */
export function ownCellEdit<T>(name: string, recordOf: (row: T) => TechOperationRecord | null | undefined): DataCellEdit<T> {
  return {
    enabled: (row) => Boolean(recordOf(row)),
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
