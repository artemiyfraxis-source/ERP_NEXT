import { useEffect, useMemo, useState } from "react";
import { failureText, kitsetsApi, type ProjectTrackEntry } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDate } from "./format";

/* «Трек» карточки проекта: всё, что по проекту создано в разделах ERP, по времени — от карточки и техкарт до
   отгрузки (GET /api/techcards/kitsets/{проект}/track). Только чтение. Записи без даты — событие ещё не
   наступило или дата не записана — сервер ставит в конец, здесь у них «—». Грузится при открытии вкладки. */

type TrackRow = ProjectTrackEntry & { n: number };

const DAY_ONLY = /^(\d{4}-\d{2}-\d{2})T00:00(:00(\.0+)?)?$/;

/** Момент записи по-русски. Сервер ставит полночь, когда известен только день, — тогда без «00:00». */
export function trackWhen(at: string | null): string {
  if (!at) return "—";
  const dayOnly = DAY_ONLY.exec(at);
  return formatDate(dayOnly ? dayOnly[1] : at) || "—";
}

/** Порядок трека (аудит 10.10, ПР-9): по дню; внутри дня сначала записи со временем, по времени, затем записи, у которых
 *  известен только день (приёмка, выпуск ГП, отгрузка пишут день), — полночь не ставит их раньше событий того же дня;
 *  без даты — в конце. Равные — в порядке сервера. */
export function trackOrder<T extends { at: string | null }>(entries: T[]): T[] {
  const key = (at: string | null) => (at ? `${at.slice(0, 10)}|${DAY_ONLY.test(at) ? "1" : `0${at.slice(10)}`}` : "~");
  return entries.map((entry, n) => ({ entry, n, key: key(entry.at) }))
    .sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : a.n - b.n))
    .map((item) => item.entry);
}

const COLUMNS: DataColumn<TrackRow>[] = [
  // Значение — ISO: по нему сортируется и ищется; пустые сортировка всегда ставит вниз.
  { key: "at", title: "Когда", width: 160, value: (row) => row.at ?? "", render: (row) => trackWhen(row.at) },
  { key: "module", title: "Раздел", width: 150, filter: true, value: (row) => row.module },
  { key: "kind", title: "Событие", width: 360, wrap: true, value: (row) => row.kind },
  { key: "ref", title: "Номер", width: 280, wrap: true, value: (row) => row.ref },
  { key: "status", title: "Статус", width: 200, filter: true, value: (row) => row.status },
  { key: "item_index", title: "КБ", width: 80, value: (row) => row.item_index },
];

export function ProjectTrack({ project }: { project: string }) {
  const [entries, setEntries] = useState<ProjectTrackEntry[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    setEntries(null); setError("");
    kitsetsApi.track(project)
      .then((result) => { if (live) setEntries(result.entries); })
      .catch((reason) => { if (live) setError(failureText(reason, "Трек проекта не загрузился")); });
    return () => { live = false; };
  }, [project]);

  const rows = useMemo<TrackRow[]>(() => trackOrder(entries || []).map((entry, n) => ({ ...entry, n })), [entries]);

  if (error) return <div className="kit-pane"><div className="error-banner" role="alert">{error}</div></div>;
  if (!entries) return <div className="kit-pane"><div className="empty">Загрузка…</div></div>;
  if (!entries.length) return <div className="kit-pane"><div className="empty">По проекту в разделах ещё ничего нет</div></div>;
  return <div className="kit-pane">
    <DataTable
      rows={rows}
      columns={COLUMNS}
      rowKey={(row) => String(row.n)}
      storageKey="kit-track"
      label="Трек проекта"
      exportName={`Трек ${project}`}
    />
  </div>;
}
