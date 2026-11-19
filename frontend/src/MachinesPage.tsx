import { useCallback, useMemo, useState } from "react";
import { EMPTY, formatMoment } from "./format";
import { machinesApi, usePolled, type MachineRow, type Tone } from "./machinesApi";
import { ManualJobWindow } from "./ManualJobWindow";
import { manualJobsApi, type ManualJob } from "./manualJobsApi";
import { Ob019Card } from "./Ob019Card";
import { PageWindow } from "./PageWindow";
import "./machines.css";

/** «Станки» (Производство): всё, что подключено к ERP, — ОБ с агентом ERP на своём ПК, принтеры агентов, сканеры и
 *  анализаторы цепей. Плитка — одно устройство (прибор с двух ПК — одна плитка с номером СИ, второй ПК — в «Также на
 *  ПК»), группы — Станки / Принтеры / Приборы и сканеры (владелец 19.11.2026: «плитками»). Плитка станка с агентом
 *  открывает живую карточку, остальные — сведения о подключении. На карточке станка — «Ручная работа»: окно новой
 *  работы вне проекта с этим станком (журнал — раздел «Ручная работа»). */

const GROUPS: [MachineRow["kind"], string][] = [["machine", "Станки"], ["printer", "Принтеры"], ["device", "Приборы и сканеры"]];

const toneClass = (tone: Tone) => (tone === "neutral" ? "machine-muted" : `tone-${tone}`);
const text = (value: string) => value || EMPTY;
/** «—» с сервера — состояние неизвестно: на плитке его не показываем. */
const known = (value: string) => Boolean(value && value !== EMPTY);

function searchText(row: MachineRow): string {
  return [row.inventory, row.name, row.pc, row.ip, row.device, row.state.text, row.link.text, row.others, row.service]
    .join(" ").toLocaleLowerCase("ru");
}

function MachineTile({ row, open }: { row: MachineRow; open: (row: MachineRow) => void }) {
  const absent = row.link.code === "not_installed";
  const place = [row.pc, row.ip].filter(Boolean).join(" · ");
  return <button type="button" className={`machine-tile${absent ? " is-absent" : ""}${row.card ? " has-card" : ""}`}
    onClick={() => open(row)} title={row.card ? "Открыть живую карточку станка" : "Сведения о подключении"}>
    <span className="machine-tile-head">
      <strong className={`machine-tile-no${row.inventory ? "" : " is-none"}`}>{row.inventory || "Без номера"}</strong>
      <span className={`machine-tile-link ${toneClass(row.link.tone)}`}>{row.link.text}</span>
    </span>
    <span className="machine-tile-name" title={row.name}>{text(row.name)}</span>
    {known(row.state.text) && <span className={`machine-tile-state ${toneClass(row.state.tone)}`}>{row.state.text}</span>}
    {row.device && <span className="machine-tile-device" title={row.device}>{row.device}</span>}
    <span className="machine-tile-foot">
      <span className="machine-mono" title={place}>{place || "ПК не известен"}</span>
      <span className="machine-mono">{row.last_seen ? formatMoment(row.last_seen) : EMPTY}</span>
    </span>
  </button>;
}

/** Сведения о плитке без живой карточки: принтер, прибор, станок без агента. */
function MachineInfo({ row, close, manual }: { row: MachineRow; close: () => void; manual?: () => void }) {
  const title = [row.inventory, row.name].filter(Boolean).join(" · ");
  const facts: [string, string, Tone?][] = [
    ["Связь", row.link.text, row.link.tone], ["Состояние", row.state.text, row.state.tone], ["Устройство", row.device],
    ["ПК", row.pc], ["IP", row.ip], ["Агент", row.service], ["Версия агента", row.version],
    ["Последний сигнал", row.last_seen ? formatMoment(row.last_seen) : ""], ["Также на ПК", row.others],
  ];
  return <PageWindow label={title} close={close} width={560} dismissable>
    <section className="panel machine-card">
      <header className="machine-card-head">
        <h2>{title}</h2>
        {manual && <button type="button" className="secondary small machine-manual" onClick={manual}>Ручная работа</button>}
        <button className="secondary small" onClick={close}>Закрыть</button>
      </header>
      {row.link.code === "not_installed" && row.kind === "machine"
        && <p className="warning-inline">Агент ERP на ПК станка не установлен — живой карточки пока нет</p>}
      <section className="machine-facts" aria-label="Подключение">
        <dl>{facts.map(([label, value, tone]) => <div key={label}>
          <dt>{label}</dt>
          <dd className={tone && tone !== "neutral" ? `tone-${tone}` : ""}>{text(value)}</dd>
        </div>)}</dl>
      </section>
    </section>
  </PageWindow>;
}

export function MachinesPage() {
  const load = useCallback((signal: AbortSignal) => machinesApi.list(signal), []);
  const { data, error } = usePolled(load);
  const [open, setOpen] = useState<MachineRow | null>(null);
  const [manual, setManual] = useState<{ row: MachineRow; job: ManualJob | null } | null>(null);
  /** Идёт ручная работа на этом станке — открываем её (завершить / отменить), иначе — новую с этим станком. */
  const openManual = useCallback(async (row: MachineRow) => {
    let job: ManualJob | null = null;
    if (row.equipment_id) {
      const now = new Date();
      const day = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
      try {
        job = (await manualJobsApi.list(day, day, row.equipment_id)).rows.find((item) => item.status === "running") ?? null;
      } catch { /* нет связи — окно новой работы скажет об этом само */ }
    }
    setManual({ row, job });
  }, []);
  const [query, setQuery] = useState("");
  const rows = useMemo(() => data?.rows ?? [], [data]);
  const found = useMemo(() => {
    const words = query.toLocaleLowerCase("ru").split(/\s+/).filter(Boolean);
    return words.length ? rows.filter((row) => { const haystack = searchText(row); return words.every((word) => haystack.includes(word)); })
      : rows;
  }, [rows, query]);
  return <section className="page machines-page">
    <header className="page-head"><h1>Станки</h1></header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!data && !error
      ? <p className="empty">Загрузка…</p>
      : <>
        <div className="machine-toolbar">
          <input className="search" type="search" aria-label="Поиск станков" placeholder="№, название, ПК или IP"
            value={query} onChange={(event) => setQuery(event.target.value)} />
        </div>
        {!rows.length && <p className="empty">Ничего не подключено — установите агент ERP на ПК станка или склада</p>}
        {rows.length > 0 && !found.length && <p className="empty">Ничего не найдено по «{query.trim()}»</p>}
        {GROUPS.map(([kind, title]) => {
          const group = found.filter((row) => row.kind === kind);
          return group.length > 0 && <section key={kind} className="machine-group" aria-label={title}>
            <h2>{title} <span className="machine-muted">· {group.length}</span></h2>
            <div className="machine-tiles">{group.map((row) => <MachineTile key={row.key} row={row} open={setOpen} />)}</div>
          </section>;
        })}
      </>}
    {open?.card && open.equipment_id
      ? <Ob019Card equipmentId={open.equipment_id} title={[open.inventory, open.name].filter(Boolean).join(" · ")}
        close={() => setOpen(null)}
        actions={<button type="button" className="secondary small machine-manual" onClick={() => void openManual(open)}>Ручная работа</button>} />
      : open && <MachineInfo row={open} close={() => setOpen(null)} manual={open.kind === "machine" ? () => void openManual(open) : undefined} />}
    {manual && <ManualJobWindow job={manual.job} equipmentId={manual.row.equipment_id} inventory={manual.row.inventory}
      close={() => setManual(null)} />}
  </section>;
}
