import { useCallback, useState, type ReactNode } from "react";
import { failureText } from "./api";
import {
  CONTROL_NAMES, CONTROL_STATES, IDLE_STATES, commandResult, machineControlApi, operatorText,
  type ControlCommand, type MachineCommand, type MachineControl, type MachineWindow,
} from "./machineControlApi";
import { usePolled, type MachineCard, type Tone } from "./machinesApi";

/** Команды ОБ.019 одной строкой вверху карточки (навык erp-machine-hmi): Стоп · Сброс · Пуск · Сброс аварии · ролики.
 *  Одно нажатие — одна команда, без диалога (владелец 19.11.2026: «убрать лишние действия по типу "уберите руки"»);
 *  состояние, которое видит карточка, уходит с командой само, и сервер откажет, если станок уже в другом. «Стоп» — всем,
 *  кто видит карточку; «Сброс», «Пуск», «Сброс аварии» — с правом «Управление станками». Подъём подающих роликов —
 *  переключатель с прочитанным со станка положением (поле `feed_wheel`). Итог каждой команды — под её кнопкой, причина
 *  недоступности — там же. Аварийного стопа в ERP нет — только кнопка на станке (это говорит строка состояния). */

const ACTIONS: ControlCommand[] = ["reset", "start", "alarm_clear"];
const FAST_MS = 1000;
const IDLE_MS = 3000;
const OPEN = ["queued", "taken"];
const ROLLERS = "rollers";

type Note = { text: string; tone: Tone };

const toneClass = (tone?: Tone) => (tone && tone !== "neutral" ? `tone-${tone}` : "");

/** Почему команду сейчас не отправить, коротко для подписи под кнопкой ('' — можно). Пуск с поднятыми роликами
 *  запрещён (сервер: machine_control.BLOCKED_BY_BITS, агент — то же на ПК). */
export function blocked(command: ControlCommand, card: MachineCard, open: MachineCommand | undefined, lifted: boolean | null = null): string {
  if (card.link.code !== "ok" || !card.live) return "нет связи";
  if (open && open.command !== "stop") return `ждёт: ${open.summary}`;
  if (command === "start" && lifted === true) return "сначала опустите ролики";
  const state = card.live.state.code;
  if (command === "alarm_clear") return card.live.alarm.code ? "" : "аварии нет";
  const allowed = CONTROL_STATES[command];
  if (!allowed || (state !== null && allowed.includes(state))) return "";
  if (state === 101) return "идёт сброс";
  if (state === 103) return command === "reset" ? "станок режет — сначала «Стоп»" : "уже режет";
  if (command === "start") return state === 100 || state === 105 || state === 107 ? "сначала «Сброс»" : "станок не готов";
  return state === 102 ? "уже сброшен" : "не нужен сейчас";
}

/** Положение подающих роликов, прочитанное со станка: true — подняты; null — неизвестно. */
function feedWheel(settings: MachineWindow | null): boolean | null {
  const field = (settings?.settings ?? []).flatMap((group) => group.fields).find((item) => item.key === "feed_wheel");
  return field && field.read && typeof field.value === "boolean" ? field.value : null;
}

export function Ob019Controls({ card }: { card: MachineCard }) {
  const [fast, setFast] = useState(false);
  const load = useCallback((signal: AbortSignal) => machineControlApi.control(card.inventory, signal), [card.inventory]);
  const loadWindow = useCallback((signal: AbortSignal) => machineControlApi.window(card.inventory, signal), [card.inventory]);
  const { data } = usePolled<MachineControl>(load, fast ? FAST_MS : IDLE_MS, "Не удалось загрузить команды станка");
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

  async function sendCommand(command: ControlCommand) {
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
      {cell("stop", <button type="button" className="primary danger machine-stop" onClick={() => void sendCommand("stop")}>Стоп</button>,
        card.link.code !== "ok" ? "без связи может не дойти" : "")}
      {canControl && ACTIONS.map((command) => {
        const why = blocked(command, card, open, lifted);
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
