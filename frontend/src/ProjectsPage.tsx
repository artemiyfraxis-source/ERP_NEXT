import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  kitsetsApi,
  type KitsetAssembly,
  type KitsetCard,
  type KitsetComponent,
  type KitsetDeleteResult,
  type KitsetLot,
  type KitsetStockLot,
  type KitsetSupplyLine,
  type KitsetTile,
  type Principal,
} from "./api";

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

/** Mirror of the server `batch_quantity`: расход × тираж × (1 + брак/100), до сотых — «На партию» при вводе. */
export function kitBatch(per: string, run: string, scrap: string) {
  const num = (value: string) => {
    const parsed = parseFloat(String(value ?? "").replace(/\s/g, "").replace(",", "."));
    return Number.isFinite(parsed) ? parsed : 0;
  };
  return Math.round(num(per) * num(run) * (1 + num(scrap) / 100) * 100) / 100;
}

/** Техкарта сборки открывается в «Техкарты проектов»: ссылка ?section=techcards&card=<id>. */
function openTechcardScreen(cardId: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("section", "techcards");
  url.searchParams.delete("block");
  url.searchParams.set("card", cardId);
  window.history.pushState({}, "", url);
  window.dispatchEvent(new PopStateEvent("popstate"));
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
  if (res.kd) parts.push(`КД ${res.kd}`);
  if (res.gp?.deleted) parts.push(`склад ГП ${res.gp.deleted}`);
  return `Снесено строк: ${parts.join(", ")}` + (res.gp?.kept ? `. На складе ГП осталось с движением: ${res.gp.kept}` : "");
}

const DELETE_TEXT = "Проект уйдёт целиком: состав и спецификация, сам проект в реестре ERP, выданные техкарты с их "
  + "ревизиями, КД и строки склада готовой продукции. Восстановить можно повторной загрузкой из расчёта — кроме правок "
  + "количеств, брака, наименований и привязок ГРН: они пропадут. Останутся только то, что каскаду не принадлежит: "
  + "цеховые задания (факт работы людей), заявки в закупке и на комплектацию по этому проекту и строки склада ГП, "
  + "по которым уже есть приход или отгрузка. Их сноси вручную в своём разделе.";

export function ProjectsPage({ principal }: { principal: Principal }) {
  const canEdit = principal.permissions.projects === "edit";
  const [open, setOpen] = useState(() => new URLSearchParams(window.location.search).get("project") || "");
  if (open) return <KitsetCardView project={open} canEdit={canEdit} back={() => setOpen("")} />;
  return <KitsetHub canEdit={canEdit} openProject={setOpen} />;
}

