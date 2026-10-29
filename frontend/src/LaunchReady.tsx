import { useCallback, useEffect, useState, type FormEvent } from "react";
import { failureText } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { PageWindow } from "./PageWindow";
import { StatusChip } from "./StatusChip";
import { formatDay, formatNumber, plural } from "./format";
import { launchHours, launchReadyApi, type LaunchReadyList, type LaunchReadyOperation, type LaunchReadyRow } from "./launchReadyApi";
import "./launchReady.css";

/* «Готово к запуску» (владелец 29.10.2026, идея №3 «Запуск задания в один клик»): полоса над вкладками «Ресурсного
   планирования» → окно со списком КБ, у которых ЭТК выдана и комплект набран, а тираж не запущен. «Запустить» — одно
   окно подтверждения с этапами, нормами, тиражом и площадкой; запуск — тот же, что «Создать задание» с доски. Новых
   обязательных шагов нет, само ничего не запускается. */

const keyOf = (row: LaunchReadyRow) => row.techcard_id;
const hours = (value: number) => formatNumber(value, { digits: 1 });
const minutes = (seconds: number) => (seconds ? formatNumber(seconds / 60, { digits: 2 }) : "—");
const dash = <span className="muted">—</span>;

/** «3, 4, 5, 7» → «3–5, 7». */
export function spans(numbers: number[]): string {
  const sorted = [...numbers].sort((a, b) => a - b);
  const parts: string[] = [];
  for (let i = 0; i < sorted.length;) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j += 1;
    parts.push(j - i >= 2 ? `${sorted[i]}–${sorted[j]}` : sorted.slice(i, j + 1).join(", "));
    i = j + 1;
  }
  return parts.join(", ");
}

function siteText(row: LaunchReadyRow) {
  return <span title={row.site_note || undefined} className={row.site === "не распределено" ? "muted" : undefined}>{row.site}</span>;
}

function doneTitle(row: LaunchReadyRow) {
  return row.done_source ? `Сделано ${formatNumber(row.done)} шт — ${row.done_source}` : "Сделанного нет";
}

export function LaunchReady({ refresh = 0, onLaunched }: { refresh?: number; onLaunched: (text: string) => void }) {
  const [data, setData] = useState<LaunchReadyList | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState<LaunchReadyRow[] | null>(null);
  const [outcome, setOutcome] = useState<{ done: string; refused: string[] } | null>(null);

  const load = useCallback(() => launchReadyApi.list()
    .then((result) => { setData(result); setError(""); })
    .catch((reason) => setError(failureText(reason, "Список «Готово к запуску» не загрузился"))), []);

  useEffect(() => { void load(); }, [load, refresh]);

  if (!data && !error) return null;
  const items = data?.items ?? [];
  const waiting = data?.waiting_kit ?? 0;
  if (!error && !items.length && !waiting) return null;

  function launched(text: string, refused: string[]) {
    setConfirm(null);
    setOutcome({ done: text, refused });
    if (text) onLaunched(text);
    void load();
  }

  const columns: DataColumn<LaunchReadyRow>[] = [
    { key: "project", title: "Проект", value: (row) => row.project, render: (row) => <b>{row.project}</b> },
    { key: "index", title: "КБ", value: (row) => row.index },
    { key: "name", title: "Изделие", value: (row) => row.name, wrap: true, grow: true },
    { key: "customer", title: "Заказчик", value: (row) => row.customer, render: (row) => row.customer || dash, filter: true },
    { key: "deadline", title: "Срок проекта", value: (row) => row.deadline ?? "9999",
      render: (row) => row.deadline ? <span className="num">{formatDay(row.deadline)}</span> : dash },
    { key: "kit", title: "Комплект", value: (row) => row.kit, render: (row) => <StatusChip status={row.kit} />, filter: true },
    { key: "run", title: "Тираж КБ", numeric: true, value: (row) => row.run },
    { key: "launched", title: "Запущено", numeric: true, value: (row) => row.launched, render: (row) => row.launched ? formatNumber(row.launched) : dash },
    { key: "done", title: "Сделано", numeric: true, value: (row) => row.done,
      render: (row) => row.done ? <span title={doneTitle(row)}>{formatNumber(row.done)}</span> : dash, hint: "Наибольшее: сдано цехом, принято складом, отгружено" },
    { key: "quantity", title: "К запуску", numeric: true, value: (row) => row.quantity, render: (row) => <b>{formatNumber(row.quantity)}</b>,
      hint: "Тираж КБ − больше из «запущено» и «сделано»" },
    { key: "hours", title: "Норма, ч", numeric: true, value: (row) => launchHours(row.operations, row.quantity),
      render: (row) => hours(launchHours(row.operations, row.quantity)), hint: "Нормочасы ЭТК на тираж задания" },
    { key: "site", title: "Площадка", value: (row) => row.site, render: siteText, filter: true },
    { key: "act", title: "", value: () => "", align: "right",
      render: (row) => <button type="button" className="secondary small" onClick={(event) => { event.stopPropagation(); setOutcome(null); setConfirm([row]); }}>Запустить</button> },
  ];

  return <>
    <div className="lr-strip" role="status">
      {error ? <span className="warning-inline">{error}</span> : <>
        <span><b>Готово к запуску: {items.length ? `${items.length} КБ` : "нет"}</b></span>
        <span className="muted">ЭТК выдана, комплект набран{waiting ? ` · ждут комплект: ${waiting}` : ""}</span>
        {items.length > 0 && <button type="button" className="primary small" onClick={() => { setOutcome(null); setOpen(true); }}>Открыть список</button>}
      </>}
    </div>
    {open && <PageWindow label="Готово к запуску" width={1480} close={() => setOpen(false)}>
      <div className="panel lr-window">
        <div className="lr-head">
          <h2>Готово к запуску</h2>
          <span className="page-count">{items.length}</span>
          <span className="muted">ЭТК выдана, комплект набран, тираж не запущен. Назначение монтажникам — потом, на доске.</span>
          <button type="button" className="secondary" onClick={() => setOpen(false)}>Закрыть</button>
        </div>
        {outcome?.done && <div className="success-banner">{outcome.done}</div>}
        {outcome && outcome.refused.length > 0 && <div className="error-banner" role="alert">
          Не запущено: {outcome.refused.join("; ")}
        </div>}
        {data && data.no_run.length > 0 && <div className="warning-inline">Нет тиража КБ — задайте тираж в наборе проекта: {data.no_run.join(", ")}</div>}
        <DataTable
          rows={items}
          columns={columns}
          rowKey={keyOf}
          label="Готово к запуску"
          storageKey="production-launch-ready"
          exportName="Готово к запуску"
          emptyText="Запускать нечего: у всех КБ с выданной ЭТК и набранным комплектом тираж уже запущен."
          onRowClick={(row) => { setOutcome(null); setConfirm([row]); }}
          rowTitle={() => "Открыть запуск задания"}
          bulkActions={[{ label: "Запустить выбранные", run: (rows) => { setOutcome(null); setConfirm(rows); } }]}
        />
      </div>
    </PageWindow>}
    {confirm && <LaunchConfirm rows={confirm} close={() => setConfirm(null)} done={launched} />}
  </>;
}

