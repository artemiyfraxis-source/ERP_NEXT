import { useEffect, useState, type ReactNode } from "react";
import { api, failureText } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { openPrintDocument } from "./print";
import "./procurementKpi.css";

/** Option `procurement.kpi`: the buyer's indicators of the lines paid in a period (server-computed, the sheet of
 * Roller_Buyer «KPI»): cards, supplier rating, best savings and the per-buyer table. `null` = «нет данных». */
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
type Sheet = {
  money: {
    spend: string; baseline_spend: string; savings: string; savings_pct: string | null; compared_lines: number;
    kp_savings: string | null; kp_savings_pct: string | null; kp_compared_lines: number;
    surplus_spend: string | null; vs_last_purchase: string | null; compared_with_last: number; delivery_spend: string | null;
  };
  delivery: {
    due: number; on_time: number; on_time_pct: string | null; avg_delay_days: string | null;
    avg_request_to_order_days: string | null; avg_request_to_delivery_days: string | null;
  };
  quality: {
    lines: number; received_lines: number | null; lines_with_defect: number | null; defect_pct: string | null;
    defect_amount: string | null; defect_without_quantity: number | null; returned_amount: string | null;
  };
  competition: {
    decisions: number | null; avg_offers: string | null; with_non_key_pct: string | null; override_pct: string | null; top1_share: string | null;
    top3_share: string | null; new_suppliers: number | null;
  };
};
export type SupplierRating = {
  supplier: string; is_key: boolean | null; asked: number | null; answered: number | null; response_pct: string | null;
  order_lines: number; spend: string; on_time_pct: string | null; defect_pct: string | null; defect_amount?: string | null;
  price_vs_best_pct: string | null;
};
export type Highlight = {
  line_id: string; order_number: string | null; order_date: string | null; supplier: string | null; item: string;
  quantity: string; unit: string; unit_price: string; baseline_unit_price: string; savings: string;
};
type KpiResult = { rows: KpiRow[]; total: KpiRow; buyers: string[]; kpi: Sheet; suppliers: SupplierRating[]; highlights: Highlight[] };

