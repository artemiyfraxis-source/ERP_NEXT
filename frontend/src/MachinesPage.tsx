import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnalyserCard, drivesBench } from "./AnalyserCard";
import { EMPTY, formatMoment } from "./format";
import { equipmentSetupApi, type SetupViewer } from "./equipmentSetupApi";
import { EquipmentSetupPage } from "./EquipmentSetupPage";
import { machinesApi, usePolled, type MachineRow, type Tone } from "./machinesApi";
import { MachineSetupPart } from "./MachineSetup";
import { MachineWorkCard } from "./MachineWorkCard";
import { ManualJobWindow } from "./ManualJobWindow";
import { MarkingMachineCard } from "./MarkingMachineCard";
import { manualJobsApi, type ManualJob } from "./manualJobsApi";
import { Ob019Card } from "./Ob019Card";
import { urlParam } from "./navigation";
import { Ob019SetupPanel } from "./Ob019SetupPanel";
import { PageWindow } from "./PageWindow";
import { vnaBenchApi } from "./vnaBenchApi";
import { WarehouseDeviceCard } from "./WarehouseDeviceCard";
import "./machines.css";

/** «Оборудование» (Производство; бывшие «Станки», владелец 19.11.2026): «Запросы на наладку» — список заданий наладки
 *  с окном задания (все шаги бывшего экрана «Наладка оборудования»: назначить, начать, продолжить, подтвердить
 *  готовность, заблокировать, отменить, рецепт и пробные измерения); «Всё оборудование» — один список плитками по
 *  группам назначения (`family` строки: «Резка», «Опрессовка», «Принтеры и МФУ», …; порядок — `families` ответа API;
 *  владелец 20.11.2026: «почему тут по разным раскидано»). Связь — значок на плитке; фильтр «Только подключённые»
 *  оставляет станки и приборы, которыми ERP управляет через агента.
 *  На плитке — артикул, участок, «Ждёт наладки» по открытому заданию; в карточке станка — «Наладка» и «Ручная работа».
 *  Анализатор цепей открывает свою карточку (`AnalyserCard`): Planar — рабочее место ВАЦ (бывшая вкладка ОТК, владелец
 *  20.11.2026; старая ссылка `?section=quality&tab=vna` ведёт сюда, `&tab=vna` открывает эту карточку), Arinst — замер файлом. */

export type View = "setup" | "equipment";
type Site = "" | "ЦАР" | "ЖУК";
const VIEWS: [View, string][] = [["setup", "Запросы на наладку"], ["equipment", "Всё оборудование"]];
const SITES: [Site, string][] = [["", "Все участки"], ["ЦАР", "ЦАР"], ["ЖУК", "ЖУК"]];
const OTHER_FAMILY = "Прочее";

const toneClass = (tone: Tone) => (tone === "neutral" ? "machine-muted" : `tone-${tone}`);
const text = (value: string) => value || EMPTY;
/** «—» с сервера — состояние неизвестно: на плитке его не показываем. */
const known = (value: string) => Boolean(value && value !== EMPTY);
const byNumber = (a: MachineRow, b: MachineRow) => a.inventory.localeCompare(b.inventory, "ru", { numeric: true }) || a.name.localeCompare(b.name, "ru");
/** Станок цеха без управления, внесённый в БД.ОБ: рабочая карточка с очередью; принтеры, приборы и станки без записи в БД.ОБ — сведения. */
const hasWorkCard = (row: MachineRow) => row.kind === "machine" && !row.controlled && Boolean(row.equipment_id);
const titleOf = (row: MachineRow) => [row.inventory, row.name].filter(Boolean).join(" · ");

function searchText(row: MachineRow): string {
  return [row.inventory, row.name, row.article, row.pc, row.ip, row.device, row.state.text, row.link.text, row.others, row.service,
    row.site, row.family, ...(row.operations ?? [])].join(" ").toLocaleLowerCase("ru");
}

