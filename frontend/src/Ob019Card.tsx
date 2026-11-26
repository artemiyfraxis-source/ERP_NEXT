import { useCallback, useState, type ReactNode } from "react";
import { failureText } from "./api";
import { PageWindow } from "./PageWindow";
import { EMPTY, formatMoment, formatNumber } from "./format";
import { machineControlApi, PULT_OPEN, operatorText, RESET_STATES, resetCountersText, type MachineCommand, type MachineControl,
  type MachineRunState } from "./machineControlApi";
import { machinesApi, usePolled, type MachineCard, type Ob019Live, type Tone } from "./machinesApi";
import { Ob099CardView } from "./Ob099Card";
import { Ob019Controls, Ob019Pult } from "./Ob019Controls";
import { Ob019SettingsPanel } from "./Ob019SettingsPanel";

/** Живая карточка ОБ.019 (линия резки и зачистки с лазерной маркировкой) как пульт у станка (навык erp-machine-hmi,
 *  владелец 19.11.2026): сверху один раз — состояние словами, связь и что делать дальше; под ним команды
 *  (Ob019Controls.tsx), затем «Выполнение задания», «Что режем», чертёж провода, запись, проба и поправка
 *  (Ob019SettingsPanel.tsx); the journal opens from the header; auxiliary settings stay inline and the connection stays in the status line. Открытый на ПК пульт станка — одной строкой, без имени
 *  программы, с кнопкой «Закрыть пульт на ПК»; на связи — тихая «Открыть пульт на ПК» (Ob019Pult). Опрос раз в 3 с,
 *  пока окно открыто. */

type Fact = [label: string, value: string, action?: ReactNode];

const toneClass = (tone?: Tone) => (tone && tone !== "neutral" && tone !== "ok" ? `tone-${tone}` : "");

/** Что делать дальше в этом состоянии станка (коды D766, machines.OB019_STATES). */
const NEXT_STEP: Record<number, string> = {
  100: "Нажмите «Сброс»",
  101: "Дождитесь конца сброса",
  102: "Выберите, что режем, проверьте значения на чертеже и нажмите «Пуск»",
  103: "Дождитесь конца тиража",
  104: "Пауза: продолжите на станке или нажмите «Стоп»",
  105: "Отпустите аварийную кнопку на станке и нажмите «Сброс»",
  106: "Пучок готов — заберите провод",
  107: "Тираж готов — снимите провод; новое задание — после «Сброс»",
};