function OperationsTable({ operations, quantity }: { operations: LaunchReadyOperation[]; quantity: number }) {
  return <div className="lr-ops-wrap">
    <table className="lr-ops">
      <thead><tr>
        <th className="num">№</th><th>Этап</th><th className="num">Шт на изделие</th>
        <th className="num">Норма, мин/шт</th><th className="num">Подготовка, мин</th><th className="num">Всего, ч</th>
      </tr></thead>
      <tbody>{operations.map((item) => <tr key={item.position}>
        <td className="num">{item.position}</td>
        <td>
          <div>{item.stage}</div>
          {item.etk_ops.length > 1
            ? <div className="lr-code">операции ЭТК {spans(item.etk_ops.map((part) => part.position))}</div>
            : item.code ? <div className="lr-code">{item.code}</div> : null}
        </td>
        <td className="num">{formatNumber(item.per_unit)}</td>
        <td className="num">{minutes(item.seconds_per_unit)}</td>
        <td className="num">{minutes(item.setup_seconds)}</td>
        <td className="num">{formatNumber(launchHours([item], quantity), { digits: 2 })}</td>
      </tr>)}</tbody>
      <tfoot><tr>
        <td /><td>Итого на {formatNumber(quantity)} шт</td><td /><td /><td />
        <td className="num"><b>{hours(launchHours(operations, quantity))}</b></td>
      </tr></tfoot>
    </table>
  </div>;
}

