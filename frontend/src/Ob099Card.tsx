import { useState } from "react";
import { WorkJournal } from "./ManualJobsPage";
import { PageWindow } from "./PageWindow";
import { EMPTY, formatDay, formatMoment, formatNumber, formatWallMoment } from "./format";
import type { MachineTaskContext, Ob099Live, Ob099MachineCard, Ob099Today, Tone } from "./machinesApi";
import { Ob099Setup, usePressSetup } from "./Ob099Setup";

/** Живая карточка пресса ОБ.099 (BZW, сервопривод, монитор усилия CFM) как пульт наладчика (навык erp-machine-hmi,
 *  владелец 23.11.2026): в шапке только «Журнал» и «Закрыть»; сверху один раз — состояние пресса словами, связь с
 *  возрастом сигнала и следующий шаг; под ним одна команда «Подтвердить наладку» (Ob099Setup.tsx), «Что обжимаем» и
 *  «Требуется | На прессе | Настроено»; счётчики и ресурс ножей — одной строкой; обжимы и аварии за день, журнал наладки,
 *  подключение и журнал работ — в окне «Журнал». Агент пресса только читает ПЛК, журнал обжимов и журнал аварий. */

type Fact = [label: string, value: string, tone?: Tone];

const toneClass = (tone?: Tone) => (tone && tone !== "neutral" && tone !== "ok" ? `tone-${tone}` : "");

const plain = (value: string | null | undefined) => (value ? value : EMPTY);

const count = (value: number | null) => (value === null ? EMPTY : formatNumber(value));

