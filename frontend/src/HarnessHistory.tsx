import { useEffect, useState } from "react";
import { failureText, harnessApi, type HarnessRevision, type HarnessRevisions } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDate } from "./format";
import { StatusChip } from "./StatusChip";

/** Как появилась версия — по-русски. */
export function revisionOrigin(item: HarnessRevision): string {
  if (item.kind === "restored") return item.restored_from ? `Восстановлена из версии ${item.restored_from}` : "Восстановлена";
  if (item.kind === "imported") return "Загружена из TECHMAP-GRAPHER";
  if (item.kind === "initial") return "Начальная (до истории)";
  const saves = item.revision - item.first_revision + 1;
  const text = saves > 1 ? `Автосохранения (${saves})` : "Сохранение";
  return item.closed ? `${text}, закреплена` : text;
}

/** Когда шла строка: «10.10.2026 14:00–14:09» (один день), иначе обе даты; одно сохранение — одно время. */
export function revisionSpan(item: HarnessRevision): string {
  const start = formatDate(item.started_at);
  const end = formatDate(item.saved_at);
  if (start === end) return end;
  const [startDay] = start.split(" ");
  const [endDay, endTime] = end.split(" ");
  return startDay === endDay && endTime ? `${start}–${endTime}` : `${start} – ${end}`;
}

function size(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1).replace(".", ",")} МБ` : `${Math.max(1, Math.round(bytes / 1024))} КБ`;
}

/** «История версий» чертежа жгута сбоку поверх редактора: каждая сохранённая версия — кто, когда, как появилась.
 *  Выбранную версию можно восстановить: она становится чертежом новой версией, история не меняется. */
export function HarnessHistory({ project, index, canEdit, onClose, onRestored }: {
  project: string; index: string; canEdit: boolean; onClose: () => void;
  onRestored: (from: number, revision: number) => void;
}) {
  const [history, setHistory] = useState<HarnessRevisions | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    let live = true;
    setError("");
    harnessApi.revisions(project, index)
      .then((loaded) => { if (live) setHistory(loaded); })
      .catch((reason) => { if (live) setError(failureText(reason, "История версий не прочитана")); });
    return () => { live = false; };
  }, [project, index, generation]);

  async function restore(revision: number) {
    if (!history) return;
    if (!window.confirm(`Восстановить версию ${revision}? Она станет текущим чертежом (версия ${history.current + 1}); несохранённые правки в редакторе пропадут.`)) return;
    setBusy(true); setError("");
    try {
      const restored = await harnessApi.restoreRevision(project, index, revision, history.current);
      onRestored(revision, restored.revision ?? history.current + 1);
      setSelected(null);
      setGeneration((value) => value + 1);
    } catch (reason) {
      setError(failureText(reason, "Версия не восстановлена"));
    } finally { setBusy(false); }
  }

  const columns: DataColumn<HarnessRevision>[] = [
    { key: "revision", title: "Версия", numeric: true, value: (item) => item.revision,
      render: (item) => <strong>{item.revision}{history?.current === item.revision ? <span className="harness-history-current"><StatusChip tone="success">текущая</StatusChip></span> : null}</strong> },
    { key: "saved_at", title: "Когда", value: revisionSpan },
    { key: "saved_by", title: "Кто", value: (item) => item.saved_by },
    { key: "origin", title: "Как появилась", value: revisionOrigin },
    { key: "size", title: "Размер", numeric: true, value: (item) => size(item.size) },
  ];
  const chosen = selected !== null && selected !== history?.current ? selected : null;

  return <div className="plan-drawer harness-history" role="dialog" aria-label="История версий">
    <div className="plan-drawer-scrim" onClick={onClose} />
    <div className="plan-drawer-body">
      <div className="manual-stock-head">
        <h2>История версий {index}</h2>
        <span className="harness-history-actions">
          {canEdit && <button className="primary small" disabled={chosen === null || busy} onClick={() => chosen !== null && void restore(chosen)}>
            {chosen === null ? "Выберите версию" : `Восстановить версию ${chosen}`}
          </button>}
          <button className="link-button" onClick={onClose}>Закрыть</button>
        </span>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!history && !error ? <div className="empty">Загрузка…</div>
        : history && <>
          {history.total > history.revisions.length && <p className="muted">Показаны последние {history.revisions.length} из {history.total}.</p>}
          <DataTable rows={history.revisions} columns={columns} rowKey={(item) => String(item.revision)} label="Версии чертежа"
            onRowClick={(item) => setSelected(item.revision)} rowClassName={(item) => (item.revision === selected ? "selected" : "")}
            rowTitle={(item) => item.revision === history.current ? "Текущий чертёж" : `Выбрать версию ${item.revision}`}
            emptyText="Версий ещё нет — чертёж появится в истории после первого сохранения" />
        </>}
    </div>
  </div>;
}
