import { useEffect, useRef, useState, type ReactNode } from "react";
import { failureText, productionApi, type ProductionWorkSession } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { PageWindow } from "./PageWindow";
import { formatMoment, formatNumber } from "./format";
import { machineControlApi, type CuttingMachineRun } from "./machineControlApi";
import { windowText } from "./productionGraph";
import { cuttingLabels, cuttingRows, cuttingState, cuttingLabelsPage, cuttingLabelsTitle, cuttingKindTitle, cuttingLook, machineGood, type CuttingGroup, type MarkingProgress, type CuttingLabel, type CuttingMember, type CuttingSubmission, type CuttingWorkResult } from "./cuttingGroups";
import { clock, useNow, workedSeconds } from "./productionTime";
import { DOWNTIME_REASONS, type DowntimeReason } from "./downtimeApi";
import type { MarkSerialRange } from "./markSerial";
import { hasMarkSerial } from "./markSerial";
import { openPrintDocument } from "./print";
import { useCanPrint } from "./rights";
import { MarkingPrintWindow } from "./MarkingPrintWindow";
import { MarkingOutsideWindow } from "./MarkingOutsideWindow";
import "./cuttingGroups.css";

const rowKey = (row: CuttingMember) => row.id;

export function CuttingGroupCard({ group: initialGroup, readOnly, renderInstruction, mutate, onBehalfOf = "", performerId, busy = false, activeSession, canRefreshEtk = false, master = false }: {
  group: CuttingGroup;
  readOnly: boolean;
  renderInstruction: (row: CuttingMember, close: () => void) => ReactNode;
  mutate?: (key: string, action: () => Promise<unknown>) => Promise<void>;
  onBehalfOf?: string;
  performerId?: string;
  busy?: boolean;
  activeSession?: ProductionWorkSession | null;
  /** Master only: re-pin the unstarted group to the current ETK revision. */
  canRefreshEtk?: boolean;
  /** Норму группы видит только мастер; оператору она не нужна (владелец 20.11). */
  master?: boolean;
}) {
  const [group, setGroup] = useState(initialGroup);
  useEffect(() => setGroup(initialGroup), [initialGroup]);
  const rows = cuttingRows(group);
  const [view, setView] = useState<"" | "submit" | "instruction" | "labels" | "downtime" | "remaining" | "outside">("");
  const look = cuttingLook(group);
  const canPrint = useCanPrint();
  // «Печать этикеток»: printed / remaining per КБ from the print queue; reloaded after the print window closes.
  const [progress, setProgress] = useState<MarkingProgress | null>(null);
  const [progressError, setProgressError] = useState("");
  const [progressTick, setProgressTick] = useState(0);
  useEffect(() => {
    if (look !== "labels") return;
    let live = true;
    productionApi.markingProgress(group.id).then((answer) => { if (live) { setProgress(answer); setProgressError(""); } })
      .catch((reason) => { if (live) setProgressError(failureText(reason, "Сколько этикеток напечатано — не загружено")); });
    return () => { live = false; };
  }, [look, group.id, group.version, progressTick]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const now = useNow(30_000);
  const session = group.session;
  const ownsSession = Boolean(session && (!performerId || session.operator_id === performerId));
  const disabled = busy || pending;
  const work = (action: string) => ({ onBehalfOf, key: `cut:${group.id}:${group.version}:${action}` });
  async function act(action: () => Promise<CuttingWorkResult>) {
    if (inFlight.current || busy) return;
    inFlight.current = true;
    setPending(true);
    setError("");
    const perform = async () => {
      try {
        const result = await action();
        setGroup(result.group);
        setView("");
        return result;
      } catch (reason) {
        setError(failureText(reason, "Действие группы не выполнено"));
        throw reason;
      }
    };
    try { if (mutate) await mutate(group.id, perform); else await perform(); }
    catch { /* The actionable error stays in the card and open form. */ }
    finally { inFlight.current = false; setPending(false); }
  }
  // «Готово» по строке (владелец 20.11): годные одной КБ уходят общей сдачей группы с одной строкой; брак — окно «Брак…».
  const perRow = look === "cut" || look === "mixed";
  const [rowQty, setRowQty] = useState<Record<string, string>>({});
  const rowRetry = useRef<Record<string, { payload: string; key: string }>>({});
  const left = (row: CuttingMember) => Math.max(0, Number(row.operation_quantity) - Number(row.good) - Number(row.defects));
  // Ключ с уже сданным: после «Готово» поле возвращается к остатку само.
  const qtyKey = (row: CuttingMember) => `${row.id}:${row.good}:${row.defects}`;
  const qtyOf = (row: CuttingMember) => rowQty[qtyKey(row)] ?? String(left(row));
  const qtyOk = (row: CuttingMember) => { const n = Number(qtyOf(row)); return Number.isInteger(n) && n > 0 && n <= left(row); };
  function rowDone(row: CuttingMember) {
    if (!qtyOk(row)) return;
    const result: CuttingSubmission[] = [{ member_id: row.id, good_quantity: qtyOf(row), defect_quantity: "0", defect_character: "", defect_description: "" }];
    const payload = JSON.stringify({ version: group.version, rows: result });
    if (rowRetry.current[row.id]?.payload !== payload) rowRetry.current[row.id] = { payload, key: crypto.randomUUID() };
    const key = rowRetry.current[row.id].key;
    void act(() => productionApi.submitCuttingGroup(group.id, group.version, result, { onBehalfOf, key }));
  }
  const [instructionId, setInstructionId] = useState(rows[0]?.id ?? "");
  const instruction = rows.find((row) => row.id === instructionId) ?? rows[0];
  if (!rows.length) return null;
  const done = rows.every((row) => row.state === "done");
  const progressOf = (row: CuttingMember) => progress?.rows.find((item) => item.task_id === row.task_id);
  const working = !readOnly && !done && ownsSession && session?.status === "В работе" && !group.partial_view;
  // Почему «Готово» по строке недоступно: видна не вся группа — сдают на полной карточке; группу ведёт другой — его
  // имя; своя на простое — закончить простой; иначе — начать.
  const waitHint = working ? undefined
    : group.partial_view ? "Видна не вся группа — сдают на полной карточке группы"
    : session && !ownsSession ? `Группу ведёт ${session.operator_name || "другой сотрудник"}`
      : session?.status === "Пауза" ? "Сначала закончите простой"
        : "Сначала нажмите «Начать»";
  const doneColumns: DataColumn<CuttingMember>[] = !perRow || readOnly ? [] : [
    { key: "left", title: "Осталось, шт", numeric: true, value: (row) => left(row) },
    { key: "qty", title: "Готово, шт", numeric: true, value: (row) => qtyOf(row), render: (row) => row.state === "done" ? "—"
      : <input type="number" name={`ready-${row.id}`} autoComplete="off" min="1" max={left(row)} step="1" inputMode="numeric" aria-label={`Сколько готово ${row.item}`}
        disabled={!working || disabled} value={qtyOf(row)} onChange={(event) => setRowQty((current) => ({ ...current, [qtyKey(row)]: event.target.value }))} /> },
    { key: "act", title: "", value: () => "", render: (row) => row.state === "done" ? <span className="cutting-row-done">Сдано</span>
      : <button type="button" className="primary small" disabled={!working || disabled || !qtyOk(row)} aria-label={`Готово ${row.item}`}
        title={waitHint} onClick={() => rowDone(row)}>Готово</button> },
  ];
  const kindColumns: Record<"cut" | "labels" | "tubes", DataColumn<CuttingMember>[]> = {
    cut: [
      { key: "item", title: "КБ", value: (row) => row.item, render: (row) => <b>{row.item}</b> },
      { key: "length", title: "Длина, мм", numeric: true, value: (row) => row.cut_length_mm == null ? null : Number(row.cut_length_mm) },
      { key: "blanks", title: "Шт.", numeric: true, value: (row) => Number(row.blank_quantity) },
      ...doneColumns,
    ],
    labels: [
      { key: "item", title: "КБ", value: (row) => row.item, render: (row) => <b>{row.item}</b> },
      { key: "printed", title: "Напечатано", numeric: true, value: (row) => progressOf(row)?.printed ?? null },
      { key: "remaining", title: "Осталось", numeric: true, value: (row) => progressOf(row)?.remaining ?? null },
    ],
    tubes: [],
  };
  const tubeRows = rows.flatMap((row) => row.tubes?.length
    ? row.tubes.map((tube, index) => ({ id: `${row.id}:${index}`, item: row.item, material: tube.material, length: Number(tube.length_mm), quantity: Number(tube.quantity) }))
    : [{ id: `${row.id}:-`, item: row.item, material: "—", length: null as number | null, quantity: null as number | null }]);
  const tubeColumns: DataColumn<(typeof tubeRows)[number]>[] = [
    { key: "item", title: "КБ", value: (row) => row.item, render: (row) => <b>{row.item}</b> },
    { key: "material", title: "Материал", value: (row) => row.material, grow: true },
    { key: "length", title: "Длина, мм", numeric: true, value: (row) => row.length },
    { key: "quantity", title: "Шт.", numeric: true, value: (row) => row.quantity },
  ];
  // Fixed labels can be left after every item's numbered ones are printed: the button follows label pieces, not items.
  const remainingLabels = progress?.remaining_labels ?? 0;
  const columns: DataColumn<CuttingMember>[] = [
    { key: "item", title: "КБ", value: (row) => row.item, render: (row) => <b>{row.item}</b> },
    { key: "name", title: "Изделие", value: (row) => row.name, grow: true, wrap: true },
    { key: "length", title: "Длина реза, мм", numeric: true, value: (row) => row.cut_length_mm == null ? null : Number(row.cut_length_mm) },
    { key: "assembly", title: "Сборок", numeric: true, value: (row) => row.assembly_quantity },
    { key: "blanks", title: "Заготовок", numeric: true, value: (row) => Number(row.blank_quantity) },
    { key: "quantity", title: "План ПФ, шт", numeric: true, value: (row) => Number(row.operation_quantity) },
    { key: "good", title: "Годные", numeric: true, value: (row) => Number(row.good) },
    { key: "defects", title: "Брак", numeric: true, value: (row) => Number(row.defects) },
    { key: "state", title: "Состояние", value: (row) => cuttingState[row.state] },
    ...doneColumns,
  ];
  const title = look === "mixed" ? `Резка · ${group.project} · ПФ-1 Заготовки · ${rows.length} КБ`
    : `${cuttingKindTitle[look]} · ${group.project} · ${rows.length} КБ`;
  return <section className={`mo-card cutting-group${done ? " done" : ""}`} aria-label={title}>
    <div className="mo-h">
      <div className="mo-name"><h2>{title}</h2><small>{group.number} · {group.site}</small></div>
      <div className="mo-side">
        {!readOnly && !done && <>
          {!session && <button type="button" className="primary" disabled={disabled || Boolean(activeSession) || group.partial_view}
            onClick={() => void act(() => productionApi.startCuttingGroup(group.id, group.version, work("start")))}>Начать</button>}
          <button type="button" className="secondary" disabled={disabled || !ownsSession || session?.status !== "В работе" || group.partial_view}
            aria-haspopup="dialog" onClick={() => { setError(""); setView("submit"); }}>{perRow ? "Брак…" : "Сдать…"}</button>
          {group.downtime ? <button type="button" className="secondary" disabled={disabled || !ownsSession || group.partial_view}
            onClick={() => void act(() => productionApi.cuttingDowntime(group.id, group.version, "stop", {}, work("downtime-stop")))}>Закончить простой</button>
            : <button type="button" className="secondary" disabled={disabled || !ownsSession || group.partial_view} aria-haspopup="dialog"
              onClick={() => { setError(""); setView("downtime"); }}>Простой</button>}
        </>}
        {canRefreshEtk && !readOnly && group.state === "open" && group.etk_stale && <button type="button" className="secondary" disabled={disabled}
          onClick={() => void act(async () => ({ ...(await productionApi.refreshCuttingGroupEtk(group.id, group.version)), outputs: [] }))}>Обновить из ЭТК</button>}
        <button type="button" className="secondary" aria-haspopup="dialog" onClick={() => setView("instruction")}>Инструкция</button>
        {look === "labels" && !readOnly && <button type="button" className="primary" disabled={disabled || group.partial_view || !progress || remainingLabels <= 0}
          aria-haspopup="dialog" onClick={() => setView("remaining")}>Печатать оставшиеся</button>}
        {look === "labels" && !readOnly && canPrint && <button type="button" className="secondary" disabled={disabled || group.partial_view}
          aria-haspopup="dialog" onClick={() => setView("outside")}>Отметить напечатанные вне ERP</button>}
        {(look === "mixed" || look === "labels") && <button type="button" className="secondary" aria-haspopup="dialog" onClick={() => setView("labels")}>Этикетки группы</button>}
      </div>
    </div>
    <div className="mo-s mo-plan">План {windowText(group.planned_start, group.planned_finish)} {master && <> · норма {formatNumber(group.norm_seconds / 60)} мин</>}</div>
    {session && <div className="mo-fact" role="status">{session.status === "Пауза" ? "На простое" : "В работе"} · время {clock(workedSeconds(session, now))}
      {group.downtime && <> · {group.downtime.reason_name}{group.downtime.comment ? ` · ${group.downtime.comment}` : ""}</>}</div>}
    {group.partial_view && <div className="warning-inline">Показаны только назначенные вам КБ. Этикетки — для показанных КБ.</div>}
    {error && !["submit", "downtime"].includes(view) && <div className="error-banner" role="alert">{error}</div>}
    {progressError && look === "labels" && <div className="error-banner" role="alert">{progressError}</div>}
    {look === "tubes"
      ? <DataTable rows={tubeRows} columns={tubeColumns} rowKey={(row) => row.id} label="Трубки группы" compactBelow={Infinity} pageSize={1000} phoneCards={false} />
      : <DataTable rows={rows} columns={look === "mixed" ? columns : kindColumns[look]} rowKey={rowKey} label={look === "labels" ? "Этикетки по КБ группы" : "КБ группы резки"} compactBelow={Infinity} pageSize={1000} phoneCards={false} />}
    {view === "submit" && <GroupSubmit group={group} busy={disabled} error={error} close={() => setView("")}
      submit={(values, key) => act(() => productionApi.submitCuttingGroup(group.id, group.version, values, { onBehalfOf, key }))} />}
    {view === "downtime" && <GroupDowntime busy={disabled} error={error} close={() => setView("")}
      submit={(reason, comment, key) => act(() => productionApi.cuttingDowntime(group.id, group.version, "start", { reason, comment }, { onBehalfOf, key }))} />}
    {view === "labels" && <GroupLabels key={`${group.id}:${group.version}`} group={group} close={() => setView("")} />}
    {view === "remaining" && <MarkingPrintWindow source={{ group_id: group.id, version: group.version }} remaining title={`${group.project} · ${group.number}`}
      close={() => { setView(""); setProgressTick((tick) => tick + 1); }} />}
    {view === "outside" && <MarkingOutsideWindow group={group} close={() => setView("")} onChange={() => setProgressTick((tick) => tick + 1)} />}
    {view === "instruction" && instruction && <PageWindow label="Инструкция группы резки" width={1400} close={() => setView("")}>
      <label className="cutting-instruction-picker">КБ<select value={instruction.id} onChange={(event) => setInstructionId(event.target.value)}>
        {rows.map((row) => <option key={row.id} value={row.id}>{row.item} · {row.name}</option>)}
      </select></label>
      {renderInstruction(instruction, () => setView(""))}
    </PageWindow>}
  </section>;
}

function GroupSubmit({ group, close, submit, busy, error }: { group: CuttingGroup; close: () => void; busy: boolean; error: string;
  submit: (rows: CuttingSubmission[], key: string) => Promise<void> }) {
  const rows = cuttingRows(group);
  const retry = useRef({ payload: "", key: crypto.randomUUID() });
  const [values, setValues] = useState(() => Object.fromEntries(rows.map((row) => [row.id, {
    good: String(Math.max(0, Number(row.operation_quantity) - Number(row.good) - Number(row.defects))), defects: "0", character: "", description: "",
  }])));
  // Pieces counted by the machine card («Выполнение задания» on ОБ.019) fill «Годные» of the КБ of that length once.
  const [machineRuns, setMachineRuns] = useState<CuttingMachineRun[]>([]);
  useEffect(() => {
    let alive = true;
    machineControlApi.cuttingRuns(group.id).then((answer) => {
      if (!alive || !Array.isArray(answer?.runs)) return;
      setMachineRuns(answer.runs);
      const filled = Object.assign({}, ...answer.runs.filter((item) => item.count !== null)
        .map((item) => machineGood(rows, item.length_mm, item.count ?? 0)));
      if (Object.keys(filled).length) {
        setValues((current) => Object.fromEntries(Object.entries(current).map(([id, value]) =>
          [id, id in filled ? { ...value, good: filled[id] } : value])));
      }
    }).catch(() => { /* no machine count: the plan stays as before */ });
    return () => { alive = false; };
  }, [group.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const valid = rows.every((row) => {
    const v = values[row.id];
    return v.good !== "" && v.defects !== "" && Number.isInteger(Number(v.good)) && Number.isInteger(Number(v.defects)) && Number(v.good) >= 0 && Number(v.defects) >= 0
      && Number(v.good) + Number(v.defects) <= Number(row.operation_quantity) - Number(row.good) - Number(row.defects)
      && (Number(v.defects) === 0 || Boolean(v.character.trim() && v.description.trim()));
  }) && rows.some((row) => Number(values[row.id].good) + Number(values[row.id].defects) > 0);
  function save() {
    const result = rows.map((row) => ({ member_id: row.id, good_quantity: values[row.id].good, defect_quantity: values[row.id].defects,
      defect_character: values[row.id].character.trim(), defect_description: values[row.id].description.trim() }));
    const payload = JSON.stringify({ version: group.version, rows: result });
    if (retry.current.payload !== payload) retry.current = { payload, key: crypto.randomUUID() };
    void submit(result, retry.current.key);
  }
  const field = (row: CuttingMember, kind: "good" | "defects") => <input type="number" name={`${kind}-${row.id}`} autoComplete="off" min="0" step="1" inputMode="numeric"
    disabled={busy} aria-label={`${kind === "good" ? "Годные" : "Брак"} ${row.item}`} value={values[row.id][kind]}
    onChange={(event) => setValues((current) => ({ ...current, [row.id]: { ...current[row.id], [kind]: event.target.value } }))} />;
  return <PageWindow label="Сдать группу резки" width={1280} close={() => { if (!busy) close(); }}>
    <div className="panel cutting-submit">
      <h2>Сдать группу · {group.project} · {rows.length} КБ</h2>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {machineRuns.map((item) => <p key={item.run_id} className={item.count === null || item.unknown ? "warning-inline" : "cutting-machine-count"} role="status">
        {item.count === null
          ? `${item.inventory}: счётчик станка сброшен — введите годные длины ${item.length_mm.replace(".", ",")} мм вручную`
          : `Счётчик ${item.inventory}: ${formatNumber(item.count)} шт провода ${item.length_mm.replace(".", ",")} мм`
            + `${item.runs > 1 ? `, прогонов: ${item.runs}` : ""} (последний — ${formatMoment(item.closed_at)}) — годные заполнены, проверьте и укажите брак`
            + `${item.unknown ? "; часть прогонов без счётчика станка — добавьте их вручную" : ""}`}
      </p>)}
      <DataTable rows={rows} rowKey={rowKey} label="Сдача по КБ" compactBelow={Infinity} pageSize={1000} columns={[
        { key: "item", title: "КБ", value: (row) => row.item },
        { key: "name", title: "Изделие", value: (row) => row.name, grow: true },
        { key: "quantity", title: "План ПФ, шт", numeric: true, value: (row) => Number(row.operation_quantity) },
        { key: "good", title: "Годные, шт ПФ", numeric: true, value: (row) => values[row.id].good, render: (row) => field(row, "good") },
        { key: "defects", title: "Брак, шт ПФ", numeric: true, value: (row) => values[row.id].defects, render: (row) => field(row, "defects") },
        { key: "reason", title: "Характер и описание брака", value: (row) => values[row.id].description, render: (row) => Number(values[row.id].defects) > 0 ? <div className="cutting-defect">
          <input disabled={busy} name={`character-${row.id}`} autoComplete="off" aria-label={`Характер брака ${row.item}`} placeholder="Характер…" maxLength={255} value={values[row.id].character}
            onChange={(event) => setValues((current) => ({ ...current, [row.id]: { ...current[row.id], character: event.target.value } }))} />
          <input disabled={busy} name={`description-${row.id}`} autoComplete="off" aria-label={`Описание брака ${row.item}`} placeholder="Описание…" maxLength={2000} value={values[row.id].description}
            onChange={(event) => setValues((current) => ({ ...current, [row.id]: { ...current[row.id], description: event.target.value } }))} />
        </div> : "—" },
      ]} />
      <div className="form-actions"><button type="button" className="secondary" disabled={busy} onClick={close}>Закрыть</button>
        <button type="button" className="primary" disabled={busy || !valid} onClick={save}>{busy ? "Сохранение…" : "Сдать группу"}</button></div>
    </div>
  </PageWindow>;
}

function GroupDowntime({ busy, error, close, submit }: { busy: boolean; error: string; close: () => void;
  submit: (reason: DowntimeReason, comment: string, key: string) => Promise<void> }) {
  const [reason, setReason] = useState<DowntimeReason>("no_material");
  const [comment, setComment] = useState("");
  const retry = useRef({ payload: "", key: crypto.randomUUID() });
  function save() {
    const payload = JSON.stringify({ reason, comment: comment.trim() });
    if (retry.current.payload !== payload) retry.current = { payload, key: crypto.randomUUID() };
    void submit(reason, comment.trim(), retry.current.key);
  }
  return <PageWindow label="Простой группы резки" width={560} close={() => { if (!busy) close(); }}>
    <div className="panel"><h2>Простой группы резки</h2>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <label>Причина<select name="reason" autoComplete="off" disabled={busy} value={reason} onChange={(event) => setReason(event.target.value as DowntimeReason)}>
        {DOWNTIME_REASONS.map((item) => <option key={item.key} value={item.key}>{item.name}</option>)}
      </select></label>
      <label>Комментарий<textarea name="comment" autoComplete="off" disabled={busy} maxLength={500} value={comment} onChange={(event) => setComment(event.target.value)} /></label>
      <div className="form-actions"><button className="secondary" disabled={busy} onClick={close}>Закрыть</button>
        <button className="primary" disabled={busy || (reason === "other" && !comment.trim())} onClick={save}>Начать простой</button></div>
    </div>
  </PageWindow>;
}

function GroupLabels({ group, close }: { group: CuttingGroup; close: () => void }) {
  const [printing, setPrinting] = useState(false);
  const canPrint = useCanPrint();
  const [labels, setLabels] = useState<CuttingLabel[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    async function load() {
      const rows = cuttingRows(group);
      const ranges: Record<string, MarkSerialRange> = {};
      // ETK reads can register label issuance. Fetch only on demand, with bounded concurrency.
      const numbered = rows.filter((row) => row.labels.some((mark) => hasMarkSerial(String(mark.text ?? ""))));
      for (let i = 0; i < numbered.length; i += 4) {
        if (!live) return;
        await Promise.all(numbered.slice(i, i + 4).map(async (row) => {
          const result = await productionApi.operationEtk(row.task_id, row.operation_id);
          if (result.serial) ranges[row.id] = result.serial;
        }));
      }
      const result = cuttingLabels(group, ranges);
      if (live) setLabels(result);
    }
    void load().catch((reason) => { if (live) setError(failureText(reason, "Этикетки группы не загружены")); });
    return () => { live = false; };
  }, [group]);
  return <PageWindow label="Этикетки группы" width={1280} close={close}>
    <div className="panel cutting-labels">
      <div className="manual-stock-head"><h2>Этикетки группы · {group.project}</h2>
        {canPrint && <button type="button" className="primary" disabled={!labels?.length || Boolean(error) || group.partial_view} aria-haspopup="dialog" onClick={() => setPrinting(true)}>Печатать все</button>}
        {canPrint && <button type="button" className="secondary" disabled={!labels?.length || Boolean(error)} onClick={() => labels && openPrintDocument("Этикетки группы", [cuttingLabelsPage(group, labels)])}>Список / PDF</button>}
        <button type="button" className="secondary" onClick={close}>Закрыть</button>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!labels && !error && <div className="empty" role="status">Загрузка…</div>}
      {labels && <>
        <p className="num" role="status">{cuttingLabelsTitle(labels)}</p>
        <DataTable rows={labels} rowKey={(row) => row.id} label="Общий список этикеток" compactBelow={Infinity} pageSize={10000} emptyText="В выданных инструкциях группы нет этикеток." columns={[
          { key: "item", title: "КБ", value: (row) => row.item },
          { key: "text", title: "Надпись", value: (row) => row.text, grow: true, wrap: true },
          { key: "material", title: "Материал", value: (row) => row.material },
          { key: "place", title: "Место", value: (row) => row.place },
          { key: "length", title: "Длина, мм", value: (row) => row.length, align: "right" },
          { key: "quantity", title: "Количество", numeric: true, value: (row) => row.quantity, render: (row) => `× ${formatNumber(row.quantity)}` },
        ]} />
      </>}
      {printing && <MarkingPrintWindow source={{ group_id: group.id, version: group.version }} title={`${group.project} · ${group.number}`} close={() => setPrinting(false)} />}
    </div>
  </PageWindow>;
}
