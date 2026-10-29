import { useEffect, useState, type MouseEvent } from "react";
import { failureText } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { EMPTY, formatDay, formatMoment, formatNumber } from "./format";
import { openPlace } from "./navigation";
import { PageWindow } from "./PageWindow";
import { normFactApi, type NormFactFact, type NormFactOperator, type NormFactReport, type NormFactTemplate } from "./normFactApi";
import "./normFact.css";

/** «Норма и факт» в «Ресурсном планировании» (владелец, 29.10.2026): сколько операции занимают на самом деле против
 *  нормы ЭТК — по шаблонам операций и по монтажникам, с предложениями (норма занижена / завышена, коэффициент
 *  скорости для «Компетенций»). Только показывает: нормы и компетенции правятся там, куда ведут ссылки. */

function isoDay(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

/** Последние `days` дней по сегодня включительно. */
export function lastDays(days: number, today = new Date()): { from: string; to: string } {
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - (days - 1));
  return { from: isoDay(start), to: isoDay(today) };
}

const PRESETS: Array<[number, string]> = [[30, "30 дней"], [60, "60 дней"], [90, "90 дней"]];

/** Отношение факт / норма: «1,42». */
export function ratioText(value: number | null): string {
  return value == null ? EMPTY : formatNumber(value, { digits: 2, minDigits: 2 });
}

/** Время на штуку: «8,4 с», «45 с», «1 мин 05 с». */
export function pieceTime(seconds: number | null): string {
  if (seconds == null) return EMPTY;
  if (seconds < 10) return `${formatNumber(seconds, { digits: 1 })} с`;
  const whole = Math.round(seconds);
  if (whole < 60) return `${whole} с`;
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  return rest ? `${minutes} мин ${String(rest).padStart(2, "0")} с` : `${minutes} мин`;
}

