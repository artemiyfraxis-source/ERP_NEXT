import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { failureText } from "./api";
import { equipmentSetupApi } from "./equipmentSetupApi";
import { EMPTY, formatMoment, formatNumber } from "./format";
import {
  clock, commandResult, IDLE_STATES, machineControlApi, operatorText, type MachineCommand, type MachineRun, type MachineRunState,
  type MachineWindow, type RunRequest,
  type SettingField, type TransferChoice, type TransferPlan, type TransferRow, type WireChoice,
} from "./machineControlApi";
import { usePolled, type Ob019Live } from "./machinesApi";
import type { ManualJob } from "./manualJobsApi";
import { Ob019Correction } from "./Ob019Correction";
import { JobSelect, NEW_MANUAL, Ob019RunJob, manualKey } from "./Ob019RunJob";
import { MARK_KEYS, MarkText, latin, latinLength, markMode, modeValues, Ob019MarkingBlock, Ob019MarkingRow, planValues,
  TEXT_CHARS } from "./Ob019MarkingBlock";
import { markCount, Ob019WireDrawing } from "./Ob019WireDrawing";
import { Ob019WirePicker } from "./Ob019WirePicker";

/** Настройки карточки ОБ.019 вживую (владелец 19.11.2026: «хочу в лайве менять данные: чертёжик, там данные
 *  актуальные, я их меняю, и они на станке так же меняются»). Поля чертежа и строки задания показывают то, что сейчас
 *  на станке; изменённое поле пишется на станок сразу по Enter, уходу из поля или кнопкам −/+ — без диалога и без
 *  отдельной кнопки; рядом с полем — «записывается…», «✓ на станке» или «отказ: причина». Выбор задания из ЭТК или
 *  провода сразу пишет их значения (выбор в списке — через 0,8 с после последнего изменения: стрелки по закрытому
 *  списку в Chrome на Windows меняют значение на каждом шаге). После своих правок параметров провода значения станка
 *  сохраняются как настройка провода (одна запись на серию правок), только если последняя запись для этого провода
 *  прошла. Записи идут по одной: следующая ждёт, пока станок закончит предыдущую (её итог виден в опросе). Пока станок
 *  режет, поля чертежа закрыты с одной причиной над чертежом.
 *  Задание (резка из ЭТК или ручная работа) — это прогон станка (Ob019RunJob.tsx, сервер production/machine_runs.py):
 *  выбор пишет его через `POST …/run`, и только такой прогон с тиражом на станке открывает «Пуск». Задание ЭТК, где не
 *  задан размер, не пишется, пока размер не введён на чертеже: тогда уходит одной записью прогона со всеми значениями.
 *  «Тираж» — штук в задании: при открытом прогоне — тираж задания (под полем «сделано · осталось»; станку пишется цель
 *  D22 = счётчик D649 + (тираж − сделано)), без него — сколько станку осталось до цели; правка тиража при выбранном
 *  задании тоже идёт прогоном. Пока прогон открыт, «Провод вручную» закрыт: провод поверх задания станок посчитал бы в
 *  задание (сервер тоже не даст «Пуск»: machine_runs.start_blocker).
 *  «Остальные параметры» (владелец 20.11.2026: «можем сделать уже полноценную настройку»): каждая строка — «Задать»
 *  (поле, пишется так же, как чертёж) рядом с «На станке»; отличие подсвечено. «только просмотр» — лишь у регистров,
 *  которые ERP не пишет (счётчики, программа, назначение не установлено). Без свежих данных станка таблицы нет — одна
 *  строка вместо столбца прочерков. */