function KitsetHub({ canEdit, openProject }: { canEdit: boolean; openProject: (project: string) => void }) {
  const [all, setAll] = useState<KitsetTile[] | null>(null);
  const [statuses, setStatuses] = useState<string[]>([]);
  const [sort, setSort] = useState<SortKey>(() => stored("kitSort", "upd") as SortKey);
  const [filter, setFilter] = useState(() => stored("kitFilt", ""));
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [creating, setCreating] = useState(false);

  async function load() {
    try {
      const result = await kitsetsApi.list(sort);
      setAll(result.items); setStatuses(result.statuses);
    } catch (reason) { setError(message(reason, "Не удалось загрузить проекты")); }
  }
  useEffect(() => { void load(); }, [sort]);

  // Запомненный фильтр, под который больше нет ни одного проекта, спрятал бы весь список.
  const activeFilter = all && filter && !all.some((item) => item.status === filter) ? "" : filter;
  const list = (all || []).filter((item) => !activeFilter || item.status === activeFilter);

  async function remove(project: string) {
    if (!window.confirm(`Удалить карточку «${project}»?\n\n${DELETE_TEXT}`)) return;
    setError(""); setNotice("");
    try {
      const result = await kitsetsApi.remove(project);
      setNotice(deleteSummary(result.res));
    } catch (reason) {
      if (gone(reason)) setNotice("Карточка уже удалена — список обновлён");
      else setError(message(reason, "Не удалось удалить проект"));
    }
    await load();
  }

  return <section className="page projects-page">
    <header className="page-head"><h1>Проекты</h1>{all && <span className="page-count">{all.length}</span>}</header>
    <p className="page-sub">Проект заводится из расчёта: номер, заказчик, сборки с тиражом и состав каждой. Он же попадает в реестр ERP вместе с позициями, поэтому техкарты, задания и план видят те же КБ. Внутри проекта — состав, спецификация и техкарты по каждой сборке.</p>
    {canEdit && <div className="page-actions"><button className="primary" onClick={() => setCreating(true)}>➕ Создать проект из расчёта</button></div>}
    {creating && <CreateFromCalc close={() => setCreating(false)} done={(project, note, bad) => {
      setCreating(false);
      if (bad) setError(note); else setNotice(note);
      openProject(project);
    }} />}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {all === null && !error && <div className="empty">Загрузка…</div>}
    {all && !all.length && <div className="empty">Наборов пока нет — загрузи первый из расчёта.</div>}
    {all && all.length > 0 && <>
      <div className="plan-filters kit-filters">
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
        <span className="muted">показано {list.length} из {all.length}</span>
      </div>
      {list.length ? <div className="tiles kit-tiles">
        {list.map((item) => <KitTile key={item.project} item={item} statuses={statuses} canEdit={canEdit} open={() => openProject(item.project)} remove={() => void remove(item.project)} />)}
      </div> : <div className="empty">Нет проектов с таким статусом.</div>}
    </>}
  </section>;
}

function toneOf(status: string, statuses: string[]) {
  const index = statuses.indexOf(status);
  const done = statuses.indexOf("Отгружено");
  if (!status || index <= 0) return "neutral";
  if (index > done) return "danger";
  if (index === done) return "done";
  if (status === "КБ на складе") return "stock";
  if (status === "В работе") return "work";
  return "prep";
}

function KitTile({ item, statuses, canEdit, open, remove }: {
  item: KitsetTile; statuses: string[]; canEdit: boolean; open: () => void; remove: () => void;
}) {
  const kbs = item.kb_stages;
  const mixed = kbs.some((kb) => kb.status !== kbs[0].status);
  const reviews = kbs.map((kb) => kb.review?.value);
  const ok = reviews.filter((value) => value === "проверена").length;
  const bad = reviews.filter((value) => value === "с погрешностями").length;
  const total = kbs.length;
  const tc = kbs.filter((kb) => kb.tc).length;
  const kit = kbs.filter((kb) => kb.kit).length;
  const unreviewed = kbs.filter((kb) => kb.tc && !(kb.review?.value && kb.review.value !== "не проверена")).length;
  const index = statuses.indexOf(item.status);
  const late = index >= statuses.indexOf("В работе") && index < statuses.indexOf("Отгружено");
  const facts: Array<[string, string, string]> = [];
  if (item.blocker) facts.push([`⛔ ${item.blocker}`, "danger", "Активный блокер из журнала проекта"]);
  // Отказ/Слился/Отменён стоят после «Отгружено»: у закрытого проекта факты не нужны.
  if (total && index <= statuses.indexOf("Отгружено")) {
    facts.push(tc === total ? ["ТК ✓", "ok", "Техкарты выданы на все КБ"] : tc ? [`ТК ${tc}/${total}`, "warn", "Техкарты выданы не на все КБ"] : ["ТК нет", "neutral", "Техкарт ещё нет"]);
    if (tc && unreviewed) facts.push([`не проверено ${unreviewed}`, "warn", "Выданные ТК без отметки проверки"]);
    if (kit === total) facts.push(["набрано ✓", "ok", "Комплектация набрана на все КБ"]);
    else if (kit) facts.push([`набрано ${kit}/${total}`, "warn", "Комплектация набрана не на все КБ"]);
    else if (late) facts.push(["не набрано", "neutral", "Заявки на комплектацию не набраны"]);
  }
  return <div className="tile kit-tile" role="button" tabIndex={0} onClick={open} onKeyDown={(event) => { if (event.key === "Enter") open(); }}>
    <div className="tile-body">
      <div className="kit-tile-head">
        <strong className="t-h">{item.project}</strong>
        {canEdit && <button className="link-button danger-link" title="Удалить карточку проекта" aria-label={`Удалить ${item.project}`} onClick={(event) => { event.stopPropagation(); remove(); }}>🗑</button>}
      </div>
      <div className="kit-chips">
        <span className={`kit-status tone-${toneOf(item.status, statuses)}`}>{item.status || "—"}</span>
        {facts.map(([text, tone, title]) => <span key={text} className={`kit-fact tone-${tone}`} title={title}>{text}</span>)}
      </div>
      {mixed && <div className="t-sub">{kbs.map((kb) => `${kb.index} — ${kb.status}`).join(" · ")}</div>}
      {(ok > 0 || bad > 0) && <div className="t-in">
        {ok > 0 && <span className="kit-ok">✓ ТК проверено {ok} </span>}
        {bad > 0 && <span className="kit-bad">⚠ с погрешностями {bad}</span>}
      </div>}
      <div className="t-sub">{item.customer || "—"}</div>
      <div className="t-in">сборок {item.assemblies} · позиций {item.lines}</div>
      <div className="t-sub">обновлено {new Date(item.updated_at).toLocaleString("ru-RU")}</div>
    </div>
  </div>;
}

