import { useCallback, useState } from "react";
import { failureText } from "./api";
import { EMPTY } from "./format";
import {
  CONTROL_NAMES, CONTROL_STATES, machineControlApi, type ControlCommand, type MachineCommand, type MachineControl,
} from "./machineControlApi";
import { usePolled, type MachineCard, type Tone } from "./machinesApi";

/** Команды ОБ.019 вверху карточки станка (план «Станки», часть 3). «Стоп» — всем, кто видит карточку, сразу, без
 *  подтверждения. «Сброс», «Пуск», «Сброс аварии» — только с правом «Управление станками» и после подтверждения «руки
 *  убраны» при текущем состоянии. Аварийного стопа в ERP нет — только кнопка на станке. Результаты команд — в списке
 *  «Настроек» вместе с записями (одна очередь станка). */

const ACTIONS: ControlCommand[] = ["reset", "start", "alarm_clear"];
const FAST_MS = 1000;
const IDLE_MS = 3000;
const OPEN = ["queued", "taken"];

const toneClass = (tone?: Tone) => (tone && tone !== "neutral" ? `tone-${tone}` : "");

/** Почему команду сейчас не отправить ('' — можно). */
function blocked(command: ControlCommand, card: MachineCard, open: MachineCommand | undefined): string {
  if (card.link.code !== "ok" || !card.live) return "Нет связи со станком";
  if (open && open.command !== "stop") return `Станок выполняет: ${open.summary}`;
  const allowed = CONTROL_STATES[command];
  const state = card.live.state.code;
  if (allowed && (state === null || !allowed.includes(state))) return `Не в состоянии «${card.live.state.text}»`;
  if (command === "alarm_clear" && !card.live.alarm.code) return "Аварии нет";
  return "";
}

export function Ob019Controls({ card }: { card: MachineCard }) {
  const [fast, setFast] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const load = useCallback((signal: AbortSignal) => machineControlApi.control(card.inventory, signal), [card.inventory]);
  const { data } = usePolled<MachineControl>(load, fast ? FAST_MS : IDLE_MS, "Не удалось загрузить команды станка");
  const [asking, setAsking] = useState<ControlCommand | null>(null);
  const [handsClear, setHandsClear] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const commands = Array.isArray(data?.commands) ? data!.commands : [];
  const open = commands.find((row) => OPEN.includes(row.status));
  const running = commands.find((row) => row.purpose === "control" && OPEN.includes(row.status));
  const canControl = data?.can_control === true;
  const sent = pending ? commands.find((row) => row.id === pending) : undefined;
  // Every second while a command is on its way, every 3 s otherwise (a new period loads at once).
  const waiting = Boolean(open) || (pending !== null && (!sent || OPEN.includes(sent.status)));
  if (waiting !== fast) setFast(waiting);

  async function send(command: ControlCommand) {
    setBusy(true);
    setError("");
    try {
      const queued = await machineControlApi.command(card.inventory, command, card.live?.state.code ?? null);
      setPending(queued.id);
      setAsking(null);
      setHandsClear(false);
    } catch (reason) {
      setError(failureText(reason, `Команда «${CONTROL_NAMES[command]}» не отправлена`));
    } finally {
      setBusy(false);
    }
  }

  const reason = asking ? blocked(asking, card, open) : "";
  return <section className="machine-controls" aria-label="Управление станком">
    <div className="machine-controls-bar">
      <button type="button" className="primary danger machine-stop" onClick={() => void send("stop")}>Стоп</button>
      {canControl && ACTIONS.map((command) => {
        const why = blocked(command, card, open);
        return <button key={command} type="button" className="secondary machine-command" disabled={Boolean(why) || busy}
          title={why || undefined} aria-pressed={asking === command}
          onClick={() => { setAsking(command); setHandsClear(false); setError(""); }}>{CONTROL_NAMES[command]}</button>;
      })}
      {running && <span className={`machine-command-now ${toneClass(running.tone)}`} role="status">
        {running.summary}: {running.status_text.toLowerCase()}
      </span>}
      <span className="machine-controls-note">
        {card.link.code !== "ok" ? "Нет связи — «Стоп» может не дойти. " : ""}Аварийная остановка — только кнопкой на станке
      </span>
    </div>
    {asking && <div className="machine-confirm machine-confirm-command" role="group"
      aria-label={`Подтверждение: ${CONTROL_NAMES[asking]}`}>
      <p>
        <strong>{CONTROL_NAMES[asking]}</strong>
        <span>Сейчас: <b className={toneClass(card.live?.state.tone)}>{card.live?.state.text ?? EMPTY}</b></span>
        <span>Авария: <b className={toneClass(card.live?.alarm.tone)}>{card.live?.alarm.text ?? EMPTY}</b></span>
      </p>
      <label className="machine-confirm-check">
        <input type="checkbox" checked={handsClear} onChange={(event) => setHandsClear(event.target.checked)} />
        Руки убраны, в зоне станка никого нет
      </label>
      {reason && <p className="warning-inline">{reason}</p>}
      <div className="machine-actions">
        <button type="button" className="primary" disabled={!handsClear || Boolean(reason) || busy}
          onClick={() => void send(asking)}>{`Подтвердить: ${CONTROL_NAMES[asking]}`}</button>
        <button type="button" className="secondary" onClick={() => setAsking(null)}>Отмена</button>
      </div>
    </div>}
    {error && <div className="error-banner" role="alert">{error}</div>}
  </section>;
}
