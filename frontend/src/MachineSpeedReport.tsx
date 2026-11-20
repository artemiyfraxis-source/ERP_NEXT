import { useEffect, useState } from "react";
import { ApiError, failureText } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { EMPTY, formatDay, formatMoment, formatNumber } from "./format";
import {
  machineSpeedApi, type MachineRef, type MachineRun, type MachineSpeedReport as Report, type MachineSpeedRow,
  type MachineSpeedScope, type MachineStop,
} from "./machineSpeedApi";
import { PageWindow } from "./PageWindow";
import "./normFact.css";

/** Раздел «Станки» в «Норме и факте» (владелец, 20.11.2026): скорость станков по истории работы (ОБ.019 пишет её, пока на связи)
 *  против циклограммы БД.ОП, наладка, простои и предложение новой скорости. «Принять» пишет ячейки циклограммы. */

const DAYS = 30;
const ETK_NOTE_FALLBACK = "Выпущенные ЭТК с этой операцией получат новую ревизию автоматически";

function isoDay(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

export function lastMachineDays(today = new Date()): { since: string; until: string } {
  return { since: isoDay(new Date(today.getFullYear(), today.getMonth(), today.getDate() - (DAYS - 1))), until: isoDay(today) };
}

/** Секунды: «6,27 с», «2 мин», «1 ч 05 мин». */
export function secondsText(value: number | string | null | undefined, digits = 0): string {
  if (value == null || value === "") return EMPTY;
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) return EMPTY;
  if (seconds < 60) return `${formatNumber(seconds, { digits })} с`;
  if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    const rest = Math.round(seconds % 60);
    return rest ? `${minutes} мин ${String(rest).padStart(2, "0")} с` : `${minutes} мин`;
  }
  const hours = Math.floor(seconds / 3600);
  return `${hours} ч ${String(Math.round((seconds % 3600) / 60)).padStart(2, "0")} мин`;
}

function stopTime(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} с`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} мин`;
  return `${formatNumber(seconds / 3600, { digits: 1 })} ч`;
}

export function stopsText(stops: MachineStop[]): string {
  return stops.filter((stop) => stop.seconds > 0).map((stop) => `${stop.label} ${stopTime(stop.seconds)}`).join(" · ");
}

export function setupText(row: MachineSpeedRow): string {
  const { wire, length_mm, strip_front_mm, strip_back_mm, marking } = row.setup;
  const parts = [wire || "провод не указан", length_mm ? `${formatNumber(length_mm)} мм` : "", `зачистка ${strip_front_mm || 0}/${strip_back_mm || 0} мм`, marking];
  return parts.filter(Boolean).join(" · ");
}

/** Факт / норма: до 1,10 — в норме, до 1,25 — предупреждение, дальше — медленно. */
export function ratioTone(ratio: number): "tone-ok" | "tone-warn" | "tone-danger" {
  return ratio <= 1.1 ? "tone-ok" : ratio <= 1.25 ? "tone-warn" : "tone-danger";
}

function Ratio({ value }: { value: string | null }) {
  if (value == null) return <span className="nf-muted">{EMPTY}</span>;
  const ratio = Number(value);
  return <b className={`nf-num ${ratioTone(ratio)}`}>{formatNumber(ratio, { digits: 2, minDigits: 2 })}</b>;
}

function perPiece(value: string | null) {
  return value == null ? <span className="nf-muted">{EMPTY}</span> : <span className="nf-num">{formatNumber(value, { digits: 2, minDigits: 2 })}</span>;
}

function setupRatio(row: MachineSpeedRow) {
  if (row.setup_fact_seconds == null) return <span className="nf-muted">{EMPTY}</span>;
  return <span className="nf-num">{secondsText(row.setup_fact_seconds)} / {row.setup_norm_seconds == null ? EMPTY : secondsText(row.setup_norm_seconds)}</span>;
}

function scopeOf(row: MachineSpeedRow, report: Report): MachineSpeedScope {
  return { equipment_id: row.equipment_id, setup_key: row.setup_key, since: report.since, until: report.until };
}