function CreateFromCalc({ close, done, number = "", title = "➕ Создать проект из расчёта" }: {
  close: () => void; done: (project: string, note: string, bad: boolean) => void; number?: string; title?: string;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState({ number, deadline: "", shipping_point: "", contact: "", phone: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = !!number;

  async function submit() {
    if (!file || busy) { if (!file) setError("Выбери файл расчёта .xlsx"); return; }
    setBusy(true); setError("");
    try {
      const { res } = await kitsetsApi.load({
        file_name: file.name, content_b64: await readFileBase64(file), number: form.number,
        deadline: form.deadline || null,
        ship: refresh ? null : { shipping_point: form.shipping_point, contact: form.contact, phone: form.phone },
      });
      const problems = [
        res.erp_error && `реестр ERP: ${res.erp_error}`,
        res.deadline_error && `дедлайн не записан: ${res.deadline_error}`,
        res.ship_to_error && `адресат не сохранён: ${res.ship_to_error}`,
        res.ship_error && `очередь отгрузки: ${res.ship_error}`,
      ].filter(Boolean);
      done(res.project, `Проект «${res.project}»: сборок ${res.assemblies}, позиций ${res.lines}` + (problems.length ? ` · ${problems.join(" · ")}` : ""), problems.length > 0);
    } catch (reason) {
      setError(message(reason, "Проект не заведён"));
    } finally { setBusy(false); }
  }

  return <div className="panel kit-modal" role="dialog" aria-label={title}>
    <h3>{title}</h3>
    <p className="page-sub">{refresh
      ? "Загрузи свежий файл расчёта. Состав обновится, правки количеств, брака, наименований и ГРН сохранятся, статус и дедлайн не тронутся."
      : "Нужен только файл расчёта (Google Таблица → Файл → Скачать → .xlsx): номер проекта, заказчик, сборки и состав читаются из листов ЗКЗ и СПЯ. Проект заведётся в реестре ERP вместе с позициями. Если он уже есть — обновится состав, правки количеств сохранятся, статус и дедлайн не тронутся."}</p>
    <div className="grid wide">
      <label>Файл расчёта (.xlsx)<input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => setFile(event.target.files?.[0] || null)} /></label>
      {!refresh && <>
        <label>№ проекта<input value={form.number} placeholder="пусто — возьмём из расчёта" onChange={(event) => setForm({ ...form, number: event.target.value })} /></label>
        <label>Дедлайн<input type="date" value={form.deadline} onChange={(event) => setForm({ ...form, deadline: event.target.value })} /></label>
        <label>Адрес отгрузки<input value={form.shipping_point} placeholder="можно позже — в «Отгрузке»" onChange={(event) => setForm({ ...form, shipping_point: event.target.value })} /></label>
        <label>Контактное лицо<input value={form.contact} placeholder="кто принимает" onChange={(event) => setForm({ ...form, contact: event.target.value })} /></label>
        <label>Телефон<input value={form.phone} placeholder="для связи" onChange={(event) => setForm({ ...form, phone: event.target.value })} /></label>
      </>}
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="page-actions">
      <button className="primary" disabled={busy} onClick={() => void submit()}>{busy ? "Читаю расчёт…" : refresh ? "Обновить" : "Создать"}</button>
      <button className="secondary" onClick={close}>Отмена</button>
    </div>
  </div>;
}

type Tab = "sostav" | "supply" | "spec" | "gen" | `tc:${string}`;

function KitsetCardView({ project, canEdit, back }: { project: string; canEdit: boolean; back: () => void }) {
  const [card, setCard] = useState<KitsetCard | null>(null);
  const [tab, setTab] = useState<Tab>("sostav");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    try { setCard(await kitsetsApi.get(project)); }
    catch (reason) {
      if (gone(reason)) { setError("Карточка уже удалена — возвращаю к списку…"); window.setTimeout(back, 1200); }
      else setError(message(reason, "Не удалось открыть проект"));
    }
  }
  useEffect(() => { void load(); }, [project]);

  async function remove() {
    if (!window.confirm(`Удалить карточку «${project}»?\n\n${DELETE_TEXT}`)) return;
    try {
      const result = await kitsetsApi.remove(project);
      window.alert(deleteSummary(result.res));
      back();
    } catch (reason) {
      if (gone(reason)) back(); else setError(message(reason, "Не удалось удалить проект"));
    }
  }

  async function openTechcard(index: string) {
    setError("");
    try { openTechcardScreen((await kitsetsApi.openTechcard(project, index)).card_id); }
    catch (reason) { setError(message(reason, "Не удалось открыть техкарту")); }
  }

  if (!card) return <section className="page projects-page">
    <header className="page-head"><button className="secondary small" onClick={back}>← Назад</button><h1>{project}</h1></header>
    {error ? <div className="error-banner" role="alert">{error}</div> : <div className="empty">Загрузка…</div>}
  </section>;

  const tcTabs = card.assemblies.filter((assembly) => assembly.techcard && assembly.techcard.stages > 0);
  const tabs: Array<[Tab, string]> = [
    ["sostav", "Состав"], ["supply", "Снабжение"], ["spec", "Спецификация"], ["gen", "Генератор"],
    ...tcTabs.map((assembly) => [`tc:${assembly.index}` as Tab, `ТК ${assembly.index}`] as [Tab, string]),
  ];

  return <section className="page projects-page">
    <header className="page-head"><button className="secondary small" onClick={back}>← Назад</button><h1>📋 {card.project}</h1><span className="kit-status">{card.status}</span></header>
    <p className="page-sub">{card.customer || "—"} · расчёт: {card.calc_source || "—"} · обновлено {new Date(card.updated_at).toLocaleString("ru-RU")} {card.updated_by}</p>
    <div className="page-actions">
      {canEdit && <button className="secondary" onClick={() => setRefreshing(true)}>🔄 Обновить из расчёта</button>}
      {canEdit && <button className="secondary" onClick={() => void remove()}>🗑 Удалить карточку</button>}
      <button className="secondary" onClick={() => openSection("shipping", { ship_project: card.project })}>🚚 Отгрузить</button>
      {note && <span className="muted">{note}</span>}
    </div>
    {refreshing && <CreateFromCalc number={card.project} title="🔄 Обновить из расчёта" close={() => setRefreshing(false)} done={(_project, text, bad) => {
      setRefreshing(false); if (bad) setError(text); else setNote(text); void load();
    }} />}
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="shipping-tabs" role="tablist">
      {tabs.map(([key, label]) => <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "secondary active" : "secondary"} onClick={() => setTab(key)}>{label}</button>)}
    </div>
    {tab === "sostav" && <SostavTab card={card} canEdit={canEdit} reload={load} setNote={setNote} setError={setError} openTechcard={(index) => void openTechcard(index)} />}
    {tab === "supply" && <SupplyTab project={card.project} />}
    {tab === "spec" && <SpecTab card={card} canEdit={canEdit} setNote={setNote} />}
    {tab === "gen" && <div className="panel kit-pane">
      <p className="page-sub">Выбери сборку — откроется её техкарта: сборка ЭТК по составу этой сборки, выдача и ревизии.</p>
      <div className="shipping-tabs">{card.assemblies.map((assembly) => <button key={assembly.index} className="secondary" onClick={() => void openTechcard(assembly.index)}>{assembly.index}</button>)}</div>
    </div>}
    {tab.startsWith("tc:") && (() => {
      const assembly = card.assemblies.find((item) => `tc:${item.index}` === tab);
      if (!assembly?.techcard) return <div className="empty">Сборка не найдена.</div>;
      return <div className="panel kit-pane">
        <p><strong>{assembly.index}</strong> {assembly.name} <span className="muted">кол-во {assembly.run} · операций {assembly.techcard.stages} · ревизия {assembly.techcard.revision} · {assembly.techcard.status}</span></p>
        <p className="page-sub">Электронную ТК правят в её карточке («Править» на операции), каждая правка поднимает ревизию; пересборка сохраняет ручные правки.</p>
        <div className="page-actions">
          <button className="primary" onClick={() => openTechcardScreen(assembly.techcard!.card_id)}>📄 Открыть ЭТК</button>
          <button className="secondary" onClick={() => void openTechcard(assembly.index)}>⚙️ Пересобрать</button>
        </div>
      </div>;
    })()}
  </section>;
}

