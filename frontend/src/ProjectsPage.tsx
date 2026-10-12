import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ApiError,
  failureText,
  fulfillmentApi,
  harnessApi,
  kitsetsApi,
  requestsApi,
  shippingApi,
  type KitsetAssembly,
  type KitsetDocument,
  type KitsetCard,
  type KitsetComponent,
  type KitsetDeleteResult,
  type KitsetIssueAssembly,
  type KitsetJournalEvent,
  type KitsetLoadResult,
  type KitsetLot,
  type KitsetReserve,
  type KitsetStockLot,
  type KitsetTile,
  type FulfillmentCandidate,
  type Principal,
  type ProjectGoodsStatus,
} from "./api";
import { canSee } from "./navigation";
import { GoodsStateBar, movesHint, piecesOf } from "./FinishedGoodsStates";
import { DataTable, type DataColumn } from "./DataTable";
import { KitTile, tcProgress } from "./ProjectCard";
import { PendingLesson } from "./AssemblyDrafts";
import { EtkSheet, GeneratorPane, useTechcard } from "./ProjectEtk";
import { AutoTextarea } from "./AutoTextarea";
import { formatDate, formatDateTime, formatDay, formatMoment } from "./format";
import { StatusChip } from "./StatusChip";
import { CANDIDATE_LEGEND, candidateTone, matchText } from "./stockCandidates";
// Редактор чертежа жгута — отдельным куском, вкладкой карточки проекта («Чертёж КБn»).
const HarnessPage = lazy(() => import("./HarnessPage").then((module) => ({ default: module.HarnessPage })));
import { useLatestSearch } from "./useLatestSearch";
import { ProjectStatus } from "./ProjectStatus";
import { StageChangeDialog, STAGE_AUTO } from "./StageChangeDialog";
import { ProjectTrack } from "./ProjectTrack";
import { supplyApi, type SupplyLine } from "./supplyApi";

/* «Проекты» — SUPPLYMAP erptechmap (PortalApp.html openKitsetHub / openKitsetCard). Проект заводится из расчёта:
   номер, заказчик, сборки с тиражом и состав каждой. Внутри — состав, снабжение, спецификация, генератор и
   готовые техкарты по сборкам. Правки «на 1 шт», «% брака», наименования и ГРН переживают обновление из расчёта. */

type SortKey = "upd" | "upd_asc" | "status" | "no" | "customer";
const SORTS: Array<[SortKey, string]> = [
  ["upd", "сначала свежие"], ["upd_asc", "сначала старые"], ["status", "по статусу"],
  ["no", "по номеру"], ["customer", "по заказчику"],
];

function stored(key: string, fallback: string) {
  try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
}

function remember(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* private window */ }
}

/* Переключатели «Скрыть …» над списком проектов: чтобы отгруженные и закрытые по техкартам проекты не засоряли
   список. Помнятся у каждого пользователя отдельно; «Скрыть отгруженные» включён, пока его не выключили. */
type HideKey = "shipped" | "tcReady" | "tcChecked" | "blocked";
const HIDES: Array<[HideKey, string, boolean]> = [
  ["shipped", "Скрыть отгруженные", true], ["tcReady", "Скрыть с готовыми ТК", false], ["tcChecked", "Скрыть с проверенными ТК", false],
  ["blocked", "Скрыть с блокерами", false],
];

function hidden(item: KitsetTile, hide: Record<HideKey, boolean>, status: string) {
  const { total, tc, unreviewed } = tcProgress(item);
  // Статус «Отгружено», выбранный в фильтре явно, важнее переключателя.
  if (hide.shipped && item.status === "Отгружено" && status !== "Отгружено") return true;
  if (hide.tcReady && total > 0 && tc === total) return true;             // чип «ТК на все КБ»
  if (hide.tcChecked && total > 0 && tc === total && !unreviewed) return true; // и ни одной «не проверена»
  if (hide.blocked && item.blocker) return true;                           // ХОЛД, СТОП, ОЖИД ИНФ, ОЖИД КОМПЛ, ПАУЗА
  return false;
}

function message(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

function gone(reason: unknown) {
  return reason instanceof ApiError && reason.status === 404
    && !!reason.detail && typeof reason.detail === "object" && (reason.detail as { gone?: boolean }).gone === true;
}

/** File → base64 без префикса data:. */
export function readFileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || "").split(",")[1] || "");
    reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
    reader.readAsDataURL(file);
  });
}

/** «Выдаём вровень» (владелец, 10.10.2026): запас на брак пока не добавляется — как `SCRAP_APPLIED` на сервере. */
export const KIT_SCRAP_APPLIED = false;

/** Mirror of the server `KitComponent.batch`: расход × тираж (× (1 + брак/100), когда запас включён), до сотых;
 *  штучное — затем вверх до целого (половинки штуки не бывает, Василий 04.10), метры до сотых — «На партию» при вводе. */
export function kitBatch(per: string, run: string, scrap: string, unit: string) {
  const num = (value: string) => {
    const parsed = parseFloat(String(value ?? "").replace(/\s/g, "").replace(",", "."));
    return Number.isFinite(parsed) ? parsed : 0;
  };
  const allowance = KIT_SCRAP_APPLIED ? num(scrap) / 100 : 0;
  const batch = Math.round(num(per) * num(run) * (1 + allowance) * 100) / 100;
  return unit === "шт" ? Math.ceil(batch) : batch;
}

/** Ячейка «ГРН склада»: выбранный ГРН и сколько из «на партию» уже в резерве под КБ. Выбран ГРН, а резерва нет
 *  (единицы партии и позиции не сходятся) — «резерв при комплектации». ГРН не выбран — пусто. */
export function lotLabel(lot: KitsetLot | null, reserve: KitsetReserve | null | undefined, batch: number) {
  if (!lot?.grn) return "";
  const grn = `${lot.grn}${lot.package ? ` / ${lot.package}` : ""}`;
  return reserve && reserve.grn === lot.grn
    ? `${grn} · резерв ${reserve.quantity} из ${batch}`
    : `${grn} · резерв при комплектации`;
}

const STALE_LOT_TEXT = "Партия изменилась — обновите список ГРН";

/** «Генератор» карточки проекта (SUPPLYMAP renderGen_ / mountGen_): сверху сборки проекта, ниже генератор выбранной.
    Карточку ТК сборки заводит открытие генератора (kitsetsApi.openTechcard). */
function GenTab({ card, principal, index, pick, done, builtId, onBuiltChange, openDrawing }: {
  card: KitsetCard; openDrawing: (index: string) => void; principal: Principal; index: string; pick: (index: string) => void; done: (index: string) => void;
  builtId: string; onBuiltChange: (draftId: string) => void;
}) {
  const [cardId, setCardId] = useState("");
  const [error, setError] = useState("");
  const { card: techcard, setCard: setTechcard, error: loadError } = useTechcard(cardId);
  const assembly = card.assemblies.find((item) => item.index === index);
  const known = assembly?.techcard?.card_id || "";
  // Одна-две сборки — генератор первой открывается сразу, без лишнего клика (аудит 10.10, ПР-10).
  const first = !index && card.assemblies.length > 0 && card.assemblies.length <= 2 ? card.assemblies[0].index : "";
  useEffect(() => { if (first) pick(first); }, [first]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    let live = true;
    setError("");
    // Карточка ТК уже есть — открываем сразу; нет — «Генератор» заводит её первым открытием.
    setCardId(known);
    if (!index || known) return;
    kitsetsApi.openTechcard(card.project, index)
      .then((result) => { if (live) setCardId(result.card_id); })
      .catch((reason) => { if (live) setError(failureText(reason, "Генератор не открылся")); });
    return () => { live = false; };
  }, [card.project, index, known]);
  return <div className="kit-pane">
    <div className="seg-tabs sub" role="tablist" aria-label="Сборка">{card.assemblies.map((item) =>
      <button key={item.index} role="tab" aria-selected={item.index === index} className={item.index === index ? "on" : ""} onClick={() => pick(item.index)}>{item.index}</button>)}</div>
    {!index && !first && <div className="empty">{card.assemblies.length ? "Выберите сборку — генератор откроется здесь." : "В проекте нет сборок."}</div>}
    {assembly && <p className="kit-meta"><strong>{assembly.index}</strong> {assembly.name} · кол-во {assembly.run} · ТК: {assembly.techcard?.issued ? `готова, ревизия ${assembly.techcard.revision}` : assembly.techcard?.stages ? "черновик" : assembly.techcard ? "не собрана" : "нет"}{assembly.kd.some((document) => document.kind === "harness") && <> · <button className="link-button" onClick={() => openDrawing(assembly.index)}>Чертёж жгута</button></>}</p>}
    {(error || loadError) && <div className="error-banner" role="alert">{error || loadError}</div>}
    {index && !error && !loadError && !techcard && <div className="empty">Читаю состав {index}…</div>}
    {techcard && <GeneratorPane key={techcard.id} card={techcard} principal={principal} onChange={setTechcard} builtId={builtId} onBuiltChange={onBuiltChange}
      onDone={(updated) => { if (updated.status === "Выдана") done(index); }} />}
  </div>;
}

