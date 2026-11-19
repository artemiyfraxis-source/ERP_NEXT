import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { failureText } from "./api";
import { equipmentSetupApi } from "./equipmentSetupApi";
import { EMPTY, formatMoment, formatNumber } from "./format";
import {
  clock, commandResult, machineControlApi, operatorText, type MachineCommand, type MachineWindow, type SettingField,
  type TransferChoice, type TransferPlan, type TransferRow, type WireChoice,
} from "./machineControlApi";
import { usePolled, type Ob019Live } from "./machinesApi";
import { Ob019Correction } from "./Ob019Correction";
import { Ob019WireDrawing } from "./Ob019WireDrawing";
import { Ob019WirePicker } from "./Ob019WirePicker";

/** Настройки карточки ОБ.019 вживую (владелец 19.11.2026: «хочу в лайве менять данные: чертёжик, там данные
 *  актуальные, я их меняю, и они на станке так же меняются»). Поля чертежа и строки задания показывают то, что сейчас
 *  на станке; изменённое поле пишется на станок сразу по Enter, уходу из поля или кнопкам −/+ — без диалога и без
 *  отдельной кнопки; рядом с полем — «записывается…», «✓ на станке» или «отказ: причина». Выбор задания из ЭТК или
 *  провода сразу пишет их значения. После своих правок значения станка сохраняются как настройка провода
 *  (одна запись на серию правок). Записи идут по одной: следующая ждёт, пока станок примет предыдущую. */

const MODES: [string, string][] = [["0", "Пошаговый"], ["1", "Один цикл"], ["2", "Автомат"]];
/** Переключатель страницы пульта (бит 0 слова): сервер меняет только этот бит. */
const SWITCH: [string, string][] = [["1", "Вкл"], ["0", "Выкл"]];
/** Поля на чертеже и в строке задания; остальные — в «Остальных параметрах» (ролики — в строке команд). */
const DRAWN = ["length_mm", "strip_front_mm", "pull_front_mm", "strip_back_mm", "pull_back_mm", "diameter_mm", "knife_back_mm"];
const JOB_ROW = ["total_set", "batch_set", "mode"];
/** Стягивание на чертеже — переключатель режима; точное значение правится в «Остальных параметрах». */
const PULL_OF: Record<string, string> = { strip_front_mm: "pull_front_mm", strip_back_mm: "pull_back_mm" };
const ON_TOP = new Set([...DRAWN.filter((key) => !Object.values(PULL_OF).includes(key)), ...JOB_ROW, "feed_wheel"]);
export type PullMode = "full" | "half" | "none";
const PULL_MODES: [PullMode, string][] = [["full", "Стягивание"], ["half", "Полу"], ["none", "Без"]];
/** Что хранит настройка провода (рецепт ОБ.019): всё с чертежа, кроме длины. */
const RECIPE_KEYS = DRAWN.filter((key) => key !== "length_mm");
/** Шаг кнопок −/+: длина — 1 мм, нож — 0,01 мм, зачистка — 0,1 мм, штуки — 1. */
const STEPS: Record<string, number> = { length_mm: 1, diameter_mm: 0.01, knife_back_mm: 0.01 };
const OPEN = ["queued", "taken"];
const FAST_MS = 1000;
const IDLE_MS = 3000;
const STEP_WAIT_MS = 600;
const RECIPE_WAIT_MS = 5000;
const DONE_SHOWN_MS = 10000;

type Tone = "ok" | "danger" | "warn" | "info";
type FieldStatus = { kind: "queued" | "writing" | "done" | "failed"; value: string; id?: string; text?: string; at?: number; recipe?: boolean };
type Queued = { value: string; recipe: boolean };
type PlanQuery = { group_id: string; length: string; wire?: undefined } | { wire: string; length: string; group_id?: undefined };

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

/** Режим стягивания по значениям станка (владелец 19.11.2026): 0 — без, меньше зачистки — полу, иначе стягивание. */
export function pullMode(strip: number | null, pull: number | null): PullMode | null {
  if (pull === null) return null;
  if (pull <= 0.004) return "none";
  if (strip !== null && pull >= strip - 0.004) return "full";
  return "half";
}

