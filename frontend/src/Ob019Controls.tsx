import { useCallback, useEffect, useState, type ReactNode } from "react";
import { failureText } from "./api";
import {
  CONTROL_NAMES, CONTROL_STATES, IDLE_STATES, PULT_STILL_OPEN, commandResult, machineControlApi, operatorText, pultResult,
  type MachineCommand, type MachineControl, type MachineRun, type MachineWindow, type PulseCommand,
  type PultCommand,
} from "./machineControlApi";
import { usePolled, type MachineCard, type Tone } from "./machinesApi";

/** Команды ОБ.019 одной строкой вверху карточки (навык erp-machine-hmi): Стоп · Сброс · Пуск · Сброс аварии · ролики.
 *  Одно нажатие — одна команда, без диалога (владелец 19.11.2026: «убрать лишние действия по типу "уберите руки"»);
 *  состояние, которое видит карточка, уходит с командой само, и сервер откажет, если станок уже в другом. «Стоп» — всем,
 *  кто видит карточку; «Сброс», «Пуск», «Сброс аварии» — с правом «Управление станками». Подъём подающих роликов —
 *  переключатель с прочитанным со станка положением (поле `feed_wheel`). Итог каждой команды — под её кнопкой, причина
 *  недоступности — там же. Аварийного стопа в ERP нет — только кнопка на станке (это говорит строка состояния). */

const ACTIONS: PulseCommand[] = ["reset", "start", "alarm_clear"];
const FAST_MS = 1000;
const IDLE_MS = 3000;
const OPEN = ["queued", "taken"];
const ROLLERS = "rollers";

type Note = { text: string; tone: Tone };

const toneClass = (tone?: Tone) => (tone && tone !== "neutral" ? `tone-${tone}` : "");

/** Почему «Пуск» не пойдёт из-за задания (сервер: production/machine_runs.start_blocker): ERP пускает станок только по
 *  открытому, записанному и проверенному прогону. `run` undefined — прогон ещё не загружен (причины нет). */
export function runBlocker(run: MachineRun | null | undefined): string {
  if (run === undefined) return "";
  if (!run || run.stage === "closed" || run.stage === "failed") return "выберите провод или задание";
  if (run.stage === "writing") return "ждём записи задания";
  if (run.stage === "moved" || run.stage === "reset") return "передайте задание заново";
  if (run.stage === "finished") return "тираж готов — подготовьте новый";
  if (run.stage === "nolink") return "нет свежих данных станка";
  return "";
}

/** Почему команду сейчас не отправить, коротко для подписи под кнопкой ('' — можно). Пуск — только по открытому
 *  заданию (`runBlocker`) и с опущенными роликами (сервер: machine_control.BLOCKED_BY_BITS, агент — то же на ПК). */
export function blocked(command: PulseCommand, card: MachineCard, open: MachineCommand | undefined, lifted: boolean | null = null,
  run?: MachineRun | null): string {
  if (card.link.code !== "ok" || !card.live) return "нет связи";
  if (open && open.command !== "stop") return `ждёт: ${open.summary}`;
  const state = card.live.state.code;
  if (command === "alarm_clear") return card.live.alarm.code ? "" : "аварии нет";
  const allowed = CONTROL_STATES[command];
  if (allowed && (state === null || !allowed.includes(state))) {
    if (state === 101) return "идёт сброс";
    if (state === 103) return command === "reset" ? "станок режет — сначала «Стоп»" : "уже режет";
    if (command === "start") return state === 100 || state === 105 || state === 107 ? "сначала «Сброс»" : "станок не готов";
    return state === 102 ? "уже сброшен" : "не нужен сейчас";
  }
  if (command !== "start") return "";
  return runBlocker(run) || (lifted === true ? "опустите ролики" : "");
}

/** Положение подающих роликов, прочитанное со станка: true — подняты; null — неизвестно. */
function feedWheel(settings: MachineWindow | null): boolean | null {
  const field = (settings?.settings ?? []).flatMap((group) => group.fields).find((item) => item.key === "feed_wheel");
  return field && field.read && typeof field.value === "boolean" ? field.value : null;
}

/** Вопрос перед командой пульту: что потеряет человек у станка. */
/** Сколько после «пульт не закрылся» предлагается «Закрыть принудительно» (сервер: machine_control.FORCE_WINDOW). */
export const FORCE_WINDOW_MS = 5 * 60 * 1000;

export const PULT_CONFIRM: Record<PultCommand | "force", string> = {
  hmi_close: "Пока открыт пульт, ERP не видит станок. Оператор у станка потеряет пульт. Закрыть пульт на ПК станка?",
  hmi_open: "ERP перестанет видеть станок, пока пульт открыт. Открыть пульт на ПК станка?",
  force: "ERP не видит, режет ли станок, пока открыт пульт. Убедитесь, что станок стоит. Закрыть пульт принудительно?",
};