export function MachineSpeedReport() {
  const [period, setPeriod] = useState(() => lastMachineDays());
  const [equipmentId, setEquipmentId] = useState("");
  const [machines, setMachines] = useState<MachineRef[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [forbidden, setForbidden] = useState(false);
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const [drill, setDrill] = useState<MachineSpeedRow | null>(null);
  const [accepting, setAccepting] = useState<MachineSpeedRow | null>(null);

  useEffect(() => {
    if (!period.since || !period.until) return;
    let live = true;
    setError("");
    setReport(null);
    machineSpeedApi.report(period.since, period.until, equipmentId)
      .then((loaded) => {
        if (!live) return;
        setReport(loaded);
        if (!equipmentId) setMachines(loaded.machines);
      })
      .catch((reason) => {
        if (!live) return;
        if (reason instanceof ApiError && reason.status === 403) setForbidden(true);
        else setError(failureText(reason, "История станков не загружена"));
      });
    return () => { live = false; };
  }, [period.since, period.until, equipmentId, reload]);

  const label = `${formatDay(period.since)} – ${formatDay(period.until)}`;
  if (forbidden) return null;  // нет права на «Станки» — раздела нет, без красной плашки

  const columns: DataColumn<MachineSpeedRow>[] = [
    { key: "machine", title: "Станок", width: 70, filter: true, value: (row) => row.inventory_number, render: (row) => <b>{row.inventory_number}</b> },
    { key: "setup", title: "Настройка", width: 150, maxWidth: 220, wrap: true, value: (row) => setupText(row), render: (row) => setupText(row) },
    { key: "op", title: "Операция БД.ОП", wrap: true, width: 120, maxWidth: 150, value: (row) => `${row.operation.name} ${row.operation.code}`,
      render: (row) => row.operation.code
        ? <><b>{row.operation.name}</b><small className="nf-code">{row.operation.code}</small></>
        : <span className="nf-muted">не найдена</span> },
    { key: "runs", title: "Прогонов", numeric: true, width: 70, value: (row) => row.runs },
    { key: "pieces", title: "Штук", numeric: true, width: 60, value: (row) => row.pieces, render: (row) => formatNumber(row.pieces) },
    { key: "running", title: "В работе", numeric: true, width: 80, value: (row) => row.running_seconds, render: (row) => secondsText(row.running_seconds) },
    { key: "fact", title: "Факт, с/шт", numeric: true, width: 70, value: (row) => Number(row.fact_seconds_per_piece ?? -1), render: (row) => perPiece(row.fact_seconds_per_piece) },
    { key: "norm", title: "Норма, с/шт (по циклограмме)", numeric: true, width: 90, hint: "По циклограмме БД.ОП для этой длины", value: (row) => Number(row.norm_seconds_per_piece ?? -1), render: (row) => perPiece(row.norm_seconds_per_piece) },
    { key: "ratio", title: "Факт / норма", numeric: true, width: 80, hint: "До 1,10 — в норме, до 1,25 — внимание, дальше — медленно", value: (row) => Number(row.ratio ?? -1), render: (row) => <Ratio value={row.ratio} /> },
    { key: "setupfact", title: "Наладка факт / норма", numeric: true, width: 120, value: (row) => Number(row.setup_fact_seconds ?? -1), render: setupRatio },
    { key: "stops", title: "Простои", wrap: true, width: 100, maxWidth: 140, value: (row) => stopsText(row.stops), render: (row) => stopsText(row.stops) || <span className="nf-muted">{EMPTY}</span> },
    { key: "suggestion", title: "Предложение", wrap: true, grow: true, minWidth: 200, maxWidth: 360, value: (row) => row.suggestion?.cells.map((cell) => cell.label).join(" ") ?? "",
      render: (row) => row.suggestion
        ? <span className="nf-suggest">{row.suggestion.cells.map((cell) => `${cell.label.split(",")[0]} ${formatNumber(cell.old)} → ${formatNumber(cell.new)}`).join("; ")}{" "}
          {report?.can_accept && <button type="button" className="secondary small" onClick={(event) => { event.stopPropagation(); setNotice(""); setAccepting(row); }}>Принять</button>}</span>
        : <span className="nf-muted">{EMPTY}</span> },
  ];

  return <section className="panel nf-report" aria-label="Станки">
    <div className="nf-head">
      <h2>Станки</h2>
      <div className="nf-period" role="group" aria-label="Период станков">
        <label>Период: с<input type="date" aria-label="Станки: начало периода" value={period.since} max={period.until} onChange={(event) => event.target.value && setPeriod({ ...period, since: event.target.value })} /></label>
        <label>по<input type="date" aria-label="Станки: конец периода" value={period.until} min={period.since} onChange={(event) => event.target.value && setPeriod({ ...period, until: event.target.value })} /></label>
        <label>Станок
          <select aria-label="Станок" value={equipmentId} onChange={(event) => setEquipmentId(event.target.value)}>
            <option value="">Все станки</option>
            {machines.map((machine) => <option key={machine.equipment_id} value={machine.equipment_id}>{machine.inventory_number}{machine.name ? ` · ${machine.name}` : ""}</option>)}
          </select>
        </label>
      </div>
      <span className="nf-muted">Скорость по истории работы станка против циклограммы БД.ОП · {label}</span>
    </div>
    {notice && <div className="success-banner" role="status">{notice}</div>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!report && !error && <div className="empty">Загрузка…</div>}
    {report && !report.rows.length && <div className="empty">Нет истории станков за период — она пишется, когда ОБ.019 на связи.</div>}
    {report && report.rows.length > 0 && <DataTable rows={report.rows} columns={columns} label="Станки: скорость" hideSearch phoneCards={false}
      rowKey={(row) => `${row.equipment_id}:${row.setup_key}:${row.operation.code}`} exportName={`Станки — скорость ${label}`}
      onRowClick={(row) => setDrill(row)} rowTitle={(row) => `Прогоны: ${row.inventory_number}, ${setupText(row)}`} />}
    {drill && report && <RunsWindow row={drill} scope={scopeOf(drill, report)} label={label} close={() => setDrill(null)} />}
    {accepting?.suggestion && report && <AcceptWindow row={accepting} scope={scopeOf(accepting, report)} close={() => setAccepting(null)}
      refresh={() => setReload((value) => value + 1)} done={(text) => { setNotice(text); setAccepting(null); setReload((value) => value + 1); }} />}
  </section>;
}

function RunsWindow({ row, scope, label, close }: { row: MachineSpeedRow; scope: MachineSpeedScope; label: string; close: () => void }) {
  const [runs, setRuns] = useState<MachineRun[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    machineSpeedApi.runs(scope)
      .then((loaded) => { if (live) setRuns(loaded.runs); })
      .catch((reason) => { if (live) setError(failureText(reason, "Прогоны не загружены")); });
    return () => { live = false; };
  }, [scope.equipment_id, scope.setup_key, scope.since, scope.until]);  // eslint-disable-line react-hooks/exhaustive-deps
  const columns: DataColumn<MachineRun>[] = [
    { key: "start", title: "Начало", width: 110, value: (run) => run.started_at, render: (run) => <span className="nf-num">{formatMoment(run.started_at)}</span> },
    { key: "end", title: "Конец", width: 110, value: (run) => run.ended_at, render: (run) => <span className="nf-num">{formatMoment(run.ended_at)}</span> },
    { key: "kind", title: "Режим", width: 110, value: (run) => run.kind ?? "", render: (run) => run.kind === "cutting" ? "Резка" : run.kind === "manual" ? "Ручной" : EMPTY },
    { key: "pieces", title: "Штук", numeric: true, width: 80, value: (run) => run.pieces, render: (run) => formatNumber(run.pieces) },
    { key: "running", title: "В работе", numeric: true, width: 100, value: (run) => run.running_seconds, render: (run) => secondsText(run.running_seconds) },
    { key: "fact", title: "Факт, с/шт", numeric: true, width: 100, value: (run) => Number(run.fact_seconds_per_piece ?? -1), render: (run) => perPiece(run.fact_seconds_per_piece) },
    { key: "setup", title: "Наладка факт", numeric: true, width: 120, value: (run) => Number(run.setup_fact_seconds ?? -1), render: (run) => secondsText(run.setup_fact_seconds) },
  ];
  return <PageWindow label={`Прогоны: ${row.inventory_number}`} width={860} dismissable close={close}>
    <section className="panel nf-report" aria-label={`Прогоны: ${row.inventory_number}`}>
      <div className="nf-head">
        <h2>{row.inventory_number}<span className="page-count">{runs?.length ?? 0}</span></h2>
        <span className="nf-muted">{setupText(row)} · прогоны за {label}</span>
        <button type="button" className="secondary small nf-close" onClick={close}>Закрыть</button>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!runs && !error && <div className="empty">Загрузка…</div>}
      {runs && !runs.length && <div className="empty">Прогонов за период нет.</div>}
      {runs && runs.length > 0 && <DataTable rows={runs} columns={columns} rowKey={(run) => run.started_at} label="Прогоны" hideSearch phoneCards={false}
        exportName={`Станки — прогоны ${row.inventory_number} ${label}`} pageSize={200} />}
    </section>
  </PageWindow>;
}