/** «ТК КБn»: электронная ТК сборки (SUPPLYMAP renderTc_). «Пересобрать» ведёт в генератор этой сборки. */
function TcTab({ assembly, principal, rebuild, changed, builtId }: { assembly: KitsetAssembly; principal: Principal; rebuild: () => void; changed: () => void; builtId?: string }) {
  const { card, setCard, error } = useTechcard(assembly.techcard?.card_id || "");
  if (error) return <div className="error-banner" role="alert">{error}</div>;
  if (!card) return <div className="empty">Загрузка техкарты…</div>;
  return <>
    {builtId && <PendingLesson draftId={builtId} />}
    <EtkSheet card={card} principal={principal} onRebuild={rebuild}
    title={<><strong>{assembly.index}</strong> <span>{assembly.name}</span> <span className="muted">кол-во {assembly.run}</span></>}
    onChange={(updated) => { setCard(updated); changed(); }} />
  </>;
}

function openSection(section: string, extra: Record<string, string> = {}) {
  const url = new URL(window.location.href);
  url.searchParams.set("section", section);
  url.searchParams.delete("block");
  for (const [key, value] of Object.entries(extra)) url.searchParams.set(key, value);
  window.history.pushState({}, "", url);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function deleteSummary(res: KitsetDeleteResult) {
  const parts = [`спецификация ${res.deleted}`];
  for (const [name, count] of Object.entries(res.erp || {})) parts.push(`${name.toLowerCase()} ${count}`);
  if (res.cards?.cards) parts.push(`техкарты ${res.cards.cards}`);
  if (res.gp?.deleted) parts.push(`склад ГП ${res.gp.deleted}`);
  const card = `Снесено строк: ${parts.join(", ")}` + (res.gp?.kept ? `. На складе ГП осталось с движением: ${res.gp.kept}` : "")
    + (res.reserve_error ? `. Резерв ГРН вернулся не полностью: ${res.reserve_error}` : "");
  // Журнал проекта и хвосты без движения: итог по-русски собирает сервер («Удалено: 4 записи журнала проекта, …») —
  // тот же текст печатает команда purge_project_tails.
  const tails = [res.tails?.message, res.tails_error].filter(Boolean).join(" ");
  return tails ? `${card}. ${tails}` : card;
}

const DELETE_TEXT = "Проект уйдёт целиком: состав и спецификация, сам проект в реестре ERP, его журнал в плане "
  + "(блокеры, дедлайны, отметки), выданные техкарты с их ревизиями, КД и строки склада готовой продукции. С ним уйдёт всё, по чему ещё не было движения: запросы на "
  + "комплектацию без выдачи (резерв вернётся на склад), производственные задания без отмеченной работы, заявки, "
  + "ещё не взятые в закупку. Восстановить можно повторной загрузкой из расчёта — кроме правок количеств, брака, "
  + "наименований и привязок ГРН: они пропадут. Останется то, по чему движение уже было: выдача со склада, собранный "
  + "комплект, работа в цеху или задание, отданное в ЖУК, заявка в закупке, приход или отгрузка готовой продукции. "
  + "После удаления будет видно, что осталось и почему.";

/** Итог, с которым карточка возвращает к списку (удаление проекта): текст и тон баннера. */
type Flash = { text: string; tone: "ok" | "warn" };

export function ProjectsPage({ principal }: { principal: Principal }) {
  const canEdit = principal.permissions.projects === "edit";
  const [open, setOpen] = useState(() => new URLSearchParams(window.location.search).get("project") || "");
  const [flash, setFlash] = useState<Flash | null>(null);
  const canRequest = principal.permissions.newrequest === "edit";
  if (open) return <KitsetCardView project={open} principal={principal} canEdit={canEdit} canRequest={canRequest}
    back={(result) => { setFlash(result ?? null); setOpen(""); }} />;
  return <KitsetHub canEdit={canEdit} canRequest={canRequest} user={principal.user.id} flash={flash}
    openProject={(project) => { setFlash(null); setOpen(project); }} />;
}

function KitsetHub({ canEdit, canRequest, user, flash, openProject }: {
  canEdit: boolean; canRequest: boolean; user: string; flash: Flash | null; openProject: (project: string) => void;
}) {
  const [all, setAll] = useState<KitsetTile[] | null>(null);
  const [statuses, setStatuses] = useState<string[]>([]);
  const [lists, setLists] = useState<{ stages?: string[]; terminals?: string[]; blockers?: string[] }>({});
  const [sort, setSort] = useState<SortKey>(() => stored("kitSort", "upd") as SortKey);
  const [filter, setFilter] = useState(() => stored("kitFilt", ""));
  const [hide, setHide] = useState(() => Object.fromEntries(HIDES.map(([key, , on]) =>
    [key, stored(`kitHide.${key}.${user}`, on ? "1" : "0") === "1"])) as Record<HideKey, boolean>);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState(flash?.tone === "ok" ? flash.text : "");
  const [warning, setWarning] = useState(flash?.tone === "warn" ? flash.text : "");
  const [creating, setCreating] = useState(false);

  async function load() {
    try {
      const result = await kitsetsApi.list(sort);
      setAll(result.items); setStatuses(result.statuses);
      setLists({ stages: result.stages, terminals: result.terminals, blockers: result.blockers });
    } catch (reason) { setError(message(reason, "Не удалось загрузить проекты")); }
  }
  useEffect(() => { void load(); }, [sort]);

  // Запомненный фильтр, под который больше нет ни одного проекта, спрятал бы весь список.
  const activeFilter = all && filter && !all.some((item) => item.status === filter) ? "" : filter;
  const list = (all || []).filter((item) => (!activeFilter || item.status === activeFilter) && !hidden(item, hide, activeFilter));

  return <section className="page projects-page">
    <header className="page-head"><h1>Проекты</h1>{all && <span className="page-count">{all.length}</span>}
      {canEdit && <div className="page-actions"><button className="primary" onClick={() => setCreating(true)}>Создать проект из расчёта</button></div>}</header>
    {creating && <CreateFromCalc canRequest={canRequest} close={() => setCreating(false)} done={(project, note, bad) => {
      setCreating(false);
      if (bad) setError(note); else setNotice(note);
      openProject(project);
    }} />}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {warning && <div className="warning-banner" role="alert">{warning}</div>}
    {all === null && !error && <div className="empty">Загрузка…</div>}
    {all && !all.length && <div className="empty">Проектов пока нет — создайте первый из расчёта.</div>}
    {all && all.length > 0 && <>
      <div className="kit-filters">
        <label>Сортировка <select aria-label="Сортировка" value={sort} onChange={(event) => { setSort(event.target.value as SortKey); remember("kitSort", event.target.value); }}>
          {SORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <label>Статус <select aria-label="Статус" value={activeFilter} onChange={(event) => { setFilter(event.target.value); remember("kitFilt", event.target.value); }}>
          <option value="">все</option>
          {statuses.map((status) => {
            const count = all.filter((item) => item.status === status).length;
            return count ? <option key={status} value={status}>{`${status} · ${count}`}</option> : null;
          })}
        </select></label>
        {HIDES.map(([key, label]) => <label key={key}><input type="checkbox" checked={hide[key]} onChange={(event) => {
          setHide({ ...hide, [key]: event.target.checked }); remember(`kitHide.${key}.${user}`, event.target.checked ? "1" : "0");
        }} />{label}</label>)}
        <span className="muted">показано {list.length} из {all.length}</span>
      </div>
      {list.length ? <div className="tiles kit-tiles">
        {list.map((item) => <KitTile key={item.project} item={item} statuses={statuses} canEdit={canEdit} open={() => openProject(item.project)}
          lists={lists} statusChanged={() => void load()} />)}
      </div> : <div className="empty">Нет проектов под выбранные фильтры.</div>}
    </>}
  </section>;
}

function dateAfter(days: number) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

/** Итог «Сразу создать заявки на закупку» для строки после заведения проекта. */
function requestsNote(res: KitsetLoadResult): string {
  const made = res.requests;
  if (!made) return "";
  if (made.existing) return `заявки по проекту уже есть (${made.existing}) — новые не созданы`;
  return `заявок на закупку ${made.created}` + (made.skipped.length ? `, без количества пропущены: ${made.skipped.join(", ")}` : "");
}

function CreateFromCalc({ canRequest, close, done }: {
  canRequest: boolean; close: () => void; done: (project: string, note: string, bad: boolean) => void;
}) {
  const [url, setUrl] = useState("");
  const [form, setForm] = useState({ number: "", deadline: "", shipping_point: "", contact: "", phone: "" });
  const [withRequests, setWithRequests] = useState(false);
  const [purchase, setPurchase] = useState({ purpose: "", delivery_point: "", procurement_deadline: dateAfter(7) });
  const [lists, setLists] = useState<{ purposes: string[]; points: string[] }>({ purposes: [], points: [] });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Справочники пачки — те же, что у «Оформить заявку → Из расчёта»; назначение по умолчанию «Обеспечение производства».
  useEffect(() => {
    if (!withRequests || lists.purposes.length) return;
    requestsApi.schema().then((schema) => {
      setLists({ purposes: schema.purposes, points: schema.delivery_points });
      setPurchase((current) => ({
        ...current,
        purpose: current.purpose || schema.purposes.find((item) => item === "Обеспечение производства") || schema.purposes[0] || "",
        delivery_point: current.delivery_point || schema.delivery_points[0] || "",
      }));
    }).catch((reason) => setError(message(reason, "Справочники заявок не загружены")));
  }, [withRequests]);

  async function submit() {
    if (busy) return;
    if (!url.trim()) { setError("Вставьте ссылку на расчёт"); return; }
    if (withRequests && !(purchase.purpose && purchase.delivery_point && purchase.procurement_deadline)) {
      setError("Для заявок нужны назначение, точка отгрузки и дедлайн закупки"); return;
    }
    setBusy(true); setError("");
    try {
      const { res } = await kitsetsApi.load({
        url: url.trim(), number: form.number,
        deadline: form.deadline || null,
        ship: { shipping_point: form.shipping_point, contact: form.contact, phone: form.phone },
        requests: withRequests ? purchase : null,
      });
      const problems = [
        res.erp_error && `реестр ERP: ${res.erp_error}`,
        res.deadline_error && `дедлайн не записан: ${res.deadline_error}`,
        res.ship_to_error && `адресат не сохранён: ${res.ship_to_error}`,
        res.ship_error && `очередь отгрузки: ${res.ship_error}`,
        res.requests_error && `заявки не созданы: ${res.requests_error}`,
        res.reserve_error && `резерв ГРН: ${res.reserve_error}`,
      ].filter(Boolean);
      const made = requestsNote(res);
      done(res.project, `Проект «${res.project}»: сборок ${res.assemblies}, позиций ${res.lines}` + (made ? `, ${made}` : "")
        + (problems.length ? ` · ${problems.join(" · ")}` : ""), problems.length > 0);
    } catch (reason) {
      setError(message(reason, "Проект не заведён"));
    } finally { setBusy(false); }
  }

  const title = "Создать проект из расчёта";
  return <div className="panel kit-modal" role="dialog" aria-label={title}>
    <h3>{title}</h3>
    <p className="page-sub">Номер, заказчик, сборки и состав читаются из листов ЗКЗ и СПЯ расчёта.</p>
    <div className="form-grid">
      <label className="wide">Ссылка на расчёт<input aria-label="Ссылка на расчёт" value={url} placeholder="https://docs.google.com/spreadsheets/…" onChange={(event) => setUrl(event.target.value)} /></label>
      <label>№ проекта<input value={form.number} placeholder="пусто — возьмём из расчёта" onChange={(event) => setForm({ ...form, number: event.target.value })} /></label>
      <label>Дедлайн<input type="date" value={form.deadline} onChange={(event) => setForm({ ...form, deadline: event.target.value })} /></label>
      <label>Контактное лицо<input value={form.contact} placeholder="кто принимает" onChange={(event) => setForm({ ...form, contact: event.target.value })} /></label>
      <label>Телефон<input value={form.phone} placeholder="для связи" onChange={(event) => setForm({ ...form, phone: event.target.value })} /></label>
      <label className="wide">Адрес отгрузки<AutoTextarea value={form.shipping_point} maxRows={4} placeholder="можно позже — в окне «Отгрузить»" onChange={(value) => setForm({ ...form, shipping_point: value })} /></label>
      {canRequest && <label className="wide check-inline"><input type="checkbox" checked={withRequests} onChange={(event) => setWithRequests(event.target.checked)} />Сразу создать заявки на закупку по листу ЗКП</label>}
      {withRequests && <>
        <label>Назначение заявок<select aria-label="Назначение заявок" value={purchase.purpose} onChange={(event) => setPurchase({ ...purchase, purpose: event.target.value })}>
          {lists.purposes.map((item) => <option key={item}>{item}</option>)}
        </select></label>
        <label>Дедлайн закупки<input type="date" aria-label="Дедлайн закупки" value={purchase.procurement_deadline} onChange={(event) => setPurchase({ ...purchase, procurement_deadline: event.target.value })} /></label>
        <label className="wide half">Точка отгрузки заявок<select aria-label="Точка отгрузки заявок" value={purchase.delivery_point} onChange={(event) => setPurchase({ ...purchase, delivery_point: event.target.value })}>
          {lists.points.map((item) => <option key={item}>{item}</option>)}
        </select></label>
      </>}
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="page-actions">
      <button className="primary" disabled={busy} onClick={() => void submit()}>{busy ? "Читаю расчёт…" : withRequests ? "Создать проект и заявки" : "Создать"}</button>
      <button className="secondary" onClick={close}>Отмена</button>
    </div>
  </div>;
}

type Tab = "sostav" | "supply" | "spec" | "gen" | "journal" | "track" | `tc:${string}` | `hn:${string}`;

function KitsetCardView({ project, principal, canEdit, canRequest, back }: {
  project: string; principal: Principal; canEdit: boolean; canRequest: boolean; back: (result?: Flash) => void;
}) {
  const [genIndex, setGenIndex] = useState("");
  // Собрано по черновику с чертежа, урок не записан: по индексу сборки, переживает переход «Генератор» → «ТК КБn».
  const [builtDrafts, setBuiltDrafts] = useState<Record<string, string>>({});
  const [card, setCard] = useState<KitsetCard | null>(null);
  const [missing, setMissing] = useState(false);
  const [tab, setTab] = useState<Tab>("sostav");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [warning, setWarning] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [deleting, setDeleting] = useState(false);

  /* kitRefresh: перечитать расчёт по сохранённой ссылке. Номер карточки передаётся явно: в ячейке расчёта он бывает
     короче («720К.»), и обновление завело бы рядом вторую карточку. */
  async function refresh() {
    if (!card || refreshing) return;
    setRefreshing(true); setError(""); setWarning("");
    try {
      const { res } = await kitsetsApi.load({ url: card.calc_source, number: card.project });
      if (res.reserve_error) setWarning(`Резерв ГРН пересчитан не полностью: ${res.reserve_error}`);
      await load();
    } catch (reason) { setError(message(reason, "Расчёт не прочитан")); }
    finally { setRefreshing(false); }
  }

  async function load() {
    try { setCard(await kitsetsApi.get(project)); setMissing(false); }
    catch (reason) {
      // 404 «gone»: проекта с таким номером в ERP нет — удалён или номер в ссылке не тот. Без автоперехода: человек
      // читает, что случилось, и сам идёт к списку (аудит 10.10).
      if (gone(reason)) { setCard(null); setMissing(true); }
      else setError(message(reason, "Не удалось открыть проект"));
    }
  }
  useEffect(() => { void load(); }, [project]);

  /** Удаление после подтверждения в окне: итог уходит баннером в список проектов. */
  async function remove() {
    try {
      const result = await kitsetsApi.remove(project);
      back({ text: `Проект «${project}» удалён. ${deleteSummary(result.res)}`, tone: result.res.reserve_error ? "warn" : "ok" });
    } catch (reason) {
      if (!gone(reason)) throw reason;
      back({ text: `Проекта «${project}» в ERP уже нет — список обновлён.`, tone: "ok" });
    }
  }

  function openGen(index: string) { setGenIndex(index); setTab("gen"); }

  const toList = <button className="secondary small" onClick={() => back()}>← Проекты</button>;
  if (missing) return <section className="page projects-page">
    <header className="page-head">{toList}<h1>{project}</h1></header>
    <div className="warning-inline kit-missing" role="alert">
      <p>Проекта «{project}» в ERP нет: его удалили или в ссылке другой номер.</p>
      <div className="page-actions">
        <button className="primary small" onClick={() => back()}>К списку проектов</button>
        <button className="secondary small" onClick={() => { setMissing(false); void load(); }}>Проверить ещё раз</button>
      </div>
    </div>
  </section>;
  if (!card) return <section className="page projects-page">
    <header className="page-head">{toList}<h1>{project}</h1></header>
    {error ? <div className="error-banner" role="alert">{error}</div> : <div className="empty">Загрузка…</div>}
  </section>;

  const tcTabs = card.assemblies.filter((assembly) => assembly.techcard && assembly.techcard.stages > 0);
  const tabs: Array<[Tab, string]> = [
    ["sostav", "Состав"], ["supply", "Снабжение"], ["spec", "Спецификация"], ["gen", "Генератор"],
    ...tcTabs.map((assembly) => [`tc:${assembly.index}` as Tab, `ТК ${assembly.index}`] as [Tab, string]),
    // «Чертёж КБn» — редактор жгута в карточке: вкладка есть у позиции с привязанным чертежом и пока она открыта.
    ...card.assemblies.filter((assembly) => assembly.kd.some((document) => document.kind === "harness") || tab === `hn:${assembly.index}`)
      .map((assembly) => [`hn:${assembly.index}` as Tab, `Чертёж ${assembly.index}`] as [Tab, string]),
    ["journal", `Журнал${card.journal?.length ? ` · ${card.journal.length}` : ""}`],
    ["track", "Трек"],
  ];
  const blockerInfo = card.blocker_info;

  /* Шапка и вкладки — как карточка проекта SUPPLYMAP (kitCardRender_): строка «заказчик · расчёт · обновлено»,
     небольшие кнопки действий и сегментные вкладки Состав / Снабжение / Спецификация / Генератор / ТК КБn. */
  return <section className="page projects-page kit-card">
    <header className="page-head kit-head">{toList}<h1>{card.project}</h1>
      <ProjectStatus project={card.project} status={card.status} blocker={card.blocker} canEdit={canEdit}
        stages={card.stages} terminals={card.terminals} blockers={card.blockers} onChanged={setCard} onError={setError} />
      {/* Рабочие действия — в «Ещё» рядом с этапом (владелец 12.10: «много кнопок рудиментов»); «Отгрузить» убрана:
          отгрузка идёт через приёмку кладовщика на экране «Отгрузка». Без прав на правку и заявку меню нет. */}
      {(canEdit || (canRequest && card.calc_source)) && <CardMoreMenu project={card.project}
        refreshing={refreshing} onRefresh={canEdit ? () => void refresh() : undefined}
        onRequest={canRequest && card.calc_source ? () => openSection("newrequest", { calc_url: card.calc_source, calc_project: card.project }) : undefined}
        onDelete={canEdit ? () => setDeleting(true) : undefined} />}
    </header>
    <p className="kit-meta">{card.customer || "—"}{card.calc_source && <> · <a className="kit-calc-link" href={card.calc_source} target="_blank" rel="noopener">расчёт</a></>}{card.deadline && <> · дедлайн {formatDay(card.deadline)}</>} · обновлено {formatDateTime(card.updated_at)} {card.updated_by}</p>
    {blockerInfo && <p className="kit-blocker"><StatusChip status={blockerInfo.type} /> <span>с {formatDay(blockerInfo.since)} · {blockerInfo.text} — {blockerInfo.who}</span></p>}
    {note && <div className="kit-actions"><span className="muted">{note}</span></div>}
    {deleting && <DeleteProjectDialog project={card.project} remove={remove} onClose={() => setDeleting(false)} />}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {warning && <div className="warning-banner" role="alert">{warning}</div>}
    <div className="seg-tabs" role="tablist">
      {tabs.map(([key, label]) => <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "on" : ""} onClick={() => setTab(key)}>{label}</button>)}
    </div>
    {tab === "sostav" && <SostavTab card={card} canEdit={canEdit} goodsVisible={canSee(principal, "shipping") || canSee(principal, "shipping_goods")} reload={load} setNote={setNote} setError={setError} openGen={openGen} openTc={(index) => setTab(`tc:${index}`)} openDrawing={(index) => setTab(`hn:${index}`)} />}
    {tab === "supply" && <SupplyTab project={card.project} canOrder={canEdit && canRequest} deadline={card.deadline}
      canSeeStock={canSee(principal, "warehouse") || canSee(principal, "fulfillment", "edit")} />}
    {tab === "spec" && <SpecTab card={card} canEdit={canEdit} setNote={setNote} setWarning={setWarning} reload={load} />}
    {tab === "gen" && <GenTab card={card} principal={principal} openDrawing={(index) => setTab(`hn:${index}`)} index={genIndex} pick={setGenIndex} builtId={builtDrafts[genIndex] || ""}
      onBuiltChange={(id) => setBuiltDrafts((current) => ({ ...current, [genIndex]: id }))}
      done={(index) => { void load(); setTab(`tc:${index}`); setNote(`Техкарта ${index} готова — открыта вкладка «ТК ${index}».`); }} />}
    {tab === "journal" && <JournalTab card={card} canEdit={canEdit} changed={setCard} />}
    {tab === "track" && <ProjectTrack project={card.project} />}
    {tab.startsWith("hn:") && <div className="kit-pane kit-pane-harness">
      <Suspense fallback={<div className="empty">Загрузка редактора…</div>}>
        <HarnessPage key={tab} project={card.project} index={tab.slice(3)} canEdit={canEdit}
          onBack={() => { setTab("sostav"); void load(); }} />
      </Suspense>
    </div>}
    {tab.startsWith("tc:") && (() => {
      const assembly = card.assemblies.find((item) => `tc:${item.index}` === tab);
      if (!assembly?.techcard) return <div className="empty">Сборка не найдена.</div>;
      return <div className="kit-pane"><TcTab key={assembly.techcard.card_id} assembly={assembly} principal={principal} rebuild={() => openGen(assembly.index)} changed={() => void load()} builtId={builtDrafts[assembly.index]} /></div>;
    })()}
  </section>;
}

/** «Ещё ▾» карточки проекта: «Обновить из расчёта», «Оформить заявку из расчёта» и — последним, красным — «Удалить
 *  проект…». Заявка — отдельный шаг, как в SUPPLYMAP: «Оформить заявку → Из расчёта» открывается с этой ссылкой и
 *  номером, оператор проверяет строки. Пункт показан только если действие доступно. */
function CardMoreMenu({ project, refreshing, onRefresh, onRequest, onDelete }: {
  project: string; refreshing: boolean; onRefresh?: () => void; onRequest?: () => void; onDelete?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => { if (root.current && !root.current.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [open]);
  return <div className="more-menu kit-more" ref={root} onKeyDown={(event) => {
    if (event.key !== "Escape" || !open) return;
    setOpen(false);
    root.current?.querySelector<HTMLButtonElement>(".more-menu-trigger")?.focus();
  }}>
    <button type="button" className="secondary small more-menu-trigger" aria-haspopup="menu" aria-expanded={open}
      aria-label={`Ещё действия с проектом ${project}`} onClick={() => setOpen((value) => !value)}>Ещё ▾</button>
    {open && <div className="more-menu-list" role="menu">
      {onRefresh && <button type="button" role="menuitem" autoFocus disabled={refreshing} onClick={() => { setOpen(false); onRefresh(); }}>{refreshing ? "Читаю расчёт…" : "Обновить из расчёта"}</button>}
      {onRequest && <button type="button" role="menuitem" autoFocus={!onRefresh} onClick={() => { setOpen(false); onRequest(); }}>Оформить заявку из расчёта</button>}
      {onDelete && <button type="button" role="menuitem" className="kit-more-danger" autoFocus={!onRefresh && !onRequest} onClick={() => { setOpen(false); onDelete(); }}>Удалить проект…</button>}
    </div>}
  </div>;
}

/** Подтверждение удаления проекта: что уйдёт и что останется (DELETE_TEXT), красная кнопка с номером проекта; фокус —
 *  на «Отмена», Enter по ошибке ничего не удаляет. */
function DeleteProjectDialog({ project, remove, onClose }: { project: string; remove: () => Promise<void>; onClose: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit() {
    if (busy) return;
    setBusy(true); setError("");
    try { await remove(); }
    catch (reason) { setError(message(reason, "Не удалось удалить проект")); setBusy(false); }
  }
  return createPortal(<div className="dialog-overlay" onKeyDown={(event) => { if (event.key === "Escape" && !busy) { event.stopPropagation(); onClose(); } }}
    onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <div className="move-dialog panel kit-delete-dialog" role="dialog" aria-modal="true" aria-label={`Удалить проект ${project}`}>
      <h2>Удалить проект {project}?</h2>
      <p className="page-sub">{DELETE_TEXT}</p>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="move-actions page-actions">
        <button type="button" className="danger" disabled={busy} onClick={() => void submit()}>{busy ? "Удаляю…" : `Удалить проект ${project}`}</button>
        <button type="button" className="secondary" autoFocus disabled={busy} onClick={onClose}>Отмена</button>
      </div>
    </div>
  </div>, document.body);
}

/** Запись смены этапа: сервер уже пишет «В работе → Отгружено · пропущены: КБ на складе · причина: …». Старая запись без
 *  «→» (этап поставили до истории) собирается из значения и причины. */
export function journalText(event: KitsetJournalEvent) {
  if (event.kind !== "stage" || event.text.includes("→")) return event.text;
  const parts = [`${event.from_value ? `${event.from_value} → ` : ""}${event.value === "авто" ? "Авто" : event.value}`];
  const reason = event.reason || event.text;
  if (reason) parts.push(`причина: ${reason}`);
  return parts.join(" · ");
}

/** Журнал проекта в карточке — то, что раньше было только в нижней панели «Производственного плана»:
 *  комментарий, дедлайн и история (блокеры, этапы, проверка ТК, статус). Блокер и статус — меню статуса в шапке. */
function JournalTab({ card, canEdit, changed }: { card: KitsetCard; canEdit: boolean; changed: (card: KitsetCard) => void }) {
  const [kind, setKind] = useState<"note" | "deadline">("note");
  const [text, setText] = useState("");
  const [deadline, setDeadline] = useState(card.deadline || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const valid = kind === "deadline" ? Boolean(deadline) : Boolean(text.trim());

  async function submit() {
    if (busy || !valid) return;
    setBusy(true); setError("");
    try {
      const result = await kitsetsApi.journal(card.project, { kind, text, deadline: kind === "deadline" ? deadline : null });
      setText(""); changed(result.card);
    } catch (reason) { setError(message(reason, "Запись не добавлена")); }
    finally { setBusy(false); }
  }

  const events = card.journal || [];
  return <div className="kit-pane kit-journal">
    {/* Одна строка: [вид][дедлайн][текст][Записать] (аудит 10.10, ПР-5); Enter в тексте записывает. */}
    {canEdit && <form className="kit-journal-form" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <select aria-label="Вид записи" value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}>
        <option value="note">Комментарий</option><option value="deadline">Дедлайн</option>
      </select>
      {kind === "deadline" && <input type="date" aria-label="Дедлайн" value={deadline} onChange={(event) => setDeadline(event.target.value)} />}
      <AutoTextarea aria-label="Текст записи" maxRows={4} placeholder={kind === "deadline" ? "почему сдвинули (необязательно)" : "что происходит с проектом"} value={text} onChange={setText}
        onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(); } }} />
      <button type="submit" className="primary small" disabled={busy || !valid}>Записать</button>
    </form>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    <ul className="kit-journal-list">
      {events.map((event) => <li key={event.id}>
        <time>{formatMoment(event.occurred_at)}</time>
        <b>{event.kind}{event.blocker ? ` · ${event.blocker}` : ""}{event.item_index ? ` · ${event.item_index}` : ""}{event.kind === "stage" ? "" : event.value ? ` · ${event.value}` : ""}</b>
        <span>{journalText(event)}</span><small>{event.who}</small>
      </li>)}
      {!events.length && <li className="empty">Записей пока нет.</li>}
    </ul>
  </div>;
}

const REVIEW_TONE: Record<string, string> = { "проверена": "ok", "с погрешностями": "danger", "не проверена": "neutral" };

function SostavTab({ card, canEdit, goodsVisible = false, reload, setNote, setError, openGen, openTc, openDrawing }: {
  card: KitsetCard; canEdit: boolean; goodsVisible?: boolean; reload: () => Promise<void>; setNote: (text: string) => void;
  setError: (text: string) => void; openGen: (index: string) => void; openTc: (index: string) => void; openDrawing: (index: string) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [kdIndex, setKdIndex] = useState("");
  const [stagePick, setStagePick] = useState<{ assembly: KitsetAssembly; value: string } | null>(null);
  // Сквозной статус готовой продукции по КБ (владелец 12.10): В производстве → Принято складом → Упаковано → Отгружено.
  const [goods, setGoods] = useState<{ status: ProjectGoodsStatus | null; error: string }>({ status: null, error: "" });
  useEffect(() => {
    if (!goodsVisible) return;
    let live = true;
    shippingApi.projectGoods(card.project)
      .then((status) => { if (live) setGoods({ status, error: "" }); })
      .catch((reason) => { if (live) setGoods({ status: null, error: failureText(reason, "Статус готовой продукции не загружен") }); });
    return () => { live = false; };
  }, [goodsVisible, card.project]);
  const goodsCell = (assembly: KitsetAssembly) => {
    if (goods.error) return <span className="muted" title={goods.error}>не загружено</span>;
    if (!goods.status) return <span className="muted">…</span>;
    const wanted = assembly.index.trim().toUpperCase();
    const kb = (goods.status.kbs ?? []).find((item) => item.item_index.trim().toUpperCase() === wanted);
    if (!kb) return <span className="muted">—</span>;
    const moves = (goods.status.moves ?? []).filter((move) => move.item_index.trim().toUpperCase() === wanted);
    return <GoodsStateBar pieces={piecesOf(kb.pieces)} unit={kb.unit} title={movesHint(moves)} />;
  };

  async function applyKbStage(assembly: KitsetAssembly, value: string, reason: string, previewToken: string) {
    const result = await kitsetsApi.mark(card.project, { kb: assembly.index, kind: "stage", value, text: "", reason, preview_token: previewToken });
    setStagePick(null);
    setNote(result.msg || `${assembly.index}: этап — ${value === STAGE_AUTO ? "по фактам" : value}`);
    await reload();
  }

  async function markKb(assembly: KitsetAssembly, kind: "stage" | "tcReview", value: string) {
    let text = "";
    if (kind === "tcReview" && value === "с погрешностями") {
      text = window.prompt(`ТК ${assembly.index}: какие погрешности? Агент прочитает этот текст и исправит карту.`) || "";
      if (!text.trim()) return;
    }
    try {
      await kitsetsApi.mark(card.project, { kb: assembly.index, kind, value, text });
      setNote(`${assembly.index}: ${kind === "stage" ? `этап — ${value === "авто" ? "по фактам" : value}` : `ТК ${value}`}`);
      await reload();
    } catch (reason) { setError(message(reason, "Отметка не записана")); }
  }

  async function upload(file: File | undefined) {
    if (!file || !kdIndex) return;
    setNote(`Загружаю ${file.name}…`);
    try {
      const { res } = await kitsetsApi.addDocument(card.project, {
        index: kdIndex, file_name: file.name, content_type: file.type || "application/octet-stream",
        content_b64: await readFileBase64(file),
      });
      setNote(`КД приложена: ${res.name}`);
      await reload();
    } catch (reason) { setError(message(reason, "КД не приложена")); }
  }

  // Привязанный чертёж жгута не удаляется: «Отвязать» оставляет его в КД позиции отвязанным, «Вернуть» привязывает обратно,
  // «Удалить» стирает только отвязанный.
  async function unlinkHarness(index: string) {
    if (!window.confirm(`Отвязать чертёж жгута от ${index}? Он останется в КД отвязанным — вернуть можно кнопкой «Вернуть».`)) return;
    try { await harnessApi.unlink(card.project, index); setNote(`${index}: чертёж жгута отвязан.`); await reload(); }
    catch (reason) { setError(message(reason, "Чертёж не отвязан")); }
  }

  async function restoreHarness(index: string, documentId: string) {
    try { await harnessApi.relink(card.project, index, documentId); setNote(`${index}: чертёж жгута возвращён.`); await reload(); }
    catch (reason) { setError(message(reason, "Чертёж не возвращён")); }
  }

  /** «Удалить» отвязанный чертёж насовсем (вместе с историей версий): только отвязанный, привязанный — сначала «Отвязать». */
  async function removeUnlinked(index: string, document: KitsetDocument) {
    if (!window.confirm(`Удалить отвязанный чертёж жгута (сохранён ${formatDate(document.uploaded_at)})? Вернуть его будет нельзя.`)) return;
    try { await harnessApi.removeUnlinked(card.project, index, document.id); setNote(`${index}: отвязанный чертёж жгута удалён.`); await reload(); }
    catch (reason) { setError(message(reason, "Чертёж не удалён")); }
  }

  async function detach(documentId: string) {
    if (!window.confirm("Отвязать КД? Файл и строка вложения удалятся из ERP.")) return;
    try { await kitsetsApi.removeDocument(card.project, documentId); setNote("Отвязано."); await reload(); }
    catch (reason) { setError(message(reason, "КД не отвязана")); }
  }

  const kdCell = (assembly: KitsetAssembly) => {
    const harness = assembly.kd.find((document) => document.kind === "harness");
    const unlinked = assembly.kd.filter((document) => document.kind === "harness_unlinked");
    return <>
    {harness && <div className="kit-kd">
      <button className="link-button" onClick={() => openDrawing(assembly.index)}>Чертёж жгута</button>
      {canEdit && <button className="link-button kit-kd-action" onClick={() => void unlinkHarness(assembly.index)}>Отвязать</button>}
    </div>}
    {unlinked.map((document) => <div key={document.id} className="kit-kd kit-kd-unlinked">
      <span className="muted">Отвязанный чертёж жгута · сохранён {formatDate(document.uploaded_at)}</span>
      {canEdit && <button className="link-button kit-kd-action" disabled={Boolean(harness)}
        title={harness ? "У позиции уже есть чертёж — сначала отвяжите его" : undefined}
        onClick={() => void restoreHarness(assembly.index, document.id)}>Вернуть</button>}
      {canEdit && <button className="link-button kit-kd-action kit-kd-delete" onClick={() => void removeUnlinked(assembly.index, document)}>Удалить</button>}
    </div>)}
    {assembly.kd.filter((document) => document.kind !== "harness" && document.kind !== "harness_unlinked").map((document) => <div key={document.id} className="kit-kd">
      {document.stored || !document.url
        ? <a href={kitsetsApi.documentUrl(card.project, document.id)} target="_blank" rel="noopener">{document.name.replace(/^\S+\s+—\s+/, "")}</a>
        : <><a href={document.url} target="_blank" rel="noopener">{document.name.replace(/^\S+\s+—\s+/, "")}</a> <span className="muted">на Диске (архив)</span></>}
      {canEdit && <button className="link-button" title="Отвязать" onClick={() => void detach(document.id)}>✕</button>}
    </div>)}
    {canEdit && !harness && <button className="kit-file" onClick={() => openDrawing(assembly.index)}>Создать в редакторе</button>}
    {canEdit && <button className="kit-file" onClick={() => { setKdIndex(assembly.index); fileRef.current?.click(); }}>＋ файл</button>}
  </>;
  };

  /** Этап КБ перескочили ручной сменой («сделали вживую»): метка с перечнем пропущенных. */
  const skippedMark = (assembly: KitsetAssembly) => assembly.stage?.skipped?.length
    ? <span className="stage-skipped-mark" title={`Пропущены: ${assembly.stage.skipped.join(", ")}`}>пропущен</span> : null;

  const stageCell = (assembly: KitsetAssembly) => canEdit
    ? <><select aria-label={`Этап ${assembly.index}`} value={assembly.stage?.manual ? assembly.stage.status : "авто"} onChange={(event) => setStagePick({ assembly, value: event.target.value })}>
      <option value="авто">авто: {assembly.stage?.manual ? "по фактам" : assembly.stage?.status || "—"}</option>
      {card.stages.map((stage) => <option key={stage} value={stage}>{stage}</option>)}
    </select>{skippedMark(assembly)}</>
    : <>{assembly.stage?.status || "—"}{assembly.stage?.manual && <span className="muted"> · вручную</span>}{skippedMark(assembly)}</>;

  const reviewCell = (assembly: KitsetAssembly) => {
    const review = assembly.stage?.review || { value: "не проверена", text: "" };
    return !assembly.techcard?.issued ? <span className="muted">—</span> : <>
      {canEdit
        ? <select aria-label={`Проверка ТК ${assembly.index}`} className={`tone-${REVIEW_TONE[review.value] || "neutral"}`} value={review.value} onChange={(event) => void markKb(assembly, "tcReview", event.target.value)}>
          {card.review_values.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
        : <span className={`tone-${REVIEW_TONE[review.value] || "neutral"}`}>{review.value}</span>}
      {review.text && review.value === "с погрешностями" && <div className="t-sub">{review.text}</div>}
    </>;
  };

  /* SUPPLYMAP renderSostav_: простая таблица без поиска и колонок — сборок в проекте единицы. Клик по строке
     открывает генератор этой сборки; «готова · N эт.» ведёт на вкладку её ТК. */
  return <div className="kit-pane">
    <input ref={fileRef} type="file" hidden data-testid="kd-file" onChange={(event) => { void upload(event.target.files?.[0]); event.target.value = ""; }} />
    {!card.assemblies.length ? <div className="empty">В проекте нет сборок.</div> : <div className="table-wrap kit-sostav"><table className="grid" aria-label="Состав проекта">
      <thead><tr><th>КБ</th><th>Наименование</th><th className="num">Кол-во</th><th className="num">Позиций</th><th>КД</th><th>Техкарта</th><th>Этап</th><th>Проверка ТК</th>{goodsVisible && <th title="Штуки КБ: в производстве · принято складом · упаковано · отгружено">Готовая продукция</th>}</tr></thead>
      <tbody>{card.assemblies.map((assembly) => <tr key={assembly.index} title={`Открыть генератор ${assembly.index}`} onClick={(event) => {
        // Внутри строки свои кликабельные вещи — ссылка на КД, «＋ файл», выпадающие списки.
        if ((event.target as HTMLElement).closest("a, button, input, select, label")) return;
        openGen(assembly.index);
      }}>
        <td><strong>{assembly.index}</strong></td>
        <td className="wrap">{assembly.name}</td>
        <td className="num">{assembly.run}</td>
        <td className="num">{assembly.components.length}</td>
        <td>{kdCell(assembly)}</td>
        <td>{assembly.techcard && assembly.techcard.stages > 0
          ? <button className="link-button" onClick={() => (assembly.techcard!.issued ? openTc : openGen)(assembly.index)}>{assembly.techcard.issued ? "готова" : "черновик"} · {assembly.techcard.stages} эт.</button>
          : <span className="muted">{assembly.techcard ? "не собрана" : "нет"}</span>}</td>
        <td>{stageCell(assembly)}</td>
        <td>{reviewCell(assembly)}</td>
        {goodsVisible && <td className="kit-goods">{goodsCell(assembly)}</td>}
      </tr>)}</tbody>
    </table></div>}
    {stagePick && <StageChangeDialog project={card.project} kb={stagePick.assembly.index}
      current={stagePick.assembly.stage?.status || "—"} value={stagePick.value}
      loadPreview={() => kitsetsApi.stagePreview(card.project, stagePick.value, stagePick.assembly.index)}
      apply={(reason, token) => applyKbStage(stagePick.assembly, stagePick.value, reason, token)} onClose={() => setStagePick(null)} />}
  </div>;
}

/* «Стадия» одним словом (владелец 12.10.2026): счётчики сверху идут в том же порядке и тех же цветах. */
const SUPPLY_STATES: Array<[key: string, label: string, tone: string]> = [
  ["short", "Не хватает", "danger"], ["procurement", "В закупке", "warn"], ["outside", "Куплено вне системы", "neutral"],
  ["stock", "Со склада", "info"], ["kitting", "В комплектации", "ok"], ["received", "Принято на склад", "ok"],
];

type Maybe = { items: FulfillmentCandidate[]; more: number };

/* «Снабжение»: Нужно / Свободно / Без ГРН / В закупке / Не хватает по каждой позиции. Позиции, где не хватает, отмечаются
   галочкой, и одна кнопка «Заказать недостающее» оформляет на них заявки на закупку (ничего не отмечено — на все).
   Тому, кому открыт склад, строки нехватки окрашены «Возможно есть» — как в комплектации. */
function SupplyTab({ project, canOrder, canSeeStock, deadline }: {
  project: string; canOrder: boolean; canSeeStock: boolean; deadline?: string | null;
}) {
  const [lines, setLines] = useState<SupplyLine[] | null>(null);
  const [error, setError] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [until, setUntil] = useState((deadline || "").slice(0, 10));
  const [ordering, setOrdering] = useState(false);
  const [result, setResult] = useState<{ text: string; bad: boolean } | null>(null);
  // «Возможно есть»: one request for the positions short of stock; a failure leaves the column empty.
  const [maybe, setMaybe] = useState<Map<string, Maybe> | null>(null);
  function load() {
    return supplyApi.lines(project).then((answer) => setLines(answer.lines)).catch((reason) => setError(message(reason, "Сервер не ответил.")));
  }
  useEffect(() => { void load(); }, [project]);  // eslint-disable-line react-hooks/exhaustive-deps
  const wanted = (lines ?? []).filter((line) => Number(line.short) > 0);
  const wantedKey = wanted.map((line) => `${line.art}\u0000${line.short}`).join("\u0001");
  useEffect(() => {
    if (!canSeeStock || !wanted.length) { setMaybe(null); return; }
    let alive = true;
    setMaybe(null);
    fulfillmentApi.candidatesFor(project, wanted.map((line) => ({ article: line.art, name: line.name, unit: line.unit })))
      .then((answer) => { if (alive) setMaybe(new Map(answer.positions.map((item) => [item.article, { items: item.candidates, more: item.more }]))); })
      .catch(() => { if (alive) setMaybe(new Map()); });
    return () => { alive = false; };
  }, [project, canSeeStock, wantedKey]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (error) return <div className="empty">{error}</div>;
  if (!lines) return <div className="empty">Считаю по заявкам и закупке…</div>;
  if (!lines.length) return <div className="empty">В спецификации проекта нет позиций с артикулом.</div>;
  const isShort = (line: SupplyLine) => Number(line.short) > 0;
  const shortLines = lines.filter(isShort);
  const chosen = shortLines.filter((line) => picked.has(line.art));
  const count = chosen.length || shortLines.length;
  const zeroDash = (value: string) => Number(value) ? value : "—";
  // «Готово · без ГРН»: партию к строке привязывает её закупщик — подсказка говорит, где.
  const grnHint = (line: SupplyLine) => {
    const row = line.rows.find((item) => item.status.endsWith("без ГРН"));
    return row ? `ГРН привязывает закупщик: Закупка → Мои позиции → ${row.source_id} → «Привязать ГРН»` : "";
  };
  const stateHint = (line: SupplyLine) => [line.state_hint, line.rows.length ? `Цепочка: ${line.stage}` : "", grnHint(line)]
    .filter(Boolean).join("\n");
  const offered = (line: SupplyLine) => (isShort(line) && maybe ? maybe.get(line.art) : undefined);
  const candTone = (line: SupplyLine) => {
    const found = offered(line);
    return found ? candidateTone(Number(line.short), found.items) : null;
  };

  function toggle(art: string, on: boolean) {
    setPicked((current) => {
      const next = new Set(current);
      if (on) next.add(art); else next.delete(art);
      return next;
    });
  }

  async function order() {
    setOrdering(true);
    setResult(null);
    try {
      const answer = await supplyApi.orderMissing(project, { articles: chosen.map((line) => line.art), deadline: until || undefined });
      setResult({ text: answer.message, bad: false });
      setPicked(new Set());
      await load();
    } catch (reason) {
      setResult({ text: message(reason, "Заявки не созданы."), bad: true });
    } finally {
      setOrdering(false);
    }
  }

  const columns: DataColumn<SupplyLine>[] = [
    ...(canOrder ? [{ key: "pick", title: "Заказ", width: 56, value: (line: SupplyLine) => (picked.has(line.art) ? 1 : 0),
      render: (line: SupplyLine) => isShort(line)
        ? <label className="sel-hit"><input type="checkbox" aria-label={`Заказать ${line.art}`} checked={picked.has(line.art)} onChange={(event) => toggle(line.art, event.target.checked)} /></label>
        : null }] : []),
    { key: "art", title: "Артикул КД", width: 170, wrap: true, value: (line) => line.art, render: (line) => <strong>{line.art}</strong> },
    // Источник имени — подсказкой (справочник / склад / спецификация / сайт); без имени — «—» и почему.
    { key: "name", title: "Наименование", width: 260, value: (line) => line.name, wrap: true,
      render: (line) => line.name
        ? <span title={line.name_hint || undefined}>{line.name}</span>
        : <span className="supply-unknown" title={line.name_hint || undefined}>— <small>артикул неизвестен</small></span> },
    // «Стадия» одним словом сразу за именем (владелец 12.10.2026): цифры за ней — в подсказке.
    { key: "stage", title: "Стадия", width: 165, wrap: true, filter: true, value: (line) => line.state_label || line.stage,
      render: (line) => <span className={`tone-${line.state_tone || "neutral"}`} title={stateHint(line) || undefined}>{line.state_label || line.stage}</span> },
    { key: "need", title: "Нужно", width: 72, value: (line) => line.need, numeric: true },
    { key: "free", title: "Свободно", width: 90, value: (line) => zeroDash(line.free), numeric: true, hint: "Свободно на складе, с резервами самого проекта" },
    // Ключевые — до «Возможно есть» (влезают в 16"), разбивка «Без ГРН / В закупке / В комплектации» — за ней.
    { key: "short", title: "Не хватает", width: 100, value: (line) => zeroDash(line.short), numeric: true,
      render: (line) => isShort(line) ? <strong className="tone-danger">{line.short}</strong> : <span className="muted">—</span> },
    ...(canSeeStock ? [{ key: "maybe", title: "Возможно есть", width: 200, wrap: true,
      hint: "Партии склада, которые могут быть этой позицией: тот же артикул или похожий",
      value: (line: SupplyLine) => offered(line)?.items[0]?.grn ?? "",
      render: (line: SupplyLine) => {
        if (!isShort(line)) return <span className="muted">—</span>;
        if (!maybe) return <span className="t-sub">…</span>;
        const found = offered(line);
        const best = found?.items[0];
        if (!best) return <span className="cand-none-text">не найдено</span>;
        const others = (found?.items.length ?? 1) - 1 + (found?.more ?? 0);
        return <span className="supply-cand" title={`${matchText(best)} · ${best.article} · ${best.name}`}>
          <span>ГРН {best.grn} · {best.available} {best.unit}</span>
          <small>{matchText(best)} · {best.warehouse}{others > 0 ? ` · ещё ${others}` : ""}</small>
        </span>;
      } }] : []),
    { key: "off_book", title: "Без ГРН", width: 76, value: (line) => zeroDash(line.off_book || "0"), numeric: true, hint: "Закупка отметила «Есть на складе, без ГРН»: лежит на полке, но не заведено в партию и не в резерве" },
    { key: "ordered", title: "В закупке", width: 84, value: (line) => zeroDash(line.ordered), numeric: true, hint: "Заявки и строки закупки проекта, ещё не принятые" },
    { key: "kitted", title: "В комплектации", width: 132, value: (line) => zeroDash(line.kitted), numeric: true, hint: "В резерве или выдано по комплектации проекта" },
    { key: "unit", title: "Ед.", width: 48, value: (line) => line.unit },
    { key: "kbs", title: "КБ", width: 72, wrap: true, value: (line) => line.kbs.join(" · ") },
    { key: "rows", title: "Строк", width: 64, hidden: true, value: (line) => line.rows.length || "—", numeric: true },
  ];

  return <div className="kit-pane">
    {/* Счётчики одной строкой (аудит 10.10, ПР-8): число — цветом своего тона, ноль — серым. */}
    <dl className="kit-supply-summary" aria-label="Итог по снабжению">
      {SUPPLY_STATES.map(([key, label, toneName]) => {
        const value = lines.filter((line) => line.state === key).length;
        return <div key={key} className="kit-supply-count"><dt>{label}</dt><dd className={value ? `tone-${toneName}` : "muted"}>{value}</dd></div>;
      })}
    </dl>
    {result && <div className={result.bad ? "error-banner" : "success-banner"} role={result.bad ? "alert" : "status"}>{result.text}</div>}
    {canSeeStock && shortLines.length > 0 && <div className="cand-legend" aria-label="Цвета строк">
      <span>Возможно есть на складе (для нехватки):</span>
      {CANDIDATE_LEGEND.filter((item) => item.tone !== "done").map((item) => <span key={item.tone} className={`cand-key cand-${item.tone}`}>{item.text}</span>)}
    </div>}
    <DataTable
      rows={lines}
      columns={columns}
      rowClassName={(line) => { const toneName = candTone(line); return toneName ? `cand-row cand-${toneName}` : ""; }}
      rowKey={(line) => line.art}
      storageKey="kit-supply"
      label="Снабжение проекта"
      exportName={`Снабжение ${project}`}
      toolbar={canOrder ? <>
        <label className="check-inline">Срок закупки <input type="date" aria-label="Срок закупки" value={until} onChange={(event) => setUntil(event.target.value)} /></label>
        <button type="button" className="primary small" disabled={ordering || !count || !until} onClick={() => void order()}
          title={until ? undefined : "Укажите срок закупки"}>
          {ordering ? "Оформляю заявки…" : `Заказать недостающее (${count})`}
        </button>
      </> : undefined}
    />
  </div>;
}

type Draft = { per: string; scrap: string; name: string; link: string; lot: KitsetLot | null };
type SpecRow = { assembly: KitsetAssembly; component: KitsetComponent };

function SpecTab({ card, canEdit, setNote, setWarning, reload }: {
  card: KitsetCard; canEdit: boolean; setNote: (text: string) => void; setWarning: (text: string) => void; reload: () => Promise<void>;
}) {
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [autosave, setAutosave] = useState("правки сохраняются сами");
  const [picking, setPicking] = useState<{ assembly: KitsetAssembly; component: KitsetComponent } | null>(null);
  const [issuing, setIssuing] = useState(false);
  const key = (index: string, art: string) => `${index}\u0000${art}`;
  const draftOf = (assembly: KitsetAssembly, component: KitsetComponent): Draft =>
    drafts[key(assembly.index, component.art)] || { per: component.per, scrap: component.scrap, name: component.name, link: component.link, lot: component.lot };

  function edit(assembly: KitsetAssembly, component: KitsetComponent, patch: Partial<Draft>) {
    setDrafts((current) => ({ ...current, [key(assembly.index, component.art)]: { ...draftOf(assembly, component), ...patch } }));
  }

  /* Автосохранение ОДНОЙ строки (владелец 02.09.2026): правка одной ячейки не перезаписывает соседние двадцать. */
  async function save(assembly: KitsetAssembly, component: KitsetComponent, patch: Partial<Draft> = {}): Promise<string> {
    const draft = { ...draftOf(assembly, component), ...patch };
    setAutosave("сохраняю…");
    try {
      await kitsetsApi.saveLines(card.project, [{
        index: assembly.index, art: component.art, per: draft.per, scrap: draft.scrap,
        name: draft.name, link: draft.link, lot: draft.lot,
      }]);
      setAutosave(`сохранено ${new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`);
      if (patch.lot !== undefined) await reload();  // резерв под выбранный ГРН уже стоит: ячейка покажет его
      return "";
    } catch (reason) {
      // 409: партию успели изменить с момента, когда список ГРН был загружен — выбор не принят.
      const text = reason instanceof ApiError && reason.status === 409 ? STALE_LOT_TEXT : message(reason, "ошибка");
      setAutosave(`не сохранено: ${text}`);
      return text;
    }
  }

  /** Выбор ГРН: пока сохранение не прошло, выбор в строке не остаётся; пустая строка — принято. */
  async function pickLot(row: SpecRow, lot: KitsetLot | null): Promise<string> {
    const before = draftOf(row.assembly, row.component).lot;
    edit(row.assembly, row.component, { lot });
    const failure = await save(row.assembly, row.component, { lot });
    if (failure) edit(row.assembly, row.component, { lot: before });
    return failure;
  }

  const specRows: SpecRow[] = card.assemblies.flatMap((assembly) => assembly.components.map((component) => ({ assembly, component })));
  const specColumns: DataColumn<SpecRow>[] = [
    { key: "art", title: "Артикул КД", width: 170, wrap: true, value: ({ component }) => component.art },
    { key: "type", title: "Тип", width: 90, filter: true, value: ({ component }) => component.type },
    { key: "name", title: "Наименование", value: ({ assembly, component }) => draftOf(assembly, component).name,
      render: ({ assembly, component }) => <input aria-label={`Наименование ${component.art}`} value={draftOf(assembly, component).name} disabled={!canEdit} onChange={(event) => edit(assembly, component, { name: event.target.value })} onBlur={() => void save(assembly, component)} /> },
    { key: "link", title: "Ссылка", width: 180, value: ({ assembly, component }) => draftOf(assembly, component).link, render: ({ assembly, component }) => {
      const link = draftOf(assembly, component).link;
      return <div className="kit-link">
        <input aria-label={`Ссылка ${component.art}`} value={link} placeholder="—" disabled={!canEdit} onChange={(event) => edit(assembly, component, { link: event.target.value })} onBlur={() => void save(assembly, component)} />
        {/^https?:\/\//i.test(link) && <a href={link} target="_blank" rel="noopener" title="Открыть">↗</a>}
      </div>;
    } },
    { key: "per", title: "На 1 шт", width: 72, numeric: true, value: ({ assembly, component }) => draftOf(assembly, component).per,
      render: ({ assembly, component }) => <input aria-label={`На 1 шт ${component.art}`} value={draftOf(assembly, component).per} disabled={!canEdit} onChange={(event) => edit(assembly, component, { per: event.target.value })} onBlur={() => void save(assembly, component)} /> },
    { key: "scrap", title: "% брака", width: 72, numeric: true, value: ({ assembly, component }) => draftOf(assembly, component).scrap,
      render: ({ assembly, component }) => <input aria-label={`% брака ${component.art}`} value={draftOf(assembly, component).scrap} disabled={!canEdit} onChange={(event) => edit(assembly, component, { scrap: event.target.value })} onBlur={() => void save(assembly, component)} /> },
    { key: "batch", title: "На партию", width: 84, numeric: true, value: ({ assembly, component }) => {
      const draft = draftOf(assembly, component);
      return kitBatch(draft.per, assembly.run, draft.scrap, component.unit);
    } },
    { key: "unit", title: "Ед.", width: 44, value: ({ component }) => component.unit },
    { key: "lot", title: "ГРН склада", width: 300, value: ({ assembly, component }) => lotLabel(
      draftOf(assembly, component).lot, component.reserve, kitBatch(draftOf(assembly, component).per, assembly.run, draftOf(assembly, component).scrap, component.unit),
    ), render: ({ assembly, component }) => {
      const draft = draftOf(assembly, component);
      const text = lotLabel(draft.lot, component.reserve, kitBatch(draft.per, assembly.run, draft.scrap, component.unit));
      return <button className="secondary small" disabled={!canEdit} onClick={() => setPicking({ assembly, component })}>
        {text || "выбрать со склада"}
      </button>;
    } },
  ];

  return <div className="kit-pane">
    <p className="page-sub">«ГРН склада» резервируется сразу при выборе; без ГРН позиция ждёт поставки. Правки сохраняются сами.{KIT_SCRAP_APPLIED ? "" : " Выдаём вровень: «% брака» пока не добавляется к «На партию», нехватку довыдают."}</p>
    {canEdit
      ? <div className="page-actions"><button className="primary" onClick={() => setIssuing(true)}>Отправить на комплектацию</button><span className="muted kit-autosave">{autosave}</span></div>
      : <p className="page-sub">Раздел открыт на просмотр — править и отправлять нельзя.</p>}
    {issuing && <IssueDialog project={card.project} close={() => setIssuing(false)} done={(text, reserveError) => {
      setIssuing(false); setNote(text);
      setWarning(reserveError ? `Резерв ГРН пересчитан не полностью: ${reserveError}` : "");
      void reload();
    }} />}
    {picking && <LotPicker project={card.project} component={picking.component} batch={kitBatch(draftOf(picking.assembly, picking.component).per, picking.assembly.run, draftOf(picking.assembly, picking.component).scrap, picking.component.unit)}
      current={draftOf(picking.assembly, picking.component).lot}
      close={() => setPicking(null)}
      pick={async (lot) => {
        const failure = await pickLot(picking, lot);
        if (!failure) setPicking(null);
        return failure;
      }} />}
    {card.assemblies.map((assembly) => <div key={assembly.index} className="kit-spec">
      <p><strong>{assembly.index}</strong> {assembly.name} <span className="muted">кол-во {assembly.run}</span></p>
      <DataTable
        rows={specRows.filter((row) => row.assembly.index === assembly.index)}
        columns={specColumns}
        rowKey={({ component }) => key(assembly.index, component.art)}
        storageKey="kit-spec"
        label={`Спецификация ${assembly.index}`}
        exportName={`Спецификация ${card.project} ${assembly.index}`}
        emptyText="В сборке нет позиций."
      />
    </div>)}
  </div>;
}

function LotPicker({ project, component, batch, current, close, pick }: {
  project: string; component: KitsetComponent; batch: number; current: KitsetLot | null; close: () => void; pick: (lot: KitsetLot | null) => Promise<string>;
}) {
  const [query, setQuery] = useState(component.name || component.art);
  const [pickError, setError] = useState("");
  const [stamp, setStamp] = useState(0);
  const [busy, setBusy] = useState(false);
  // Only the answer to the current query/project is shown; «Выбрать» waits while the list belongs to an older one.
  const found = useLatestSearch(() => kitsetsApi.lots(query, project).then((result) => result.lots), [query, project, stamp], { fallback: "Склад не ответил" });
  const lots = found.data;
  const error = pickError || found.error;
  /* Отказ сохранения (партия изменилась): окно остаётся, текст отказа сверху, список ГРН перечитывается. */
  async function choose(lot: KitsetLot | null) {
    if (busy) return;
    setBusy(true); setError("");
    const failure = await pick(lot);
    if (failure) { setError(failure); setStamp((value) => value + 1); setBusy(false); }
  }
  return <div className="panel kit-modal" role="dialog" aria-label="Выбор ГРН">
    <h3>ГРН для «{component.name || component.art}»</h3>
    <p className="page-sub">Нужно на партию: {batch}. Выбранный ГРН резервируется сразу при выборе.</p>
    <input aria-label="Поиск по складу" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="наименование, артикул, ГРН" />
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="table-wrap"><table>
      <thead><tr><th>Склад</th><th>ГРН</th><th>Пакет</th><th>Наименование</th><th>Место</th><th>Доступно</th><th /></tr></thead>
      <tbody>{(lots || []).map((lot) => <tr key={lot.lot_id} className={current?.grn === lot.grn && (current?.package || "") === lot.package ? "selected" : ""}>
        <td>{lot.warehouse}</td><td>{lot.grn}</td><td>{lot.package}</td><td>{lot.name}</td><td>{lot.storage_location}</td>
        <td className="num">{lot.available} {lot.unit}{lot.reserved && <div className="muted">из них резерв проекта {lot.reserved} {lot.unit}</div>}</td>
        <td><button className="secondary small" disabled={busy || found.loading} onClick={() => void choose({ lot_id: lot.lot_id, warehouse: lot.warehouse, grn: lot.grn, package: lot.package, version: lot.version })}>Выбрать</button></td>
      </tr>)}</tbody>
    </table></div>
    {found.loading && <div className="empty" role="status">Ищу…</div>}
    {lots && !found.loading && !lots.length && <div className="empty">Ничего не нашлось — сократите запрос или ищите по артикулу.</div>}
    <div className="page-actions">
      {current && <button className="secondary" disabled={busy} onClick={() => void choose(null)}>Снять привязку</button>}
      <button className="secondary" onClick={close}>Закрыть</button>
    </div>
  </div>;
}

/* «Отправить на комплектацию» уходит по одной заявке на КБ; КБ с живой заявкой (не «Отменена») второй раз не уходит
   (прод 180К.11, 10.10.2026: двойное нажатие задвоило КБ1 и её резерв) — окно показывает их с номером заявки. */
function IssueDialog({ project, close, done }: { project: string; close: () => void; done: (text: string, reserveError: string) => void }) {
  const [deadline, setDeadline] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [kbs, setKbs] = useState<KitsetIssueAssembly[] | null>(null);
  useEffect(() => {
    let live = true;
    kitsetsApi.issueState(project)
      .then((result) => { if (live && Array.isArray(result.assemblies)) setKbs(result.assemblies); })
      .catch(() => undefined);  // без списка окно работает как раньше: повтор КБ отклонит сервер
    return () => { live = false; };
  }, [project]);
  const sent = (kbs ?? []).filter((item) => item.request);
  const toSend = (kbs ?? []).filter((item) => !item.request && item.has_items);
  const nothingLeft = sent.length > 0 && !toSend.length;
  async function submit() {
    if (busy) return;
    if (!deadline) { setError("Укажите дедлайн."); return; }
    setBusy(true); setError("");
    try {
      const result = await kitsetsApi.issue(project, deadline);
      const numbers = result.created.map((item) => item.no + (item.shortfall.length ? " (недобор)" : "")).join(", ");
      done(`${result.message} ${numbers}`.trim(), result.reserve_error || "");
    } catch (reason) { setError(message(reason, "Заявки не созданы")); }
    finally { setBusy(false); }
  }
  return <div className="panel kit-modal" role="dialog" aria-label="Отправить на комплектацию">
    <h3>Отправить на комплектацию</h3>
    <p className="page-sub">По заявке на каждую КБ проекта {project}. Позиции с ГРН встают в резерв, остальные ждут поставки.</p>
    {kbs && kbs.length > 0 && <ul className="issue-kbs" aria-label="КБ проекта">
      {kbs.map((item) => <li key={item.index} className={item.request || !item.has_items ? "muted" : undefined} aria-disabled={Boolean(item.request) || !item.has_items}>
        <strong>{item.index}</strong> <span>{item.name}</span>
        <span className={item.request ? "issue-kb-state" : item.has_items ? "issue-kb-state tone-ok" : "issue-kb-state"}>
          {item.request ? `уже отправлена: ${item.request.no} · ${item.request.status}` : item.has_items ? "уйдёт" : "нет состава"}
        </span>
      </li>)}
    </ul>}
    {nothingLeft
      ? <div className="warning-inline">Все КБ уже отправлены: {sent.map((item) => `${item.index} (${item.request!.no})`).join(", ")}. Чтобы отправить КБ заново, отмените её заявку в «Комплектации».</div>
      : <label>Дедлайн<input type="date" value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="page-actions">
      <button className="primary" disabled={busy || nothingLeft} onClick={() => void submit()}>{toSend.length && sent.length ? `Отправить ${toSend.map((item) => item.index).join(", ")}` : "Отправить"}</button>
      <button className="secondary" onClick={close}>Отмена</button>
    </div>
  </div>;
}