function LaunchConfirm({ rows, close, done }: {
  rows: LaunchReadyRow[]; close: () => void; done: (text: string, refused: string[]) => void;
}) {
  const [quantities, setQuantities] = useState<Record<string, string>>(() => Object.fromEntries(rows.map((row) => [keyOf(row), String(row.quantity)])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const single = rows.length === 1 ? rows[0] : null;

  const quantityOf = (row: LaunchReadyRow) => Number(quantities[keyOf(row)]);
  const invalid = rows.filter((row) => {
    const value = quantityOf(row);
    return !Number.isInteger(value) || value < 1 || value > row.remainder;
  });

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || invalid.length) return;
    setBusy(true);
    setError("");
    try {
      const result = await launchReadyApi.launch(rows.map((row) => ({
        techcard_id: row.techcard_id, techcard_version: row.techcard_version, launched: row.launched, quantity: quantityOf(row),
      })));
      const names = new Map(rows.map((row) => [row.techcard_id, `${row.project} ${row.index}`]));
      const text = result.created.length
        ? `Запущено ${result.created.length} ${plural(result.created.length, ["задание", "задания", "заданий"])}: `
          + result.created.map((task) => `${task.number} (${task.project} ${task.item}, ${formatNumber(task.quantity)} шт)`).join(", ")
        : "";
      done(text, result.refused.map((item) => `${names.get(item.techcard_id) ?? ""} — ${item.reason}`));
    } catch (reason) {
      setError(failureText(reason, "Задание не запущено"));
      setBusy(false);
    }
  }

  const label = single ? "Запуск задания" : "Запуск заданий";
  const total = rows.reduce((sum, row) => sum + launchHours(row.operations, quantityOf(row) || 0), 0);
  const field = (row: LaunchReadyRow) => <input
    type="number" inputMode="numeric" min={1} max={row.remainder} step={1} className="lr-qty-input"
    aria-label={`Тираж задания ${row.project} ${row.index}`} value={quantities[keyOf(row)]}
    onChange={(event) => setQuantities((current) => ({ ...current, [keyOf(row)]: event.target.value }))}
  />;

  return <PageWindow label={label} width={single ? 960 : 1120} close={() => { if (!busy) close(); }}>
    <form className="panel lr-confirm" onSubmit={submit}>
      <div className="lr-head">
        <h2>{single ? `${single.project} ${single.index} · ${single.name}` : `${label}: ${rows.length} КБ`}</h2>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {single ? <>
        <dl className="lr-facts">
          <div><dt>Тираж задания, шт</dt><dd>{field(single)}<span className="muted"> из {formatNumber(single.remainder)}
            {single.launched ? ` (тираж КБ ${formatNumber(single.run)}, запущено ${formatNumber(single.launched)})` : ""}</span></dd></div>
          <div><dt>Площадка</dt><dd>{siteText(single)}{single.site_note && <div className="lr-code">{single.site_note}</div>}</dd></div>
          <div><dt>Срок задания</dt><dd className="num">{single.task_deadline ? formatDay(single.task_deadline) : "—"}
            {single.deadline && <div className="lr-code">срок проекта {formatDay(single.deadline)}</div>}</dd></div>
          <div><dt>Комплект</dt><dd><StatusChip status={single.kit} /></dd></div>
        </dl>
        {single.warnings.map((text) => <div key={text} className="warning-inline">{text}</div>)}
        <OperationsTable operations={single.operations} quantity={quantityOf(single) || 0} />
      </> : <div className="lr-ops-wrap">
        <table className="lr-ops">
          <thead><tr>
            <th>КБ</th><th>Изделие</th><th className="num">Тираж задания, шт</th><th className="num">Этапов</th>
            <th className="num">Норма, ч</th><th>Площадка</th><th>Срок задания</th>
          </tr></thead>
          <tbody>{rows.map((row) => <tr key={keyOf(row)}>
            <td><b>{row.project} {row.index}</b></td>
            <td>{row.name}</td>
            <td className="num">{field(row)} <span className="muted">из {formatNumber(row.remainder)}</span></td>
            <td className="num">{row.operations.length}</td>
            <td className="num">{hours(launchHours(row.operations, quantityOf(row) || 0))}</td>
            <td>{siteText(row)}</td>
            <td className="num">{row.task_deadline ? formatDay(row.task_deadline) : "—"}</td>
          </tr>)}</tbody>
          <tfoot><tr><td /><td>Итого</td><td /><td /><td className="num"><b>{hours(total)}</b></td><td /><td /></tr></tfoot>
        </table>
      </div>}
      {invalid.length > 0 && <div className="warning-inline">
        Тираж задания — целое число от 1 до остатка тиража КБ{single ? ` (${formatNumber(single.remainder)})` : `: ${invalid.map((row) => `${row.project} ${row.index}`).join(", ")}`}
      </div>}
      <div className="form-actions">
        <button type="button" className="secondary" disabled={busy} onClick={close}>Отмена</button>
        <button type="submit" className="primary" disabled={busy || invalid.length > 0}>
          {busy ? "Запускаем…" : single ? `Запустить задание · ${formatNumber(quantityOf(single) || 0)} шт` : `Запустить ${rows.length} ${plural(rows.length, ["задание", "задания", "заданий"])}`}
        </button>
      </div>
    </form>
  </PageWindow>;
}