const NO_DATA = "нет данных";
const group = (value: number, digits: number) => value.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const money = (value: string) => `${group(Number(value), 2)} ₽`;
const pct = (value: string) => `${group(Number(value), 1)} %`;
const days = (value: string) => `${group(Number(value), 1)} дн.`;
const count = (value: string) => group(Number(value), 1);
const signedPct = (value: string) => `${Number(value) > 0 ? "+" : ""}${pct(value)}`;
const average = (value: Average) => (value.days === null ? "—" : `${value.days.replace(".", ",")} (n=${value.n})`);
const number = (value: string | null) => (value === null ? null : Number(value));
const dayMonth = (iso: string | null) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}` : "—");
const text = (value: string | null, format: (value: string) => string) => (value === null ? NO_DATA : format(value));
const cell = (value: string | null, format: (value: string) => string): ReactNode =>
  value === null ? <span className="kpi-nodata" title="Нет данных за период">{NO_DATA}</span> : format(value);

const buyerColumns: DataColumn<KpiRow>[] = [
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

const supplierColumns: DataColumn<SupplierRating>[] = [
  {
    key: "supplier", title: "Поставщик", value: (row) => row.supplier,
    render: (row) => <><strong>{row.supplier}</strong>{row.is_key && <small title="Ключевой поставщик в справочнике"> ключевой</small>}</>,
  },
  { key: "asked", title: "Спрошен", numeric: true, value: (row) => row.asked, render: (row) => row.asked ?? cell(null, String) },
  { key: "answered", title: "Ответил", numeric: true, value: (row) => row.answered, render: (row) => row.answered ?? cell(null, String) },
  { key: "response", title: "Отклик", numeric: true, hint: "Доля запросов КП, отправленных в периоде, на которые поставщик прислал КП.", value: (row) => number(row.response_pct), render: (row) => cell(row.response_pct, pct) },
  { key: "lines", title: "Строк заказа", numeric: true, value: (row) => row.order_lines },
  { key: "spend", title: "Сумма с НДС", numeric: true, value: (row) => Number(row.spend), render: (row) => money(row.spend) },
  { key: "ontime", title: "В срок", numeric: true, hint: "Доля его строк, пришедших к ожидаемой дате.", value: (row) => number(row.on_time_pct), render: (row) => cell(row.on_time_pct, pct) },
  {
    key: "defect", title: "Брак", numeric: true, hint: "Доля его принятых строк, где входной контроль нашёл брак; ниже — брак в рублях (кол-во брака × цена оплаты с НДС).",
    value: (row) => number(row.defect_pct),
    render: (row) => <>{cell(row.defect_pct, pct)}{row.defect_amount ? <small>{money(row.defect_amount)}</small> : null}</>,
  },
  {
    key: "best", title: "Цена к лучшей", numeric: true, hint: "Насколько его цена в среднем выше самого дешёвого КП по тем же строкам.",
    value: (row) => number(row.price_vs_best_pct), render: (row) => cell(row.price_vs_best_pct, signedPct),
  },
];

const highlightColumns: DataColumn<Highlight>[] = [
  { key: "order", title: "Заказ", value: (row) => `${dayMonth(row.order_date)} ${row.order_number ?? ""}`, render: (row) => `${dayMonth(row.order_date)} · ${row.order_number ?? "—"}` },
  { key: "item", title: "Позиция", value: (row) => row.item, wrap: true, grow: true },
  { key: "supplier", title: "Поставщик", value: (row) => row.supplier ?? "—" },
  { key: "qty", title: "Кол-во", numeric: true, value: (row) => Number(row.quantity), render: (row) => `${group(Number(row.quantity), 0)} ${row.unit}` },
  { key: "base", title: "Цена расчёта", numeric: true, value: (row) => Number(row.baseline_unit_price), render: (row) => money(row.baseline_unit_price) },
  { key: "price", title: "Цена оплаты", numeric: true, value: (row) => Number(row.unit_price), render: (row) => money(row.unit_price) },
  { key: "saved", title: "Экономия", numeric: true, value: (row) => Number(row.savings), render: (row) => <strong>{money(row.savings)}</strong> },
];

type Preset = "month" | "last" | "quarter" | "year";
const PRESETS: Array<[Preset, string]> = [["month", "Этот месяц"], ["last", "Прошлый месяц"], ["quarter", "Квартал"], ["year", "Год"]];

function isoDay(date: Date) {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Roller `presetRange`: the current month / quarter / year run up to today, «прошлый месяц» is the whole month. */
export function presetRange(preset: Preset, now = new Date()): { from: string; to: string } {
  const year = now.getFullYear();
  const month = now.getMonth();
  if (preset === "last") return { from: isoDay(new Date(year, month - 1, 1)), to: isoDay(new Date(year, month, 0)) };
  const first = preset === "month" ? month : preset === "quarter" ? Math.floor(month / 3) * 3 : 0;
  return { from: isoDay(new Date(year, first, 1)), to: isoDay(now) };
}

/** «Брак, ₽»: сумма по строкам с количеством брака; строки с браком без количества (приняты до 24.10.2026) названы,
 *  а не считаются нулём. Ни у одной нет количества — «нет данных». */
export function defectText(quality: Sheet["quality"]) {
  const missing = quality.defect_without_quantity ?? 0;
  const note = missing > 0 ? `без количества: ${missing} ${missing === 1 ? "строка" : missing < 5 ? "строки" : "строк"}` : "";
  if (quality.defect_amount === null) return note ? `${NO_DATA} (${note})` : NO_DATA;
  return note ? `${money(quality.defect_amount)} (${note})` : money(quality.defect_amount);
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return <section className="panel kpi-card"><h3>{title}</h3><dl>{children}</dl></section>;
}

function Metric({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <div title={hint}><dt>{label}</dt><dd>{children}</dd></div>;
}

function printSheet(result: KpiResult, from: string, to: string, buyer: string) {
  const { kpi } = result;
  const best = result.highlights.map((row) => [`${dayMonth(row.order_date)} ${row.order_number ?? ""}`.trim(), row.item, money(row.baseline_unit_price), money(row.unit_price), money(row.savings)]);
  openPrintDocument("Итоги работы закупщика", [{
    title: "Итоги работы закупщика за период",
    subtitle: `${dayMonth(from)} – ${dayMonth(to)}${buyer ? `, закупщик ${buyer}` : ""}`,
    blocks: [
      { table: { columns: ["Показатель", "Значение"], rows: [
        ["Экономия к расчёту", kpi.money.savings_pct === null ? NO_DATA : `${money(kpi.money.savings)} (${pct(kpi.money.savings_pct)})`],
        ["Экономия к КП", kpi.money.kp_savings_pct === null || kpi.money.kp_savings === null ? NO_DATA : `${money(kpi.money.kp_savings)} (${pct(kpi.money.kp_savings_pct)})`],
        ["Оплачено за период", money(kpi.money.spend)],
        ["Поставок в срок", text(kpi.delivery.on_time_pct, pct)],
        ["Строк с браком", text(kpi.quality.defect_pct, pct)],
      ] } },
      { heading: "Топ-5 экономии" },
      best.length
        ? { table: { columns: ["Заказ", "Позиция", "Цена расчёта", "Цена оплаты", "Экономия"], rows: best } }
        : { text: "Нет оплаченных позиций дешевле расчёта." },
      { heading: "Сроки и качество" },
      { table: { columns: ["Показатель", "Значение"], rows: [
        ["Средняя просрочка", text(kpi.delivery.avg_delay_days, days)],
        ["Заявка → заказ", text(kpi.delivery.avg_request_to_order_days, days)],
        ["Заявка → приёмка", text(kpi.delivery.avg_request_to_delivery_days, days)],
        ["Брак, ₽", defectText(kpi.quality)],
        ["Возвраты, ₽", text(kpi.quality.returned_amount, money)],
      ] } },
    ],
    sign: `Сформировано ${new Date().toLocaleString("ru-RU")}`,
  }]);
}

export function ProcurementKpi() {
  const [preset, setPreset] = useState<Preset | null>("month");
  const [range, setRange] = useState(() => presetRange("month"));
  const [buyer, setBuyer] = useState("");
  const [result, setResult] = useState<KpiResult | null>(null);
  const [buyers, setBuyers] = useState<string[]>([]);
  const [error, setError] = useState("");
  const { from, to } = range;

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

  const choose = (next: Preset) => { setPreset(next); setRange(presetRange(next)); };
  const edit = (patch: Partial<typeof range>) => { setPreset(null); setRange((current) => ({ ...current, ...patch })); };
  const k = result?.kpi;
  return (
    <div className="kpi-page">
      <section className="panel table-panel">
        <div className="panel-heading"><h2>Показатели закупщика</h2><span>оплаченные позиции за {dayMonth(from)} – {dayMonth(to)}</span></div>
        <div className="kpi-filters">
          <div className="seg-tabs" role="group" aria-label="Период">
            {PRESETS.map(([key, label]) => (
              <button key={key} type="button" className={preset === key ? "on" : undefined} aria-pressed={preset === key} onClick={() => choose(key)}>{label}</button>
            ))}
          </div>
          <label>С<input type="date" aria-label="Период с" value={from} onChange={(event) => edit({ from: event.target.value })} /></label>
          <label>По<input type="date" aria-label="Период по" value={to} onChange={(event) => edit({ to: event.target.value })} /></label>
          <label>Закупщик
            <select aria-label="Закупщик" value={buyer} onChange={(event) => setBuyer(event.target.value)}>
              <option value="">Все</option>
              {buyers.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </label>
          <button type="button" className="secondary small kpi-print" disabled={!result} onClick={() => result && printSheet(result, from, to, buyer)}>Итоги на печать</button>
        </div>
        {error && <div className="error-banner" role="alert">{error}</div>}
        {!error && !result && <div className="empty">Загружаю…</div>}
      </section>

      {result && k && (
        <>
          <div className="kpi-cards">
            <Card title="Деньги">
              <Metric label="Экономия к расчёту" hint="Цена расчёта с НДС минус цена оплаты с НДС, на потребность. Минус — заплатили дороже расчёта.">
                {k.money.savings_pct === null ? cell(null, String) : <>{money(k.money.savings)} <small>({pct(k.money.savings_pct)})</small></>}
              </Metric>
              <Metric label="Экономия к КП" hint="Самое дешёвое КП ключевого поставщика на день решения минус цена оплаты с НДС, на потребность. Только строки с решением по запросу КП в тех же единицах.">
                {k.money.kp_savings_pct === null || k.money.kp_savings === null ? cell(null, String) : <>{money(k.money.kp_savings)} <small>({pct(k.money.kp_savings_pct)}, строк {k.money.kp_compared_lines})</small></>}
              </Metric>
              <Metric label="Оплачено с НДС" hint="Сумма оплаченных в периоде позиций.">{money(k.money.spend)}</Metric>
              <Metric label="Сверх потребности" hint="Заплачено за количество сверх заявки — минимальные партии, целые катушки. Это запас, а не экономия.">{cell(k.money.surplus_spend, money)}</Metric>
              <Metric label="К прошлой закупке" hint="Сколько стоила бы потребность по цене прошлой оплаты той же позиции (артикул или название, те же единицы) минус заплаченное. Плюс — купили дешевле прошлого раза.">
                {k.money.vs_last_purchase === null ? cell(null, String) : <>{money(k.money.vs_last_purchase)} <small>(строк {k.money.compared_with_last})</small></>}
              </Metric>
              <Metric label="Доставка по заказам" hint="Доставка, внесённая в заказы этих позиций, один раз на заказ. В «Оплачено» и в доли поставщиков не входит.">{cell(k.money.delivery_spend, money)}</Metric>
            </Card>
            <Card title="Сроки">
              <Metric label="В срок к ожидаемой дате" hint="Доля позиций, пришедших не позже ожидаемой даты. Просроченные без приёмки считаются опоздавшими; ожидаемую дату можно сдвигать до поступления.">
                {cell(k.delivery.on_time_pct, pct)}{k.delivery.due > 0 && <small> ({k.delivery.on_time} из {k.delivery.due})</small>}
              </Metric>
              <Metric label="Средняя просрочка" hint="Среднее опоздание опоздавших позиций, дней от ожидаемой даты.">{cell(k.delivery.avg_delay_days, days)}</Metric>
              <Metric label="Заявка → заказ" hint="Сколько в среднем проходит от даты заявки до заказа — скорость работы закупки.">{cell(k.delivery.avg_request_to_order_days, days)}</Metric>
              <Metric label="Заявка → приёмка" hint="От даты заявки до фактической даты приёмки.">{cell(k.delivery.avg_request_to_delivery_days, days)}</Metric>
            </Card>
            <Card title="Качество">
              <Metric label="Строк с браком" hint="Доля принятых строк, где входной контроль нашёл «Механическое повреждение / брак». Строки, ещё не прошедшие приёмку, не считаются.">
                {cell(k.quality.defect_pct, pct)}{k.quality.received_lines !== null && <small> ({k.quality.lines_with_defect} из {k.quality.received_lines})</small>}
              </Metric>
              <Metric label="Брак, ₽" hint="Кол-во брака, внесённое при приёмке, × цена оплаты с НДС. Строки с браком, принятые без количества, в сумму не входят и названы отдельно.">
                {cell(k.quality.defect_amount, money)}{(k.quality.defect_without_quantity ?? 0) > 0 && <small> (без количества: {k.quality.defect_without_quantity})</small>}
              </Metric>
              <Metric label="Возвраты, ₽" hint="Возвращённое поставщику количество по журналу приёмки × цена оплаты с НДС.">{cell(k.quality.returned_amount, money)}</Metric>
            </Card>
            <Card title="Конкуренция">
              <Metric label="Решений по КП" hint="Решений о выборе поставщика, принятых в периоде по запросам закупщика.">{k.competition.decisions === null ? cell(null, String) : k.competition.decisions}</Metric>
              <Metric label="Среднее КП на решение" hint="Сколько КП с ценой было в среднем, когда выбирали поставщика.">{cell(k.competition.avg_offers, count)}</Metric>
              <Metric label="С не ключевым" hint="Доля решений, где среди КП был не ключевой поставщик.">{cell(k.competition.with_non_key_pct, pct)}</Metric>
              <Metric label="Обход гейта" hint="Доля решений, принятых без нужного числа КП.">{cell(k.competition.override_pct, pct)}</Metric>
              <Metric label="Топ-1 поставщик" hint="Доля оплат у самого крупного поставщика. Высокая — зависимость от одного.">{cell(k.competition.top1_share, pct)}</Metric>
              <Metric label="Топ-3 поставщика" hint="Доля оплат у трёх самых крупных поставщиков.">{cell(k.competition.top3_share, pct)}</Metric>
              <Metric label="Новых поставщиков" hint="Поставщики этого периода, у которых это первая оплата за всё время (склад не считается).">{k.competition.new_suppliers === null ? cell(null, String) : k.competition.new_suppliers}</Metric>
            </Card>
          </div>

          <section className="panel table-panel">
            <div className="panel-heading"><h2>Рейтинг поставщиков</h2><span>по оплатам периода</span></div>
            <DataTable label="Рейтинг поставщиков" rows={result.suppliers} columns={supplierColumns} rowKey={(row) => row.supplier} compactBelow={1000} emptyText="За период оплат нет — выберите период шире." />
          </section>

          <section className="panel table-panel">
            <div className="panel-heading"><h2>Лучшая экономия</h2><span>пять позиций, оплаченных дешевле расчёта</span></div>
            <DataTable label="Лучшая экономия" rows={result.highlights} columns={highlightColumns} rowKey={(row) => row.line_id} compactBelow={1000} emptyText="Нет позиций дешевле расчёта: экономия считается, когда у строки есть цена расчёта и оплаты в одних единицах." />
          </section>

          <section className="panel table-panel">
            <div className="panel-heading"><h2>По закупщикам</h2><span>оплаченные позиции за период</span></div>
            <DataTable label="Показатели закупщиков" rows={[...result.rows, result.total]} columns={buyerColumns} rowKey={(row) => row.buyer} compactBelow={1000} emptyText="За период оплат нет." />
            <p className="kpi-note">«В срок» — дата приёмки не позже ожидаемой даты (она может сдвигаться до поступления). Экономия — (цена расчёта с НДС − цена оплаты с НДС) × количество, но не больше потребности заявки, только где совпадают единицы.</p>
          </section>
        </>
      )}
    </div>
  );
}
