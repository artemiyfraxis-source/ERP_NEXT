import { useCallback, useState, type ReactNode } from "react";
import { PageWindow } from "./PageWindow";
import { EMPTY, formatMoment, formatNumber } from "./format";
import { PULT_OPEN, operatorText, type MachineControl, type MachineRunState } from "./machineControlApi";
import { machinesApi, usePolled, type MachineCard, type Ob019Live, type Tone } from "./machinesApi";
import { Ob099CardView } from "./Ob099Card";
import { Ob019Controls, Ob019Pult } from "./Ob019Controls";
import { Ob019SettingsPanel } from "./Ob019SettingsPanel";

/** Живая карточка ОБ.019 (линия резки и зачистки с лазерной маркировкой) как пульт у станка (навык erp-machine-hmi,
 *  владелец 19.11.2026): сверху один раз — состояние словами, связь и что делать дальше; под ним команды
 *  (Ob019Controls.tsx), затем «Выполнение задания», «Что режем», чертёж провода, запись, проба и поправка
 *  (Ob019SettingsPanel.tsx); «Подключение» — свёрнуто внизу. Открытый на ПК пульт станка — одной строкой, без имени
 *  программы, с кнопкой «Закрыть пульт на ПК»; на связи — тихая «Открыть пульт на ПК» (Ob019Pult). Опрос раз в 3 с,
 *  пока окно открыто. */

type Fact = [label: string, value: string];

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
 *  уже в строке состояния и в «Выполнении задания». */
function liveFacts(live: Ob019Live, running: boolean): Fact[] {
  const { job } = live;
  const facts: Fact[] = [
    ["Режим", live.mode.text],
    ["Сделано", job.total_set === null || running ? "" : `${count(job.total_finished)} из ${count(job.total_set)} шт`],
    ["Пучок", job.batch_set ? `${count(job.batch_finished)} из ${count(job.batch_set)} шт` : ""],
    ["Заказ", job.order_number ?? ""],
    ["Изделие", job.product ?? ""],
    ["Скорость", job.uph ? `${formatNumber(job.uph)} шт/ч` : ""],
  ];
  return facts.filter(([, value]) => value && value !== EMPTY);
}

function plain(value: string | null | undefined): string {
  return value ? value : EMPTY;
}

/** `controls` — строка команд (Ob019Controls.tsx) под строкой состояния. */
export function Ob019CardView({ card, controls = false }: { card: MachineCard; controls?: boolean }) {
  const old = card.link.code !== "ok";
  // The run «Выполнение задания» polls (Ob019SettingsPanel) and why «Пуск» would not go (Ob019Controls): the status
  // strip and the command row say the same.
  const [runState, setRunState] = useState<MachineRunState | null>(null);
  const [startWhy, setStartWhy] = useState<string | undefined>(undefined);
  // The command poll of the command row: the operator panel button in the status strip shows its progress too.
  const [control, setControl] = useState<MachineControl | null>(null);
  const run = runState?.run;
  const running = !!run && !["closed", "failed", "moved"].includes(run.stage);
  const status = machineStatus(card, startWhy);
  const facts = card.live ? liveFacts(card.live, running) : [];
  // The agent's answer only when it says more than the state line above (the server already translates known ones).
  const agentNote = card.error && card.error !== card.link.text && operatorText(card.error) !== PULT_OPEN ? card.error : "";
  return <>
    <div className={`machine-status${status.tone !== "neutral" ? ` is-${status.tone}` : ""}`} role="status" aria-label="Состояние станка">
      <strong className={`machine-status-title ${toneClass(status.tone)}`}>{status.title}</strong>
      <span className="machine-status-link">{status.link}</span>
      {status.hint && <span className="machine-status-hint">{status.hint}</span>}
      {controls && <Ob019Pult card={card} control={control} />}
      <span className="machine-status-estop">Аварийная остановка — кнопкой на станке</span>
    </div>
    {facts.length > 0 && <dl className={`machine-summary${old ? " machine-old" : ""}`}
      aria-label={old ? "Последние полученные данные" : "Данные станка"}>
      {old && card.reported_at && <div><dt>Данные на</dt><dd>{formatMoment(card.reported_at)}</dd></div>}
      {facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl>}
    {controls && <Ob019Controls card={card} run={runState ? runState.run : undefined} onStartWhy={setStartWhy} onControl={setControl} />}
    <Ob019SettingsPanel inventory={card.inventory} equipmentId={card.equipment_id} live={card.live} old={old}
      pult={card.link.code === "port_busy"} onRun={setRunState} />
    <details className="machine-fold machine-connection">
      <summary>Подключение</summary>
      <dl className="machine-summary">
        {([["ПК", plain(card.pc)], ["IP", plain(card.ip)], ["Версия агента", plain(card.bridge_version)],
          ["Служба Modbus", plain(card.service_version)], ...(agentNote ? [["Ответ агента", agentNote]] : [])] as Fact[])
          .map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
      </dl>
    </details>
  </>;
}

export function Ob019Card({ equipmentId, title, close, actions }: { equipmentId: string; title: string; close: () => void; actions?: ReactNode }) {
  const load = useCallback((signal: AbortSignal) => machinesApi.card(equipmentId, signal), [equipmentId]);
  const { data, error } = usePolled(load, undefined, "Не удалось загрузить станок");
  return <PageWindow label={title} close={close} width={1100} dismissable>
    <section className="panel machine-card">
      <header className="machine-card-head">
        <h2>{title}</h2>
        {actions}
        <button className="secondary small" onClick={close}>Закрыть</button>
      </header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!data && !error && <p className="empty">Загрузка…</p>}
      {data && (data.kind === "ob099_fins" ? <Ob099CardView card={data} />
        : <Ob019CardView card={data} controls />)}
    </section>
  </PageWindow>;
}