const MODES: [string, string][] = [["0", "Пошаговый"], ["1", "Один цикл"], ["2", "Автомат"]];
/** Переключатель страницы пульта (бит 0 слова): сервер меняет только этот бит. */
const SWITCH: [string, string][] = [["1", "Вкл"], ["0", "Выкл"]];
/** Поля на чертеже и в строке задания; остальные — в «Остальных параметрах» (ролики — в строке команд). */
const DRAWN = ["length_mm", "strip_front_mm", "pull_front_mm", "strip_back_mm", "pull_back_mm", "diameter_mm", "knife_back_mm"];
const JOB_ROW = ["total_set", "batch_set", "mode"];
/** Стягивание на чертеже — переключатель режима; точное значение правится в «Остальных параметрах». */
const PULL_OF: Record<string, string> = { strip_front_mm: "pull_front_mm", strip_back_mm: "pull_back_mm" };
/** Маркировка — свой блок и поля под проводом; шрифт, скорости, поворот и прочее — в «Остальных параметрах». */
const ON_TOP = new Set([...DRAWN.filter((key) => !Object.values(PULL_OF).includes(key)), ...JOB_ROW, "feed_wheel", ...MARK_KEYS]);
export type PullMode = "full" | "half" | "none";
/** Режим по значениям станка; «own» — своё стягивание, не по правилу режима (правка зачистки его не трогает). */
export type PullState = PullMode | "own";
const PULL_MODES: [PullMode, string][] = [["full", "Стягивание"], ["half", "Полу"], ["none", "Без"]];
/** Что хранит настройка провода (рецепт ОБ.019): всё с чертежа, кроме длины. */
const RECIPE_KEYS = DRAWN.filter((key) => key !== "length_mm");
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
/** «Остальные параметры» без свежих данных станка: одна строка вместо таблицы прочерков. */
export const NO_DATA = "Нет связи со станком — параметры появятся, когда ERP получит данные";
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
/** Прогон, ждущий своей очереди: станок ещё пишет предыдущее. */
type RunDue = { body: RunRequest; values: Record<string, string>; wire?: string };

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
    <span className="machine-muted">{command.created_by} · {formatMoment(command.created_at)}</span>
    {command.error && <span className="tone-danger">{operatorText(command.error)}</span>}
  </li>;
}

const without = <T,>(values: Record<string, T>, keys: string[]) =>
  Object.fromEntries(Object.entries(values).filter(([key]) => !keys.includes(key)));

/** Тот же прогон: резка — группа и длина, ручная работа — сама работа. */
function sameRun(run: MachineRun, job: RunRequest): boolean {
  if (job.kind === "manual") return run.kind === "manual" && run.job_id === job.job_id;
  return run.kind === "cutting" && run.group_id === job.group_id
    && Number((run.length_mm ?? "").replace(",", ".")) === Number(job.length_mm.replace(",", "."));
}

