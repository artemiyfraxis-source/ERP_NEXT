import { useEffect, useMemo, useState } from "react";
import { failureText } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDay } from "./format";
import { searchRows } from "./searchText";
import { vnaBenchApi, type VnaBenchInstrumentTarget, type VnaBenchTarget } from "./vnaBenchApi";
import "./VnaTargetList.css";

const columns: DataColumn<VnaBenchInstrumentTarget>[] = [
  { key: "number", title: "Номер СИ", value: (row) => row.number, width: 112,
    render: (row) => <span className="vna-target-number">{row.number}</span> },
  { key: "name", title: "Наименование", value: (row) => row.name, grow: true, wrap: true },
  { key: "interfaces", title: "Разъёмы", value: (row) => row.interfaces_text || "—", wrap: true, minWidth: 200 },
  { key: "measured", title: "Последний замер", value: (row) => row.last_measured_at ?? "", width: 148, align: "right",
    render: (row) => <span className="vna-target-date">{row.last_measured_at ? formatDay(row.last_measured_at) : "не мерили"}</span> },
];

export function VnaTargetList({ sessionId, query, visible, onPick }: {
  sessionId: string | null; query: string; visible: boolean; onPick: (target: VnaBenchTarget) => void;
}) {
  const [rows, setRows] = useState<VnaBenchInstrumentTarget[] | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  // Stay mounted after picking: returning to search uses the same registry snapshot.
  useEffect(() => {
    let live = true;
    setRows(null); setError("");
    vnaBenchApi.targets().then((result) => { if (live) setRows(result.targets); })
      .catch((reason) => { if (live) setError(failureText(reason, "Не удалось загрузить СИ для замера")); });
    return () => { live = false; };
  }, [sessionId, attempt]);

  const filtered = useMemo(() => searchRows(rows ?? [],
    (row) => [row.number, row.name, row.interfaces_text, row.serial_number], query).sort((a, b) => {
      if (!a.last_measured_at && b.last_measured_at) return -1;
      if (a.last_measured_at && !b.last_measured_at) return 1;
      return (a.last_measured_at && b.last_measured_at ? Date.parse(a.last_measured_at) - Date.parse(b.last_measured_at) : 0)
        || a.number.localeCompare(b.number, "ru", { numeric: true });
    }), [rows, query]);

  if (!visible) return null;
  return <section className="vna-target-list" aria-label="СИ для замера">
    <h3>СИ для замера</h3>
    {error ? <>
      <div className="error-banner" role="alert">{error}</div>
      <button type="button" className="secondary small" onClick={() => setAttempt((value) => value + 1)}>Повторить</button>
    </> : rows === null ? <p className="empty" role="status">Загрузка…</p>
      : rows.length === 0 ? <p className="empty" role="status">Нет СИ с ВЧ-разъёмами — заведите СИ в реестре</p>
      : <DataTable rows={filtered} columns={columns} rowKey={(row) => row.instrument_id} hideSearch pageSize={50}
        label="Список СИ" emptyText="Нет СИ по запросу — измените поиск" onRowClick={(row) => onPick({
          kind: "instrument", instrument_id: row.instrument_id, number: row.number, name: row.name, serial_number: row.serial_number,
        })} rowTitle={(row) => `Выбрать ${row.number} · ${row.name}`} />}
  </section>;
}
