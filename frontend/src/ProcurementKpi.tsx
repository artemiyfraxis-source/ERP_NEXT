import { useEffect, useState } from "react";
import { api, failureText } from "./api";
import { DataTable, type DataColumn } from "./DataTable";

/** Option `procurement.kpi`: indicators of the lines paid in a period, per buyer and in total (server-computed). */
type Average = { days: string | null; n: number };
export type KpiRow = {
  buyer: string;
  lines: number;
  paid_sum: string;
  received: number;
  on_time: { n: number; ok: number; percent: string | null };
  invoice_to_paid: Average;
  paid_to_received: Average;
  savings: { sum: string; n: number; excluded: number };
};
type KpiResult = { rows: KpiRow[]; total: KpiRow; buyers: string[] };

const money = (value: string) => `${Number(value).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`;
const average = (value: Average) => (value.days === null ? "—" : `${value.days.replace(".", ",")} (n=${value.n})`);
const number = (value: string | null) => (value === null ? null : Number(value));

const columns: DataColumn<KpiRow>[] = [
  { key: "buyer", title: "Закупщик", value: (row) => row.buyer, render: (row) => <strong>{row.buyer}</strong> },
  { key: "lines", title: "Позиций оплачено", numeric: true, value: (row) => row.lines },
  { key: "sum", title: "Сумма с НДС", numeric: true, value: (row) => Number(row.paid_sum), render: (row) => money(row.paid_sum) },
  {
    key: "ontime", title: "В срок к ожидаемой дате", numeric: true, value: (row) => number(row.on_time.percent),
    render: (row) => (row.on_time.percent === null ? "—" : `${row.on_time.percent.replace(".", ",")}% (${row.on_time.ok} из ${row.on_time.n})`),
  },
  { key: "invoice", title: "Счёт → оплата, дней", numeric: true, value: (row) => number(row.invoice_to_paid.days), render: (row) => average(row.invoice_to_paid) },
  { key: "received", title: "Оплата → приёмка, дней", numeric: true, value: (row) => number(row.paid_to_received.days), render: (row) => average(row.paid_to_received) },
  {
    key: "savings", title: "Экономия к расчёту", numeric: true, value: (row) => Number(row.savings.sum),
    render: (row) => <>{money(row.savings.sum)}<small>сравнено {row.savings.n}, не сравнить {row.savings.excluded}</small></>,
  },
];

function isoDay(date: Date) {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function ProcurementKpi() {
  const now = new Date();
  const [from, setFrom] = useState(isoDay(new Date(now.getFullYear(), now.getMonth(), 1)));
  const [to, setTo] = useState(isoDay(new Date(now.getFullYear(), now.getMonth() + 1, 0)));
  const [buyer, setBuyer] = useState("");
  const [result, setResult] = useState<KpiResult | null>(null);
  const [buyers, setBuyers] = useState<string[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!from || !to) return;
    let live = true;
    const query = new URLSearchParams({ from, to });
    if (buyer) query.set("buyer", buyer);
    setError("");
    api<KpiResult>(`/api/procurement/kpi?${query}`)
      .then((next) => { if (!live) return; setResult(next); setBuyers(next.buyers); })
      .catch((reason) => { if (live) setError(failureText(reason, "Показатели не загрузились")); });
    return () => { live = false; };
  }, [from, to, buyer]);

  const rows = result ? [...result.rows, result.total] : [];
  return (
    <section className="panel table-panel">
      <div className="panel-heading"><h2>Показатели закупщика</h2><span>оплаченные позиции за период</span></div>
      <div className="inline-action kpi-filters">
        <label>С<input type="date" aria-label="Период с" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
        <label>По<input type="date" aria-label="Период по" value={to} onChange={(event) => setTo(event.target.value)} /></label>
        <label>Закупщик
          <select aria-label="Закупщик" value={buyer} onChange={(event) => setBuyer(event.target.value)}>
            <option value="">Все</option>
            {buyers.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </label>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!error && !result && <div className="empty">Загружаю…</div>}
      {result && (
        <>
          <DataTable label="Показатели закупщиков" rows={rows} columns={columns} rowKey={(row) => row.buyer} compactBelow={1000} emptyText="За период оплат нет." />
          <p className="kpi-note">«В срок» — дата приёмки не позже ожидаемой даты (она может сдвигаться до поступления). Экономия — (цена расчёта с НДС − цена факт с НДС) × количество факт, только где совпадают единицы.</p>
        </>
      )}
    </section>
  );
}
