import { useEffect, useState } from "react";
import { api, failureText } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDate } from "./format";

/** Option `procurement.today`: the buyer's prioritized to-do list, computed by the server on read. */
export type TodayItem = {
  id: string | null;
  name: string;
  article: string | null;
  supplier: string | null;
  order_number: string | null;
  date: string | null;
  days: number | null;
  hint: string;
};
export type TodaySection = { key: string; title: string; count: number; items: TodayItem[] };

const columns: DataColumn<TodayItem>[] = [
  { key: "name", title: "Позиция", wrap: true, value: (item) => item.name, render: (item) => <>{item.name}{item.article && <small>{item.article}</small>}</> },
  { key: "supplier", title: "Поставщик", value: (item) => item.supplier, render: (item) => item.supplier || "—" },
  { key: "order", title: "Заказ №", value: (item) => item.order_number, render: (item) => item.order_number || "—" },
  { key: "date", title: "Дата", value: (item) => item.date, render: (item) => (item.date ? formatDate(item.date) : "—") },
  { key: "days", title: "Дней", numeric: true, value: (item) => item.days, render: (item) => (item.days === null ? "—" : item.days) },
  { key: "hint", title: "Что делать", wrap: true, value: (item) => item.hint },
];

export function ProcurementToday({ onOpenQueues }: { onOpenQueues?: () => void }) {
  const [sections, setSections] = useState<TodaySection[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    api<{ sections: TodaySection[] }>("/api/procurement/today")
      .then((result) => { if (live) setSections(result.sections); })
      .catch((reason) => { if (live) setError(failureText(reason, "«Сегодня» не загрузилось")); });
    return () => { live = false; };
  }, []);

  return (
    <section className="page">
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!error && sections === null && <div className="empty">Загружаю…</div>}
      {sections !== null && !sections.length && <div className="empty">На сегодня всё сделано</div>}
      {sections?.map((section) => (
        <section className="panel table-panel" key={section.key}>
          <div className="panel-heading"><h2>{section.title}</h2><span>{section.count}</span></div>
          <DataTable
            label={section.title}
            rows={section.items}
            columns={columns}
            rowKey={(item) => item.id ?? section.key}
            compactBelow={1000}
            onRowClick={onOpenQueues ? () => onOpenQueues() : undefined}
            rowTitle={() => "Открыть в «Очередях»"}
          />
        </section>
      ))}
    </section>
  );
}
