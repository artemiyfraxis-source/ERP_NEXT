import { EMPTY, formatDay, formatNumber, formatWallMoment } from "./format";
import type { Ob099Live, Ob099MachineCard, Ob099Today, Tone } from "./machinesApi";

/** Живая карточка пресса ОБ.099 (BZW, сервопривод, монитор усилия CFM): состояние ПЛК, текущее задание, высота обжима,
 *  ресурс ножей, обжимы за день с причинами брака и аварии — только чтение. Агент на ПК пресса читает ПЛК, журнал
 *  обжимов BZW2.5 и журнал аварий; управляет прессом по-прежнему BZW2.5. */

type Fact = [label: string, value: string, tone?: Tone];

const toneClass = (tone?: Tone) => (tone && tone !== "neutral" ? `tone-${tone}` : "");

const plain = (value: string | null | undefined) => (value ? value : EMPTY);

const count = (value: number | null) => (value === null ? EMPTY : formatNumber(value));

function mm(value: number | null): string {
  return value === null ? EMPTY : `${formatNumber(value, { digits: 3, minDigits: 3 })} мм`;
}

function ago(seconds: number | null): string {
  if (seconds === null) return EMPTY;
  if (seconds < 60) return `${seconds} с назад`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} мин назад`;
  return `${Math.floor(seconds / 3600)} ч назад`;
}

function yesNo(value: boolean | null, yes: string, no: string): string {
  return value === null ? EMPTY : value ? yes : no;
}

function doneOf(done: number | null, planned: number | null): string {
  if (done === null && planned === null) return EMPTY;
  return `${count(done)} из ${count(planned)}`;
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

function liveBlocks(live: Ob099Live): Fact[][] {
  const { job, crimp, counters } = live;
  const height = crimp.height_mm === null ? EMPTY
    : crimp.tolerance_mm === null ? mm(crimp.height_mm)
      : `${mm(crimp.height_mm)} ± ${formatNumber(crimp.tolerance_mm, { digits: 3, minDigits: 3 })}`;
  const limits = crimp.height_min_mm === null || crimp.height_max_mm === null ? EMPTY
    : `${formatNumber(crimp.height_min_mm, { digits: 3, minDigits: 3 })} – ${mm(crimp.height_max_mm)}`;
  return [
    [
      ["Состояние", live.state.text, live.state.tone],
      ["Режим «Работа»", yesNo(crimp.work_mode, "Включён", "Выключен")],
      ["Контроль усилия CFM", yesNo(crimp.cfm_on, "Включён", "Выключен"), crimp.cfm_on === false ? "warn" : undefined],
      ["Партия, сделано", doneOf(counters.batch_done, counters.batch_set)],
      ["Всего, сделано", doneOf(counters.total_done, counters.total_set)],
      ["Номер оборудования", plain(job.equipment_no)],
    ],
    [
      ["Изделие (рецепт)", plain(job.product)],
      ["Клемма", plain(job.terminal)],
      ["Провод", plain(job.wire)],
      ["Аппликатор", plain(job.applicator)],
      ["Высота обжима", height],
      ["Пределы высоты", limits],
    ],
    live.tools.flatMap((tool): Fact[] => [
      [`${tool.name}, израсходовано`,
        tool.used_pct === null ? EMPTY : `${formatNumber(tool.used_pct, { digits: 1, minDigits: 1 })} %`, tool.tone],
      [`${tool.name}, обжимов`, doneOf(tool.used, tool.life)],
    ]),
  ];
}

function todayBlocks(today: Ob099Today): Fact[][] {
  const last = today.last;
  const ngShare = today.ng_pct === null ? "" : ` · ${formatNumber(today.ng_pct, { digits: 1, minDigits: 1 })} %`;
  return [
    [
      ["Всего обжимов", formatNumber(today.total)],
      ["Годных", formatNumber(today.ok), today.ok ? "ok" : undefined],
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

export function Ob099CardView({ card, incoming }: { card: Ob099MachineCard; incoming?: import("./machinesApi").MachineTaskContext }) {
  const old = card.link.code !== "ok";
  const blocks = card.live ? liveBlocks(card.live) : null;
  const day = formatDay(card.today.day).slice(0, 5);
  const [summary, reasons, alarms] = todayBlocks(card.today);
  return <>
    {incoming && <p className="warning-inline" role="status">Операция ЭТК {incoming.action.etk_position} · из задания. {incoming.action.blocker || "Пресс доступен для наблюдения; загрузка задания не поддерживается"}</p>}
    <p className={`machine-link ${toneClass(card.link.tone)}`} role="status">
      {card.live && !old && <strong className={`machine-state ${toneClass(card.live.state.tone)}`}>{card.live.state.text}</strong>}
      <strong>{card.link.text}</strong>
      <span>Последний сигнал: {card.reported_at ? `${formatWallMoment(card.reported_at)} · ${ago(card.age_seconds)}` : EMPTY}</span>
    </p>
    {card.error && old && <p className="warning-inline">{card.error}</p>}
    {card.live && !old && card.live.alarms.length > 0 && <ul className="machine-alarms" aria-label="Аварии сейчас">
      {card.live.alarms.map((alarm) => <li key={alarm.code} className={toneClass(alarm.tone)}>{alarm.text}</li>)}
    </ul>}
    {!blocks
      ? <p className="empty">Нет данных ПЛК — агент на связи, но пресс ещё не ответил</p>
      : <div className={`machine-grid${old ? " machine-old" : ""}`} aria-label={old ? "Последние полученные данные" : "Данные пресса"}>
        <Facts title="Пресс" facts={blocks[0]} />
        <Facts title="Задание" facts={blocks[1]} />
        <Facts title="Ресурс ножей" facts={blocks[2]} />
      </div>}
    <div className="machine-grid">
      <Facts title={`Обжимы за ${day}`} facts={summary} />
      <Facts title={`Причины брака за ${day}`} facts={reasons} empty="Брака нет" />
      <Facts title={`Аварии за ${day}`} facts={alarms} empty="Аварий нет" />
    </div>
    <Facts title="Подключение" facts={[
      ["ПК", plain(card.pc)], ["IP", plain(card.ip)], ["Версия агента", plain(card.bridge_version)],
      ["Связь с ПЛК", plain(card.service_version)],
    ]} />
  </>;
}