/** «Стягивание» тянет дальше зачистки на 2,00 мм (сервер: recipes.FULL_PULL_EXTRA). */
export const FULL_PULL_EXTRA = 2;

/** Стягивание для режима при этой зачистке: зачистка + 2,00 мм, половина (до 0,01 мм) или ноль. */
export function pullFor(mode: PullMode, strip: number): number {
  return mode === "full" ? Math.round((strip + FULL_PULL_EXTRA) * 100) / 100 : mode === "half" ? Math.round(strip * 50) / 100 : 0;
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

/** Откуда значение плана, словами: «0,74 = Ø жилы 0,64 + 0,10», «из настройки 18.11», «ЭТК 5,00 + поправка 0,40». */
export function sourceText(row: TransferRow, conductor: string | null, recipeAt: string | null): string {
  if (row.value === null) return "";
  const offset = row.offset && row.offset !== "0,00" ? row.offset : "";
  const plus = offset ? ` + поправка ${offset.startsWith("-") ? offset : `+${offset}`}` : "";
  if (row.source.startsWith("Ø жилы")) return conductor ? `${row.value} = Ø жилы ${conductor} + 0,10` : `${row.value} = Ø жилы + 0,10`;
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

export function Ob019SettingsPanel({ inventory, equipmentId, live = null, old = false }: {
  inventory: string; equipmentId?: string; live?: Ob019Live | null; old?: boolean;
}) {
  const [fast, setFast] = useState(false);
  const period = fast ? FAST_MS : IDLE_MS;
  const loadWindow = useCallback((signal: AbortSignal) => machineControlApi.window(inventory, signal), [inventory]);
  const loadControl = useCallback((signal: AbortSignal) => machineControlApi.control(inventory, signal), [inventory]);
  const { data: card, error: cardError } = usePolled<MachineWindow>(loadWindow, period, "Не удалось загрузить настройки станка");
  const { data: control, error: controlError } = usePolled(loadControl, period, "Не удалось загрузить запись на станок");
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
  const [recipeDue, setRecipeDue] = useState(0);
  const query = useRef<PlanQuery | null>(null);
  const stepTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

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
  const canWrite = !!control && control.can_edit && !control.problem && !old;
  const linkUp = !old && !!card;

  // Statuses follow the machine queue: done → «✓ на станке» once read back, failed → the reason, the field shows live.
  const statusRef = useRef(status);
  statusRef.current = status;
  useEffect(() => {
    if (!control) return;
    let changed = false;
    let saved = 0;
    const next = { ...statusRef.current };
    for (const [key, item] of Object.entries(next)) {
      if (item.kind !== "writing") continue;
      const row = control.commands.find((command) => command.id === item.id);
      if (!row || OPEN.includes(row.status)) continue;
      changed = true;
      if (row.status === "done") {
        next[key] = { ...item, kind: "done", at: Date.now() };
        if (item.recipe) saved += 1;
      } else next[key] = { ...item, kind: "failed", text: commandResult(row).text.replace(/^отказ: /, "") };
    }
    if (changed) setStatus(next);
    if (saved) setRecipeDue((count) => count + 1);
  }, [control]);

  const busyWriting = Object.values(status).some((item) => item.kind === "queued" || item.kind === "writing") || Object.keys(queue).length > 0;
  if (busyWriting !== fast) setFast(busyWriting);

  // One write at a time: what was committed meanwhile goes out together once the machine took the previous one.
  useEffect(() => {
    const keys = Object.keys(queue);
    if (!keys.length || sending || !control) return;
    if (!canWrite) {
      const why = old ? "нет связи со станком" : !control.can_edit ? "запись вам не разрешена" : operatorText(control.problem);
      setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { kind: "failed", value: queue[key].value, text: why }])) }));
      setQueue({});
      return;
    }
    if (open) return;
    const batch = queue;
    setQueue({});
    setSending(true);
    machineControlApi.write(inventory, Object.fromEntries(keys.map((key) => [key, batch[key].value])), control.state)
      .then((command) => setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) =>
        [key, { kind: "writing", value: batch[key].value, id: command.id, recipe: batch[key].recipe }])) })))
      .catch((reason) => {
        const text = operatorText(failureText(reason, "не записано"));
        setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { kind: "failed", value: batch[key].value, text }])) }));
      })
      .finally(() => setSending(false));
  }, [queue, sending, control, canWrite, open, old, inventory]);

  /** Значение без набираемого: то, что уже уходит на станок, иначе то, что на станке. */
  const settledOf = (key: string) => {
    const st = status[key];
    return st && (st.kind === "queued" || st.kind === "writing") ? st.value : liveText(fields.get(key));
  };

  /** Поле изменено — на станок. Пусто, то же, что на станке, или не число — запись не уходит. Зачистка уходит вместе со
   *  стягиванием своего конца по текущему режиму (стягивание = зачистка + 2,00, полу = половина, без = 0). */
  const commit = (key: string, text: string, recipe = true) => {
    setDrafts((current) => without(current, [key]));
    const field = fields.get(key);
    const value = text.trim();
    if (!field || !value || !enteredDiffers(field, value)) return;
    if (numeric(field) && typeof field.value !== "string" && decimal(value) === null) {
      setStatus((current) => ({ ...current, [key]: { kind: "failed", value, text: "нужно число" } }));
      return;
    }
    const values: Record<string, string> = { [key]: value };
    const pullKey = PULL_OF[key];
    const strip = decimal(value);
    if (pullKey && fields.has(pullKey) && strip !== null) {
      const mode = pullMode(decimal(settledOf(key)), decimal(settledOf(pullKey)));
      const pull = mode ? mm(pullFor(mode, strip)) : "";
      if (pull && enteredDiffers(fields.get(pullKey), pull)) values[pullKey] = pull;
    }
    const keys = Object.keys(values);
    setQueue((current) => ({ ...current, ...Object.fromEntries(keys.map((item) => [item, { value: values[item], recipe }])) }));
    setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((item) => [item, { kind: "queued", value: values[item] }])) }));
  };
  const commitRef = useRef(commit);
  commitRef.current = commit;

  // «Запомнить для провода» — само: после серии своих правок значения станка сохраняются как настройка провода.
  const latest = useRef({ fields, plan, linkUp, equipmentId });
  latest.current = { fields, plan, linkUp, equipmentId };
  useEffect(() => {
    if (!recipeDue) return;
    const timer = setTimeout(() => void saveRecipe(), RECIPE_WAIT_MS);
    return () => clearTimeout(timer);
  }, [recipeDue]); // eslint-disable-line react-hooks/exhaustive-deps

  async function saveRecipe() {
    const { fields: now, plan: job, linkUp: up, equipmentId: machine } = latest.current;
    if (!machine || !job?.wire || !up) return;
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

  /** Значения задания или провода — сразу на станок: задание ЭТК — «Передать на станок» (сервер считает сам), провод —
   *  одной записью отличающихся полей. */
  async function writePlan(next: TransferPlan) {
    const values: Record<string, string> = {};
    for (const row of next.rows) if (row.value !== null && fields.has(row.key) && enteredDiffers(fields.get(row.key), row.value)) values[row.key] = row.value;
    for (const [key, value] of Object.entries(next.job)) {
      if (value !== null && value !== "" && fields.has(key) && enteredDiffers(fields.get(key), String(value))) values[key] = String(value);
    }
    const keys = Object.keys(values);
    if (!keys.length) { setMessage({ tone: "info", text: "Станок уже настроен на это" }); return; }
    if (!canWrite) {
      setMessage({ tone: "warn", text: `Не записано: ${old ? "нет связи со станком" : !control?.can_edit ? "запись вам не разрешена" : operatorText(control?.problem ?? "")}` });
      return;
    }
    if (next.group_id && next.blocking.length === 0) {
      setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { kind: "queued", value: values[key] }])) }));
      try {
        const command = await machineControlApi.transfer(inventory, next.group_id, query.current?.length ?? next.length_mm, control?.state ?? null);
        setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { kind: "writing", value: values[key], id: command.id }])) }));
      } catch (reason) {
        const text = operatorText(failureText(reason, "не записано"));
        setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { kind: "failed", value: values[key], text }])) }));
      }
      return;
    }
    setQueue((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { value: values[key], recipe: false }])) }));
    setStatus((current) => ({ ...current, ...Object.fromEntries(keys.map((key) => [key, { kind: "queued", value: values[key] }])) }));
  }

  async function pick(next: PlanQuery | null) {
    query.current = next;
    setPlan(null);
    setMessage(null);
    setRecipeNote(null);
    if (!next) return;
    setPlanLoading(true);
    try {
      const answer = await loadPlan(next);
      if (query.current !== next) return;
      setPlan(answer);
      await writePlan(answer);
    } catch (reason) {
      if (query.current === next) setMessage({ tone: "danger", text: failureText(reason, "Не удалось посчитать значения") });
    } finally {
      if (query.current === next) setPlanLoading(false);
    }
  }
  const pickJob = (key: string) => {
    setChoice(key);
    setWire("");
    const item = choices?.find((row) => row.key === key);
    void pick(item ? { group_id: item.group_id, length: item.length_mm } : null);
  };
  const pickWire = (label: string) => {
    setWire(label);
    setChoice("");
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
      if (!enteredDiffers(fields.get(parameter), row.value)) return "";
      commitRef.current(parameter, row.value, false);
      return `${row.label} ${row.value} мм записывается на станок`;
    } catch (reason) {
      return failureText(reason, "не удалось пересчитать значения");
    }
  }

  const stepper = (key: string, field: SettingField, direction: 1 | -1) => {
    const step = STEPS[key] ?? (field.unit === "мм" ? 0.1 : 1);
    const st = status[key];
    const base = decimal(drafts[key] ?? (st && (st.kind === "queued" || st.kind === "writing") ? st.value : liveText(field))) ?? 0;
    const next = Math.max(0, Math.round((base + direction * step) * 100) / 100);
    const text = field.unit === "мм" ? next.toFixed(2).replace(".", ",") : String(Math.round(next));
    setDrafts((current) => ({ ...current, [key]: text }));
    clearTimeout(stepTimers.current[key]);
    stepTimers.current[key] = setTimeout(() => commitRef.current(key, text), STEP_WAIT_MS);
  };
  useEffect(() => () => { Object.values(stepTimers.current).forEach(clearTimeout); }, []);

  /** Итог записи поля: «записывается…», «✓ на станке», «отказ: причина». */
  const fieldNote = (key: string): { tone: Tone; text: string } | null => {
    const st = status[key];
    if (!st) return null;
    if (st.kind === "failed") return { tone: "danger", text: `отказ: ${st.text}` };
    if (st.kind === "done") {
      if (enteredDiffers(fields.get(key), st.value)) return { tone: "info", text: "записывается…" };
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
        {pull !== null && <span className="machine-muted">{mm(pull)} мм</span>}
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
          ? <select id={`ob019-${key}`} aria-label={name} value={value} disabled={!editable} onChange={(event) => commit(key, event.target.value)}>
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
  const machine = Object.fromEntries(DRAWN.map((key) => { const value = fields.get(key)?.value; return [key, typeof value === "number" ? value : null]; }));
  const size = (key: string) => decimal(valueOf(key));
  const job = live?.job;
  const problem = control && !old ? (!control.can_edit ? "Изменять значения станка вам не разрешено"
    : control.problem ? `Изменения не записываются: ${operatorText(control.problem)}` : "") : "";
  const picked = choices?.find((item) => item.key === choice) ?? null;
  const wireInfo = wires?.find((item) => item.label === wire);

  return <section className="machine-settings" aria-label="Настройки станка">
    {error && <div className="error-banner" role="alert">{error}</div>}
    <section className="machine-block machine-what" aria-label="Что режем">
      <h3>Что режем</h3>
      <div className="machine-ways">
        <div className={`machine-way${choice ? " is-on" : ""}`}>
          <label className="machine-way-title" htmlFor="ob019-job">Задание из ЭТК</label>
          <select id="ob019-job" aria-label="Задание резки" value={choice} onChange={(event) => pickJob(event.target.value)}>
            <option value="">{choices === null ? "Загрузка…" : choices.length ? "Выберите задание резки" : "Нет открытых групп резки провода"}</option>
            {(choices ?? []).map((item) => <option key={item.key} value={item.key}>
              {`${item.number} · ${item.project} · ${item.wire} · ${item.length_mm.replace(".", ",")} мм · ${item.quantity} шт`}
            </option>)}
          </select>
          {picked && plan && <span className="machine-muted">Заказ {plan.job.order_number || EMPTY} · провод {plan.wire}</span>}
        </div>
        <div className={`machine-way${wire ? " is-on" : ""}`}>
          <span className="machine-way-title">Провод вручную</span>
          <Ob019WirePicker wires={wires} value={wire} onPick={pickWire} />
          {wireInfo && <span className="machine-muted">
            {[wireInfo.conductor_mm ? `Ø жилы ${wireInfo.conductor_mm} мм` : "", wireInfo.has_recipe ? "есть настройка" : "настройки ещё нет"].filter(Boolean).join(" · ")}
          </span>}
        </div>
      </div>
      <div className="machine-job-row">
        {input("total_set", "Тираж", typeof job?.total_finished === "number" ? `сделано ${formatNumber(job.total_finished)}` : "")}
        {input("batch_set", "Пучок", typeof job?.batch_finished === "number" ? `сделано ${formatNumber(job.batch_finished)}` : "")}
        {input("mode", "Режим")}
      </div>
      {planLoading && <p className="machine-muted">Считаю значения…</p>}
      {plan && plan.blocking.length > 0 && <p className="warning-inline" role="status">
        В ЭТК не задано: {plan.blocking.join(", ")} — на станке осталось прежнее, введите на чертеже
      </p>}
      {message && <p className={`machine-message tone-${message.tone}`} role="status">{message.text}</p>}
    </section>

    {problem && <p className="warning-inline" role="status">{problem}</p>}
    <Ob019WireDrawing middle={middle} field={drawn}
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
      {more && <>
        <div className="seg-tabs" role="tablist" aria-label="Страницы пульта">
          {groups.map((item, index) => <button key={item.title} role="tab" aria-selected={index === group}
            className={index === group ? "on" : undefined} onClick={() => setGroup(index)}>{item.title}</button>)}
        </div>
        {current && <table className="machine-table" aria-label={current.title}>
          <thead><tr><th>Параметр</th><th>На станке</th><th></th></tr></thead>
          <tbody>{current.fields.map((field) => {
            const key = field.key;
            const note = key ? fieldNote(key) : null;
            return <tr key={`${field.label}-${field.kind}`} className={note?.text === "записывается…" ? "is-writing" : undefined}>
              <th scope="row">{field.label}</th>
              <td>{key && canWrite && field.kind === "bit0"
                ? <select aria-label={field.label} value={valueOf(key)} onChange={(event) => commit(key, event.target.value)}>
                  {!valueOf(key) && <option value="">{EMPTY}</option>}
                  {SWITCH.map(([code, text]) => <option key={code} value={code}>{text}</option>)}
                </select>
                : key && canWrite
                ? <input aria-label={field.label} value={valueOf(key)} autoComplete="off"
                  inputMode={field.kind === "text15" ? "text" : "decimal"} maxLength={field.kind === "text15" ? 30 : 12}
                  placeholder={field.kind === "text15" ? "латиница, до 30 знаков" : field.unit}
                  onChange={(event) => setDrafts((values) => ({ ...values, [key]: event.target.value }))}
                  onBlur={() => { if (key in drafts) commit(key, drafts[key]); }}
                  onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); commit(key, (event.target as HTMLInputElement).value); } }} />
                : <span className={old ? "machine-muted" : undefined}>{field.read ? shown(field) : EMPTY}</span>}</td>
              <td>{note ? <span className={`tone-${note.tone}`} role="status">{note.text}</span>
                : !key ? <span className="machine-muted">только просмотр</span> : field.unit && canWrite ? <span className="machine-muted">{field.unit}</span> : null}</td>
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
