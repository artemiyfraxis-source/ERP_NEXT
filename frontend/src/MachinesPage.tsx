import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnalyserCard, drivesBench } from "./AnalyserCard";
import { failureText, productionApi } from "./api";
import { EMPTY, formatMoment } from "./format";
import { equipmentSetupApi, type SetupViewer } from "./equipmentSetupApi";
import { EquipmentSetupPage } from "./EquipmentSetupPage";
import { machinesApi, usePolled, type MachineRow, type MachineTaskContext, type Tone } from "./machinesApi";
import { MachineSetupPart } from "./MachineSetup";
import { MachineWorkCard } from "./MachineWorkCard";
import { WorkJournal } from "./ManualJobsPage";
import { MarkingMachineCard } from "./MarkingMachineCard";
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
 *  На плитке — артикул, участок, «Ждёт наладки» по открытому заданию; в карточке станка — «Наладка» и журнал работ.
 *  Анализатор цепей открывает свою карточку (`AnalyserCard`): Planar — рабочее место ВАЦ (бывшая вкладка ОТК, владелец
 *  20.11.2026; старая ссылка `?section=quality&tab=vna` ведёт сюда, `&tab=vna` открывает эту карточку), Arinst — замер файлом. */

export type View = "setup" | "equipment" | "journal";
type Site = "" | "ЦАР" | "ЖУК";
const VIEWS: [View, string][] = [["setup", "Запросы на наладку"], ["equipment", "Всё оборудование"], ["journal", "Журнал работ"]];
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

/** Операции резки из БД.ОБ → что станок умеет сверх резки (владелец 23.11.2026: «Резка + разделка, маркировка» вместо
 *  «Резка, Резка с разделкой, Резка с разделкой+ маркир…»). Только известные названия; прочие идут как есть. */
const CUT_EXTRAS: Record<string, string[]> = {
  "резка": [],
  "резка с разделкой": ["разделка"],
  "резка с разделкой + маркировка": ["разделка", "маркировка"],
  "резка с маркировкой": ["маркировка"],
  // ОБ.019 режет и трубки — это та же резка; станок, который режет только трубки и оплётки, так и называется.
  "резка линейных материалов": [],
};
const LINEAR = "резка линейных материалов";
const operationKey = (name: string) => name.toLocaleLowerCase("ru").replace(/ё/g, "е").replace(/\s*\+\s*/g, " + ").replace(/\s+/g, " ").trim();

/** Короткая строка возможностей плитки: резка одним «Резка + …», остальные операции без повторов, в исходном порядке. */
export function shortOperations(operations: string[]): string {
  const parts: string[] = [], extras: string[] = [], seen = new Set<string>();
  let cut = -1, cutName = "";
  for (const name of operations) {
    const key = operationKey(name);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    const known = CUT_EXTRAS[key];
    if (!known) { parts.push(name.trim()); continue; }
    if (cut < 0) { cut = parts.length; parts.push(""); }
    cutName = cutName && cutName !== name.trim() ? "Резка" : name.trim();
    for (const extra of known) if (!extras.includes(extra)) extras.push(extra);
  }
  if (cut >= 0) parts[cut] = extras.length ? `Резка + ${extras.join(", ")}` : operationKey(cutName) === LINEAR ? cutName : "Резка";
  return parts.join(", ");
}

