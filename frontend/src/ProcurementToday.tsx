import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import { api, failureText } from "./api";
import { formatDate } from "./format";
import { projectLabel, useInternalProjects } from "./internalProjects";
import { openPlace } from "./navigation";

/** Option `procurement.today`: Roller_Buyer's «Сегодня» (TodayPage.tsx) — what needs the buyer's hand now, section by
 *  section in the order of doing. Every row opens the place where the next step is taken (in-app, without a reload);
 *  a card of a project or an order opens in place to show its positions. Refreshed every minute. */
export type TodayTone = "danger" | "warning" | "accent" | "neutral" | "success";
/** `?section=procurement` params of the place: `{tab: "orders", order: "PO-1"}` (CONTRACTS.md deep links). */
export type TodayLink = Record<string, string>;
export type TodayItem = {
  title: string;
  detail: string;
  link: TodayLink | null;
  /** Days late / waiting; null when not about time. */
  days: number | null;
  /** A card: its positions (the first ones) and how many there are. */
  lines?: TodayItem[];
  lines_count?: number;
  project?: { key: string; kind: string; value: string };
};
export type TodaySection = {
  key: string; title: string; action: string; tone: TodayTone; count: number; positions?: number; items: TodayItem[];
};
export type TodayDto = { today: string; sections: TodaySection[]; calm: boolean };

const SHOWN = 8; // RB: eight items, then «и ещё N»
const REFRESH_MS = 60_000;

function hrefOf(link: TodayLink): string {
  const params = new URLSearchParams({ section: "procurement", ...link });
  return `?${params.toString()}`;
}

function follow(event: MouseEvent, link: TodayLink) {
  // A plain click stays in the app; Ctrl/middle click opens the address in a new tab as any link does.
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  openPlace("procurement", link);
}

function titleOf(item: TodayItem): string {
  return item.project?.kind === "project" ? projectLabel(item.project.value) : item.title;
}

function Days({ days }: { days: number | null }) {
  return days !== null ? <span className="today-days">{days} дн</span> : null;
}

function Row({ item }: { item: TodayItem }) {
  const body = (
    <>
      <span className="today-title">{titleOf(item)}</span>
      <span className="today-detail">{item.detail}</span>
      <Days days={item.days} />
    </>
  );
  if (!item.link) return <span className="today-row">{body}</span>;
  const link = item.link;
  return <a className="today-row" href={hrefOf(link)} onClick={(event) => follow(event, link)}>{body}</a>;
}

/** A project (order) card: opens in place to show the positions waiting in this section. */
function GroupRow({ item }: { item: TodayItem }) {
  const [open, setOpen] = useState(false);
  const lines = item.lines ?? [];
  const rest = (item.lines_count ?? lines.length) - lines.length;
  return (
    <>
      <button type="button" className={open ? "today-row today-group open" : "today-row today-group"} aria-expanded={open}
        onClick={() => setOpen(!open)}>
        <span className="today-title"><span className="today-caret" aria-hidden="true">{open ? "▾" : "▸"}</span> {titleOf(item)}</span>
        <span className="today-detail">{item.detail}</span>
        <Days days={item.days} />
      </button>
      {open && (
        <ul className="today-lines" aria-label={`Позиции: ${titleOf(item)}`}>
          {lines.map((line, index) => <li key={index}><Row item={line} /></li>)}
          {item.link && (
            <li className="today-lines-all">
              <a href={hrefOf(item.link)} onClick={(event) => follow(event, item.link!)}>
                {rest > 0 ? `и ещё ${rest} — открыть все →` : "открыть все →"}
              </a>
            </li>
          )}
        </ul>
      )}
    </>
  );
}

function SectionCard({ section }: { section: TodaySection }) {
  return (
    <section className={`today-card tone-${section.tone}`} aria-label={section.title}>
      <header>
        <h3>
          {section.title} <span className="today-count">{section.count}</span>
          {section.positions !== undefined && <span className="muted today-positions"> {section.positions} поз.</span>}
        </h3>
        <div className="muted">{section.action}</div>
      </header>
      <ul>
        {section.items.slice(0, SHOWN).map((item, index) => (
          <li key={`${section.key}-${item.title}-${index}`}>{item.lines ? <GroupRow item={item} /> : <Row item={item} />}</li>
        ))}
      </ul>
      {section.count > SHOWN && <div className="muted today-more">и ещё {section.count - SHOWN}</div>}
    </section>
  );
}

export function ProcurementToday({ ordersOn = false }: { ordersOn?: boolean } = {}) {
  useInternalProjects(); // option projects.internal: project cards say «ВП-001 · Название»
  const [data, setData] = useState<TodayDto | null>(null);
  const [error, setError] = useState("");
  const live = useRef(true);

  const load = useCallback(() => {
    api<TodayDto>("/api/procurement/today")
      .then((next) => { if (live.current) { setData(next); setError(""); } })
      .catch((reason) => { if (live.current) setError(failureText(reason, "«Сегодня» не загрузилось")); });
  }, []);

  useEffect(() => {
    live.current = true;
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => { live.current = false; window.clearInterval(timer); };
  }, [load]);

  const total = data?.sections.reduce((sum, section) => sum + section.count, 0) ?? 0;
  return (
    <section className="page today-page">
      <div className="today-head">
        <span className="muted">{data ? `${formatDate(data.today)} · ${total ? `дел: ${total}` : "дел нет"}` : ""}</span>
        <span className="spacer" />
        {ordersOn && (
          <a className="secondary" href={hrefOf({ tab: "orders" })} onClick={(event) => follow(event, { tab: "orders" })}>Счета и поставки</a>
        )}
        <button type="button" className="primary" onClick={() => openPlace("newrequest")}>Новая заявка</button>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!error && !data && <div className="empty">Загружаю…</div>}
      {data?.calm && (
        <div className="panel today-calm">
          <h3>Всё спокойно</h3>
          <p className="muted">Просрочек нет, КП запрошены, счета оплачены. Новые заявки появятся здесь сами.</p>
        </div>
      )}
      {data && data.sections.length > 0 && (
        <div className="today-grid">
          {data.sections.map((section) => <SectionCard key={section.key} section={section} />)}
        </div>
      )}
    </section>
  );
}
