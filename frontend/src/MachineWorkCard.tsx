import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { failureText } from "./api";
import { CuttingQueue } from "./CuttingQueue";
import { cuttingQueueApi } from "./cuttingQueueApi";
import { EMPTY, formatNumber, formatWallMoment, formatWallTime } from "./format";
import { machineQueueApi, type MachineQueue, type MachineQueueRow } from "./machineQueueApi";
import type { MachineRow } from "./machinesApi";
import { StartForm } from "./ManualJobWindow";
import { manualJobsApi, type DowntimeReasonOption, type ManualJob, type ManualJobOptions, type ManualJobsList } from "./manualJobsApi";
import { PageWindow } from "./PageWindow";
import "./machineWork.css";

/** Рабочая карточка станка без управления («Оборудование», «Без управления»; план T4, 21.11.2026): станок ничего не
 *  сообщает, оператор ведёт счёт сам. Сверху вниз: шапка с «Наладка» → строка состояния («Свободен» / «Работает» /
 *  «Простой») → «Что делаем» (очередь станка, очередь резки, «Вне задания») → «Режим по ЭТК» выбранной строки → главное
 *  действие («Начать», либо «Сделано» и «Готово») → свёрнутые «Журнал» и «Подключение».
 *  «Готово» завершает только ручную работу; сдать полуфабрикат — в «Заданиях». Больше плана — предупреждение, не отказ. */

export const QUEUE_REFRESH_MS = 60_000;
type Tab = "queue" | "cut" | "free";
const CUT_FAMILIES = new Set(["Резка", "Резка и опрессовка", "Зачистка"]);
const OTHER_REASON = "other";
const SOURCE_MATERIAL = "Материал из задания";

