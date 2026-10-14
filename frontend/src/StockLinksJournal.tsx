import { useEffect, useState } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { failureText, type TechOperationTab } from "./api";
import { fieldTitle as defaultFieldTitle } from "./referenceLabels";
import { EMPTY, formatDateTime } from "./format";
import { STOCK_LINK_EVENT_LABELS, stockLinksApi, type StockLinkEntry, type StockLinkEvent } from "./stockLinksApi";

/* Журнал «Склад → база данных»: в карточке записи — её строки, на странице «Базы данных» — весь список. */

const BASE_LABELS: Record<string, string> = {
  ob: "Оборудование", op: "Операции", ter: "Терминалы", coax: "Коаксиальные разъёмы", prov: "Провода и кабели", mat: "Материалы",
};
const EVENT_TONES: Record<StockLinkEvent, string> = { linked: "tone-info", created: "tone-ok", updated: "tone-warn" };

export function StockLinksJournal({ recordId, fieldTitle = defaultFieldTitle }: {
  /** Запись базы; без неё — весь журнал. */
  recordId?: string;
  fieldTitle?: (field: string) => string;
}) {
  const [entries, setEntries] = useState<StockLinkEntry[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setEntries(null); setError("");
    stockLinksApi.list(recordId, controller.signal)
      .then((result) => setEntries(result.entries))
      .catch((reason) => { if (!controller.signal.aborted) { setEntries([]); setError(failureText(reason, "Журнал склада не загрузился")); } });
    return () => controller.abort();
  }, [recordId]);

  const changesText = (entry: StockLinkEntry) => entry.changes
    .map((change) => `${fieldTitle(change.field)}: ${change.old || EMPTY} → ${change.new || EMPTY}`).join("; ");
  const columns: DataColumn<StockLinkEntry>[] = [
    { key: "when", title: "Когда", value: (entry) => entry.occurred_at, render: (entry) => <span className="tnum">{formatDateTime(entry.occurred_at)}</span> },
    { key: "actor", title: "Кто", value: (entry) => entry.actor_name },
    { key: "event", title: "Событие", filter: true, value: (entry) => STOCK_LINK_EVENT_LABELS[entry.event] ?? entry.event,
      render: (entry) => <span className={EVENT_TONES[entry.event]}>{STOCK_LINK_EVENT_LABELS[entry.event] ?? entry.event}</span> },
    { key: "grn", title: "ГРН", value: (entry) => entry.grn, render: (entry) => <strong className="tnum">{entry.grn}</strong> },
    { key: "package", title: "Пакет", hidden: !recordId, value: (entry) => entry.package || EMPTY },
    { key: "warehouse", title: "Склад", filter: true, value: (entry) => entry.warehouse },
    { key: "article", title: "Артикул", wrap: true, maxWidth: 180, value: (entry) => entry.article },
    // Весь журнал на 16" не влезает с пакетом и наименованием партии: они в «Колонках», ключ партии — ГРН и артикул.
    { key: "name", title: "Наименование", wrap: true, hidden: !recordId, value: (entry) => entry.name },
    ...(recordId ? [] : [
      { key: "base", title: "База", filter: true, value: (entry: StockLinkEntry) => BASE_LABELS[entry.record_tab] ?? entry.record_tab },
      { key: "record", title: "Запись", wrap: true, maxWidth: 220, value: (entry: StockLinkEntry) => entry.record_text,
        render: (entry: StockLinkEntry) => entry.record_id ? entry.record_text
          : <span className="muted" title="Запись удалена перезагрузкой базы; в журнале остался её текст">{entry.record_text} (удалена)</span> },
    ]),
    { key: "changes", title: "Что поменялось", wrap: true, grow: true, minWidth: 200, value: changesText,
      render: (entry) => entry.changes.length ? changesText(entry) : <span className="muted">запись не менялась</span> },
  ];

  if (entries === null) return <div className="empty">Загрузка…</div>;
  return <>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <DataTable
      label={recordId ? "Со склада" : "Журнал «Склад → база данных»"}
      rows={entries}
      columns={columns}
      rowKey={(entry) => entry.id}
      storageKey={recordId ? "db:stock-links:card" : "db:stock-links"}
      exportName="Склад - база данных"
      hideSearch={Boolean(recordId)}
      emptyText={error ? "Журнал не загрузился." : recordId
        ? "Позицию склада к этой записи ещё не привязывали."
        : "Событий нет: строка появится, когда позицию склада привяжут к записи базы или сделают из неё запись."}
    />
  </>;
}
