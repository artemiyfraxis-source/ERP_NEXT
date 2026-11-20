import { useCallback, useEffect, useMemo, useState } from "react";
import { EMPTY, formatMoment } from "./format";
import { equipmentSetupApi, type SetupViewer } from "./equipmentSetupApi";
import { EquipmentSetupPage } from "./EquipmentSetupPage";
import { machinesApi, usePolled, type MachineRow, type Tone } from "./machinesApi";
import { MachineSetupPart } from "./MachineSetup";
import { ManualJobWindow } from "./ManualJobWindow";
import { manualJobsApi, type ManualJob } from "./manualJobsApi";
import { Ob019Card } from "./Ob019Card";
import { Ob019SetupPanel } from "./Ob019SetupPanel";
import { PageWindow } from "./PageWindow";
import "./machines.css";

/** «Оборудование» (Производство; бывшие «Станки», владелец 19.11.2026): «Запросы на наладку» — список заданий наладки
 *  с окном задания (все шаги бывшего экрана «Наладка оборудования»: назначить, начать, продолжить, подтвердить
 *  готовность, заблокировать, отменить, рецепт и пробные измерения); станки цеха — «С управлением» (ERP достаёт их
 *  через агента: живое состояние, запись на станок) и «Без управления» (отмечены в БД.ОБ галочкой «Показывать в
 *  «Оборудовании»»), плитками по семействам операций; принтеры и приборы агентов — своя вкладка; «Все» — всё сразу.
 *  На плитке — артикул, участок, «Ждёт наладки» по открытому заданию; в карточке станка — «Наладка» и «Ручная работа». */

export type View = "setup" | "controlled" | "manual" | "agents" | "all";
type Site = "" | "ЦАР" | "ЖУК";
const VIEWS: [View, string][] = [["setup", "Запросы на наладку"], ["controlled", "С управлением"], ["manual", "Без управления"],
  ["agents", "Принтеры и приборы"], ["all", "Все"]];
const SITES: [Site, string][] = [["", "Все участки"], ["ЦАР", "ЦАР"], ["ЖУК", "ЖУК"]];
const AGENT_GROUPS: [MachineRow["kind"], string][] = [["printer", "Принтеры"], ["device", "Приборы и сканеры"]];
const OTHER_FAMILY = "Прочее оборудование";

const toneClass = (tone: Tone) => (tone === "neutral" ? "machine-muted" : `tone-${tone}`);
const text = (value: string) => value || EMPTY;
/** «—» с сервера — состояние неизвестно: на плитке его не показываем. */
const known = (value: string) => Boolean(value && value !== EMPTY);
const byNumber = (a: MachineRow, b: MachineRow) => a.inventory.localeCompare(b.inventory, "ru", { numeric: true }) || a.name.localeCompare(b.name, "ru");
const viewOf = (row: MachineRow): View => (row.kind !== "machine" ? "agents" : row.controlled ? "controlled" : "manual");
const titleOf = (row: MachineRow) => [row.inventory, row.name].filter(Boolean).join(" · ");

function searchText(row: MachineRow): string {
  return [row.inventory, row.name, row.article, row.pc, row.ip, row.device, row.state.text, row.link.text, row.others, row.service,
    row.site, row.family, ...(row.operations ?? [])].join(" ").toLocaleLowerCase("ru");
}

function MachineTile({ row, open }: { row: MachineRow; open: (row: MachineRow) => void }) {
  const absent = row.link.code === "not_installed" || row.link.code === "not_connected";
  const place = [row.pc, row.ip].filter(Boolean).join(" · ");
  const model = [row.article, row.site].filter(Boolean).join(" · ");
  const pending = row.pending_setup ?? 0;
  const setup = row.setup?.code ? row.setup : null;
  return <button type="button" className={`machine-tile${absent ? " is-absent" : ""}${row.card ? " has-card" : ""}`}
    onClick={() => open(row)} title={row.card ? "Открыть карточку станка" : "Сведения и наладка"}>
    <span className="machine-tile-head">
      <strong className={`machine-tile-no${row.inventory ? "" : " is-none"}`}>{row.inventory || "Без номера"}</strong>
      <span className={`machine-tile-link ${toneClass(row.link.tone)}`}>{row.link.text}</span>
    </span>
    <span className="machine-tile-name" title={row.name}>{text(row.name)}</span>
    {model && <span className="machine-tile-article machine-mono" title={model}>{model}</span>}
    {row.operations?.length > 0 && <span className="machine-tile-ops" title={row.operations.join(", ")}>{row.operations.join(", ")}</span>}
    {known(row.state.text) && <span className={`machine-tile-state ${toneClass(row.state.tone)}`}>{row.state.text}</span>}
    {row.device && <span className="machine-tile-device" title={row.device}>{row.device}</span>}
    {(pending > 0 || setup) && <span className="machine-tile-badges">
      {pending > 0 && <span className="machine-badge tone-warn">Ждёт наладки{pending > 1 ? ` · ${pending}` : ""}</span>}
      {setup && <span className="machine-badge">{setup.code === "none" ? "Без наладки" : `Наладка: ${setup.label}`}</span>}
    </span>}
    {(place || row.last_seen || row.link.code === "not_installed") && <span className="machine-tile-foot">
      <span className="machine-mono" title={place}>{place || "ПК не известен"}</span>
      <span className="machine-mono">{row.last_seen ? formatMoment(row.last_seen) : EMPTY}</span>
    </span>}
  </button>;
}