function MachineTile({ row, open }: { row: MachineRow; open: (row: MachineRow) => void }) {
  const absent = row.link.code === "not_installed" || row.link.code === "not_connected";
  const seen = row.pc && row.last_seen ? formatMoment(row.last_seen) : "";
  const place = row.pc ? [row.pc, row.ip].filter(Boolean).join(" · ") : "ПК не известен";
  const model = [row.article || (row.kind !== "machine" ? row.device : ""), row.site].filter(Boolean).join(" · ");
  const operations = (row.operations ?? []).join(", ");
  const pending = row.pending_setup ?? 0;
  const setup = row.setup?.code ? row.setup : null;
  const kind = row.station?.kind === "labels" ? "Принтер этикеток"
    : row.station?.kind === "mfp" ? (row.station.scanners.length ? (row.station.printers.length ? "МФУ" : "Сканер") : "Принтер документов")
      : row.kind === "printer" ? "Принтер" : row.kind === "device" ? "Прибор" : "Станок";
  return <button type="button" className={`machine-tile${absent ? " is-absent" : ""}${row.card ? " has-card" : ""}`}
    onClick={() => open(row)} title={row.card ? "Открыть карточку станка" : row.vna || row.station ? "Открыть карточку прибора" : "Сведения и наладка"}>
    <span className="machine-tile-head">
      <strong className="machine-tile-no">{row.inventory || kind}</strong>
      <span className={`machine-tile-link ${toneClass(row.link.tone)}`}>{row.link.code === "not_installed" ? "Нет агента" : row.link.text}</span>
    </span>
    <span className="machine-tile-name" title={row.name}>{text(row.name)}</span>
    <span className="machine-tile-article machine-mono" title={model}>{model}</span>
    <span className="machine-tile-ops" title={operations}>{operations}</span>
    <span className="machine-tile-device" title={[known(row.state.text) ? row.state.text : "", row.device].filter(Boolean).join(" · ")}>
      {known(row.state.text) && <span className={`machine-tile-state ${toneClass(row.state.tone)}`}>{row.state.text}</span>}
    </span>
    <span className="machine-tile-badges">
      {pending > 0 && <span className="machine-badge tone-warn" title={`Ждёт наладки${pending > 1 ? ` · ${pending}` : ""}`}>
        Ждёт наладки{pending > 1 ? ` · ${pending}` : ""}</span>}
      {setup && <span className="machine-badge" title={`Наладка: ${setup.label}`}>Наладка: {setup.label}</span>}
    </span>
    <span className="machine-tile-foot">
      <span className="machine-mono" title={place}>{place}</span>
      {seen && <span className="machine-tile-seen machine-mono">{seen}</span>}
    </span>
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
export function MachinesPage({ setupViewer = false, canWrite = false, canQuality = false, initialView }: {
  setupViewer?: boolean; canWrite?: boolean; canQuality?: boolean; initialView?: View;
}) {
  const load = useCallback((signal: AbortSignal) => machinesApi.list(signal), []);
  const { data, error } = usePolled(load);
  /** Открытая карточка; «bench» — рабочее место ВАЦ по ссылке, когда прибора нет в списке агентов. */
  const [open, setOpen] = useState<MachineRow | "bench" | null>(null);
  // СИ, которым управляет рабочее место ВАЦ (сервер называет его сам); «» — рабочее место не отвечает.
  const [benchNumber, setBenchNumber] = useState<string | null>(canQuality ? null : "");
  useEffect(() => {
    if (!canQuality) return;
    let live = true;
    vnaBenchApi.state().then((state) => { if (live) setBenchNumber(state?.device?.instrument_number || ""); })
      .catch(() => { if (live) setBenchNumber(""); });
    return () => { live = false; };
  }, [canQuality]);
  const [setupOf, setSetupOf] = useState<MachineRow | null>(null);
  const [manual, setManual] = useState<{ row: MachineRow; job: ManualJob | null } | null>(null);
  const wantsBench = useRef(urlParam("tab") === "vna");
  const [chosen, setView] = useState<View | null>(initialView ?? (wantsBench.current ? "equipment" : null));
  useEffect(() => {
    if (!setupViewer || chosen !== null) return;
    let live = true;
    equipmentSetupApi.tasks().then((page) => {
      if (live && page.items.some((item) => OPEN_SETUP.has(item.status))) setView((current) => current ?? "setup");
    }).catch(() => undefined);
    return () => { live = false; };
  }, [setupViewer, chosen]);
  // «Запросы на наладку» — только тому, кому открыта наладка (её ключ проверяет App).
  const view: View = chosen === "setup" && setupViewer ? "setup" : "equipment";
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
  const wantsPrinter = useRef(urlParam("printer") === "labels" || new URLSearchParams(location.search).get("section") === "labeldesigner");
  useEffect(() => {
    if (!wantsPrinter.current || !data) return;
    const printer = rows.find(row => row.station?.kind === "labels");
    if (printer) { wantsPrinter.current=false; setView("equipment"); setOpen(printer); }
  }, [data,rows]);
  // Ссылка на рабочее место ВАЦ (`&tab=vna`, в т. ч. старая из ОТК): карточка Planar, которым оно управляет.
  useEffect(() => {
    if (!wantsBench.current || !data || benchNumber === null) return;
    wantsBench.current = false;
    const row = rows.find((item) => drivesBench(item, benchNumber)) ?? rows.find((item) => item.vna === "s2vna");
    setOpen(row ?? (benchNumber ? "bench" : null));
  }, [data, benchNumber, rows]);
  // Открытая карточка Planar держит `&tab=vna` в адресе — её можно сохранить закладкой.
  const opened = open === "bench" ? null : open && (rows.find((row) => row.key === open.key) ?? open);
  const benchOpen = open === "bench" || opened?.vna === "s2vna";
  useEffect(() => {
    const url = new URL(window.location.href);
    if ((url.searchParams.get("tab") === "vna") === benchOpen) return;
    if (benchOpen) url.searchParams.set("tab", "vna"); else url.searchParams.delete("tab");
    window.history.replaceState(window.history.state, "", url);
  }, [benchOpen]);
  const [onlyConnected, setOnlyConnected] = useState(false);
  const found = useMemo(() => {
    const words = query.toLocaleLowerCase("ru").split(/\s+/).filter(Boolean);
    return words.length ? rows.filter((row) => { const haystack = searchText(row); return words.every((word) => haystack.includes(word)); })
      : rows;
  }, [rows, query]);
  const showSites = new Set(rows.filter((row) => row.kind === "machine" && row.site).map((row) => row.site)).size >= 2;
  useEffect(() => { if (!showSites) setSite(""); }, [showSites]);
  const activeSite = showSites ? site : "";
  const atSite = (row: MachineRow) => !activeSite || (row.kind === "machine" && row.site === activeSite);
  /** «Только подключённые» — на связи сейчас (координатор 21.11.2026), а не «есть агент»: станок — связь «На связи»;
   *  принтер и прибор — ещё и сам виден агенту («Принтер виден», «Подключён»). */
  const connected = (row: MachineRow) => row.link.code === "ok" && (row.kind === "machine" || row.state.tone === "ok");
  const connectedCount = found.filter((row) => atSite(row) && connected(row)).length;
  const siteCount = (which: Site) => found.filter((row) => (!which || (row.kind === "machine" && row.site === which)) && (!onlyConnected || connected(row))).length;
  const shown = view === "setup" ? [] : found.filter((row) => atSite(row) && (!onlyConnected || connected(row))).sort(byNumber);
  const families = data?.families ?? [];
  // Группа строки — её `family`; группа, которой нет в `families` ответа, встаёт перед «Прочее», а не прячется в нём.
  const inGroup = (row: MachineRow) => row.family || OTHER_FAMILY;
  const order = [...new Set([...families, ...shown.map(inGroup)].filter((family) => family !== OTHER_FAMILY)), OTHER_FAMILY];
  const groups: [string, MachineRow[]][] = order.map((family) => [family, shown.filter((row) => inGroup(row) === family)]);
  const empty = onlyConnected && !query.trim() ? "Нет подключённого оборудования — снимите «Только подключённые» или установите агент ERP на ПК станка или прибора"
    : "Нет оборудования — установите агент ERP или отметьте станок в БД.ОБ галочкой «Показывать в «Оборудовании»»";
  return <section className="page machines-page">
    <header className="page-head"><h1>Оборудование</h1></header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!data && !error
      ? <p className="empty">Загрузка…</p>
      : <>
        <div className="machine-toolbar">
          {setupViewer && <div className="seg-tabs" role="tablist" aria-label="Оборудование">
            {VIEWS.map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={view === id} className={view === id ? "on" : ""}
              onClick={() => setView(id)}>{label}</button>)}
          </div>}
          {view !== "setup" && showSites && <div className="seg-tabs sub" role="radiogroup" aria-label="Участок">
            {SITES.map(([id, label]) => <button key={id || "all"} type="button" role="radio" aria-checked={site === id} className={site === id ? "on" : ""}
              onClick={() => setSite(id)}>{label}<span className={`ship-tab-count${siteCount(id) ? "" : " is-zero"}`}>{siteCount(id)}</span></button>)}
          </div>}
          {view !== "setup" && <label className="machine-only-connected">
            <input type="checkbox" checked={onlyConnected} onChange={(event) => setOnlyConnected(event.target.checked)} />
            Только подключённые<span className="ship-tab-count">{connectedCount}</span></label>}
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
    {/* Карточка по признакам строки, без пересечений: живая ОБ.019 → станок маркировки → анализатор цепей → принтер или МФУ склада → паспорт. */}
    {opened?.card && opened.equipment_id
      ? <Ob019Card equipmentId={opened.equipment_id} title={[titleOf(opened), opened.article].filter(Boolean).join(" · ")}
        close={() => setOpen(null)}
        actions={<>
          <button type="button" className="secondary small machine-manual" onClick={() => setSetupOf(opened)}>
            Наладка{opened.pending_setup ? " · ждёт" : ""}</button>
          <button type="button" className="secondary small" onClick={() => void openManual(opened)}>Ручная работа</button>
        </>} />
      : opened?.marking && opened.equipment_id
        ? <MarkingMachineCard row={opened} canWrite={canWrite} close={() => setOpen(null)} />
        : open === "bench" || opened?.vna
          ? <AnalyserCard row={opened} benchNumber={benchNumber} canQuality={canQuality} close={() => setOpen(null)} />
          : opened?.station ? <WarehouseDeviceCard row={opened} station={opened.station} close={() => setOpen(null)} />
          : opened && hasWorkCard(opened)
            ? <MachineWorkCard row={opened} canWrite={canWrite} close={() => setOpen(null)} openSetup={() => setSetupOf(opened)} />
            : opened && <MachineInfo row={opened} close={() => setOpen(null)} canWrite={canWrite} saved={() => undefined}
            manual={opened.kind === "machine" ? () => void openManual(opened) : undefined} />}
    {setupOf && <SetupWindow row={setupOf} canWrite={canWrite} setupViewer={setupViewer} close={() => setSetupOf(null)} saved={() => undefined} />}
    {manual && <ManualJobWindow job={manual.job} equipmentId={manual.row.equipment_id} inventory={manual.row.inventory}
      close={() => setManual(null)} />}
  </section>;
}