function ago(seconds: number | null): string {
  if (seconds === null) return "";
  if (seconds < 60) return `${seconds} с назад`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} мин назад`;
  return `${Math.floor(seconds / 3600)} ч назад`;
}

function Facts({ title, facts, empty }: { title: string; facts: Fact[]; empty?: string }) {
  return <section className="machine-facts" aria-label={title}>
    <h3>{title}</h3>
    {facts.length === 0
      ? <p className="machine-muted machine-none">{empty}</p>
      : <dl>
        {facts.map(([label, value, tone], index) => <div key={`${label}-${index}`}>
          <dt>{label}</dt>
          <dd className={toneClass(tone)}>{value}</dd>
        </div>)}
      </dl>}
  </section>;
}

export interface PressStatus { title: string; tone: Tone; link: string; hint: string }

/** Что делать дальше в состоянии пресса (ob099._state); «Готов»/«Ожидание» — подсказка наладки или «можно обжимать». */
const NEXT_STEP: Record<string, string> = {
  "Нужен сброс": "Сбросьте пресс на его пульте",
  "Сброс выполняется": "Дождитесь конца сброса",
  "Калибровка": "Идёт калибровка — дождитесь конца",
  "Приработка": "Идёт приработка — дождитесь конца",
  "CMK-проверка": "Идёт CMK-проверка — дождитесь конца",
  "Работа": "Идёт обжим — наладку меняйте после остановки",
};

/** Одна строка состояния пресса: что с ним, связь с возрастом сигнала и следующий шаг (`setupHint` — от наладки). */
export function pressStatus(card: Ob099MachineCard, setupHint = ""): PressStatus {
  const signal = card.reported_at ? `сигнал ${formatMoment(card.reported_at)}${card.age_seconds === null ? "" : `, ${ago(card.age_seconds)}`}`
    : "сигнала ещё не было";
  const live = card.live;
  if (card.link.code !== "ok") {
    return { title: card.link.text, tone: card.link.tone === "neutral" ? "neutral" : "danger", link: `Последний ${signal}`,
      hint: live ? "Значения пресса — когда он на связи; ниже серым — последние полученные" : "Значения пресса — когда он на связи" };
  }
  const link = `На связи · ${signal}`;
  if (!live) return { title: "Агент на связи, пресс ещё не ответил", tone: "warn", link, hint: "Проверьте, что пресс включён" };
  const alarm = live.alarms.find((item) => item.tone === "danger" && item.code.startsWith("W80"));
  if (live.state.text === "Авария" || alarm) {
    return { title: `Авария: ${alarm?.text ?? "см. пульт пресса"}`, tone: "danger", link, hint: "Устраните причину и сбросьте аварию на пульте пресса" };
  }
  const { batch_set: set, batch_done: done } = live.counters;
  const title = live.state.text === "Работа" ? set ? `Обжимает · партия ${count(done)} из ${count(set)}` : "Обжимает" : live.state.text;
  const tone: Tone = live.state.tone === "danger" ? "danger" : live.state.tone === "warn" ? "warn" : "neutral";
  const next = NEXT_STEP[live.state.text] ?? (setupHint || "Пресс готов — можно обжимать");
  return { title, tone, link, hint: next };
}

/** Счётчики, ресурс ножей и обжимы за день — одной строкой; отклонения окрашены. */
function summary(live: Ob099Live | null, today: Ob099Today): Fact[] {
  const ngShare = today.ng_pct === null ? "" : ` (${formatNumber(today.ng_pct, { digits: 1, minDigits: 1 })} %)`;
  const facts: Fact[] = [];
  if (live) {
    const { counters, crimp } = live;
    if (counters.batch_set !== null) facts.push(["Партия", `${count(counters.batch_done)} из ${count(counters.batch_set)} шт`]);
    if (counters.total_set !== null) facts.push(["Всего", `${count(counters.total_done)} из ${count(counters.total_set)} шт`]);
    for (const tool of live.tools) {
      if (tool.used_pct !== null) facts.push([tool.name, `${formatNumber(tool.used_pct, { digits: 1, minDigits: 1 })} % ресурса`, tool.tone]);
    }
    if (crimp.cfm_on === false) facts.push(["Контроль усилия", "выключен — брак не будет замечен", "warn"]);
  }
  facts.push(["Сегодня", today.total ? `${formatNumber(today.total)} обжимов · брак ${formatNumber(today.ng)}${ngShare}` : "обжимов нет",
    today.ng ? "danger" : undefined]);
  return facts;
}

function dayFacts(today: Ob099Today): Fact[][] {
  const last = today.last;
  const ngShare = today.ng_pct === null ? "" : ` · ${formatNumber(today.ng_pct, { digits: 1, minDigits: 1 })} %`;
  return [
    [
      ["Всего обжимов", formatNumber(today.total)],
      ["Годных", formatNumber(today.ok)],
      ["Брак", `${formatNumber(today.ng)}${ngShare}`, today.ng ? "danger" : undefined],
      ["Последний обжим", last
        ? [formatWallMoment(last.at), last.result === "OK" ? "годный" : "брак",
          last.peak === null ? "" : `пик ${formatNumber(last.peak)}`].filter(Boolean).join(" · ")
        : EMPTY, last?.result === "NG" ? "danger" : undefined],
    ],
    today.ng_reasons.map((reason): Fact => [reason.text, formatNumber(reason.count), "danger"]),
    today.alarms.map((alarm): Fact => [alarm.text,
      `${formatNumber(alarm.count)}${alarm.last_at ? ` · последняя ${formatWallMoment(alarm.last_at)}` : ""}`]),
  ];
}

export function Ob099CardView({ card, incoming, header, canWrite }: {
  card: Ob099MachineCard; incoming?: MachineTaskContext; header?: { title: string; close: () => void }; canWrite?: boolean;
}) {
  const fresh = card.link.code === "ok";
  const [hint, setHint] = useState("");
  const [journal, setJournal] = useState(false);
  const { setup, error, reload } = usePressSetup(card.inventory);
  const status = pressStatus(card, hint);
  const shownAlarm = status.title.startsWith("Авария: ") ? status.title.slice(8) : "";
  const alarms = fresh && card.live ? card.live.alarms.filter((alarm) => alarm.text !== shownAlarm) : [];
  const facts = summary(card.live, card.today);
  const day = formatDay(card.today.day).slice(0, 5);
  const [crimps, reasons, dayAlarms] = dayFacts(card.today);
  const agentNote = card.error && card.error !== card.link.text ? card.error : "";
  return <>
    <header className="machine-card-head">
      {header && <h2>{header.title}</h2>}
      <button type="button" className="secondary small" onClick={() => setJournal(true)}>Журнал</button>
      {header && <button type="button" className="secondary small" onClick={header.close}>Закрыть</button>}
    </header>
    <div className={`machine-status${status.tone !== "neutral" ? ` is-${status.tone}` : ""}`} role="status" aria-label="Состояние пресса">
      <strong className={`machine-status-title ${toneClass(status.tone)}`}>{status.title}</strong>
      <span className="machine-status-link">ПК: {plain(card.pc)} · агент: {plain(card.bridge_version)} · {status.link}
        {agentNote && <> · {agentNote}</>}</span>
      {status.hint && <span className="machine-status-hint">{status.hint}</span>}
      <span className="machine-status-estop">Аварийная остановка — кнопкой на прессе</span>
    </div>
    {alarms.length > 0 && <ul className="machine-alarms" aria-label="Предупреждения пресса">
      {alarms.map((alarm) => <li key={alarm.code} className={toneClass(alarm.tone)}>{alarm.text}</li>)}
    </ul>}
    <Ob099Setup inventory={card.inventory} live={card.live} fresh={fresh} canWrite={canWrite} incoming={incoming}
      setup={setup} error={error} reload={reload} onHint={setHint} />
    <dl className={`machine-summary${fresh ? "" : " machine-old"}`} aria-label="Счётчики и ресурс">
      {facts.map(([label, value, tone]) => <div key={label}><dt>{label}</dt><dd className={toneClass(tone)}>{value}</dd></div>)}
    </dl>
    {journal && <PageWindow label={`Журнал · ${card.inventory}`} close={() => setJournal(false)} width={1100} dismissable>
      <section className="panel machine-card">
        <header className="machine-card-head"><h2>Журнал · {card.inventory}</h2>
          <button type="button" className="secondary small" onClick={() => setJournal(false)}>Закрыть</button></header>
        <div className="machine-grid">
          <Facts title={`Обжимы за ${day}`} facts={crimps} />
          <Facts title={`Причины брака за ${day}`} facts={reasons} empty="Брака нет" />
          <Facts title={`Аварии за ${day}`} facts={dayAlarms} empty="Аварий нет" />
        </div>
        <section className="machine-facts" aria-label="Журнал наладки">
          <h3>Журнал наладки</h3>
          {!setup?.history.length ? <p className="machine-muted machine-none">Записей наладки нет</p>
            : <ul className="machine-commands">{setup.history.map((item, index) => <li key={index}>
              <span className="machine-mono">{formatMoment(item.at)}</span><span>{item.by}</span>
              <span>{item.operation ? `по заданию: ${item.operation}` : `без задания: ${item.reason}`}</span>
              <span>{Object.entries(item.values).map(([key, text]) => `${setup.parameters.find((row) => row.key === key)?.label ?? key} ${/^-?\d+\.\d+$/.test(text) ? text.replace(".", ",") : text}`).join(", ")}</span>
            </li>)}</ul>}
        </section>
        <Facts title="Подключение" facts={[
          ["ПК", plain(card.pc)], ["IP", plain(card.ip)], ["Версия агента", plain(card.bridge_version)],
          ["Связь с ПЛК", plain(card.service_version)], ["Номер оборудования", plain(card.live?.job.equipment_no)],
        ]} />
        <WorkJournal equipmentId={card.equipment_id} />
      </section>
    </PageWindow>}
  </>;
}
