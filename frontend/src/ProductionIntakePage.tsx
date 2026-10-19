import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, failureText, productionApi, shippingApi, type FinishedGood, type FinishedGoodsReadyBox, type Principal } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { DispatchCard } from "./DispatchCard";
import { FinishedGoodsHandoff } from "./FinishedGoodsHandoff";
import { formatDay, formatDayMonth } from "./format";
import { isoDay } from "./Gantt";
import { daysUntil } from "./productionGraph";
import { canAct } from "./navigation";
import { intakeApi, type IntakeAct, type IntakeRow } from "./productionIntakeApi";
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
/** Строка таблицы: КБ (или её остаток, когда часть уже принята) либо акт приёмки — зелёная строка без ввода. */
type IntakeLine = IntakeRow & { act?: IntakeAct };
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

/** КБ с принятым → зелёные строки актов, затем остаток (заказано − принято; нет остатка — КБ принята целиком). */
function expand(row: IntakeRow): IntakeLine[] {
  const acts = row.acts ?? [];
  if (!acts.length) return [row];
  const lines: IntakeLine[] = acts.map((act) => ({
    ...row, act, stage: "", needed: "0", launched: "0", delivered: "0", accepted: act.quantity, remaining: "0", packed: "0", sent: "0",
    grn: act.grn, location: act.location, tasks: [],
  }));
  const rest = round2(Math.max(num(row.needed) - num(row.accepted), 0));
  if (rest > 0 || num(row.remaining) > 0) lines.push({ ...row, needed: String(rest), accepted: "0", grn: null, location: "" });
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
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [qty, setQty] = useState<Record<string, string>>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [day, setDay] = useState(today);
  const [location, setLocation] = useState("");
  const [places, setPlaces] = useState<Record<string, string>>({});
  const [card, setCard] = useState<string | null>(null);
  // Ключ повтора живёт до успеха: повтор после обрыва связи вернёт уже сделанную приёмку, а не примет второй раз.
  const acceptKey = useRef(crypto.randomUUID());

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

  // Одно намерение — одно поле (владелец 15.10): у неотмеченной строки количество пустое («до 20»), отметка ставит «всё
  // сданное», ввод количества строку отмечает, пустое поле — снимает.
  const amount = (row: IntakeRow) => qty[keyOf(row)] ?? (picked[keyOf(row)] ? row.remaining : "");
  const acceptable = rows.filter((row) => num(row.remaining) > 0);
  const chosen = acceptable.filter((row) => picked[keyOf(row)] && num(amount(row)) > 0);
  const total = round2(chosen.reduce((sum, row) => sum + num(amount(row)), 0));
  const shown = useMemo(() => rows.filter((row) => inSegment(row, segment)).flatMap(expand), [rows, segment]);
  const head = useMemo(() => {
    const byProject: Record<string, IntakeRow> = {};
    for (const row of rows) if (!byProject[row.project]) byProject[row.project] = row;
    return byProject;
  }, [rows]);

  const pick = (row: IntakeRow, on: boolean) => {
    setPicked((current) => ({ ...current, [keyOf(row)]: on }));
    setRowErrors((current) => ({ ...current, [keyOf(row)]: "" }));
  };
  /** Отметка строки флажком: количество — снова «всё сданное». */
  const tick = (rowsToTick: IntakeRow[], on: boolean) => {
    const keys = rowsToTick.map(keyOf);
    setPicked((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, on])) }));
    setQty((current) => Object.fromEntries(Object.entries(current).filter(([key]) => !keys.includes(key))));
    setRowErrors((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, ""])) }));
  };

  async function submit() {
    if (busy) return;
    const over: Record<string, string> = {};
    for (const row of chosen) if (num(amount(row)) > num(row.remaining)) over[keyOf(row)] = `больше сданного: можно ${row.remaining}`;
    setRowErrors(over);
    if (Object.keys(over).length) return;
    // Место строки заменяет общее «Место хранения» (сервер: непустое место строки важнее места пакета).
    const lines = chosen.map((row) => {
      const place = (places[keyOf(row)] ?? "").trim();
      return { project: row.project, item_index: row.item_index, quantity: String(amount(row)).replace(",", "."), ...(place ? { location: place } : {}) };
    });
    setBusy("accept"); setError(""); setNotice("");
    try {
      await intakeApi.accept(lines, day || null, location, acceptKey.current);
      acceptKey.current = crypto.randomUUID();
      setNotice(`Принято на склад: ${total} шт`);
      setPicked({}); setQty({}); setPlaces({});
      await reload();
    } catch (reason) {
      // Пакет — все строки или ни одной: сервер называет строку («591К.3 КБ1: сдано цехом …»), показываем её там.
      const text = reason instanceof Error ? reason.message : "";
      const hit = reason instanceof ApiError && reason.status === 422 ? chosen.find((row) => text.startsWith(`${row.project} ${row.item_index}:`)) : undefined;
      if (hit) {
        const why = text.slice(`${hit.project} ${hit.item_index}:`.length).trim();
        setRowErrors({ [keyOf(hit)]: why });
        setError(`Ничего не принято: ${hit.project} ${hit.item_index} — ${why}`);
      } else setError(failureText(reason, "Приёмка не выполнена"));
    } finally { setBusy(""); }
  }

  const mutateBoxes = async (key: string, operation: () => Promise<unknown>, message: string) => {
    if (busy) return false;
    setBusy(key); setError(""); setNotice("");
    try { await operation(); setNotice(message); await reload(); return true; }
    catch (reason) { setError(failureText(reason, "Операция не выполнена")); return false; }
    finally { setBusy(""); }
  };

  const columns: DataColumn<IntakeLine>[] = [
    ...(canWarehouse ? [{ key: "take", title: "Принять, шт", value: (row: IntakeLine) => picked[keyOf(row)] ? num(amount(row)) : "",
      render: (row: IntakeLine) => {
        const key = keyOf(row);
        if (row.act || !(num(row.remaining) > 0)) return dash;
        const problem = rowErrors[key];
        return <span className="intake-take"><span className="ship-take">
          <input type="checkbox" aria-label={`Принять ${row.project} ${row.item_index}`} checked={Boolean(picked[key])} onChange={(event) => tick([row], event.target.checked)} />
          <input className={problem ? "ship-qty invalid" : "ship-qty"} aria-label={`Сколько принять ${row.project} ${row.item_index}`} aria-invalid={problem ? true : undefined}
            type="number" min="0" step="any" value={amount(row)} placeholder={`до ${row.remaining}`}
            onChange={(event) => { setQty({ ...qty, [key]: event.target.value }); pick(row, num(event.target.value) > 0); }} />
          </span>
          {problem && <small className="tone-danger ship-row-error" role="alert">{problem}</small>}
        </span>;
      } }] : []),
    { key: "project", title: "Проект", value: (row) => row.project, hidden: true },
    { key: "kb", title: "КБ", value: (row) => row.item_index, render: (row) => <strong>{row.item_index}</strong> },
    // Строка — одна линия (владелец 15.10): длинное наименование обрезается «…», целиком — в подсказке.
    { key: "name", title: "Наименование", value: (row) => row.name, render: (row) => <span className="intake-clip intake-name" title={row.name}>{row.name}</span> },
    { key: "stage", title: "Этап", value: (row) => row.stage, render: (row) => row.stage ? <span className="intake-clip intake-stage" title={row.stage}>{row.stage}</span> : dash },
    { key: "needed", title: "Заказано", value: (row) => num(row.needed), numeric: true, render: (row) => row.act ? dash : row.needed },
    { key: "launched", title: "Запущено", value: (row) => num(row.launched), numeric: true, render: (row) => count(row.launched) },
    { key: "delivered", title: "Сдано цехом", value: (row) => num(row.delivered), numeric: true, render: (row) => row.act ? dash : <span title={tasksText(row)}>{count(row.delivered)}</span> },
    { key: "accepted", title: "Принято", value: (row) => num(row.accepted), numeric: true, render: (row) => count(row.accepted) },
    { key: "remaining", title: "Можно принять", value: (row) => num(row.remaining), numeric: true,
      render: (row) => !row.act && num(row.remaining) > 0 ? <strong className="tone-ok">{row.remaining}</strong> : dash },
    { key: "packed", title: "Упаковано", value: (row) => num(row.packed), numeric: true, render: (row) => count(row.packed) },
    { key: "sent", title: "Отгружено", value: (row) => num(row.sent), numeric: true, render: (row) => count(row.sent) },
    { key: "received", title: "Дата приёмки", value: (row) => row.act?.received_at ?? "", render: (row) => row.act ? formatDay(row.act.received_at) : dash },
    // Место — своё у отмеченной строки (пусто — общее из шапки); у остальных — где КБ лежит сейчас.
    { key: "location", title: "Место хранения", value: (row) => row.location, render: (row) => {
      const key = keyOf(row);
      if (canWarehouse && !row.act && picked[key] && num(row.remaining) > 0) {
        return <input className="intake-place" type="text" autoComplete="off" maxLength={255} aria-label={`Место хранения ${row.project} ${row.item_index}`}
          placeholder={location.trim() || row.location || "ячейка"} value={places[key] ?? ""} onChange={(event) => setPlaces({ ...places, [key]: event.target.value })} />;
      }
      return row.location ? <span className="intake-clip intake-stage" title={row.location}>{row.location}</span> : dash;
    } },
    { key: "grn", title: "ГРН", value: (row) => row.grn ?? "", render: (row) => row.grn ? <span className="goods-grn">{row.grn}</span> : dash },
    // Задания — по умолчанию в подсказке «Сдано цехом» (колонку можно включить в «Колонках»): на 16" таблица без прокрутки вбок.
    { key: "tasks", title: "Задания", hidden: true, value: (row) => row.tasks.map((task) => task.number).join(", "),
      render: (row) => row.tasks.length ? <span className="intake-clip intake-stage muted" title={tasksText(row)}>{row.tasks.map((task) => task.number).join(", ")}</span> : dash },
  ];
  // Колонка, где у всех строк вкладки пусто, не показывается (владелец 15.10: «почти все числа — прочерки»).
  // Место хранения у склада остаётся: туда пишут место отмеченной строки.
  const OPTIONAL: Record<string, (row: IntakeRow) => boolean> = {
    launched: (row) => num(row.launched) > 0, accepted: (row) => num(row.accepted) > 0, packed: (row) => num(row.packed) > 0,
    sent: (row) => num(row.sent) > 0, grn: (row) => Boolean(row.grn), received: (row) => Boolean(row.act), tasks: (row) => row.tasks.length > 0, stage: (row) => Boolean(row.stage),
    ...(canWarehouse ? {} : { location: (row: IntakeRow) => Boolean(row.location) }),
  };
  const visibleColumns = columns.filter((column) => !OPTIONAL[column.key] || shown.some(OPTIONAL[column.key]));

  const toolbar = canWarehouse ? <span className="ship-accept-bar">
    <label className="ship-inline">Дата приёмки<input type="date" aria-label="Дата приёмки" value={day} max={today()} onChange={(event) => setDay(event.target.value)} /></label>
    <label className="ship-inline">Место хранения<input className="intake-location" type="text" aria-label="Место хранения" title="Для всех отмеченных строк; своё место строки — в колонке «Место хранения»"
      value={location} maxLength={255} placeholder="для отмеченных" onChange={(event) => setLocation(event.target.value)} /></label>
    <button type="button" className="primary" disabled={!chosen.length || Boolean(busy)} onClick={() => void submit()}>
      {busy === "accept" ? "Принимаем…" : `Принять на склад · ${total} шт`}</button>
  </span> : undefined;

  const groupLabel = (project: string, groupRows: IntakeLine[]) => {
    const first = head[project];
    const title = [project, first?.customer].filter(Boolean).join(" · ");
    const left = first?.deadline ? daysUntil(first.deadline) : null;
    const ready = groupRows.filter((row) => num(row.remaining) > 0);
    const all = ready.length > 0 && ready.every((row) => picked[keyOf(row)]);
    const some = ready.some((row) => picked[keyOf(row)]);
    const pieces = round2(ready.reduce((sum, row) => sum + num(row.remaining), 0));
    return <span className="intake-group">{title}
      {first?.deadline && <span className={left !== null && left < 0 ? "intake-due tone-danger" : left !== null && left <= 3 ? "intake-due tone-warn" : "intake-due"}>
        срок {formatDayMonth(first.deadline)}{left !== null && left < 0 ? ` · просрочен на ${-left} дн.` : left === 0 ? " · сегодня" : ""}</span>}
      {canWarehouse && ready.length > 0 && <label className="intake-all">
        <input type="checkbox" aria-label={`Отметить все КБ проекта ${project}`} checked={all}
          ref={(input) => { if (input) input.indeterminate = some && !all; }} onChange={(event) => tick(ready, event.target.checked)} />
        все к приёмке: {ready.length} КБ · {pieces} шт</label>}
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
        groupBy={(row) => row.project} groupLabel={groupLabel} toolbar={toolbar} exportName="Приёмка с производства"
        searchPlaceholder="Проект, КБ, наименование или задание"
        rowClassName={(row) => row.act ? "intake-act-row" : num(row.remaining) > 0 ? (picked[keyOf(row)] ? "ship-row-picked" : "") : "ship-line-off"}
        emptyText={rows.length ? "В этом списке пусто — другие КБ во вкладках «В производстве» и «Все»." : "Нет КБ в работе"} />
      {canWarehouse && boxes.length > 0 && <FinishedGoodsHandoff boxes={boxes} goods={goods} busy={busy} mutate={mutateBoxes} />}
    </>}
    {card && <DispatchCard project={card} customer={head[card]?.customer} canEdit={canDispatch} close={() => setCard(null)} />}
  </section>;
}
