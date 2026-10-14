import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, failureText, productionApi, shippingApi, type FinishedGood, type FinishedGoodsReadyBox, type Principal } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { DispatchCard } from "./DispatchCard";
import { FinishedGoodsHandoff } from "./FinishedGoodsHandoff";
import { formatDayMonth } from "./format";
import { isoDay } from "./Gantt";
import { canAct } from "./navigation";
import { intakeApi, type IntakeRow } from "./productionIntakeApi";
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
const dash = <span className="muted">—</span>;
const count = (value: string) => num(value) > 0 ? value : dash;

function tasksText(row: IntakeRow) {
  return row.tasks.map((task) => `${task.number}${task.site ? ` · ${task.site}` : ""} · сдано ${task.delivered} из ${task.quantity}`).join("\n");
}

/** «В производстве» (владелец 14.10): по этапу КБ — «В работе» или «ОТК», — или запущено больше, чем сдано цехом. */
const IN_WORK_STAGES = new Set(["В работе", "ОТК"]);
const inSegment = (row: IntakeRow, segment: Segment) => segment === "all"
  || (segment === "ready" ? num(row.remaining) > 0 : IN_WORK_STAGES.has(row.stage) || num(row.launched) > num(row.delivered));

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

  const amount = (row: IntakeRow) => qty[keyOf(row)] ?? row.remaining;
  const acceptable = rows.filter((row) => num(row.remaining) > 0);
  const chosen = acceptable.filter((row) => picked[keyOf(row)] && num(amount(row)) > 0);
  const total = round2(chosen.reduce((sum, row) => sum + num(amount(row)), 0));
  const shown = useMemo(() => rows.filter((row) => inSegment(row, segment)), [rows, segment]);
  const head = useMemo(() => {
    const byProject: Record<string, IntakeRow> = {};
    for (const row of rows) if (!byProject[row.project]) byProject[row.project] = row;
    return byProject;
  }, [rows]);

  const pick = (row: IntakeRow, on: boolean) => {
    setPicked((current) => ({ ...current, [keyOf(row)]: on }));
    setRowErrors((current) => ({ ...current, [keyOf(row)]: "" }));
  };

  async function submit() {
    if (busy) return;
    const over: Record<string, string> = {};
    for (const row of chosen) if (num(amount(row)) > num(row.remaining)) over[keyOf(row)] = `больше сданного: можно ${row.remaining}`;
    setRowErrors(over);
    if (Object.keys(over).length) return;
    const lines = chosen.map((row) => ({ project: row.project, item_index: row.item_index, quantity: String(amount(row)).replace(",", ".") }));
    setBusy("accept"); setError(""); setNotice("");
    try {
      await intakeApi.accept(lines, day || null, location, acceptKey.current);
      acceptKey.current = crypto.randomUUID();
      setNotice(`Принято на склад: ${total} шт`);
      setPicked({}); setQty({});
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

  const columns: DataColumn<IntakeRow>[] = [
    ...(canWarehouse ? [{ key: "take", title: "Принять, шт", value: (row: IntakeRow) => picked[keyOf(row)] ? num(amount(row)) : "",
      render: (row: IntakeRow) => {
        const key = keyOf(row);
        if (!(num(row.remaining) > 0)) return dash;
        const problem = rowErrors[key];
        return <span className="intake-take"><span className="ship-take">
          <input type="checkbox" aria-label={`Принять ${row.project} ${row.item_index}`} checked={Boolean(picked[key])} onChange={(event) => pick(row, event.target.checked)} />
          <input className={problem ? "ship-qty invalid" : "ship-qty"} aria-label={`Сколько принять ${row.project} ${row.item_index}`} aria-invalid={problem ? true : undefined}
            type="number" min="0" step="any" value={amount(row)}
            onChange={(event) => { setQty({ ...qty, [key]: event.target.value }); pick(row, num(event.target.value) > 0); }} />
          </span>
          {problem && <small className="tone-danger ship-row-error" role="alert">{problem}</small>}
        </span>;
      } }] : []),
    { key: "project", title: "Проект", value: (row) => row.project, hidden: true },
    { key: "kb", title: "КБ", value: (row) => row.item_index, render: (row) => <strong>{row.item_index}</strong> },
    { key: "name", title: "Наименование", value: (row) => row.name, wrap: true, maxWidth: 190 },
    { key: "stage", title: "Этап", value: (row) => row.stage, wrap: true, maxWidth: 100, render: (row) => row.stage || dash },
    { key: "needed", title: "Заказано", value: (row) => num(row.needed), numeric: true },
    { key: "launched", title: "Запущено", value: (row) => num(row.launched), numeric: true, render: (row) => count(row.launched) },
    { key: "delivered", title: "Сдано цехом", value: (row) => num(row.delivered), numeric: true, render: (row) => <span title={tasksText(row)}>{count(row.delivered)}</span> },
    { key: "accepted", title: "Принято", value: (row) => num(row.accepted), numeric: true, render: (row) => count(row.accepted) },
    { key: "remaining", title: "Можно принять", value: (row) => num(row.remaining), numeric: true,
      render: (row) => num(row.remaining) > 0 ? <strong className="tone-ok">{row.remaining}</strong> : dash },
    { key: "packed", title: "Упаковано", value: (row) => num(row.packed), numeric: true, render: (row) => count(row.packed) },
    { key: "sent", title: "Отгружено", value: (row) => num(row.sent), numeric: true, render: (row) => count(row.sent) },
    { key: "location", title: "Место хранения", value: (row) => row.location, wrap: true, maxWidth: 110, render: (row) => row.location || dash },
    { key: "grn", title: "ГРН", value: (row) => row.grn ?? "", render: (row) => row.grn ? <span className="goods-grn">{row.grn}</span> : dash },
    { key: "tasks", title: "Задания", value: (row) => row.tasks.map((task) => task.number).join(", "), wrap: true, maxWidth: 90,
      render: (row) => row.tasks.length ? <span className="muted" title={tasksText(row)}>{row.tasks.map((task) => task.number).join(", ")}</span> : dash },
  ];

  const toolbar = canWarehouse ? <span className="ship-accept-bar">
    <label className="ship-inline">Дата приёмки<input type="date" aria-label="Дата приёмки" value={day} max={today()} onChange={(event) => setDay(event.target.value)} /></label>
    <label className="ship-inline">Место хранения<input className="intake-location" type="text" aria-label="Место хранения" value={location} maxLength={255} placeholder="ячейка" onChange={(event) => setLocation(event.target.value)} /></label>
    <button type="button" className="primary" disabled={!chosen.length || Boolean(busy)} onClick={() => void submit()}>
      {busy === "accept" ? "Принимаем…" : `Принять на склад · ${total} шт`}</button>
  </span> : undefined;

  const groupLabel = (project: string) => {
    const first = head[project];
    const title = [project, first?.customer, first?.deadline ? `срок ${formatDayMonth(first.deadline)}` : ""].filter(Boolean).join(" · ");
    return <span className="intake-group">{title}
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
      <DataTable label="Приёмка с производства" storageKey="production-intake" rows={shown} columns={columns} rowKey={keyOf}
        groupBy={(row) => row.project} groupLabel={groupLabel} toolbar={toolbar} exportName="Приёмка с производства"
        searchPlaceholder="Проект, КБ, наименование или задание"
        rowClassName={(row) => num(row.remaining) > 0 ? (picked[keyOf(row)] ? "ship-row-picked" : "") : "ship-line-off"}
        emptyText={rows.length ? "В этом списке пусто — другие КБ во вкладках «В производстве» и «Все»." : "Нет КБ в работе"} />
      {canWarehouse && boxes.length > 0 && <FinishedGoodsHandoff boxes={boxes} goods={goods} busy={busy} mutate={mutateBoxes} />}
    </>}
    {card && <DispatchCard project={card} customer={head[card]?.customer} canEdit={canDispatch} close={() => setCard(null)} />}
  </section>;
}
