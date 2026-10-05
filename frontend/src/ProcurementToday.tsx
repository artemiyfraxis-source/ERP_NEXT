import { useEffect, useState } from "react";
import { api, failureText } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDate } from "./format";

/** Option `procurement.today`: the buyer's board. Fields in priority order hold project cards (one project may sit
 *  in several fields); a card opens the project's open positions with their stage and field. */
export type TodayItem = {
  id: string | null;
  name: string;
  article: string | null;
  supplier: string | null;
  order_number: string | null;
  date: string | null;
  days: number | null;
  hint: string;
  project?: string;
  status?: string;
};
export type TodayCard = { project: string; count: number; days: number | null; date: string | null; suppliers: string[] };
export type TodaySection = { key: string; title: string; count: number; items: TodayItem[]; projects?: TodayCard[] };
export type TodayLine = {
  id: string; name: string; article: string | null; supplier: string | null; order_number: string | null;
  status: string; expected: string | null; quantity: string | null; unit: string | null; field: string | null;
};
type Board = { sections: TodaySection[] };

const URGENT = new Set(["overdue", "arriving"]);

const lineColumns: DataColumn<TodayLine>[] = [
  { key: "name", title: "Позиция", wrap: true, value: (row) => row.name, render: (row) => <>{row.name}{row.article && <small>{row.article}</small>}</> },
  { key: "status", title: "Этап", value: (row) => row.status, render: (row) => <span className="today-stage">{row.status}</span> },
  { key: "field", title: "На доске", value: (row) => row.field, render: (row) => row.field || "—" },
  { key: "supplier", title: "Поставщик", value: (row) => row.supplier, render: (row) => row.supplier || "—" },
  { key: "qty", title: "Кол-во", numeric: true, value: (row) => row.quantity, render: (row) => (row.quantity ? `${Number(row.quantity).toLocaleString("ru-RU")} ${row.unit ?? ""}` : "—") },
  { key: "order", title: "Заказ №", value: (row) => row.order_number, render: (row) => row.order_number || "—" },
  { key: "expected", title: "Ожидаем", value: (row) => row.expected, render: (row) => (row.expected ? formatDate(row.expected) : "—") },
];

function daysText(section: string, days: number | null) {
  if (days === null) return "";
  if (section === "overdue") return `просрочка ${days} дн.`;
  if (section === "arriving") return days === 0 ? "сегодня" : "завтра";
  // A fresh card (0 days) carries no age: «0 дн.» reads like a deadline.
  return days > 0 ? `ждёт ${days} дн.` : "";
}

export function ProcurementToday({ onOpenQueues }: { onOpenQueues?: () => void }) {
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<{ project: string; field: string } | null>(null);
  const [onlyField, setOnlyField] = useState(true);
  // Positions of the opened card, fetched on click (the board itself carries only the cards).
  const [drill, setDrill] = useState<TodayLine[] | null>(null);
  const [drillError, setDrillError] = useState("");

  useEffect(() => {
    let live = true;
    api<Board>("/api/procurement/today")
      .then((result) => { if (live) setBoard(result); })
      .catch((reason) => { if (live) setError(failureText(reason, "«Сегодня» не загрузилось")); });
    return () => { live = false; };
  }, []);

  const project = open?.project ?? null;
  useEffect(() => {
    if (project === null) return;
    let live = true;
    setDrill(null);
    setDrillError("");
    api<{ lines: TodayLine[] }>(`/api/procurement/today/project?project=${encodeURIComponent(project)}`)
      .then((result) => { if (live) setDrill(result.lines); })
      .catch((reason) => { if (live) setDrillError(failureText(reason, "Позиции проекта не загрузились")); });
    return () => { live = false; };
  }, [project]);

  const sections = board?.sections ?? null;
  const rowsOfProject = drill ?? [];
  const shown = open && onlyField ? rowsOfProject.filter((row) => row.field === open.field) : rowsOfProject;

  return (
    <section className="page">
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!error && sections === null && <div className="empty">Загружаю…</div>}
      {sections !== null && !sections.length && <div className="empty">На сегодня всё сделано</div>}
      {sections !== null && sections.length > 0 && (
        <div className="today-board" role="list" aria-label="Доска «Сегодня»">
          {sections.map((section) => (
            <section className={`today-field${URGENT.has(section.key) ? " urgent" : ""}`} key={section.key} role="listitem" aria-label={section.title}>
              <header><h2>{section.title}</h2><span className="today-count">{section.count}</span></header>
              {section.key === "available" ? (
                <button type="button" className="today-card" onClick={() => onOpenQueues?.()}>
                  <strong>{section.items[0]?.name}</strong>
                  <small>{section.items[0]?.hint}</small>
                </button>
              ) : (section.projects ?? []).map((card) => {
                const active = open?.project === card.project && open.field === section.title;
                return (
                  <button
                    type="button"
                    key={card.project}
                    className={`today-card${active ? " active" : ""}`}
                    aria-pressed={active}
                    onClick={() => { setOpen(active ? null : { project: card.project, field: section.title }); setOnlyField(true); }}
                  >
                    <span className="today-card-head"><strong>{card.project}</strong><span>{card.count} поз.</span></span>
                    {daysText(section.key, card.days) && <span className={`today-days${section.key === "overdue" ? " late" : ""}`}>{daysText(section.key, card.days)}</span>}
                    {card.suppliers.length > 0 && <small>{card.suppliers.slice(0, 3).join(", ")}{card.suppliers.length > 3 ? ` +${card.suppliers.length - 3}` : ""}</small>}
                  </button>
                );
              })}
            </section>
          ))}
        </div>
      )}
      {open && (
        <section className="panel table-panel today-drill" aria-label={`Проект ${open.project}`}>
          <div className="panel-heading">
            <h2>{open.project}</h2>
            <span>{open.field}</span>
            <span className="today-drill-actions">
              <button type="button" className={`secondary small${onlyField ? " active" : ""}`} aria-pressed={onlyField} onClick={() => setOnlyField(true)}>В этом поле</button>
              <button type="button" className={`secondary small${!onlyField ? " active" : ""}`} aria-pressed={!onlyField} onClick={() => setOnlyField(false)}>Все позиции проекта{drill ? ` · ${drill.length}` : ""}</button>
              {onOpenQueues && <button type="button" className="secondary small" onClick={() => onOpenQueues()}>В «Очереди»</button>}
              <button type="button" className="link-button" onClick={() => setOpen(null)}>Закрыть</button>
            </span>
          </div>
          {drillError && <div className="error-banner" role="alert">{drillError}</div>}
          {!drillError && drill === null && <div className="empty">Загружаю…</div>}
          {drill !== null && <DataTable label={`Позиции проекта ${open.project}`} rows={shown} columns={lineColumns} rowKey={(row) => row.id} compactBelow={1000} />}
        </section>
      )}
    </section>
  );
}