/** «Закрыть пульт на ПК» в строке состояния, пока пульт открыт; «Открыть пульт на ПК» — тише, когда станок на связи.
 *  С правом «Управление станками», после вопроса (PULT_CONFIRM); итог и отказ сервера — рядом с кнопкой, как есть.
 *  Пульт не закрылся сам (`still_open`, возможно, ждёт ответа оператора) — вторая кнопка «Закрыть принудительно» со
 *  своим вопросом. Итог — по строке команды (что отправлено, то и написано), а не по кнопке, которая видна сейчас.
 *  `control` — то, что опрашивает строка команд (Ob019Controls): второй опрос не нужен. */
export function Ob019Pult({ card, control }: { card: MachineCard; control: MachineControl | null }) {
  const [sent, setSent] = useState<{ id: string; command: PultCommand; force: boolean } | null>(null);
  const [refused, setRefused] = useState("");
  const [busy, setBusy] = useState(false);
  const pult = card.link.code === "port_busy";
  if (!control?.can_control || (!pult && card.link.code !== "ok")) return null;
  const command: PultCommand = pult ? "hmi_close" : "hmi_open";
  const commands = Array.isArray(control.commands) ? control.commands : [];
  const open = commands.find((row) => OPEN.includes(row.status));
  const row = sent ? commands.find((item) => item.id === sent.id) : undefined;
  // The newest panel command of anyone: a close that did not work stays offered until the panel closes.
  const last = commands.find((item) => item.command === "hmi_close" || item.command === "hmi_open");
  // Its failure stays said next to the button it belongs to (no helper, did not close) until the next try.
  const failed = last && last.command === command && last.status === "failed" ? last : undefined;
  // «Закрыть принудительно» only as the answer to a close that stayed open in the last 5 minutes (the server: the same).
  const stuck = pult && failed?.reason === PULT_STILL_OPEN && !!failed.finished_at
    && Date.now() - new Date(failed.finished_at).getTime() <= FORCE_WINDOW_MS ? failed : undefined;
  const pending = sent && !row ? pultResult({ command: sent.command, force: sent.force, status: "queued", status_text: "",
    error: "", finished_at: null }) : null;
  const result: Note | null = refused ? { text: `отказ: ${refused}`, tone: "danger" } : row ? pultResult(row)
    : pending ?? (failed ? pultResult(failed) : null);
  const waiting = open && open.id !== sent?.id ? `ждёт: ${open.summary}` : "";
  async function send(force: boolean) {
    if (!window.confirm(PULT_CONFIRM[force ? "force" : command])) return;
    setBusy(true);
    setRefused("");
    try {
      const queued = await machineControlApi.command(card.inventory, command, null, force);
      setSent({ id: queued.id, command, force });
    } catch (reason) {
      setSent(null);
      setRefused(failureText(reason, "команда не отправлена"));
    } finally {
      setBusy(false);
    }
  }
  const working = !!row && OPEN.includes(row.status);
  const blocked = busy || working || Boolean(waiting);
  return <div className={`machine-pult${pult ? " is-open" : ""}`}>
    <button type="button" className={pult ? "primary machine-pult-close" : "secondary small machine-pult-open"}
      disabled={blocked} onClick={() => void send(false)}>{CONTROL_NAMES[command]}</button>
    {stuck && <button type="button" className="secondary machine-pult-force" disabled={blocked}
      onClick={() => void send(true)}>Закрыть принудительно</button>}
    {(result || waiting) && <span className={`machine-cmd-note ${result ? toneClass(result.tone) : "machine-muted"}`}
      role={result ? "status" : undefined}>{result ? result.text : waiting}</span>}
  </div>;
}

/** `run` — прогон станка из «Выполнения задания» (undefined — не загружен); `onStartWhy` — почему «Пуск» не пойдёт
 *  ('' — пойдёт; undefined — без права пуска): та же причина — следующим шагом в строке состояния. `onControl` —
 *  опрос команд станка для кнопки пульта в строке состояния (Ob019Pult). */