const pad = (value: number) => String(value).padStart(2, "0");
const today = () => { const now = new Date(); return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`; };
const num = (value: string | number | null | undefined) => formatNumber(value, { digits: 2 });
const whole = (value: string) => (/^\d+$/.test(value.trim()) ? Number(value.trim()) : null);
const titleOf = (row: MachineRow) => [row.inventory, row.name, row.article, row.site].filter(Boolean).join(" · ");
const leftOf = (row: MachineQueueRow) => Math.trunc(Number(row.left)) || 0;
const sourceOf = (row: MachineQueueRow) => ({ task_id: row.task_id, operation_id: row.operation_id, etk_position: row.etk_position });
const purposeOf = (row: MachineQueueRow) => [row.project, row.item, row.step].filter(Boolean).join(" · ");
const clock = (value: string | null | undefined) => formatWallTime(value) || EMPTY;

export function MachineWorkCard({ row, canWrite, close, openSetup }: {
  row: MachineRow; canWrite: boolean; close: () => void; openSetup: () => void;
}) {
  const equipmentId = row.equipment_id ?? "";
  const [queue, setQueue] = useState<MachineQueue | null>(null);
  const [queueError, setQueueError] = useState("");
  const [list, setList] = useState<ManualJobsList | null>(null);
  const [error, setError] = useState("");
  const [cutRows, setCutRows] = useState(0);
  // Cutters and strippers work from the cutting queue: open it first.
  const [tab, setTab] = useState<Tab>(() => CUT_FAMILIES.has(row.family) ? "cut" : "queue");
  const [picked, setPicked] = useState("");
  const [options, setOptions] = useState<ManualJobOptions | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [edit, setEdit] = useState<{ key: string; value: string } | null>(null);
  const [finishing, setFinishing] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [stopForm, setStopForm] = useState(false);
  const ticket = useRef(0);

  const reload = useCallback(async () => {
    if (!equipmentId) return;
    const mine = ++ticket.current;
    const day = today();
    const [jobs, rows, cut] = await Promise.allSettled([
      manualJobsApi.list(day, day, equipmentId), machineQueueApi.queue(equipmentId), cuttingQueueApi.queueOfUnit(equipmentId)]);
    if (mine !== ticket.current) return;
    if (jobs.status === "fulfilled") { setList(jobs.value); setError(""); }
    else setError(failureText(jobs.reason, "Нет связи с ERP"));
    if (rows.status === "fulfilled") { setQueue(rows.value); setQueueError(""); }
    else setQueueError(failureText(rows.reason, "«Очередь станка» не загрузилась"));
    if (cut.status === "fulfilled") setCutRows(cut.value.rows.length);
  }, [equipmentId]);

  useEffect(() => {
    void reload();
    const timer = window.setInterval(() => { if (!document.hidden) void reload(); }, QUEUE_REFRESH_MS);
    return () => { window.clearInterval(timer); ticket.current += 1; };
  }, [reload]);

  const loadOptions = useCallback(() => {
    if (options) return;
    manualJobsApi.options().then(setOptions).catch((reason) => setError(failureText(reason, "Не удалось загрузить справочники")));
  }, [options]);
  useEffect(() => { if (tab === "free") loadOptions(); }, [tab, loadOptions]);

  const job: ManualJob | null = list?.rows.find((item) => item.status === "running" && item.equipment_id === equipmentId) ?? null;
  const downtime = list?.downtimes?.find((item) => item.open && item.equipment_id === equipmentId) ?? null;
  const editable = canWrite && list?.can_edit !== false;
  const rows = useMemo(() => queue?.rows ?? [], [queue]);
  const running = job ? rows.find((item) => item.job?.id === job.id) : undefined;
  const selected = running ?? rows.find((item) => item.key === picked) ?? rows[0] ?? null;
  const showCut = cutRows > 0 || CUT_FAMILIES.has(row.family);
  const planned = job?.quantity_planned ?? null;
  const progress = job?.progress_quantity ?? 0;
  // Набранное значение живёт, пока не сменилась версия работы; иначе показываем записанное.
  const editKey = `${job?.id ?? ""}:${job?.version ?? 0}`;
  const count = edit?.key === editKey ? edit.value : String(progress);
  const setCount = (value: string) => setEdit({ key: editKey, value });
  useEffect(() => { setFinishing(false); setCancelling(false); }, [job?.id, job?.version]);
  const typed = whole(count);
  const over = job !== null && planned !== null && typed !== null && typed > planned;

  async function act(work: () => Promise<unknown>, done: string, fallback: string): Promise<boolean> {
    setBusy(true);
    setNotice("");
    try {
      await work();
      setNotice(done);
      setError("");
      await reload();
      return true;
    } catch (reason) {
      setError(failureText(reason, fallback));
      await reload();
      return false;
    } finally { setBusy(false); }
  }

  const begin = (item: MachineQueueRow) => act(() => manualJobsApi.start({
    equipment_id: equipmentId, purpose: purposeOf(item), wire: "", contact: "", length_mm: "",
    quantity_planned: leftOf(item) > 0 ? leftOf(item) : null, reference_id: null, source: sourceOf(item) }), "Работа начата", "Не удалось начать работу");
  const record = () => {
    if (!job || typed === null) { setError("Укажите целое число штук"); return; }
    void act(() => manualJobsApi.progress(job.id, job.version, typed), `Записано: сделано ${typed}`, "Не удалось записать «Сделано»");
  };

  const status = downtime
    ? { tone: "warn", text: `Простой: ${downtime.reason_name}${downtime.comment ? ` · ${downtime.comment}` : ""} с ${clock(downtime.started_at)}` }
    : job
      ? { tone: "ok", text: `Работает: ${job.number} · ${job.source?.label || job.purpose} · с ${clock(job.started_at)} (${job.operator})` }
      : { tone: "neutral", text: "Свободен · без связи с ERP — счёт вручную" };
  const blockStart = job ? `Начать — сначала закончите ${job.number}` : "";
  const tabs: [Tab, string][] = [["queue", `Очередь станка (${rows.length})`], ...(showCut ? [["cut", `Очередь резки (${cutRows})`] as [Tab, string]] : []),
    ["free", "Вне задания"]];

  return <PageWindow label={titleOf(row)} close={close} width={CUT_FAMILIES.has(row.family) ? 1720 : 1200}>
    <section className="panel mw-card">
      <header className="mw-head">
        <h2>{titleOf(row)}</h2>
        <button type="button" className="secondary small" onClick={openSetup}>Наладка{row.pending_setup ? " · ждёт" : ""}</button>
        <button type="button" className="secondary small" onClick={close}>Закрыть</button>
      </header>
      {error && <p className="error-banner" role="alert">{error}</p>}
      <div className={`mw-status tone-bar-${status.tone}`} role="status" aria-label="Состояние станка">
        <strong>{status.text}</strong>
        {downtime && job && <span className="mw-sub">Работа {job.number} идёт</span>}
        {editable && <span className="mw-status-act">
          {downtime ? <button type="button" className="secondary small" disabled={busy}
            onClick={() => void act(() => manualJobsApi.stopDowntime(downtime.id), "Простой закончен", "Не удалось закончить простой")}>Закончить простой</button>
            : <button type="button" className="secondary small" disabled={busy} aria-expanded={stopForm}
              onClick={() => { setStopForm((open) => !open); loadOptions(); }}>Простой</button>}
        </span>}
      </div>
      {stopForm && !downtime && editable && <DowntimeForm reasons={options?.reasons ?? []} busy={busy} cancel={() => setStopForm(false)}
        submit={async (reason, comment) => { if (await act(() => manualJobsApi.startDowntime(equipmentId, reason, comment), "Простой начат", "Не удалось начать простой")) setStopForm(false); }} />}
      {notice && <p className="success-banner" role="status">{notice}</p>}

      <h3 className="mw-h">Что делаем</h3>
      <div className="seg-tabs" role="tablist" aria-label="Что делаем">
        {tabs.map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""}
          onClick={() => setTab(id)}>{label}</button>)}
      </div>
      {tab === "queue" && <>
        {queueError && <p className="error-banner" role="alert">{queueError}</p>}
        {!queue && !queueError ? <p className="empty">Загрузка…</p>
          : queue && !rows.length ? <p className="empty">В очереди станка пусто — задания с операциями для этого станка появятся здесь.</p>
            : rows.length > 0 && <div className="table-wrap"><table className="grid mw-table">
              <thead><tr><th>Проект</th><th>КБ</th><th>ПФ</th><th>Операция ЭТК</th><th className="num">План</th><th className="num">Сделано</th>
                <th className="num">Осталось</th><th>Замечания</th><th>{editable ? "Действие" : ""}</th></tr></thead>
              <tbody>{rows.map((item) => <tr key={item.key} className={`${item.key === selected?.key ? "mw-current" : ""}${item.job ? " mw-running" : ""}`}
                aria-current={item.key === selected?.key ? "true" : undefined} onClick={() => setPicked(item.key)}>
                <td>{item.project}</td><td>{item.item}</td><td>{item.pf || EMPTY}</td><td>{item.step}</td>
                <td className="num">{num(item.planned)}</td><td className="num">{num(item.done)}</td><td className="num"><b>{num(item.left)}</b></td>
                <td className="mw-warn">{(item.warnings ?? []).map((text) => <div key={text}>{text}</div>)}</td>
                <td className="mw-act">{item.job ? <span className="mw-badge">в работе {item.job.number}</span>
                  : editable && <button type="button" className="primary small" disabled={busy || Boolean(job)} title={blockStart || undefined}
                    onClick={(event) => { event.stopPropagation(); void begin(item); }}>Начать</button>}</td>
              </tr>)}</tbody>
            </table></div>}
        {editable && job && !running && rows.length > 0 && <p className="mw-hint">{blockStart}</p>}
      </>}
      {tab === "cut" && <CuttingQueue equipmentId={equipmentId} readOnly={!canWrite} onChanged={() => void reload()} />}
      {tab === "free" && (!options ? <p className="empty">Загрузка…</p>
        : editable ? (job ? <p className="mw-hint">{blockStart}</p>
          : <StartForm options={options} equipmentId={equipmentId} inventory={row.inventory} onStarted={() => { setNotice("Работа начата"); setTab("queue"); void reload(); }} />)
          : <p className="empty">Начинать работу вам не разрешено</p>)}

      {tab === "queue" && selected && <section className="mw-etk" aria-label="Режим по ЭТК">
        <h3 className="mw-h">Режим по ЭТК <span className="mw-sub">{[selected.project, selected.item, selected.step].filter(Boolean).join(" · ")}</span></h3>
        {selected.etk?.length ? <ul className="mw-chips">{selected.etk.map((chip, index) => <li key={`${index}:${chip.label}`} className={chip.value ? "" : "is-missing"}>
          <span className="mw-chip-label">{chip.label}</span> {chip.value ? <b>{chip.value}</b> : <b>не задано</b>}
          {chip.value && chip.source === "etk" && <span className="mw-sub"> · из ЭТК</span>}</li>)}</ul>
          : <p className="mw-hint">В ЭТК для шага нет режима станка</p>}
      </section>}

      {job && <section className="mw-main" aria-label="Работа">
        <h3 className="mw-h">{job.number} · {job.source?.label || job.purpose}</h3>
        {editable ? <>
          <div className="mw-counter">
            <label>Сделано{" "}
              <input type="number" min={0} step={1} inputMode="numeric" value={count} aria-label="Сделано, шт"
                onChange={(event) => setCount(event.target.value)} />
            </label>
            <button type="button" className="secondary" disabled={busy} onClick={() => setCount(String((typed ?? 0) + 1))}>+1</button>
            <button type="button" className="secondary" disabled={busy} onClick={() => setCount(String((typed ?? 0) + 10))}>+10</button>
            <button type="button" className="primary" disabled={busy || typed === null} onClick={record}>Записать</button>
            {planned !== null && <span className="mw-plan">из {planned}</span>}
            <span className="mw-spacer" />
            <button type="button" className="primary" disabled={busy} aria-expanded={finishing} onClick={() => { setFinishing((open) => !open); setCancelling(false); }}>Готово</button>
          </div>
          {over && <p className="warning-inline">Больше плана ({planned}) — запись принимается</p>}
          {job.progress_over_plan && !over && <p className="warning-inline">Записано больше плана</p>}
          {job.source && <p className="mw-hint">Сдать ПФ — в «Заданиях»</p>}
          {finishing && <FinishInline job={job} typed={progress} busy={busy} submit={(good, defect, reason) => void act(() => manualJobsApi.finish(job.id, {
            version: job.version, good, defect, defect_reason: reason, material: job.source ? "not_issued" : "pending",
            material_note: job.source ? SOURCE_MATERIAL : "" }), `Работа ${job.number} завершена`, "Не удалось завершить работу")} />}
          {!finishing && <p className="mw-cancel">{cancelling
            ? <CancelInline busy={busy} back={() => setCancelling(false)} submit={(reason) => void act(() => manualJobsApi.cancel(job.id, job.version, reason),
              `Работа ${job.number} отменена`, "Не удалось отменить работу")} />
            : <button type="button" className="secondary small" onClick={() => setCancelling(true)}>Отменить</button>}</p>}
        </> : <p className="mw-hint">Сделано {progress}{planned !== null ? ` из ${planned}` : ""} · только просмотр</p>}
      </section>}

      <details className="mw-fold">
        <summary>Журнал</summary>
        {!list || (!list.rows.length && !list.downtimes?.length) ? <p className="mw-hint">Сегодня на станке ничего не отмечено</p>
          : <ul className="mw-log">
            {(list.downtimes ?? []).map((item) => <li key={item.id}>Простой · {item.reason_name}{item.comment ? ` · ${item.comment}` : ""} · с {formatWallMoment(item.started_at)}</li>)}
            {list.rows.map((item) => <li key={item.id}>{item.number} · {item.status_text} · {item.source?.label || item.purpose}
              {item.status === "done" ? ` · годных ${item.good ?? 0}${item.defect ? `, брак ${item.defect}` : ""}` : item.status === "running" ? ` · сделано ${item.progress_quantity ?? 0}` : ""}
              {" "}· {formatWallMoment(item.started_at)}{item.finished_at ? `–${clock(item.finished_at)}` : ""}</li>)}
          </ul>}
      </details>
      <details className="mw-fold">
        <summary>Подключение</summary>
        <dl className="mw-facts">{([["Артикул", row.article], ["Участок", row.site], ["Операции", (row.operations ?? []).join(", ")], ["Связь", row.link.text]] as [string, string][])
          .map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || EMPTY}</dd></div>)}</dl>
      </details>
    </section>
  </PageWindow>;
}

function DowntimeForm({ reasons, busy, submit, cancel }: {
  reasons: DowntimeReasonOption[]; busy: boolean; submit: (reason: string, comment: string) => void; cancel: () => void;
}) {
  const [reason, setReason] = useState(""), [comment, setComment] = useState("");
  const chosen = reason || reasons[0]?.key || "";
  const need = chosen === OTHER_REASON && !comment.trim();
  const send = (event: FormEvent) => { event.preventDefault(); if (chosen && !need) submit(chosen, comment.trim()); };
  return <form className="mw-inline" onSubmit={send} aria-label="Простой станка">
    <label>Причина <select value={chosen} onChange={(event) => setReason(event.target.value)} disabled={!reasons.length}>
      {reasons.map((item) => <option key={item.key} value={item.key}>{item.name}</option>)}</select></label>
    <label>Комментарий <input value={comment} maxLength={300} onChange={(event) => setComment(event.target.value)} /></label>
    <button type="submit" className="primary small" disabled={busy || !chosen || need} title={need ? "Для «Другое» нужен комментарий" : undefined}>Начать простой</button>
    <button type="button" className="secondary small" onClick={cancel}>Не нужно</button>
    {need && <span className="mw-hint">Для «Другое» нужен комментарий</span>}
  </form>;
}

function FinishInline({ job, typed, busy, submit }: { job: ManualJob; typed: number; busy: boolean; submit: (good: number, defect: number, reason: string) => void }) {
  const [good, setGood] = useState(String(typed)), [defect, setDefect] = useState("0"), [reason, setReason] = useState("");
  const g = whole(good), d = whole(defect);
  const bad = g === null || d === null || (d > 0 && !reason.trim());
  const send = (event: FormEvent) => { event.preventDefault(); if (!bad) submit(g, d, reason.trim()); };
  return <form className="mw-inline" onSubmit={send} aria-label={`Завершить ${job.number}`}>
    <label>Годные <input type="number" min={0} step={1} value={good} onChange={(event) => setGood(event.target.value)} /></label>
    <label>Брак <input type="number" min={0} step={1} value={defect} onChange={(event) => setDefect(event.target.value)} /></label>
    {d !== null && d > 0 && <label>Причина <input value={reason} maxLength={300} onChange={(event) => setReason(event.target.value)} /></label>}
    <button type="submit" className="primary small" disabled={busy || bad}>Завершить работу</button>
  </form>;
}

function CancelInline({ busy, submit, back }: { busy: boolean; submit: (reason: string) => void; back: () => void }) {
  const [reason, setReason] = useState("");
  return <span className="mw-inline">
    <label>Причина отмены <input value={reason} maxLength={300} onChange={(event) => setReason(event.target.value)} /></label>
    <button type="button" className="secondary small" disabled={busy || !reason.trim()} onClick={() => submit(reason.trim())}>Отменить работу</button>
    <button type="button" className="secondary small" onClick={back}>Не нужно</button>
  </span>;
}
