import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { failureText } from "./api";
import { EMPTY, formatMoment, formatNumber } from "./format";
import {
  machineControlApi, type CorrectionCase, type MachineCommand, type MachineControl, type SettingField, type TransferChoice,
  type TransferPlan,
} from "./machineControlApi";
import { usePolled, type Ob019Live } from "./machinesApi";
import type { ManualJob } from "./manualJobsApi";
import { NEW_MANUAL, Ob019RunJob, manualKey } from "./Ob019RunJob";
import { Ob019WireDrawing } from "./Ob019WireDrawing";

/** «Настройки» карточки ОБ.019: сверху «Выполнение задания» (Ob019RunJob.tsx) — выбранное задание (резка из ЭТК или
 *  ручная работа) заполняет чертёж; чертёж провода с полями длины и зачистки на своих местах, рядом — значение станка
 *  (владелец 19.11.2026), под ним тираж, пучок, режим; «Передать на станок» пишет задание и начинает его счёт; без
 *  задания — «Записать на станок» (просто настройки). Редкие страницы пульта — в «Остальных параметрах»; правка после
 *  пробы. Запись — только когда станок ждёт пуска или тираж готов, пульт станка на ПК закрыт. */

const STATES: Record<number, string> = {
  100: "Не сброшен", 101: "Идёт сброс", 102: "Готов, ждёт пуска", 103: "Работает", 104: "Пауза", 105: "Аварийный стоп",
  106: "Пучок готов", 107: "Тираж готов",
};
const MODES: [string, string][] = [["0", "Пошаговый"], ["1", "Один цикл"], ["2", "Автомат"]];
/** Переключатель страницы пульта (бит 0 слова): сервер меняет только этот бит. */
const SWITCH: [string, string][] = [["1", "Вкл"], ["0", "Выкл"]];
const RUNNER_OUTDATED = "Служба записи на ПК станка устарела — обновите (нужна 1.2.0)";
const CASES: [CorrectionCase, string][] = [
  ["machine", "Станок режет не так"], ["etk", "Размер в ЭТК неверный"], ["both", "И станок, и ЭТК"],
];
/** Поля на чертеже и в строке под ним; остальные — в «Остальных параметрах». */
const DRAWN = ["length_mm", "strip_front_mm", "pull_front_mm", "strip_back_mm", "pull_back_mm", "diameter_mm", "knife_back_mm"];
const JOB_ROW = ["total_set", "batch_set", "mode"];
const ON_TOP = new Set([...DRAWN, ...JOB_ROW]);

function stateText(state: number | null): string {
  return state === null ? "неизвестно" : STATES[state] ?? `код ${state}`;
}

/** Число как на пульте станка, без единицы: мм — два знака, секунды — три. */
function bare(value: number, unit: string): string {
  const digits = unit === "мм" ? 2 : unit === "с" ? 3 : 0;
  return formatNumber(value, { digits, minDigits: digits });
}

function shown(field: SettingField): string {
  if (field.value === null || field.value === undefined) return EMPTY;
  if (field.kind === "mode") return field.text ?? String(field.value);
  if (typeof field.value === "boolean") return field.value ? "Вкл" : "Выкл";
  if (typeof field.value === "string") return field.value || EMPTY;
  const number = bare(field.value, field.unit);
  return field.unit ? `${number} ${field.unit}` : number;
}

function decimal(text: string): number | null {
  const value = Number(text.replace(",", ".").replace(/\s/g, ""));
  return text.trim() && Number.isFinite(value) ? value : null;
}

function comma(value: number): string {
  return value.toFixed(2).replace(".", ",");
}

/** Введённое отличается от того, что сейчас на станке (пусто — «не менять», не отличается). */
export function enteredDiffers(field: SettingField | undefined, entered: string): boolean {
  if (!entered.trim()) return false;
  if (!field || field.value === null || field.value === undefined) return true;
  if (field.kind === "mode") return String(field.value) !== entered.trim();
  if (typeof field.value === "string") return field.value !== entered.split(/\s+/).filter(Boolean).join(" ");
  if (typeof field.value === "boolean") return (entered.trim() === "1") !== field.value;
  const number = decimal(entered);
  return number === null || Math.abs(number - field.value) >= 0.005;
}

