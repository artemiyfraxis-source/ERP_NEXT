import { useCallback, type ReactNode } from "react";
import { PageWindow } from "./PageWindow";
import { EMPTY, formatMoment, formatNumber } from "./format";
import { machinesApi, usePolled, type MachineCard, type Ob019Live, type Tone } from "./machinesApi";
import { Ob099CardView } from "./Ob099Card";
import { Ob019Controls } from "./Ob019Controls";
import { Ob019SettingsPanel } from "./Ob019SettingsPanel";

/** Живая карточка ОБ.019 (линия резки и зачистки с лазерной маркировкой): состояние ПЛК и строка задания, команды станку
 *  (Ob019Controls.tsx), под ними чертёж провода и запись настроек (Ob019SettingsPanel.tsx); опрос раз в 3 с, пока окно
 *  открыто. */

type Fact = [label: string, value: string, tone?: Tone];

const toneClass = (tone?: Tone) => (tone && tone !== "neutral" ? `tone-${tone}` : "");

function count(value: number | null): string {
  return value === null ? EMPTY : formatNumber(value);
}

function plain(value: string | null | undefined): string {
  return value ? value : EMPTY;
}

function ago(seconds: number | null): string {
  if (seconds === null) return EMPTY;
  if (seconds < 60) return `${seconds} с назад`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} мин назад`;
  return `${Math.floor(seconds / 3600)} ч назад`;
}

function Facts({ title, facts }: { title: string; facts: Fact[] }) {
  return <section className="machine-facts" aria-label={title}>
    <h3>{title}</h3>
    <dl>
      {facts.map(([label, value, tone]) => <div key={label}>
        <dt>{label}</dt>
        <dd className={toneClass(tone)}>{value}</dd>
      </div>)}
    </dl>
  </section>;
}

/** Одна строка о станке и задании под состоянием; длины и тираж — на чертеже ниже (владелец 19.11.2026). */
function liveFacts(live: Ob019Live): Fact[] {
  const { job } = live;
  const order = job.order_quantity === null ? EMPTY : `${count(job.order_finished)} из ${count(job.order_quantity)}`;
  return [
    ["Авария", live.alarm.text, live.alarm.tone],
    ["Режим", live.mode.text],
    ["Программа", count(job.program)],
    ["Производительность", job.uph === null ? EMPTY : `${formatNumber(job.uph)} шт/ч`],
    ["Заказ", plain(job.order_number)],
    ["Изделие", plain(job.product)],
    ["Заказ, выполнено", order],
    ["Задание на станке", job.order_running === null ? EMPTY : job.order_running ? "Идёт" : "Нет"],
  ];
}

export function Ob019CardView({ card, controls }: { card: MachineCard; controls?: ReactNode }) {
  const old = card.link.code !== "ok";
  return <>
    <p className={`machine-link ${toneClass(card.link.tone)}`} role="status">
      {card.live && !old && <strong className={`machine-state ${toneClass(card.live.state.tone)}`}>{card.live.state.text}</strong>}
      <strong>{card.link.text}</strong>
      <span>Последний сигнал: {card.reported_at ? `${formatMoment(card.reported_at)} · ${ago(card.age_seconds)}` : EMPTY}</span>
    </p>
    {card.live && <dl className={`machine-summary${old ? " machine-old" : ""}`}
      aria-label={old ? "Последние полученные данные" : "Данные станка"}>
      {liveFacts(card.live).map(([label, value, tone]) => <div key={label}>
        <dt>{label}</dt>
        <dd className={toneClass(tone)}>{value}</dd>
      </div>)}
    </dl>}
    {card.error && card.link.code !== "ok" && <p className="warning-inline">{card.error}</p>}
    {controls}
    {!card.live && <p className="empty">Нет данных ПЛК — агент на связи, но станок ещё не ответил</p>}
    <Ob019SettingsPanel inventory={card.inventory} live={card.live} old={old} />
    <Facts title="Подключение" facts={[
      ["ПК", plain(card.pc)], ["IP", plain(card.ip)], ["Версия агента", plain(card.bridge_version)],
      ["Служба Modbus", plain(card.service_version)],
    ]} />
  </>;
}

export function Ob019Card({ equipmentId, title, close }: { equipmentId: string; title: string; close: () => void }) {
  const load = useCallback((signal: AbortSignal) => machinesApi.card(equipmentId, signal), [equipmentId]);
  const { data, error } = usePolled(load, undefined, "Не удалось загрузить станок");
  return <PageWindow label={title} close={close} width={1100} dismissable>
    <section className="panel machine-card">
      <header className="machine-card-head">
        <h2>{title}</h2>
        <button className="secondary small" onClick={close}>Закрыть</button>
      </header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!data && !error && <p className="empty">Загрузка…</p>}
      {data && (data.kind === "ob099_fins" ? <Ob099CardView card={data} />
        : <Ob019CardView card={data} controls={<Ob019Controls card={data} />} />)}
    </section>
  </PageWindow>;
}