/** Часы: «12,5 ч», меньше часа — «35 мин». */
export function hoursText(seconds: number): string {
  if (!seconds) return EMPTY;
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} мин`;
  return `${formatNumber(seconds / 3600, { digits: 1 })} ч`;
}

type Tone = "ok" | "slow" | "fast" | "";

function toneOf(value: number | null, slow: number, fast: number): Tone {
  if (value == null) return "";
  return value > slow ? "slow" : value < fast ? "fast" : "ok";
}

const TONE_WORD: Record<Exclude<Tone, "">, string> = { ok: "в норме", slow: "медленнее нормы", fast: "быстрее нормы" };

function Ratio({ value, slow, fast }: { value: number | null; slow: number; fast: number }) {
  const tone = toneOf(value, slow, fast);
  if (!tone) return <span className="nf-muted">{EMPTY}</span>;
  return <span className={`nf-ratio nf-${tone}`} title={TONE_WORD[tone]}>{ratioText(value)}</span>;
}

function Spread({ low, high }: { low: number | null; high: number | null }) {
  if (low == null || high == null) return <span className="nf-muted">{EMPTY}</span>;
  return <span className="nf-num">{ratioText(low)} – {ratioText(high)}</span>;
}

function FactsCount({ facts, noNorm, enough }: { facts: number; noNorm: number; enough: boolean }) {
  return <span className="nf-num">
    {facts}
    {!enough && <span className="nf-few" title="Фактов меньше, чем нужно для предложения"> · мало данных</span>}
    {noNorm > 0 && <span className="nf-muted" title="Фактов без нормы: в медиану не идут"> · без нормы {noNorm}</span>}
  </span>;
}

function go(event: MouseEvent, section: string, params: Record<string, string>) {
  event.stopPropagation();
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
  event.preventDefault();
  openPlace(section, params);
}

function href(params: Record<string, string>): string {
  return `?${new URLSearchParams(params).toString()}`;
}

type Drill = { kind: "template"; row: NormFactTemplate } | { kind: "operator"; row: NormFactOperator };

export function ProductionNormFact() {
  const [period, setPeriod] = useState(() => lastDays(60));
  const [report, setReport] = useState<NormFactReport | null>(null);
  const [error, setError] = useState("");
  const [drill, setDrill] = useState<Drill | null>(null);

  useEffect(() => {
    if (!period.from || !period.to) return;
    let live = true;
    setError("");
    setReport(null);
    normFactApi.report(period.from, period.to)
      .then((loaded) => { if (live) setReport(loaded); })
      .catch((reason) => { if (live) setError(failureText(reason, "Отчёт «Норма и факт» не загружен")); });
    return () => { live = false; };
  }, [period.from, period.to]);

  const slow = report?.thresholds.slow ?? 1.25;
  const fast = report?.thresholds.fast ?? 0.8;
  const minFacts = report?.thresholds.min_facts ?? 5;
  const label = `${formatDay(period.from)} – ${formatDay(period.to)}`;
  const counted = report?.facts.filter((fact) => fact.ratio != null) ?? [];
  const actual = counted.reduce((sum, fact) => sum + fact.actual_seconds, 0);
  const norm = counted.reduce((sum, fact) => sum + fact.norm_seconds, 0);

  const templateColumns: DataColumn<NormFactTemplate>[] = [
    { key: "stage", title: "Операция", width: 260, value: (row) => `${row.stage} ${row.operation_code}`,
      render: (row) => <><b>{row.stage || row.template}</b>{row.operation_code && <small className="nf-code">{row.operation_code}</small>}</> },
    { key: "facts", title: "Фактов", numeric: true, width: 150, value: (row) => row.facts, render: (row) => <FactsCount facts={row.facts} noNorm={row.no_norm} enough={row.enough} /> },
    { key: "median", title: "Факт / норма", numeric: true, width: 110, hint: "Медиана: 1,00 — по норме, 1,40 — на 40 % дольше", value: (row) => row.median ?? -1, render: (row) => <Ratio value={row.median} slow={slow} fast={fast} /> },
    { key: "spread", title: "Разброс", numeric: true, width: 120, hint: "Средняя половина фактов (от нижней до верхней четверти)", value: (row) => row.high != null && row.low != null ? row.high - row.low : -1, render: (row) => <Spread low={row.low} high={row.high} /> },
    { key: "actual", title: "Часов факт", numeric: true, width: 100, value: (row) => row.actual_seconds, render: (row) => hoursText(row.actual_seconds) },
    { key: "norm", title: "Часов по норме", numeric: true, width: 120, value: (row) => row.norm_seconds, render: (row) => hoursText(row.norm_seconds) },
    { key: "unit", title: "Штучное по норме", numeric: true, width: 130, value: (row) => row.seconds_per_unit ?? -1, render: (row) => pieceTime(row.seconds_per_unit) },
    { key: "suggestion", title: "Предложение", wrap: true, grow: true, value: (row) => row.suggestion,
      render: (row) => row.suggestion
        ? <span className="nf-suggest">{row.suggestion}{" "}
          <a href={href({ section: "references", tab: "templates" })} onClick={(event) => go(event, "references", { tab: "templates" })}
            title={`База данных → Операции${row.operation_code ? `, карточка ${row.operation_code}` : ""}`}>Изменить норму в библиотеке операций</a></span>
        : <span className="nf-muted">{EMPTY}</span> },
  ];

  const operatorColumns: DataColumn<NormFactOperator>[] = [
    { key: "name", title: "Монтажник", width: 260, value: (row) => row.operator_name, render: (row) => <b>{row.operator_name}</b> },
    { key: "facts", title: "Фактов", numeric: true, width: 150, value: (row) => row.facts, render: (row) => <FactsCount facts={row.facts} noNorm={row.no_norm} enough={row.enough} /> },
    { key: "median", title: "Факт / норма", numeric: true, width: 110, hint: "Медиана: 1,00 — по норме, 1,40 — на 40 % дольше", value: (row) => row.median ?? -1, render: (row) => <Ratio value={row.median} slow={slow} fast={fast} /> },
    { key: "spread", title: "Разброс", numeric: true, width: 120, hint: "Средняя половина фактов (от нижней до верхней четверти)", value: (row) => row.high != null && row.low != null ? row.high - row.low : -1, render: (row) => <Spread low={row.low} high={row.high} /> },
    { key: "actual", title: "Часов факт", numeric: true, width: 100, value: (row) => row.actual_seconds, render: (row) => hoursText(row.actual_seconds) },
    { key: "relative", title: "К обычному", numeric: true, width: 120, hint: "Медиана против того, как эту операцию делают все (если по ней хватает фактов): 1,00 — как все", value: (row) => row.relative ?? -1, render: (row) => <Ratio value={row.relative} slow={slow} fast={fast} /> },
    { key: "coefficient", title: "Коэф. скорости", numeric: true, width: 130, hint: "Предложение для «Компетенций»: план делит норму на коэффициент", value: (row) => row.coefficient ?? -1, render: (row) => row.coefficient == null ? <span className="nf-muted">{EMPTY}</span> : <b className="nf-num">{ratioText(row.coefficient)}</b> },
    { key: "suggestion", title: "Предложение", wrap: true, grow: true, value: (row) => row.suggestion,
      render: (row) => row.suggestion
        ? <span className="nf-suggest">{row.suggestion}{" "}
          <a href={href({ section: "card", user: row.operator_id, tab: "skills" })} onClick={(event) => go(event, "card", { user: row.operator_id, tab: "skills" })}
            title={`Карточка сотрудника ${row.operator_name} → Компетенции`}>Открыть компетенции</a></span>
        : <span className="nf-muted">{EMPTY}</span> },
  ];

  return <section className="panel nf-report" aria-label="Норма и факт">
    <div className="nf-head">
      <h2>Норма и факт</h2>
      <div className="nf-period" role="group" aria-label="Период">
        {PRESETS.map(([days, title]) => {
          const value = lastDays(days);
          const on = value.from === period.from && value.to === period.to;
          return <button key={days} type="button" className={on ? "secondary small on" : "secondary small"} aria-pressed={on} onClick={() => setPeriod(value)}>{title}</button>;
        })}
        <label>с<input type="date" aria-label="Начало периода" value={period.from} max={period.to} onChange={(event) => event.target.value && setPeriod({ ...period, from: event.target.value })} /></label>
        <label>по<input type="date" aria-label="Конец периода" value={period.to} min={period.from} onChange={(event) => event.target.value && setPeriod({ ...period, to: event.target.value })} /></label>
      </div>
      <ul className="nf-legend" aria-label="Цвета факт / норма">
        <li><span className="nf-ok">в норме</span> {ratioText(fast)}–{ratioText(slow)}</li>
        <li><span className="nf-slow">медленнее</span> &gt; {ratioText(slow)}</li>
        <li><span className="nf-fast">быстрее</span> &lt; {ratioText(fast)}</li>
        <li><span className="nf-few">мало данных</span> — меньше {minFacts} фактов, без предложения</li>
      </ul>
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!report && !error && <div className="empty">Загрузка…</div>}
    {report && !report.facts.length && <div className="empty">За {label} фактов нет. Фактов пока мало — отчёт наполнится, когда монтажники отмечают начало и сдачу работы.</div>}
    {report && report.facts.length > 0 && <>
      <dl className="nf-summary">
        <div><dt>Период сдачи</dt><dd>{label}</dd></div>
        <div><dt>Фактов</dt><dd>{report.facts.length}</dd></div>
        <div><dt>Часов факт</dt><dd>{hoursText(actual)}</dd></div>
        <div><dt>Часов по норме</dt><dd>{hoursText(norm)}</dd></div>
        <div><dt>Факт к норме, всего</dt><dd><Ratio value={norm ? actual / norm : null} slow={slow} fast={fast} /></dd></div>
      </dl>
      <h3 className="nf-title">По операциям<span className="page-count">{report.templates.length}</span></h3>
      <DataTable rows={report.templates} columns={templateColumns} rowKey={(row) => row.template} label="По операциям" hideSearch phoneCards={false}
        exportName={`Норма и факт — операции ${label}`} onRowClick={(row) => setDrill({ kind: "template", row })} rowTitle={(row) => `Факты: ${row.stage || row.template}`} />
      <h3 className="nf-title">По монтажникам<span className="page-count">{report.operators.length}</span></h3>
      <DataTable rows={report.operators} columns={operatorColumns} rowKey={(row) => row.operator_id} label="По монтажникам" hideSearch phoneCards={false}
        exportName={`Норма и факт — монтажники ${label}`} onRowClick={(row) => setDrill({ kind: "operator", row })} rowTitle={(row) => `Факты: ${row.operator_name}`} />
    </>}
    {drill && report && <FactsWindow drill={drill} facts={report.facts} slow={slow} fast={fast} label={label} close={() => setDrill(null)} />}
  </section>;
}

function FactsWindow({ drill, facts, slow, fast, label, close }: {
  drill: Drill; facts: NormFactFact[]; slow: number; fast: number; label: string; close: () => void;
}) {
  const byTemplate = drill.kind === "template";
  const title = byTemplate ? (drill.row.stage || drill.row.template) : drill.row.operator_name;
  const rows = facts.filter((fact) => (byTemplate ? fact.template === drill.row.template : fact.operator_id === drill.row.operator_id))
    .sort((a, b) => b.finished_at.localeCompare(a.finished_at));
  const columns: DataColumn<NormFactFact>[] = [
    { key: "finished", title: "Сдано", width: 110, value: (row) => row.finished_at, render: (row) => <span className="nf-num">{formatMoment(row.finished_at)}</span> },
    { key: "task", title: "Задание", width: 220, value: (row) => `${row.task_number} ${row.project} ${row.item}`,
      render: (row) => <>{row.task_number} · {row.project}{row.item ? ` · ${row.item}` : ""}</> },
    byTemplate
      ? { key: "who", title: "Монтажник", filter: true, width: 170, value: (row) => row.operator_name, render: (row) => <b>{row.operator_name}</b> }
      : { key: "op", title: "Операция", filter: true, width: 240, value: (row) => row.stage || row.template,
        render: (row) => <><b>{row.stage || row.template}</b>{row.operation_code && <small className="nf-code">{row.operation_code}</small>}</> },
    { key: "qty", title: "Штук", numeric: true, width: 80, value: (row) => Number(row.quantity), render: (row) => formatNumber(row.quantity) },
    { key: "actual", title: "Факт", numeric: true, width: 100, value: (row) => row.actual_seconds, render: (row) => hoursText(row.actual_seconds) },
    { key: "norm", title: "Норма", numeric: true, width: 100, value: (row) => row.norm_seconds, render: (row) => hoursText(row.norm_seconds) },
    { key: "apiece", title: "Факт на шт", numeric: true, width: 110, value: (row) => row.actual_per_piece ?? -1, render: (row) => pieceTime(row.actual_per_piece) },
    { key: "npiece", title: "Норма на шт", numeric: true, width: 110, value: (row) => row.norm_per_piece ?? -1, render: (row) => pieceTime(row.norm_per_piece) },
    { key: "ratio", title: "Факт / норма", numeric: true, width: 110, value: (row) => row.ratio ?? -1,
      render: (row) => row.ratio == null ? <span className="nf-muted">без нормы</span> : <Ratio value={row.ratio} slow={slow} fast={fast} /> },
    { key: "submissions", title: "Сдач", numeric: true, width: 70, value: (row) => row.submissions },
  ];
  return <PageWindow label={`Факты: ${title}`} width={1280} dismissable close={close}>
    <section className="panel nf-report" aria-label={`Факты: ${title}`}>
      <div className="nf-head">
        <h2>{title}<span className="page-count">{rows.length}</span></h2>
        <span className="nf-muted">{byTemplate ? "Операция" : "Монтажник"} · сдачи за {label}</span>
        <button type="button" className="secondary small nf-close" onClick={close}>Закрыть</button>
      </div>
      <DataTable rows={rows} columns={columns} rowKey={(row) => `${row.operation_id}:${row.operator_id}`} label="Факты" hideSearch phoneCards={false}
        exportName={`Норма и факт — ${title} ${label}`} pageSize={200} />
    </section>
  </PageWindow>;
}