function ago(seconds: number | null): string {
  if (seconds === null) return "";
  if (seconds < 60) return `${seconds} с назад`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} мин назад`;
  return `${Math.floor(seconds / 3600)} ч назад`;
}

function count(value: number | null): string {
  return value === null ? EMPTY : formatNumber(value);
}

export interface MachineStatus { title: string; tone: Tone; link: string; hint: string }

const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** Одна строка состояния: станок словами, связь с возрастом сигнала и следующий шаг. `startWhy` — почему «Пуск» не пойдёт
 *  (та же причина, что под кнопкой; '' — пойдёт; undefined — неизвестно): у готового станка шаг называет её. */
export function machineStatus(card: MachineCard, startWhy?: string): MachineStatus {
  const linkOk = card.link.code === "ok";
  const pult = card.link.code === "port_busy" || operatorText(card.error || "") === PULT_OPEN;
  const signal = card.reported_at ? `сигнал ${formatMoment(card.reported_at)}${card.age_seconds === null ? "" : `, ${ago(card.age_seconds)}`}`
    : "сигнала ещё не было";
  if (!linkOk) {
    return {
      title: pult ? PULT_OPEN : card.link.text, tone: pult ? "warn" : card.link.tone === "neutral" ? "neutral" : "danger",
      link: `Последний ${signal}`,
      hint: card.live ? "Команды и запись — когда станок на связи; ниже серым — последние полученные значения" : "Команды и запись — когда станок на связи",
    };
  }
  const link = `На связи · ${signal}`;
  const live = card.live;
  if (!live) return { title: "Агент на связи, станок ещё не ответил", tone: "warn", link, hint: pult ? PULT_OPEN : "Проверьте, что станок включён" };
  if (pult) return { title: live.state.text, tone: "warn", link, hint: PULT_OPEN };
  if (live.alarm.tone === "danger" || live.alarm.tone === "warn") {
    return { title: `Авария: ${live.alarm.text}`, tone: "danger", link, hint: "Устраните причину на станке, затем «Сброс аварии»" };
  }
  const code = live.state.code;
  const { total_set: set, total_finished: done } = live.job;
  const title = code === 103 && set ? `Режет ${count(done)} из ${count(set)}` : live.state.text;
  const tone = live.state.tone === "danger" ? "danger" : live.state.tone === "warn" ? "warn" : "neutral";
  const ready = code === 102 && startWhy !== undefined
    ? startWhy ? capital(startWhy) : "Значения на станке ✓ — нажмите «Пуск»" : null;
  return { title, tone, link, hint: ready ?? (code === null ? "" : NEXT_STEP[code] ?? "") };
}

/** Ход задания на станке одной строкой; пустое не показывается. «Сделано» — только без открытого задания: при нём счёт
 *  уже в строке состояния и в «Выполнении задания». `zero` — кнопка «Обнулить» рядом со «Сделано». */
function liveFacts(live: Ob019Live, running: boolean, zero?: ReactNode): Fact[] {
  const { job } = live;
  const facts: Fact[] = [
    ["Режим", live.mode.text],
    ["Сделано", job.total_set === null || running ? "" : `${count(job.total_finished)} из ${count(job.total_set)} шт`, zero],
    ["Пучок", job.batch_set ? `${count(job.batch_finished)} из ${count(job.batch_set)} шт` : ""],
    ["Заказ", job.order_number ?? ""],
    ["Изделие", job.product ?? ""],
    ["Скорость", job.uph ? `${formatNumber(job.uph)} шт/ч` : ""],
  ];
  return facts.filter(([, value]) => value && value !== EMPTY);
}

/** The newest done «Обнулить счётчики»: the machine read 0 back, the bridge may not have reported since — the card shows
 *  the read-back until a snapshot newer than the command arrives (owner 23.11.2026: «после обнуления видно 0»). */
export function zeroedLive(live: Ob019Live, commands: MachineCommand[], reportedAt: string | null): Ob019Live {
  const done = commands.find((row) => row.reset_counters);
  const after = done?.status === "done" ? done.reset_counters?.after : null;
  if (!after || !done?.finished_at) return live;
  if (reportedAt && new Date(reportedAt).getTime() > new Date(done.finished_at).getTime()) return live;
  return { ...live, job: { ...live.job, total_finished: after.total ?? live.job.total_finished,
    batch_finished: after.batch ?? live.job.batch_finished } };
}

/** «Обнулить» у «Сделано … из …», когда тираж сделан и станок стоит: одно подтверждение «Сделано N → 0, пучок M → 0»,
 *  итог — рядом с кнопкой. Только D649 и D645; тираж D22 остаётся (новое задание — «Новый тираж»). */
export function Ob019ZeroCounters({ card, live, control }: { card: MachineCard; live: Ob019Live; control: MachineControl }) {
  const [asking, setAsking] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const [refused, setRefused] = useState("");
  const [busy, setBusy] = useState(false);
  const { total_finished: total, batch_finished: batch, total_set: set } = live.job;
  const state = live.state.code;
  const commands = Array.isArray(control.commands) ? control.commands : [];
  const row = sent ? commands.find((item) => item.id === sent) : undefined;
  const open = commands.some((item) => item.status === "queued" || item.status === "taken");
  const finished = state === 107 || (set !== null && set > 0 && total !== null && total >= set);
  const shown = control.can_edit && card.link.code === "ok" && finished && total !== null && batch !== null
    && (total > 0 || batch > 0) && state !== null && RESET_STATES.includes(state);
  const note = refused ? `отказ: ${refused}` : row ? (row.status === "done" ? "обнулено"
    : row.status === "queued" || row.status === "taken" ? "обнуляется…" : `не обнулено: ${operatorText(row.error || row.status_text)}`) : "";
  async function send() {
    if (total === null || batch === null) return;
    setBusy(true); setRefused("");
    try {
      const queued = await machineControlApi.resetCounters(card.inventory, state, { total, batch });
      setSent(queued.id); setAsking(false);
    } catch (reason) {
      setRefused(failureText(reason, "не отправлено")); setAsking(false);
    } finally { setBusy(false); }
  }
  if (!shown && !note) return null;
  return <>
    {shown && <button type="button" className="secondary small machine-zero" disabled={busy || open}
      onClick={() => { setRefused(""); setAsking(true); }}>Обнулить</button>}
    {note && <span className={`machine-cmd-note${refused || (row && row.status !== "done" && row.status !== "queued" && row.status !== "taken") ? " tone-danger" : ""}`}
      role="status"> {note}</span>}
    {asking && total !== null && batch !== null && <PageWindow label="Обнулить счётчик" close={() => { if (!busy) setAsking(false); }} width={520}>
      <section className="panel machine-card">
        <h2>Обнулить счётчик</h2>
        <p><strong className="tnum">{resetCountersText(total, batch)}</strong></p>
        {set !== null && set > 0 && <p>Тираж {formatNumber(set)} шт остаётся на станке: «Пуск» начнёт его заново. Другое задание — «Новый тираж».</p>}
        <div className="machine-actions">
          <button type="button" className="secondary" disabled={busy} onClick={() => setAsking(false)}>Отмена</button>
          <button type="button" className="primary" disabled={busy} onClick={() => void send()}>Обнулить</button>
        </div>
      </section>
    </PageWindow>}
  </>;
}

function plain(value: string | null | undefined): string {
  return value ? value : EMPTY;
}

/** `controls` — строка команд (Ob019Controls.tsx) под строкой состояния. */
export function Ob019CardView({ card, controls = false, header, incoming }: {
  card: MachineCard; controls?: boolean; header?: { title: string; actions?: ReactNode; close: () => void };
  incoming?: import("./machinesApi").MachineTaskContext;
}) {
  const old = card.link.code !== "ok";
  // The run «Выполнение задания» polls (Ob019SettingsPanel) and why «Пуск» would not go (Ob019Controls): the status
  // strip and the command row say the same.
  const [runState, setRunState] = useState<MachineRunState | null>(null);
  const [startWhy, setStartWhy] = useState<string | undefined>(undefined);
  // The command poll of the command row: the operator panel button in the status strip shows its progress too.
  const [control, setControl] = useState<MachineControl | null>(null);
  const [toolsWindow, setToolsWindow] = useState<"journal" | null>(null);
  const run = runState?.run;
  const running = !!run && !["closed", "failed", "moved"].includes(run.stage);
  const status = machineStatus(card, startWhy);
  const live = card.live && control ? zeroedLive(card.live, Array.isArray(control.commands) ? control.commands : [], card.reported_at) : card.live;
  const zero = controls && live && control && !running ? <Ob019ZeroCounters card={card} live={live} control={control} /> : undefined;
  const facts = live ? liveFacts(live, running, zero) : [];
  // The agent's answer only when it says more than the state line above (the server already translates known ones).
  const agentNote = card.error && card.error !== card.link.text && operatorText(card.error) !== PULT_OPEN ? card.error : "";
  return <>
    <header className="machine-card-head">
      {header && <h2>{header.title}</h2>}
      <button type="button" className="secondary small" onClick={() => setToolsWindow("journal")}>Журнал</button>
      {header && <button type="button" className="secondary small" onClick={header.close}>Закрыть</button>}
    </header>
    <div className={`machine-status${status.tone !== "neutral" ? ` is-${status.tone}` : ""}`} role="status" aria-label="Состояние станка">
      <strong className={`machine-status-title ${toneClass(status.tone)}`}>{status.title}</strong>
      <span className="machine-status-link">ПК: {plain(card.pc)} · агент: {plain(card.bridge_version)} · {status.link}
        {agentNote && <> · {agentNote}</>}</span>
      {status.hint && <span className="machine-status-hint">{status.hint}</span>}
      {controls && <Ob019Pult card={card} control={control} />}
      <span className="machine-status-estop">Аварийная остановка — кнопкой на станке</span>
    </div>
    {facts.length > 0 && <dl className={`machine-summary${old ? " machine-old" : ""}`}
      aria-label={old ? "Последние полученные данные" : "Данные станка"}>
      {old && card.reported_at && <div><dt>Данные на</dt><dd>{formatMoment(card.reported_at)}</dd></div>}
      {facts.map(([label, value, action]) => <div key={label}><dt>{label}</dt><dd>{value}{action}</dd></div>)}
    </dl>}
    {controls && <Ob019Controls card={card} run={runState ? runState.run : undefined} onStartWhy={setStartWhy} onControl={setControl} />}
    <Ob019SettingsPanel incoming={incoming} inventory={card.inventory} equipmentId={card.equipment_id} live={card.live} old={old}
      pult={card.link.code === "port_busy"} onRun={setRunState} toolsWindow={toolsWindow} closeTools={() => setToolsWindow(null)} />
  </>;
}

export function Ob019Card({ equipmentId, title, close, actions, incoming, openJournal }: { equipmentId: string; title: string; close: () => void; actions?: ReactNode; incoming?: import("./machinesApi").MachineTaskContext; openJournal?: () => void }) {
  const load = useCallback((signal: AbortSignal) => machinesApi.card(equipmentId, signal), [equipmentId]);
  const { data, error } = usePolled(load, undefined, "Не удалось загрузить станок");
  return <PageWindow label={title} close={close} width={1100} dismissable>
    <section className="panel machine-card">
      {(!data || data.kind === "ob099_fins") && <header className="machine-card-head">
        <h2>{title}</h2>
        {data?.kind === "ob099_fins" && actions}
        {data?.kind === "ob099_fins" && openJournal && <button type="button" className="secondary small" onClick={openJournal}>Журнал</button>}
        <button className="secondary small" onClick={close}>Закрыть</button>
      </header>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!data && !error && <p className="empty">Загрузка…</p>}
      {data && (data.kind === "ob099_fins" ? <Ob099CardView card={data} incoming={incoming} />
        : <Ob019CardView card={data} controls incoming={incoming} header={{ title, actions, close }} />)}
    </section>
  </PageWindow>;
}