/** Карточка плитки без живой карточки: станок без управления (сведения + «Наладка»), принтер, прибор. */
function MachineInfo({ row, close, manual, canWrite, saved }: {
  row: MachineRow; close: () => void; manual?: () => void; canWrite: boolean; saved: () => void;
}) {
  const title = titleOf(row);
  const facts: [string, string, Tone?][] = row.kind === "machine" && !row.controlled
    ? [["Артикул", row.article], ["Участок", row.site], ["Операции", (row.operations ?? []).join(", ")], ["Связь", row.link.text, row.link.tone]]
    : [["Артикул", row.article], ["Связь", row.link.text, row.link.tone], ["Состояние", row.state.text, row.state.tone], ["Устройство", row.device],
      ["ПК", row.pc], ["IP", row.ip], ["Агент", row.service], ["Версия агента", row.version],
      ["Последний сигнал", row.last_seen ? formatMoment(row.last_seen) : ""], ["Также на ПК", row.others]];
  return <PageWindow label={title} close={close} width={row.kind === "machine" ? 960 : 560} dismissable>
    <section className="panel machine-card">
      <header className="machine-card-head">
        <h2>{title}</h2>
        {manual && <button type="button" className="secondary small machine-manual" onClick={manual}>Ручная работа</button>}
        <button type="button" className="secondary small" onClick={close}>Закрыть</button>
      </header>
      {row.link.code === "not_installed" && row.kind === "machine"
        && <p className="warning-inline">Агент ERP на ПК станка не установлен — живой карточки пока нет</p>}
      <section className="machine-facts" aria-label="Сведения">
        <dl>{facts.map(([label, value, tone]) => <div key={label}>
          <dt>{label}</dt>
          <dd className={tone && tone !== "neutral" ? `tone-${tone}` : ""}>{text(value)}</dd>
        </div>)}</dl>
      </section>
      {row.kind === "machine" && (row.equipment_id
        ? <MachineSetupPart inventory={row.inventory} canWrite={canWrite} saved={saved} />
        : <section className="machine-facts machine-setup" aria-label="Наладка"><h3>Наладка</h3>
          <p className="machine-muted">Станка нет в БД.ОБ — наладка появится, когда его внесут в базу.</p></section>)}
    </section>
  </PageWindow>;
}

/** «Наладка» станка с управлением — окно поверх его живой карточки; у ОБ.019 — ещё подбор по эталону с записью на станок. */
function SetupWindow({ row, canWrite, setupViewer, close, saved }: {
  row: MachineRow; canWrite: boolean; setupViewer: boolean; close: () => void; saved: () => void;
}) {
  const [viewer, setViewer] = useState<SetupViewer | null>(null), [panel, setPanel] = useState(false), [error, setError] = useState("");
  const ob019 = row.inventory.replace(/\s+/g, "").toUpperCase() === "ОБ.019" && row.equipment_id && setupViewer;
  async function openPanel() {
    try { setViewer(viewer ?? await equipmentSetupApi.viewer()); setPanel(true); }
    catch { setError("Подбор по эталону сейчас недоступен"); }
  }
  return <PageWindow label={`Наладка ${row.inventory}`} close={close} width={960} dismissable>
    <section className="panel machine-card">
      <header className="machine-card-head"><h2>Наладка · {titleOf(row)}</h2><button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      {error && <p className="error-banner" role="alert">{error}</p>}
      <MachineSetupPart inventory={row.inventory} canWrite={canWrite} saved={saved}
        extra={ob019 ? <button type="button" className="secondary small" onClick={() => void openPanel()}>Подбор по эталону и запись на станок</button> : undefined} />
    </section>
    {panel && viewer && row.equipment_id && <Ob019SetupPanel machineId={row.equipment_id} viewer={viewer} close={() => setPanel(false)} />}
  </PageWindow>;
}

const OPEN_SETUP = new Set(["New", "InProgress", "Blocked"]);

/** `initialView` — вкладка, выбранная ссылкой; без неё «Запросы на наладку» открываются первыми, когда у сотрудника
 *  с доступом к наладке есть открытые задания, иначе — «С управлением» (владелец 19.11.2026). */
