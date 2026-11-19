import { useCallback } from "react";
import { PageWindow } from "./PageWindow";
import { EMPTY, formatMoment, formatNumber } from "./format";
import { machinesApi, usePolled, type MachineCard, type Ob019Live, type Tone } from "./machinesApi";
import { Ob019Controls } from "./Ob019Controls";
import { Ob019SettingsPanel } from "./Ob019SettingsPanel";

/** Живая карточка ОБ.019 (линия резки и зачистки с лазерной маркировкой): команды станку (Ob019Controls.tsx), состояние
 *  ПЛК, задание и счётчики, параметры провода, настройки (Ob019SettingsPanel.tsx); опрос раз в 3 с, пока окно открыто. */

type Fact = [label: string, value: string, tone?: Tone];

const toneClass = (tone?: Tone) => (tone && tone !== "neutral" ? `tone-${tone}` : "");

function count(value: number | null): string {
  return value === null ? EMPTY : formatNumber(value);
}

function mm(value: number | null): string {
  return value === null ? EMPTY : `${formatNumber(value, { digits: 2, minDigits: 2 })} мм`;
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

function liveFacts(live: Ob019Live): Fact[][] {
  const { job, wire } = live;
  return [
    [
      ["Состояние", live.state.text, live.state.tone],
      ["Авария", live.alarm.text, live.alarm.tone],
      ["Режим", live.mode.text],
      ["Программа", count(job.program)],
      ["Производительность", job.uph === null ? EMPTY : `${formatNumber(job.uph)} шт/ч`],
    ],
    [
      ["Тираж, задано", count(job.total_set)],
      ["Тираж, сделано", count(job.total_finished)],
      ["Пучок, задано", count(job.batch_set)],
      ["Пучок, сделано", count(job.batch_finished)],
      ["Заказ", plain(job.order_number)],
      ["Изделие", plain(job.product)],
      ["Заказ, количество", count(job.order_quantity)],
      ["Заказ, выполнено", count(job.order_finished)],
      ["Задание на станке", job.order_running === null ? EMPTY : job.order_running ? "Идёт" : "Нет"],
    ],
    [
      ["Длина", mm(wire.length_mm)],
      ["Зачистка начала", mm(wire.strip_front_mm)],
      ["Стягивание начала", mm(wire.pull_front_mm)],
      ["Зачистка конца", mm(wire.strip_back_mm)],
      ["Стягивание конца", mm(wire.pull_back_mm)],
      ["Диаметр провода", mm(wire.diameter_mm)],
      ["Отвод ножа", mm(wire.knife_back_mm)],
    ],
  ];
}

export function Ob019CardView({ card }: { card: MachineCard }) {
  const old = card.link.code !== "ok";
  const blocks = card.live ? liveFacts(card.live) : null;
  return <>
    <p className={`machine-link ${toneClass(card.link.tone)}`} role="status">
      {card.live && !old && <strong className={`machine-state ${toneClass(card.live.state.tone)}`}>{card.live.state.text}</strong>}
      <strong>{card.link.text}</strong>
      <span>Последний сигнал: {card.reported_at ? `${formatMoment(card.reported_at)} · ${ago(card.age_seconds)}` : EMPTY}</span>
    </p>
    {card.error && card.link.code !== "ok" && <p className="warning-inline">{card.error}</p>}
    {!blocks
      ? <p className="empty">Нет данных ПЛК — агент на связи, но станок ещё не ответил</p>
      : <div className={`machine-grid${old ? " machine-old" : ""}`} aria-label={old ? "Последние полученные данные" : "Данные станка"}>
        <Facts title="Станок" facts={blocks[0]} />
        <Facts title="Задание и счётчики" facts={blocks[1]} />
        <Facts title="Провод" facts={blocks[2]} />
      </div>}
    <Ob019SettingsPanel inventory={card.inventory} />
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
      {data && <Ob019Controls card={data} />}
      {data && <Ob019CardView card={data} />}
    </section>
  </PageWindow>;
}