export function Ob019Controls({ card, run, onStartWhy, onControl }: {
  card: MachineCard; run?: MachineRun | null; onStartWhy?: (why: string | undefined) => void;
  onControl?: (control: MachineControl | null) => void;
}) {
  const [fast, setFast] = useState(false);
  const load = useCallback((signal: AbortSignal) => machineControlApi.control(card.inventory, signal), [card.inventory]);
  const loadWindow = useCallback((signal: AbortSignal) => machineControlApi.window(card.inventory, signal), [card.inventory]);
  const { data } = usePolled<MachineControl>(load, fast ? FAST_MS : IDLE_MS, "Не удалось загрузить команды станка");
  useEffect(() => { onControl?.(data ?? null); }, [data, onControl]);
  const { data: settings } = usePolled<MachineWindow>(loadWindow, fast ? FAST_MS : IDLE_MS, "Не удалось прочитать ролики");
  const [busy, setBusy] = useState(false);
  // Command (or «rollers») → id of what this card sent / the refusal it got.
  const [sent, setSent] = useState<Record<string, string>>({});
  const [refused, setRefused] = useState<Record<string, string>>({});
  const commands = Array.isArray(data?.commands) ? data!.commands : [];
  const open = commands.find((row) => OPEN.includes(row.status));
  const canControl = data?.can_control === true;
  // Every second while a command is on its way, every 3 s otherwise (a new period loads at once).
  const waiting = Boolean(open) || Object.values(sent).some((id) => {
    const row = commands.find((item) => item.id === id);
    return !row || OPEN.includes(row.status);
  });
  if (waiting !== fast) setFast(waiting);

  /** Итог под кнопкой: отказ при отправке, затем то, что вернула очередь станка. */
  const note = (key: string): Note | null => {
    if (refused[key]) return { text: `отказ: ${refused[key]}`, tone: "danger" };
    const id = sent[key];
    const row = id ? commands.find((item) => item.id === id)
      : commands.find((item) => item.purpose === "control" && item.command === key && OPEN.includes(item.status));
    if (row) return commandResult(row);
    return id ? { text: "отправлено…", tone: "info" } : null;
  };

  async function sendCommand(command: PulseCommand) {
    setBusy(true);
    setRefused((values) => ({ ...values, [command]: "" }));
    try {
      const queued = await machineControlApi.command(card.inventory, command, card.live?.state.code ?? null);
      setSent((values) => ({ ...values, [command]: queued.id }));
    } catch (reason) {
      setRefused((values) => ({ ...values, [command]: operatorText(failureText(reason, "команда не отправлена")) }));
    } finally {
      setBusy(false);
    }
  }

  const lifted = feedWheel(settings);
  async function toggleRollers() {
    if (lifted === null) return;
    setBusy(true);
    setRefused((values) => ({ ...values, [ROLLERS]: "" }));
    try {
      const queued = await machineControlApi.write(card.inventory, { feed_wheel: lifted ? "0" : "1" }, data?.state ?? null);
      setSent((values) => ({ ...values, [ROLLERS]: queued.id }));
    } catch (reason) {
      setRefused((values) => ({ ...values, [ROLLERS]: operatorText(failureText(reason, "ролики не переключены")) }));
    } finally {
      setBusy(false);
    }
  }
  const startWhy = canControl ? blocked("start", card, open, lifted, run) : undefined;
  useEffect(() => { onStartWhy?.(startWhy); }, [startWhy, onStartWhy]);
  const rollersWhy = card.link.code !== "ok" || !card.live ? "нет связи"
    : !data ? "" : !data.can_edit ? "нет права записи"
    : data.state === null || !IDLE_STATES.includes(data.state) ? "только когда станок готов или тираж готов"
    : open ? `ждёт: ${open.summary}` : lifted === null ? "положение не прочитано" : "";

  // Without a link the status strip already says so once: no «нет связи» under every button.
  const noLink = card.link.code !== "ok" || !card.live;
  const cell = (key: string, button: ReactNode, reason: string) => {
    const result = note(key);
    const why = noLink && reason === "нет связи" ? "" : reason;
    return <div className="machine-cmd" key={key}>
      {button}
      <span className={`machine-cmd-note ${result ? toneClass(result.tone) : "machine-muted"}`} role={result ? "status" : undefined}>
        {result ? result.text : why}
      </span>
    </div>;
  };
  return <section className="machine-controls" aria-label="Управление станком">
    <div className="machine-controls-bar">
      {data?.can_stop !== false && cell("stop", <button type="button" className="primary danger machine-stop" onClick={() => void sendCommand("stop")}>Стоп</button>,
        card.link.code !== "ok" ? "без связи может не дойти" : "")}
      {canControl && ACTIONS.map((command) => {
        const why = command === "start" ? startWhy ?? "" : blocked(command, card, open, lifted, run);
        return cell(command, <button type="button" className="secondary machine-command" disabled={Boolean(why) || busy}
          onClick={() => void sendCommand(command)}>{CONTROL_NAMES[command]}</button>, why);
      })}
      {cell(ROLLERS, <button type="button" className="secondary machine-command machine-rollers" aria-pressed={lifted === true}
        disabled={Boolean(rollersWhy) || busy || !data} onClick={() => void toggleRollers()}>
        {lifted === null ? "Ролики: положение неизвестно" : lifted ? <>Ролики подняты <span>· опустить</span></>
          : <>Ролики опущены <span>· поднять</span></>}
      </button>, rollersWhy)}
    </div>
  </section>;
}