const REVIEW_TONE: Record<string, string> = { "проверена": "ok", "с погрешностями": "danger", "не проверена": "neutral" };

function SostavTab({ card, canEdit, reload, setNote, setError, openTechcard }: {
  card: KitsetCard; canEdit: boolean; reload: () => Promise<void>; setNote: (text: string) => void;
  setError: (text: string) => void; openTechcard: (index: string) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [kdIndex, setKdIndex] = useState("");

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

  async function detach(documentId: string) {
    if (!window.confirm("Отвязать КД? Файл удалится из карточки проекта.")) return;
    try { await kitsetsApi.removeDocument(card.project, documentId); setNote("Отвязано."); await reload(); }
    catch (reason) { setError(message(reason, "КД не отвязана")); }
  }

  return <div className="kit-pane">
    <p className="page-sub">Позиции проекта: сборки, их количество и приложенная КД. Состав каждой — на вкладке «Спецификация», техкарта собирается в «Генераторе».</p>
    <input ref={fileRef} type="file" hidden data-testid="kd-file" onChange={(event) => { void upload(event.target.files?.[0]); event.target.value = ""; }} />
    <div className="table-wrap"><table>
      <thead><tr><th>КБ</th><th>Наименование</th><th>Кол-во</th><th>Позиций</th><th>КД</th><th>Техкарта</th><th>Этап</th><th>Проверка ТК</th></tr></thead>
      <tbody>{card.assemblies.map((assembly) => {
        const review = assembly.stage?.review || { value: "не проверена", text: "" };
        return <tr key={assembly.index}>
          <td><strong>{assembly.index}</strong></td>
          <td>{assembly.name}</td>
          <td className="num">{assembly.run}</td>
          <td className="num">{assembly.components.length}</td>
          <td>
            {assembly.kd.map((document) => <div key={document.id} className="kit-kd">
              <a href={kitsetsApi.documentUrl(card.project, document.id)} target="_blank" rel="noopener">{document.name.replace(/^\S+\s+—\s+/, "")}</a>
              {canEdit && <button className="link-button" title="Отвязать" onClick={() => void detach(document.id)}>✕</button>}
            </div>)}
            {canEdit && <button className="link-button" onClick={() => { setKdIndex(assembly.index); fileRef.current?.click(); }}>＋ файл</button>}
          </td>
          <td>{assembly.techcard
            ? <button className="link-button" onClick={() => openTechcard(assembly.index)}>{assembly.techcard.issued ? "готова" : "черновик"} · {assembly.techcard.stages} оп.</button>
            : <span className="muted">нет</span>}</td>
          <td>{canEdit
            ? <select aria-label={`Этап ${assembly.index}`} value={assembly.stage?.manual ? assembly.stage.status : "авто"} onChange={(event) => void markKb(assembly, "stage", event.target.value)}>
              <option value="авто">авто: {assembly.stage?.manual ? "по фактам" : assembly.stage?.status || "—"}</option>
              {card.stages.map((stage) => <option key={stage} value={stage}>{stage}</option>)}
            </select>
            : <>{assembly.stage?.status || "—"}{assembly.stage?.manual && <span className="muted" title="выставлен вручную"> ✍</span>}</>}</td>
          <td>{!assembly.techcard?.issued ? <span className="muted">—</span> : <>
            {canEdit
              ? <select aria-label={`Проверка ТК ${assembly.index}`} className={`tone-${REVIEW_TONE[review.value] || "neutral"}`} value={review.value} onChange={(event) => void markKb(assembly, "tcReview", event.target.value)}>
                {card.review_values.map((value) => <option key={value} value={value}>{value}</option>)}
              </select>
              : <span className={`tone-${REVIEW_TONE[review.value] || "neutral"}`}>{review.value}</span>}
            {review.text && review.value === "с погрешностями" && <div className="t-sub">{review.text}</div>}
          </>}</td>
        </tr>;
      })}</tbody>
    </table></div>
  </div>;
}

function SupplyTab({ project }: { project: string }) {
  const [lines, setLines] = useState<KitsetSupplyLine[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    kitsetsApi.supply(project).then((result) => setLines(result.lines)).catch((reason) => setError(message(reason, "Сервер не ответил.")));
  }, [project]);
  if (error) return <div className="empty">{error}</div>;
  if (!lines) return <div className="empty">Считаю по заявкам и закупке…</div>;
  if (!lines.length) return <div className="empty">В спецификации проекта нет позиций с артикулом.</div>;
  const missing = lines.filter((line) => line.stage === "не заведено").length;
  const received = lines.filter((line) => line.received).length;
  const stock = lines.filter((line) => line.stage === "со склада").length;
  const tone = (line: KitsetSupplyLine) => line.received ? "ok" : line.stage === "не заведено" ? "danger" : line.stage === "со склада" ? "info" : "warn";
  return <div className="kit-pane">
    <p className="page-sub">Позиции спецификации против закупки. Связь — по артикулу КД и проекту в заявках и закупке. «Не заведено» — позиции, по которым заявки нет вообще.</p>
    <div className="kit-supply-summary">
      {([["Принято на склад", received, "ok"], ["В работе снабжения", lines.length - missing - received - stock, "warn"], ["Со склада, без закупки", stock, "info"], ["Не заведено", missing, "danger"]] as Array<[string, number, string]>).map(([label, value, toneName]) =>
        <div key={label} className="panel kit-supply-count"><strong className={`tone-${toneName}`}>{value}</strong><span>{label}</span></div>)}
    </div>
    <div className="table-wrap"><table>
      <thead><tr><th>Артикул КД</th><th>Наименование</th><th>КБ</th><th>Нужно</th><th>Стадия</th><th>Строк</th></tr></thead>
      <tbody>{lines.map((line) => <tr key={line.art}>
        <td><strong>{line.art}</strong></td><td>{line.name}</td><td>{line.kbs.join(" · ")}</td>
        <td className="num">{line.need}</td>
        <td className={`tone-${tone(line)}`}>{line.stage}{line.grn && line.stage !== "со склада" && <span className="t-sub"> · ГРН в спец.</span>}</td>
        <td className="num">{line.rows.length || "—"}</td>
      </tr>)}</tbody>
    </table></div>
  </div>;
}