/** `onRun` — прогон станка, как его видит эта панель (карточка показывает по нему строку состояния и «Пуск»). */
export function Ob019SettingsPanel({ inventory, equipmentId, live = null, old = false, onRun }: {
  inventory: string; equipmentId?: string; live?: Ob019Live | null; old?: boolean;
  onRun?: (state: MachineRunState | null) => void;
}) {
  const [fast, setFast] = useState(false);
  const period = fast ? FAST_MS : IDLE_MS;
  const loadWindow = useCallback((signal: AbortSignal) => machineControlApi.window(inventory, signal), [inventory]);
  const loadControl = useCallback((signal: AbortSignal) => machineControlApi.control(inventory, signal), [inventory]);
  const { data: card, error: cardError } = usePolled<MachineWindow>(loadWindow, period, "Не удалось загрузить настройки станка");
  const { data: control, error: controlError } = usePolled(loadControl, period, "Не удалось загрузить запись на станок");
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
  const [group, setGroup] = useState(0);
  const [more, setMore] = useState(false);
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
  const middle = useMemo(() => (card?.settings ?? []).find((item) => item.title === "Средняя зачистка")
    ?.fields.find((field) => field.kind === "bit0")?.value === true, [card]);
  const commands = Array.isArray(control?.commands) ? control!.commands : [];
  const open = commands.find((row) => OPEN.includes(row.status));
  // The machine is in a cycle (or not ready): drawing fields closed with one reason, not a refusal per field. The card's
  // own live state counts too — it may be fresher than this panel's poll.
  const notIdle = (state: number | null | undefined) => typeof state === "number" && !IDLE_STATES.includes(state);
  const busyState = !old && control ? (notIdle(control.state) ? control.state : notIdle(live?.state.code) ? live!.state.code : null) : null;
  const linkProblem = !!control?.problem && !control.problem.startsWith(STATE_PROBLEM);
  const runningText = busyState === null || linkProblem ? ""
    : RUNNING_STATES.includes(busyState) ? "Станок режет — правка после остановки" : "Станок не готов — правка после сброса";
  const canWrite = !!control && control.can_edit && !control.problem && !old && busyState === null;
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
  const left = counter !== null && target !== null ? target - counter : null;
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
      if ((row && !OPEN.includes(row.status)) || Date.now() - inFlight.at > IN_FLIGHT_MAX_MS) setInFlight(null);
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
    || Object.keys(queue).length > 0 || !!inFlight || !!due;
  const free = !open && !inFlight && !sending && Object.keys(queue).length === 0;
  const refusal = () => old ? "нет связи со станком" : !control?.can_edit ? "запись вам не разрешена"
    : runningText ? runningText.toLowerCase() : operatorText(control?.problem ?? "");
  /** Выбранное задание как прогон станка: правка тиража при нём идёт прогоном (сервер считает цель от счётчика). */
  const jobRequest = (): RunRequest | null => query.current?.group_id
    ? { kind: "cutting", group_id: query.current.group_id, length_mm: query.current.length }
    : manualJob ? { kind: "manual", job_id: manualJob.id }
      // Nothing picked here (the card was reopened) but a job is open on the machine: «Тираж» goes as its run, so the
      // run's target follows and «Пуск» is not refused («Тираж на станке не совпадает с заданием»).
      : !choice && !query.current && runOpen
        ? runOpen.kind === "manual" && runOpen.job_id ? { kind: "manual", job_id: runOpen.job_id }
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
    setSending(true);
    (job ? machineControlApi.startRun(inventory, { ...job, values }, control.state) : machineControlApi.write(inventory, values, control.state))
      .then((command) => {
        setInFlight({ id: command.id, at: Date.now() });
        if (job) reloadRun();
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
  async function sendRun(body: RunRequest, values: Record<string, string>, wire?: string) {
    const keys = Object.keys(values);
    setSending(true);
    setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { kind: "queued", value: values[key] }])) }));
    try {
      const command = await machineControlApi.startRun(inventory, body, control?.state ?? null);
      runWrites.current[command.id] = wire;
      setInFlight({ id: command.id, at: Date.now() });
      setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) =>
        [key, { kind: "writing", value: values[key], id: command.id, wire, plan: true }])) }));
      reloadRun();
    } catch (reason) {
      const text = operatorText(failureText(reason, "не записано"));
      markWire(wire, "failed", true);
      if (keys.length) setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { kind: "failed", value: values[key], text }])) }));
      else setMessage({ tone: "danger", text: `Задание не передано: ${text}` });
    } finally {
      setSending(false);
    }
  }

  /** Прогон сейчас, а если станок ещё пишет предыдущее — следом за ним. */
  function runJob(body: RunRequest, values: Record<string, string>, wire?: string) {
    if (!canWrite) {
      markWire(wire, "failed", true);
      setMessage({ tone: "warn", text: `Не записано: ${refusal()}` });
      return;
    }
    markWire(wire, "pending", true);
    if (!free) {
      setDue({ body, values, wire });
      setStatus((current) => ({ ...current, ...Object.fromEntries(Object.keys(values).map((key) => [key, { kind: "queued", value: values[key] }])) }));
      return;
    }
    void sendRun(body, values, wire);
  }

  useEffect(() => {
    if (!due || !control) return;
    if (!canWrite) {
      const why = refusal();
      markWire(due.wire, "failed", true);
      const keys = Object.keys(due.values);
      setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { kind: "failed", value: due.values[key], text: why }])) }));
      if (!keys.length) setMessage({ tone: "warn", text: `Не записано: ${why}` });
      setDue(null);
      return;
    }
    if (!free) return;
    setDue(null);
    void sendRun(due.body, due.values, due.wire);
  }, [due, control, canWrite, free]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Значение без набираемого: то, что уже уходит на станок, иначе то, что на станке. */
  const liveOf = (key: string) => key === "total_set" ? (tirazh === null ? "" : String(tirazh)) : liveText(fields.get(key));
  const settledOf = (key: string) => {
    const st = status[key];
    return st && (st.kind === "queued" || st.kind === "held" || st.kind === "writing") ? st.value : liveOf(key);
  };
  /** Отличается ли значение от станка; «Тираж» — от того, сколько станку осталось до цели. */
  const differs = (key: string, value: string) => key === "total_set"
    ? !!value.trim() && (tirazh === null || decimal(value) !== tirazh) : enteredDiffers(fields.get(key), value);
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
    if (!field || !value || !differs(key, value)) return;
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
      if (pull && enteredDiffers(fields.get(pullKey), pull)) values[pullKey] = pull;
    }
    const keys = Object.keys(values);
    const wire = plan?.wire;
    if (held && plan?.group_id) {
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
    const missing = RECIPE_KEYS.filter((key) => typeof now.get(key)?.value !== "number");
    if (missing.length) {
      setRecipeNote({ tone: "danger", text: `Настройка провода не сохранена: станок не прислал ${missing.map((key) => now.get(key)?.label ?? key).join(", ")}` });
      return;
    }
    try {
      const proposal = await equipmentSetupApi.ob019Proposal(machine, job.group_id
        ? { group_id: job.group_id, length_mm: job.length_mm.replace(",", ".") } : { wire: job.wire });
      const saved = await equipmentSetupApi.ob019SaveVersion(machine, {
        ...proposal.publish, wire: proposal.wire,
        values: Object.fromEntries(RECIPE_KEYS.map((key) => [key, String(now.get(key)!.value)])),
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
    const missing = blockingKeys(next).filter((key) => !(key in typed));
    if (missing.length) {
      setHeld(typed);
      setStatus((current) => ({ ...current, ...Object.fromEntries(Object.keys(values).map((key) => [key, { kind: "held", value: values[key] }])) }));
      return;
    }
    setHeld(null);
    const all: Record<string, string> = { ...planDiff(next), ...typed };
    // The job's own quantity, unless the operator typed «Тираж» meanwhile.
    if (fields.has("total_set") && next.quantity !== null && !("total_set" in typed)) all.total_set = String(next.quantity);
    runJob({ kind: "cutting", group_id: next.group_id!, length_mm: query.current?.length ?? next.length_mm, values: all }, all, next.wire);
  }

  /** Значения задания или провода — сразу на станок: задание ЭТК — прогоном станка (сервер считает значения, тираж и
   *  маркировку сам и начинает счёт), провод — одной записью отличающихся полей. Такие записи настройку провода не
   *  сохраняют, но их итог решает, можно ли её потом сохранить. */
  async function writePlan(next: TransferPlan) {
    const values = planDiff(next);
    const wire = next.wire;
    if (next.group_id) {
      if (markingRefused(next)) return;
      // A size missing in the ETK: nothing is written until it is typed on the drawing (`holdForJob`).
      if (next.blocking.length) {
        markWire(wire, "pending", true);
        setHeld({});
        return;
      }
      runJob({ kind: "cutting", group_id: next.group_id, length_mm: query.current?.length ?? next.length_mm }, values, wire);
      return;
    }
    const keys = Object.keys(values);
    if (!keys.length) {
      markWire(wire, "done", true);
      setMessage({ tone: "info", text: "Станок уже настроен на это" });
      return;
    }
    if (!canWrite) {
      markWire(wire, "failed", true);
      setMessage({ tone: "warn", text: `Не записано: ${refusal()}` });
      return;
    }
    markWire(wire, "pending", true);
    setQueue((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { value: values[key], recipe: false, wire, plan: true }])) }));
    setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { kind: "queued", value: values[key] }])) }));
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
      // The latest render's writePlan: open/inFlight/queue as they are now, not as they were when the pick started.
      await writePlanRef.current(answer);
    } catch (reason) {
      if (query.current === next) setMessage({ tone: "danger", text: failureText(reason, "Не удалось посчитать значения") });
    } finally {
      if (query.current === next) setPlanLoading(false);
    }
  }
  const pickRef = useRef(pick);
  pickRef.current = pick;
  /** Выбор задания в списке: значение видно сразу, на станок — через 0,8 с после последнего изменения (стрелки по
   *  закрытому списку в Chrome на Windows дают изменение на каждом шаге — пишется только итог). */
  const pickJob = (key: string) => {
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
  /** Ручная работа — сразу на станок прогоном: длину, остаток тиража и номер для станка сервер берёт из работы. */
  const pickManual = (job: ManualJob) => {
    setMessage(null);
    setRecipeNote(null);
    const run = runNow && runNow.kind === "manual" && runNow.job_id === job.id && runNow.stage !== "closed" ? runNow : null;
    const done = run?.done ?? 0;
    if (done && job.quantity_planned) setMessage({ tone: "info", text: `Уже сделано ${done} из ${job.quantity_planned} — на станок уходит остаток` });
    runJob({ kind: "manual", job_id: job.id }, {});
  };
  const pickManualRef = useRef(pickManual);
  pickManualRef.current = pickManual;
  const pickWire = (label: string) => {
    if (runOpen) return;  // the picker is closed while a run is open
    clearTimeout(pickTimer.current);
    setWire(label);
    setChoice("");
    setHeld(null);
    setDue(null);
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
    if (st.kind === "held") return { tone: "warn", text: "уйдёт с заданием, когда введены все размеры" };
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
    const editable = !!field && canWrite;
    const note = fieldNote(key);
    const row = plan?.rows.find((item) => item.key === key);
    const missing = !!row && row.value === null && !!plan?.blocking.includes(row.label);
    const source = row && field && !enteredDiffers(field, row.value ?? "") ? sourceText(row, conductor, recipeAt) : "";
    const value = valueOf(key);
    const name = field?.label ?? label;
    const writing = note?.text === "записывается…";
    return <div key={key} className={`machine-input${writing ? " is-writing" : ""}${missing ? " is-missing" : ""}${old ? " is-old" : ""}`}>
      <label className="machine-input-label" htmlFor={`ob019-${key}`}>{label}</label>
      <span className="machine-input-box">
        {field?.kind === "mode"
          ? <select id={`ob019-${key}`} aria-label={name} value={value} disabled={!editable}
            onChange={(event) => commitLater(key, event.target.value, SELECT_WAIT_MS)}>
            {!value && <option value="">{EMPTY}</option>}
            {MODES.map(([code, text]) => <option key={code} value={code}>{text}</option>)}
          </select>
          : <>
            {field && numeric(field) && <button type="button" className="machine-step" aria-label={`${name}: меньше`} disabled={!editable}
              onClick={() => stepper(key, field, -1)}>−</button>}
            <input id={`ob019-${key}`} aria-label={name} value={value} disabled={!editable} inputMode="decimal" maxLength={12}
              autoComplete="off" placeholder={field?.read ? "" : EMPTY}
              onChange={(event) => setDrafts((current) => ({ ...current, [key]: event.target.value }))}
              onBlur={() => { if (key in drafts) commit(key, drafts[key]); }}
              onKeyDown={(event) => {
                if (event.key === "Enter") { event.preventDefault(); commit(key, (event.target as HTMLInputElement).value); }
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
  const groups = (card?.settings ?? []).map((item) => ({ ...item, fields: item.fields.filter((field) => !field.key || !ON_TOP.has(field.key)) }))
    .filter((item) => item.fields.length > 0);
  const current = groups[Math.min(group, Math.max(groups.length - 1, 0))];
  // Fresh words from the machine: the link is up and the snapshot carries values (the panel on the PC sends none).
  const hasData = !old && (card?.settings ?? []).some((item) => item.fields.some((field) => field.value !== null && field.value !== undefined));
  const machine = Object.fromEntries(DRAWN.map((key) => { const value = fields.get(key)?.value; return [key, typeof value === "number" ? value : null]; }));
  const size = (key: string) => decimal(valueOf(key));
  const job = live?.job;
  const problem = control && !old ? (!control.can_edit ? "Изменять значения станка вам не разрешено"
    : runningText || (control.problem ? `Изменения не записываются: ${operatorText(control.problem)}` : "")) : "";
  const picked = choices?.find((item) => item.key === choice) ?? null;
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
    : planLoading || !!due || runSent ? { tone: "info", text: "записывается на станок…" } : null;
  // The machine's last known job, greyed in the empty «Задание» (pult open, nothing picked here).
  const lastJob = runNow?.label || [live?.job?.order_number, live?.job?.product].filter(Boolean).join(" · ");
  return <section className="machine-settings" aria-label="Настройки станка">
    {error && <div className="error-banner" role="alert">{error}</div>}
    <Ob019RunJob inventory={inventory} equipmentId={equipmentId} picker={false} live={live} state={runState} choices={choices}
      choice={choice} onPick={pickJob} reload={reloadRun} pickNote={pickNote}
      onStarted={(job) => { clearTimeout(pickTimer.current); setStarted(job); setChoice(manualKey(job.id)); setWire(""); query.current = null;
        setPlan(null); pickManual(job); }} />
    <section className="machine-block machine-what" aria-label="Что режем">
      <h3>Что режем</h3>
      <div className="machine-ways">
        <div className={`machine-way${choice ? " is-on" : ""}`}>
          <label className="machine-way-title" htmlFor="ob019-job">Задание</label>
          <JobSelect id="ob019-job" choices={choices} manual={runManual} open={!!runNow && runNow.stage !== "closed"} choice={choice} last={lastJob}
            onPick={pickJob} />
          {picked && plan && <span className="machine-muted">Заказ {plan.job.order_number || EMPTY} · провод {plan.wire}</span>}
          {manualJob && <span className="machine-muted">На станок: заказ {manualJob.plc_number}</span>}
        </div>
        <div className={`machine-way${wire ? " is-on" : ""}`}>
          <span className="machine-way-title">Провод вручную</span>
          <Ob019WirePicker wires={wires} value={wire} onPick={pickWire} disabled={!!runOpen} />
          {runOpen && <span className="machine-muted">Открыто задание «{runOpen.label}» — провод вручную после «Завершить»</span>}
          {wireInfo && <span className="machine-muted">
            {[wireInfo.conductor_mm ? `Ø жилы ${wireInfo.conductor_mm} мм` : "", wireInfo.has_recipe ? "есть настройка" : "настройки ещё нет"].filter(Boolean).join(" · ")}
          </span>}
        </div>
      </div>
      <div className="machine-job-row">
        {input("total_set", "Тираж (штук в этом задании)",
          runOpen && runOpen.done !== null && runOpen.quantity !== null
            ? `сделано ${formatNumber(runOpen.done)} · осталось ${formatNumber(Math.max(runOpen.quantity - runOpen.done, 0))}` : "")}
        {input("batch_set", "Пучок", typeof job?.batch_finished === "number" ? `сделано ${formatNumber(job.batch_finished)}` : "")}
        {input("mode", "Режим")}
      </div>
      {planLoading && !choice && <p className="machine-muted">Считаю значения…</p>}
      {plan && plan.blocking.length > 0 && held !== null && <p className="warning-inline" role="status">
        В ЭТК не задано: {plan.blocking.join(", ")} — введите на чертеже; задание уйдёт на станок, когда все размеры заданы
      </p>}
      {message && !refusedPick && <p className={`machine-message tone-${message.tone}`} role="status">{message.text}</p>}
    </section>

    {problem && <p className="warning-inline" role="status">{problem}</p>}
    {(hasMarking || plan?.marking) && <Ob019MarkingBlock mode={markNow} disabled={!hasMarking || !canWrite} old={old}
      note={fieldNote("marking")} plan={plan?.marking ?? null} onPick={(mode) => commitBatch(modeValues(mode, settledOf))} />}
    <Ob019WireDrawing middle={middle} field={drawn} markRow={markRow}
      marks={{ mode: markNow, count: markCount(size("length_mm"), size("mark_step_mm")) }}
      sizes={{ strip_front: size("strip_front_mm"), pull_front: size("pull_front_mm"), strip_back: size("strip_back_mm"),
        pull_back: size("pull_back_mm") }} />
    {recipeNote && <p className={`machine-message tone-${recipeNote.tone}`} role="status">{recipeNote.text}</p>}

    {control && <Ob019Correction inventory={inventory} control={control} plan={plan} machine={machine} linkUp={linkUp}
      onSaved={afterCorrection} />}

    {groups.length > 0 && <div className="machine-block machine-more">
      <button type="button" className="machine-more-toggle" aria-expanded={more} onClick={() => setMore(!more)}>
        <span aria-hidden="true">{more ? "▾" : "▸"}</span> Остальные параметры
        <span className="machine-muted"> · {groups.map((item) => item.title).join(", ")}</span>
      </button>
      {more && !hasData && <p className="machine-muted machine-nodata" role="status">{NO_DATA}</p>}
      {more && hasData && <>
        <div className="seg-tabs" role="tablist" aria-label="Страницы пульта">
          {groups.map((item, index) => <button key={item.title} role="tab" aria-selected={index === group}
            className={index === group ? "on" : undefined} onClick={() => setGroup(index)}>{item.title}</button>)}
        </div>
        {current && <table className="machine-table machine-params" aria-label={current.title}>
          <thead><tr><th>Параметр</th><th>Задать</th><th>На станке</th><th aria-label="Итог записи"></th></tr></thead>
          <tbody>{current.fields.map((field) => {
            const key = field.key;
            const note = key ? fieldNote(key) : null;
            const value = key ? valueOf(key) : "";
            const differs = !!key && field.read && enteredDiffers(field, value);
            return <tr key={`${field.label}-${field.kind}`}
              className={[note?.text === "записывается…" ? "is-writing" : "", differs ? "is-diff" : ""].filter(Boolean).join(" ") || undefined}>
              <th scope="row">{field.label}</th>
              <td>{!key ? <span className="machine-muted">только просмотр</span>
                : field.kind === "bit0"
                ? <select aria-label={field.label} value={value} disabled={!canWrite}
                  onChange={(event) => commitLater(key, event.target.value, SELECT_WAIT_MS)}>
                  {!value && <option value="">{EMPTY}</option>}
                  {SWITCH.map(([code, text]) => <option key={code} value={code}>{text}</option>)}
                </select>
                : field.kind === "mode"
                ? <select aria-label={field.label} value={value} disabled={!canWrite}
                  onChange={(event) => commitLater(key, event.target.value, SELECT_WAIT_MS)}>
                  {!value && <option value="">{EMPTY}</option>}
                  {MODES.map(([code, text]) => <option key={code} value={code}>{text}</option>)}
                </select>
                : <span className={`machine-param-input${field.kind === "text15" ? " is-text" : ""}`}>
                  <input aria-label={field.label} value={value} autoComplete="off" disabled={!canWrite}
                    inputMode={field.kind === "text15" ? "text" : "decimal"} maxLength={field.kind === "text15" ? 30 : 12}
                    placeholder={field.kind === "text15" ? "латиница, до 30 знаков" : ""}
                    onChange={(event) => setDrafts((values) => ({ ...values, [key]: event.target.value }))}
                    onBlur={() => { if (key in drafts) commit(key, drafts[key]); }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") { event.preventDefault(); commit(key, (event.target as HTMLInputElement).value); }
                      if (event.key === "Escape") setDrafts((values) => without(values, [key]));
                    }} />
                  {field.unit && <span className="machine-input-unit">{field.unit}</span>}
                </span>}</td>
              <td className="machine-param-live">{field.read ? shown(field) : EMPTY}</td>
              <td>{note && <span className={`tone-${note.tone}`} role="status">{note.text}</span>}</td>
            </tr>;
          })}</tbody>
        </table>}
      </>}
    </div>}

    {commands.length > 0 && <details className="machine-fold machine-journal">
      <summary>Журнал станка</summary>
      <ul className="machine-commands" aria-label="Последние записи">
        {commands.slice(0, 10).map((command) => <CommandLine key={command.id} command={command} />)}
      </ul>
    </details>}
  </section>;
}