/** Что пишет «Передать на станок» (сервер: длины с поправкой станка, тираж, заказ, изделие) — для заполнения чертежа. */
function planValues(plan: TransferPlan): Record<string, string> {
  const values: Record<string, string> = {};
  for (const row of plan.rows) if (row.value !== null) values[row.key] = row.value;
  for (const [key, value] of Object.entries(plan.job)) if (value !== "" && value !== null) values[key] = String(value);
  return values;
}

function sameValues(a: Record<string, string>, b: Record<string, string>): boolean {
  const keys = (values: Record<string, string>) => Object.keys(values).filter((key) => values[key].trim() !== "").sort();
  const left = keys(a), right = keys(b);
  return left.length === right.length && left.every((key, index) => key === right[index] && a[key].trim() === b[key].trim());
}

function CommandLine({ command }: { command: MachineCommand }) {
  return <li>
    <span className={`tone-${command.tone}`}>{command.status_text}</span>
    <span>{command.summary}</span>
    {(command.state_before || command.state_after) && <span>{command.state_before ?? EMPTY} → {command.state_after ?? EMPTY}</span>}
    <span className="machine-muted">{command.created_by} · {formatMoment(command.created_at)}</span>
    {command.error && <span className="tone-danger">{command.error}</span>}
  </li>;
}

/** Подтверждение записи: что изменится и в каком состоянии станок сейчас. */
function Confirm({ state, lines, busy, onYes, onNo }: {
  state: number | null; lines: string[]; busy: boolean; onYes: () => void; onNo: () => void;
}) {
  return <div className="machine-confirm" role="alertdialog" aria-label="Подтверждение записи на станок">
    <p><strong>Станок: {stateText(state)}.</strong> Записать на станок:</p>
    <ul>{lines.map((line) => <li key={line}>{line}</li>)}</ul>
    <div className="machine-actions">
      <button className="primary small" disabled={busy} onClick={onYes}>Подтвердить запись</button>
      <button className="secondary small" disabled={busy} onClick={onNo}>Отмена</button>
    </div>
  </div>;
}

const RUN_FAST = ["writing", "running", "ready"];