function AcceptWindow({ row, scope, close, refresh, done }: {
  row: MachineSpeedRow; scope: MachineSpeedScope; close: () => void; refresh: () => void; done: (text: string) => void;
}) {
  const suggestion = row.suggestion!;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const evidence = suggestion.evidence;

  async function accept() {
    setBusy(true);
    setError("");
    try {
      await machineSpeedApi.accept(suggestion, scope);
      done(`Записано в циклограмму «${row.operation.name || row.operation.code}»: ${suggestion.cells.length} яч.`);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) setStale(true);
      setError(failureText(reason, "Циклограмма не записана"));
      setBusy(false);
    }
  }

  return <PageWindow label={`Принять скорость: ${row.inventory_number}`} width={720} close={close}>
    <section className="panel nf-report" aria-label={`Принять скорость: ${row.inventory_number}`}>
      <div className="nf-head">
        <h2>Принять скорость · {row.inventory_number}</h2>
        <span className="nf-muted">{row.operation.name} · {setupText(row)}</span>
      </div>
      <table className="ms-cells" aria-label="Ячейки циклограммы">
        <thead><tr><th>Ячейка циклограммы</th><th className="num">Сейчас</th><th className="num">Станет</th></tr></thead>
        <tbody>{suggestion.cells.map((cell) => <tr key={cell.field}>
          <td>{cell.label}</td><td className="num nf-muted">{formatNumber(cell.old)}</td><td className="num"><b>{formatNumber(cell.new)}</b></td>
        </tr>)}</tbody>
      </table>
      <dl className="nf-summary" aria-label="Основание">
        <div><dt>Штук</dt><dd>{formatNumber(evidence.pieces)}</dd></div>
        <div><dt>Прогонов</dt><dd>{evidence.runs}</dd></div>
        <div><dt>Длины, мм</dt><dd>{evidence.lengths.map((length) => formatNumber(length)).join(", ") || EMPTY}</dd></div>
        <div><dt>Подгонка</dt><dd>{evidence.fit || EMPTY}</dd></div>
        <div><dt>Период</dt><dd>{formatDay(evidence.since)} – {formatDay(evidence.until)}</dd></div>
      </dl>
      {suggestion.note && <div className="warning-inline">{suggestion.note}</div>}
      <div className="warning-inline">{suggestion.etk_note || ETK_NOTE_FALLBACK}</div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="ms-actions">
        {stale
          ? <button type="button" className="primary" onClick={() => { refresh(); close(); }}>Обновить отчёт</button>
          : <button type="button" className="primary" disabled={busy} onClick={() => void accept()}>{busy ? "Записываю…" : "Записать в циклограмму"}</button>}
        <button type="button" className="secondary" onClick={close}>Отмена</button>
      </div>
    </section>
  </PageWindow>;
}