export function MachinesPage({ setupViewer = false, canWrite = false, initialView }: {
  setupViewer?: boolean; canWrite?: boolean; initialView?: View;
}) {
  const load = useCallback((signal: AbortSignal) => machinesApi.list(signal), []);
  const { data, error } = usePolled(load);
  const [open, setOpen] = useState<MachineRow | null>(null);
  const [setupOf, setSetupOf] = useState<MachineRow | null>(null);
  const [manual, setManual] = useState<{ row: MachineRow; job: ManualJob | null } | null>(null);
  const [chosen, setView] = useState<View | null>(initialView ?? null);
  useEffect(() => {
    if (!setupViewer || chosen !== null) return;
    let live = true;
    equipmentSetupApi.tasks().then((page) => {
      if (live && page.items.some((item) => OPEN_SETUP.has(item.status))) setView((current) => current ?? "setup");
    }).catch(() => undefined);
    return () => { live = false; };
  }, [setupViewer, chosen]);
  // «Запросы на наладку» — только тому, кому открыта наладка (её ключ проверяет App).
  const view: View = chosen === null || (chosen === "setup" && !setupViewer) ? "controlled" : chosen;
  const views = VIEWS.filter(([id]) => id !== "setup" || setupViewer);
  const [site, setSite] = useState<Site>("");
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
  const atSite = (row: MachineRow) => row.kind !== "machine" || !site || row.site === site;
  const inView = (row: MachineRow, which: View) => which === "all" || viewOf(row) === which;
  const count = (which: View) => found.filter((row) => inView(row, which) && atSite(row)).length;
  const siteCount = (which: Site) => found.filter((row) => row.kind === "machine" && inView(row, view) && (!which || row.site === which)).length;
  const shown = view === "setup" ? [] : found.filter((row) => inView(row, view) && atSite(row)).sort(byNumber);
  const families = data?.families ?? [];
  const machineRows = shown.filter((row) => row.kind === "machine");
  const familyGroups: [string, MachineRow[]][] = [...families, ...new Set(machineRows.map((row) => row.family || OTHER_FAMILY)
    .filter((family) => !families.includes(family)))].map((family) => [family, machineRows.filter((row) => (row.family || OTHER_FAMILY) === family)]);
  const agentGroups: [string, MachineRow[]][] = AGENT_GROUPS.map(([kind, title]) => [title, shown.filter((row) => row.kind === kind)]);
  const groups = view === "agents" ? agentGroups : view === "all" ? [...familyGroups, ...agentGroups] : familyGroups;
  const empty = view === "controlled" ? "Нет станков с агентом ERP — установите агент на ПК станка"
    : view === "manual" ? "Нет станков без управления — отметьте станок в БД.ОБ галочкой «Показывать в «Оборудовании»»"
      : view === "all" ? "Ничего не подключено и не отмечено — установите агент ERP или отметьте станок в БД.ОБ"
        : "Принтеры и приборы агентов не найдены";
  return <section className="page machines-page">
    <header className="page-head"><h1>Оборудование</h1></header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!data && !error
      ? <p className="empty">Загрузка…</p>
      : <>
        <div className="machine-toolbar">
          <div className="seg-tabs" role="tablist" aria-label="Оборудование">
            {views.map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={view === id} className={view === id ? "on" : ""}
              onClick={() => setView(id)}>{label}{id !== "setup" && <span className="ship-tab-count">{count(id)}</span>}</button>)}
          </div>
          {view !== "agents" && view !== "setup" && <div className="seg-tabs sub" role="radiogroup" aria-label="Участок">
            {SITES.map(([id, label]) => <button key={id || "all"} type="button" role="radio" aria-checked={site === id} className={site === id ? "on" : ""}
              onClick={() => setSite(id)}>{label}<span className={`ship-tab-count${siteCount(id) ? "" : " is-zero"}`}>{siteCount(id)}</span></button>)}
          </div>}
          {view !== "setup" && <input className="search" type="search" name="equipment-search" autoComplete="off" spellCheck={false} aria-label="Поиск оборудования" placeholder="№, название, артикул, ПК или IP"
            value={query} onChange={(event) => setQuery(event.target.value)} />}
        </div>
        {view === "setup" && <EquipmentSetupPage embedded />}
        {view !== "setup" && !shown.length && (query.trim() ? <p className="empty">Ничего не найдено по «{query.trim()}»</p> : <p className="empty">{empty}</p>)}
        {groups.map(([title, group]) => group.length > 0 && <section key={title} className="machine-group" aria-label={title}>
          <h2>{title} <span className="machine-muted">· {group.length}</span></h2>
          <div className="machine-tiles">{group.map((row) => <MachineTile key={row.key} row={row} open={setOpen} />)}</div>
        </section>)}
      </>}
    {open?.card && open.equipment_id
      ? <Ob019Card equipmentId={open.equipment_id} title={[titleOf(open), open.article].filter(Boolean).join(" · ")}
        close={() => setOpen(null)}
        actions={<>
          <button type="button" className="secondary small machine-manual" onClick={() => setSetupOf(open)}>
            Наладка{open.pending_setup ? " · ждёт" : ""}</button>
          <button type="button" className="secondary small" onClick={() => void openManual(open)}>Ручная работа</button>
        </>} />
      : open && <MachineInfo row={open} close={() => setOpen(null)} canWrite={canWrite} saved={() => undefined}
        manual={open.kind === "machine" ? () => void openManual(open) : undefined} />}
    {setupOf && <SetupWindow row={setupOf} canWrite={canWrite} setupViewer={setupViewer} close={() => setSetupOf(null)} saved={() => undefined} />}
    {manual && <ManualJobWindow job={manual.job} equipmentId={manual.row.equipment_id} inventory={manual.row.inventory}
      close={() => setManual(null)} />}
  </section>;
}
