import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, failureText, productionApi } from "./api";
import { equipmentSetupApi } from "./equipmentSetupApi";
import { EMPTY, formatMoment, formatNumber } from "./format";
import {
  clock, commandResult, IDLE_STATES, machineControlApi, operatorText, type MachineCommand, type MachineRun, type MachineRunState,
  type ConfirmedRunRequest, type MachineWindow, type RunRequest,
  type SettingField, type TransferChoice, type TransferPlan, type TransferRow, type WireChoice,
} from "./machineControlApi";
import { usePolled, type MachineTaskContext, type Ob019Live } from "./machinesApi";
import { PageWindow } from "./PageWindow";
import type { ManualJob } from "./manualJobsApi";
import { WorkJournal } from "./ManualJobsPage";
import { Ob019Correction } from "./Ob019Correction";
import { JobSelect, NEW_MANUAL, Ob019RunJob, manualKey } from "./Ob019RunJob";
import { MarkText, latin, latinLength, markMode, modeValues, Ob019MarkingBlock, Ob019MarkingRow, planValues,
  TEXT_CHARS } from "./Ob019MarkingBlock";
import { markCount, Ob019WireDrawing } from "./Ob019WireDrawing";
import { Ob019WirePicker } from "./Ob019WirePicker";

/** Live OB.019 setpoints, run previews and wire presets. Direct field edits use the serial command queue;
 * selecting an ETK task or a wire stages its settings until explicit load confirmation. Marking modes and texts
 * follow the same staging path, including clearing old texts for a new unmarked job. A standalone wire run needs
 * a positive quantity and complete drawing geometry, without creating a manual-work record.
 * Quantities are logical pieces: the backend converts them to D22 = D649 + remaining and keeps each run's baseline.
 * New-batch confirmation captures the displayed run/state, then waits for the reset command's verified result before
 * clearing local selection. The PLC's cumulative counters are never cleared here.
 * Known auxiliary settings remain in inline collapsed groups; unknown registers are hidden and never written.
 * Successfully applied geometry and available speeds are saved per wire after a quiet period. */

const MODES: [string, string][] = [["0", "Пошаговый"], ["1", "Один цикл"], ["2", "Автомат"]];
/** Переключатель страницы пульта (бит 0 слова): сервер меняет только этот бит. */
const SWITCH: [string, string][] = [["1", "Вкл"], ["0", "Выкл"]];
/** Primary wire geometry shown on the drawing. */
const DRAWN = ["length_mm", "strip_front_mm", "pull_front_mm", "strip_back_mm", "pull_back_mm", "diameter_mm", "knife_back_mm"];
/** The drawing derives pull-off distances from the chosen mode. */
const PULL_OF: Record<string, string> = { strip_front_mm: "pull_front_mm", strip_back_mm: "pull_back_mm" };
export type PullMode = "full" | "half" | "none";
/** Режим по значениям станка; «own» — своё стягивание, не по правилу режима (правка зачистки его не трогает). */
export type PullState = PullMode | "own";
const PULL_MODES: [PullMode, string][] = [["full", "Стягивание"], ["half", "Полу"], ["none", "Без"]];
/** Что хранит настройка провода (рецепт ОБ.019): всё с чертежа, кроме длины. */
const SPEED_KEYS = ["speed_feed", "speed_cutter", "speed_pipe", "speed_clamp_front", "speed_clamp_back",
  "speed_cut_slow", "speed_strip", "speed_out", "speed_film"];
const RECIPE_KEYS = [...DRAWN.filter((key) => key !== "length_mm"), ...SPEED_KEYS];
/** Шаг кнопок −/+: длина — 1 мм, нож — 0,01 мм, зачистка — 0,1 мм, штуки — 1. */
const STEPS: Record<string, number> = { length_mm: 1, diameter_mm: 0.01, knife_back_mm: 0.01 };
const OPEN = ["queued", "taken"];
const FAST_MS = 1000;
const IDLE_MS = 3000;
const STEP_WAIT_MS = 600;
/** Выбор в списке пишется после паузы: стрелки по закрытому списку меняют значение на каждом шаге. */
export const SELECT_WAIT_MS = 800;
/** Запись, итога которой опрос так и не показал, перестаёт держать очередь (сервер: 10 с на взятие + 30 с на ответ). */
const IN_FLIGHT_MAX_MS = 45000;
/** Начало текста сервера «нельзя писать в этом состоянии» (machine_writes.idle_problem) — его заменяет одна причина. */
const STATE_PROBLEM = "Записывать можно, когда";
/** Full parameters without fresh machine data: one status line instead of empty rows. */
export const NO_DATA = "Нет связи со станком — параметры появятся, когда ERP получит данные";
/** То же, когда порт держит открытый на ПК пульт станка. */
export const PULT_NO_DATA = "Открыт пульт станка на ПК — параметры появятся, когда пульт закроют";
/** Состояния, когда станок в цикле (работает, пауза, пучок готов): правка — после остановки. */
const RUNNING_STATES = [103, 104, 106];
const RECIPE_WAIT_MS = 5000;
/** Стадии прогона, когда его опрашивают чаще. */
const RUN_FAST = ["writing", "running", "ready"];
const DONE_SHOWN_MS = 10000;

type Tone = "ok" | "danger" | "warn" | "info";
type FieldStatus = { kind: "queued" | "held" | "writing" | "done" | "failed"; value: string; id?: string; text?: string; at?: number; recipe?: boolean; wire?: string; plan?: boolean };
type Queued = { value: string; recipe: boolean; wire?: string; plan?: boolean };
type WireWrite = "pending" | "done" | "failed";
type PlanQuery = { group_id: string; length: string; wire?: undefined } | { wire: string; length: string; group_id?: undefined };
/** A proposed run waiting for explicit confirmation of its preview. */
type RunDue = { body: RunRequest; values: Record<string, string>; wire?: string;
  preview: TransferPlan | null; activeRunId: string | null; label?: string };

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

/** Значение станка в поле ввода: «325,00», код режима, «1»/«0» переключателя; не прочитано — пусто. */
function liveText(field: SettingField | undefined): string {
  if (!field || !field.read || field.value === null || field.value === undefined) return "";
  if (field.kind === "mode") return String(field.value);
  if (typeof field.value === "boolean") return field.value ? "1" : "0";
  if (typeof field.value === "string") return field.value;
  return bare(field.value, field.unit).replace(/\s/g, "");
}

function decimal(text: string): number | null {
  const value = Number(text.replace(",", ".").replace(/\s/g, ""));
  return text.trim() && Number.isFinite(value) ? value : null;
}

const numeric = (field: SettingField) => field.kind !== "mode" && field.kind !== "bit0" && field.kind !== "text15";

const mm = (value: number) => value.toFixed(2).replace(".", ",");

const cents = (value: number) => Math.round(value * 100);

/** «Стягивание» тянет дальше зачистки на 2,00 мм (сервер: recipes.FULL_PULL_EXTRA), не больше слова ПЛК 655,35. */
export const FULL_PULL_EXTRA = 2;
const PLC_MAX_CENTS = 65535;

/** Стягивание для режима при этой зачистке: зачистка + 2,00 мм (до 655,35), половина или ноль. Счёт в сотых, половина
 *  округляется 0,005 вверх — как сервер (recipes.ob019_proposal, ROUND_HALF_UP). */
export function pullFor(mode: PullMode, strip: number): number {
  const strip100 = cents(strip);
  if (mode === "full") return Math.min(strip100 + FULL_PULL_EXTRA * 100, PLC_MAX_CENTS) / 100;
  return mode === "half" ? Math.floor((strip100 + 1) / 2) / 100 : 0;
}

/** Режим стягивания по значениям станка (владелец 19.11.2026): 0 — без; ровно половина зачистки — полу; зачистка + 2,00
 *  или не меньше зачистки — стягивание; иначе своё значение («own»). */