function MachineTile({ row, open }: { row: MachineRow; open: (row: MachineRow) => void }) {
  const absent = row.link.code === "not_installed" || row.link.code === "not_connected";
  const seen = row.pc && row.last_seen ? formatMoment(row.last_seen) : "";
  const place = row.pc ? [row.pc, row.ip].filter(Boolean).join(" · ") : "ПК не известен";
  const model = [row.article || (row.kind !== "machine" ? row.device : ""), row.site].filter(Boolean).join(" · ");
  const operations = (row.operations ?? []).join(", ");
  const short = shortOperations(row.operations ?? []);
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
    <span className="machine-tile-ops" title={operations}>{short}</span>
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
function MachineInfo({ row, close, canWrite, saved, journal }: {
  row: MachineRow; close: () => void; canWrite: boolean; saved: () => void; journal?: () => void;
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
        {journal && <button type="button" className="secondary small" onClick={journal}>Журнал</button>}
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
  const [journalOf, setJournalOf] = useState<MachineRow | null>(null);
  const [route, setRoute] = useState(() => window.location.search);
  const params = useMemo(() => new URLSearchParams(route), [route]);
  const equipment = params.get("equipment");
  const hasSource = ["task", "operation", "etk_position", "load"].some((key) => params.has(key));
  const [resolved, setResolved] = useState<{ route: string; context: MachineTaskContext | null; error: string } | null>(null);
  useEffect(() => {
    const changed = () => setRoute(window.location.search);
    window.addEventListener("popstate", changed);
    return () => window.removeEventListener("popstate", changed);
  }, []);
  useEffect(() => {
    setJournalOf(null); setSetupOf(null);
  }, [route]);
  useEffect(() => {
    let alive = true;
    const taskId = params.get("task"), operationId = params.get("operation");
    const position = Number(params.get("etk_position"));
    if (!hasSource) { setResolved({ route, context: null, error: "" }); return; }
    if (!equipment || !taskId || !operationId || !Number.isSafeInteger(position) || position < 1) {
      setResolved({ route, context: null, error: "Ссылка на задание неполная — откройте станок из инструкции заново" });
      return;
    }
    productionApi.operationMachineActions(taskId, operationId).then((answer) => {
      if (!alive) return;
      const matches = answer.actions.filter((action) => action.etk_position === position && action.equipment_id === equipment);
      const action = matches.length === 1 ? matches[0] : null;
      if (answer.task_id !== taskId || answer.operation_id !== operationId || !action?.can_open
        || (action.card_kind !== "ob099" && !action.source)) {
        setResolved({ route, context: null, error: action?.blocker || "Задание или станок изменились — откройте инструкцию заново" });
      } else setResolved({ route, context: { taskId, operationId, action, loadRequested: params.get("load") === "1" }, error: "" });
    }).catch((reason) => {
      if (alive) setResolved({ route, context: null, error: failureText(reason, "Не удалось проверить задание — откройте инструкцию заново") });
    });
    return () => { alive = false; };
  }, [params, route, equipment, hasSource]);
  const incoming = resolved?.route === route ? resolved.context : null;
  const routeError = resolved?.route === route ? resolved.error : "";
  const sourceReady = !hasSource || Boolean(incoming);
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
  const view: View = params.get("view") === "journal" ? "journal" : equipment || wantsBench.current ? "equipment"
    : chosen === "setup" && setupViewer ? "setup" : chosen === "journal" ? "journal" : "equipment";
  const changeView = (next: View) => {
    setView(next);
    setOpen(null);
    const url = new URL(window.location.href);
    for (const key of ["equipment", "task", "operation", "etk_position", "load"]) url.searchParams.delete(key);
    url.searchParams.set("view", next);
    window.history.replaceState(window.history.state, "", url);
    setRoute(url.search);
  };
  const [site, setSite] = useState<Site>("");
  const [query, setQuery] = useState("");
  const rows = useMemo(() => data?.rows ?? [], [data]);
  const openMachine = (row: MachineRow) => {
    const url = new URL(window.location.href);
    for (const key of ["task", "operation", "etk_position", "load"]) url.searchParams.delete(key);
    if (row.equipment_id) url.searchParams.set("equipment", row.equipment_id); else url.searchParams.delete("equipment");
    window.history.replaceState(window.history.state, "", url);
    setRoute(url.search);
    setOpen(row);
  };
  const closeMachine = () => {
    setOpen(null);
    const url = new URL(window.location.href);
    for (const key of ["equipment", "task", "operation", "etk_position", "load"]) url.searchParams.delete(key);
    window.history.replaceState(window.history.state, "", url);
    setRoute(url.search);
  };
  // Ссылка на рабочее место ВАЦ (`&tab=vna`, в т. ч. старая из ОТК): карточка Planar, которым оно управляет.
  useEffect(() => {
    if (!wantsBench.current || !data || benchNumber === null) return;
    wantsBench.current = false;
    const row = rows.find((item) => drivesBench(item, benchNumber)) ?? rows.find((item) => item.vna === "s2vna");
    setOpen(row ?? (benchNumber ? "bench" : null));
  }, [data, benchNumber, rows]);
  // Открытая карточка Planar держит `&tab=vna` в адресе — её можно сохранить закладкой.
  const opened = equipment ? (sourceReady ? rows.find((row) => row.equipment_id === equipment) ?? null : null)
    : hasSource ? null : open === "bench" ? null : open && (rows.find((row) => row.key === open.key) ?? open);
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
  const shown = view !== "equipment" ? [] : found.filter((row) => atSite(row) && (!onlyConnected || connected(row))).sort(byNumber);
  const families = data?.families ?? [];
  // Группа строки — её `family`; группа, которой нет в `families` ответа, встаёт перед «Прочее», а не прячется в нём.
  const inGroup = (row: MachineRow) => row.family || OTHER_FAMILY;
  const order = [...new Set([...families, ...shown.map(inGroup)].filter((family) => family !== OTHER_FAMILY)), OTHER_FAMILY];
  const groups: [string, MachineRow[]][] = order.map((family) => [family, shown.filter((row) => inGroup(row) === family)]);
  const empty = onlyConnected && !query.trim() ? "Нет подключённого оборудования — снимите «Только подключённые» или установите агент ERP на ПК станка или прибора"
    : "Нет оборудования — установите агент ERP или отметьте станок в БД.ОБ галочкой «Показывать в «Оборудовании»»";
  return <section className="page machines-page">
    <header className="page-head"><h1>Оборудование</h1></header>
    {routeError && <div className="error-banner" role="alert">{routeError}</div>}
    {hasSource && resolved?.route !== route && <p className="empty">Проверка задания…</p>}
    {equipment && sourceReady && data && !opened && <p className="error-banner" role="alert">Станок из ссылки не найден — откройте инструкцию заново</p>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!data && !error
      ? <p className="empty">Загрузка…</p>
      : <>
        <div className="machine-toolbar">
          <div className="seg-tabs" role="tablist" aria-label="Оборудование">
            {VIEWS.filter(([id]) => id !== "setup" || setupViewer).map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={view === id} className={view === id ? "on" : ""}
              onClick={() => changeView(id)}>{label}</button>)}
          </div>
          {view === "equipment" && showSites && <div className="seg-tabs sub" role="radiogroup" aria-label="Участок">
            {SITES.map(([id, label]) => <button key={id || "all"} type="button" role="radio" aria-checked={site === id} className={site === id ? "on" : ""}
              onClick={() => setSite(id)}>{label}<span className={`ship-tab-count${siteCount(id) ? "" : " is-zero"}`}>{siteCount(id)}</span></button>)}
          </div>}
          {view === "equipment" && <label className="machine-only-connected">
            <input type="checkbox" checked={onlyConnected} onChange={(event) => setOnlyConnected(event.target.checked)} />
            Только подключённые<span className="ship-tab-count">{connectedCount}</span></label>}
          {view === "equipment" && <input className="search" type="search" name="equipment-search" autoComplete="off" spellCheck={false} aria-label="Поиск оборудования" placeholder="№, название, артикул, ПК или IP"
            value={query} onChange={(event) => setQuery(event.target.value)} />}
        </div>
        {view === "setup" && <EquipmentSetupPage embedded />}
        {view === "equipment" && !shown.length && (query.trim() ? <p className="empty">Ничего не найдено по «{query.trim()}»</p> : <p className="empty">{empty}</p>)}
        {groups.map(([title, group]) => group.length > 0 && <section key={title} className="machine-group" aria-label={title}>
          <h2>{title} <span className="machine-muted">· {group.length}</span></h2>
          <div className="machine-tiles">{group.map((row) => <MachineTile key={row.key} row={row} open={openMachine} />)}</div>
        </section>)}
      </>}
    {view === "journal" && <WorkJournal />}
    {/* Карточка по признакам строки, без пересечений: живая ОБ.019 → станок маркировки → анализатор цепей → принтер или МФУ склада → паспорт. */}
    {opened?.card && opened.equipment_id
      ? <Ob019Card key={`${opened.equipment_id}:${route}`} incoming={incoming ?? undefined} equipmentId={opened.equipment_id} title={[titleOf(opened), opened.article].filter(Boolean).join(" · ")}
        close={closeMachine} openJournal={() => setJournalOf(opened)} canWrite={canWrite}
        actions={<>
          <button type="button" className="secondary small machine-manual" onClick={() => setSetupOf(opened)}>
            Наладка{opened.pending_setup ? " · ждёт" : ""}</button>
        </>} />
      : opened?.marking && opened.equipment_id
        ? <MarkingMachineCard key={`${opened.equipment_id}:${route}`} incoming={incoming ?? undefined} row={opened} canWrite={canWrite} close={closeMachine} openJournal={() => setJournalOf(opened)} />
        : open === "bench" || opened?.vna
          ? <AnalyserCard row={opened} benchNumber={benchNumber} canQuality={canQuality} close={closeMachine} />
          : opened?.station ? <WarehouseDeviceCard row={opened} station={opened.station} close={closeMachine} />
          : opened && hasWorkCard(opened)
            ? <MachineWorkCard key={`${opened.equipment_id}:${route}`} incoming={incoming ?? undefined} row={opened} canWrite={canWrite} close={closeMachine} openSetup={() => setSetupOf(opened)} openJournal={() => setJournalOf(opened)} />
            : opened && <MachineInfo row={opened} close={closeMachine} canWrite={canWrite} saved={() => undefined} journal={opened.equipment_id ? () => setJournalOf(opened) : undefined} />}
    {setupOf && <SetupWindow row={setupOf} canWrite={canWrite} setupViewer={setupViewer} close={() => setSetupOf(null)} saved={() => undefined} />}
    {journalOf?.equipment_id && <PageWindow label={`Журнал · ${titleOf(journalOf)}`} close={() => setJournalOf(null)} width={1400} dismissable>
      <section className="panel machine-card">
      <header className="machine-card-head"><strong>{titleOf(journalOf)}</strong>
        <button type="button" className="secondary small" onClick={() => setJournalOf(null)}>Закрыть журнал</button></header>
      <WorkJournal equipmentId={journalOf.equipment_id} />
      </section>
    </PageWindow>}
  </section>;
}
