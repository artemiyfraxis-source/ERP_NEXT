import { useEffect, useMemo, useRef, useState } from "react";
import { failureText, productionApi, shippingApi, type FinishedGood, type FinishedGoodsReadyBox, type Principal } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { DispatchCard } from "./DispatchCard";
import { FinishedGoodsHandoff } from "./FinishedGoodsHandoff";
import { formatDay, formatDayMonth } from "./format";
import { isoDay } from "./Gantt";
import { daysUntil } from "./productionGraph";
import { canAct } from "./navigation";
import { intakeApi, type IntakeAct, type IntakeRow } from "./productionIntakeApi";
import { IntakeWindow, leftOf } from "./IntakeWindow";
import { PrintOutcome, type PrintResult } from "./LabelPrint";
import "./productionIntake.css";

/* «Приёмка с производства» (владелец 14.10): всё, что у нас в работе, по проектам. Склад принимает кусками —
   сколько цех уже сдал; принятое встаёт в «Отгрузку». Данные отправки вносят, пока проект в работе. */

type Segment = "ready" | "production" | "all";
const SEGMENTS: Array<[Segment, string]> = [["ready", "Можно принять"], ["production", "В производстве"], ["all", "Все"]];

const num = (value: string | number | null | undefined) => {
  const parsed = Number(String(value ?? "").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
};
const round2 = (value: number) => Math.round(value * 100) / 100;
const today = () => isoDay(new Date());
const keyOf = (row: IntakeRow) => `${row.project}\u0000${row.item_index}`;
const lineKey = (row: IntakeLine) => row.act ? `${keyOf(row)}\u0000${row.act.grn}` : keyOf(row);
const RECENT_DAYS = 7;
const dash = <span className="muted">—</span>;
const count = (value: string) => num(value) > 0 ? value : dash;

function tasksText(row: IntakeRow) {
  return row.tasks.map((task) => `${task.number}${task.site ? ` · ${task.site}` : ""} · сдано ${task.delivered} из ${task.quantity}`).join("\n");
}

/** «В производстве» (владелец 14.10): по этапу КБ — «В работе» или «ОТК», — или запущено больше, чем сдано цехом. */
const IN_WORK_STAGES = new Set(["В работе", "ОТК"]);
const recentAct = (row: IntakeRow) => {
  const from = new Date(); from.setDate(from.getDate() - RECENT_DAYS);
  return (row.acts ?? []).some((act) => act.received_at >= isoDay(from));
};
const inSegment = (row: IntakeRow, segment: Segment) => segment === "all"
  || (segment === "ready" ? num(row.remaining) > 0 || recentAct(row) : IN_WORK_STAGES.has(row.stage) || num(row.launched) > num(row.delivered));

/** КБ с принятым → зелёные строки актов, затем остаток (заказано − принято по КБ в целом, не по ГРН). «Заказано» — на каждой
 *  строке; «Остаток» — у строки КБ без акта (у актов «—»: остаток один на КБ, а не на каждый ГРН). */
type IntakeLine = IntakeRow & { act?: IntakeAct; left: number };
function expand(row: IntakeRow): IntakeLine[] {
  const acts = row.acts ?? [];
  const left = leftOf(row);
  if (!acts.length) return [{ ...row, left }];
  const lines: IntakeLine[] = acts.map((act) => ({
    ...row, act, left: 0, stage: "", launched: "0", delivered: "0", accepted: act.quantity, remaining: "0", packed: "0", sent: "0",
    grn: act.grn, location: act.location, tasks: [],
  }));
  if (left > 0 || num(row.remaining) > 0) lines.push({ ...row, left, accepted: "0", grn: null, location: "" });
  return lines;
}

export function ProductionIntakePage({ principal }: { principal: Principal }) {
  // Принимает кладовщик («Отгрузка — склад»); данные отправки вносят и менеджер («Отгрузка»), и кладовщик.
  const canWarehouse = canAct(principal, "shipping_goods");
  const canDispatch = canAct(principal, "shipping") || canWarehouse;
  const [rows, setRows] = useState<IntakeRow[]>([]);
  const [goods, setGoods] = useState<FinishedGood[]>([]);
  const [boxes, setBoxes] = useState<FinishedGoodsReadyBox[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [segment, setSegment] = useState<Segment>("ready");
  const segmentChosen = useRef(false);
  const [intake, setIntake] = useState<IntakeRow[] | null>(null);
  const [label, setLabel] = useState<{ grn: string; result: PrintResult | null; error: string; busy: boolean } | null>(null);
  const [card, setCard] = useState<string | null>(null);
  async function reload() {
    try {
      const [intake, goodsResult, boxesResult] = await Promise.all([
        intakeApi.rows(),
        canWarehouse ? shippingApi.goods() : Promise.resolve({ goods: [] as FinishedGood[] }),
        canWarehouse ? productionApi.finishedGoodsReady() : Promise.resolve({ boxes: [] as FinishedGoodsReadyBox[] }),
      ]);
      const loadedRows = intake.rows ?? [];
      setRows(loadedRows); setGoods(goodsResult.goods ?? []); setBoxes(boxesResult.boxes ?? []); setError("");
      // Принимать нечего — сразу то, что в производстве (если и там пусто — всё).
      if (!segmentChosen.current && !loadedRows.some((row) => inSegment(row, "ready"))) {
        setSegment(loadedRows.some((row) => inSegment(row, "production")) ? "production" : "all");
      }
    } catch (reason) {
      setError(failureText(reason, "Не удалось загрузить список КБ в работе"));
    } finally { setLoaded(true); }
  }
  useEffect(() => { void reload(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = useMemo(() => rows.filter((row) => inSegment(row, segment)).flatMap(expand), [rows, segment]);
  const head = useMemo(() => {
    const byProject: Record<string, IntakeRow> = {};
    for (const row of rows) if (!byProject[row.project]) byProject[row.project] = row;
    return byProject;
  }, [rows]);

  /** Строка КБ как пришла с сервера (у строки-остатка «Принято» обнулено для таблицы, окну нужно настоящее). */
  const source = (line: IntakeRow) => rows.find((row) => keyOf(row) === keyOf(line)) ?? line;

  async function accepted(pieces: number) {
    setIntake(null); setError("");
    setNotice(`Принято на склад: ${pieces} шт`);
    await reload();
  }

  /** «Печать этикетки» ГРН: та же очередь печати, что у приёмки закупки; статус — под таблицей. */
  async function printLabel(row: IntakeLine) {
    if (!row.grn || label?.busy) return;
    const grn = row.grn;
    setLabel({ grn, result: null, error: "", busy: true });
    try {
      const result = await intakeApi.printLabel(grn);
      setLabel({ grn, result, error: "", busy: false });
    } catch (reason) {
      setLabel({ grn, result: null, error: failureText(reason, "Этикетка не напечатана"), busy: false });
    }
  }

  const mutateBoxes = async (key: string, operation: () => Promise<unknown>, message: string) => {
    if (busy) return false;
    setBusy(key); setError(""); setNotice("");
    try { await operation(); setNotice(message); await reload(); return true; }
    catch (reason) { setError(failureText(reason, "Операция не выполнена")); return false; }
    finally { setBusy(""); }
  };

  const columns: DataColumn<IntakeLine>[] = [
    { key: "project", title: "Проект", value: (row) => row.project, hidden: true },
    { key: "kb", title: "КБ", value: (row) => row.item_index, render: (row) => <strong>{row.item_index}</strong> },
    // Строка — одна линия (владелец 15.10): длинное наименование обрезается «…», целиком — в подсказке.
    { key: "name", title: "Наименование", value: (row) => row.name, render: (row) => <span className="intake-clip intake-name" title={row.name}>{row.name}</span> },
    { key: "needed", title: "Заказано", value: (row) => num(row.needed), numeric: true, render: (row) => row.needed },
    { key: "left", title: "Остаток", value: (row) => row.left, numeric: true, render: (row) => row.left > 0 ? row.left : dash },
    { key: "launched", title: "Запущено", value: (row) => num(row.launched), numeric: true, render: (row) => count(row.launched) },
    { key: "accepted", title: "Принято", value: (row) => num(row.accepted), numeric: true, render: (row) => count(row.accepted) },
    { key: "remaining", title: "Можно принять", value: (row) => row.act ? 0 : num(row.remaining), numeric: true,
      render: (row) => {
        if (row.act || !(num(row.remaining) > 0)) return dash;
        if (!canWarehouse) return <strong className="tone-ok" title={tasksText(row)}>{row.remaining}</strong>;
        return <button type="button" className="intake-take-btn" title={tasksText(row) || "Принять на склад"} aria-label={`Принять ${row.project} ${row.item_index}: можно ${row.remaining}`}
          onClick={() => setIntake([source(row)])}>{row.remaining}</button>;
      } },
    { key: "packed", title: "Упаковано", value: (row) => num(row.packed), numeric: true, render: (row) => count(row.packed) },
    { key: "sent", title: "Отгружено", value: (row) => num(row.sent), numeric: true, render: (row) => count(row.sent) },
    { key: "received", title: "Дата приёмки", value: (row) => row.act?.received_at ?? "", render: (row) => row.act ? formatDay(row.act.received_at) : dash },
    { key: "grn", title: "ГРН", value: (row) => row.grn ?? "", render: (row) => {
      if (!row.grn) return dash;
      const printable = canWarehouse && (row.act || num(row.accepted) > 0);
      return <span className="intake-grn"><span className="goods-grn">{row.grn}</span>
        {printable && <button type="button" className="secondary small intake-print" title="Печать этикетки" aria-label={`Печать этикетки ${row.grn}`}
          disabled={Boolean(label?.busy)} onClick={() => void printLabel(row)}>{label?.busy && label.grn === row.grn ? "…" : "Печать"}</button>}</span>;
    } },
    // Задания — по умолчанию в подсказке «Сдано цехом» (колонку можно включить в «Колонках»): на 16" таблица без прокрутки вбок.
    { key: "tasks", title: "Задания", hidden: true, value: (row) => row.tasks.map((task) => task.number).join(", "),
      render: (row) => row.tasks.length ? <span className="intake-clip intake-stage muted" title={tasksText(row)}>{row.tasks.map((task) => task.number).join(", ")}</span> : dash },
  ];
  // Колонка, где у всех строк вкладки пусто, не показывается (владелец 15.10: «почти все числа — прочерки»).
  const OPTIONAL: Record<string, (row: IntakeLine) => boolean> = {
    launched: (row) => num(row.launched) > 0, accepted: (row) => num(row.accepted) > 0, packed: (row) => num(row.packed) > 0,
    sent: (row) => num(row.sent) > 0, grn: (row) => Boolean(row.grn), received: (row) => Boolean(row.act), tasks: (row) => row.tasks.length > 0,
  };
  const visibleColumns = columns.filter((column) => !OPTIONAL[column.key] || shown.some(OPTIONAL[column.key]));

  const groupLabel = (project: string, groupRows: IntakeLine[]) => {
    const first = head[project];
    const title = [project, first?.customer].filter(Boolean).join(" · ");
    const left = first?.deadline ? daysUntil(first.deadline) : null;
    const ready = groupRows.filter((row) => num(row.remaining) > 0);
    const pieces = round2(ready.reduce((sum, row) => sum + num(row.remaining), 0));
    return <span className="intake-group">{title}
      {first?.deadline && <span className={left !== null && left < 0 ? "intake-due tone-danger" : left !== null && left <= 3 ? "intake-due tone-warn" : "intake-due"}>
        срок {formatDayMonth(first.deadline)}{left !== null && left < 0 ? ` · просрочен на ${-left} дн.` : left === 0 ? " · сегодня" : ""}</span>}
      {canWarehouse && ready.length > 1 && <button type="button" className="primary small" onClick={(event) => { event.stopPropagation(); setIntake(ready.map(source)); }}>
        Принять всё: {ready.length} КБ · {pieces} шт</button>}
      <button type="button" className="secondary small" onClick={(event) => { event.stopPropagation(); setCard(project); }}>Данные отправки</button></span>;
  };

  return <section className="page intake-page">
    <header className="page-head"><h1>Приёмка с производства</h1></header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner" role="status">{notice}</div>}
    {!loaded ? <div className="empty">Загрузка…</div> : <>
      <div className="seg-tabs" role="tablist" aria-label="Что показать">{SEGMENTS.map(([key, label]) =>
        <button key={key} type="button" role="tab" aria-selected={segment === key} className={segment === key ? "on" : undefined} onClick={() => { segmentChosen.current = true; setSegment(key); }}>
          {label}<span className="page-count"> {rows.filter((row) => inSegment(row, key)).length}</span></button>)}</div>
      <DataTable label="Приёмка с производства" storageKey="production-intake" rows={shown} columns={visibleColumns} rowKey={lineKey}
        groupBy={(row) => row.project} groupLabel={groupLabel} exportName="Приёмка с производства"
        searchPlaceholder="Проект, КБ, наименование или задание"
        rowClassName={(row) => row.act ? "intake-act-row" : num(row.remaining) > 0 ? "" : "ship-line-off"}
        emptyText={rows.length ? "В этом списке пусто — другие КБ во вкладках «В производстве» и «Все»." : "Нет КБ в работе"} />
      {label && <div className="intake-print-status"><strong>Этикетка {label.grn}</strong><PrintOutcome result={label.result} error={label.error} /></div>}
      {canWarehouse && boxes.length > 0 && <FinishedGoodsHandoff boxes={boxes} goods={goods} busy={busy} mutate={mutateBoxes} />}
    </>}
    {intake && <IntakeWindow rows={intake} close={() => setIntake(null)} onDone={accepted} />}
    {card && <DispatchCard project={card} customer={head[card]?.customer} canEdit={canDispatch} close={() => setCard(null)} />}
  </section>;
}