export function pullMode(strip: number | null, pull: number | null): PullState | null {
  if (pull === null) return null;
  const pull100 = cents(pull);
  if (pull100 === 0) return "none";
  if (strip === null) return "own";
  if (pull100 === cents(pullFor("half", strip))) return "half";
  if (pull100 === cents(pullFor("full", strip)) || pull100 >= cents(strip)) return "full";
  return "own";
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

/** Как `enteredDiffers`, но «» — тоже значение (стереть текст метки), а не «не менять». */
export function markDiffers(field: SettingField | undefined, value: string): boolean {
  if (!field || field.value === null || field.value === undefined) return true;
  if (typeof field.value === "string") return field.value !== value.split(/\s+/).filter(Boolean).join(" ");
  return enteredDiffers(field, value);
}

/** Запас диаметра реза над жилой без настройки провода (сервер: recipes.DIAMETER_OVER_CONDUCTOR, владелец 20.11.2026). */
export const DIAMETER_OVER = "0,15";

/** Откуда значение плана, словами: «0,79 = Ø жилы 0,64 + 0,15», «из настройки 18.11», «ЭТК 5,00 + поправка 0,40». */
export function sourceText(row: TransferRow, conductor: string | null, recipeAt: string | null): string {
  if (row.value === null) return "";
  const offset = row.offset && row.offset !== "0,00" ? row.offset : "";
  const plus = offset ? ` + поправка ${offset.startsWith("-") ? offset : `+${offset}`}` : "";
  if (row.source.startsWith("Ø жилы")) {
    // The sum names the base (Ø жилы + запас), not the value with the machine's correction on top.
    const base = row.nominal ?? row.value;
    return (conductor ? `${base} = Ø жилы ${conductor} + ${DIAMETER_OVER}` : `${base} = Ø жилы + ${DIAMETER_OVER}`) + plus;
  }
  if (row.source.startsWith("Рецепт")) return recipeAt ? `из настройки ${formatMoment(recipeAt).split(" ")[0]}` : "из настройки провода";
  if (row.source === "Задание") return plus ? `задание ${row.nominal}${plus}` : "из задания";
  if (row.source.startsWith("ЭТК")) return plus ? `ЭТК ${row.nominal}${plus}` : row.source === "ЭТК" ? "из ЭТК" : row.source;
  return row.source;
}

function CommandLine({ command }: { command: MachineCommand }) {
  return <li>
    <span className={`tone-${command.tone}`}>{command.status_text}</span>
    <span>{command.summary}</span>
    {(command.state_before || command.state_after) && <span>{command.state_before ?? EMPTY} → {command.state_after ?? EMPTY}</span>}
    {command.length_correction_before != null && <span>Поправка длины: {command.length_correction_before}
      {command.length_correction_after != null ? ` → ${command.length_correction_after} мм` : " мм; результат ещё не подтверждён"}</span>}
    <span className="machine-muted">{command.created_by} · {formatMoment(command.created_at)}</span>
    {command.error && <span className="tone-danger">{operatorText(command.error)}</span>}
  </li>;
}

const without = <T,>(values: Record<string, T>, keys: string[]) =>
  Object.fromEntries(Object.entries(values).filter(([key]) => !keys.includes(key)));

/** Тот же прогон: резка — группа и длина, ручная работа — сама работа. */
function sameRun(run: MachineRun, job: RunRequest): boolean {
  if (job.kind === "manual") return run.kind === "manual" && run.job_id === job.job_id;
  if (job.kind === "wire") return run.kind === "wire" && run.wire === job.wire;
  return run.kind === "cutting" && run.group_id === job.group_id
    && Number((run.length_mm ?? "").replace(",", ".")) === Number(job.length_mm.replace(",", "."));
}

/** `onRun` — прогон станка, как его видит эта панель (карточка показывает по нему строку состояния и «Пуск»); `pult` —
 *  порт держит открытый на ПК пульт станка. */
export function Ob019SettingsPanel({ inventory, equipmentId, live = null, old = false, pult = false, onRun, incoming: initialIncoming,
  toolsWindow = null, closeTools = () => {} }: {
  inventory: string; equipmentId?: string; live?: Ob019Live | null; old?: boolean; pult?: boolean;
  onRun?: (state: MachineRunState | null) => void;
  incoming?: MachineTaskContext;
  toolsWindow?: "journal" | null;
  closeTools?: () => void;
}) {
  const [incomingReleased, setIncomingReleased] = useState(false);
  const incoming = incomingReleased ? undefined : initialIncoming;
  const source = incoming?.action.source?.kind === "ob019" ? incoming.action.source : null;
  const [confirmLoad, setConfirmLoad] = useState(incoming?.loadRequested ?? false);
  const [checkingLoad, setCheckingLoad] = useState(false);
  const loadLock = useRef(false);
  const runPanel = useRef<HTMLDivElement>(null);
  const focusRun = useRef(false);
  useEffect(() => {
    if (!confirmLoad && focusRun.current) { focusRun.current = false; runPanel.current?.focus(); }
  }, [confirmLoad]);
  const postingLoad = useRef(false);
  const loadAttempt = useRef(0);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const dismissLoad = () => {
    if (postingLoad.current) return;
    loadAttempt.current += 1;
    setConfirmLoad(false);
  };
  const [sourceProblem, setSourceProblem] = useState("");
  const [fast, setFast] = useState(false);
  const period = fast ? FAST_MS : IDLE_MS;
  const loadWindow = useCallback((signal: AbortSignal) => machineControlApi.window(inventory, signal), [inventory]);
  const loadControl = useCallback((signal: AbortSignal) => machineControlApi.control(inventory, signal), [inventory]);
  const { data: card, error: cardError, reload: reloadWindow } = usePolled<MachineWindow>(loadWindow, period, "Не удалось загрузить настройки станка");
  const { data: control, error: controlError, reload: reloadControl } = usePolled(loadControl, period, "Не удалось загрузить запись на станок");
  const loadRun = useCallback((signal: AbortSignal) => machineControlApi.run(inventory, signal), [inventory]);
  const [runFast, setRunFast] = useState(false);
  const { data: runData, reload: reloadRun } = usePolled(loadRun, runFast || fast ? FAST_MS : IDLE_MS, "Не удалось загрузить задание станка");
  const runState = runData && typeof runData === "object" && "run" in runData ? runData : null;
  const fastNow = !!runState?.run && RUN_FAST.includes(runState.run.stage);
  if (fastNow !== runFast) setRunFast(fastNow);
  // A manual job just started here: picked at once, before the next poll brings it.
  const [started, setStarted] = useState<ManualJob | null>(null);
  const [due, setDue] = useState<RunDue | null>(null);
  // An ETK job with a size missing in the ETK: what was typed for it, sent with the job once every size is there.
  const [held, setHeld] = useState<Record<string, string> | null>(null);
  const [confirmReset, setConfirmReset] = useState<{ activeRunId: string | null; state: number | null; description: string } | null>(null);
  const [resetPending, setResetPending] = useState<string | null>(null);
  const [choices, setChoices] = useState<TransferChoice[] | null>(null);
  const [wires, setWires] = useState<WireChoice[] | null>(null);
  const [choice, setChoice] = useState("");
  const [wire, setWire] = useState("");
  const [plan, setPlan] = useState<TransferPlan | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<Record<string, FieldStatus>>({});
  const [queue, setQueue] = useState<Record<string, Queued>>({});
  const [sending, setSending] = useState(false);
  const [message, setMessage] = useState<{ tone: Tone; text: string } | null>(null);
  const [recipeAt, setRecipeAt] = useState<string | null>(null);
  const [recipeNote, setRecipeNote] = useState<{ tone: Tone; text: string } | null>(null);
  const [recipeDue, setRecipeDue] = useState<{ wire: string; count: number } | null>(null);
  // The write this card sent last: open until the poll shows it finished (one open write per machine on the server).
  const [inFlight, setInFlight] = useState<{ id: string; at: number } | null>(null);
  const query = useRef<PlanQuery | null>(null);
  const stepTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const pickTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // Per wire: how its last write ended and how the write of its plan (job / wire pick) ended — the wire's setting is
  // saved only when both went through.
  const wireWrites = useRef<Record<string, WireWrite>>({});
  const planWrites = useRef<Record<string, WireWrite>>({});
  // Job runs sent from here, by command id → the wire whose plan they wrote (also when no field differed).
  const runWrites = useRef<Record<string, string | undefined>>({});

  useEffect(() => {
    let alive = true;
    machineControlApi.choices(inventory).then((answer) => {
      if (!alive) return;
      setChoices(answer.choices);
      setWires(Array.isArray(answer.wires) ? answer.wires : []);
    }).catch((reason) => { if (alive) setMessage({ tone: "danger", text: failureText(reason, "Не удалось загрузить задания резки") }); });
    return () => { alive = false; };
  }, [inventory]);

  const fields = useMemo(() => new Map((card?.settings ?? []).flatMap((item) => item.fields)
    .filter((field) => field.key).map((field) => [field.key as string, field])), [card]);
  const commands = Array.isArray(control?.commands) ? control!.commands : [];
  const open = commands.find((row) => OPEN.includes(row.status));
  // The machine is in a cycle (or not ready): drawing fields closed with one reason, not a refusal per field. The card's
  // own live state counts too — it may be fresher than this panel's poll.
  const notIdle = (state: number | null | undefined) => typeof state === "number" && !IDLE_STATES.includes(state);
  const busyState = !old && control ? (notIdle(control.state) ? control.state : notIdle(live?.state.code) ? live!.state.code : null) : null;
  const linkProblem = !!control?.problem && !control.problem.startsWith(STATE_PROBLEM);
  const runningText = busyState === null || linkProblem ? ""
    : RUNNING_STATES.includes(busyState) ? "Станок режет — правка после остановки" : "Станок не готов — правка после сброса";
  const canWrite = !!control && control.can_edit && !control.problem && !old && busyState === null && !resetPending;
  const linkUp = !old && !!card;
  const runManual = runState?.manual ?? null;
  const runNow = runState?.run ?? null;
  // A run that still counts on the machine (not closed, failed or void).
  const runOpen = runNow && !["closed", "failed", "moved"].includes(runNow.stage) ? runNow : null;
  useEffect(() => { onRun?.(runState); }, [runState, onRun]);
  const manualJob = [runManual, started].find((item) => item && choice === manualKey(item.id)) ?? null;
  /** «Тираж» — штук в этом задании: сколько станку осталось до цели (цель D22 − счётчик D649). */
  const counter = typeof live?.job?.total_finished === "number" ? live.job.total_finished : null;
  const target = typeof live?.job?.total_set === "number" ? live.job.total_set : null;
  const left = counter !== null && target !== null ? Math.max(target - counter, 0) : null;
  /** «Тираж» в поле: при открытом прогоне — тираж задания, иначе — сколько станку осталось до цели. */
  const tirazh = runOpen?.quantity ?? left;

  const markWire = (wire: string | undefined, result: WireWrite, plan = false) => {
    if (!wire) return;
    wireWrites.current[wire] = result;
    if (plan) planWrites.current[wire] = result;
  };

  // Statuses follow the machine queue: done → «✓ на станке» once read back, failed → the reason, the field shows live.
  const statusRef = useRef(status);
  statusRef.current = status;
  useEffect(() => {
    if (!control) return;
    if (inFlight) {
      const row = control.commands.find((command) => command.id === inFlight.id);
      if ((row && !OPEN.includes(row.status)) || Date.now() - inFlight.at > IN_FLIGHT_MAX_MS) {
        setInFlight(null);
        if (resetPending === inFlight.id && !row) {
          setResetPending(null);
          setMessage({ tone: "danger", text: "Нет подтверждения нового тиража — обновите карточку и проверьте станок" });
        }
      }
    }
    for (const [id, wire] of Object.entries(runWrites.current)) {
      const row = control.commands.find((command) => command.id === id);
      if (!row || OPEN.includes(row.status)) continue;
      markWire(wire, row.status === "done" ? "done" : "failed", true);
      delete runWrites.current[id];
    }
    let changed = false;
    let savedWire: string | null = null;
    const next = { ...statusRef.current };
    for (const [key, item] of Object.entries(next)) {
      if (item.kind !== "writing") continue;
      const row = control.commands.find((command) => command.id === item.id);
      if (!row || OPEN.includes(row.status)) continue;
      changed = true;
      markWire(item.wire, row.status === "done" ? "done" : "failed", item.plan);
      if (row.status === "done") {
        next[key] = { ...item, kind: "done", at: Date.now() };
        if (item.recipe && item.wire) savedWire = item.wire;
      } else next[key] = { ...item, kind: "failed", text: commandResult(row).text.replace(/^отказ: /, "") };
    }
    if (changed) setStatus(next);
    if (savedWire) {
      const wire = savedWire;
      setRecipeDue((due) => ({ wire, count: (due?.count ?? 0) + 1 }));
    }
  }, [control]); // eslint-disable-line react-hooks/exhaustive-deps

  const busyWriting = Object.values(status).some((item) => item.kind === "queued" || item.kind === "writing")
    || Object.keys(queue).length > 0 || !!inFlight;
  const free = !open && !inFlight && !sending && Object.keys(queue).length === 0;
  const refusal = () => old ? "нет связи со станком" : !control?.can_edit ? "запись вам не разрешена"
    : runningText ? runningText.toLowerCase() : operatorText(control?.problem ?? "");
  /** Выбранное задание как прогон станка: правка тиража при нём идёт прогоном (сервер считает цель от счётчика). */
  const jobRequest = (): RunRequest | null => query.current?.group_id
    ? { kind: "cutting", group_id: query.current.group_id, length_mm: query.current.length }
    : query.current?.wire ? { kind: "wire", wire: query.current.wire }
    : manualJob ? { kind: "manual", job_id: manualJob.id }
      // Nothing picked here (the card was reopened) but a job is open on the machine: «Тираж» goes as its run, so the
      // run's target follows and «Пуск» is not refused («Тираж на станке не совпадает с заданием»).
      : !choice && !query.current && runOpen
        ? runOpen.kind === "wire" && runOpen.wire ? { kind: "wire", wire: runOpen.wire }
          : runOpen.kind === "manual" && runOpen.job_id ? { kind: "manual", job_id: runOpen.job_id }
          : runOpen.group_id && runOpen.length_mm ? { kind: "cutting", group_id: runOpen.group_id, length_mm: runOpen.length_mm } : null
        : null;
  if (busyWriting !== fast) setFast(busyWriting);

  // One write at a time: what was committed meanwhile goes out together once the machine finished the previous one —
  // the one this card sent (`inFlight`, until the poll shows its result) or anyone's open write the poll shows.
  useEffect(() => {
    const keys = Object.keys(queue);
    if (!keys.length || sending || !control) return;
    if (!canWrite) {
      const why = old ? "нет связи со станком" : !control.can_edit ? "запись вам не разрешена"
        : runningText ? runningText.toLowerCase() : operatorText(control.problem);
      for (const key of keys) markWire(queue[key].wire, "failed", queue[key].plan);
      setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { kind: "failed", value: queue[key].value, text: why }])) }));
      setQueue({});
      return;
    }
    if (open || inFlight || due) return;
    const batch = queue;
    setQueue({});
    const typed = Object.fromEntries(keys.map((key) => [key, batch[key].value]));
    const job = "total_set" in typed ? jobRequest() : null;
    const values = pieces(typed, job);
    if (values === null) {
      // The job's «Тираж» without the machine counter: how many are done is unknown — nothing is written.
      setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) =>
        [key, { kind: "failed", value: batch[key].value, text: "счётчик станка не прочитан — повторите, когда станок на связи" }])) }));
      return;
    }
    if (job) {
      setStatus((current) => without(current, keys));
      void runJob({ ...job, values }, values, batch[keys[0]]?.wire);
      return;
    }
    setSending(true);
    machineControlApi.write(inventory, values, control.state)
      .then((command) => {
        setInFlight({ id: command.id, at: Date.now() });
        setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) =>
          [key, { kind: "writing", value: batch[key].value, id: command.id, recipe: batch[key].recipe, wire: batch[key].wire, plan: batch[key].plan }])) }));
      })
      .catch((reason) => {
        const text = operatorText(failureText(reason, "не записано"));
        for (const key of keys) markWire(batch[key].wire, "failed", batch[key].plan);
        setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { kind: "failed", value: batch[key].value, text }])) }));
      })
      .finally(() => setSending(false));
  }, [queue, sending, control, canWrite, open, inFlight, due, old, inventory]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Задание — прогоном станка (`POST …/run`): сервер пишет значения задания, тираж и маркировку и начинает счёт. */
  async function sendRun(body: ConfirmedRunRequest, values: Record<string, string>, wire?: string) {
    const attempt = loadAttempt.current;
    postingLoad.current = true;
    const keys = Object.keys(values);
    markWire(wire, "pending", true);
    setSending(true);
    setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { kind: "queued", value: values[key] }])) }));
    try {
      const command = await machineControlApi.startRun(inventory, body, control?.state ?? null);
      runWrites.current[command.id] = wire;
      setInFlight({ id: command.id, at: Date.now() });
      setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) =>
        [key, { kind: "writing", value: values[key], id: command.id, wire, plan: true }])) }));
      reloadRun();
      setConfirmLoad(false);
      setHeld(null);
      setDue(null);
    } catch (reason) {
      if (!active.current || attempt !== loadAttempt.current) return;
      const text = operatorText(failureText(reason, "не записано"));
      markWire(wire, "failed", true);
      if (keys.length) setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { kind: "failed", value: values[key], text }])) }));
      setMessage({ tone: "danger", text: `Задание не передано: ${text}` });
      if (reason instanceof ApiError && reason.status === 409) {
        // Refresh only the preview. A second POST always requires another operator click.
        setDue(null);
        setPlan(null);
        setHeld(null);
        try {
          if (body.kind === "cutting") {
            const fresh = await machineControlApi.plan(inventory, body.group_id, body.length_mm);
            if (!active.current || attempt !== loadAttempt.current) return;
            setPlan(fresh);
            setHeld({});
            setDue({ body: { kind: "cutting", group_id: body.group_id, length_mm: body.length_mm },
              values: planDiff(fresh), wire: fresh.wire, preview: fresh, activeRunId: fresh.expected_active_run_id ?? null });
          } else {
            const fresh = await machineControlApi.run(inventory);
            if (!active.current || attempt !== loadAttempt.current) return;
            if (fresh.expected_active_run_id === undefined) throw new Error("Обновите карточку и подтвердите загрузку ещё раз");
            setDue({ body, values: body.values ?? {}, wire,
              preview: null, activeRunId: fresh.expected_active_run_id, label: fresh.run?.label });
          }
          setConfirmLoad(true);
          setMessage({ tone: "danger", text: `${text}. Проверьте обновлённые значения и подтвердите загрузку ещё раз` });
          reloadRun();
        } catch (refreshError) {
          setMessage({ tone: "danger", text: `${text}. ${failureText(refreshError, "Не удалось обновить план — выберите задание заново")}` });
        }
      }
    } finally {
      postingLoad.current = false;
      setSending(false);
    }
  }

  /** Selecting a job or changing its quantity prepares a preview, never a write. */
  async function runJob(body: RunRequest, values: Record<string, string>, wire?: string, preview?: TransferPlan) {
    if (!canWrite) {
      markWire(wire, "failed", true);
      setMessage({ tone: "warn", text: `Не записано: ${refusal()}` });
      return;
    }
    const attempt = ++loadAttempt.current;
    try {
      if (body.kind === "cutting") {
        const shown = preview ?? await machineControlApi.plan(inventory, body.group_id, body.length_mm);
        if (!active.current || attempt !== loadAttempt.current) return;
        setDue({ body, values, wire, preview: shown, activeRunId: shown.expected_active_run_id ?? null });
      } else {
        const shown = await machineControlApi.run(inventory);
        if (!active.current || attempt !== loadAttempt.current) return;
        if (shown.expected_active_run_id === undefined) throw new Error("Обновите карточку и подтвердите загрузку ещё раз");
        setDue({ body, values, wire, preview: null, activeRunId: shown.expected_active_run_id, label: shown.run?.label });
      }
      setConfirmLoad(true);
    } catch (reason) {
      if (active.current && attempt === loadAttempt.current) setMessage({ tone: "danger", text: failureText(reason, "Не удалось обновить план загрузки") });
    }
  }

  /** Значение без набираемого: то, что уже уходит на станок, иначе то, что на станке. */
  const liveOf = (key: string) => key === "total_set" ? (tirazh === null ? "" : String(tirazh)) : liveText(fields.get(key));
  const settledOf = (key: string) => {
    const st = status[key];
    return held?.[key] ?? (st && (st.kind === "queued" || st.kind === "held" || st.kind === "writing") ? st.value : liveOf(key));
  };
  /** Отличается ли значение от станка; «Тираж» — от того, сколько станку осталось до цели. */
  const differs = (key: string, value: string) => key === "total_set"
    ? !!value.trim() && (tirazh === null || decimal(value) !== tirazh) : enteredDiffers(fields.get(key), value);
  /** A revert must replace the held value even when it already matches the machine. */
  const stagedDiffers = (key: string, value: string) => {
    const staged = held?.[key];
    if (staged === undefined) return differs(key, value);
    const field = fields.get(key);
    return field && numeric(field) ? decimal(value) !== decimal(staged) : value !== staged;
  };
  /** «Тираж» задания при открытом прогоне этого задания → штук станку: тираж − уже сделано (сервер пишет цель от счётчика).
   *  null — сделано неизвестно (счётчик не прочитан): не писать. */
  const pieces = (values: Record<string, string>, job: RunRequest | null): Record<string, string> | null => {
    if (!("total_set" in values) || !runOpen || runOpen.quantity === null || (job && !sameRun(runOpen, job))) return values;
    const typed = decimal(values.total_set);
    if (runOpen.done === null || typed === null) return null;
    return { ...values, total_set: String(Math.max(typed - runOpen.done, 0)) };
  };

  /** Поле изменено — на станок. Пусто, то же, что на станке, или не число — запись не уходит. Зачистка уходит вместе со
   *  стягиванием своего конца по текущему режиму (стягивание = зачистка + 2,00, полу = половина, без = 0; своё
   *  стягивание не трогается). `own` — своя правка наладчика: только она по параметрам провода (RECIPE_KEYS)
   *  сохраняет настройку провода; поправка и выбор задания/провода — нет. */
  const commit = (key: string, text: string, own = true) => {
    setDrafts((current) => without(current, [key]));
    const field = fields.get(key);
    const value = text.trim();
    const missingSize = plan?.group_id && blockingKeys(plan).includes(key);
    if (!field || !value || (!missingSize && !stagedDiffers(key, value))) return;
    if (numeric(field) && typeof field.value !== "string" && decimal(value) === null) {
      setStatus((current) => ({ ...current, [key]: { kind: "failed", value, text: "нужно число" } }));
      return;
    }
    // «Тираж» of the open job itself (not another job picked meanwhile) must be above what it has made.
    const sentJob = key === "total_set" ? jobRequest() : null;
    if (key === "total_set" && runOpen && runOpen.done !== null && (!sentJob || sameRun(runOpen, sentJob))
      && (decimal(value) ?? 0) <= runOpen.done) {
      setStatus((current) => ({ ...current, [key]: { kind: "failed", value, text: `уже сделано ${runOpen.done} — тираж больше` } }));
      return;
    }
    const values: Record<string, string> = { [key]: value };
    const pullKey = PULL_OF[key];
    const strip = decimal(value);
    if (pullKey && fields.has(pullKey) && strip !== null) {
      const mode = pullMode(decimal(settledOf(key)), decimal(settledOf(pullKey)));
      const pull = mode && mode !== "own" ? mm(pullFor(mode, strip)) : "";
      if (pull && stagedDiffers(pullKey, pull)) values[pullKey] = pull;
    }
    const keys = Object.keys(values);
    const wire = plan?.wire;
    if (held && plan && !plan.group_id) {
      holdWire(values);
      return;
    }
    if ((held || incoming || due) && plan?.group_id) {
      holdForJob(plan, values);
      return;
    }
    if (wire) wireWrites.current[wire] = "pending";
    setQueue((current) => ({ ...current, ...Object.fromEntries(keys.map((item) =>
      [item, { value: values[item], recipe: own && RECIPE_KEYS.includes(item), wire }])) }));
    setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((item) => [item, { kind: "queued", value: values[item] }])) }));
  };
  const commitRef = useRef(commit);
  commitRef.current = commit;

  /** Маркировка: все значения одной записью, как есть — и совпадающие со станком, и «» (стереть текст). Настройку провода
   *  не трогает. */
  const commitBatch = (values: Record<string, string>) => {
    const keys = Object.keys(values).filter((key) => fields.has(key));
    if (!keys.length) return;
    setDrafts((current) => without(current, keys));
    const batch = Object.fromEntries(keys.map((key) => [key, values[key]]));
    if (plan?.group_id && (held !== null || incoming || due)) {
      holdForJob(plan, batch);
      return;
    }
    if (held && plan && !plan.group_id) {
      holdWire(batch);
      return;
    }
    setQueue((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { value: values[key], recipe: false }])) }));
    setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { kind: "queued", value: values[key] }])) }));
  };

  /** Текст метки: в латиницу, как ляжет на станок; пустой — стирает текст на станке; длиннее 30 знаков — не уходит. */
  const commitText = (key: string, raw: string) => {
    setDrafts((current) => without(current, [key]));
    if (!fields.has(key)) return;
    if (latinLength(raw) > TEXT_CHARS) {
      setStatus((current) => ({ ...current, [key]: { kind: "failed", value: raw, text: `не длиннее ${TEXT_CHARS} знаков` } }));
      return;
    }
    const text = latin(raw);
    if (text === settledOf(key)) return;
    commitBatch({ [key]: text });
  };

  // «Запомнить для провода» — само: после серии своих правок значения станка сохраняются как настройка провода.
  const latest = useRef({ fields, plan, linkUp, equipmentId });
  latest.current = { fields, plan, linkUp, equipmentId };
  useEffect(() => {
    if (!recipeDue) return;
    const timer = setTimeout(() => void saveRecipe(recipeDue.wire), RECIPE_WAIT_MS);
    return () => clearTimeout(timer);
  }, [recipeDue]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Сохраняет значения станка как настройку провода `wire` — только если он всё ещё выбран и его последняя запись
   *  прошла: после отказа станок держит не то, что видно для этого провода. Запись ещё идёт — позже. */
  async function saveRecipe(wire: string) {
    const { fields: now, plan: job, linkUp: up, equipmentId: machine } = latest.current;
    if (!machine || !job?.wire || job.wire !== wire || !up) return;
    const last = wireWrites.current[wire];
    const planned = planWrites.current[wire];
    if (last === "pending" || planned === "pending") { setRecipeDue((due) => due && due.count < 20 ? { ...due, count: due.count + 1 } : due); return; }
    if (last !== "done" || planned !== "done") {
      setRecipeNote({ tone: "warn", text: planned !== "done"
        ? "Настройка провода не сохранена: значения задания или провода не записаны на станок — выберите его ещё раз"
        : "Настройка провода не сохранена: последняя запись на станок не прошла" });
      return;
    }
    const recipeKeys = RECIPE_KEYS.filter((key) => !SPEED_KEYS.includes(key)
      || (now.get(key)?.read && typeof now.get(key)?.value === "number"));
    const missing = recipeKeys.filter((key) => typeof now.get(key)?.value !== "number");
    if (missing.length) {
      setRecipeNote({ tone: "danger", text: `Настройка провода не сохранена: станок не прислал ${missing.map((key) => now.get(key)?.label ?? key).join(", ")}` });
      return;
    }
    try {
      const proposal = await equipmentSetupApi.ob019Proposal(machine, job.group_id
        ? { group_id: job.group_id, length_mm: job.length_mm.replace(",", ".") } : { wire: job.wire });
      const saved = await equipmentSetupApi.ob019SaveVersion(machine, {
        ...proposal.publish, wire: proposal.wire,
        values: Object.fromEntries(recipeKeys.map((key) => [key, String(now.get(key)!.value)])),
        etk_strip_front_mm: proposal.etk?.strip_front_mm ?? null, etk_strip_back_mm: proposal.etk?.strip_back_mm ?? null,
        context: job.number ? `Карточка станка, ${job.number}` : "Карточка станка",
      });
      setRecipeAt(saved.created_at);
      setRecipeNote({ tone: "ok", text: `Настройка провода ${proposal.wire} сохранена ${clock(saved.created_at)}` });
      setWires((current) => current?.map((item) => item.label === proposal.wire ? { ...item, has_recipe: true } : item) ?? current);
    } catch (reason) {
      setRecipeNote({ tone: "danger", text: failureText(reason, "Настройка провода не сохранена") });
    }
  }

  // The date of the wire's saved setting, for «из настройки 18.11».
  useEffect(() => {
    setRecipeAt(null);
    if (!equipmentId || !plan?.wire) return;
    let alive = true;
    equipmentSetupApi.ob019Proposal(equipmentId, { wire: plan.wire })
      .then((answer) => { if (alive) setRecipeAt(answer?.recipe?.created_at ?? null); })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [equipmentId, plan?.wire]);

  const loadPlan = (next: PlanQuery) => next.group_id
    ? machineControlApi.plan(inventory, next.group_id, next.length)
    : machineControlApi.wirePlan(inventory, next.wire!, next.length);

  /** Значения плана, которые отличаются от станка, и маркировка ЭТК. Маркировку с ошибкой сюда не берут: такое задание
   *  не пишется вовсе (`markingRefused` — до записи; сервер тоже отказывает 422 «Маркировка: …»). */
  function planDiff(next: TransferPlan): Record<string, string> {
    const values: Record<string, string> = {};
    for (const row of next.rows) if (row.value !== null && fields.has(row.key) && differs(row.key, row.value)) values[row.key] = row.value;
    for (const [key, value] of Object.entries(next.job)) {
      if (value !== null && value !== "" && fields.has(key) && differs(key, String(value))) values[key] = String(value);
    }
    const marking = next.marking ?? null;
    if (marking && marking.blocking.length === 0) {
      for (const [key, value] of Object.entries(planValues(marking))) if (fields.has(key) && markDiffers(fields.get(key), value)) values[key] = value;
    } else if (!marking && next.group_id && !(runOpen?.kind === "cutting" && runOpen.group_id === next.group_id
      && decimal(runOpen.length_mm ?? "") === decimal(next.length_mm))) {
      for (const [key, value] of Object.entries(modeValues("none", () => ""))) {
        if (fields.has(key) && markDiffers(fields.get(key), value)) values[key] = value;
      }
    }
    return values;
  }

  /** Маркировка ЭТК с ошибкой: задание не пишется вовсе (без маркировки оно ушло бы молча). true — отказано. */
  function markingRefused(next: TransferPlan): boolean {
    const blocking = next.marking?.blocking ?? [];
    if (!blocking.length) return false;
    markWire(next.wire, "failed", true);
    setHeld(null);
    // The reason itself is in «Маркировка» (Ob019MarkingBlock: «Маркировка из ЭТК не записывается: …»).
    setMessage({ tone: "danger", text: "Задание не передано: маркировка из ЭТК с ошибкой — исправьте ЭТК" });
    return true;
  }

  /** Ключи размеров, которых нет в ЭТК задания. */
  const blockingKeys = (next: TransferPlan) => next.rows.filter((row) => row.value === null && next.blocking.includes(row.label)).map((row) => row.key);

  /** Задание ЭТК без размера: введённое ждёт, пока введены все недостающие размеры, затем всё уходит одним прогоном
   *  (сервер: значения прогона вместо плана ЭТК, тираж — задания). Раньше на станок не пишется ничего. */
  function holdForJob(next: TransferPlan, values: Record<string, string>) {
    if (markingRefused(next)) return;
    const typed = { ...(held ?? {}), ...values };
    setHeld(typed);
    setStatus((current) => ({ ...current, ...Object.fromEntries(Object.keys(values).map((key) => [key, { kind: "held", value: values[key] }])) }));
    const missing = blockingKeys(next).filter((key) => !(key in typed));
    if (missing.length) return;
    const body: RunRequest = { kind: "cutting", group_id: next.group_id!, length_mm: query.current?.length ?? next.length_mm };
    const remaining = pieces(typed, body);
    if (remaining === null) {
      setMessage({ tone: "warn", text: "Счётчик станка не прочитан — повторите, когда станок на связи" });
      return;
    }
    // A partial resend of the open job preserves its adjusted settings; only ETK marking is automatic.
    const sameOpenJob = !!runOpen && sameRun(runOpen, body);
    const markingKeys = new Set(Object.keys(next.marking ? planValues(next.marking) : {}));
    const automatic = Object.fromEntries(Object.entries(planDiff(next)).filter(([key]) => !sameOpenJob || markingKeys.has(key)));
    const all: Record<string, string> = { ...automatic, ...remaining };
    // A new job supplies its quantity unless the operator typed «Тираж» meanwhile.
    if (!sameOpenJob && fields.has("total_set") && next.quantity !== null && !("total_set" in typed)) all.total_set = String(next.quantity);
    void runJob({ ...body, values: all }, all, next.wire, next);
  }

  function holdWire(values: Record<string, string>) {
    setHeld((current) => ({ ...current, ...values }));
    setStatus((current) => ({ ...current, ...Object.fromEntries(Object.entries(values)
      .map(([key, value]) => [key, { kind: "held", value }])) }));
    setDue(null);
  }

  async function prepareWire() {
    if (!plan || plan.group_id || !wire || !held) return;
    const values = { ...held };
    for (const key of DRAWN) if (!(key in values) && fields.has(key)) values[key] = settledOf(key);
    if ((decimal(values.total_set ?? "") ?? 0) <= 0 || (decimal(values.length_mm ?? "") ?? 0) <= 0) {
      setMessage({ tone: "warn", text: "Укажите положительные длину и тираж" });
      return;
    }
    await runJob({ kind: "wire", wire, values }, values, wire);
  }

  function clearSelection() {
    clearTimeout(pickTimer.current);
    Object.values(stepTimers.current).forEach(clearTimeout);
    loadAttempt.current += 1;
    query.current = null;
    setChoice(""); setWire(""); setPlan(null); setHeld(null); setDue(null);
    setDrafts({}); setStatus({}); setQueue({}); setRecipeDue(null); setRecipeNote(null);
    setConfirmLoad(false);
  }

  async function resetJob() {
    if (!canWrite || !free || !confirmReset || resetPending || loadLock.current) return;
    loadLock.current = true;
    setSending(true);
    try {
      const command = await machineControlApi.resetRun(inventory, confirmReset.activeRunId, confirmReset.state);
      setResetPending(command.id);
      setInFlight({ id: command.id, at: Date.now() });
      setConfirmReset(null);
      reloadControl();
    } catch (reason) {
      setMessage({ tone: "danger", text: failureText(reason, "Новый тираж не подготовлен") });
      reloadRun();
    } finally { loadLock.current = false; setSending(false); }
  }

  useEffect(() => {
    if (!resetPending) return;
    const command = commands.find((item) => item.id === resetPending);
    if (!command || OPEN.includes(command.status)) return;
    setResetPending(null);
    if (command.status === "done") {
      clearSelection();
      setIncomingReleased(true);
      setMessage({ tone: "info", text: "Новый тираж: выберите провод или задание" });
      reloadRun(); reloadWindow();
    } else setMessage({ tone: "danger", text: `Новый тираж не подготовлен: ${operatorText(command.error || command.status_text)}` });
  }, [control]); // eslint-disable-line react-hooks/exhaustive-deps

  /** ETK tasks open a guarded load preview; standalone wires stage geometry, presets and cleared job values.
   * Neither selection writes to the machine before the operator confirms the load. */
  async function writePlan(next: TransferPlan) {
    const values = planDiff(next);
    const wire = next.wire;
    if (next.group_id) {
      if (markingRefused(next)) return;
      setHeld({});
      // A size missing in the ETK: nothing is written until it is typed on the drawing (`holdForJob`).
      if (next.blocking.length) {
        markWire(wire, "pending", true);
        setHeld({});
        return;
      }
      return runJob({ kind: "cutting", group_id: next.group_id, length_mm: query.current?.length ?? next.length_mm }, values, wire, next);
    }
    const reset = { ...modeValues("none", () => ""), total_set: "0", order_number: "", product: "" };
    const preset = Object.fromEntries(next.rows.filter((row) => row.value !== null && fields.has(row.key))
      .map((row) => [row.key, row.value!]));
    holdWire({ ...values, ...preset, ...Object.fromEntries(Object.entries(reset).filter(([key]) => fields.has(key))) });
  }

  const writePlanRef = useRef(writePlan);
  writePlanRef.current = writePlan;

  async function pick(next: PlanQuery | null) {
    query.current = next;
    setPlan(null);
    setMessage(null);
    setRecipeNote(null);
    setHeld(null);
    setDue(null);
    if (!next) return;
    setPlanLoading(true);
    try {
      const answer = await loadPlan(next);
      if (query.current !== next) return;
      setPlan(answer);
      if (incoming && blockingKeys(answer).length) setHeld({});
      // The latest render's writePlan: open/inFlight/queue as they are now, not as they were when the pick started.
      if (!incoming) await writePlanRef.current(answer);
    } catch (reason) {
      if (query.current === next) setMessage({ tone: "danger", text: failureText(reason, "Не удалось посчитать значения") });
    } finally {
      if (query.current === next) setPlanLoading(false);
    }
  }
  const pickRef = useRef(pick);
  pickRef.current = pick;
  useEffect(() => {
    if (!incoming || !choices) return;
    const matches = source ? choices.filter((item) => item.group_id === source.group_id
      && decimal(item.length_mm) === decimal(source.length_mm)) : [];
    if (matches.length !== 1) {
      setSourceProblem("Задание резки из инструкции больше недоступно — откройте инструкцию заново");
      return;
    }
    const item = matches[0];
    setChoice(item.key);
    void pickRef.current({ group_id: item.group_id, length: item.length_mm });
  }, [incoming, choices, source]);
  /** Job selection is previewed after the pause and then confirmed (стрелки по
   *  закрытому списку в Chrome на Windows дают изменение на каждом шаге — пишется только итог). */
  const pickJob = (key: string) => {
    if (postingLoad.current || busyWriting || resetPending) return;
    clearSelection();
    setChoice(key);
    setWire("");
    query.current = null;  // what an earlier choice still loads is dropped
    setPlan(null);
    setHeld(null);
    setDue(null);
    clearTimeout(pickTimer.current);
    if (key === NEW_MANUAL) return;  // the form opens in «Выполнение задания»; its job is picked once started
    const job = [runManual, started].find((item) => item && manualKey(item.id) === key);
    if (job) {
      pickTimer.current = setTimeout(() => pickManualRef.current(job), SELECT_WAIT_MS);
      return;
    }
    const item = choices?.find((row) => row.key === key);
    pickTimer.current = setTimeout(() => void pickRef.current(item ? { group_id: item.group_id, length: item.length_mm } : null), SELECT_WAIT_MS);
  };
  /** A manual job also requires confirmation; the server derives its length, remaining quantity and order number. */
  const pickManual = (job: ManualJob) => {
    setMessage(null);
    setRecipeNote(null);
    const run = runNow && runNow.kind === "manual" && runNow.job_id === job.id && runNow.stage !== "closed" ? runNow : null;
    const done = run?.done ?? 0;
    if (done && job.quantity_planned) setMessage({ tone: "info", text: `Уже сделано ${done} из ${job.quantity_planned} — на станок уходит остаток` });
    if (!incoming) void runJob({ kind: "manual", job_id: job.id }, {});
  };
  const pickManualRef = useRef(pickManual);
  pickManualRef.current = pickManual;
  const pickWire = (label: string) => {
    if (runOpen || !free || resetPending) return;
    clearSelection();
    setWire(label);
    const length = fields.get("length_mm")?.value;
    if (typeof length !== "number" || length <= 0) {
      query.current = null;
      setPlan(null);
      setMessage({ tone: "warn", text: "Станок не прислал длину — задайте её на чертеже и выберите провод ещё раз" });
      return;
    }
    void pick({ wire: label, length: String(length) });
  };

  /** После поправки: план задания или провода заново; новое значение этого параметра — сразу на станок. */
  async function afterCorrection(parameter: string): Promise<string> {
    // Length correction is executed by the PLC. Never rewrite its D32 job setpoint here.
    if (parameter === "length_mm") {
      await Promise.all([reloadControl(), reloadWindow()]);
      return "";
    }
    const current = query.current;
    if (!current) return "выберите задание или провод — поправка войдёт в их значения";
    try {
      const answer = await loadPlan(current);
      if (query.current !== current) return "";
      setPlan(answer);
      const row = answer.rows.find((item) => item.key === parameter);
      if (!row || row.value === null) return "";
      if (row.offset === null) return "у этого провода значение из настройки — исправьте его на чертеже";
      if (!differs(parameter, row.value)) return "";
      commitRef.current(parameter, row.value, false);
      return `${row.label} ${row.value} мм записывается на станок`;
    } catch (reason) {
      return failureText(reason, "не удалось пересчитать значения");
    }
  }

  const stepper = (key: string, field: SettingField, direction: 1 | -1) => {
    const step = STEPS[key] ?? (field.unit === "мм" ? 0.1 : 1);
    const st = status[key];
    const base = decimal(drafts[key] ?? (st && (st.kind === "queued" || st.kind === "held" || st.kind === "writing") ? st.value : liveOf(key))) ?? 0;
    const next = Math.max(0, Math.round((base + direction * step) * 100) / 100);
    const text = field.unit === "мм" ? next.toFixed(2).replace(".", ",") : String(Math.round(next));
    commitLater(key, text, STEP_WAIT_MS);
  };
  /** Показать сразу, записать после паузы: −/+ и выбор в списке (стрелки по закрытому списку — изменение на шаг). */
  const commitLater = (key: string, text: string, wait: number) => {
    setDrafts((current) => ({ ...current, [key]: text }));
    clearTimeout(stepTimers.current[key]);
    stepTimers.current[key] = setTimeout(() => commitRef.current(key, text), wait);
  };
  useEffect(() => () => { Object.values(stepTimers.current).forEach(clearTimeout); clearTimeout(pickTimer.current); }, []);

  /** Итог записи поля: «записывается…», «✓ на станке», «отказ: причина». */
  const fieldNote = (key: string): { tone: Tone; text: string } | null => {
    const st = status[key];
    if (!st) return null;
    if (st.kind === "failed") return { tone: "danger", text: `отказ: ${st.text}` };
    if (st.kind === "held") return { tone: "warn", text: plan && blockingKeys(plan).some((key) => !(key in (held ?? {})))
      ? "уйдёт с заданием, когда введены все размеры" : "уйдёт с заданием после подтверждения" };
    if (st.kind === "done") {
      // «Тираж» without the live counter cannot be compared: written is written.
      if ((key !== "total_set" || tirazh !== null) && differs(key, st.value)) return { tone: "info", text: "записывается…" };
      return Date.now() - (st.at ?? 0) < DONE_SHOWN_MS ? { tone: "ok", text: "✓ на станке" } : null;
    }
    return { tone: "info", text: "записывается…" };
  };
  const valueOf = (key: string) => drafts[key] ?? settledOf(key);

  /** Стягивание конца на чертеже: «Стягивание · Полу · Без» по значениям станка; выбор сразу пишет стягивание. */
  const pullSwitch = (key: string, label: string) => {
    const stripKey = key === "pull_front_mm" ? "strip_front_mm" : "strip_back_mm";
    const strip = decimal(settledOf(stripKey));
    const pull = decimal(settledOf(key));
    const mode = pullMode(strip, pull);
    const editable = fields.has(key) && canWrite && strip !== null;
    const note = fieldNote(key);
    return <div key={key} className={`machine-input machine-pull${old ? " is-old" : ""}`}>
      <span className="machine-input-label">{label}</span>
      <span className="machine-pull-modes" role="radiogroup" aria-label={label}>
        {PULL_MODES.map(([code, text]) => <button key={code} type="button" role="radio" aria-checked={mode === code}
          className={mode === code ? "on" : undefined} disabled={!editable}
          onClick={() => { if (mode !== code && strip !== null) commit(key, mm(pullFor(code, strip))); }}>{text}</button>)}
      </span>
      <span className="machine-input-note">
        {note && <span className={`tone-${note.tone}`} role="status">{note.text}</span>}
        {pull !== null && <span className="machine-muted">{mode === "own" ? `своё: ${mm(pull)} мм` : `${mm(pull)} мм`}</span>}
      </span>
    </div>;
  };
  const drawn = (key: string, label: string) => key in PULL_OF || !Object.values(PULL_OF).includes(key) ? input(key, label) : pullSwitch(key, label);

  const conductor = plan ? wires?.find((item) => item.label === plan.wire)?.conductor_mm ?? null : null;
  /** Поле чертежа и строки задания: в поле — значение станка; правка уходит на станок по Enter, уходу из поля, −/+. */
  const input = (key: string, label: string, extra = "") => {
    const field = fields.get(key);
    const editable = !!field && canWrite && !resetPending;
    const note = fieldNote(key);
    const row = plan?.rows.find((item) => item.key === key);
    const missing = !!row && row.value === null && !!plan?.blocking.includes(row.label);
    const value = valueOf(key);
    const source = row && row.value !== null && decimal(row.value) === decimal(value) ? sourceText(row, conductor, recipeAt) : "";
    const name = field?.label ?? label;
    const writing = note?.text === "записывается…";
    const options = field?.kind === "mode" ? MODES : SWITCH;
    return <div key={key} className={`machine-input${writing ? " is-writing" : ""}${missing ? " is-missing" : ""}${old ? " is-old" : ""}`}>
      <label className="machine-input-label" htmlFor={`ob019-${key}`}>{label}</label>
      <span className="machine-input-box">
        {field?.kind === "mode" || field?.kind === "bit0"
          ? <span id={`ob019-${key}`} className="machine-pull-modes" role="radiogroup" aria-label={name}
            onKeyDown={(event) => {
              if (!editable || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
              event.preventDefault();
              const current = Math.max(options.findIndex(([code]) => code === value), 0);
              const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1
                : (current + (event.key === "ArrowRight" ? 1 : -1) + options.length) % options.length;
              commitLater(key, options[next][0], SELECT_WAIT_MS);
              event.currentTarget.querySelectorAll<HTMLButtonElement>("button")[next]?.focus();
            }}>
            {options.map(([code, text]) => <button key={code} type="button" role="radio"
              aria-checked={value === code} className={value === code ? "on" : undefined} disabled={!editable}
              onClick={() => { if (value !== code) commitLater(key, code, SELECT_WAIT_MS); }}>{text}</button>)}
          </span>
          : <>
            {field && numeric(field) && <button type="button" className="machine-step" aria-label={`${name}: меньше`} disabled={!editable}
              onClick={() => stepper(key, field, -1)}>−</button>}
            <input id={`ob019-${key}`} name={key} aria-label={name} value={value} disabled={!editable} spellCheck={false}
              inputMode={field?.kind === "text15" ? "text" : "decimal"} maxLength={field?.kind === "text15" ? 30 : 12}
              autoComplete="off" placeholder={field?.read ? "" : EMPTY}
              onChange={(event) => setDrafts((current) => ({ ...current, [key]: event.target.value }))}
              onBlur={() => { if (key in drafts) (field?.kind === "text15" ? commitText : commit)(key, drafts[key]); }}
              onKeyDown={(event) => {
                if (event.key === "Enter") { event.preventDefault(); (field?.kind === "text15" ? commitText : commit)(key, (event.target as HTMLInputElement).value); }
                if (event.key === "Escape") setDrafts((current) => without(current, [key]));
              }} />
            {field?.unit && <span className="machine-input-unit">{field.unit}</span>}
            {field && numeric(field) && <button type="button" className="machine-step" aria-label={`${name}: больше`} disabled={!editable}
              onClick={() => stepper(key, field, 1)}>+</button>}
          </>}
      </span>
      {(note || missing || source || extra) && <span className="machine-input-note">
        {note && <span className={`tone-${note.tone}`} role="status">{note.text}</span>}
        {missing ? <span className="tone-warn">в ЭТК не задано — введите</span>
          : source ? <span className="machine-muted">{source}</span> : !note && extra ? <span className="machine-muted">{extra}</span> : null}
      </span>}
    </div>;
  };

  const error = cardError || controlError;
  // Fresh words from the machine: the link is up and the snapshot carries values (the panel on the PC sends none).
  const machine = Object.fromEntries(DRAWN.map((key) => { const value = fields.get(key)?.value; return [key, typeof value === "number" ? value : null]; }));
  const size = (key: string) => decimal(valueOf(key));
  const middle = settledOf("middle_strip") === "1";
  const job = live?.job;
  const problem = control && !old ? (!control.can_edit ? "Изменять значения станка вам не разрешено"
    : runningText || (control.problem ? `Изменения не записываются: ${operatorText(control.problem)}` : "")) : "";
  const picked = choices?.find((item) => item.key === choice) ?? null;
  const shownPlan = due ? due.preview : plan;
  const selectedJob = due?.body ?? jobRequest();
  const differentJob = !!runOpen && !!selectedJob && !sameRun(runOpen, selectedJob);
  const openRunProblem = differentJob ? `На станке идёт задание ${runOpen.label} — сначала завершите или прервите его` : "";
  const openRunningJob = () => {
    if (confirmLoad) { focusRun.current = true; dismissLoad(); }
    else runPanel.current?.focus();
  };
  const shownValues = due?.body.values ?? {};
  const repeatsOpenRun = !!runOpen && shownPlan?.expected_active_run_id === runOpen.id
    && !!due?.body.values && Object.keys(due.body.values).length > 0 && sameRun(runOpen, due.body);
  const missingSizes = repeatsOpenRun ? [] : shownPlan?.rows.filter((row) => row.value === null && shownPlan.blocking.includes(row.label)
    && !(row.key in shownValues)).map((row) => row.label) ?? [];
  const loadProblem = sourceProblem || (incoming && !incoming.action.load_permitted ? "Загрузка задания вам не разрешена" : "")
    || (!canWrite ? refusal() : "") || (!old && (live?.alarm.tone === "danger" || live?.alarm.tone === "warn") ? "Устраните аварию на станке" : "")
    || (missingSizes.length ? `Не заданы параметры: ${missingSizes.join(", ")}` : "")
    || (shownPlan?.marking?.blocking.length ? "Маркировка из ЭТК с ошибкой — исправьте ЭТК" : "")
    || (shownPlan?.group_id && (!shownPlan.expected_source || shownPlan.expected_active_run_id === undefined)
      ? "Обновите план и подтвердите загрузку ещё раз" : "")
    || openRunProblem;
  const planChanges = shownPlan ? planDiff(shownPlan) : {};
  // Partial resends keep the open run's dimensions and identity; the backend still adds ETK marking.
  const automaticKeys = new Set([...(repeatsOpenRun ? [] : ["length_mm", "order_number", "product"]),
    ...Object.keys(shownPlan?.marking ? planValues(shownPlan.marking) : {})]);
  const changes = due?.body.values ? { ...Object.fromEntries(Object.entries(planChanges).filter(([key]) => automaticKeys.has(key))), ...shownValues }
    : planChanges;
  const changedRows = Object.entries(changes).filter(([key]) => fields.has(key)).map(([key, value]) => {
    const field = fields.get(key)!;
    const after = field.kind === "mode" ? MODES.find(([code]) => code === value)?.[1] ?? value
      : field.kind === "bit0" ? SWITCH.find(([code]) => code === value)?.[1] ?? value
      : `${value}${field.unit ? ` ${field.unit}` : ""}`;
    return { key, label: key === "total_set" ? "Тираж задания" : field.label,
      before: key === "total_set" ? `${tirazh ?? EMPTY} шт` : shown(field), after };
  });
  async function confirmJobLoad() {
    if ((!due && (!incoming || !source || !plan)) || loadProblem || !free || loadLock.current) return;
    loadLock.current = true;
    const attempt = ++loadAttempt.current;
    setCheckingLoad(true);
    try {
      if (incoming && source) {
        const result = await productionApi.operationMachineActions(incoming.taskId, incoming.operationId);
        if (!active.current || attempt !== loadAttempt.current) return;
        const current = result.actions.find((item) => item.etk_position === incoming.action.etk_position && item.equipment_id === equipmentId);
        if (!current || JSON.stringify(current.source) !== JSON.stringify(source)) {
          setSourceProblem(current?.blocker || "Задание изменилось — откройте инструкцию заново");
          return;
        }
        const fresh = await loadPlan({ group_id: source.group_id, length: source.length_mm });
        if (!active.current || attempt !== loadAttempt.current) return;
        // Discovery still sees the issued ETK; only its missing-input blocker can be resolved by staged values.
        const suppliedSizes = fresh.blocking.length > 0
          && blockingKeys(fresh).length === fresh.blocking.length
          && blockingKeys(fresh).every((key) => key in shownValues)
          && current.load_permitted && current.blocker === `В ЭТК не задано: ${fresh.blocking.join(", ")}`;
        if (!current.can_load && !suppliedSizes) {
          setMessage({ tone: "warn", text: current.blocker || "Станок сейчас не готов к загрузке" }); return;
        }
        // Include the source and active-run guards: identical values can still come from a newer source.
        if (JSON.stringify([fresh.rows, fresh.job, fresh.quantity, fresh.marking, fresh.blocking, fresh.expected_source, fresh.expected_active_run_id])
          !== JSON.stringify([shownPlan?.rows, shownPlan?.job, shownPlan?.quantity, shownPlan?.marking, shownPlan?.blocking,
            shownPlan?.expected_source, shownPlan?.expected_active_run_id])) {
          setPlan(fresh);
          setDue(null);
          setHeld(null);
          setMessage({ tone: "warn", text: "Параметры задания изменились — проверьте значения и подтвердите ещё раз" });
          return;
        }
      }
      const body = due?.body ?? (source ? { kind: "cutting" as const, group_id: source.group_id, length_mm: source.length_mm } : null);
      if (!body) return;
      if (body.kind === "cutting") {
        if (!shownPlan?.expected_source || shownPlan.expected_active_run_id === undefined) return;
        await sendRun({ ...body, expected_source: shownPlan.expected_source,
          expected_active_run_id: shownPlan.expected_active_run_id }, due?.values ?? planDiff(shownPlan), shownPlan.wire);
      } else if (due) {
        await sendRun({ ...body, expected_active_run_id: due.activeRunId }, due.values, due.wire);
      }
    } catch (reason) {
      setMessage({ tone: "danger", text: failureText(reason, "Задание не загружено") });
    } finally { loadLock.current = false; setCheckingLoad(false); }
  }
  const wireInfo = wires?.find((item) => item.label === wire);
  // Маркировка: режим — с регистров станка (то, что уже уходит на станок, видно сразу); поля — под проводом на чертеже.
  const hasMarking = fields.has("marking");
  const markNow = hasMarking ? markMode(settledOf("marking"), settledOf("mark_by_step")) : null;
  const markText = (key: string, label: string) => <MarkText key={key} id={`ob019-${key}`} label={label}
    name={fields.get(key)?.label ?? label} value={valueOf(key)} dirty={key in drafts} disabled={!fields.has(key) || !canWrite}
    old={old} note={fieldNote(key)} onChange={(value) => setDrafts((current) => ({ ...current, [key]: value }))}
    onCommit={(value) => commitText(key, value)} onCancel={() => setDrafts((current) => without(current, [key]))} />;
  const markRow = hasMarking ? <Ob019MarkingRow mode={markNow} text={markText} number={(key, label) => input(key, label)} /> : null;

  // The job just picked, before its run shows up in «Выполнение задания»: its write in words — «записывается…» or the
  // refusal (said there, not again in «Что режем»; a size still missing is said at the fields).
  const jobPicked = !!choice && choice !== NEW_MANUAL;
  const runSent = !!inFlight && inFlight.id in runWrites.current && runNow?.id !== inFlight.id;
  const refusedPick = jobPicked && message?.tone === "danger" ? message : null;
  const pickNote = !jobPicked ? null : refusedPick ? { tone: "danger", text: refusedPick.text }
    : due && confirmLoad ? { tone: "warn", text: "Проверьте значения и подтвердите загрузку" }
      : planLoading || runSent ? { tone: "info", text: planLoading ? "Расчёт параметров…" : "записывается на станок…" } : null;
  // The machine's last known job, greyed in the empty «Задание» (pult open, nothing picked here).
  const lastJob = runNow?.label || [live?.job?.order_number, live?.job?.product].filter(Boolean).join(" · ");
  // Unknown register meanings remain read-only and hidden; known editable settings stay reachable.
  const extraSettings = [
    { title: "Внутренний слой", keys: [...fields.keys()].filter((key) => key.startsWith("inner_")) },
    { title: "Средняя зачистка", keys: ["middle_strip", ...(middle ? ["middle_end_fix_mm", "middle_dia_fix_mm"] : [])] },
    { title: "Разделение жил", keys: [...fields.keys()].filter((key) => key.startsWith("split_")) },
    { title: "Намотка", keys: [...fields.keys()].filter((key) => key.startsWith("coil_") && key !== "coil_speed") },
    { title: "Настройка маркировки", keys: ["mark_tail_mm", "mark_head_mm", "mark_hold_s", "mark_roll_speed", "mark_char_mm",
      "mark_font", "mark_bold", "mark_divider", "mark_turn_front", "mark_turn_middle", "mark_turn_back", "mark_drop_mm"] },
    { title: "Скорости", keys: SPEED_KEYS },
  ];
  const completed = !!runOpen && runOpen.quantity !== null && runOpen.done !== null && runOpen.done >= runOpen.quantity
    || !runOpen && target !== null && target > 0 && counter !== null && counter >= target;
  return <section className="machine-settings" aria-label="Настройки станка">
    {incoming && <section className="machine-block" aria-label="Задание из инструкции">
      <h3>Операция ЭТК {incoming.action.etk_position} · из задания</h3>
      {sourceProblem && <p className="error-banner" role="alert">{sourceProblem}</p>}
      {planLoading && <p className="empty">Расчёт параметров…</p>}
      {plan && <>
        <p>{plan.number} · {plan.wire} · длина {plan.length_mm} мм · тираж {plan.quantity ?? EMPTY} шт</p>
        {source && <ul>{source.members.map((member) => <li key={`${member.task_id}:${member.operation_id}`}>{member.item} · {member.quantity} шт</li>)}</ul>}
        <button type="button" className="primary" disabled={Boolean(loadProblem) || !free || checkingLoad} onClick={() => setConfirmLoad(true)}>Загрузить в станок</button>
        {loadProblem && !sourceProblem && <p className="warning-inline">{loadProblem}</p>}
        {differentJob && <button type="button" className="secondary" onClick={openRunningJob}>К заданию на станке</button>}
      </>}
    </section>}
    {confirmLoad && (shownPlan || due) && !sourceProblem && <PageWindow label="Загрузить в станок" close={dismissLoad} width={760}>
      <section className="panel machine-card">
      <h2>Загрузить в станок · {inventory}</h2>
      {shownPlan ? <p>{shownPlan.number} · {shownPlan.wire} · {shownPlan.length_mm} мм · {shownPlan.quantity ?? EMPTY} шт</p>
        : due?.body.kind === "wire" ? <p>{due.body.wire} · {due.values.length_mm || runOpen?.length_mm} мм · {due.values.total_set} шт</p>
          : <p>{manualJob?.number} · {manualJob?.wire} · {manualJob?.length_mm} мм · {manualJob?.quantity_planned ?? EMPTY} шт</p>}
      <p>Задание на станке: {due ? due.label || (due.activeRunId ? runNow?.label || "Открытое задание" : "нет")
        : shownPlan?.expected_active_run_id ? runNow?.label || "Открытое задание" : "нет"}</p>
      {source && <ul>{source.members.map((member) => <li key={`${member.task_id}:${member.operation_id}`}>{member.item} · {member.quantity} шт</li>)}</ul>}
      {changedRows.length ? <table className="machine-table" aria-label="Изменённые значения">
        <thead><tr><th>Параметр</th><th>На станке</th><th>Будет записано</th></tr></thead>
        <tbody>{changedRows.map((row) => <tr key={row.key}><th scope="row">{row.label}</th><td className="num">{row.before}</td><td className="num">{row.after}</td></tr>)}</tbody>
      </table> : <p>Параметры совпадают. Будет загружено выбранное задание.</p>}
      {loadProblem && <p className="warning-inline">{loadProblem}</p>}
      {message && <p className={`tone-${message.tone}`} role="status">{message.text}</p>}
      <div className="machine-actions">
        {differentJob && <button type="button" className="secondary" disabled={sending} onClick={openRunningJob}>К заданию на станке</button>}
        <button type="button" className="secondary" disabled={sending} onClick={dismissLoad}>Отмена</button>
        <button type="button" className="primary" disabled={Boolean(loadProblem) || !free || checkingLoad} onClick={() => void confirmJobLoad()}>
          {checkingLoad ? "Проверка…" : "Подтвердить загрузку"}</button>
      </div>
      </section>
    </PageWindow>}
    {confirmReset && <PageWindow label="Новый тираж" close={() => { if (!sending) setConfirmReset(null); }} width={620}>
      <section className="panel machine-card">
        <h2>Новый тираж</h2>
        <p>{confirmReset.description}</p>
        <p>Тираж станет 0, маркировка и связь с заданием будут сняты. Общий счётчик станка сохранится.</p>
        {message?.tone === "danger" && <p className="error-banner" role="alert">{message.text}</p>}
        <div className="machine-actions">
          <button type="button" className="secondary" disabled={sending} onClick={() => setConfirmReset(null)}>Отмена</button>
          <button type="button" className="primary" disabled={!canWrite || !free || sending} onClick={() => void resetJob()}>Подтвердить новый тираж</button>
        </div>
      </section>
    </PageWindow>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div ref={runPanel} tabIndex={-1} aria-label="Текущее задание"><Ob019RunJob inventory={inventory} equipmentId={equipmentId} picker={false} live={live} state={runState} choices={choices}
      choice={choice} onPick={pickJob} reload={reloadRun} pickNote={pickNote}
      onStarted={(job) => { clearTimeout(pickTimer.current); setStarted(job); setChoice(manualKey(job.id)); setWire(""); query.current = null;
        setPlan(null); pickManual(job); }} /></div>
    <section className="machine-block machine-what" aria-label="Что режем">
      <h3>Что режем</h3>
      <div className={`machine-way${wire ? " is-on" : ""}`}>
        <span className="machine-way-title">Провод вручную</span>
        <Ob019WirePicker wires={wires} value={wire || plan?.wire || ""} onPick={pickWire}
          disabled={!!runOpen || Boolean(incoming) || busyWriting || !!resetPending} />
        {runOpen && <span className="machine-muted">Сменить провод — «Новый тираж»</span>}
        {wireInfo && <span className="machine-muted">
          {[wireInfo.conductor_mm ? `Ø жилы ${wireInfo.conductor_mm} мм` : "", wireInfo.has_recipe ? "есть настройка" : "настройки ещё нет"].filter(Boolean).join(" · ")}
        </span>}
      </div>
      <div className="machine-job-row">
        {input("total_set", "Тираж",
          runOpen && runOpen.done !== null && runOpen.quantity !== null
            ? `сделано ${formatNumber(runOpen.done)} · осталось ${formatNumber(Math.max(runOpen.quantity - runOpen.done, 0))}` : "")}
        {input("batch_set", "Пучок", typeof job?.batch_finished === "number" ? `сделано ${formatNumber(job.batch_finished)}` : "")}
        {input("mode", "Режим")}
        {fields.has("batch_delay_s") && input("batch_delay_s", "Пауза между пучками")}
      </div>
      {<div className="machine-actions">
        {wire && held && !plan?.group_id && <button type="button" className="primary" disabled={!canWrite || !free || planLoading}
          onClick={() => void prepareWire()}>Загрузить в станок</button>}
        <button type="button" className="secondary" disabled={!canWrite || !free || !!resetPending}
          onClick={() => setConfirmReset({ activeRunId: runOpen?.id ?? null, state: control?.state ?? null,
            description: runOpen ? `${runOpen.label}: сделано ${runOpen.done ?? EMPTY} шт. Результат сохранится в журнале.`
              : `Текущий тираж: осталось ${left ?? EMPTY} шт.` })}>Новый тираж</button>
        {completed && <span className="machine-muted">Тираж готов — подготовьте новый</span>}
        {resetPending && <span className="machine-muted" role="status">Подготовка нового тиража…</span>}
      </div>}
      {planLoading && !choice && <p className="machine-muted">Считаю значения…</p>}
      {plan && blockingKeys(plan).some((key) => !(key in (held ?? {}))) && held !== null && <p className="warning-inline" role="status">
        В ЭТК не задано: {plan.blocking.join(", ")} — введите на чертеже и подтвердите загрузку
      </p>}
      {message && !refusedPick && <p className={`machine-message tone-${message.tone}`} role="status">{message.text}</p>}
    </section>

    <section className="machine-block" aria-label="Задание резки">
      <h3>Задание</h3>
      <label className="machine-pick"><span>Из ЭТК — необязательно</span>
        <JobSelect id="ob019-job" choices={choices} manual={runManual} open={!!runOpen} choice={choice} last={lastJob}
          onPick={pickJob} disabled={Boolean(incoming) || busyWriting || !!resetPending} />
      </label>
      {picked && plan && <span className="machine-muted">Заказ {plan.job.order_number || EMPTY} · провод {plan.wire}</span>}
      {!incoming && due?.body.kind === "cutting" && !confirmLoad && <button type="button" className="secondary"
        onClick={() => setConfirmLoad(true)}>Загрузить в станок</button>}
    </section>

    {problem && <p className="warning-inline" role="status">{problem}</p>}
    <Ob019WireDrawing middle={middle} field={drawn} markRow={null}
      marks={{ mode: markNow, count: markCount(size("length_mm"), size("mark_step_mm")) }}
      sizes={{ strip_front: size("strip_front_mm"), pull_front: size("pull_front_mm"), strip_back: size("strip_back_mm"),
        pull_back: size("pull_back_mm") }} />
    {(hasMarking || plan?.marking) && <>
      <Ob019MarkingBlock mode={markNow} disabled={!hasMarking || !canWrite || !!resetPending} old={old}
        note={fieldNote("marking")} plan={plan?.marking ?? null} onPick={(mode) => commitBatch(modeValues(mode, settledOf))} />
      {markRow}
    </>}
    {control && <Ob019Correction inventory={inventory} control={control} plan={plan} machine={machine} linkUp={linkUp}
      onSaved={afterCorrection} />}
    {extraSettings.map(({ title, keys }) => keys.some((key) => fields.has(key)) && <details key={title} className="machine-extra">
      <summary>{title}</summary>
      <div className="machine-extra-fields">{keys.filter((key) => fields.has(key)).map((key) => input(key, fields.get(key)!.label))}</div>
    </details>)}
    {recipeNote && <p className={`machine-message tone-${recipeNote.tone}`} role="status">{recipeNote.text}</p>}

    {toolsWindow === "journal" && <PageWindow label="Журнал станка" close={closeTools} width={1000} dismissable>
      <section className="panel machine-card">
      <header className="machine-card-head"><h2>Журнал станка</h2><button type="button" className="secondary small" onClick={closeTools}>Закрыть</button></header>
      {controlError && <p className="error-banner" role="alert">{controlError}</p>}
      {!control && !controlError && <p className="empty">Загрузка…</p>}
      {control && !commands.length && <p className="empty">Записей пока нет</p>}
      <ul className="machine-commands" aria-label="Последние записи">
        {commands.slice(0, 10).map((command) => <CommandLine key={command.id} command={command} />)}
      </ul>
      {equipmentId && <WorkJournal equipmentId={equipmentId} />}
      </section>
    </PageWindow>}
  </section>;
}