type Draft = { per: string; scrap: string; name: string; link: string; lot: KitsetLot | null };

function SpecTab({ card, canEdit, setNote }: { card: KitsetCard; canEdit: boolean; setNote: (text: string) => void }) {
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
  async function save(assembly: KitsetAssembly, component: KitsetComponent, patch: Partial<Draft> = {}) {
    const draft = { ...draftOf(assembly, component), ...patch };
    setAutosave("сохраняю…");
    try {
      await kitsetsApi.saveLines(card.project, [{
        index: assembly.index, art: component.art, per: draft.per, scrap: draft.scrap,
        name: draft.name, link: draft.link, lot: draft.lot,
      }]);
      setAutosave(`сохранено ${new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`);
    } catch (reason) { setAutosave(`не сохранено: ${message(reason, "ошибка")}`); }
  }

  return <div className="kit-pane">
    <p className="page-sub">Артикул КД, тип и ссылка — из расчёта. Наименование — то, что закуплено под этот артикул КД, пустое — впиши сам. По типу генератор раскладывает состав по операциям. «На партию» = расход на штуку × кол-во с учётом брака. «ГРН склада» — с какой складской строки брать: выбери, и при отправке на комплектацию количество зарезервируется. Не выбрал — позиция всё равно уедет заявкой и будет ждать поставки, ГРН к ней подцепит кладовщик после приёмки. Правки сохраняются сами и переживают обновление из расчёта.</p>
    {canEdit
      ? <div className="page-actions"><button className="primary" onClick={() => setIssuing(true)}>🧺 Отправить на комплектацию</button><span className="muted kit-autosave">{autosave}</span></div>
      : <p className="page-sub">Раздел открыт на просмотр — править и отправлять нельзя.</p>}
    {issuing && <IssueDialog project={card.project} close={() => setIssuing(false)} done={(text) => { setIssuing(false); setNote(text); }} />}
    {picking && <LotPicker component={picking.component} batch={kitBatch(draftOf(picking.assembly, picking.component).per, picking.assembly.run, draftOf(picking.assembly, picking.component).scrap)}
      current={draftOf(picking.assembly, picking.component).lot}
      close={() => setPicking(null)}
      pick={(lot) => { edit(picking.assembly, picking.component, { lot }); void save(picking.assembly, picking.component, { lot }); setPicking(null); }} />}
    {card.assemblies.map((assembly) => <div key={assembly.index} className="kit-spec">
      <p><strong>{assembly.index}</strong> {assembly.name} <span className="muted">кол-во {assembly.run}</span></p>
      <div className="table-wrap"><table className="kit-spec-table">
        <thead><tr><th>Артикул КД</th><th>Тип</th><th>Наименование</th><th>Ссылка</th><th>На 1 шт</th><th>% брака</th><th>На партию</th><th>ГРН склада</th></tr></thead>
        <tbody>{assembly.components.map((component) => {
          const draft = draftOf(assembly, component);
          const commit = () => void save(assembly, component);
          return <tr key={component.art}>
            <td>{component.art}</td>
            <td>{component.type}</td>
            <td><input aria-label={`Наименование ${component.art}`} value={draft.name} disabled={!canEdit} onChange={(event) => edit(assembly, component, { name: event.target.value })} onBlur={commit} /></td>
            <td><div className="kit-link">
              <input aria-label={`Ссылка ${component.art}`} value={draft.link} placeholder="—" disabled={!canEdit} onChange={(event) => edit(assembly, component, { link: event.target.value })} onBlur={commit} />
              {/^https?:\/\//i.test(draft.link) && <a href={draft.link} target="_blank" rel="noopener" title="Открыть">↗</a>}
            </div></td>
            <td><input aria-label={`На 1 шт ${component.art}`} value={draft.per} disabled={!canEdit} onChange={(event) => edit(assembly, component, { per: event.target.value })} onBlur={commit} /></td>
            <td><input aria-label={`% брака ${component.art}`} value={draft.scrap} disabled={!canEdit} onChange={(event) => edit(assembly, component, { scrap: event.target.value })} onBlur={commit} /></td>
            <td className="num">{kitBatch(draft.per, assembly.run, draft.scrap)}</td>
            <td><button className="secondary small" disabled={!canEdit} onClick={() => setPicking({ assembly, component })}>
              {draft.lot?.grn ? `${draft.lot.grn}${draft.lot.package ? ` / ${draft.lot.package}` : ""}` : "📦 выбрать со склада"}
            </button></td>
          </tr>;
        })}</tbody>
      </table></div>
    </div>)}
  </div>;
}

function LotPicker({ component, batch, current, close, pick }: {
  component: KitsetComponent; batch: number; current: KitsetLot | null; close: () => void; pick: (lot: KitsetLot | null) => void;
}) {
  const [query, setQuery] = useState(component.name || component.art);
  const [lots, setLots] = useState<KitsetStockLot[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => {
      kitsetsApi.lots(query).then((result) => setLots(result.lots)).catch((reason) => setError(message(reason, "Склад не ответил")));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  return <div className="panel kit-modal" role="dialog" aria-label="Выбор ГРН">
    <h3>📦 ГРН для «{component.name || component.art}»</h3>
    <p className="page-sub">Нужно на партию: {batch}. Выбранный ГРН зарезервируется при отправке на комплектацию.</p>
    <input aria-label="Поиск по складу" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="наименование, артикул, ГРН" />
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="table-wrap"><table>
      <thead><tr><th>Склад</th><th>ГРН</th><th>Пакет</th><th>Наименование</th><th>Место</th><th>Доступно</th><th /></tr></thead>
      <tbody>{(lots || []).map((lot) => <tr key={lot.lot_id} className={current?.grn === lot.grn && (current?.package || "") === lot.package ? "selected" : ""}>
        <td>{lot.warehouse}</td><td>{lot.grn}</td><td>{lot.package}</td><td>{lot.name}</td><td>{lot.storage_location}</td>
        <td className="num">{lot.available} {lot.unit}</td>
        <td><button className="secondary small" onClick={() => pick({ lot_id: lot.lot_id, warehouse: lot.warehouse, grn: lot.grn, package: lot.package })}>Выбрать</button></td>
      </tr>)}</tbody>
    </table></div>
    {lots && !lots.length && <div className="empty">Ничего не нашлось — сотри слова и ищи по своим.</div>}
    <div className="page-actions">
      {current && <button className="secondary" onClick={() => pick(null)}>Снять привязку</button>}
      <button className="secondary" onClick={close}>Закрыть</button>
    </div>
  </div>;
}

function IssueDialog({ project, close, done }: { project: string; close: () => void; done: (text: string) => void }) {
  const [deadline, setDeadline] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit() {
    if (busy) return;
    if (!deadline) { setError("Укажи дедлайн."); return; }
    setBusy(true); setError("");
    try {
      const result = await kitsetsApi.issue(project, deadline);
      const numbers = result.created.map((item) => item.no + (item.shortfall.length ? " (недобор)" : "")).join(", ");
      done(`${result.message} ${numbers}`.trim());
    } catch (reason) { setError(message(reason, "Заявки не созданы")); }
    finally { setBusy(false); }
  }
  return <div className="panel kit-modal" role="dialog" aria-label="Отправить на комплектацию">
    <h3>🧺 Отправить на комплектацию</h3>
    <p className="page-sub">На каждую сборку набора уйдёт отдельная заявка: назначение — {project} и индекс КБ. Позиции с выбранным ГРН сразу встают в резерв, остальные уедут в заявку ждать поставки — кладовщик подцепит к ним ГРН после приёмки.</p>
    <label>Дедлайн<input type="date" value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="page-actions">
      <button className="primary" disabled={busy} onClick={() => void submit()}>Отправить</button>
      <button className="secondary" onClick={close}>Отмена</button>
    </div>
  </div>;
}