export function Ob019SettingsPanel({ inventory, equipmentId, live = null, old = false }: {
  inventory: string; equipmentId?: string; live?: Ob019Live | null; old?: boolean;
}) {
  const loadWindow = useCallback((signal: AbortSignal) => machineControlApi.window(inventory, signal), [inventory]);
  const loadControl = useCallback((signal: AbortSignal) => machineControlApi.control(inventory, signal), [inventory]);
  const { data: card, error: cardError } = usePolled(loadWindow, undefined, "Не удалось загрузить настройки станка");
  const { data: control, error: controlError } = usePolled(loadControl, undefined, "Не удалось загрузить запись на станок");
  const loadRun = useCallback((signal: AbortSignal) => machineControlApi.run(inventory, signal), [inventory]);
  const [runFast, setRunFast] = useState(false);
  const { data: runState, reload: reloadRun } = usePolled(loadRun, runFast ? 1000 : 3000, "Не удалось загрузить задание станка");
  const fastNow = !!runState?.run && RUN_FAST.includes(runState.run.stage);
  if (fastNow !== runFast) setRunFast(fastNow);
  // A manual job just started here: picked at once, before the next poll brings it.
  const [started, setStarted] = useState<ManualJob | null>(null);
  const [group, setGroup] = useState(0);
  const [more, setMore] = useState(false);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [confirming, setConfirming] = useState<"" | "settings" | "transfer">("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  const [choices, setChoices] = useState<TransferChoice[] | null>(null);
  const [choice, setChoice] = useState("");
  const [plan, setPlan] = useState<TransferPlan | null>(null);

  useEffect(() => {
    let alive = true;
    machineControlApi.choices(inventory).then((answer) => { if (alive) setChoices(answer.choices); })
      .catch((reason) => { if (alive) setMessage({ tone: "danger", text: failureText(reason, "Не удалось загрузить задания резки") }); });
    return () => { alive = false; };
  }, [inventory]);

  const picked = choices?.find((item) => item.key === choice) ?? null;
  const manualJob = [runState?.manual, started].find((job) => job && choice === manualKey(job.id)) ?? null;
  const sameRun = runState?.run && !["closed", "failed", "moved", "reset", "nolink"].includes(runState.run.stage) ? runState.run : null;
  const manualDone = manualJob && sameRun?.kind === "manual" && sameRun.job_id === manualJob.id ? sameRun.done ?? 0 : 0;
  /** Уже сделано по станку в идущем прогоне этого же задания и его план — для строки подтверждения. */
  const doneOf = (): [number, number] | null => plan?.done ? [plan.done, plan.planned ?? plan.quantity + plan.done]
    : manualJob && manualDone ? [manualDone, manualJob.quantity_planned ?? manualDone] : null;
  // A manual job fills the drawing with its length and quantity; the rest stays as on the machine or as typed.
  useEffect(() => {
    if (!manualJob) return;
    // Written again after part of it was made: only the rest (owner review 19.11 — never the full plan again).
    const rest = manualJob.quantity_planned ? Math.max(manualJob.quantity_planned - manualDone, 0) : 0;
    setEdits((values) => ({ ...(manualJob.length_mm ? { length_mm: manualJob.length_mm } : {}),
      ...(rest ? { total_set: String(rest) } : {}), ...values }));
  }, [manualJob?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  // Fields typed by hand since the plan request started: a late plan must not overwrite them.
  const touched = useRef(new Set<string>());
  useEffect(() => {
    setPlan(null);
    touched.current = new Set();
    if (!picked) return;
    let alive = true;
    machineControlApi.plan(inventory, picked.group_id, picked.length_mm).then((answer) => {
      if (!alive) return;
      setPlan(answer);
      // «Передать на станок из ЭТК» заполняет чертёж тем, что будет записано; набранное за время загрузки остаётся.
      setEdits((values) => ({ ...planValues(answer),
        ...Object.fromEntries(Object.entries(values).filter(([key]) => touched.current.has(key))) }));
    }).catch((reason) => { if (alive) setMessage({ tone: "danger", text: failureText(reason, "Не удалось посчитать значения") }); });
    return () => { alive = false; };
  }, [inventory, picked?.group_id, picked?.length_mm]); // eslint-disable-line react-hooks/exhaustive-deps

  const fields = useMemo(() => new Map((card?.settings ?? []).flatMap((item) => item.fields)
    .filter((field) => field.key).map((field) => [field.key as string, field])), [card]);
  const middle = useMemo(() => (card?.settings ?? []).find((item) => item.title === "Средняя зачистка")
    ?.fields.find((field) => field.kind === "bit0")?.value === true, [card]);
  const changes = Object.entries(edits).filter(([key, value]) => fields.has(key) && value.trim() !== "");
  const hiddenChanges = changes.filter(([key, value]) => !ON_TOP.has(key) && enteredDiffers(fields.get(key), value)).length;
  const canWrite = !!control && control.can_edit && !control.problem;
  // Старая служба записи на ПК станка: остальные страницы пульта и переключатели пока не пишутся.
  const blocked = useMemo(() => new Set(control?.runner_outdated ? control.runner_keys ?? [] : []), [control]);
  const blockedChanges = changes.filter(([key]) => blocked.has(key)).length;
  // Значения ЭТК не тронуты — пишем тем же путём «Передать на станок» (сервер сам пересчитает их из ЭТК).
  const asTransfer = !!plan && sameValues(edits, planValues(plan));
  // A job whose ETK lacks a size is written only when the operator has typed that size by hand.
  const unfilled = plan ? plan.blocking.filter((label) => {
    const row = plan.rows.find((item) => item.label === label);
    return !row || !(edits[row.key] ?? "").trim();
  }) : [];
  /** Набранное вручную: не пусто и не то, что подставило задание из ЭТК. */
  const handEdits = () => {
    const fromPlan = plan ? planValues(plan) : {};
    return Object.entries(edits).some(([key, value]) => value.trim() !== "" && value.trim() !== (fromPlan[key] ?? "").trim());
  };
  const pickJob = (key: string) => {
    if (key === choice) return;
    if (key !== NEW_MANUAL && handEdits() && !window.confirm("Заменить введённые значения?")) return;
    setChoice(key);
    if (key !== NEW_MANUAL) setEdits({});
    setConfirming("");
  };

  const run = async (action: () => Promise<MachineCommand>, done: string) => {
    setBusy(true);
    setMessage(null);
    try {
      const command = await action();
      setMessage({ tone: "ok", text: `${done}: ${command.status_text.toLowerCase()}` });
      setConfirming("");
      return true;
    } catch (reason) {
      setMessage({ tone: "danger", text: failureText(reason, "Не удалось записать на станок") });
      return false;
    } finally {
      setBusy(false);
    }
  };

  const reset = () => { setEdits({}); setChoice(""); setConfirming(""); };
  const writeSettings = async () => {
    const values = Object.fromEntries(changes.map(([key, value]) => [key, value]));
    if (await run(() => machineControlApi.write(inventory, values, control?.state ?? null), "Запись настроек")) setEdits({});
  };
  // «Передать на станок» of a picked job starts its run (counted by the machine from the verified write). The picked
  // job stays after it: «Правка после пробы» corrects its ETK.
  const values = () => Object.fromEntries(changes.map(([key, value]) => [key, value]));
  const transfer = async () => {
    const body = manualJob ? { kind: "manual" as const, job_id: manualJob.id, values: values() }
      : plan ? { kind: "cutting" as const, group_id: plan.group_id, length_mm: picked?.length_mm ?? plan.length_mm,
        ...(asTransfer ? {} : { values: values() }) } : null;
    if (body && await run(() => machineControlApi.startRun(inventory, body, control?.state ?? null), "Передать на станок")) reloadRun();
  };
  const tirazhLine = (pieces: string) => {
    const before = doneOf();
    return `Тираж: ${pieces} шт в этом задании${before ? ` (уже сделано ${before[0]} из ${before[1]})` : ""}`
      + ` — цель станка = счётчик + ${pieces}`;
  };
  const changeLines = () => changes.map(([key, value]) => {
    const field = fields.get(key);
    if (key === "total_set") return tirazhLine(value);
    const after = field?.kind === "mode" ? MODES.find(([code]) => code === value)?.[1] ?? value
      : field?.kind === "bit0" ? SWITCH.find(([code]) => code === value)?.[1] ?? value : value || "пусто";
    return `${field?.label ?? key}: ${field ? shown(field) : EMPTY} → ${after}${field?.unit && field.kind !== "mode" ? ` ${field.unit}` : ""}`;
  });
  const edit = (key: string, value: string) => {
    touched.current.add(key);
    setEdits((values) => ({ ...values, [key]: value }));
    setConfirming("");
  };

  const error = cardError || controlError;
  const groups = (card?.settings ?? []).map((item) => ({ ...item, fields: item.fields.filter((field) => !field.key || !ON_TOP.has(field.key)) }))
    .filter((item) => item.fields.length > 0);
  const current = groups[Math.min(group, Math.max(groups.length - 1, 0))];
  const lastCommand = control?.commands[0];
  const size = (key: string) => {
    const entered = decimal(edits[key] ?? "");
    const value = fields.get(key)?.value;
    return entered ?? (typeof value === "number" ? value : null);
  };

  /** Поле значения: подпись, ввод, рядом — что сейчас на станке; отличие подсвечено. */
  const input = (key: string, label: string, note = "", own?: { live: string; diff: boolean }) => {
    const field = fields.get(key);
    const value = edits[key] ?? "";
    const diff = own ? own.diff : enteredDiffers(field, value);
    const editable = !!field && !!control?.can_edit;
    const aria = `${field?.label ?? label}: новое значение`;
    return <label key={key} className={`machine-input${diff ? " is-diff" : ""}`}>
      <span className="machine-input-label">{label}</span>
      {field?.kind === "mode"
        ? <select aria-label={aria} value={value} disabled={!editable} onChange={(event) => edit(key, event.target.value)}>
          <option value="">Не менять</option>
          {MODES.map(([code, text]) => <option key={code} value={code}>{text}</option>)}
        </select>
        : <span className="machine-input-box">
          <input aria-label={aria} value={value} disabled={!editable} inputMode="decimal" maxLength={12} autoComplete="off"            onChange={(event) => edit(key, event.target.value)} />
          {field?.unit && <span className="machine-input-unit">{field.unit}</span>}
        </span>}
      <span className={`machine-input-live${old ? " is-old" : ""}`} title={diff ? "Введённое отличается от значения на станке" : undefined}>
        {diff ? "≠ " : ""}{own ? own.live : <>на станке {field?.read ? shown(field) : EMPTY}{note}</>}
      </span>
    </label>;
  };

  const job = live?.job;
  /** «Тираж» — штук в этом задании; станок сравнивает свою цель D22 с накопленным счётчиком D649, ERP пишет
   *  D22 = D649 + N (сервер, production/machine_writes.py). Под полем — сырые значения станка. */
  const tirazh = () => {
    const counter = typeof job?.total_finished === "number" ? job.total_finished : null;
    const target = typeof job?.total_set === "number" ? job.total_set : null;
    const entered = (edits.total_set ?? "").trim();
    const left = counter !== null && target !== null ? target - counter : null;
    return { live: `счётчик станка ${counter === null ? EMPTY : formatNumber(counter)} / цель ${target === null ? EMPTY : formatNumber(target)}`,
      diff: entered !== "" && (left === null || Number(entered) !== left) };
  };
  return <section className="machine-settings" aria-label="Настройки станка">
    {error && <div className="error-banner" role="alert">{error}</div>}
    <Ob019RunJob inventory={inventory} equipmentId={equipmentId} state={runState} live={live} choices={choices}
      choice={choice} onPick={pickJob} reload={reloadRun}
      onStarted={(job) => { setStarted(job); setChoice(manualKey(job.id)); setEdits({}); }} />
    <Ob019WireDrawing middle={middle} field={input}
      sizes={{ strip_front: size("strip_front_mm"), pull_front: size("pull_front_mm"), strip_back: size("strip_back_mm"),
        pull_back: size("pull_back_mm") }} />

    <div className="machine-job-row">
      {input("total_set", "Тираж (штук в этом задании)", "", tirazh())}
      {input("batch_set", "Пучок", typeof job?.batch_finished === "number" ? ` · сделано ${formatNumber(job.batch_finished)}` : "")}
      {input("mode", "Режим")}
      {(picked || manualJob) && <p className="machine-input-live machine-run-order">
        {picked && !plan ? "Загрузка…" : plan ? `На станок: заказ ${plan.job.order_number || EMPTY} · изделие ${plan.job.product || EMPTY}`
          : manualJob ? `На станок: заказ ${manualJob.plc_number}` : ""}
      </p>}
    </div>
    {plan && <p className="machine-plan-note machine-muted">
      {[...plan.rows.filter((row) => row.value !== null && row.offset !== "0,00").map((row) => `${row.label}: ЭТК ${row.nominal ?? EMPTY}, поправка станка ${row.offset}`),
        ...plan.rows.filter((row) => row.value === null).map((row) => `${row.label} — остаётся как на станке`)].join(" · ")}
    </p>}
    {plan && plan.blocking.length > 0 && <p className="warning-inline" role="status">
      {unfilled.length
        ? `В ЭТК не задано: ${unfilled.join(", ")} — введите на чертеже или выберите другое задание`
        : `В ЭТК не задано: ${plan.blocking.join(", ")} — введено вручную`}
    </p>}

    {control && <p className={control.problem ? "warning-inline" : "machine-muted"} role="status">
      {control.problem || `Можно записывать: ${stateText(control.state).toLowerCase()}, пульт станка на ПК закрыт`}
      {!control.can_edit && " · запись вам не разрешена"}
    </p>}
    {confirming === "transfer" && (plan || manualJob)
      ? <Confirm state={control?.state ?? null} busy={busy} onNo={() => setConfirming("")} onYes={() => void transfer()}
        lines={plan && asTransfer
          ? [...plan.rows.filter((row) => row.value !== null).map((row) => `${row.label}: ${row.value} мм`), tirazhLine(String(plan.quantity))]
          : [...changeLines(), ...(manualJob ? [`Заказ: ${manualJob.plc_number}`] : [])]} />
      : confirming === "settings"
        ? <Confirm state={control?.state ?? null} busy={busy} onNo={() => setConfirming("")} onYes={() => void writeSettings()}
          lines={changeLines()} />
        : <div className="machine-actions">
          {asTransfer
            ? <button className="primary" disabled={!canWrite || busy || plan!.blocking.length > 0}
              onClick={() => setConfirming("transfer")}>Передать на станок</button>
            : picked || manualJob
            ? <button className="primary" disabled={!canWrite || busy || (!manualJob && changes.length === 0) || unfilled.length > 0
              || blockedChanges > 0 || (!!picked && !plan)}
              title={blockedChanges ? RUNNER_OUTDATED : unfilled.length ? `В ЭТК не задано: ${unfilled.join(", ")}` : undefined}
              onClick={() => setConfirming("transfer")}>{changes.length ? `Передать на станок (${changes.length})` : "Передать на станок"}</button>
            : <button className="primary" disabled={!canWrite || busy || changes.length === 0 || unfilled.length > 0 || blockedChanges > 0}
              title={blockedChanges ? RUNNER_OUTDATED : unfilled.length ? `В ЭТК не задано: ${unfilled.join(", ")}` : undefined}
              onClick={() => setConfirming("settings")}>{changes.length ? `Записать на станок (${changes.length})` : "Записать на станок"}</button>}
          {(changes.length > 0 || choice) && <button className="secondary" disabled={busy} onClick={reset}>Сбросить</button>}
        </div>}
    {message && <div className={message.tone === "ok" ? "success-banner" : "error-banner"} role="status">{message.text}</div>}
    {lastCommand && <ul className="machine-commands" aria-label="Последние записи">
      {control!.commands.slice(0, 3).map((command) => <CommandLine key={command.id} command={command} />)}
    </ul>}

    {groups.length > 0 && <div className="machine-block machine-more">
      <button type="button" className="machine-more-toggle" aria-expanded={more} onClick={() => setMore(!more)}>
        <span aria-hidden="true">{more ? "▾" : "▸"}</span> Остальные параметры
        <span className="machine-muted">{hiddenChanges ? ` · изменено ${hiddenChanges}` : ` · ${groups.map((item) => item.title).join(", ")}`}</span>
      </button>
      {more && <>
        {blocked.size > 0 && <p className="warning-inline" role="status">{RUNNER_OUTDATED} — переключатели и параметры
          остальных страниц пока только читаются</p>}
        <div className="seg-tabs" role="tablist" aria-label="Страницы пульта">
          {groups.map((item, index) => <button key={item.title} role="tab" aria-selected={index === group}
            className={index === group ? "on" : undefined} onClick={() => setGroup(index)}>{item.title}</button>)}
        </div>
        {current && <table className="machine-table" aria-label={current.title}>
          <thead><tr><th>Параметр</th><th>На станке</th><th>Записать</th></tr></thead>
          <tbody>{current.fields.map((field) => <tr key={`${field.label}-${field.kind}`}
            className={field.key && enteredDiffers(field, edits[field.key] ?? "") ? "is-diff" : undefined}>
            <th scope="row">{field.label}</th>
            <td>{field.read ? shown(field) : EMPTY}</td>
            <td>{field.key && control?.can_edit && field.kind === "bit0"
              ? <select aria-label={`${field.label}: новое значение`} value={edits[field.key] ?? ""}
                disabled={blocked.has(field.key)} title={blocked.has(field.key) ? RUNNER_OUTDATED : undefined}
                onChange={(event) => edit(field.key as string, event.target.value)}>
                <option value="">Не менять</option>
                {SWITCH.map(([code, text]) => <option key={code} value={code}>{text}</option>)}
              </select>
              : field.key && control?.can_edit
              ? <input aria-label={`${field.label}: новое значение`} value={edits[field.key] ?? ""}
                disabled={blocked.has(field.key)} title={blocked.has(field.key) ? RUNNER_OUTDATED : undefined}
                inputMode={field.kind === "text15" ? "text" : "decimal"} maxLength={field.kind === "text15" ? 30 : 12}
                placeholder={field.kind === "text15" ? "латиница, до 30 знаков" : field.unit}
                onChange={(event) => edit(field.key as string, event.target.value)} />
              : <span className="machine-muted">{field.key ? "" : "только просмотр"}</span>}</td>
          </tr>)}</tbody>
        </table>}
      </>}
    </div>}

    {control && <Correction inventory={inventory} control={control} plan={plan} onDone={setMessage} />}
  </section>;
}

/** «Правка после пробы»: наладчик говорит, какой это случай (владелец 19.11.2026). */
function Correction({ inventory, control, plan, onDone }: {
  inventory: string; control: MachineControl; plan: TransferPlan | null;
  onDone: (message: { tone: "ok" | "danger"; text: string }) => void;
}) {
  const [parameter, setParameter] = useState("strip_front_mm");
  const [kind, setKind] = useState<CorrectionCase>("machine");
  const [measured, setMeasured] = useState("");
  const [offset, setOffset] = useState("");
  const [etkValue, setEtkValue] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const stored = control.offsets.find((item) => item.key === parameter);
  const row = plan?.rows.find((item) => item.key === parameter);
  const etkAllowed = !!stored?.etk;
  useEffect(() => { if (!etkAllowed) setKind("machine"); }, [etkAllowed]);

  // The suggestion: what the machine lacked against the ETK size goes into its correction.
  useEffect(() => {
    const got = decimal(measured);
    const nominal = row?.nominal ? decimal(row.nominal) : null;
    const old = stored ? decimal(stored.offset) ?? 0 : 0;
    if (got !== null && nominal !== null) setOffset(comma(old + nominal - got));
  }, [measured, row?.nominal, stored?.offset]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    setBusy(true);
    try {
      const answer = await machineControlApi.correct(inventory, {
        parameter, case: kind, measured: measured || undefined, note: note || undefined,
        offset: kind !== "etk" ? offset : undefined, etk_value: kind !== "machine" ? etkValue : undefined,
        group_id: kind !== "machine" ? plan?.group_id : undefined,
      });
      const parts = [answer.offset !== null ? `поправка станка ${answer.offset} мм` : "",
        ...answer.etk.map((item) => `ЭТК ${item.project}, ревизия ${item.revision}`)].filter(Boolean);
      onDone({ tone: "ok", text: `Правка записана: ${parts.join("; ") || "без изменений"}` });
      setMeasured(""); setNote("");
    } catch (reason) {
      onDone({ tone: "danger", text: failureText(reason, "Не удалось записать правку") });
    } finally {
      setBusy(false);
    }
  };

  return <div className="machine-block">
    <h4>Правка после пробы</h4>
    <div className="machine-form">
      <label><span>Параметр</span>
        <select value={parameter} onChange={(event) => setParameter(event.target.value)}>
          {control.offsets.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
        </select>
      </label>
      <label><span>Получилось, мм</span>
        <input inputMode="decimal" value={measured} onChange={(event) => setMeasured(event.target.value)} />
      </label>
      <fieldset className="machine-cases"><legend>Что не так</legend>
        {CASES.map(([value, text]) => <label key={value}>
          <input type="radio" name="machine-case" value={value} checked={kind === value}
            disabled={value !== "machine" && !etkAllowed} onChange={() => setKind(value)} />{text}
        </label>)}
      </fieldset>
      {kind !== "etk" && <label><span>Поправка станка, мм</span>
        <input inputMode="decimal" value={offset} onChange={(event) => setOffset(event.target.value)}
          placeholder={stored ? `сейчас ${stored.offset}` : ""} />
      </label>}
      {kind !== "machine" && <label><span>Верный размер ЭТК, мм</span>
        <input inputMode="decimal" value={etkValue} onChange={(event) => setEtkValue(event.target.value)}
          placeholder={row?.nominal ? `сейчас ${row.nominal}` : ""} />
      </label>}
      <label className="wide"><span>Комментарий</span>
        <input value={note} maxLength={200} onChange={(event) => setNote(event.target.value)} />
      </label>
    </div>
    {kind !== "machine" && !plan && <p className="warning-inline">Выберите задание резки выше — правится его ЭТК</p>}
    {!etkAllowed && <p className="machine-muted">В ЭТК с карточки правится только зачистка начала и конца</p>}
    <div className="machine-actions">
      <button className="primary small" disabled={busy || !control.can_edit || (kind !== "machine" && !plan)
        || (kind !== "etk" && !offset.trim()) || (kind !== "machine" && !etkValue.trim())}
        onClick={() => void save()}>Записать правку</button>
    </div>
    <table className="machine-table" aria-label="Поправки станка">
      <thead><tr><th>Поправка станка</th><th>Значение</th><th>Почему</th><th>Кто и когда</th></tr></thead>
      <tbody>{control.offsets.map((item) => <tr key={item.key}>
        <th scope="row">{item.label}</th>
        <td>{item.offset === "0,00" ? EMPTY : `${item.offset} мм`}</td>
        <td>{item.reason || EMPTY}</td>
        <td>{item.updated_at ? `${item.updated_by} · ${formatMoment(item.updated_at)}` : EMPTY}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}
