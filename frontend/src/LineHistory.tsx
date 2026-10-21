import { useEffect, useState, type ReactNode } from "react";
import { api, failureText } from "./api";
import { formatDate } from "./format";
import { useStageOption } from "./StageOptions";

/** Option `procurement.history`: the life of one procurement line as a vertical timeline in a side panel. The server
 *  rebuilds it from stored facts; an event without a stored date comes last and says so. */
export type HistoryEvent = { at: string | null; time: boolean; title: string; details: string; who: string | null; source: string };
type History = { line: { id: string; legacy_id: string; name: string; status: string }; events: HistoryEvent[] };

export function LineHistoryPanel({ lineId, title, onClose }: { lineId: string; title: string; onClose: () => void }) {
  const [history, setHistory] = useState<History | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    setHistory(null);
    setError("");
    api<History>(`/api/procurement/${encodeURIComponent(lineId)}/history`)
      .then((result) => { if (live) setHistory(result); })
      .catch((reason) => { if (live) setError(failureText(reason, "История не загрузилась")); });
    return () => { live = false; };
  }, [lineId]);

  return (
    <aside className="history-panel" role="dialog" aria-label={`История ${title}`}>
      <header>
        <h2>История · {title}</h2>
        <button type="button" className="link-button" onClick={onClose}>Закрыть</button>
      </header>
      {history && <small className="history-sub">{history.line.name} · этап «{history.line.status}»</small>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!error && history === null && <div className="empty">Загружаю…</div>}
      {history && !history.events.length && <div className="empty">Событий нет</div>}
      {history && history.events.length > 0 && (
        <ol className="history-timeline" aria-label="События строки">
          {history.events.map((event, index) => (
            <li key={index} className={event.at ? undefined : "undated"}>
              <time>{event.at ? formatDate(event.time ? event.at : event.at.slice(0, 10)) : "без даты"}</time>
              <div>
                <strong>{event.title}</strong>
                {event.details && <span>{event.details}</span>}
                {(event.who || event.source) && <small>{[event.who, event.source].filter(Boolean).join(" · ")}</small>}
              </div>
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}

/** «История» of a row: `open(id, title)` from the row's action, `node` rendered anywhere (the panel is fixed). */
export function useLineHistory(): { enabled: boolean; open: (lineId: string, title: string) => void; node: ReactNode } {
  const enabled = useStageOption("procurement.history");
  const [shown, setShown] = useState<{ lineId: string; title: string } | null>(null);
  const node = enabled && shown ? <LineHistoryPanel lineId={shown.lineId} title={shown.title} onClose={() => setShown(null)} /> : null;
  return { enabled, open: (lineId, title) => setShown({ lineId, title }), node };
}
